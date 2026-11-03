import pytest

from dfs.sources.rg_table import parse
from dfs.sources.uploads import UploadError, parse_rg_projections

W = 46


def _header():
    h = [f"C{i}" for i in range(W)]
    h[0], h[1], h[2], h[3], h[4], h[5], h[6] = "PLAYER", "TEAM", "POS", "SAL", "OPP", "", "MIN"
    h[27], h[28], h[29], h[30], h[33], h[34], h[44] = "PP", "FPTS", "FPTS/$", "POWN", "FLOOR", "CEIL", "SIM85"
    return h


def _row(name, team, sal, fpts, own, inj="-", pp=None):
    r = ["0"] * W
    r[0], r[1], r[2], r[3], r[5], r[6] = name, team, "PG", f"${sal:,}", inj, "34"
    r[27], r[28], r[29], r[30], r[33], r[34], r[44] = str(pp if pp is not None else fpts + 5), str(fpts), "5.1", f"{own}%", "30", "60", "58"
    return r


def _file(rows, header=None):
    lines = ["#RG-TABLE\thttps://rotogrinders.com/projected-stats/nba?site=draftkings\t2026-11-03T20:00:00Z\t" + str(len(rows))]
    lines.append("#H\tGROUP A\tGROUP B")               # grouped header row with fewer cells is ignored
    lines.append("#H\t" + "\t".join(header or _header()))
    lines += ["\t".join(r) for r in rows]
    return "\n".join(lines)


def _rows(n=25):
    rows = [_row("Shai Gilgeous-Alexander", "OKC", 10100, 47.2, 9), _row("Ja'Kobe Walter", "TOR", 4100, 22.4, 0.4, inj="Q")]
    rows += [_row(f"Player {i}", "BOS", 4000 + i * 100, 20 + i, 3) for i in range(n - 2)]
    return rows


def test_parse_uses_fpts_not_prizepicks_and_dash_is_no_tag():
    out = parse_rg_projections(_file(_rows()))
    shai = out[0]
    assert shai["proj_rg"] == 47.2 and shai["salary_rg"] == 10100 and shai["own_pct"] == 9
    assert shai["rg_inj"] == "" and out[1]["rg_inj"] == "Q"
    assert shai["floor_rg"] == 30 and shai["ceil_rg"] == 60 and shai["sim85"] == 58 and shai["minutes"] == 34
    assert len(out) == 25                                   # no row dropped


def test_refuses_when_layout_shifts():
    h = _header()
    h.insert(10, "NEW")                                     # a new column shifts FPTS to 29
    rows = [r[:10] + ["x"] + r[10:] for r in _rows()]
    with pytest.raises(UploadError, match="column 28 should be FPTS"):
        parse(_file(rows, h))


def test_refuses_rows_with_wrong_width():
    rows = _rows()
    rows[5] = rows[5][:-3]
    with pytest.raises(UploadError, match="different number of columns"):
        parse(_file(rows))


def test_refuses_tiny_capture():
    with pytest.raises(UploadError, match="only 3 rows"):
        parse(_file(_rows()[:3]))


def test_html_reader_feeds_the_same_checks():
    from dfs.sources.rg_fetch import html_to_table_text
    from dfs.sources.rg_table import parse as parse_tbl
    head = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in _header()) + "</tr></thead>"
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in _rows())
    html = f"<html><body><table><tr><td>small</td></tr></table><table>{head}<tbody>{body}</tbody></table></body></html>"
    out = parse_tbl(html_to_table_text(html))["rows"]
    assert len(out) == 25 and out[0]["proj_rg"] == 47.2


def test_not_connected_is_explained(monkeypatch):
    from dfs.sources import rg_fetch
    monkeypatch.delenv("RG_COOKIE", raising=False)
    with pytest.raises(rg_fetch.RGNotConnected, match="isn't connected"):
        rg_fetch.fetch_html()
