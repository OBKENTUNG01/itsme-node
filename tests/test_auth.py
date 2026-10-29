"""Uji tanda tangan permintaan antar-node (inode/auth.py)."""

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import auth  # noqa: E402


class Cfg(dict):
    def __init__(self, join_code):
        super().__init__(join_code=join_code)

    @property
    def token(self):
        return hashlib.sha256(("itsme-node:" + self["join_code"]).encode()).hexdigest()


def _headers(d):
    return dict(d)


class SignVerifyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Cfg("kode-rahasia-bersama")
        self.v = auth.Verifier()

    def _signed(self, method="POST", target="/gossip", body=b'{"a":1}'):
        return auth.sign_headers(self.cfg, method, target, body)

    def test_roundtrip(self):
        h = self._signed()
        self.assertTrue(self.v.check(self.cfg, "POST", "/gossip", b'{"a":1}', _headers(h)))

    def test_body_path_method_tampering_rejected(self):
        for method, target, body in (("POST", "/gossip", b'{"a":2}'),
                                     ("POST", "/status", b'{"a":1}'),
                                     ("GET", "/gossip", b'{"a":1}')):
            h = self._signed()
            self.assertFalse(self.v.check(self.cfg, method, target, body, _headers(h)),
                             (method, target, body))

    def test_replay_rejected(self):
        h = _headers(self._signed())
        self.assertTrue(self.v.check(self.cfg, "POST", "/gossip", b'{"a":1}', h))
        self.assertFalse(self.v.check(self.cfg, "POST", "/gossip", b'{"a":1}', h))

    def test_clock_skew_rejected(self):
        for shift in (-120, 120):
            with mock.patch.object(auth.time, "time", return_value=time.time() + shift):
                h = self._signed()  # tanda tangan sah, tetapi dibuat dengan jam yang melenceng
            self.assertFalse(self.v.check(self.cfg, "POST", "/gossip", b'{"a":1}', _headers(h)))

    def test_other_mesh_rejected(self):
        h = auth.sign_headers(Cfg("kode-lain"), "POST", "/gossip", b"")
        self.assertFalse(self.v.check(self.cfg, "POST", "/gossip", b"", _headers(h)))

    def test_missing_or_garbage_headers_rejected(self):
        self.assertFalse(self.v.check(self.cfg, "GET", "/status", b"", _headers({})))
        self.assertFalse(self.v.check(self.cfg, "GET", "/status", b"",
                                      _headers({"X-Itsme-Ts": "bukan-angka", "X-Itsme-Nonce": "a", "X-Itsme-Sig": "b"})))

    def test_bearer_token_cannot_forge_signature(self):
        # Token Bearer (yang bocor lewat node lama) tidak boleh cukup untuk memalsukan
        # tanda tangan: kuncinya diturunkan dari kode gabung, bukan dari token.
        ts, nonce = str(int(time.time())), "ab" * 8
        msg = auth._message("GET", "/status", ts, nonce, b"")
        forged = hmac.new(hashlib.sha256(("itsme-sign:" + self.cfg.token).encode()).digest(),
                          msg, hashlib.sha256).hexdigest()
        h = {"X-Itsme-Ts": ts, "X-Itsme-Nonce": nonce, "X-Itsme-Sig": forged}
        self.assertFalse(self.v.check(self.cfg, "GET", "/status", b"", _headers(h)))


class _Node:
    """Server HTTP kecil. mode: "new" (tanda tangan ATAU Bearer), "strict" (hanya
    tanda tangan), "old" (hanya Bearer, seperti node versi lama)."""

    def __init__(self, cfg, mode):
        self.cfg, self.mode, self.seen = cfg, mode, []
        verifier = auth.Verifier()
        node = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                node.seen.append("bearer" if self.headers.get("Authorization") else "sig")
                ok = False
                if node.mode in ("new", "strict"):
                    ok = verifier.check(cfg, self.command, self.path, body, self.headers)
                if not ok and node.mode in ("new", "old"):
                    got = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    ok = hmac.compare_digest(got, cfg.token)
                out = json.dumps({"ok": ok, "echo": body.decode()}).encode()
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _go

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addr = f"127.0.0.1:{self.srv.server_address[1]}"

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class HttpRequestTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Cfg("kode-rahasia-bersama")
        self.legacy = {}

    def _node(self, mode, cfg=None):
        n = _Node(cfg or self.cfg, mode)
        self.addCleanup(n.stop)
        return n

    def _call(self, node, method="POST", target="/gossip?x=1", body=b'{"k":"v"}'):
        return auth.http_request(self.cfg, node.addr, method, target, body, timeout=5,
                                 headers={"Content-Type": "application/json"}, legacy=self.legacy)

    def test_new_node_gets_signature_only(self):
        n = self._node("new")
        code, _, data, _ = self._call(n)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(data)["echo"], '{"k":"v"}')
        self.assertEqual(n.seen, ["sig"])      # token tidak pernah dikirim
        self.assertEqual(self.legacy, {})

    def test_get_with_query_is_signed_too(self):
        n = self._node("strict")
        code, *_ = self._call(n, method="GET", target="/status?a=1&b=2", body=None)
        self.assertEqual(code, 200)
        self.assertEqual(n.seen, ["sig"])

    def test_old_node_falls_back_to_bearer_then_remembers(self):
        n = self._node("old")
        self.assertEqual(self._call(n)[0], 200)
        self.assertEqual(n.seen, ["sig", "bearer"])           # coba tanda tangan, lalu Bearer
        self.assertGreater(self.legacy[n.addr], time.time())
        n.seen.clear()
        self.assertEqual(self._call(n)[0], 200)
        self.assertEqual(n.seen, ["bearer"])                  # langsung Bearer, tak mencoba lagi

    def test_strict_node_rejects_a_bearer_only_client(self):
        n = self._node("strict")
        self.legacy[n.addr] = time.time() + 600               # klien terpaksa Bearer
        self.assertEqual(self._call(n)[0], 401)

    def test_foreign_mesh_is_rejected_both_ways(self):
        n = self._node("new", cfg=Cfg("kode-orang-lain"))
        self.assertEqual(self._call(n)[0], 401)
        self.assertEqual(n.seen, ["sig", "bearer"])

    def test_unreachable_raises_oserror(self):
        with self.assertRaises(OSError):
            auth.http_request(self.cfg, "127.0.0.1:1", "GET", "/status", None, timeout=1,
                              legacy=self.legacy)


if __name__ == "__main__":
    unittest.main()
