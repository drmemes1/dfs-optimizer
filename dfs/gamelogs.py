"""Per-player game logs, built from the same verified ESPN box scores the grader uses.

Only verified games are written. An unverified box score would poison a player's recent rate for
as long as it sits in the lookback, which is exactly the silent corruption PLAN.md's box-score
verification rule exists to prevent."""
from datetime import timedelta

from .names import name_norm
from .scoring import dk_points
from .sources import boxscores


def _player_index(conn):
    """Every known player, for matching box-score names. Players enter this table when they first
    appear on a slate, so a name matching nobody is simply someone we have never drafted."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, name_norm, last_team FROM players")
        rows = cur.fetchall()
    by_norm = {}
    for r in rows:
        by_norm.setdefault(r["name_norm"], []).append(r)
    return by_norm


def ingest_day(conn, game_date, day) -> dict:
    """Write one row per player line in every verified game on `game_date`."""
    by_norm = _player_index(conn)
    out = {"written": 0, "skipped_games": {}, "unmatched": [], "ambiguous": []}
    rows = []
    for g in day.get("games", []):
        label = "@".join(sorted(g["teams"]))
        box = day.get("boxes", {}).get(g["event_id"], {})
        problems = boxscores.verify_game(g, box, day.get("bref") or {})
        if problems:
            out["skipped_games"][label] = problems
            continue
        teams = list(g["teams"])
        for team, lines in box.items():
            opp = next((t for t in teams if t != team), None)
            for line in lines:
                cands = by_norm.get(name_norm(line.get("name") or ""), [])
                if not cands:
                    out["unmatched"].append(line.get("name"))
                    continue
                if len(cands) > 1:
                    # two players share a normalised name; attributing games to the wrong one
                    # would silently corrupt a rate, so neither gets the game unless the team
                    # settles it
                    same_team = [c for c in cands if c["last_team"] == team]
                    if len(same_team) != 1:
                        out["ambiguous"].append(line.get("name"))
                        continue
                    cands = same_team
                rows.append((cands[0]["id"], game_date, team, opp,
                             line.get("min") or 0,
                             dk_points(pts=line.get("pts") or 0, fg3m=line.get("fg3m") or 0,
                                       reb=line.get("reb") or 0, ast=line.get("ast") or 0,
                                       stl=line.get("stl") or 0, blk=line.get("blk") or 0,
                                       tov=line.get("tov") or 0, dnp=bool(line.get("dnp"))),
                             line.get("pts") or 0, line.get("fg3m") or 0, line.get("reb") or 0,
                             line.get("ast") or 0, line.get("stl") or 0, line.get("blk") or 0,
                             line.get("tov") or 0, bool(line.get("dnp")), "espn"))
    if rows:
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO player_games(player_id, game_date, team, opp, min, dk_pts,
                                 pts, fg3m, reb, ast, stl, blk, tov, dnp, source)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (player_id, game_date) DO UPDATE SET
                                 team=EXCLUDED.team, opp=EXCLUDED.opp, min=EXCLUDED.min,
                                 dk_pts=EXCLUDED.dk_pts, pts=EXCLUDED.pts, fg3m=EXCLUDED.fg3m,
                                 reb=EXCLUDED.reb, ast=EXCLUDED.ast, stl=EXCLUDED.stl,
                                 blk=EXCLUDED.blk, tov=EXCLUDED.tov, dnp=EXCLUDED.dnp,
                                 source=EXCLUDED.source, updated_at=now()""", rows)
        conn.commit()
        out["written"] = len(rows)
    return out


def backfill(conn, through_date, days: int, fetch_day=None) -> dict:
    """Walk back `days` nights from the day before `through_date`. Safe to re-run."""
    fetch = fetch_day or boxscores.fetch_day
    out = {"days": {}, "errors": {}}
    for i in range(1, days + 1):
        d = through_date - timedelta(days=i)
        try:
            out["days"][str(d)] = ingest_day(conn, d, fetch(d))
        except Exception as e:  # noqa: BLE001 - one bad night must not stop the walk
            out["errors"][str(d)] = f"{type(e).__name__}: {e}"[:200]
    return out
