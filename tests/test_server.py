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
from inode import server, wol  # noqa: E402

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
    def __init__(self):
        self._macs = {}

    def search(self, *a, **k):
        return []

    def recent(self, *a, **k):
        return []

    def stats(self):
        return {}

    def last_seen_mac(self, name):
        return self._macs.get((name or "").strip().lower(), "")

    def remember_seen(self, name, mac):
        self._macs[(name or "").strip().lower()] = mac


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
        cls.srvA, cls.nodeA = make("A")
        cls.srvB, cls.nodeB = make("B")
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

    def test_power_local(self):
        # remote.power() DIPALSUKAN di sini sengaja - jangan sampai tes benar-benar mematikan mesin CI.
        from inode import remote

        with mock.patch.object(remote, "power", return_value=(True, "Komputer akan mati dalam 5 detik.")) as power:
            c, res = self.post(PORTS["A"], "/power", {"action": "shutdown"})
        self.assertEqual(c, 200)
        self.assertTrue(res["ok"])
        power.assert_called_once_with("shutdown")

    def test_power_proxy_to_B(self):
        from inode import remote

        with mock.patch.object(remote, "power", return_value=(True, "Komputer akan nyala ulang dalam 5 detik.")) as power:
            c, res = self.post(PORTS["A"], "/power", {"node": "B", "action": "restart"})
        self.assertEqual(c, 200)
        self.assertTrue(res["ok"])
        power.assert_called_once_with("restart")  # dijalankan di proses B, bukan A

    def test_cursor_route_local_and_proxied(self):
        from inode import remote

        fake = {"x": 10, "y": 20, "w": 1920, "h": 1080, "nx": 0.0052, "ny": 0.0185, "visible": True, "kind": "text"}
        with mock.patch.object(remote, "cursor_info", return_value=fake):
            for q in ("/cursor", "/cursor?node=B"):
                c, ct, b = self.get(PORTS["A"], q)
                self.assertEqual(c, 200)
                self.assertEqual(json.loads(b)["kind"], "text")

    def test_input_reply_carries_cursor_position(self):
        """Dashboard menggambar kursornya sendiri: /input harus mengembalikan posisi sesudahnya."""
        from inode import remote

        with mock.patch.object(remote, "inject", return_value=1), \
             mock.patch.object(remote, "cursor_info", return_value={"nx": 0.5, "ny": 0.25}):
            c, res = self.post(PORTS["A"], "/input", {"node": "B", "events": [{"t": "move", "x": 0.5, "y": 0.25}]})
        self.assertEqual(c, 200)
        self.assertEqual(res["cursor"], {"nx": 0.5, "ny": 0.25})

    def test_ollama_status_and_install_forwarded_to_B(self):
        from inode import ollama_ctl

        with mock.patch.object(ollama_ctl, "status", return_value={"installed": False, "job": None}) as st:
            c, res = self.post(PORTS["A"], "/ollama", {"node": "B", "action": "status"})
        self.assertEqual((c, res["installed"]), (200, False))
        st.assert_called_once()
        with mock.patch.object(ollama_ctl, "start", return_value={"ok": True, "job": {"action": "install"}}) as start:
            c, res = self.post(PORTS["A"], "/ollama", {"node": "B", "action": "install"})
        self.assertTrue(res["ok"])
        start.assert_called_once_with("install")  # dijalankan di proses B

    # --- keamanan: halaman web di browser pemilik tidak boleh menyuruh node ------------------
    def raw_request(self, method, path, headers=None, body=None, port=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", port or PORTS["A"], timeout=30)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            hdrs = {"Host": f"127.0.0.1:{port or PORTS['A']}"}
            hdrs.update(headers or {})
            data = json.dumps(body).encode() if body is not None else None
            if data is not None:
                hdrs["Content-Type"] = "application/json"
                hdrs["Content-Length"] = str(len(data))
            for k, v in hdrs.items():
                conn.putheader(k, v)
            conn.endheaders(data)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_get_cannot_trigger_mutating_routes(self):
        """<img src="http://127.0.0.1:7171/power?action=shutdown"> dari situs mana pun tak boleh berefek."""
        from inode import ollama_ctl, remote

        with mock.patch.object(remote, "power") as power, mock.patch.object(remote, "inject") as inject, \
             mock.patch.object(remote, "fs_write") as fs_write, \
             mock.patch.object(ollama_ctl, "start") as start, \
             mock.patch.object(ollama_ctl, "status", return_value={"installed": True, "job": None}):
            for path in ("/power?action=shutdown", "/input?events=x", "/fs/write?path=x&b64=eA=="):
                code, _ = self.raw_request("GET", path)
                self.assertEqual(code, 405, path)
            code, body = self.raw_request("GET", "/ollama?action=uninstall")
            self.assertEqual(code, 200)                       # GET /ollama = status saja
            self.assertTrue(json.loads(body)["installed"])
        power.assert_not_called()
        inject.assert_not_called()
        fs_write.assert_not_called()
        start.assert_not_called()

    def test_other_state_changing_routes_are_post_only_too(self):
        for path in ("/invite-new?code=ABCDEFGHIJKL&ttl=600", "/wake?name=x", "/enrolled-merge?devices=x",
                     "/doc?url=x", "/links?rows=x"):
            code, _ = self.raw_request("GET", path)
            self.assertEqual(code, 405, path)

    def test_cross_site_requests_to_loopback_are_refused(self):
        from inode import remote

        with mock.patch.object(remote, "power", return_value=(True, "ok")) as power:
            body = {"action": "shutdown"}
            for hdrs in ({"Origin": "http://evil.example"}, {"Origin": "null"},
                         {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                         {"Host": "evil.example:80"}, {"Host": "evil.example"}, {"Host": ""}):
                code, _ = self.raw_request("POST", "/power", hdrs, body)
                self.assertEqual(code, 403, hdrs)
            power.assert_not_called()
            # halaman node sendiri (asal-sama) dan pemanggil lokal biasa tetap boleh
            for hdrs in ({}, {"Origin": f"http://127.0.0.1:{PORTS['A']}"}, {"Sec-Fetch-Site": "same-origin"},
                         {"Sec-Fetch-Site": "none"}, {"Host": f"localhost:{PORTS['A']}"}):
                code, _ = self.raw_request("POST", "/power", hdrs, body)
                self.assertEqual(code, 200, hdrs)
            self.assertEqual(power.call_count, 5)

    def test_local_request_safe_helper(self):
        from inode.server import local_request_safe

        self.assertTrue(local_request_safe({"Host": "127.0.0.1:7171"}))
        self.assertTrue(local_request_safe({"Host": "[::1]:7171", "Origin": "http://[::1]:7171"}))
        self.assertFalse(local_request_safe({}))
        self.assertFalse(local_request_safe({"Host": "192.168.1.3:7171"}))
        self.assertFalse(local_request_safe({"Host": "127.0.0.1:7171", "Origin": "https://evil.example"}))

    def test_ollama_busy_reported_as_conflict(self):
        from inode import ollama_ctl

        with mock.patch.object(ollama_ctl, "start", return_value={"ok": False, "error": "masih ada pekerjaan"}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post(PORTS["A"], "/ollama", {"action": "uninstall"})
        self.assertEqual(cm.exception.code, 409)

    def test_status_lists_ollama_per_computer(self):
        cl = self.nodeA.cluster
        cl.addr = "127.0.0.1:%d" % PORTS["A"]
        self.addCleanup(delattr, cl, "addr")
        state = cl.alive()
        state["B"]["ollama"] = {"installed": False, "version": None, "job": None}
        with mock.patch.object(cl, "alive", return_value=state), \
             mock.patch.object(self.nodeA.store, "seen_list", create=True, return_value=[]):
            c, ct, b = self.get(PORTS["A"], "/status")
        by_id = {n["id"]: n for n in json.loads(b)["nodes"]}
        self.assertEqual(by_id["B"]["ollama"]["installed"], False)
        self.assertIn("ollama", by_id["A"])

    def test_power_unknown_action_local(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post(PORTS["A"], "/power", {"action": "hibernate"})
        self.assertEqual(cm.exception.code, 400)
        self.assertFalse(json.loads(cm.exception.read())["ok"])

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

    def test_wake_unknown_everywhere(self):
        # Tidak dikenal di A maupun B: harus selesai cepat (tak pingpong tanpa akhir)
        # dengan ok:false - bukan HTTP error (lihat server.py wake(), relay=False).
        with mock.patch.object(wol, "send_magic_packet") as send:
            c, res = self.post(PORTS["A"], "/wake", {"name": "TidakPernahAda"})
        self.assertEqual(c, 200)
        self.assertFalse(res["ok"])
        send.assert_not_called()

    def test_wake_known_locally(self):
        self.nodeA.store.remember_seen("Printer-A", "112233445566")
        with mock.patch.object(wol, "send_magic_packet", return_value=True) as send:
            c, res = self.post(PORTS["A"], "/wake", {"name": "printer-a"})  # tak peka besar/kecil
        self.assertEqual(c, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mac"], "112233445566")
        send.assert_called_once_with("112233445566")

    def test_wake_relayed_through_peer(self):
        # A sendiri tidak tahu MAC-nya, tapi B (tetangga LAN-nya) tahu -> A meneruskan.
        self.nodeB.store.remember_seen("PCIbuJauh", "aabbccddeeff")
        with mock.patch.object(wol, "send_magic_packet", return_value=True) as send:
            c, res = self.post(PORTS["A"], "/wake", {"name": "PCIbuJauh"})
        self.assertEqual(c, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mac"], "aabbccddeeff")
        send.assert_called_once_with("aabbccddeeff")  # terkirim oleh B


if __name__ == "__main__":
    unittest.main()
