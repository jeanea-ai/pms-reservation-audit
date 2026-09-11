#!/usr/bin/env python3
"""Log into ChoiceADVANTAGE without exposing credentials to the agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

try:
    from scripts.pms_access import AccessError, resolve_access
except ModuleNotFoundError:
    from pms_access import AccessError, resolve_access


DEFAULT_CDP_URL = "http://127.0.0.1:18800"
SIGN_IN_URL = "https://www.choiceadvantage.com/choicehotels/sign_in.jsp"
REPORTS_URL = "https://www.choiceadvantage.com/choicehotels/ReportViewStart.init"
SKIP_MFA_LABEL = "Skip MFA"
DEFAULT_INTERACTION_DELAY = 0.75


class LoginError(RuntimeError):
    """Raised when deterministic browser login cannot safely continue."""


class BrowserChallenge(LoginError):
    """Raised when the site presents an access-control or human-verification challenge."""


class CdpSession:
    def __init__(
        self,
        websocket_url: str,
        *,
        interaction_delay: float = DEFAULT_INTERACTION_DELAY,
    ):
        if not 0.25 <= interaction_delay <= 3:
            raise ValueError("interaction delay must be between 0.25 and 3 seconds")
        try:
            import websocket
        except ImportError as exc:
            raise LoginError("websocket-client is required for browser login") from exc
        self._ws = websocket.create_connection(
            websocket_url, timeout=10, suppress_origin=True
        )
        self._next_id = 0
        self._events: list[dict] = []
        self.interaction_delay = interaction_delay

    def close(self) -> None:
        self._ws.close()

    def call(self, method: str, params: dict | None = None) -> dict:
        self._ws.settimeout(10)
        self._next_id += 1
        call_id = self._next_id
        self._ws.send(
            json.dumps({"id": call_id, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != call_id:
                if message.get("method"):
                    self._events.append(message)
                continue
            if "error" in message:
                raise LoginError(f"browser command failed: {message['error'].get('message', 'unknown error')}")
            return message.get("result", {})

    def clear_events(self) -> None:
        self._events.clear()

    def next_event(self, timeout: float) -> dict:
        """Return the next CDP event without losing events seen during calls."""
        if self._events:
            return self._events.pop(0)
        if timeout <= 0:
            raise LoginError("browser event wait timed out")
        self._ws.settimeout(timeout)
        while True:
            try:
                message = json.loads(self._ws.recv())
            except Exception as exc:
                raise LoginError("browser event wait timed out") from exc
            if message.get("method"):
                return message

    def evaluate(self, expression: str, *, user_gesture: bool = False):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": user_gesture,
            },
        )
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise LoginError("browser evaluation failed")
        return remote.get("value")

    def navigate(self, url: str) -> None:
        self.pace()
        self.call("Page.navigate", {"url": url})

    def pace(self) -> None:
        """Apply a small deterministic gap between site-facing interactions."""
        time.sleep(self.interaction_delay)

    def trusted_click(self, x: float, y: float) -> None:
        """Dispatch one browser-trusted click at previously verified coordinates."""
        self.pace()
        common = {"x": x, "y": y, "button": "left", "clickCount": 1}
        self.call("Input.dispatchMouseEvent", {"type": "mousePressed", **common})
        self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **common})


def _select_page_target(pages: list[dict]) -> dict:
    def priority(target: dict) -> int:
        raw_url = str(target.get("url") or "")
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").casefold()
        if host != "choiceadvantage.com" and not host.endswith(".choiceadvantage.com"):
            return 0
        path = parsed.path.casefold()
        if "reportviewstart.init" in path:
            return 5
        if "sign_in.jsp" in path or "login.do" in path:
            return 4
        if "reportproxyservlet.proxy" in path:
            return 1
        return 3

    if not pages:
        raise LoginError("the persistent browser has no page target")
    best_priority = max(priority(target) for target in pages)
    if best_priority > 0:
        matches = [target for target in pages if priority(target) == best_priority]
    elif len(pages) == 1:
        matches = pages
    else:
        raise LoginError(
            "the persistent browser has multiple pages and no exact ChoiceADVANTAGE target"
        )
    if len(matches) != 1:
        raise LoginError(
            "the persistent browser has multiple equally valid ChoiceADVANTAGE targets"
        )
    return matches[0]


def _page_websocket(cdp_url: str) -> str:
    try:
        with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json", timeout=5) as response:
            targets = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LoginError("the persistent browser CDP endpoint is unavailable") from exc
    pages = [
        target
        for target in targets
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
    ]
    return _select_page_target(pages)["webSocketDebuggerUrl"]


def _snapshot(session: CdpSession) -> dict:
    return session.evaluate(
        """(() => ({
          url: location.href,
          title: document.title || '',
          body: (document.body?.innerText || '').slice(0, 12000),
          ready: document.readyState,
          hasLogin: !!document.querySelector('input[name="j_username"]') &&
                    !!document.querySelector('input[name="j_password"]'),
          hasBrowserChallenge: !!document.querySelector(
            'iframe[src*="recaptcha" i], iframe[src*="hcaptcha" i], .g-recaptcha, '
            + '.h-captcha, [data-sitekey], #cf-challenge-running, #challenge-form'
          ),
          hasReportMenu: [...document.querySelectorAll('a')].some(a =>
            ['Guest Ledger', 'Future Reservations', 'Future Reservation Report']
              .includes((a.textContent || '').trim()))
        }))()"""
    ) or {}


def _snapshot_signature(snapshot: dict) -> tuple:
    return (
        snapshot.get("url"),
        snapshot.get("title"),
        snapshot.get("ready"),
        str(snapshot.get("body") or "")[:2000],
    )


def _looks_like_browser_challenge(snapshot: dict) -> bool:
    if snapshot.get("hasBrowserChallenge"):
        return True
    url = str(snapshot.get("url") or "").casefold()
    text = f"{snapshot.get('title', '')}\n{snapshot.get('body', '')}".casefold()
    url_markers = ("/captcha", "/challenge", "/cdn-cgi/")
    text_markers = (
        "verify you are human",
        "unusual traffic",
        "automated queries",
        "checking your browser",
        "complete the captcha",
        "access denied",
        "temporarily blocked",
    )
    return any(marker in url for marker in url_markers) or any(
        marker in text for marker in text_markers
    )


def _raise_if_browser_challenge(snapshot: dict) -> None:
    if _looks_like_browser_challenge(snapshot):
        raise BrowserChallenge(
            "ChoiceADVANTAGE presented an access-verification challenge"
        )


def _wait_for_settle(
    session: CdpSession,
    timeout: float = 15.0,
    *,
    changed_from: dict | None = None,
) -> dict:
    deadline = time.monotonic() + timeout
    original = _snapshot_signature(changed_from) if changed_from is not None else None
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        current = _snapshot(session)
        _raise_if_browser_challenge(current)
        signature = _snapshot_signature(current)
        stable = stable + 1 if signature == previous else 0
        changed = original is None or signature != original
        if changed and current.get("ready") == "complete" and stable >= 1:
            return current
        previous = signature
        time.sleep(0.2)
    raise LoginError("browser page did not settle before the login timeout")


def _has_exact_control(session: CdpSession, label: str) -> bool:
    encoded = json.dumps(label.casefold())
    return bool(
        session.evaluate(
            f"""(() => [...document.querySelectorAll('a,button,input[type=button],input[type=submit]')]
              .some(node => ((node.textContent || node.value || '').trim().toLowerCase() === {encoded})))()"""
        )
    )


def _trusted_click_point(session: CdpSession, expression: str, failure: str) -> None:
    point = session.evaluate(expression)
    if not isinstance(point, dict) or not isinstance(point.get("x"), (int, float)) or not isinstance(
        point.get("y"), (int, float)
    ):
        raise LoginError(failure)
    session.trusted_click(float(point["x"]), float(point["y"]))


def _click_exact_control(session: CdpSession, label: str) -> None:
    encoded = json.dumps(label.casefold())
    _trusted_click_point(
        session,
        f"""(() => {{
          const node = [...document.querySelectorAll('a,button,input[type=button],input[type=submit]')]
            .find(item => ((item.textContent || item.value || '').trim().toLowerCase() === {encoded}));
          if (!node) return null;
          const rect = node.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return null;
          return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()""",
        f"expected {label!r} control is unavailable",
    )


def _has_traditional_login_continue(session: CdpSession) -> bool:
    return bool(
        session.evaluate(
            """(() => [...document.querySelectorAll('a')].some(node =>
              (node.textContent || '').trim() === 'Continue' &&
              /formSubmit/.test(node.getAttribute('onclick') || '')))()"""
        )
    )


def _click_traditional_login_continue(session: CdpSession) -> None:
    _trusted_click_point(
        session,
        """(() => {
          const node = [...document.querySelectorAll('a')].find(item =>
            (item.textContent || '').trim() === 'Continue' &&
            /formSubmit/.test(item.getAttribute('onclick') || ''));
          if (!node) return null;
          const rect = node.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return null;
          return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        })()""",
        "traditional-login Continue control is unavailable",
    )


def _click_login_control(session: CdpSession) -> None:
    _trusted_click_point(
        session,
        """(() => {
          const node = [...document.querySelectorAll('button,input[type=submit],a')]
            .find(item => /^(login|sign in)$/i.test(
              (item.textContent || item.value || '').trim()));
          if (!node) return null;
          const rect = node.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return null;
          return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        })()""",
        "ChoiceADVANTAGE login control was not found",
    )


def _is_authenticated(snapshot: dict) -> bool:
    return bool(
        snapshot.get("hasReportMenu")
        or "ReportViewStart.init" in str(snapshot.get("url") or "")
    )


def _looks_like_mfa(snapshot: dict) -> bool:
    text = f"{snapshot.get('title', '')}\n{snapshot.get('body', '')}".casefold()
    markers = (
        "multi-factor",
        "multifactor",
        "verification code",
        "authenticator",
        "one-time code",
        "skip mfa",
    )
    return any(marker in text for marker in markers)


def _browser_saved_fields_present(session: CdpSession) -> bool:
    """Check autofill presence without returning either credential value."""
    return bool(
        session.evaluate(
            """(() => {
              const username = document.querySelector('input[name="j_username"]');
              const password = document.querySelector('input[name="j_password"]');
              return !!username && !!password &&
                     username.value.length > 0 && password.value.length > 0;
            })()"""
        )
    )


def _finish_login(
    session: CdpSession,
    snapshot: dict,
    *,
    allow_skip_mfa: bool,
) -> dict[str, object]:
    # This is the account-migration deferral, not the temporary MFA skip.
    if _has_traditional_login_continue(session) and "migrat" in str(
        snapshot.get("body", "")
    ).casefold():
        before_click = snapshot
        _click_traditional_login_continue(session)
        snapshot = _wait_for_settle(session, changed_from=before_click)

    mfa_skipped = False
    if _has_exact_control(session, SKIP_MFA_LABEL):
        if not allow_skip_mfa:
            return {
                "status": "needs_mfa",
                "next_question": (
                    "ChoiceADVANTAGE is offering Skip MFA. May I select it for this "
                    "test login?"
                ),
            }
        _click_exact_control(session, SKIP_MFA_LABEL)
        mfa_skipped = True
        snapshot = _wait_for_settle(session, changed_from=snapshot)
    elif _looks_like_mfa(snapshot):
        return {
            "status": "needs_mfa",
            "next_question": (
                "Skip MFA is unavailable. Please complete MFA in the persistent "
                "browser, then retry the test."
            ),
        }

    session.navigate(REPORTS_URL)
    snapshot = _wait_for_settle(session)
    if not _is_authenticated(snapshot):
        raise LoginError("ChoiceADVANTAGE did not reach the report menu after login")
    return {
        "status": "authenticated",
        "session_reused": False,
        "mfa_skipped": mfa_skipped,
    }


def login_with_browser_saved_access(
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    allow_skip_mfa: bool = False,
    autofill_timeout: float = 15.0,
    interaction_delay: float = DEFAULT_INTERACTION_DELAY,
) -> dict[str, object]:
    """Login by clicking already-autofilled browser fields without reading them."""
    session = CdpSession(
        _page_websocket(cdp_url), interaction_delay=interaction_delay
    )
    try:
        session.call("Page.enable")
        session.navigate(REPORTS_URL)
        snapshot = _wait_for_settle(session)
        if _is_authenticated(snapshot):
            return {
                "status": "authenticated",
                "session_reused": True,
                "mfa_skipped": False,
                "browser_saved_access": True,
            }

        session.navigate(SIGN_IN_URL)
        snapshot = _wait_for_settle(session)
        if not snapshot.get("hasLogin"):
            raise LoginError("ChoiceADVANTAGE login fields were not found")
        deadline = time.monotonic() + autofill_timeout
        filled = False
        while time.monotonic() < deadline:
            filled = _browser_saved_fields_present(session)
            if filled:
                break
            time.sleep(0.2)
        if not filled:
            raise LoginError(
                "saved browser credentials did not autofill the ChoiceADVANTAGE login"
            )
        _click_login_control(session)
        snapshot = _wait_for_settle(session, changed_from=snapshot)
        result = _finish_login(
            session,
            snapshot,
            allow_skip_mfa=allow_skip_mfa,
        )
        result["browser_saved_access"] = True
        return result
    finally:
        session.close()


def login(
    access: dict[str, object],
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    allow_skip_mfa: bool = False,
    interaction_delay: float = DEFAULT_INTERACTION_DELAY,
) -> dict[str, object]:
    session = CdpSession(
        _page_websocket(cdp_url), interaction_delay=interaction_delay
    )
    try:
        session.call("Page.enable")
        session.navigate(REPORTS_URL)
        snapshot = _wait_for_settle(session)
        if _is_authenticated(snapshot):
            return {"status": "authenticated", "session_reused": True, "mfa_skipped": False}

        session.navigate(SIGN_IN_URL)
        snapshot = _wait_for_settle(session)
        if not snapshot.get("hasLogin"):
            raise LoginError("ChoiceADVANTAGE login fields were not found")
        values = json.dumps(
            {"username": access["username"], "password": access["password"]}
        )
        filled = session.evaluate(
            f"""(() => {{
              const values = {values};
              const username = document.querySelector('input[name="j_username"]');
              const password = document.querySelector('input[name="j_password"]');
              if (!username || !password) return false;
              for (const [node, value] of [[username, values.username], [password, values.password]]) {{
                node.value = value;
                node.dispatchEvent(new Event('input', {{bubbles: true}}));
                node.dispatchEvent(new Event('change', {{bubbles: true}}));
              }}
              return true;
            }})()""",
        )
        if not filled:
            raise LoginError("ChoiceADVANTAGE login fields could not be populated")
        _click_login_control(session)
        snapshot = _wait_for_settle(session, changed_from=snapshot)

        return _finish_login(
            session,
            snapshot,
            allow_skip_mfa=allow_skip_mfa,
        )
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", required=True)
    parser.add_argument("--test-access", action="store_true")
    parser.add_argument("--test-access-file", type=Path)
    parser.add_argument(
        "--allow-skip-mfa",
        action="store_true",
        help="select only the exact ChoiceADVANTAGE 'Skip MFA' control for this login",
    )
    parser.add_argument(
        "--cdp-url", default=os.environ.get("PMS_RECON_CDP_URL", DEFAULT_CDP_URL)
    )
    parser.add_argument(
        "--interaction-delay-seconds",
        type=float,
        default=DEFAULT_INTERACTION_DELAY,
        help="bounded delay between site-facing browser interactions",
    )
    args = parser.parse_args()
    if args.test_access_file and not args.test_access:
        parser.error("--test-access-file requires --test-access")
    if args.allow_skip_mfa and not args.test_access:
        parser.error("--allow-skip-mfa is available only with --test-access")
    if not 0.25 <= args.interaction_delay_seconds <= 3:
        parser.error("--interaction-delay-seconds must be between 0.25 and 3")
    try:
        access = resolve_access(
            args.hotel,
            allow_test_access=args.test_access,
            test_access_file=args.test_access_file,
        )
        result = login(
            access,
            cdp_url=args.cdp_url,
            allow_skip_mfa=args.allow_skip_mfa,
            interaction_delay=args.interaction_delay_seconds,
        )
    except BrowserChallenge as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "bot_challenge",
                    "error": str(exc),
                    "next_question": (
                        "Please complete the access verification in the persistent "
                        "browser. Is it ready for one new bounded login?"
                    ),
                },
                sort_keys=True,
            )
        )
        return 3
    except (AccessError, LoginError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 2
    result.update(
        {
            "property_code": access["property_code"],
            "timezone": access["timezone"],
            "access_source": access["source"],
            "test_only": access["test_only"],
        }
    )
    print(json.dumps(result, sort_keys=True))
    return 3 if result["status"] == "needs_mfa" else 0


if __name__ == "__main__":
    raise SystemExit(main())
