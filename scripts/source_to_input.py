#!/usr/bin/env python3
"""Parse fresh ChoiceADVANTAGE report artifacts into audit_input.json.

The browser/report-pull helper owns authentication and report retrieval. This
module owns only deterministic, local parsing and reconciliation.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


MISSING = "Not displayed."
DATE_TOKEN = r"\d{1,2}/\d{1,2}/\d{2,4}"
MONEY_TOKEN = r"\(?[\d,]+\.\d{2}\)?"
LEDGER_ROW = re.compile(
    rf"^\s*(No Show|Checked Out|Cancelled|In House)\s+"
    rf"(?:(.*?)\s+)?(\d{{7,12}})\s+(?:(\d{{2,4}})\s+)?"
    rf"({DATE_TOKEN})\s+({DATE_TOKEN})\s+({MONEY_TOKEN})\s*$",
    re.IGNORECASE,
)
FUTURE_ROW_START = re.compile(r"^\s*(\d{7,12})\b")
FUTURE_REQUIRED_COLUMNS = (
    "Account", "Guest Name", "Traveler", "Arrival", "Departure", "Nights",
)


def _iso_date(value: str) -> str:
    text = value.strip()
    for pattern in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unsupported date: {value!r}")


def _money(value: str) -> float:
    text = value.strip().replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    amount = float(text)
    return -amount if negative else amount


def _metadata(text: str) -> dict[str, str]:
    patterns = {
        "property_name": r"Property Name:\s*(.+?)\s*$",
        "property_code": r"Property Code:\s*([^\s]+)",
        "business_date": rf"Business Date:\s*({DATE_TOKEN})",
    }
    result = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            result[key] = match.group(1).strip()
    if "business_date" in result:
        result["business_date"] = _iso_date(result["business_date"])
    return result


def _is_noise(line: str) -> bool:
    value = " ".join(line.split())
    return (
        not value
        or value in {
            "Guest Ledger", "Reservation Activity Report", "Future Reservation Report",
            "Future Reservations Report", "Future Reservations",
        }
        or value.startswith("Business Date:")
        or value.startswith("Date/Time of Printing:")
        or value.startswith("Status Name Account Room Arrival Departure Balance")
        or value.startswith("Account Guest Name Arrive Depart Nights Status")
        or value.startswith("Total Reservations:")
        or value.startswith("Total Room Nights:")
    )


def _ledger_section_lines(text: str, wanted: str) -> list[str]:
    section = None
    output = []
    headings = {
        "No-Show Accounts": "no_shows",
        "Checked Out Accounts": "other",
        "Cancelled Accounts": "other",
        "In House Accounts": "other",
        "Group": "groups",
    }
    for raw in text.splitlines():
        value = " ".join(raw.split())
        if value in headings:
            section = headings[value]
            continue
        if section == wanted:
            output.append(raw)
    return output


def _name_fragment(line: str) -> str | None:
    value = " ".join(line.split())
    if _is_noise(value) or value.startswith("Subtotal ") or value.startswith("Total For All Accounts:"):
        return None
    if re.search(rf"{DATE_TOKEN}|{MONEY_TOKEN}$", value):
        return None
    return value or None


def _parse_ledger_rows(
    lines: Iterable[str], *, include_zero_balances: bool = False
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    fragments: list[str] = []

    def finish() -> None:
        nonlocal current, fragments
        if current is None:
            return
        if fragments:
            current["guest_name"] = " ".join(
                part for part in [current["guest_name"], *fragments] if part
            )
        current.pop("_had_inline_name", None)
        if include_zero_balances or abs(current["balance"]) >= 0.005:
            records.append(current)
        current = None
        fragments = []

    for raw in lines:
        match = LEDGER_ROW.match(raw)
        if match:
            prefix = fragments
            if current is not None:
                finish()
                prefix = []
            inline_name = " ".join((match.group(2) or "").split())
            name = " ".join([*prefix, inline_name]).strip()
            fragments = []
            ledger_status = {
                "no show": "No Show", "checked out": "Checked Out",
                "cancelled": "Cancelled", "in house": "In House",
            }[match.group(1).casefold()]
            current = {
                "guest_name": name,
                "status": ledger_status,
                "folio_number": MISSING,
                "account_number": match.group(3),
                "confirmation_number": MISSING,
                "check_in": _iso_date(match.group(5)),
                "check_out": _iso_date(match.group(6)),
                "balance": _money(match.group(7)),
                "_had_inline_name": bool(inline_name),
            }
            continue
        fragment = _name_fragment(raw)
        if fragment:
            fragments.append(fragment)
    finish()
    return records


def _printed_subtotal(text: str, label: str) -> float:
    match = re.search(
        rf"Subtotal\s+{re.escape(label)}:\s*({MONEY_TOKEN})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"missing printed {label} subtotal")
    return _money(match.group(1))


def parse_guest_ledger_text(text: str) -> dict[str, Any]:
    if "Guest" not in text or "Ledger" not in text:
        raise ValueError("source is not a Guest Ledger report")
    no_shows = _parse_ledger_rows(_ledger_section_lines(text, "no_shows"))
    cancelled = _parse_ledger_rows(_ledger_section_lines(text, "other"))
    cancelled = [item for item in cancelled if item["status"] == "Cancelled"]
    groups = _parse_ledger_rows(
        _ledger_section_lines(text, "groups"), include_zero_balances=True
    )
    expected = {
        "No-Show Accounts": _printed_subtotal(text, "No-Show Accounts"),
        "Cancelled Accounts": _printed_subtotal(text, "Cancelled Accounts"),
        "Group": _printed_subtotal(text, "Group"),
    }
    actual = {
        "No-Show Accounts": round(sum(item["balance"] for item in no_shows), 2),
        "Cancelled Accounts": round(sum(item["balance"] for item in cancelled), 2),
        "Group": round(sum(item["balance"] for item in groups), 2),
    }
    for label in expected:
        if abs(round(expected[label], 2) - actual[label]) > 0.005:
            raise ValueError(
                f"{label} subtotal mismatch: parsed {actual[label]:.2f}, "
                f"printed {expected[label]:.2f}"
            )
    return {
        "metadata": _metadata(text),
        "no_shows": no_shows,
        "cancelled": cancelled,
        "groups": groups,
        "printed_subtotals": expected,
    }


def _future_column_starts(line: str) -> dict[str, int] | None:
    """Return fixed-width column starts from a real Future Reservations header."""
    starts: dict[str, int] = {}
    search_from = 0
    for label in FUTURE_REQUIRED_COLUMNS:
        position = line.find(label, search_from)
        if position < 0:
            return None
        starts[label] = position
        search_from = position + len(label)
    return starts


def _future_guest_fragment(line: str, columns: dict[str, int]) -> str:
    return " ".join(line[:columns["Traveler"]].split())


def _future_row(line: str, columns: dict[str, int]) -> dict[str, Any] | None:
    account_match = FUTURE_ROW_START.match(line)
    if not account_match:
        return None
    dates = list(re.finditer(DATE_TOKEN, line))
    if len(dates) < 2:
        return None
    arrival, departure = dates[0].group(), dates[1].group()
    nights_match = re.match(r"\s+(\d+)\b", line[dates[1].end():])
    try:
        check_in, check_out = _iso_date(arrival), _iso_date(departure)
    except ValueError:
        return None
    if not nights_match:
        return None
    return {
        "guest_name": " ".join(
            line[account_match.end():min(columns["Traveler"], dates[0].start())].split()
        ),
        "confirmation_number": MISSING,
        "folio_number": MISSING,
        "account_number": account_match.group(1),
        "check_in": check_in,
        "check_out": check_out,
        "rooms_booked": 1,
        "primary_email": MISSING,
        "secondary_email": MISSING,
        # The real Future Reservations report has no reservation-status field.
        # Every row is future inventory, so do not reinterpret Rate Plan as status.
        "status": "Reserved",
    }


def parse_future_reservations_text(text: str) -> dict[str, Any]:
    if not re.search(r"^\s*Future Reservations?(?: Report)?\s*$", text, re.IGNORECASE | re.MULTILINE):
        raise ValueError("source is not a Future Reservation Report")
    reservations: list[dict[str, Any]] = []
    columns: dict[str, int] | None = None
    for line in text.splitlines():
        header = _future_column_starts(line)
        if header:
            columns = header
            continue
        if not columns:
            continue
        row = _future_row(line, columns)
        if row:
            if not row["guest_name"]:
                raise ValueError(f"Future Reservation row has no guest name: {row['account_number']}")
            reservations.append(row)
            continue
        # Long guest names wrap into the fixed Guest Name column on the next
        # physical line. Ignore wrapping in every other report column.
        if (
            reservations
            and not FUTURE_ROW_START.match(line)
            and not line[columns["Traveler"]:].strip()
        ):
            fragment = _future_guest_fragment(line, columns)
            if (
                fragment
                and not fragment.isdigit()
                and not re.search(
                    rf"{DATE_TOKEN}|Total Reservations|Total Room Nights|Future Reservations",
                    fragment,
                    re.IGNORECASE,
                )
            ):
                reservations[-1]["guest_name"] = " ".join(
                    [reservations[-1]["guest_name"], fragment]
                )
    total = re.search(r"Total Reservations:\s*([\d,]+)", text, flags=re.IGNORECASE)
    if not total:
        raise ValueError("missing Future Reservation total")
    expected = int(total.group(1).replace(",", ""))
    if len(reservations) != expected:
        raise ValueError(
            f"Future Reservation count mismatch: parsed {len(reservations)}, printed {expected}"
        )
    return {"metadata": _metadata(text), "reservations": reservations, "printed_total": expected}


def read_report_text(path: str | Path) -> str:
    source = Path(path)
    with source.open("rb") as stream:
        is_pdf = stream.read(5) == b"%PDF-"
    if not is_pdf:
        return source.read_text(encoding="utf-8")
    converter = shutil.which("pdftotext")
    if converter:
        result = subprocess.run(
            [converter, "-layout", str(source), "-"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PDF parsing requires pdftotext or pdfplumber") from exc
    with pdfplumber.open(source) as report:
        return "\n\f\n".join(page.extract_text(layout=True) or "" for page in report.pages)


def _consistent(values: Iterable[str], label: str) -> str | None:
    present = {value for value in values if value}
    if len(present) > 1:
        raise ValueError(f"source reports disagree on {label}: {sorted(present)}")
    return next(iter(present), None)


def build_audit_input(
    *,
    guest_ledger_text: str | None,
    future_reservation_texts: Iterable[str],
    property_local_date: str,
    reviewed_at: str,
    property_name: str | None = None,
    business_date: str | None = None,
) -> dict[str, Any]:
    local_today = date.fromisoformat(property_local_date)
    if not reviewed_at.strip():
        raise ValueError("reviewed_at must include local time and named zone")
    ledger = parse_guest_ledger_text(guest_ledger_text) if guest_ledger_text else None
    future_reports = [parse_future_reservations_text(text) for text in future_reservation_texts]
    if ledger is None and not future_reports:
        raise ValueError("at least one Guest Ledger or Future Reservation report is required")

    parsed_sources = ([ledger] if ledger else []) + future_reports
    source_metadata = [item["metadata"] for item in parsed_sources]
    source_code = _consistent((item.get("property_code", "") for item in source_metadata), "property code")
    source_name = _consistent((item.get("property_name", "") for item in source_metadata), "property name")
    ledger_business_date = ledger["metadata"].get("business_date") if ledger else None
    future_business_date = _consistent(
        (item["metadata"].get("business_date", "") for item in future_reports),
        "Future Reservation business date",
    )
    resolved_property = property_name or " - ".join(value for value in (source_code, source_name) if value)
    if not resolved_property:
        raise ValueError("property name could not be inferred; pass --property")
    # Guest Ledger normally uses the latest closed business date while the
    # Future Reservation report uses the current operating date.
    resolved_business_date = business_date or ledger_business_date or future_business_date or MISSING
    if resolved_business_date != MISSING:
        resolved_business_date = _iso_date(resolved_business_date)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "metadata": {
            "property": resolved_property,
            "reviewed_at": reviewed_at.strip(),
            "business_date": resolved_business_date,
            "property_local_date": property_local_date,
        },
        "complete": True,
        "completion_warning": None,
        "limitations": [],
    }
    if ledger:
        ledger_start = local_today - timedelta(days=30)
        recent_no_show_and_cancelled = [
            item for item in [*ledger["no_shows"], *ledger["cancelled"]]
            if ledger_start <= date.fromisoformat(item["check_in"]) <= local_today
        ]
        balances = [*recent_no_show_and_cancelled, *ledger["groups"]]
        payload["guest_ledger"] = {
            "completion_statement": (
                "Every Guest Ledger page was parsed; No-Show, Cancelled, and Group "
                "subtotals reconciled to the printed report."
            ),
            "balances": balances,
            "notes": [],
        }
    if future_reports:
        try:
            future_end = local_today.replace(year=local_today.year + 1)
        except ValueError:
            future_end = local_today.replace(year=local_today.year + 1, day=28)
        payload["reservations"] = [
            reservation
            for report in future_reports
            for reservation in report["reservations"]
        ]
        outside = [
            item for item in payload["reservations"]
            if not local_today <= date.fromisoformat(item["check_in"]) <= future_end
        ]
        if outside:
            raise ValueError(
                "Future Reservation report contains arrivals outside the required "
                f"{local_today} through {future_end} inclusive window"
            )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse fresh ChoiceADVANTAGE PDFs/text into validated audit input JSON."
    )
    parser.add_argument("--guest-ledger", help="fresh Guest Ledger PDF or extracted text")
    parser.add_argument(
        "--future-reservations", action="append", default=[],
        help="fresh Future Reservation Report PDF or extracted text",
    )
    parser.add_argument("--property-local-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--reviewed-at", required=True, help="owner-local timestamp with named zone")
    parser.add_argument("--property", help="property label; inferred from reports when omitted")
    parser.add_argument("--business-date", help="override report business date")
    parser.add_argument("-o", "--out", required=True, help="audit_input.json destination")
    args = parser.parse_args()
    try:
        payload = build_audit_input(
            guest_ledger_text=read_report_text(args.guest_ledger) if args.guest_ledger else None,
            future_reservation_texts=[read_report_text(path) for path in args.future_reservations],
            property_local_date=args.property_local_date,
            reviewed_at=args.reviewed_at,
            property_name=args.property,
            business_date=args.business_date,
        )
        destination = Path(args.out)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(destination)
        print(json.dumps({
            "status": "ok",
            "guest_ledger_rows": sum(
                len(payload.get("guest_ledger", {}).get(key, [])) for key in ("balances",)
            ),
            "reservation_rows": len(payload.get("reservations", [])),
            "out": str(destination),
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Source parser failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

