"""Uji tunnel-follows-leader: alamat publik (tunnel_cmd) hanya jalan di pemimpin."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import __main__ as m  # noqa: E402


class FakeProc:
    def __init__(self):
        self.terminated = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._alive = False


class ReconcileTunnelTest(unittest.TestCase):
    def test_disabled_when_no_cmd(self):
        with mock.patch.object(m.subprocess, "Popen") as popen:
            self.assertIsNone(m.reconcile_tunnel({"tunnel_cmd": ""}, True, None))
        popen.assert_not_called()

    def test_starts_when_leader(self):
        fp = FakeProc()
        with mock.patch.object(m.subprocess, "Popen", return_value=fp) as popen:
            out = m.reconcile_tunnel({"tunnel_cmd": "cloudflared tunnel run itsme"}, True, None)
        popen.assert_called_once()
        self.assertIs(out, fp)

    def test_no_restart_when_already_running(self):
        fp = FakeProc()
        with mock.patch.object(m.subprocess, "Popen") as popen:
            out = m.reconcile_tunnel({"tunnel_cmd": "x"}, True, fp)
        popen.assert_not_called()
        self.assertIs(out, fp)

    def test_stops_when_not_leader(self):
        fp = FakeProc()
        out = m.reconcile_tunnel({"tunnel_cmd": "x"}, False, fp)
        self.assertTrue(fp.terminated)
        self.assertIsNone(out)

    def test_idle_when_not_leader_and_nothing_running(self):
        self.assertIsNone(m.reconcile_tunnel({"tunnel_cmd": "x"}, False, None))


if __name__ == "__main__":
    unittest.main()
