"""Undangan sekali pakai untuk komputer baru.

Bot It's Me (perintah /pasang) membuat kode acak dan menitipkannya ke node di
komputernya lewat 127.0.0.1, bersama isi .env bot. Komputer baru yang menjalankan
install.ps1 dengan kode itu mencarinya di jaringan Tailscale dan menerima kode
gabung + isi .env tersebut, sehingga pemilik tidak perlu mengetik apa pun.

Kode hanya berlaku sekali, kedaluwarsa sendiri, dan disimpan di memori saja.
Tebakan yang salah dibatasi supaya kode tidak bisa ditebak.
"""

import hmac
import threading
import time

MAX_TTL = 3600
MAX_ITEMS = 20
# Paling banyak sekian tebakan salah per jendela waktu (detik).
MAX_FAILS, FAIL_WINDOW = 30, 600


class Invites:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._items = {}  # kode -> (kedaluwarsa, env)
        self._fails = []

    def _purge(self, now):
        self._items = {c: v for c, v in self._items.items() if v[0] > now}
        self._fails = [t for t in self._fails if now - t < FAIL_WINDOW]

    def add(self, code, ttl, env):
        code = str(code or "").strip().upper()
        if len(code) < 10:
            raise ValueError("kode undangan terlalu pendek")
        env = {str(k): str(v) for k, v in (env or {}).items()}
        ttl = max(60, min(int(ttl or 1800), MAX_TTL))
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._items) >= MAX_ITEMS:
                self._items.pop(min(self._items, key=lambda c: self._items[c][0]))
            self._items[code] = (now + ttl, env)

    def redeem(self, code):
        """env undangan (lalu kodenya hangus), atau None kalau salah/kedaluwarsa.
        Melempar PermissionError kalau terlalu banyak tebakan salah."""
        code = str(code or "").strip().upper()
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._fails) >= MAX_FAILS:
                raise PermissionError("terlalu banyak percobaan, coba lagi nanti")
            for known in list(self._items):
                if hmac.compare_digest(known.encode(), code.encode()):
                    return self._items.pop(known)[1]
            self._fails.append(now)
            return None
