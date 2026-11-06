"""Uji ollama_ctl (status, pasang, copot Ollama dari dashboard).

Semua proses, registry, dan jeda DIPALSUKAN - tes tidak boleh benar-benar memasang atau
mencopot apa pun. Uji nyata copot -> pasang ulang dilakukan terpisah di komputer sungguhan.
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inode import ollama_ctl, system  # noqa: E402

IS_WIN = sys.platform == "win32"


class _Base(unittest.TestCase):
    def setUp(self):
        ollama_ctl._job = None
        ollama_ctl._invalidate()
        system.OLLAMA_MAINTENANCE.clear()
        self.addCleanup(system.OLLAMA_MAINTENANCE.clear)
        self.addCleanup(setattr, ollama_ctl, "_job", None)

    def wait_job(self, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            j = ollama_ctl.job()
            if j and j["state"] != "running":
                return j
            time.sleep(0.02)
        self.fail("pekerjaan latar tidak selesai")


@unittest.skipIf(IS_WIN, "jalur non-Windows")
class NonWindowsTest(_Base):
    def test_refused_outside_windows(self):
        self.assertFalse(ollama_ctl.start("install")["ok"])


class _WinBase(_Base):
    """Logika pasang/copot memakai tiruan penuh (registry, proses, jeda) - jalan di Linux/macOS juga
    selama IS_WIN dianggap True, jadi CI (Linux) ikut menguji logikanya, bukan hanya Windows."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(ollama_ctl, "IS_WIN", True)
        p.start()
        self.addCleanup(p.stop)


class StartTest(_WinBase):
    def test_unknown_action_refused(self):
        res = ollama_ctl.start("format-c")
        self.assertFalse(res["ok"])
        self.assertIsNone(ollama_ctl.job())

    def test_only_one_job_at_a_time(self):
        ollama_ctl._job = {"action": "uninstall", "state": "running", "step": "x"}
        res = ollama_ctl.start("install")
        self.assertFalse(res["ok"])
        self.assertIn("masih", res["error"])

    def test_job_holds_maintenance_flag_while_running_then_restarts_ollama(self):
        seen = {}

        def fake_install():
            seen["during"] = system.OLLAMA_MAINTENANCE.is_set()
            return True, "terpasang"

        with mock.patch.object(ollama_ctl, "_install", fake_install), \
             mock.patch.object(system, "start_ollama") as start_ollama:
            self.assertTrue(ollama_ctl.start("install")["ok"])
            j = self.wait_job()
        self.assertTrue(seen["during"], "penjaga harus tahu Ollama sedang dipasang")
        self.assertFalse(system.OLLAMA_MAINTENANCE.is_set(), "tanda harus dilepas sesudahnya")
        self.assertEqual((j["state"], j["message"]), ("done", "terpasang"))
        start_ollama.assert_called_once()

    def test_crash_becomes_failed_job_and_releases_flag(self):
        with mock.patch.object(ollama_ctl, "_uninstall", side_effect=RuntimeError("meledak")):
            ollama_ctl.start("uninstall")
            j = self.wait_job()
        self.assertEqual(j["state"], "failed")
        self.assertIn("meledak", j["message"])
        self.assertFalse(system.OLLAMA_MAINTENANCE.is_set())


