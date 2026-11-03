"""GET /api/health — applies pending migrations, seeds params v1, reports table counts."""
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import db  # noqa: E402

TABLES = ["slates", "players", "pool", "actuals", "lineups", "params", "grades", "source_snapshots"]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with db.connect() as conn:
                applied = db.migrate(conn)
                seeded = db.seed_params(conn)
                with conn.cursor() as cur:
                    counts = {}
                    for t in TABLES:
                        cur.execute(f"SELECT count(*) AS n FROM {t}")
                        counts[t] = cur.fetchone()["n"]
                    cur.execute("SELECT version() AS v")
                    pg = cur.fetchone()["v"].split(",")[0]
                version, _ = db.current_params(conn)
            body = {"ok": True, "postgres": pg, "migrations_applied_now": applied,
                    "params_seeded_version": seeded, "params_version": version, "counts": counts}
            status = 200
        except Exception as e:  # report the error type, never connection strings
            traceback.print_exc()
            body = {"ok": False, "error": type(e).__name__, "detail": str(e)[:300]}
            status = 500
        data = json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
