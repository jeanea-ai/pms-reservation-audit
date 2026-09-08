import unittest

from scripts.audit_report import _cell_text, _money


class AuditReportFormattingTests(unittest.TestCase):
    def test_currency_formatting(self):
        self.assertEqual(_money(150.5), "$150.50")
        self.assertEqual(_money(-150.5), "($150.50)")

    def test_identifiers_remain_strings(self):
        self.assertEqual(_cell_text("001234"), "001234")
        self.assertEqual(_cell_text(None), "")


if __name__ == "__main__":
    unittest.main()
