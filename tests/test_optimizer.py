import itertools
from collections import Counter

import pytest

from dfs.optimizer import SLOTS, Settings, build_lineups, eligible_slots, objective_score
from dfs.params import default_params
from tests.conftest import make_slate


def check_valid(lu, s, by_key):
    ps = [by_key[x["key"]] for x in lu["players"]]
    assert [x["slot"] for x in lu["players"]] == SLOTS
    assert len({p["key"] for p in ps}) == 8
    for x in lu["players"]:
        assert x["slot"] in eligible_slots(by_key[x["key"]]["pos"])
    assert s.min_salary <= lu["salary"] <= 50000
    assert max(Counter(p["team"] for p in ps).values()) <= s.max_from_team
    assert max(Counter(p["game"] for p in ps).values()) <= 7
    assert len({p["game"] for p in ps}) >= 2
    if s.max_own_sum is not None:
        assert lu["own_sum"] <= s.max_own_sum + 1e-6


def test_eligible_slots():
    assert eligible_slots("PF/C") == ["PF", "C", "F", "UTIL"]
    assert eligible_slots("PG") == ["PG", "G", "UTIL"]
    assert eligible_slots("SG/SF") == ["SG", "SF", "G", "F", "UTIL"]


@pytest.mark.parametrize("mode", ["GPP", "CASH"])
def test_multi_lineup_constraints(mode):
    params = default_params()
    players = make_slate(n_games=5, seed=3)
    s = Settings.from_params(params, mode, max_own_sum=params["GPP"]["max_own_sum"] if mode == "GPP" else None)
    lus = build_lineups(players, s, n=10)
    assert len(lus) == 10
    by_key = {p["key"]: p for p in players}
    for lu in lus:
        check_valid(lu, s, by_key)
    keysets = [{x["key"] for x in lu["players"]} for lu in lus]
    for a, b in itertools.combinations(keysets, 2):
        assert len(a & b) <= 8 - s.min_uniques
    objs = [lu["objective"] for lu in lus]
    assert objs == sorted(objs, reverse=True)


def test_locks_excludes_exposure():
    players = make_slate(n_games=4, seed=5)
    s = Settings(mode="GPP", min_salary=0, locks=[3], excludes=[7, 8], max_exposure=0.5, min_uniques=1)
    lus = build_lineups(players, s, n=6)
    counts = Counter(x["key"] for lu in lus for x in lu["players"])
    assert all(3 in {x["key"] for x in lu["players"]} for lu in lus)
    assert counts[7] == 0 and counts[8] == 0
    assert all(c <= 3 for k, c in counts.items() if k != 3)


def test_two_game_rule_on_two_game_slate():
    players = make_slate(n_games=2, per_team=10, seed=9)
    lu = build_lineups(players, Settings(mode="OPTIMAL", min_salary=0, max_from_team=8), n=1)[0]
    assert len({x["key"] for x in lu["players"]}) == 8
    games = Counter({p["key"]: p for p in players}[x["key"]]["game"] for x in lu["players"])
    assert max(games.values()) <= 7


def _brute_force(players, s):
    best = None
    for combo in itertools.combinations(players, 8):
        if not (s.min_salary <= sum(p["salary"] for p in combo) <= 50000):
            continue
        if max(Counter(p["team"] for p in combo).values()) > s.max_from_team:
            continue
        if max(Counter(p["game"] for p in combo).values()) > 7:
            continue
        if not any(all(sl in eligible_slots(p["pos"]) for p, sl in zip(perm, SLOTS))
                   for perm in itertools.permutations(combo)):
            continue
        v = sum(objective_score(p, s) for p in combo)
        best = v if best is None or v > best else best
    return best


@pytest.mark.parametrize("seed", [1, 2])
def test_matches_brute_force(seed):
    players = make_slate(n_games=2, per_team=5, seed=seed)[:11]
    s = Settings(mode="GPP", min_salary=0, max_from_team=5)
    lus = build_lineups(players, s, n=1)
    bf = _brute_force(players, s)
    if bf is None:
        assert lus == []
    else:
        assert lus[0]["objective"] == pytest.approx(bf, abs=1e-6)
