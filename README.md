# PMS Reservation Audit

A read-only audit skill for **ChoiceADVANTAGE (SkyTouch) PMS**, designed to
run on-demand inside [Kolo](https://kolo.ai) (built on OpenClaw). It performs
two reviews and reports results in a scannable, structured format:

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

## Key properties

- **Read-only** — never edits, cancels, merges, creates, or modifies any
  reservation, folio, account, balance, or report.
- **Fresh every run** — pulls current data from ChoiceADVANTAGE each time;
  never reuses a prior run's results.
- **Credential-safe** — credentials are read from the host's secrets file at
  run time; nothing is hardcoded in the skill.

## Usage

This is a Kolo/OpenClaw skill. Install it to your Kolo pod, then ask
naturally — for example:

- "Audit my ChoiceADVANTAGE guest ledger."
- "Who owes us money in the guest ledger?"
- "Find duplicate reservations in ChoiceADVANTAGE."
- "Are there repeat offenders booking with us?"

## Structure

- `SKILL.md` — the complete skill definition (trigger description + workflow).
