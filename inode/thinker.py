"""Layanan BERPIKIR: komputer ini ikut berpikir untuk bot It's Me ("otak lokal bersama").

Bot It's Me (di komputer pemimpin) membagi permintaan berpikir ke komputer yang sudah bergabung lewat
rute /think (satu permintaan dikerjakan satu node). Agar komputer ini tetap siap sebisa mungkin, walau
sedang dipakai pemiliknya untuk mengetik atau lainnya:

- Ollama dijaga hidup: kalau mati, dinyalakan lagi (jeda makin panjang kalau terus gagal).
- Model TETAP termuat di memori (keep_alive -1), tidak dilepas saat komputer dipakai.
- Ollama berjalan berprioritas RENDAH (best-effort Windows): proses lain lebih mudah mendapat giliran CPU,
  tetapi ini bukan batas CPU/RAM dan tidak menjamin jendela pemilik bebas tersendat.
- Windows dicegah tidur otomatis hanya selama ada permintaan berpikir/antrean aktif; saat idle komputer
  boleh tidur. Tombol Sleep / tutup laptop oleh pemiliknya tetap berlaku.
- Riset latar (crawler) mengalah: selama ada permintaan berpikir, riset menunggu.

Berhenti berpikir hanya dalam SATU keadaan, sesuai rancangan jaringan (cluster.py): saat pemimpin
mati dan pemilihan pemimpin baru berjalan (fase "electing"), semua komputer berhenti sampai
pemimpin baru terdengar - lalu langsung berpikir lagi.
"""

import ctypes
import threading
import time

from . import llm, system
from .cluster import log

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

MAX_TIMEOUT = 3600.0
DEFAULT_TIMEOUT = 900.0
QUEUE_WAIT = 600.0       # paling lama permintaan menunggu giliran di komputer ini (detik)
MAX_BODY_CHARS = 2_000_000
# Opsi model yang boleh diatur pemanggil; sisanya (mis. num_gpu) diurus node ini sendiri.
OPTION_KEYS = {"temperature", "top_p", "top_k", "num_predict", "seed", "repeat_penalty",
               "presence_penalty", "frequency_penalty", "num_ctx", "stop"}


def default_ctx(ram_gb):
    """Konteks (token) yang aman untuk RAM komputer ini. Makin besar makin 'luas' ingatannya."""
    if ram_gb >= 24:
        return 32768
    if ram_gb >= 12:
        return 16384
    if ram_gb >= 8:
        return 8192
    return 4096


def clean_options(options):
    out = {}
    for key, value in (options or {}).items():
        if key not in OPTION_KEYS:
            continue
        if key == "stop":
            if isinstance(value, list) and all(isinstance(s, str) for s in value):
                out[key] = value[:8]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = value
    return out


