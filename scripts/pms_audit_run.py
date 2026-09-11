#!/usr/bin/env python3
"""Run one bounded ChoiceADVANTAGE acquisition and deterministic audit."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scripts.audit_pipeline import build_report_spec
    from scripts.audit_report import render_pdf_atomic
    from scripts.pms_access import AccessError, CODE_RE, resolve_access
    from scripts.pms_login import (
        BrowserChallenge,
        CdpSession,
        DEFAULT_CDP_URL,
        DEFAULT_INTERACTION_DELAY,
        LoginError,
        REPORTS_URL,
        _page_websocket,
        login,
        login_with_browser_saved_access,
    )
    from scripts.pms_report_pull import REPORTS, ReportPullError, pull_report
    from scripts.source_to_input import build_audit_input, read_report_text
except ModuleNotFoundError:
    from audit_pipeline import build_report_spec
    from audit_report import render_pdf_atomic
    from pms_access import AccessError, CODE_RE, resolve_access
    from pms_login import (
        BrowserChallenge,
        CdpSession,
        DEFAULT_CDP_URL,
        DEFAULT_INTERACTION_DELAY,
        LoginError,
        REPORTS_URL,
        _page_websocket,
        login,
        login_with_browser_saved_access,
    )
    from pms_report_pull import REPORTS, ReportPullError, pull_report
    from source_to_input import build_audit_input, read_report_text


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _new_run_dir(root: Path, now: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stem = now.strftime("run-%Y%m%dT%H%M%S%z")
    candidate = root / stem
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = root / f"{stem}-{suffix}"
    candidate.mkdir()
    return candidate


def _acquire_run_lock(root: Path, hotel: str):
    """Hold one stable lock per property across manual and scheduled runs."""
    root.mkdir(parents=True, exist_ok=True)
    normalized = hotel.strip().upper()
    if not CODE_RE.fullmatch(normalized):
        raise ValueError("hotel code must be 2-24 letters, digits, dash, or underscore")
    lock_path = root / f".pms-audit-{normalized}.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(
            f"another {normalized} audit is already using this output root"
        ) from None
    return handle


def _failure(
    error: str,
    run_dir: Path | None = None,
    *,
    source_failures: dict[str, str] | None = None,
    error_code: str | None = None,
    next_question: str | None = None,
) -> dict:
    payload = {
        "status": "failed",
        "error": error,
        "next_question": next_question or (
            "The bounded audit stopped without retrying indefinitely. Should I preserve "
            "this run for diagnosis and try one new bounded run later?"
        ),
    }
    if error_code:
        payload["error_code"] = error_code
    if run_dir is not None:
        payload["run_dir"] = str(run_dir)
    if source_failures:
        payload["source_failures"] = source_failures
    return payload


def _redacted_summary(payload: dict, analysis: dict | None) -> dict:
    """Return aggregate-only findings that are safe to repeat outside the PDF."""
    summary: dict[str, int | float] = {}
    ledger = payload.get("guest_ledger")
    if isinstance(ledger, dict):
        balances = ledger.get("balances")
        groups = ledger.get("groups")
        if isinstance(balances, list):
            summary["ledger_balance_accounts"] = len(balances)
            summary["ledger_balance_total"] = round(
                sum(
                    float(item.get("balance"))
                    for item in balances
                    if isinstance(item, dict)
                    and isinstance(item.get("balance"), (int, float))
                ),
                2,
            )
        if isinstance(groups, list):
            summary["group_accounts"] = len(groups)
            summary["group_balance_total"] = round(
                sum(
                    float(item.get("balance"))
                    for item in groups
                    if isinstance(item, dict)
                    and isinstance(item.get("balance"), (int, float))
                ),
                2,
            )
    if isinstance(analysis, dict):
        counts = analysis.get("counts")
        if isinstance(counts, dict):
            summary["future_reservations_input"] = int(counts.get("input") or 0)
            summary["future_reservations_unique"] = int(counts.get("unique") or 0)
        window = (analysis.get("windows") or {}).get("future_12_months")
        if isinstance(window, dict):
            summary["duplicate_groups"] = int(window.get("groups_found") or 0)
            summary["duplicate_rooms"] = int(window.get("rooms_total") or 0)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--timezone",
        help="IANA timezone; required with --session-only or --browser-saved-login",
    )
    parser.add_argument("--session-only", action="store_true")
    parser.add_argument("--browser-saved-login", action="store_true")
    parser.add_argument("--test-access", action="store_true")
    parser.add_argument("--test-access-file", type=Path)
    parser.add_argument("--allow-skip-mfa", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--interaction-delay-seconds",
        type=float,
        default=DEFAULT_INTERACTION_DELAY,
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    if args.session_only and (
        args.browser_saved_login
        or args.test_access
        or args.test_access_file
        or args.allow_skip_mfa
    ):
        parser.error("--session-only cannot be combined with login or MFA options")
    if args.browser_saved_login and (args.test_access or args.test_access_file):
        parser.error("--browser-saved-login cannot be combined with test-access inputs")
    if (args.session_only or args.browser_saved_login) and not args.timezone:
        parser.error("--timezone is required with browser-only access modes")
    if args.test_access_file and not args.test_access:
        parser.error("--test-access-file requires --test-access")
    if args.allow_skip_mfa and not (args.test_access or args.browser_saved_login):
        parser.error("--allow-skip-mfa requires an explicit test login mode")
    if not 1 <= args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be between 1 and 120")
    if not 0.25 <= args.interaction_delay_seconds <= 3:
        parser.error("--interaction-delay-seconds must be between 0.25 and 3")

    run_dir = None
    session = None
    run_lock = None
    try:
        run_lock = _acquire_run_lock(args.output_root, args.hotel)
        access = None
        timezone_name = args.timezone
        if args.browser_saved_login:
            login_result = login_with_browser_saved_access(
                cdp_url=args.cdp_url,
                allow_skip_mfa=args.allow_skip_mfa,
                interaction_delay=args.interaction_delay_seconds,
            )
            if login_result["status"] != "authenticated":
                print(json.dumps(login_result, sort_keys=True))
                return 3
        elif not args.session_only:
            access = resolve_access(
                args.hotel,
                allow_test_access=args.test_access,
                test_access_file=args.test_access_file,
            )
            timezone_name = str(access["timezone"])
            login_result = login(
                access,
                cdp_url=args.cdp_url,
                allow_skip_mfa=args.allow_skip_mfa,
                interaction_delay=args.interaction_delay_seconds,
            )
            if login_result["status"] != "authenticated":
                print(json.dumps(login_result, sort_keys=True))
                return 3
        try:
            timezone = ZoneInfo(str(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ValueError("property timezone is not a valid IANA timezone") from exc
        now = datetime.now(timezone)
        local_day = now.date()
        run_dir = _new_run_dir(args.output_root, now)
        state_file = run_dir / "capture-state.json"

        session = CdpSession(
            _page_websocket(args.cdp_url),
            interaction_delay=args.interaction_delay_seconds,
        )
        session.call("Page.enable")
        acquisition: dict[str, dict] = {}
        failures: dict[str, str] = {}
        for key, spec in REPORTS.items():
            try:
                acquisition[key] = pull_report(
                    session,
                    spec,
                    local_day=local_day,
                    output=run_dir / spec.filename,
                    state_file=state_file,
                    timeout=args.timeout_seconds,
                )
            except BrowserChallenge:
                raise
            except (LoginError, ReportPullError) as exc:
                failures[key] = str(exc)

        if not acquisition:
            print(
                json.dumps(
                    _failure(
                        "neither required source report was captured",
                        run_dir,
                        source_failures=failures,
                    ),
                    sort_keys=True,
                )
            )
            return 2

        guest_path = run_dir / REPORTS["guest-ledger"].filename
        future_path = run_dir / REPORTS["future-reservations"].filename
        payload = build_audit_input(
            guest_ledger_text=read_report_text(str(guest_path)) if guest_path.exists() else None,
            future_reservation_texts=(
                [read_report_text(str(future_path))] if future_path.exists() else []
            ),
            property_local_date=local_day.isoformat(),
            reviewed_at=f"{now:%Y-%m-%d %H:%M} {timezone_name}",
            expected_features=["guest_ledger", "duplicates"],
        )
        test_only = bool(
            args.test_access or args.session_only or args.browser_saved_login
        )
        payload["test_mode"] = test_only
        input_path = run_dir / "audit-input.json"
        _atomic_json(input_path, payload)
        spec, analysis = build_report_spec(payload)
        spec_path = run_dir / "report-spec.json"
        _atomic_json(spec_path, spec)
        report_path = run_dir / f"PMS Reconciliation {args.hotel.upper()}.pdf"
        render_pdf_atomic(spec, report_path)
        result = {
            "status": "ok",
            "complete": payload["complete"],
            "test_only": test_only,
            "property_code": args.hotel.upper(),
            "run_dir": str(run_dir),
            "report": str(report_path),
            "sources": acquisition,
            "source_failures": failures,
            "summary": _redacted_summary(payload, analysis),
            "next_question": payload.get("next_question"),
        }
        if analysis:
            result["duplicate_counts"] = analysis["counts"]
        print(json.dumps(result, sort_keys=True))
        return 0
    except BrowserChallenge as exc:
        print(
            json.dumps(
                _failure(
                    str(exc),
                    run_dir,
                    error_code="bot_challenge",
                    next_question=(
                        "Please complete the ChoiceADVANTAGE access verification in the "
                        "persistent browser. Is it ready for one new bounded audit?"
                    ),
                ),
                sort_keys=True,
            )
        )
        return 3
    except (
        AccessError,
        LoginError,
        ReportPullError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(json.dumps(_failure(str(exc), run_dir), sort_keys=True))
        return 2
    finally:
        if session is not None:
            # Leave the shared acquisition tab at a deterministic starting page.
            # The final audit PDF is written to disk and is never opened here.
            try:
                session.navigate(REPORTS_URL)
            except Exception:
                pass
            try:
                session.close()
            except Exception:
                pass
        if run_lock is not None:
            run_lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
