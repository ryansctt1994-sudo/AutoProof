import unittest
from unittest.mock import patch

from app.lean import check_lean


class LeanAuthorityTests(unittest.TestCase):
    def test_demo_mode_never_claims_verification(self):
        with patch("app.lean.shutil.which", return_value=None):
            result = check_lean("example : 1 = 1 := by rfl")

        self.assertFalse(result["ok"])
        self.assertFalse(result["verified"])
        self.assertTrue(result["advisory"])
        self.assertEqual(result["status"], "DEMO_PLAUSIBLE")
        self.assertEqual(result["authority"], "NONE")
        self.assertEqual(result["evidence_level"], "E0")
        self.assertEqual(result["mode"], "demo")
        self.assertEqual(len(result["source_sha256"]), 64)

    def test_demo_mode_without_known_tactic_is_unresolved(self):
        with patch("app.lean.shutil.which", return_value=None):
            result = check_lean("theorem unresolved : True := by")

        self.assertEqual(result["status"], "DEMO_UNRESOLVED")
        self.assertFalse(result["verified"])
        self.assertEqual(result["authority"], "NONE")


if __name__ == "__main__":
    unittest.main()