class Thinker:
    def __init__(self, cfg, profile, cluster):
        self.cfg, self.profile, self.cluster = cfg, profile, cluster
        self.url = cfg["ollama_url"]
        self.enabled = bool(cfg.get("think", True))
        self.ram = float(profile.get("ram") or system.ram_gb())
        self.parallel = int(cfg.get("think_parallel") or 0) or (2 if float(profile.get("vram") or 0) >= 6 else 1)
        self.queue_max = max(1, int(cfg.get("think_queue") or 4))
        self._sem = threading.Semaphore(self.parallel)
        self._lock = threading.Lock()
        self.inflight = 0
        self.waiting = 0
        self.served = 0
        self.failed = 0
        self.tps = 0.0          # rata-rata bergerak token/detik hasil kerja nyata
        self.up = False         # Ollama menjawab
        self.loaded = False     # model terpilih sedang termuat
        self.ctl = None         # 'think: false' dipatuhi model? None = belum diuji
        self.restarts = 0
        self.started = time.time()
        self.last_end = 0.0
        self._fails = 0
        self._next_restart = 0.0
        self._last_warm = 0.0
        self._last_prio = 0.0
        self._last_probe = 0.0
        self._lowered = set()

    # --- keadaan ----------------------------------------------------------------------------
    def model(self):
        forced = (self.cfg.get("think_model") or "").strip()
        if forced and forced != "auto":
            return forced
        return (self.profile.get("model") or "").strip()

    def busy(self):
        """Sedang (atau baru saja) berpikir: riset latar harus mengalah."""
        return self.inflight > 0 or self.waiting > 0 or (time.time() - self.last_end) < 5.0

    def health(self):
        """Ringkasan yang ikut bergosip ke semua node. Murah: tanpa I/O, tanpa kunci lama."""
        model = self.model() if self.enabled else ""
        known = self.ctl if self.ctl is not None else llm.known_ctl(model)
        return {
            "model": model, "ok": bool(model and self.up), "up": self.up, "loaded": self.loaded,
            "ctl": known, "tps": round(self.tps or float(self.profile.get("tps") or 0.0), 1),
            "inflight": self.inflight, "waiting": self.waiting, "slots": self.parallel,
            "served": self.served, "restarts": self.restarts, "cpu": bool(model and llm.cpu_only(model)),
            "ctx": default_ctx(self.ram), "ram": self.ram, "since": int(self.started),
        }

    # --- melayani permintaan ----------------------------------------------------------------
    def _pick_model(self, wanted):
        mine = self.model()
        if wanted and llm.same_model(wanted, mine):
            return mine
        return mine  # satu model per komputer: yang dipilih tes kemampuan (atau think_model)

    def handle(self, payload):
        """Layani satu permintaan berpikir. None = node ini sedang tidak boleh berpikir (503)."""
        if not self.enabled:
            return {"ok": False, "kind": "disabled", "error": "berpikir dimatikan di komputer ini"}
        if self.cluster.paused:
            # Pemimpin mati dan pemilihan baru berjalan: SEMUA komputer berhenti berpikir sampai
            # pemimpin baru terdengar (rancangan jaringan, lihat cluster.py).
            return None
        model = self._pick_model(payload.get("model"))
        if not model:
            return {"ok": False, "kind": "no_model",
                    "error": "komputer ini belum punya model (RAM kecil atau Ollama belum terpasang)"}
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
            return {"ok": False, "kind": "bad", "error": "messages harus berupa daftar pesan"}
        if sum(len(str(m.get("content") or "")) for m in messages) > MAX_BODY_CHARS:
            return {"ok": False, "kind": "bad", "error": "pesan terlalu besar"}
        try:
            timeout = min(max(float(payload.get("timeout") or DEFAULT_TIMEOUT), 5.0), MAX_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        options = clean_options(payload.get("options"))
        options.setdefault("num_ctx", default_ctx(self.ram))
        think = payload.get("think")
        think = think if isinstance(think, bool) else None
        tools = payload.get("tools") if isinstance(payload.get("tools"), list) else None
        fmt = payload.get("format")
        fmt = fmt if isinstance(fmt, (str, dict)) and fmt else None

        t0 = time.time()
        with self._lock:
            if self.waiting >= self.queue_max:
                return {"ok": False, "kind": "busy", "error": "antrean berpikir di komputer ini penuh"}
            self.waiting += 1
        got = self._sem.acquire(timeout=min(timeout, QUEUE_WAIT))
        with self._lock:
            self.waiting -= 1
        if not got:
            return {"ok": False, "kind": "busy", "error": "menunggu giliran terlalu lama"}
        try:
            if self.cluster.paused:
                return None
            with self._lock:
                self.inflight += 1
            queued = time.time() - t0
            try:
                message, stats = llm.chat(self.url, model, messages, options=options, think=think,
                                          tools=tools, fmt=fmt, keep_alive=-1,
                                          timeout=max(5.0, timeout - queued))
            except llm.ThinkError as exc:
                self.failed += 1
                if exc.kind in ("unavailable", "crashed"):
                    self.up = exc.kind != "unavailable" and self.up
                return {"ok": False, "kind": exc.kind, "error": str(exc)}
            finally:
                with self._lock:
                    self.inflight -= 1
                self.last_end = time.time()
            self.up, self.loaded = True, True
            self.served += 1
            count, secs = stats.get("eval_count") or 0, (stats.get("eval_duration") or 0) / 1e9
            tps = (count / secs) if count and secs else 0.0
            if tps > 0:
                self.tps = tps if not self.tps else 0.6 * self.tps + 0.4 * tps
            return {"ok": True, "message": message, "model": stats.get("model") or model,
                    "node": self.cluster.id, "name": self.cfg["name"],
                    "ms": int((time.time() - t0) * 1000), "queued_ms": int(queued * 1000),
                    "eval_count": count, "tps": round(tps, 1), "cpu": llm.cpu_only(model),
                    "ctx": options["num_ctx"]}
        finally:
            self._sem.release()

    # --- penjaga hidup ---------------------------------------------------------------------
    def start(self, stop):
        if not self.enabled:
            log("berpikir dimatikan di komputer ini (think=false)")
            return
        threading.Thread(target=self._supervise, args=(stop,), daemon=True, name="thinker").start()
        if self.cfg.get("think_keep_awake", True):
            threading.Thread(target=self._keep_awake, args=(stop,), daemon=True, name="keep-awake").start()

    def _supervise(self, stop):
        while not stop.is_set():
            try:
                self.tick(time.time())
            except Exception as exc:  # noqa: BLE001 - penjaga tidak boleh ikut mati
                log("penjaga berpikir error:", exc)
            stop.wait(5)

    def tick(self, now):
        """Satu putaran penjagaan (dipanggil tiap 5 detik; terpisah supaya bisa diuji)."""
        model = self.model()
        if not model:
            self.up = llm.available(self.url)
            self.loaded = False
            return
        self.up = llm.available(self.url)
        if not self.up:
            self.loaded = False
            if system.OLLAMA_MAINTENANCE.is_set():
                return  # sedang dipasang/dicopot dari dashboard (ollama_ctl): jangan diganggu
            if now >= self._next_restart:
                self._fails += 1
                self.restarts += 1
                started = system.start_ollama(low_priority=True)
                log("Ollama tidak menjawab; dinyalakan lagi" if started else
                    "Ollama tidak menjawab dan tidak bisa dinyalakan (terpasang?)")
                self._next_restart = now + min(15.0 * self._fails, 300.0)
            return
        self._fails = 0
        if now - self._last_prio > 60:
            self._last_prio = now
            system.lower_ollama_priority(self._lowered)
        self.loaded = any(llm.same_model(model, name) for name in llm.loaded(self.url))
        if not self.loaded and self.inflight == 0 and now - self._last_warm > 45:
            self._last_warm = now
            try:
                llm.warm(self.url, model)
                self.loaded = True
                log(f"model {model} dimuat dan ditahan di memori")
            except llm.ThinkError as exc:
                log(f"model {model} belum bisa dimuat: {exc}")
                return
        if self.loaded and self.ctl is None and self.inflight == 0 and now - self._last_probe > 120:
            self._last_probe = now
            known = llm.known_ctl(model)
            self.ctl = known if known is not None else llm.probe_ctl(self.url, model)
            if self.ctl is not None:
                log(f"model {model}: 'think: false' {'dipatuhi' if self.ctl else 'DIABAIKAN (model selalu berpikir)'}")

    def _keep_awake(self, stop):
        """Cegah tidur otomatis hanya selama permintaan berpikir aktif (Windows); lepaskan saat idle/berhenti.

        ES_CONTINUOUS mempertahankan pilihan sampai panggilan berikutnya, jadi cukup panggil saat status berubah;
        jangan menahan ES_SYSTEM_REQUIRED terus-menerus saat node menganggur.
        """
        if not system.IS_WIN:
            return
        try:
            kernel = ctypes.windll.kernel32
        except (AttributeError, OSError):
            return
        held = False
        warned = False
        try:
            while not stop.is_set():
                active = self.busy()
                if active and not held:
                    ok = bool(kernel.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
                    held = ok
                    if not ok and not warned:
                        log("Windows gagal menahan tidur otomatis saat berpikir")
                    warned = not ok
                elif not active and held:
                    ok = bool(kernel.SetThreadExecutionState(ES_CONTINUOUS))
                    held = not ok
                    if not ok and not warned:
                        log("Windows gagal melepas permintaan pencegah tidur")
                    warned = not ok
                stop.wait(1.0)
        finally:
            if held and not kernel.SetThreadExecutionState(ES_CONTINUOUS) and not warned:
                log("Windows gagal melepas permintaan pencegah tidur saat node berhenti")
