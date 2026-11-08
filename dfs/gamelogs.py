"""Per-player game logs, built from the same verified ESPN box scores the grader uses.

Only verified games are written. An unverified box score would poison a player's recent rate for
as long as it sits in the lookback, which is exactly the silent corruption PLAN.md's box-score
verification rule exists to prevent."""
from datetime import timedelta

from .names import NameIndex
from .scoring import dk_points
from .sources import boxscores


def _player_index(conn):
    """Every known player, for matching box-score names. Players enter this table when they first
    appear on a slate, so a name matching nobody is simply someone we have never drafted.

    Matched the way grading matches: NameIndex applies the alias table, so ESPN's legal names
    reach DraftKings' nicknames (Luguentz Dort -> Lu Dort, and eleven more). Bare name_norm
    misses every one of them, silently."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, name_norm, last_team FROM players")
        rows = cur.fetchall()
    return NameIndex([{**r, "team": r["last_team"]} for r in rows])


def ingest_day(conn, game_date, day) -> dict:
    """Write one row per player line in every verified game on `game_date`."""
    index = _player_index(conn)
    out = {"written": 0, "skipped_games": {}, "unmatched": [], "ambiguous": [],
           "team_mismatch": []}
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
                name = line.get("name") or ""
                hit, how = index.find(name, team)
                if hit is None:
                    out["unmatched"].append(name)
                    continue
                if how != "exact":
                    # grading accepts a same-team last-name fallback because it only ever looks
                    # inside one slate's pool. Here we scan a whole league night against every
                    # player we have ever drafted, so a fallback is far likelier to be a
                    # different human. Report it and write nothing.
                    out["ambiguous"].append(name)
                    continue
                if hit.get("last_team") and hit["last_team"] != team:
                    # one matching row is not proof it is the same person: a same-name call-up
                    # would have his line written to this player's log and corrupt the rate for
                    # the whole lookback. A traded player loses his pre-trade games this way,
                    # leaving him short of form_min_games and therefore unadjusted - the safe
                    # failure rather than the wrong one.
                    out["team_mismatch"].append({"name": name, "box": team,
                                                 "known": hit["last_team"]})
                    continue
                rows.append((hit["id"], game_date, team, opp,
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


def recent_rates(conn, name_norms, before_date, lookback: int) -> dict:
    """DK points per minute over each player's last `lookback` games with minutes, strictly before
    `before_date`.

    Keyed on name_norm because prepare() runs before players have database ids. A name_norm shared
    by two players yields no rate at all: merging their games would corrupt both."""
    names = sorted({n for n in name_norms if n})
    if not names:
        return {}
    with conn.cursor() as cur:
        cur.execute("""SELECT name_norm, count(*) AS n_ids FROM players
                       WHERE name_norm = ANY(%s) GROUP BY name_norm""", (names,))
        ambiguous = {r["name_norm"] for r in cur.fetchall() if r["n_ids"] > 1}
        usable = [n for n in names if n not in ambiguous]
        if not usable:
            return {}
        cur.execute("""SELECT name_norm, game_date, min, dk_pts FROM (
                         SELECT p.name_norm, g.game_date, g.min, g.dk_pts,
                                row_number() OVER (PARTITION BY p.name_norm
                                                   ORDER BY g.game_date DESC) AS rn
                         FROM player_games g JOIN players p ON p.id = g.player_id
                         WHERE p.name_norm = ANY(%s) AND g.game_date < %s AND g.min > 0
                       ) t WHERE rn <= %s""", (usable, before_date, lookback))
        rows = cur.fetchall()
    agg = {}
    for r in rows:
        a = agg.setdefault(r["name_norm"], {"minutes": 0.0, "points": 0.0, "games": 0,
                                            "last_game_date": None})
        a["minutes"] += float(r["min"])
        a["points"] += float(r["dk_pts"])
        a["games"] += 1
        if a["last_game_date"] is None or r["game_date"] > a["last_game_date"]:
            a["last_game_date"] = r["game_date"]
    return {k: {"rate": (v["points"] / v["minutes"]) if v["minutes"] else 0.0,
                "minutes": v["minutes"], "games": v["games"],
                "last_game_date": v["last_game_date"]}
            for k, v in agg.items()}
