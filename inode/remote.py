"""Akses jarak jauh ke komputer di jaringan: tangkap layar, kirim klik/ketik, dan
telusuri berkas. Hanya pustaka bawaan Python (ctypes untuk Windows).

Dipakai server.py: komputer yang kamu lihat menyajikan layar & berkasnya; komputer
tempat kamu duduk meneruskan permintaan ke sana lewat Tailscale (lihat server.py).

Aman: berkas rahasia (.env, kunci, token, login browser/aplikasi) tidak boleh
dibaca/diunduh walau berkasnya tetap terlihat di daftar.
"""

import os
import re
import struct
import sys
import threading
import zlib

IS_WIN = sys.platform == "win32"
_cap_lock = threading.Lock()

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
    RDN, RUP, MDN, MUP, WHEEL = 0x0008, 0x0010, 0x0020, 0x0040, 0x0800
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

    def absxy(e):
        return int(float(e.get("x", 0)) * 65535), int(float(e.get("y", 0)) * 65535)

    for e in events:
        t = e.get("t")
        if t == "move":
            x, y = absxy(e)
            mouse(MOVE | ABS, x, y)
        elif t in ("click", "dblclick", "down", "up"):
            if "x" in e:
                x, y = absxy(e)
                mouse(MOVE | ABS, x, y)
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
            mouse(WHEEL, data=int(e.get("dy", 0)))
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
    path = os.path.abspath(path or "")
    if not os.path.isfile(path):
        raise FileNotFoundError("berkas tidak ada")
    if is_secret(path):
        raise PermissionError("berkas rahasia tidak boleh dibuka")
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


def fs_file(path, max_bytes=100_000_000):
    """(nama, bytes) untuk diunduh. Menolak berkas rahasia & yang terlalu besar."""
    path = os.path.abspath(path or "")
    if not os.path.isfile(path):
        raise FileNotFoundError("berkas tidak ada")
    if is_secret(path):
        raise PermissionError("berkas rahasia tidak boleh diunduh")
    if os.path.getsize(path) > max_bytes:
        raise ValueError("berkas terlalu besar untuk diunduh lewat jaringan")
    with open(path, "rb") as f:
        return os.path.basename(path), f.read()
