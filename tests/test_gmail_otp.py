from __future__ import annotations

import base64
import json
import urllib.error
import unittest

from scripts.gmail_otp import GmailOtpReader, OtpError, _is_voice_sender


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return self.payload


def encoded(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class FakeClock:
    def __init__(self, value=2_000_000_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class GmailOtpTests(unittest.TestCase):
    def test_voice_sender_matching_rejects_lookalike_domains(self):
        self.assertTrue(_is_voice_sender("Google Voice <notice@txt.voice.google.com>"))
        self.assertTrue(_is_voice_sender("txt.voice.google.com"))
        self.assertFalse(_is_voice_sender("notice@txt.voice.google.com.evil.test"))

    def test_fresh_sender_matched_code_is_returned_without_logging_values(self):
        clock = FakeClock()
        requested = []

        def opener(request, timeout):
            requested.append((request, timeout))
            if request.full_url.endswith("/profile"):
                return Response({"emailAddress": "ops@example.test"})
            if "messages?" in request.full_url:
                return Response({"messages": [{"id": "message-1"}]})
            return Response(
                {
                    "internalDate": str(int(clock.value * 1000)),
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Google Voice <txt.voice.google.com>"}
                        ],
                        "body": {"data": encoded("Your verification code is 123456")},
                    },
                }
            )

        reader = GmailOtpReader(
            "ops@example.test",
            environ={"MATON_API_KEY": "canary-token"},
            opener=opener,
            clock=clock,
            sleeper=clock.sleep,
        )
        self.assertEqual(
            "123456",
            reader.wait_for_code(after_epoch=clock.value - 1, timeout_seconds=10),
        )
        rendered = " ".join(item[0].full_url for item in requested)
        self.assertIn("from%3Atxt.voice.google.com", rendered)
        self.assertNotIn("canary-token", rendered)

    def test_mailbox_mismatch_fails_before_messages_are_read(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return Response({"emailAddress": "wrong@example.test"})

        reader = GmailOtpReader(
            "ops@example.test",
            environ={"MATON_API_KEY": "secret"},
            opener=opener,
        )
        with self.assertRaisesRegex(OtpError, "does not match"):
            reader.wait_for_code(after_epoch=1_000_000_000, timeout_seconds=10)
        self.assertEqual(1, len(calls))

    def test_connected_mailbox_can_supply_legacy_okta_identity_once(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return Response({"emailAddress": "Ops@Example.Test"})

        reader = GmailOtpReader(
            environ={"MATON_API_KEY": "secret"},
            opener=opener,
        )
        deadline = 2_000_000_010.0
        self.assertEqual("ops@example.test", reader.mailbox_email(deadline=deadline))
        self.assertEqual("ops@example.test", reader.verify_mailbox(deadline=deadline))
        self.assertEqual(1, len(calls))

    def test_stale_wrong_sender_and_ambiguous_messages_are_rejected(self):
        clock = FakeClock()
        replies = iter(
            [
                {"emailAddress": "ops@example.test"},
                {"messages": [{"id": "message-1"}]},
                {
                    "internalDate": str(int(clock.value * 1000)),
                    "payload": {
                        "headers": [{"name": "From", "value": "txt.voice.google.com"}],
                        "body": {"data": encoded("Codes 123456 and 654321")},
                    },
                },
            ]
        )
        reader = GmailOtpReader(
            "ops@example.test",
            environ={"MATON_API_KEY": "secret"},
            opener=lambda *_args, **_kwargs: Response(next(replies)),
            clock=clock,
            sleeper=clock.sleep,
        )
        with self.assertRaisesRegex(OtpError, "multiple possible"):
            reader.wait_for_code(after_epoch=clock.value - 1, timeout_seconds=10)

    def test_missing_token_and_authorization_errors_are_secret_free(self):
        with self.assertRaisesRegex(OtpError, "credential is unavailable"):
            GmailOtpReader("ops@example.test", environ={})

        def forbidden(_request, timeout):
            raise urllib.error.HTTPError("https://gateway.invalid", 403, "", {}, None)

        reader = GmailOtpReader(
            "ops@example.test",
            environ={"MATON_API_KEY": "do-not-print"},
            opener=forbidden,
        )
        with self.assertRaises(OtpError) as caught:
            reader.wait_for_code(after_epoch=1_000_000_000, timeout_seconds=10)
        self.assertNotIn("do-not-print", str(caught.exception))

    def test_one_transient_gateway_failure_is_retried_within_deadline(self):
        clock = FakeClock()
        calls = []

        def opener(_request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.HTTPError("https://gateway.invalid", 503, "", {}, None)
            return Response({"emailAddress": "ops@example.test"})

        reader = GmailOtpReader(
            "ops@example.test",
            environ={"MATON_API_KEY": "secret"},
            opener=opener,
            clock=clock,
            sleeper=clock.sleep,
        )
        reader.verify_mailbox(deadline=clock.value + 10)
        self.assertEqual(2, len(calls))
        self.assertEqual(0.5, clock.value - 2_000_000_000.0)


if __name__ == "__main__":
    unittest.main()
