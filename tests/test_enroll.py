"""Uji gabung-ulang otomatis setelah install ulang: sidik jari + endpoint /enroll."""

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import server, store as store_mod, system  # noqa: E402


class FingerprintTest(unittest.TestCase):
    def test_stable_hex(self):
        fp = system.machine_fingerprint()
        self.assertTrue(fp and len(fp) == 24)
        self.assertEqual(fp, system.machine_fingerprint())  # cache -> konsisten
        int(fp, 16)  # hex valid


class StoreEnrollTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.s = store_mod.Store(self.d)

    def test_enroll_and_query(self):
        self.assertFalse(self.s.is_enrolled("aaa"))
        self.s.enroll_device("aaa", "PC-1")
        self.assertTrue(self.s.is_enrolled("aaa"))
        self.assertEqual([d["name"] for d in self.s.enrolled_list()], ["PC-1"])
        self.s.enroll_device("aaa", "PC-1-baru")  # update nama
        self.assertEqual(self.s.enrolled_list()[0]["name"], "PC-1-baru")

    def test_merge_keeps_newest(self):
        self.s.enroll_device("aaa", "lama")
        self.s.merge_enrolled([{"fp": "aaa", "name": "baru", "ts": 1e12},
                               {"fp": "bbb", "name": "PC-2", "ts": 5.0}])
        fps = {d["fp"]: d for d in self.s.enrolled_list()}
        self.assertEqual(set(fps), {"aaa", "bbb"})
        self.assertEqual(fps["aaa"]["name"], "baru")  # ts lebih baru menang
        self.s.merge_enrolled([{"fp": "bbb", "name": "kuno", "ts": 1.0}])
        self.assertEqual({d["fp"]: d["name"] for d in self.s.enrolled_list()}["bbb"], "PC-2")  # ts lama kalah


class InTailnetTest(unittest.TestCase):
    def test_range(self):
        for ip in ("100.64.0.1", "100.100.5.9", "100.127.255.255", "fd7a:aaaa::1"):
            self.assertTrue(server._in_tailnet(ip), ip)
        for ip in ("100.63.0.1", "100.128.0.1", "10.0.0.1", "8.8.8.8", "192.168.1.5", ""):
            self.assertFalse(server._in_tailnet(ip), ip)


class InLanTest(unittest.TestCase):
    def test_local_networks(self):
        for ip in ("192.168.1.5", "10.1.2.3", "172.16.0.9", "169.254.1.1", "fe80::1", "fd00::5"):
            self.assertTrue(server._in_lan(ip), ip)

    def test_public_networks(self):
        for ip in ("8.8.8.8", "1.1.1.1", "104.16.0.1", "2001:db8::1", "", "bukan-ip"):
            self.assertFalse(server._in_lan(ip), ip)


class Cfg(dict):
    def __init__(self, port, botdir=None):
        super().__init__(port=port, node_id="me", name="PC-INI",
                         ollama_url="http://127.0.0.1:11434", join_code="RAHASIA-GABUNG",
                         itsme_dir=botdir, _fingerprint="fpme")

    @property
    def token(self):
        return "tok"


class Cluster:
    id = "me"
    leader = "me"
    phase = "running"
    epoch = 1
    is_leader = True
    bot = True

    def alive(self):
        return {"me": {"id": "me", "name": "PC-INI", "addr": "127.0.0.1:0", "leader": "me"}}

    def _gossip_payload(self):
        return {}

    def learn(self, p):
        pass

    def rpc(self, *a, **k):
        return None


class Node:
    pass


class EnrollEndpointTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        # .env bot supaya itsme.ready() True & env_bundle mengembalikan kunci
        self.botdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.botdir, ignore_errors=True)
        with open(os.path.join(self.botdir, ".env"), "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN=123:abc\nOWNER_TELEGRAM_ID=42\nITSME_DATA_DIR=/x\n")
        n = Node()
        n.cfg = Cfg(0, self.botdir)
        n.cluster = Cluster()
        n.store = store_mod.Store(self.d)
        n.profile = {"model": "", "score": 1}
        self.store = n.store
        self.srv = server.make_server(n)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def post(self, path, obj, extra=None):
        headers = {"Content-Type": "application/json"}
        headers.update(extra or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(),
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())

    def test_proxy_cannot_smuggle_public_ip_into_enroll(self):
        """Dashboard meneruskan IP asli: permintaan dari internet ditolak, LAN tetap boleh.

        Tanpa ini penjaga /enroll selalu melihat 127.0.0.1 (peer = dashboard sendiri),
        jadi jalur publik bisa memanen token bot + kata sandi + kunci API.
        """
        self.store.enroll_device("fp-lama", "Laptop")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/enroll", {"fingerprint": "fp-lama", "name": "Laptop"},
                      {"X-Itsme-Client-IP": "8.8.8.8"})
        self.assertEqual(cm.exception.code, 403)
        code, res = self.post("/enroll", {"fingerprint": "fp-lama", "name": "Laptop"},
                              {"X-Itsme-Client-IP": "192.168.1.20"})
        self.assertEqual(code, 200)
        self.assertEqual(res["env"]["TELEGRAM_BOT_TOKEN"], "123:abc")

    def test_spoofed_header_ignored_for_non_local_peer(self):
        """Header hanya dipercaya dari peer lokal (dashboard), bukan dari internet."""
        hdr = {"X-Itsme-Client-IP": "127.0.0.1"}
        self.assertEqual(server.client_ip("8.8.8.8", hdr), "8.8.8.8")   # spoof ditolak
        self.assertEqual(server.client_ip("127.0.0.1", hdr), "127.0.0.1")  # dari dashboard
        self.assertEqual(server.client_ip("127.0.0.1", {}), "127.0.0.1")

    def test_unknown_fingerprint_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/enroll", {"fingerprint": "belum-terdaftar", "name": "X"})
        self.assertEqual(cm.exception.code, 404)

    def test_known_fingerprint_rejoins_with_code_and_env(self):
        self.store.enroll_device("fp-lama", "Laptop")        # dulu pernah terdaftar
        code, res = self.post("/enroll", {"fingerprint": "fp-lama", "name": "Laptop"})
        self.assertEqual(code, 200)
        self.assertEqual(res["join_code"], "RAHASIA-GABUNG")  # kode gabung dikembalikan
        self.assertEqual(res["env"]["TELEGRAM_BOT_TOKEN"], "123:abc")  # bot ikut pulih
        self.assertNotIn("ITSME_DATA_DIR", res["env"])        # kunci per-komputer dibuang

    def test_empty_fingerprint(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/enroll", {"fingerprint": "", "name": "X"})
        self.assertEqual(cm.exception.code, 400)

    def test_enrolled_sync_endpoint(self):
        self.store.enroll_device("fp1", "A")
        req = urllib.request.Request(self.base + "/enrolled",
                                     headers={"Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        self.assertIn("fp1", [d["fp"] for d in data["devices"]])


if __name__ == "__main__":
    unittest.main()
