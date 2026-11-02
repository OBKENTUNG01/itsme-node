"""Ollama palsu untuk uji: server HTTP SUNGGUHAN di loopback yang meniru /api/tags, /api/ps,
/api/chat, /api/generate. Jawabannya bisa diatur (antrean `script`) termasuk keadaan rusak:
status 500 (proses model mati), koneksi diputus mendadak, lambat, atau 503 sibuk."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    def __init__(self, models=("m1",), loaded=()):
        self.models = list(models)
        self.loaded = list(loaded)
        self.requests = []   # [(path, body)] semua POST yang diterima, berurutan
        self.script = []     # antrean jawaban /api/chat dan /api/generate (dipakai satu per satu)
        self.reply = "halo"  # isi jawaban bawaan
        self.eval_count = 20
        self.eval_seconds = 2.0
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": m} for m in fake.models]})
                if self.path == "/api/ps":
                    return self._send(200, {"models": [{"name": m, "model": m} for m in fake.loaded]})
                self._send(404, {"error": "tidak ada"})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw or b"{}")
                with fake.lock:
                    fake.requests.append((self.path, body))
                    step = fake.script.pop(0) if fake.script else None
                if self.path not in ("/api/chat", "/api/generate"):
                    return self._send(404, {"error": "tidak ada"})
                if step:
                    if step.get("reset"):  # proses model mati: koneksi diputus tanpa jawaban
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        self.connection.close()
                        return
                    if step.get("sleep"):
                        time.sleep(step["sleep"])
                    if step.get("status", 200) != 200:
                        return self._send(step["status"], {"error": step.get("error", "gagal")})
                    if step.get("json") is not None:
                        return self._send(200, step["json"])
                model = body.get("model") or (fake.models[0] if fake.models else "")
                if self.path == "/api/generate":
                    keep = body.get("keep_alive")
                    with fake.lock:
                        if keep == 0:
                            fake.loaded = [m for m in fake.loaded if m != model]
                        elif model not in fake.loaded:
                            fake.loaded.append(model)
                    return self._send(200, {"response": "" if not body.get("prompt") else fake.reply,
                                            "done": True, "model": model, "eval_count": fake.eval_count,
                                            "eval_duration": int(fake.eval_seconds * 1e9)})
                with fake.lock:
                    if model not in fake.loaded:
                        fake.loaded.append(model)
                self._send(200, {"model": model, "done": True, "eval_count": fake.eval_count,
                                 "eval_duration": int(fake.eval_seconds * 1e9),
                                 "prompt_eval_count": 12, "load_duration": 1,
                                 "message": {"role": "assistant", "content": fake.reply}})

        class Server(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass  # klien memutus koneksi (timeout / uji crash): bukan masalah, jangan cetak galat

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def posts(self, path):
        return [body for p, body in self.requests if p == path]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def dead_url():
    """Alamat loopback yang tidak ada yang mendengarkan (Ollama mati)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"
