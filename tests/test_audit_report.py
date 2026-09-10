from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.audit_report import _cell_text, _money, render_html_fallback, render_pdf_atomic, verify_pdf_structure
from scripts.report_spec import SpecValidationError, validate_report_spec


def valid_spec(row_count=1):
    rows = [[f"Guest {index}", "F-1", 10.0] for index in range(row_count)]
    return {
        "title": "ChoiceADVANTAGE Guest Ledger & Duplicate Reservation Audit",
        "property": "CAF15 — Test Hotel",
        "reviewed_at": "2026-09-08 12:00 America/Los_Angeles",
        "business_date": "2026-09-08",
        "date_ranges": {"ledger_past_30": "2026-08-09 through 2026-09-08 inclusive", "future_12_months": "2026-09-08 through 2027-09-08 inclusive"},
        "max_pages": 3,
        "disclaimer": "Read-only audit; no records were modified.",
        "complete": True,
        "completion_warning": None,
        "audit_features": ["guest_ledger"],
        "sections": [{"heading": "1. Guest Ledger Balance Review", "body": ["All applicable pages were reviewed."], "tables": [
            {"table_title": "Past 30 Days — No Show / Cancelled Balances", "headers": ["Guest", "Account", "Balance"], "rows": rows, "summary": "Total"},
        ], "notes": []}],
        "limitations": [],
    }


class AuditReportTests(unittest.TestCase):
    def test_currency_and_identifiers(self):
        self.assertEqual(_money(150.5), "$150.50")
        self.assertEqual(_money(-150.5), "($150.50)")
        self.assertEqual(_cell_text("001234"), "001234")

    def test_strict_validation_aggregates_malformed_spec_errors(self):
        spec = valid_spec()
        spec["complete"] = False
        spec["completion_warning"] = None
        spec["sections"][0]["tables"][0]["rows"] = [["Guest", None]]
        with self.assertRaises(SpecValidationError) as raised:
            validate_report_spec(spec)
        message = str(raised.exception)
        self.assertIn("completion_warning is required", message)
        self.assertIn("exactly 3 cells", message)
        self.assertIn("limitations must describe", message)

    def test_cli_rejects_malformed_spec_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "bad.json"
            output = Path(tmp) / "bad.pdf"
            spec_path.write_text("{}", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(Path("scripts/audit_report.py")), str(spec_path), "-o", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("invalid report spec", completed.stderr)
            self.assertFalse(output.exists())

    def test_atomic_failure_preserves_existing_output_and_retries_once(self):
        calls = []
        def fail_reportlab(spec, path):
            calls.append(("reportlab", path)); Path(path).write_bytes(b"not-a-pdf")
        def fail_chromium(spec, path, executable):
            calls.append(("chromium", path)); raise RuntimeError("renderer failed")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.pdf"
            output.write_bytes(b"%PDF-old")
            with self.assertRaises(RuntimeError):
                render_pdf_atomic(valid_spec(), output, reportlab_renderer=fail_reportlab,
                                  chromium_renderer=fail_chromium, chromium_path="chromium")
            self.assertEqual(output.read_bytes(), b"%PDF-old")
            self.assertEqual([item[0] for item in calls], ["reportlab", "chromium"])

    def test_chromium_nonzero_exit_is_failure_even_if_output_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.pdf"
            output.write_bytes(b"%PDF-stale")
            with patch("scripts.audit_report.subprocess.run", return_value=SimpleNamespace(returncode=9, stderr="boom", stdout="")):
                with self.assertRaisesRegex(RuntimeError, "exited 9"):
                    render_html_fallback(valid_spec(), output, chromium_path="chromium")

    def test_three_page_limit_is_enforced(self):
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("pypdf unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "multi.pdf"
            with self.assertRaisesRegex(RuntimeError, "maximum is 3"):
                render_pdf_atomic(valid_spec(180), output, chromium_path=None)
            self.assertFalse(output.exists())

    def test_structural_verifier_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "signed-only.pdf"
            output.write_bytes(b"%PDF-not-verified")
            with patch.dict(sys.modules, {"pypdf": None}):
                with self.assertRaisesRegex(RuntimeError, "pypdf is required"):
                    verify_pdf_structure(output, valid_spec())


if __name__ == "__main__":
    unittest.main()
