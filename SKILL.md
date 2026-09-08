---
name: "choiceadvantage-guest-ledger-duplicate-audit"
version: "0.3.1"
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

## Readiness

Before the first audit after installation or an environment change, run:

`python3 scripts/readiness.py`

It changes nothing and prints one PASS, FAIL, or SKIP line per dependency.
Do not begin an audit when it reports FAIL. Resolve the named dependency, then
run readiness once more. A SKIP is a live browser/session check that must be
confirmed during the audit; it is not proof that access works.

Readiness validates the credential JSON structure without printing values and
the installed report-pull skill's login/navigation contract—not merely file
existence. A helper version is enforced when declared; a versionless legacy
helper is accepted when its contract is intact. Missing explicit property-local
timezone guidance is deferred as a live-session check rather than blocking the
audit.

## Low-input operating policy

Complete the requested audit with as little owner interaction as possible.
Proceed automatically whenever the required choice can be resolved safely from
the active session, saved configuration, current property context, or the
request itself. Do not ask for routine confirmation, date ranges, report
parameters, output preferences, or permission to continue read-only work.

- Reuse a valid authenticated session when one exists. Otherwise authenticate
  with the configured secrets automatically.
- If exactly one property is available, select it without asking. If multiple
  properties are available, use a saved default or the active property when it
  is unambiguous. Ask the owner only when no reliable property choice exists.
- Use the default Guest Ledger parameters and the fixed duplicate-search
  windows defined below unless the owner explicitly requests something else.
- If the owner asks generally to "run the audit" or equivalent, run both the
  Guest Ledger review and Duplicate reservation review. If the request names
  only one feature, run only that feature.
- Do not interrupt the run with progress questions. Ask only when owner action
  is required for MFA, missing/expired access, or an unresolved property
  choice. Consolidate non-blocking limitations into the final report.
- Deliver one consolidated result and its PDF after the accessible work is
  complete.

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
3. Resolve the property under the low-input policy above. Ask only if multiple
   properties remain genuinely ambiguous after checking saved and active
   property context. Never guess between unresolved properties.
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
Pass that explicit local date and the collected reservation records through
`scripts/duplicate_analysis.py`; its inclusive boundaries, cancellation filter,
identity deduplication, name matching, email classification, counts, and room
totals are the reporting source of truth. The previous window is
`today - 90 days` through `today`, inclusive; the next window is `today`
through `today + 90 days`, inclusive. A reservation on the shared `today`
boundary is assigned once, to the previous window.

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

## Required PDF deliverable

After every completed or partially completed audit, automatically generate and
deliver a clean, clear, easy-to-read PDF. Do not ask whether the owner wants a
PDF. If the request covers both audit features, place both in one PDF. If it
covers only one feature, include only that feature.

Build a strict JSON spec with `complete` (boolean), `audit_features` (one or
both of `guest_ledger` and `duplicates`), all required metadata, both explicit
90-day ranges, `completion_warning`, sections, tables, and `limitations`.
Missing cells must be the literal `Not displayed.`; never use null or an empty
string. Each table row must have exactly the same number of cells as its unique,
non-empty headers. The renderer validates required feature sections and tables.
An incomplete spec must include both a completion warning and at least one
limitation; a complete spec must not carry a warning.

Use this filename pattern, with unsafe filename characters removed:
`choiceadvantage-audit-{property}-{YYYY-MM-DD-HHmm}.pdf`.

### Mandatory report template

Use the same structure and visual style as the approved CAF15 audit report on
every run. Treat the rules below as the default report template, not as optional
design suggestions. Do not substitute a different theme or reorganize the
report unless the owner explicitly asks for a different format.

Use US Letter pages in portrait orientation with a white background and about
0.5-inch margins. The report may use additional pages when the data requires
them, but every continuation page must preserve the same styling.

Apply this visual system consistently:

- Dark navy (`#203F68` or the closest supported equivalent) for the main title,
  table headers, totals, and emphasized summary lines.
- Teal (`#0B7477` or the closest supported equivalent) for numbered section
  headings, heading rules, and the read-only notice.
- Alternating white and very light blue-gray (`#EEF3F8`) table rows with thin,
  light gray-blue borders.
- Red for negative balances/credits, formatted with parentheses, such as
  `($150.50)`. Display positive balances in dark text, such as `$150.50`.
- A clean sans-serif font throughout. Use a bold 18-20 point main title,
  14-16 point section headings, 9-10 point body text, and no table text smaller
  than 8 points.

Build the report in this order:

1. **Title block** - "ChoiceADVANTAGE Guest Ledger & Duplicate Reservation
   Audit" in dark navy, followed by a thin navy rule.
2. **Audit metadata** - property code and name, reviewed date/time and named
   time zone, business date, previous 90-day range, and next 90-day range.
3. **Read-only notice** - a short teal sentence confirming that no records were
   modified. If incomplete, place a prominent red **INCOMPLETE AUDIT** warning
   immediately below this notice.
4. **1. Guest Ledger Balance Review** - completion statement, No-Show Accounts
   table and total, Group Accounts table and total, all-account-sections summary
   when available, and short bullet callouts for notable findings.
5. **2. Duplicate Reservation Review** - search explanation, then the four
   duplicate-review sections in their required order. Use compact summary tables
   for high-volume historical groups and detailed reservation tables for
   individual matches. Follow each period with a bold navy summary line showing
   records reviewed, cancellations excluded, groups found, and rooms booked when
   available. Place possible spelling-variant matches in a separate bullet.
6. **Notes & Limitations** - teal heading and concise bullets explaining missing
   emails, incomplete ranges, inaccessible records, or other limitations.

Use the same table conventions throughout:

- Dark navy header row with high-contrast text.
- Guest Ledger columns: guest/group name, available account identifiers, and
  balance.
- Historical duplicate summary columns: guest name, active reservation count,
  and match strength.
- Detailed duplicate columns: guest name, available identifiers, arrival,
  departure, nights when available, rate when available, and rooms.
- Repeat the table header after every page break. Keep related reservation rows
  together when practical, wrap long values, and prevent clipping or overlap.
- Clearly show "None found" for an empty required section instead of omitting
  the section.

Place `Read-only audit - generated by Kolo` at the lower-left of every page and
`Page {number}` at the lower-right. Keep footer placement and color consistent.
Do not include passwords, authentication details, secrets-file paths, or hidden
system data.

Before delivery, render every PDF page to images and visually inspect it.
Correct clipped text, overlapping elements, broken tables, blank unintended
pages, unreadable characters, or inconsistent spacing, then render and check
again. Do not deliver an unverified PDF.

Attach the verified PDF to the final response and include a short plain-text
summary of the most important findings. If PDF generation fails after one safe
retry, deliver the structured text report, clearly state that the PDF could not
be produced, and do not claim full delivery success.

`scripts/audit_report.py` writes to a unique temporary file, validates the PDF
signature, and atomically replaces the destination only after success. It makes
at most two total attempts. A non-zero renderer exit, missing/invalid PDF, or
exception is failure even if an older destination PDF already exists; never
deliver that stale file as the current audit.

## Approvals / logging

This skill is read-only — no approval is required under AGENTS.md. Log the
delivered result via `kolo log-action` (no `--category`) after each completed
review.
