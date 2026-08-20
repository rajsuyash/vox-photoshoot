-- Credit packs sold, and the GST invoice raised for each.
--
-- Prepaid: an invoice is raised for a pack, the customer pays by UPI, and the webhook
-- credits the workspace. The invoice document itself is Razorpay's — it carries the
-- GSTIN, HSN/SAC and place-of-supply that Indian law requires and that a CA will accept.
-- This table is the local record of what was sold and whether it has been paid.

CREATE TABLE invoices (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,

    razorpay_invoice_id text UNIQUE,
    -- The identity of the money. A single payment emits several webhook events with
    -- different event ids, so the ledger keys on this rather than on the event.
    razorpay_payment_id text,

    credits             integer NOT NULL,
    amount_paise        bigint  NOT NULL,     -- integers only; never float money
    status              text    NOT NULL DEFAULT 'issued',
    short_url           text,                 -- the payment link sent to the customer

    issued_at           timestamptz NOT NULL DEFAULT now(),
    paid_at             timestamptz,

    CONSTRAINT invoices_status_check CHECK (status IN
        ('issued', 'paid', 'cancelled', 'expired', 'refunded')),
    CONSTRAINT invoices_credits_check CHECK (credits > 0),
    CONSTRAINT invoices_amount_check  CHECK (amount_paise > 0)
);

CREATE INDEX invoices_by_workspace ON invoices (workspace_id, issued_at DESC);
CREATE INDEX invoices_by_payment ON invoices (razorpay_payment_id)
    WHERE razorpay_payment_id IS NOT NULL;
