"""Uji mode jembatan: komputer menyambung keluar ke dashboard (tanpa Tailscale)."""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import bridge, remote, wol  # noqa: E402


class FakeStore:
    """Hanya apa yang dipakai wol.wake_by_name (lihat test_wol_* di bawah)."""

    def __init__(self, macs=None):
        self.macs = macs or {}

    def last_seen_mac(self, name):
        return self.macs.get((name or "").strip().lower(), "")


class FakeDashboard:
    """Server kecil yang meniru /api/hp/poll|result|frame dari dashboard."""

    def __init__(self):
        self.lock = threading.Lock()
        self.queue = []
        self.results = {}   # id -> value (dict)
        self.frames = {}    # id -> bytes
        self.last_poll = None
        dash = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _json(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                path = urlsplit(self.path).path
                if path == "/api/hp/frame":
                    q = parse_qs(urlsplit(self.path).query)
                    body = self._body()
                    with dash.lock:
                        dash.frames[(q.get("id") or [""])[0]] = body
                    return self._json({"ok": True})
                data = json.loads(self._body() or b"{}")
                if path == "/api/hp/poll":
                    with dash.lock:
                        dash.last_poll = data
                        cmd = dash.queue.pop(0) if dash.queue else {}
                    return self._json(cmd)
                if path == "/api/hp/result":
                    with dash.lock:
                        dash.results[data.get("id")] = data.get("value")
                    return self._json({"ok": True})
                return self._json({"error": "?"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def enqueue(self, cmd):
        with self.lock:
            self.queue.append(cmd)

    def wait_result(self, cid, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if cid in self.results:
                    return self.results[cid]
            time.sleep(0.05)
        return None

    def wait_frame(self, cid, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if cid in self.frames:
                    return self.frames[cid]
            time.sleep(0.05)
        return None

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.dash = FakeDashboard()
        self.addCleanup(self.dash.stop)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        with open(os.path.join(self.tmp, "a.txt"), "w", encoding="utf-8") as f:
            f.write("halo")
        self.cfg = {"bridge_url": self.dash.base, "bridge_token": "T", "name": "PC-Uji"}
        self.stop = threading.Event()
        self.store = FakeStore({"pc-ibu": "aabbccddeeff"})
        self.t = threading.Thread(target=bridge.run, args=(self.cfg, self.stop, self.store), daemon=True)
        self.t.start()
        self.addCleanup(lambda: (self.stop.set(), self.t.join(timeout=3)))

    def _registered(self, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if self.dash.last_poll:
                return True
            time.sleep(0.05)
        return False

    def test_registers_as_computer(self):
        self.assertTrue(self._registered())
        self.assertEqual(self.dash.last_poll["name"], "PC-Uji")
        self.assertEqual(self.dash.last_poll["kind"], "computer")
        self.assertIn("fs", self.dash.last_poll["caps"])

    def test_fs_list_bridged(self):
        self.assertTrue(self._registered())
        self.dash.enqueue({"id": "c1", "type": "fs_list", "path": self.tmp})
        res = self.dash.wait_result("c1")
        self.assertIsNotNone(res)
        self.assertIn("a.txt", [e["name"] for e in res["entries"]])

    def test_input_bridged(self):
        self.assertTrue(self._registered())
        with mock.patch.object(remote, "inject", return_value=1) as inj:
            self.dash.enqueue({"id": "c2", "type": "input", "events": [{"t": "move", "x": 0.1, "y": 0.1}]})
            res = self.dash.wait_result("c2")
        self.assertEqual(res, {"ok": True})
        self.assertEqual(inj.call_args[0][0], [{"t": "move", "x": 0.1, "y": 0.1}])

    def test_screen_bridged_as_png(self):
        self.assertTrue(self._registered())
        fake = b"\x89PNG\r\n\x1a\nFAKE"
        with mock.patch.object(remote, "capture_png", return_value=fake):
            self.dash.enqueue({"id": "c3", "type": "screen", "w": "800"})
            frame = self.dash.wait_frame("c3")
        self.assertEqual(frame, fake)

    def test_wol_bridged_known_mac(self):
        # Hub minta komputer jembatan ini membangunkan "PC-Ibu", yang MAC-nya pernah
        # terlihat langsung di LAN node ini (lihat FakeStore di setUp).
        self.assertTrue(self._registered())
        with mock.patch.object(wol, "send_magic_packet", return_value=True) as send:
            self.dash.enqueue({"id": "c4", "type": "wol", "target": "PC-Ibu"})
            res = self.dash.wait_result("c4")
        self.assertEqual(res, {"ok": True, "mac": "aabbccddeeff"})
        send.assert_called_once_with("aabbccddeeff")

    def test_wol_bridged_unknown_mac(self):
        self.assertTrue(self._registered())
        with mock.patch.object(wol, "send_magic_packet") as send:
            self.dash.enqueue({"id": "c5", "type": "wol", "target": "TidakAda"})
            res = self.dash.wait_result("c5")
        self.assertFalse(res["ok"])
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
