#!/usr/bin/env python3
"""Collect test credentials through a one-use loopback-only browser form."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scripts.pms_access import CODE_RE
except ModuleNotFoundError:
    from pms_access import CODE_RE


MAX_FORM_BYTES = 4096


class SetupError(RuntimeError):
    """Raised when credential setup cannot continue safely."""


def write_access_file(
    path: Path,
    payload: dict[str, str],
    *,
    replace: bool = False,
) -> None:
    """Atomically create or explicitly rotate one mode-0600 JSON file."""
    if not path.is_absolute():
        raise SetupError("credential output path must be absolute")
    if path.is_symlink():
        raise SetupError("credential output must not be a symbolic link")
    if path.exists() and not replace:
        raise SetupError("credential output already exists; use explicit rotation")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            if path.is_symlink():
                raise SetupError("credential output became a symbolic link")
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise SetupError("credential output already exists") from exc
            temporary.unlink()
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SetupError("credential output permissions are not owner-only")
    finally:
        temporary.unlink(missing_ok=True)


def _form_page(token: str, hotel: str, timezone_name: str, message: str = "") -> bytes:
    notice = f"<p>{html.escape(message)}</p>" if message else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ChoiceADVANTAGE test access</title></head><body><main>
<h1>ChoiceADVANTAGE test access</h1>
<p>Property: {html.escape(hotel)} · Timezone: {html.escape(timezone_name)}</p>
<p>This one-use page submits only to the local Kolo process. Do not paste
credentials into chat.</p>{notice}
<form method="post" action="/{token}" autocomplete="off">
<input type="hidden" name="token" value="{token}">
<p><label>Username <input name="username" required maxlength="256"
autocomplete="off" autocapitalize="none" spellcheck="false"></label></p>
<p><label>Password <input name="password" type="password" required
maxlength="1024" autocomplete="new-password"></label></p>
<button type="submit">Save test access</button>
</form></main></body></html>""".encode("utf-8")


def _success_page() -> bytes:
    return b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Access saved</title></head><body><main><h1>Access saved</h1>
<p>The one-use local form has closed. You may close this tab.</p>
</main></body></html>"""


def _make_handler(
    *,
    token: str,
    hotel: str,
    timezone_name: str,
    output: Path,
    replace: bool,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PmsAccessSetup/1"

        def log_message(self, _format: str, *_args) -> None:
            return

        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; form-action 'self'; base-uri 'none'; "
                "frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if urlsplit(self.path).path != f"/{token}":
                self._send(404, b"Not found")
                return
            self._send(200, _form_page(token, hotel, timezone_name))

        def do_POST(self) -> None:
            if urlsplit(self.path).path != f"/{token}":
                self._send(404, b"Not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            if not 1 <= length <= MAX_FORM_BYTES:
                self._send(400, _form_page(token, hotel, timezone_name, "Invalid form size."))
                return
            if not (self.headers.get("Content-Type") or "").startswith(
                "application/x-www-form-urlencoded"
            ):
                self._send(415, _form_page(token, hotel, timezone_name, "Invalid form type."))
                return
            try:
                fields = parse_qs(
                    self.rfile.read(length).decode("utf-8", errors="strict"),
                    keep_blank_values=True,
                    strict_parsing=True,
                )
            except (UnicodeDecodeError, ValueError):
                self._send(400, _form_page(token, hotel, timezone_name, "Invalid form data."))
                return
            supplied_token = fields.get("token", [""])[0]
            username = fields.get("username", [""])[0].strip()
            password = fields.get("password", [""])[0]
            if not secrets.compare_digest(supplied_token, token):
                self._send(403, _form_page(token, hotel, timezone_name, "Setup token expired."))
                return
            if not username or len(username) > 256 or not password or len(password) > 1024:
                self._send(400, _form_page(token, hotel, timezone_name, "Both fields are required."))
                return
            try:
                write_access_file(
                    output,
                    {
                        "property_code": hotel,
                        "username": username,
                        "password": password,
                        "timezone": timezone_name,
                    },
                    replace=replace,
                )
            except (OSError, SetupError):
                self._send(500, _form_page(token, hotel, timezone_name, "Access could not be saved."))
                return
            self.server.saved = True
            self._send(200, _success_page())

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotel", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    hotel = args.hotel.strip().upper()
    if not CODE_RE.fullmatch(hotel):
        parser.error("hotel code must be 2-24 letters, digits, dash, or underscore")
    try:
        ZoneInfo(args.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        parser.error("timezone must be a valid IANA zone")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if not 60 <= args.timeout_seconds <= 1800:
        parser.error("timeout-seconds must be between 60 and 1800")
    output = args.output.expanduser()
    if not output.is_absolute():
        parser.error("output must be an absolute path")
    if output.is_symlink():
        parser.error("output must not be a symbolic link")
    if output.exists() and not args.replace:
        parser.error("output already exists; use --replace for intentional rotation")

    token = secrets.token_urlsafe(24)
    handler = _make_handler(
        token=token,
        hotel=hotel,
        timezone_name=args.timezone,
        output=output,
        replace=args.replace,
    )
    try:
        server = HTTPServer(("127.0.0.1", args.port), handler)
    except OSError as exc:
        parser.exit(2, f"setup failed: loopback port is unavailable ({exc.errno})\n")
    server.saved = False
    server.timeout = 0.5
    deadline = time.monotonic() + args.timeout_seconds
    expires_at = datetime.fromtimestamp(
        time.time() + args.timeout_seconds,
        tz=timezone.utc,
    ).isoformat()
    print(
        json.dumps(
            {
                "status": "waiting_for_owner",
                "setup_url": f"http://127.0.0.1:{args.port}/{token}",
                "expires_at": expires_at,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        while not server.saved and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not server.saved:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "credential setup expired without saving",
                    "next_question": "The private login form expired. Open one new setup form?",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "saved",
                "property_code": hotel,
                "credential_file": str(output),
                "secret_values_displayed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
