"""Akses jarak jauh ke komputer di jaringan: tangkap layar, kirim klik/ketik, dan
telusuri berkas. Hanya pustaka bawaan Python (ctypes untuk Windows).

Dipakai server.py: komputer yang kamu lihat menyajikan layar & berkasnya; komputer
tempat kamu duduk meneruskan permintaan ke sana lewat Tailscale (lihat server.py).

Aman: berkas rahasia (.env, kunci, token, login browser/aplikasi) tidak boleh
dibaca/diunduh walau berkasnya tetap terlihat di daftar.
"""

import math
import os
import re
import struct
import subprocess
import sys
import threading
import time
import zlib

IS_WIN = sys.platform == "win32"
_cap_lock = threading.Lock()

# "Dipakai" kini berarti SEDANG DIAKSES LEWAT WEB (layar/kendali/berkas dari
# dashboard), BUKAN ada ketikan/klik lokal - pemilik yang mengetik di komputer
# sendiri tidak boleh menghentikan riset latar; hanya akses jarak jauh yang
# menghentikannya. touch_remote_access() dipanggil server.py tiap permintaan
# jarak jauh yang dilayani KOMPUTER INI SENDIRI (bukan diteruskan ke node lain).
_last_remote_access = 0.0
_remote_lock = threading.Lock()


def touch_remote_access():
    global _last_remote_access
    with _remote_lock:
        _last_remote_access = time.time()


def remote_idle_seconds():
    """Detik sejak akses jarak jauh TERAKHIR (layar/kendali/berkas). Besar
    sekali kalau belum pernah diakses sejak komputer ini menyala."""
    with _remote_lock:
        last = _last_remote_access
    return (time.time() - last) if last else 10**9

# Berkas yang tidak boleh dibaca/diunduh isinya (tetap muncul di daftar, ditandai).
_SECRET = re.compile(
    r"(^\.env$|\.env\.|(^|[._-])(secret|secrets|password|passwd|credential|credentials)s?([._-]|$)"
    r"|\.(key|pem|pfx|p12|ppk|keystore|jks)$|(^|[._-])id_(rsa|ed25519|ecdsa|dsa)(\.|$)"
    r"|(^|[._-])token(s)?([._-]|$)|cookies?(\.sqlite)?$|login data|key[34]\.db|logins\.json"
    r"|drive_token|drive_credentials|\.npmrc$|\.netrc$)", re.I)
# Folder yang isinya rahasia (login aplikasi/browser); daftar boleh, baca tidak.
_SECRET_DIR = re.compile(r"[\\/](\.ssh|\.aws|\.gnupg|\.claude)([\\/]|$)", re.I)


def is_secret(path):
    base = os.path.basename(path.rstrip("\\/")) or path
    return bool(_SECRET.search(base) or _SECRET_DIR.search(path))


def _target(path):
    """Path sebenarnya (symlink diselesaikan) supaya is_secret tak bisa diakali."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path or "")))


def _hardlinked(path):
    """True bila berkas punya nama lain (hardlink) -> nama aslinya tak bisa diperiksa."""
    try:
        return os.stat(path).st_nlink > 1
    except OSError:
        return False


# =============================== LAYAR =======================================
def _png(width, height, rgba):
    """Encode RGBA (baris dari atas ke bawah) jadi PNG, hanya pakai zlib."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter "none" per baris
        raw += rgba[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def _win_api():
    import ctypes
    from ctypes import wintypes

    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor, agar ukuran benar
    except OSError:
        u.SetProcessDPIAware()
    u.GetDC.restype, u.GetDC.argtypes = wintypes.HDC, [wintypes.HWND]
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.GetSystemMetrics.restype, u.GetSystemMetrics.argtypes = ctypes.c_int, [ctypes.c_int]
    g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = wintypes.HDC, [wintypes.HDC]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype, g.SelectObject.argtypes = wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ]
    g.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
    g.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.DWORD]
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    return ctypes, wintypes, u, g


def screen_size():
    """(lebar, tinggi) layar utama, atau (0, 0) kalau tidak ada layar."""
    if not IS_WIN:
        return (0, 0)
    ctypes, _, u, _ = _win_api()
    return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))


