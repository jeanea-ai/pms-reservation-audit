import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.readiness import _check_report_pull, _check_secrets, _check_timezone_guidance, inspect


class ReadinessTests(unittest.TestCase):
    def test_secrets_structure_is_checked_without_values_in_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".secrets.json"
            path.write_text(json.dumps({"choiceadvantage": {"username": "alice", "password": "super-secret"}}), encoding="utf-8")
            ok, detail = _check_secrets(path)
            self.assertTrue(ok)
            self.assertNotIn("alice", detail)
            self.assertNotIn("super-secret", detail)
            path.write_text("{}", encoding="utf-8")
            self.assertFalse(_check_secrets(path)[0])

    def test_timezone_guidance_requires_content_not_just_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AGENTS.md"
            path.write_text("Use UTC.", encoding="utf-8")
            self.assertFalse(_check_timezone_guidance(path)[0])
            path.write_text("Resolve dates in the hotel property's local time zone.", encoding="utf-8")
            self.assertTrue(_check_timezone_guidance(path)[0])

    def test_report_pull_contract_and_version_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text("---\nname: choiceadvantage-report-pull\nversion: 0.1.0\n---\nUse j_username and j_password; choose Continue, never Migrate.\n", encoding="utf-8")
            self.assertTrue(_check_report_pull(path)[0])
            path.write_text("---\nname: choiceadvantage-report-pull\nversion: 0.0.1\n---\n", encoding="utf-8")
            self.assertFalse(_check_report_pull(path)[0])

    def test_live_access_is_explicitly_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            with patch("scripts.readiness.importlib.util.find_spec", return_value=None), patch("scripts.readiness.find_chromium", return_value=None), patch("scripts.readiness.shutil.which", return_value=None):
                results = inspect(skill, {})
            self.assertIn(("SKIP", "live ChoiceADVANTAGE access", "confirm property, report visibility, and MFA state during the requested audit"), results)


if __name__ == "__main__":
    unittest.main()
