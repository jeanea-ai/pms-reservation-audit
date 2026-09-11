#!/usr/bin/env python3
"""Pull parser-grade ChoiceADVANTAGE reports with one bounded CDP workflow."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import time
import uuid

try:
    from scripts.one_shot_pdf import CaptureError, preserve_response
    from scripts.pms_login import (
        BrowserChallenge,
        CdpSession,
        DEFAULT_CDP_URL,
        DEFAULT_INTERACTION_DELAY,
        LoginError,
        REPORTS_URL,
        _page_websocket,
        _raise_if_browser_challenge,
        _snapshot,
    )
except ModuleNotFoundError:
    from one_shot_pdf import CaptureError, preserve_response
    from pms_login import (
        BrowserChallenge,
        CdpSession,
        DEFAULT_CDP_URL,
        DEFAULT_INTERACTION_DELAY,
        LoginError,
        REPORTS_URL,
        _page_websocket,
        _raise_if_browser_challenge,
        _snapshot,
    )


REPORT_PROXY_FRAGMENT = "/choicehotels/ReportProxyServlet.proxy"
CAPTURE_CHUNK_BYTES = 192 * 1024


class ReportPullError(RuntimeError):
    """Raised when a bounded report pull cannot safely continue."""


class AuthenticationRequired(ReportPullError):
    """Raised when the persistent browser is not authenticated."""


@dataclass(frozen=True)
class ReportSpec:
    key: str
    menu_id: str
    menu_label: str
    form_name: str
    filename: str


REPORTS = {
    "guest-ledger": ReportSpec(
        key="guest-ledger",
        menu_id="GuestLedgerReport",
        menu_label="Guest Ledger",
        form_name="genericReportsForm",
        filename="guest-ledger.pdf",
    ),
    "future-reservations": ReportSpec(
        key="future-reservations",
        menu_id="FutureReservationsReport",
        menu_label="Future Reservations",
        form_name="ReportFutureReservationsForm",
        filename="future-reservations.pdf",
    ),
}


def same_date_next_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def _browser_date(value: date) -> str:
    return f"{value.month}/{value.day}/{value.year}"


def _wait_for_value(
    session: CdpSession,
    expression: str,
    *,
    timeout: float,
    failure: str,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = session.evaluate(expression)
        if value:
            return value
        _raise_if_browser_challenge(_snapshot(session))
        time.sleep(0.2)
    raise ReportPullError(failure)


def _remaining_timeout(timeout: float, overall_deadline: float | None) -> float:
    if overall_deadline is None:
        return timeout
    remaining = overall_deadline - time.monotonic()
    if remaining <= 0:
        raise ReportPullError("overall audit deadline exceeded")
    return min(timeout, remaining)


def _open_report_form(
    session: CdpSession,
    spec: ReportSpec,
    timeout: float,
    *,
    overall_deadline: float | None = None,
) -> None:
    session.navigate(REPORTS_URL)
    encoded_id = json.dumps(spec.menu_id)
    encoded_label = json.dumps(spec.menu_label)
    menu_state = _wait_for_value(
        session,
        f"""(() => {{
          if (document.querySelector('input[name="j_username"]') ||
              document.querySelector('input[name="j_password"]')) return 'login';
          const node = document.getElementById({encoded_id});
          return !!node && (node.textContent || '').trim() === {encoded_label} ? 'ready' : '';
        }})()""",
        timeout=_remaining_timeout(timeout, overall_deadline),
        failure=f"{spec.menu_label} is unavailable on the reports menu",
    )
    if menu_state == "login":
        raise AuthenticationRequired("the persistent browser is not authenticated")
    session.pace()
    clicked = session.evaluate(
        f"""(() => {{
          const node = document.getElementById({encoded_id});
          if (!node || (node.textContent || '').trim() !== {encoded_label}) return false;
          node.click();
          return true;
        }})()""",
        user_gesture=True,
    )
    if not clicked:
        raise ReportPullError(f"could not open {spec.menu_label}")
    encoded_form = json.dumps(spec.form_name)
    _wait_for_value(
        session,
        f"""(() => !!document.querySelector(
          'form[name=' + CSS.escape({encoded_form}) + ']'
        ) && !!document.querySelector('#doSubmit'))()""",
        timeout=_remaining_timeout(timeout, overall_deadline),
        failure=f"{spec.menu_label} parameters did not load",
    )


def _prepare_form(session: CdpSession, spec: ReportSpec, local_day: date) -> dict:
    if spec.key == "guest-ledger":
        expression = """(() => {
          const form = document.querySelector(
            'form[name="genericReportsForm"][action*="ReportProxyServlet.proxy"]');
          const dateField = document.querySelector('input[name="queryDatePast"]');
          const submit = document.querySelector('#doSubmit');
          if (!form || !dateField || !dateField.value.trim() || !submit) return null;
          if ((submit.textContent || '').trim().toLowerCase() !== 'submit') return null;
          return {business_date: dateField.value.trim()};
        })()"""
    else:
        start = _browser_date(local_day)
        end = _browser_date(same_date_next_year(local_day))
        values = json.dumps(
            {
                "arrivalDateFrom": start,
                "arrivalDateTo": end,
                "bookingDateFrom": "",
                "bookingDateTo": "",
            }
        )
        expression = f"""(() => {{
          const form = document.querySelector('form[name="ReportFutureReservationsForm"]');
          const submit = document.querySelector('#doSubmit');
          if (!form || !submit) return null;
          if ((submit.textContent || '').trim().toLowerCase() !== 'submit') return null;
          const values = {values};
          for (const [name, value] of Object.entries(values)) {{
            const field = form.querySelector('input[name="' + name + '"]');
            if (!field) return null;
            field.value = value;
            field.dispatchEvent(new Event('input', {{bubbles: true}}));
            field.dispatchEvent(new Event('change', {{bubbles: true}}));
            if (field.value !== value) return null;
          }}
          const csv = form.querySelector('#CSVcheckbox');
          if (csv?.checked) csv.click();
          return {{arrival_from: values.arrivalDateFrom, arrival_to: values.arrivalDateTo}};
        }})()"""
    result = session.evaluate(expression)
    if not isinstance(result, dict):
        raise ReportPullError(f"{spec.menu_label} form contract did not match")
    return result


def _capture_submit(
    session: CdpSession,
    timeout: float,
    *,
    overall_deadline: float | None = None,
) -> bytes:
    started = session.evaluate(
        """(() => {
          if (window.__pmsAuditCapture) return {started: false, code: 'already_used'};
          const submit = document.querySelector('#doSubmit');
          if (!submit || (submit.textContent || '').trim().toLowerCase() !== 'submit') {
            return {started: false, code: 'submit_unavailable'};
          }
          const state = window.__pmsAuditCapture = {
            status: 'arming', bytes: null, contentType: '', responseUrl: '',
            httpStatus: 0, errorCode: ''
          };
          const nativeSubmit = HTMLFormElement.prototype.submit;
          let intercepted = false;
          const capture = form => {
            if (intercepted) return;
            intercepted = true;
            try {
              const method = (form.method || 'GET').toUpperCase();
              const params = new URLSearchParams(new FormData(form));
              let url = new URL(form.action, location.href);
              const options = {
                method, credentials: 'include', redirect: 'follow',
                headers: {'Accept': 'application/pdf'}
              };
              if (method === 'GET') {
                for (const [key, value] of params) url.searchParams.append(key, value);
              } else {
                options.body = params;
                options.headers['Content-Type'] =
                  'application/x-www-form-urlencoded;charset=UTF-8';
              }
              state.status = 'fetching';
              fetch(url.toString(), options).then(async response => {
                state.httpStatus = response.status;
                state.responseUrl = response.url;
                state.contentType = response.headers.get('content-type') || '';
                state.bytes = new Uint8Array(await response.arrayBuffer());
                state.status = 'ready';
              }).catch(() => {
                state.errorCode = 'fetch_failed';
                state.status = 'failed';
              });
            } catch (_) {
              state.errorCode = 'form_serialization_failed';
              state.status = 'failed';
            }
          };
          const onSubmit = event => {
            event.preventDefault();
            event.stopImmediatePropagation();
            capture(event.target);
          };
          HTMLFormElement.prototype.submit = function() { capture(this); };
          document.addEventListener('submit', onSubmit, true);
          try {
            submit.click();
          } finally {
            HTMLFormElement.prototype.submit = nativeSubmit;
            document.removeEventListener('submit', onSubmit, true);
          }
          if (!intercepted) {
            state.errorCode = 'submission_not_intercepted';
            state.status = 'failed';
          }
          return {started: intercepted, code: state.errorCode};
        })()""",
        user_gesture=True,
    )
    if not isinstance(started, dict) or not started.get("started"):
        code = started.get("code") if isinstance(started, dict) else "capture_not_started"
        raise ReportPullError(f"report capture did not start ({code})")

    deadline = time.monotonic() + _remaining_timeout(timeout, overall_deadline)
    state = None
    while time.monotonic() < deadline:
        state = session.evaluate(
            """(() => {
              const state = window.__pmsAuditCapture;
              if (!state) return null;
              return {
                status: state.status,
                length: state.bytes ? state.bytes.length : 0,
                contentType: state.contentType,
                responseUrl: state.responseUrl,
                httpStatus: state.httpStatus,
                errorCode: state.errorCode
              };
            })()"""
        )
        if isinstance(state, dict) and state.get("status") in {"ready", "failed"}:
            break
        time.sleep(0.2)
    if not isinstance(state, dict) or state.get("status") not in {"ready", "failed"}:
        raise ReportPullError("authenticated report request did not finish before timeout")
    if state.get("status") == "failed":
        raise ReportPullError(
            f"authenticated report request failed ({state.get('errorCode') or 'unknown'})"
        )
    http_status = int(state.get("httpStatus") or 0)
    if http_status in {403, 429}:
        raise BrowserChallenge(
            f"ChoiceADVANTAGE returned an access-control response (HTTP {http_status})"
        )
    if http_status != 200:
        raise ReportPullError(
            f"report endpoint returned HTTP {http_status}"
        )
    response_url = str(state.get("responseUrl") or "")
    if REPORT_PROXY_FRAGMENT not in response_url:
        raise ReportPullError("report request was redirected away from the report endpoint")
    length = int(state.get("length") or 0)
    if length <= 0:
        raise ReportPullError("report endpoint returned an empty response")

    chunks = []
    for offset in range(0, length, CAPTURE_CHUNK_BYTES):
        encoded = session.evaluate(
            f"""(() => {{
              const bytes = window.__pmsAuditCapture?.bytes;
              if (!bytes) return null;
              const chunk = bytes.subarray({offset}, {min(offset + CAPTURE_CHUNK_BYTES, length)});
              let binary = '';
              for (let index = 0; index < chunk.length; index += 1) {{
                binary += String.fromCharCode(chunk[index]);
              }}
              return btoa(binary);
            }})()"""
        )
        if not isinstance(encoded, str):
            raise ReportPullError("browser returned an invalid report chunk")
        try:
            chunks.append(base64.b64decode(encoded, validate=True))
        except ValueError as exc:
            raise ReportPullError("browser returned invalid base64 report data") from exc
    session.evaluate("(() => { delete window.__pmsAuditCapture; return true; })()")
    raw = b"".join(chunks)
    if len(raw) != length:
        raise ReportPullError("captured report length did not match browser response")
    if not raw.startswith(b"%PDF-"):
        raise ReportPullError("report response was not an original PDF")
    return raw


def pull_report(
    session: CdpSession,
    spec: ReportSpec,
    *,
    local_day: date,
    output: Path,
    state_file: Path,
    timeout: float,
    max_attempts: int = 2,
    overall_deadline: float | None = None,
) -> dict:
    if output.exists():
        raise ReportPullError(f"refusing to overwrite existing artifact: {output}")
    failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            _remaining_timeout(timeout, overall_deadline)
            _open_report_form(
                session,
                spec,
                timeout,
                overall_deadline=overall_deadline,
            )
            parameters = _prepare_form(session, spec, local_day)
            session.pace()
            response = _capture_submit(
                session,
                timeout,
                overall_deadline=overall_deadline,
            )
            record = preserve_response(
                response,
                output,
                state_file,
                f"{spec.key}:{uuid.uuid4().hex}",
                require_text=True,
            )
            return {
                "status": "saved",
                "report": spec.key,
                "artifact": record["artifact"],
                "sha256": record["sha256"],
                "bytes": record["bytes"],
                "attempts": attempt,
                "parameters": parameters,
            }
        except AuthenticationRequired:
            raise
        except BrowserChallenge:
            raise
        except (CaptureError, LoginError, ReportPullError) as exc:
            failures.append(str(exc))
    raise ReportPullError(
        f"{spec.menu_label} failed after {max_attempts} attempts: {failures[-1]}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", choices=[*REPORTS, "all"], default="all")
    parser.add_argument("--property-local-date", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--interaction-delay-seconds",
        type=float,
        default=DEFAULT_INTERACTION_DELAY,
    )
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be between 1 and 120")
    if not 0.25 <= args.interaction_delay_seconds <= 3:
        parser.error("--interaction-delay-seconds must be between 0.25 and 3")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_file = args.output_dir / "capture-state.json"
    keys = list(REPORTS) if args.report == "all" else [args.report]
    results = []
    session = None
    try:
        session = CdpSession(
            _page_websocket(args.cdp_url),
            interaction_delay=args.interaction_delay_seconds,
        )
        session.call("Page.enable")
        for key in keys:
            spec = REPORTS[key]
            results.append(
                pull_report(
                    session,
                    spec,
                    local_day=args.property_local_date,
                    output=args.output_dir / spec.filename,
                    state_file=state_file,
                    timeout=args.timeout_seconds,
                )
            )
    except BrowserChallenge as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": "bot_challenge",
                    "error": str(exc),
                    "next_question": (
                        "Please complete the ChoiceADVANTAGE access verification in the "
                        "persistent browser. Is it ready for one new bounded report pull?"
                    ),
                },
                sort_keys=True,
            )
        )
        return 3
    except (OSError, LoginError, ReportPullError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "next_question": (
                        "The deterministic report pull stopped. Should I preserve this "
                        "run for diagnosis and try one new bounded run later?"
                    ),
                },
                sort_keys=True,
            )
        )
        return 2
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
    print(json.dumps({"status": "ok", "reports": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
