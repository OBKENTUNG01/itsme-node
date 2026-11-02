"""Model lokal lewat Ollama (HTTP, tanpa library tambahan).

Dipakai untuk dua hal: merangkum halaman web saat riset latar, dan BERPIKIR untuk bot It's Me
(thinker.py -> rute /think). Karena komputer pemilik bermacam-macam, panggilan chat/generate
memakai "tangga keamanan": kalau proses model di Ollama mati mendadak (biasanya driver atau
memori GPU, mis. "ggml_vulkan: device lost" di kartu grafis lama), panggilan diulang dengan
pengaturan lebih aman - konteks lebih kecil, lalu CPU saja. Tingkat yang akhirnya jalan diingat
(think_state.json) supaya node tidak crash lagi tiap kali menyala.
"""

import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

# Tingkat 0 = pengaturan biasa, 1 = konteks lebih kecil, 2 = CPU saja.
SAFE = ({}, {"num_ctx": 8192}, {"num_ctx": 8192, "num_gpu": 0})
# Tanda di jawaban Ollama bahwa proses modelnya (runner) mati mendadak.
CRASH_SIGNS = (
    "error was encountered while running the model",
    "runner has unexpectedly stopped",
    "process has terminated",
    "process no longer running",
    "out-of-memory",
    "out of memory",
    "requires more system memory",
    "error starting llama-server",
    "startup failed",
    "timed out waiting for llama-server",
    "forcibly closed",
    "unexpected eof",
)
# Tanda di log Ollama bahwa yang mati adalah GPU-nya: langsung pakai CPU saja.
GPU_CRASH_SIGNS = ("device lost", "vk_error_device_lost", "cuda error", "cudaerror", "hiperror")
# Jeda sebelum mengulang setelah crash (detik); uji menggantinya dengan 0.
RETRY_PAUSE = 3.0

# Awal teks yang menandakan model menulis proses berpikirnya (bahasa Inggris) di dalam jawaban.
# Sengaja sempit: "Ok, siap" atau "Okay" saja adalah jawaban Indonesia yang wajar.
_REASONING_START = re.compile(
    r"^\s*(<think>"
    r"|okay,? (so|let|the user)\b|alright,? (so|let)\b"
    r"|let me (think|see|start|figure|check|recall)\b|let's (see|think|start|break)\b"
    r"|hmm[,.…]|so,? the user\b|the user (is|wants|asks|asked)\b"
    r"|first,? i (need|should|'ll)\b|i need to (figure|find|determine|confirm|check)\b|wait,)",
    re.IGNORECASE,
)

_lock = threading.RLock()
_state = {"path": None, "levels": {}, "ctl": {}}
_no_think_param = set()  # model yang menolak parameter "think" sama sekali


