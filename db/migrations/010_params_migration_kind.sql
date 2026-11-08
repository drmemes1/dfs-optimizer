-- A params row is a snapshot of the keys that existed when it was written. When a new signal adds
-- a key, db.current_params appends the filled payload so the lineage stays honest: paramstore
-- reads the raw stored row and refuses keys it cannot find, and params_version is stamped on every
-- pool and lineup, so an in-memory-only fill would both lock the learner out and make a replay
-- irreproducible. That append needs its own kind, distinct from a human or learner decision.
ALTER TABLE params DROP CONSTRAINT IF EXISTS params_kind_check;
ALTER TABLE params ADD CONSTRAINT params_kind_check
  CHECK (kind IN ('initial','measured_bias','manual','migration'));
