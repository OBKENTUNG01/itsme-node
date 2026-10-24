"""Uji pick_model: RAM ikut syarat, bukan hanya VRAM."""

import unittest

from inode.system import MODELS, pick_model


class PickModelTest(unittest.TestCase):
    def test_gpu_besar_ram_kecil_tidak_dipilih(self):
        # 4 GB VRAM cukup untuk 4b, tapi RAM 8 GB < syarat 10 GB -> jangan.
        self.assertNotEqual(pick_model(ram=8, vram=4), "qwen3:4b")

    def test_cukup_dua_duanya(self):
        self.assertEqual(pick_model(ram=32, vram=12), "qwen3:14b")

    def test_tanpa_gpu_pakai_ram(self):
        self.assertEqual(pick_model(ram=16, vram=0), "qwen3:4b")
        self.assertEqual(pick_model(ram=6, vram=0), "qwen3:1.7b")

    def test_ram_terlalu_kecil_tidak_ada_model(self):
        self.assertEqual(pick_model(ram=2, vram=0), "")

    def test_semua_model_punya_syarat_masuk_akal(self):
        for name, need_ram, need_vram in MODELS:
            self.assertTrue(name.startswith("qwen3:"))
            self.assertGreater(need_ram, 0)
            self.assertGreaterEqual(need_vram, 0)


if __name__ == "__main__":
    unittest.main()
