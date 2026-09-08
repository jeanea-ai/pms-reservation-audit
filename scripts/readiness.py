#!/usr/bin/env python3
"""Read-only readiness checks for the ChoiceADVANTAGE audit skill."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    from scripts.audit_report import find_chromium
except ModuleNotFoundError:
    from audit_report import find_chromium


REPORT_PULL_NAME = "choiceadvantage-report-pull"
REPORT_PULL_MIN_VERSION = (0, 1, 0)
REPORT_PULL_CONTRACT_TOKENS = ("j_username", "j_password", "Continue", "Migrate")


def _result(status: str, name: str, detail: str) -> tuple[str, str, str]:
    return status, name, detail


def _version_tuple(value: str):
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value.strip())
    return tuple(map(int, match.groups())) if match else None


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    result = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip().strip('"\'')
    return result


def _check_report_pull(path: Path):
    if not path.is_file():
        return False, "choiceadvantage-report-pull is not installed"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False, "SKILL.md could not be read"
    metadata = _frontmatter(text)
    version = _version_tuple(metadata.get("version", ""))
    missing = [token for token in REPORT_PULL_CONTRACT_TOKENS if token not in text]
    if metadata.get("name") != REPORT_PULL_NAME:
        return False, "frontmatter name does not match the report-pull contract"
    if version is None or version < REPORT_PULL_MIN_VERSION:
        return False, "version is missing, invalid, or older than 0.1.0"
    if missing:
        return False, "required login/navigation contract markers are missing"
    return True, f"contract {metadata['name']} version {metadata['version']}"


def _check_secrets(path: Path):
    if not path.is_file():
        return False, "configured/default credentials file is missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False, "credentials file is not readable valid JSON"
    if not isinstance(payload, dict):
        return False, "credentials JSON root must be an object"
    credentials = payload.get("choiceadvantage", payload)
    if not isinstance(credentials, dict):
        return False, "choiceadvantage credentials must be an object"
    username = credentials.get("username") or credentials.get("j_username")
    password = credentials.get("password") or credentials.get("j_password")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        return False, "credentials JSON must contain non-empty username and password fields"
    return True, "valid credential structure (values not displayed)"


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


def inspect(skill_dir: Path, environ: dict[str, str] | None = None):
    env = os.environ if environ is None else environ
    workspace = skill_dir.parent.parent
    results = []

    for relative in ("SKILL.md", "scripts/audit_report.py", "assets/caf15_audit_report.pdf"):
        path = skill_dir / relative
        results.append(_result("PASS" if path.is_file() else "FAIL", relative,
                               "present" if path.is_file() else "missing from installed skill"))

    reportlab = importlib.util.find_spec("reportlab") is not None
    chromium = find_chromium(env)
    results.append(_result("PASS" if reportlab or chromium else "FAIL", "PDF renderer",
                           "reportlab" if reportlab else "Chromium fallback" if chromium else
                           "install reportlab or Chromium"))

    helper = workspace / "skills" / REPORT_PULL_NAME / "SKILL.md"
    helper_ok, helper_detail = _check_report_pull(helper)
    results.append(_result("PASS" if helper_ok else "FAIL", "report-pull skill", helper_detail))

    configured_secret = env.get("CHOICEADVANTAGE_SECRETS_FILE")
    secret_path = Path(configured_secret) if configured_secret else workspace / "kolo-hotels" / "config" / ".secrets.json"
    secret_ok, secret_detail = _check_secrets(secret_path)
    results.append(_result("PASS" if secret_ok else "FAIL", "credentials file", secret_detail))

    agents_candidates = [workspace / "AGENTS.md", skill_dir / "AGENTS.md"]
    agents_path = next((p for p in agents_candidates if p.is_file()), agents_candidates[0])
    time_ok, time_detail = _check_timezone_guidance(agents_path)
    results.append(_result("PASS" if time_ok else "FAIL", "property-local time rules", time_detail))

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
    skill_dir = Path(__file__).resolve().parent.parent
    results = inspect(skill_dir)
    for status, name, detail in results:
        print(f"{status:<4} {name}: {detail}")
    failures = sum(status == "FAIL" for status, _, _ in results)
    print(f"SUMMARY {len(results) - failures} non-failing, {failures} failing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
