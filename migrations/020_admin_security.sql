ALTER TABLE users ADD COLUMN mfa_secret bytea;
ALTER TABLE users ADD COLUMN mfa_pending bytea;
ALTER TABLE users ADD COLUMN mfa_pending_until timestamptz;
ALTER TABLE users ADD COLUMN mfa_last_counter bigint NOT NULL DEFAULT -1;
ALTER TABLE users ADD COLUMN mfa_failures integer NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN mfa_locked_until timestamptz;
ALTER TABLE sessions ADD COLUMN mfa_verified_until timestamptz;
CREATE TABLE admin_recovery_codes (
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash text NOT NULL,
    PRIMARY KEY(user_id,code_hash)
);
