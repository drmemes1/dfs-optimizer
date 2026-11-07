-- One verified box-score line per player per night. Written only for games that passed the same
-- verification the grader uses: a wrong box score would poison a player's recent rate for as long
-- as it sits in the lookback.
CREATE TABLE IF NOT EXISTS player_games (
  player_id  bigint  NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  game_date  date    NOT NULL,
  team       text,
  opp        text,
  min        numeric NOT NULL DEFAULT 0,
  dk_pts     numeric NOT NULL DEFAULT 0,
  pts int, fg3m int, reb int, ast int, stl int, blk int, tov int,
  dnp        boolean NOT NULL DEFAULT false,
  source     text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (player_id, game_date)
);
CREATE INDEX IF NOT EXISTS player_games_player_date_idx ON player_games (player_id, game_date DESC);

-- the recent-form multiplier, stored per build like every other signal so grading measures it
ALTER TABLE pool ADD COLUMN IF NOT EXISTS m_form numeric;
