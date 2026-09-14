from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.pms_login import LoginError
from scripts.pms_audit_run import (
    AuditTimedOut,
    AuditTerminated,
    _acquire_run_lock,
    _failure,
    _handle_sigterm,
    _handle_overall_timeout,
    _new_run_dir,
    _redacted_summary,
    main,
)


class PmsAuditRunTests(unittest.TestCase):
    def test_overall_alarm_becomes_structured_timeout(self):
        with self.assertRaisesRegex(AuditTimedOut, "overall runtime"):
            _handle_overall_timeout(None, None)

    def test_sigterm_becomes_a_structured_audit_termination(self):
        with self.assertRaisesRegex(AuditTerminated, "SIGTERM"):
            _handle_sigterm(None, None)

    def test_run_directory_never_reuses_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 9, 10, 8, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
            first = _new_run_dir(Path(tmp), now)
            second = _new_run_dir(Path(tmp), now)
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_same_property_and_output_root_cannot_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = _acquire_run_lock(Path(tmp), "caf15")
            try:
                with self.assertRaisesRegex(RuntimeError, "already using"):
                    _acquire_run_lock(Path(tmp), "CAF15")
            finally:
                first.close()
            second = _acquire_run_lock(Path(tmp), "CAF15")
            second.close()

    def test_auth_failure_preserves_redacted_transition_and_failed_status(self):
        access = {
            "property_code": "CA307",
            "timezone": "America/Los_Angeles",
            "auth_mode": "okta_sso",
            "test_only": False,
            "_source_attestation": [],
        }

        def fail_login(_access, **kwargs):
            kwargs["transition_recorder"](
                {
                    "event": "login_failed",
                    "origin": "https://choicehotels.okta.com",
                    "path": "/signout/…",
                }
            )
            raise LoginError("controlled authentication failure")

        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "pms_audit_run.py",
                "--hotel",
                "CA307",
                "--output-root",
                tmp,
            ]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), patch(
                "scripts.pms_audit_run.resolve_access", return_value=access
            ), patch(
                "scripts.pms_audit_run.login", side_effect=fail_login
            ), redirect_stdout(
                output
            ):
                result_code = main()

            self.assertEqual(2, result_code)
            result = json.loads(output.getvalue())
            run_dir = Path(result["run_dir"])
            status = json.loads((run_dir / "run-status.json").read_text())
            transition = (run_dir / "auth-transitions.jsonl").read_text()
            self.assertEqual("failed", status["status"])
            self.assertEqual("authenticating", status["last_stage"])
            self.assertIn('"path": "/signout/…"', transition)

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

    def test_bot_challenge_failure_has_stable_code_and_manual_question(self):
        payload = _failure(
            "access verification",
            error_code="bot_challenge",
            next_question=(
                "Please complete access verification in the persistent browser, then retry?"
            ),
        )
        self.assertEqual(payload["error_code"], "bot_challenge")
        self.assertEqual(payload["next_question"].count("?"), 1)

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
            "windows": {"future_12_months": {"groups_found": 1, "rooms_total": 2}},
        }
        summary = _redacted_summary(payload, analysis)
        self.assertEqual(summary["ledger_balance_total"], 14.75)
        self.assertEqual(summary["group_balance_total"], 100.0)
        self.assertEqual(summary["duplicate_groups"], 1)
        self.assertNotIn("Private", str(summary))
        self.assertNotIn("123", str(summary))


if __name__ == "__main__":
    unittest.main()
