-- System of record for the NBA DFS pipeline. Every per-player write is an upsert
-- keyed on (slate_id, player_id) so a late-afternoon re-run updates in place.

CREATE TABLE IF NOT EXISTS slates (
  id              bigserial PRIMARY KEY,
  date            date        NOT NULL,
  site            text        NOT NULL DEFAULT 'DK',
  draft_group_id  bigint      NOT NULL,
  slate_type      text        NOT NULL DEFAULT 'Classic',
  n_games         int,
  lock_time_utc   timestamptz,
  game_list       jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (site, draft_group_id)
);

CREATE TABLE IF NOT EXISTS players (
  id            bigserial PRIMARY KEY,
  dk_player_id  bigint UNIQUE,
  name          text NOT NULL,
  name_norm     text NOT NULL
);
CREATE INDEX IF NOT EXISTS players_name_norm_idx ON players (name_norm);

CREATE TABLE IF NOT EXISTS pool (
  slate_id           bigint  NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  player_id          bigint  NOT NULL REFERENCES players(id),
  draftable_id       bigint,          -- what DK bulk upload expects
  team               text, opp text, game text, pos text,
  salary             int     NOT NULL,
  minutes            numeric,
  proj_rg            numeric, proj_adj numeric, floor numeric, ceil numeric, sim85 numeric,
  own_pct            numeric,
  own_estimated      boolean NOT NULL DEFAULT false,
  starter_rg         boolean, starter_rw boolean, starter_consensus boolean,
  contingent         boolean NOT NULL DEFAULT false,
  inj_status         text,
  questionable_mult  numeric,          -- per-player override; default comes from params
  vegas_total        numeric, fav_ml numeric, b2b boolean,
  signal_mult        numeric,
  signals            text[],           -- buckets that fired: blowout_starter, b2b, ...
  value              numeric,
  value_grade        char(1),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (slate_id, player_id)
);

CREATE TABLE IF NOT EXISTS actuals (
  slate_id   bigint NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  player_id  bigint NOT NULL REFERENCES players(id),
  dk_pts numeric, min numeric, pts int, fg3m int, reb int, ast int, stl int, blk int, tov int,
  dnp        boolean NOT NULL DEFAULT false,
  source     text,                      -- which verified box score this came from
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (slate_id, player_id)
);

CREATE TABLE IF NOT EXISTS params (
  version     serial PRIMARY KEY,
  created_at  timestamptz NOT NULL DEFAULT now(),
  payload     jsonb NOT NULL,
  reason      text  NOT NULL,
  author      text  NOT NULL DEFAULT 'human',   -- 'human' | 'learner'
  evidence    jsonb                              -- backtest numbers behind the change
);

-- params is append-only: never overwrite, never delete.
CREATE OR REPLACE FUNCTION params_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'params is append-only; insert a new version instead';
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS params_no_update ON params;
CREATE TRIGGER params_no_update BEFORE UPDATE OR DELETE ON params
  FOR EACH ROW EXECUTE FUNCTION params_append_only();

CREATE TABLE IF NOT EXISTS lineups (
  id              bigserial PRIMARY KEY,
  slate_id        bigint NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  mode            text   NOT NULL CHECK (mode IN ('GPP','CASH','OPTIMAL')),
  run_id          uuid   NOT NULL,            -- one build run; re-runs get a new run_id
  rank            int    NOT NULL,
  params_version  int    REFERENCES params(version),
  built_at        timestamptz NOT NULL DEFAULT now(),
  salary int, proj numeric, ceil numeric, own_sum numeric,
  players         jsonb  NOT NULL,
  actual_score    numeric,
  contest_result  jsonb
);
CREATE INDEX IF NOT EXISTS lineups_slate_idx ON lineups (slate_id, built_at DESC);

CREATE TABLE IF NOT EXISTS grades (
  slate_id    bigint NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  payload     jsonb NOT NULL,   -- MAE (ours vs RG raw), bucket bias, game bias, hindsight-perfect, missed plays
  PRIMARY KEY (slate_id, created_at)
);

-- Raw inputs as fetched/uploaded, so any slate can be re-graded or re-backtested later.
CREATE TABLE IF NOT EXISTS source_snapshots (
  id          bigserial PRIMARY KEY,
  slate_id    bigint REFERENCES slates(id) ON DELETE CASCADE,
  source      text NOT NULL,     -- dk_draftables | rg_projections | rg_lineups | rotowire | boxscore
  fetched_at  timestamptz NOT NULL DEFAULT now(),
  payload     jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS source_snapshots_slate_idx ON source_snapshots (slate_id, source, fetched_at DESC);
