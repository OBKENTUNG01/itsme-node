"""Uji Invites: kode sekali-pakai (bawaan) dan kode reusable (/pasang_tetap).

Tidak ada tes khusus untuk modul ini sebelumnya - hanya tersentuh tak langsung
lewat test_enroll.py/test_server.py (jalur sekali-pakai saja). Reusable adalah
fitur baru, jadi dites langsung di sini.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode.invites import MAX_REUSABLE_TTL, MAX_TTL, Invites  # noqa: E402


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class OneTimeInviteTest(unittest.TestCase):
    """Perilaku bawaan (reusable=False) harus persis seperti sebelumnya."""

    def setUp(self):
        self.clock = FakeClock()
        self.inv = Invites(clock=self.clock)

    def test_redeem_once_then_gone(self):
        self.inv.add("ABCD-1234-XYZQ", 300, {"K": "V"})
        self.assertEqual(self.inv.redeem("abcd-1234-xyzq"), {"K": "V"})  # tak peka besar/kecil
        self.assertIsNone(self.inv.redeem("ABCD-1234-XYZQ"))  # sudah hangus

    def test_expires_on_its_own(self):
        self.inv.add("ABCD-1234-XYZQ", 60, {})
        self.clock.t += 61
        self.assertIsNone(self.inv.redeem("ABCD-1234-XYZQ"))

    def test_wrong_code_rejected(self):
        self.inv.add("ABCD-1234-XYZQ", 300, {})
        self.assertIsNone(self.inv.redeem("SALAH-0000-0000"))

    def test_ttl_capped_at_max(self):
        self.inv.add("ABCD-1234-XYZQ", MAX_TTL * 10, {})
        self.clock.t += MAX_TTL + 1
        self.assertIsNone(self.inv.redeem("ABCD-1234-XYZQ"))  # dipotong ke MAX_TTL, bukan 10x-nya

    def test_too_short_code_rejected(self):
        with self.assertRaises(ValueError):
            self.inv.add("PENDEK", 300, {})


class ReusableInviteTest(unittest.TestCase):
    """reusable=True: dipakai berkali-kali untuk komputer berbeda, tetap
    kedaluwarsa sendiri (tidak selamanya), dan diganti saat /pasang_tetap baru."""

    def setUp(self):
        self.clock = FakeClock()
        self.inv = Invites(clock=self.clock)

    def test_redeemed_many_times_without_expiring(self):
        self.inv.add("TETAP-0001-AAAA", 3600, {"K": "V"}, reusable=True)
        for _ in range(5):  # lima "komputer" menukar kode yang sama
            self.assertEqual(self.inv.redeem("TETAP-0001-AAAA"), {"K": "V"})

    def test_still_expires_eventually(self):
        self.inv.add("TETAP-0001-AAAA", 60, {}, reusable=True)
        self.clock.t += 61
        self.assertIsNone(self.inv.redeem("TETAP-0001-AAAA"))

    def test_ttl_capped_at_reusable_max_not_onetime_max(self):
        # Minta lebih lama dari MAX_TTL (1 jam) - untuk reusable harus tetap
        # berlaku (dipotong ke MAX_REUSABLE_TTL, bukan MAX_TTL yang jauh lebih pendek).
        self.inv.add("TETAP-0001-AAAA", MAX_TTL * 2, {}, reusable=True)
        self.clock.t += MAX_TTL + 10  # lewat MAX_TTL biasa, tapi belum lewat MAX_REUSABLE_TTL
        self.assertIsNotNone(self.inv.redeem("TETAP-0001-AAAA"))

    def test_reusable_ttl_itself_is_capped(self):
        self.inv.add("TETAP-0001-AAAA", MAX_REUSABLE_TTL * 10, {}, reusable=True)
        self.clock.t += MAX_REUSABLE_TTL + 1
        self.assertIsNone(self.inv.redeem("TETAP-0001-AAAA"))

    def test_new_reusable_code_replaces_old_one(self):
        self.inv.add("LAMA-0001-AAAA", 3600, {"v": "1"}, reusable=True)
        self.inv.add("BARU-0002-BBBB", 3600, {"v": "2"}, reusable=True)
        self.assertIsNone(self.inv.redeem("LAMA-0001-AAAA"))  # kode lama langsung mati
        self.assertEqual(self.inv.redeem("BARU-0002-BBBB"), {"v": "2"})
        self.assertEqual(self.inv.redeem("BARU-0002-BBBB"), {"v": "2"})  # yang baru tetap reusable

    def test_replacing_reusable_does_not_evict_onetime_codes(self):
        self.inv.add("SEKALI-01-AAAA", 3600, {"ot": "1"})
        self.inv.add("TETAP-01-AAAA", 3600, {}, reusable=True)
        self.inv.add("TETAP-02-BBBB", 3600, {}, reusable=True)  # ganti kode tetap
        self.assertEqual(self.inv.redeem("SEKALI-01-AAAA"), {"ot": "1"})  # tak ikut terhapus


class GuessLimitTest(unittest.TestCase):
    def test_too_many_wrong_guesses_blocks_further_tries(self):
        inv = Invites(clock=FakeClock())
        inv.add("ABCD-1234-XYZQ", 300, {})
        with self.assertRaises(PermissionError):
            for _ in range(31):
                inv.redeem("SALAH-0000-0000")


if __name__ == "__main__":
    unittest.main()
