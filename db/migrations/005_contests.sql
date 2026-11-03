-- Contest context captured on every build/rebuild (what the lineups were for).
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS contest_context jsonb;

-- DraftKings contest results, imported from the contest standings CSV the user downloads.
CREATE TABLE IF NOT EXISTS contests (
  id                 bigint PRIMARY KEY,          -- DK contest id
  slate_id           bigint REFERENCES slates(id) ON DELETE SET NULL,
  name               text,
  entry_fee          numeric,
  entries            int,                          -- field size (rows in the standings file)
  max_entries_per_user int,
  prize_pool         numeric,
  places_paid        int,
  payout             jsonb,                        -- optional ladder [{from_rank,to_rank,prize}]
  top_heaviness      text,                         -- top_heavy | flat | double_up | fifty_fifty
  winning_score      numeric,
  min_cash_score     numeric,                      -- score of the last paid place, when places_paid known
  score_quantiles    jsonb,                        -- {p50,p75,p90,p99,p999,max}
  imported_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contest_entries (
  contest_id   bigint NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
  entry_id     bigint NOT NULL,
  entry_name   text,
  rank         int,
  points       numeric,
  lineup       jsonb,                              -- [{slot, name}]
  is_mine      boolean NOT NULL DEFAULT false,
  lineup_id    bigint REFERENCES lineups(id) ON DELETE SET NULL,   -- our saved lineup it matches
  PRIMARY KEY (contest_id, entry_id)
);
CREATE INDEX IF NOT EXISTS contest_entries_mine_idx ON contest_entries (contest_id) WHERE is_mine;

CREATE TABLE IF NOT EXISTS contest_ownership (
  contest_id   bigint NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
  name         text NOT NULL,
  name_key     text NOT NULL,
  player_id    bigint REFERENCES players(id),
  roster_pos   text,
  pct_drafted  numeric,                            -- actual ownership, percent
  fpts         numeric,
  PRIMARY KEY (contest_id, name_key)
);
