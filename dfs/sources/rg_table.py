"""RotoGrinders NBA projected-stats table, captured from the user's logged-in browser by the
'Grab RG projections' bookmarklet (public/rg.html).

File format (tab-separated, written by the bookmarklet):
  #RG-TABLE <tab> page url <tab> captured-at ISO time <tab> row count
  #H <tab> header cell 0 <tab> header cell 1 ...        (one line per header row)
  <row innerText>                                        (each <tr>'s innerText; cells split on tabs)

Column positions for the numbers come from measured use of this page:
  FPTS=28, FPTS/$=29, POWN=30, FLOOR=33, CEIL=34, SIM85=44 (27 is PrizePicks scoring, NOT FPTS).
They are checked against the header text; any mismatch refuses the file instead of guessing.
Name/team/position/salary/minutes are found by header name. The injury column uses "-" for no tag."""
import re
from collections import Counter

from ..names import name_norm
from .uploads import UploadError, _num

MARKER = "#RG-TABLE"
FIXED = {"fpts": 28, "fpts_per_k": 29, "own": 30, "floor": 33, "ceil": 34, "sim85": 44}
EXPECT = {"fpts": ("FPTS",), "fpts_per_k": ("FPTS/$", "PT/$", "FPTS/$K", "VALUE"), "own": ("POWN", "OWN"),
          "floor": ("FLOOR",), "ceil": ("CEIL",), "sim85": ("SIM85", "85")}
BY_NAME = {"name": ("PLAYER", "NAME"), "team": ("TEAM", "TM"), "pos": ("POS", "POSITION"),
           "salary": ("SAL", "SALARY"), "minutes": ("MIN", "MINS", "MINUTES", "PMIN", "PROJ MIN"),
           "inj": ("INJ", "INJURY", "STATUS")}
INJ_FALLBACK_INDEX = 5   # from measured use; only used if the header says something injury-like there


def is_rg_table(text: str) -> bool:
    return text.lstrip("﻿").startswith(MARKER)


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip()).upper()


def parse(text: str) -> dict:
    lines = [ln.rstrip("\r") for ln in text.lstrip("﻿").split("\n") if ln.strip()]
    meta = lines[0].split("\t")
    headers = [ln.split("\t")[1:] for ln in lines[1:] if ln.startswith("#H\t")]
    data = [ln.split("\t") for ln in lines[1:] if not ln.startswith("#")]
    if len(data) < 20:
        raise UploadError(f"only {len(data)} rows captured; open the full NBA projections table and grab again")
    width = Counter(len(r) for r in data).most_common(1)[0][0]
    if width <= max(FIXED.values()):
        raise UploadError(f"rows have {width} columns; expected at least {max(FIXED.values()) + 1}. "
                          "Is this the DraftKings NBA projected-stats page?")
    header = next((h for h in reversed(headers) if len(h) == width), None)
    if header is None:
        raise UploadError("couldn't find a header row matching the table's columns; nothing was guessed")
    H = [_norm(h) for h in header]

    for key, idx in FIXED.items():
        if not any(H[idx] == e or H[idx].replace(" ", "") == e.replace(" ", "") for e in EXPECT[key]):
            around = ", ".join(f"{i}={H[i]}" for i in range(max(0, idx - 2), min(width, idx + 3)))
            raise UploadError(f"column {idx} should be {EXPECT[key][0]} but the header says '{header[idx]}' "
                              f"({around}). The page layout changed; refusing rather than using the wrong column.")
    cols = {}
    for key, names in BY_NAME.items():
        hit = next((i for i, h in enumerate(H) if h in names), None)
        if hit is not None:
            cols[key] = hit
    if "inj" not in cols and INJ_FALLBACK_INDEX < width and H[INJ_FALLBACK_INDEX] in ("", "INJ", "STATUS", "I"):
        cols["inj"] = INJ_FALLBACK_INDEX
    missing = [k for k in ("name", "salary") if k not in cols]
    if missing:
        raise UploadError(f"couldn't find the {', '.join(missing)} column by header; headers were: "
                          + ", ".join(f"{i}={h}" for i, h in enumerate(header)))

    out, short = [], 0
    for r in data:
        if len(r) != width:
            short += 1
            continue
        name = r[cols["name"]].strip()
        if not name:
            continue
        inj = r[cols["inj"]].strip() if "inj" in cols else ""
        out.append({
            "name": name, "name_norm": name_norm(name),
            "team": (r[cols["team"]].strip().upper() or None) if "team" in cols else None,
            "salary_rg": _num(r[cols["salary"]]),
            "proj_rg": _num(r[FIXED["fpts"]], 0.0),
            "floor_rg": _num(r[FIXED["floor"]]),
            "ceil_rg": _num(r[FIXED["ceil"]]),
            "sim85": _num(r[FIXED["sim85"]]),
            "minutes": _num(r[cols["minutes"]]) if "minutes" in cols else None,
            "own_pct": _num(r[FIXED["own"]], 0.0),
            "rg_inj": "" if inj in ("", "-") else inj.upper(),   # "-" means no tag (truthy in JS; not here)
        })
    if short:
        raise UploadError(f"{short} rows had a different number of columns than the header; refusing so no "
                          "player is silently dropped. Re-grab after the page fully loads.")
    return {"rows": out, "page": meta[1] if len(meta) > 1 else None, "captured_at": meta[2] if len(meta) > 2 else None}
