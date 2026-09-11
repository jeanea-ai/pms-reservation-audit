---
name: "choiceadvantage-guest-ledger-duplicate-audit"
version: "0.4.2"
description: >
  Self-contained, on-demand, read-only ChoiceADVANTAGE (SkyTouch) audit for guest-ledger
  recent No Show and Cancelled balances, all Group balances, duplicate reservations, and
  double bookings. Trigger on natural requests such as "audit my guest
  ledger", "who owes us money?", "check no-shows and cancellations", or "find
  duplicate reservations". Pulls
  fresh PMS data and never edits reservations, folios, accounts, or reports.
metadata:
  version: "0.4.2"
---

# PMS Reconciliation

Run this on demand only. It is strictly read-only: never edit, cancel, merge,
create, or otherwise modify any PMS record. Every audit must use fresh reports;
never reuse report data from an earlier run.

## Standalone test access

When the operator explicitly requests a test but PMS Setup is incomplete, use
the gated standalone test-access path. Never ask for or accept a username,
password, OTP, or test-access JSON in chat. The operator must provide credentials
through protected environment secrets or an owner-only file outside the skill:

- `PMS_RECON_TEST_USERNAME`
- `PMS_RECON_TEST_PASSWORD`
- `PMS_RECON_TEST_TIMEZONE`
- optional `PMS_RECON_TEST_PROPERTY_CODE`
- or `PMS_RECON_TEST_ACCESS_FILE`, pointing to an owner-only (`chmod 600`),
  non-symlink JSON file matching `references/test-access.example.json`

Do not combine the file and direct environment forms. Confirm the non-secret
contract with `python3 scripts/readiness.py --test-access --hotel <CODE>`, then
log in with:

`python3 scripts/pms_login.py --hotel <CODE> --test-access --allow-skip-mfa`

`--allow-skip-mfa` is authorized only for this explicit testing path. On every
login it may select exactly one visible control whose label is **Skip MFA** while
ChoiceADVANTAGE offers that official option. It must not match approximate labels,
bypass another challenge, migrate the account, or store an OTP/MFA token. If the
exact control is absent, stop with `needs_mfa` and let the operator complete MFA.
All access and report data are still real and read-only; describe resulting
artifacts as test output and do not schedule or email them.

## Normal execution path

Use a fresh, minimal worker/task for an audit run. After loading this file, do
not inspect repository history, reread reference files, run readiness checks, or
perform a narrative preflight unless the command reports that exact need. The
execution budget is one shell command and one response of at most 250 words.
The command itself uses no model calls.

1. Resolve the property from the request, saved default, or unambiguous active
   property. Ask only if multiple properties remain ambiguous.
2. Run exactly one bounded command; do not navigate the reports UI with browser
   tools and do not invent another CDP, download, print, screenshot, or OCR path:

   `python3 scripts/pms_audit_run.py --hotel <CODE> --output-root <persistent-directory>`

   For explicitly authorized standalone access, add `--test-access
   --allow-skip-mfa`. When the operator has already signed in manually for a
   one-time test, use `--session-only --timezone <IANA-zone>` instead. Never put
   `--session-only` on a schedule.

   The command owns authentication, exact report selection, property-local date
   entry, original-response capture, the single fresh-key retry, searchable-PDF
   verification, source parsing, subtotal/count reconciliation, duplicate
   analysis, test labeling, and atomic rendering. It produces redacted JSON and
   never prints report contents or credentials.
3. Deliver the generated PDF with a short findings summary. The command owns
   date windows, cancellation filtering, identity deduplication, match/category
   decisions, totals, spec validation, structural PDF verification, retry, and
   atomic publication. Do not redo those stages in chat.

Run `python3 scripts/readiness.py` only after installation or an environment,
credential, renderer, or platform change—not before every audit.

If the command fails or produces an incomplete audit, ask its exact
`next_question`. Do not retry it in the same turn, hand-edit JSON, inspect guest
records in chat, or begin open-ended browser experiments.

## Authentication and access

- Credentials come from the configured secrets file at runtime. Never display
  them or ask the owner to paste them into chat or skill instructions.
- On the Okta migration interstitial choose **Continue**, not **Migrate**.
- Outside explicitly authorized standalone testing, never bypass MFA. During an
  authorized test login, only the exact official **Skip MFA** control may be
  selected; otherwise let the owner complete the challenge.
- Confirm the active property and required reports are accessible. Record any
  inaccessible report, page, record, or field as a limitation.
- Do not claim completion after a session expires or a required page is missed.

## Guest Ledger input

Pull a new Guest Ledger with current/default parameters. Review every page and
locate **No-Show Accounts**, **Cancelled Accounts**, and **Group**. Keep only
nonzero-balance No Show and Cancelled reservations whose arrival date is within
the inclusive window from local today minus 30 days through local today. Include
every account in the Group section regardless of its status, arrival date, or
balance. Collect guest name, account number, status, arrival date, and balance.

Never invent an identifier. Use `Not displayed.` for unavailable fields.
Reconcile the complete extracted No-Show, Cancelled, and Group section subtotals
to the printed report before applying the 30-day filter to No Show and Cancelled
accounts; a mismatch makes the audit incomplete. Never apply an age filter to
Group accounts. Do not include Checked Out or In House accounts from sections
other than Group, older No Show or Cancelled accounts, or zero-balance No Show
or Cancelled accounts in the findings.

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
