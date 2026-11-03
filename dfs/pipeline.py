"""Build job: uploads -> merge -> signals -> pool -> value board -> lineups -> database.
Re-runnable: pool rows upsert on (slate_id, player_id); each run's lineups share a run_id."""
import json
import uuid

from . import db
from .optimizer import Settings, build_lineups
from .pool import eligible_pool, value_board
from .signals import apply_signals, estimate_missing_ownership, is_questionable
from .sources.uploads import merge, parse_dk_salaries, parse_rg_projections


def prepare(dk_text: str, rg_text: str, params: dict) -> dict:
    dk = parse_dk_salaries(dk_text)
    rg = parse_rg_projections(rg_text)
    m = merge(dk, rg)
    players = m["players"]
    for i, p in enumerate(players):
        p["key"] = p["draftable_id"]
    apply_signals(players, params)
    estimate_missing_ownership(players)
    board = value_board(players, params)
    return {"dk": dk, "merge": m, "players": players, "value_board": board}


def make_lineups(players: list[dict], params: dict, n_gpp=10, n_cash=3) -> dict:
    out = {}
    gpp = Settings.from_params(params, "GPP")
    out["GPP"] = build_lineups(eligible_pool(players, params, "GPP"), gpp, n=n_gpp)
    cash = Settings.from_params(params, "CASH")
    out["CASH"] = build_lineups(eligible_pool(players, params, "CASH"), cash, n=n_cash)
    opt = Settings(mode="OPTIMAL", min_salary=0, max_from_team=8, min_uniques=1)
    out["OPTIMAL"] = build_lineups(eligible_pool(players, params, "GPP"), opt, n=1)
    return out


def _upsert_player(cur, p) -> int:
    cur.execute("SELECT id, last_team FROM players WHERE name_norm = %s", (p["name_norm"],))
    rows = cur.fetchall()
    match = [r for r in rows if r["last_team"] == p["team"]] or (rows if len(rows) == 1 else [])
    if match:
        pid = match[0]["id"]
        cur.execute("UPDATE players SET name=%s, last_team=%s WHERE id=%s", (p["name"], p["team"], pid))
        return pid
    cur.execute("INSERT INTO players(name, name_norm, last_team) VALUES (%s,%s,%s) RETURNING id",
                (p["name"], p["name_norm"], p["team"]))
    return cur.fetchone()["id"]


POOL_COLS = ["draftable_id", "team", "opp", "game", "pos", "salary", "minutes", "proj_rg", "proj_adj",
             "floor", "ceil", "sim85", "own_pct", "own_estimated", "starter_rg", "starter_rw",
             "starter_consensus", "contingent", "inj_status", "questionable_mult", "vegas_total",
             "fav_ml", "b2b", "signal_mult", "signals", "value", "value_grade"]


def save(conn, prep: dict, lineups: dict, params_version: int, dk_text: str, rg_text: str,
         draft_group_id=None) -> dict:
    dk = prep["dk"]
    slate_key = ",".join(sorted(g["game"] for g in dk["games"]))
    run_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO slates(date, site, draft_group_id, slate_type, n_games, lock_time_utc,
                                          game_list, slate_key)
                       VALUES (%s,'DK',%s,'Classic',%s,%s,%s,%s)
                       ON CONFLICT (site, date, slate_key) DO UPDATE SET
                         draft_group_id = COALESCE(EXCLUDED.draft_group_id, slates.draft_group_id),
                         lock_time_utc = EXCLUDED.lock_time_utc, updated_at = now()
                       RETURNING id""",
                    (dk["date"], draft_group_id, len(dk["games"]), dk["lock_time"],
                     json.dumps([{"game": g["game"], "start": g["start"].isoformat()} for g in dk["games"]]),
                     slate_key))
        slate_id = cur.fetchone()["id"]
        key_to_pid = {}
        for p in prep["players"]:
            pid = _upsert_player(cur, p)
            key_to_pid[p["key"]] = pid
            vals = [p.get(c) for c in POOL_COLS]
            vals[POOL_COLS.index("own_estimated")] = bool(p.get("own_estimated"))
            vals[POOL_COLS.index("contingent")] = bool(p.get("contingent"))
            cols = ", ".join(POOL_COLS)
            sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in POOL_COLS)
            cur.execute(f"""INSERT INTO pool(slate_id, player_id, {cols}, updated_at)
                            VALUES (%s, %s, {", ".join(["%s"] * len(POOL_COLS))}, now())
                            ON CONFLICT (slate_id, player_id) DO UPDATE SET {sets}, updated_at=now()""",
                        [slate_id, pid, *vals])
        for mode, lus in lineups.items():
            for rank, lu in enumerate(lus, 1):
                for x in lu["players"]:
                    x["player_id"] = key_to_pid.get(x["key"])
                cur.execute("""INSERT INTO lineups(slate_id, mode, run_id, rank, params_version, salary, proj,
                                                   ceil, own_sum, players)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (slate_id, mode, run_id, rank, params_version, lu["salary"], lu["proj"],
                             lu["ceil"], lu["own_sum"], json.dumps(lu["players"])))
        for source, text in (("dk_salaries_csv", dk_text), ("rg_projections_csv", rg_text)):
            cur.execute("INSERT INTO source_snapshots(slate_id, source, payload) VALUES (%s,%s,%s)",
                        (slate_id, source, json.dumps({"text": text})))
    conn.commit()
    return {"slate_id": slate_id, "run_id": str(run_id)}


def run_build(dk_text: str, rg_text: str, n_gpp=10, n_cash=3) -> dict:
    with db.connect() as conn:
        db.migrate(conn)
        db.seed_params(conn)
        version, params = db.current_params(conn)
        prep = prepare(dk_text, rg_text, params)
        lineups = make_lineups(prep["players"], params, n_gpp, n_cash)
        ids = save(conn, prep, lineups, version, dk_text, rg_text)
    m = prep["merge"]
    return {
        **ids, "params_version": version, "date": str(prep["dk"]["date"]),
        "games": [g["game"] for g in prep["dk"]["games"]],
        "players": len(prep["players"]),
        "warnings": {"unmatched_dk": m["unmatched_dk"], "unmatched_rg": m["unmatched_rg"][:40],
                     "salary_mismatch": m["salary_mismatch"],
                     "own_estimated": sum(1 for p in prep["players"] if p.get("own_estimated")),
                     "starters_source": "not connected yet - consensus starter box is empty"},
        "lineups": lineups,
        "value_board": [{k: r.get(k) for k in ("name", "team", "pos", "salary", "minutes", "proj_adj",
                                                "value", "value_grade", "boxes", "own_pct")}
                        for r in prep["value_board"]],
        "watch": [{"name": p["name"], "team": p["team"], "salary": p["salary"], "status": p["inj_status"],
                   "proj_adj": p["proj_adj"]} for p in prep["players"] if is_questionable(p)],
    }
