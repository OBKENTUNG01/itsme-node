"""Uji Cluster: status membawa MAC sendiri, dan menyimpan MAC peer yang tergosip
LANGSUNG (dipakai /wake untuk membangunkan komputer yang sedang tidur, lihat wol.py)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode.cluster import Cluster  # noqa: E402
from inode.server import make_server  # noqa: E402
from inode.store import Store  # noqa: E402


class _Node:
    pass


class _Cfg(dict):
    """dict + .token, seperti Config asli - cukup untuk Cluster, tanpa berkas."""

    @property
    def token(self):
        return "token-" + self.get("join_code", "")


def _cfg(**over):
    base = _Cfg(node_id="A", name="Komputer-A", advertise="127.0.0.1:1", seeds=[],
                leader_priority=0, port=1, join_code="x", _fingerprint="fpA",
                _mac="aaaaaaaaaaaa", tailscale_discovery=False,
                lan_discovery=False)  # uji tidak boleh menyapu LAN asli pemilik
    base.update(over)
    return base


class ClusterWolTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.store = Store(self.d)
        self.cluster = Cluster(_cfg(), {"score": 1, "model": ""}, self.store)

    def test_state_includes_own_mac(self):
        self.assertEqual(self.cluster.state()["mac"], "aaaaaaaaaaaa")

    def test_learn_remembers_peer_mac_for_wake(self):
        peer_state = {"id": "B", "name": "Komputer-B", "addr": "127.0.0.1:2",
                      "mac": "bbbbbbbbbbbb", "score": 1, "model": ""}
        self.cluster.learn({"state": peer_state, "peers": []})
        self.assertEqual(self.store.last_seen_mac("Komputer-B"), "bbbbbbbbbbbb")

    def test_learn_without_mac_does_not_crash_or_remember(self):
        peer_state = {"id": "C", "name": "Komputer-C", "addr": "127.0.0.1:3",
                      "score": 1, "model": ""}
        self.cluster.learn({"state": peer_state, "peers": []})
        self.assertEqual(self.store.last_seen_mac("Komputer-C"), "")

    def test_learn_ignores_own_state(self):
        # learn() mengabaikan payload yang id-nya diri sendiri (lihat cluster.py).
        self.cluster.learn({"state": self.cluster.state(), "peers": []})
        self.assertEqual(self.store.last_seen_mac("Komputer-A"), "")


class GossipDiscoveryTest(unittest.TestCase):
    """Dua node dengan join_code SAMA tapi tanpa titik awal (seed) TIDAK PERNAH
    saling tahu alamat - gosip tidak punya cara menemukan siapa pun dari nol
    (lihat server.py invite()/enroll() "seed" & install.ps1 langkah 5, juga
    catatan di memory itsme-architecture.md). Tes ini membuktikan perbaikannya
    sungguh bekerja: node baru yang diberi alamat node lain sebagai seed benar-
    benar saling menemukan lewat satu kali gossip_once() yang nyata lewat HTTP."""

    def _node(self, node_id, port, seeds):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        store = Store(d)
        cfg = _cfg(node_id=node_id, name=f"Komputer-{node_id}", port=port,
                   advertise=f"127.0.0.1:{port}", seeds=seeds, join_code="SAMA-UNTUK-SEMUA")
        cluster = Cluster(cfg, {"score": 1, "model": ""}, store)
        node = _Node()
        node.cfg, node.cluster, node.store = cfg, cluster, store
        node.profile = {"model": "", "score": 1}
        srv = make_server(node)
        # addCleanup berjalan terbalik: shutdown() harus lebih dulu, baru server_close().
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return cluster

    def test_without_seed_nobody_finds_anybody(self):
        hub = self._node("HUB1", 18461, seeds=[])
        joiner = self._node("JOIN1", 18462, seeds=[])  # persis seperti sebelum perbaikan
        joiner.gossip_once()
        self.assertEqual(joiner.peers, {})
        self.assertEqual(hub.peers, {})

    def test_seeded_joiner_discovers_hub_and_hub_learns_back(self):
        hub = self._node("HUB2", 18463, seeds=[])
        joiner = self._node("JOIN2", 18464, seeds=[hub.addr])  # persis hasil "seed" dari /pasang
        joiner.gossip_once()
        self.assertIn("HUB2", joiner.peers)
        self.assertIn("JOIN2", hub.peers)  # Hub otomatis tahu balik, tanpa perlu seed sendiri


if __name__ == "__main__":
    unittest.main()
