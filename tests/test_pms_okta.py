from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.pms_login import CdpSession, LoginError
from scripts.pms_okta import (
    ADVANTAGE_HOST,
    CONNECT_HOST,
    OktaSurface,
    OktaLoginError,
    _fill,
    _is_exact_host,
    discover_okta_surface,
    login_okta,
)


class FakeSession:
    def __init__(self, tree, *, attached=None, context=71):
        self.tree = tree
        self.attached = attached or []
        self.context = context
        self.calls = []
        self.expressions = []

    def call(self, method, params=None, *, session_id=None):
        self.calls.append((method, params or {}, session_id))
        if method == "Page.getFrameTree":
            return {"frameTree": self.tree}
        if method == "Page.createIsolatedWorld":
            if self.context == "error":
                raise LoginError("unavailable")
            return {"executionContextId": self.context}
        raise AssertionError(method)

    def attached_targets(self):
        return self.attached

    def evaluate(self, expression, **kwargs):
        self.expressions.append((expression, kwargs))
        return True

    def pace(self):
        pass


def frame(frame_id, url, children=None):
    value = {"frame": {"id": frame_id, "url": url}}
    if children:
        value["childFrames"] = children
    return value


class PmsOktaTests(unittest.TestCase):
    def test_discovers_exact_top_level_okta_origin(self):
        session = FakeSession(frame("root", "https://choicehotels.okta.com/login"))
        surface = discover_okta_surface(session)
        self.assertEqual("top", surface.kind)
        self.assertIsNone(surface.context_id)

    def test_discovers_same_process_iframe_with_isolated_context(self):
        session = FakeSession(
            frame(
                "root",
                "https://connect.choicehotels.com/login",
                [frame("child", "https://choicehotels.okta.com/signin")],
            ),
            context=91,
        )
        surface = discover_okta_surface(session)
        self.assertEqual("iframe", surface.kind)
        self.assertEqual(91, surface.context_id)
        self.assertIn(
            ("Page.createIsolatedWorld", {
                "frameId": "child",
                "worldName": "pms-okta-audit",
                "grantUniveralAccess": False,
            }, None),
            session.calls,
        )

    def test_discovers_oopif_and_routes_evaluation_to_its_session(self):
        attached = [{
            "session_id": "session-7",
            "target_info": {
                "targetId": "child",
                "type": "iframe",
                "url": "https://choicehotels.okta.com/signin",
            },
        }]
        session = FakeSession(
            frame(
                "root",
                "https://connect.choicehotels.com/login",
                [frame("child", "https://choicehotels.okta.com/signin")],
            ),
            attached=attached,
        )
        surface = discover_okta_surface(session)
        self.assertEqual("oopif", surface.kind)
        surface.evaluate(session, "document.title")
        self.assertEqual("session-7", session.expressions[-1][1]["session_id"])

    def test_refuses_lookalike_origin_and_ambiguous_surfaces(self):
        self.assertTrue(_is_exact_host("https://choicehotels.okta.com/login", "choicehotels.okta.com"))
        self.assertFalse(_is_exact_host("http://choicehotels.okta.com/login", "choicehotels.okta.com"))
        self.assertFalse(_is_exact_host("https://choicehotels.okta.com:444/login", "choicehotels.okta.com"))
        lookalike = FakeSession(frame("root", "https://choicehotels.okta.com.evil.test"))
        self.assertIsNone(discover_okta_surface(lookalike))
        ambiguous = FakeSession(
            frame(
                "root",
                "https://choicehotels.okta.com/login",
                [frame("child", "https://choicehotels.okta.com/signin")],
            )
        )
        with self.assertRaisesRegex(OktaLoginError, "multiple exact-origin"):
            discover_okta_surface(ambiguous)

    def test_secret_field_fill_returns_only_presence(self):
        session = FakeSession(frame("root", "https://choicehotels.okta.com/login"))
        surface = discover_okta_surface(session)
        secret = "not-for-logs-481516"
        self.assertIsNone(_fill(session, surface, "password", secret))
        # CDP must receive the value in memory, but no result or exception includes it.
        self.assertTrue(session.expressions)

    def test_target_events_keep_oopif_session_current(self):
        session = object.__new__(CdpSession)
        session._events = []
        session._attached_targets = {}
        session._record_event({
            "method": "Target.attachedToTarget",
            "params": {
                "sessionId": "session-1",
                "targetInfo": {"targetId": "frame-1", "type": "iframe", "url": "about:blank"},
            },
        })
        session._record_event({
            "method": "Target.targetInfoChanged",
            "params": {
                "targetInfo": {
                    "targetId": "frame-1",
                    "type": "iframe",
                    "url": "https://choicehotels.okta.com/signin",
                },
            },
        })
        self.assertEqual(
            "https://choicehotels.okta.com/signin",
            session.attached_targets()[0]["target_info"]["url"],
        )
        session._record_event({
            "method": "Target.detachedFromTarget",
            "params": {"sessionId": "session-1"},
        })
        self.assertEqual([], session.attached_targets())

    def test_one_sms_vertical_slice_reaches_reports(self):
        class WorkflowSession:
            instances = []

            def __init__(self, _url, *, interaction_delay):
                self.state = "connect"
                self.sms_requests = 0
                self.navigations = []
                self.__class__.instances.append(self)

            def call(self, *_args, **_kwargs):
                return {}

            def navigate(self, url, **_kwargs):
                self.navigations.append(url)
                if "ReportViewStart.init" in url:
                    self.state = "reports"

            def evaluate(self, *_args, **_kwargs):
                return True

            def pace(self):
                pass

            def close(self):
                pass

            def snapshot(self):
                if self.state == "advantage":
                    return {"url": f"https://{ADVANTAGE_HOST}/home"}
                if self.state == "reports":
                    return {
                        "url": f"https://{ADVANTAGE_HOST}/choicehotels/ReportViewStart.init",
                        "hasReportMenu": True,
                        "ready": "complete",
                    }
                return {"url": f"https://{CONNECT_HOST}/dashboard", "ready": "complete"}

        class Reader:
            def __init__(self):
                self.calls = []

            def wait_for_code(self, **kwargs):
                self.calls.append(kwargs)
                return "123456"

        reader = Reader()
        surface = OktaSurface("top", "https://choicehotels.okta.com/login")

        def surface_snapshot(session, _surface):
            states = {
                "username": {"hasUsername": True, "hasPassword": True, "controls": ["Sign In"], "url": surface.url},
                "phone": {"controls": ["Verify with your phone"], "url": surface.url},
                "sms": {"controls": ["Receive a code via SMS"], "url": surface.url},
                "otp": {"hasOtp": True, "controls": ["Verify"], "url": surface.url},
            }
            return states[session.state]

        def top_click(session, *labels):
            if "Connect Now" in labels:
                session.state = "username"
                return True
            if "Choice Advantage" in labels:
                session.state = "advantage"
                return True
            return False

        def click(session, _surface, *labels):
            if "Sign In" in labels:
                session.state = "phone"
            elif "Verify with your phone" in labels:
                session.state = "sms"
            elif "Receive a code via SMS" in labels:
                session.sms_requests += 1
                session.state = "otp"
            elif "Verify" in labels:
                session.state = "connect"
            else:
                return False
            return True

        access = {
            "username": "ops@example.test",
            "password": "secret",
            "ops_email": "ops@example.test",
            "okta_mfa": "gv_sms",
        }
        with patch("scripts.pms_okta._create_page_target", return_value={"webSocketDebuggerUrl": "ws://test"}), \
                patch("scripts.pms_okta.CdpSession", WorkflowSession), \
                patch("scripts.pms_okta._wait_for_settle", side_effect=lambda session, **_kwargs: session.snapshot()), \
                patch("scripts.pms_okta._wait_for_okta", return_value=surface), \
                patch("scripts.pms_okta.discover_okta_surface", side_effect=lambda session: surface if session.state in {"username", "phone", "sms", "otp"} else None), \
                patch("scripts.pms_okta._surface_snapshot", side_effect=surface_snapshot), \
                patch("scripts.pms_okta._top_click_exact", side_effect=top_click), \
                patch("scripts.pms_okta._click_exact", side_effect=click), \
                patch("scripts.pms_okta._fill"), \
                patch("scripts.pms_okta._wait_for_choice_destination", return_value=CONNECT_HOST), \
                patch("scripts.pms_okta._snapshot", side_effect=lambda session: session.snapshot()), \
                patch("scripts.pms_okta._list_page_targets", return_value=[]):
            result = login_okta(
                access,
                cdp_url="http://browser.test",
                interaction_delay=0.75,
                otp_reader=reader,
            )
        session = WorkflowSession.instances[0]
        self.assertEqual("authenticated", result["status"])
        self.assertEqual(1, session.sms_requests)
        self.assertEqual(1, len(reader.calls))
        self.assertNotIn("123456", repr(result))


if __name__ == "__main__":
    unittest.main()
