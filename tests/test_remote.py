"""Uji akses jarak jauh node: PNG, keamanan berkas, dan terjemahan input."""

import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import remote  # noqa: E402

IS_WIN = sys.platform == "win32"


class PngTest(unittest.TestCase):
    def test_valid_png(self):
        w, h = 4, 3
        rgba = bytes([10, 20, 30, 255]) * (w * h)
        png = remote._png(w, h, rgba)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        i, chunks = 8, []
        while i < len(png):
            ln = struct.unpack(">I", png[i:i + 4])[0]
            tag, data = png[i + 4:i + 8], png[i + 8:i + 8 + ln]
            crc = struct.unpack(">I", png[i + 8 + ln:i + 12 + ln])[0]
            self.assertEqual(crc, zlib.crc32(tag + data) & 0xffffffff)
            chunks.append(tag.decode())
            i += 12 + ln
        self.assertEqual(chunks, ["IHDR", "IDAT", "IEND"])
        iw, ih, bitd, ctype = struct.unpack(">IIBB", png[16:26])
        self.assertEqual((iw, ih, bitd, ctype), (w, h, 8, 6))  # RGBA 8-bit


class SecretTest(unittest.TestCase):
    def test_patterns(self):
        for p in (".env", ".env.local", "id_rsa", "server.key", "cert.pem",
                  "cookies.sqlite", "Login Data", "secret.txt", "api_token.json",
                  r"C:\Users\x\.ssh\config", r"C:\Users\x\.claude\x.json", "drive_token.json"):
            self.assertTrue(remote.is_secret(p), p)
        for p in ("notes.txt", "report.pdf", "foto.png", "main.py", "data.csv", "readme.md"):
            self.assertFalse(remote.is_secret(p), p)


class FsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))
        with open(os.path.join(self.d, "hello.txt"), "w", encoding="utf-8") as f:
            f.write("halo dunia")
        with open(os.path.join(self.d, ".env"), "w", encoding="utf-8") as f:
            f.write("TOKEN=rahasia")
        os.mkdir(os.path.join(self.d, "sub"))

    def test_list_marks_secret_and_sorts_dirs_first(self):
        d = remote.fs_list(self.d)
        names = [e["name"] for e in d["entries"]]
        self.assertEqual(names[0], "sub")  # folder dulu
        env = next(e for e in d["entries"] if e["name"] == ".env")
        self.assertTrue(env["secret"])
        self.assertTrue(d["parent"])

    def test_list_root_has_drive_or_home(self):
        d = remote.fs_list("")
        self.assertTrue(d["entries"])
        self.assertTrue(all(e["dir"] for e in d["entries"]))

    def test_read_text(self):
        d = remote.fs_read(os.path.join(self.d, "hello.txt"))
        self.assertEqual(d["text"], "halo dunia")
        self.assertFalse(d["binary"])

    def test_read_secret_refused(self):
        with self.assertRaises(PermissionError):
            remote.fs_read(os.path.join(self.d, ".env"))

    def test_download_secret_refused(self):
        with self.assertRaises(PermissionError):
            remote.fs_file(os.path.join(self.d, ".env"))

    def test_download_size_limit(self):
        big = os.path.join(self.d, "big.bin")
        with open(big, "wb") as f:
            f.write(b"x" * 2048)
        with self.assertRaises(ValueError):
            remote.fs_file(big, max_bytes=1024)
        name, data = remote.fs_file(big, max_bytes=4096)
        self.assertEqual((name, len(data)), ("big.bin", 2048))

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            remote.fs_read(os.path.join(self.d, "nope.txt"))


@unittest.skipUnless(IS_WIN, "input & layar hanya Windows")
class InputTest(unittest.TestCase):
    def inject(self, events):
        seen = []
        with mock.patch.object(remote, "_send", lambda seq: seen.extend(seq) or len(seq)):
            remote.inject(events)
        return [e.type for e in seen]  # 0 = mouse, 1 = keyboard

    def test_move_and_click(self):
        self.assertEqual(self.inject([{"t": "move", "x": 0.5, "y": 0.5}]), [0])
        # klik dengan posisi = pindah + tekan + lepas
        self.assertEqual(self.inject([{"t": "click", "button": "left", "x": 0.2, "y": 0.3}]), [0, 0, 0])
        # klik tanpa posisi = tekan + lepas
        self.assertEqual(self.inject([{"t": "click"}]), [0, 0])
        self.assertEqual(self.inject([{"t": "dblclick", "x": 0.1, "y": 0.1}]), [0, 0, 0, 0, 0])
        self.assertEqual(self.inject([{"t": "scroll", "dy": 120}]), [0])

    def test_text_and_keys(self):
        self.assertEqual(self.inject([{"t": "text", "text": "hi"}]), [1, 1, 1, 1])  # 2 huruf x (down,up)
        # ctrl+a = ctrl down, a down, a up, ctrl up
        self.assertEqual(self.inject([{"t": "key", "key": "a", "ctrl": True}]), [1, 1, 1, 1])
        self.assertEqual(self.inject([{"t": "key", "key": "Enter"}]), [1, 1])
        self.assertEqual(self.inject([{"t": "key", "key": "unknownnn"}]), [])  # diabaikan

    def test_capture_returns_png(self):
        png = remote.capture_png(max_w=640)
        if png is not None:  # butuh sesi layar
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            iw = struct.unpack(">I", png[16:20])[0]
            self.assertLessEqual(iw, 640)

    def test_inject_empty_is_noop(self):
        with mock.patch.object(remote, "_send", side_effect=AssertionError("tak boleh kirim")):
            self.assertEqual(remote.inject([]), 0)


if __name__ == "__main__":
    unittest.main()
