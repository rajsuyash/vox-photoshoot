-- The client's own reference for the piece: a SKU, style number, or design code.
--
-- Without it a shoot is identified by "ring / signet / udaipur-palace" plus a sentence
-- the model wrote about the photograph. A manufacturer with two thousand designs cannot
-- find RG-4471 in that, cannot match a file back to a listing, and cannot hand anything
-- to a PIM. It is the difference between a gallery and a catalogue.
--
-- Deliberately free text, not a foreign key to a products table. Every jewellery
-- business already has its own coding scheme and none of them want a second one; the
-- job of this column is to carry theirs through untouched.
ALTER TABLE jobs ADD COLUMN sku text;

-- Case-insensitive, because rg-4471 and RG-4471 are the same piece to everyone except
-- a database. Partial: most rows have no SKU and should not sit in the index.
CREATE INDEX jobs_by_sku ON jobs (workspace_id, lower(sku))
    WHERE sku IS NOT NULL;
