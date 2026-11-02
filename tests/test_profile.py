"""Uji profil kemampuan komputer: kapan diuji ulang, dan bahwa tes yang gagal sesaat tidak menghapus model yang
sudah terbukti jalan (komputer ini sedang ikut berpikir untuk bot)."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import system  # noqa: E402
from inode.__main__ import merge_profile, read_profile, should_unload  # noqa: E402


class _Cfg(dict):
    def __init__(self, data_dir, **kw):
        super().__init__(model="auto", **kw)
        self.data_dir = data_dir


class ReadProfileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.cfg = _Cfg(self.dir)

    def write(self, **over):
        prof = {"model": "qwen3:1.7b", "tps": 5.0, "score": 60, "tested": time.time() - 60, "retry": False,
                "model_setting": "auto", "bench_v": system.BENCH_VERSION}
        prof.update(over)
        prof = {k: v for k, v in prof.items() if v is not None}
        with open(os.path.join(self.dir, "profile.json"), "w") as f:
            json.dump(prof, f)

    def test_masih_berlaku(self):
        self.write()
        prof, fresh = read_profile(self.cfg)
        self.assertTrue(fresh)
        self.assertEqual(prof["model"], "qwen3:1.7b")

    def test_tidak_ada_berkas(self):
        self.assertEqual(read_profile(self.cfg), (None, False))

    def test_berkas_rusak(self):
        with open(os.path.join(self.dir, "profile.json"), "w") as f:
            f.write("{rusak")
        self.assertEqual(read_profile(self.cfg), (None, False))

    def test_cara_tes_berubah_diuji_ulang(self):
        # Profil dari kode lama (tanpa bench_v, mis. Hub yang tercatat "tanpa model" gara-gara crash GPU).
        self.write(bench_v=None)
        self.assertFalse(read_profile(self.cfg)[1])
        self.write(bench_v=system.BENCH_VERSION - 1)
        self.assertFalse(read_profile(self.cfg)[1])
        self.write(bench_v=system.BENCH_VERSION + 1)
        self.assertTrue(read_profile(self.cfg)[1])

    def test_kedaluwarsa_tujuh_hari(self):
        self.write(tested=time.time() - 8 * 86400)
        self.assertFalse(read_profile(self.cfg)[1])
        self.write(tested=time.time() - 6 * 86400)
        self.assertTrue(read_profile(self.cfg)[1])

    def test_model_belum_siap_diuji_lagi_setengah_jam(self):
        self.write(retry=True, model="", tested=time.time() - 1500)
        self.assertTrue(read_profile(self.cfg)[1])
        self.write(retry=True, model="", tested=time.time() - 1900)
        self.assertFalse(read_profile(self.cfg)[1])

    def test_pengaturan_model_berubah(self):
        self.write(model_setting="qwen3:8b")
        self.assertFalse(read_profile(self.cfg)[1])


class MergeProfileTest(unittest.TestCase):
    old = {"model": "qwen3:1.7b", "tps": 5.0, "score": 60.0, "workers": 4}

    def test_tes_ulang_gagal_sesaat_model_lama_dipertahankan(self):
        new = {"model": "", "tps": 0.0, "score": 8.0, "workers": 2, "retry": True}
        merged = merge_profile(dict(self.old), new)
        self.assertEqual((merged["model"], merged["tps"], merged["score"]), ("qwen3:1.7b", 5.0, 60.0))
        self.assertTrue(merged["retry"])              # tetap diuji ulang sebentar lagi
        self.assertEqual(merged["workers"], 2)        # hal lain mengikuti hasil tes baru

    def test_tes_ulang_berhasil_memakai_hasil_baru(self):
        new = {"model": "qwen3:4b-instruct-2507-q4_K_M", "tps": 7.0, "score": 80.0, "retry": False}
        self.assertEqual(merge_profile(dict(self.old), dict(new))["model"], "qwen3:4b-instruct-2507-q4_K_M")

    def test_terlalu_lambat_bukan_kegagalan_sesaat(self):
        # Ollama siap tapi modelnya terlalu lambat (< 0,8 tok/s): hasil tes baru dipercaya, bukan dibatalkan.
        new = {"model": "", "tps": 0.3, "score": 8.0, "retry": False}
        self.assertEqual(merge_profile(dict(self.old), new)["model"], "")

    def test_tes_pertama_gagal_tanpa_model_lama(self):
        new = {"model": "", "tps": 0.0, "score": 8.0, "retry": True}
        self.assertEqual(merge_profile({"model": ""}, new)["model"], "")


class ShouldUnloadTest(unittest.TestCase):
    def test_tabel(self):
        on, off = {"think": True}, {"think": False}
        model, none = {"model": "m"}, {"model": ""}
        # (cfg, profile, working) -> lepas model?
        self.assertFalse(should_unload(on, model, False))    # dipakai, tapi ikut berpikir: model TETAP termuat
        self.assertFalse(should_unload({}, model, False))    # bawaan: think aktif
        self.assertTrue(should_unload(off, model, False))    # tidak ikut berpikir: boleh dilepas saat dipakai
        self.assertFalse(should_unload(off, model, True))    # sedang bekerja/menganggur: jangan dilepas
        self.assertFalse(should_unload(off, none, False))    # tak ada model yang perlu dilepas


if __name__ == "__main__":
    unittest.main()
