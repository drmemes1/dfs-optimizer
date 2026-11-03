-- Standing preferences that apply to every build until changed (e.g. players always excluded).
CREATE TABLE IF NOT EXISTS user_prefs (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
