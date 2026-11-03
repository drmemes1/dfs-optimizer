"""Lock / exclude players by name, and standing preferences that persist across slates."""
import json
import re

from .names import NameIndex, match_key
from .signals import is_out

PREF_KEY = "name_lists"   # {"lock": [names], "exclude": [names]}


def split_names(text) -> list[str]:
    """'Jalen Brunson, Lu Dort; Shai' -> ['Jalen Brunson', 'Lu Dort', 'Shai']. Accepts a list too."""
    if isinstance(text, list):
        items = text
    else:
        items = re.split(r"[,;\n]+", text or "")
    seen, out = set(), []
    for n in (i.strip() for i in items):
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def resolve(players: list[dict], lock_names, exclude_names) -> dict:
    """Map typed names onto pool keys. Every name that doesn't resolve, resolves only by the
    last-name fallback, or can't be locked (Out) is reported."""
    index = NameIndex(players)
    out = {"locks": [], "excludes": [], "unmatched": [], "fuzzy": [], "cant_lock": [], "conflicts": []}
    for kind, names in (("locks", lock_names), ("excludes", exclude_names)):
        for name in split_names(names):
            m = re.match(r"^(.*?)\s*\(([A-Za-z]{2,4})\)$", name)
            bare, team = (m.group(1), m.group(2).upper()) if m else (name, None)
            same = [p for p in index.by_key.get(match_key(bare), [])
                    if team is None or (p.get("team") or "").upper() == team]
            if len(same) > 1:
                out["unmatched"].append({"name": name, "list": "lock" if kind == "locks" else "exclude",
                                         "why": "ambiguous: " + ", ".join(f'{p["name"]} ({p["team"]})' for p in same)
                                                + " - add the team, e.g. Name (OKC)"})
                continue
            hit, how = (same[0], "exact") if same else index.find(bare, team)
            if hit is None:
                out["unmatched"].append({"name": name, "list": "lock" if kind == "locks" else "exclude"})
                continue
            if how != "exact":
                out["fuzzy"].append({"name": name, "matched": hit["name"]})
            if kind == "locks" and is_out(hit):
                out["cant_lock"].append({"name": hit["name"], "status": hit.get("inj_status")})
                continue
            out[kind].append(hit["key"])
    both = set(out["locks"]) & set(out["excludes"])
    if both:
        names = {p["key"]: p["name"] for p in players}
        out["conflicts"] = [names[k] for k in both]
        out["locks"] = [k for k in out["locks"] if k not in both]   # exclude wins on a conflict
    return out


def load(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM user_prefs WHERE key=%s", (PREF_KEY,))
        r = cur.fetchone()
    return r["value"] if r else {"lock": [], "exclude": []}


def save(conn, lock_names, exclude_names) -> dict:
    value = {"lock": split_names(lock_names), "exclude": split_names(exclude_names)}
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO user_prefs(key, value) VALUES (%s,%s)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                    (PREF_KEY, json.dumps(value)))
    conn.commit()
    return value
