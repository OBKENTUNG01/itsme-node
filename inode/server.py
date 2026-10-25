"""Server HTTP node: dipakai node lain (gosip, tautan, rangkuman) dan pemilik (status, tanya)."""

import base64
import concurrent.futures
import hmac
import ipaddress
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import itsme, llm, remote, viewer
from .invites import Invites
from .cluster import log

LOCAL = {"127.0.0.1", "::1"}
# Rute akses jarak jauh (layar, input, berkas). Boleh menyebut ?node=<id> untuk
# komputer lain; node ini meneruskannya ke sana lewat Tailscale.
REMOTE_PATHS = {"/screen", "/input", "/fs/list", "/fs/read", "/fs/download", "/fs/write"}


# Jaringan lokal: sepercaya Tailscale untuk gabung-ulang (satu rumah/kantor).
_LAN_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "fc00::/7", "fe80::/10"))


def client_ip(peer, headers):
    """IP pemanggil sebenarnya. Header hanya dipercaya bila peer-nya lokal (dashboard)."""
    if peer in LOCAL:
        fwd = (headers.get("X-Itsme-Client-IP") or "").strip()
        if fwd:
            return fwd
    return peer


def _in_lan(ip):
    """IP di jaringan lokal (LAN/link-local), bukan internet publik."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    return any(addr in net for net in _LAN_NETS)


# Jaringan lokal: sepercaya Tailscale untuk gabung-ulang (satu rumah/kantor).
_LAN_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "fc00::/7", "fe80::/10"))


def client_ip(peer, headers):
    """IP pemanggil sebenarnya. Header hanya dipercaya bila peer-nya lokal (dashboard)."""
    if peer in LOCAL:
        fwd = (headers.get("X-Itsme-Client-IP") or "").strip()
        if fwd:
            return fwd
    return peer


def _in_lan(ip):
    """IP di jaringan lokal (LAN/link-local), bukan internet publik."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    return any(addr in net for net in _LAN_NETS)


def _in_tailnet(ip):
    """IP di rentang Tailscale (100.64.0.0/10) atau alamat Tailscale IPv6 (fd7a:)."""
    ip = (ip or "").lower()
    if ip.startswith("fd7a:"):
        return True
    parts = ip.split(".")
    if len(parts) == 4 and parts[0] == "100":
        try:
            return 64 <= int(parts[1]) <= 127
        except ValueError:
            return False
    return False


