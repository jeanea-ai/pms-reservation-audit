---
name: "choiceadvantage-guest-ledger-duplicate-audit"
version: "0.3.2-candidate.2"
description: >
  On-demand, read-only ChoiceADVANTAGE (SkyTouch) audit for guest-ledger
  balances, No Shows, Groups, duplicate reservations, repeat guests, and
  double bookings. Trigger on natural requests such as "audit my guest
  ledger", "who owes us money?", "check no-shows and groups", "find duplicate
  reservations", "check the last 90 days", or "find repeat offenders". Pulls
  fresh PMS data and never edits reservations, folios, accounts, or reports.
---

# ChoiceADVANTAGE Guest Ledger & Duplicate Reservation Audit

Run this on demand only. It is strictly read-only: never edit, cancel, merge,
create, or otherwise modify any PMS record. Every audit must use fresh reports;
never reuse report data from an earlier run.

## Normal execution path

1. Reuse a valid authenticated ChoiceADVANTAGE session when available.
   Otherwise follow the installed `choiceadvantage-report-pull` skill for login,
   report navigation, trusted clicks, and report retrieval. Do not invent a new
   browser/CDP method during an audit.
2. Resolve the property from the request, saved default, or unambiguous active
   property. Ask only if multiple properties remain ambiguous. Pause for MFA or
   expired access.
3. Pull the requested fresh report(s) and extract every applicable record. For
   a full audit pull the Guest Ledger plus separate previous-90 and next-90
   Reservation Activity reports.
4. Write one versioned `audit_input.json` matching
   `tests/fixtures/audit_input.json`. Use `rooms_booked` for the number of rooms;
   never put a physical `room_number` in that field.
5. Run exactly one post-extraction command:

   `python3 scripts/audit_pipeline.py audit_input.json -o report.pdf --spec-out report-spec.json`

6. Deliver the generated PDF with a short findings summary. The command owns
   date windows, cancellation filtering, identity deduplication, match/category
   decisions, totals, spec validation, structural PDF verification, retry, and
   atomic publication. Do not redo those stages in chat.

Run `python3 scripts/readiness.py` only after installation or an environment,
credential, helper-skill, renderer, or platform change—not before every audit.

If report extraction fails, refresh current browser state and make one retry
using the report-pull skill's documented fallback. If it still fails, preserve
the successful artifacts, mark the audit incomplete, and ask one actionable
question. Do not begin open-ended browser experiments.

## Authentication and access

- Credentials come from the configured secrets file at runtime. Never display
  them or ask the owner to paste them into chat or skill instructions.
- On the Okta migration interstitial choose **Continue**, not **Migrate**.
- Never bypass MFA. Let the owner complete it.
- Confirm the active property and required reports are accessible. Record any
  inaccessible report, page, record, or field as a limitation.
- Do not claim completion after a session expires or a required page is missed.

## Guest Ledger input

Pull a new Guest Ledger with current/default parameters unless the owner names a
date. Review every page and locate **No Shows** and **Groups**. For every nonzero
balance collect:

- guest or group name;
- folio, account, and confirmation number when displayed;
- balance;
- No Shows or Groups category.

Never invent an identifier. Use `Not displayed.` for unavailable fields. Report
the entries, count, and subtotal for each section separately. Reconcile the
extracted subtotals to the printed report subtotals; a mismatch makes the audit
incomplete.

## Duplicate-reservation input

Use the property's local date. Pull two searches and keep their source ranges
explicit:

- previous: local today minus 90 days through today, inclusive;
- next: today through today plus 90 days, inclusive.

Collect each reservation's entered guest name, available identifiers, arrival,
departure, `rooms_booked`, displayed email addresses, and status. Exclude
cancelled records. The shared-today boundary and duplicate identities are
counted once by `duplicate_analysis.py`.

Matching is deterministic:

- **exact**: entered names are identical;
- **normalized**: names match after case, punctuation, spacing, or trailing
  number normalization;
- **possible**: a spelling variant, shared email, or similar name plus matching
  or overlapping dates provides evidence.

Groups use the strongest set of links needed to connect all members. A
redundant fuzzy link cannot downgrade an otherwise exact group. Possible matches
must explain their evidence and must never be merged in ChoiceADVANTAGE.

Classification:

- **Duplicates**: at least one displayed email uses a company domain.
- **Repeat Offenders**: displayed emails are personal providers only, or no
  email is displayed.

When the source report has no email column, retain the required category but
state that it is provisional and that company-domain classification could not
be verified.

## Completion and output

Every report includes the property, owner-local review time and named zone,
business date, searched ranges, read-only disclosure, findings, and limitations.
An incomplete run must carry an `INCOMPLETE AUDIT` warning; a complete run must
not.

The deterministic renderer keeps the approved CAF15 visual style, validates
required sections and table shapes, verifies that the output is a readable,
nonempty PDF with the expected headings when the PDF library is available, and
publishes atomically after at most one fallback attempt. Never deliver a stale
destination file after failure.

Full page-by-page visual inspection is required only when the report template,
renderer, fonts, or page-layout code changes. Normal data-only audits use the
pipeline's structural verification.

If PDF generation fails after its bounded retry, deliver the structured text
result, say the PDF failed, and do not claim full delivery success.

After delivery, log the result with `kolo log-action` without `--category`.
