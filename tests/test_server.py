"""Uji rute HTTP node: viewer, layar, berkas, dan proxy ke node lain."""

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import server  # noqa: E402

IS_WIN = sys.platform == "win32"
PORTS = {"A": 7401, "B": 7402}


class Cfg(dict):
    def __init__(self, port, node_id, name):
        super().__init__(port=port, node_id=node_id, name=name,
                         ollama_url="http://127.0.0.1:11434")

    @property
    def token(self):
        return "shared-token"


class Cluster:
    def __init__(self, node_id):
        self.id = node_id
        self.leader = node_id
        self.phase = "running"
        self.epoch = 1
        self.is_leader = True
        self.bot = False

    def alive(self):
        return {nid: {"id": nid, "name": nid, "addr": f"127.0.0.1:{PORTS[nid]}",
                      "working": False, "model": "", "score": 1, "stats": {}, "leader": nid == self.id}
                for nid in PORTS}

    def _gossip_payload(self):
        return {}

    def learn(self, p):
        pass

    def rpc(self, addr, path, payload=None, timeout=5):
        body = json.dumps(payload or {}).encode()
        req = urllib.request.Request(f"http://{addr}{path}", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer shared-token"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except OSError:
            return None


class Store:
    def search(self, *a, **k):
        return []

    def recent(self, *a, **k):
        return []

    def stats(self):
        return {}


class Node:
    pass


def make(node_id):
    n = Node()
    n.cfg = Cfg(PORTS[node_id], node_id, node_id)
    n.cluster = Cluster(node_id)
    n.store = Store()
    n.profile = {"model": "", "score": 1}
    return server.make_server(n), n


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srvA, _ = make("A")
        cls.srvB, _ = make("B")
        cls.tmp = tempfile.mkdtemp()
        with open(os.path.join(cls.tmp, "note.txt"), "w", encoding="utf-8") as f:
            f.write("isi B")
        with open(os.path.join(cls.tmp, ".env"), "w", encoding="utf-8") as f:
            f.write("K=rahasia")

    @classmethod
    def tearDownClass(cls):
        for s in (cls.srvA, cls.srvB):
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def post(self, port, path, obj):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())

    def test_viewer_page(self):
        c, ct, b = self.get(PORTS["A"], "/")
        self.assertEqual(c, 200)
        self.assertIn("text/html", ct)
        self.assertIn(b"It's Me", b)

    def test_fs_list_local(self):
        c, ct, b = self.get(PORTS["A"], "/fs/list?path=" + urllib.request.quote(self.tmp))
        d = json.loads(b)
        self.assertIn("note.txt", [e["name"] for e in d["entries"]])

    def test_self_served_request_touches_remote_access(self):
        # "Dipakai" kini = sedang diakses lewat web (lihat __main__.py); server.py
        # harus menandainya tiap kali melayani permintaan jarak jauh untuk DIRINYA
        # SENDIRI, supaya riset latar dijeda selama itu - lihat inode/remote.py.
        from inode import remote

        with mock.patch.object(remote, "touch_remote_access") as touch:
            self.get(PORTS["A"], "/fs/list?path=" + urllib.request.quote(self.tmp))
        touch.assert_called_once()

    def test_fs_list_proxy_to_B(self):
        # A diminta berkas milik B -> diteruskan ke B.
        c, ct, b = self.get(PORTS["A"], "/fs/list?node=B&path=" + urllib.request.quote(self.tmp))
        self.assertEqual(c, 200)
        self.assertIn("note.txt", [e["name"] for e in json.loads(b)["entries"]])

    def test_fs_read_proxy_secret_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(PORTS["A"], "/fs/read?node=B&path=" + urllib.request.quote(os.path.join(self.tmp, ".env")))
        self.assertEqual(cm.exception.code, 403)

    def test_download_proxy(self):
        c, ct, b = self.get(PORTS["A"], "/fs/download?node=B&path=" + urllib.request.quote(os.path.join(self.tmp, "note.txt")))
        self.assertEqual((c, b), (200, b"isi B"))

    def test_unknown_node_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(PORTS["A"], "/fs/list?node=ZZZ&path=x")
        self.assertEqual(cm.exception.code, 404)

    def test_input_proxy_noop(self):
        c, res = self.post(PORTS["A"], "/input", {"node": "B", "events": []})
        self.assertEqual(c, 200)
        self.assertIn("sent", res)

    @unittest.skipUnless(IS_WIN, "layar hanya Windows")
    def test_screen_local_png(self):
        c, ct, b = self.get(PORTS["A"], "/screen?w=640")
        if c == 200:
            self.assertIn("image/png", ct)
            self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")
            self.assertLessEqual(struct.unpack(">I", b[16:20])[0], 640)

    @unittest.skipUnless(IS_WIN, "layar hanya Windows")
    def test_screen_proxy_png(self):
        c, ct, b = self.get(PORTS["A"], "/screen?node=B&w=640")
        if c == 200:
            self.assertIn("image/png", ct)
            self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
