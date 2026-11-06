"""Ollama di komputer ini: status, pasang, dan copot - dipicu dari dashboard It's Me lewat
rute /ollama (server.py), yang bisa diteruskan ke komputer lain di jaringan.

Pasang: winget (paket Ollama.Ollama - installer diverifikasi SHA256 oleh winget dan dipindai
antivirus, tanpa admin karena Ollama terpasang per-user), lalu pemasang resmi ollama.com
sebagai cadangan - sama seperti jarvis/setup.ps1, hanya urutannya dibalik karena winget
memberi kode keluar yang jelas. Kode 0x8A15002B ("tidak ada pembaruan") dan 0x8A150061
("sudah terpasang") artinya SUDAH terpasang, bukan gagal.

Copot: uninstaller resmi (Inno Setup) dengan /VERYSILENT. Model di ~/.ollama/models TETAP
tersimpan (mode senyap tidak menghapusnya), jadi pasang ulang tidak perlu unduh model lagi.
Dua jebakan nyata yang ditangani di sini:
  - unins000.exe menyalin dirinya ke TEMP lalu langsung keluar; uninstall SESUNGGUHNYA masih
    berjalan. Jadi hasilnya dipantau sampai registry, folder program, dan server benar-benar
    hilang - bukan dari kode keluarnya.
  - Kalau ada yang menyalakan Ollama lagi di tengah uninstall (penjaga berpikir thinker.py,
    aplikasi tray, node versi lama di komputer yang sama), file yang sedang dipakai gagal
    dihapus TAPI kode keluar tetap 0 dan registry tetap terhapus. Karena itu selama pekerjaan
    berjalan system.OLLAMA_MAINTENANCE dipasang (thinker tidak menyalakan Ollama) dan proses
    Ollama dimatikan terus-menerus sampai uninstaller selesai.

Pekerjaan pasang/copot bisa makan beberapa menit (installer +-1,7 GB), sementara rpc antar
node hanya menunggu 25 detik - jadi keduanya PEKERJAAN LATAR; statusnya dibaca lewat
action "status". Hanya satu pekerjaan dalam satu waktu.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from . import system

IS_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WIN else 0
PRODUCT_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1"
WINGET_OK = {0, 0x8A15002B, 0x8A150061}  # sukses / tak ada pembaruan / sudah terpasang
# Proses milik Ollama: aplikasi tray, server, dan runner model lama. "llama-server.exe" SENGAJA tidak
# ada: itu nama biner llama.cpp milik siapa pun (bukan khusus Ollama); runner Ollama 0.40.x anak dari
# ollama.exe, jadi ikut mati lewat taskkill /T pada induknya.
PROCS = ("ollama app.exe", "ollama.exe", "ollama_llama_server.exe")
INSTALL_TIMEOUT = 60 * 60   # unduh +-1,7 GB di koneksi lambat
UNINSTALL_WAIT = 180

_lock = threading.Lock()
_job = None            # pekerjaan terakhir (dict), lihat _new_job
_summary = (0.0, None)  # (waktu, ringkasan) - cache untuk gosip tiap 2 detik


# --- deteksi ------------------------------------------------------------------------
def _registry():
    """{"version","path","uninstall"} dari key uninstall Inno Setup milik Ollama, atau None."""
    if not IS_WIN:
        return None
    import winreg
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, PRODUCT_KEY) as k:
                def q(name):
                    try:
                        return str(winreg.QueryValueEx(k, name)[0])
                    except OSError:
                        return ""
                return {"version": q("DisplayVersion"), "path": q("Inno Setup: App Path"),
                        "uninstall": q("UninstallString").strip('"')}
        except OSError:
            continue
    return None


def _default_dir():
    base = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(base, "Programs", "Ollama") if base else ""


def _exe(reg=None):
    reg = reg if reg is not None else _registry()
    for d in ((reg or {}).get("path"), _default_dir()):
        if d and os.path.isfile(os.path.join(d, "ollama.exe")):
            return os.path.join(d, "ollama.exe")
    return shutil.which("ollama")


def _unmanaged(reg, exe):
    """Ollama ADA tapi bukan dari pemasang resmi di folder bawaan (scoop/choco/zip/PATH): tak punya
    uninstaller untuk dicopot, dan prosesnya tidak boleh dibunuh/ditimpa otomatis."""
    if reg or not exe:
        return False
    default = _default_dir()
    try:
        return not (default and os.path.normcase(os.path.dirname(exe)) == os.path.normcase(default))
    except (TypeError, ValueError):
        return True


def _server_version(url="http://127.0.0.1:11434"):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/version", timeout=2) as r:
            import json
            return json.loads(r.read()).get("version") or "?"
    except (OSError, ValueError):
        return None


def status(url="http://127.0.0.1:11434"):
    """Keadaan Ollama di komputer ini untuk dashboard."""
    if not IS_WIN:
        return {"supported": False, "installed": bool(shutil.which("ollama")), "version": None,
                "running": _server_version(url) is not None, "job": job()}
    reg = _registry()
    exe = _exe(reg)
    unmanaged = _unmanaged(reg, exe)
    models = os.path.join(os.path.expanduser("~"), ".ollama", "models", "blobs")
    return {"supported": True, "installed": bool(exe and (reg or unmanaged)),
            "version": (reg or {}).get("version") or None,
            "path": exe, "unmanaged": unmanaged,
            # file tersisa di folder bawaan tanpa registry = uninstall tak tuntas
            "leftover": bool(exe and not reg and not unmanaged),
            "running": _server_version(url) is not None, "models_kept": os.path.isdir(models),
            "job": job()}


def summary():
    """Ringkasan murah untuk gosip (dipanggil tiap 2 detik): cache 10 detik, tanpa HTTP."""
    global _summary
    now = time.time()
    stamp, cached = _summary          # satu kali baca: _invalidate() dari thread lain bisa menimpanya
    if now - stamp < 10 and cached is not None:
        out = dict(cached)
    else:
        reg = _registry() if IS_WIN else None
        exe = _exe(reg) if IS_WIN else shutil.which("ollama")
        out = {"installed": bool(exe and (reg or _unmanaged(reg, exe))) if IS_WIN else bool(exe),
               "version": (reg or {}).get("version") or None}
        _summary = (now, dict(out))
    j = job()
    out["job"] = ({"action": j["action"], "state": j["state"], "step": j["step"],
                   "tail": _last_line(j.get("log")),
                   # dihitung di node ini (bebas selisih jam antar komputer) - unduhan bisa puluhan menit
                   "elapsed": int((j.get("ended") or time.time()) - j["started"]) if j.get("started") else None}
                  if j else None)
    return out


def _last_line(text, limit=90):
    """Baris terakhir yang tidak kosong dari log (winget memperbarui bilah kemajuan dengan CR),
    supaya dashboard bisa menampilkan kemajuan unduhan, bukan cuma 'sedang memasang'."""
    parts = [p.strip() for p in re.split(r"[\r\n]+", text or "") if p.strip()]
    return parts[-1][:limit] if parts else ""


def _invalidate():
    global _summary
    _summary = (0.0, None)


# --- pekerjaan latar ------------------------------------------------------------------
def job():
    with _lock:
        return dict(_job) if _job else None


def _update(**kw):
    with _lock:
        if _job is not None:
            _job.update(kw)


def start(action):
    """Mulai pekerjaan "install" / "uninstall" di latar. {"ok": True, "job": ...} atau error."""
    global _job
    if not IS_WIN:
        return {"ok": False, "error": "Pasang/copot Ollama otomatis baru didukung di Windows."}
    if action not in ("install", "uninstall"):
        return {"ok": False, "error": f"aksi tidak dikenal: {action!r}"}
    with _lock:
        if _job and _job["state"] == "running":
            return {"ok": False, "error": f"masih ada pekerjaan '{_job['action']}' yang berjalan", "job": dict(_job)}
        _job = {"action": action, "state": "running", "step": "mulai", "started": time.time(),
                "ended": None, "message": "", "log": ""}
        snapshot = dict(_job)
    target = _install if action == "install" else _uninstall
    threading.Thread(target=_run_job, args=(target,), daemon=True, name=f"ollama-{action}").start()
    return {"ok": True, "job": snapshot}


def _run_job(target):
    system.OLLAMA_MAINTENANCE.set()
    try:
        ok, message = target()
    except Exception as exc:  # noqa: BLE001 - pekerjaan latar harus selalu selesai dengan status
        ok, message = False, f"{type(exc).__name__}: {exc}"
    finally:
        system.OLLAMA_MAINTENANCE.clear()
        _invalidate()
    if ok and job() and job()["action"] == "install":
        system.start_ollama(low_priority=True)  # node menjalankan `ollama serve` sendiri
    _update(state="done" if ok else "failed", message=message, ended=time.time(), step="selesai")


def _tail(path, limit=1500):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit * 2))
            return f.read().decode("utf-8", "replace").replace("\x00", "")[-limit:]
    except OSError:
        return ""


def _wait_file_process(args, log_path, timeout):
    """Jalankan program dengan keluaran ke BERKAS, bukan pipe: pemasang Ollama menyalakan
    aplikasi tray yang mewarisi handle - kalau pipe, kita akan menunggu selamanya (catatan
    yang sama ada di jarvis/setup.ps1). Kembalikan kode keluar (unsigned) atau None (timeout)."""
    with open(log_path, "ab") as log:
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=_NO_WINDOW, close_fds=True)
        except OSError as exc:  # mis. alias winget (App Execution Alias) yang tak bisa dijalankan
            log.write(f"gagal menjalankan {args[0]}: {exc}\n".encode("utf-8", "replace"))
            return 0xFFFFFFFF    # bukan kode sukses: pemanggil lanjut ke cara pasang berikutnya
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None:
                return rc & 0xFFFFFFFF
            _update(log=_tail(log_path))
            time.sleep(2)
        proc.kill()
        return None


def _winget():
    exe = shutil.which("winget")
    if not exe:
        alias = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "winget.exe")
        exe = alias if os.path.exists(alias) else None
    return exe


class _KeepKilling:
    """Mematikan proses Ollama terus-menerus selama blok `with` berjalan.

    Siapa pun yang menyalakan Ollama lagi di tengah pekerjaan (penjaga thinker.py milik node
    LAIN di komputer yang sama, aplikasi tray, atau pemasang itu sendiri) mengunci berkasnya;
    uninstaller/pemasang Inno Setup lalu melewatkan berkas itu atau gagal. Ditemukan nyata:
    sisa uninstall yang tak tuntas = ollama.exe terkunci + tanpa registry (status "leftover")."""

    def __enter__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ollama-killer")
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            _kill()
            self._stop.wait(1.0)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)
        return False


def _install():
    st = status()
    if st["installed"]:
        if st.get("unmanaged"):
            return True, (f"Ollama sudah ada di {st.get('path')} (dipasang di luar pemasang resmi) - tidak dipasang "
                          "ulang dan prosesnya tidak diganggu.")
        return True, f"Ollama sudah terpasang (versi {st['version']})."
    log_path = os.path.join(tempfile.gettempdir(), "itsme-ollama-install.log")
    open(log_path, "wb").close()
    with _KeepKilling():  # sisa proses (mis. "leftover" dari uninstall tak tuntas) mengunci ollama.exe
        return _install_with_installers(log_path)


def _install_with_installers(log_path):
    winget = _winget()
    if winget:
        _update(step="mengunduh & memasang lewat winget (+-1,7 GB)")
        cmd = [winget, "install", "--id", "Ollama.Ollama", "-e", "--source", "winget", "--silent",
               "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
        rc = _wait_file_process(cmd, log_path, INSTALL_TIMEOUT)
        if rc in (0x8A15002B, 0x8A150061) and not status()["installed"]:
            # Ditemukan nyata: sesudah Ollama dicopot lewat uninstaller-nya sendiri, catatan winget masih
            # mengira paketnya terpasang (kode "tak ada pembaruan") padahal registry sudah bersih.
            # Paksa pasang ulang lewat winget dulu (terverifikasi hash) sebelum turun ke pemasang resmi.
            _update(step="winget mengira Ollama masih terpasang - memaksa pasang ulang (+-1,7 GB)")
            rc = _wait_file_process(cmd + ["--force"], log_path, INSTALL_TIMEOUT)
        if rc in WINGET_OK and status()["installed"]:
            _disable_tray()
            return True, f"Ollama {status()['version']} terpasang lewat winget."
        _update(message=f"winget gagal (kode {hex(rc) if rc is not None else 'timeout'}), mencoba pemasang resmi")
    _update(step="pemasang resmi ollama.com")
    rc = _wait_file_process(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
         "irm https://ollama.com/install.ps1 | iex"], log_path, INSTALL_TIMEOUT)
    if status()["installed"]:
        _disable_tray()
        return True, f"Ollama {status()['version']} terpasang lewat pemasang resmi."
    return False, ("Ollama gagal dipasang (winget " + ("tidak ada" if not winget else "gagal") +
                   f", pemasang resmi kode {rc}). Lihat log: {log_path}")


def _disable_tray():
    """Sama seperti Disable-ItsMeOllamaTray di jarvis/setup.ps1 ("bersih tanpa ikon"): tray
    Ollama tidak menyala sendiri saat login dan tidak berjalan sekarang - node yang
    menjalankan `ollama serve` tersembunyi (thinker.py). Best-effort."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "Ollama")
    except OSError:
        pass
    startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu",
                           "Programs", "Startup", "Ollama.lnk")
    try:
        os.remove(startup)
    except OSError:
        pass
    _kill(["ollama app.exe"])


