---
name: "choiceadvantage-guest-ledger-duplicate-audit"
version: "0.4.7"
description: >
  Self-contained, on-demand or explicitly scheduled, read-only ChoiceADVANTAGE
  (SkyTouch) audit for guest-ledger
  recent No Show and Cancelled balances, all Group balances, duplicate reservations, and
  double bookings. Trigger on natural requests such as "audit my guest
  ledger", "who owes us money?", "check no-shows and cancellations", or "find
  duplicate reservations". Pulls
  fresh PMS data and never edits reservations, folios, accounts, or reports.
metadata:
  version: "0.4.7"
requires: [mf-hotel-pms-setup]
---

# PMS Reconciliation

Run this on demand unless the operator explicitly requests a schedule. It is
strictly read-only: never edit, cancel, merge, create, or
otherwise modify any PMS record. Every audit must use fresh reports; never reuse
report data from an earlier run.

## Production access through PMS Setup

`mf-hotel-pms-setup` owns the property record and credentials. This skill is a
read-only consumer and must never create, update, or repair PMS Setup state.
After onboarding or a credential/configuration change, run exactly one
non-secret contract check:

`python3 scripts/pms_access.py --hotel <CODE>`

It must report `source: mf-hotel-pms-setup`, `test_only: false`, and presence
only for username/password. Production audits then use the normal command with
no `--test-access`, `--test-access-file`, `--session-only`,
`--browser-saved-login`, or `--allow-skip-mfa` flags. The deterministic resolver
reads the property code, timezone, vendor and legacy username from PMS Setup,
then resolves the password from its established protected environment or
`.secrets.json` path without displaying any value.

If PMS Setup exposes only a `pms.password_ref`, stop with the emitted credential
provider error until the owning skill's resolver is integrated. Never interpret
the reference, query an undocumented vault, or fall back to test credentials for
a production run.

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

For an operator without terminal access, run `scripts/pms_access_setup.py` with
an absolute output path outside the skill, then open only its emitted
`127.0.0.1` URL in a new Kolo browser tab. The operator enters credentials
directly. The agent must not inspect the form DOM, take screenshots, read field
values, or open the resulting file. The one-use page binds only to loopback,
expires after ten minutes, logs no requests, writes mode `0600`, refuses
implicit replacement, and exits after saving. Pass only the non-secret path to
`--test-access-file`.

`--allow-skip-mfa` is authorized only for this explicit testing path. On every
login it may select exactly one visible control whose label is **Skip MFA** while
ChoiceADVANTAGE offers that official option. It must not match approximate labels,
bypass another challenge, migrate the account, or store an OTP/MFA token. If the
exact control is absent, stop with `needs_mfa` and let the operator complete MFA.
All access and report data are still real and read-only; describe resulting
artifacts as test output and never email them. Never put standalone test access
on a schedule.

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

   When the operator explicitly accepts Chrome's saved credentials for temporary
   cron testing, add `--browser-saved-login`, `--timezone <IANA-zone>`, and
   `--allow-skip-mfa`. This path checks only whether both login fields autofilled;
   it never reads or returns their values. It reuses an active session first and
   stops if autofill or the exact official **Skip MFA** control is unavailable.
   Treat it as test-only, not as production credential storage.

   If saved credentials appear only after clicking a field, do not retry,
   inspect the password chooser, or simulate arrow-key selection. That chooser
   is privileged browser UI and cannot be identity-checked through the page DOM.
   Use the loopback setup page and `--test-access-file` instead.

   The command owns authentication, exact report selection, property-local date
   entry, original-response capture, the single fresh-key retry, searchable-PDF
   verification, source parsing, subtotal/count reconciliation, duplicate
   analysis, test labeling, and atomic rendering. It produces redacted JSON and
   never prints report contents or credentials.
3. Deliver the generated PDF with only the aggregate values in the command's
   redacted `summary` object. Do not open the source PDFs, audit-input JSON, or
   final PDF to compose the chat response. Never reproduce a guest or group
   name, account/confirmation/folio number, stay date, email address, or
   row-level balance in chat. Detailed findings belong only in the attached
   owner-facing PDF. A complete successful run needs no follow-up question.
   Never offer to investigate, merge, cancel, or edit individual PMS records.
   The command owns
   date windows, cancellation filtering, identity deduplication, match/category
   decisions, totals, spec validation, structural PDF verification, retry, and
   atomic publication. Do not redo those stages in chat.

Run `python3 scripts/readiness.py` only after installation or an environment,
credential, renderer, or platform change—not before every audit.

If the command fails or produces an incomplete audit, ask its exact
`next_question`. Do not retry it in the same turn, hand-edit JSON, inspect guest
records in chat, or begin open-ended browser experiments.

## Authentication and access

- Credentials come from the configured secrets file at runtime, except for the
  explicitly authorized temporary browser-saved test path. Never display them,
  read autofilled values, or ask the owner to paste them into chat or skill
  instructions.
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

## Command schedule

Create a schedule only when the operator explicitly asks. Scheduled audits are
deterministic OpenClaw command jobs: they start no model turn, use production
access from `mf-hotel-pms-setup`, pull fresh source reports, and run the same
`pms_audit_run.py` entrypoint as an on-demand audit. Never schedule
`--session-only`, browser-saved access, standalone test credentials, MFA bypass,
or a hand-built shell command.

Translate the requested frequency into exactly one form:

- A local clock time or calendar pattern uses `--cron` plus the property's IANA
  timezone. Ask one short question only if the intended time, day, or timezone
  is genuinely ambiguous.
- A fixed elapsed interval uses `--every`, such as `30m`, `6h`, or `1d`. An
  interval is not a promise to run at the same local wall-clock time after DST.

Preview without changing cron state, then use the identical command without
`--dry-run`:

`python3 scripts/schedule_audit.py --hotel <CODE> --cron "15 9 * * *" --timezone America/Los_Angeles --output-root <persistent-directory> --dry-run`

For an interval, replace `--cron ... --timezone ...` with `--every <interval>`.
Use `--exact` only when the operator requires an exact top-of-hour time; normal
cron scheduling may use OpenClaw's bounded staggering. Use `--disabled` when the
operator asks to inspect or test the job before enabling it.

The default delivery is `--no-deliver`. Add `--announce-to kolo:<chat-id>` only
when that exact destination is known and requested. Announcement output is the
command's aggregate-only JSON, including the final report path; it does not open
the generated PDF in the Kolo browser or expose row-level findings.

The scheduler uses exact argv boundaries, an explicit skill directory and
output root, bounded execution/no-output timeouts, and one job name per property.
It refuses to overwrite a same-name job. If it returns `status: exists`, ask its
exact `next_question`; never delete or replace the existing schedule without the
operator's answer. Inspect created jobs with `openclaw cron list --json --all`.

Manual and scheduled runs share a per-property lock in the output root. If a run
is already active, the second command fails safely instead of overlapping the
shared ChoiceADVANTAGE browser. Every run returns the browser to the reports menu
after acquisition; the final audit PDF is always written directly to disk.
