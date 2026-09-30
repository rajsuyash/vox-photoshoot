# Donna Photoshoot admin and customer communications audit

Date: 2026-09-30. Site: https://photo.voxdonna.com. Code baseline: `8526fbfdba02b6972fd3858be5c40954ef163470`.

## Initial finding

The core admin panel exists, but it does not yet cover the full customer-support and communications workflow. There is no application-owned signup welcome email or credit-purchase receipt sender. Razorpay notifications are enabled; their actual recipient and delivery remain to be verified.

This is a code-based preliminary assessment and an execution plan, not a completed production audit. “Built” below means an implementation was found, not that every acceptance test passed. Infrastructure settings and email delivery have not been tested in this audit. No application code, payment settings, or customer communications were changed.

Assumption: signup confirmation means a welcome email after the first successful registration. Routine login emails are optional; security notices for suspicious sign-ins are a separate requirement. Google already verifies the email address used for Google signup.

## Preliminary feature inventory

| Capability | Current assessment | Evidence and remaining check |
| --- | --- | --- |
| Google signup and verified email | Built | `oauth_google.py:132`, `auth.py:129`. Check new vs returning users, account linking, and trial credits. |
| Signup welcome email | Missing in app | `auth.py:129`, `app.py:177`. New accounts receive a browser welcome banner; no email trigger or sender. |
| Admin invitations and password recovery | Missing email workflow | `admin.py:29`, `static/admin.html`. Admin gets a generated password; no emailed invitation, setup link, or password-reset flow. |
| Purchase and monthly renewal emails | Partial; delivery unverified | `subscriptions.py:91` enables Razorpay notifications. `subscriptions.py:115` commits credits and invoice without an app receipt. |
| Invoice email | Partial | `billing.py:96` requests Razorpay invoice email delivery. Issuing an invoice is distinct from confirming payment and the resulting credit balance. |
| Failed-payment, halted-subscription, cancellation, refund emails | Partial provider coverage | Subscription state handling exists. No application-owned lifecycle emails. Verify Razorpay coverage, recipients, refund coverage, and overlap. |
| Email delivery history, retries, bounce handling, resend | Missing in app | No notification/outbox/delivery schema or corresponding admin controls found. |
| Workspace creation, balances, credit grants, invoice creation | Built | `admin.py:22`, `admin.py:47`, `app.py:1813`, `static/admin.html`. Test permissions, validation, and failure handling. |
| Customer search, profile, suspension, session revocation | Incomplete | Workspace list exists. No equivalent full customer-management UI or suspend/revoke workflow found. |
| Member invitations and role management | Partial | Owner/member memberships and CLI helpers exist; no complete admin lifecycle UI. |
| All-customer billing and subscription support view | Incomplete | Workspace billing APIs and invoices exist; admin lacks a consolidated payment/subscription/reconciliation view. |
| Attribution for administrative changes | Partial | Credit ledger records adjustments, note, and time, but no acting administrator field or general admin audit trail. |
| Failed-generation support and usage reporting | Partial | Job state, heartbeat/recovery, and logs exist; no consolidated admin triage or business dashboard found. |
| Account/data export, deletion, retention workflow | Needs design and verification | No complete admin workflow found. Audit storage, backups, financial-record retention, and current policies before choosing deletion behavior. |
| Admin hardening, alerts, backups, restore readiness | Needs runtime/infrastructure verification | Global admin guard and secure session primitives exist. Rate limits, admin MFA, revocation behavior, alarms, and restore capability need evidence. |

