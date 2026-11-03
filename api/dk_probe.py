"""GET /api/dk_probe — read-only: identify today's main Classic slate on DK and summarize
its draftables. Writes nothing. Used to verify the DK parsers against live data."""
import json
import sys
import traceback
from collections import Counter
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dfs.sources import dk  # noqa: E402


def _keys(obj, depth=0):
    if isinstance(obj, dict) and depth < 2:
        return {k: _keys(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [_keys(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            lobby = dk.fetch_lobby()
            groups = dk.classic_groups(lobby)
            body = {"lobby_shape": {k: _keys(v[:1] if isinstance(v, list) else v)
                                    for k, v in lobby.items()},
                    "classic_groups": [{**g, "start": str(g["start"])} for g in groups[:8]]}
            if groups:
                raw = dk.fetch_draftables(groups[0]["draft_group_id"])
                parsed = dk.parse_draftables(raw)
                body["draftables_shape"] = _keys({k: (v[:1] if isinstance(v, list) else v)
                                                  for k, v in raw.items()})
                body["main"] = {
                    "draft_group_id": groups[0]["draft_group_id"],
                    "lock_time_utc": str(parsed["lock_time_utc"]),
                    "games": [g["game"] for g in parsed["games"]],
                    "raw_draftables": len(raw.get("draftables", [])),
                    "unique_players": len(parsed["players"]),
                    "status_counts": Counter(p["inj_status"] or "-" for p in parsed["players"]),
                    "sample": sorted(parsed["players"], key=lambda p: -p["salary"])[:3],
                }
            status = 200
        except Exception as e:
            traceback.print_exc()
            body, status = {"ok": False, "error": type(e).__name__, "detail": str(e)[:300]}, 500
        data = json.dumps(body, indent=1, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
