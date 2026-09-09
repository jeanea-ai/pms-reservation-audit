from pathlib import Path
import unittest

from scripts.audit_pipeline import build_report_spec
from scripts.source_to_input import (
    build_audit_input,
    parse_guest_ledger_text,
    parse_future_reservations_text,
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
        self.assertEqual([row["guest_name"] for row in result["cancelled"]], [
            "SAMPLE, RECENT", "SAMPLE, OLD",
        ])
        self.assertEqual(sum(row["balance"] for row in result["cancelled"]), 60.0)

    def test_guest_ledger_mismatch_fails_closed(self):
        broken = self.ledger.replace("Subtotal Cancelled Accounts: 60.00", "Subtotal Cancelled Accounts: 61.00")
        with self.assertRaisesRegex(ValueError, "subtotal mismatch"):
            parse_guest_ledger_text(broken)

    def test_real_future_reservation_layout_and_wrapped_name(self):
        result = parse_future_reservations_text(self.activity)
        self.assertEqual(len(result["reservations"]), 3)
        self.assertEqual(result["reservations"][0]["check_in"], "2026-09-08")
        self.assertEqual(result["reservations"][0]["guest_name"], "SAMPLE, ALPHA")
        self.assertEqual(result["reservations"][2]["guest_name"], "ANDRADE / PATRON,ELIZABETH")
        self.assertTrue(all(row["status"] == "Reserved" for row in result["reservations"]))
        self.assertTrue(all(row["rooms_booked"] == 1 for row in result["reservations"]))

    def test_reservation_count_mismatch_fails_closed(self):
        broken = self.activity.replace("Total Reservations: 3", "Total Reservations: 4")
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            parse_future_reservations_text(broken)

    def test_reservation_count_accepts_thousands_separator(self):
        row = next(
            line for line in self.activity.splitlines()
            if line.lstrip().startswith("1066000001 ")
        )
        header = "\n".join(self.activity.splitlines()[:3])
        high_volume = (
            f"{header}\n"
            + "\n".join([row] * 2765)
            + "\nTotal Reservations: 2,765\nTotal Room Nights: 5,824\n"
        )
        result = parse_future_reservations_text(high_volume)
        self.assertEqual(len(result["reservations"]), 2765)
        self.assertEqual(result["printed_total"], 2765)

    def test_end_to_end_payload_is_accepted_by_pipeline(self):
        payload = build_audit_input(
            guest_ledger_text=self.ledger,
            future_reservation_texts=[self.activity],
            property_local_date="2026-09-08",
            reviewed_at="2026-09-08 17:00 America/Los_Angeles",
        )
        self.assertEqual(payload["metadata"]["property"], "TST01")
        self.assertEqual(len(payload["reservations"]), 3)
        self.assertEqual([row["guest_name"] for row in payload["guest_ledger"]["balances"]], [
            "SAMPLE, ALPHA", "SAMPLE, BETA", "SAMPLE, RECENT",
        ])
        spec, analysis = build_report_spec(payload)
        self.assertEqual(spec["audit_features"], ["guest_ledger", "duplicates"])
        self.assertEqual(analysis["counts"]["cancelled_excluded"], 0)

    def test_future_report_outside_required_year_fails_closed(self):
        outside = self.activity.replace("9/9/26     9/10/26", "9/9/28     9/10/28")
        with self.assertRaisesRegex(ValueError, "outside the required"):
            build_audit_input(
                guest_ledger_text=self.ledger,
                future_reservation_texts=[outside],
                property_local_date="2026-09-08",
                reviewed_at="2026-09-08 17:00 America/Los_Angeles",
            )


if __name__ == "__main__":
    unittest.main()
