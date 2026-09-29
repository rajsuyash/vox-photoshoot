-- concepts.mode gets its own column. Phase 2 stashed it in campaigns.brief->'concept_modes'
-- (a JSONB side-map keyed by concept id) because adding this migration wasn't in scope yet
-- — ads_api.py's _merge_campaign_brief/get_campaign_detail were the only readers/writers.
-- Now that it's a real column, storyboard.save_concepts takes mode directly and ads_api.py
-- stops touching the side-map.

ALTER TABLE concepts ADD COLUMN mode text;

UPDATE concepts c
   SET mode = COALESCE(camp.brief -> 'concept_modes' ->> c.id::text, 'story')
  FROM campaigns camp
 WHERE camp.id = c.campaign_id;

ALTER TABLE concepts ALTER COLUMN mode SET NOT NULL;
ALTER TABLE concepts ALTER COLUMN mode SET DEFAULT 'story';
ALTER TABLE concepts ADD CONSTRAINT concepts_mode_check CHECK (mode IN ('story', 'showcase'));
