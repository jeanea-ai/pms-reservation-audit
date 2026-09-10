from __future__ import annotations

import base64
from datetime import date
import json
import unittest

from scripts.pms_login import CdpSession
from scripts.pms_report_pull import (
    REPORTS,
    ReportPullError,
    _capture_submit,
    _prepare_form,
    same_date_next_year,
)


class FakeCaptureSession:
    def __init__(self, events, bodies):
        self.events = list(events)
        self.bodies = bodies
        self.calls = []
        self.expressions = []

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == "Network.getResponseBody":
            return self.bodies[params["requestId"]]
        return {}

    def clear_events(self):
        pass

    def evaluate(self, expression, **kwargs):
        self.expressions.append((expression, kwargs))
        return True

    def next_event(self, timeout):
        if not self.events:
            raise ReportPullError("test event queue exhausted")
        return self.events.pop(0)


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
        self.assertIn("form.target = '_self'", session.expression)

    def test_guest_form_preserves_default_business_date(self):
        session = FakeFormSession({"business_date": "9/9/2026"})
        result = _prepare_form(session, REPORTS["guest-ledger"], date(2026, 9, 10))
        self.assertEqual(result["business_date"], "9/9/2026")
        self.assertNotIn("dateField.value =", session.expression)

    def test_capture_skips_html_wrapper_and_returns_first_pdf(self):
        pdf = b"%PDF-1.7\nparser grade"
        events = [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "html",
                    "response": {
                        "url": "https://www.choiceadvantage.com/choicehotels/ReportProxyServlet.proxy?ie=pdf",
                        "mimeType": "text/html",
                    },
                },
            },
            {"method": "Network.loadingFinished", "params": {"requestId": "html"}},
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "pdf",
                    "response": {"url": "blob:https://www.choiceadvantage.com/id", "mimeType": "application/pdf"},
                },
            },
            {"method": "Network.loadingFinished", "params": {"requestId": "pdf"}},
        ]
        session = FakeCaptureSession(
            events,
            {
                "html": {"body": "<!doctype html>", "base64Encoded": False},
                "pdf": {
                    "body": base64.b64encode(pdf).decode("ascii"),
                    "base64Encoded": True,
                },
            },
        )
        self.assertEqual(_capture_submit(session, 1), pdf)
        self.assertEqual(
            [method for method, _ in session.calls].count("Network.getResponseBody"), 2
        )

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
