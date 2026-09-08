import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.readiness import inspect


class ReadinessTests(unittest.TestCase):
    def test_missing_runtime_dependencies_fail_with_actionable_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            with patch("scripts.readiness.importlib.util.find_spec", return_value=None), \
                 patch("scripts.readiness.shutil.which", return_value=None):
                results = inspect(skill, {})
            failures = {name: detail for status, name, detail in results if status == "FAIL"}
            self.assertIn("PDF renderer", failures)
            self.assertIn("report-pull skill", failures)
            self.assertIn("credentials file", failures)
            self.assertIn("property-local time rules", failures)
            self.assertIn("kolo log-action", failures)

    def test_live_access_is_explicitly_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "workspace" / "skills" / "audit"
            skill.mkdir(parents=True)
            with patch("scripts.readiness.importlib.util.find_spec", return_value=None), \
                 patch("scripts.readiness.shutil.which", return_value=None):
                results = inspect(skill, {})
            self.assertIn(("SKIP", "live ChoiceADVANTAGE access",
                           "confirm property, report visibility, and MFA state during the requested audit"), results)


if __name__ == "__main__":
    unittest.main()
