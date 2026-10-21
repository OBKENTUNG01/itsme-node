"""Info perangkat: RAM, GPU, status menganggur, Tailscale, sidik jari, dan tes."""

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

from . import llm

IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WIN else 0


def _run(args, timeout=10):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                             creationflags=_NO_WINDOW)
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_FINGERPRINT = None
# Nilai sampah yang sering diisi pabrik, bukan ID asli.
_FP_JUNK = re.compile(r"^(0{6,}|to be filled|default string|none|not (specified|available)|"
                      r"system serial number|o\.?e\.?m\.?|00000000-0000-0000-0000-000000000000)$", re.I)


def machine_fingerprint():
    """ID perangkat keras yang TETAP walau OS diinstal ulang (UUID motherboard,
    serial BIOS/baseboard). Sama sebelum & sesudah install ulang di komputer yang
    sama, jadi jaringan bisa mengenali komputer yang kembali tanpa kode undangan baru."""
    global _FINGERPRINT
    if _FINGERPRINT:
        return _FINGERPRINT
    parts = []
    if IS_WIN:
        out = _run(["powershell", "-NoProfile", "-Command",
                    "$p=Get-CimInstance Win32_ComputerSystemProduct;"
                    "$b=Get-CimInstance Win32_BIOS;$m=Get-CimInstance Win32_BaseBoard;"
                    "\"$($p.UUID)|$($b.SerialNumber)|$($m.SerialNumber)\""], timeout=25)
        parts = out.strip().split("|")
    else:
        for path in ("/sys/class/dmi/id/product_uuid", "/etc/machine-id",
                     "/var/lib/dbus/machine-id"):
            try:
                with open(path) as f:
                    parts.append(f.read().strip())
            except OSError:
                pass
    good = [p.strip() for p in parts if p and len(p.strip()) >= 6
            and not _FP_JUNK.match(p.strip()) and set(p.strip()) - {"0", "-", " "}]
    if not good:
        good = ["mac-%012x" % uuid.getnode()]  # cadangan: alamat MAC
    _FINGERPRINT = hashlib.sha256(("itsme-fp|" + "|".join(sorted(good))).encode()).hexdigest()[:24]
    return _FINGERPRINT


def ram_gb():
    if IS_WIN:
        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMSTAT()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.ullTotalPhys / 2**30, 1)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (ValueError, OSError):
        return 4.0


def gpu():
    """(nama, VRAM GB yang bisa dipakai Ollama). GPU NVIDIA lama (< Maxwell) diabaikan."""
    if not shutil.which("nvidia-smi"):
        return ("", 0.0)
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not out.strip():
        return ("", 0.0)
    name, mem = [x.strip() for x in out.splitlines()[0].split(",")[:2]]
    cap = _run(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"]).strip()
    try:
        usable = float(cap) >= 5.0
    except ValueError:
        usable = False  # driver lama tidak mengenal compute_cap -> anggap GPU lama
    return (name, round(float(mem) / 1024, 1) if usable else 0.0)


def idle_seconds():
    if not IS_WIN:
        return 10**9

    class LASTINPUT(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    li = LASTINPUT()
    li.cbSize = ctypes.sizeof(li)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0
    tick = ctypes.windll.kernel32.GetTickCount
    tick.restype = ctypes.c_uint32  # bawaan ctypes c_int: negatif setelah ±24,8 hari menyala
    # Keduanya hitungan 32-bit yang berputar ulang tiap ±49,7 hari.
    return ((tick() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def _tailscale():
    exe = shutil.which("tailscale")
    if not exe and IS_WIN:
        p = r"C:\Program Files\Tailscale\tailscale.exe"
        exe = p if os.path.exists(p) else None
    return exe


def tailscale_ip():
    exe = _tailscale()
    if not exe:
        return ""
    out = _run([exe, "ip", "-4"]).strip()
    return out.splitlines()[0] if out else ""


def tailscale_peers():
    """IP Tailscale dari perangkat lain yang sedang online."""
    exe = _tailscale()
    if not exe:
        return []
    try:
        data = json.loads(_run([exe, "status", "--json"]) or "{}")
    except ValueError:
        return []
    ips = []
    for peer in (data.get("Peer") or {}).values():
        if peer.get("Online") and peer.get("TailscaleIPs"):
            ips.append(peer["TailscaleIPs"][0])
    return ips


# (nama model, RAM minimal GB, VRAM yang membuatnya cepat)
MODELS = [
    ("qwen3:14b", 24, 12),
    ("qwen3:8b", 16, 8),
    ("qwen3:4b", 10, 4),
    ("qwen3:1.7b", 6, 2),
]


def pick_model(ram, vram):
    for name, need_ram, need_vram in MODELS:
        if vram >= need_vram:
            return name
    # Tanpa GPU: model kecil supaya tidak terlalu lambat di CPU.
    if ram >= 10:
        return "qwen3:4b"
    if ram >= 6:
        return "qwen3:1.7b"
    return ""


def start_ollama():
    """Nyalakan Ollama kalau terpasang tapi belum jalan (mis. baru dipasang winget)."""
    exe = shutil.which("ollama")
    if not exe and IS_WIN:
        p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        exe = p if os.path.exists(p) else None
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_NO_WINDOW | (0x00000008 if IS_WIN else 0))  # DETACHED_PROCESS
        return True
    except OSError:
        return False


def wait_ollama(url, seconds=60):
    """True kalau Ollama menjawab; kalau belum, coba nyalakan lalu tunggu."""
    if llm.available(url):
        return True
    if not start_ollama():
        return False
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(2)
        if llm.available(url):
            return True
    return False


def benchmark(cfg):
    """Ukur kemampuan node. Hasil: dict profil (skor makin besar makin kuat)."""
    ram = ram_gb()
    gname, vram = gpu()
    threads = os.cpu_count() or 2
    model = cfg["model"]
    if model == "auto":
        model = pick_model(ram, vram)
    tps = 0.0
    retry = False  # model diinginkan tapi Ollama belum siap: tes lagi sebentar lagi
    if model:
        if not wait_ollama(cfg["ollama_url"]):
            model, retry = "", True
        elif model not in llm.installed(cfg["ollama_url"]):
            print(f"[tes] mengunduh model {model} ...", flush=True)
            if not llm.pull(cfg["ollama_url"], model):
                model, retry = "", True
    if model:
        tps = llm.speed(cfg["ollama_url"], model)
        if tps < 0.8:  # terlalu lambat untuk merangkum, jadikan penjelajah saja
            model = ""
    score = round(threads + tps * 10 + vram * 2, 1)
    workers = cfg["workers"] or max(2, min(8, threads // 2 + (2 if score > 60 else 0)))
    return {"ram": ram, "gpu": gname, "vram": vram, "threads": threads,
            "model": model, "tps": round(tps, 1), "score": score,
            "workers": workers, "tested": int(time.time()), "retry": retry}


def provisional_profile(cfg):
    """Profil sementara selama tes kemampuan pertama berjalan (tanpa model)."""
    threads = os.cpu_count() or 2
    return {"ram": ram_gb(), "gpu": "", "vram": 0.0, "threads": threads, "model": "",
            "tps": 0.0, "score": float(threads), "workers": cfg["workers"] or 2,
            "tested": 0, "retry": True}