# =============================== KURSOR ======================================
# Tangkapan GDI (StretchBlt di capture_png) TIDAK pernah memuat kursor mouse - kursor
# adalah lapisan terpisah di atas desktop. Jadi dashboard menggambar kursornya sendiri
# di atas gambar layar (seperti Chrome Remote Desktop: kursor digambar di sisi klien,
# bukan bagian video), dari posisi & jenis yang dilaporkan cursor_info() di sini.
#
# Kursor standar Windows berbagi handle yang sama dengan LoadCursor(NULL, IDC_*), jadi
# jenisnya bisa dikenali dengan membandingkan handle. Namanya sengaja nama CSS cursor.
_IDC_KIND = {
    32512: "default", 32513: "text", 32514: "wait", 32515: "crosshair", 32516: "default",
    32642: "nwse-resize", 32643: "nesw-resize", 32644: "ew-resize", 32645: "ns-resize",
    32646: "move", 32648: "not-allowed", 32649: "pointer", 32650: "progress", 32651: "help",
}
_CURSOR_API = None


def _cursor_api():
    global _CURSOR_API
    if _CURSOR_API is not None:
        return _CURSOR_API
    ctypes, wintypes, u, _ = _win_api()

    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hCursor", ctypes.c_void_p), ("ptScreenPos", wintypes.POINT)]

    u.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    u.GetCursorInfo.restype = wintypes.BOOL
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.GetCursorPos.restype = wintypes.BOOL
    u.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.LoadCursorW.restype = ctypes.c_void_p
    kinds = {}
    for idc, kind in _IDC_KIND.items():
        h = u.LoadCursorW(None, ctypes.c_void_p(idc))
        if h:
            kinds.setdefault(h, kind)
    _CURSOR_API = (ctypes, wintypes, u, CURSORINFO, kinds)
    return _CURSOR_API


def _cursor_pos():
    """(x, y) kursor sekarang dalam piksel layar (proses sudah DPI-aware, lihat _win_api)."""
    ctypes, wintypes, u, _, _ = _cursor_api()
    p = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def cursor_info():
    """Posisi & jenis kursor di layar utama, atau None kalau tidak bisa dibaca.

    {"x","y": piksel, "w","h": ukuran layar, "nx","ny": 0..1 (relatif layar utama),
     "visible": False kalau kursor disembunyikan (mis. saat mengetik/sentuh),
     "kind": nama CSS cursor ("default", "text", "pointer", ...) atau "other"}."""
    if not IS_WIN:
        return None
    try:
        ctypes, _, u, CURSORINFO, kinds = _cursor_api()
    except (AttributeError, OSError):
        return None
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not u.GetCursorInfo(ctypes.byref(ci)):
        return None
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    if sw <= 0 or sh <= 0:
        return None
    x, y = ci.ptScreenPos.x, ci.ptScreenPos.y
    # Kursor di monitor LAIN (di luar layar utama) tidak punya posisi yang berarti di gambar layar
    # utama: lapor tidak terlihat supaya dashboard tidak menggambarnya menempel di tepi.
    inside = 0 <= x < sw and 0 <= y < sh
    return {"x": x, "y": y, "w": sw, "h": sh,
            "nx": round(min(max(x / max(sw - 1, 1), 0.0), 1.0), 5),
            "ny": round(min(max(y / max(sh - 1, 1), 0.0), 1.0), 5),
            "visible": bool(ci.flags & 1) and inside,  # CURSOR_SHOWING
            "kind": kinds.get(ci.hCursor, "other") if ci.hCursor else "none"}


def _abs_norm(x, y, sw, sh):
    """Piksel -> koordinat absolut SendInput (0..65535) yang mendarat TEPAT di piksel itu
    (Windows memetakan balik dengan floor(n * lebar / 65536)) - diuji nyata, lihat tes."""
    nx = (int(x) * 65536 + sw - 1) // sw
    ny = (int(y) * 65536 + sh - 1) // sh
    return min(nx, 65535), min(ny, 65535)


