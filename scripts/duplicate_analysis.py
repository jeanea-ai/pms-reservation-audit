#!/usr/bin/env python3
"""Deterministic helpers for read-only duplicate reservation analysis."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
import re
from typing import Any, Iterable


PERSONAL_EMAIL_DOMAINS = {
    "aol.com", "gmail.com", "googlemail.com", "hotmail.com", "icloud.com",
    "live.com", "me.com", "msn.com", "outlook.com", "proton.me",
    "protonmail.com", "yahoo.com", "ymail.com",
}
CANCELLED_STATUSES = {"cancelled", "canceled", "cancel", "void", "voided"}


def inclusive_windows(today: date) -> dict[str, tuple[date, date]]:
    """Return explicit inclusive 90-day lookback/lookahead windows."""
    return {
        "previous_90": (today - timedelta(days=90), today),
        "next_90": (today, today + timedelta(days=90)),
    }


def normalize_name(name: str) -> str:
    text = re.sub(r"\s+\d+\s*$", "", name.strip(), flags=re.ASCII)
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return " ".join(text.split())


def is_cancelled(reservation: dict[str, Any]) -> bool:
    return str(reservation.get("status", "")).strip().casefold() in CANCELLED_STATUSES


def reservation_identity(reservation: dict[str, Any]) -> tuple[str, str]:
    for key in ("confirmation_number", "folio_number", "account_number"):
        value = str(reservation.get(key, "")).strip()
        if value and value != "Not displayed.":
            return key, value.casefold()
    fields = ("guest_name", "check_in", "check_out", "primary_email", "rooms")
    return "fallback", "|".join(str(reservation.get(k, "")).strip().casefold() for k in fields)


def deduplicate_reservations(reservations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for reservation in reservations:
        identity = reservation_identity(reservation)
        if identity not in seen:
            seen.add(identity)
            result.append(reservation)
    return result


def _parsed_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _dates_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    li, lo = _parsed_date(left.get("check_in")), _parsed_date(left.get("check_out"))
    ri, ro = _parsed_date(right.get("check_in")), _parsed_date(right.get("check_out"))
    return bool(li and lo and ri and ro and max(li, ri) <= min(lo, ro))


def _emails(reservation: dict[str, Any]) -> set[str]:
    values = [reservation.get("primary_email"), reservation.get("secondary_email")]
    return {str(v).strip().casefold() for v in values if v and v != "Not displayed." and "@" in str(v)}


def classify_match(left: dict[str, Any], right: dict[str, Any]) -> tuple[str | None, str]:
    left_name = str(left.get("guest_name", "")).strip()
    right_name = str(right.get("guest_name", "")).strip()
    if not left_name or not right_name:
        return None, "missing guest name"
    if left_name == right_name:
        return "exact", "guest names are entered identically"
    ln, rn = normalize_name(left_name), normalize_name(right_name)
    if ln and ln == rn:
        return "normalized", "guest names match after case, punctuation, spacing, or trailing-number normalization"

    similarity = SequenceMatcher(None, ln, rn).ratio() if ln and rn else 0.0
    shared_email = _emails(left) & _emails(right)
    same_dates = left.get("check_in") == right.get("check_in") and left.get("check_out") == right.get("check_out")
    if similarity >= 0.86:
        return "possible", f"guest names are similar ({similarity:.2f})"
    if shared_email:
        return "possible", "reservations share an email address"
    if similarity >= 0.72 and (same_dates or _dates_overlap(left, right)):
        return "possible", "similar guest names have identical or overlapping stay dates"
    return None, "insufficient deterministic evidence"


def email_kind(email: str) -> str:
    value = email.strip().casefold()
    if "@" not in value:
        return "missing"
    domain = value.rsplit("@", 1)[1].rstrip(".")
    return "personal" if domain in PERSONAL_EMAIL_DOMAINS else "company"


def group_category(reservations: Iterable[dict[str, Any]]) -> tuple[str, str]:
    emails = sorted({email for item in reservations for email in _emails(item)})
    company = [email for email in emails if email_kind(email) == "company"]
    if company:
        return "Duplicates", "at least one reservation uses a company-domain email"
    return "Repeat Offenders", "reservations use only personal email addresses or no displayed email"


def analyze_duplicates(reservations: Iterable[dict[str, Any]], today: date) -> dict[str, Any]:
    source = list(reservations)
    unique = sorted(deduplicate_reservations(source), key=reservation_identity)
    active = [item for item in unique if not is_cancelled(item)]
    windows = inclusive_windows(today)
    by_window: dict[str, list[dict[str, Any]]] = {name: [] for name in windows}
    assigned: set[tuple[str, str]] = set()
    for name, (start, end) in windows.items():
        for item in active:
            arrival = _parsed_date(item.get("check_in"))
            identity = reservation_identity(item)
            if arrival and start <= arrival <= end and identity not in assigned:
                by_window[name].append(item)
                assigned.add(identity)

    output: dict[str, Any] = {"windows": {}, "counts": {"input": len(source), "unique": len(unique), "cancelled_excluded": len(unique) - len(active)}}
    for window_name, items in by_window.items():
        edges: dict[int, set[int]] = defaultdict(set)
        evidence: dict[tuple[int, int], tuple[str, str]] = {}
        for i, left in enumerate(items):
            for j in range(i + 1, len(items)):
                strength, reason = classify_match(left, items[j])
                if strength:
                    edges[i].add(j); edges[j].add(i)
                    evidence[(i, j)] = (strength, reason)
        groups = []
        visited: set[int] = set()
        rank = {"exact": 0, "normalized": 1, "possible": 2}
        for seed in sorted(edges):
            if seed in visited:
                continue
            stack, component = [seed], []
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node); component.append(node); stack.extend(sorted(edges[node] - visited, reverse=True))
            pairs = [value for (i, j), value in evidence.items() if i in component and j in component]
            strength, reason = max(pairs, key=lambda value: rank[value[0]])
            members = [items[index] for index in sorted(component)]
            category, category_reason = group_category(members)
            rooms = sum(int(item.get("rooms", 0)) for item in members if str(item.get("rooms", "")).isdigit())
            groups.append({"match_strength": strength, "match_reason": reason, "category": category, "category_reason": category_reason, "reservation_count": len(members), "rooms_total": rooms, "reservations": members})
        output["windows"][window_name] = {"records_reviewed": len(items), "groups_found": len(groups), "rooms_total": sum(g["rooms_total"] for g in groups), "groups": groups}
    return output
