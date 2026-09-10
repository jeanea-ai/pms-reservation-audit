# ChoiceADVANTAGE report acquisition

Read this reference only when a fresh Guest Ledger or Future Reservations PDF
must be retrieved. These rules belong to PMS Reconciliation itself; no sibling
report-pull skill is required.

## Use PMS Setup access without changing it

Read `kolo-hotels/config/<CODE>.json`, which is created and owned by
`mf-hotel-pms-setup`. An audit must never edit that file.

- Direct ChoiceADVANTAGE username: `pms.legacy_username`.
- Property timezone: top-level `timezone`.
- Password: resolve `PMS_PASSWORD_<CODE>`, then `PMS_PASSWORD`, then
  `kolo-hotels/config/.secrets.json` at `<CODE>.pms_password`.
- For backward compatibility, an older `pms_username` beside
  `pms_password` may be read when `pms.legacy_username` is absent.

Never print credentials, password references, OTPs, or report keys. If the
property config is absent, the PMS block is incomplete, or access is not
verified, ask the operator to finish PMS Setup; do not repair setup from this
skill.

Run `python3 scripts/pms_access.py --hotel <CODE>` for a non-secret preflight.
It reports only whether the established access fields resolve. Browser login
code may import `resolve_access()` from that module; never print its returned
username or password.

### Standalone test access

If the operator explicitly requested testing before PMS Setup can be completed,
use `resolve_access(..., allow_test_access=True)` through
`scripts/pms_login.py --test-access`. The protected credential inputs and their
owner-only file contract are documented in `SKILL.md`; never place the file in
the repository or pass credentials as command-line values.

## Login

1. Open `https://www.choiceadvantage.com/choicehotels/sign_in.jsp`.
2. Set `input[name="j_username"]` and `input[name="j_password"]` through
   the DOM and dispatch `input` and `change` events. Verify only field
   lengths.
3. Select **Login**. A transient "try again" interstitial may be retried with
   the same DOM-verified values at most twice.
4. If offered **Migrate** or **Continue**, select **Continue**. Never migrate
   the account unless the operator explicitly authorizes that account change.
5. For an explicitly authorized standalone test, the login command may select
   the exact official **Skip MFA** control once per login when ChoiceADVANTAGE
   displays it. If it is absent, pause for the operator. Normal access must never
   bypass MFA.

## Navigate and set parameters

Open **Run > Reports** or, after authentication,
`https://www.choiceadvantage.com/choicehotels/ReportViewStart.init`. Refresh
the browser snapshot after navigation and select the exact report name.

- Guest Ledger: keep its current/default business-date parameters.
- Future Reservations: set the start to property-local today and the end to
  the same calendar date next year, inclusive. Dispatch `input` and
  `change` events and verify the displayed values.

## Capture the one-shot source PDF

`reportServerKey` is single-use. Arm response capture for
`ReportProxyServlet.proxy?ie=pdf` before selecting **Submit**. Capture the
original network response body, not a rendered-page export.

1. Select **Submit** exactly once.
2. Treat the key as consumed as soon as its response arrives, regardless of
   response status, length, or content type.
3. Read the response body once. Do not probe, reload, issue a range or HEAD
   request, duplicate the navigation, or request the same key again.
4. If the body is empty or does not begin `%PDF-`, fail that attempt. Reopen
   the parameters and generate a fresh key for one bounded retry.
5. Save the first `%PDF-` body immediately to a new file. Never overwrite an
   existing artifact.
6. Validate and preserve it with:

   `python3 scripts/one_shot_pdf.py --response-file <captured-body> --output <source.pdf> --state-file <run-state.json> --key-id <run-local-key-identifier> --require-text`

The verifier stores only a SHA-256 fingerprint of the identifier, preserves
the exact response bytes, rejects reuse and empty/non-PDF content, and requires
extractable text.

`Page.printToPDF`, browser print/save-as-PDF, screenshots, and rasterized PDF
exports are forbidden for Guest Ledger and Future Reservations parser input.
This does not prohibit the skill's deterministic renderer from generating the
final PMS Reconciliation PDF.

Return the saved source path, report name, property code, and effective date
range. Never return the report key.
