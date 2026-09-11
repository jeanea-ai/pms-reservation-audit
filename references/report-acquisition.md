# ChoiceADVANTAGE report acquisition

Read this reference only when a fresh Guest Ledger or Future Reservations PDF
must be retrieved. These rules belong to PMS Reconciliation itself; no sibling
report-pull skill is required.

## Use PMS Setup access without changing it

Read `kolo-hotels/config/<CODE>.json`, which is created and owned by
`mf-hotel-pms-setup`. An audit must never edit that file.

- Authentication mode: `pms.auth_mode`, either `direct_login_no_mfa` or
  `okta_sso`.
- Direct ChoiceADVANTAGE username: `pms.legacy_username`.
- Okta username: matching `pms.username` and `identity.ops_email` when that
  richer contract exists; otherwise the connected Gmail profile identity is
  resolved at login time for a deployed PMS Setup 3.0 Choice record.
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

For `okta_sso`, validate the richer identity and Google Voice fields strictly
when any of them are present. Deployed PMS Setup 3.0 Choice records may instead
contain `legacy_username` plus `okta_mfa: email` and no identity block. Consume
that record unchanged and derive the Okta email from the platform-bound Gmail
profile. The exact Okta UI must still present **Verify with your phone** and
**Receive a code via SMS**; otherwise fail closed. `MATON_API_KEY` must be
present in the runtime. The Gmail password is not read by this skill.

### Standalone test access

If the operator explicitly requested testing before PMS Setup can be completed,
use `resolve_access(..., allow_test_access=True)` through
`scripts/pms_login.py --test-access`. The protected credential inputs and their
owner-only file contract are documented in `SKILL.md`; never place the file in
the repository or pass credentials as command-line values.

For explicitly authorized temporary testing, `--browser-saved-login` may reuse
Chrome's saved login. It selects the same exact field names below and receives
only one Boolean stating whether both fields are nonempty; it must never return,
log, or inspect either value. This path is not the production credential store.

## Direct login

Before navigation, choose the exact ChoiceADVANTAGE page target. Prefer the
reports page over login, other ChoiceADVANTAGE pages, and stale report-proxy
targets. Refuse equally valid matches instead of selecting by tab order.

1. Open `https://www.choiceadvantage.com/choicehotels/sign_in.jsp`.
2. Set `input[name="j_username"]` and `input[name="j_password"]` through
   the DOM and dispatch `input` and `change` events. Verify only field
   lengths.
3. Select **Login** once. Stop on an unidentified transient interstitial rather
   than guessing or resubmitting credentials.
4. If offered **Migrate** or **Continue**, select **Continue**. Never migrate
   the account unless the operator explicitly authorizes that account change.
5. For an explicitly authorized standalone test, the login command may select
   the exact official **Skip MFA** control once per login when ChoiceADVANTAGE
   displays it. If it is absent, pause for the operator. Normal access must never
   bypass MFA.

The login contract uses stable DOM semantics rather than visual interpretation:
`input[name="j_username"]`, `input[name="j_password"]`, an exact **Login** or
**Sign in** control, the traditional-login **Continue** handler, and the exact
**Skip MFA** label. Locate each visible control immediately before use, perform
the proven DOM click under a user-gesture context, then verify the resulting page
state. Do not substitute coordinate input merely because it appears more human;
the live ChoiceADVANTAGE report menu did not respond to that approach. If any
required selector or exact control is absent, stop; never guess from screen
position, screenshots, OCR, or approximate text.

Use a deterministic 0.75-second gap between site-facing navigations and clicks
unless a measured test justifies another value from 0.25 through 3 seconds. This
is bounded load pacing, not human imitation. Never add random mouse movement,
fingerprint changes, stealth patches, proxy rotation, or CAPTCHA-solving.

Recognize CAPTCHA widgets, human-verification or unusual-traffic text, access
blocks, known challenge URLs, and report HTTP 403/429 responses as
`bot_challenge`. Stop immediately without consuming the normal report retry.
The operator must complete any permitted verification manually in the
persistent browser before one new bounded run.

