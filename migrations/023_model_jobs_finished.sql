-- Two 'model' jobs were created, charged and delivered before _make_talent() claimed the
-- job it created (see app.py). jobs.finish() only closes a row it owns — status='running'
-- AND claimed_by=itself — so on both their success and failure paths finish() matched zero
-- rows and silently no-op'd, leaving them status='queued' forever with no heartbeat. The
-- one-minute monitor in notification_worker.py then counts them as StalledJobs: both have
-- alarmed since 2026-09-30. Both portraits were verified delivered and the credit was
-- correctly charged — settle() already wrote nothing because the single reserved credit
-- was fully used — so this closes the two rows with no credit_ledger write.
UPDATE jobs SET status='succeeded', settled_credits=1, finished_at=created_at
 WHERE id IN ('0414d0fa-7f1d-450a-b093-38b7a7331831', 'dd57fd80-ab47-4fd4-9122-dbf8f813c192')
   AND kind='model' AND status='queued' AND claimed_by IS NULL;
