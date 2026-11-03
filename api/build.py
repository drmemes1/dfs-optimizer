"""POST /api/build {dk_csv, rg_csv, n_gpp?, n_cash?} — run the build job and save it."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs.pipeline import run_build  # noqa: E402
from dfs.sources.uploads import UploadError  # noqa: E402
from dfs.web import guarded, read_json, send  # noqa: E402


@guarded
def _post(h):
    body = read_json(h)
    dk, rg = body.get("dk_csv") or "", body.get("rg_csv") or ""
    if not dk or not rg:
        raise UploadError("Upload both DKSalaries.csv and the RotoGrinders export")
    n_gpp = max(1, min(int(body.get("n_gpp") or 10), 40))
    n_cash = max(1, min(int(body.get("n_cash") or 3), 10))
    send(h, 200, {"ok": True, **run_build(dk, rg, n_gpp=n_gpp, n_cash=n_cash)})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        _post(self)
