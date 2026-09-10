"""Regression tests enforcing the approved v0.3.3 report layout contract plus
the v0.3.6 Group section, page/orientation limits, and source reconciliation.

These tests are the machine-checkable mirror of report_layout_contract.json.
They fail on any change to the approved section order/table headers, on Group
accounts missing or mixed into the No Show/Cancelled balance table, on any
Folio or Confirmation text or column, on more than three pages or non-portrait
output, and on dropped findings or unreconciled source counts/subtotals.
"""

import json
import tempfile
from pathlib import Path
import unittest

from scripts.audit_pipeline import build_report_spec
from scripts.audit_report import render_pdf_atomic, verify_pdf_structure
from scripts.source_to_input import build_audit_input


FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = Path(__file__).parent.parent / "report_layout_contract.json"


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _payload():
    return build_audit_input(
        guest_ledger_text=(FIXTURES / "guest_ledger.txt").read_text(encoding="utf-8"),
        future_reservation_texts=[
            (FIXTURES / "reservation_activity.txt").read_text(encoding="utf-8"),
        ],
        property_local_date="2026-09-08",
        reviewed_at="2026-09-08 17:00 America/Los_Angeles",
    )


def _pdf_text(path):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return " ".join(
        "\n".join(page.extract_text() or "" for page in reader.pages).split()
    )


class LayoutContractTests(unittest.TestCase):
    def test_spec_matches_contract_verbatim(self):
        contract = _contract()
        spec, _ = build_report_spec(_payload())
        headings = [section["heading"] for section in spec["sections"]]
        self.assertEqual(headings, [s["heading"] for s in contract["sections"]])
        for section, contract_section in zip(spec["sections"], contract["sections"]):
            titles = [table["table_title"] for table in section["tables"]]
            self.assertEqual(titles, contract_section["table_titles"])
            for table in section["tables"]:
                self.assertEqual(table["headers"], contract_section["headers"])
        self.assertEqual(spec["title"], contract["document"]["title"])
        self.assertEqual(spec["max_pages"], contract["document"]["max_pages"])
        self.assertEqual(spec["disclaimer"], contract["document"]["disclaimer"])

    def test_group_accounts_are_separate_and_never_merged(self):
        spec, _ = build_report_spec(_payload())
        balance_section = spec["sections"][0]
        balance_rows = balance_section["tables"][0]["rows"]
        statuses = {row[1] for row in balance_rows}
        self.assertEqual(balance_section["heading"], "1. Guest Ledger Balance Review")
        self.assertEqual(spec["sections"][-1]["heading"], "3. Group Accounts")
        for forbidden in _contract()["group_isolation"]["balance_table_excludes_statuses"]:
            self.assertNotIn(forbidden, statuses)
        group_section = spec["sections"][-1]
        balance_names = {row[0] for row in balance_rows}
        group_names = [row[0] for row in group_section["tables"][0]["rows"]]
        for name in group_names:
            self.assertNotIn(name, balance_names)

    def test_no_folio_or_confirmation_text_anywhere(self):
        spec, _ = build_report_spec(_payload())
        blob = json.dumps(spec, default=str)
        for term in _contract()["prohibited_terms"]:
            self.assertNotIn(term, blob)

    def test_pdf_is_portrait_within_three_pages_and_has_no_prohibited_text(self):
        spec, _ = build_report_spec(_payload())
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.pdf"
            render_pdf_atomic(spec, output)
            self.assertEqual(verify_pdf_structure(output, spec), "pypdf")
            text = _pdf_text(output)
            for term in _contract()["prohibited_terms"]:
                self.assertNotIn(term, text)


class ReconciliationTests(unittest.TestCase):
    def test_group_subtotal_and_names_reconcile(self):
        payload = _payload()
        groups = payload["guest_ledger"]["groups"]
        self.assertEqual([g["guest_name"] for g in groups], [
            "Example Education Foundation",
            "STURDY CO Education & Community",
            "Partnership IECP",
        ])
        self.assertEqual(round(sum(g["balance"] for g in groups), 2), 600.0)

    def test_balances_reconcile_to_recent_30_day_window_without_groups(self):
        payload = _payload()
        balances = payload["guest_ledger"]["balances"]
        self.assertEqual([b["guest_name"] for b in balances], [
            "SAMPLE, ALPHA", "SAMPLE, BETA", "SAMPLE, RECENT",
        ])
        self.assertEqual(round(sum(b["balance"] for b in balances), 2), 50.8)
        self.assertTrue(all(b["status"] in ("No Show", "Cancelled") for b in balances))

    def test_group_summary_reports_count_and_total(self):
        spec, _ = build_report_spec(_payload())
        group_section = spec["sections"][-1]
        summary = group_section["tables"][0]["summary"]
        self.assertIn("3 account(s)", summary)
        self.assertIn("$600.00", summary)


if __name__ == "__main__":
    unittest.main()
