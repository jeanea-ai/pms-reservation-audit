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


def _same_date_next_year(value: date) -> date:
    """Return the same calendar date next year, clamping leap day to Feb 28."""
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def inclusive_windows(today: date) -> dict[str, tuple[date, date]]:
    """Return the permanent inclusive ledger and future-reservation windows."""
    return {
        "ledger_past_30": (today - timedelta(days=30), today),
        "future_12_months": (today, _same_date_next_year(today)),
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
    fields = ("guest_name", "check_in", "check_out", "primary_email", "rooms_booked")
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
    # Hotel departure dates are exclusive: checkout on another stay's arrival
    # date is back-to-back, not an overlapping occupied night.
    return bool(li and lo and ri and ro and max(li, ri) < min(lo, ro))


def _emails(reservation: dict[str, Any]) -> set[str]:
    values = [reservation.get("primary_email"), reservation.get("secondary_email")]
    return {str(v).strip().casefold() for v in values if v and v != "Not displayed." and "@" in str(v)}


def classify_match(left: dict[str, Any], right: dict[str, Any]) -> tuple[str | None, str]:
    left_name = str(left.get("guest_name", "")).strip()
    right_name = str(right.get("guest_name", "")).strip()
    if not left_name or not right_name:
        return None, "missing guest name"
    if not _dates_overlap(left, right):
        return None, "stay dates do not overlap"
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
    if similarity >= 0.72 and same_dates:
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


def room_count(reservation: dict[str, Any]) -> int:
    """Return the explicit booking count; never reinterpret a room number."""
    if "rooms_booked" not in reservation:
        raise ValueError("reservation is missing rooms_booked; do not use room_number as a count")
    value = reservation["rooms_booked"]
    if isinstance(value, bool):
        raise ValueError("rooms_booked must be a positive integer")
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ValueError("rooms_booked must be a positive integer") from None
    if str(value).strip() != str(count) or count < 1:
        raise ValueError("rooms_booked must be a positive integer")
    return count


def _unique_displayed_values(
    reservations: Iterable[dict[str, Any]], *keys: str
) -> list[str]:
    values = {
        str(item.get(key, "")).strip()
        for item in reservations
        for key in keys
        if item.get(key) not in (None, "", "Not displayed.")
    }
    return sorted(values, key=lambda value: (value.casefold(), value))


def consolidate_guest_stays(reservations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse room-level records only when guest and stay dates agree.

    This is a reporting transformation, not a broader duplicate match. Fuzzy
    names and different date ranges remain separate even when they belong to
    the same duplicate-analysis group.
    """
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in reservations:
        key = (
            normalize_name(str(item.get("guest_name", ""))),
            str(item.get("check_in", "")).strip(),
            str(item.get("check_out", "")).strip(),
        )
        buckets[key].append(item)

    stays = []
    for key in sorted(buckets):
        members = sorted(buckets[key], key=reservation_identity)
        names = _unique_displayed_values(members, "guest_name")
        stays.append({
            "guest_name": min(names, key=lambda value: (len(value), value.casefold(), value))
            if names else "Not displayed.",
            "entered_guest_names": names,
            "check_in": key[1] or "Not displayed.",
            "check_out": key[2] or "Not displayed.",
            "rooms_booked": sum(room_count(item) for item in members),
            "source_reservation_count": len(members),
            "folio_numbers": _unique_displayed_values(members, "folio_number"),
            "account_numbers": _unique_displayed_values(members, "account_number"),
            "confirmation_numbers": _unique_displayed_values(members, "confirmation_number"),
            "emails": _unique_displayed_values(members, "primary_email", "secondary_email"),
            "statuses": _unique_displayed_values(members, "status"),
        })
    return stays


def _best_connection_evidence(
    component: list[int], evidence: dict[tuple[int, int], tuple[str, str]]
) -> tuple[str, str, dict[str, int]]:
    """Choose the strongest set of links needed to connect a group.

    A redundant fuzzy edge must not downgrade an otherwise exact group. This is
    Kruskal's minimum spanning tree with exact evidence preferred over
    normalized evidence, and normalized evidence preferred over possible.
    """
    rank = {"exact": 0, "normalized": 1, "possible": 2}
    parent = {node: node for node in component}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    selected: list[tuple[str, str]] = []
    candidates = [
        (rank[value[0]], i, j, value)
        for (i, j), value in evidence.items()
        if i in parent and j in parent
    ]
    for _, i, j, value in sorted(candidates):
        left, right = find(i), find(j)
        if left == right:
            continue
        parent[right] = left
        selected.append(value)
        if len(selected) == len(component) - 1:
            break
    if len(selected) != len(component) - 1:
        raise ValueError("duplicate group evidence is not connected")

    breakdown = {name: sum(1 for strength, _ in selected if strength == name) for name in rank}
    strength, reason = max(selected, key=lambda value: rank[value[0]])
    summary = ", ".join(f"{count} {name}" for name, count in breakdown.items() if count)
    return strength, f"{reason}; required links: {summary}", breakdown


def analyze_duplicates(reservations: Iterable[dict[str, Any]], today: date) -> dict[str, Any]:
    source = list(reservations)
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("each reservation must be an object")
        room_count(item)
    unique = sorted(deduplicate_reservations(source), key=reservation_identity)
    active = [item for item in unique if not is_cancelled(item)]
    windows = inclusive_windows(today)
    by_window: dict[str, list[dict[str, Any]]] = {"future_12_months": []}
    for name, (start, end) in windows.items():
        if name != "future_12_months":
            continue
        for item in active:
            arrival = _parsed_date(item.get("check_in"))
            if arrival and start <= arrival <= end:
                by_window[name].append(item)

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
        for seed in sorted(edges):
            if seed in visited:
                continue
            stack, component = [seed], []
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node); component.append(node); stack.extend(sorted(edges[node] - visited, reverse=True))
            strength, reason, breakdown = _best_connection_evidence(component, evidence)
            members = [items[index] for index in sorted(component)]
            category, category_reason = group_category(members)
            rooms = sum(room_count(item) for item in members)
            guest_stays = consolidate_guest_stays(members)
            groups.append({"match_strength": strength, "match_reason": reason, "match_breakdown": breakdown, "category": category, "category_reason": category_reason, "reservation_count": len(members), "guest_stay_count": len(guest_stays), "rooms_total": rooms, "reservations": members, "guest_stays": guest_stays})
        output["windows"][window_name] = {"records_reviewed": len(items), "groups_found": len(groups), "rooms_total": sum(g["rooms_total"] for g in groups), "groups": groups}
    return output