def capture_png(max_w=1280, quality_div=1):
    """PNG layar utama, diperkecil sampai lebar <= max_w. None kalau tak bisa."""
    if not IS_WIN:
        return None
    ctypes, wintypes, u, g = _win_api()
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    if sw <= 0 or sh <= 0:
        return None
    scale = min(1.0, max_w / float(sw))
    w, h = max(1, int(sw * scale)), max(1, int(sh * scale))

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    with _cap_lock:
        sdc = u.GetDC(None)
        mdc = g.CreateCompatibleDC(sdc)
        bmp = g.CreateCompatibleBitmap(sdc, w, h)
        old = g.SelectObject(mdc, bmp)
        try:
            g.SetStretchBltMode(mdc, 4)  # HALFTONE
            if not g.StretchBlt(mdc, 0, 0, w, h, sdc, 0, 0, sw, sh, 0x00CC0020):  # SRCCOPY
                return None
            bmi = BMI()
            bmi.biSize = ctypes.sizeof(BMI)
            bmi.biWidth, bmi.biHeight = w, -h  # tinggi negatif = baris dari atas
            bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0  # BI_RGB
            buf = (ctypes.c_char * (w * h * 4))()
            if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
                return None
            data = bytearray(buf.raw)  # BGRA
        finally:
            g.SelectObject(mdc, old)
            g.DeleteObject(bmp)
            g.DeleteDC(mdc)
            u.ReleaseDC(None, sdc)
    data[0::4], data[2::4] = data[2::4], data[0::4]  # BGRA -> RGBA
    data[3::4] = b"\xff" * (w * h)                   # alpha penuh
    return _png(w, h, bytes(data))


# =============================== INPUT =======================================
_VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "backspace": 0x08, "delete": 0x2E,
    "escape": 0x1B, "esc": 0x1B, "space": 0x20, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "win": 0x5B, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "shift": 0x10, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


_INPUT_API = None


def _input_api():
    # Didefinisikan sekali lalu disimpan: tipe ctypes yang dibuat ulang tiap panggilan
    # dianggap "tipe berbeda" oleh ctypes, jadi array INPUT akan ditolak.
    global _INPUT_API
    if _INPUT_API is not None:
        return _INPUT_API
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    u = ctypes.windll.user32
    u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _INPUT_API = (ctypes, u, INPUT, MOUSEINPUT, KEYBDINPUT)
    return _INPUT_API


def _send(seq):
    """Kirim daftar INPUT ke Windows. Dipisah supaya bisa diperiksa saat tes."""
    ctypes, u, INPUT, _, _ = _input_api()
    arr = (INPUT * len(seq))(*seq)
    return u.SendInput(len(seq), arr, ctypes.sizeof(INPUT))


