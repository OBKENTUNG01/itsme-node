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
import sys
import threading
import time
import urllib.request

from . import VERSION, system, updater
from .cluster import Cluster, log
from .config import Config
from .crawler import Crawler
from .server import make_server
from .store import Store

DEFAULT_DATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                            "ItsMeNode", "data")


class Node:
    pass


def load_profile(cfg):
    path = os.path.join(cfg.data_dir, "profile.json")
    try:
        with open(path) as f:
            prof = json.load(f)
        if prof.get("model_setting") == cfg["model"] and time.time() - prof["tested"] < 7 * 86400:
            return prof
    except (OSError, ValueError, KeyError):
        pass
    log("menguji kemampuan komputer ini ...")
    prof = system.benchmark(cfg)
    prof["model_setting"] = cfg["model"]
    with open(path, "w") as f:
        json.dump(prof, f, indent=2)
    return prof


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
    node.profile = load_profile(cfg)
    node.store = Store(cfg.data_dir)
    node.cluster = Cluster(cfg, node.profile, node.store)
    node.crawler = Crawler(cfg, node.profile, node.cluster, node.store)
    make_server(node)
    p = node.profile
    log(f"It's Me Node {VERSION} | {cfg['name']} @ {node.cluster.addr} | skor {p['score']} | "
        f"model {p['model'] or '-'} ({p['tps']} tok/s) | {p['workers']} pekerja")

    stop = threading.Event()
    threading.Thread(target=node.cluster.gossip_loop, args=(stop,), daemon=True).start()
    node.crawler.start(stop)

    last_update, last_report = time.time(), time.time()
    try:
        while not stop.is_set():
            idle = system.idle_seconds() >= cfg["idle_minutes"] * 60
            working = cfg["always_work"] or idle
            if working != node.cluster.working:
                node.cluster.working = working
                log("komputer menganggur, mulai riset" if working else
                    "komputer sedang dipakai, riset dijeda")
            if time.time() - last_report > 300:
                last_report = time.time()
                log(f"status: {node.store.stats()} | node hidup {len(node.cluster.alive())} | "
                    f"fase {node.cluster.phase}")
            if cfg["auto_update"] and time.time() - last_update > 1800:
                last_update = time.time()
                if updater.update_if_needed():
                    stop.set()
                    if node.cluster.is_leader:
                        node.cluster._hook("leader_stop")
                    sys.exit(updater.RESTART_CODE)
            stop.wait(2)
    except KeyboardInterrupt:
        stop.set()
        if node.cluster.is_leader:
            node.cluster._hook("leader_stop")


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
    ap.add_argument("cmd", choices=["run", "status", "ask", "recent", "code"])
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
    if args.cmd == "code":
        return print(Config(args.data)["join_code"])
    if args.cmd == "status":
        s = local(args, "/status")
        print(f"fase: {s['phase']} | epoch {s['epoch']}")
        for n in s["nodes"]:
            print(f"{'*' if n['leader'] else ' '} {n['name']:<18} {n['addr']:<22} "
                  f"{'kerja' if n['working'] else 'dipakai':<8} skor {n['score']:<6} "
                  f"model {n['model'] or '-':<12} {n['stats']}")
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
