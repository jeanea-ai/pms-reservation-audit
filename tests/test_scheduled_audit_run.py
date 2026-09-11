from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.scheduled_audit_run import (
    DELIVERY_MODELS,
    DeliveryError,
    _delivery_prompt,
    _queue_delivery_agent,
    _validate_completed_report,
    run_scheduled,
)


HELP = " ".join(
    [
        "--at",
        "--delete-after-run",
        "--session",
        "--message",
        "--model",
        "--thinking",
        "--light-context",
        "--announce",
        "--channel",
        "--to",
        "--tools",
        "--best-effort-deliver",
        "--json",
    ]
)


def completed(command=None, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command or [], returncode, stdout, stderr)


class ScheduledAuditRunTests(unittest.TestCase):
    def _report(self, root: Path):
        run_dir = root / "run-20260911T090000-0700"
        run_dir.mkdir()
        report = run_dir / "PMS Reconciliation CAF15.pdf"
        report.write_bytes(b"%PDF-1.7\n%%EOF\n")
        result = {
            "status": "ok",
            "complete": True,
            "property_code": "CAF15",
            "run_dir": str(run_dir),
            "report": str(report),
            "summary": {"duplicate_groups": 2},
        }
        return run_dir, report, result

    def test_report_validation_refuses_symlink_and_outside_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            root.mkdir()
            run_dir, report, result = self._report(root)
            self.assertEqual(
                (run_dir.resolve(), report.resolve()),
                _validate_completed_report(result, root),
            )
            outside = Path(tmp) / "outside.pdf"
            outside.write_bytes(b"%PDF-1.7\n")
            result["report"] = str(outside)
            with self.assertRaisesRegex(DeliveryError, "directly inside"):
                _validate_completed_report(result, root)

    def test_delivery_prompt_forbids_pdf_inspection_and_row_data(self):
        prompt = _delivery_prompt(Path("/safe/receipt.json"), Path("/safe/report.pdf"), "CAF15")
        self.assertIn("Do not open", prompt)
        self.assertIn("Attach the exact local PDF", prompt)
        self.assertIn("aggregate", prompt)
        self.assertNotIn("password value", prompt)

    @patch("scripts.scheduled_audit_run._run")
    def test_delivery_model_creation_falls_back_in_requested_order(self, run):
        run.side_effect = [
            completed(stdout=HELP),
            completed(stdout='{"jobs": []}'),
            completed(returncode=1, stderr="model unavailable"),
            completed(stdout='{"jobs": []}'),
            completed(stdout='{"id": "delivery-2"}'),
        ]
        result = _queue_delivery_agent(
            openclaw="openclaw",
            destination="kolo:chat-1",
            hotel="CAF15",
            run_dir=Path("/runs/run-1"),
            receipt=Path("/runs/run-1/delivery-receipt.json"),
            report=Path("/runs/run-1/report.pdf"),
        )
        create_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0][:3] == ["openclaw", "cron", "create"]
            and "--help" not in call.args[0]
        ]
        self.assertEqual(result["model"], DELIVERY_MODELS[1])
        self.assertEqual(
            [call[call.index("--model") + 1] for call in create_calls],
            list(DELIVERY_MODELS[:2]),
        )
        self.assertIn("--light-context", create_calls[-1])
        self.assertEqual(create_calls[-1][create_calls[-1].index("--session") + 1], "isolated")

    def test_real_wrapper_path_queues_delivery_and_prints_only_no_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, report, audit_result = self._report(root)
            commands = []

            def fake_run(command, timeout):
                commands.append(command)
                if command[0] == "kolo":
                    return completed(command, stdout='{"status":"ok"}')
                if command[:4] == ["openclaw", "cron", "create", "--help"]:
                    return completed(command, stdout=HELP)
                if command[:4] == ["openclaw", "cron", "list", "--json"]:
                    return completed(command, stdout='{"jobs": []}')
                if command[:3] == ["openclaw", "cron", "create"]:
                    return completed(command, stdout='{"id":"delivery-1"}')
                return completed(command, stdout="renderer note\n" + json.dumps(audit_result) + "\n")

            args = argparse.Namespace(
                hotel="CAF15",
                output_root=root,
                delivery_to="kolo:chat-1",
                audit_timeout_seconds=90,
                overall_timeout_seconds=480,
            )
            output = io.StringIO()
            with patch("scripts.scheduled_audit_run._run", side_effect=fake_run), patch(
                "scripts.scheduled_audit_run.shutil.which", side_effect=lambda name: name
            ), redirect_stdout(output):
                self.assertEqual(run_scheduled(args), 0)
            self.assertEqual(output.getvalue().strip(), "NO_REPLY")
            state = json.loads((run_dir / "delivery-state.json").read_text())
            self.assertEqual(state["status"], "queued")
            self.assertEqual(state["model"], DELIVERY_MODELS[0])
            flattened = " ".join(part for command in commands for part in command)
            self.assertNotIn("pms_password", flattened)
            self.assertTrue(report.exists())


if __name__ == "__main__":
    unittest.main()
