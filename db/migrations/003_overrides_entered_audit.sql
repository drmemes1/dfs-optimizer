-- Lineup modes: add the consensus (most-picked) lineup.
ALTER TABLE lineups DROP CONSTRAINT IF EXISTS lineups_mode_check;
ALTER TABLE lineups ADD CONSTRAINT lineups_mode_check CHECK (mode IN ('GPP','CASH','OPTIMAL','CONSENSUS'));

-- What was actually entered on DK, and any edits made after the build.
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS entered boolean NOT NULL DEFAULT false;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS entered_at timestamptz;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS edited boolean NOT NULL DEFAULT false;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS players_original jsonb;   -- as generated, before any swap
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS settings jsonb;            -- constraints/overrides used
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS locks jsonb;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS excludes jsonb;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS cost_vs_unconstrained jsonb; -- {proj, objective} delta from locks/excludes

-- Each applied signal multiplier as its own column, so bias can be attributed per signal.
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_pace numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_blowout numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_close numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_b2b numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_questionable numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_contingent numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS floor_rg numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS ceil_rg numeric;
ALTER TABLE pool ADD COLUMN IF NOT EXISTS game_start timestamptz;      -- per-player lock (his game's tip)

-- Pool history: every build/refresh appends a full snapshot (pool itself holds the latest).
CREATE TABLE IF NOT EXISTS pool_snapshots (
  id          bigserial PRIMARY KEY,
  slate_id    bigint NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  taken_at    timestamptz NOT NULL DEFAULT now(),
  reason      text NOT NULL,          -- build | refresh | rebuild
  params_version int,
  rows        jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS pool_snapshots_slate_idx ON pool_snapshots (slate_id, taken_at DESC);

-- Exact uploaded bytes, so old slates stay reproducible after parser changes.
ALTER TABLE source_snapshots ADD COLUMN IF NOT EXISTS raw bytea;
ALTER TABLE source_snapshots ADD COLUMN IF NOT EXISTS filename text;
ALTER TABLE source_snapshots ADD COLUMN IF NOT EXISTS sha256 text;

-- Params audit: what kind of change, and the per-key old -> new list.
ALTER TABLE params ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'manual'
  CHECK (kind IN ('initial','measured_bias','manual'));
ALTER TABLE params ADD COLUMN IF NOT EXISTS changes jsonb;   -- [{key, old, new, sample_n}]
