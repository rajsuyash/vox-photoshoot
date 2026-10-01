# Photoshoot support operations

Region: `ap-south-1`. App Runner: `vox-photoshoot`, service ID `74c51f50e3014e2587ea8fa9caed99f0`. Production RDS: `vox-photoshoot-db`. Output bucket: `vox-photoshoot-085193942944`.

Current application release: `86ef88e`, image `86ef88e-clothing-20261001162841`, digest `sha256:fb4a96021d5e71db61d53df2aac6d3cf24755c69ae9db0acd99e32301d5b5135`. App Runner operation `dfa00c599830418b9444c59325157818` succeeded. This release adds model clothing choices while preserving admin runtime, credentials, private egress and disabled customer sending. Exact-archive notification/support regression checks and live health/form checks pass. The prior admin release `d00a9be` verified migration 022 and atomic password-change notices with a private production-owned fixture; it is the immediate rollback image (`d00a9be-admin-20261001155007`, digest `sha256:82f21d6bed765dd4086fcb0d186b778439435af3dc157eedf19b65a81f059b71`) with compatible additive schema. The unchanged worker ZIP SHA-256 is `lpfa1FORK4zpAfIp7iM6WOf3nQWrCmbg9oqo1tp0Goc=`. Documentation-only follow-up commits do not change runtime files.

## Support access

Sign in, open Admin, and enroll an authenticator from a fresh session. Save the ten recovery codes privately; each works once. Support access requires a verified factor and expires after 15 minutes. Enrollment revokes other sessions. Existing Google sign-in is not described as MFA.

Every support mutation requires a reason and stable request key. Credit corrections append ledger entries; retry an uncertain request with the same key. Negative corrections require debit confirmation. Billing reconciliation reads verified provider evidence and never recreates purchased credits. Failed-job inspection preserves existing outputs; missing provider references are shown as unknown. No blind paid generation retry is offered.

## Email and monitoring

SES sender: `notifications@voxdonna.com`; support reply-to: `suyash@voxdonna.com`. Identity/DKIM/dedicated MAIL FROM are verified. Customer dispatch and email-based invitations/provisioning/recovery/resend remain disabled until production access is approved. Case: `179079869200146`; the [approved response](admin-ses-support-response.md) was submitted unchanged on `2026-10-01T08:29:14Z`. Correspondence and **Customer action completed** status are visible in the console; AWS review is pending.

`vox-photoshoot-notifications` runs every minute through EventBridge. Lambda reads only its own `vox-photoshoot/notifications` secret. It publishes monitoring even when sending is disabled. SES delivery/bounce/complaint events reach the same handler through the scoped SNS feedback topic; permanent bounces and complaints suppress future sends. Accepted is not delivered. SES has no send idempotency key: an accepted remote send followed by a crash before local acceptance can produce a retry duplicate.

After approval, activate customer sending with an activation timestamp in both worker/app configuration. Preserve existing secret/environment entries. Do not replay historical notification rows. First verify one explicitly permitted new event and its delivery feedback, then inspect queue drain. These are future activation instructions, not a claim activation occurred.

Completed password resets enqueue a password-change notice to the account email in the same transaction as password/session changes. Link replay cannot enqueue it twice; initial invitation setup does not claim a password was changed. Sender outage does not prevent committing the local outbox event. The 2026-10-01 private verification sent no mail and removed its owned fixture.

[Real Test Mode acceptance](admin-razorpay-acceptance.md) verifies all three pack purchases, Starter renewal, cumulative refunds, duplicate/delayed signed HTTP deliveries and House UI cancellation. Start and cancellation emails reached the owned inbox. Failure-email delivery remains unproven: its simulator payment was still created before disposable mandates were cancelled. No live charge occurred; local signed deliveries are not provider-to-host webhook evidence. Sandbox invoice tax was zero, so these checks do not establish production GST configuration.

Operational alarms send to the confirmed owned inbox:

| Signal | Threshold |
| --- | --- |
| App Runner 5xx responses | Five in one minute |
| Razorpay webhook failure log | One in one minute |
| Oldest queued email | Ten minutes |
| Exhausted email attempts | One |
| Stalled jobs | One, heartbeat older than 15 minutes |
| Daily reserved credits | 1,000; usage signal, not measured currency cost |
| Worker errors or throttles | One in one minute |

The webhook filter matches the existing `razorpay webhook failed` application log. Inspect `/aws/apprunner/vox-photoshoot/74c51f50e3014e2587ea8fa9caed99f0/application` and `/aws/lambda/vox-photoshoot-notifications` to distinguish root causes. Worker logs retain 14 days. A synthetic metric triggered a real CloudWatch alarm delivered to the owned inbox on 2026-09-30; its temporary alarm was removed.

## Recovery and rollback

RDS is encrypted and retains seven days of automated backups. Restore to a **new isolated instance**, with a dedicated security group restricted to the authorized verification client. Compare immutable credit entries by workspace: count, sum(delta), max(seq), bounded by the selected restore timestamp. Compare accounts/workspaces/jobs/invoices created before that cutoff. Treat differences from later writes separately. Do not replace production or valid newer payments merely to roll back code.

Verified drill: restore at `2026-09-30T20:17:56Z` matched 38 credit entries across eight ledger workspaces, nine users, nine workspaces, 26 jobs and 12 invoices. The isolated restored database also proved the real Lambda/SES/feedback path using two labeled zero-value owned-inbox messages, without customer mail or charges. The temporary restore and its security group were removed after verification.

S3 versioning is enabled. Recover an object by retrieving its prior version and comparing expected bytes before choosing that version as current. The 2026-09-30 drill retrieved an original version after overwrite; only the owned test versions were removed afterward. Existing output/upload transitions to STANDARD_IA after 90 days remain. No expiration/purge period was added.

Application rollback uses the previous immutable image and the additive compatible schema. If rolling back to code predating support authentication hardening, revoke test/support sessions and account for the older access behavior; do not assume the older app enforces new suspension or MFA fields. Keep private VPC egress. Preserve ledger, invoice, outbox and audit records. Disable sending first if communication behavior is wrong; pause the scheduler only if the worker itself is unsafe, since that also pauses its metrics. Restore prior application configuration without discarding new Secrets Manager values.

## Data requests and ceilings

Workspace owners can download bounded metadata pages from Account. Administrators can export a workspace with attribution. Tokens, passwords and internal prompts are excluded; media remains available through History. Each page is a separate snapshot, so multi-page exports should occur during a quiet period. Deletion requests require workspace-name confirmation and admin review. Automatic purge is disabled pending retention/financial-record rules and backup implications.

The app and worker use private VPC egress; public PostgreSQL ingress is removed. A new worker connection was tested after removal. The private network uses one NAT gateway at this scale. Its failure is an egress availability ceiling; use one per availability zone when that requirement changes. There is one shared support mutation lock; split by workspace if measured throughput warrants it. Provider currency spend is not fabricated from credit counts.
