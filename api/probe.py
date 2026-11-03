"""TEMPORARY, read-only: which box-score / final-score sources answer from Vercel. Writes nothing."""
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dfs.http import UA  # noqa: E402
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
from dfs.web import query, send  # noqa: E402

URLS = [
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20261102",
    "https://www.statmuse.com/nba/ask/nba-box-scores-november-2-2026",
    "https://statsdmz.nba.com/pdfs/20261102/20261102_OKCCLE.pdf",
    "https://www.cbssports.com/nba/scoreboard/20261102/",
    "https://www.basketball-reference.com/boxscores/?month=11&day=2&year=2026",
    "https://stats.nba.com/stats/scoreboardv3?GameDate=2026-11-02&LeagueID=00",
]


def probe(url, n=600, find=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
            b = r.read()
            if find:
                i = b.find(find.encode())
                b = b[max(0, i - 300):] if i >= 0 else b"(not found) " + b
            return {"status": r.status, "bytes": len(b), "type": r.headers.get("Content-Type"),
                    "head": b[:n].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except Exception as e:
        return {"status": None, "error": f"{type(e).__name__}: {e}"[:200]}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = query(self)
        if q.get("boxday"):   # read-only: parse + verify a day's box scores, write nothing
            from datetime import date
            from dfs.sources import boxscores
            y, m, d = int(q["boxday"][:4]), int(q["boxday"][4:6]), int(q["boxday"][6:8])
            day = boxscores.fetch_day(date(y, m, d))
            out = {"games": len(day["games"]), "bref_finals": len(day["bref"]), "verification": {}}
            for g in day["games"]:
                box = day["boxes"].get(g["event_id"], {})
                label = "@".join(sorted(g["teams"]))
                out["verification"][label] = {
                    "problems": boxscores.verify_game(g, box, day["bref"]) or "verified",
                    "minutes": {t: sum(r["min"] for r in rows) for t, rows in box.items()},
                    "top": sorted(([r["name"], r["min"], r["pts"], r["dk_pts"]] for rows in box.values() for r in rows),
                                  key=lambda x: -x[3])[:2]}
            return send(self, 200, out)
        if q.get("url"):
            return send(self, 200, probe(q["url"], int(q.get("n", 4000)), q.get("find")))
        send(self, 200, {u: probe(u) for u in URLS})
