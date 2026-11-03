-- Skip storing a pool snapshot that is identical to the previous one for the slate.
ALTER TABLE pool_snapshots ADD COLUMN IF NOT EXISTS rows_sha text;
