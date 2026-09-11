from __future__ import annotations

import unittest

from scripts.pms_login import (
    BrowserChallenge,
    _browser_saved_fields_present,
    _click_traditional_login_continue,
    _has_traditional_login_continue,
    _is_authenticated,
    _looks_like_browser_challenge,
    _looks_like_mfa,
    _raise_if_browser_challenge,
    _select_page_target,
    _snapshot_signature,
)


class PmsLoginTests(unittest.TestCase):
    def test_report_menu_is_required_for_authenticated_state(self):
        self.assertTrue(_is_authenticated({"hasReportMenu": True, "url": "https://example.test"}))
        self.assertTrue(_is_authenticated({"hasReportMenu": False, "url": "https://example.test/ReportViewStart.init"}))
        self.assertFalse(_is_authenticated({"hasReportMenu": False, "url": "https://example.test/Login.do"}))

    def test_skip_mfa_and_challenge_pages_are_detected(self):
        self.assertTrue(_looks_like_mfa({"title": "Verify", "body": "Skip MFA"}))
        self.assertTrue(_looks_like_mfa({"title": "", "body": "Enter verification code"}))
        self.assertFalse(_looks_like_mfa({"title": "Reports", "body": "Guest Ledger"}))

    def test_bot_challenge_is_distinct_from_mfa(self):
        challenge = {
            "url": "https://www.choiceadvantage.com/challenge",
            "title": "Security check",
            "body": "Please verify you are human",
        }
        self.assertTrue(_looks_like_browser_challenge(challenge))
        self.assertFalse(_looks_like_mfa(challenge))
        with self.assertRaises(BrowserChallenge):
            _raise_if_browser_challenge(challenge)
        self.assertFalse(
            _looks_like_browser_challenge(
                {"url": "https://example.test/mfa", "body": "Enter verification code"}
            )
        )

    def test_target_selection_prefers_reports_page_over_pdf_viewer(self):
        target = _select_page_target(
            [
                {
                    "url": "https://www.choiceadvantage.com/choicehotels/ReportProxyServlet.proxy?ie=pdf",
                    "webSocketDebuggerUrl": "ws://pdf",
                },
                {
                    "url": "https://www.choiceadvantage.com/choicehotels/ReportViewStart.init",
                    "webSocketDebuggerUrl": "ws://reports",
                },
            ]
        )
        self.assertEqual(target["webSocketDebuggerUrl"], "ws://reports")

    def test_target_selection_refuses_equally_valid_tabs(self):
        with self.assertRaisesRegex(Exception, "multiple equally valid"):
            _select_page_target(
                [
                    {
                        "url": "https://www.choiceadvantage.com/choicehotels/ReportViewStart.init",
                        "webSocketDebuggerUrl": "ws://one",
                    },
                    {
                        "url": "https://www.choiceadvantage.com/choicehotels/ReportViewStart.init",
                        "webSocketDebuggerUrl": "ws://two",
                    },
                ]
            )

    def test_target_selection_refuses_unrelated_tab(self):
        with self.assertRaisesRegex(Exception, "no exact ChoiceADVANTAGE"):
            _select_page_target(
                [
                    {
                        "url": "https://example.test/private-work",
                        "webSocketDebuggerUrl": "ws://unrelated",
                    }
                ]
            )

    def test_page_change_signature_includes_body(self):
        before = {"url": "https://example.test/Login.do", "title": "Choice", "ready": "complete", "body": "Login"}
        after = {**before, "body": "Skip MFA"}
        self.assertNotEqual(_snapshot_signature(before), _snapshot_signature(after))

    def test_migration_continue_requires_traditional_login_handler(self):
        class FakeSession:
            def __init__(self):
                self.expressions = []

            def evaluate(self, expression, **kwargs):
                self.expressions.append(expression)
                return True

            def pace(self):
                self.expressions.append("pace")

        session = FakeSession()
        self.assertTrue(_has_traditional_login_continue(session))
        _click_traditional_login_continue(session)
        rendered = "\n".join(session.expressions)
        self.assertIn("formSubmit", rendered)
        self.assertNotIn("logoutThenRedirect", rendered)

    def test_browser_saved_login_checks_presence_without_returning_values(self):
        class FakeSession:
            def __init__(self):
                self.expressions = []

            def evaluate(self, expression, **kwargs):
                self.expressions.append(expression)
                return True

        session = FakeSession()
        self.assertTrue(_browser_saved_fields_present(session))
        rendered = "\n".join(session.expressions)
        self.assertIn("value.length", rendered)
        self.assertNotIn("return username.value", rendered)
        self.assertNotIn("return password.value", rendered)


if __name__ == "__main__":
    unittest.main()
