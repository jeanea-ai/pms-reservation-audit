from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.schedule_audit import create_schedule, parser


HELP = " ".join(
    [
        "--command-argv",
        "--command-cwd",
        "--timeout-seconds",
        "--no-output-timeout-seconds",
        "--cron",
        "--every",
        "--announce",
        "--no-deliver",
        "--disabled",
        "--exact",
    ]
)


def completed(command=None, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command or [], returncode, stdout, stderr)


class ScheduleAuditTests(unittest.TestCase):
    def _cron_args(self, output_root, *extra):
        return parser().parse_args(
            [
                "--hotel",
                "CAF15",
                "--cron",
                "15 9 * * *",
                "--timezone",
                "America/Los_Angeles",
                "--output-root",
                output_root,
                *extra,
            ]
        )

    def test_dry_run_uses_exact_command_argv_and_production_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = create_schedule(self._cron_args(tmp, "--dry-run"))
        command = result["command_argv"]
        self.assertEqual(result["status"], "dry_run")
        self.assertIn("--command-argv", command)
        self.assertIn("--command-cwd", command)
        self.assertIn("--no-deliver", command)
        self.assertNotIn("--message", command)
        runner = json.loads(command[command.index("--command-argv") + 1])
        self.assertTrue(runner[1].endswith("scripts/pms_audit_run.py"))
        self.assertIn("CAF15", runner)
        self.assertEqual(
            runner[runner.index("--overall-timeout-seconds") + 1], "480"
        )
        for forbidden in (
            "--test-access",
            "--test-access-file",
            "--session-only",
            "--browser-saved-login",
            "--allow-skip-mfa",
        ):
            self.assertNotIn(forbidden, runner)

    def test_interval_schedule_has_no_timezone_or_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = parser().parse_args(
                [
                    "--hotel",
                    "CAF15",
                    "--every",
                    "6h",
                    "--output-root",
                    tmp,
                    "--dry-run",
                ]
            )
            result = create_schedule(args)
        command = result["command_argv"]
        self.assertIn("--every", command)
        self.assertNotIn("--tz", command)
        self.assertNotIn("--command", command)

    def test_clock_schedule_requires_valid_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self._cron_args(tmp)
            args.timezone = None
            with self.assertRaisesRegex(ValueError, "timezone"):
                create_schedule(args)
            args.timezone = "Not/AZone"
            with self.assertRaisesRegex(ValueError, "unknown IANA"):
                create_schedule(args)

    def test_invalid_frequency_and_skill_local_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = parser().parse_args(
                [
                    "--hotel",
                    "CAF15",
                    "--every",
                    "often",
                    "--output-root",
                    tmp,
                ]
            )
            with self.assertRaisesRegex(ValueError, "positive interval"):
                create_schedule(args)
        args = self._cron_args(".")
        with self.assertRaisesRegex(ValueError, "outside the installed skill"):
            create_schedule(args)

    def test_existing_name_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.schedule_audit._validate_production_access"
        ), patch(
            "scripts.schedule_audit.shutil.which", return_value="/usr/bin/openclaw"
        ), patch("scripts.schedule_audit._run") as run:
            run.side_effect = [
                completed(stdout=HELP),
                completed(
                    stdout=json.dumps(
                        {"jobs": [{"id": "job-1", "name": "PMS Reconciliation CAF15"}]}
                    )
                ),
            ]
            result = create_schedule(self._cron_args(tmp))
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result["next_question"].count("?"), 1)

    def test_create_binds_explicit_kolo_destination(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.schedule_audit._validate_production_access"
        ), patch(
            "scripts.schedule_audit.shutil.which", return_value="/usr/bin/openclaw"
        ), patch("scripts.schedule_audit._run") as run:
            run.side_effect = [
                completed(stdout=HELP),
                completed(stdout='{"jobs": []}'),
                completed(stdout='{"id": "job-2"}'),
            ]
            result = create_schedule(
                self._cron_args(tmp, "--announce-to", "kolo:chat-123", "--exact")
            )
            create_command = run.call_args_list[2].args[0]
        self.assertEqual(result["status"], "created")
        self.assertIn("--announce", create_command)
        self.assertIn("--exact", create_command)
        self.assertEqual(
            create_command[create_command.index("--to") + 1], "kolo:chat-123"
        )

    def test_ambiguous_announcement_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "kolo:<chat-id>"):
                create_schedule(self._cron_args(tmp, "--announce-to", "last"))

    def test_job_timeout_must_leave_shutdown_margin(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self._cron_args(
                tmp,
                "--overall-timeout-seconds",
                "480",
                "--job-timeout-seconds",
                "500",
            )
            with self.assertRaisesRegex(ValueError, "at least 60"):
                create_schedule(args)


if __name__ == "__main__":
    unittest.main()
