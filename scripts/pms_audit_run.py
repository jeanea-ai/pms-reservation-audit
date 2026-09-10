#!/usr/bin/env python3
"""Run one bounded ChoiceADVANTAGE acquisition and deterministic audit."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scripts.audit_pipeline import build_report_spec
    from scripts.audit_report import render_pdf_atomic
    from scripts.pms_access import AccessError, resolve_access
    from scripts.pms_login import (
        CdpSession,
        DEFAULT_CDP_URL,
        LoginError,
        _page_websocket,
        login,
    )
    from scripts.pms_report_pull import REPORTS, ReportPullError, pull_report
    from scripts.source_to_input import build_audit_input, read_report_text
except ModuleNotFoundError:
    from audit_pipeline import build_report_spec
    from audit_report import render_pdf_atomic
    from pms_access import AccessError, resolve_access
    from pms_login import (
        CdpSession,
        DEFAULT_CDP_URL,
        LoginError,
        _page_websocket,
        login,
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


def _failure(error: str, run_dir: Path | None = None) -> dict:
    payload = {
        "status": "failed",
        "error": error,
        "next_question": (
            "The bounded audit stopped without retrying indefinitely. Should I preserve "
            "this run for diagnosis and try one new bounded run later?"
        ),
    }
    if run_dir is not None:
        payload["run_dir"] = str(run_dir)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--timezone", help="IANA timezone; required with --session-only")
    parser.add_argument("--session-only", action="store_true")
    parser.add_argument("--test-access", action="store_true")
    parser.add_argument("--test-access-file", type=Path)
    parser.add_argument("--allow-skip-mfa", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    if args.session_only and (args.test_access or args.test_access_file or args.allow_skip_mfa):
        parser.error("--session-only cannot be combined with login or MFA options")
    if args.session_only and not args.timezone:
        parser.error("--timezone is required with --session-only")
    if args.test_access_file and not args.test_access:
        parser.error("--test-access-file requires --test-access")
    if args.allow_skip_mfa and not args.test_access:
        parser.error("--allow-skip-mfa requires --test-access")
    if not 1 <= args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be between 1 and 120")

    run_dir = None
    session = None
    try:
        access = None
        timezone_name = args.timezone
        if not args.session_only:
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

        session = CdpSession(_page_websocket(args.cdp_url))
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
            except (LoginError, ReportPullError) as exc:
                failures[key] = str(exc)

        if not acquisition:
            raise ReportPullError("neither required source report was captured")

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
        test_only = bool(args.test_access or args.session_only)
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
            "next_question": payload.get("next_question"),
        }
        if analysis:
            result["duplicate_counts"] = analysis["counts"]
        print(json.dumps(result, sort_keys=True))
        return 0
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
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
