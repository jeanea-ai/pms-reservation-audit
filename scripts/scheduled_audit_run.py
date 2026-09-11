#!/usr/bin/env python3
"""Run one production audit, then queue a light isolated PDF-delivery agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

try:
    from scripts.pms_access import CODE_RE
except ModuleNotFoundError:
    from pms_access import CODE_RE


DELIVERY_MODELS = (
    "litellm-fireworks/qwen-3-7-plus",
    "litellm-fireworks/glm-5-3-flash",
    "litellm/claude-haiku-4-5",
)
DELIVERY_REQUIRED_FLAGS = (
    "--at",
    "--delete-after-run",
    "--session",
    "--message",
    "--model",
    "--thinking",
    "--light-context",
    "--announce",
    "--channel",
    "--to",
    "--tools",
    "--best-effort-deliver",
    "--json",
)


class DeliveryError(RuntimeError):
    """Raised when a completed report cannot be queued for safe delivery."""


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _last_json(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise DeliveryError("audit command produced no structured result")


def _validate_completed_report(result: dict, output_root: Path) -> tuple[Path, Path]:
    if result.get("status") != "ok":
        raise DeliveryError("audit did not report a successful result")
    try:
        root = output_root.expanduser().resolve(strict=True)
        run_dir_raw = Path(str(result["run_dir"])).expanduser()
        report_raw = Path(str(result["report"])).expanduser()
        if run_dir_raw.is_symlink() or report_raw.is_symlink():
            raise DeliveryError("run directory and report must not be symbolic links")
        run_dir = run_dir_raw.resolve(strict=True)
        report = report_raw.resolve(strict=True)
    except (KeyError, OSError) as exc:
        raise DeliveryError("audit result does not identify an accessible report") from exc
    if root != run_dir and root not in run_dir.parents:
        raise DeliveryError("audit run directory is outside the configured output root")
    if report.parent != run_dir or report.suffix.casefold() != ".pdf":
        raise DeliveryError("audit report is not a PDF directly inside its run directory")
    if not report.is_file() or not 5 <= report.stat().st_size <= 20 * 1024 * 1024:
        raise DeliveryError("audit report size is invalid")
    with report.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise DeliveryError("audit report does not have a PDF signature")
    return run_dir, report


def _atomic_receipt(path: Path, payload: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _delivery_prompt(receipt: Path, report: Path, hotel: str) -> str:
    return (
        "Deliver one completed read-only PMS reconciliation report. This is file delivery, "
        "not analysis. Read only the aggregate delivery receipt at "
        f"{receipt}. Do not open, extract, summarize, or inspect the PDF or any source report. "
        f"Attach the exact local PDF {report} to this Kolo chat using the platform file-attachment "
        f"capability. State that the {hotel} reconciliation is complete and repeat only the "
        "aggregate summary from the receipt. Do not include guest names, account numbers, stay "
        "dates, row-level balances, credentials, or source-file contents. If attachment is "
        "unavailable, report that delivery failed; do not claim the file was attached and do not "
        "rerun the audit."
    )


def _existing_delivery(openclaw: str, name: str) -> dict | None:
    try:
        listed = _run([openclaw, "cron", "list", "--json", "--all"], timeout=10)
        payload = json.loads(listed.stdout) if listed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    return next(
        (item for item in jobs if isinstance(item, dict) and item.get("name") == name),
        None,
    )


def _queue_delivery_agent(
    *,
    openclaw: str,
    destination: str,
    hotel: str,
    run_dir: Path,
    receipt: Path,
    report: Path,
) -> dict:
    help_result = _run([openclaw, "cron", "create", "--help"], timeout=10)
    help_text = (help_result.stdout or "") + (help_result.stderr or "")
    missing = [flag for flag in DELIVERY_REQUIRED_FLAGS if flag not in help_text]
    if help_result.returncode != 0 or missing:
        raise DeliveryError(
            "installed OpenClaw lacks isolated delivery flags: "
            + ", ".join(missing or ["help unavailable"])
        )
    name = f"PMS PDF delivery {hotel} {run_dir.name}"
    existing = _existing_delivery(openclaw, name)
    if existing:
        return {"job": existing, "model": existing.get("model"), "reconciled": True}
    prompt = _delivery_prompt(receipt, report, hotel)
    failures = []
    for model in DELIVERY_MODELS:
        command = [
            openclaw,
            "cron",
            "create",
            "--name",
            name,
            "--at",
            "5s",
            "--delete-after-run",
            "--session",
            "isolated",
            "--message",
            prompt,
            "--model",
            model,
            "--thinking",
            "off",
            "--tools",
            "read",
            "--timeout-seconds",
            "300",
            "--light-context",
            "--announce",
            "--channel",
            "kolo",
            "--to",
            destination,
            "--best-effort-deliver",
            "--json",
        ]
        try:
            created = _run(command, timeout=30)
        except subprocess.TimeoutExpired as exc:
            reconciled = _existing_delivery(openclaw, name)
            if reconciled:
                return {"job": reconciled, "model": model, "reconciled": True}
            raise DeliveryError(
                "delivery job creation outcome is uncertain; not retrying another model"
            ) from exc
        except OSError as exc:
            failures.append(f"{model}: {type(exc).__name__}")
            continue
        if created.returncode == 0:
            try:
                job = json.loads(created.stdout)
            except json.JSONDecodeError:
                job = {"created": True}
            return {"job": job, "model": model, "reconciled": False}
        reconciled = _existing_delivery(openclaw, name)
        if reconciled:
            return {"job": reconciled, "model": model, "reconciled": True}
        detail = (created.stderr or created.stdout or "creation failed").strip()[:160]
        failures.append(f"{model}: {detail}")
    raise DeliveryError("no requested delivery model could queue the agent: " + "; ".join(failures))


def _log_completed_report(kolo: str, hotel: str, run_dir: Path, result: dict) -> str:
    details = {
        "property_code": hotel,
        "run_id": run_dir.name,
        "complete": bool(result.get("complete")),
        "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
    }
    command = [
        kolo,
        "log-action",
        "--agent-id",
        "main",
        "--title",
        f"PMS Reconciliation {hotel} completed",
        "--description",
        "Read-only audit completed and its PDF was queued for delivery",
        "--event-type",
        "report_generated",
        "--details",
        json.dumps(details, sort_keys=True),
        "--idempotency-key",
        f"pms-reconciliation-{hotel}-{run_dir.name}",
    ]
    try:
        logged = _run(command, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return "ok" if logged.returncode == 0 else "failed"


def run_scheduled(args: argparse.Namespace) -> int:
    hotel = args.hotel.strip().upper()
    if not CODE_RE.fullmatch(hotel):
        raise DeliveryError("hotel code must be 2-24 letters, digits, dash, or underscore")
    if not re.fullmatch(r"kolo:[A-Za-z0-9._:-]+", args.delivery_to):
        raise DeliveryError("delivery-to must be an explicit kolo:<chat-id> destination")
    output_root = args.output_root.expanduser().resolve()
    audit_command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "pms_audit_run.py"),
        "--hotel",
        hotel,
        "--timeout-seconds",
        str(args.audit_timeout_seconds),
        "--overall-timeout-seconds",
        str(args.overall_timeout_seconds),
        "--output-root",
        str(output_root),
    ]
    try:
        completed = _run(audit_command, timeout=int(args.overall_timeout_seconds + 30))
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "audit subprocess exceeded its shutdown allowance",
                    "next_question": "Should I preserve the timed-out run for diagnosis?",
                },
                sort_keys=True,
            )
        )
        return 124
    result = _last_json(completed.stdout)
    if completed.returncode != 0 or result.get("status") != "ok":
        print(json.dumps(result, sort_keys=True))
        return completed.returncode or 2
    run_dir, report = _validate_completed_report(result, output_root)
    receipt = run_dir / "delivery-receipt.json"
    receipt_payload = {
        "status": "ready",
        "property_code": hotel,
        "complete": bool(result.get("complete")),
        "report": str(report),
        "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
    }
    _atomic_receipt(receipt, receipt_payload)
    openclaw = shutil.which("openclaw") or "openclaw"
    try:
        queued = _queue_delivery_agent(
            openclaw=openclaw,
            destination=args.delivery_to,
            hotel=hotel,
            run_dir=run_dir,
            receipt=receipt,
            report=report,
        )
    except DeliveryError:
        _atomic_receipt(
            run_dir / "delivery-state.json",
            {"status": "failed", "audit_completed": True},
        )
        raise
    kolo = shutil.which("kolo") or "kolo"
    log_status = _log_completed_report(kolo, hotel, run_dir, result)
    _atomic_receipt(
        run_dir / "delivery-state.json",
        {
            "status": "queued",
            "model": queued["model"],
            "audit_log_status": log_status,
            "reconciled": queued["reconciled"],
        },
    )
    print("NO_REPLY")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--hotel", required=True)
    result.add_argument("--output-root", required=True, type=Path)
    result.add_argument("--delivery-to", required=True)
    result.add_argument("--audit-timeout-seconds", type=float, default=90)
    result.add_argument("--overall-timeout-seconds", type=float, default=480)
    return result


def main() -> int:
    try:
        return run_scheduled(parser().parse_args())
    except (DeliveryError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "delivery_failed",
                    "error": str(exc),
                    "next_question": (
                        "The audit delivery step stopped safely. Should I preserve the report "
                        "and inspect the delivery state?"
                    ),
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
