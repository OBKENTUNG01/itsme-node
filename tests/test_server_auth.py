"""Uji server.check_auth: siapa yang boleh memanggil node ini dari komputer lain.

Dari 127.0.0.1 selalu lolos, jadi logikanya dipisah menjadi fungsi murni supaya
bisa diuji tanpa alamat non-loopback."""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import auth, server  # noqa: E402


class Cfg(dict):
    def __init__(self, code, **over):
        super().__init__(join_code=code, **over)

    @property
    def token(self):
        return hashlib.sha256(("itsme-node:" + self["join_code"]).encode()).hexdigest()


def _bearer(cfg):
    return {"Authorization": "Bearer " + cfg.token}


class CheckAuthTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Cfg("kode-bersama")
        self.v = auth.Verifier()

    def _call(self, headers, peer="192.168.1.44", method="POST", target="/gossip", raw=b"{}", cfg=None):
        return server.check_auth(cfg or self.cfg, self.v, peer, method, target, raw, headers)

    def test_local_always_allowed(self):
        self.assertTrue(self._call({}, peer="127.0.0.1"))
        self.assertTrue(self._call({}, peer="::1"))

    def test_remote_without_credentials_denied(self):
        self.assertFalse(self._call({}))

    def test_remote_signed_request_allowed(self):
        h = auth.sign_headers(self.cfg, "POST", "/gossip", b"{}")
        self.assertTrue(self._call(h))

    def test_signed_request_cannot_be_replayed(self):
        h = auth.sign_headers(self.cfg, "POST", "/gossip", b"{}")
        self.assertTrue(self._call(h))
        self.assertFalse(self._call(h))

    def test_signature_is_bound_to_path_and_body(self):
        h = auth.sign_headers(self.cfg, "POST", "/gossip", b"{}")
        self.assertFalse(self._call(h, target="/input"))
        h = auth.sign_headers(self.cfg, "POST", "/gossip", b"{}")
        self.assertFalse(self._call(h, raw=b'{"x":1}'))

    def test_other_mesh_denied(self):
        other = Cfg("kode-orang-lain")
        self.assertFalse(self._call(auth.sign_headers(other, "POST", "/gossip", b"{}")))
        self.assertFalse(self._call(_bearer(other)))

    def test_legacy_bearer_accepted_by_default(self):
        self.assertTrue(self._call(_bearer(self.cfg)))

    def test_legacy_bearer_refused_once_switched_off(self):
        cfg = Cfg("kode-bersama", accept_legacy_bearer=False)
        self.assertFalse(self._call(_bearer(cfg), cfg=cfg))
        # tanda tangan tetap diterima
        self.assertTrue(self._call(auth.sign_headers(cfg, "POST", "/gossip", b"{}"), cfg=cfg))

    def test_non_ascii_authorization_header_does_not_crash(self):
        self.assertFalse(self._call({"Authorization": "Bearer tést"}))


if __name__ == "__main__":
    unittest.main()
