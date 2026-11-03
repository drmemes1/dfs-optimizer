"""Record lineup-page state over time: starters, injury tags, team lineup status, Vegas lines.
A row is written only when the observed value differs from the last row for the same key, so the
tables hold the sequence of changes (first observation through lock) without duplicates."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .names import match_key

ET = ZoneInfo("America/New_York")


def et_date(now: datetime):
    return now.astimezone(ET).date()


def _player_rows(src: dict):
    for g in src.get("rotowire") or []:
        for team, t in g["teams"].items():
            for is_starter, entries in ((True, t["starters"]), (False, t["may_not_play"])):
                for e in entries:
                    yield ("rotowire", team, e["name"], is_starter, e.get("inj") or "", e.get("pct_play"), t["status"])
    for g in src.get("rg") or []:
        for team, t in g["teams"].items():
            status = "confirmed" if t["confirmed"] else "unconfirmed"
            for is_starter, entries in ((True, t["starters"]), (False, t["bench"])):
                for e in entries:
                    yield ("rotogrinders", team, e["name"], is_starter, "", None, status)


def _vegas_rows(src: dict):
    for source, games in (("rotowire", src.get("rotowire") or []), ("rotogrinders", src.get("rg") or [])):
        for g in games:
            fav, spr = g.get("fav_ml") or {}, g.get("spread") or {}
            yield (source, g["away"], g["home"], g.get("total"), fav.get("team"), fav.get("ml"),
                   spr.get("team"), spr.get("spread"))


def record(conn, src: dict, now: datetime) -> dict:
    """Insert changed rows. Returns counts of new rows."""
    gd = et_date(now)
    new_players = new_vegas = 0
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (source, team, name_key) source, team, name_key, is_starter, inj,
                              pct_play, team_status
                       FROM player_status_history WHERE game_date = %s
                       ORDER BY source, team, name_key, observed_at DESC""", (gd,))
        last = {(r["source"], r["team"], r["name_key"]): (r["is_starter"], r["inj"], r["pct_play"], r["team_status"])
                for r in cur.fetchall()}
        for source, team, name, is_starter, inj, pct, status in _player_rows(src):
            key = (source, team, match_key(name))
            val = (is_starter, inj, pct, status)
            if last.get(key) == val:
                continue
            last[key] = val
            cur.execute("""INSERT INTO player_status_history(observed_at, game_date, source, team, name, name_key,
                                                             is_starter, inj, pct_play, team_status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (now, gd, source, team, name, key[2], is_starter, inj, pct, status))
            new_players += 1
        cur.execute("""SELECT DISTINCT ON (source, away, home) source, away, home, total, fav_team, fav_ml,
                              spread_team, spread
                       FROM vegas_history WHERE game_date = %s
                       ORDER BY source, away, home, observed_at DESC""", (gd,))
        lastv = {(r["source"], r["away"], r["home"]): (_f(r["total"]), r["fav_team"], r["fav_ml"], r["spread_team"],
                                                       _f(r["spread"])) for r in cur.fetchall()}
        for source, away, home, total, fav_team, fav_ml, spread_team, spread in _vegas_rows(src):
            val = (_f(total), fav_team, fav_ml, spread_team, _f(spread))
            if lastv.get((source, away, home)) == val:
                continue
            lastv[(source, away, home)] = val
            cur.execute("""INSERT INTO vegas_history(observed_at, game_date, source, away, home, total, fav_team,
                                                     fav_ml, spread_team, spread)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (now, gd, source, away, home, total, fav_team, fav_ml, spread_team, spread))
            new_vegas += 1
    conn.commit()
    return {"player_changes": new_players, "vegas_changes": new_vegas, "game_date": str(gd)}


def _f(v):
    return None if v is None else round(float(v), 2)
