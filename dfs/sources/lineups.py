"""Starting lineups, injury tags and Vegas lines from two public pages, cross-checked:

  RotoWire     https://www.rotowire.com/basketball/nba-lineups.php
  RotoGrinders https://rotogrinders.com/lineups/nba?site=draftkings   (public fields only)

The RotoGrinders page also carries subscriber-only projection/ownership numbers hidden from
non-subscribers; those are deliberately NOT read here."""
import re

from bs4 import BeautifulSoup

from ..http import get
from ..names import NameIndex, match_key, name_norm

ROTOWIRE_URL = "https://www.rotowire.com/basketball/nba-lineups.php"
RG_LINEUPS_URL = "https://rotogrinders.com/lineups/nba?site=draftkings"

# Every source spells a few teams differently; map all of them to one code.
_TEAM_ALIASES = {
    "GS": "GSW", "GSW": "GSW", "NO": "NOP", "NOP": "NOP", "NOR": "NOP", "NY": "NYK", "NYK": "NYK",
    "SA": "SAS", "SAS": "SAS", "PHO": "PHX", "PHX": "PHX", "UTA": "UTA", "UTAH": "UTA", "WAS": "WAS",
    "WSH": "WAS", "BKN": "BKN", "BRK": "BKN", "CHA": "CHA", "CHO": "CHA", "PHL": "PHI",
}


def team_code(abbr: str | None) -> str | None:
    if not abbr:
        return None
    a = abbr.strip().upper()
    return _TEAM_ALIASES.get(a, a)


_INJ = {"out": "O", "ofs": "O", "o": "O", "doubtful": "D", "doub": "D", "d": "D",
        "questionable": "Q", "ques": "Q", "q": "Q", "gtd": "GTD", "game time decision": "GTD",
        "probable": "P", "prob": "P", "p": "P"}


def norm_tag(tag: str | None) -> str:
    t = (tag or "").strip().lower().rstrip(".")
    return _INJ.get(t, t.upper() if t else "")


def _num(s):
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else None