class ThinkError(OSError):
    """Ollama tidak bisa menjawab. kind: unavailable | timeout | no_model | crashed | busy | bad."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


# --- keadaan yang diingat ---------------------------------------------------------------------
def configure(data_dir):
    """Tentukan folder data (think_state.json) dan muat tingkat/uji yang tersimpan."""
    with _lock:
        _state["path"] = os.path.join(data_dir, "think_state.json") if data_dir else None
        _state["levels"], _state["ctl"] = {}, {}
        if not _state["path"]:
            return
        try:
            with open(_state["path"], encoding="utf-8") as f:
                data = json.load(f)
            for name, level in (data.get("levels") or {}).items():
                if isinstance(level, int) and 0 <= level < len(SAFE):
                    _state["levels"][str(name)] = level
            for name, flag in (data.get("ctl") or {}).items():
                if isinstance(flag, bool):
                    _state["ctl"][str(name)] = flag
        except (OSError, ValueError, AttributeError):
            pass


def _save():
    path = _state["path"]
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"levels": _state["levels"], "ctl": _state["ctl"]}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def level_for(model):
    with _lock:
        return _state["levels"].get(model, 0)


def cpu_only(model):
    return level_for(model) >= len(SAFE) - 1


def known_ctl(model):
    """True/False kalau 'think: false' sudah pernah diuji untuk model ini, None kalau belum."""
    with _lock:
        return _state["ctl"].get(model)


def _remember_ctl(model, flag):
    with _lock:
        _state["ctl"][model] = bool(flag)
        _save()


def _bump(model, gpu_crashed=False):
    with _lock:
        last = len(SAFE) - 1
        new = last if gpu_crashed else min(_state["levels"].get(model, 0) + 1, last)
        _state["levels"][model] = new
        _save()
    print(time.strftime("[%H:%M:%S]"), f"model {model} berhenti mendadak -> pengaturan lebih aman (tingkat {new})",
          flush=True)
    return new


def _ollama_log_path():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return os.path.join(base, "Ollama", "server.log") if base else None
    if sys.platform == "darwin":
        return os.path.expanduser("~/.ollama/logs/server.log")
    return None


def gpu_crashed():
    """Lihat ujung log Ollama: apakah crash terakhir terjadi di GPU?"""
    path = _ollama_log_path()
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 32768))
            tail = f.read().decode("utf-8", "replace").lower()
    except OSError:
        return False
    return any(sign in tail for sign in GPU_CRASH_SIGNS)


# --- HTTP dasar -------------------------------------------------------------------------------
def _post(base, path, payload, timeout=300):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(base, path, timeout=5):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def _detail(err):
    try:
        raw = err.read().decode("utf-8", "replace")
    except (OSError, AttributeError):
        raw = ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except ValueError:
        pass
    return raw[:300]


def _merge(options, safe):
    out = dict(options or {})
    for key, value in safe.items():
        if key == "num_ctx":
            out["num_ctx"] = min(int(out.get("num_ctx") or value), value)
        else:
            out[key] = value
    return out


def _crash_like(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, (ConnectionResetError, ConnectionAbortedError, http.client.RemoteDisconnected,
                               http.client.IncompleteRead, BrokenPipeError))


def _timed_out(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, TimeoutError)


def _call(base, path, body, model, timeout):
    """POST dengan tangga keamanan. Kembalikan JSON jawaban; lempar ThinkError."""
    body = dict(body)
    for _ in range(len(SAFE) + 2):
        level = level_for(model)
        sent = dict(body)
        sent["options"] = _merge(body.get("options"), SAFE[level])
        try:
            return _post(base, path, sent, timeout)
        except urllib.error.HTTPError as exc:
            detail = _detail(exc)
            low = detail.lower()
            if exc.code == 404 and "not found" in low:
                raise ThinkError("no_model", f"model '{model}' belum ada di komputer ini") from None
            if exc.code == 400 and "think" in body and "support" in low and "think" in low:
                body.pop("think", None)  # model tanpa mode berpikir menolak parameternya
                _no_think_param.add(model)
                continue
            if exc.code == 503 and ("busy" in low or "pending" in low):
                # Antrean Ollama penuh: ini sibuk, BUKAN crash - jangan turunkan pengaturan.
                raise ThinkError("busy", f"Ollama sedang sibuk: {detail}") from None
            if exc.code >= 500 or any(sign in low for sign in CRASH_SIGNS):
                if level < len(SAFE) - 1:
                    _bump(model, gpu_crashed())
                    time.sleep(RETRY_PAUSE)
                    continue
                raise ThinkError("crashed", f"Ollama berhenti mendadak setiap kali, juga dengan CPU saja: {detail}") from None
            raise ThinkError("bad", f"Ollama menolak permintaan ({exc.code}): {detail}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if _timed_out(exc):
                raise ThinkError("timeout", "Ollama terlalu lama menjawab") from None
            if _crash_like(exc):
                if level < len(SAFE) - 1:
                    _bump(model, gpu_crashed())
                    time.sleep(RETRY_PAUSE)
                    continue
                raise ThinkError("crashed", "koneksi ke Ollama terputus setiap kali, juga dengan CPU saja") from None
            raise ThinkError("unavailable", f"Ollama tidak bisa dihubungi: {exc}") from None
        except ValueError:
            raise ThinkError("bad", "jawaban Ollama bukan JSON") from None
    raise ThinkError("crashed", "Ollama tetap gagal setelah semua pengaturan aman dicoba")


# --- status & pengelolaan model ---------------------------------------------------------------
def available(base):
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=3):
            return True
    except OSError:
        return False


def installed(base):
    try:
        return [m["name"] for m in _get(base, "/api/tags", 5).get("models", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def loaded(base):
    """Nama model yang sedang termuat di memori Ollama."""
    try:
        return [m.get("name") or m.get("model") or "" for m in _get(base, "/api/ps", 4).get("models", [])]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def same_model(a, b):
    """Nama model sama? Akhiran ':latest' diabaikan ('qwen3' sama dengan 'qwen3:latest')."""
    def norm(name):
        name = (name or "").strip()
        return name[: -len(":latest")] if name.endswith(":latest") else name

    return bool(a) and norm(a) == norm(b)


def pull(base, model):
    try:
        _post(base, "/api/pull", {"model": model, "stream": False}, timeout=3600)
        return any(same_model(model, name) for name in installed(base))
    except OSError:
        return False


def unload(base, model):
    """Lepas model dari memori, supaya komputer lega saat dipakai pemiliknya."""
    try:
        _post(base, "/api/generate", {"model": model, "keep_alive": 0}, timeout=30)
    except (OSError, ValueError):
        pass


def warm(base, model, timeout=900):
    """Muat model ke memori dan TAHAN selamanya (keep_alive -1), tanpa menghasilkan teks."""
    _call(base, "/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": -1}, model, timeout)


# --- membuat teks -----------------------------------------------------------------------------
def generate(base, model, prompt, max_tokens=300, timeout=300):
    payload = {
        "model": model, "prompt": prompt, "stream": False, "think": False,
        "options": {"num_predict": max_tokens, "num_ctx": 4096, "temperature": 0.3},
    }
    data = _call(base, "/api/generate", payload, model, timeout)
    return data.get("response", "").strip(), data


def chat(base, model, messages, *, options=None, think=None, tools=None, fmt=None, keep_alive=-1, timeout=600):
    """Satu giliran chat. Kembalikan (message, statistik); lempar ThinkError.

    think: True/False untuk model yang punya mode berpikir; None = bawaan model.
    fmt: "json" (atau skema JSON) untuk jawaban terstruktur."""
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": keep_alive,
            "options": dict(options or {})}
    if think is not None and model not in _no_think_param:
        body["think"] = bool(think)
    if tools:
        body["tools"] = tools
    if fmt:
        body["format"] = fmt
    data = _call(base, "/api/chat", body, model, timeout)
    message = data.get("message")
    if not isinstance(message, dict):
        raise ThinkError("bad", "jawaban Ollama tidak berisi pesan")
    stats = {k: data.get(k) for k in ("eval_count", "eval_duration", "prompt_eval_count",
                                       "prompt_eval_duration", "load_duration", "total_duration")}
    stats["model"] = data.get("model") or model
    return message, stats


def looks_like_reasoning(text):
    """Teks jawaban yang sebenarnya proses berpikir model (bukan jawaban)."""
    text = (text or "").strip()
    return bool(_REASONING_START.match(text)) or "<think>" in text.lower()


def probe_ctl(base, model, timeout=600):
    """Apakah 'think: false' benar-benar dipatuhi model ini? True = jawaban langsung; False = model
    selalu berpikir (proses berpikirnya bocor ke jawaban atau jawabannya kosong); None = tak bisa diuji."""
    try:
        message, _ = chat(base, model, [{"role": "user", "content": "Balas hanya dengan satu kata: ya"}],
                          think=False, options={"num_predict": 48, "temperature": 0}, timeout=timeout)
    except ThinkError:
        return None
    content = (message.get("content") or "").strip()
    flag = bool(content) and not (message.get("thinking") or "").strip() and not looks_like_reasoning(content)
    _remember_ctl(model, flag)
    return flag


def speed(base, model):
    """Token per detik (setelah model dimuat)."""
    try:
        generate(base, model, "Hai", max_tokens=1, timeout=600)  # muat model
        t = time.time()
        _, data = generate(base, model, "Sebutkan 10 nama buah dalam satu kalimat.", max_tokens=60)
        n, dur = data.get("eval_count", 0), data.get("eval_duration", 0) / 1e9
        return n / dur if dur else n / max(time.time() - t, 0.01)
    except OSError:
        return 0.0


def summarize(base, model, title, text):
    prompt = (
        "Ringkas halaman web berikut dalam bahasa Indonesia, 3-5 kalimat berisi fakta "
        "terpenting. Baris terakhir: 'Kata kunci:' diikuti 5 kata kunci dipisah koma. "
        "Jangan menambah informasi yang tidak ada di teks.\n\n"
        f"Judul: {title}\n\nTeks:\n{text[:6000]}"
    )
    out, _ = generate(base, model, prompt, max_tokens=260)
    return out


def answer(base, model, question, docs):
    ctx = "\n\n".join(f"[{i + 1}] {d['title']} ({d['url']})\n{d['summary']}"
                      for i, d in enumerate(docs))
    prompt = (
        "Jawab pertanyaan berikut dalam bahasa Indonesia hanya berdasarkan catatan riset. "
        "Sebutkan nomor sumber [n]. Kalau catatannya tidak cukup, katakan terus terang.\n\n"
        f"Catatan:\n{ctx}\n\nPertanyaan: {question}"
    )
    out, _ = generate(base, model, prompt, max_tokens=400)
    return out
