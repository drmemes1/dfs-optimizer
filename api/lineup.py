"""POST /api/lineup {id, action: "entered", value: bool}
                    {id, action: "swap", out_key, in_key}     (validated against DK rules + lock times)"""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import db, pipeline  # noqa: E402
from dfs.sources.uploads import UploadError  # noqa: E402
from dfs.web import guarded, read_json, send  # noqa: E402


@guarded
def _post(h):
    b = read_json(h)
    lid = int(b["id"])
    with db.connect() as conn:
        if b.get("action") == "entered":
            out = pipeline.set_entered(conn, lid, bool(b.get("value")))
        elif b.get("action") == "swap":
            out = pipeline.swap(conn, lid, int(b["out_key"]), int(b["in_key"]))
        else:
            raise UploadError("action must be entered or swap")
    send(h, 200, {"ok": True, **out})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        _post(self)
