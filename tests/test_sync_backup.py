"""Salinan memori/otak/akses dari pemimpin tidak boleh menghapus catatan tanpa jejak.

Kejadian nyata 29 Okt 2026: komputer-komputer yang tadinya memimpin sendiri-sendiri
bergabung, dan memori di komputer utama langsung ditimpa memori pemimpin sementara
(kosong) tanpa cadangan apa pun."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from inode import itsme

ISI = "# Memori It's Me\n\n- Suka kopi tanpa gula\n- Rapat tiap Senin pukul 9\n"


class WriteStateBackupTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.cfg = {"itsme_dir": self.home}
        self.data = os.path.join(self.home, "data")
        os.makedirs(self.data)
        self.backups = os.path.join(self.data, "sync-backup")

    def _put(self, name, text):
        with open(os.path.join(self.data, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def _read(self, name, folder=None):
        with open(os.path.join(folder or self.data, name), encoding="utf-8", newline="") as f:
            return f.read()

    def _saved(self, stem="memory"):
        if not os.path.isdir(self.backups):
            return []
        return sorted(p for p in os.listdir(self.backups) if p.startswith(stem + "-"))

    def test_overwriting_real_notes_keeps_a_backup_of_the_old_ones(self):
        self._put("memory.md", ISI)
        baru = "# Memori It's Me\n\n"  # memori pemimpin yang kosong
        self.assertEqual(itsme.write_state(self.cfg, {"memory.md": baru}), ["memory.md"])
        self.assertEqual(self._read("memory.md"), baru)
        saved = self._saved()
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].endswith(".md"))
        self.assertEqual(self._read(saved[0], self.backups), ISI)

    def test_empty_or_header_only_files_need_no_backup(self):
        for lama in ("", "   \n", "# Memori It's Me\n\n", "# Memori It's Me"):
            self._put("memory.md", lama)
            itsme.write_state(self.cfg, {"memory.md": ISI + lama})  # isi baru selalu beda
        self.assertEqual(self._saved(), [])

    def test_a_missing_file_is_simply_created(self):
        self.assertEqual(itsme.write_state(self.cfg, {"memory.md": ISI}), ["memory.md"])
        self.assertEqual(self._read("memory.md"), ISI)
        self.assertEqual(self._saved(), [])

    def test_identical_content_writes_and_backs_up_nothing(self):
        self._put("memory.md", ISI)
        self.assertEqual(itsme.write_state(self.cfg, {"memory.md": ISI}), [])
        self.assertEqual(self._saved(), [])

    def test_only_the_latest_backups_are_kept(self):
        self._put("memory.md", ISI)
        for n in range(itsme.BACKUP_KEEP + 7):
            itsme.write_state(self.cfg, {"memory.md": ISI + f"- catatan ke-{n}\n"})
        saved = self._saved()
        self.assertEqual(len(saved), itsme.BACKUP_KEEP)
        # yang terbaru adalah isi tepat sebelum penimpaan terakhir
        self.assertEqual(self._read(saved[-1], self.backups), ISI + f"- catatan ke-{itsme.BACKUP_KEEP + 5}\n")

    def test_backups_of_different_files_do_not_evict_each_other(self):
        self._put("memory.md", ISI)
        self._put("brain.json", '{"backend": "claude_code"}')
        itsme.write_state(self.cfg, {"memory.md": "# Memori It's Me\n", "brain.json": '{"backend": "ollama"}'})
        self.assertEqual(len(self._saved("memory")), 1)
        self.assertEqual(len(self._saved("brain")), 1)
        self.assertTrue(self._saved("brain")[0].endswith(".json"))

    def test_a_failing_backup_never_blocks_the_sync(self):
        self._put("memory.md", ISI)
        open(self.backups, "w").close()  # sync-backup berupa BERKAS: makedirs gagal
        baru = "# Memori It's Me\n"
        self.assertEqual(itsme.write_state(self.cfg, {"memory.md": baru}), ["memory.md"])
        self.assertEqual(self._read("memory.md"), baru)

    def test_unknown_names_and_oversized_files_are_still_ignored(self):
        big = "x" * (itsme.MAX_FILE + 1)
        self.assertEqual(itsme.write_state(self.cfg, {"rahasia.txt": "x", "memory.md": big, "access.json": 5}), [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "rahasia.txt")))

    def test_backup_runs_before_the_overwrite_not_after(self):
        self._put("memory.md", ISI)
        seen = {}

        def spy(data, name, old):
            seen["disk_at_backup_time"] = self._read("memory.md")
            seen["old"] = old

        with mock.patch.object(itsme, "_backup_before_overwrite", spy):
            itsme.write_state(self.cfg, {"memory.md": "# Memori It's Me\n"})
        self.assertEqual(seen["disk_at_backup_time"], ISI)
        self.assertEqual(seen["old"], ISI)


if __name__ == "__main__":
    unittest.main()