def make_server(node):
    cfg, cluster, store = node.cfg, node.cluster, node.store
    invites = Invites()

    def status(_):
        alive = cluster.alive()
        return {"me": cluster.id, "leader": cluster.leader, "phase": cluster.phase,
                "epoch": cluster.epoch, "profile": node.profile,
                "nodes": sorted(({"name": s["name"], "id": s["id"], "addr": s["addr"],
                                  "working": s.get("working"), "model": s.get("model"),
                                  "score": s.get("score"), "stats": s.get("stats"),
                                  "bot": bool(s.get("bot")),
                                  "leader": s["id"] == cluster.leader}
                                 for s in alive.values()), key=lambda s: s["name"])}

    def itsme_state(_):
        # Hanya pemimpin yang menjalankan bot yang membagikan memori/otak/akses It's Me.
        if not (cluster.is_leader and cluster.bot):
            return None
        return {"files": itsme.read_state(cfg)}

    def summarize(p):
        if not node.profile["model"] or not cluster.working:
            return None
        return {"summary": llm.summarize(cfg["ollama_url"], node.profile["model"],
                                         p.get("title", ""), p.get("text", ""))}

    def search(p):
        return {"docs": store.search(p.get("q", ""), int(p.get("limit", 8)))}

    def ask(p):
        q = p.get("q", "")
        peers = [s for i, s in cluster.alive().items() if i != cluster.id]
        docs = store.search(q, 8)
        with concurrent.futures.ThreadPoolExecutor(16) as ex:
            for res in ex.map(lambda s: cluster.rpc(s["addr"], "/search", {"q": q}, 10), peers):
                docs += (res or {}).get("docs", [])
        best, seen = [], set()
        for d in sorted(docs, key=lambda d: d["rank"]):
            if d["url"] not in seen:
                seen.add(d["url"])
                best.append(d)
        best = best[:8]
        answer = ""
        if best and node.profile["model"] and not p.get("docs_only"):
            try:
                answer = llm.answer(cfg["ollama_url"], node.profile["model"], q, best)
            except Exception as e:
                answer = f"(model lokal gagal menjawab: {e})"
        return {"answer": answer, "docs": best}

    def invite_new(p):
        # Hanya dari komputer ini (bot It's Me lewat 127.0.0.1), lihat _handle.
        invites.add(p.get("code"), p.get("ttl"), p.get("env"))
        return {"ok": True}

    def invite(p):
        # Satu-satunya jalur tanpa kode gabung: komputer baru menukar kode undangan.
        try:
            env = invites.redeem(p.get("code"))
        except PermissionError as e:
            return 429, {"error": str(e)}
        if env is None:
            return 404, {"error": "kode undangan salah, sudah dipakai, atau kedaluwarsa"}
        log("info", "undangan dipakai oleh", str(p.get("name", "?"))[:60])
        return 200, {"join_code": cfg["join_code"], "env": env, "from": cfg["name"]}

    def enroll(p, ip):
        """Komputer yang DULU pernah terdaftar lalu diinstal ulang gabung lagi tanpa
        kode undangan baru: cukup sidik jari perangkat kerasnya (tetap walau OS
        diinstal ulang) yang sudah dikenal jaringan, dan ia harus di jaringan Tailscale."""
        if ip not in LOCAL and not _in_tailnet(ip) and not _in_lan(ip):
            # Penjaga ini dulu MATI: dashboard meneruskan permintaan dari 127.0.0.1,
            # jadi setiap panggilan lewat tunnel terlihat "lokal". Sekarang dashboard
            # mengirim IP asli di X-Itsme-Client-IP (hanya dipercaya dari peer lokal).
            return 403, {"error": "gabung-ulang otomatis hanya dari jaringan lokal/Tailscale. "
                                  "Dari internet pakai kode undangan (/pasang)."}
        fp = str(p.get("fingerprint") or "").strip()
        if not fp:
            return 400, {"error": "sidik jari kosong"}
        if not store.is_enrolled(fp):
            return 404, {"error": "komputer ini belum pernah terdaftar; pakai /pasang sekali dulu"}
        store.enroll_device(fp, p.get("name"))
        env = itsme.env_bundle(cfg) if itsme.ready(cfg) else {}
        log("info", "gabung-ulang otomatis oleh", str(p.get("name", "?"))[:60], fp[:8])
        return 200, {"join_code": cfg["join_code"], "env": env, "from": cfg["name"]}

    # --- akses jarak jauh: layar, input, berkas (lokal atau diteruskan ke node lain) ---
    def _addr_of(node_id):
        return (cluster.alive().get(node_id) or {}).get("addr")

    def _node_get(addr, path, params):
        """GET ke node lain dengan token; kembalikan (kode, content-type, bytes, disposition)."""
        url = f"http://{addr}{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cfg.token})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read(), \
                    r.headers.get("Content-Disposition", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", "application/json"), e.read(), ""

    def remote_route(path, p):
        """Kembalikan ('json', code, obj) atau ('raw', code, ctype, bytes, headers)."""
        node_id = p.get("node")
        if node_id and node_id != cluster.id:
            addr = _addr_of(node_id)
            if not addr:
                return ("json", 404, {"error": "komputer tidak ditemukan atau sedang mati"})
            rest = {k: v for k, v in p.items() if k not in ("node", "t")}
            if path in ("/input", "/fs/write"):
                res = cluster.rpc(addr, path, rest, timeout=25)
                return ("json", 200 if res else 502, res or {"error": "komputer tujuan tak terjawab"})
            code, ctype, body, disp = _node_get(addr, path, rest)
            headers = {"Content-Disposition": disp} if disp else {}
            return ("raw", code, ctype or "application/octet-stream", body, headers)

        # komputer ini sendiri
        if path == "/screen":
            try:
                w = int(p.get("w", 1280) or 1280)
            except ValueError:
                w = 1280
            png = remote.capture_png(max(320, min(1920, w)))
            if not png:
                return ("json", 503, {"error": "layar tidak bisa diambil (bukan Windows / tidak ada sesi layar)"})
            return ("raw", 200, "image/png", png, {"Cache-Control": "no-store"})
        if path == "/input":
            return ("json", 200, {"sent": remote.inject(p.get("events") or [])})
        if path == "/fs/list":
            return ("json", 200, remote.fs_list(p.get("path", "")))
        if path == "/fs/read":
            return ("json", 200, remote.fs_read(p.get("path", "")))
        if path == "/fs/write":
            raw = p.get("b64") or ""
            try:
                data = base64.b64decode(raw, validate=True)
            except (ValueError, TypeError):
                return ("json", 400, {"error": "isi berkas rusak (base64)"})
            res = remote.fs_write(p.get("path", ""), data)
            return ("json", 200 if res.get("ok") else 400, res)
        if path == "/fs/download":
            name, data = remote.fs_file(p.get("path", ""))
            quoted = urllib.parse.quote(name)
            return ("raw", 200, "application/octet-stream", data,
                    {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
        return ("json", 404, {"error": "tidak ada"})

    routes = {
        "/gossip": lambda p: (cluster.learn(p), cluster._gossip_payload())[1],
        "/links": lambda p: {"added": store.add_links([tuple(r) for r in p.get("rows", [])])},
        "/doc": lambda p: {"ok": store.add_doc(p["url"], p["title"], p["summary"], p["node"])},
        "/summarize": summarize,
        "/search": search,
        "/status": status,
        "/ask": ask,
        "/recent": lambda p: {"docs": store.recent(int(p.get("limit", 10)))},
        "/itsme-state": itsme_state,
        "/enrolled": lambda p: {"devices": store.enrolled_list()},
        "/enrolled-merge": lambda p: {"ok": (store.merge_enrolled(p.get("devices")), True)[1]},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _client_ip(self):
            return client_ip(self.client_address[0], self.headers)

        def _authorized(self):
            if self.client_address[0] in LOCAL:
                return True
            got = self.headers.get("Authorization", "").removeprefix("Bearer ")
            return hmac.compare_digest(got, cfg.token)

        def _reply(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reply_raw(self, code, ctype, body, headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_remote(self, path, payload):
            try:
                res = remote_route(path, payload)
            except PermissionError as e:
                return self._reply(403, {"error": str(e)})
            except FileNotFoundError as e:
                return self._reply(404, {"error": str(e)})
            except (NotADirectoryError, ValueError) as e:
                return self._reply(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                log("remote error", path, e)
                return self._reply(500, {"error": str(e)})
            if res[0] == "raw":
                _, code, ctype, body, headers = res
                return self._reply_raw(code, ctype, body, headers)
            return self._reply(res[1], res[2])

        def _handle(self, payload):
            path = urlsplit(self.path).path
            if path in ("/", "/remote"):
                if self._client_ip() not in LOCAL:
                    return self._reply(403, {"error": "halaman ini hanya dari komputer ini"})
                return self._reply_raw(200, "text/html; charset=utf-8", viewer.PAGE.encode())
            if path == "/invite":
                return self._reply(*invite(payload))
            if path == "/enroll":
                return self._reply(*enroll(payload, self._client_ip()))
            if path == "/invite-new":
                if self._client_ip() not in LOCAL:
                    return self._reply(403, {"error": "hanya dari komputer ini"})
                try:
                    return self._reply(200, invite_new(payload))
                except (ValueError, TypeError) as e:
                    return self._reply(400, {"error": str(e)})
            if not self._authorized():
                return self._reply(401, {"error": "kode gabung salah"})
            if path in REMOTE_PATHS:
                return self._send_remote(path, payload)
            fn = routes.get(path)
            if not fn:
                return self._reply(404, {"error": "tidak ada"})
            try:
                res = fn(payload)
            except Exception as e:
                log("error", path, e)
                return self._reply(500, {"error": str(e)})
            if res is None:
                return self._reply(503, {"error": "sedang tidak bisa"})
            self._reply(200, res)

        def do_GET(self):
            qs = parse_qs(urlsplit(self.path).query)
            self._handle({k: v[0] for k, v in qs.items()})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._reply(400, {"error": "json rusak"})
            self._handle(payload)

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # node lain memutus koneksi (timeout) bukan masalah

    srv = Server(("0.0.0.0", cfg["port"]), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
