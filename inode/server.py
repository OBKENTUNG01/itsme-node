"""Server HTTP node: dipakai node lain (gosip, tautan, rangkuman) dan pemilik (status, tanya)."""

import concurrent.futures
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import itsme, llm
from .invites import Invites
from .cluster import log

LOCAL = {"127.0.0.1", "::1"}


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
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

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

        def _handle(self, payload):
            path = urlsplit(self.path).path
            if path == "/invite":
                return self._reply(*invite(payload))
            if path == "/invite-new":
                if self.client_address[0] not in LOCAL:
                    return self._reply(403, {"error": "hanya dari komputer ini"})
                try:
                    return self._reply(200, invite_new(payload))
                except (ValueError, TypeError) as e:
                    return self._reply(400, {"error": str(e)})
            if not self._authorized():
                return self._reply(401, {"error": "kode gabung salah"})
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
