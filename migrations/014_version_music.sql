-- Version-level music: the storyboard's chosen bed (or an explicit "no music, silent ad"
-- choice), and the version this decision lives on. Approval itself is an approvals row
-- (stage='music'), same audit trail every other approve/reject/reset already uses — no
-- new column needed for that half.
--
-- Fork copies both columns (storyboard._fork_version) so a new draft inherits its
-- parent's music choice rather than reverting to "nothing chosen" on every edit.

ALTER TABLE storyboard_versions
    ADD COLUMN selected_music_asset_id uuid REFERENCES generated_assets(id) ON DELETE SET NULL,
    ADD COLUMN music_skipped boolean NOT NULL DEFAULT false;
