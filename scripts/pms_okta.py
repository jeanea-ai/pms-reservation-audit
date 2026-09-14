#!/usr/bin/env python3
"""Deterministic Choice Connect / Okta / Google Voice SMS login."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
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
CONNECT_HOME_URL = "https://connect.choicehotels.com/"
OKTA_HOST = "choicehotels.okta.com"
CONNECT_HOST = "connect.choicehotels.com"
ADVANTAGE_HOST = "www.choiceadvantage.com"
APPS_HOST = "apps.choicecentral.com"
DEFAULT_LOGIN_TIMEOUT = 180.0
TransitionRecorder = Callable[[dict[str, object]], None]


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


def _is_okta_auth_url(url: object) -> bool:
    """Return true for an Okta authentication document, not cleanup helpers."""
    if not _is_exact_host(url, OKTA_HOST):
        return False
    path = urlparse(str(url or "")).path.casefold()
    return not any(marker in path for marker in ("signout", "sign-out", "sign_out"))


def _navigation_state(url: object) -> str:
    raw = str(url or "")
    if raw in {"", "about:blank"}:
        return "loading"
    if _is_exact_host(raw, ADVANTAGE_HOST):
        return "choiceadvantage"
    if _is_exact_host(raw, CONNECT_HOST):
        return "choice_connect"
    if _is_exact_host(raw, OKTA_HOST):
        return "okta"
    if _is_exact_host(raw, APPS_HOST):
        return "choice_app_link"
    return "unapproved"


def _redacted_path(url: object) -> str:
    path = urlparse(str(url or "")).path.casefold()
    if "applink" in path:
        return "/appLinks/…"
    if "signout" in path or "sign-out" in path or "sign_out" in path:
        return "/signout/…"
    if "reportviewstart.init" in path:
        return "/…/ReportViewStart.init"
    for marker in ("verify", "signin", "login", "dashboard"):
        if marker in path:
            return f"/{marker}/…"
    return "/" if path in {"", "/"} else "/<redacted>"


def _record_transition(
    recorder: TransitionRecorder | None,
    event: str,
    url: object = None,
    **details: object,
) -> None:
    if recorder is None:
        return
    parsed = urlparse(str(url or ""))
    payload: dict[str, object] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    if parsed.scheme == "https" and parsed.hostname:
        payload["origin"] = f"https://{parsed.hostname.casefold()}"
        payload["path"] = _redacted_path(url)
    elif str(url or "") == "about:blank":
        payload["origin"] = "about:blank"
        payload["path"] = ""
    payload.update(details)
    recorder(payload)


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
    root_url = str((tree.get("frame") or {}).get("url") or "")
    # A top-level exact Okta document is authoritative. Okta may legitimately
    # embed a same-origin helper/signout iframe inside that page; it is not a
    # second login session and must not make the surface ambiguous.
    if _is_okta_auth_url(root_url):
        return OktaSurface("top", root_url, frame_id=root_id)
    candidates: list[OktaSurface] = []

    attached = []
    for item in session.attached_targets():
        info = item.get("target_info") or {}
        url = str(info.get("url") or "")
        if info.get("type") in {"iframe", "page"} and _is_okta_auth_url(url):
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
        if not _is_okta_auth_url(url) or frame_id in attached_ids:
            continue
        try:
            context = session.call(
                "Page.createIsolatedWorld",
                {
                    "frameId": frame_id,
                    "worldName": "pms-okta-audit",
                    "grantUniveralAccess": False,
                },
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
        raise OktaLoginError(
            "multiple exact-origin Okta authentication surfaces were found"
        )
    return next(iter(unique.values()), None)


def _surface_snapshot(session: CdpSession, surface: OktaSurface) -> dict:
    return (
        surface.evaluate(
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
        })()""",
        )
        or {}
    )


def _has_label(snapshot: dict, *labels: str) -> bool:
    available = {
        str(value).strip().casefold() for value in snapshot.get("controls") or []
    }
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


def _has_choice_advantage_app(session: CdpSession) -> bool:
    return bool(
        session.evaluate(
            """(() => [...document.querySelectorAll('button,a,input[type=submit],input[type=button]')]
              .map(node => (node.textContent || node.value || '').trim().toLowerCase())
              .some(label => label === 'choice advantage' || label === 'choiceadvantage'))()"""
        )
    )


def _click_choice_advantage_app(session: CdpSession) -> str:
    if _top_click_exact(session, "Choice Advantage", "ChoiceADVANTAGE"):
        return "exact_label"
    session.pace()
    clicked = session.evaluate(
        f"""(() => {{
          const matches = [...document.querySelectorAll('a[href]')].filter(node => {{
            try {{
              const url = new URL(node.href, location.href);
              return url.protocol === 'https:' &&
                     url.hostname.toLowerCase() === {json.dumps(APPS_HOST)} &&
                     url.pathname.toLowerCase().includes('applink');
            }} catch (_) {{ return false; }}
          }});
          if (matches.length !== 1) return false;
          matches[0].click();
          return true;
        }})()""",
        user_gesture=True,
    )
    if not clicked:
        raise OktaLoginError("Choice Advantage app control was not found")
    return "app_link"