# ---------------------------------------------------------------- RotoWire
def parse_rotowire(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for box in soup.select("div.lineup__box"):
        abbrs = [team_code(a.get_text(strip=True)) for a in box.select(".lineup__teams .lineup__abbr")]
        lists = {"visit": box.select_one("ul.lineup__list.is-visit"), "home": box.select_one("ul.lineup__list.is-home")}
        if len(abbrs) != 2 or not all(lists.values()):
            continue  # promo boxes etc.
        teams = {}
        for side, abbr in zip(("visit", "home"), abbrs):
            ul = lists[side]
            status_li = ul.select_one("li.lineup__status")
            cls = " ".join(status_li.get("class", [])) if status_li else ""
            status = "confirmed" if "is-confirmed" in cls else "expected" if status_li else "unknown"
            starters, may_not = [], []
            bucket = starters
            for li in ul.find_all("li", recursive=False):
                if "lineup__title" in li.get("class", []):
                    bucket = may_not
                    continue
                if "lineup__player" not in li.get("class", []):
                    continue
                a = li.find("a")
                if not a:
                    continue
                name = a.get("title") or a.get_text(strip=True)
                pct = next((int(c.split("-")[-1]) for c in li.get("class", []) if c.startswith("is-pct-play-")), None)
                inj = li.select_one(".lineup__inj")
                pos = li.select_one(".lineup__pos")
                bucket.append({"name": name, "name_norm": name_norm(name), "pos": pos.get_text(strip=True) if pos else None,
                               "pct_play": pct, "inj": norm_tag(inj.get_text(strip=True) if inj else "")})
            teams[abbr] = {"status": status, "starters": starters, "may_not_play": may_not}
        ml = total = spread = None
        for item in box.select(".lineup__odds-item"):
            label = (item.find("b").get_text(" ", strip=True) if item.find("b") else "").upper()
            span = item.select_one("span.composite") or item.find("span")
            text = span.get_text(" ", strip=True) if span else ""
            if label == "LINE":
                m = re.match(r"([A-Z]{2,4})\s+([-+]\d+)", text)
                if m:
                    ml = {"team": team_code(m.group(1)), "ml": int(m.group(2))}
            elif label == "SPREAD":
                m = re.match(r"([A-Z]{2,4})\s+([-+]?\d+(?:\.\d+)?)", text)
                if m:
                    spread = {"team": team_code(m.group(1)), "spread": float(m.group(2))}
            elif "O/U" in label:
                total = _num(text)
        games.append({"away": abbrs[0], "home": abbrs[1], "teams": teams, "fav_ml": ml, "total": total,
                      "spread": spread})
    return games


# ---------------------------------------------------------------- RotoGrinders (public fields)
def parse_rg_lineups(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    games = []
    for card in soup.select("div.game-card"):
        abbrs = [team_code(t.get("data-abbr")) for t in card.select(".game-card-teams .team-nameplate-title")]
        if len(abbrs) != 2:
            continue
        mls, total = [], None
        bar = card.select_one(".vegas-bar")
        if bar:
            tot = bar.select_one(".vegas-bar-total-points")
            total = _num(tot.get_text()) if tot else None
            for side in bar.find_all("div", recursive=False):
                if "vegas-bar-total" in side.get("class", []):
                    continue
                sp = side.find("span")
                mls.append(int(_num(sp.get_text())) if sp and _num(sp.get_text()) is not None else None)
        teams = {}
        for abbr, lc in zip(abbrs, card.select(".game-card-lineups > .lineup-card")):
            body = lc.select_one(".lineup-card-body")
            confirmed = bool(body) and "unconfirmed" not in body.get("class", [])
            starters, bench, bucket = [], [], None
            players_div = lc.select_one(".lineup-card-players")
            for el in (players_div.children if players_div else []):
                if getattr(el, "name", None) == "span":
                    txt = el.get_text(strip=True).lower()
                    bucket = starters if txt.startswith("starter") else bench if txt.startswith("bench") else bucket
                elif getattr(el, "name", None) == "ul" and bucket is not None:
                    for np_ in el.select(".player-nameplate"):
                        a = np_.select_one(".player-nameplate-name")
                        if not a:
                            continue
                        name = a.get_text(strip=True)
                        bucket.append({"name": name, "name_norm": name_norm(name), "pos": np_.get("data-position"),
                                       "salary": int(np_.get("data-salary") or 0) or None,
                                       "disabled": "disabled" in np_.get("class", [])})
            teams[abbr] = {"confirmed": confirmed, "starters": starters, "bench": bench}
        fav = None
        if len(mls) == 2 and None not in mls:
            i = 0 if mls[0] < mls[1] else 1
            fav = {"team": abbrs[i], "ml": mls[i]}
        games.append({"away": abbrs[0], "home": abbrs[1], "teams": teams, "fav_ml": fav, "total": total})
    return games


def fetch_all() -> dict:
    """Fetch both pages; a failure in one source is recorded, never raised."""
    out = {"rotowire": None, "rg": None, "errors": {}, "raw": {}}
    for key, url, parser in (("rotowire", ROTOWIRE_URL, parse_rotowire), ("rg", RG_LINEUPS_URL, parse_rg_lineups)):
        try:
            html = get(url, timeout=20).decode("utf-8", "replace")
            out["raw"][key] = html
            out[key] = parser(html)
            if not out[key]:
                out["errors"][key] = "page loaded but no games were found (layout may have changed)"
        except Exception as e:  # noqa: BLE001 - recorded and surfaced in the UI
            out["errors"][key] = f"{type(e).__name__}: {e}"[:200]
    return out


# ---------------------------------------------------------------- cross-check onto the pool
def _pos_groups(pos: str | None) -> set:
    """'SF/PF' -> {'F'}; 'G' -> {'G'}; 'PF/C' -> {'F', 'C'}."""
    groups = set()
    for x in (pos or "").upper().replace(" ", "").split("/"):
        if x in ("PG", "SG", "G"):
            groups.add("G")
        elif x in ("SF", "PF", "F"):
            groups.add("F")
        elif x == "C":
            groups.add("C")
    return groups


SEVERITY = {"": 0, "P": 0, "Q": 1, "GTD": 1, "D": 2, "O": 3, "OUT": 3}


def _worse(a: str, b: str) -> str:
    return a if SEVERITY.get(a, 0) >= SEVERITY.get(b, 0) else b


def apply_lineups(players: list[dict], src: dict, cheap_cutoff: int = 5500) -> dict:
    """Sets starter_rw, starter_rg, starter_consensus, contingent, inj_status (most severe of the
    upload and RotoWire tags), vegas_total and fav_ml on each player. Returns a report."""
    rw_games, rg_games = src.get("rotowire") or [], src.get("rg") or []
    rw_team = {abbr: t for g in rw_games for abbr, t in g["teams"].items()}
    rg_team = {abbr: t for g in rg_games for abbr, t in g["teams"].items()}

    def game_key(a, b):
        return frozenset((team_code(a), team_code(b)))

    vegas = {}
    for g in rw_games:  # RotoWire first, RotoGrinders overrides (spec: RG lineups page is the Vegas source)
        vegas[game_key(g["away"], g["home"])] = {"total": g["total"], "fav_ml": g["fav_ml"]}
    for g in rg_games:
        k = game_key(g["away"], g["home"])
        cur = vegas.get(k, {})
        vegas[k] = {"total": g["total"] or cur.get("total"), "fav_ml": g["fav_ml"] or cur.get("fav_ml")}

    have_rw, have_rg = bool(rw_games), bool(rg_games)
    report = {"sources": {"rotowire": have_rw, "rotogrinders": have_rg}, "disagreements": [],
              "unmatched_teams": [], "unmatched_names": [], "fuzzy_matches": []}
    for p in players:
        p["team_code"] = team_code(p.get("team"))
    index = NameIndex(players)
    slate_teams = {p["team_code"] for p in players}

    def resolve(source, team, entries):
        """Map a source's player list for one team onto pool keys; report every miss loudly."""
        keys, tags = set(), {}
        for e in entries:
            hit, how = index.find(e["name"], team)
            if hit is None:
                report["unmatched_names"].append({"source": source, "team": team, "name": e["name"]})
                continue
            if how != "exact":
                report["fuzzy_matches"].append({"source": source, "team": team, "name": e["name"],
                                                "matched": hit["name"]})
            keys.add(id(hit))
            if e.get("inj"):
                tags[id(hit)] = e["inj"]
        return keys, tags

    rw_start, rw_tags, rg_start = {}, {}, {}
    for team in slate_teams:
        if team in rw_team:
            k, t = resolve("rotowire", team, rw_team[team]["starters"])
            _, t2 = resolve("rotowire", team, rw_team[team]["may_not_play"])
            rw_start[team], rw_tags[team] = k, {**t2, **t}
        elif have_rw:
            report["unmatched_teams"].append(team)
        if team in rg_team:
            rg_start[team], _ = resolve("rotogrinders", team, rg_team[team]["starters"])
        elif have_rg:
            report["unmatched_teams"].append(team)

    for p in players:
        team, opp = p["team_code"], team_code(p.get("opp"))
        rw = rw_team.get(team)
        p["starter_rw"] = (id(p) in rw_start[team]) if team in rw_start else None
        p["starter_rg"] = (id(p) in rg_start[team]) if team in rg_start else None
        known = [x for x in (p["starter_rw"], p["starter_rg"]) if x is not None]
        p["starter_consensus"] = bool(known) and all(known)
        rw_tag = rw_tags.get(team, {}).get(id(p), "")
        p["inj_status"] = _worse((p.get("inj_status") or "").upper(), rw_tag)
        disputed = len(known) == 2 and known[0] != known[1]
        unresolved = False
        if p["starter_consensus"] and rw and rw["status"] != "confirmed":
            # his start plausibly depends on a teammate RotoWire lists as may-not-play with an unresolved
            # tag at an overlapping position (not merely any questionable teammate)
            mine = _pos_groups(p.get("pos"))
            unresolved = any(e["inj"] in ("Q", "GTD", "D") and _pos_groups(e.get("pos")) & mine
                             and match_key(e["name"]) != match_key(p["name"])
                             for e in rw["may_not_play"])
        p["contingent"] = p["salary"] < cheap_cutoff and (disputed or unresolved)
        if disputed and p["salary"] < cheap_cutoff:
            report["disagreements"].append({"name": p["name"], "team": team, "rotowire_starter": p["starter_rw"],
                                            "rotogrinders_starter": p["starter_rg"]})
        v = vegas.get(game_key(team, opp))
        if v:
            p["vegas_total"] = v["total"]
            p["fav_ml"] = v["fav_ml"]["ml"] if v["fav_ml"] else None
    report["unmatched_teams"] = sorted(set(t for t in report["unmatched_teams"] if t))
    report["consensus_starters"] = sum(1 for p in players if p.get("starter_consensus"))
    report["contingent"] = [p["name"] for p in players if p.get("contingent")]
    return report
