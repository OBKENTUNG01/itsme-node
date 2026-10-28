"""Uji Wake-on-LAN: bentuk paket ajaib dan pencarian MAC lewat store.seen (wol.py)."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import wol  # noqa: E402
from inode.store import Store  # noqa: E402


class NormalizeMacTest(unittest.TestCase):
    def test_accepts_common_separators(self):
        self.assertEqual(wol.normalize_mac("AA:BB:CC:DD:EE:FF"), "aabbccddeeff")
        self.assertEqual(wol.normalize_mac("aa-bb-cc-dd-ee-ff"), "aabbccddeeff")
        self.assertEqual(wol.normalize_mac("aabbccddeeff"), "aabbccddeeff")

    def test_rejects_garbage(self):
        self.assertEqual(wol.normalize_mac(""), "")
        self.assertEqual(wol.normalize_mac("bukan-mac"), "")
        self.assertEqual(wol.normalize_mac("aa:bb:cc"), "")  # terlalu pendek
        self.assertEqual(wol.normalize_mac(None), "")


class SendMagicPacketTest(unittest.TestCase):
    def test_packet_format_and_broadcast_flag(self):
        sent = {"opts": False, "calls": []}

        class FakeSocket:
            def setsockopt(self, *a):
                sent["opts"] = True

            def sendto(self, payload, addr):
                sent["calls"].append((payload, addr))

            def close(self):
                pass

        with mock.patch.object(wol.socket, "socket", return_value=FakeSocket()):
            ok = wol.send_magic_packet("AA:BB:CC:DD:EE:FF")
        self.assertTrue(ok)
        self.assertTrue(sent["opts"])  # SO_BROADCAST dipasang
        expected_payload = b"\xff" * 6 + b"\xaa\xbb\xcc\xdd\xee\xff" * 16
        self.assertEqual(len(sent["calls"]), 2)  # port 9 lalu 7 (lihat default ports=)
        for payload, addr in sent["calls"]:
            self.assertEqual(payload, expected_payload)
            self.assertEqual(addr[0], "255.255.255.255")
        self.assertEqual({a[1] for _, a in sent["calls"]}, {9, 7})

    def test_bad_mac_sends_nothing(self):
        with mock.patch.object(wol.socket, "socket") as sock:
            self.assertFalse(wol.send_magic_packet("bukan-mac"))
        sock.assert_not_called()

    def test_socket_error_fails_quietly(self):
        class FakeSocket:
            def setsockopt(self, *a):
                raise OSError("tidak didukung")

            def close(self):
                pass

        with mock.patch.object(wol.socket, "socket", return_value=FakeSocket()):
            self.assertFalse(wol.send_magic_packet("aabbccddeeff"))


class WakeByNameTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.store = Store(self.d)

    def test_unknown_name_fails_without_sending(self):
        with mock.patch.object(wol, "send_magic_packet") as send:
            ok, mac = wol.wake_by_name(self.store, "TidakAda")
        self.assertFalse(ok)
        self.assertEqual(mac, "")
        send.assert_not_called()

    def test_known_name_sends_last_seen_mac(self):
        self.store.remember_seen("PC-Ibu", "aabbccddeeff")
        with mock.patch.object(wol, "send_magic_packet", return_value=True) as send:
            ok, mac = wol.wake_by_name(self.store, "pc-ibu")  # tak peka besar/kecil
        self.assertTrue(ok)
        self.assertEqual(mac, "aabbccddeeff")
        send.assert_called_once_with("aabbccddeeff")

    def test_remembering_again_overwrites_old_mac(self):
        self.store.remember_seen("PC-Ibu", "111111111111")
        self.store.remember_seen("PC-Ibu", "222222222222")
        self.assertEqual(self.store.last_seen_mac("PC-Ibu"), "222222222222")

    def test_seen_list_reports_original_casing(self):
        self.store.remember_seen("Laptop-Budi", "aabbccddeeff")
        names = [r["name"] for r in self.store.seen_list()]
        self.assertIn("Laptop-Budi", names)


if __name__ == "__main__":
    unittest.main()
