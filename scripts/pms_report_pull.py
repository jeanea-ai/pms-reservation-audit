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
        CdpSession,
        DEFAULT_CDP_URL,
        LoginError,
        REPORTS_URL,
        _page_websocket,
    )
except ModuleNotFoundError:
    from one_shot_pdf import CaptureError, preserve_response
    from pms_login import (
        CdpSession,
        DEFAULT_CDP_URL,
        LoginError,
        REPORTS_URL,
        _page_websocket,
    )


REPORT_PROXY_FRAGMENT = "/choicehotels/ReportProxyServlet.proxy"


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
        time.sleep(0.2)
    raise ReportPullError(failure)


def _open_report_form(session: CdpSession, spec: ReportSpec, timeout: float) -> None:
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
        timeout=timeout,
        failure=f"{spec.menu_label} is unavailable on the reports menu",
    )
    if menu_state == "login":
        raise AuthenticationRequired("the persistent browser is not authenticated")
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
        timeout=timeout,
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
          form.target = '_self';
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
          form.target = '_self';
          return {{arrival_from: values.arrivalDateFrom, arrival_to: values.arrivalDateTo}};
        }})()"""
    result = session.evaluate(expression)
    if not isinstance(result, dict):
        raise ReportPullError(f"{spec.menu_label} form contract did not match")
    return result


def _decode_body(result: dict) -> bytes:
    body = result.get("body")
    if not isinstance(body, str):
        raise ReportPullError("browser returned an invalid report body")
    if result.get("base64Encoded"):
        try:
            return base64.b64decode(body, validate=True)
        except ValueError as exc:
            raise ReportPullError("browser returned invalid base64 report data") from exc
    return body.encode("latin-1")


def _capture_submit(session: CdpSession, timeout: float) -> bytes:
    session.call(
        "Network.enable",
        {
            "maxTotalBufferSize": 100_000_000,
            "maxResourceBufferSize": 100_000_000,
        },
    )
    session.clear_events()
    clicked = session.evaluate(
        """(() => {
          if (window.__pmsAuditSubmitUsed) return false;
          const submit = document.querySelector('#doSubmit');
          if (!submit || (submit.textContent || '').trim().toLowerCase() !== 'submit') {
            return false;
          }
          window.__pmsAuditSubmitUsed = true;
          submit.click();
          return true;
        })()""",
        user_gesture=True,
    )
    if not clicked:
        raise ReportPullError("report Submit was unavailable or already used")

    deadline = time.monotonic() + timeout
    candidates: dict[str, dict] = {}
    non_pdf_received = False
    while time.monotonic() < deadline:
        event = session.next_event(max(0.01, deadline - time.monotonic()))
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Network.responseReceived":
            response = params.get("response") or {}
            url = str(response.get("url") or "")
            mime = str(response.get("mimeType") or "").casefold()
            if REPORT_PROXY_FRAGMENT in url or mime == "application/pdf":
                candidates[str(params.get("requestId"))] = response
        elif method == "Network.loadingFinished":
            request_id = str(params.get("requestId"))
            if request_id not in candidates:
                continue
            try:
                raw = _decode_body(
                    session.call("Network.getResponseBody", {"requestId": request_id})
                )
            except (LoginError, ReportPullError):
                continue
            if raw.startswith(b"%PDF-"):
                return raw
            non_pdf_received = True
        elif method == "Network.loadingFailed":
            candidates.pop(str(params.get("requestId")), None)

    if non_pdf_received:
        raise ReportPullError("report response was not an original PDF")
    raise ReportPullError("no completed ChoiceADVANTAGE PDF response was captured")


def pull_report(
    session: CdpSession,
    spec: ReportSpec,
    *,
    local_day: date,
    output: Path,
    state_file: Path,
    timeout: float,
    max_attempts: int = 2,
) -> dict:
    if output.exists():
        raise ReportPullError(f"refusing to overwrite existing artifact: {output}")
    failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            _open_report_form(session, spec, timeout)
            parameters = _prepare_form(session, spec, local_day)
            response = _capture_submit(session, timeout)
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
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be between 1 and 120")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_file = args.output_dir / "capture-state.json"
    keys = list(REPORTS) if args.report == "all" else [args.report]
    results = []
    session = None
    try:
        session = CdpSession(_page_websocket(args.cdp_url))
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
            session.close()
    print(json.dumps({"status": "ok", "reports": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