Razorpay documents customer emails for successful subscription charges, failures, halted subscriptions, and cancellations. Verify these first to avoid duplicating working provider messages. A notification flag does not prove receipt by the correct customer or show Donna credits added. [Razorpay subscription notifications](https://razorpay.com/docs/payments/subscriptions/notifications).

## Phase 1 — Establish the inventory and evidence

1. Trace signup, password login, admin-created accounts, checkout, first paid cycle, renewal, refund, cancellation, credit grant, generation failure, and recovery through UI → API → database → provider.
2. Inspect production configuration read-only: deployed revision, available email integration, Razorpay notification/customer settings, enabled webhook events, billing recipients, and relevant logs. Keep credentials and customer data out of the report.
3. Record each capability as verified, partial, missing, or unknown, with code/config evidence, an acceptance test, and severity. Separate missing UI from missing backend capability.
4. Use existing connected integrations first. If an email provider is already configured, evaluate it before proposing another service.

Deliverable: a feature matrix with evidence and a prioritized gap list. No real payments or customer emails are needed for this phase.

## Phase 2 — Audit transactional emails first

### Required customer journeys

| Trigger | Expected customer message | Acceptance condition |
| --- | --- | --- |
| First successful signup | Branded welcome, actual trial credits granted, start-photoshoot link, support contact | One welcome per new account; returning sign-ins do not resend it. A capped trial must not promise credits that were not granted. |
| Admin invitation | Expiring, single-use account setup or workspace invitation link | Correct recipient and workspace; existing users are not given a new password. Never email a plaintext password. |
| First paid credit cycle and each renewal | Payment confirmation with credits added, resulting balance, amount/currency/tax, invoice link, and payment reference | Only after verified payment and committed ledger/invoice; mandate authorization alone is not a credit purchase. |
| Payment failure or halted subscription | Clear payment-update/retry action and accurate subscription status | No credits promised; distinguish temporary retry from halted service. Reuse verified Razorpay coverage where sufficient. |
| Cancellation | Renewal stopped and accurate explanation of remaining credits | Sent only after confirmed cancellation; delayed webhooks do not generate contradictory messages. |
| Refund | Refunded amount and actual credit adjustment | Handle partial/full refunds and delayed events; do not claim an adjustment until it is committed. |
| Password reset, where password accounts remain supported | Expiring reset link and subsequent password-change notice | No account enumeration, reusable token, or plaintext password; test session invalidation. |

Confirm the recipient policy: welcome to the account email; financial messages to the validated billing contact, with an explicit owner fallback where appropriate. Test owner and billing-contact addresses that differ. Decide which messages the provider owns and which Donna owns before implementing templates.

### Delivery reliability audit

- Trace the actual trigger, not just the template or provider API response. Verify accepted, delivered, bounced, and permanently failed states separately; delivery does not prove the email was read.
- Check sender authentication and configuration: SPF, DKIM, DMARC, verified sender, reply-to support address, provider mode, and suppression/bounce behavior.
- Check crash recovery and retry behavior. If Donna must send its own transaction emails, the smallest reliable design is a durable database outbox written in the same transaction as the account/credit event, then dispatched after commit.
- Deduplicate business events by account/event or payment ID. Use provider idempotency when supported. Do not promise exactly-once email delivery where a crash after sending but before recording success can cause a repeat.
- Verify provider failure does not roll back a paid credit purchase or successful account creation. Failed messages must remain visible and retryable, with an auditable manual resend.
- Separate transactional mail from promotional campaigns. Add promotional opt-in and unsubscribe handling only if marketing emails are introduced.

Deliverable: trigger/recipient/provider map, verified delivery evidence from an authorized test inbox, and a small fix specification for uncovered journeys. Sending test emails and charging payments require separate execution authorization; use provider test mode where supported.

## Phase 3 — Audit the admin support workflow

Audit these essential capabilities:

1. **Customer and workspace support:** search by email/brand, view account/workspace details and memberships, edit billing contact/GST details, invite/remove members, suspend/reactivate access, and revoke sessions.
2. **Credits and billing:** see balance and ledger history, grant/debit credits with actor and required reason, prevent duplicate adjustments on retries, inspect invoices/payments/subscription status, and reconcile provider payment IDs against the local ledger. Keep corrections append-only.
3. **Communications:** see notification event, recipient, delivery state, failure reason, attempt count, and authorized resend history. Avoid exposing reset/invite tokens in logs or support views.
4. **Generation support:** inspect failed/stalled jobs, credit reservation/refund outcomes, retry eligibility, and storage/provider errors. A retry must not silently double-charge credits.
5. **Reporting and data handling:** measure signups, paying customers, payments/refunds, credits consumed, failed generations, and provider cost where available. Define export/deletion/retention requirements and verify them against existing policies.

Test admin workspace creation with a duplicate owner or another mid-operation failure: the current route creates a workspace before creating its owner, so partial provisioning needs examination. Test repeated credit-grant requests: current default grant idempotency uses a fresh UUID, so network retries may represent separate adjustments.

Deliverable: tested admin journey checklist and the minimum controls needed for daily support. Defer coupons, affiliate tools, CRM automation, and elaborate analytics until a concrete requirement exists.

## Phase 4 — Audit security and operations

- **Authorization:** ordinary users cannot call admin endpoints; members cannot perform owner-only billing actions; altered workspace/job/product IDs cannot expose another workspace. Exercise APIs directly, not only hidden UI controls.
- **Revocation:** remove membership or archive/suspend an account/workspace and test already-issued sessions. Session lookup and workspace-list filtering use different paths, so visible removal alone is insufficient evidence.
- **Authentication:** audit brute-force/rate-limit controls, admin MFA or equivalent enforced identity protection, OAuth state validation, account linking, session expiry, logout-all, and CSRF/origin checks on mutations.
- **Audit trail:** identify who changed credits, roles, billing contacts, or access; record target, action, outcome, reason, and time. Review log access/retention and exclude credentials, payment details, and secret tokens.
- **Money and event integrity:** verify webhook signatures, duplicate and out-of-order handling, retry behavior, unknown subscriptions, reconciliation, and visibility of failed events. Razorpay explicitly documents duplicate delivery and unordered events. [Razorpay webhook best practices](https://razorpay.com/docs/webhooks/best-practices).
- **Operations:** verify alarms for API errors, failed/stuck jobs, payment/webhook failures, exhausted email retries, and spending anomalies. Inspect backup retention and perform a controlled restore test; merely having a backup setting is insufficient.

Use the [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html), [password recovery guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html), and [logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) as security checklists, not a claim of compliance.

Deliverable: evidence-backed security/operations findings with reproducible failures, impact, and recommended fixes.

## Minimum acceptance scenarios

- New Google signup, returning Google login, capped trial grant, and admin-created account/invitation.
- Expired/reused invite/reset link and a Google-only user requesting password recovery.
- First paid cycle, renewal, failed charge, cancellation, partial/full refund, and refund arriving before the charge webhook.
- Checkout confirmation plus repeated/reordered webhooks: one credit posting and one logical receipt event per payment.
- Provider outage and worker crash around dispatch: credits remain correct, unsent email recovers, possible duplicate delivery is handled or documented.
- Different owner/billing emails, invalid address, suppression/bounce, delivered message, and authorized resend.
- Ordinary user/admin boundary, owner/member boundary, cross-workspace object access, and revoked membership with an existing session.
- Retried manual credit adjustment, failed workspace provisioning, stalled generation, and credit refund verification.
- Alarm delivery and controlled database restore with records compared against the source.

## Priority and completion criteria

**P0:** fix any demonstrated cross-workspace/admin access defect, incorrect financial posting/receipt, unsafe account recovery, or unrecoverable transaction loss.

**P1:** signup welcome/invitations, proven purchase/renewal notification coverage, reliable email delivery history/retries, customer support controls, attributable credit adjustments, and operational alerts.

**P2:** low-credit reminders, optional completed-generation emails, richer analytics, and convenience automation after the core journeys are dependable.

The audit is complete when each applicable feature has evidence, each critical journey has an acceptance result, unknown infrastructure settings are resolved or explicitly recorded, and every gap has severity and a concrete fix scope. Do not label “enabled” or “API returned 200” as “delivered.”

Implementation follows the audit and is a separate task. Use small verified batches of at most approximately five files, with review/approval between batches as required by the project instructions. Run configured type checks, linting, and relevant behavioral tests on each code batch; currently no project type-checker or linter is configured. This planning task changes documentation only.
