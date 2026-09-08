#!/usr/bin/env python3
"""Read-only readiness checks for the ChoiceADVANTAGE audit skill."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys


def _result(status: str, name: str, detail: str) -> tuple[str, str, str]:
    return status, name, detail


def inspect(skill_dir: Path, environ: dict[str, str] | None = None):
    env = os.environ if environ is None else environ
    workspace = skill_dir.parent.parent
    results = []

    for relative in ("SKILL.md", "scripts/audit_report.py", "assets/caf15_audit_report.pdf"):
        path = skill_dir / relative
        results.append(_result("PASS" if path.is_file() else "FAIL", relative,
                               "present" if path.is_file() else "missing from installed skill"))

    reportlab = importlib.util.find_spec("reportlab") is not None
    chromium = any(shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome"))
    results.append(_result("PASS" if reportlab or chromium else "FAIL", "PDF renderer",
                           "reportlab" if reportlab else "Chromium fallback" if chromium else
                           "install reportlab or Chromium"))

    helper = workspace / "skills" / "choiceadvantage-report-pull" / "SKILL.md"
    results.append(_result("PASS" if helper.is_file() else "FAIL", "report-pull skill",
                           str(helper) if helper.is_file() else "choiceadvantage-report-pull is not installed"))

    configured_secret = env.get("CHOICEADVANTAGE_SECRETS_FILE")
    secret_path = Path(configured_secret) if configured_secret else workspace / "kolo-hotels" / "config" / ".secrets.json"
    results.append(_result("PASS" if secret_path.is_file() else "FAIL", "credentials file",
                           "configured path exists" if secret_path.is_file() else "configured/default path is missing"))

    agents_candidates = [workspace / "AGENTS.md", skill_dir / "AGENTS.md"]
    agents_path = next((p for p in agents_candidates if p.is_file()), None)
    results.append(_result("PASS" if agents_path else "FAIL", "property-local time rules",
                           str(agents_path) if agents_path else "AGENTS.md not found"))

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
