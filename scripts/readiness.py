#!/usr/bin/env python3
"""Read-only readiness checks for the ChoiceADVANTAGE audit skill."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from scripts.audit_report import find_chromium
    from scripts.pms_access import AccessError, resolve_access
except ModuleNotFoundError:
    from audit_report import find_chromium
    from pms_access import AccessError, resolve_access


def _result(status: str, name: str, detail: str) -> tuple[str, str, str]:
    return status, name, detail


def _check_secrets(path: Path):
    if not path.is_file():
        return False, "configured/default credentials file is missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, "credentials file is not readable valid JSON"
    if not isinstance(payload, dict):
        return False, "credentials JSON root must be an object"
    candidates = [payload.get("choiceadvantage", payload)]
    candidates.extend(value for value in payload.values() if isinstance(value, dict))
    valid = False
    for credentials in candidates:
        if not isinstance(credentials, dict):
            continue
        password = credentials.get("password") or credentials.get("j_password") or credentials.get("pms_password")
        if isinstance(password, str) and password:
            valid = True
            break
    if not valid:
        return False, "credentials JSON must contain a top-level or property-scoped non-empty PMS password"
    return True, "valid credential secret structure (values not displayed); username resolves from PMS Setup property config"


def _check_timezone_guidance(path: Path):
    if not path.is_file():
        return False, "AGENTS.md not found"
    try:
        text = path.read_text(encoding="utf-8").casefold()
    except OSError:
        return False, "AGENTS.md could not be read"
    has_zone = "timezone" in text or "time zone" in text
    has_property = "property" in text or "hotel" in text
    has_local = "local" in text
    if not (has_zone and has_property and has_local):
        return False, "AGENTS.md lacks property-local timezone guidance"
    return True, "property-local timezone guidance present"


def inspect(
    skill_dir: Path,
    environ: dict[str, str] | None = None,
    *,
    production_hotel: str | None = None,
    test_hotel: str | None = None,
    test_access_file: Path | None = None,
):
    env = os.environ if environ is None else environ
    workspace = skill_dir.parent.parent
    results = []

    for relative in (
        "SKILL.md",
        "scripts/source_to_input.py",
        "scripts/audit_pipeline.py",
        "scripts/duplicate_analysis.py",
        "scripts/audit_report.py",
        "scripts/one_shot_pdf.py",
        "scripts/pms_access.py",
        "scripts/pms_access_setup.py",
        "scripts/pms_login.py",
        "scripts/pms_report_pull.py",
        "scripts/pms_audit_run.py",
        "scripts/schedule_audit.py",
        "references/report-acquisition.md",
        "assets/caf15_audit_report.pdf",
    ):
        path = skill_dir / relative
        results.append(_result("PASS" if path.is_file() else "FAIL", relative,
                               "present" if path.is_file() else "missing from installed skill"))

    reportlab = importlib.util.find_spec("reportlab") is not None
    chromium = find_chromium(env)
    results.append(_result("PASS" if reportlab or chromium else "FAIL", "PDF renderer",
                           "reportlab" if reportlab else "Chromium fallback" if chromium else
                           "install reportlab or Chromium"))

    pypdf = importlib.util.find_spec("pypdf") is not None
    results.append(_result(
        "PASS" if pypdf else "FAIL",
        "PDF structural verifier",
        "pypdf" if pypdf else "install pypdf; PDF publication must fail closed without it",
    ))

    websocket_client = importlib.util.find_spec("websocket") is not None
    results.append(_result(
        "PASS" if websocket_client else "FAIL",
        "browser CDP client",
        "websocket-client" if websocket_client else "install websocket-client",
    ))

    pdftotext = shutil.which("pdftotext")
    pdfplumber = importlib.util.find_spec("pdfplumber") is not None
    results.append(_result(
        "PASS" if pdftotext or pdfplumber else "FAIL",
        "PDF text extractor",
        "pdftotext" if pdftotext else "pdfplumber" if pdfplumber else
        "install pdftotext or pdfplumber",
    ))

    test_access = None
    if test_hotel:
        try:
            test_access = resolve_access(
                test_hotel,
                environ=env,
                allow_test_access=True,
                test_access_file=test_access_file,
            )
            results.append(_result(
                "PASS", "standalone test access",
                "protected credentials and IANA timezone resolve (values not displayed)",
            ))
        except AccessError as exc:
            results.append(_result("FAIL", "standalone test access", str(exc)))
    elif production_hotel:
        try:
            production_access = resolve_access(production_hotel, environ=env)
            results.append(
                _result(
                    "PASS",
                    "PMS Setup access",
                    (
                        f"{production_access['property_code']} resolves through "
                        f"{production_access['source']} (values not displayed)"
                    ),
                )
            )
        except AccessError as exc:
            results.append(_result("FAIL", "PMS Setup access", str(exc)))
    else:
        configured_secret = env.get("CHOICEADVANTAGE_SECRETS_FILE")
        secret_path = Path(configured_secret) if configured_secret else workspace / "kolo-hotels" / "config" / ".secrets.json"
        secret_ok, secret_detail = _check_secrets(secret_path)
        results.append(_result("PASS" if secret_ok else "FAIL", "credentials file", secret_detail))

    agents_candidates = [workspace / "AGENTS.md", skill_dir / "AGENTS.md"]
    agents_path = next((p for p in agents_candidates if p.is_file()), agents_candidates[0])
    time_ok, time_detail = _check_timezone_guidance(agents_path)
    if test_access:
        results.append(_result(
            "PASS", "property-local time rules",
            "validated from standalone test access (value not displayed)",
        ))
    else:
        results.append(_result("PASS" if time_ok else "SKIP", "property-local time rules",
                               time_detail if time_ok else
                               "confirm the selected property's local timezone during the live audit"))

    kolo = shutil.which("kolo")
    if not kolo:
        results.append(_result("FAIL", "kolo log-action", "kolo command not found"))
    else:
        try:
            probe = subprocess.run([kolo, "--help"], capture_output=True, text=True, timeout=8)
            help_text = (probe.stdout or "") + (probe.stderr or "")
            ok = probe.returncode == 0 and "log-action" in help_text
            results.append(_result("PASS" if ok else "FAIL", "kolo log-action",
                                   "command listed" if ok else "not listed by kolo --help"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(_result("FAIL", "kolo log-action", f"help probe failed: {type(exc).__name__}"))

    results.append(_result("SKIP", "live ChoiceADVANTAGE access",
                           "confirm property, report visibility, and MFA state during the requested audit"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-access", action="store_true")
    parser.add_argument("--hotel")
    parser.add_argument("--test-access-file", type=Path)
    args = parser.parse_args()
    if args.test_access and not args.hotel:
        parser.error("--test-access requires --hotel")
    if args.test_access_file and not args.test_access:
        parser.error("--test-access-file requires --test-access")
    skill_dir = Path(__file__).resolve().parent.parent
    results = inspect(
        skill_dir,
        production_hotel=args.hotel if args.hotel and not args.test_access else None,
        test_hotel=args.hotel if args.test_access else None,
        test_access_file=args.test_access_file,
    )
    for status, name, detail in results:
        print(f"{status:<4} {name}: {detail}")
    failures = sum(status == "FAIL" for status, _, _ in results)
    print(f"SUMMARY {len(results) - failures} non-failing, {failures} failing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
