"""GET /api/net_probe — read-only: report which upstream data sources answer from Vercel."""
import json
import urllib.error
from http.server import BaseHTTPRequestHandler
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dfs.http import UA  # noqa: E402
import urllib.request  # noqa: E402

URLS = [
    "https://www.draftkings.com/lobby/getcontests?sport=NBA",
    "https://www.draftkings.com/lobby/getcontests?sport=NBA&format=json",
    "https://api.draftkings.com/sites/US-DK/sports/v1/sports?format=json",
    "https://api.draftkings.com/draftgroups/v1/draftgroups/1/draftables?format=json",
    "https://api.draftkings.com/lineups/v1/gametypes/70/rules?format=json",
    "https://rotogrinders.com/lineups/nba?site=draftkings",
    "https://www.rotowire.com/basketball/nba-lineups.php",
    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
    "https://www.statmuse.com/nba",
]


def probe(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/html,*/*",
                                               "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            b = r.read(400)
            return {"status": r.status, "bytes_head": b[:120].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "server": e.headers.get("Server"), "bytes_head": e.read(120).decode("utf-8", "replace")}
    except Exception as e:
        return {"status": None, "error": f"{type(e).__name__}: {e}"[:200]}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = {u: probe(u) for u in URLS}
        data = json.dumps(body, indent=1).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
