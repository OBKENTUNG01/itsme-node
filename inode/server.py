"""Server HTTP node: dipakai node lain (gosip, tautan, rangkuman) dan pemilik (status, tanya)."""

import base64
import concurrent.futures
import hmac
import ipaddress
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import auth, itsme, llm, remote, viewer, wol
from .invites import Invites
from .cluster import log

LOCAL = {"127.0.0.1", "::1"}
# Bounded POST bodies: 16 MiB normally; /fs/write permits 128 MiB to carry a
# 100 MB file encoded as base64. Reject oversized bodies before reading them.
MAX_POST_BODY_BYTES = 16 * 1024 * 1024
MAX_THINK_BODY_BYTES = MAX_POST_BODY_BYTES
MAX_FS_WRITE_BODY_BYTES = 128 * 1024 * 1024  # cukup untuk berkas biner 100 MB (base64)
MAX_ENROLL_BODY_BYTES = 64 * 1024  # drain hanya body lama yang kecil sebelum 410
# Rute akses jarak jauh (layar, input, berkas, daya). Boleh menyebut ?node=<id> untuk
# komputer lain; node ini meneruskannya ke sana lewat Tailscale.
REMOTE_PATHS = {"/screen", "/input", "/fs/list", "/fs/read", "/fs/download", "/fs/write", "/power"}


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


