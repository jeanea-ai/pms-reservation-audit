import json
from datetime import date
from pathlib import Path
import unittest

from scripts.duplicate_analysis import analyze_duplicates, classify_match, consolidate_guest_stays, deduplicate_reservations, email_kind, inclusive_windows, normalize_name, room_count


FIXTURE = Path(__file__).parent / "fixtures" / "duplicate_reservations.json"


class DuplicateAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.records = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_inclusive_boundaries_are_explicit(self):
        windows = inclusive_windows(date(2026, 9, 8))
        self.assertEqual(windows["ledger_past_30"], (date(2026, 8, 9), date(2026, 9, 8)))
        self.assertEqual(windows["future_12_months"], (date(2026, 9, 8), date(2027, 9, 8)))

    def test_leap_day_clamps_to_february_28_next_year(self):
        windows = inclusive_windows(date(2028, 2, 29))
        self.assertEqual(windows["future_12_months"][1], date(2029, 2, 28))

    def test_normalization_and_match_strength(self):
        self.assertEqual(normalize_name(" John-Smith  2 "), "john smith")
        dates = {"check_in": "2026-10-10", "check_out": "2026-10-12"}
        self.assertEqual(classify_match({"guest_name": "John Smith", **dates}, {"guest_name": "John Smith", **dates})[0], "exact")
        self.assertEqual(classify_match({"guest_name": "John Smith 1", **dates}, {"guest_name": "john-smith 2", **dates})[0], "normalized")
        self.assertEqual(classify_match({"guest_name": "Jane Doe", "check_in": "2026-09-09", "check_out": "2026-09-10"}, {"guest_name": "Jane Doee", "check_in": "2026-09-09", "check_out": "2026-09-11"})[0], "possible")

    def test_same_name_without_overlapping_stay_is_not_a_duplicate(self):
        left = {"guest_name": "Jane Doe", "check_in": "2026-10-01", "check_out": "2026-10-02"}
        right = {"guest_name": "Jane Doe", "check_in": "2026-11-01", "check_out": "2026-11-02"}
        self.assertIsNone(classify_match(left, right)[0])

    def test_back_to_back_stays_are_not_overlapping_duplicates(self):
        left = {"guest_name": "Jane Doe", "check_in": "2026-10-01", "check_out": "2026-10-02"}
        right = {"guest_name": "Jane Doe", "check_in": "2026-10-02", "check_out": "2026-10-03"}
        self.assertIsNone(classify_match(left, right)[0])

    def test_email_and_deduplication_contracts(self):
        self.assertEqual(email_kind("person@gmail.com"), "personal")
        self.assertEqual(email_kind("person@buildco.com"), "company")
        self.assertEqual(len(deduplicate_reservations(self.records)), 5)

    def test_fixture_analysis_is_deterministic(self):
        first = analyze_duplicates(self.records, date(2026, 9, 8))
        second = analyze_duplicates(list(reversed(self.records)), date(2026, 9, 8))
        self.assertEqual(first["counts"], {"input": 6, "unique": 5, "cancelled_excluded": 1})
        upcoming = first["windows"]["future_12_months"]
        self.assertEqual((upcoming["groups_found"], upcoming["rooms_total"]), (2, 5))
        self.assertEqual(first, second)

    def test_exact_group_remains_exact(self):
        records = [
            {"guest_name": "GARCIA, JUAN CARLOS", "confirmation_number": f"G{i}",
             "check_in": "2026-10-01", "check_out": "2026-10-02", "rooms_booked": 1,
             "primary_email": "Not displayed.", "secondary_email": "Not displayed.", "status": "Active"}
            for i in range(3)
        ]
        result = analyze_duplicates(records, date(2026, 9, 8))["windows"]["future_12_months"]["groups"][0]
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

    def test_same_normalized_guest_and_dates_are_consolidated(self):
        records = [
            {"guest_name": "Morgan Lee 1", "account_number": "A1", "confirmation_number": "C1",
             "check_in": "2026-08-01", "check_out": "2026-08-03", "rooms_booked": 1,
             "primary_email": "morgan@example.com", "status": "Active"},
            {"guest_name": "morgan-lee 2", "account_number": "A2", "confirmation_number": "C2",
             "check_in": "2026-08-01", "check_out": "2026-08-03", "rooms_booked": 2,
             "secondary_email": "other@example.com", "status": "Active"},
        ]
        stays = consolidate_guest_stays(records)
        self.assertEqual(len(stays), 1)
        self.assertEqual(stays[0]["rooms_booked"], 3)
        self.assertEqual(stays[0]["source_reservation_count"], 2)
        self.assertEqual(stays[0]["account_numbers"], ["A1", "A2"])
        self.assertEqual(stays[0]["confirmation_numbers"], ["C1", "C2"])
        self.assertEqual(stays[0]["emails"], ["morgan@example.com", "other@example.com"])

    def test_consolidation_keeps_different_dates_and_fuzzy_names_separate(self):
        records = [
            {"guest_name": "Morgan Lee", "account_number": "A1", "check_in": "2026-08-01",
             "check_out": "2026-08-03", "rooms_booked": 1},
            {"guest_name": "Morgan Lee", "account_number": "A2", "check_in": "2026-08-02",
             "check_out": "2026-08-04", "rooms_booked": 1},
            {"guest_name": "Morgen Lee", "account_number": "A3", "check_in": "2026-08-01",
             "check_out": "2026-08-03", "rooms_booked": 1},
        ]
        stays = consolidate_guest_stays(records)
        self.assertEqual(len(stays), 3)
        self.assertEqual(sum(stay["rooms_booked"] for stay in stays), 3)

    def test_analysis_room_total_survives_consolidation(self):
        records = [
            {"guest_name": "Morgan Lee", "account_number": f"A{i}",
             "check_in": "2026-10-01", "check_out": "2026-10-03",
             "rooms_booked": rooms, "status": "Active"}
            for i, rooms in enumerate((1, 2, 1), start=1)
        ]
        group = analyze_duplicates(records, date(2026, 9, 8))["windows"]["future_12_months"]["groups"][0]
        self.assertEqual((group["reservation_count"], group["guest_stay_count"]), (3, 1))
        self.assertEqual(group["rooms_total"], 4)
        self.assertEqual(sum(item["rooms_booked"] for item in group["guest_stays"]), 4)


if __name__ == "__main__":
    unittest.main()
