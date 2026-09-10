#!/usr/bin/env python3
"""Persist a captured one-shot ChoiceADVANTAGE PDF response fail-closed."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile


CONTRACT = "one-shot-pdf-v1"
CAPTURE_METHOD = "network-response"


class CaptureError(RuntimeError):
    """Raised when preserving the one-shot response would violate contract."""


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"contract": CONTRACT, "consumed_keys": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureError("capture state is unreadable or invalid") from exc
    if state.get("contract") != CONTRACT or not isinstance(
        state.get("consumed_keys"), dict
    ):
        raise CaptureError("capture state does not match one-shot-pdf-v1")
    return state


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _fingerprint(key_id: str) -> str:
    if not key_id.strip():
        raise CaptureError("key identifier must not be empty")
    return hashlib.sha256(key_id.encode("utf-8")).hexdigest()


def _require_extractable_text(pdf_bytes: bytes) -> None:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise CaptureError(
            "pypdf is required for parser-input PDF verification"
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        text = "".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise CaptureError("PDF structure or text extraction is invalid") from exc
    if not text.strip():
        raise CaptureError("PDF contains no extractable text")


def preserve_response(
    response: bytes,
    output: Path,
    state_file: Path,
    key_id: str,
    *,
    capture_method: str = CAPTURE_METHOD,
    require_text: bool = False,
) -> dict:
    """Record key consumption and preserve the first original PDF bytes."""
    if capture_method != CAPTURE_METHOD:
        raise CaptureError(
            "parser input must come from the original network response"
        )
    if output.exists():
        raise CaptureError("refusing to overwrite an existing artifact")

    state = _load_state(state_file)
    fingerprint = _fingerprint(key_id)
    if fingerprint in state["consumed_keys"]:
        raise CaptureError("report key was already consumed; generate a fresh key")

    # A response spends the server-side key even if its content is invalid.
    record = {"status": "received", "bytes": len(response)}
    state["consumed_keys"][fingerprint] = record
    _atomic_json(state_file, state)

    if not response:
        record["status"] = "failed-empty"
        _atomic_json(state_file, state)
        raise CaptureError("empty report response")
    if not response.startswith(b"%PDF-"):
        record["status"] = "failed-not-pdf"
        _atomic_json(state_file, state)
        raise CaptureError("report response is not a PDF")
    if require_text:
        try:
            _require_extractable_text(response)
        except CaptureError:
            record["status"] = "failed-no-text"
            _atomic_json(state_file, state)
            raise

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(response)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        record["status"] = "failed-save"
        _atomic_json(state_file, state)
        raise

    record.update(
        {
            "status": "saved",
            "artifact": str(output),
            "sha256": hashlib.sha256(response).hexdigest(),
            "capture_method": CAPTURE_METHOD,
        }
    )
    _atomic_json(state_file, state)
    return record.copy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument(
        "--key-id", required=True, help="Opaque run-local identifier; never printed"
    )
    parser.add_argument(
        "--capture-method", default=CAPTURE_METHOD, choices=[CAPTURE_METHOD]
    )
    parser.add_argument("--require-text", action="store_true")
    args = parser.parse_args()
    try:
        record = preserve_response(
            args.response_file.read_bytes(),
            args.output,
            args.state_file,
            args.key_id,
            capture_method=args.capture_method,
            require_text=args.require_text,
        )
    except (OSError, CaptureError) as exc:
        parser.exit(1, f"capture failed: {exc}\n")
    print(
        json.dumps(
            {
                "status": record["status"],
                "artifact": record["artifact"],
                "sha256": record["sha256"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