def _target_id(target: dict) -> str:
    return str(target.get("id") or target.get("targetId") or "")


def _follow_choice_app_launch(
    session: CdpSession,
    *,
    cdp_url: str,
    before_targets: set[str],
    deadline: float,
    interaction_delay: float,
    recorder: TransitionRecorder | None = None,
    accept_connect_dashboard: bool = False,
) -> CdpSession:
    """Follow one same-tab or new-tab approved Choice SSO/appLinks route."""
    seen_targets = set(before_targets)
    last_transition = None
    stable_unapproved = 0
    stable_connect_login = 0
    connect_recovery_attempted = False
    reported_unapproved_targets: set[str] = set()
    while time.monotonic() < deadline:
        current = _snapshot(session)
        current_url = current.get("url")
        current_state = _navigation_state(current_url)
        signature = (current_state, str(urlparse(str(current_url or "")).path or ""))
        if signature != last_transition:
            _record_transition(
                recorder, "app_launch_transition", current_url, state=current_state
            )
            last_transition = signature
        if current_state == "choiceadvantage":
            return session
        has_choice_app = (
            accept_connect_dashboard
            and current_state == "choice_connect"
            and _has_choice_advantage_app(session)
        )
        if has_choice_app:
            return session
        stable_unapproved = (
            stable_unapproved + 1 if current_state == "unapproved" else 0
        )
        current_path = str(urlparse(str(current_url or "")).path or "").casefold()
        stable_connect_login = (
            stable_connect_login + 1
            if accept_connect_dashboard
            and current_state == "choice_connect"
            and current_path.rstrip("/") == "/login"
            and not has_choice_app
            else 0
        )

        new_pages = [
            item
            for item in _list_page_targets(cdp_url)
            if _target_id(item) and _target_id(item) not in seen_targets
        ]
        for item in new_pages:
            target_id = _target_id(item)
            if (
                _navigation_state(item.get("url")) == "unapproved"
                and target_id not in reported_unapproved_targets
            ):
                # Do not attach to or fail because of an unrelated browser tab.
                # Record its redacted presence once; only the active route can
                # become a stable unapproved final destination.
                reported_unapproved_targets.add(target_id)
                _record_transition(
                    recorder,
                    "unapproved_target_ignored",
                    item.get("url"),
                    state="unapproved",
                )
        ranked = {
            "choiceadvantage": 4,
            "choice_app_link": 3,
            "choice_connect": 2,
            "okta": 1,
        }
        approved = [
            (ranked.get(_navigation_state(item.get("url")), 0), item)
            for item in new_pages
            if ranked.get(_navigation_state(item.get("url")), 0)
        ]
        if approved:
            best_rank = max(rank for rank, _item in approved)
            best = [item for rank, item in approved if rank == best_rank]
            if len(best) != 1:
                raise OktaLoginError(
                    "Choice app launch opened ambiguous approved targets"
                )
            target = best[0]
            seen_targets.add(_target_id(target))
            _record_transition(
                recorder,
                "app_target_selected",
                target.get("url"),
                state=_navigation_state(target.get("url")),
            )
            session.close()
            session = CdpSession(
                target["webSocketDebuggerUrl"], interaction_delay=interaction_delay
            )
            session.call("Page.enable")
            session.call("Runtime.enable")
            continue
        # Some Choice SSO responses finish in an Okta signout helper iframe
        # while the top-level Connect document remains the inert /login shell.
        # Resolve the already-established Connect session once via its canonical
        # home URL. This is not an authentication retry and never guesses an
        # appLinks URL.
        if stable_connect_login >= 4:
            if connect_recovery_attempted:
                _record_transition(
                    recorder,
                    "connect_session_not_established",
                    current_url,
                    state="choice_connect",
                )
                raise OktaLoginError(
                    "Choice Connect returned to login after Okta authentication"
                )
            connect_recovery_attempted = True
            stable_connect_login = 0
            _record_transition(
                recorder,
                "connect_session_resolution_started",
                CONNECT_HOME_URL,
                state="choice_connect",
            )
            session.navigate(CONNECT_HOME_URL)
            _wait_for_settle(
                session,
                timeout=min(10, max(1, deadline - time.monotonic())),
            )
            continue
        if stable_unapproved >= 4:
            raise OktaLoginError(
                "Choice app launch reached a stable unapproved destination"
            )
        time.sleep(0.25)
    if accept_connect_dashboard:
        raise OktaLoginError("Okta did not return to an approved Choice destination")
    raise OktaLoginError("Choice Advantage app did not open")


