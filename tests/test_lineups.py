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
        _p("James Harden", "CLE", "OKC", 9000, pos="PG"),  # consensus starter, expensive -> never contingent
        _p("Chet Holmgren", "OKC", "CLE", 7500),        # Out per RotoWire
        _p("Jarrett Allen", "CLE", "OKC", 5000, pos="C"),   # CLE unconfirmed, but the Q teammate is a forward -> not contingent
        _p("Evan Mobley", "CLE", "OKC", 5200, pos="PF"),    # same position group as the Q forward -> contingent
    ]
    rep = apply_lineups(ps, src)
    k, mt, jt, em, ch, ja, em_cheap = ps
    assert k["starter_consensus"] and not k["contingent"]
    assert mt["starter_rw"] and not mt["starter_rg"] and mt["contingent"]
    assert jt["contingent"] and jt["inj_status"] == "Q"
    assert em["starter_consensus"] and not em["contingent"]   # Harden
    assert ch["inj_status"] == "O"
    assert ja["starter_consensus"] and not ja["contingent"]
    assert em_cheap["starter_consensus"] and em_cheap["contingent"]
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


def test_unmatched_and_fuzzy_names_are_loud():
    src = {"rotowire": parse_rotowire((FX / "rotowire_sample.html").read_text()),
           "rg": parse_rg_lineups((FX / "rg_lineups_sample.html").read_text())}
    ps = [_p("Kenrich Williams", "OKC", "CLE", 4300), _p("Shai Gilgeous-Alexander", "OKC", "CLE", 10100)]
    rep = apply_lineups(ps, src)
    names = {u["name"] for u in rep["unmatched_names"]}
    assert "Ajay Mitchell" in names and "Isaiah Hartenstein" in names   # starters not in this pool -> reported
    assert ps[1]["starter_consensus"]


# ---------------------------------------------------------------- back-to-back

def test_b2b_is_set_from_the_teams_that_played_yesterday():
    """b2b_mult is already tuned and already multiplied into every projection; the only missing
    piece was the flag."""
    src = {"rotowire": None, "rg": None, "b2b_teams": {"OKC", "MIL"}}
    ps = [_p("A", "OKC", "CLE", 9000), _p("B", "CLE", "OKC", 8000), _p("C", "MIL", "TOR", 7000)]
    apply_lineups(ps, src)
    assert ps[0]["b2b"] is True and ps[2]["b2b"] is True
    assert ps[1]["b2b"] is False


def test_b2b_is_left_unknown_when_the_schedule_could_not_be_read():
    """An unreachable scoreboard must not be recorded as 'nobody is on a back-to-back' - that is
    a claim we cannot make, and signals.py treats a missing key as no adjustment anyway."""
    ps = [_p("A", "OKC", "CLE", 9000)]
    apply_lineups(ps, {"rotowire": None, "rg": None})
    assert "b2b" not in ps[0]


def test_b2b_matches_on_normalised_team_codes():
    """ESPN and DraftKings disagree on some abbreviations; team_code is the shared normaliser."""
    src = {"rotowire": None, "rg": None, "b2b_teams": {team_code("NO")}}
    ps = [_p("A", "NOP", "SAS", 6000)]
    apply_lineups(ps, src)
    assert ps[0]["b2b"] is True


# ---------------------------------------------------------------- role change

def test_role_change_is_set_when_a_positional_teammate_is_out_at_any_salary():
    """`contingent` only fires under $5,500 with an *unresolved* tag. The ordinary role change -
    a starter ruled OUT and his backup promoted - needs its own flag at any salary."""
    ps = [_p("Backup Big", "OKC", "CLE", 6200, pos="C"),
          _p("Star Big", "OKC", "CLE", 10400, pos="C", inj_status="O"),
          _p("Guard Guy", "OKC", "CLE", 7000, pos="PG")]
    apply_lineups(ps, {"rotowire": None, "rg": None, "b2b_teams": set()})
    assert ps[0]["role_change"] is True, "his position group lost a starter"
    assert ps[2]["role_change"] is False, "a guard is unaffected by a centre being out"


def test_role_change_ignores_the_other_team():
    ps = [_p("Mine", "OKC", "CLE", 6000, pos="C"),
          _p("Theirs", "CLE", "OKC", 9000, pos="C", inj_status="O")]
    apply_lineups(ps, {"rotowire": None, "rg": None})
    assert ps[0]["role_change"] is False


def test_a_merely_questionable_teammate_is_not_a_role_change():
    """Q usually plays. Only a resolved absence changes the job."""
    ps = [_p("Backup", "OKC", "CLE", 6000, pos="C"),
          _p("Starter", "OKC", "CLE", 9000, pos="C", inj_status="Q")]
    apply_lineups(ps, {"rotowire": None, "rg": None})
    assert ps[0]["role_change"] is False


def test_role_known_reflects_whether_either_lineup_page_loaded():
    ps = [_p("A", "OKC", "CLE", 9000, pos="C")]
    apply_lineups(ps, {"rotowire": None, "rg": None})
    assert ps[0]["role_known"] is False, "neither page loaded, so roles are unknown"

    ps2 = [_p("B", "OKC", "CLE", 9000, pos="C")]
    apply_lineups(ps2, {"rotowire": parse_rotowire((FX / "rotowire_sample.html").read_text()),
                        "rg": None})
    assert ps2[0]["role_known"] is True
