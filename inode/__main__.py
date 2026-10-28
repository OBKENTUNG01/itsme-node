"""Titik masuk.

python -m inode run              jalankan node
python -m inode status           lihat semua node di jaringan
python -m inode ask "pertanyaan" tanya basis pengetahuan seluruh jaringan
python -m inode recent           hasil riset terbaru di node ini
python -m inode code             tampilkan kode gabung
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

from . import VERSION, bridge, itsme, llm, remote, system, updater
from .cluster import Cluster, log
from .config import Config
from .crawler import Crawler
from .server import make_server
from .store import Store

DEFAULT_DATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                            "ItsMeNode", "data")


class Node:
    pass


def _stop_proc(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except OSError:
        pass


def reconcile_tunnel(cfg, is_leader, proc):
    """Jalankan 'tunnel_cmd' (mis. cloudflared) HANYA selama node ini pemimpin, supaya
    alamat publik dashboard selalu menunjuk ke pemimpin yang hidup. Begitu kepemimpinan
    pindah (failover), node lama mematikan tunnel dan pemimpin baru menyalakannya, jadi
    komputer/HP di jaringan lain otomatis tersambung lagi ke alamat yang sama.

    Kembalikan proses tunnel yang sedang jalan, atau None."""
    cmd = (cfg.get("tunnel_cmd") or "").strip()
    if not cmd:
        return None
    running = proc is not None and proc.poll() is None
    if is_leader and not running:
        try:
            p = subprocess.Popen(cmd, shell=True)
            log("tunnel publik dinyalakan (node ini pemimpin)")
            return p
        except OSError as e:
            log("gagal menyalakan tunnel:", e)
            return None
    if not is_leader and running:
        _stop_proc(proc)
        log("tunnel publik dimatikan (bukan pemimpin lagi)")
        return None
    return proc if running else None


def read_profile(cfg):
    """(profil tersimpan atau None, masih berlaku?)."""
    try:
        with open(os.path.join(cfg.data_dir, "profile.json")) as f:
            prof = json.load(f)
        age = time.time() - prof["tested"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, False
    # Model belum siap saat dites (mis. Ollama belum menyala): tes lagi sebentar lagi.
    max_age = 1800 if prof.get("retry") else 7 * 86400
    return prof, prof.get("model_setting") == cfg["model"] and age < max_age


def refresh_profile(node, stop, first):
    """Tes kemampuan di belakang, supaya node langsung bergabung ke jaringan.
    Tes ulang (bukan yang pertama) menunggu komputer menganggur."""
    while not first and not node.cluster.working and not stop.is_set():
        stop.wait(10)
    if stop.is_set():
        return
    log("menguji kemampuan komputer ini ...")
    prof = system.benchmark(node.cfg)
    prof["model_setting"] = node.cfg["model"]
    with open(os.path.join(node.cfg.data_dir, "profile.json"), "w") as f:
        json.dump(prof, f, indent=2)
    node.profile.update(prof)
    node.crawler.ensure_workers(stop, prof["workers"])
    log(f"tes selesai: skor {prof['score']} | model {prof['model'] or '-'} "
        f"({prof['tps']} tok/s) | {prof['workers']} pekerja")


def sync_itsme(node):
    """Salin memori, otak, dan mode akses It's Me dari pemimpin ke komputer ini."""
    c = node.cluster
    if not c.bot or c.is_leader or c.paused or not c.leader:
        return
    lead = c.alive().get(c.leader)
    if not lead or not lead.get("bot"):
        return
    res = c.rpc(lead["addr"], "/itsme-state", timeout=5)
    changed = itsme.write_state(node.cfg, (res or {}).get("files"))
    if changed:
        log("salinan It's Me dari pemimpin diperbarui:", ", ".join(changed))


def sync_enrolled(node):
    """Satukan daftar perangkat terdaftar dengan node lain, supaya semua node ingat
    komputer yang pernah bergabung (walau komputer itu sedang mati / diinstal ulang)."""
    c = node.cluster
    for s in list(c.alive().values()):
        if s["id"] == c.id:
            continue
        res = c.rpc(s["addr"], "/enrolled", timeout=5)
        if res and res.get("devices"):
            node.store.merge_enrolled(res["devices"])


