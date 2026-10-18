"""Keanggotaan, pemilihan pemimpin, dan pembagian kerja antar node (tanpa hub).

- Setiap node bergosip ke semua node yang dikenalnya tiap GOSSIP_EVERY detik.
- Node yang tidak terdengar DEAD_AFTER detik dianggap mati.
- Kalau pemimpin mati, SEMUA node berhenti bekerja (fase "electing"), menunggu
  SETTLE detik agar pandangan semua node sama, lalu node terbaik (menganggur,
  prioritas, skor, id) mengklaim kepemimpinan dengan epoch baru. Begitu
  pemimpin baru terdengar, semua node melanjutkan tugasnya.
- Klaim ganda diselesaikan otomatis: epoch lebih besar menang, kalau sama,
  node dengan peringkat lebih tinggi menang.
- Domain internet dibagi ke node yang sedang menganggur dengan weighted
  rendezvous hashing, sehingga satu domain hanya dikunjungi satu node.
"""

import concurrent.futures
import hashlib
import json
import math
import subprocess
import threading
import time
import urllib.request

from . import VERSION, system

GOSSIP_EVERY = 2.0
DEAD_AFTER = 10.0
FORGET_AFTER = 600.0
SETTLE = 6.0
CLAIM_TIMEOUT = 25.0


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