## Okta SSO with Google Voice SMS

When PMS Setup selects `okta_sso`, open a dedicated
Choice Connect tab and select the exact **Connect Now** control. Discover one
exact-origin `choicehotels.okta.com` DOM surface. It may be the top-level page,
a same-process iframe (use an isolated execution context for its frame), or an
out-of-process iframe (use its flattened attached CDP session). Refuse multiple
surfaces, lookalike hosts, and coordinate/OCR fallbacks.

Populate the username/password fields in memory and follow exact Okta controls.
Select **Verify with your phone**, record the request time immediately before
selecting **Receive a code via SMS**, and request no more than one SMS per run.
Use `scripts/gmail_otp.py` to verify the connected Gmail profile against
`identity.ops_email` when configured, or to supply the missing runtime identity
for the deployed PMS Setup 3.0 shape. List only messages from
`txt.voice.google.com` after that
timestamp, and accept only one unambiguous six-digit code from the newest fresh
message. Never save or print message bodies, credentials, or the OTP. A
transient read may retry once, but an authentication error, mailbox mismatch,
ambiguous code, or timeout fails closed without resending SMS.

After verification, accept only Choice Connect or ChoiceADVANTAGE destinations.
If an existing SSO session bypasses credential entry, require the displayed
Choice Connect identity to match the configured or gateway-resolved operations
email. Select the exact
**Choice Advantage** app control and verify the normal report menu before report
acquisition. The live selector and post-auth route require one controlled test
by the developer who has the Okta-enabled account; update selectors from
sanitized DOM metadata only, never from credentials or message bodies.

An on-demand audit has a four-minute overall deadline by default. Individual
page/report waits are capped by both their own timeout and the remaining overall
budget, so two reports and bounded retries cannot multiply a 90-second setting
into an unexpectedly long run. Scheduled commands explicitly use an eight-minute
audit budget inside a fifteen-minute job budget.

## Navigate and set parameters

Open **Run > Reports** or, after authentication,
`https://www.choiceadvantage.com/choicehotels/ReportViewStart.init`. Refresh
the browser snapshot after navigation and select the exact report name.

- Guest Ledger: keep its current/default business-date parameters.
- Future Reservations: set the start to property-local today and the end to
  the same calendar date next year, inclusive. Leave both Booking Date fields
  blank. Dispatch `input` and `change` events and verify all four displayed
  values.

Live form contract verified against ChoiceADVANTAGE build 10.283.3 on
2026-09-10:

- Menu IDs: `GuestLedgerReport` and `FutureReservationsReport`.
- Submit ID: `doSubmit`, with exact visible label `Submit`.
- Guest Ledger default date: `queryDatePast`; do not replace it.
- Guest Ledger PDF form: `genericReportsForm` whose action contains
  `ReportProxyServlet.proxy`.
- Future Reservations PDF form: `ReportFutureReservationsForm`; arrival fields
  are `arrivalDateFrom` and `arrivalDateTo`, booking fields are
  `bookingDateFrom` and `bookingDateTo`.

`scripts/pms_report_pull.py` owns this contract. It temporarily intercepts the
validated native form submission and performs the same authenticated,
same-origin request without navigating into Chrome's PDF viewer. It transfers
the original response bytes in bounded chunks. Agents must not repeat these DOM
operations themselves.

## Capture the one-shot source PDF

`reportServerKey` is single-use. Arm response capture for
`ReportProxyServlet.proxy?ie=pdf` before selecting **Submit**. Capture the
original network response body, not a rendered-page export.

1. Select **Submit** exactly once while the supported executor intercepts that
   form submission before browser navigation.
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

The supported implementation is `scripts/pms_report_pull.py`, normally called
only through `scripts/pms_audit_run.py`. Browser tools, `Page.printToPDF`, PDF
viewer downloads, screenshots, OCR, and hand-written capture scripts are not
fallbacks. After the command's bounded retry is exhausted, ask its emitted
`next_question` and stop.
