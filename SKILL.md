---
name: "choiceadvantage-guest-ledger-duplicate-audit"
version: "0.4.0"
description: >
  On-demand, read-only ChoiceADVANTAGE (SkyTouch) audit for guest-ledger
  recent No Show and Cancelled balances, duplicate reservations, and
  double bookings. Trigger on natural requests such as "audit my guest
  ledger", "who owes us money?", "check no-shows and cancellations", or "find
  duplicate reservations". Pulls
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
   a full audit pull the Guest Ledger plus one Future Reservation Report whose
   start date is local today and whose end date is the same calendar date next
   year (12 months ahead), both inclusive.
4. Save the fresh Guest Ledger and Future Reservation Report as PDF
   files, then build the versioned input deterministically:

   `python3 scripts/source_to_input.py --guest-ledger guest-ledger.pdf --future-reservations future-reservations.pdf --property-local-date YYYY-MM-DD --reviewed-at "YYYY-MM-DD HH:MM America/Los_Angeles" --expected-feature guest_ledger --expected-feature duplicates -o audit_input.json`

   The parser reconciles Guest Ledger subtotals and Future Reservation row
   counts to their printed totals and fails closed on a mismatch. It treats
   each Future Reservation line as one room record and never reinterprets a
   physical room number as `rooms_booked`.
5. Run exactly one analysis/render command:

   `python3 scripts/audit_pipeline.py audit_input.json -o report.pdf --spec-out report-spec.json`

6. Deliver the generated PDF with a short findings summary. The command owns
   date windows, cancellation filtering, identity deduplication, match/category
   decisions, totals, spec validation, structural PDF verification, retry, and
   atomic publication. Do not redo those stages in chat.

Run `python3 scripts/readiness.py` only after installation or an environment,
credential, helper-skill, renderer, or platform change—not before every audit.

If report extraction fails, refresh current browser state and make one retry
using the report-pull skill's documented fallback. If it still fails, preserve
the successful artifacts and run the same input command with every originally
requested `--expected-feature` but only the successfully retrieved report file(s).
The command marks the audit incomplete and prints one actionable `next_question`;
ask that exact question. Do not hand-edit JSON or begin open-ended browser experiments.

## Authentication and access

- Credentials come from the configured secrets file at runtime. Never display
  them or ask the owner to paste them into chat or skill instructions.
- On the Okta migration interstitial choose **Continue**, not **Migrate**.
- Never bypass MFA. Let the owner complete it.
- Confirm the active property and required reports are accessible. Record any
  inaccessible report, page, record, or field as a limitation.
- Do not claim completion after a session expires or a required page is missed.

## Guest Ledger input

Pull a new Guest Ledger with current/default parameters. Review every page and
locate **No-Show Accounts** and **Cancelled Accounts**. Keep only nonzero-balance
reservations whose arrival date is within the inclusive window from local today
minus 30 days through local today. Collect guest name, account number, status,
arrival date, and balance.

Never invent an identifier. Use `Not displayed.` for unavailable fields.
Reconcile the complete extracted No-Show and Cancelled section subtotals to the
printed report before applying the 30-day filter; a mismatch makes the audit
incomplete. Do not include Group, Checked Out, In House, older, or zero-balance
accounts in the findings.

## Duplicate-reservation input

Use the property's local date. Pull the **Future Reservation Report** with start
date set to local today and end date set to the same calendar date next year
(12 months ahead), inclusive. Do not substitute Reservation Activity reports.

Collect each reservation's entered guest name, account number, arrival and
departure. The Future Reservations report does not display email addresses or
reservation status; the parser records those fields as unavailable and treats
each listed row as reserved future inventory. It treats every physical report
row as one booked room. Duplicate identities are counted once by
`duplicate_analysis.py`.

Matching is deterministic:

- **exact**: entered names are identical and stay dates overlap;
- **normalized**: names match after case, punctuation, spacing, or trailing
  number normalization and stay dates overlap;
- **possible**: a spelling variant or shared email plus overlapping stay dates
  provides evidence.

Groups use the strongest set of links needed to connect all members. A
redundant fuzzy link cannot downgrade an otherwise exact group. Possible matches
must explain their evidence and must never be merged in ChoiceADVANTAGE.

In the report, consolidate every detected duplicate group to one row. Show the
entered guest name(s), every displayed account number, each overlapping stay
range, total rooms, and match evidence once. Keep folio and confirmation values
in structured analysis when present, but never display Folio or Confirmation
columns or labels in the PDF.

## Completion and output

Every report includes the property, owner-local review time and named zone,
business date, searched ranges, read-only disclosure, findings, and limitations.
An incomplete run must carry an `INCOMPLETE AUDIT` warning; a complete run must
not.

The PDF must preserve the approved CAF15 portrait style and contain no more than
three pages. If all findings cannot render within three pages, the renderer must
fail closed instead of silently dropping rows; deliver the structured result and
state that the PDF page limit was exceeded.

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

## Command-cron test schedule

Scheduling is test-only and opt-in. It renders the packaged synthetic fixture or
another explicitly supplied non-live `audit_input.json`; it never logs in, opens a
browser, accesses ChoiceADVANTAGE, or schedules stale PMS reports as a real audit.

Preview the exact command job without changing the pod:

`python3 scripts/schedule_test.py --cron "15 9 * * *" --timezone America/Los_Angeles --output-dir /persistent/path/pms-audit-tests --dry-run`

Remove `--dry-run` to create it. By default the job uses `--no-deliver`; pass
`--announce-to kolo:<chat-id>` only when that exact test destination is known.
The installer refuses to replace or duplicate an existing job with the same name.
Every generated PDF and filename says `TEST ONLY`. Use `openclaw cron list --json --all`
to inspect the job. This facility is not a scheduler for live PMS audits.
