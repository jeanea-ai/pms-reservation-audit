# PMS Reservation Audit

A read-only audit skill for **ChoiceADVANTAGE (SkyTouch) PMS**, designed to
run on-demand inside [Kolo](https://kolo.ai) (built on OpenClaw). It performs
two reviews and reports results as a clean, printable **PDF** plus a scannable
chat summary:

## Features

1. **Guest Ledger balance review** — pulls a fresh Guest Ledger report,
   reads the **No Shows** and **Groups** sections directly in-system, and
   lists every account with an outstanding balance (plus per-section counts
   and totals).

2. **Duplicate reservation review** — two independent 90-day searches
   (previous and next), excludes cancelled reservations, flags reservations
   that appear to belong to the same person (exact / normalized / possible
   matches), and buckets each group into **Duplicates** (company-domain email
   present) or **Repeat Offenders** (personal/no email).

3. **PDF report** — renders every review into a styled, easy-to-read PDF
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
- **Verified output** — every complete or partial audit produces one PDF that
  is rendered to images and visually checked before delivery.

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
  headless-Chromium fallback). Reads a JSON report spec and emits a styled PDF:

  ```bash
  python3 scripts/audit_report.py audit_spec.json -o report.pdf
  ```
- `scripts/readiness.py` — read-only pod preflight for required files, renderer,
  helper skill, credentials path, property-time rules, and Kolo audit logging.
- `assets/caf15_audit_report.pdf` — approved visual reference for report layout,
  colors, typography, tables, and footers.

## Validate

```bash
python3 scripts/readiness.py
python3 -m unittest discover -s tests
```
