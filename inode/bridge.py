"""Mode jembatan (tanpa Tailscale): komputer menyambung KELUAR ke dashboard It's Me
lewat domain/tunnel milik sendiri, lalu bisa diakses (layar, kontrol, berkas) dari
dashboard — sama seperti aplikasi HP. Karena komputer yang menyambung keluar, tidak
perlu VPN/Tailscale dan tetap jalan walau di balik NAT / jaringan berbeda.

Memakai ulang remote.py (tangkap layar, kirim input, berkas). Hanya jalan kalau
bridge_url & bridge_token diisi di config; jalur Tailscale biasa tidak terpengaruh.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import remote
from .cluster import log

HDR = {"Content-Type": "application/json", "X-Itsme-Dashboard": "1"}


def _post(url, obj, timeout):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _post_bytes(url, body, ctype, timeout):
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": ctype, "X-Itsme-Dashboard": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def run(cfg, stop):
    base = (cfg.get("bridge_url") or "").rstrip("/")
    token = cfg.get("bridge_token") or ""
    name = cfg.get("name") or "Komputer"
    if not base or not token:
        return
    log("mode jembatan: menyambung ke dashboard", base)
    while not stop.is_set():
        caps = ["fs"] + (["screen", "input"] if remote.IS_WIN else [])
        try:
            cmd = _post(base + "/api/hp/poll",
                        {"name": name, "token": token, "caps": caps, "kind": "computer"}, 35)
        except (urllib.error.URLError, OSError, ValueError):
            stop.wait(5)
            continue
        if not cmd or "type" not in cmd:
            continue
        try:
            _handle(base, token, name, cmd)
        except Exception as e:  # noqa: BLE001 - jangan sampai loop berhenti
            log("jembatan error:", e)


def _handle(base, token, name, cmd):
    cid, t = cmd.get("id"), cmd.get("type")
    if t == "screen":
        try:
            w = int(cmd.get("w", 1280) or 1280)
        except ValueError:
            w = 1280
        png = remote.capture_png(max(320, min(1920, w)))
        if png:
            q = urllib.parse.urlencode({"id": cid, "name": name, "token": token})
            try:
                _post_bytes(f"{base}/api/hp/frame?{q}", png, "image/png", 20)
            except OSError:
                pass
        else:
            _result(base, token, name, cid, {"error": "layar tidak tersedia"})
    elif t == "input":
        remote.inject(cmd.get("events") or [])
        _result(base, token, name, cid, {"ok": True})
    elif t == "fs_list":
        _result(base, token, name, cid, _safe(remote.fs_list, cmd.get("path", "")))
    elif t == "fs_read":
        _result(base, token, name, cid, _safe(remote.fs_read, cmd.get("path", "")))


def _safe(fn, arg):
    try:
        return fn(arg)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _result(base, token, name, cid, value):
    try:
        _post(base + "/api/hp/result", {"id": cid, "name": name, "token": token, "value": value}, 20)
    except OSError:
        pass
