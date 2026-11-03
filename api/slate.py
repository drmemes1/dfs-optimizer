"""GET /api/slate?id=<slate_id>  (latest slate when omitted)
   GET /api/slate?list=1         (slate history)
   GET /api/slate?id=..&export=GPP|CASH  (DK bulk-upload CSV)"""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import db, queries  # noqa: E402
from dfs.web import guarded, query, send  # noqa: E402


@guarded
def _get(h):
    q = query(h)
    with db.connect() as conn:
        db.migrate(conn)
        if q.get("list"):
            return send(h, 200, {"ok": True, "slates": queries.list_slates(conn)})
        sid = int(q["id"]) if q.get("id") else None
        if q.get("export"):
            mode = q["export"].upper()
            if mode not in ("GPP", "CASH", "OPTIMAL") or sid is None:
                return send(h, 400, {"ok": False, "error": "export needs id and mode GPP|CASH|OPTIMAL"})
            csv_text = queries.dk_upload_csv(conn, sid, mode)
            return send(h, 200, csv_text.encode(), "text/csv", filename=f"dk-upload-{sid}-{mode.lower()}.csv")
        detail = queries.slate_detail(conn, sid)
    if not detail:
        return send(h, 404, {"ok": False, "error": "no slate yet"})
    send(h, 200, {"ok": True, **detail})


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _get(self)