def _kill(names=PROCS):
    args = ["taskkill", "/F", "/T"]
    for n in names:
        args += ["/IM", n]
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def _gone():
    reg = _registry()
    d = _default_dir()
    return reg is None and not (d and os.path.isfile(os.path.join(d, "ollama.exe")))


def _uninstall():
    reg = _registry()
    uninstaller = (reg or {}).get("uninstall") or os.path.join(_default_dir(), "unins000.exe")
    if not reg and not (uninstaller and os.path.isfile(uninstaller)):
        st = status()
        if st.get("unmanaged"):
            return False, (f"Ollama di komputer ini tidak dipasang lewat pemasang resmi (ada di {st.get('path')}), "
                           "jadi tidak bisa dicopot otomatis - copot dengan cara pemasangnya (scoop/choco/hapus folder).")
        return True, "Ollama memang tidak terpasang di komputer ini."
    if not os.path.isfile(uninstaller):
        return False, f"Uninstaller Ollama tidak ditemukan: {uninstaller}"
    log_path = os.path.join(tempfile.gettempdir(), "itsme-ollama-uninstall.log")
    with _KeepKilling():  # siapa pun yang menyalakan Ollama lagi selama uninstall berjalan
        for attempt in (1, 2):
            _update(step=f"menjalankan uninstaller resmi (percobaan {attempt})")
            try:
                os.remove(log_path)
            except OSError:
                pass
            subprocess.Popen([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log_path}"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW, close_fds=True)
            _update(step="menunggu uninstall selesai")
            deadline = time.time() + UNINSTALL_WAIT
            while time.time() < deadline and not _gone():
                _update(log=_tail(log_path))
                time.sleep(2)
            _update(log=_tail(log_path))
            if _gone():
                time.sleep(3)  # salinan _unins.tmp masih merapikan berkas terakhirnya
                return True, "Ollama sudah dicopot. Model yang sudah diunduh tetap tersimpan di ~/.ollama."
            if not os.path.isfile(uninstaller):
                break
        return False, f"Uninstall belum tuntas (registry/berkas masih ada). Lihat log: {log_path}"
