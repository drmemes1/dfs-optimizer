"""Build job and the actions around it.

build    uploads -> merge -> signals -> pool -> value board -> lineups -> database
rebuild  stored pool + locks/excludes/constraint overrides -> new lineups (shows the cost)
refresh  fresh RotoGrinders (and optionally DK) upload -> pool updated in place, no new lineups
check    saved lineups vs current statuses -> flags + same-slot swaps that fit the salary left
swap     validated in-place edit of a saved lineup (original kept)

Pool rows upsert on (slate_id, player_id); every build/refresh also appends a full pool snapshot.
Each lineup run gets its own run_id and records params_version, settings, locks and excludes."""
import base64
import gzip
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timezone

from . import db, prefs
from .optimizer import SALARY_CAP, SLOTS, Settings, build_lineups, eligible_slots
from .pool import eligible_pool, value_board
from .signals import SIGNAL_COLUMNS, apply_signals, estimate_missing_ownership, is_out, is_questionable
from .sources import lineups as lineup_sources
from .sources.uploads import UploadError, merge, parse_dk_salaries, parse_rg_projections

OVERRIDE_KEYS = {"max_from_team": int, "max_per_game": int, "max_own_sum": float, "min_salary": int,
                 "min_uniques": int, "ceil_weight": float, "own_penalty": float}
FLAG_STATUSES = {"Q", "GTD", "D", "O", "OUT", "NOPROJ"}


# ---------------------------------------------------------------- inputs
def decode_upload(b64: str | None, text: str | None) -> tuple[str, bytes]:
    """Accept the exact file bytes (base64) or plain text. Returns (text, raw bytes)."""
    if b64:
        raw = base64.b64decode(b64)
        for enc in ("utf-8-sig", "cp1252"):
            try:
                return raw.decode(enc), raw
            except UnicodeDecodeError:
                continue
        raise UploadError("file is not readable text")
    if text:
        return text, text.encode("utf-8")
    return "", b""


def clean_overrides(raw: dict | None) -> dict:
    """{"GPP": {...}, "CASH": {...}} -> typed values for known keys only."""
    out = {}
    for mode in ("GPP", "CASH"):
        vals = {}
        for k, v in ((raw or {}).get(mode) or {}).items():
            if k in OVERRIDE_KEYS and v not in (None, ""):
                vals[k] = OVERRIDE_KEYS[k](v)
        out[mode] = vals
    return out


# ---------------------------------------------------------------- core build
def prepare(dk_text: str, rg_text: str, params: dict, lineup_src: dict | None = None) -> dict:
    dk = parse_dk_salaries(dk_text)
    rg = parse_rg_projections(rg_text)
    m = merge(dk, rg)
    players = m["players"]
    starts = {g["game"]: g["start"] for g in dk["games"]}
    for p in players:
        p["key"] = p["draftable_id"]
        p["game_start"] = starts[p["game"]].isoformat() if p.get("game") in starts else None
    lineup_report = None
    if lineup_src is not None:
        lineup_report = lineup_sources.apply_lineups(players, lineup_src)
        lineup_report["errors"] = lineup_src.get("errors", {})
    apply_signals(players, params)
    estimate_missing_ownership(players)
    board = value_board(players, params)
    return {"dk": dk, "merge": m, "players": players, "value_board": board, "lineups": lineup_report}


def _settings(params, mode, overrides, locks, excludes) -> Settings:
    ov = overrides.get("GPP" if mode == "CONSENSUS" else mode, {})
    return Settings.from_params(params, mode, **ov, locks=list(locks), excludes=list(excludes))


def _mode_pool(players, params, mode, locks):
    pool = eligible_pool(players, params, mode)
    have = {p["key"] for p in pool}
    # A lock is an explicit human call: the locked player is eligible even if the pool rule would drop him.
    pool += [p for p in players if p["key"] in locks and p["key"] not in have and not is_out(p)]
    return pool


