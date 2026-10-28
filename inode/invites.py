"""Undangan untuk komputer baru: sekali pakai (bawaan), atau bisa dipakai ULANG
untuk beberapa komputer dalam satu kode (reusable=True, dari /pasang_tetap).

Bot It's Me (perintah /pasang atau /pasang_tetap) membuat kode acak dan
menitipkannya ke node di komputernya lewat 127.0.0.1, bersama isi .env bot.
Komputer baru yang menjalankan install.ps1 dengan kode itu menukarnya dan
menerima kode gabung + isi .env tersebut, sehingga pemilik tidak perlu
mengetik apa pun.

Kode sekali-pakai hangus setelah ditukar SEKALI. Kode reusable TIDAK hangus
dipakai (dipakai lagi oleh komputer berikutnya), tapi tetap kedaluwarsa
sendiri (lebih lama, tapi tidak selamanya - sengaja dibatasi, bukan rahasia
abadi yang kalau bocor bisa dipakai siapa pun tanpa batas waktu) dan bisa
diganti kapan saja lewat /pasang_tetap lagi (kode lama langsung tak berlaku,
hanya SATU kode reusable aktif sekaligus).

Disimpan di memori saja (hilang kalau node dinyalakan ulang - sengaja, supaya
tidak ada kode lama yang diam-diam tetap hidup setelah pembaruan/restart).
Tebakan yang salah dibatasi supaya kode tidak bisa ditebak.
"""

import hmac
import threading
import time

MAX_TTL = 3600
MAX_REUSABLE_TTL = 30 * 24 * 3600  # 30 hari - lama tapi tetap ada batasnya
MAX_ITEMS = 20
# Paling banyak sekian tebakan salah per jendela waktu (detik).
MAX_FAILS, FAIL_WINDOW = 30, 600


class Invites:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._items = {}  # kode -> (kedaluwarsa, env, reusable)
        self._fails = []

    def _purge(self, now):
        self._items = {c: v for c, v in self._items.items() if v[0] > now}
        self._fails = [t for t in self._fails if now - t < FAIL_WINDOW]

    def add(self, code, ttl, env, reusable=False):
        code = str(code or "").strip().upper()
        if len(code) < 10:
            raise ValueError("kode undangan terlalu pendek")
        env = {str(k): str(v) for k, v in (env or {}).items()}
        cap = MAX_REUSABLE_TTL if reusable else MAX_TTL
        ttl = max(60, min(int(ttl or 1800), cap))
        with self._lock:
            now = self._clock()
            self._purge(now)
            if reusable:
                # Hanya satu kode tetap aktif sekaligus - kode reusable lama
                # (kalau ada) langsung tak berlaku saat kode baru dibuat.
                for c, v in list(self._items.items()):
                    if v[2]:
                        del self._items[c]
            if len(self._items) >= MAX_ITEMS:
                self._items.pop(min(self._items, key=lambda c: self._items[c][0]))
            self._items[code] = (now + ttl, env, bool(reusable))

    def redeem(self, code):
        """env undangan (kode sekali-pakai lalu hangus; kode reusable TETAP
        aktif untuk komputer berikutnya), atau None kalau salah/kedaluwarsa.
        Melempar PermissionError kalau terlalu banyak tebakan salah."""
        code = str(code or "").strip().upper()
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._fails) >= MAX_FAILS:
                raise PermissionError("terlalu banyak percobaan, coba lagi nanti")
            for known in list(self._items):
                if hmac.compare_digest(known.encode(), code.encode()):
                    expiry, env, reusable = self._items[known]
                    if not reusable:
                        del self._items[known]
                    return env
            self._fails.append(now)
            return None
