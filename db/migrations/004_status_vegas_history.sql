-- Timestamped history of what the lineup pages said, recorded only when something changes.
-- The sequence of changes (tagged Q at 2pm, ruled out at 6:40pm) is unrecoverable after the fact.
CREATE TABLE IF NOT EXISTS player_status_history (
  id             bigserial PRIMARY KEY,
  observed_at    timestamptz NOT NULL DEFAULT now(),
  game_date      date NOT NULL,             -- ET date of the games on the page
  source         text NOT NULL,             -- rotowire | rotogrinders
  team           text NOT NULL,
  name           text NOT NULL,
  name_key       text NOT NULL,
  is_starter     boolean,
  inj            text,                      -- O | D | Q | GTD | P | ''
  pct_play       int,
  team_status    text                       -- confirmed | expected | unconfirmed
);
CREATE INDEX IF NOT EXISTS psh_lookup_idx ON player_status_history (game_date, source, team, name_key, observed_at DESC);

CREATE TABLE IF NOT EXISTS vegas_history (
  id           bigserial PRIMARY KEY,
  observed_at  timestamptz NOT NULL DEFAULT now(),
  game_date    date NOT NULL,
  source       text NOT NULL,
  away         text NOT NULL,
  home         text NOT NULL,
  total        numeric,
  fav_team     text,
  fav_ml       int,
  spread_team  text,
  spread       numeric
);
CREATE INDEX IF NOT EXISTS vh_lookup_idx ON vegas_history (game_date, source, away, home, observed_at DESC);

-- One row per scheduled run, so a failing scheduler is visible on the page instead of silent.
CREATE TABLE IF NOT EXISTS job_runs (
  id          bigserial PRIMARY KEY,
  job         text NOT NULL,                -- snapshot | grade | ...
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  ok          boolean,
  summary     jsonb,
  errors      jsonb
);
CREATE INDEX IF NOT EXISTS job_runs_idx ON job_runs (job, started_at DESC);
