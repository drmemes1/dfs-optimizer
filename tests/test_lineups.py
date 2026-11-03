from pathlib import Path

from dfs.names import name_norm
from dfs.sources.lineups import apply_lineups, parse_rg_lineups, parse_rotowire, team_code

FX = Path(__file__).parent / "fixtures"


def test_parse_rotowire():
    games = parse_rotowire((FX / "rotowire_sample.html").read_text())
    assert len(games) == 1                                   # promo box skipped
    g = games[0]
    assert (g["away"], g["home"]) == ("OKC", "CLE")
    assert g["fav_ml"] == {"team": "CLE", "ml": -159} and g["total"] == 235.0
    okc, cle = g["teams"]["OKC"], g["teams"]["CLE"]
    assert okc["status"] == "confirmed" and cle["status"] == "expected"
    assert [s["name"] for s in okc["starters"]][0] == "Shai Gilgeous-Alexander"   # full name from title
    assert okc["may_not_play"][0]["inj"] == "O"
    assert cle["starters"][1]["inj"] == "GTD" and cle["starters"][1]["pct_play"] == 50
    assert cle["may_not_play"][0]["inj"] == "Q"


def test_parse_rg_lineups_public_fields_only():
    games = parse_rg_lineups((FX / "rg_lineups_sample.html").read_text())
    g = games[0]
    assert g["total"] == 229.5 and g["fav_ml"] == {"team": "CLE", "ml": -173}
    assert g["teams"]["OKC"]["confirmed"] and not g["teams"]["CLE"]["confirmed"]
    assert len(g["teams"]["OKC"]["starters"]) == 5 and g["teams"]["OKC"]["bench"][0]["name"] == "Aday Mara"
    assert "proj" not in g["teams"]["OKC"]["starters"][0]   # subscriber-only numbers are not read


def _p(name, team, opp, salary, **kw):
    return {"name": name, "name_norm": name_norm(name), "team": team, "opp": opp, "salary": salary,
            "inj_status": "", **kw}


def test_cross_check_consensus_contingent_vegas():
    src = {"rotowire": parse_rotowire((FX / "rotowire_sample.html").read_text()),
           "rg": parse_rg_lineups((FX / "rg_lineups_sample.html").read_text())}
    ps = [
        _p("Kenrich Williams", "OKC", "CLE", 4300),     # both say starter, OKC confirmed -> consensus, not contingent
        _p("Meleek Thomas", "CLE", "OKC", 3300),        # RotoWire starter, RG bench -> disputed -> contingent
        _p("Jaylon Tyson", "CLE", "OKC", 3500),         # RG starter, RotoWire may-not-play (Ques) -> disputed
        _p("Evan Mobley", "CLE", "OKC", 8000),          # consensus starter, expensive -> never contingent
        _p("Chet Holmgren", "OKC", "CLE", 7500),        # Out per RotoWire
        _p("Jarrett Allen", "CLE", "OKC", 5000),        # consensus but CLE unconfirmed with a GTD starter -> contingent
    ]
    rep = apply_lineups(ps, src)
    k, mt, jt, em, ch, ja = ps
    assert k["starter_consensus"] and not k["contingent"]
    assert mt["starter_rw"] and not mt["starter_rg"] and mt["contingent"]
    assert jt["contingent"] and jt["inj_status"] == "Q"
    assert em["starter_consensus"] and not em["contingent"]
    assert ch["inj_status"] == "O"
    assert ja["starter_consensus"] and ja["contingent"]
    assert k["vegas_total"] == 229.5 and k["fav_ml"] == -173       # RotoGrinders Vegas preferred
    assert {d["name"] for d in rep["disagreements"]} == {"Meleek Thomas", "Jaylon Tyson"}
    assert rep["sources"] == {"rotowire": True, "rotogrinders": True}


def test_one_source_down_still_works():
    src = {"rotowire": parse_rotowire((FX / "rotowire_sample.html").read_text()), "rg": None}
    ps = [_p("Kenrich Williams", "OKC", "CLE", 4300)]
    rep = apply_lineups(ps, src)
    assert ps[0]["starter_consensus"] and ps[0]["starter_rg"] is None
    assert rep["sources"]["rotogrinders"] is False


def test_team_aliases():
    assert team_code("GS") == team_code("GSW") == "GSW"
    assert team_code("pho") == "PHX" and team_code("NO") == "NOP"
