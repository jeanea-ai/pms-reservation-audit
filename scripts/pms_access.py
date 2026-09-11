#!/usr/bin/env python3
"""Read ChoiceADVANTAGE access from the existing PMS Setup contract."""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,23}$")
VERIFIED_PMS_SETUP_STATUSES = {"ACCESS_VERIFIED"}
CHOICE_VENDORS = {
    "choice_advantage",
    "choiceadvantage",
    "skytouch",
    "skytouch / choice advantage",
}


class AccessError(RuntimeError):
    """Raised when PMS Setup has not produced usable Choice access."""


TEST_ACCESS_FILE_ENV = "PMS_RECON_TEST_ACCESS_FILE"
TEST_ACCESS_ENV = {
    "username": "PMS_RECON_TEST_USERNAME",
    "password": "PMS_RECON_TEST_PASSWORD",
    "timezone": "PMS_RECON_TEST_TIMEZONE",
    "property_code": "PMS_RECON_TEST_PROPERTY_CODE",
}


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


def _read_protected_json(path: Path, label: str) -> dict:
    """Read one owner-only regular file without following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        if path.is_symlink():
            raise AccessError(f"{label} must not be a symbolic link")
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except FileNotFoundError as exc:
        raise AccessError(f"{label} is missing: {path}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise AccessError(f"{label} must not be a symbolic link") from exc
        raise AccessError(f"{label} is inaccessible") from exc
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise AccessError(f"{label} must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        os.close(descriptor)
        raise AccessError(f"{label} must be owner-only (chmod 600)")
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AccessError(f"{label} is unreadable or invalid JSON") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise AccessError(f"{label} root must be an object")
    return value


def _validated_timezone(value: object) -> str:
    timezone = value.strip() if isinstance(value, str) else ""
    if not timezone:
        raise AccessError("property timezone is missing")
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AccessError("property timezone must be a valid IANA zone") from exc
    return timezone


def _standalone_test_access(
    normalized: str,
    env: dict[str, str],
    test_access_file: Path | None,
) -> dict[str, object]:
    configured_file = test_access_file or (
        Path(env[TEST_ACCESS_FILE_ENV]).expanduser()
        if env.get(TEST_ACCESS_FILE_ENV)
        else None
    )
    direct_present = [name for name in TEST_ACCESS_ENV.values() if env.get(name)]
    if configured_file and direct_present:
        raise AccessError(
            "standalone test access is ambiguous; use either the protected file or environment secrets"
        )

    if configured_file:
        try:
            if configured_file.is_symlink():
                raise AccessError("standalone test access file must not be a symbolic link")
            mode = stat.S_IMODE(configured_file.stat().st_mode)
        except FileNotFoundError as exc:
            raise AccessError("standalone test access file is missing") from exc
        except OSError as exc:
            raise AccessError("standalone test access file is inaccessible") from exc
        if mode & 0o077:
            raise AccessError("standalone test access file must be owner-only (chmod 600)")
        payload = _read_json(configured_file, "standalone test access file")
    else:
        missing = [
            env_name
            for field, env_name in TEST_ACCESS_ENV.items()
            if field != "property_code" and not env.get(env_name)
        ]
        if missing:
            raise AccessError(
                "standalone test access requires protected username, password, and timezone secrets"
            )
        payload = {
            field: env.get(env_name)
            for field, env_name in TEST_ACCESS_ENV.items()
            if env.get(env_name)
        }

    supplied_code = str(payload.get("property_code") or normalized).strip().upper()
    if supplied_code != normalized:
        raise AccessError("standalone test access property does not match --hotel")
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not username.strip():
        raise AccessError("standalone test username is missing")
    if not isinstance(password, str) or not password:
        raise AccessError("standalone test password is missing")
    return {
        "property_code": normalized,
        "username": username.strip(),
        "password": password,
        "timezone": _validated_timezone(payload.get("timezone")),
        "source": "standalone-test",
        "test_only": True,
    }


def resolve_access(
    code: str,
    *,
    environ: dict[str, str] | None = None,
    config_dir: Path | None = None,
    allow_test_access: bool = False,
    test_access_file: Path | None = None,
) -> dict[str, object]:
    """Return runtime credentials without modifying PMS Setup files."""
    env = os.environ if environ is None else environ
    normalized = (code or "").strip().upper()
    if not CODE_RE.fullmatch(normalized):
        raise AccessError("hotel code must be 2-24 letters, digits, dash, or underscore")
    if allow_test_access:
        return _standalone_test_access(normalized, env, test_access_file)
    root = config_dir or Path(
        env.get(
            "KOLO_HOTELS_CONFIG_DIR",
            os.path.expanduser("~/.openclaw/workspace-main/kolo-hotels/config"),
        )
    )
    config = _read_json(root / f"{normalized}.json", "PMS Setup property config")
    configured_code = str(config.get("property_code") or normalized).strip().upper()
    if configured_code != normalized:
        raise AccessError("PMS Setup property config does not match --hotel")
    status = str(config.get("status") or "").strip().upper()
    if status not in VERIFIED_PMS_SETUP_STATUSES:
        raise AccessError(
            f"{normalized} access status is not explicitly verified; finish PMS Setup before auditing"
        )
    pms = config.get("pms")
    if not isinstance(pms, dict):
        raise AccessError("PMS Setup property config has no pms block")
    vendor = str(pms.get("vendor") or "").strip().casefold()
    if not vendor:
        raise AccessError("PMS Setup property config has no explicit pms.vendor")
    if vendor not in CHOICE_VENDORS:
        raise AccessError(f"PMS Reconciliation does not support vendor {vendor!r}")

    environment_password = env.get(f"PMS_PASSWORD_{normalized}") or env.get(
        "PMS_PASSWORD"
    )
    secrets_path = root / ".secrets.json"
    secrets = {}
    if not environment_password and secrets_path.exists():
        secrets = _read_protected_json(secrets_path, "PMS Setup secrets file")
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
        environment_password
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
        password_ref = pms.get("password_ref")
        if password_ref:
            raise AccessError(
                "PMS Setup password_ref is present but its credential provider is unavailable"
            )
        raise AccessError(
            "Choice password is not resolvable from the established secret paths"
        )
    timezone = _validated_timezone(config.get("timezone"))

    return {
        "property_code": normalized,
        "username": username.strip(),
        "password": password,
        "timezone": timezone,
        "source": "mf-hotel-pms-setup",
        "test_only": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", required=True)
    parser.add_argument(
        "--test-access",
        action="store_true",
        help="use explicitly configured standalone test credentials instead of PMS Setup",
    )
    parser.add_argument(
        "--test-access-file",
        type=Path,
        help="owner-only JSON file; requires --test-access",
    )
    args = parser.parse_args()
    if args.test_access_file and not args.test_access:
        parser.error("--test-access-file requires --test-access")
    try:
        access = resolve_access(
            args.hotel,
            allow_test_access=args.test_access,
            test_access_file=args.test_access_file,
        )
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
                "source": access["source"],
                "test_only": access["test_only"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
