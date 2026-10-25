"""Uji unggah berkas (paritas remote desktop: kirim berkas ke komputer tujuan)."""

import base64
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import remote  # noqa: E402


class FsWriteTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_writes_utf8_bytes(self):
        target = os.path.join(self.d, "sub", "catatan.txt")
        res = remote.fs_write(target, "halo dunia ✓".encode("utf-8"))
        self.assertTrue(res.get("ok"), res)
        with open(target, "rb") as f:
            self.assertEqual(f.read().decode("utf-8"), "halo dunia ✓")

    def test_empty_path_refused(self):
        self.assertIn("error", remote.fs_write("", b"x"))

    def test_secret_file_refused(self):
        """Jangan menulis .env / token / kunci lewat unggah jarak jauh."""
        for name in (".env", "id_rsa", "credentials.json"):
            res = remote.fs_write(os.path.join(self.d, name), b"x")
            self.assertIn("error", res, name)

    def test_size_cap(self):
        res = remote.fs_write(os.path.join(self.d, "besar.bin"), b"x" * 64, max_bytes=32)
        self.assertIn("error", res)
        self.assertFalse(os.path.exists(os.path.join(self.d, "besar.bin")))

    def test_base64_roundtrip(self):
        """Bentuk yang dikirim dashboard: base64 -> bytes -> berkas."""
        raw = bytes(range(256))
        path = os.path.join(self.d, "biner.dat")
        res = remote.fs_write(path, base64.b64decode(base64.b64encode(raw)))
        self.assertTrue(res.get("ok"), res)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), raw)


if __name__ == "__main__":
    unittest.main()
