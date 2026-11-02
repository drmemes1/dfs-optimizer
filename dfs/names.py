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
