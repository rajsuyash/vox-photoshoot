-- Optional branding a workspace can stamp onto its own images at download time.
--
-- On the workspace rather than on the job: a brand has one identity across every shoot,
-- and storing it per job would mean re-entering it every time and leaving old images
-- carrying an old logo after a rebrand.
--
-- Nothing here is ever baked into the stored image. The file in S3 stays the clean
-- master and branding is applied on the way out, so the same shoot can be downloaded
-- branded for a reseller and clean for a magazine.

ALTER TABLE workspaces
    -- The logo lives in S3 like everything else; this is its key, not its bytes.
    ADD COLUMN brand_logo_key text,
    -- One free-text line. Every jewellery business formats this differently and none of
    -- them want a template: "Kalyan Jewellers · +91 98765 43210 · kalyan.com".
    ADD COLUMN brand_text     text,
    ADD COLUMN brand_position text NOT NULL DEFAULT 'bottom-right',
    ADD COLUMN brand_opacity  integer NOT NULL DEFAULT 70;

ALTER TABLE workspaces
    ADD CONSTRAINT workspaces_brand_position_check CHECK (brand_position IN
        ('bottom-right', 'bottom-left', 'top-right', 'top-left', 'bottom-centre')),
    -- Below about 10% it is invisible, which reads as a bug rather than as subtlety.
    ADD CONSTRAINT workspaces_brand_opacity_check CHECK (brand_opacity BETWEEN 10 AND 100);
