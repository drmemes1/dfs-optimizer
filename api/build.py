"""POST /api/build
  {action: "build",   dk_b64|dk_csv, rg_b64|rg_csv, dk_name?, rg_name?, n_gpp?, n_cash?, overrides?, locks?, excludes?}
  {action: "rebuild", slate_id, n_gpp?, n_cash?, overrides?, locks?, excludes?}
  {action: "import_contest", csv_b64, filename, meta: {contest_id?, name, entry_fee, prize_pool, places_paid,
                                                        max_entries, top_heaviness, dk_username, slate_id?}}
  every build/rebuild may carry contest: {name, entry_fee, field_size, top_heaviness, entry_mode SE|ME, max_entries}
  {action: "refresh", slate_id?, rg_b64?, dk_b64?}  -> fresh lineup pages (+ optional new files), pool updated,
                                                        late-scratch check
overrides = {"GPP": {max_from_team, max_per_game, max_own_sum, min_salary, min_uniques, ceil_weight, own_penalty},
             "CASH": {...}}; locks/excludes are DK draftable IDs."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs import contests, db, pipeline  # noqa: E402
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
    common = dict(overrides=b.get("overrides"), locks=_ints(b.get("locks")), excludes=_ints(b.get("excludes")),
                  contest=contests.clean_context(b.get("contest")))
    names = dict(lock_names=b.get("lock_names"), exclude_names=b.get("exclude_names"), remember=bool(b.get("remember")))
    if action == "build":
        dk, dk_raw = pipeline.decode_upload(b.get("dk_b64"), b.get("dk_csv"))
        rg, rg_raw = pipeline.decode_upload(b.get("rg_b64"), b.get("rg_csv"))
        if not dk:
            raise UploadError("Upload DKSalaries.csv")
        # rg may be empty: run_build then reads RotoGrinders with the stored login (or explains why it can't)
        out = pipeline.run_build(dk, rg, n_gpp, n_cash, dk_raw=dk_raw, rg_raw=rg_raw,
                                 dk_name=b.get("dk_name"), rg_name=b.get("rg_name"), **common, **names)
    elif action == "rebuild":
        out = pipeline.run_rebuild(int(b["slate_id"]), n_gpp, n_cash, **common, **names)
    elif action == "refresh":
        rg, rg_raw = pipeline.decode_upload(b.get("rg_b64"), b.get("rg_csv"))
        dk, dk_raw = pipeline.decode_upload(b.get("dk_b64"), b.get("dk_csv"))
        out = pipeline.run_refresh(rg or None, rg_raw or None, b.get("rg_name"), dk or None, dk_raw or None,
                                   b.get("dk_name"), slate_id=int(b["slate_id"]) if b.get("slate_id") else None)
    elif action == "import_contest":
        _, raw = pipeline.decode_upload(b.get("csv_b64"), None)
        if not raw:
            raise UploadError("choose the contest standings CSV")
        with db.connect() as conn:
            db.migrate(conn)
            out = contests.import_standings(conn, raw, b.get("filename"), b.get("meta") or {})
    else:
        raise UploadError(f"unknown action {action}")
    send(h, 200, {"ok": True, **out})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        _post(self)
