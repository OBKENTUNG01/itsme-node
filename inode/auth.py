"""Tanda tangan permintaan antar-node (HMAC-SHA256).

Dulu setiap permintaan antar-node membawa token statis ("Authorization: Bearer
<token>") lewat HTTP polos. Di LAN bersama token itu bisa diendus, padahal itu
satu-satunya syarat untuk mengendalikan layar, keyboard, dan berkas komputer
lain. Sekarang permintaan DITANDATANGANI: kuncinya tidak pernah melintas di
kabel, dan tiap tanda tangan terikat ke metode, alamat, isi, dan waktu, serta
tidak bisa diputar ulang (nonce sekali pakai).

Node lama (belum diperbarui) hanya mengerti Bearer, jadi untuk sementara:
- server menerima tanda tangan ATAU Bearer (config "accept_legacy_bearer",
  bawaan True);
- klien mencoba tanda tangan dulu, jatuh ke Bearer hanya bila ditolak (401/403),
  lalu mengingat alamat itu sebagai "lama" selama LEGACY_RETRY detik.
Setelah semua komputer diperbarui, "accept_legacy_bearer" dimatikan: token yang
bocor pun tak berguna lagi.
"""

import hashlib
import hmac
import secrets
import threading
import time
import urllib.error
import urllib.request

MAX_SKEW = 60.0       # selisih jam yang masih diterima (detik)
NONCE_TTL = 150.0     # nonce diingat sekian detik (> 2 x MAX_SKEW)
NONCE_CAP = 20000     # batas keras ingatan nonce
LEGACY_RETRY = 600.0  # alamat "lama" dicoba lagi dengan tanda tangan setelah sekian detik

_legacy = {}  # alamat -> waktu (epoch) sampai kapan langsung pakai Bearer


def sign_key(cfg):
    """Kunci tanda tangan, diturunkan dari kode gabung (BUKAN dari token Bearer,
    supaya token lama yang terlanjur bocor tidak bisa dipakai memalsukan tanda
    tangan)."""
    code = ""
    try:
        code = cfg.get("join_code") or ""
    except AttributeError:
        pass
    return hashlib.sha256(("itsme-sign:" + (code or cfg.token)).encode()).digest()


def _message(method, target, ts, nonce, body):
    return "|".join([method.upper(), target, str(ts), nonce,
                     hashlib.sha256(body or b"").hexdigest()]).encode()


def sign_headers(cfg, method, target, body=b""):
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    sig = hmac.new(sign_key(cfg), _message(method, target, ts, nonce, body),
                   hashlib.sha256).hexdigest()
    return {"X-Itsme-Ts": ts, "X-Itsme-Nonce": nonce, "X-Itsme-Sig": sig}


class Verifier:
    """Memeriksa tanda tangan di server; mengingat nonce supaya tak bisa diputar ulang."""

    def __init__(self):
        self._seen = {}
        self._lock = threading.Lock()

    def check(self, cfg, method, target, body, headers):
        ts, nonce, sig = (headers.get("X-Itsme-Ts"), headers.get("X-Itsme-Nonce"),
                          headers.get("X-Itsme-Sig"))
        if not (ts and nonce and sig):
            return False
        try:
            stamp = float(ts)
        except ValueError:
            return False
        now = time.time()
        if abs(now - stamp) > MAX_SKEW:
            return False
        good = hmac.new(sign_key(cfg), _message(method, target, ts, nonce, body),
                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig):
            return False
        with self._lock:
            for n in [n for n, exp in self._seen.items() if exp < now]:
                del self._seen[n]
            if nonce in self._seen:
                return False
            if len(self._seen) >= NONCE_CAP:
                self._seen.pop(min(self._seen, key=self._seen.get))
            self._seen[nonce] = now + NONCE_TTL
        return True


def http_request(cfg, addr, method, target, body=None, timeout=5, headers=None, legacy=None):
    """Satu permintaan HTTP ke node lain. Kembalikan (kode, content-type, isi, disposisi).

    Melempar OSError bila node tidak terjangkau (seperti urllib). Mencoba tanda
    tangan dulu; bila ditolak 401/403, jatuh ke Bearer (node lama) dan mengingat
    alamat itu sebagai "lama" sebentar."""
    cache = _legacy if legacy is None else legacy
    modes = ("bearer",) if cache.get(addr, 0) > time.time() else ("sig", "bearer")
    for mode in modes:
        req = urllib.request.Request(f"http://{addr}{target}", data=body, method=method,
                                     headers=dict(headers or {}))
        if mode == "sig":
            for k, v in sign_headers(cfg, method, req.selector, body or b"").items():
                req.add_header(k, v)
        else:
            req.add_header("Authorization", "Bearer " + cfg.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if mode == "bearer" and len(modes) == 2:
                    cache[addr] = time.time() + LEGACY_RETRY
                return (r.status, r.headers.get("Content-Type", ""), r.read(),
                        r.headers.get("Content-Disposition", ""))
        except urllib.error.HTTPError as e:
            if mode == "sig" and e.code in (401, 403):
                continue
            return e.code, e.headers.get("Content-Type", "application/json"), e.read(), ""