def inject(events):
    """Jalankan daftar aksi mouse/keyboard. Lihat viewer.py untuk bentuknya."""
    if not IS_WIN or not events:
        return 0
    _ctypes, _u, INPUT, MOUSEINPUT, KEYBDINPUT = _input_api()
    MOVE, ABS, LDN, LUP = 0x0001, 0x8000, 0x0002, 0x0004
    RDN, RUP, MDN, MUP, WHEEL, HWHEEL = 0x0008, 0x0010, 0x0020, 0x0040, 0x0800, 0x1000
    KEYUP, UNICODE = 0x0002, 0x0004
    btn = {"left": (LDN, LUP), "right": (RDN, RUP), "middle": (MDN, MUP)}
    seq = []

    def mouse(flags, dx=0, dy=0, data=0):
        inp = INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        seq.append(inp)

    def keybd(vk, scan, flags):
        inp = INPUT()
        inp.type = 1  # INPUT_KEYBOARD
        inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        seq.append(inp)

    def key_unicode(ch):
        code = ord(ch)
        keybd(0, code, UNICODE)
        keybd(0, code, UNICODE | KEYUP)

    def key_vk(vk, down=True):
        keybd(vk, 0, 0 if down else KEYUP)

    sw, sh = screen_size()  # juga memastikan proses DPI-aware sebelum GetCursorPos

    def finite(v, default=0.0):
        """Angka terhingga dari isian klien; NaN/inf/teks/None -> default (json.loads Python menerima
        NaN dan 1e999, dan satu isian aneh tidak boleh menggagalkan seluruh batch)."""
        try:
            f = float(v)
        except (TypeError, ValueError, OverflowError):
            return default
        return f if math.isfinite(f) else default

    def absxy(e):
        """x,y klien (0..1, sama dengan nx/ny di cursor_info: piksel / (lebar-1)) -> koordinat
        absolut SendInput yang mendarat TEPAT di piksel itu, bukan "kira-kira"."""
        fx = min(max(finite(e.get("x", 0)), 0.0), 1.0)
        fy = min(max(finite(e.get("y", 0)), 0.0), 1.0)
        if sw > 0 and sh > 0:
            return _abs_norm(round(fx * (sw - 1)), round(fy * (sh - 1)), sw, sh)
        return int(fx * 65535), int(fy * 65535)

    def reldxy(e):
        """dx, dy relatif (unit mouse, bukan skala absolut 0..65535), dijepit supaya satu
        kejadian aneh dari klien tidak bisa melempar kursor jauh di luar kendali."""
        def clamp(v):
            return max(-2000, min(2000, int(finite(v))))
        return clamp(e.get("dx", 0)), clamp(e.get("dy", 0))

    vpos = None  # posisi kursor yang dilacak di dalam satu batch (lihat move_rel)

    for e in events:
        t = e.get("t")
        if t == "move":
            x, y = absxy(e)
            mouse(MOVE | ABS, x, y)
            vpos = None
        elif t == "move_rel":
            # Gerak RELATIF terhadap posisi kursor sekarang, tapi diinjeksi sebagai gerak
            # ABSOLUT ke (posisi + dx, dy). SendInput relatif murni ikut "pointer speed" &
            # akselerasi Windows (bisa dilipatgandakan sampai 4x) sehingga hasilnya tidak
            # bisa ditebak klien - diuji nyata: dx=100 bisa jadi lompatan ratusan piksel.
            # Chrome Remote Desktop juga selalu menyuntik koordinat absolut.
            dx, dy = reldxy(e)
            if (dx or dy) and sw > 0 and sh > 0:
                if vpos is None:
                    vpos = list(_cursor_pos())
                vpos[0] = min(max(vpos[0] + dx, 0), sw - 1)
                vpos[1] = min(max(vpos[1] + dy, 0), sh - 1)
                mouse(MOVE | ABS, *_abs_norm(vpos[0], vpos[1], sw, sh))
        elif t in ("click", "dblclick", "down", "up"):
            if "x" in e:
                x, y = absxy(e)
                mouse(MOVE | ABS, x, y)
                vpos = None
            dn, up = btn.get(e.get("button", "left"), (LDN, LUP))
            if t == "down":
                mouse(dn)
            elif t == "up":
                mouse(up)
            else:
                for _ in range(2 if t == "dblclick" else 1):
                    mouse(dn)
                    mouse(up)
        elif t == "scroll":
            # dy: roda vertikal (+ = ke atas, 120 = satu klik). dx: roda horizontal (+ = ke
            # kanan). Boleh tidak kelipatan 120 (gulir halus dari gestur dua jari).
            def wheel(v):
                return max(-2400, min(2400, int(finite(v))))
            wy, wx = wheel(e.get("dy", 0)), wheel(e.get("dx", 0))
            if wy:
                mouse(WHEEL, data=wy)
            if wx:
                mouse(HWHEEL, data=wx)
        elif t == "text":
            for ch in str(e.get("text", ""))[:2000]:
                key_unicode(ch)
        elif t == "key":
            name = str(e.get("key", "")).lower()
            mods = [m for m in ("ctrl", "alt", "shift", "win") if e.get(m)]
            vk = _VK.get(name)
            if vk is None and len(name) == 1:
                vk = ord(name.upper())
            if vk is None:
                continue
            if e.get("down") or e.get("up"):
                # Tahan/lepas tanpa klik (Ctrl, Shift, Alt, Win): sama seperti remote
                # desktop sungguhan, pengubah tetap turun selama tombol ditahan.
                down = not e.get("up")
                for m in mods:
                    key_vk(_VK[m], down)
                key_vk(vk, down)
                continue
            for m in mods:
                key_vk(_VK[m], True)
            key_vk(vk, True)
            key_vk(vk, False)
            for m in reversed(mods):
                key_vk(_VK[m], False)

    if not seq:
        return 0
    return _send(seq)


# =============================== DAYA ========================================
_POWER_FLAG = {"shutdown": "/s", "restart": "/r"}


