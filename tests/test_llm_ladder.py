"""Uji llm.py terhadap Ollama palsu SUNGGUHAN (server HTTP di loopback): tangga keamanan saat proses
model mati (konteks lebih kecil -> CPU saja), pengingat tingkat, dan pengenalan model yang selalu berpikir."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_ollama import FakeOllama, dead_url  # noqa: E402
from inode import llm  # noqa: E402

MSG = [{"role": "user", "content": "halo"}]


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        llm.configure(self.dir)
        llm._no_think_param.clear()
        patcher = mock.patch.object(llm, "RETRY_PAUSE", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(llm, "gpu_crashed", return_value=False)
        self.gpu = patcher.start()
        self.addCleanup(patcher.stop)
        self.fake = FakeOllama(models=["m1"])
        self.addCleanup(self.fake.close)
        self.addCleanup(llm.configure, None)

    def state_file(self):
        with open(os.path.join(self.dir, "think_state.json"), encoding="utf-8") as f:
            return json.load(f)


class ChatTest(_Base):
    def test_jawaban_dan_statistik(self):
        self.fake.reply = "Bandung."
        message, stats = llm.chat(self.fake.url, "m1", MSG, options={"temperature": 0.2}, think=False)
        self.assertEqual(message["content"], "Bandung.")
        self.assertEqual(stats["eval_count"], 20)
        body = self.fake.posts("/api/chat")[0]
        self.assertEqual(body["keep_alive"], -1)          # model ditahan di memori
        self.assertIs(body["stream"], False)
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["temperature"], 0.2)
        self.assertNotIn("num_gpu", body["options"])      # tingkat 0 = pengaturan biasa

    def test_think_tidak_dikirim_kalau_none(self):
        llm.chat(self.fake.url, "m1", MSG)
        self.assertNotIn("think", self.fake.posts("/api/chat")[0])

    def test_alat_dan_format_diteruskan(self):
        llm.chat(self.fake.url, "m1", MSG, tools=[{"type": "function"}], fmt="json")
        body = self.fake.posts("/api/chat")[0]
        self.assertEqual(body["tools"], [{"type": "function"}])
        self.assertEqual(body["format"], "json")

    def test_model_tidak_ada(self):
        self.fake.script = [{"status": 404, "error": "model 'x' not found"}]
        with self.assertRaises(llm.ThinkError) as cm:
            llm.chat(self.fake.url, "x", MSG)
        self.assertEqual(cm.exception.kind, "no_model")

    def test_ollama_mati(self):
        with self.assertRaises(llm.ThinkError) as cm:
            llm.chat(dead_url(), "m1", MSG, timeout=3)
        self.assertEqual(cm.exception.kind, "unavailable")

    def test_terlalu_lama(self):
        self.fake.script = [{"sleep": 1.5}]
        with self.assertRaises(llm.ThinkError) as cm:
            llm.chat(self.fake.url, "m1", MSG, timeout=0.3)
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertEqual(llm.level_for("m1"), 0)  # lambat bukan crash: pengaturan tidak diturunkan

    def test_antrean_ollama_penuh_bukan_crash(self):
        self.fake.script = [{"status": 503, "error": "server busy, please try again. maximum pending requests exceeded"}]
        with self.assertRaises(llm.ThinkError) as cm:
            llm.chat(self.fake.url, "m1", MSG)
        self.assertEqual(cm.exception.kind, "busy")
        self.assertEqual(llm.level_for("m1"), 0)

    def test_model_tanpa_mode_berpikir_menolak_parameter_think(self):
        self.fake.script = [{"status": 400, "error": '"m1" does not support thinking'}]
        message, _ = llm.chat(self.fake.url, "m1", MSG, think=False)
        self.assertEqual(message["content"], "halo")
        first, second = self.fake.posts("/api/chat")
        self.assertIn("think", first)
        self.assertNotIn("think", second)
        llm.chat(self.fake.url, "m1", MSG, think=False)   # diingat: tidak dikirim lagi
        self.assertNotIn("think", self.fake.posts("/api/chat")[2])

    def test_jawaban_tanpa_pesan_ditolak(self):
        self.fake.script = [{"json": {"done": True}}]
        with self.assertRaises(llm.ThinkError) as cm:
            llm.chat(self.fake.url, "m1", MSG)
        self.assertEqual(cm.exception.kind, "bad")


class LadderTest(_Base):
    def test_500_diulang_dengan_konteks_lebih_kecil(self):
        self.fake.script = [{"status": 500, "error": "llama runner process has terminated: exit status 2"}]
        message, _ = llm.chat(self.fake.url, "m1", MSG, options={"num_ctx": 32768})
        self.assertEqual(message["content"], "halo")
        first, second = self.fake.posts("/api/chat")
        self.assertEqual(first["options"]["num_ctx"], 32768)
        self.assertEqual(second["options"]["num_ctx"], 8192)
        self.assertNotIn("num_gpu", second["options"])
        self.assertEqual(llm.level_for("m1"), 1)

    def test_koneksi_putus_dua_kali_berakhir_di_cpu_saja(self):
        self.fake.script = [{"reset": True}, {"reset": True}]
        message, _ = llm.chat(self.fake.url, "m1", MSG)
        self.assertEqual(message["content"], "halo")
        bodies = self.fake.posts("/api/chat")
        self.assertEqual(len(bodies), 3)
        self.assertEqual(bodies[2]["options"]["num_gpu"], 0)
        self.assertEqual(bodies[2]["options"]["num_ctx"], 8192)
        self.assertTrue(llm.cpu_only("m1"))

    def test_gpu_mati_langsung_cpu_saja(self):
        self.gpu.return_value = True   # log Ollama: "device lost" -> lompat ke CPU saja
        self.fake.script = [{"status": 500, "error": "ggml_vulkan: device lost"}]
        llm.chat(self.fake.url, "m1", MSG)
        second = self.fake.posts("/api/chat")[1]
        self.assertEqual(second["options"]["num_gpu"], 0)
        self.assertEqual(llm.level_for("m1"), len(llm.SAFE) - 1)

    def test_tingkat_diingat_dan_dimuat_lagi_setelah_menyala_ulang(self):
        self.fake.script = [{"reset": True}, {"reset": True}]
        llm.chat(self.fake.url, "m1", MSG)
        self.assertEqual(self.state_file()["levels"], {"m1": 2})
        llm.configure(self.dir)  # seolah node menyala ulang
        self.assertTrue(llm.cpu_only("m1"))
        llm.chat(self.fake.url, "m1", MSG)
        self.assertEqual(self.fake.posts("/api/chat")[-1]["options"]["num_gpu"], 0)

    def test_tingkat_per_model(self):
        self.fake.script = [{"reset": True}, {"reset": True}]
        llm.chat(self.fake.url, "m1", MSG)
        self.assertFalse(llm.cpu_only("m2"))

    def test_tetap_gagal_di_semua_tingkat(self):
        self.fake.script = [{"reset": True}] * 6
        with self.assertRaises(llm.ThinkError) as cm:
            llm.chat(self.fake.url, "m1", MSG)
        self.assertEqual(cm.exception.kind, "crashed")
        self.assertEqual(len(self.fake.posts("/api/chat")), len(llm.SAFE))

    def test_generate_juga_memakai_tangga(self):
        self.fake.script = [{"status": 500, "error": "boom"}]
        text, _ = llm.generate(self.fake.url, "m1", "Hai")
        self.assertEqual(text, "halo")
        self.assertEqual(llm.level_for("m1"), 1)

    def test_speed_tidak_nol_kalau_crash_lalu_pulih(self):
        # Dulu: crash saat tes = 0.0 tok/s -> model dibuang (Hub tercatat "tanpa model" padahal bisa jalan di CPU).
        self.fake.script = [{"status": 500, "error": "boom"}]
        self.assertGreater(llm.speed(self.fake.url, "m1"), 0)


class StateTest(_Base):
    def test_berkas_rusak_diabaikan(self):
        with open(os.path.join(self.dir, "think_state.json"), "w", encoding="utf-8") as f:
            f.write("{rusak")
        llm.configure(self.dir)
        self.assertEqual(llm.level_for("m1"), 0)

    def test_nilai_di_luar_batas_diabaikan(self):
        with open(os.path.join(self.dir, "think_state.json"), "w", encoding="utf-8") as f:
            json.dump({"levels": {"m1": 99, "m2": "a", "m3": 1}, "ctl": {"m1": "ya", "m2": True}}, f)
        llm.configure(self.dir)
        self.assertEqual((llm.level_for("m1"), llm.level_for("m2"), llm.level_for("m3")), (0, 0, 1))
        self.assertEqual((llm.known_ctl("m1"), llm.known_ctl("m2")), (None, True))


class InventoryTest(_Base):
    def test_installed_dan_loaded(self):
        self.fake.models = ["a", "b:latest"]
        self.fake.loaded = ["a"]
        self.assertEqual(llm.installed(self.fake.url), ["a", "b:latest"])
        self.assertEqual(llm.loaded(self.fake.url), ["a"])
        self.assertTrue(llm.available(self.fake.url))
        self.assertFalse(llm.available(dead_url()))
        self.assertEqual(llm.loaded(dead_url()), [])

    def test_same_model(self):
        self.assertTrue(llm.same_model("qwen3", "qwen3:latest"))
        self.assertTrue(llm.same_model("m1", "m1"))
        self.assertFalse(llm.same_model("m1", "m2"))
        self.assertFalse(llm.same_model("", ""))

    def test_warm_menahan_model_selamanya(self):
        llm.warm(self.fake.url, "m1")
        body = self.fake.posts("/api/generate")[0]
        self.assertEqual(body["keep_alive"], -1)
        self.assertEqual(body["prompt"], "")
        self.assertEqual(llm.loaded(self.fake.url), ["m1"])

    def test_unload(self):
        llm.warm(self.fake.url, "m1")
        llm.unload(self.fake.url, "m1")
        self.assertEqual(llm.loaded(self.fake.url), [])


class ProbeTest(_Base):
    def test_model_patuh(self):
        self.fake.reply = "Ya."
        self.assertIs(llm.probe_ctl(self.fake.url, "m1"), True)
        self.assertIs(llm.known_ctl("m1"), True)
        self.assertIs(self.state_file()["ctl"]["m1"], True)

    def test_penalaran_bocor_ke_jawaban(self):
        self.fake.reply = "Okay, so the user wants me to reply with one word. Let me think about it."
        self.assertIs(llm.probe_ctl(self.fake.url, "m1"), False)

    def test_jawaban_kosong_dan_berpikir_terpisah(self):
        self.fake.script = [{"json": {"message": {"role": "assistant", "content": "", "thinking": "Okay..."}}}]
        self.assertIs(llm.probe_ctl(self.fake.url, "m1"), False)

    def test_bagian_berpikir_terisi_walau_ada_jawaban(self):
        self.fake.script = [{"json": {"message": {"role": "assistant", "content": "Ya.", "thinking": "hm"}}}]
        self.assertIs(llm.probe_ctl(self.fake.url, "m1"), False)

    def test_tidak_bisa_diuji(self):
        self.assertIsNone(llm.probe_ctl(dead_url(), "m1", timeout=3))
        self.assertIsNone(llm.known_ctl("m1"))

    def test_deteksi_penalaran(self):
        for text in ("Okay, so the user is asking", "Let me think about this", "Hmm, the capital", "<think>x</think>",
                     "The user wants a summary", "Wait, that is wrong", "I need to figure out the capital"):
            self.assertTrue(llm.looks_like_reasoning(text), text)
        for text in ("Ibu kota Jawa Barat adalah Bandung.", "Ok, siap!", "Okay", "Ya.", "", "Hai, ada yang bisa kubantu?",
                     "Kota Bandung dipilih karena iklimnya sejuk."):
            self.assertFalse(llm.looks_like_reasoning(text), text)


if __name__ == "__main__":
    unittest.main()
