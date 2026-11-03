"""TEMPORARY, read-only: GET /api/peek?src=rotowire|rglineups&start=0&n=20000
Returns a slice of a public lineups page's HTML so the parser can be written against the real layout.
Writes nothing. Remove once the lineup parsers are done."""
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs.http import get  # noqa: E402
from dfs.web import guarded, query, send  # noqa: E402

SOURCES = {
    "rotowire": "https://www.rotowire.com/basketball/nba-lineups.php",
    "rglineups": "https://rotogrinders.com/lineups/nba?site=draftkings",
}


@guarded
def _get(h):
    q = query(h)
    if q.get("parse"):
        from dfs.sources import lineups
        src = lineups.fetch_all()
        summ = {"errors": src["errors"]}
        for key in ("rotowire", "rg"):
            games = src[key] or []
            summ[key] = {"games": len(games), "sample": [
                {"game": f'{g["away"]}@{g["home"]}', "total": g["total"], "fav_ml": g["fav_ml"],
                 "teams": {t: {"status": v.get("status", v.get("confirmed")),
                               "starters": [x["name"] for x in v["starters"]],
                               "tags": [f'{x["name"]}:{x.get("inj")}' for x in v.get("may_not_play", []) + v["starters"]
                                        if x.get("inj")]}
                           for t, v in g["teams"].items()}} for g in games[:3]],
                "teams_with_5_starters": sum(1 for g in games for v in g["teams"].values() if len(v["starters"]) == 5),
                "teams": sum(len(g["teams"]) for g in games)}
        return send(h, 200, summ)
    url = SOURCES[q.get("src", "rotowire")]
    html = get(url).decode("utf-8", "replace")
    start, n = int(q.get("start", 0)), min(int(q.get("n", 20000)), 60000)
    find = q.get("find")
    if find:
        idx = html.find(find)
        start = max(0, idx - 200) if idx >= 0 else start
    send(h, 200, f"<!-- total {len(html)} chars; slice {start}-{start + n} -->\n{html[start:start + n]}".encode(),
         "text/plain; charset=utf-8")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        _get(self)
