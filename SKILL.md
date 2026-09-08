---
name: "choiceadvantage-guest-ledger-duplicate-audit"
description: >
  On-demand, read-only review of ChoiceADVANTAGE (SkyTouch) PMS data. Activates
  on any ask to audit, review, check, or pull the ChoiceADVANTAGE guest ledger
  for outstanding balances in the No Shows or Groups sections, or to find, flag,
  or investigate duplicate or repeat-guest reservations. Casual triggers include:
  "audit my ChoiceADVANTAGE guest ledger", "check our no-shows and groups for
  balances", "who owes us money in the guest ledger?", "find duplicate
  reservations in ChoiceADVANTAGE", "are there repeat offenders booking with
  us?", "run a duplicate reservation audit", "check the last 90 days for
  duplicate bookings", "review ChoiceADVANTAGE for duplicate stays", "any
  groups with an outstanding balance?", "pull the guest ledger and flag no
  shows". Read-only: never edit, cancel, merge, create, or modify anything.
---

# ChoiceADVANTAGE Guest Ledger & Duplicate Reservation Audit

Read-only, on-demand audit of ChoiceADVANTAGE (SkyTouch) PMS. Every run
fetches **fresh** data live from ChoiceADVANTAGE — never reuse a prior
run's results or any cached report. There is no scheduled/background mode;
run only when the owner asks.

**Hard safety rule:** this skill is strictly READ-ONLY. Never edit, cancel,
merge, split, create, or otherwise modify reservations, folios, accounts,
balances, reports, or any other record in ChoiceADVANTAGE. If any step
would produce or require a write, stop and tell the owner it is out of scope.

## Access & authentication (every run)

1. Log in to ChoiceADVANTAGE via the `browser` tool — follow the
   `choiceadvantage-report-pull` skill's mechanics (navigate to
   `https://www.choiceadvantage.com`, fill `j_username`/`j_password` via
   `evaluate` + dispatched input/change events, click Login, and choose
   **Continue** (not **Migrate**) on any Okta interstitial). Read credentials
   from `kolo-hotels/config/.secrets.json` at run time — **never** ask the
   owner to paste credentials into this skill or into chat.
2. If the session prompts for multifactor authentication (MFA) or any other
   security step, pause and let the owner complete it. Do not attempt to
   bypass MFA and never store MFA codes.
3. If the account exposes multiple hotel properties, **confirm which
   property** to review before pulling anything. Never assume.
4. Confirm the required reports and reservation records can actually be
   viewed for that property. If a needed report/section/field is missing or
   blocked, note it as "could not access" in the report.
5. If access expires or ChoiceADVANTAGE asks for authentication again
   mid-review, **pause**, tell the owner, and guide them through restoring
   access before continuing. Do not paper over a lost session.

## Feature 1 — Guest Ledger balance review

Triggered by asks about the guest ledger, no-shows, groups, or outstanding
balances. Steps:

1. Pull a **new** Guest Ledger report (Run > Reports > Guest Ledger) using
   current/default parameters unless the owner specifies a date range.
2. The Guest Ledger generally cannot be exported to CSV — read it directly
   in the rendered report. Navigate **every applicable page and section**.
   If any page/section is skipped, state so and mark the review incomplete.
3. Locate the sections labeled **No Shows** and **Groups**. For each, find
   every account with an outstanding balance (balance ≠ 0).
4. For each qualifying entry collect: guest/group name, folio number, account
   number, confirmation number (if shown), outstanding balance, and the
   category (No Shows or Groups).
5. Identifier rule: treat "folio number", "account number", and "confirmation
   number" as references to ChoiceADVANTAGE's reservation/account identifiers.
   Collect all of them when shown separately. When the screen shows only one
   identifier (or uses the terms interchangeably), report it under the label
   the system shows. **Never invent a missing identifier.**

Report No Shows and Groups **separately**. For each section report: every
qualifying account, the count of accounts with outstanding balances, and the
total outstanding balance. State clearly when a section has no outstanding
balances.

## Feature 2 — Duplicate reservation review

Triggered by asks about duplicates, repeat offenders, or double bookings.
Two independent searches, reported separately:

- **Previous 90 days** — from today going back 90 days.
- **Next 90 days** — from today going forward 90 days.

Use the hotel property's **local** date (see AGENTS.md time rules — never
resolve "today" from the UTC pod clock). Exclude all **cancelled**
reservations. Do not double-count a reservation that appears in both windows.

### Finding related reservations

Flag reservations that are reasonably likely to belong to the same person.
Signals include (any one is enough to flag):

- Guest names that match exactly.
- Matching names with a trailing number (e.g. "John Smith 1", "John Smith 2").
- Minor spelling differences / likely typos (e.g. "John Smith" vs "John Smitth").
- Same/similar name plus identical stay dates.
- Same/similar name plus overlapping stay dates.
- Apparent connection via name, email, dates, room count, or other visible
  reservation fields.

When comparing names, ignore capitalization, extra spaces, punctuation, and
numbers appended to the end of the name.

Classify match strength:

- **Exact match** — names entered identically.
- **Normalized match** — names match after ignoring capitalization,
  punctuation, extra spaces, or a trailing number.
- **Possible match** — minor spelling differences or other info suggest the
  reservations may belong to the same person. **Flag possible matches** but
  explain *why* they appear related. Never auto-merge or alter them.

### Categories

Place every flagged group into exactly one category:

- **Duplicates** — at least one related reservation uses a company-domain
  email (e.g. `guest@companyname.com`, `traveler@constructioncompany.net`).
  Personal providers (Gmail, Yahoo, Outlook, Hotmail, AOL, iCloud, and the
  like) are NOT company domains. If a reservation has both a company email and
  a personal email, classify as Duplicates and report both, labeling the
  company vs secondary email when ChoiceADVANTAGE makes that distinction.
- **Repeat Offenders** — the related reservations contain only personal
  email addresses, or no email address.

State *why* each group landed in its category.

### Fields to collect per reservation in a flagged group

Guest name exactly as entered • folio number • account number •
confirmation number • check-in date • check-out date • number of rooms booked •
primary email • secondary email (if shown) • reservation status • match
strength • reason the reservations appear related • category.

When any field is unavailable, write **"Not displayed."** Never guess or
invent.

### Duplicate-review report format

Four sections, in order:

- Previous 90 Days — Duplicates
- Previous 90 Days — Repeat Offenders
- Next 90 Days — Duplicates
- Next 90 Days — Repeat Offenders

For every flagged group report: reason for the match, match strength, every
associated reservation, number of associated reservations, and total number of
rooms booked. State clearly when a section has no qualifying reservations.

## General reporting requirements

Every completed review must include:

- Hotel property reviewed.
- Date and time the review was performed (owner's local time, named zone).
- Date ranges searched.
- Any pages, records, reports, or fields that could not be accessed.
- A clear warning if the review could **not** be fully completed.

Do not claim a review completed successfully unless every applicable page,
date range, and record was actually reviewed. Keep the report scannable; use
tables where they help.

## Approvals / logging

This skill is read-only — no approval is required under AGENTS.md. Log the
delivered result via `kolo log-action` (no `--category`) after each completed
review.
