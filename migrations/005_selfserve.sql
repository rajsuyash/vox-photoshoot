-- Self-serve: sign in with Google, get a workspace and trial credits, buy more.
--
-- Until now every account was created by hand through admin.py with a generated
-- password. This adds the public front door beside that, without removing it: the
-- sales-led path still works, and you still log in with a password.

-- Google's stable subject id. NOT the email — a Google account can change its email,
-- and matching on one that has moved either locks a customer out or, worse, hands their
-- workspace to whoever inherited the address.
ALTER TABLE users ADD COLUMN google_sub text;

-- Partial, so the password accounts that have no google_sub do not collide on NULL.
CREATE UNIQUE INDEX users_google_sub_key ON users (google_sub)
    WHERE google_sub IS NOT NULL;

-- A Google-only account has no password to hash. verify_password() already returns
-- False rather than raising on a malformed hash, so a NULL here is a failed password
-- login and not a crash — but authenticate() must still never be handed one.
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- An issued Razorpay invoice carries an order_id, which is what Checkout opens against.
-- Persisting it means the webhook resolves a payment to a workspace from our own row
-- rather than from notes that have round-tripped through someone else's system.
ALTER TABLE invoices ADD COLUMN razorpay_order_id text;

CREATE UNIQUE INDEX invoices_order_key ON invoices (razorpay_order_id)
    WHERE razorpay_order_id IS NOT NULL;
