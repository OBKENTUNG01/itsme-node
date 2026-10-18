"""Pembaruan otomatis dari GitHub: unduh commit terbaru lalu minta dijalankan ulang."""

import io
import json
import os
import shutil
import urllib.request
import zipfile

from . import REPO
from .cluster import log

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_FILE = os.path.join(APP_DIR, "COMMIT")
RESTART_CODE = 5


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "itsme-node"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_sha():
    data = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/main"))
    return data["sha"]


def current_sha():
    try:
        with open(SHA_FILE) as f:
            return f.read().strip()
    except OSError:
        return ""


def update_if_needed():
    """True kalau kode baru sudah dipasang (proses perlu dijalankan ulang)."""
    if os.path.isdir(os.path.join(APP_DIR, ".git")):
        return False  # salinan pengembang, jangan ditimpa
    try:
        sha = latest_sha()
    except Exception:
        return False
    if sha == current_sha():
        return False
    log("memasang versi baru", sha[:7])
    z = zipfile.ZipFile(io.BytesIO(_get(f"https://codeload.github.com/{REPO}/zip/{sha}", 120)))
    root = z.namelist()[0].split("/")[0]
    tmp = os.path.join(APP_DIR, "_update")
    shutil.rmtree(tmp, ignore_errors=True)
    z.extractall(tmp)
    src = os.path.join(tmp, root)
    for name in os.listdir(src):
        dst = os.path.join(APP_DIR, name)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.move(os.path.join(src, name), dst)
    shutil.rmtree(tmp, ignore_errors=True)
    with open(SHA_FILE, "w") as f:
        f.write(sha)
    return True
