"""Morning grading: verified box scores -> actuals -> every saved lineup scored -> accuracy report.

Only verified games are written. A slate is graded (a `grades` row) only when every one of its
games passed verification; otherwise the run reports which games are still pending and the next
scheduled run retries. Re-grading is idempotent (actuals upsert; a new grades row is appended)."""
import json
from collections import defaultdict
from datetime import date

from .names import NameIndex
from .optimizer import Settings, build_lineups
from .sources import boxscores
from .sources.lineups import team_code


def _f(v):
    return None if v is None else float(v)


def _slate_pool(cur, slate_id):
    cur.execute("""SELECT pl.player_id, pl.draftable_id AS key, p.name, pl.team, pl.opp, pl.game, pl.pos, pl.salary,
                          pl.proj_rg, pl.proj_adj, pl.own_pct, pl.minutes, pl.signals, pl.value_grade, pl.inj_status
                   FROM pool pl JOIN players p ON p.id = pl.player_id WHERE pl.slate_id = %s""", (slate_id,))
    rows = cur.fetchall()
    for r in rows:
        for k in ("proj_rg", "proj_adj", "own_pct", "minutes"):
            r[k] = _f(r[k])
        r["team_code"] = team_code(r["team"])
    return rows


def grade_slate(conn, slate_id: int, day: dict) -> dict:
    """day = boxscores.fetch_day(...) output for the slate's date."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, date, game_list FROM slates WHERE id=%s", (slate_id,))
        slate = cur.fetchone()
        pool = _slate_pool(cur, slate_id)
    slate_games = [frozenset(team_code(t) for t in g["game"].split("@")) for g in slate["game_list"] or []]
    by_teams = {frozenset(g["teams"]): g for g in day["games"]}
    report = {"slate_id": slate_id, "games": {}, "unmatched_box_names": []}
    verified_teams = set()
    team_rows = {}
    for gk in slate_games:
        label = "@".join(sorted(gk))
        g = by_teams.get(gk)
        if g is None:
            report["games"][label] = ["game not found on ESPN scoreboard"]
            continue
        box = day["boxes"].get(g["event_id"], {})
        problems = boxscores.verify_game(g, box, day["bref"])
        report["games"][label] = problems or "verified"
        if not problems:
            verified_teams |= set(gk)
            for t in gk:
                team_rows[t] = box[t]

    # actuals for players whose game verified; no box line in a verified game = DNP (0)
    index = NameIndex([{**p, "team": p["team_code"]} for p in pool])
    actual = {}
    for team, rows in team_rows.items():
        for r in rows:
            hit, how = index.find(r["name"], team)
            if hit is None:
                if not r["dnp"]:
                    report["unmatched_box_names"].append(f'{r["name"]} ({team})')
                continue
            actual[hit["player_id"]] = r
    with conn.cursor() as cur:
        for p in pool:
            if p["team_code"] not in verified_teams:
                continue
            r = actual.get(p["player_id"]) or {"min": 0, "pts": 0, "fg3m": 0, "reb": 0, "ast": 0, "stl": 0,
                                               "blk": 0, "tov": 0, "dnp": True, "dk_pts": 0.0}
            cur.execute("""INSERT INTO actuals(slate_id, player_id, dk_pts, min, pts, fg3m, reb, ast, stl, blk, tov,
                                               dnp, source, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'espn+bref-verified',now())
                           ON CONFLICT (slate_id, player_id) DO UPDATE SET dk_pts=EXCLUDED.dk_pts, min=EXCLUDED.min,
                             pts=EXCLUDED.pts, fg3m=EXCLUDED.fg3m, reb=EXCLUDED.reb, ast=EXCLUDED.ast, stl=EXCLUDED.stl,
                             blk=EXCLUDED.blk, tov=EXCLUDED.tov, dnp=EXCLUDED.dnp, source=EXCLUDED.source, updated_at=now()""",
                        (slate_id, p["player_id"], r["dk_pts"], r["min"], r["pts"], r["fg3m"], r["reb"], r["ast"],
                         r["stl"], r["blk"], r["tov"], r["dnp"]))
            p["dk_pts"], p["dnp"], p["actual_min"] = r["dk_pts"], r["dnp"], r["min"]
    conn.commit()

    complete = bool(slate_games) and all(v == "verified" for v in report["games"].values())
    report["complete"] = complete
    if not complete:
        return report

    payload = summarize(conn, slate_id, pool)
    payload["games_verified"] = report["games"]
    payload["unmatched_box_names"] = report["unmatched_box_names"]
    with conn.cursor() as cur:
        cur.execute("INSERT INTO grades(slate_id, payload) VALUES (%s,%s)", (slate_id, json.dumps(payload, default=str)))
    conn.commit()
    report["grade"] = payload
    return report


def _mae(pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    return round(sum(abs(a - b) for a, b in pairs) / len(pairs), 2) if pairs else None


def _bias(rows):
    rows = [r for r in rows if r["proj_adj"] is not None]
    if not rows:
        return None
    return {"n": len(rows), "mean_error": round(sum(r["dk_pts"] - r["proj_adj"] for r in rows) / len(rows), 2),
            "mean_pct": round(sum(r["dk_pts"] for r in rows) / max(1e-9, sum(r["proj_adj"] for r in rows)) - 1, 3)}


def summarize(conn, slate_id, pool) -> dict:
    graded = [p for p in pool if "dk_pts" in p]
    # Accuracy on players who were expected to play (projection > 0) — DNPs included: a late scratch is projection error.
    played = [p for p in graded if (p["proj_adj"] or 0) > 0]
    out = {"n_players": len(played),
           "mae_ours": _mae([(p["proj_adj"], p["dk_pts"]) for p in played]),
           "mae_rotogrinders": _mae([(p["proj_rg"], p["dk_pts"]) for p in played])}
    buckets = defaultdict(list)
    for p in played:
        for s in p["signals"] or []:
            buckets[s].append(p)
        if p["salary"] < 4500:
            buckets["punt_under_4500"].append(p)
        if p["salary"] >= 8000:
            buckets["stud_8000_plus"].append(p)
        if p["value_grade"]:
            buckets[f"value_grade_{p['value_grade']}"].append(p)
    out["bias_by_bucket"] = {k: _bias(v) for k, v in sorted(buckets.items())}
    games = defaultdict(list)
    for p in played:
        games[p["game"]].append(p)
    out["bias_by_game"] = {g: {**_bias(v), "actual_total_dk": round(sum(x["dk_pts"] for x in v), 1),
                               "projected_total_dk": round(sum(x["proj_adj"] for x in v), 1)}
                           for g, v in sorted(games.items())}
    # value-board check: 25+ DK points hit rate by grade
    vb = defaultdict(lambda: [0, 0])
    for p in played:
        if p["value_grade"]:
            vb[p["value_grade"]][0] += 1
            vb[p["value_grade"]][1] += p["dk_pts"] >= 25
    out["value_board_hits"] = {g: {"n": n, "hit_25": h, "rate": round(h / n, 2) if n else None} for g, (n, h) in sorted(vb.items())}

    # every saved lineup gets its actual score
    by_key = {p["key"]: p for p in graded}
    with conn.cursor() as cur:
        cur.execute("SELECT id, mode, rank, proj, players, entered, run_id, built_at FROM lineups WHERE slate_id=%s", (slate_id,))
        lus = cur.fetchall()
        scored = []
        for lu in lus:
            keys = [x["key"] for x in lu["players"]]
            if all(k in by_key for k in keys):
                score = round(sum(by_key[k]["dk_pts"] for k in keys), 2)
                cur.execute("UPDATE lineups SET actual_score=%s WHERE id=%s", (score, lu["id"]))
                scored.append({"id": lu["id"], "mode": lu["mode"], "rank": lu["rank"], "entered": lu["entered"],
                               "proj": _f(lu["proj"]), "actual": score})
    conn.commit()
    ent = [s for s in scored if s["entered"]]
    out["lineups"] = {"scored": len(scored), "entered": ent,
                      "best_generated": max(scored, key=lambda s: s["actual"]) if scored else None}

    # hindsight-optimal: best possible DK lineup from this slate's pool, DK rules only
    hs = build_lineups([p for p in graded], Settings(mode="ACTUAL", min_salary=0, max_from_team=8, min_uniques=1), n=1)
    if hs:
        h = hs[0]
        sal = sorted((x["salary"] for x in h["players"]), reverse=True)
        out["hindsight_optimal"] = {
            "score": round(sum(by_key[x["key"]]["dk_pts"] for x in h["players"]), 2), "salary": h["salary"],
            "players": [{"slot": x["slot"], "name": x["name"], "salary": x["salary"],
                         "proj": by_key[x["key"]]["proj_adj"], "actual": by_key[x["key"]]["dk_pts"],
                         "own": by_key[x["key"]]["own_pct"]} for x in h["players"]],
            "salary_shape": {"under_4500": sum(s < 4500 for s in sal), "4500_6500": sum(4500 <= s < 6500 for s in sal),
                             "6500_8000": sum(6500 <= s < 8000 for s in sal), "8000_plus": sum(s >= 8000 for s in sal)}}
    # best plays we missed: top actual value (pts per $1K) not in any saved lineup
    used = {x["key"] for lu in lus for x in lu["players"]}
    missed = sorted((p for p in played if p["key"] not in used),
                    key=lambda p: -(p["dk_pts"] / (p["salary"] / 1000)))[:8]
    out["missed_plays"] = [{"name": p["name"], "salary": p["salary"], "proj": p["proj_adj"], "actual": p["dk_pts"],
                            "value": round(p["dk_pts"] / (p["salary"] / 1000), 2), "own": p["own_pct"]} for p in missed]
    return out


def slates_to_grade(conn, today: date) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("""SELECT s.id FROM slates s WHERE s.date < %s
                         AND NOT EXISTS (SELECT 1 FROM grades g WHERE g.slate_id = s.id)
                         AND s.date >= %s::date - 7
                       ORDER BY s.date""", (today, today))
        return [r["id"] for r in cur.fetchall()]
