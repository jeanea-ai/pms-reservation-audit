#!/usr/bin/env python3
"""Deterministic Choice Connect / Okta / Google Voice SMS login."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from urllib.parse import urlparse

try:
    from scripts.gmail_otp import GmailOtpReader, OtpError
    from scripts.pms_login import (
        BrowserChallenge,
        CdpSession,
        LoginError,
        REPORTS_URL,
        _create_page_target,
        _is_authenticated,
        _list_page_targets,
        _snapshot,
        _wait_for_settle,
    )
except ModuleNotFoundError:
    from gmail_otp import GmailOtpReader, OtpError
    from pms_login import (
        BrowserChallenge,
        CdpSession,
        LoginError,
        REPORTS_URL,
        _create_page_target,
        _is_authenticated,
        _list_page_targets,
        _snapshot,
        _wait_for_settle,
    )


CONNECT_URL = "https://connect.choicehotels.com/login"
OKTA_HOST = "choicehotels.okta.com"
CONNECT_HOST = "connect.choicehotels.com"
ADVANTAGE_HOST = "www.choiceadvantage.com"
DEFAULT_LOGIN_TIMEOUT = 180.0


class OktaLoginError(LoginError):
    """Raised when Okta authentication cannot proceed deterministically."""


@dataclass(frozen=True)
class OktaSurface:
    kind: str
    url: str
    frame_id: str | None = None
    context_id: int | None = None
    session_id: str | None = None

    def evaluate(self, session: CdpSession, expression: str, *, user_gesture=False):
        return session.evaluate(
            expression,
            user_gesture=user_gesture,
            context_id=self.context_id,
            session_id=self.session_id,
        )


def _host(url: object) -> str:
    try:
        return (urlparse(str(url)).hostname or "").casefold()
    except ValueError:
        return ""


def _is_exact_host(url: object, expected: str) -> bool:
    try:
        parsed = urlparse(str(url))
        return (
            parsed.scheme.casefold() == "https"
            and (parsed.hostname or "").casefold() == expected
            and parsed.port in {None, 443}
        )
    except ValueError:
        return False


def _flatten_frame_tree(node: dict) -> list[dict]:
    frames = []
    frame = node.get("frame")
    if isinstance(frame, dict):
        frames.append(frame)
    for child in node.get("childFrames") or []:
        if isinstance(child, dict):
            frames.extend(_flatten_frame_tree(child))
    return frames


def discover_okta_surface(session: CdpSession) -> OktaSurface | None:
    """Find one exact-origin Okta DOM surface, including iframe and OOPIF cases."""
    tree = session.call("Page.getFrameTree").get("frameTree") or {}
    frames = _flatten_frame_tree(tree)
    root_id = str((tree.get("frame") or {}).get("id") or "")
    candidates: list[OktaSurface] = []

    attached = []
    for item in session.attached_targets():
        info = item.get("target_info") or {}
        url = str(info.get("url") or "")
        if info.get("type") in {"iframe", "page"} and _is_exact_host(url, OKTA_HOST):
            attached.append(
                OktaSurface(
                    "oopif",
                    url,
                    frame_id=str(info.get("targetId") or "") or None,
                    session_id=str(item.get("session_id") or "") or None,
                )
            )

    attached_ids = {surface.frame_id for surface in attached}
    candidates.extend(attached)
    for frame in frames:
        url = str(frame.get("url") or "")
        frame_id = str(frame.get("id") or "")
        if not _is_exact_host(url, OKTA_HOST) or frame_id in attached_ids:
            continue
        if frame_id == root_id:
            candidates.append(OktaSurface("top", url, frame_id=frame_id))
            continue
        try:
            context = session.call(
                "Page.createIsolatedWorld",
                {"frameId": frame_id, "worldName": "pms-okta-audit", "grantUniveralAccess": False},
            ).get("executionContextId")
        except LoginError:
            continue
        if isinstance(context, int):
            candidates.append(
                OktaSurface("iframe", url, frame_id=frame_id, context_id=context)
            )

    unique = {
        (item.kind, item.frame_id, item.context_id, item.session_id): item
        for item in candidates
    }
    if len(unique) > 1:
        raise OktaLoginError("multiple exact-origin Okta authentication surfaces were found")
    return next(iter(unique.values()), None)


def _surface_snapshot(session: CdpSession, surface: OktaSurface) -> dict:
    return surface.evaluate(
        session,
        """(() => {
          const text = node => (node.textContent || node.value || '').trim();
          const controls = [...document.querySelectorAll('button,a,input[type=submit],input[type=button]')]
            .map(text).filter(Boolean).slice(0, 80);
          return {
            url: location.href,
            title: document.title || '',
            body: (document.body?.innerText || '').slice(0, 5000),
            controls,
            hasUsername: !!document.querySelector(
              'input[type=email],input[name=identifier],input[name=username],input[name=login]'),
            hasPassword: !!document.querySelector('input[type=password]'),
            hasOtp: !!document.querySelector(
              'input[autocomplete="one-time-code"],input[name=passCode],input[name=answer],input[name=code]')
          };
        })()"""
    ) or {}


def _has_label(snapshot: dict, *labels: str) -> bool:
    available = {str(value).strip().casefold() for value in snapshot.get("controls") or []}
    return any(label.casefold() in available for label in labels)


def _click_exact(session: CdpSession, surface: OktaSurface, *labels: str) -> bool:
    encoded = json.dumps([label.casefold() for label in labels])
    session.pace()
    return bool(
        surface.evaluate(
            session,
            f"""(() => {{
              const labels = new Set({encoded});
              const node = [...document.querySelectorAll('button,a,input[type=submit],input[type=button]')]
                .find(item => labels.has((item.textContent || item.value || '').trim().toLowerCase()));
              if (!node || node.disabled) return false;
              node.click();
              return true;
            }})()""",
            user_gesture=True,
        )
    )


def _fill(session: CdpSession, surface: OktaSurface, kind: str, value: str) -> None:
    selectors = {
        "username": "input[type=email],input[name=identifier],input[name=username],input[name=login]",
        "password": "input[type=password]",
        "otp": 'input[autocomplete="one-time-code"],input[name=passCode],input[name=answer],input[name=code]',
    }
    selector = selectors[kind]
    payload = json.dumps(value)
    session.pace()
    filled = surface.evaluate(
        session,
        f"""(() => {{
          const node = document.querySelector({json.dumps(selector)});
          if (!node || node.disabled || node.readOnly) return false;
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(node, {payload});
          node.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText'}}));
          node.dispatchEvent(new Event('change', {{bubbles: true}}));
          return node.value.length > 0;
        }})()""",
        user_gesture=True,
    )
    if not filled:
        raise OktaLoginError(f"Okta {kind} field could not be populated")


def _top_click_exact(session: CdpSession, *labels: str) -> bool:
    surface = OktaSurface("top", "")
    return _click_exact(session, surface, *labels)


def _wait_for_okta(session: CdpSession, deadline: float) -> OktaSurface:
    while time.monotonic() < deadline:
        surface = discover_okta_surface(session)
        if surface:
            return surface
        time.sleep(0.25)
    raise OktaLoginError("Okta authentication surface did not load")


def _looks_like_challenge(snapshot: dict) -> bool:
    text = f"{snapshot.get('title', '')}\n{snapshot.get('body', '')}".casefold()
    return any(
        marker in text
        for marker in (
            "verify you are human",
            "complete the captcha",
            "unusual traffic",
            "access denied",
            "temporarily blocked",
        )
    )


def _wait_for_choice_destination(session: CdpSession, deadline: float) -> str:
    while time.monotonic() < deadline:
        snapshot = _snapshot(session)
        host = _host(snapshot.get("url"))
        if host == ADVANTAGE_HOST:
            return host
        if host == CONNECT_HOST:
            controls = session.evaluate(
                """(() => [...document.querySelectorAll('button,a,input[type=submit],input[type=button]')]
                  .map(node => (node.textContent || node.value || '').trim().toLowerCase())
                  .some(label => label === 'choice advantage' || label === 'choiceadvantage'))()"""
            )
            if controls:
                return host
        time.sleep(0.25)
    raise OktaLoginError("Okta did not return to an approved Choice destination")


def login_okta(
    access: dict[str, object],
    *,
    cdp_url: str,
    interaction_delay: float,
    timeout: float = DEFAULT_LOGIN_TIMEOUT,
    otp_reader: GmailOtpReader | None = None,
) -> dict[str, object]:
    """Authenticate through Okta using one bounded SMS request and a fresh Gmail OTP."""
    if access.get("okta_mfa") not in {"gv_sms", "email"}:
        raise OktaLoginError("Okta login requires supported PMS Setup MFA metadata")
    username = str(access.get("username") or "")
    password = str(access.get("password") or "")
    ops_email = str(access.get("ops_email") or "")
    if not password:
        raise OktaLoginError("Okta access is incomplete")

    deadline = time.monotonic() + timeout
    reader = otp_reader or GmailOtpReader(ops_email or None)
    if not username:
        try:
            username = reader.mailbox_email(deadline=deadline)
        except OtpError as exc:
            raise OktaLoginError(str(exc)) from exc
        ops_email = username
    elif not ops_email:
        try:
            mailbox = reader.verify_mailbox(deadline=deadline)
        except OtpError as exc:
            raise OktaLoginError(str(exc)) from exc
        if mailbox.casefold() != username.casefold():
            raise OktaLoginError("connected Gmail mailbox does not match Okta username")
        ops_email = username
    target = _create_page_target(cdp_url, CONNECT_URL)
    session = CdpSession(
        target["webSocketDebuggerUrl"], interaction_delay=interaction_delay
    )
    submitted_identity = False
    sms_requested = False
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
        )
        session.navigate(CONNECT_URL)
        _wait_for_settle(session, timeout=min(20, max(1, deadline - time.monotonic())))
        if not _top_click_exact(session, "Connect Now"):
            # An active SSO session may already have reached the dashboard.
            destination = _host((_snapshot(session) or {}).get("url"))
            if destination != CONNECT_HOST:
                raise OktaLoginError("Choice Connect login control was not found")
        else:
            _wait_for_okta(session, deadline)
            for _ in range(12):
                if time.monotonic() >= deadline:
                    break
                surface = discover_okta_surface(session)
                if surface is None:
                    current = _snapshot(session)
                    current_host = _host(current.get("url"))
                    if current_host == ADVANTAGE_HOST:
                        break
                    if current_host == CONNECT_HOST:
                        app_ready = session.evaluate(
                            """(() => [...document.querySelectorAll('button,a,input[type=submit],input[type=button]')]
                              .map(node => (node.textContent || node.value || '').trim().toLowerCase())
                              .some(label => label === 'choice advantage' || label === 'choiceadvantage'))()"""
                        )
                        if app_ready:
                            break
                    time.sleep(0.5)
                    continue
                snapshot = _surface_snapshot(session, surface)
                if _looks_like_challenge(snapshot):
                    raise BrowserChallenge("Okta presented an access-verification challenge")
                if snapshot.get("hasUsername"):
                    _fill(session, surface, "username", username)
                    if snapshot.get("hasPassword"):
                        _fill(session, surface, "password", password)
                        submitted_identity = True
                        if not _click_exact(session, surface, "Sign In", "Sign in"):
                            raise OktaLoginError("Okta Sign In control was not found")
                    elif not _click_exact(session, surface, "Next"):
                        raise OktaLoginError("Okta Next control was not found")
                    time.sleep(0.5)
                    continue
                if snapshot.get("hasPassword"):
                    _fill(session, surface, "password", password)
                    submitted_identity = True
                    if not _click_exact(session, surface, "Sign In", "Sign in"):
                        raise OktaLoginError("Okta Sign In control was not found")
                    time.sleep(0.5)
                    continue
                if _has_label(snapshot, "Verify with your phone"):
                    if not _click_exact(session, surface, "Verify with your phone"):
                        raise OktaLoginError("Okta phone verification control was not clickable")
                    time.sleep(0.5)
                    continue
                if _has_label(snapshot, "Receive a code via SMS", "Send me a code"):
                    if sms_requested:
                        raise OktaLoginError("Okta attempted to request more than one SMS code")
                    requested_after = time.time()
                    if not _click_exact(
                        session, surface, "Receive a code via SMS", "Send me a code"
                    ):
                        raise OktaLoginError("Okta SMS request control was not clickable")
                    sms_requested = True
                    time.sleep(0.5)
                    continue
                if snapshot.get("hasOtp"):
                    if not sms_requested:
                        raise OktaLoginError("Okta requested a code without this run sending SMS")
                    remaining = min(90.0, max(1.0, deadline - time.monotonic()))
                    try:
                        code = reader.wait_for_code(
                            after_epoch=requested_after,
                            timeout_seconds=remaining,
                        )
                    except OtpError as exc:
                        raise OktaLoginError(str(exc)) from exc
                    _fill(session, surface, "otp", code)
                    code = ""
                    if not _click_exact(session, surface, "Verify", "Submit"):
                        raise OktaLoginError("Okta verification control was not found")
                    break
                if _host(snapshot.get("url")) != OKTA_HOST:
                    break
                time.sleep(0.5)
            else:
                raise OktaLoginError("Okta authentication exceeded its bounded step count")

        destination = _wait_for_choice_destination(session, deadline)
        snapshot = _snapshot(session)
        if destination == CONNECT_HOST:
            if not submitted_identity:
                expected = json.dumps(ops_email.casefold())
                identity_matches = bool(
                    session.evaluate(
                        f"(document.body?.innerText || '').toLowerCase().includes({expected})"
                    )
                )
                if not identity_matches:
                    raise OktaLoginError(
                        "existing Choice Connect session identity could not be verified"
                    )
            before_targets = {
                str(item.get("id") or item.get("targetId") or "")
                for item in _list_page_targets(cdp_url)
            }
            if not _top_click_exact(session, "Choice Advantage", "ChoiceADVANTAGE"):
                raise OktaLoginError("Choice Advantage app control was not found")
            # Prefer same-tab launch; otherwise require one newly-created exact-origin page.
            for _ in range(80):
                current = _snapshot(session)
                if _is_exact_host(current.get("url"), ADVANTAGE_HOST):
                    break
                pages = [
                    item for item in _list_page_targets(cdp_url)
                    if _is_exact_host(item.get("url"), ADVANTAGE_HOST)
                    and str(item.get("id") or item.get("targetId") or "") not in before_targets
                ]
                if len(pages) == 1:
                    session.close()
                    session = CdpSession(
                        pages[0]["webSocketDebuggerUrl"],
                        interaction_delay=interaction_delay,
                    )
                    session.call("Page.enable")
                    break
                if len(pages) > 1:
                    raise OktaLoginError("Choice Advantage opened multiple new browser targets")
                time.sleep(0.25)
            else:
                raise OktaLoginError("Choice Advantage app did not open")

        session.navigate(REPORTS_URL)
        final = _wait_for_settle(
            session, timeout=min(20, max(1, deadline - time.monotonic()))
        )
        if not _is_authenticated(final):
            raise OktaLoginError("ChoiceADVANTAGE did not reach the report menu after Okta")
        return {
            "status": "authenticated",
            "session_reused": not submitted_identity,
            "mfa_skipped": False,
            "auth_mode": "okta_sso",
            "sms_requested": sms_requested,
        }
    finally:
        session.close()
