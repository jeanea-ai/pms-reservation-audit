import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_pipeline import _duplicate_rows, build_report_spec


FIXTURE = Path(__file__).parent / "fixtures" / "audit_input.json"


class AuditPipelineTests(unittest.TestCase):
    def test_one_command_input_builds_both_valid_sections(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        spec, analysis = build_report_spec(payload)
        self.assertEqual(spec["audit_features"], ["guest_ledger", "duplicates"])
        self.assertEqual([section["heading"] for section in spec["sections"]], [
            "1. Guest Ledger Balance Review", "2. Duplicate Reservation Review",
        ])
        self.assertEqual(analysis["windows"]["future_12_months"]["groups_found"], 1)
        self.assertEqual(spec["max_pages"], 3)

    def test_pipeline_is_post_extraction_only(self):
        source = Path("scripts/audit_pipeline.py").read_text(encoding="utf-8")
        for forbidden in ("choiceadvantage.com", "j_username", "j_password", "Input.dispatchMouseEvent"):
            self.assertNotIn(forbidden, source)

    def test_missing_extracted_data_fails_before_rendering(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload.pop("guest_ledger")
        payload.pop("reservations")
        with self.assertRaisesRegex(ValueError, "must contain"):
            build_report_spec(payload)

    def test_schema_version_is_required(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload.pop("schema_version")
        with self.assertRaisesRegex(ValueError, "schema_version must be 1"):
            build_report_spec(payload)

    def test_duplicate_pdf_rows_use_consolidated_stays_and_one_match_note(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["reservations"][1].update({
            "guest_name": "john-smith 2",
            "check_in": "2026-10-10",
            "check_out": "2026-10-12",
        })
        _, analysis = build_report_spec(payload)
        group = analysis["windows"]["future_12_months"]["groups"][0]
        rows = _duplicate_rows([group])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], 3)
        self.assertEqual(rows[0][1], "A101, A102")
        self.assertNotIn("Confirmation", " ".join(map(str, rows[0])))


if __name__ == "__main__":
    unittest.main()
