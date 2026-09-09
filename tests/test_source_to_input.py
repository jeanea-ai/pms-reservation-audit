from pathlib import Path
import unittest

from scripts.audit_pipeline import build_report_spec
from scripts.source_to_input import (
    build_audit_input,
    parse_guest_ledger_text,
    parse_reservation_activity_text,
)


FIXTURES = Path(__file__).parent / "fixtures"


class SourceToInputTests(unittest.TestCase):
    def setUp(self):
        self.ledger = (FIXTURES / "guest_ledger.txt").read_text(encoding="utf-8")
        self.activity = (FIXTURES / "reservation_activity.txt").read_text(encoding="utf-8")

    def test_guest_ledger_rows_and_printed_totals_reconcile(self):
        result = parse_guest_ledger_text(self.ledger)
        self.assertEqual(len(result["no_shows"]), 2)
        self.assertEqual(sum(row["balance"] for row in result["no_shows"]), 10.8)
        self.assertEqual([row["guest_name"] for row in result["groups"]], [
            "Example Education Foundation", "STURDY CO", "Community Partnership",
        ])
        self.assertEqual(sum(row["balance"] for row in result["groups"]), 600.0)

    def test_guest_ledger_mismatch_fails_closed(self):
        broken = self.ledger.replace("Subtotal Group: 600.00", "Subtotal Group: 601.00")
        with self.assertRaisesRegex(ValueError, "subtotal mismatch"):
            parse_guest_ledger_text(broken)

    def test_reservation_activity_rows_and_cancelled_status(self):
        result = parse_reservation_activity_text(self.activity)
        self.assertEqual(len(result["reservations"]), 3)
        self.assertEqual(result["reservations"][0]["check_in"], "2026-09-08")
        self.assertEqual(result["reservations"][2]["status"], "Cancelled")
        self.assertTrue(all(row["rooms_booked"] == 1 for row in result["reservations"]))

    def test_reservation_count_mismatch_fails_closed(self):
        broken = self.activity.replace("Total Reservations: 3", "Total Reservations: 4")
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            parse_reservation_activity_text(broken)

    def test_end_to_end_payload_is_accepted_by_pipeline(self):
        payload = build_audit_input(
            guest_ledger_text=self.ledger,
            reservation_activity_texts=[self.activity],
            property_local_date="2026-09-08",
            reviewed_at="2026-09-08 17:00 America/Los_Angeles",
        )
        self.assertEqual(payload["metadata"]["property"], "TST01 - Test Hotel")
        self.assertEqual(len(payload["reservations"]), 3)
        spec, analysis = build_report_spec(payload)
        self.assertEqual(spec["audit_features"], ["guest_ledger", "duplicates"])
        self.assertEqual(analysis["counts"]["cancelled_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
