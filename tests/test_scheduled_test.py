import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.schedule_test import create_schedule, parser
from scripts.scheduled_test_run import run_test_report


FIXTURE = Path(__file__).parent.parent / "assets" / "scheduled_test_input.json"


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class ScheduledTestTests(unittest.TestCase):
    def _args(self, output_dir, *extra):
        return parser().parse_args([
            "--cron", "15 9 * * *",
            "--timezone", "America/Los_Angeles",
            "--output-dir", str(output_dir),
            *extra,
        ])

    def test_runner_marks_every_artifact_as_test_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_test_report(FIXTURE.resolve(), Path(tmp), "America/Los_Angeles")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["mode"], "test_only")
            self.assertIn("TEST-ONLY", Path(result["pdf"]).name)
            spec = json.loads(Path(result["spec"]).read_text(encoding="utf-8"))
            self.assertTrue(spec["title"].startswith("TEST ONLY"))
            self.assertTrue(spec["disclaimer"].startswith("TEST ONLY"))

    def test_dry_run_is_command_argv_and_does_not_deliver(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = create_schedule(self._args(tmp, "--dry-run"))
        command = result["command_argv"]
        self.assertEqual(result["status"], "dry_run")
        self.assertIn("--command-argv", command)
        self.assertIn("--command-cwd", command)
        self.assertIn("--no-deliver", command)
        self.assertNotIn("--message", command)
        runner = json.loads(command[command.index("--command-argv") + 1])
        self.assertIn("scheduled_test_run.py", runner[1])

    def test_create_checks_help_and_refuses_duplicate_name(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("scripts.schedule_test.shutil.which", return_value="/usr/bin/openclaw"), \
                patch("scripts.schedule_test._run") as run:
            run.side_effect = [
                completed([], stdout="--command-argv --command-cwd --timeout-seconds --no-deliver"),
                completed([], stdout=json.dumps({
                    "jobs": [{"id": "job-1", "name": "PMS Reservation Audit - TEST ONLY"}],
                })),
            ]
            result = create_schedule(self._args(tmp))
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(run.call_count, 2)

    def test_create_uses_explicit_announcement_target(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("scripts.schedule_test.shutil.which", return_value="/usr/bin/openclaw"), \
                patch("scripts.schedule_test._run") as run:
            run.side_effect = [
                completed([], stdout="--command-argv --command-cwd --timeout-seconds --announce"),
                completed([], stdout='{"jobs": []}'),
                completed([], stdout='{"id": "job-2"}'),
            ]
            result = create_schedule(self._args(tmp, "--announce-to", "kolo:chat-123"))
            create_command = run.call_args_list[2].args[0]
        self.assertEqual(result["status"], "created")
        self.assertIn("--announce", create_command)
        self.assertEqual(create_command[create_command.index("--to") + 1], "kolo:chat-123")
        self.assertNotIn("--no-deliver", create_command)

    def test_invalid_announcement_target_is_rejected_before_openclaw(self):
        with tempfile.TemporaryDirectory() as tmp, \
                self.assertRaisesRegex(ValueError, "kolo:<chat-id>"):
            create_schedule(self._args(tmp, "--announce-to", "last"))

    def test_runner_cli_prints_exactly_one_json_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable, "scripts/scheduled_test_run.py",
                    "--input", str(FIXTURE),
                    "--output-dir", tmp,
                    "--timezone", "America/Los_Angeles",
                ],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["mode"], "test_only")


if __name__ == "__main__":
    unittest.main()
