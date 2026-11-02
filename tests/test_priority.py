"""Uji prioritas rendah untuk Ollama (supaya komputer yang sedang dipakai tidak tersendat): membaca daftar
proses, menurunkan prioritas sekali per proses, dan menyalakan Ollama dengan prioritas rendah. Windows ditiru
(ctypes.windll palsu) supaya berjalan di CI Linux; satu uji memakai proses umpan sungguhan di Windows."""

import os
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import system  # noqa: E402

TASKLIST = "\n".join([
    '"System Idle Process","0","Services","0","8 K"',
    '"ollama.exe","8364","Services","0","123,456 K"',
    '"ollama app.exe","11612","Console","1","40,000 K"',      # aplikasi tray: bukan server/runner
    '"python.exe","2020","Console","1","30,000 K"',
    '"llama-server.exe","9000","Services","0","2,500,000 K"',
    '"OLLAMA_LLAMA_SERVER.EXE","9100","Services","0","2,500,000 K"',   # huruf besar kecil tidak berpengaruh
    '"ollama.exe","bukan-angka","Services","0","1 K"',
    "",
])


class FakeWinFunction:
    """Panggilan WinAPI palsu yang menampung argtypes/restype ctypes."""

    def __init__(self, target):
        self.target = target
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.target(*args)


class FakeKernel:
    def __init__(self, deny=()):
        self.deny = set(deny)
        self.opened, self.set_calls, self.closed = [], [], []
        self.OpenProcess = FakeWinFunction(self._open_process)
        self.SetPriorityClass = FakeWinFunction(self._set_priority_class)
        self.CloseHandle = FakeWinFunction(self._close_handle)

    def _open_process(self, access, inherit, pid):
        self.opened.append((access, pid))
        return 0 if pid in self.deny else 1000 + pid

    def _set_priority_class(self, handle, cls):
        self.set_calls.append((handle, cls))
        return 1

    def _close_handle(self, handle):
        self.closed.append(handle)


def fake_windll(kernel):
    return mock.Mock(kernel32=kernel)


class OllamaPidsTest(unittest.TestCase):
    def test_hanya_server_dan_runner_ollama(self):
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(system, "_run", return_value=TASKLIST):
            self.assertEqual(system.ollama_pids(), [8364, 9000, 9100])

    def test_bukan_windows(self):
        with mock.patch.object(system, "IS_WIN", False):
            self.assertEqual(system.ollama_pids(), [])
            self.assertEqual(system.lower_ollama_priority(set()), 0)

    def test_tasklist_gagal(self):
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(system, "_run", return_value=""):
            self.assertEqual(system.ollama_pids(), [])


