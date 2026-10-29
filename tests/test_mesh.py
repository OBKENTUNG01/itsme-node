"""Uji mesh: prioritas Hub, penemuan LAN, dan penyatuan kelompok node yang terpisah.

Kasus nyata yang diuji: Hub + beberapa PC yang SUDAH gabung (kode gabung sama) tapi
tak pernah saling kenal, sehingga tiap PC memilih dirinya pemimpin dan menjalankan
bot Telegram sendiri dengan token yang sama. Server HTTP-nya sungguhan (loopback)."""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import auth, cluster as cluster_mod, server as server_mod, system  # noqa: E402
from inode.cluster import HUB_PRIORITY, Cluster  # noqa: E402
from inode.server import make_server  # noqa: E402
from inode.store import Store  # noqa: E402


class _Cfg(dict):
    @property
    def token(self):
        return "token-" + self.get("join_code", "")


class _Node:
    pass


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _cfg(name, port, **over):
    base = _Cfg(node_id=name, name=name, advertise=f"127.0.0.1:{port}", seeds=[],
                leader_priority=0, port=port, join_code="SAMA", _fingerprint="fp-" + name,
                _mac="aabbccddee%02d" % (port % 100), tailscale_discovery=False,
                lan_discovery=False)
    base.update(over)
    return base


def _state(id, bot=True, priority=0, age=None, score=1):
    return {"id": id, "name": id, "bot": bot, "priority": priority, "lead_age": age, "score": score}


