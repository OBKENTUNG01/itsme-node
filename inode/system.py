"""Info perangkat: RAM, GPU, status menganggur, Tailscale, sidik jari, dan tes."""

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
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


def mac_address():
    """Alamat MAC komputer ini (untuk Wake-on-LAN, lihat wol.py). Kosong kalau
    uuid.getnode() cuma mengarang alamat acak (tak ada NIC asli yang terbaca)."""
    n = uuid.getnode()
    if (n >> 40) & 1:  # bit "acak" RFC 4122 milik uuid.getnode() saat gagal baca NIC asli
        return ""
    return "%012x" % n


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


def lan_ip():
    """Alamat IP LAN komputer ini (tanpa Tailscale), untuk node sejaringan. Kosong
    kalau gagal. Soket UDP ini tidak mengirim apa-apa; hanya memilih antarmuka keluar."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


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


# Versi tes kemampuan. Dinaikkan kalau cara memilih/menguji model berubah, supaya profil lama
# (disimpan 7 hari) diuji ulang. 2 = tangga keamanan CPU-saja + model 4B non-berpikir.
BENCH_VERSION = 2

# (nama model, RAM minimal GB, VRAM yang membuatnya cepat)
# Tag "qwen3:4b" kini menunjuk ke build THINKING-2507 yang SELALU berpikir (parameter think:false
# diabaikan, penalarannya bocor ke jawaban) - tidak cocok untuk merangkum maupun menjawab. Yang
# dipakai varian instruct 2507 (non-berpikir). qwen3:1.7b/8b/14b adalah build hibrida asli: think:false dipatuhi.
MODEL_4B = "qwen3:4b-instruct-2507-q4_K_M"
MODELS = [
    ("qwen3:14b", 24, 12),
    ("qwen3:8b", 16, 8),
    (MODEL_4B, 10, 4),
    ("qwen3:1.7b", 6, 2),
]


def pick_model(ram, vram):
    for name, need_ram, need_vram in MODELS:
        # RAM juga harus cukup: dulu hanya VRAM yang diperiksa, jadi mesin dengan
        # GPU besar tapi RAM kecil bisa dipilihkan model yang tidak muat.
        if vram >= need_vram and ram >= need_ram:
            return name
    # Tanpa GPU: model kecil supaya tidak terlalu lambat di CPU.
    if ram >= 10:
        return MODEL_4B
    if ram >= 6:
        return "qwen3:1.7b"
    return ""


BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
PROCESS_SET_INFORMATION = 0x0200
# Proses milik Ollama yang menjalankan model; semuanya diturunkan prioritasnya.
OLLAMA_PROCS = {"ollama.exe", "ollama_llama_server.exe", "llama-server.exe", "ollama-runner.exe"}
# Dipasang selama ollama_ctl memasang/mencopot Ollama: jangan nyalakan Ollama di tengah
# jalan (menyalakannya saat uninstall membuat berkas terkunci dan gagal terhapus).
OLLAMA_MAINTENANCE = threading.Event()


def start_ollama(low_priority=True):
    """Nyalakan Ollama kalau terpasang tapi belum jalan (mis. baru dipasang winget).

    low_priority: berjalan di bawah prioritas normal (Windows), supaya komputer yang sedang
    dipakai pemiliknya tidak tersendat; proses model yang dibuatnya mewarisi prioritas itu."""
    if OLLAMA_MAINTENANCE.is_set():
        return False
    exe = shutil.which("ollama")
    if not exe and IS_WIN:
        p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        exe = p if os.path.exists(p) else None
    if not exe:
        return False
    flags = _NO_WINDOW | (0x00000008 if IS_WIN else 0)  # DETACHED_PROCESS
    if IS_WIN and low_priority:
        flags |= BELOW_NORMAL_PRIORITY_CLASS
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
        return True
    except OSError:
        return False


def ollama_pids():
    """PID proses Ollama dan runner modelnya (Windows). Kosong di sistem lain."""
    if not IS_WIN:
        return []
    out = _run(["tasklist", "/FO", "CSV", "/NH"], timeout=15)
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].strip('"').lower() in OLLAMA_PROCS:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def lower_ollama_priority(done):
    """Turunkan prioritas semua proses Ollama yang belum (set `done` berisi PID yang sudah).
    Hanya mengatur giliran CPU - tidak menghentikan atau membatasi apa pun. Mengembalikan jumlah
    proses yang baru diturunkan."""
    if not IS_WIN:
        return 0
    count = 0
    try:
        kernel = ctypes.windll.kernel32
        # HANDLE adalah pointer-sized; ctypes default menganggap hasil C int (32-bit di Windows).
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetPriorityClass.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return 0
    for pid in ollama_pids():
        if pid in done:
            continue
        handle = kernel.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
        if not handle:
            done.add(pid)  # bukan milik pengguna ini / sudah hilang: jangan dicoba terus
            continue
        try:
            if kernel.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS):
                count += 1
        finally:
            kernel.CloseHandle(handle)
        done.add(pid)
    return count


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
            "workers": workers, "tested": int(time.time()), "retry": retry,
            "bench_v": BENCH_VERSION}


def provisional_profile(cfg):
    """Profil sementara selama tes kemampuan pertama berjalan (tanpa model)."""
    threads = os.cpu_count() or 2
    return {"ram": ram_gb(), "gpu": "", "vram": 0.0, "threads": threads, "model": "",
            "tps": 0.0, "score": float(threads), "workers": cfg["workers"] or 2,
            "tested": 0, "retry": True}
