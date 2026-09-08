import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_pipeline import build_report_spec


FIXTURE = Path(__file__).parent / "fixtures" / "audit_input.json"


class AuditPipelineTests(unittest.TestCase):
    def test_one_command_input_builds_both_valid_sections(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        spec, analysis = build_report_spec(payload)
        self.assertEqual(spec["audit_features"], ["guest_ledger", "duplicates"])
        self.assertEqual([section["heading"] for section in spec["sections"]], [
            "1. Guest Ledger Balance Review", "2. Duplicate Reservation Review",
        ])
        self.assertEqual(analysis["windows"]["previous_90"]["groups_found"], 1)

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


if __name__ == "__main__":
    unittest.main()
