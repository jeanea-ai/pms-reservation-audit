from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from scripts.pms_audit_run import _failure, _new_run_dir


class PmsAuditRunTests(unittest.TestCase):
    def test_run_directory_never_reuses_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 9, 10, 8, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
            first = _new_run_dir(Path(tmp), now)
            second = _new_run_dir(Path(tmp), now)
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_failure_has_one_actionable_question(self):
        payload = _failure(
            "capture stopped",
            source_failures={"guest-ledger": "report response was not an original PDF"},
        )
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["source_failures"]["guest-ledger"],
            "report response was not an original PDF",
        )
        self.assertEqual(payload["next_question"].count("?"), 1)
        self.assertNotIn("traceback", payload["next_question"].casefold())


if __name__ == "__main__":
    unittest.main()
