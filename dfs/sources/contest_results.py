"""DraftKings contest standings CSV (contest page -> Download standings), e.g. contest-standings-123456.csv.

Layout (two side-by-side blocks in one file):
  Rank, EntryId, EntryName, TimeRemaining, Points, Lineup, <blank>, Player, Roster Position, %Drafted, FPTS
The left block has one row per entry; the right block lists each player's actual ownership.
A Lineup cell looks like: "C Jarrett Allen F Evan Mobley G James Harden PF Jaylin Williams PG ... UTIL ..."."""
import csv
import io
import re

from ..names import match_key
from .uploads import UploadError, _num

SLOT_RE = re.compile(r"(?:^|\s)(PG|SG|SF|PF|C|G|F|UTIL)\s+(?=\S)")
REQUIRED_LEFT = {"Rank", "EntryId", "EntryName", "Points", "Lineup"}
REQUIRED_RIGHT = {"Player", "%Drafted"}


def contest_id_from_filename(name: str | None):
    m = re.search(r"(\d{6,})", name or "")
    return int(m.group(1)) if m else None


def parse_lineup(cell: str) -> list[dict]:
    cell = (cell or "").strip()
    if not cell:
        return []
    marks = list(SLOT_RE.finditer(cell))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(cell)
        out.append({"slot": m.group(1), "name": cell[m.end():end].strip()})
    return out


def parse_standings(text: str) -> dict:
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise UploadError("standings file is empty")
    header = [h.strip() for h in rows[0]]
    missing = (REQUIRED_LEFT | REQUIRED_RIGHT) - set(header)
    if missing:
        raise UploadError(f"not a DraftKings contest standings file; missing columns: {', '.join(sorted(missing))}")
    col = {h: i for i, h in enumerate(header) if h}
    entries, ownership = [], {}
    for r in rows[1:]:
        cell = lambda h: (r[col[h]].strip() if col.get(h) is not None and col[h] < len(r) else "")  # noqa: E731
        if cell("EntryId"):
            lineup = parse_lineup(cell("Lineup"))
            if cell("Lineup") and len(lineup) != 8:
                raise UploadError(f"could not read the lineup for entry {cell('EntryId')}: {cell('Lineup')[:80]}")
            entries.append({"entry_id": int(cell("EntryId")), "entry_name": cell("EntryName"),
                            "rank": int(_num(cell("Rank"), 0)), "points": _num(cell("Points"), 0.0),
                            "lineup": lineup})
        if cell("Player"):
            name = cell("Player")
            ownership[match_key(name)] = {"name": name, "name_key": match_key(name),
                                          "roster_pos": cell("Roster Position") or None,
                                          "pct_drafted": _num(cell("%Drafted"), 0.0), "fpts": _num(cell("FPTS"))}
    if not entries:
        raise UploadError("standings file has no entries")
    return {"entries": entries, "ownership": list(ownership.values())}


def score_quantiles(points: list[float]) -> dict:
    s = sorted(points)
    if not s:
        return {}

    def q(p):
        return round(s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))], 2)
    return {"p50": q(0.5), "p75": q(0.75), "p90": q(0.9), "p99": q(0.99), "p999": q(0.999), "max": s[-1]}
