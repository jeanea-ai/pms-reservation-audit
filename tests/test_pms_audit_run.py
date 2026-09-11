from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from scripts.pms_audit_run import _failure, _new_run_dir, _redacted_summary


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

    def test_redacted_summary_contains_aggregates_but_no_source_rows(self):
        payload = {
            "guest_ledger": {
                "balances": [
                    {
                        "guest_name": "Private Guest",
                        "account_number": "123",
                        "balance": 10.5,
                    },
                    {
                        "guest_name": "Private Guest 2",
                        "account_number": "456",
                        "balance": 4.25,
                    },
                ],
                "groups": [
                    {
                        "guest_name": "Private Group",
                        "account_number": "789",
                        "balance": 100.0,
                    }
                ],
            }
        }
        analysis = {
            "counts": {"input": 8, "unique": 7},
            "windows": {
                "future_12_months": {"groups_found": 1, "rooms_total": 2}
            },
        }
        summary = _redacted_summary(payload, analysis)
        self.assertEqual(summary["ledger_balance_total"], 14.75)
        self.assertEqual(summary["group_balance_total"], 100.0)
        self.assertEqual(summary["duplicate_groups"], 1)
        self.assertNotIn("Private", str(summary))
        self.assertNotIn("123", str(summary))


if __name__ == "__main__":
    unittest.main()
