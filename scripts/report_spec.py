#!/usr/bin/env python3
"""Strict validation for ChoiceADVANTAGE audit report specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


FEATURE_SECTIONS = {
    "guest_ledger": "1. Guest Ledger Balance Review",
    "duplicates": "2. Duplicate Reservation Review",
}
FEATURE_TABLES = {
    "guest_ledger": ("Guest Ledger — Recent No Show / Cancelled + All Groups",),
    "duplicates": ("Future 12 Months — Duplicate Reservations",),
}


@dataclass
class SpecValidationError(ValueError):
    errors: list[str]

    def __str__(self) -> str:
        return "invalid report spec:\n- " + "\n- ".join(self.errors)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_report_spec(spec: Any) -> dict[str, Any]:
    """Return *spec* when valid; otherwise raise one aggregated error."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        raise SpecValidationError(["root must be a JSON object"])

    for key in ("title", "property", "reviewed_at", "business_date", "disclaimer"):
        if not _nonempty_string(spec.get(key)):
            errors.append(f"{key} must be a non-empty string")

    complete = spec.get("complete")
    if not isinstance(complete, bool):
        errors.append("complete must be true or false")
    warning = spec.get("completion_warning")
    if complete is False and not _nonempty_string(warning):
        errors.append("completion_warning is required when complete is false")
    if complete is True and warning not in (None, ""):
        errors.append("completion_warning must be null or empty when complete is true")

    ranges = spec.get("date_ranges")
    if not isinstance(ranges, dict):
        errors.append("date_ranges must be an object")
    else:
        for key in ("ledger_past_30", "future_12_months"):
            if not _nonempty_string(ranges.get(key)):
                errors.append(f"date_ranges.{key} must be a non-empty string")

    max_pages = spec.get("max_pages")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 3:
        errors.append("max_pages must be an integer from 1 through 3")

    features = spec.get("audit_features")
    if not isinstance(features, list) or not features:
        errors.append("audit_features must be a non-empty array")
        features = []
    else:
        unknown = [item for item in features if item not in FEATURE_SECTIONS]
        if unknown:
            errors.append(f"audit_features contains unsupported values: {unknown}")
        if len(features) != len(set(features)):
            errors.append("audit_features must not contain duplicates")

    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("sections must be a non-empty array")
        sections = []

    headings: list[str] = []
    table_titles_by_heading: dict[str, set[str]] = {}
    for si, section in enumerate(sections):
        prefix = f"sections[{si}]"
        if not isinstance(section, dict):
            errors.append(f"{prefix} must be an object")
            continue
        heading = section.get("heading")
        if not _nonempty_string(heading):
            errors.append(f"{prefix}.heading must be a non-empty string")
        else:
            headings.append(heading)
        for list_key in ("body", "notes"):
            value = section.get(list_key, [])
            if not isinstance(value, list) or any(not _nonempty_string(x) for x in value):
                errors.append(f"{prefix}.{list_key} must contain only non-empty strings")
        tables = section.get("tables", [])
        if not isinstance(tables, list):
            errors.append(f"{prefix}.tables must be an array")
            continue
        titles: set[str] = set()
        for ti, table in enumerate(tables):
            tprefix = f"{prefix}.tables[{ti}]"
            if not isinstance(table, dict):
                errors.append(f"{tprefix} must be an object")
                continue
            table_title = table.get("table_title")
            if not _nonempty_string(table_title):
                errors.append(f"{tprefix}.table_title must be a non-empty string")
            else:
                titles.add(table_title)
            headers = table.get("headers")
            rows = table.get("rows")
            if not isinstance(headers, list) or not headers or any(not _nonempty_string(h) for h in headers):
                errors.append(f"{tprefix}.headers must contain non-empty strings")
                continue
            if len(headers) != len(set(headers)):
                errors.append(f"{tprefix}.headers must be unique")
            if not isinstance(rows, list):
                errors.append(f"{tprefix}.rows must be an array")
                continue
            for ri, row in enumerate(rows):
                if not isinstance(row, list) or len(row) != len(headers):
                    errors.append(f"{tprefix}.rows[{ri}] must have exactly {len(headers)} cells")
                    continue
                for ci, cell in enumerate(row):
                    if cell is None or (isinstance(cell, str) and not cell.strip()):
                        errors.append(
                            f"{tprefix}.rows[{ri}][{ci}] is missing; use 'Not displayed.'"
                        )
                    elif not isinstance(cell, (str, int, float, bool)):
                        errors.append(f"{tprefix}.rows[{ri}][{ci}] has an unsupported type")
        if _nonempty_string(heading):
            table_titles_by_heading[heading] = titles

    if len(headings) != len(set(headings)):
        errors.append("section headings must be unique")
    for feature in features:
        expected = FEATURE_SECTIONS.get(feature)
        if expected and expected not in headings:
            errors.append(f"required section missing for {feature}: {expected}")
        elif expected:
            titles = table_titles_by_heading.get(expected, set())
            for required_title in FEATURE_TABLES[feature]:
                if required_title not in titles:
                    errors.append(f"required table missing for {feature}: {required_title}")

    limitations = spec.get("limitations")
    if not isinstance(limitations, list) or any(not _nonempty_string(x) for x in limitations):
        errors.append("limitations must be an array of non-empty strings")
    if complete is False and isinstance(limitations, list) and not limitations:
        errors.append("limitations must describe why an incomplete audit is incomplete")

    if errors:
        raise SpecValidationError(errors)
    return spec

