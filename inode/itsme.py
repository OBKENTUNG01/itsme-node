"""Bot Telegram It's Me di komputer ini.

It's Me (repo OBKENTUNG01/website) yang dipasang dengan token bot yang sama di
beberapa komputer menanyakan node lokal siapa pemimpinnya, dan hanya aktif di
komputer pemimpin. Modul ini memberi tahu jaringan apakah komputer ini bisa
menjadi bot aktif, dan menyalin memori, otak yang dipilih, dan mode akses dari
pemimpin ke sini, supaya bot tetap sama walau pindah komputer.

Isi .env (token, kunci) hanya dibaca untuk memeriksa ada/tidaknya, KECUALI saat
komputer yang sudah terdaftar menginstal ulang OS-nya dan minta gabung kembali
(/enroll): node ini mengirim isi .env itu balik ke komputer tersebut lewat
Tailscale supaya bot It's Me ikut pulih. Lihat env_bundle().
"""

import os
import time

SYNC_FILES = ("memory.md", "brain.json", "access.json")
MAX_FILE = 256 * 1024
# Salinan LAMA tiap berkas yang ditimpa pemimpin disimpan di <data>/sync-backup (sebanyak ini).
BACKUP_KEEP = 20
_MEMORY_HEADER = "# Memori It's Me"
_OFF = ("0", "false", "no", "off")
# Kunci .env yang khusus per-komputer (folder, alamat, perangkat keras): tidak
# ikut dikirim ke komputer lain. Sama dengan daftar di bot (node.py _LOCAL_ONLY).
_LOCAL_ONLY = {
    "ITSME_DATA_DIR", "ITSME_DEVICE_NAME", "ITSME_DRIVE_CREDENTIALS", "ITSME_DRIVE_DOWNLOAD_DIR",
    "ITSME_FOLLOW_NODE", "ITSME_HUB_URL", "ITSME_HUB_SECRET", "ITSME_HUB_HEARTBEAT",
    "ITSME_LOG_FILE", "ITSME_MEMORY_FILE", "ITSME_NODE_URL", "ITSME_OLLAMA_MODEL",
    "ITSME_OLLAMA_NUM_CTX", "ITSME_OLLAMA_NUM_GPU", "OLLAMA_URL", "WEBSITE_REPO_DIR",
}


def env_bundle(cfg):
    """Isi .env bot (token, pemilik, kunci otak) untuk memulihkan It's Me di komputer
    yang terdaftar tapi diinstal ulang. Kunci khusus per-komputer dibuang."""
    out = {}
    try:
        with open(os.path.join(home(cfg), ".env"), encoding="utf-8-sig") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                key = key.strip()
                if sep and key and not key.startswith("#"):
                    value = value.strip().strip("'\"").strip()
                    if value and key not in _LOCAL_ONLY and "\n" not in value:
                        out[key] = value
    except OSError:
        return {}
    return out


def home(cfg):
    if cfg.get("itsme_dir"):
        return cfg["itsme_dir"]
    return os.path.join(os.path.expanduser("~"), "ItsMe" if os.name == "nt" else "itsme")


def _env_has(path, names):
    """{nama: terisi?} dari .env It's Me, tanpa menyimpan nilainya."""
    found = {}
    try:
        with open(os.path.join(path, ".env"), encoding="utf-8-sig") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                key = key.strip()
                if sep and key in names and not key.startswith("#"):
                    value = value.strip().strip("'\"").strip()
                    found[key] = value.lower() if key == "ITSME_FOLLOW_NODE" else bool(value)
    except OSError:
        return None
    return found


def ready(cfg):
    """True kalau It's Me terpasang di sini dan bisa menjadi bot Telegram aktif."""
    if not cfg.get("itsme_follow", True):
        return False
    env = _env_has(home(cfg), {"TELEGRAM_BOT_TOKEN", "OWNER_TELEGRAM_ID",
                               "ITSME_HUB_URL", "ITSME_FOLLOW_NODE"})
    if not env:
        return False
    if env.get("ITSME_HUB_URL") or env.get("ITSME_FOLLOW_NODE") in _OFF:
        return False  # mode perangkat Hub lama, atau sengaja tidak mengikuti node
    return bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("OWNER_TELEGRAM_ID"))


def read_state(cfg):
    """Isi berkas yang harus sama di semua komputer (dipanggil di pemimpin)."""
    out = {}
    data = os.path.join(home(cfg), "data")
    for name in SYNC_FILES:
        path = os.path.join(data, name)
        try:
            if os.path.getsize(path) <= MAX_FILE:
                with open(path, encoding="utf-8", newline="") as f:
                    out[name] = f.read()
        except (OSError, UnicodeDecodeError):
            pass
    return out


def _backup_before_overwrite(data, name, old):
    """Simpan isi LAMA `name` di <data>/sync-backup sebelum ditimpa salinan pemimpin.

    29 Okt 2026: begitu komputer-komputer yang tadinya memimpin sendiri-sendiri bergabung,
    memori satu komputer langsung ditimpa memori pemimpin tanpa jejak - kalau yang tertimpa
    berisi catatan, hilang selamanya. Isi kosong (atau hanya judul memori) tak perlu
    dicadangkan. Gagal mencadangkan tidak boleh menghentikan sinkronisasi."""
    if old.strip() in ("", _MEMORY_HEADER):
        return
    try:
        folder = os.path.join(data, "sync-backup")
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(name)
        now = time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
        for seq in range(100):  # dua penimpaan dalam milidetik yang sama tidak saling menimpa cadangan
            target = os.path.join(folder, f"{stem}-{stamp}-{seq:02d}{ext}")
            if not os.path.exists(target):
                break
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(old)
        mine = sorted(p for p in os.listdir(folder) if p.startswith(stem + "-") and p.endswith(ext))
        for p in mine[:-BACKUP_KEEP]:
            os.remove(os.path.join(folder, p))
    except OSError:
        pass


def write_state(cfg, files):
    """Tulis salinan dari pemimpin. Kembalikan nama berkas yang berubah. Isi lama yang
    ditimpa dicadangkan dulu (lihat _backup_before_overwrite)."""
    data = os.path.join(home(cfg), "data")
    changed = []
    for name, text in (files or {}).items():
        if name not in SYNC_FILES or not isinstance(text, str) or len(text) > MAX_FILE:
            continue
        path = os.path.join(data, name)
        try:
            with open(path, encoding="utf-8", newline="") as f:
                old = f.read()
        except (OSError, UnicodeDecodeError):
            old = None
        if old == text:
            continue
        if old:
            _backup_before_overwrite(data, name, old)
        os.makedirs(data, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
        changed.append(name)
    return changed