def build_all(players, params, n_gpp=10, n_cash=3, overrides=None, locks=(), excludes=()):
    overrides = overrides or {"GPP": {}, "CASH": {}}
    locks, excludes = set(locks), set(excludes)
    results, costs, used = {}, {}, {}
    for mode, n in (("GPP", n_gpp), ("CASH", n_cash)):
        pool = _mode_pool(players, params, mode, locks)
        s = _settings(params, mode, overrides, locks, excludes)
        used[mode] = {k: getattr(s, k) for k in OVERRIDE_KEYS}
        results[mode] = build_lineups(pool, s, n=n)
        if (locks or excludes) and results[mode]:
            free = _settings(params, mode, overrides, (), ())
            base = build_lineups(eligible_pool(players, params, mode), free, n=1)
            if base:
                top = results[mode][0]
                costs[mode] = {"proj": round(top["proj"] - base[0]["proj"], 2),
                               "objective": round(top["objective"] - base[0]["objective"], 2),
                               "unconstrained_proj": base[0]["proj"]}
    # Consensus: the lineup the GPP set agrees on (most-picked players), all rules still apply.
    gpp = results.get("GPP") or []
    if gpp:
        counts = Counter(x["key"] for lu in gpp for x in lu["players"])
        cplayers = [{**p, "exposure": counts.get(p["key"], 0) / len(gpp)}
                    for p in _mode_pool(players, params, "GPP", locks)]
        cs = _settings(params, "CONSENSUS", overrides, locks, excludes)
        results["CONSENSUS"] = build_lineups(cplayers, cs, n=1)
        for lu in results["CONSENSUS"]:
            for x in lu["players"]:
                x["exposure"] = round(counts.get(x["key"], 0) / len(gpp), 3)
    opt = Settings(mode="OPTIMAL", min_salary=0, max_from_team=8, min_uniques=1)
    results["OPTIMAL"] = build_lineups(eligible_pool(players, params, "GPP"), opt, n=1)
    return results, costs, used


# ---------------------------------------------------------------- persistence
POOL_COLS = ["draftable_id", "team", "opp", "game", "game_start", "pos", "salary", "minutes", "proj_rg",
             "floor_rg", "ceil_rg", "proj_adj", "floor", "ceil", "sim85", "own_pct", "own_estimated",
             "starter_rg", "starter_rw", "starter_consensus", "contingent", "inj_status",
             "questionable_mult", "vegas_total", "fav_ml", "b2b", "signal_mult", "signals", "value",
             "value_grade", *SIGNAL_COLUMNS]


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


def save_slate(cur, dk) -> int:
    slate_key = ",".join(sorted(g["game"] for g in dk["games"]))
    cur.execute("""INSERT INTO slates(date, site, slate_type, n_games, lock_time_utc, game_list, slate_key)
                   VALUES (%s,'DK','Classic',%s,%s,%s,%s)
                   ON CONFLICT (site, date, slate_key) DO UPDATE SET
                     lock_time_utc = EXCLUDED.lock_time_utc, game_list = EXCLUDED.game_list, updated_at = now()
                   RETURNING id""",
                (dk["date"], len(dk["games"]), dk["lock_time"],
                 json.dumps([{"game": g["game"], "start": g["start"].isoformat()} for g in dk["games"]]),
                 slate_key))
    return cur.fetchone()["id"]


def save_pool(cur, slate_id, players, reason, params_version) -> dict:
    key_to_pid, pids = {}, []
    cols = ", ".join(POOL_COLS)
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in POOL_COLS)
    for p in players:
        pid = _upsert_player(cur, p)
        key_to_pid[p["key"]] = pid
        pids.append(pid)
        vals = [p.get(c) for c in POOL_COLS]
        for c in ("own_estimated", "contingent"):
            vals[POOL_COLS.index(c)] = bool(p.get(c))
        cur.execute(f"""INSERT INTO pool(slate_id, player_id, {cols}, updated_at)
                        VALUES (%s, %s, {", ".join(["%s"] * len(POOL_COLS))}, now())
                        ON CONFLICT (slate_id, player_id) DO UPDATE SET {sets}, updated_at=now()""",
                    [slate_id, pid, *vals])
    # Anyone on the slate who no longer has a projection is flagged rather than left stale.
    cur.execute("""UPDATE pool SET proj_adj = 0, inj_status = 'NOPROJ', updated_at = now()
                   WHERE slate_id = %s AND NOT (player_id = ANY(%s))""", (slate_id, pids))
    snap = [{"player_id": key_to_pid[p["key"]], "name": p["name"],
             **{c: p.get(c) for c in POOL_COLS}} for p in players]
    body = json.dumps(snap, default=str, sort_keys=True)
    sha = hashlib.sha256(body.encode()).hexdigest()
    cur.execute("SELECT rows_sha FROM pool_snapshots WHERE slate_id=%s ORDER BY taken_at DESC LIMIT 1", (slate_id,))
    last = cur.fetchone()
    if not last or last["rows_sha"] != sha:   # unchanged pools (most 20-minute re-checks) are not stored again
        cur.execute("INSERT INTO pool_snapshots(slate_id, reason, params_version, rows, rows_sha) VALUES (%s,%s,%s,%s,%s)",
                    (slate_id, reason, params_version, body, sha))
    return key_to_pid


