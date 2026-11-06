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

    def test_hardlink_ke_rahasia_ditolak(self):
        """Hardlink bernama polos tidak bisa dibedakan realpath -> harus ditolak."""
        link = os.path.join(self.d, "foto.txt")
        try:
            os.link(os.path.join(self.d, ".env"), link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hardlink tidak didukung: {exc}")
        with self.assertRaises(PermissionError):
            remote.fs_read(link)
        with self.assertRaises(PermissionError):
            remote.fs_file(link)
        self.assertIn("error", remote.fs_write(link, b"x"))
        with open(os.path.join(self.d, ".env"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "TOKEN=rahasia")  # isi asli tidak tertimpa

    def test_symlink_ke_rahasia_ditolak(self):
        """Tautan bernama polos yang menunjuk .env tidak boleh lolos."""
        link = os.path.join(self.d, "catatan.txt")
        try:
            os.symlink(os.path.join(self.d, ".env"), link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink tidak didukung: {exc}")
        with self.assertRaises(PermissionError):
            remote.fs_read(link)
        with self.assertRaises(PermissionError):
            remote.fs_file(link)
        hasil = remote.fs_write(link, b"x")
        self.assertIn("error", hasil)

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

    def test_move_rel(self):
        self.assertEqual(self.inject([{"t": "move_rel", "dx": 5, "dy": -3}]), [0])
        # dx=dy=0 -> tidak ada yang dikirim (bukan gerak "ke (0,0)" seperti move absolut)
        self.assertEqual(self.inject([{"t": "move_rel", "dx": 0, "dy": 0}]), [])
        # input bukan angka diabaikan jadi 0, bukan error
        self.assertEqual(self.inject([{"t": "move_rel", "dx": "oops", "dy": 4}]), [0])

    def _moves(self, events, start=(100, 200), size=(1920, 1080)):
        """Piksel tujuan tiap gerak yang dikirim, dengan posisi awal & ukuran layar dipalsukan."""
        seen = []
        with mock.patch.object(remote, "_send", lambda seq: seen.extend(seq) or len(seq)), \
             mock.patch.object(remote, "_cursor_pos", return_value=start), \
             mock.patch.object(remote, "screen_size", return_value=size):
            remote.inject(events)
        sw, sh = size
        out = []
        for e in seen:
            self.assertTrue(e.u.mi.dwFlags & 0x8000, "move_rel harus disuntik ABSOLUT (bebas akselerasi)")
            out.append((e.u.mi.dx * sw // 65536, e.u.mi.dy * sh // 65536))  # cara Windows memetakan balik
        return out

    def test_move_rel_lands_exactly_relative_to_current_position(self):
        """Relatif terhadap kursor sekarang, tapi tepat piksel - tidak ikut akselerasi Windows."""
        self.assertEqual(self._moves([{"t": "move_rel", "dx": 5, "dy": -3}]), [(105, 197)])

    def test_move_rel_steps_accumulate_within_one_batch(self):
        evs = [{"t": "move_rel", "dx": 10, "dy": 0}, {"t": "move_rel", "dx": 10, "dy": 1}]
        self.assertEqual(self._moves(evs), [(110, 200), (120, 201)])

    def test_move_rel_clamps_extreme_values_and_screen_edges(self):
        """Nilai ekstrem (klien nakal/rusak) dijepit +-2000 lalu ke tepi layar."""
        self.assertEqual(self._moves([{"t": "move_rel", "dx": 99999, "dy": -99999}]), [(1919, 0)])

    def test_move_lands_exactly_on_the_pixel_the_client_means(self):
        """x,y klien = piksel / (lebar-1), persis nx/ny di cursor_info - mendarat tepat piksel itu."""
        for px, py in ((0, 0), (1, 1), (959, 539), (960, 540), (1500, 700), (1919, 1079)):
            nx, ny = round(px / 1919, 5), round(py / 1079, 5)  # seperti dibulatkan cursor_info
            self.assertEqual(self._moves([{"t": "move", "x": nx, "y": ny}]), [(px, py)])

    def test_move_clamps_out_of_range_and_garbage(self):
        self.assertEqual(self._moves([{"t": "move", "x": 7, "y": -3}]), [(1919, 0)])
        self.assertEqual(self._moves([{"t": "move", "x": "oops", "y": None}]), [(0, 0)])

    def test_click_with_position_moves_to_the_same_exact_pixel(self):
        seen = []
        with mock.patch.object(remote, "_send", lambda seq: seen.extend(seq) or len(seq)), \
             mock.patch.object(remote, "screen_size", return_value=(1920, 1080)):
            remote.inject([{"t": "click", "x": round(1500 / 1919, 5), "y": round(700 / 1079, 5)}])
        first = seen[0].u.mi
        self.assertTrue(first.dwFlags & 0x8000)
        self.assertEqual((first.dx * 1920 // 65536, first.dy * 1080 // 65536), (1500, 700))

    def test_scroll_vertical_horizontal_and_smooth_values(self):
        seen = []
        with mock.patch.object(remote, "_send", lambda seq: seen.extend(seq) or len(seq)):
            remote.inject([{"t": "scroll", "dy": -45, "dx": 120}])
        got = [(e.u.mi.dwFlags, e.u.mi.mouseData) for e in seen]
        # roda vertikal (0x0800) lalu horizontal (0x1000); negatif = dua-komplemen 32-bit
        self.assertEqual(got, [(0x0800, (-45) & 0xFFFFFFFF), (0x1000, 120)])

    def test_scroll_without_amount_sends_nothing_and_clamps_extremes(self):
        self.assertEqual(self.inject([{"t": "scroll"}]), [])
        self.assertEqual(self.inject([{"t": "scroll", "dy": "oops", "dx": None}]), [])
        seen = []
        with mock.patch.object(remote, "_send", lambda seq: seen.extend(seq) or len(seq)):
            remote.inject([{"t": "scroll", "dy": 10**9}])
        self.assertEqual(seen[0].u.mi.mouseData, 2400)

    def test_nan_infinity_and_garbage_do_not_break_the_batch(self):
        """json.loads Python menerima NaN dan 1e999: satu isian aneh tidak boleh menggagalkan klik di sebelahnya."""
        inf, nan = float("inf"), float("nan")
        got = self.inject([{"t": "move", "x": nan, "y": inf}, {"t": "scroll", "dy": inf, "dx": nan},
                           {"t": "move_rel", "dx": inf, "dy": -inf}, {"t": "click"}])
        self.assertEqual(got, [0, 0, 0])   # move (ke 0,0) + klik (tekan, lepas); scroll & move_rel NaN/inf diabaikan

    def test_cursor_on_another_monitor_is_reported_not_visible(self):
        """Gambar layar hanya layar utama: kursor di monitor lain tak boleh digambar menempel di tepi."""
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class CURSORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("hCursor", ctypes.c_void_p),
                        ("ptScreenPos", POINT)]

        def api(x, y):
            class U:
                def GetCursorInfo(self, ref):
                    ref._obj.flags, ref._obj.ptScreenPos.x, ref._obj.ptScreenPos.y = 1, x, y
                    return 1

                def GetSystemMetrics(self, i):
                    return 1920 if i == 0 else 1080
            return (ctypes, None, U(), CURSORINFO, {})

        for x, y, visible in ((100, 100, True), (1919, 1079, True), (2500, 100, False), (-300, 50, False)):
            with mock.patch.object(remote, "IS_WIN", True), mock.patch.object(remote, "_cursor_api", return_value=api(x, y)):
                info = remote.cursor_info()
            self.assertEqual((info["x"], info["y"], info["visible"]), (x, y, visible), (x, y))
            self.assertTrue(0.0 <= info["nx"] <= 1.0 and 0.0 <= info["ny"] <= 1.0)

    def test_cursor_info_reports_position_and_kind(self):
        info = remote.cursor_info()
        if info is None:
            self.skipTest("tidak ada sesi layar")
        for key in ("x", "y", "w", "h", "nx", "ny", "visible", "kind"):
            self.assertIn(key, info)
        self.assertTrue(0.0 <= info["nx"] <= 1.0 and 0.0 <= info["ny"] <= 1.0)
        self.assertIsInstance(info["kind"], str)


class AbsNormTest(unittest.TestCase):
    """Rumus piksel -> koordinat SendInput harus mendarat TEPAT di piksel yang diminta
    (Windows memetakan balik dengan floor(n * lebar / 65536)) - juga sudah diuji nyata."""

    def test_round_trip_is_exact_for_common_screens(self):
        for sw, sh in ((1920, 1080), (1366, 768), (2560, 1440), (3840, 2160), (1280, 1024)):
            for x in (0, 1, 2, sw // 3, sw // 2 + 1, sw - 2, sw - 1):
                nx, _ = remote._abs_norm(x, 0, sw, sh)
                self.assertLessEqual(nx, 65535)
                self.assertEqual(nx * sw // 65536, x, (sw, x))


class RemoteAccessIdleTest(unittest.TestCase):
    """'Dipakai' kini berarti sedang diakses lewat web, bukan ketikan lokal -
    lihat __main__.py (idle = remote.remote_idle_seconds() >= idle_minutes*60)."""

    def setUp(self):
        remote._last_remote_access = 0.0  # belum pernah diakses sejak modul dimuat

    def test_never_accessed_is_effectively_never_idle_free(self):
        self.assertGreater(remote.remote_idle_seconds(), 10**8)

    def test_touch_resets_to_near_zero(self):
        remote.touch_remote_access()
        self.assertLess(remote.remote_idle_seconds(), 1.0)

    def test_idle_grows_without_further_touches(self):
        remote.touch_remote_access()
        with mock.patch.object(remote.time, "time", return_value=remote._last_remote_access + 400):
            self.assertGreaterEqual(remote.remote_idle_seconds(), 400)


@unittest.skipUnless(IS_WIN, "shutdown.exe hanya Windows")
class PowerTest(unittest.TestCase):
    """power() hanya menjalankan shutdown.exe - jangan pernah memanggilnya sungguhan di tes."""

    def test_unknown_action_rejected(self):
        with mock.patch.object(remote.subprocess, "Popen") as popen:
            ok, msg = remote.power("hibernate")
        self.assertFalse(ok)
        self.assertIn("tidak dikenal", msg)
        popen.assert_not_called()

    def test_shutdown_calls_shutdown_exe_with_delay(self):
        with mock.patch.object(remote.subprocess, "Popen") as popen:
            ok, msg = remote.power("shutdown")
        self.assertTrue(ok)
        popen.assert_called_once_with(["shutdown", "/s", "/t", "5"])

    def test_restart_calls_shutdown_exe_with_restart_flag(self):
        with mock.patch.object(remote.subprocess, "Popen") as popen:
            ok, msg = remote.power("restart")
        self.assertTrue(ok)
        popen.assert_called_once_with(["shutdown", "/r", "/t", "5"])

    def test_popen_failure_reported_not_raised(self):
        with mock.patch.object(remote.subprocess, "Popen", side_effect=OSError("nope")):
            ok, msg = remote.power("shutdown")
        self.assertFalse(ok)
        self.assertIn("nope", msg)


@unittest.skipIf(IS_WIN, "jalur non-Windows")
class PowerNonWindowsTest(unittest.TestCase):
    def test_non_windows_reports_unsupported(self):
        ok, msg = remote.power("shutdown")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
