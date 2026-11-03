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
