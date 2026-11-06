-- The cached RotoGrinders session, so logins stay roughly weekly instead of dozens a night,
-- plus failure bookkeeping so a revoked password cannot be retried every five minutes.
CREATE TABLE IF NOT EXISTS rg_session (
  id              int         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  cookie          text,
  acquired_at     timestamptz,
  failures        int         NOT NULL DEFAULT 0,
  last_failure_at timestamptz
);

-- One alert per slate per kind. The unique constraint is what makes the pre-lock alert fire
-- exactly once across the ~30 evening ticks, even if two runs overlap.
CREATE TABLE IF NOT EXISTS alerts_sent (
  id       bigserial   PRIMARY KEY,
  slate_id bigint      NOT NULL REFERENCES slates(id) ON DELETE CASCADE,
  kind     text        NOT NULL,
  sent_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (slate_id, kind)
);
