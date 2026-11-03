"""Name normalization for matching players across DK, RotoGrinders, RotoWire and box scores."""
import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def name_norm(name: str) -> str:
    """lowercase, accents stripped, punctuation removed, trailing Jr/Sr/II/III/IV removed.

    >>> name_norm("Nikola Jokić")
    'nikola jokic'
    >>> name_norm("Jaren Jackson Jr.")
    'jaren jackson'
    >>> name_norm("Ja'Kobe Walter")
    'jakobe walter'
    >>> name_norm("Kelly Oubre Jr")
    'kelly oubre'
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = s.replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    parts = s.split()
    while len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


# Nicknames / legal names that normalization alone cannot equate. Both spellings map to one key.
# Add to this list whenever the unmatched-name report shows a real mismatch.
_ALIASES = {
    "lu dort": "luguentz dort",
    "nic claxton": "nicolas claxton",
    "herb jones": "herbert jones",
    "cam johnson": "cameron johnson",
    "cam thomas": "cameron thomas",
    "cam whitmore": "cameron whitmore",
    "moe wagner": "moritz wagner",
    "gg jackson": "gregory jackson",
    "kj martin": "kenyon martin",
    "bub carrington": "carlton carrington",
    "bones hyland": "nahshon hyland",
    "ron holland": "ronald holland",
}


def match_key(name: str) -> str:
    """name_norm, then the alias table, so nickname and legal name share a key."""
    n = name_norm(name)
    return _ALIASES.get(n, n)


class NameIndex:
    """Find a pool player by name (and team). Exact key first; then a same-team fallback on
    last name + first initial when that is unique. Every fallback match is reported, never silent."""

    def __init__(self, players: list[dict]):
        self.by_key: dict[str, list[dict]] = {}
        self.by_team_last: dict[tuple, list[dict]] = {}
        for p in players:
            k = match_key(p["name"])
            self.by_key.setdefault(k, []).append(p)
            parts = k.split()
            if parts:
                self.by_team_last.setdefault((p.get("team_code") or p.get("team"), parts[-1]), []).append(p)

    def find(self, name: str, team: str | None = None):
        """Returns (player or None, method) where method is 'exact', 'initial' or 'none'."""
        k = match_key(name)
        cands = self.by_key.get(k, [])
        if len(cands) > 1 and team:
            cands = [c for c in cands if (c.get("team_code") or c.get("team")) == team] or cands
        if len(cands) == 1 or (cands and not team):
            return cands[0], "exact"
        if cands:
            return cands[0], "exact"
        parts = k.split()
        if team and len(parts) >= 2:
            same = [c for c in self.by_team_last.get((team, parts[-1]), [])
                    if match_key(c["name"]).split()[0][:1] == parts[0][:1]]
            if len(same) == 1:
                return same[0], "initial"
        return None, "none"