class Cluster:
    def __init__(self, cfg, profile, store):
        self.cfg, self.profile, self.store = cfg, profile, store
        self.id = cfg["node_id"]
        self.addr = self._my_addr()
        self.peers = {}  # id -> {"state":..., "seen": ts}
        self.addrs = set(cfg["seeds"])  # alamat yang belum tentu dikenal
        self.lock = threading.RLock()
        self.leader = None
        # Epoch tersimpan hanya dipakai saat mengklaim, supaya node yang baru
        # menyala tetap menerima pemimpin yang sedang berjalan.
        self.epoch = 0
        self._stored_epoch = int(store.get("epoch", 0))
        self.phase = "electing"
        self.elect_start = time.time() + 2  # beri waktu gosip pertama
        self.started = time.time()
        self.working = False
        self.pool = concurrent.futures.ThreadPoolExecutor(16)
        self._owner_cache = ({}, None)
        self._last_discovery = 0

    def _my_addr(self):
        if self.cfg["advertise"]:
            return self.cfg["advertise"]
        ip = system.tailscale_ip() or "127.0.0.1"
        return f"{ip}:{self.cfg['port']}"

    # --- RPC -------------------------------------------------------------------
    def rpc(self, addr, path, payload=None, timeout=5):
        req = urllib.request.Request(
            f"http://{addr}{path}", data=json.dumps(payload or {}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.cfg.token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (OSError, ValueError):
            return None

    # --- status ----------------------------------------------------------------
    def state(self):
        return {"id": self.id, "name": self.cfg["name"], "addr": self.addr,
                "score": self.profile["score"], "model": self.profile["model"],
                "working": self.working, "priority": self.cfg["leader_priority"],
                "leader": self.leader, "epoch": self.epoch, "phase": self.phase,
                "version": VERSION, "started": self.started, "stats": self.store.stats()}

    def alive(self):
        """Semua node yang hidup, termasuk diri sendiri: id -> state."""
        now = time.time()
        with self.lock:
            out = {i: p["state"] for i, p in self.peers.items() if now - p["seen"] < DEAD_AFTER}
        out[self.id] = self.state()
        return out

    @staticmethod
    def rank(s):
        return (bool(s.get("working")), s.get("priority", 0), s.get("score", 0), s["id"])

    @property
    def paused(self):
        return self.phase != "running"

    @property
    def is_leader(self):
        return self.leader == self.id

    # --- gosip -----------------------------------------------------------------
    def learn(self, payload):
        s = payload.get("state")
        if s and s.get("id") and s["id"] != self.id:
            with self.lock:
                self.peers[s["id"]] = {"state": s, "seen": time.time()}
                self.addrs.discard(s["addr"])
            self._consider_claim(s)
        for a in payload.get("peers", []):
            if a != self.addr and not any(p["state"]["addr"] == a for p in self.peers.values()):
                self.addrs.add(a)

    def _gossip_payload(self):
        return {"state": self.state(), "peers": [s["addr"] for s in self.alive().values()]}

    def gossip_once(self):
        now = time.time()
        if self.cfg["tailscale_discovery"] and now - self._last_discovery > 30:
            self._last_discovery = now
            for ip in system.tailscale_peers():
                self.addrs.add(f"{ip}:{self.cfg['port']}")
        with self.lock:
            # Hapus node yang sudah lama mati.
            for i in [i for i, p in self.peers.items() if now - p["seen"] > FORGET_AFTER]:
                del self.peers[i]
            targets = {p["state"]["addr"] for p in self.peers.values()} | set(self.addrs)
        targets.discard(self.addr)
        payload = self._gossip_payload()
        futs = {self.pool.submit(self.rpc, a, "/gossip", payload, 3): a for a in targets}
        for f in concurrent.futures.as_completed(futs):
            res = f.result()
            if res:
                self.learn(res)
        self.check_leader()

    def gossip_loop(self, stop):
        while not stop.is_set():
            t = time.time()
            try:
                self.gossip_once()
            except Exception as e:  # jangan sampai gosip berhenti
                log("gosip error:", e)
            stop.wait(max(0.2, GOSSIP_EVERY - (time.time() - t)))

    # --- pemimpin --------------------------------------------------------------
    def _consider_claim(self, s):
        """s adalah state node lain; terima klaim pemimpinnya kalau lebih kuat."""
        if s.get("leader") != s["id"] or s.get("phase") != "running":
            return
        with self.lock:
            cur = self.alive().get(self.leader) if self.leader else None
            better = s["epoch"] > self.epoch or (
                s["epoch"] == self.epoch and (cur is None or self.rank(s) > self.rank(cur)))
            if not better or self.leader == s["id"]:
                if self.leader == s["id"]:
                    self.epoch = max(self.epoch, s["epoch"])
                return
            was_leader = self.is_leader
            self.leader, self.epoch = s["id"], s["epoch"]
            self.store.put("epoch", self.epoch)
            self._set_phase("running", f"pemimpin: {s['name']} (epoch {self.epoch})")
        if was_leader:
            log("kepemimpinan diserahkan ke", s["name"])
            self._hook("leader_stop")

    def check_leader(self):
        now = time.time()
        with self.lock:
            alive = self.alive()
            if self.leader and self.leader in alive:
                if self.phase != "running":
                    self._set_phase("running", "pemimpin terdengar lagi")
                return
            if self.phase == "running":
                name = (self.peers.get(self.leader) or {}).get("state", {}).get("name", "?")
                self._set_phase("electing", f"pemimpin {name} hilang, semua node berhenti sejenak")
                self.elect_start = now
                self.leader = None
            waited = now - self.elect_start
            if waited < SETTLE:
                return
            best = max(alive.values(), key=self.rank)
            if best["id"] == self.id or waited > SETTLE + CLAIM_TIMEOUT:
                self._become_leader(alive)

    def _become_leader(self, alive):
        self.epoch = max([self.epoch, self._stored_epoch]
                         + [s.get("epoch", 0) for s in alive.values()]) + 1
        self.leader = self.id
        self.store.put("epoch", self.epoch)
        self._set_phase("running", f"node ini menjadi PEMIMPIN (epoch {self.epoch})")
        self._hook("leader_start")
        # Umumkan segera supaya node lain cepat melanjutkan kerja.
        threading.Thread(target=self.gossip_once, daemon=True).start()

    def _set_phase(self, phase, why):
        if phase != self.phase:
            log(("LANJUT: " if phase == "running" else "JEDA: ") + why)
        self.phase = phase

    def _hook(self, key):
        cmd = self.cfg.get(key)
        if cmd:
            try:
                subprocess.Popen(cmd, shell=True)
            except OSError as e:
                log(f"gagal menjalankan {key}:", e)

    # --- pembagian domain ------------------------------------------------------
    def workers(self):
        return sorted((s for s in self.alive().values() if s.get("working")),
                      key=lambda s: s["id"])

    @staticmethod
    def _weight(domain, s):
        h = int(hashlib.sha1((domain + "|" + s["id"]).encode()).hexdigest(), 16)
        u = (h + 1) / (2**160 + 2)
        return -max(1.0, s.get("score", 1)) / math.log(u)

    def owner(self, domain):
        ws = self.workers()
        sig = tuple((s["id"], s.get("score")) for s in ws)
        cache, csig = self._owner_cache
        if csig != sig:
            cache = {}
            self._owner_cache = (cache, sig)
        if domain not in cache:
            cache[domain] = max(ws, key=lambda s: self._weight(domain, s)) if ws else None
        return cache[domain]

    def replica_for(self, domain):
        """Node lain (hidup) yang menyimpan salinan hasil riset domain ini."""
        others = [s for i, s in self.alive().items() if i != self.id]
        return max(others, key=lambda s: self._weight(domain, s)) if others else None
