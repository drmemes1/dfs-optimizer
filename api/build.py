"""POST /api/build
  {action: "build",   dk_b64|dk_csv, rg_b64|rg_csv, dk_name?, rg_name?, n_gpp?, n_cash?, overrides?, locks?, excludes?}
  {action: "rebuild", slate_id, n_gpp?, n_cash?, overrides?, locks?, excludes?}
  {action: "refresh", rg_b64|rg_csv, dk_b64?|dk_csv?, slate_id?}   -> pool updated + late-scratch check
overrides = {"GPP": {max_from_team, max_per_game, max_own_sum, min_salary, min_uniques, ceil_weight, own_penalty},
             "CASH": {...}}; locks/excludes are DK draftable IDs."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import pipeline  # noqa: E402
from dfs.sources.uploads import UploadError  # noqa: E402
from dfs.web import guarded, read_json, send  # noqa: E402


def _ints(xs):
    return [int(x) for x in (xs or [])]


@guarded
def _post(h):
    b = read_json(h)
    action = b.get("action") or "build"
    n_gpp = max(1, min(int(b.get("n_gpp") or 10), 40))
    n_cash = max(1, min(int(b.get("n_cash") or 3), 10))
    common = dict(overrides=b.get("overrides"), locks=_ints(b.get("locks")), excludes=_ints(b.get("excludes")))
    if action == "build":
        dk, dk_raw = pipeline.decode_upload(b.get("dk_b64"), b.get("dk_csv"))
        rg, rg_raw = pipeline.decode_upload(b.get("rg_b64"), b.get("rg_csv"))
        if not dk or not rg:
            raise UploadError("Upload both DKSalaries.csv and the RotoGrinders export")
        out = pipeline.run_build(dk, rg, n_gpp, n_cash, dk_raw=dk_raw, rg_raw=rg_raw,
                                 dk_name=b.get("dk_name"), rg_name=b.get("rg_name"), **common)
    elif action == "rebuild":
        out = pipeline.run_rebuild(int(b["slate_id"]), n_gpp, n_cash, **common)
    elif action == "refresh":
        rg, rg_raw = pipeline.decode_upload(b.get("rg_b64"), b.get("rg_csv"))
        if not rg:
            raise UploadError("Upload a fresh RotoGrinders export to re-check inactives")
        dk, dk_raw = pipeline.decode_upload(b.get("dk_b64"), b.get("dk_csv"))
        out = pipeline.run_refresh(rg, rg_raw, b.get("rg_name"), dk or None, dk_raw or None, b.get("dk_name"),
                                   slate_id=int(b["slate_id"]) if b.get("slate_id") else None)
    else:
        raise UploadError(f"unknown action {action}")
    send(h, 200, {"ok": True, **out})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        _post(self)