def check_auth(cfg, verifier, peer, method, target, raw, headers):
    """Apakah permintaan dari `peer` boleh? Komputer ini sendiri selalu boleh.
    Node lain harus menandatangani permintaannya (auth.py - kunci tak pernah
    melintas di kabel); token Bearer lama hanya diterima selama node lain belum
    diperbarui (config accept_legacy_bearer)."""
    if peer in LOCAL:
        return True
    if verifier.check(cfg, method, target, raw, headers):
        return True
    if cfg.get("accept_legacy_bearer", True):
        got = headers.get("Authorization", "").removeprefix("Bearer ")
        try:
            return hmac.compare_digest(got, cfg.token)
        except TypeError:  # header non-ASCII
            return False
    return False


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
    verifier = auth.Verifier()

    def status(_):
        alive = cluster.alive()
        return {"me": cluster.id, "addr": cluster.addr, "leader": cluster.leader, "phase": cluster.phase,
                "epoch": cluster.epoch, "profile": node.profile,
                "nodes": sorted(({"name": s["name"], "id": s["id"], "addr": s["addr"],
                                  "working": s.get("working"), "model": s.get("model"),
                                  "score": s.get("score"), "stats": s.get("stats"),
                                  "bot": bool(s.get("bot")),
                                  # Kesiapan berpikir tiap komputer (model, kecepatan, antrean): dibaca
                                  # bot untuk membagi pekerjaan ke semua komputer (thinker.py).
                                  "think": s.get("think"),
                                  "leader": s["id"] == cluster.leader}
                                 for s in alive.values()), key=lambda s: s["name"]),
                # Komputer yang pernah satu LAN langsung dengan node ini, hidup atau
                # tidur - dashboard memakainya untuk tombol Nyalakan (Wake-on-LAN, /wake).
                "known": store.seen_list()}

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

    def think(p):
        """Berpikir (chat dengan model lokal). Dengan 'node' = id komputer lain, permintaan diteruskan
        ke sana (ditandatangani) - bot cukup bicara dengan node di komputernya sendiri."""
        target = p.get("node")
        if target and target != cluster.id:
            addr = (cluster.alive().get(target) or {}).get("addr")
            if not addr:
                return {"ok": False, "kind": "gone", "error": "komputer tidak ditemukan atau sedang mati"}
            rest = {k: v for k, v in p.items() if k != "node"}
            try:
                wait = min(max(float(p.get("timeout") or 900), 5.0), 3600.0)
            except (TypeError, ValueError):
                wait = 900.0
            res = cluster.rpc(addr, "/think", rest, timeout=wait + 20)
            if res is None:
                return {"ok": False, "kind": "unreachable",
                        "error": "komputer tujuan tak menjawab (mati, sibuk memilih pemimpin, atau terputus)"}
            return res
        thinker = getattr(node, "thinker", None)
        if thinker is None:
            return {"ok": False, "kind": "disabled", "error": "layanan berpikir belum menyala"}
        return thinker.handle(p)

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

    def wake(p):
        """Bangunkan komputer bernama p['name'] lewat Wake-on-LAN (siaran lokal).
        Kalau node ini sendiri tidak pernah melihat MAC-nya langsung, tanya tiap
        tetangga LAN yang masih hidup (relay=False mencegah tanya-berantai tanpa
        akhir - satu putaran ke tetangga cukup, karena semua node di LAN yang sama
        saling bergosip langsung). Lihat wol.py untuk batas fisik Wake-on-LAN."""
        name = (p.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "nama komputer kosong"}
        sent, mac = wol.wake_by_name(store, name)
        if sent:
            return {"ok": True, "mac": mac, "via": cfg["name"]}
        if not p.get("relay", True):
            return {"ok": False, "error": f'node {cfg["name"]} tidak tahu MAC "{name}"'}
        for s in cluster.alive().values():
            if s["id"] == cluster.id:
                continue
            res = cluster.rpc(s["addr"], "/wake", {"name": name, "relay": False}, timeout=5)
            if res and res.get("ok"):
                return res
        return {"ok": False,
                "error": f'MAC "{name}" belum pernah terlihat langsung di jaringan lokal '
                         "node ini atau tetangganya (komputer itu harus pernah satu LAN "
                         "dengan SATU node It's Me yang masih hidup sekarang)"}

    def invite_new(p):
        # Hanya dari komputer ini (bot It's Me lewat 127.0.0.1), lihat _handle.
        invites.add(p.get("code"), p.get("ttl"), p.get("env"), bool(p.get("reusable")))
        return {"ok": True}

    def invite(p, ip):
        # Satu-satunya jalur tanpa kode gabung: komputer baru menukar kode undangan.
        try:
            env = invites.redeem(p.get("code"), caller=ip)
        except PermissionError as e:
            return 429, {"error": str(e)}
        if env is None:
            return 404, {"error": "kode undangan salah, sudah dipakai, atau kedaluwarsa"}
        log("info", "undangan dipakai oleh", str(p.get("name", "?"))[:60])
        # "seed": alamat node ini sendiri, supaya komputer baru punya titik awal
        # untuk gosip (tanpa ini, dua node dengan join_code sama TETAP tidak
        # pernah saling tahu alamat - tak ada cara lain menemukan satu sama lain).
        return 200, {"join_code": cfg["join_code"], "env": env, "from": cfg["name"], "seed": cluster.addr}

    def enroll():
        """Gabung ulang berbasis fingerprint dinonaktifkan: fingerprint bukan bukti rahasia.

        Komputer yang kehilangan konfigurasi harus memakai kode undangan acak sekali pakai.
        """
        return 410, {"error": "gabung ulang via sidik jari dinonaktifkan; gunakan kode undangan sekali pakai (/pasang)"}

    # --- akses jarak jauh: layar, input, berkas (lokal atau diteruskan ke node lain) ---
    def _addr_of(node_id):
        return (cluster.alive().get(node_id) or {}).get("addr")

    def _node_get(addr, path, params):
        """GET ke node lain (ditandatangani, lihat auth.py); kembalikan
        (kode, content-type, bytes, disposition)."""
        return auth.http_request(cfg, addr, "GET", f"{path}?" + urllib.parse.urlencode(params),
                                 None, timeout=25)

    def remote_route(path, p):
        """Kembalikan ('json', code, obj) atau ('raw', code, ctype, bytes, headers)."""
        node_id = p.get("node")
        if node_id and node_id != cluster.id:
            addr = _addr_of(node_id)
            if not addr:
                return ("json", 404, {"error": "komputer tidak ditemukan atau sedang mati"})
            rest = {k: v for k, v in p.items() if k not in ("node", "t")}
            if path in ("/input", "/fs/write", "/power"):
                res = cluster.rpc(addr, path, rest, timeout=25)
                return ("json", 200 if res else 502, res or {"error": "komputer tujuan tak terjawab"})
            code, ctype, body, disp = _node_get(addr, path, rest)
            headers = {"Content-Disposition": disp} if disp else {}
            return ("raw", code, ctype or "application/octet-stream", body, headers)

        # komputer ini sendiri - tandai "sedang diakses dari web" supaya riset
        # latar dijeda SELAMA dashboard memakai komputer ini (lihat remote.py).
        remote.touch_remote_access()
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
        if path == "/power":
            ok, message = remote.power(p.get("action", ""))
            return ("json", 200 if ok else 400, {"ok": ok, "message": message})
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
        "/think": think,
        "/search": search,
        "/status": status,
        "/ask": ask,
        "/recent": lambda p: {"docs": store.recent(int(p.get("limit", 10)))},
        "/itsme-state": itsme_state,
        "/enrolled": lambda p: {"devices": store.enrolled_list()},
        "/enrolled-merge": lambda p: {"ok": (store.merge_enrolled(p.get("devices")), True)[1]},
        "/wake": wake,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _client_ip(self):
            return client_ip(self.client_address[0], self.headers)

        def _authorized(self):
            return check_auth(cfg, verifier, self.client_address[0], self.command, self.path,
                              getattr(self, "_raw", b""), self.headers)

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
                return self._reply(*invite(payload, self._client_ip()))
            if path == "/enroll":
                return self._reply(*enroll())
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
            self._raw = b""
            qs = parse_qs(urlsplit(self.path).query)
            self._handle({k: v[0] for k, v in qs.items()})

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                if path == "/enroll":
                    return self._reply(*enroll())
                return self._reply(400, {"error": "Content-Length tidak valid"})
            if n < 0:
                if path == "/enroll":
                    return self._reply(*enroll())
                return self._reply(400, {"error": "Content-Length tidak valid"})
            if path == "/enroll":
                if 0 < n <= MAX_ENROLL_BODY_BYTES:
                    self.rfile.read(n)
                return self._reply(*enroll())
            limit = MAX_FS_WRITE_BODY_BYTES if path == "/fs/write" else MAX_POST_BODY_BYTES
            if n > limit:
                message = "permintaan berpikir terlalu besar" if path == "/think" else "permintaan terlalu besar"
                return self._reply(413, {"error": message})
            self._raw = self.rfile.read(n) if n else b""  # isi mentah: ikut ditandatangani
            try:
                payload = json.loads(self._raw or b"{}")
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
