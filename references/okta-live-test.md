# Controlled live Okta verification

Use this only on a Choice property that PMS Setup has already verified for
`pms.auth_mode: okta_sso`. Do not change a direct
login property merely to exercise this branch.

## Preconditions

PMS Setup owns all access state. Richer property records contain matching valid
`pms.username` and `identity.ops_email` plus verified Google Voice fields.
Deployed PMS Setup 3.0 Choice records may instead have `legacy_username`,
`okta_mfa: email`, and no identity block; the audit resolves the Okta username
from the connected Gmail profile and does not modify that record.
The PMS password must resolve through `PMS_PASSWORD_<CODE>`, `PMS_PASSWORD`, or
the owner-only property entry in `.secrets.json`. `MATON_API_KEY` must be
injected into the same runtime. Never paste any of these values into chat or
command arguments.

From the installed skill directory, run:

```bash
python3 scripts/readiness.py --hotel <CODE>
python3 scripts/pms_audit_run.py \
  --hotel <CODE> \
  --output-root ~/.openclaw/workspace-main/pms-audits
```

Do not add `--test-access`, `--allow-skip-mfa`, `--session-only`, or
`--browser-saved-login`. The same command works for direct-login properties;
PMS Setup selects the authentication branch.

## Expected evidence

- Readiness reports `PMS Setup access` and `Okta Gmail gateway` as PASS without
  displaying values.
- A dedicated Choice Connect tab opens.
- Exactly one SMS is requested.
- The configured Gmail mailbox is identity-checked before message bodies are
  read.
- The run reaches the normal ChoiceADVANTAGE report menu, acquires both source
  PDFs, and produces a structurally verified reconciliation PDF.
- Chat output remains aggregate-only; credentials, OTPs, guest rows, and email
  bodies do not appear.

Do not retry a failed live run automatically. Preserve its run directory and
report the stage/error once.

## Sanitized selector report after a failure

If the failure is a missing selector or redirect, return only:

- failing stage and redacted error;
- top-level URL origin and title;
- each frame's URL origin and whether it is same-process or OOPIF;
- input `name`, `type`, `autocomplete`, and `aria-label` attributes with all
  values omitted;
- visible button/link labels;
- whether Choice Advantage opens in the same tab or one new tab;
- final URL origin after successful Okta verification.

Do not include screenshots, DOM body text, field values, cookies, tokens,
credentials, OTPs, Gmail message bodies, guest data, or report contents. A Loom
is optional only if this sanitized metadata cannot identify the mismatch.
