import pytest

from dfs.names import name_norm
from dfs.params import default_params
from dfs.pool import eligible_pool, value_grade
from dfs.scoring import dk_points
from dfs.signals import apply_signals, estimate_missing_ownership


@pytest.mark.parametrize("raw,norm", [
    ("Nikola Jokić", "nikola jokic"), ("Jaren Jackson Jr.", "jaren jackson"),
    ("Ja'Kobe Walter", "jakobe walter"), ("Gary Trent Jr", "gary trent"),
    ("Robert Williams III", "robert williams"), ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
    ("Luka Dončić", "luka doncic"), ("  P.J. Washington ", "pj washington"),
])
def test_name_norm(raw, norm):
    assert name_norm(raw) == norm


def test_dk_scoring():
    assert dk_points(pts=20, fg3m=2, reb=5, ast=4, stl=1, blk=1, tov=3) == 20 + 1 + 6.25 + 6 + 2 + 2 - 1.5
    assert dk_points(pts=20, reb=10) == 20 + 12.5 + 1.5                  # double-double
    assert dk_points(pts=10, reb=10, ast=10) == 10 + 12.5 + 15 + 4.5      # triple-double stacks
    assert dk_points(pts=30, dnp=True) == 0


def test_signal_layer_exact():
    params = default_params()
    p = {"proj_rg": 40, "floor_rg": 30, "ceil_rg": 55, "salary": 8000, "minutes": 34,
         "vegas_total": 240, "fav_ml": -450, "b2b": True, "inj_status": "Q"}
    apply_signals([p], params)
    m = (240 / 232) ** 0.5 * 0.965 * 0.9751 * 0.88
    assert p["proj_adj"] == pytest.approx(40 * m, abs=1e-3)
    assert p["ceil"] == pytest.approx(55 * m, abs=1e-3)
    assert p["floor"] == pytest.approx(30 * m, abs=1e-3)
    assert set(p["signals"]) == {"blowout_starter", "b2b", "questionable"}
    bench = {"proj_rg": 20, "salary": 4000, "minutes": 20, "fav_ml": 400}
    close = {"proj_rg": 20, "salary": 4000, "minutes": 20, "fav_ml": -150, "inj_status": "-"}
    apply_signals([bench, close], params)
    assert bench["proj_adj"] == pytest.approx(20 * 1.03)
    assert close["proj_adj"] == pytest.approx(20 * 1.02)   # RG "-" is not a flag
    override = {"proj_rg": 20, "salary": 4000, "inj_status": "GTD", "questionable_mult": 0.95,
                "contingent": True}
    apply_signals([override], params)
    assert override["proj_adj"] == pytest.approx(20 * 0.95 * 0.93)


def test_pool_rule():
    params = default_params()
    base = {"proj_adj": 20, "minutes": 20}
    rich = {**base, "salary": 5500}
    starter = {**base, "salary": 4000, "starter_consensus": True}
    minutes = {**base, "salary": 4000, "minutes": 24}
    bench = {**base, "salary": 4000}
    contingent = {**base, "salary": 9000, "contingent": True}
    quest = {**base, "salary": 9000, "minutes": 30, "inj_status": "Q"}
    out = {**base, "salary": 9000, "inj_status": "O"}
    gpp = eligible_pool([rich, starter, minutes, bench, contingent, quest, out], params, "GPP")
    assert gpp == [rich, starter, minutes, quest]
    cash = eligible_pool([rich, starter, minutes, bench, contingent, quest, out], params, "CASH")
    assert cash == [minutes]   # cash: drop questionable, min_minutes 24


def test_value_grades():
    params = default_params()
    g = lambda **kw: value_grade({"salary": 4500, "minutes": 20, "value": 4.0, **kw}, params)[0]
    assert g(starter_consensus=True, minutes=26, value=5.2) == "A"
    assert g(starter_consensus=True, minutes=26) == "B"
    assert g(value=5.0) == "C"
    assert g() == "D"
    assert g(minutes=11) is None
    assert g(salary=5500, starter_consensus=True, minutes=30, value=6) is None


def test_ownership_estimate_only_for_unpublished_games(  ):
    from tests.conftest import make_slate
    ps = make_slate(n_games=3)
    for p in ps:
        if p["game"].endswith("H2"):
            p["own_pct"] = 0
    estimate_missing_ownership(ps)
    est = [p for p in ps if p.get("own_estimated")]
    assert est and all(p["game"].endswith("H2") and p["own_pct"] > 0 for p in est)
    assert not any(p.get("own_estimated") for p in ps if not p["game"].endswith("H2"))


def test_aliases_and_initial_fallback_are_reported():
    from dfs.names import NameIndex, match_key
    assert match_key("Lu Dort") == match_key("Luguentz Dort")
    assert match_key("Nic Claxton") == match_key("Nicolas Claxton")
    pool = [{"name": "Luguentz Dort", "team": "ATL"}, {"name": "Jalen Williams", "team": "OKC"},
            {"name": "Jaylin Williams", "team": "OKC"}, {"name": "Kenyon Martin Jr.", "team": "UTA"}]
    idx = NameIndex(pool)
    assert idx.find("Lu Dort", "ATL") == (pool[0], "exact")
    assert idx.find("K.J. Martin", "UTA")[0] is pool[3]
    # same last name on one team: initial fallback must not guess between Jalen and Jaylin
    assert idx.find("J. Williams", "OKC") == (None, "none")
    p, how = idx.find("Luke Dort", "ATL")
    assert p is pool[0] and how == "initial"          # fallback match, flagged as such
    assert idx.find("Nobody Here", "ATL") == (None, "none")
