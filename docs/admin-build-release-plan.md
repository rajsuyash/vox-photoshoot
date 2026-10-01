# Donna Photoshoot admin features: build and live-release plan

Prepared 2026-09-30 against commit `8526fbfdba02b6972fd3858be5c40954ef163470` for https://photo.voxdonna.com.

Input: [preliminary audit and acceptance checklist](admin-audit-plan.md). The original plan below is retained; implementation status is recorded separately here.

## Execution checkpoint — 2026-09-30

Update 2026-10-01: the user specifically approved sharing the prepared AWS response. It was submitted unchanged to case `179079869200146` at `08:29:14Z`; the correspondence is visible and case status is **Customer action completed**. Customer dispatch remains disabled pending SES production access. The runtime at the time of that response was `32ef82b`; later deployments are recorded below.

Current runtime is `86ef88e3fd3daa1479b3926a9cd3eea05730a70e`, which adds the separately requested model clothing controls. App Runner operation `dfa00c599830418b9444c59325157818` succeeded with image `86ef88e-clothing-20261001162841`, digest `sha256:fb4a96021d5e71db61d53df2aac6d3cf24755c69ae9db0acd99e32301d5b5135`. Health and the live clothing form pass. Ninety-eight other Python/HTML/JS/CSS/SQL files match the prior `d00a9be` archive byte for byte. `notification_test.py` and `admin_test.py` pass again against the exact current archive and disposable PostgreSQL, covering queue recovery/concurrency and membership/suspension/audit guards. App configuration, private egress and the worker release are unchanged. Both app and worker-secret dispatch settings are `0`; SES production access remains false with the original denied review. This is a successful code release, not completion of customer email activation. Evidence: `/private/tmp/vox-admin-current-proof.json`.

Final audit follow-up: password recovery now enqueues the promised password-change notice in the reset transaction, with session revocation and a stable action key. A real PostgreSQL red/green check proves one notice under concurrent link consumption; forced outbox failure rolls back password/session changes, and initial invitation setup sends no change notice. Migration 022 runs through the existing coordinated migration runner. Notification regression checks and compilation pass; no type checker or linter is configured.

Release `d00a9be09d91c508444080125128c011a0306efe` is pushed and deployed. Operation `cccd8820ccdd4be68bf948112238cfb6` succeeded with image `d00a9be-admin-20261001155007`, digest `sha256:82f21d6bed765dd4086fcb0d186b778439435af3dc157eedf19b65a81f059b71`. The email worker ZIP matches its recorded SHA-256 and returns disabled while publishing metrics. A temporary verifier in the existing private network exercised a real production-owned fixture: migration 022 present, one change notice, correct account recipient, reset replay denied, old session revoked, no token/password in rendered mail. The fixture user/outbox rows and verifier were removed; zero emails sent. Exact committed-archive checks and `/healthz` pass. Proof: `/private/tmp/vox-password-live-proof.json`.

Real Razorpay Test Mode checks now exercise the exact `d00a9be` application archive with a disposable PostgreSQL database. Starter, Studio and House initial payments captured and posted 30/100/300 credits with one receipt each. A dashboard-simulated Starter renewal captured, added 30 credits and a second receipt. Actual partial and cumulative full refunds reversed 30 credits; duplicate signed HTTP deliveries and delayed charges did not recreate credits or messages. House cancellation through the real billing UI retained 300 credits; Razorpay start/cancellation emails reached the owned inbox. All three disposable mandates were then cancelled. No live payment or production credential/database change occurred. See [provider acceptance evidence](admin-razorpay-acceptance.md).

Remaining acceptance gates: SES production access and new-event activation/delivery. Failure-email delivery remains unproven: the Studio failure simulator was still a created payment/issued invoice before test cleanup. Locally signed webhook checks refetch real provider objects but do not prove Razorpay-to-host webhook delivery. Automatic purge remains off pending an approved retention policy. No full-plan completion is claimed.

The user approved all remaining verified batches through deployment. Account support, audited billing corrections, delivery history, failed-job inspection, mandatory authenticator step-up, and bounded owner/admin metadata exports and deletion review are implemented. Automatic purge is disabled pending a retention policy; no period is invented.

