-- Post-reset notices share the account transaction and existing delivery worker.
ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('welcome','receipt','refund','invite','reset','password_changed'));
