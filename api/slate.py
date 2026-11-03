"""GET /api/slate?id=<slate_id>  (latest slate when omitted)
   GET /api/slate?list=1         (slate history)
   GET /api/slate?id=..&export=GPP|CASH|CONSENSUS|ENTERED  (DK bulk-upload CSV)
   GET /api/slate?id=..&check=1   (late-scratch check of saved lineups)
   GET /api/slate?params=1        (current params, for form defaults)"""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import db, pipeline, queries  # noqa: E402
from dfs.web import guarded, query, send  # noqa: E402


@guarded
def _get(h):
    q = query(h)
    with db.connect() as conn:
        db.migrate(conn)
        db.seed_params(conn)
        if q.get("params"):
            version, params = db.current_params(conn)
            return send(h, 200, {"ok": True, "version": version, "params": params})
        if q.get("list"):
            return send(h, 200, {"ok": True, "slates": queries.list_slates(conn)})
        sid = int(q["id"]) if q.get("id") else None
        if q.get("export"):
            mode = q["export"].upper()
            if mode not in ("GPP", "CASH", "OPTIMAL", "CONSENSUS", "ENTERED") or sid is None:
                return send(h, 400, {"ok": False, "error": "export needs id and mode GPP|CASH|CONSENSUS|ENTERED"})
            csv_text = queries.dk_upload_csv(conn, sid, mode, entered_only=(mode == "ENTERED"))
            return send(h, 200, csv_text.encode(), "text/csv", filename=f"dk-upload-{sid}-{mode.lower()}.csv")
        if q.get("check"):
            if sid is None:
                return send(h, 400, {"ok": False, "error": "check needs id"})
            return send(h, 200, {"ok": True, **pipeline.check(conn, sid)})
        detail = queries.slate_detail(conn, sid)
    if not detail:
        return send(h, 404, {"ok": False, "error": "no slate yet"})
    send(h, 200, {"ok": True, **detail})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _get(self)
