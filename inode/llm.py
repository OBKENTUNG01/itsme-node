"""Model lokal lewat Ollama (HTTP, tanpa library tambahan)."""

import json
import time
import urllib.error
import urllib.request


def _post(base, path, payload, timeout=300):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def available(base):
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=3):
            return True
    except OSError:
        return False


def installed(base):
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (OSError, ValueError):
        return []


def pull(base, model):
    try:
        _post(base, "/api/pull", {"model": model, "stream": False}, timeout=3600)
        return model in installed(base)
    except OSError:
        return False


def unload(base, model):
    """Lepas model dari memori, supaya komputer lega saat dipakai pemiliknya."""
    try:
        _post(base, "/api/generate", {"model": model, "keep_alive": 0}, timeout=30)
    except (OSError, ValueError):
        pass


def generate(base, model, prompt, max_tokens=300, timeout=300):
    payload = {
        "model": model, "prompt": prompt, "stream": False, "think": False,
        "options": {"num_predict": max_tokens, "num_ctx": 4096, "temperature": 0.3},
    }
    try:
        data = _post(base, "/api/generate", payload, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code != 500:
            raise
        time.sleep(3)  # runner Ollama kadang gagal sesaat ketika model dimuat ulang
        data = _post(base, "/api/generate", payload, timeout=timeout)
    return data.get("response", "").strip(), data


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
