"""Uji Cluster: status membawa MAC sendiri, dan menyimpan MAC peer yang tergosip
LANGSUNG (dipakai /wake untuk membangunkan komputer yang sedang tidur, lihat wol.py)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode.cluster import Cluster  # noqa: E402
from inode.store import Store  # noqa: E402


def _cfg(**over):
    base = {"node_id": "A", "name": "Komputer-A", "advertise": "127.0.0.1:1", "seeds": [],
            "leader_priority": 0, "port": 1, "join_code": "x", "_fingerprint": "fpA",
            "_mac": "aaaaaaaaaaaa"}
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


if __name__ == "__main__":
    unittest.main()