class InstallTest(_WinBase):
    def setUp(self):
        super().setUp()
        # Penjaga pembunuh proses TIDAK boleh mematikan Ollama sungguhan milik pemilik saat tes.
        p = mock.patch.object(ollama_ctl, "_kill")
        self.kill = p.start()
        self.addCleanup(p.stop)

    def test_keep_killing_runs_until_the_block_ends(self):
        calls = []
        self.kill.side_effect = lambda *a, **k: calls.append(1)
        with ollama_ctl._KeepKilling() as k:
            end = time.time() + 2
            while not calls and time.time() < end:
                time.sleep(0.01)
        n = len(calls)
        time.sleep(0.2)
        self.assertGreaterEqual(n, 1, "harus mematikan proses Ollama selama pekerjaan")
        self.assertEqual(len(calls), n, "harus berhenti begitu blok selesai")
        self.assertFalse(k._thread.is_alive())

    def test_leftover_files_without_registry_still_installs(self):
        """Sisa uninstall tak tuntas (ollama.exe ada, registry hilang) bukan 'sudah terpasang'."""
        def fake(*a, **k):
            fake.n += 1
            return {"installed": fake.n > 1, "version": "0.40.15", "leftover": True}
        fake.n = 0
        with mock.patch.object(ollama_ctl, "status", fake),              mock.patch.object(ollama_ctl, "_winget", return_value="winget.exe"),              mock.patch.object(ollama_ctl, "_wait_file_process", return_value=0) as run,              mock.patch.object(ollama_ctl, "_disable_tray"):
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok, msg)
        run.assert_called_once()

    def status_seq(self, *installed):
        it = iter(installed)
        last = [False]

        def fake(*a, **k):
            last[0] = next(it, last[0])
            return {"installed": last[0], "version": "0.40.15" if last[0] else None}
        return fake

    def test_already_installed_runs_nothing(self):
        with mock.patch.object(ollama_ctl, "status", self.status_seq(True)), \
             mock.patch.object(ollama_ctl, "_wait_file_process") as run:
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok)
        run.assert_not_called()

    def test_winget_no_upgrade_code_counts_as_installed(self):
        with mock.patch.object(ollama_ctl, "status", self.status_seq(False, True)), \
             mock.patch.object(ollama_ctl, "_winget", return_value="winget.exe"), \
             mock.patch.object(ollama_ctl, "_wait_file_process", return_value=0x8A15002B) as run, \
             mock.patch.object(ollama_ctl, "_disable_tray") as tray:
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok, msg)
        self.assertIn("winget", msg)
        args = run.call_args[0][0]
        for flag in ("Ollama.Ollama", "--silent", "--disable-interactivity", "--accept-source-agreements"):
            self.assertIn(flag, args)
        tray.assert_called_once()

    def test_winget_thinks_it_is_installed_after_manual_uninstall_so_it_is_forced(self):
        """Kasus nyata: uninstaller dijalankan di luar winget -> winget menjawab 0x8A15002B padahal
        registry sudah bersih. Harus dipaksa (--force) lewat winget, bukan langsung ke pemasang resmi."""
        with mock.patch.object(ollama_ctl, "status", self.status_seq(False, False, True)), \
             mock.patch.object(ollama_ctl, "_winget", return_value="winget.exe"), \
             mock.patch.object(ollama_ctl, "_wait_file_process", side_effect=[0x8A15002B, 0]) as run, \
             mock.patch.object(ollama_ctl, "_disable_tray"):
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok, msg)
        self.assertIn("winget", msg)
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("--force", run.call_args_list[0][0][0])
        self.assertIn("--force", run.call_args_list[1][0][0])

    def test_winget_failure_falls_back_to_official_installer(self):
        # status(): cek awal (belum), lalu sesudah pemasang resmi (sudah). Kode winget
        # 0x8A150008 (unduhan gagal) bukan "sudah terpasang", jadi status tak dicek di antaranya.
        with mock.patch.object(ollama_ctl, "status", self.status_seq(False, True)), \
             mock.patch.object(ollama_ctl, "_winget", return_value="winget.exe"), \
             mock.patch.object(ollama_ctl, "_wait_file_process", side_effect=[0x8A150008, 0]) as run, \
             mock.patch.object(ollama_ctl, "_disable_tray"):
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok, msg)
        self.assertIn("irm https://ollama.com/install.ps1 | iex", run.call_args_list[1][0][0])

    def test_total_failure_is_reported_with_log(self):
        with mock.patch.object(ollama_ctl, "status", self.status_seq(False)), \
             mock.patch.object(ollama_ctl, "_winget", return_value=None), \
             mock.patch.object(ollama_ctl, "_wait_file_process", return_value=1):
            ok, msg = ollama_ctl._install()
        self.assertFalse(ok)
        self.assertIn("log", msg.lower())