def login_okta(
    access: dict[str, object],
    *,
    cdp_url: str,
    interaction_delay: float,
    timeout: float = DEFAULT_LOGIN_TIMEOUT,
    otp_reader: GmailOtpReader | None = None,
    transition_recorder: TransitionRecorder | None = None,
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
    _record_transition(transition_recorder, "login_target_created", CONNECT_URL)
    sso_targets = {_target_id(item) for item in _list_page_targets(cdp_url)}
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
        _record_transition(transition_recorder, "choice_connect_loaded", CONNECT_URL)
        if not _top_click_exact(session, "Connect Now"):
            # An active SSO session may already have reached the dashboard.
            destination = _host((_snapshot(session) or {}).get("url"))
            if destination != CONNECT_HOST:
                raise OktaLoginError("Choice Connect login control was not found")
        else:
            initial_surface = _wait_for_okta(session, deadline)
            _record_transition(
                transition_recorder,
                "okta_surface_selected",
                initial_surface.url,
                surface=initial_surface.kind,
            )
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
                    raise BrowserChallenge(
                        "Okta presented an access-verification challenge"
                    )
                if snapshot.get("hasUsername"):
                    _fill(session, surface, "username", username)
                    if snapshot.get("hasPassword"):
                        _fill(session, surface, "password", password)
                        submitted_identity = True
                        if not _click_exact(session, surface, "Sign In", "Sign in"):
                            raise OktaLoginError("Okta Sign In control was not found")
                        _record_transition(
                            transition_recorder,
                            "okta_identity_submitted",
                            snapshot.get("url"),
                        )
                    elif not _click_exact(session, surface, "Next"):
                        raise OktaLoginError("Okta Next control was not found")
                    time.sleep(0.5)
                    continue
                if snapshot.get("hasPassword"):
                    _fill(session, surface, "password", password)
                    submitted_identity = True
                    if not _click_exact(session, surface, "Sign In", "Sign in"):
                        raise OktaLoginError("Okta Sign In control was not found")
                    _record_transition(
                        transition_recorder,
                        "okta_identity_submitted",
                        snapshot.get("url"),
                    )
                    time.sleep(0.5)
                    continue
                if _has_label(snapshot, "Verify with your phone"):
                    if not _click_exact(session, surface, "Verify with your phone"):
                        raise OktaLoginError(
                            "Okta phone verification control was not clickable"
                        )
                    time.sleep(0.5)
                    continue
                if _has_label(snapshot, "Receive a code via SMS", "Send me a code"):
                    if sms_requested:
                        raise OktaLoginError(
                            "Okta attempted to request more than one SMS code"
                        )
                    requested_after = time.time()
                    if not _click_exact(
                        session, surface, "Receive a code via SMS", "Send me a code"
                    ):
                        raise OktaLoginError(
                            "Okta SMS request control was not clickable"
                        )
                    sms_requested = True
                    _record_transition(
                        transition_recorder,
                        "okta_sms_requested",
                        snapshot.get("url"),
                    )
                    time.sleep(0.5)
                    continue
                if snapshot.get("hasOtp"):
                    if not sms_requested:
                        raise OktaLoginError(
                            "Okta requested a code without this run sending SMS"
                        )
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
                    _record_transition(
                        transition_recorder,
                        "okta_otp_submitted",
                        snapshot.get("url"),
                    )
                    break
                if _host(snapshot.get("url")) != OKTA_HOST:
                    break
                time.sleep(0.5)
            else:
                raise OktaLoginError(
                    "Okta authentication exceeded its bounded step count"
                )

        session = _follow_choice_app_launch(
            session,
            cdp_url=cdp_url,
            before_targets=sso_targets,
            deadline=deadline,
            interaction_delay=interaction_delay,
            recorder=transition_recorder,
            accept_connect_dashboard=True,
        )
        snapshot = _snapshot(session)
        destination = _host(snapshot.get("url"))
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
            before_targets = {_target_id(item) for item in _list_page_targets(cdp_url)}
            launch_route = _click_choice_advantage_app(session)
            _record_transition(
                transition_recorder,
                "choice_app_launch_clicked",
                snapshot.get("url"),
                route=launch_route,
            )
            session = _follow_choice_app_launch(
                session,
                cdp_url=cdp_url,
                before_targets=before_targets,
                deadline=deadline,
                interaction_delay=interaction_delay,
                recorder=transition_recorder,
            )

        session.navigate(REPORTS_URL)
        final = _wait_for_settle(
            session, timeout=min(20, max(1, deadline - time.monotonic()))
        )
        if not _is_authenticated(final):
            raise OktaLoginError(
                "ChoiceADVANTAGE did not reach the report menu after Okta"
            )
        _record_transition(
            transition_recorder,
            "choice_reports_ready",
            final.get("url"),
            state="choiceadvantage",
        )
        return {
            "status": "authenticated",
            "session_reused": not submitted_identity,
            "mfa_skipped": False,
            "auth_mode": "okta_sso",
            "sms_requested": sms_requested,
        }
    except Exception as exc:
        try:
            failed_url = _snapshot(session).get("url")
        except Exception:
            failed_url = None
        _record_transition(
            transition_recorder,
            "login_failed",
            failed_url,
            error_type=type(exc).__name__,
        )
        raise
    finally:
        try:
            closing_url = _snapshot(session).get("url")
        except Exception:
            closing_url = None
        _record_transition(
            transition_recorder,
            "cdp_transport_closing",
            closing_url,
        )
        try:
            session.close()
        except Exception:
            pass
