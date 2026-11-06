"""DraftKings: slate identification and draftables (authoritative salaries, positions, status)."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..http import get_json

ET = ZoneInfo("America/New_York")
MAIN_WINDOW_ET = (19 * 60, 20 * 60)   # 7:00pm-8:00pm ET inclusive, in minutes past midnight;
                                      # 7:00, 7:30 and 8:00 starts are all normal main draws

LOBBY_URL = "https://www.draftkings.com/lobby/getcontests?sport=NBA"
DRAFTABLES_URL = "https://api.draftkings.com/draftgroups/v1/draftgroups/{dg}/draftables?format=json"
CLASSIC_GAME_TYPE_ID = 70


def _ms(v):
    """DK dates come as '/Date(1730000000000)/' or ISO strings."""
    if v is None:
        return None
    m = re.search(r"(\d{12,})", str(v))
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def classic_groups(lobby: dict) -> list[dict]:
    """Classic (gameTypeId 70) draft groups with the guaranteed prize money they carry."""
    groups = {g["DraftGroupId"]: g for g in lobby.get("DraftGroups", [])
              if g.get("GameTypeId") == CLASSIC_GAME_TYPE_ID}
    prize = {dg: 0.0 for dg in groups}
    n_contests = {dg: 0 for dg in groups}
    for c in lobby.get("Contests", []):
        dg = c.get("dg")
        if dg in groups:
            n_contests[dg] += 1
            prize[dg] += float(c.get("po") or 0)  # "po" = total payout of the contest
    out = []
    for dg, g in groups.items():
        out.append({
            "draft_group_id": dg,
            "start": _ms(g.get("StartDate") or g.get("StartDateEst")),
            "suffix": g.get("ContestStartTimeSuffix"),
            "tag": g.get("DraftGroupTag"),
            "game_count": g.get("GameCount"),
            "prize_pool": prize[dg],
            "n_contests": n_contests[dg],
        })
    return sorted(out, key=lambda r: -r["prize_pool"])


def _et(start):
    """StartDateEst carries no offset, so a naive value is Eastern by definition. Pinning it
    explicitly matters: Vercel runs in UTC, so astimezone() on a naive datetime would otherwise
    read 7:00pm as 7:00pm UTC (2:00pm ET) and the window check would move with the server."""
    if start is None:
        return None
    return (start.replace(tzinfo=ET) if start.tzinfo is None else start).astimezone(ET)


def pick_main_slate(lobby: dict, date=None, override_group_id: int | None = None) -> dict | None:
    """The main draw for a given ET date: the Classic group with the most games starting between
    7:00pm and 8:00pm ET, prize pool breaking ties only.

    Prize pool alone is not safe. On 2026-11-05 the richest Classic group was a 7-game 7:00pm
    slate at $1,033,857 and the runner-up a 2-game 9:00pm slate at $1,029,974 - 0.38% apart. The
    lobby also carries several days at once, so an unfiltered pick can return tomorrow's slate.
    Returns None rather than guessing when nothing qualifies: a dark or late-only night is normal."""
    groups = classic_groups(lobby)
    if override_group_id is not None:
        return next((g for g in groups if g["draft_group_id"] == override_group_id), None)
    target = date or datetime.now(ET).date()
    lo, hi = MAIN_WINDOW_ET
    eligible = []
    for g in groups:
        local = _et(g.get("start"))
        if local is None or local.date() != target:
            continue
        # minutes, not hours: `lo <= hour <= 20` would admit an 8:30pm three-game late slate as
        # the main draw, which contradicts the docstring and the user-facing message
        if lo <= local.hour * 60 + local.minute <= hi:
            eligible.append(g)
    if not eligible:
        return None
    return max(eligible, key=lambda g: ((g.get("game_count") or 0), g["prize_pool"]))


def parse_draftables(data: dict) -> dict:
    """Dedupe draftables by playerId (each player appears once per roster slot) and
    return the confirmed game list and lock time straight from DK."""
    comps = {}
    for c in data.get("competitions", []):
        away = (c.get("awayTeam") or {}).get("abbreviation")
        home = (c.get("homeTeam") or {}).get("abbreviation")
        comps[c["competitionId"]] = {
            "competition_id": c["competitionId"], "away": away, "home": home,
            "game": f"{away}@{home}", "start": _ms(c.get("startTime")),
        }
    players = {}
    for d in data.get("draftables", []):
        pid = d.get("playerId")
        if pid is None or pid in players:
            continue
        comp = d.get("competition") or {}
        cinfo = comps.get(comp.get("competitionId"), {})
        team = d.get("teamAbbreviation")
        opp = cinfo.get("home") if team == cinfo.get("away") else cinfo.get("away")
        status = (d.get("status") or "").strip()
        players[pid] = {
            "dk_player_id": pid,
            "draftable_id": d.get("draftableId"),
            "name": d.get("displayName") or f'{d.get("firstName", "")} {d.get("lastName", "")}'.strip(),
            "pos": d.get("position"),
            "salary": int(d.get("salary") or 0),
            "team": team, "opp": opp, "game": cinfo.get("game"),
            "inj_status": "" if status.lower() in ("", "none") else status.upper(),
            "disabled": bool(d.get("isDisabled")),
        }
    games = sorted(comps.values(), key=lambda g: (g["start"] or datetime.max.replace(tzinfo=timezone.utc)))
    lock = min((g["start"] for g in games if g["start"]), default=None)
    return {"players": list(players.values()), "games": games, "lock_time_utc": lock}


def fetch_lobby() -> dict:
    return get_json(LOBBY_URL)


def fetch_draftables(dg: int) -> dict:
    return get_json(DRAFTABLES_URL.format(dg=dg))