class LowerPriorityTest(unittest.TestCase):
    def run_lower(self, kernel, pids, done):
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(system, "ollama_pids", return_value=pids), \
                mock.patch.object(system.ctypes, "windll", fake_windll(kernel), create=True):
            return system.lower_ollama_priority(done)

    def test_semua_diturunkan_ke_di_bawah_normal(self):
        kernel, done = FakeKernel(), set()
        self.assertEqual(self.run_lower(kernel, [10, 20], done), 2)
        self.assertEqual(kernel.set_calls, [(1010, system.BELOW_NORMAL_PRIORITY_CLASS)] * 1 + [(1020, 0x4000)])
        self.assertEqual(system.BELOW_NORMAL_PRIORITY_CLASS, 0x00004000)
        self.assertEqual(done, {10, 20})
        self.assertEqual(kernel.closed, [1010, 1020])                   # pegangan selalu ditutup

    def test_hak_yang_diminta_hanya_mengatur_prioritas(self):
        kernel = FakeKernel()
        self.run_lower(kernel, [10], set())
        self.assertEqual(kernel.opened, [(system.PROCESS_SET_INFORMATION, 10)])   # bukan hak penuh

    def test_windows_api_prototypes_use_pointer_sized_handles(self):
        from ctypes import wintypes

        kernel = FakeKernel()
        self.run_lower(kernel, [10], set())

        self.assertEqual(kernel.OpenProcess.argtypes,
                         [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
        self.assertIs(kernel.OpenProcess.restype, wintypes.HANDLE)
        self.assertEqual(kernel.SetPriorityClass.argtypes,
                         [wintypes.HANDLE, wintypes.DWORD])
        self.assertIs(kernel.SetPriorityClass.restype, wintypes.BOOL)
        self.assertEqual(kernel.CloseHandle.argtypes, [wintypes.HANDLE])
        self.assertIs(kernel.CloseHandle.restype, wintypes.BOOL)

    def test_tidak_diulang_untuk_proses_yang_sama(self):
        kernel, done = FakeKernel(), set()
        self.run_lower(kernel, [10, 20], done)
        kernel.set_calls.clear()
        self.assertEqual(self.run_lower(kernel, [10, 20, 30], done), 1)   # hanya yang baru
        self.assertEqual(len(kernel.set_calls), 1)

    def test_proses_yang_tak_bisa_dibuka_dilewati_dan_tidak_dicoba_terus(self):
        kernel, done = FakeKernel(deny={20}), set()
        self.assertEqual(self.run_lower(kernel, [10, 20], done), 1)
        self.assertIn(20, done)
        kernel.opened.clear()
        self.run_lower(kernel, [10, 20], done)
        self.assertEqual(kernel.opened, [])


class StartOllamaTest(unittest.TestCase):
    def flags(self, low_priority, win=True):
        with mock.patch.object(system, "IS_WIN", win), mock.patch.object(system, "_NO_WINDOW", 0x08000000 if win else 0), \
                mock.patch.object(system.shutil, "which", return_value="/bin/ollama"), \
                mock.patch.object(system.subprocess, "Popen") as popen:
            self.assertTrue(system.start_ollama(low_priority=low_priority))
        return popen.call_args.kwargs["creationflags"], popen.call_args.args[0]

    def test_prioritas_rendah_bawaan(self):
        flags, cmd = self.flags(True)
        self.assertTrue(flags & system.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertTrue(flags & 0x00000008)           # DETACHED_PROCESS: tidak ikut mati bersama node
        self.assertTrue(flags & 0x08000000)           # tanpa jendela
        self.assertEqual(cmd, ["/bin/ollama", "serve"])

    def test_tanpa_prioritas_rendah(self):
        flags, _ = self.flags(False)
        self.assertFalse(flags & system.BELOW_NORMAL_PRIORITY_CLASS)

    def test_default_adalah_prioritas_rendah(self):
        with mock.patch.object(system, "IS_WIN", True), mock.patch.object(system.shutil, "which", return_value="x"), \
                mock.patch.object(system.subprocess, "Popen") as popen:
            system.start_ollama()
        self.assertTrue(popen.call_args.kwargs["creationflags"] & system.BELOW_NORMAL_PRIORITY_CLASS)

    def test_bukan_windows_tanpa_bendera(self):
        flags, _ = self.flags(True, win=False)
        self.assertEqual(flags, 0)

    def test_ollama_tidak_terpasang(self):
        with mock.patch.object(system.shutil, "which", return_value=None), mock.patch.object(system, "IS_WIN", False):
            self.assertFalse(system.start_ollama())


@unittest.skipUnless(sys.platform == "win32", "proses umpan sungguhan hanya di Windows")
class RealProcessTest(unittest.TestCase):
    def test_umpan_sungguhan_turun_ke_di_bawah_normal(self):
        decoy = subprocess.Popen(["ping.exe", "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL,
                                 creationflags=0x08000000)
        self.addCleanup(decoy.kill)
        self.addCleanup(decoy.wait)
        time.sleep(0.3)
        with mock.patch.object(system, "OLLAMA_PROCS", {"ping.exe"}):
            self.assertIn(decoy.pid, system.ollama_pids())
            done = set()
            self.assertGreaterEqual(system.lower_ollama_priority(done), 1)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {decoy.pid}).PriorityClass"],
                             capture_output=True, text=True, timeout=30, creationflags=0x08000000).stdout.strip()
        self.assertEqual(out, "BelowNormal")


if __name__ == "__main__":
    unittest.main()
