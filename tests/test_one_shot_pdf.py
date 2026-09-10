from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.one_shot_pdf import CaptureError, preserve_response


PDF = b"%PDF-1.4\noriginal response bytes\n%%EOF\n"


class OneShotPdfTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state = self.root / "state.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_preserves_exact_first_pdf_bytes(self):
        output = self.root / "report.pdf"
        record = preserve_response(PDF, output, self.state, "key-one")
        self.assertEqual(PDF, output.read_bytes())
        self.assertEqual("network-response", record["capture_method"])

    def test_same_key_cannot_be_consumed_twice(self):
        preserve_response(PDF, self.root / "first.pdf", self.state, "key-one")
        with self.assertRaisesRegex(CaptureError, "already consumed"):
            preserve_response(PDF, self.root / "second.pdf", self.state, "key-one")

    def test_empty_response_fails_and_spends_key(self):
        with self.assertRaisesRegex(CaptureError, "empty"):
            preserve_response(b"", self.root / "empty.pdf", self.state, "spent")
        with self.assertRaisesRegex(CaptureError, "already consumed"):
            preserve_response(PDF, self.root / "retry.pdf", self.state, "spent")

    def test_non_pdf_fails_and_spends_key(self):
        with self.assertRaisesRegex(CaptureError, "not a PDF"):
            preserve_response(
                b"<html>error</html>",
                self.root / "bad.pdf",
                self.state,
                "bad",
            )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        record = next(iter(state["consumed_keys"].values()))
        self.assertEqual("failed-not-pdf", record["status"])

    def test_retry_succeeds_with_fresh_key(self):
        with self.assertRaises(CaptureError):
            preserve_response(
                b"", self.root / "failed.pdf", self.state, "old-key"
            )
        output = self.root / "fresh.pdf"
        preserve_response(PDF, output, self.state, "new-key")
        self.assertEqual(PDF, output.read_bytes())

    def test_existing_artifact_is_never_overwritten(self):
        output = self.root / "report.pdf"
        output.write_bytes(b"keep me")
        with self.assertRaisesRegex(CaptureError, "overwrite"):
            preserve_response(PDF, output, self.state, "key-one")
        self.assertEqual(b"keep me", output.read_bytes())

    def test_print_to_pdf_capture_is_rejected(self):
        with self.assertRaisesRegex(CaptureError, "original network response"):
            preserve_response(
                PDF,
                self.root / "report.pdf",
                self.state,
                "key-one",
                capture_method="Page.printToPDF",
            )


if __name__ == "__main__":
    unittest.main()
