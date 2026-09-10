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
   local today through the same date next year and flags overlapping listed
   stays that appear to belong to the same person (exact / normalized /
   possible matches). The source report does not display reservation status.

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
- **Credential-safe** — login identity, timezone, and password references come
  from the existing PMS Setup property contract; password values resolve from
  the host's established environment/secrets path at run time.
- **Testable before full setup** — an explicitly gated standalone test mode can
  use protected environment secrets or an owner-only JSON file without exposing
  credentials to the agent or accepting them on the command line.
- **Self-contained** — browser report-acquisition rules and one-shot PDF
  verification ship with this skill; no report-pull helper is required.
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
  the bundled acquisition contract, top-level or property-scoped credential
  secrets, property-time rules, and Kolo audit logging.
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
- `scripts/one_shot_pdf.py` — preserves the first original PDF response bytes,
  rejects spent-key reuse, and verifies searchable parser input.
- `scripts/pms_access.py` — resolves the existing PMS Setup property/login
  contract without modifying it or printing credentials.
- `scripts/pms_login.py` — injects credentials directly into the persistent
  ChoiceADVANTAGE browser, reuses an authenticated session, and in standalone
  test mode can select only the exact official **Skip MFA** control when
  explicitly authorized.
- `scripts/pms_report_pull.py` — selects the two exact reports, sets the required
  arrival window, captures the original PDF response in the same browser target,
  and permits only one fresh-parameter retry.
- `scripts/pms_audit_run.py` — the single bounded entrypoint for login, both
  source pulls, parsing, reconciliation, analysis, and final PDF rendering.
- `references/report-acquisition.md` — ChoiceADVANTAGE login and report capture
  procedure using the access contract created by PMS Setup.
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

### Standalone access for a live read-only test

Use the host's protected secret configuration for
`PMS_RECON_TEST_USERNAME`, `PMS_RECON_TEST_PASSWORD`, and
`PMS_RECON_TEST_TIMEZONE`; optionally bind `PMS_RECON_TEST_PROPERTY_CODE`.
Do not paste these values into chat or put them in this repository. Alternatively,
copy `references/test-access.example.json` to a location outside the repository,
replace its placeholders locally, run `chmod 600` on it, and set
`PMS_RECON_TEST_ACCESS_FILE` to that path.

Then run:

```bash
python3 scripts/readiness.py --test-access --hotel CAF15
python3 scripts/pms_login.py --hotel CAF15 --test-access --allow-skip-mfa
```

Or run the entire bounded test in one command:

```bash
python3 scripts/pms_audit_run.py \
  --hotel CAF15 \
  --test-access --allow-skip-mfa \
  --output-root /persistent/path/pms-audit-tests
```

If the operator already authenticated manually in the persistent browser, use
`--session-only --timezone America/Los_Angeles` for that one test. Session-only
runs are never suitable for cron.

The second command prints only a redacted JSON state. While ChoiceADVANTAGE
offers the official option, it selects the exact **Skip MFA** control on each
new login. If the option disappears, it returns `needs_mfa` instead of attempting
another bypass. Standalone test access remains read-only and must not be used by
a schedule or email-delivery path.

After this skill's acquisition procedure saves the fresh source reports:

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