Real disposable PostgreSQL checks pass for membership/session revocation, last-owner/admin protection, stable mutation keys, concurrent credit writes, invitation/reset single use and expiry, Google-only recovery exclusion, authenticator/recovery-code replay and lockout, tenant-scoped exports, and deletion review. Existing subscription/refund/auth/credit and video API checks pass with gateway/generation providers stubbed. Python compilation and JavaScript syntax checks pass; no configured type checker or linter exists.

SES domain, DKIM and MAIL FROM are verified in Mumbai. The actual Lambda/SES/outbox/SNS feedback path delivered a welcome and receipt to the owned inbox using only an isolated restored database; the receipt explicitly represents a zero-value delivery test, not a real purchase. A synthetic CloudWatch metric triggered an alert that reached the owned inbox. Eight operational alarms are configured. Provider acceptance alone was not used as delivery proof.

An isolated RDS point-in-time restore at `2026-09-30T20:17:56Z` matched 38 immutable credit entries across eight ledger workspaces, plus nine users, nine workspaces, 26 jobs and 12 invoices created before that cutoff. S3 versioning is enabled and a prior version was retrieved with matching bytes from an owned temporary test object. Existing 90-day storage-tier rules remain; there is no new automatic expiration.

At the initial 2026-09-30 checkpoint, customer email dispatch was disabled pending additional information for AWS case `179079869200146`; the [prepared response](admin-ses-support-response.md) had not been submitted because specific sharing approval was outstanding. The approved response was subsequently submitted on 2026-10-01, as recorded above. Email-based provisioning, invitations, resend and recovery return a clear unavailable response until sending is enabled. Read-only support, existing Google/password login and safe audited support controls can ship independently.

Local browser sign-in, mandatory verification screen, support directory and workspace billing controls were observed. A synthetic adjustment confirmation stalled in browser automation; HTTP/database checks independently verify adjustments and replay protection. Full browser confirmation completion is not claimed.

A final account-page regression check reproduced an expired-link redirect trap. The page now clears its pending-link marker only on terminal invalid/expired responses, retaining it for retry after temporary server failures. `node account_link_check.js` exercises the actual inline page script with terminal and temporary responses; its red/green check passes.

Initial release `47484623055bab0bacff3a39be8401d6b21a8229` was pushed and deployed. App Runner operation `8dde15c13df441d0a02ea17f55ec567a` succeeded with image `4748462-admin-20260930225807`, digest `sha256:b5dde84f744fa5b27c06b255200f1fbc9d50fd8789b0020d1d55a30c30b3f2be`. Real HTTPS smoke checks verified login, KMS-backed MFA, stable credit adjustment replay, zero net fixture credits, export boundaries, removed-member session denial, cross-origin denial, sender gating and authenticated pages. Owned temporary fixture accounts/workspace were removed; no customer email or real charges occurred. The signed-in browser loaded all ten real product images successfully.

Final release `32ef82bdee287fed5638f4b5f2afccb1166cfa4d` includes the expired-link correction. Operation `68a53efdeddb48a28667eae6a2bd28f3` succeeded with image `32ef82b-admin-20260930231413`, digest `sha256:de6a0f19e3631115659b7a9b50f6fdea9da69781abcc8e64b0f90d349e405247`. The served account page matches its exact archive bytes; the browser handled an invalid link and subsequently loaded Products without redirecting back. All other previously tested runtime files match the initial release. Final archive compilation, account-script red/green and account-action checks pass. The final image keeps private egress and customer sending disabled.

Public PostgreSQL ingress was removed after live verification. A fresh worker connection succeeded through the private app/worker security group afterward. The one-minute monitor is producing actual CloudWatch samples while customer sending stays disabled. The temporary RDS restore and its verification security group were removed. The [operations runbook](admin-operations.md) records recovery, rollback, thresholds and known ceilings. Milestone 1 and email-based account setup remain gated on sender approval; full-plan completion is not claimed.

## Scope and release order

Build the missing customer communications and support controls in the existing Python/FastAPI, PostgreSQL, static HTML, Razorpay, and AWS stack. Keep owner/member/global-admin roles unless a real permission requirement calls for more. Reuse the ledger, invoices, jobs, storage, and authentication modules.

