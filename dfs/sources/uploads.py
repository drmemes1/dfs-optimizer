"""Parsers for the two daily uploads: DraftKings DKSalaries.csv and the RotoGrinders export."""
import csv
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..names import NameIndex, name_norm

ET = ZoneInfo("America/New_York")


class UploadError(ValueError):
    pass


def _rows(text: str) -> list[dict]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UploadError("file is empty")
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]
    return [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]


def _num(v, default=None):
    if v is None:
        return default
    s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "-", "—"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


# ---------------- DraftKings DKSalaries.csv ----------------
# Header: Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame
_GAME_INFO = re.compile(r"^(?P<away>[A-Z0-9]{2,4})@(?P<home>[A-Z0-9]{2,4})\s+(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<time>\d{1,2}:\d{2}[AP]M)")


def parse_dk_salaries(text: str) -> dict:
    rows = _rows(text)
    need = {"Position", "Name", "ID", "Salary", "Game Info", "TeamAbbrev"}
    missing = need - set(rows[0].keys() if rows else [])
    if missing:
        raise UploadError(f"DKSalaries.csv is missing columns: {', '.join(sorted(missing))}")
    players, games = {}, {}
    for r in rows:
        did = int(r["ID"])
        if did in players:
            continue
        m = _GAME_INFO.match(r["Game Info"])
        if not m:
            raise UploadError(f'Could not read Game Info "{r["Game Info"]}" for {r["Name"]}; '
                              "every player needs a game for the 2+ games rule")
        game = f'{m["away"]}@{m["home"]}'
        start = datetime.strptime(f'{m["date"]} {m["time"]}', "%m/%d/%Y %I:%M%p").replace(tzinfo=ET)
        opp = m["home"] if r["TeamAbbrev"] == m["away"] else m["away"]
        games.setdefault(game, start)
        players[did] = {
            "draftable_id": did, "name": r["Name"], "name_norm": name_norm(r["Name"]),
            "pos": r["Position"], "salary": int(_num(r["Salary"], 0)),
            "team": r["TeamAbbrev"], "opp": opp, "game": game,
            "dk_avg": _num(r.get("AvgPointsPerGame")),
        }
    if not players:
        raise UploadError("DKSalaries.csv has no players")
    lock = min(games.values()) if games else None
    return {"players": list(players.values()),
            "games": [{"game": g, "start": s} for g, s in sorted(games.items(), key=lambda kv: kv[1])],
            "lock_time": lock,
            "date": lock.date() if lock else None}


# ---------------- RotoGrinders projections export ----------------
# Columns are located by header name, never by position. The projected-stats page carries a
# PrizePicks-scoring column next to FPTS; we refuse anything that isn't unambiguously DK FPTS.
_ALIASES = {
    "name": ["name", "player", "player name"],
    "team": ["team", "tm"],
    "pos": ["pos", "position"],
    "salary": ["salary", "sal"],
    "fpts": ["fpts", "proj fpts", "projected fpts", "dk fpts"],
    "floor": ["floor"],
    "ceil": ["ceil", "ceiling"],
    "sim85": ["sim85", "sim 85", "85th"],
    "minutes": ["minutes", "min", "mins", "proj min", "proj minutes", "pmin"],
    "own": ["pown", "proj_own", "proj own", "own", "ownership", "pown%"],
    "inj": ["inj", "injury", "status", "injury status"],
    "opp": ["opp", "opponent"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9% ]", " ", h.lower()).replace("  ", " ").strip().replace("_", " ")


def map_rg_headers(headers: list[str]) -> dict:
    normed = {_norm_header(h).replace(" ", ""): h for h in headers}
    found = {}
    for field, aliases in _ALIASES.items():
        hits = [normed[a.replace(" ", "").replace("_", "")] for a in aliases
                if a.replace(" ", "").replace("_", "") in normed]
        if hits:
            found[field] = hits[0]
    bad = [h for h in headers if "prize" in h.lower() or h.lower().startswith("pp")]
    if "fpts" not in found:
        raise UploadError("RotoGrinders file has no FPTS column"
                          + (f" (found {bad}, which is PrizePicks scoring, not DK)" if bad else ""))
    for f in ("name", "salary"):
        if f not in found:
            raise UploadError(f"RotoGrinders file has no {f} column")
    return found


def parse_rg_projections(text: str) -> list[dict]:
    from . import rg_table
    if rg_table.is_rg_table(text):          # captured by the bookmarklet from the projections page
        return rg_table.parse(text)["rows"]
    rows = _rows(text)
    if not rows:
        raise UploadError("RotoGrinders file has no rows")
    cols = map_rg_headers(list(rows[0].keys()))
    out = []
    for r in rows:
        name = r.get(cols["name"], "")
        if not name:
            continue
        inj = r.get(cols["inj"], "") if "inj" in cols else ""
        out.append({
            "name": name, "name_norm": name_norm(name),
            "team": (r.get(cols.get("team", ""), "") or "").upper() or None,
            "salary_rg": _num(r.get(cols["salary"])),
            "proj_rg": _num(r.get(cols["fpts"]), 0.0),
            "floor_rg": _num(r.get(cols.get("floor", ""))),
            "ceil_rg": _num(r.get(cols.get("ceil", ""))),
            "sim85": _num(r.get(cols.get("sim85", ""))),
            "minutes": _num(r.get(cols.get("minutes", ""))),
            "own_pct": _num(r.get(cols.get("own", "")), 0.0),
            "rg_inj": "" if inj.strip() in ("", "-") else inj.strip().upper(),  # "-" means no flag
        })
    return out


def merge(dk: dict, rg: list[dict]) -> dict:
    """Attach RG projections to DK players (DK is the pool; never the reverse). Exact normalized/alias
    name first, then a reported same-team last-name + initial fallback. DK salary/position win;
    RG salary mismatches are reported, not used."""
    index = NameIndex(dk["players"])
    by_player, unmatched_rg, fuzzy = {}, [], []
    for r in rg:
        hit, how = index.find(r["name"], r.get("team"))
        if hit is None:
            if r["proj_rg"]:
                unmatched_rg.append(r["name"])
            continue
        if how != "exact":
            fuzzy.append({"name": r["name"], "matched": hit["name"]})
        by_player.setdefault(id(hit), r)
    merged, unmatched_dk, salary_mismatch = [], [], []
    for p in dk["players"]:
        r = by_player.get(id(p))
        if r is None:
            unmatched_dk.append(p["name"])
            continue
        if r["salary_rg"] and int(r["salary_rg"]) != p["salary"]:
            salary_mismatch.append({"name": p["name"], "dk": p["salary"], "rg": int(r["salary_rg"])})
        floor = r["floor_rg"] if r["floor_rg"] is not None else r["proj_rg"]
        ceil = r["ceil_rg"] if r["ceil_rg"] is not None else r["proj_rg"]
        merged.append({**p, "proj_rg": r["proj_rg"], "floor_rg": floor, "ceil_rg": ceil,
                       "sim85": r["sim85"], "minutes": r["minutes"], "own_pct": r["own_pct"],
                       "inj_status": r["rg_inj"]})
    return {"players": merged, "unmatched_dk": unmatched_dk, "unmatched_rg": unmatched_rg,
            "salary_mismatch": salary_mismatch, "fuzzy_matches": fuzzy}