def run(args):
    cfg = Config(args.data)
    if args.port:
        cfg["port"] = args.port
    if args.seed:
        cfg["seeds"] = args.seed
    if args.advertise:
        cfg["advertise"] = args.advertise
    if args.no_model:
        cfg["model"] = ""
    if args.always_work:
        cfg["always_work"] = True
    if not cfg["join_code"]:
        sys.exit("Kode gabung belum diisi. Jalankan install.ps1 atau isi join_code di " + cfg.path)

    node = Node()
    node.cfg = cfg
    prof, fresh = read_profile(cfg)
    node.profile = prof or system.provisional_profile(cfg)
    node.store = Store(cfg.data_dir)
    # Sidik jari perangkat keras (tetap walau OS diinstal ulang): daftarkan diri,
    # sebarkan lewat gosip, jadi komputer ini bisa gabung lagi otomatis nanti.
    cfg["_fingerprint"] = system.machine_fingerprint()
    node.store.enroll_device(cfg["_fingerprint"], cfg["name"])
    node.cluster = Cluster(cfg, node.profile, node.store, bot=itsme.ready(cfg))
    node.crawler = Crawler(cfg, node.profile, node.cluster, node.store)
    make_server(node)
    p = node.profile
    log(f"It's Me Node {VERSION} | {cfg['name']} @ {node.cluster.addr} | skor {p['score']} | "
        f"model {p['model'] or '-'} ({p['tps']} tok/s) | {p['workers']} pekerja | "
        f"bot It's Me {'siap' if node.cluster.bot else '-'}")

    stop = threading.Event()
    threading.Thread(target=node.cluster.gossip_loop, args=(stop,), daemon=True).start()
    node.crawler.start(stop)
    if cfg.get("bridge_url") and cfg.get("bridge_token"):
        # Jembatan ke dashboard (tanpa Tailscale), lihat bridge.py.
        threading.Thread(target=bridge.run, args=(cfg, stop), daemon=True).start()
    if not fresh:
        threading.Thread(target=refresh_profile, args=(node, stop, prof is None),
                         daemon=True).start()

    last_update = last_report = last_bot = last_sync = last_enrolled = time.time()
    tunnel_proc = None
    try:
        while not stop.is_set():
            now = time.time()
            # Alamat publik (tunnel) mengikuti pemimpin, supaya failover lintas-jaringan
            # tetap terjangkau di alamat yang sama. Mati bawaan (tunnel_cmd kosong).
            tunnel_proc = reconcile_tunnel(cfg, node.cluster.is_leader, tunnel_proc)
            # "Dipakai" = sedang diakses lewat web (layar/kendali/berkas dari
            # dashboard), BUKAN ketikan/klik lokal - pemilik tetap bisa
            # memakai komputernya seperti biasa tanpa menghentikan riset latar;
            # hanya akses jarak jauh yang menjeda (lihat remote.touch_remote_access).
            idle = remote.remote_idle_seconds() >= cfg["idle_minutes"] * 60
            working = cfg["always_work"] or idle
            if working != node.cluster.working:
                node.cluster.working = working
                log("komputer menganggur, mulai riset" if working else
                    "komputer sedang dipakai, riset dijeda")
                if not working and node.profile["model"]:
                    # Lepas model dari memori supaya komputer lega untuk pemiliknya.
                    threading.Thread(target=llm.unload, daemon=True,
                                     args=(cfg["ollama_url"], node.profile["model"])).start()
            if now - last_bot > 60:
                last_bot = now
                node.cluster.bot = itsme.ready(cfg)
            if now - last_sync > 20:
                last_sync = now
                sync_itsme(node)
            if now - last_enrolled > 120:
                last_enrolled = now
                sync_enrolled(node)
            if now - last_report > 300:
                last_report = now
                log(f"status: {node.store.stats()} | node hidup {len(node.cluster.alive())} | "
                    f"fase {node.cluster.phase}")
            if cfg["auto_update"] and now - last_update > 1800:
                last_update = now
                if updater.update_if_needed():
                    # Pemimpin langsung memimpin lagi setelah menyala ulang
                    # (Cluster._resume), jadi bot It's Me tidak ikut berhenti.
                    stop.set()
                    sys.exit(updater.RESTART_CODE)
            stop.wait(2)
    except KeyboardInterrupt:
        stop.set()
        node.cluster.resign()
    finally:
        _stop_proc(tunnel_proc)  # jangan tinggalkan tunnel menggantung saat berhenti/menyala ulang


def local(args, path, payload=None):
    cfg = Config(args.data)
    port = args.port or cfg["port"]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(prog="inode")
    ap.add_argument("cmd", choices=["run", "status", "ask", "recent", "code", "fingerprint"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--port", type=int)
    ap.add_argument("--seed", action="append")
    ap.add_argument("--advertise")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--always-work", action="store_true")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.cmd == "run":
        return run(args)
    if args.cmd == "fingerprint":
        return print(system.machine_fingerprint())
    if args.cmd == "code":
        return print(Config(args.data)["join_code"])
    if args.cmd == "status":
        s = local(args, "/status")
        print(f"fase: {s['phase']} | epoch {s['epoch']}")
        for n in s["nodes"]:
            print(f"{'*' if n['leader'] else ' '} {n['name']:<18} {n['addr']:<22} "
                  f"{'kerja' if n['working'] else 'dipakai':<8} skor {n['score']:<6} "
                  f"model {n['model'] or '-':<12} bot {'ya' if n.get('bot') else '-':<3} "
                  f"{n['stats']}")
        return
    if args.cmd == "recent":
        for d in local(args, "/recent")["docs"]:
            print(f"- {d['title']}\n  {d['url']}\n  {d['summary'][:300]}\n")
        return
    if args.cmd == "ask":
        r = local(args, "/ask", {"q": args.text or ""})
        print(r["answer"] or "(tanpa jawaban model; sumber terkait di bawah)")
        for i, d in enumerate(r["docs"], 1):
            print(f"[{i}] {d['title']} - {d['url']}")


if __name__ == "__main__":
    main()
