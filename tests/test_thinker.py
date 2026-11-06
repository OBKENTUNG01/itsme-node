"""Uji thinker.py: layanan berpikir yang tidak boleh mati. Ollama-nya palsu tapi server HTTP-nya
sungguhan; yang diuji adalah perilaku komputer ini: melayani, antre, menolak saat pemilihan pemimpin,
menjaga Ollama tetap hidup & model tetap termuat, prioritas rendah, dan tidak tidur."""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_ollama import FakeOllama, dead_url  # noqa: E402
from inode import crawler, llm, system, thinker  # noqa: E402

MSG = [{"role": "user", "content": "apa ibu kota Jawa Barat?"}]


class _Cluster:
    id = "node-a"
    paused = False


def _wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        llm.configure(self.dir)
        self.addCleanup(llm.configure, None)
        p = mock.patch.object(llm, "RETRY_PAUSE", 0)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(llm, "gpu_crashed", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.fake = FakeOllama(models=["m1"])
        self.addCleanup(self.fake.close)
        self.cluster = _Cluster()
        self.cluster.paused = False

    def make(self, url=None, profile=None, **cfg):
        base = {"name": "PC-A", "ollama_url": url or self.fake.url, "think": True}
        base.update(cfg)
        prof = {"model": "m1", "tps": 5.0, "ram": 16.0, "vram": 0.0}
        prof.update(profile or {})
        return thinker.Thinker(base, prof, self.cluster)


class HandleTest(_Base):
    def test_jawaban_lengkap(self):
        self.fake.reply = "Bandung."
        t = self.make()
        res = t.handle({"messages": MSG, "think": False, "options": {"temperature": 0.1}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["message"]["content"], "Bandung.")
        self.assertEqual((res["node"], res["name"], res["model"]), ("node-a", "PC-A", "m1"))
        self.assertEqual(res["eval_count"], 20)
        self.assertEqual(res["tps"], 10.0)
        body = self.fake.posts("/api/chat")[0]
        self.assertEqual(body["keep_alive"], -1)
        self.assertEqual(body["options"]["num_ctx"], 16384)   # RAM 16 GB -> konteks luas
        self.assertEqual(body["options"]["temperature"], 0.1)
        self.assertIs(body["think"], False)
        self.assertEqual((t.served, t.inflight, t.waiting), (1, 0, 0))
        self.assertEqual(t.tps, 10.0)

    def test_opsi_di_luar_daftar_putih_dibuang(self):
        t = self.make()
        t.handle({"messages": MSG, "options": {"num_gpu": 99, "temperature": 0.5, "evil": 1, "num_predict": True,
                                              "stop": ["x", 3], "top_k": 40}})
        opts = self.fake.posts("/api/chat")[0]["options"]
        self.assertNotIn("num_gpu", opts)       # urusan node ini, bukan pemanggil
        self.assertNotIn("evil", opts)
        self.assertNotIn("num_predict", opts)   # bool bukan angka
        self.assertNotIn("stop", opts)          # daftar harus berisi teks semua
        self.assertEqual((opts["temperature"], opts["top_k"]), (0.5, 40))

    def test_konteks_pemanggil_dihormati_dan_dibatasi_tangga(self):
        t = self.make()
        t.handle({"messages": MSG, "options": {"num_ctx": 2048}})
        self.assertEqual(self.fake.posts("/api/chat")[0]["options"]["num_ctx"], 2048)

    def test_konteks_bawaan_menurut_ram(self):
        for ram, ctx in ((32, 32768), (16, 16384), (8, 8192), (4, 4096)):
            self.assertEqual(thinker.default_ctx(ram), ctx)

    def test_alat_dan_format_diteruskan(self):
        t = self.make()
        t.handle({"messages": MSG, "tools": [{"type": "function", "function": {"name": "x"}}], "format": "json"})
        body = self.fake.posts("/api/chat")[0]
        self.assertEqual(body["tools"][0]["function"]["name"], "x")
        self.assertEqual(body["format"], "json")

    def test_pemimpin_mati_semua_berhenti_berpikir(self):
        self.cluster.paused = True       # fase "electing"
        t = self.make()
        self.assertIsNone(t.handle({"messages": MSG}))
        self.assertEqual(self.fake.requests, [])
        self.cluster.paused = False      # pemimpin baru terdengar -> langsung berpikir lagi
        self.assertTrue(t.handle({"messages": MSG})["ok"])

    def test_pemilihan_dimulai_saat_menunggu_giliran(self):
        t = self.make()
        self.fake.script = [{"sleep": 0.6}]
        first = threading.Thread(target=t.handle, args=({"messages": MSG},))
        first.start()
        self.assertTrue(_wait_for(lambda: t.inflight == 1))
        results = []
        second = threading.Thread(target=lambda: results.append(t.handle({"messages": MSG})))
        second.start()
        self.assertTrue(_wait_for(lambda: t.waiting == 1))
        self.cluster.paused = True       # pemimpin mati selagi antre
        first.join(5)
        second.join(5)
        self.assertEqual(results, [None])  # yang menunggu tidak diteruskan ke Ollama
        self.assertEqual(len(self.fake.posts("/api/chat")), 1)

    def test_dimatikan(self):
        res = self.make(think=False).handle({"messages": MSG})
        self.assertEqual((res["ok"], res["kind"]), (False, "disabled"))

    def test_tanpa_model(self):
        res = self.make(profile={"model": ""}).handle({"messages": MSG})
        self.assertEqual((res["ok"], res["kind"]), (False, "no_model"))

    def test_model_dipaksa_lewat_config(self):
        t = self.make(profile={"model": "kecil"}, think_model="besar")
        self.assertEqual(t.model(), "besar")
        t.handle({"messages": MSG})
        self.assertEqual(self.fake.posts("/api/chat")[0]["model"], "besar")

    def test_permintaan_rusak(self):
        t = self.make()
        for payload in ({}, {"messages": []}, {"messages": "halo"}, {"messages": ["a"]}):
            res = t.handle(payload)
            self.assertEqual((res["ok"], res["kind"]), (False, "bad"), payload)
        big = [{"role": "user", "content": "x" * (thinker.MAX_BODY_CHARS + 1)}]
        self.assertEqual(t.handle({"messages": big})["kind"], "bad")
        self.assertEqual(self.fake.requests, [])

    def test_kegagalan_ollama_dilaporkan_dan_dihitung(self):
        self.fake.script = [{"status": 404, "error": "model 'm1' not found"}]
        t = self.make()
        res = t.handle({"messages": MSG})
        self.assertEqual((res["ok"], res["kind"]), (False, "no_model"))
        self.assertEqual((t.failed, t.inflight), (1, 0))
        self.assertTrue(t.handle({"messages": MSG})["ok"])    # pulih tanpa campur tangan

    def test_ollama_mati_saat_melayani(self):
        t = self.make()
        t.up = True
        with mock.patch.object(llm, "chat", side_effect=llm.ThinkError("unavailable", "Ollama tidak bisa dihubungi")):
            res = t.handle({"messages": MSG})
        self.assertEqual((res["ok"], res["kind"]), (False, "unavailable"))
        self.assertFalse(t.up)    # penjaga melihatnya mati dan menyalakannya lagi (SupervisorTest)
        self.assertEqual((t.failed, t.inflight), (1, 0))


class QueueTest(_Base):
    def test_antre_lalu_penuh(self):
        t = self.make(think_queue=1)
        self.assertEqual(t.parallel, 1)
        self.fake.script = [{"sleep": 0.8}]
        out = []
        first = threading.Thread(target=lambda: out.append(t.handle({"messages": MSG})))
        first.start()
        self.assertTrue(_wait_for(lambda: t.inflight == 1))
        self.assertTrue(t.busy())
        second = threading.Thread(target=lambda: out.append(t.handle({"messages": MSG})))
        second.start()
        self.assertTrue(_wait_for(lambda: t.waiting == 1))
        third = t.handle({"messages": MSG})                  # antrean penuh: ditolak LANGSUNG
        self.assertEqual((third["ok"], third["kind"]), (False, "busy"))
        first.join(5)
        second.join(5)
        self.assertEqual([r["ok"] for r in out], [True, True])   # dua yang diterima tetap dilayani berurutan
        self.assertEqual(t.served, 2)

    def test_menunggu_terlalu_lama_dianggap_sibuk(self):
        t = self.make(think_queue=3)
        self.fake.script = [{"sleep": 1.2}]
        threading.Thread(target=t.handle, args=({"messages": MSG},), daemon=True).start()
        self.assertTrue(_wait_for(lambda: t.inflight == 1))
        with mock.patch.object(thinker, "QUEUE_WAIT", 0.2):
            res = t.handle({"messages": MSG})
        self.assertEqual((res["ok"], res["kind"]), (False, "busy"))   # bot lalu memakai komputer lain
        self.assertEqual(t.waiting, 0)                                # antrean dibersihkan lagi

    def test_dua_slot_untuk_gpu_besar(self):
        self.assertEqual(self.make(profile={"vram": 8.0}).parallel, 2)
        self.assertEqual(self.make(think_parallel=3).parallel, 3)
        self.assertEqual(self.make().parallel, 1)

    def test_busy_hanya_selama_dan_sebentar_setelah_berpikir(self):
        t = self.make()
        self.assertFalse(t.busy())
        t.handle({"messages": MSG})
        self.assertTrue(t.busy())                 # baru selesai: riset latar tetap mengalah beberapa detik
        t.last_end = time.time() - 10
        self.assertFalse(t.busy())


class HealthTest(_Base):
    def test_ringkasan_untuk_gosip(self):
        t = self.make()
        h = t.health()
        self.assertEqual((h["model"], h["ok"], h["up"], h["tps"]), ("m1", False, False, 5.0))  # belum dicek: belum siap
        t.up = True
        t.handle({"messages": MSG})
        h = t.health()
        self.assertTrue(h["ok"] and h["loaded"])
        self.assertEqual((h["served"], h["inflight"], h["waiting"], h["slots"]), (1, 0, 0, 1))
        self.assertEqual((h["ctx"], h["ram"], h["cpu"]), (16384, 16.0, False))
        self.assertEqual(h["tps"], 10.0)

    def test_dimatikan_atau_tanpa_model_tidak_siap(self):
        self.assertEqual(self.make(think=False).health()["model"], "")
        h = self.make(profile={"model": ""}).health()
        self.assertFalse(h["ok"])

    def test_cpu_saja_dilaporkan(self):
        t = self.make()
        llm._state["levels"]["m1"] = 2
        self.assertTrue(t.health()["cpu"])


def _ollama_down():
    # Menyambung ke port mati di Windows butuh ±2 detik; yang diuji jadwal penyalaan, bukan koneksinya
    # (koneksi ke port mati diuji di test_llm_ladder).
    return mock.patch.object(llm, "available", return_value=False)


class SupervisorTest(_Base):
    def test_ollama_mati_dinyalakan_lagi_berprioritas_rendah(self):
        t = self.make(url=dead_url())
        with _ollama_down(), mock.patch.object(system, "start_ollama", return_value=True) as start:
            t.tick(1000.0)
            start.assert_called_once_with(low_priority=True)
            self.assertEqual((t.restarts, t.up), (1, False))
            t.tick(1005.0)                         # terlalu cepat: tunggu jeda, jangan membanjiri
            self.assertEqual(start.call_count, 1)
            t.tick(1000.0 + 15.0)                  # jeda 15 detik lewat: coba lagi
            self.assertEqual(start.call_count, 2)
            t.tick(1000.0 + 15.0 + 29.0)           # jeda makin panjang kalau terus gagal (2 x 15)
            self.assertEqual(start.call_count, 2)
            t.tick(1000.0 + 15.0 + 31.0)
            self.assertEqual(start.call_count, 3)

    def test_tidak_menyalakan_ollama_saat_dipasang_atau_dicopot(self):
        """Selama ollama_ctl memasang/mencopot, menyalakan Ollama membuat berkas terkunci."""
        t = self.make(url=dead_url())
        system.OLLAMA_MAINTENANCE.set()
        self.addCleanup(system.OLLAMA_MAINTENANCE.clear)
        with _ollama_down(), mock.patch.object(system, "start_ollama", return_value=True) as start:
            t.tick(1000.0)
            t.tick(2000.0)
            start.assert_not_called()
            self.assertEqual(t.restarts, 0)  # bukan kegagalan, jadi tidak menambah jeda
            system.OLLAMA_MAINTENANCE.clear()
            t.tick(2005.0)
            start.assert_called_once_with(low_priority=True)

    def test_jeda_penyalaan_dibatasi(self):
        t = self.make(url=dead_url())
        with _ollama_down(), mock.patch.object(system, "start_ollama", return_value=False):
            now, gaps = 1000.0, []
            for _ in range(30):
                t.tick(now)
                gaps.append(t._next_restart - now)
                now = t._next_restart
        self.assertEqual(gaps[:3], [15.0, 30.0, 45.0])      # makin panjang kalau terus gagal
        self.assertEqual(gaps[-1], 300.0)                   # tapi tak pernah lebih dari 5 menit

    def test_model_belum_termuat_dimuat_dan_ditahan(self):
        t = self.make()
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
        self.assertTrue(t.up and t.loaded)
        warm = [b for b in self.fake.posts("/api/generate") if b.get("keep_alive") == -1 and b.get("prompt") == ""]
        self.assertEqual(len(warm), 1)
        self.assertEqual(self.fake.loaded, ["m1"])

    def test_model_sudah_termuat_tidak_dimuat_lagi(self):
        self.fake.loaded = ["m1"]
        t = self.make()
        llm._state["ctl"]["m1"] = True
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
        self.assertEqual(self.fake.posts("/api/generate"), [])
        self.assertTrue(t.loaded)

    def test_model_terlepas_dimuat_lagi(self):
        """Model dilepas pihak lain (mis. bot memanggil unload): penjaga menahannya lagi."""
        self.fake.loaded = ["m1"]
        t = self.make()
        llm._state["ctl"]["m1"] = True
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
            self.fake.loaded = []
            t.tick(1050.0)
        self.assertTrue(t.loaded)
        self.assertEqual(self.fake.loaded, ["m1"])

    def test_tidak_memuat_saat_sedang_melayani(self):
        t = self.make()
        t.inflight = 1
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
        self.assertEqual(self.fake.posts("/api/generate"), [])

    def test_gagal_memuat_dicoba_lagi_nanti_bukan_terus_menerus(self):
        t = self.make()
        self.fake.script = [{"status": 404, "error": "model 'm1' not found"}]
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
            self.assertFalse(t.loaded)
            t.tick(1010.0)                 # belum 45 detik
            self.assertEqual(len(self.fake.posts("/api/generate")), 1)
            t.tick(1050.0)
            self.assertEqual(len(self.fake.posts("/api/generate")), 2)

    def test_uji_think_false(self):
        self.fake.reply = "Ya."
        t = self.make()
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
        self.assertIs(t.ctl, True)
        self.assertIs(t.health()["ctl"], True)

    def test_model_yang_selalu_berpikir_ditandai(self):
        self.fake.reply = "Okay, so the user wants one word."
        t = self.make()
        with mock.patch.object(system, "lower_ollama_priority", return_value=0):
            t.tick(1000.0)
        self.assertIs(t.ctl, False)

    def test_prioritas_diturunkan_sekali_per_menit(self):
        t = self.make()
        with mock.patch.object(system, "lower_ollama_priority", return_value=0) as lower:
            t.tick(1000.0)
            t.tick(1010.0)
            self.assertEqual(lower.call_count, 1)
            t.tick(1070.0)
            self.assertEqual(lower.call_count, 2)

    def test_tanpa_model_tidak_menyalakan_ollama(self):
        t = self.make(url=dead_url(), profile={"model": ""})
        with _ollama_down(), mock.patch.object(system, "start_ollama") as start:
            t.tick(1000.0)
        start.assert_not_called()
        self.assertFalse(t.up)

    def test_penjaga_tidak_mati_karena_error(self):
        t = self.make()
        stop = threading.Event()
        calls = []

        def boom(now):
            calls.append(now)
            if len(calls) == 1:
                raise RuntimeError("tak terduga")
            stop.set()

        t.tick = boom
        with mock.patch.object(stop, "wait", lambda s: stop.is_set()):
            t._supervise(stop)
        self.assertEqual(len(calls), 2)

    def test_start_tidak_menyalakan_apa_apa_kalau_dimatikan(self):
        stop = threading.Event()
        with mock.patch.object(threading, "Thread") as thread:
            self.make(think=False).start(stop)
        thread.assert_not_called()


class KeepAwakeTest(_Base):
    def test_windows_hanya_menahan_tidur_selama_ada_permintaan(self):
        calls = []
        kernel = mock.Mock()
        kernel.SetThreadExecutionState.side_effect = lambda flags: calls.append(flags) or 1
        windll = mock.Mock(kernel32=kernel)
        t = self.make()

        class StopAfterThreePolls:
            def __init__(self):
                self.polls = 0

            def is_set(self):
                return self.polls >= 3

            def wait(self, _seconds):
                self.polls += 1
                return self.is_set()

        stop = StopAfterThreePolls()
        t.busy = mock.Mock(side_effect=[False, True, False])
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(thinker.ctypes, "windll", windll, create=True):
            t._keep_awake(stop)
        self.assertEqual(calls, [thinker.ES_CONTINUOUS | thinker.ES_SYSTEM_REQUIRED,
                                 thinker.ES_CONTINUOUS])
        self.assertEqual(t.busy.call_count, 3)

    def test_windows_melepas_pencegah_tidur_saat_node_berhenti_di_tengah_kerja(self):
        calls = []
        kernel = mock.Mock()
        kernel.SetThreadExecutionState.side_effect = lambda flags: calls.append(flags) or 1
        windll = mock.Mock(kernel32=kernel)
        t = self.make()

        class StopAfterOnePoll:
            polls = 0

            def is_set(self):
                return self.polls >= 1

            def wait(self, _seconds):
                self.polls += 1
                return self.is_set()

        t.busy = mock.Mock(return_value=True)
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(thinker.ctypes, "windll", windll, create=True):
            t._keep_awake(StopAfterOnePoll())
        self.assertEqual(calls, [thinker.ES_CONTINUOUS | thinker.ES_SYSTEM_REQUIRED,
                                 thinker.ES_CONTINUOUS])

    def test_windows_mencoba_lagi_jika_gagal_melepas_pencegah_tidur(self):
        calls = []
        kernel = mock.Mock()
        results = iter([1, 0, 1])
        kernel.SetThreadExecutionState.side_effect = lambda flags: (calls.append(flags), next(results))[1]
        windll = mock.Mock(kernel32=kernel)
        t = self.make()

        class StopAfterThreePolls:
            polls = 0

            def is_set(self):
                return self.polls >= 3

            def wait(self, _seconds):
                self.polls += 1
                return self.is_set()

        t.busy = mock.Mock(side_effect=[True, False, False])
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(thinker.ctypes, "windll", windll, create=True):
            t._keep_awake(StopAfterThreePolls())
        self.assertEqual(calls, [thinker.ES_CONTINUOUS | thinker.ES_SYSTEM_REQUIRED,
                                 thinker.ES_CONTINUOUS, thinker.ES_CONTINUOUS])

    def test_bukan_windows_tidak_melakukan_apa_apa(self):
        t = self.make()
        with mock.patch.object(system, "IS_WIN", False):
            t._keep_awake(threading.Event())    # kembali seketika

    def test_bisa_dimatikan_lewat_config(self):
        stop = threading.Event()
        started = []
        real = threading.Thread

        class Spy:
            def __init__(self, *a, **kw):
                started.append(kw.get("name"))
                self.t = real(target=lambda: None)

            def start(self):
                self.t.start()

        with mock.patch.object(threading, "Thread", Spy):
            self.make(think_keep_awake=False).start(stop)
        self.assertEqual(started, ["thinker"])
        started.clear()
        with mock.patch.object(threading, "Thread", Spy):
            self.make().start(stop)
        self.assertEqual(started, ["thinker", "keep-awake"])


class OptionsTest(unittest.TestCase):
    def test_clean_options(self):
        self.assertEqual(thinker.clean_options(None), {})
        self.assertEqual(thinker.clean_options({"temperature": 0.3, "seed": 7, "num_gpu": 0, "x": 1}),
                         {"temperature": 0.3, "seed": 7})
        self.assertEqual(thinker.clean_options({"stop": ["a", "b"]}), {"stop": ["a", "b"]})
        self.assertEqual(thinker.clean_options({"stop": "a"}), {})


class CrawlerYieldsTest(unittest.TestCase):
    def test_riset_mengalah_saat_berpikir(self):
        cluster = mock.Mock(working=True, paused=False)
        c = crawler.Crawler({"name": "x", "max_pages_per_domain": 1, "domain_delay": 0}, {"model": "", "workers": 1},
                            cluster, mock.Mock())
        self.assertTrue(c.can_work())
        c.thinker = mock.Mock(busy=lambda: True)
        self.assertFalse(c.can_work())
        c.thinker = mock.Mock(busy=lambda: False)
        self.assertTrue(c.can_work())


if __name__ == "__main__":
    unittest.main()