| Milestone | What becomes live | Release prerequisite |
| --- | --- | --- |
| 1. Reliable customer emails | Signup welcome, paid credit/renewal receipt, refund confirmation where provider coverage is insufficient, email delivery history/retries/resend; verified provider failure/cancellation notifications | Sender verified, recipient policy tested, duplicate events tested, delivered test email proven, basic admin/tenant access checks pass |
| 2. Safe account support | Invitations, password recovery, customer search/detail, workspace/member management, suspend/reactivate, session revocation | Single-use tokens, account recovery and revoked-session tests pass |
| 3. Billing and support console | Searchable payments/invoices/subscriptions, reconciliation, attributable credit adjustments, notification and failed-job support views | Money/event race tests pass; corrections and admin actions are auditable |
| 4. Operational readiness | Admin authentication hardening, alerts, backup/restore proof, usage dashboard, controlled exports and data lifecycle | Tested alerts/restore, export isolation, retention rules settled; no unresolved critical access or financial defects |

Ship each milestone independently after its acceptance gates. Any demonstrated critical access, money, or recovery defect blocks the affected release and is fixed before rollout. Low-credit reminders and completed-generation emails are optional follow-up work, not prerequisites for these milestones.

## Decisions to resolve through preflight

- **Email service:** inspect existing connected email integrations first. Composio is preferred for discovery, but no Composio tool is exposed in this session. At execution, use it if available, otherwise installed provider/AWS CLIs and existing scoped credentials. Reuse an existing production sender if suitable. AWS SES is the fallback candidate because AWS and `boto3` are already used; it is not yet verified as production-ready in this account.
- **Sender:** propose `Donna Photoshoot <notifications@voxdonna.com>` with a working support reply-to; confirm the actual mailbox/domain configuration before enabling it. Welcome goes to the account email; financial mail uses a validated workspace billing contact, with a documented owner fallback.
- **Message ownership:** retain working Razorpay payment-failure/cancellation notices. Add Donna's welcome and credit receipt because they explain product credits and balance. Confirm any additional refund/lifecycle gaps rather than duplicating every provider email. [Razorpay notification coverage](https://razorpay.com/docs/payments/subscriptions/notifications).
- **Delivery:** inspect available scheduled workers. If none is reusable, use an EventBridge-scheduled Lambda dispatcher reading the PostgreSQL outbox. Do not depend exclusively on request-bound background tasks or an idle App Runner process. AWS documents CPU throttling when App Runner has no traffic. [AWS container guidance](https://docs.aws.amazon.com/pdfs/whitepapers/latest/containers-on-aws/containers-on-aws.pdf).
- **Account recovery:** keep password recovery for existing password accounts; Google-only users retain Google sign-in unless an explicit account-setup flow is selected. An invite must not overwrite an existing user's password.
- **Data handling:** inspect current policy and financial retention requirements before implementing irreversible deletion. This plan does not invent a legal retention period.

Email preflight must check region-specific sandbox status, sending quotas, identity verification, DKIM/SPF/DMARC, and DNS access. SES sandbox restrictions prevent arbitrary customer delivery, so verification and production access are release gates. [SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html). If service approval or human consent is required, prepare all automatable configuration first and request only that specific missing input.

## Implementation batches

File lists below are proposed touch sets, not claims that new files exist. Each batch has at most approximately five files, including its migration/test. If the actual scope exceeds that, split it before editing. At each boundary, verify the batch, report what works and remains, and obtain the next-phase approval required by AGENTS.md. Deployment configuration counts as a changed file.

### Batch 0 — Preflight and reproducible test baseline

Inspect production configuration read-only, provider notification recipients, staging/database availability, DNS authority, backups, and alarms. Record current live image tag/digest. Preserve unrelated dirty files and choose an isolated checkout of the intended baseline.

Run existing relevant self-checks against a disposable PostgreSQL database and stubbed providers. Establish a test-mode Razorpay integration and owned email test inbox for later authorized delivery checks. Record which tests exercise real services versus stubs.

Output: implementation readiness and resolved provider/recipient choices in this plan. No real customer mail, charges, or production migrations in this batch.

### Batch 1 — Durable notifications

Proposed files: `migrations/017_notifications.sql`, `notifications.py`, `notification_worker.py`, `notification_test.py`, `db.py` only if migration coordination needs a fix.

- Add a PostgreSQL outbox with business event key, workspace/user, recipient, immutable message data, state, attempts, next attempt time, lease, provider message ID, and timestamps. Retain enough history for support without storing secrets or reset tokens in delivery logs.
- Add template rendering and one selected sender implementation. Escape all user-controlled HTML and validate links/recipients; no user-supplied email headers.
- Claim small batches with database locking, commit the claim before the network call, use an expiring lease, retry transient failures with bounded backoff, and surface exhausted/permanent failures.
- Unique business keys prevent repeated enqueue. Document ambiguous provider timeouts and the crash-after-send duplicate window; use provider idempotency where supported. Do not call an accepted send “delivered.”
- Test two dispatchers, restart/recovery, provider outage, permanent rejection, template escaping, and rollback of enqueue with the originating transaction. Test simultaneous migration startup; fix coordination only if required.

### Batch 2 — Sender infrastructure and delivery feedback

Proposed files: `infra/email.json`, `tools/deploy_notifications.py`, `notification_worker.py`, `notifications.py`, `notification_test.py`.

- Configure the selected sender, verified identity, narrowly scoped IAM, secrets, DNS records, and provider delivery/bounce/complaint events using available automated integrations.
- If a new dispatcher is needed, deploy the scheduled Lambda with packaged runtime dependencies, bounded concurrency, network access to RDS, and scheduler failure handling. Keep connection use within the existing database capacity. Do not open broad database ingress to make it work.
- For SES, route delivery feedback through native authenticated AWS events to a handler. For another provider, authenticate its callback and deduplicate feedback events. Correlate by provider message ID/business event tag; never downgrade delivered/bounced terminal state on a delayed accepted event.
- Persist delivery results and suppress inappropriate retries after permanent bounces/complaints. Add queue-age and dispatcher-error monitoring.

Gate: a permitted test message reaches the owned inbox and its delivery event is recorded; worker continues without browser requests. Infrastructure and provider approval are verified, not assumed.

### Batch 3 — Welcome and credit receipts

Proposed files: `auth.py`, `subscriptions.py`, `notifications.py`, `notification_test.py`.

- Enqueue welcome in the new-account transaction, using the actual trial-credit grant. Do not send on returning login or retroactively email every existing account.
- Enqueue one receipt per verified paid invoice/payment in the same transaction as credits and invoice. Both checkout confirmation and webhook handling must converge on the existing shared posting function.
- Include plan, credits added, resulting balance at posting, paid amount/tax/currency, invoice link, payment reference, and support contact. Mandate authorization is not a monthly purchase.
- Test concurrent confirmation/webhook, duplicate/reordered events, rollback, different owner/billing addresses, and full refund already present when a delayed charge arrives. Do not send a misleading “credits added” message for an already-reversed purchase.

### Batch 4 — Refund/lifecycle coverage and email support

Proposed files: `billing.py`, `subscriptions.py`, `notifications.py`, `app.py`, `static/admin.html`.

- Add app messages only for verified provider gaps, including actual refund/credit-adjustment details. Reuse cumulative refund reconciliation and committed state.
- Add an admin notification list with recipient, event, status, last error, attempts, and resend action. Record resend actor/reason and distinguish an intentional resend from automatic event deduplication.
- Show delivery unknown when provider evidence is unavailable. Never substitute a successful API response for delivery proof.
- Keep failed/cancelled/refund notices consistent with current state under delayed events; a stale failure event must not contradict a recovered subscription.

Reuse the notification regression check and existing billing/subscription self-checks. If new cases cannot fit those checks without a sixth changed file, split this batch before editing.

**Milestone 1 release:** reviewed branded templates, proven welcome/receipt delivery, reliable retries, admin visibility, and authenticated live smoke checks. Enable only new events after an activation timestamp; no historical purchase-mail flood.

### Batch 5 — Invitations and password recovery

Proposed files: `migrations/018_account_actions.sql`, `account_actions.py`, `auth.py`, `notifications.py`, `account_actions_test.py`.

- Add hashed, expiring, single-use invitation/reset tokens bound to user and purpose. Deliver the secret link without exposing it in admin lists or logs; protect any stored delivery payload containing it and expire/purge it promptly.
- Enforce token use and password change atomically. Revoke appropriate sessions after password reset. Return consistent recovery responses for existing/nonexistent accounts and apply rate limits.
- For existing Google-only accounts, recovery must not silently create a password or bypass Google ownership checks.
- Protect recovery links from Host-header injection and token leakage. [OWASP recovery guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).

### Batch 6 — Access controls, suspension, and audit attribution

Proposed files: `migrations/019_admin_controls.sql`, `auth.py`, `admin.py`, `app.py`, `admin_test.py`.

- Add account/workspace suspension and reactivation, global sign-out, and authorization checks against current membership/account/workspace state on every protected request. Revoke or deny already-issued sessions after access removal.
- Add an admin action log: actor, action, target, outcome, reason, timestamp, correlation/idempotency key. Store safe metadata, not secrets.
- Enforce ordinary-user/admin and owner/member boundaries through direct API tests. Protect mutations against cross-origin requests and audit account linking/session behavior.
- Stage any necessary UI changes as a separate batch if the five-file limit would be exceeded. Reuse current roles rather than building a generalized permission framework.

### Batch 7 — Account routes and customer support UI

Proposed files: `app.py`, `admin.py`, `static/account.html`, `static/admin.html`, `account_actions_test.py`.

- Wire invitation/reset acceptance and the admin invite action. Replace plaintext password handoff with setup links.
- Make workspace/owner/membership provisioning atomic, including the welcome/invitation event.
- Add paginated customer search and detail by email, name, and workspace, showing memberships and account state.
- Add billing-contact/GST editing and member invite/remove/role actions with validation and the audit attribution added in Batch 6. Guard the last owner and last active administrator against accidental removal.
- Expose suspension/reactivation and session-revocation controls implemented in Batch 6, with clear confirmation and reason capture.

**Milestone 2 release:** secure invite/reset journeys, customer lookup, editable workspace support, revoked-session behavior, and audited administrative changes.

### Batch 8 — Billing console and safe adjustments

Proposed files: `credits.py`, `admin.py`, `app.py`, `static/admin.html`, `admin_test.py`.

- Show customer credit ledger, payments, invoices, refund totals, subscription state, and provider references, with pagination and filters.
- Require adjustment reason, acting administrator, and stable request idempotency. Keep grant/debit corrections append-only and define limits/confirmation for material debits.
- Add provider-versus-local reconciliation results and safe refresh actions; do not let a refresh recreate paid credits or overwrite terminal state after a provider failure.
- Keep refund and cancellation support actions tied to provider-verified outcomes, reason, and audit history. Separate read-only views from charge/refund actions and test their permissions.

### Batch 9 — Failed-generation support and reporting

Proposed files: `jobs.py`, `admin.py`, `app.py`, `static/admin.html`, `admin_test.py`.

- Add failed/stalled job inspection with provider reference, stored output, reserved/settled/refunded credits, and errors safe for support.
- Expose only retries that can avoid duplicate provider spend; uncertain external outcomes require reconciliation before retry, not blind regeneration.
- Add basic totals: new accounts, paying workspaces, payments/refunds, credits consumed, failed jobs. Report provider cost only where actual evidence exists; label estimates explicitly.

**Milestone 3 release:** support can investigate customer billing, change credits safely, trace communications, and resolve generation incidents from the admin console.

### Batch 10 — Admin authentication and operational safeguards

Split into small batches as needed, each no more than five files:

- Add enforced administrator MFA/step-up appropriate to the existing identity setup, with enrollment, recovery, throttling, and session revocation tested. Google email verification alone must not be described as MFA. Prevent administrator lockout during enrollment and verify recovery before enforcement.
- Configure existing CloudWatch/native service alerts for API errors, stalled jobs, payment webhook failures, email queue age/exhausted attempts, and spending anomalies. Test actual alert delivery.
- Verify automated backup retention and perform an isolated RDS restore with record comparisons. Add a documented recovery procedure and confirm storage recovery/lifecycle configuration.
- Add scoped exports and account-deletion requests with authorization and audit records. Implement data purge only after retention/policy decisions and backup implications are resolved; retain required financial records safely.

Proposed touch areas: `auth.py`, `admin.py`, `app.py`, numbered SQL migrations, `static/admin.html`, bounded verification checks, and small infrastructure scripts. Final file sets depend on verified identity/provider/retention choices and are fixed before each batch.

**Milestone 4 release:** enforce verified admin protection, enable tested alerts and recovery, and ship approved data workflows. Optional reminders can follow through the existing notification path without adding another delivery system.

## Verification for every code release

- Use disposable/staging PostgreSQL for migrations, transactions, concurrency, and rollback tests. Run migrations twice and verify the final schema; never run a destructive self-check against customer data.
- Run relevant existing `demo()` checks and the smallest focused new checks for notifications/accounts/admin behavior. Real Razorpay test-mode capability tests supplement stubs; they do not prove live billing without an authorized live transaction.
- Exercise signup, welcome, paid renewal, partial/full refund, delayed/duplicate webhook, revoked session, cross-workspace access, admin adjustment retry, and provider outage as applicable to the milestone.
- Verify deployed UI/API behavior and delivery events; static text, hidden buttons, provider flags, and HTTP 200 alone are insufficient acceptance evidence.
- Run configured type checks/lint. None are currently configured; report that explicitly and run Python compilation plus targeted behavioral checks. If adopting tools, add only narrowly scoped configuration that is feasible to keep green; do not disguise compilation as type checking.
- Validate generated JSON, SQL migrations, and deployment configuration with their consumers. Re-run the checks after any merge/rebase/change to the shipping artifact.

## Making each milestone live

1. Build from a clean, reviewed commit/checkout; preserve unrelated SDK/video edits. Record the previous production revision/image digest and database backup/restore reference.
2. Use additive backward-compatible migrations so the old app can still run during rollout. Apply once under migration coordination and validate; avoid destructive schema changes in the same release as new code.
3. Provision email/worker settings with dispatch initially disabled. Wire secrets and narrowly scoped IAM; validate App Runner and worker configuration separately. Do not change checkout prices or subscriber mandates during this work.
4. Run staging tests and permitted email delivery checks. Require milestone acceptance evidence, reviewed templates, and any required next-phase approval before enabling customer communications.
5. Commit/push the reviewed release, build the exact committed archive, push an immutable ECR tag, and update the existing App Runner service in `ap-south-1`. Reuse `deploy.sh` where suitable, but prevent its build context/secret sync from shipping unrelated work or overwriting deployed settings.
6. Wait for the deployment operation to succeed; confirm actual image identifier/digest, migrations, health, authenticated admin/customer routes, Products, campaigns, and billing. A service status alone is not sufficient.
7. Enable dispatch for new events only, verify queue drain and provider delivery evidence from an authorized test account, then monitor failures, queue age, and billing/generation regressions. Report the live URL, commit, deployed image, test evidence, and remaining limits.

**Rollback:** disable dispatch first if emails are wrong or duplicated; pause the scheduler and prevent resumed backlog from replaying until the cause is fixed. Revert the app to the previous immutable image using the compatible schema. Preserve credit/invoice/outbox/audit records. Do not restore a database over valid newer payments as a routine code rollback; reconcile financial discrepancies instead. Re-enable dispatch only after the corrected event set and templates are verified.

## Definition of live and complete

The features are complete only when all four milestones satisfy their acceptance gates on the deployed artifacts, automated emails have delivery evidence, support controls work for the intended roles, failures recover without changing money twice, and operations have tested alerts/restore. Record any provider-managed email whose delivery visibility remains limited as a known limitation.

External sender approval, DNS propagation, identity enforcement, and retention policy can affect rollout timing. No delivery-date estimate is asserted before preflight resolves those dependencies. This plan contains no authorization to bulk-message existing customers or charge real payments for testing.