def save_uploads(cur, slate_id, files: dict):
    for source, (name, raw) in files.items():
        if not raw:
            continue
        cur.execute("""INSERT INTO source_snapshots(slate_id, source, filename, raw, sha256, payload)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (slate_id, source, name, raw, hashlib.sha256(raw).hexdigest(),
                     json.dumps({"bytes": len(raw)})))


def save_pages(cur, slate_id, lineup_src: dict | None):
    """Keep the fetched lineup pages (gzipped) so a slate can be re-derived later."""
    for key, html in ((lineup_src or {}).get("raw") or {}).items():
        raw = gzip.compress(html.encode("utf-8"))
        cur.execute("""INSERT INTO source_snapshots(slate_id, source, filename, raw, sha256, payload)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (slate_id, f"{key}_lineups_html", f"{key}.html.gz", raw, hashlib.sha256(raw).hexdigest(),
                     json.dumps({"bytes": len(html), "gzip": True})))


def save_lineups(cur, slate_id, lineups, costs, used, locks, excludes, params_version, key_to_pid,
                 contest=None) -> str:
    run_id = uuid.uuid4()
    for mode, lus in lineups.items():
        for rank, lu in enumerate(lus, 1):
            for x in lu["players"]:
                x["player_id"] = key_to_pid.get(x["key"])
            cur.execute("""INSERT INTO lineups(slate_id, mode, run_id, rank, params_version, salary, proj, ceil,
                                               own_sum, players, settings, locks, excludes, cost_vs_unconstrained,
                                               contest_context)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (slate_id, mode, run_id, rank, params_version, lu["salary"], lu["proj"], lu["ceil"],
                         lu["own_sum"], json.dumps(lu["players"], default=str),
                         json.dumps(used.get("GPP" if mode == "CONSENSUS" else mode)),
                         json.dumps(sorted(locks)), json.dumps(sorted(excludes)),
                         json.dumps(costs.get(mode)) if rank == 1 and costs.get(mode) else None,
                         json.dumps(contest) if contest else None))
    return str(run_id)


def pool_from_db(cur, slate_id) -> tuple[list[dict], dict]:
    cur.execute(f"""SELECT pl.player_id, p.name, p.name_norm, {", ".join("pl." + c for c in POOL_COLS)}
                    FROM pool pl JOIN players p ON p.id = pl.player_id WHERE pl.slate_id = %s""", (slate_id,))
    players, key_to_pid = [], {}
    for r in cur.fetchall():
        d = {k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, bool)) else v)
             for k, v in r.items()}
        d["key"] = d["draftable_id"]
        d["game_start"] = r["game_start"].isoformat() if r["game_start"] else None
        key_to_pid[d["key"]] = r["player_id"]
        players.append(d)
    return players, key_to_pid


# ---------------------------------------------------------------- actions
def _lineup_warning(rep):
    if rep is None:
        return "not fetched"
    down = [k for k, ok in rep["sources"].items() if not ok]
    if len(down) == 2:
        return "both lineup sources failed - consensus starter box is empty; check cheap plays by hand"
    msg = f'{rep["consensus_starters"]} consensus starters'
    if down:
        msg += f" (only one source: {', '.join(down)} unavailable - starters are single-source today)"
    return msg


def _summary(prep):
    m = prep["merge"]
    return {"date": str(prep["dk"]["date"]), "games": [g["game"] for g in prep["dk"]["games"]],
            "players": len(prep["players"]),
            "warnings": {"unmatched_dk": m["unmatched_dk"], "unmatched_rg": m["unmatched_rg"][:40],
                         "salary_mismatch": m["salary_mismatch"],
                         "own_estimated": sum(1 for p in prep["players"] if p.get("own_estimated")),
                         "fuzzy_matches": m.get("fuzzy_matches", []) + ((prep.get("lineups") or {}).get("fuzzy_matches") or []),
                         "unmatched_lineup_names": (prep.get("lineups") or {}).get("unmatched_names") or [],
                         "lineup_source_errors": (prep.get("lineups") or {}).get("errors") or {},
                         "starters_source": _lineup_warning(prep.get("lineups"))},
            "lineups": prep.get("lineups")}


def _name_lists(conn, players, lock_names, exclude_names, remember):
    """Typed lock/exclude names for this build, plus the standing lists saved earlier."""
    if remember:
        prefs.save(conn, lock_names, exclude_names)
    standing = prefs.load(conn)
    locks = prefs.split_names(lock_names) + [n for n in standing["lock"] if n not in prefs.split_names(lock_names)]
    excl = prefs.split_names(exclude_names) + [n for n in standing["exclude"] if n not in prefs.split_names(exclude_names)]
    out = prefs.resolve(players, locks, excl)
    typed = {n.lower() for n in prefs.split_names(lock_names) + prefs.split_names(exclude_names)}
    out["not_on_slate"] = [u["name"] for u in out["unmatched"] if u["name"].lower() not in typed and "why" not in u]
    out["unmatched"] = [u for u in out["unmatched"] if u["name"] not in out["not_on_slate"]]
    out["standing"] = standing
    return out


def run_build(dk_text, rg_text, n_gpp=10, n_cash=3, overrides=None, locks=(), excludes=(),
              dk_raw=None, rg_raw=None, dk_name=None, rg_name=None, fetch=None, contest=None,
              lock_names=None, exclude_names=None, remember=False) -> dict:
    overrides = clean_overrides(overrides)
    lineup_src = (fetch or lineup_sources.fetch_all)()
    with db.connect() as conn:
        db.migrate(conn)
        db.seed_params(conn)
        version, params = db.current_params(conn)
        prep = prepare(dk_text, rg_text, params, lineup_src)
        names = _name_lists(conn, prep["players"], lock_names, exclude_names, remember)
        locks, excludes = set(locks) | set(names["locks"]), set(excludes) | set(names["excludes"])
        lineups, costs, used = build_all(prep["players"], params, n_gpp, n_cash, overrides, locks, excludes)
        with conn.cursor() as cur:
            slate_id = save_slate(cur, prep["dk"])
            key_to_pid = save_pool(cur, slate_id, prep["players"], "build", version)
            save_uploads(cur, slate_id, {"dk_salaries_csv": (dk_name, dk_raw or dk_text.encode()),
                                         "rg_projections_csv": (rg_name, rg_raw or rg_text.encode())})
            save_pages(cur, slate_id, lineup_src)
            run_id = save_lineups(cur, slate_id, lineups, costs, used, locks, excludes, version, key_to_pid, contest)
        conn.commit()
    return {"slate_id": slate_id, "run_id": run_id, "params_version": version, **_summary(prep),
            "costs": costs, "names": names}


def run_rebuild(slate_id, n_gpp=10, n_cash=3, overrides=None, locks=(), excludes=(), contest=None,
                lock_names=None, exclude_names=None, remember=False) -> dict:
    overrides = clean_overrides(overrides)
    with db.connect() as conn:
        db.migrate(conn)
        version, params = db.current_params(conn)
        with conn.cursor() as cur:
            players, key_to_pid = pool_from_db(cur, slate_id)
            if not players:
                raise UploadError("that slate has no player pool")
            names = _name_lists(conn, players, lock_names, exclude_names, remember)
            locks, excludes = set(locks) | set(names["locks"]), set(excludes) | set(names["excludes"])
            lineups, costs, used = build_all(players, params, n_gpp, n_cash, overrides, locks, excludes)
            run_id = save_lineups(cur, slate_id, lineups, costs, used, locks, excludes, version, key_to_pid, contest)
        conn.commit()
    return {"slate_id": slate_id, "run_id": run_id, "params_version": version, "costs": costs, "names": names,
            "counts": {m: len(v) for m, v in lineups.items()}}


def _stored_upload(cur, source, slate_id):
    cur.execute("""SELECT raw FROM source_snapshots WHERE source=%s AND raw IS NOT NULL
                     AND (%s::bigint IS NULL OR slate_id = %s)
                   ORDER BY fetched_at DESC LIMIT 1""", (source, slate_id, slate_id))
    r = cur.fetchone()
    return bytes(r["raw"]).decode("utf-8-sig", errors="replace") if r else None


def run_refresh(rg_text=None, rg_raw=None, rg_name=None, dk_text=None, dk_raw=None, dk_name=None,
                slate_id=None, fetch=None, now=None, save_raw_pages=True) -> dict:
    """Late news. Re-reads both lineup pages for starters and injury tags; a fresh RotoGrinders export
    and/or DKSalaries.csv are optional (the slate's stored files are reused). The pool is updated in
    place, saved lineups are left alone, then the late-scratch check runs."""
    new_dk_raw = (dk_raw or dk_text.encode()) if dk_text else None
    new_rg_raw = (rg_raw or rg_text.encode()) if rg_text else None
    lineup_src = (fetch or lineup_sources.fetch_all)()
    with db.connect() as conn:
        db.migrate(conn)
        version, params = db.current_params(conn)
        with conn.cursor() as cur:
            dk_text = dk_text or _stored_upload(cur, "dk_salaries_csv", slate_id)
            rg_text = rg_text or _stored_upload(cur, "rg_projections_csv", slate_id)
            if not dk_text or not rg_text:
                raise UploadError("this slate has no stored uploads yet; build it first")
            prep = prepare(dk_text, rg_text, params, lineup_src)
            slate_id = save_slate(cur, prep["dk"])
            save_pool(cur, slate_id, prep["players"], "refresh", version)
            save_uploads(cur, slate_id, {"rg_projections_csv": (rg_name, new_rg_raw),
                                         "dk_salaries_csv": (dk_name, new_dk_raw)})
            if save_raw_pages:   # the scheduler skips this: history tables already record every change
                save_pages(cur, slate_id, lineup_src)
        conn.commit()
        result = check(conn, slate_id, now=now)
    return {"slate_id": slate_id, **_summary(prep), "check": result}


def _now():
    return datetime.now(timezone.utc)


def _started(game_start, now) -> bool:
    if not game_start:
        return False
    gs = game_start if isinstance(game_start, datetime) else datetime.fromisoformat(str(game_start))
    return gs <= now


def check(conn, slate_id, now=None) -> dict:
    """Flag players in saved lineups (latest run + anything marked entered) whose status is now
    Out/Doubtful/Questionable/no projection, with same-slot replacements that fit the salary left
    and whose game has not tipped."""
    now = now or _now()
    with conn.cursor() as cur:
        players, _ = pool_from_db(cur, slate_id)
        cur.execute("""SELECT id, mode, rank, salary, proj, players, entered FROM lineups
                       WHERE slate_id=%s AND (entered OR run_id = (
                         SELECT run_id FROM lineups WHERE slate_id=%s ORDER BY built_at DESC LIMIT 1))
                       ORDER BY entered DESC, mode, rank""", (slate_id, slate_id))
        lineups = cur.fetchall()
    by_key = {p["key"]: p for p in players}
    flags = []
    for lu in lineups:
        keys = {x["key"] for x in lu["players"]}
        for x in lu["players"]:
            cur_p = by_key.get(x["key"], {})
            status = (cur_p.get("inj_status") or "").upper()
            if status not in FLAG_STATUSES and not cur_p.get("contingent"):
                continue
            locked = _started(cur_p.get("game_start") or x.get("game_start"), now)
            room = SALARY_CAP - (lu["salary"] - x["salary"])
            subs = []
            if not locked:
                cands = [p for p in players
                         if p["key"] not in keys and x["slot"] in eligible_slots(p["pos"])
                         and p["salary"] <= room and not _started(p["game_start"], now)
                         and (p.get("inj_status") or "").upper() not in FLAG_STATUSES
                         and not p.get("contingent") and (p.get("proj_adj") or 0) > 0]
                cands.sort(key=lambda p: -(p["proj_adj"] or 0))
                subs = [{"key": p["key"], "name": p["name"], "team": p["team"], "pos": p["pos"],
                         "salary": p["salary"], "proj": p["proj_adj"],
                         "proj_delta": round((p["proj_adj"] or 0) - (cur_p.get("proj_adj") or x.get("proj") or 0), 2)}
                        for p in cands[:3]]
            flags.append({"lineup_id": lu["id"], "mode": lu["mode"], "rank": lu["rank"], "entered": lu["entered"],
                          "slot": x["slot"], "key": x["key"], "name": x["name"], "status": status or "contingent",
                          "locked": locked, "salary_room": room, "suggestions": subs})
    return {"checked_lineups": len(lineups), "flags": flags, "checked_at": now.isoformat()}


def _validate(lineup_players, by_key, now):
    if [x["slot"] for x in lineup_players] != SLOTS:
        raise UploadError("lineup must fill PG, SG, SF, PF, C, G, F, UTIL in order")
    if len({x["key"] for x in lineup_players}) != 8:
        raise UploadError("a player can only be used once")
    for x in lineup_players:
        p = by_key.get(x["key"])
        if not p:
            raise UploadError(f"{x.get('name')} is not in this slate's pool")
        if x["slot"] not in eligible_slots(p["pos"]):
            raise UploadError(f"{p['name']} ({p['pos']}) can't play {x['slot']}")
    sal = sum(by_key[x["key"]]["salary"] for x in lineup_players)
    if sal > SALARY_CAP:
        raise UploadError(f"salary ${sal:,} is over the $50,000 cap")
    if len({by_key[x["key"]]["game"] for x in lineup_players}) < 2:
        raise UploadError("players must come from at least 2 different games")
    return sal


def swap(conn, lineup_id, out_key, in_key, now=None) -> dict:
    now = now or _now()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM lineups WHERE id=%s", (lineup_id,))
        lu = cur.fetchone()
        if not lu:
            raise UploadError("lineup not found")
        players, _ = pool_from_db(cur, lu["slate_id"])
        by_key = {p["key"]: p for p in players}
        cur_players = lu["players"]
        idx = next((i for i, x in enumerate(cur_players) if x["key"] == out_key), None)
        if idx is None:
            raise UploadError("that player is not in this lineup")
        out_p, in_p = by_key.get(out_key, {}), by_key.get(in_key)
        if not in_p:
            raise UploadError("replacement is not in this slate's pool")
        if _started(out_p.get("game_start") or cur_players[idx].get("game_start"), now):
            raise UploadError(f"{cur_players[idx]['name']}'s game has started; he is locked on DK")
        if _started(in_p["game_start"], now):
            raise UploadError(f"{in_p['name']}'s game has started")
        new = [dict(x) for x in cur_players]
        new[idx] = {"slot": new[idx]["slot"], "key": in_p["key"], "name": in_p["name"], "pos": in_p["pos"],
                    "team": in_p["team"], "salary": in_p["salary"], "proj": in_p["proj_adj"],
                    "own": in_p["own_pct"], "game": in_p["game"], "game_start": in_p["game_start"],
                    "draftable_id": in_p["draftable_id"], "player_id": None}
        sal = _validate(new, by_key, now)
        proj = round(sum(by_key[x["key"]]["proj_adj"] or 0 for x in new), 2)
        ceil = round(sum(by_key[x["key"]]["ceil"] or 0 for x in new), 2)
        own = round(sum(by_key[x["key"]]["own_pct"] or 0 for x in new), 2)
        cur.execute("""UPDATE lineups SET players=%s, salary=%s, proj=%s, ceil=%s, own_sum=%s, edited=true,
                         players_original = COALESCE(players_original, players)
                       WHERE id=%s""", (json.dumps(new, default=str), sal, proj, ceil, own, lineup_id))
    conn.commit()
    return {"lineup_id": lineup_id, "salary": sal, "proj": proj, "proj_delta": round(proj - float(lu["proj"]), 2)}


def set_entered(conn, lineup_id, value: bool) -> dict:
    with conn.cursor() as cur:
        cur.execute("""UPDATE lineups SET entered=%s, entered_at = CASE WHEN %s THEN now() ELSE NULL END
                       WHERE id=%s RETURNING id, entered""", (value, value, lineup_id))
        r = cur.fetchone()
    conn.commit()
    if not r:
        raise UploadError("lineup not found")
    return r
