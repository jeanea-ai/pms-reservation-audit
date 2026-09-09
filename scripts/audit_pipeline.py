#!/usr/bin/env python3
"""Build and render an audit from already-extracted, read-only PMS data.

This command does not open a browser, authenticate, or call ChoiceADVANTAGE.
It owns the deterministic post-extraction path only.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

try:
    from scripts.audit_report import render_pdf_atomic
    from scripts.duplicate_analysis import analyze_duplicates, inclusive_windows
    from scripts.report_spec import SpecValidationError, validate_report_spec
except ModuleNotFoundError:  # direct script execution
    from audit_report import render_pdf_atomic
    from duplicate_analysis import analyze_duplicates, inclusive_windows
    from report_spec import SpecValidationError, validate_report_spec


MISSING = "Not displayed."
LEDGER_HEADERS = ["Guest", "Status", "Arrival", "Account", "Balance"]
DUPLICATE_HEADERS = ["Guest(s)", "Account(s)", "Stay", "Rooms", "Match"]


def _shown(value: Any) -> Any:
    return MISSING if value is None or value == "" else value


def _ledger_rows(entries: list[dict[str, Any]]) -> list[list[Any]]:
    return [[
        _shown(item.get("guest_name")), _shown(item.get("status")),
        _shown(item.get("check_in")), _shown(item.get("account_number")),
        _shown(item.get("balance")),
    ] for item in entries]


def _ledger_summary(label: str, entries: list[dict[str, Any]]) -> str:
    total = sum(float(item["balance"]) for item in entries if isinstance(item.get("balance"), (int, float)))
    return f"{label}: {len(entries)} account(s), ${total:,.2f} total balance"


def _duplicate_rows(groups: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for group in groups:
        stays = group["guest_stays"]
        names = sorted({str(item.get("guest_name") or MISSING) for item in stays})
        accounts = sorted({value for item in stays for value in item.get("account_numbers", [])})
        dates = sorted({f"{item.get('check_in')}–{item.get('check_out')}" for item in stays})
        rows.append([
            " / ".join(names), ", ".join(accounts) or MISSING, "; ".join(dates),
            group["rooms_total"], f"{group['match_strength']}: {group['match_reason']}",
        ])
    return rows


def build_report_spec(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(payload, dict):
        raise ValueError("input root must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    try:
        today = date.fromisoformat(str(metadata["property_local_date"]))
    except (KeyError, ValueError):
        raise ValueError("metadata.property_local_date must use YYYY-MM-DD") from None

    features, sections = [], []
    ledger = payload.get("guest_ledger")
    if ledger is not None:
        if not isinstance(ledger, dict):
            raise ValueError("guest_ledger must be an object")
        balances = ledger.get("balances", [])
        if not isinstance(balances, list):
            raise ValueError("guest_ledger.balances must be an array")
        features.append("guest_ledger")
        sections.append({
            "heading": "1. Guest Ledger Balance Review",
            "body": [_shown(ledger.get("completion_statement"))],
            "tables": [
                {"table_title": "Guest Ledger — Recent No Show / Cancelled + All Groups", "headers": LEDGER_HEADERS,
                 "rows": _ledger_rows(balances), "summary": _ledger_summary("Balances", balances)},
            ],
            "notes": ledger.get("notes", []),
        })

    analysis = None
    reservations = payload.get("reservations")
    if reservations is not None:
        if not isinstance(reservations, list):
            raise ValueError("reservations must be an array")
        features.append("duplicates")
        analysis = analyze_duplicates(reservations, today)
        window = analysis["windows"]["future_12_months"]
        tables = [{
            "table_title": "Future 12 Months — Duplicate Reservations",
            "headers": DUPLICATE_HEADERS,
            "rows": _duplicate_rows(window["groups"]),
            "summary": (
                f"{window['records_reviewed']} active record(s) reviewed; "
                f"{len(window['groups'])} duplicate group(s); "
                f"{sum(g['reservation_count'] for g in window['groups'])} reservation record(s); "
                f"{sum(g['rooms_total'] for g in window['groups'])} room(s)"
            ),
        }]
        sections.append({
            "heading": "2. Duplicate Reservation Review",
            "body": ["Future Reservation Report only; cancelled reservations were excluded and all displayed duplicate groups have overlapping stays."],
            "tables": tables,
            "notes": [],
        })

    if not features:
        raise ValueError("input must contain guest_ledger, reservations, or both")
    windows = inclusive_windows(today)
    complete = payload.get("complete")
    limitations = payload.get("limitations", [])
    spec = {
        "title": "ChoiceADVANTAGE Guest Ledger & Duplicate Reservation Audit",
        "property": metadata.get("property"),
        "reviewed_at": metadata.get("reviewed_at"),
        "business_date": metadata.get("business_date", MISSING),
        "date_ranges": {
            "ledger_past_30": f"{windows['ledger_past_30'][0]} through {windows['ledger_past_30'][1]} inclusive",
            "future_12_months": f"{windows['future_12_months'][0]} through {windows['future_12_months'][1]} inclusive",
        },
        "disclaimer": "Read-only audit; no ChoiceADVANTAGE records were modified.",
        "complete": complete,
        "completion_warning": payload.get("completion_warning"),
        "audit_features": features,
        "sections": sections,
        "limitations": limitations,
        "max_pages": 3,
    }
    return validate_report_spec(spec), analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate extracted audit data, analyze duplicates, and render one PDF.")
    parser.add_argument("input", help="extracted audit JSON")
    parser.add_argument("-o", "--out", required=True, help="final PDF path")
    parser.add_argument("--spec-out", help="optional validated report-spec JSON path")
    args = parser.parse_args()
    started = perf_counter()
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        spec, analysis = build_report_spec(payload)
        if args.spec_out:
            Path(args.spec_out).write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        render_pdf_atomic(spec, args.out)
        summary = {
            "status": "ok", "features": spec["audit_features"], "complete": spec["complete"],
            "post_processing_ms": round((perf_counter() - started) * 1000),
        }
        if analysis:
            summary["duplicate_counts"] = analysis["counts"]
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ValueError, SpecValidationError, RuntimeError) as exc:
        print(f"Audit pipeline failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

