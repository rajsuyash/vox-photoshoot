-- SOFT director-rule warnings (style guidance — see director.py's RULES severities),
-- kept on the version they were generated against so the editor can show a dismissible
-- "Director notes" callout on reload, not just at the moment the board job finishes.
-- Not creative_summary: that field is prose for a human, this is structured data the UI
-- renders as a list (director.validate_storyboard's own {'rule','severity','message'}
-- shape, JSON-serialised as-is).

ALTER TABLE storyboard_versions ADD COLUMN warnings jsonb NOT NULL DEFAULT '[]'::jsonb;
