-- Slates created from uploads may not have a DK draft group id yet (lobby match is best-effort),
-- so identify a slate by date + its exact game set.
ALTER TABLE slates ALTER COLUMN draft_group_id DROP NOT NULL;
ALTER TABLE slates ADD COLUMN IF NOT EXISTS slate_key text;
ALTER TABLE slates DROP CONSTRAINT IF EXISTS slates_site_draft_group_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS slates_site_date_key_idx ON slates (site, date, slate_key);
ALTER TABLE players ADD COLUMN IF NOT EXISTS last_team text;