class UninstallTest(_WinBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(ollama_ctl.time, "sleep")  # tanpa jeda sungguhan
        p.start()
        self.addCleanup(p.stop)

    def test_not_installed_is_a_noop(self):
        with mock.patch.object(ollama_ctl, "_registry", return_value=None), \
             mock.patch.object(ollama_ctl.os.path, "isfile", return_value=False), \
             mock.patch.object(ollama_ctl, "status", return_value={"unmanaged": False}), \
             mock.patch.object(ollama_ctl.subprocess, "Popen") as popen:
            ok, msg = ollama_ctl._uninstall()
        self.assertTrue(ok)
        popen.assert_not_called()

    def test_waits_until_really_gone_while_killing_restarts(self):
        reg = {"version": "0.40.13", "path": r"C:\x", "uninstall": r"C:\x\unins000.exe"}
        with mock.patch.object(ollama_ctl, "_registry", return_value=reg), \
             mock.patch.object(ollama_ctl.os.path, "isfile", return_value=True), \
             mock.patch.object(ollama_ctl, "_gone", side_effect=[False, False, True, True, True]), \
             mock.patch.object(ollama_ctl, "_kill") as kill, \
             mock.patch.object(ollama_ctl.subprocess, "Popen") as popen:
            ok, msg = ollama_ctl._uninstall()
        self.assertTrue(ok, msg)
        self.assertIn("model", msg.lower())  # pemilik diberi tahu model tetap tersimpan
        args = popen.call_args[0][0]
        self.assertEqual(args[0], reg["uninstall"])
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"):
            self.assertIn(flag, args)
        self.assertGreaterEqual(kill.call_count, 1, "proses Ollama yang hidup lagi harus dimatikan")

    def test_partial_uninstall_is_not_reported_as_success(self):
        reg = {"version": "0.40.13", "path": r"C:\x", "uninstall": r"C:\x\unins000.exe"}
        with mock.patch.object(ollama_ctl, "_registry", return_value=reg), \
             mock.patch.object(ollama_ctl.os.path, "isfile", return_value=True), \
             mock.patch.object(ollama_ctl, "_gone", return_value=False), \
             mock.patch.object(ollama_ctl, "UNINSTALL_WAIT", 0), \
             mock.patch.object(ollama_ctl, "_kill"), \
             mock.patch.object(ollama_ctl.subprocess, "Popen") as popen:
            ok, msg = ollama_ctl._uninstall()
        self.assertFalse(ok)
        self.assertIn("belum tuntas", msg)
        self.assertEqual(popen.call_count, 2)  # dicoba ulang sekali


class SummaryTest(_WinBase):
    def test_summary_is_cached_for_gossip(self):
        with mock.patch.object(ollama_ctl, "_registry", return_value={"version": "1.2.3", "path": "", "uninstall": ""}) as reg, \
             mock.patch.object(ollama_ctl, "_exe", return_value=r"C:\x\ollama.exe"):
            a = ollama_ctl.summary()
            b = ollama_ctl.summary()
        self.assertEqual(reg.call_count, 1)
        self.assertEqual((a["installed"], a["version"], b["version"]), (True, "1.2.3", "1.2.3"))

    def test_summary_shows_running_job(self):
        ollama_ctl._job = {"action": "install", "state": "running", "step": "mengunduh"}
        with mock.patch.object(ollama_ctl, "_registry", return_value=None):
            self.assertEqual(ollama_ctl.summary()["job"]["step"], "mengunduh")

    def test_summary_carries_last_progress_line_of_the_log(self):
        ollama_ctl._job = {"action": "install", "state": "running", "step": "mengunduh",
                           "log": "Found Ollama\r\n  ##-- 3 MB / 1.69 GB\r  ####- 12 MB / 1.69 GB\r\n"}
        with mock.patch.object(ollama_ctl, "_registry", return_value=None):
            self.assertEqual(ollama_ctl.summary()["job"]["tail"], "####- 12 MB / 1.69 GB")

    def test_summary_carries_elapsed_seconds_computed_on_this_node(self):
        now = time.time()
        ollama_ctl._job = {"action": "install", "state": "running", "step": "x", "started": now - 125, "ended": None}
        with mock.patch.object(ollama_ctl, "_registry", return_value=None):
            self.assertIn(ollama_ctl.summary()["job"]["elapsed"], (125, 126))
        ollama_ctl._job = {"action": "install", "state": "done", "step": "selesai", "started": now - 100, "ended": now - 40}
        with mock.patch.object(ollama_ctl, "_registry", return_value=None):
            self.assertEqual(ollama_ctl.summary()["job"]["elapsed"], 60)  # job selesai: durasi, bukan terus naik

    def test_last_line_edge_cases(self):
        self.assertEqual(ollama_ctl._last_line(""), "")
        self.assertEqual(ollama_ctl._last_line(None), "")
        self.assertEqual(ollama_ctl._last_line("\r\n\r\n"), "")
        self.assertEqual(len(ollama_ctl._last_line("x" * 500)), 90)


class UnmanagedAndEdgeCasesTest(_WinBase):
    """Temuan review: Ollama di luar pemasang resmi, winget tak bisa dijalankan, nama proses."""
    DEFAULT = os.path.join(tempfile.gettempdir(), "Programs", "Ollama")
    SCOOP = os.path.join(tempfile.gettempdir(), "scoop", "shims", "ollama.exe")

    def status_with(self, exe, reg=None):
        with mock.patch.object(ollama_ctl, "_registry", return_value=reg), \
             mock.patch.object(ollama_ctl, "_exe", return_value=exe), \
             mock.patch.object(ollama_ctl, "_default_dir", return_value=self.DEFAULT), \
             mock.patch.object(ollama_ctl, "_server_version", return_value="0.5.1"):
            return ollama_ctl.status()

    def test_scoop_or_zip_ollama_counts_as_installed_but_unmanaged(self):
        st = self.status_with(self.SCOOP)
        self.assertEqual((st["installed"], st["unmanaged"], st["leftover"]), (True, True, False))

    def test_leftover_files_in_the_default_folder_are_not_installed(self):
        st = self.status_with(os.path.join(self.DEFAULT, "ollama.exe"))
        self.assertEqual((st["installed"], st["unmanaged"], st["leftover"]), (False, False, True))

    def test_official_install_is_installed_and_managed(self):
        reg = {"version": "0.40.15", "path": self.DEFAULT, "uninstall": os.path.join(self.DEFAULT, "unins000.exe")}
        st = self.status_with(os.path.join(self.DEFAULT, "ollama.exe"), reg)
        self.assertEqual((st["installed"], st["unmanaged"], st["leftover"], st["version"]), (True, False, False, "0.40.15"))

    def test_gossip_summary_uses_the_same_rule(self):
        for exe, want in ((self.SCOOP, True), (os.path.join(self.DEFAULT, "ollama.exe"), False), (None, False)):
            ollama_ctl._invalidate()
            with mock.patch.object(ollama_ctl, "_registry", return_value=None), \
                 mock.patch.object(ollama_ctl, "_exe", return_value=exe), \
                 mock.patch.object(ollama_ctl, "_default_dir", return_value=self.DEFAULT):
                self.assertEqual(ollama_ctl.summary()["installed"], want, exe)

    def test_install_never_touches_an_ollama_that_lives_elsewhere(self):
        st = {"installed": True, "unmanaged": True, "path": self.SCOOP, "version": None}
        with mock.patch.object(ollama_ctl, "status", return_value=st), \
             mock.patch.object(ollama_ctl, "_wait_file_process") as run, \
             mock.patch.object(ollama_ctl, "_kill") as kill:
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok)
        self.assertIn("di luar pemasang resmi", msg)
        run.assert_not_called()
        kill.assert_not_called()          # prosesnya TIDAK dibunuh

    def test_uninstall_explains_when_ollama_is_not_ours_to_remove(self):
        with mock.patch.object(ollama_ctl, "_registry", return_value=None), \
             mock.patch.object(ollama_ctl.os.path, "isfile", return_value=False), \
             mock.patch.object(ollama_ctl, "status", return_value={"unmanaged": True, "path": self.SCOOP}), \
             mock.patch.object(ollama_ctl.subprocess, "Popen") as popen:
            ok, msg = ollama_ctl._uninstall()
        self.assertFalse(ok)
        self.assertIn("tidak dipasang lewat pemasang resmi", msg)
        popen.assert_not_called()

    def test_generic_llama_server_is_never_killed_by_name(self):
        self.assertNotIn("llama-server.exe", ollama_ctl.PROCS)
        for name in ollama_ctl.PROCS:
            self.assertIn("ollama", name)

    def test_unlaunchable_winget_falls_back_to_the_official_installer(self):
        log = os.path.join(tempfile.mkdtemp(), "x.log")
        with mock.patch.object(ollama_ctl.subprocess, "Popen", side_effect=OSError(1920, "tidak bisa diakses")):
            self.assertEqual(ollama_ctl._wait_file_process(["winget.exe"], log, 5), 0xFFFFFFFF)
        with open(log, encoding="utf-8") as f:
            self.assertIn("gagal menjalankan winget.exe", f.read())
        calls = []

        def run(args, *_):
            calls.append(args)
            return 0xFFFFFFFF if len(calls) == 1 else 0

        with mock.patch.object(ollama_ctl, "status", side_effect=[{"installed": False}, {"installed": True, "version": "1"},
                                                                  {"installed": True, "version": "1"}]), \
             mock.patch.object(ollama_ctl, "_winget", return_value="winget.exe"), \
             mock.patch.object(ollama_ctl, "_wait_file_process", side_effect=run), \
             mock.patch.object(ollama_ctl, "_disable_tray"), mock.patch.object(ollama_ctl, "_kill"):
            ok, msg = ollama_ctl._install()
        self.assertTrue(ok, msg)
        self.assertIn("pemasang resmi", msg)
        self.assertEqual(len(calls), 2)


class MaintenanceGuardTest(_Base):
    def test_start_ollama_refuses_during_maintenance(self):
        system.OLLAMA_MAINTENANCE.set()
        with mock.patch.object(system.subprocess, "Popen") as popen:
            self.assertFalse(system.start_ollama())
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
