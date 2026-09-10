#!/usr/bin/env python3
"""Safely create a command-based OpenClaw cron for test-only report rendering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REQUIRED_CREATE_FLAGS = ("--command-argv", "--command-cwd", "--timeout-seconds")


def _run(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        raise ValueError(f"unknown IANA timezone: {value}") from None
    return value


def build_create_command(args, *, openclaw: str, python: str, skill_dir: Path) -> list[str]:
    runner_argv = [
        python,
        str(skill_dir / "scripts" / "scheduled_test_run.py"),
        "--input", str(Path(args.input).expanduser().resolve()),
        "--output-dir", str(Path(args.output_dir).expanduser().resolve()),
        "--timezone", args.timezone,
    ]
    command = [
        openclaw, "cron", "create",
        "--name", args.name,
        "--cron", args.cron,
        "--tz", args.timezone,
        "--command-argv", json.dumps(runner_argv),
        "--command-cwd", str(skill_dir),
        "--timeout-seconds", str(args.timeout_seconds),
        "--json",
    ]
    if args.announce_to:
        command.extend(["--announce", "--channel", "kolo", "--to", args.announce_to])
    else:
        command.append("--no-deliver")
    return command


def _existing_job(openclaw: str, name: str) -> dict | None:
    listed = _run([openclaw, "cron", "list", "--json", "--all"])
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "unknown error").strip()
        raise RuntimeError(f"could not inspect existing cron jobs: {detail[:300]}")
    try:
        payload = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cron list did not return valid JSON") from exc
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    return next((job for job in jobs if isinstance(job, dict) and job.get("name") == name), None)


def create_schedule(args) -> dict:
    skill_dir = Path(__file__).resolve().parent.parent
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise ValueError(f"test input does not exist: {input_path}")
    if not args.name.strip() or not args.cron.strip():
        raise ValueError("name and cron expression must be non-empty")
    if not 30 <= args.timeout_seconds <= 900:
        raise ValueError("timeout-seconds must be between 30 and 900")
    if args.announce_to and not args.announce_to.startswith("kolo:"):
        raise ValueError("announce-to must be an explicit kolo:<chat-id> destination")
    _validate_timezone(args.timezone)

    openclaw = shutil.which("openclaw") or "openclaw"
    command = build_create_command(
        args, openclaw=openclaw, python=sys.executable, skill_dir=skill_dir
    )
    if args.dry_run:
        return {"status": "dry_run", "command_argv": command}
    if not shutil.which("openclaw"):
        raise RuntimeError("openclaw command not found; run this installer on the Kolo pod")

    help_result = _run([openclaw, "cron", "create", "--help"])
    help_text = (help_result.stdout or "") + (help_result.stderr or "")
    missing = [flag for flag in REQUIRED_CREATE_FLAGS if flag not in help_text]
    delivery_flag = "--announce" if args.announce_to else "--no-deliver"
    if help_result.returncode != 0 or missing or delivery_flag not in help_text:
        raise RuntimeError(
            "installed OpenClaw cron command lacks required command-job flags: "
            + ", ".join([*missing, delivery_flag] if missing else [delivery_flag])
        )

    existing = _existing_job(openclaw, args.name)
    if existing:
        return {
            "status": "exists",
            "job_id": existing.get("id"),
            "name": args.name,
            "message": "a job with this name already exists; no changes were made",
        }

    created = _run(command, timeout=20)
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "unknown error").strip()
        raise RuntimeError(f"cron creation failed: {detail[:300]}")
    try:
        result = json.loads(created.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cron create succeeded but did not return valid JSON") from exc
    return {"status": "created", "job": result}


def parser() -> argparse.ArgumentParser:
    skill_dir = Path(__file__).resolve().parent.parent
    result = argparse.ArgumentParser(
        description="Create an explicitly test-only command cron; never accesses ChoiceADVANTAGE."
    )
    result.add_argument("--name", default="PMS Reservation Audit - TEST ONLY")
    result.add_argument("--cron", required=True, help="five-field cron expression")
    result.add_argument("--timezone", required=True, help="IANA timezone")
    result.add_argument(
        "--input", default=str(skill_dir / "assets" / "scheduled_test_input.json"),
        help="non-live audit input; defaults to the packaged fixture",
    )
    result.add_argument("--output-dir", required=True, help="persistent output directory")
    result.add_argument("--timeout-seconds", type=int, default=120)
    result.add_argument(
        "--announce-to",
        help="explicit Kolo destination such as kolo:<chat-id>; omission uses --no-deliver",
    )
    result.add_argument("--dry-run", action="store_true", help="print argv without creating a job")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(create_schedule(args), sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
