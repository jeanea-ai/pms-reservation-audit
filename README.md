# PMS Reservation Audit

A read-only audit skill for **ChoiceADVANTAGE (SkyTouch) PMS**, designed to
run on-demand inside [Kolo](https://kolo.ai) (built on OpenClaw). It performs
two reviews and reports results as a clean, printable **PDF** plus a scannable
chat summary:

## Features

1. **Guest Ledger balance review** — pulls a fresh Guest Ledger report and
   lists nonzero **No Show** or **Cancelled** accounts with arrivals in the past
   30 days plus every **Group** account regardless of arrival date or balance.

2. **Duplicate reservation review** — pulls one Future Reservation Report from
   local today through the same date next year, treats each exposed row as
   reserved future inventory, and flags overlapping stays that appear to belong to the same
   person (exact / normalized / possible matches).

3. **PDF report** — renders every review into a styled, easy-to-read PDF of no
   more than three pages
   (title/metadata header, sectioned tables, right-aligned currency with
   negative balances in red, and a read-only disclosure). Generated
   deterministically via `scripts/audit_report.py`.

## Key properties

- **Read-only** — never edits, cancels, merges, creates, or modifies any
  reservation, folio, account, balance, or report.
- **Fresh every run** — pulls current data from ChoiceADVANTAGE each time;
  never reuses a prior run's results.
- **Credential-safe** — credentials are read from the host's secrets file at
  run time; nothing is hardcoded in the skill.
- **Low-input** — reuses a valid session and an unambiguous active/default
  property, pausing only for MFA, missing access, or unresolved property choice.
- **Verified output** — every complete or partial audit produces one validated,
  structurally checked PDF. Full visual QA runs when layout code changes, not
  during routine data-only audits.

## Usage

This is a Kolo/OpenClaw skill. Install it to your Kolo pod, then ask
naturally — for example:

- "Audit my ChoiceADVANTAGE guest ledger."
- "Who owes us money in the guest ledger?"
- "Find duplicate reservations in ChoiceADVANTAGE."
- "Are there repeat offenders booking with us?"

## Structure

- `SKILL.md` — the complete skill definition (trigger description + workflow).
- `scripts/audit_report.py` — deterministic PDF renderer (reportlab, with a
  discovered headless-Chromium fallback). Strictly validates the JSON report
  spec and atomically publishes a verified PDF after at most two attempts:

  ```bash
  python3 scripts/audit_report.py audit_spec.json -o report.pdf
  ```
- `scripts/readiness.py` — read-only pod preflight for required files, renderer,
  helper skill contract and optional declared version, top-level or
  property-scoped credential JSON, property-time rules, and Kolo audit logging.
  Missing explicit timezone guidance is deferred to the live audit. It never
  displays credential values.
- `scripts/report_spec.py` — strict required-field, section, table-shape,
  missing-value, and incomplete-warning validation.
- `scripts/duplicate_analysis.py` — deterministic inclusive windows,
  cancellation filtering, identity deduplication, overlap-aware match
  classification, strongest-required-link group evidence, compact group-level
  report consolidation, and counts/totals.
- `scripts/source_to_input.py` — parses fresh Guest Ledger and Future
  Reservation PDFs (or layout-preserving extracted text), reconciles printed
  subtotals/counts, and atomically writes schema-versioned `audit_input.json`.
- `scripts/audit_pipeline.py` — one post-extraction command that builds the
  validated spec, runs duplicate analysis, and atomically renders the PDF. It
  deliberately contains no login, browser, or ChoiceADVANTAGE automation.
- `assets/caf15_audit_report.pdf` — approved visual reference for report layout,
  colors, typography, tables, and footers.

## Validate

```bash
python3 -m pip install -r requirements.txt
python3 scripts/readiness.py
python3 -m unittest discover -s tests
```

After the existing browser/report-pull automation downloads fresh reports:

```bash
python3 scripts/source_to_input.py \
  --guest-ledger guest-ledger.pdf \
  --future-reservations future-reservations.pdf \
  --property-local-date 2026-09-08 \
  --reviewed-at "2026-09-08 17:00 America/Los_Angeles" \
  --expected-feature guest_ledger \
  --expected-feature duplicates \
  -o audit_input.json
```

Then analyze and render:

```bash
python3 scripts/audit_pipeline.py audit_input.json --spec-out report-spec.json
```

This writes `PMS Reconciliation CAF15.pdf` by default; pass `-o <path>` to
override.

The input is schema version `1`; reservation room counts use `rooms_booked` so
physical room numbers cannot be mistaken for quantities.

## Schedule a test-only report

The repository includes an opt-in command-cron installer for synthetic report
rendering. It never accesses ChoiceADVANTAGE and marks every artifact `TEST ONLY`.
Preview without changing the pod:

```bash
python3 scripts/schedule_test.py \
  --cron "15 9 * * *" \
  --timezone America/Los_Angeles \
  --output-dir /persistent/path/pms-audit-tests \
  --dry-run
```

Remove `--dry-run` to create the command job. The safe default is `--no-deliver`;
use `--announce-to kolo:<chat-id>` only for a verified destination. The installer
checks the installed OpenClaw flags, uses `--command-argv`, explicit cwd/timezone
and a 120-second timeout, and will not replace a same-named job.
