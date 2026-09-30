ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('shoot', 'reshoot', 'retouch', 'model', 'video',
                   'ad_concepts', 'ad_board', 'ad_frame', 'ad_video', 'ad_music',
                   'ad_render', 'campaign'));
