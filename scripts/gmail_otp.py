#!/usr/bin/env python3
"""Read one fresh Google Voice-forwarded Okta OTP through the Gmail gateway."""

from __future__ import annotations

import base64
from email.utils import parseaddr
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


GMAIL_GATEWAY_BASE = "https://gateway.maton.ai/google-mail/gmail/v1/users/me"
VOICE_SENDER = "txt.voice.google.com"
OTP_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
MAX_RESPONSE_BYTES = 1024 * 1024


class OtpError(RuntimeError):
    """Raised when a fresh unambiguous OTP cannot be obtained safely."""


def _unwrap(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise OtpError("Gmail gateway returned an invalid JSON envelope")
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _decode_websafe(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, UnicodeError):
        return ""


def _message_text(payload: dict) -> str:
    chunks: list[str] = []
    body = payload.get("body")
    if isinstance(body, dict) and isinstance(body.get("data"), str):
        chunks.append(_decode_websafe(body["data"]))
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                chunks.append(_message_text(part))
    return "\n".join(item for item in chunks if item)


def _headers(payload: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    values = payload.get("headers")
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.casefold()] = value
    return result


def _is_voice_sender(value: str) -> bool:
    address = parseaddr(value)[1].strip().casefold()
    if not address:
        address = value.strip().casefold()
    return address == VOICE_SENDER or address.endswith(f"@{VOICE_SENDER}")


class GmailOtpReader:
    """Small read-only Gmail adapter with bounded requests and no secret output."""

    def __init__(
        self,
        ops_email: str,
        *,
        environ: dict[str, str] | None = None,
        opener=None,
        clock=time.time,
        sleeper=time.sleep,
    ):
        env = os.environ if environ is None else environ
        api_key = env.get("MATON_API_KEY")
        if not api_key:
            raise OtpError("Gmail gateway credential is unavailable")
        self._ops_email = ops_email.strip().casefold()
        self._api_key = api_key
        self._opener = opener or urllib.request.urlopen
        self._clock = clock
        self._sleeper = sleeper

    def _request(self, suffix: str, *, query: dict | None, deadline: float) -> dict:
        url = f"{GMAIL_GATEWAY_BASE}/{suffix.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
        )
        raw = None
        for attempt in range(2):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise OtpError("fresh Okta code did not arrive before the OTP deadline")
            try:
                with self._opener(request, timeout=min(10.0, remaining)) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise OtpError("Gmail gateway authorization was rejected") from exc
                if attempt == 0 and (exc.code == 429 or 500 <= exc.code <= 599):
                    self._sleeper(min(0.5, max(0.0, deadline - self._clock())))
                    continue
                raise OtpError(f"Gmail gateway request failed with HTTP {exc.code}") from exc
            except (OSError, urllib.error.URLError) as exc:
                if attempt == 0:
                    self._sleeper(min(0.5, max(0.0, deadline - self._clock())))
                    continue
                raise OtpError("Gmail gateway request failed") from exc
        if raw is None:
            raise OtpError("Gmail gateway request failed")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OtpError("Gmail gateway response exceeded the safe size limit")
        try:
            return _unwrap(json.loads(raw))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OtpError("Gmail gateway returned invalid JSON") from exc

    def verify_mailbox(self, *, deadline: float) -> None:
        profile = self._request("profile", query=None, deadline=deadline)
        mailbox = profile.get("emailAddress")
        if not isinstance(mailbox, str) or mailbox.strip().casefold() != self._ops_email:
            raise OtpError("connected Gmail mailbox does not match PMS Setup")

    def _fresh_candidates(self, *, after_epoch: float, deadline: float) -> list[tuple[int, str]]:
        listed = self._request(
            "messages",
            query={
                "q": f"from:{VOICE_SENDER} after:{int(after_epoch)}",
                "maxResults": "10",
            },
            deadline=deadline,
        )
        messages = listed.get("messages") or []
        if not isinstance(messages, list):
            raise OtpError("Gmail message listing has an invalid shape")
        candidates: list[tuple[int, str]] = []
        for item in messages[:10]:
            message_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(message_id, str) or not message_id:
                continue
            message = self._request(
                f"messages/{urllib.parse.quote(message_id, safe='')}",
                query={"format": "full"},
                deadline=deadline,
            )
            try:
                received_ms = int(message.get("internalDate") or 0)
            except (TypeError, ValueError):
                continue
            if received_ms < int(after_epoch * 1000):
                continue
            payload = message.get("payload")
            if not isinstance(payload, dict):
                continue
            sender = _headers(payload).get("from", "")
            if not _is_voice_sender(sender):
                continue
            text = _message_text(payload)
            if not text and isinstance(message.get("snippet"), str):
                text = message["snippet"]
            codes = sorted(set(OTP_RE.findall(text)))
            if len(codes) == 1:
                candidates.append((received_ms, codes[0]))
            elif len(codes) > 1:
                raise OtpError("fresh Google Voice message contains multiple possible codes")
        return candidates

    def wait_for_code(
        self,
        *,
        after_epoch: float,
        timeout_seconds: float = 90.0,
        poll_seconds: float = 2.0,
    ) -> str:
        if not 10 <= timeout_seconds <= 180:
            raise ValueError("OTP timeout must be between 10 and 180 seconds")
        if not 0.25 <= poll_seconds <= 10:
            raise ValueError("OTP poll interval must be between 0.25 and 10 seconds")
        deadline = self._clock() + timeout_seconds
        self.verify_mailbox(deadline=deadline)
        while self._clock() < deadline:
            candidates = self._fresh_candidates(after_epoch=after_epoch, deadline=deadline)
            if candidates:
                candidates.sort(reverse=True)
                newest_time = candidates[0][0]
                newest_codes = {code for received, code in candidates if received == newest_time}
                if len(newest_codes) != 1:
                    raise OtpError("multiple fresh Okta codes arrived at the same time")
                return newest_codes.pop()
            remaining = deadline - self._clock()
            if remaining > 0:
                self._sleeper(min(poll_seconds, remaining))
        raise OtpError("fresh Okta code did not arrive before the OTP deadline")