def power(action):
    """Matikan atau nyalakan ulang komputer ini lewat shutdown.exe bawaan Windows.

    Jeda 5 detik (bukan 0) supaya ada celah sempit untuk `shutdown /a` kalau ini
    salah pencet - tidak bisa dibatalkan dari jarak jauh lewat tool ini sendiri.
    Kembalikan (ok, pesan)."""
    if not IS_WIN:
        return False, "Matikan/nyalakan ulang jarak jauh baru didukung di Windows."
    flag = _POWER_FLAG.get(action)
    if not flag:
        return False, f"Aksi tidak dikenal: {action!r} (pakai 'shutdown' atau 'restart')."
    try:
        subprocess.Popen(["shutdown", flag, "/t", "5"])
    except OSError as exc:
        return False, f"Gagal menjalankan shutdown.exe: {exc}"
    return True, (
        "Komputer akan mati dalam 5 detik." if action == "shutdown"
        else "Komputer akan nyala ulang dalam 5 detik."
    )


# =============================== BERKAS ======================================
def _drives():
    if not IS_WIN:
        return ["/"]
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask & (1 << i)]


def fs_list(path):
    """Isi folder. path kosong = daftar drive + folder rumah."""
    path = (path or "").strip()
    if not path:
        home = os.path.expanduser("~")
        roots = [{"name": d, "path": d, "dir": True, "size": 0, "mtime": 0} for d in _drives()]
        roots.append({"name": f"Rumah ({os.path.basename(home) or home})",
                      "path": home, "dir": True, "size": 0, "mtime": 0})
        return {"path": "", "parent": None, "entries": roots}
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise NotADirectoryError("bukan folder")
    entries = []
    with os.scandir(path) as it:
        for e in it:
            try:
                st = e.stat()
                is_dir = e.is_dir()
            except OSError:
                continue
            entries.append({"name": e.name, "path": e.path, "dir": is_dir,
                            "size": 0 if is_dir else st.st_size, "mtime": int(st.st_mtime),
                            "secret": is_secret(e.path)})
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    parent = os.path.dirname(path.rstrip("\\/")) or None
    if parent == path:
        parent = ""  # sudah di akar drive -> naik ke daftar drive
    return {"path": path, "parent": parent if parent is not None else "", "entries": entries}


def fs_read(path, max_bytes=1_000_000):
    """Isi berkas teks untuk pratinjau. Menolak berkas rahasia & biner besar."""
    path = _target(path)
    if not os.path.isfile(path):
        raise FileNotFoundError("berkas tidak ada")
    if is_secret(path):
        raise PermissionError("berkas rahasia tidak boleh dibuka")
    if _hardlinked(path):
        raise PermissionError("berkas ini hardlink; namanya bisa menyembunyikan berkas rahasia")
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        raw = f.read(max_bytes)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {"path": path, "size": size, "binary": True,
                    "text": "(berkas biner — pakai unduh)"}
    return {"path": path, "size": size, "binary": False,
            "truncated": size > len(raw), "text": text}


def fs_write(path, data, max_bytes=100_000_000):
    """Tulis berkas di komputer ini (dipakai unggah dari dashboard). Kembalikan info."""
    target = _target(path)
    if not path or len(data) > max_bytes:
        return {"error": "berkas terlalu besar atau nama kosong"}
    if is_secret(target):
        return {"error": "menolak menulis berkas rahasia (.env, token, kunci)"}
    if _hardlinked(target):
        return {"error": "berkas ini hardlink; menulisnya bisa menimpa berkas rahasia"}
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        return {"ok": True, "path": target, "size": len(data)}
    except OSError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def fs_file(path, max_bytes=100_000_000):
    """(nama, bytes) untuk diunduh. Menolak berkas rahasia & yang terlalu besar."""
    path = _target(path)
    if not os.path.isfile(path):
        raise FileNotFoundError("berkas tidak ada")
    if is_secret(path):
        raise PermissionError("berkas rahasia tidak boleh diunduh")
    if _hardlinked(path):
        raise PermissionError("berkas ini hardlink; namanya bisa menyembunyikan berkas rahasia")
    if os.path.getsize(path) > max_bytes:
        raise ValueError("berkas terlalu besar untuk diunduh lewat jaringan")
    with open(path, "rb") as f:
        return os.path.basename(path), f.read()
