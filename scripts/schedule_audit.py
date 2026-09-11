#!/usr/bin/env python3
"""Create one deterministic OpenClaw command cron for PMS Reconciliation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scripts.pms_access import CODE_RE, resolve_access
except ModuleNotFoundError:
    from pms_access import CODE_RE, resolve_access


REQUIRED_CREATE_FLAGS = (
    "--command-argv",
    "--command-cwd",
    "--timeout-seconds",
    "--no-output-timeout-seconds",
)
EVERY_RE = re.compile(r"^[1-9][0-9]*(?:ms|s|m|h|d|w)$")


def _run(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError(f"unknown IANA timezone: {value}") from None
    return value


def _cron_expression(value: str) -> str:
    if any(character in value for character in "\r\n\x00"):
        raise ValueError("cron expression must be one line")
    fields = value.split()
    if len(fields) not in {5, 6}:
        raise ValueError("cron expression must contain five or six fields")
    return " ".join(fields)


def _job_name(value: str | None, hotel: str) -> str:
    name = value.strip() if isinstance(value, str) else f"PMS Reconciliation {hotel}"
    if not name or len(name) > 120 or any(character in name for character in "\r\n\x00"):
        raise ValueError("name must be 1-120 characters on one line")
    return name


def _existing_job(openclaw: str, name: str) -> dict | None:
    listed = _run([openclaw, "cron", "list", "--json", "--all"])
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "unknown error").strip()
        raise RuntimeError(f"could not inspect existing cron jobs: {detail[:300]}")
    try:
        payload = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cron list did not return valid JSON") from exc
    if isinstance(payload, dict):
        jobs = payload.get("jobs", [])
    elif isinstance(payload, list):
        jobs = payload
    else:
        jobs = []
    return next(
        (job for job in jobs if isinstance(job, dict) and job.get("name") == name),
        None,
    )


def _validate_production_access(hotel: str) -> None:
    access = resolve_access(hotel)
    if access.get("source") != "mf-hotel-pms-setup" or access.get("test_only") is not False:
        raise RuntimeError("scheduled audits require production access from mf-hotel-pms-setup")


def build_create_command(
    args: argparse.Namespace,
    *,
    openclaw: str,
    python: str,
    skill_dir: Path,
) -> list[str]:
    hotel = args.hotel.strip().upper()
    name = _job_name(args.name, hotel)
    output_root = Path(args.output_root).expanduser().resolve()
    if args.announce_to:
        runner_argv = [
            python,
            str(skill_dir / "scripts" / "scheduled_audit_run.py"),
            "--hotel",
            hotel,
            "--audit-timeout-seconds",
            str(args.audit_timeout_seconds),
            "--overall-timeout-seconds",
            str(args.overall_timeout_seconds),
            "--output-root",
            str(output_root),
            "--delivery-to",
            args.announce_to,
        ]
    else:
        runner_argv = [
            python,
            str(skill_dir / "scripts" / "pms_audit_run.py"),
            "--hotel",
            hotel,
            "--timeout-seconds",
            str(args.audit_timeout_seconds),
            "--overall-timeout-seconds",
            str(args.overall_timeout_seconds),
            "--output-root",
            str(output_root),
        ]
    command = [openclaw, "cron", "create", "--name", name]
    if args.cron:
        command.extend(["--cron", _cron_expression(args.cron), "--tz", args.timezone])
        if args.exact:
            command.append("--exact")
    else:
        command.extend(["--every", args.every])
    command.extend(
        [
            "--command-argv",
            json.dumps(runner_argv),
            "--command-cwd",
            str(skill_dir),
            "--timeout-seconds",
            str(args.job_timeout_seconds),
            "--no-output-timeout-seconds",
            str(args.job_timeout_seconds),
            "--output-max-bytes",
            "16384",
            "--json",
        ]
    )
    if args.disabled:
        command.append("--disabled")
    if args.announce_to:
        command.extend(["--announce", "--channel", "kolo", "--to", args.announce_to])
    else:
        command.append("--no-deliver")
    return command


def create_schedule(args: argparse.Namespace) -> dict:
    skill_dir = Path(__file__).resolve().parent.parent
    hotel = args.hotel.strip().upper()
    if not CODE_RE.fullmatch(hotel):
        raise ValueError("hotel code must be 2-24 letters, digits, dash, or underscore")
    name = _job_name(args.name, hotel)
    output_root = Path(args.output_root).expanduser().resolve()
    if output_root == skill_dir or skill_dir in output_root.parents:
        raise ValueError("output-root must be persistent storage outside the installed skill")
    if args.cron:
        _cron_expression(args.cron)
        if not args.timezone:
            raise ValueError("--timezone is required with --cron")
        _timezone(args.timezone)
    else:
        if not EVERY_RE.fullmatch(args.every or ""):
            raise ValueError("--every must be a positive interval such as 30m, 6h, or 1d")
        if args.timezone:
            raise ValueError("--timezone applies to --cron; interval schedules have no clock zone")
        if args.exact:
            raise ValueError("--exact applies only to --cron")
    if not 1 <= args.audit_timeout_seconds <= 120:
        raise ValueError("audit-timeout-seconds must be between 1 and 120")
    if not 120 <= args.job_timeout_seconds <= 3600:
        raise ValueError("job-timeout-seconds must be between 120 and 3600")
    if not 120 <= args.overall_timeout_seconds <= 1800:
        raise ValueError("overall-timeout-seconds must be between 120 and 1800")
    if args.job_timeout_seconds < args.overall_timeout_seconds + 60:
        raise ValueError("job-timeout-seconds must exceed the audit deadline by at least 60")
    if args.announce_to and not re.fullmatch(r"kolo:[A-Za-z0-9._:-]+", args.announce_to):
        raise ValueError("announce-to must be an explicit kolo:<chat-id> destination")

    openclaw = shutil.which("openclaw") or "openclaw"
    command = build_create_command(
        args,
        openclaw=openclaw,
        python=sys.executable,
        skill_dir=skill_dir,
    )
    if args.dry_run:
        return {
            "status": "dry_run",
            "name": name,
            "hotel": hotel,
            "command_argv": command,
        }
    _validate_production_access(hotel)
    if not shutil.which("openclaw"):
        raise RuntimeError("openclaw command not found; run this scheduler on the Kolo pod")

    help_result = _run([openclaw, "cron", "create", "--help"])
    help_text = (help_result.stdout or "") + (help_result.stderr or "")
    required = [*REQUIRED_CREATE_FLAGS, "--cron" if args.cron else "--every"]
    required.append("--announce" if args.announce_to else "--no-deliver")
    if args.disabled:
        required.append("--disabled")
    if args.exact:
        required.append("--exact")
    missing = [flag for flag in required if flag not in help_text]
    if help_result.returncode != 0 or missing:
        raise RuntimeError(
            "installed OpenClaw cron command lacks required command-job flags: "
            + ", ".join(missing or ["help unavailable"])
        )

    existing = _existing_job(openclaw, name)
    if existing:
        return {
            "status": "exists",
            "job_id": existing.get("id"),
            "name": name,
            "next_question": (
                f"A schedule named {name!r} already exists, so nothing changed. "
                "Should I leave it alone or replace it with the newly requested frequency?"
            ),
        }

    created = _run(command, timeout=30)
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "unknown error").strip()
        raise RuntimeError(f"cron creation failed: {detail[:300]}")
    try:
        result = json.loads(created.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cron creation succeeded but did not return valid JSON") from exc
    return {"status": "created", "name": name, "hotel": hotel, "job": result}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--hotel", required=True)
    schedule = result.add_mutually_exclusive_group(required=True)
    schedule.add_argument("--cron", help="five- or six-field local-clock expression")
    schedule.add_argument("--every", help="fixed interval such as 30m, 6h, or 1d")
    result.add_argument("--timezone", help="IANA timezone; required with --cron")
    result.add_argument("--output-root", required=True)
    result.add_argument("--name")
    result.add_argument("--audit-timeout-seconds", type=int, default=90)
    result.add_argument("--overall-timeout-seconds", type=int, default=480)
    result.add_argument("--job-timeout-seconds", type=int, default=900)
    result.add_argument("--announce-to", help="exact Kolo destination: kolo:<chat-id>")
    result.add_argument("--exact", action="store_true", help="disable cron staggering")
    result.add_argument("--disabled", action="store_true", help="create disabled for inspection")
    result.add_argument("--dry-run", action="store_true", help="preview argv without mutation")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(create_schedule(args), sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
