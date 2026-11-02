"""Contract tests for security-sensitive installer enrollment paths."""

from pathlib import Path
import unittest


class InstallerSecurityTest(unittest.TestCase):
    def test_installer_never_uses_public_fingerprint_rejoin_endpoint(self):
        script = (Path(__file__).resolve().parent.parent / "install.ps1").read_text(encoding="utf-8")
        self.assertNotIn("/api/node/enroll", script)
        self.assertIn("kode undangan sekali-pakai", script.lower())


if __name__ == "__main__":
    unittest.main()
