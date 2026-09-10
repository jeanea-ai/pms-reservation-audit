#!/usr/bin/env python3
"""Render one explicitly marked test report from supplied, non-live audit input."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime
import fcntl
import io
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scripts.audit_pipeline import build_report_spec
    from scripts.audit_report import render_pdf_atomic
except ModuleNotFoundError:  # direct script execution
    from audit_pipeline import build_report_spec
    from audit_report import render_pdf_atomic


def run_test_report(input_path: Path, output_dir: Path, timezone_name: str) -> dict:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ValueError(f"unknown IANA timezone: {timezone_name}") from None

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("test input root must be a JSON object")
    payload = dict(payload)
    payload["test_mode"] = True

    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".scheduled-test.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "message": "a scheduled test report is already running"}

        stamp = datetime.now(zone).strftime("%Y%m%d-%H%M%S-%f")
        pdf_path = output_dir / f"TEST-ONLY-pms-reservation-audit-{stamp}.pdf"
        spec_path = output_dir / f"TEST-ONLY-pms-reservation-audit-{stamp}.json"
        spec, analysis = build_report_spec(payload)
        # The command-cron delivery contract is exactly one stdout line. The
        # renderer's progress messages are local diagnostics, not announcements.
        with redirect_stdout(io.StringIO()):
            render_pdf_atomic(spec, pdf_path)
        temporary_spec = spec_path.with_name(f".{spec_path.name}.tmp")
        temporary_spec.write_text(
            json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary_spec.replace(spec_path)

    result = {
        "status": "ok",
        "mode": "test_only",
        "complete": spec["complete"],
        "pdf": str(pdf_path),
        "spec": str(spec_path),
    }
    if analysis:
        result["duplicate_counts"] = analysis["counts"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a TEST ONLY report from a supplied audit-input JSON file."
    )
    parser.add_argument("--input", required=True, help="non-live audit_input.json")
    parser.add_argument("--output-dir", required=True, help="persistent output directory")
    parser.add_argument("--timezone", required=True, help="IANA timezone for output filenames")
    args = parser.parse_args()
    try:
        result = run_test_report(
            Path(args.input).expanduser().resolve(),
            Path(args.output_dir).expanduser().resolve(),
            args.timezone,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
