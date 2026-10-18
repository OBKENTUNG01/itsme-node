"""Keanggotaan, pemilihan pemimpin, dan pembagian kerja antar node (tanpa hub).

- Setiap node bergosip ke semua node yang dikenalnya tiap GOSSIP_EVERY detik.
- Node yang tidak terdengar DEAD_AFTER detik dianggap mati.
- Kalau pemimpin mati, SEMUA node berhenti bekerja (fase "electing"), menunggu
  SETTLE detik agar pandangan semua node sama, lalu node terbaik mengklaim
  kepemimpinan. Begitu pemimpin baru terdengar, semua node melanjutkan tugasnya.
- Node terbaik: yang bisa menjalankan bot It's Me, lalu prioritas, menganggur,
  skor, id. Kalau pemimpin tidak punya bot sedangkan node lain punya, node itu
  mengambil alih, supaya bot Telegram selalu jalan di komputer pemimpin.
- Dua klaim pemimpin bertemu (jaringan sempat terputus, atau komputer yang baru
  menyala sempat sendirian): yang punya bot menang, lalu yang paling lama
  memimpin. Jadi komputer yang baru menyala tidak merebut posisi pemimpin.
- Pemimpin yang menyala ulang cepat (mis. memasang pembaruan) langsung memimpin
  lagi, sebelum node lain sempat menganggapnya mati.
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
# Node yang belum pernah mendengar node lain sejak menyala menunggu lebih lama
# sebelum mengangkat dirinya (beri waktu Tailscale tersambung saat komputer baru menyala).
LONE_WAIT = 30.0
CLAIM_TIMEOUT = 25.0
# Pemimpin yang menyala lagi dalam sekian detik langsung memimpin lagi.
RESUME_WINDOW = 8.0
# Selisih lama memimpin yang dianggap sama (jeda gosip tidak persis).
AGE_TOLERANCE = 5.0


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


class Cluster:
    def __init__(self, cfg, profile, store, bot=False):
        self.cfg, self.profile, self.store = cfg, profile, store
        self.id = cfg["node_id"]
        self.addr = self._my_addr()
        self.peers = {}  # id -> {"state":..., "seen": ts}
        self.addrs = set(cfg["seeds"])  # alamat yang belum tentu dikenal
        self.lock = threading.RLock()
        self.leader = None
        # Epoch hanya penanda urutan pemimpin (untuk log & status).
        self.epoch = 0
        self._stored_epoch = int(store.get("epoch", 0))
        self.phase = "electing"
        self.elect_start = time.time() + 2  # beri waktu gosip pertama
        self.started = time.time()
        self.working = False
        # Bot It's Me di komputer ini siap menjadi bot Telegram aktif?
        self.bot = bot
        self.lead_since = 0.0
        self._beat = 0.0
        self._ever_heard = False
        self.pool = concurrent.futures.ThreadPoolExecutor(16)
        self._owner_cache = ({}, None)
        self._last_discovery = 0
        self._resume()

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
                "bot": self.bot, "leader": self.leader, "epoch": self.epoch,
                "phase": self.phase,
                "lead_age": time.time() - self.lead_since if self.is_leader else None,
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
        """Urutan calon pemimpin saat pemilihan (lebih besar lebih diutamakan)."""
        return (bool(s.get("bot")), s.get("priority", 0), bool(s.get("working")),
                s.get("score", 0), s["id"])

    @staticmethod
    def claims(s):
        """Apakah state s menyatakan dirinya pemimpin yang sedang memimpin."""
        return s.get("leader") == s["id"] and s.get("phase") == "running"

    def _lead_age(self, s):
        """Lama s memimpin (detik), diukur dengan jam s sendiri ditambah umur kabarnya."""
        if s["id"] == self.id:
            return time.time() - self.lead_since if self.is_leader else -1.0
        age = s.get("lead_age")
        if age is None:
            return -1.0
        p = self.peers.get(s["id"])
        return age + (time.time() - p["seen"] if p else 0.0)

    def stronger(self, a, b):
        """True kalau klaim pemimpin a mengalahkan klaim b."""
        if bool(a.get("bot")) != bool(b.get("bot")):
            return bool(a.get("bot"))
        age_a, age_b = self._lead_age(a), self._lead_age(b)
        if abs(age_a - age_b) > AGE_TOLERANCE:
            return age_a > age_b
        key = lambda s: (s.get("priority", 0), s.get("score", 0), s["id"])  # noqa: E731
        return key(a) > key(b)

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
                self._ever_heard = True
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
        """s adalah state node lain yang mengaku pemimpin; ikuti kalau lebih kuat."""
        if not self.claims(s):
            return
        with self.lock:
            if self.leader == s["id"]:
                self.epoch = max(self.epoch, s.get("epoch", 0))
                return
            cur = self.alive().get(self.leader) if self.leader else None
            if cur is not None and self.claims(cur) and not self.stronger(s, cur):
                return
            was_leader = self.is_leader
            self.leader = s["id"]
            self.epoch = max(self.epoch, s.get("epoch", 0))
            self.store.put("epoch", self.epoch)
            if was_leader:
                self._forget_leadership()
            if self.phase == "running":
                log(f"pemimpin sekarang: {s['name']} (epoch {self.epoch})")
            self._set_phase("running", f"pemimpin: {s['name']} (epoch {self.epoch})")
        if was_leader:
            log("kepemimpinan diserahkan ke", s["name"])
            self._hook("leader_stop")

    def check_leader(self):
        now = time.time()
        with self.lock:
            alive = self.alive()
            cur = alive.get(self.leader) if self.leader else None
            if cur is not None and not self.claims(cur):
                # Pemimpin masih hidup tapi sudah tidak memimpin (mis. menyerahkan
                # ke node yang punya bot): ikuti penggantinya kalau jelas.
                nxt = alive.get(cur.get("leader"))
                if nxt is not None and self.claims(nxt):
                    self._consider_claim(nxt)
                    cur = alive.get(self.leader)
                else:
                    cur = None
            if cur is not None:
                if self.phase != "running":
                    self._set_phase("running", "pemimpin terdengar lagi")
                if self.is_leader:
                    self._heartbeat()
                else:
                    self._maybe_take_over(alive, cur)
                return
            if self.phase == "running":
                name = ((self.peers.get(self.leader) or {}).get("state") or {}).get("name", "?")
                self._set_phase("electing", f"pemimpin {name} hilang, semua node berhenti sejenak")
                self.elect_start = now
                self.leader = None
            waited = now - self.elect_start
            if waited < (SETTLE if self._ever_heard else LONE_WAIT):
                return
            best = max(alive.values(), key=self.rank)
            if best["id"] == self.id or waited > SETTLE + CLAIM_TIMEOUT:
                self._become_leader(alive)

    def _maybe_take_over(self, alive, cur):
        """Pemimpin tidak punya bot It's Me padahal node ini punya: ambil alih,
        supaya bot Telegram jalan di pemimpin. Hanya node ber-bot terbaik."""
        if cur.get("bot") or not self.bot:
            return
        best = max((s for s in alive.values() if s.get("bot")), key=self.rank)
        if best["id"] == self.id:
            log(f"pemimpin {cur['name']} tidak punya bot It's Me; node ini mengambil alih")
            self._become_leader(alive)

    def _become_leader(self, alive):
        self.epoch = max([self.epoch, self._stored_epoch]
                         + [s.get("epoch", 0) for s in alive.values()]) + 1
        self.leader = self.id
        self.lead_since = time.time()
        self._beat = 0.0
        self._heartbeat()
        self.store.put("epoch", self.epoch)
        if self.phase == "running":
            log(f"node ini menjadi PEMIMPIN (epoch {self.epoch})")
        self._set_phase("running", f"node ini menjadi PEMIMPIN (epoch {self.epoch})")
        self._hook("leader_start")
        # Umumkan segera supaya node lain cepat melanjutkan kerja.
        threading.Thread(target=self.gossip_once, daemon=True).start()

    def _heartbeat(self):
        """Catat bahwa node ini masih memimpin, untuk _resume setelah menyala ulang."""
        now = time.time()
        if now - self._beat >= GOSSIP_EVERY / 2:
            self._beat = now
            self.store.put("leadership", json.dumps(
                {"epoch": self.epoch, "since": self.lead_since, "beat": now}))

    def _forget_leadership(self):
        self.lead_since = 0.0
        self.store.put("leadership", "")

    def _resume(self):
        """Pemimpin yang baru saja menyala ulang (mis. pembaruan) memimpin lagi."""
        try:
            saved = json.loads(self.store.get("leadership") or "null")
            fresh = saved and 0 <= time.time() - float(saved["beat"]) < RESUME_WINDOW
        except (ValueError, KeyError, TypeError):
            fresh = False
        if not fresh:
            return
        self.leader, self.epoch = self.id, int(saved["epoch"])
        self.lead_since = float(saved["since"])
        self.phase = "running"
        log(f"menyala ulang cepat, langsung memimpin lagi (epoch {self.epoch})")

    def resign(self):
        """Berhenti memimpin karena node dimatikan."""
        with self.lock:
            if self.is_leader:
                self._forget_leadership()
                self._hook("leader_stop")

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

    def owner(self, domain, ws=None):
        """Node yang mengerjakan domain ini. `ws` = hasil workers() kalau sudah ada
        (hemat saat menanyakan banyak domain sekaligus)."""
        ws = self.workers() if ws is None else ws
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
