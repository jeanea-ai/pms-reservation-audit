from __future__ import annotations

import base64
from datetime import date
import json
import re
import unittest
from unittest.mock import patch

from scripts.pms_login import BrowserChallenge, CdpSession, _page_websocket
from scripts.pms_report_pull import (
    REPORTS,
    _capture_submit,
    _prepare_form,
    same_date_next_year,
)


class FakeCaptureSession:
    def __init__(self, pdf, status=200):
        self.pdf = pdf
        self.status = status
        self.expressions = []
        self.polls = 0

    def evaluate(self, expression, **kwargs):
        self.expressions.append((expression, kwargs))
        if "const submit = document.querySelector" in expression:
            return {"started": True, "code": ""}
        if "length: state.bytes" in expression:
            self.polls += 1
            return {
                "status": "ready",
                "length": len(self.pdf),
                "contentType": "application/pdf",
                "responseUrl": "https://www.choiceadvantage.com/choicehotels/ReportProxyServlet.proxy?ie=pdf",
                "httpStatus": self.status,
                "errorCode": "",
            }
        if "return btoa(binary)" in expression:
            match = re.search(r"subarray\((\d+), (\d+)\)", expression)
            if not match:
                return None
            start, end = map(int, match.groups())
            return base64.b64encode(self.pdf[start:end]).decode("ascii")
        return True


class FakeFormSession:
    def __init__(self, result):
        self.result = result
        self.expression = ""

    def evaluate(self, expression, **kwargs):
        self.expression = expression
        return self.result


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = [json.dumps(message) for message in messages]
        self.sent = []
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        return self.messages.pop(0)


class PmsReportPullTests(unittest.TestCase):
    def test_page_websocket_prefers_existing_choiceadvantage_page(self):
        targets = [
            {
                "type": "page",
                "url": "https://example.com/",
                "webSocketDebuggerUrl": "ws://browser/first",
            },
            {
                "type": "page",
                "url": "https://www.choiceadvantage.com/choicehotels/Welcome.do",
                "webSocketDebuggerUrl": "ws://browser/pms",
            },
        ]

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(targets).encode("utf-8")

        with patch("scripts.pms_login.urllib.request.urlopen", return_value=Response()):
            self.assertEqual(_page_websocket("http://browser"), "ws://browser/pms")

    def test_same_date_next_year_handles_leap_day(self):
        self.assertEqual(same_date_next_year(date(2024, 2, 29)), date(2025, 2, 28))
        self.assertEqual(same_date_next_year(date(2026, 9, 10)), date(2027, 9, 10))

    def test_future_form_uses_arrival_window_and_leaves_booking_blank(self):
        session = FakeFormSession(
            {"arrival_from": "9/10/2026", "arrival_to": "9/10/2027"}
        )
        result = _prepare_form(
            session, REPORTS["future-reservations"], date(2026, 9, 10)
        )
        self.assertEqual(result["arrival_to"], "9/10/2027")
        self.assertIn('"bookingDateFrom": ""', session.expression)
        self.assertIn('"bookingDateTo": ""', session.expression)
        self.assertNotIn("form.target = '_self'", session.expression)

    def test_guest_form_preserves_default_business_date(self):
        session = FakeFormSession({"business_date": "9/9/2026"})
        result = _prepare_form(session, REPORTS["guest-ledger"], date(2026, 9, 10))
        self.assertEqual(result["business_date"], "9/9/2026")
        self.assertNotIn("dateField.value =", session.expression)

    def test_capture_uses_authenticated_fetch_and_returns_original_pdf(self):
        pdf = b"%PDF-1.7\n" + (b"parser grade\n" * 40000)
        session = FakeCaptureSession(pdf)
        self.assertEqual(_capture_submit(session, 1), pdf)
        self.assertTrue(
            any(
                "fetch(url.toString(), options)" in item[0]
                for item in session.expressions
            )
        )
        self.assertTrue(
            any("return btoa(binary)" in item[0] for item in session.expressions)
        )
        self.assertGreater(
            sum("return btoa(binary)" in item[0] for item in session.expressions), 1
        )
        self.assertFalse(any("Network." in item[0] for item in session.expressions))

    def test_capture_classifies_access_control_status_without_retrying(self):
        session = FakeCaptureSession(b"denied", status=429)
        with self.assertRaisesRegex(BrowserChallenge, "HTTP 429"):
            _capture_submit(session, 1)

    def test_cdp_call_buffers_events_seen_before_response(self):
        session = CdpSession.__new__(CdpSession)
        session._ws = FakeWebSocket(
            [
                {"method": "Network.responseReceived", "params": {"requestId": "r1"}},
                {"id": 1, "result": {"ok": True}},
            ]
        )
        session._next_id = 0
        session._events = []
        self.assertEqual(session.call("Network.enable"), {"ok": True})
        self.assertEqual(session.next_event(1)["method"], "Network.responseReceived")


if __name__ == "__main__":
    unittest.main()
