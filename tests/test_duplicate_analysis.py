import json
from datetime import date
from pathlib import Path
import unittest

from scripts.duplicate_analysis import analyze_duplicates, classify_match, deduplicate_reservations, email_kind, inclusive_windows, normalize_name, room_count


FIXTURE = Path(__file__).parent / "fixtures" / "duplicate_reservations.json"


class DuplicateAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_inclusive_boundaries_are_explicit(self):
        windows = inclusive_windows(date(2026, 9, 8))
        self.assertEqual(windows["previous_90"], (date(2026, 6, 10), date(2026, 9, 8)))
        self.assertEqual(windows["next_90"], (date(2026, 9, 8), date(2026, 12, 7)))

    def test_normalization_and_match_strength(self):
        self.assertEqual(normalize_name(" John-Smith  2 "), "john smith")
        self.assertEqual(classify_match({"guest_name": "John Smith"}, {"guest_name": "John Smith"})[0], "exact")
        self.assertEqual(classify_match({"guest_name": "John Smith 1"}, {"guest_name": "john-smith 2"})[0], "normalized")
        self.assertEqual(classify_match({"guest_name": "Jane Doe", "check_in": "2026-09-09", "check_out": "2026-09-10"}, {"guest_name": "Jane Doee", "check_in": "2026-09-09", "check_out": "2026-09-11"})[0], "possible")

    def test_email_and_deduplication_contracts(self):
        self.assertEqual(email_kind("person@gmail.com"), "personal")
        self.assertEqual(email_kind("person@buildco.com"), "company")
        self.assertEqual(len(deduplicate_reservations(self.records)), 5)

    def test_fixture_analysis_is_deterministic(self):
        first = analyze_duplicates(self.records, date(2026, 9, 8))
        second = analyze_duplicates(list(reversed(self.records)), date(2026, 9, 8))
        self.assertEqual(first["counts"], {"input": 6, "unique": 5, "cancelled_excluded": 1})
        previous = first["windows"]["previous_90"]
        upcoming = first["windows"]["next_90"]
        self.assertEqual((previous["groups_found"], previous["rooms_total"]), (1, 3))
        self.assertEqual(previous["groups"][0]["category"], "Duplicates")
        self.assertEqual((upcoming["groups_found"], upcoming["rooms_total"]), (1, 2))
        self.assertEqual(upcoming["groups"][0]["category"], "Repeat Offenders")
        self.assertEqual(first, second)

    def test_exact_group_remains_exact(self):
        records = [
            {"guest_name": "GARCIA, JUAN CARLOS", "confirmation_number": f"G{i}",
             "check_in": "2026-08-01", "check_out": "2026-08-02", "rooms_booked": 1,
             "primary_email": "Not displayed.", "secondary_email": "Not displayed.", "status": "Active"}
            for i in range(3)
        ]
        result = analyze_duplicates(records, date(2026, 9, 8))["windows"]["previous_90"]["groups"][0]
        self.assertEqual(result["match_strength"], "exact")
        self.assertEqual(result["match_breakdown"], {"exact": 2, "normalized": 0, "possible": 0})

    def test_room_number_cannot_be_used_as_room_count(self):
        with self.assertRaisesRegex(ValueError, "missing rooms_booked"):
            room_count({"room_number": 223})

    def test_room_contract_is_checked_even_without_a_duplicate_group(self):
        record = {"guest_name": "Solo Guest", "confirmation_number": "S1",
                  "check_in": "2026-08-01", "check_out": "2026-08-02",
                  "room_number": 223, "status": "Active"}
        with self.assertRaisesRegex(ValueError, "missing rooms_booked"):
            analyze_duplicates([record], date(2026, 9, 8))


if __name__ == "__main__":
    unittest.main()
