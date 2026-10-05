# Stalled model jobs (2026-10-05) — what was done and why

Record of the "Photoshoot Stalled jobs" alarm, its root cause, and the fix. Code:
commit `846ead8` (PR #3). Migration: `migrations/023_model_jobs_finished.sql`. Test:
`model_job_test.py`.

## Symptom

CloudWatch alarm "Photoshoot Stalled jobs" (metric `Donna/Photoshoot StalledJobs ≥ 1`,
60 s period) in ALARM from **2026-09-30 20:59 UTC**; the metric sat flat at **2.0** for
3+ days. Reported by another session on 2026-10-04.

The monitor (`notification_worker.py`) defines a stalled job as:

```
status IN ('queued','running') AND COALESCE(heartbeat_at, created_at) < now() - 15 min
```

It started publishing with release `4748462` on 2026-09-30 (~20:44 UTC first datapoint,
alarm 20:46).

## Ruled out before the real cause

- **NAT cutover** (2026-10-03) — happened after the alarm started.
- **RDS restore drill** — ran against an isolated instance, never touched production rows.
- **Lost container work** — App Runner logs 20:00–21:10 UTC on 2026-09-30 show no user
  traffic in that window.
- **Sweep-triggering polls on 2026-10-01** (15:25 shoot poll, 16:03 campaign poll) — did
  not clear the alarm.

## The 2 rows

Read via Admin → Jobs (GET only):

- `0414d0fa-7f1d-450a-b093-38b7a7331831` — workspace "suyash raj", created
  2026-08-23 19:47 UTC. `POST /api/talent/upload` returned 200 and the user opened
  `/?model=73f8e01c3a84`.
- `dd57fd80-ab47-4fd4-9122-dbf8f813c192` — workspace "Pintu Work", created
  2026-09-21 07:24 UTC. User opened `/?model=1a63bd8a2a9e` at 07:27; the POST log line
  was not found, so delivery is "very likely" rather than confirmed.

Both: `kind='model'`, `status='queued'`, `attempts=0`, `heartbeat_at` NULL, 1 credit
reserved, ledger shows exactly one reserve entry each (correct — `settle` writes nothing
when a job is fully delivered). **No customer impact, no refund owed.**

## Root cause

`_make_talent` in `app.py` runs the model job inline and never calls `jobs.claim()`.
`jobs.finish()` is fenced on `claimed_by = this instance AND status = 'running'`, so on
both the success and failure path it silently matched **zero rows** — the job stayed
`'queued'` forever regardless of outcome. Every model job ever created has this shape;
only 2 exist, which is why the alarm has been pinned at exactly 2.0.

## Rejected approach

Widening `jobs.sweep()` to reap stale `'queued'` jobs, instead of hand-fixing the two
rows. Rejected because `sweep()` counts delivery via `job_images`, which model jobs never
write to — sweeping would have refunded both rows even though both portraits were
delivered.

## Fix

- `_make_talent` now claims the job before generating, returning 409 if the claim fails.
- Migration `023_model_jobs_finished.sql` sets the 2 existing rows to
  `succeeded` / `settled_credits = 1`, with no ledger write (matches the "already
  delivered" case).
- `model_job_test.py` (disposable local Postgres): red on the pre-fix code
  (`expected succeeded, got 'queued'`), green on `846ead8`.
- Also passing: `jobs.py` / `credits.py` self-checks, `admin_test.py`,
  `notification_test.py`. `campaign_test.py` fails identically on unmodified `main` —
  pre-existing, unrelated to this change.

## Deploy (2026-10-05, ~05:28 UTC)

Image `vox-photoshoot:846ead8-modeljobs-20261005052809`
(digest `sha256:4b978bd3fe57f5fd4c42d6a5bfedf9d3deeee57ba73d40a1f873a2c87d2e3820`), built
from a clean worktree. Deployed by copying the **live** `SourceConfiguration` and
swapping only `ImageIdentifier` — no Secrets Manager write. Rollback image:
`86ef88e-clothing-20261001162841`.

- Health check OK.
- Alarm back to OK at **2026-10-05 05:30:44 UTC** (datapoint 0.0 at 05:29).

### Why `deploy.sh` was NOT used

`deploy.sh` sets 4 env vars / 9 secret mappings, but the live service has 6 env vars
(adds `NOTIFICATIONS_ENABLED`, `NOTIFICATIONS_SINCE`) and 11 secrets (adds
`ACCOUNT_LINK_KEY`, `ADMIN_KMS_KEY`). It also overwrites Secrets Manager
`vox-photoshoot/env` from the local `.env` and builds from the working tree. Running it
as-is would have stripped live config. Until it's fixed, deploys to this service must
copy the live `SourceConfiguration` and swap only the image (see Remaining, below, and
`docs/admin-operations.md`).

## Also shipped

Commit `45723f5` (admin notice scroll/focus fix, `static/admin.html`) was already on
`main` and previously undeployed; it went out in this same image.

## Remaining

1. Fix `deploy.sh` to derive its env/secret config from the live service (or at least
   match it), and stop implicitly syncing Secrets Manager from local `.env`.
2. `campaign_test.py`'s pre-existing failure on `main` is untouched by this change.
3. Not urgent: `jobs.sweep()` only reaps `'running'` non-campaign jobs, and is only
   triggered from shoot/campaign polling — not video-ad polling. A non-campaign job lost
   before `claim()` would be caught by the stalled-jobs alarm but never auto-refunded.
   The alarm is the detector for that case; there is no code fix pending for it.
