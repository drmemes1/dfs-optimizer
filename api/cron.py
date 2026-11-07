"""GET /api/cron?job=snapshot|grade   — scheduled work. Requires `Authorization: Bearer $CRON_SECRET`.
Called by the GitHub Actions schedule (.github/workflows/snapshots.yml) or Vercel Cron."""
import hmac
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import jobs  # noqa: E402
from dfs.web import guarded, query, send  # noqa: E402


@guarded
def _get(h):
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return send(h, 503, {"ok": False, "error": "CRON_SECRET is not set in Vercel; scheduled jobs are disabled"})
    got = (h.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(got, secret):
        return send(h, 401, {"ok": False, "error": "unauthorized"})
    job = query(h).get("job")
    if job == "snapshot":
        out = jobs.snapshot()
        return send(h, 200 if out["ok"] else 500, out)
    if job == "grade":
        out = jobs.grade()
        # pending verification is normal early in the morning; only hard errors are a 500
        hard = {k: v for k, v in out["errors"].items() if "will retry" not in v}
        return send(h, 500 if hard else 200, out)
    if job == "prelock":
        out = jobs.prelock()
        # only a newly-reported problem fails the workflow: a source that is down all evening
        # would otherwise email on every one of the ~30 five-minute ticks
        return send(h, 500 if out["new_errors"] else 200, out)
    if job == "build":
        out = jobs.build()
        return send(h, 500 if out["errors"] else 200, out)
    if job == "backfill":
        out = jobs.backfill_logs()
        return send(h, 500 if out["errors"] else 200, out)
    send(h, 400, {"ok": False, "error": "unknown job"})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _get(self)
