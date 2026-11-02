"""Uji rute /think antar-komputer: beberapa node SUNGGUHAN (server HTTP di loopback, tanda tangan
HMAC asli) masing-masing dengan Ollama palsunya. Yang dibuktikan: bot cukup bicara dengan node di
komputernya sendiri, permintaan diteruskan ke komputer lain, dan SEMUA berhenti berpikir saat
pemimpin mati (fase pemilihan) lalu pulih."""

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_ollama import FakeOllama  # noqa: E402
from inode import auth, llm, server as server_mod  # noqa: E402
from inode.cluster import Cluster  # noqa: E402
from inode.server import make_server  # noqa: E402
from inode.store import Store  # noqa: E402
from inode.thinker import Thinker  # noqa: E402

MSG = [{"role": "user", "content": "halo"}]


class _Cfg(dict):
    @property
    def token(self):
        return "token-" + self.get("join_code", "")


class _Node:
    port: int


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Base(unittest.TestCase):
    def setUp(self):
        # Loopback dianggap "komputer ini sendiri" dan otomatis lolos; anggap sebagai jaringan luar
        # supaya jalur tanda tangan SUNGGUHAN ikut teruji.
        p = mock.patch.object(server_mod, "LOCAL", set())
        p.start()
        self.addCleanup(p.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        llm.configure(d)
        self.addCleanup(llm.configure, None)
        self.nodes = {}

    def spawn(self, name, *, join_code="SAMA", model="m1", reply=None, **extra):
        port = _free_port()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        fake = FakeOllama(models=[model] if model else [])
        if reply:
            fake.reply = reply
        self.addCleanup(fake.close)
        cfg = _Cfg(node_id=name, name=name, advertise=f"127.0.0.1:{port}", seeds=[], leader_priority=0, port=port,
                   join_code=join_code, _fingerprint="fp-" + name, _mac="aabbccddee01", tailscale_discovery=False,
                   lan_discovery=False, ollama_url=fake.url, think=True)
        cfg.update(extra)
        store = Store(d)
        profile = {"model": model, "score": 10, "tps": 5.0, "ram": 16.0, "vram": 0.0}
        cluster = Cluster(cfg, profile, store, bot=True)
        node = _Node()
        node.cfg, node.cluster, node.store, node.profile = cfg, cluster, store, profile
        node.thinker = Thinker(cfg, profile, cluster)
        node.thinker.up = True
        cluster.thinker = node.thinker
        srv = make_server(node)
        self.addCleanup(srv.server_close)   # addCleanup berjalan terbalik: shutdown() lebih dulu
        self.addCleanup(srv.shutdown)
        cluster._become_leader(cluster.alive())
        node.fake, node.port = fake, port
        self.nodes[name] = node
        return node

    def know(self, a, b):
        """Beri tahu node a bahwa node b hidup (pengganti gosip)."""
        a.cluster.peers[b.cluster.id] = {"state": b.cluster.state(), "seen": time.time()}

    def post(self, node, path, payload, cfg=None, timeout=20):
        body = json.dumps(payload).encode()
        code, _, data, _ = auth.http_request(cfg if cfg is not None else node.cfg, f"127.0.0.1:{node.port}", "POST", path,
                                             body, timeout, headers={"Content-Type": "application/json"})
        return code, (json.loads(data) if data else None)

    def get(self, node, path):
        code, _, data, _ = auth.http_request(node.cfg, f"127.0.0.1:{node.port}", "GET", path, None, 10)
        return code, json.loads(data)


class ThinkRouteTest(_Base):
    def test_berpikir_di_komputer_ini(self):
        a = self.spawn("A", reply="dari A")
        code, res = self.post(a, "/think", {"messages": MSG, "think": False})
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertEqual((res["name"], res["message"]["content"]), ("A", "dari A"))
        self.assertEqual(len(a.fake.posts("/api/chat")), 1)

    def test_body_berpikir_terlalu_besar_ditolak_sebelum_dibaca(self):
        a = self.spawn("A")
        size = 16 * 1024 * 1024 + 1
        with socket.create_connection(("127.0.0.1", a.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall((f"POST /think HTTP/1.1\r\nHost: local\r\nContent-Length: {size}\r\n"
                          "Connection: close\r\n\r\n").encode())
            sock.shutdown(socket.SHUT_WR)
            response = sock.recv(1024)
        self.assertRegex(response.split(b"\r\n", 1)[0], rb"HTTP/1\.[01] 413")

    def test_diteruskan_ke_komputer_lain(self):
        a, b = self.spawn("A", reply="dari A"), self.spawn("B", reply="dari B")
        self.know(a, b)
        code, res = self.post(a, "/think", {"messages": MSG, "node": "B"})
        self.assertEqual(code, 200)
        self.assertEqual((res["ok"], res["name"], res["message"]["content"]), (True, "B", "dari B"))
        self.assertEqual(len(b.fake.posts("/api/chat")), 1)
        self.assertEqual(a.fake.posts("/api/chat"), [])      # A hanya perantara
        self.assertNotIn("node", b.fake.posts("/api/chat")[0])

    def test_dua_arah(self):
        a, b = self.spawn("A", reply="dari A"), self.spawn("B", reply="dari B")
        self.know(a, b)
        self.know(b, a)
        self.assertEqual(self.post(b, "/think", {"messages": MSG, "node": "A"})[1]["name"], "A")
        self.assertEqual(self.post(a, "/think", {"messages": MSG, "node": "B"})[1]["name"], "B")

    def test_node_sendiri_disebut_dengan_id(self):
        a = self.spawn("A")
        code, res = self.post(a, "/think", {"messages": MSG, "node": "A"})
        self.assertEqual((code, res["ok"]), (200, True))

    def test_komputer_tidak_dikenal(self):
        a = self.spawn("A")
        code, res = self.post(a, "/think", {"messages": MSG, "node": "zzz"})
        self.assertEqual((code, res["ok"], res["kind"]), (200, False, "gone"))
        self.assertEqual(a.fake.posts("/api/chat"), [])

    def test_komputer_tujuan_mati(self):
        a, b = self.spawn("A"), self.spawn("B")
        self.know(a, b)
        b.fake.close()
        # Node B sendiri hidup tapi Ollama-nya mati: ThinkError "unavailable" dilaporkan apa adanya.
        code, res = self.post(a, "/think", {"messages": MSG, "node": "B", "timeout": 5})
        self.assertEqual(code, 200)
        self.assertEqual((res["ok"], res["kind"]), (False, "unavailable"))

    def test_node_tujuan_tak_terjangkau(self):
        a = self.spawn("A")
        a.cluster.peers["ghost"] = {"state": {"id": "ghost", "name": "ghost", "addr": f"127.0.0.1:{_free_port()}"},
                                    "seen": time.time()}
        code, res = self.post(a, "/think", {"messages": MSG, "node": "ghost", "timeout": 5})
        self.assertEqual((code, res["ok"], res["kind"]), (200, False, "unreachable"))

    def test_tanpa_tanda_tangan_ditolak(self):
        a = self.spawn("A")
        req = urllib.request.Request(f"http://127.0.0.1:{a.port}/think", data=json.dumps({"messages": MSG}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 401)
        self.assertEqual(a.fake.requests, [])

    def test_kode_gabung_lain_ditolak(self):
        a = self.spawn("A")
        stranger = _Cfg(join_code="LAIN")
        code, _ = self.post(a, "/think", {"messages": MSG}, cfg=stranger)
        self.assertEqual(code, 401)
        self.assertEqual(a.fake.requests, [])

    def test_bot_di_komputer_ini_tanpa_tanda_tangan_boleh(self):
        a = self.spawn("A", reply="dari A")
        with mock.patch.object(server_mod, "LOCAL", {"127.0.0.1"}):
            req = urllib.request.Request(f"http://127.0.0.1:{a.port}/think", data=json.dumps({"messages": MSG}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read())
        self.assertEqual(res["message"]["content"], "dari A")

    def test_dimatikan_di_node_tujuan(self):
        a, b = self.spawn("A"), self.spawn("B", think=False)
        b.thinker.enabled = False
        self.know(a, b)
        res = self.post(a, "/think", {"messages": MSG, "node": "B"})[1]
        self.assertEqual((res["ok"], res["kind"]), (False, "disabled"))

    def test_tanpa_model_di_node_tujuan(self):
        a, b = self.spawn("A"), self.spawn("B", model="")
        self.know(a, b)
        res = self.post(a, "/think", {"messages": MSG, "node": "B"})[1]
        self.assertEqual((res["ok"], res["kind"]), (False, "no_model"))


class LeaderLostTest(_Base):
    """Pemimpin mati: SEMUA komputer berhenti berpikir sampai pemimpin baru terdengar."""

    def test_semua_berhenti_lalu_berpikir_lagi(self):
        a, b = self.spawn("A", reply="dari A"), self.spawn("B", reply="dari B")
        self.know(a, b)
        self.assertTrue(self.post(a, "/think", {"messages": MSG, "node": "B"})[1]["ok"])

        b.cluster.phase = "electing"          # B melihat pemimpin mati
        code, _ = self.post(b, "/think", {"messages": MSG})
        self.assertEqual(code, 503)           # langsung: "sedang tidak bisa"
        res = self.post(a, "/think", {"messages": MSG, "node": "B"})[1]
        self.assertEqual((res["ok"], res["kind"]), (False, "unreachable"))   # lewat perantara juga
        chats = len(b.fake.posts("/api/chat"))
        self.assertEqual(chats, 1)            # tidak ada yang sampai ke Ollama selama pemilihan

        a.cluster.phase = "electing"
        self.assertEqual(self.post(a, "/think", {"messages": MSG})[0], 503)

        b.cluster.phase = "running"           # pemimpin baru terdengar
        a.cluster.phase = "running"
        self.assertTrue(self.post(a, "/think", {"messages": MSG, "node": "B"})[1]["ok"])
        self.assertTrue(self.post(a, "/think", {"messages": MSG})[1]["ok"])


class StatusTest(_Base):
    def test_status_memuat_kesiapan_berpikir_semua_komputer(self):
        a, b = self.spawn("A"), self.spawn("B", model="besar", reply="x")
        b.thinker.up, b.thinker.tps = True, 7.5
        self.know(a, b)
        code, st = self.get(a, "/status")
        self.assertEqual(code, 200)
        by_name = {n["name"]: n for n in st["nodes"]}
        self.assertEqual(set(by_name), {"A", "B"})
        self.assertEqual(by_name["A"]["think"]["model"], "m1")
        self.assertTrue(by_name["A"]["think"]["ok"])
        self.assertEqual((by_name["B"]["think"]["model"], by_name["B"]["think"]["tps"]), ("besar", 7.5))
        for key in ("inflight", "waiting", "slots", "ctx", "cpu", "ctl", "loaded", "restarts"):
            self.assertIn(key, by_name["B"]["think"])

    def test_komputer_tanpa_layanan_berpikir_tidak_merusak_status(self):
        a = self.spawn("A")
        a.cluster.thinker = None            # node versi lama / belum menyala
        code, st = self.get(a, "/status")
        self.assertEqual(code, 200)
        self.assertIsNone(st["nodes"][0]["think"])

    def test_gosip_membawa_kesiapan_berpikir(self):
        a, b = self.spawn("A"), self.spawn("B", model="besar")
        b.thinker.up = True
        # A menggosipi B lewat HTTP bertanda tangan sungguhan; B membalas dengan state-nya.
        res = a.cluster.rpc(b.cluster.addr, "/gossip", a.cluster._gossip_payload(), 5)
        self.assertEqual(res["state"]["think"]["model"], "besar")


if __name__ == "__main__":
    unittest.main()
