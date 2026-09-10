#!/usr/bin/env python3
"""Read ChoiceADVANTAGE access from the existing PMS Setup contract."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re


CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,23}$")


class AccessError(RuntimeError):
    """Raised when PMS Setup has not produced usable Choice access."""


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AccessError(f"{label} is missing: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccessError(f"{label} is unreadable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise AccessError(f"{label} root must be an object")
    return value


def resolve_access(
    code: str,
    *,
    environ: dict[str, str] | None = None,
    config_dir: Path | None = None,
) -> dict[str, str]:
    """Return runtime credentials without modifying PMS Setup files."""
    env = os.environ if environ is None else environ
    normalized = (code or "").strip().upper()
    if not CODE_RE.fullmatch(normalized):
        raise AccessError("hotel code must be 2-24 letters, digits, dash, or underscore")
    root = config_dir or Path(
        env.get(
            "KOLO_HOTELS_CONFIG_DIR",
            os.path.expanduser("~/.openclaw/workspace-main/kolo-hotels/config"),
        )
    )
    config = _read_json(root / f"{normalized}.json", "PMS Setup property config")
    status = str(config.get("status") or "")
    if status in {"NEW", "ACCESS_PENDING"}:
        raise AccessError(
            f"{normalized} access is not verified; finish PMS Setup before auditing"
        )
    pms = config.get("pms")
    if not isinstance(pms, dict):
        raise AccessError("PMS Setup property config has no pms block")
    vendor = str(pms.get("vendor") or "choice_advantage").strip().casefold()
    if vendor not in {
        "choice_advantage",
        "choiceadvantage",
        "skytouch",
        "skytouch / choice advantage",
    }:
        raise AccessError(f"PMS Reconciliation does not support vendor {vendor!r}")

    secrets = _read_json(root / ".secrets.json", "PMS Setup secrets file")
    scoped = secrets.get(normalized) or secrets.get(normalized.lower()) or {}
    if not isinstance(scoped, dict):
        scoped = {}
    legacy = secrets.get("choiceadvantage") or {}
    if not isinstance(legacy, dict):
        legacy = {}

    username = (
        pms.get("legacy_username")
        or scoped.get("pms_username")
        or scoped.get("username")
        or legacy.get("username")
        or legacy.get("j_username")
    )
    password = (
        env.get(f"PMS_PASSWORD_{normalized}")
        or env.get("PMS_PASSWORD")
        or scoped.get("pms_password")
        or scoped.get("password")
        or legacy.get("password")
        or legacy.get("j_password")
    )
    if not isinstance(username, str) or not username.strip():
        raise AccessError(
            "Choice username is missing from pms.legacy_username and legacy fallbacks"
        )
    if not isinstance(password, str) or not password:
        raise AccessError("Choice password is not resolvable from the established secret paths")
    timezone = config.get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        raise AccessError("property timezone is missing from PMS Setup config")

    return {
        "property_code": normalized,
        "username": username.strip(),
        "password": password,
        "timezone": timezone.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", required=True)
    args = parser.parse_args()
    try:
        access = resolve_access(args.hotel)
    except AccessError as exc:
        parser.exit(1, f"access check failed: {exc}\n")
    # Deliberately report presence only; credentials stay inside the process.
    print(
        json.dumps(
            {
                "property_code": access["property_code"],
                "timezone": access["timezone"],
                "username_present": True,
                "password_present": True,
                "source": "mf-hotel-pms-setup",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
