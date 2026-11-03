"""Box scores for grading, with the binding verification guard.

Primary:      ESPN scoreboard + per-game summary JSON (player minutes and stats).
Independent:  Basketball-Reference's daily scores page (final scores only), one request per night.

A game's box score is accepted only if ALL of these hold:
  1. ESPN marks the game final;
  2. each team's player minutes sum to 240 (+25 per overtime) within rounding tolerance
     (ESPN reports whole minutes, so a sum can drift by a minute or two; a frozen half-game
     snapshot is off by ~120 and fails);
  3. each team's summed player points equals ESPN's team score;
  4. each team's score equals Basketball-Reference's final score.
Anything else is rejected and retried on the next run."""

import re

from bs4 import BeautifulSoup

from ..http import get, get_json
from ..scoring import dk_points
from .lineups import team_code

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={ymd}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={event_id}"
BREF_DAY = "https://www.basketball-reference.com/boxscores/?month={m}&day={d}&year={y}"
MINUTES_TOLERANCE = 3   # whole-minute rounding across ~10-13 players per team

BREF_TEAMS = {
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA", "Chicago": "CHI",
    "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Golden State": "GSW",
    "Houston": "HOU", "Indiana": "IND", "LA Clippers": "LAC", "LA Lakers": "LAL", "Memphis": "MEM",
    "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN", "New Orleans": "NOP", "New York": "NYK",
    "Oklahoma City": "OKC", "Orlando": "ORL", "Philadelphia": "PHI", "Phoenix": "PHX", "Portland": "POR",
    "Sacramento": "SAC", "San Antonio": "SAS", "Toronto": "TOR", "Utah": "UTA", "Washington": "WAS",
}


def _int(v, default=0):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- ESPN
def parse_scoreboard(data: dict) -> list[dict]:
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        status = (comp.get("status") or ev.get("status") or {})
        teams = {}
        for c in comp.get("competitors", []):
            abbr = team_code((c.get("team") or {}).get("abbreviation"))
            teams[abbr] = {"score": _int(c.get("score"), None), "home": c.get("homeAway") == "home"}
        games.append({"event_id": ev.get("id"), "teams": teams,
                      "final": (status.get("type") or {}).get("name") == "STATUS_FINAL"
                               or bool((status.get("type") or {}).get("completed")),
                      "period": _int(status.get("period"), 4)})
    return games


def parse_summary(data: dict) -> dict:
    """{abbr: [{name, min, pts, fg3m, reb, ast, stl, blk, tov, dnp, starter}]}"""
    out = {}
    for team_block in (data.get("boxscore") or {}).get("players", []):
        abbr = team_code((team_block.get("team") or {}).get("abbreviation"))
        rows = []
        for stat in team_block.get("statistics", []):
            keys = [k.upper() for k in (stat.get("labels") or stat.get("names") or [])]
            idx = {k: i for i, k in enumerate(keys)}
            for a in stat.get("athletes", []):
                name = (a.get("athlete") or {}).get("displayName")
                vals = a.get("stats") or []
                dnp = bool(a.get("didNotPlay")) or not vals

                def val(*names):
                    for n in names:
                        if n in idx and idx[n] < len(vals):
                            return vals[idx[n]]
                    return None
                three = str(val("3PT", "3PM-A", "FG3") or "0")
                rows.append({
                    "name": name, "starter": bool(a.get("starter")), "dnp": dnp,
                    "min": 0 if dnp else _int(val("MIN")),
                    "pts": 0 if dnp else _int(val("PTS")),
                    "fg3m": 0 if dnp else _int(three.split("-")[0]),
                    "reb": 0 if dnp else _int(val("REB")),
                    "ast": 0 if dnp else _int(val("AST")),
                    "stl": 0 if dnp else _int(val("STL")),
                    "blk": 0 if dnp else _int(val("BLK")),
                    "tov": 0 if dnp else _int(val("TO", "TOV")),
                })
        out[abbr] = rows
    for rows in out.values():
        for r in rows:
            r["dk_pts"] = dk_points(r["pts"], r["fg3m"], r["reb"], r["ast"], r["stl"], r["blk"], r["tov"], r["dnp"])
    return out


# ---------------------------------------------------------------- Basketball-Reference (finals only)
def parse_bref_day(html: str) -> dict:
    """{frozenset({A, B}): {A: score, B: score}} for games marked Final."""
    soup = BeautifulSoup(html, "html.parser")
    finals = {}
    for g in soup.select("div.game_summary"):
        teams_table = g.select_one("table.teams")
        if not teams_table:
            continue
        scores = {}
        for tr in teams_table.select("tr"):
            a = tr.find("a", href=re.compile(r"^/teams/"))
            score_td = tr.find("td", class_="right")
            if not a or not score_td:
                continue
            m = re.match(r"^/teams/([A-Z]{3})/", a["href"])
            abbr = team_code(m.group(1)) if m else BREF_TEAMS.get(a.get_text(strip=True))
            score = _int(score_td.get_text(strip=True), None)
            if abbr and score is not None:
                scores[abbr] = score
        # "Final" is rendered as F<span>inal</span>, so join text without separators
        is_final = "final" in teams_table.get_text("", strip=True).lower()
        if len(scores) == 2 and is_final:
            finals[frozenset(scores)] = scores
    return finals


# ---------------------------------------------------------------- verification guard (binding)
def verify_game(game: dict, box: dict, bref_finals: dict) -> list[str]:
    """Returns a list of problems; empty means the box score is accepted."""
    problems = []
    if not game.get("final"):
        problems.append("ESPN does not mark the game final")
    period = max(4, game.get("period") or 4)
    expected = 240 + 25 * (period - 4)
    teams = list(game["teams"])
    indep = bref_finals.get(frozenset(teams))
    if indep is None:
        problems.append("no independent final score (Basketball-Reference) yet")
    for t in teams:
        rows = box.get(t)
        if not rows:
            problems.append(f"{t}: no player box score")
            continue
        mins = sum(r["min"] for r in rows)
        if abs(mins - expected) > MINUTES_TOLERANCE:
            problems.append(f"{t}: player minutes sum to {mins}, expected {expected}")
        pts = sum(r["pts"] for r in rows)
        espn_score = game["teams"][t]["score"]
        if pts != espn_score:
            problems.append(f"{t}: player points sum to {pts} but ESPN final is {espn_score}")
        if indep is not None and indep.get(t) != espn_score:
            problems.append(f"{t}: ESPN final {espn_score} disagrees with Basketball-Reference {indep.get(t)}")
    return problems


def fetch_day(game_date) -> dict:
    """Fetch everything for one ET date. Network errors propagate to the caller (the job records them)."""
    ymd = game_date.strftime("%Y%m%d")
    games = parse_scoreboard(get_json(SCOREBOARD.format(ymd=ymd)))
    bref = parse_bref_day(get(BREF_DAY.format(m=game_date.month, d=game_date.day, y=game_date.year)).decode("utf-8", "replace"))
    boxes = {}
    for g in games:
        if g["final"]:
            boxes[g["event_id"]] = parse_summary(get_json(SUMMARY.format(event_id=g["event_id"])))
    return {"games": games, "bref": bref, "boxes": boxes}