class _Base(unittest.TestCase):
    def setUp(self):
        # Semua uji lewat loopback, yang oleh server dianggap "komputer ini sendiri" dan
        # otomatis lolos. Anggap loopback sebagai jaringan luar supaya jalur otorisasi
        # SUNGGUHAN (tanda tangan, penolakan 401) ikut teruji, bukan terlewati.
        patcher = mock.patch.object(server_mod, "LOCAL", set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cluster(self, name="A", port=1, bot=True, **over):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return Cluster(_cfg(name, port, **over), {"score": 1, "model": ""}, Store(d), bot=bot)

    def _spawn(self, name, *, hub=False, lead_age=0.0, join_code="SAMA", bot=True):
        """Node sungguhan dengan server HTTP; langsung jadi pemimpin 'pulau'-nya sendiri."""
        port = _free_port()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        over = {"join_code": join_code}
        if not hub:
            over["bridge_url"] = "https://hub.example"  # PC hasil /pasang punya jembatan keluar
        cfg = _cfg(name, port, **over)
        store = Store(d)
        c = Cluster(cfg, {"score": 1, "model": ""}, store, bot=bot)
        node = _Node()
        node.cfg, node.cluster, node.store = cfg, c, store
        node.profile = {"model": "", "score": 1}
        srv = make_server(node)
        # addCleanup berjalan terbalik: shutdown() harus lebih dulu, baru server_close().
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        c._become_leader(c.alive())
        c.lead_since = time.time() - lead_age
        return c


class ElectionRulesTest(_Base):
    def test_bot_beats_no_bot(self):
        c = self._cluster()
        self.assertTrue(c.stronger(_state("a", bot=True, age=1), _state("b", bot=False, age=999, priority=50)))

    def test_priority_beats_longer_leading(self):
        c = self._cluster()
        hub = _state("hub", priority=HUB_PRIORITY, age=60)
        pc = _state("pc", priority=0, age=86400 * 5)
        self.assertTrue(c.stronger(hub, pc))
        self.assertFalse(c.stronger(pc, hub))

    def test_same_priority_longer_leading_wins(self):
        c = self._cluster()
        self.assertTrue(c.stronger(_state("old", age=500), _state("new", age=10)))
        self.assertFalse(c.stronger(_state("new", age=10), _state("old", age=500)))

    def test_same_priority_and_age_falls_back_to_score_then_id(self):
        c = self._cluster()
        self.assertTrue(c.stronger(_state("a", age=100, score=5), _state("b", age=101, score=1)))
        self.assertTrue(c.stronger(_state("b", age=100), _state("a", age=100)))


class PriorityTest(_Base):
    def test_hub_defaults_to_hub_priority_spoke_to_zero(self):
        self.assertEqual(self._cluster().priority, HUB_PRIORITY)
        self.assertEqual(self._cluster(bridge_url="https://hub.example").priority, 0)

    def test_explicit_priority_overrides_default(self):
        self.assertEqual(self._cluster(leader_priority=3).priority, 3)
        self.assertEqual(self._cluster(leader_priority=99, bridge_url="https://hub.example").priority, 99)

    def test_state_advertises_priority_and_proto(self):
        st = self._cluster().state()
        self.assertEqual(st["priority"], HUB_PRIORITY)
        self.assertEqual(st["proto"], 2)


class TakeOverTest(_Base):
    """Hub yang menyala lagi mengambil alih dari PC yang sempat memimpin."""

    def _setup(self, leader_priority=0, leader_bot=True, me_bot=True, age_s=60, **over):
        me = self._cluster("HUB", 7, bot=me_bot, **over)
        me.started = time.time() - age_s
        leader = _state("PC", bot=leader_bot, priority=leader_priority)
        leader.update(leader="PC", phase="running")
        alive = {me.id: me.state(), "PC": leader}
        return me, leader, alive

    def test_takes_over_from_lower_priority_leader(self):
        me, leader, alive = self._setup()
        with mock.patch.object(me, "_become_leader") as become:
            me._maybe_take_over(alive, leader)
        become.assert_called_once()

    def test_does_not_take_over_from_equal_or_higher_priority(self):
        for pri in (HUB_PRIORITY, HUB_PRIORITY + 5):
            me, leader, alive = self._setup(leader_priority=pri)
            with mock.patch.object(me, "_become_leader") as become:
                me._maybe_take_over(alive, leader)
            become.assert_not_called()

    def test_waits_until_settled_after_start(self):
        me, leader, alive = self._setup(age_s=1)
        with mock.patch.object(me, "_become_leader") as become:
            me._maybe_take_over(alive, leader)
        become.assert_not_called()

    def test_node_without_bot_never_takes_over(self):
        me, leader, alive = self._setup(me_bot=False)
        with mock.patch.object(me, "_become_leader") as become:
            me._maybe_take_over(alive, leader)
        become.assert_not_called()

    def test_still_takes_over_from_botless_leader(self):
        me, leader, alive = self._setup(leader_priority=HUB_PRIORITY, leader_bot=False)
        with mock.patch.object(me, "_become_leader") as become:
            me._maybe_take_over(alive, leader)
        become.assert_called_once()


class AddressTest(_Base):
    def test_lan_ip_preferred_over_tailscale(self):
        with mock.patch.object(system, "lan_ip", return_value="192.168.1.3"), \
             mock.patch.object(system, "tailscale_ip", return_value="100.127.248.110"):
            c = self._cluster(advertise="")
        self.assertEqual(c.addr, "192.168.1.3:1")

    def test_tailscale_only_as_fallback(self):
        with mock.patch.object(system, "lan_ip", return_value=""), \
             mock.patch.object(system, "tailscale_ip", return_value="100.127.248.110"):
            c = self._cluster(advertise="")
        self.assertEqual(c.addr, "100.127.248.110:1")

    def test_explicit_advertise_wins(self):
        with mock.patch.object(system, "lan_ip", return_value="192.168.1.3"):
            c = self._cluster(advertise="10.0.0.9:7171")
        self.assertEqual(c.addr, "10.0.0.9:7171")


class LanCandidatesTest(_Base):
    def test_sweeps_own_slash24_only(self):
        c = self._cluster(port=7171)
        with mock.patch.object(system, "lan_ip", return_value="192.168.1.3"):
            cands = c._lan_candidates()
        self.assertEqual(len(cands), 253)
        self.assertNotIn("192.168.1.3:7171", cands)
        self.assertIn("192.168.1.44:7171", cands)
        self.assertTrue(all(a.startswith("192.168.1.") and a.endswith(":7171") for a in cands))

    def test_never_sweeps_public_loopback_or_cgnat(self):
        c = self._cluster()
        for ip in ("", "127.0.0.1", "8.8.8.8", "100.127.248.110", "169.254.1.5", "bukan-ip"):
            with mock.patch.object(system, "lan_ip", return_value=ip):
                self.assertEqual(c._lan_candidates(), [], ip)


class ProbeTest(_Base):
    def _plain_server(self, code, body):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                out = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"127.0.0.1:{srv.server_address[1]}"

    def test_recognises_an_itsme_node_without_sending_any_secret(self):
        node = self._spawn("PC1")
        self.assertEqual(Cluster._probe(node.addr), node.addr)

    def test_ignores_other_services(self):
        self.assertIsNone(Cluster._probe(self._plain_server(200, {"ok": True})))
        self.assertIsNone(Cluster._probe(self._plain_server(401, {"error": "lain"})))
        self.assertIsNone(Cluster._probe(self._plain_server(401, {})))

    def test_closed_port_is_none(self):
        self.assertIsNone(Cluster._probe(f"127.0.0.1:{_free_port()}"))


class MergeTest(_Base):
    def test_separate_islands_merge_and_the_hub_wins(self):
        # Hub baru 1 menit memimpin, PC sudah 3 dan 5 hari: dulu PC yang menang.
        hub = self._spawn("HUB", hub=True, lead_age=60)
        pc1 = self._spawn("PC1", lead_age=86400 * 3)
        pc2 = self._spawn("PC2", lead_age=86400 * 5)
        for c in (hub, pc1, pc2):
            self.assertEqual(len(c.alive()), 1)  # tiap pulau sendirian
            self.assertTrue(c.is_leader)         # dan mengangkat dirinya

        found = hub.discover_lan(candidates=[pc1.addr, pc2.addr, f"127.0.0.1:{_free_port()}"])
        self.assertEqual(sorted(found), sorted([pc1.addr, pc2.addr]))

        for _ in range(3):
            for c in (hub, pc1, pc2):
                c.gossip_once()

        for c in (hub, pc1, pc2):
            self.assertEqual(len(c.alive()), 3, c.cfg["name"])
            self.assertEqual(c.leader, hub.id, c.cfg["name"])
        self.assertTrue(hub.is_leader)
        self.assertFalse(pc1.is_leader)
        self.assertFalse(pc2.is_leader)

    def test_wake_on_lan_macs_are_remembered_after_merge(self):
        hub = self._spawn("HUB", hub=True)
        pc = self._spawn("PC1")
        hub.discover_lan(candidates=[pc.addr])
        hub.gossip_once()
        self.assertEqual(hub.store.last_seen_mac("PC1"), pc.cfg["_mac"])

    def test_a_foreign_mesh_is_never_adopted_nor_pestered(self):
        hub = self._spawn("HUB", hub=True)
        foreign = self._spawn("ASING", join_code="KODE-ORANG-LAIN")
        self.assertEqual(hub.discover_lan(candidates=[foreign.addr]), [foreign.addr])  # sidik jari cocok
        hub.gossip_once()                                                              # tapi kode gabung beda
        self.assertIn(foreign.addr, hub._rejected)
        self.assertNotIn(foreign.id, hub.peers)
        with mock.patch.object(auth, "http_request", wraps=auth.http_request) as spy:
            hub.gossip_once()
        self.assertFalse([c for c in spy.call_args_list if c.args[1] == foreign.addr])
        self.assertEqual(hub.discover_lan(candidates=[foreign.addr]), [])


class BackoffTest(_Base):
    def test_dead_candidate_is_delayed_then_dropped(self):
        c = self._cluster(port=1)
        dead = "127.0.0.1:1"
        c.addrs.add(dead)
        now = time.time()
        c._note_failure(dead, now)
        c._note_failure(dead, now)
        self.assertLessEqual(c._fail[dead][1], now)          # dua kali pertama: coba lagi segera
        c._note_failure(dead, now)
        self.assertGreater(c._fail[dead][1], now)            # ketiga: ditunda
        for _ in range(12):
            c._note_failure(dead, now)
        self.assertNotIn(dead, c.addrs)                      # sekitar 40 menit gagal terus: dibuang
        self.assertNotIn(dead, c._fail)

    def test_seed_from_config_is_delayed_but_never_dropped(self):
        seed = "192.168.1.3:7171"
        c = self._cluster(port=2, seeds=[seed])
        for _ in range(30):
            c._note_failure(seed, time.time())
        self.assertIn(seed, c.addrs)
        self.assertGreater(c._fail[seed][1], time.time())

    def test_delayed_candidate_is_skipped_by_gossip_rounds(self):
        c = self._cluster(port=3)
        dead = f"127.0.0.1:{_free_port()}"
        c.addrs.add(dead)
        calls = []
        real = c.rpc
        c.rpc = lambda addr, *a, **k: (calls.append(addr), real(addr, *a, **k))[1]
        for _ in range(8):
            c.gossip_once()
        self.assertEqual(calls.count(dead), 3)  # dua kali gagal + ketiga, lalu ditunda


class SweepSchedulingTest(_Base):
    def test_sweep_runs_when_alone_and_is_rate_limited(self):
        c = self._cluster(port=4, lan_discovery=True)
        with mock.patch.object(c, "discover_lan") as disc:
            c._maybe_sweep(1000.0)
            for _ in range(50):  # utas latar selesai
                if not c._sweeping:
                    break
                time.sleep(0.02)
            c._maybe_sweep(1010.0)   # terlalu cepat (< SWEEP_ALONE)
            time.sleep(0.1)
        self.assertEqual(disc.call_count, 1)

    def test_sweep_can_be_switched_off(self):
        c = self._cluster(port=5, lan_discovery=False)
        with mock.patch.object(c, "discover_lan") as disc:
            c._maybe_sweep(1000.0)
            time.sleep(0.1)
        disc.assert_not_called()

    def test_busy_mesh_sweeps_less_often(self):
        c = self._cluster(port=6, lan_discovery=True)
        c.peers["x"] = {"state": _state("x"), "seen": time.time()}
        with mock.patch.object(c, "discover_lan") as disc:
            c._maybe_sweep(cluster_mod.SWEEP_ALONE * 2)  # cukup untuk sendirian, belum untuk 'sibuk'
            time.sleep(0.1)
        disc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
