import os
import random

import pytest

from dfs.params import default_params
from dfs.pipeline import make_lineups, prepare


def synthetic_files(seed=1, n_games=4):
    rng = random.Random(seed)
    dk = ["Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame"]
    rg = ["name,team,pos,salary,fpts,floor,ceil,minutes,proj_own,inj"]
    pid = 50000000
    for g in range(n_games):
        away, home = f"A{g}X", f"H{g}X"
        for team in (away, home):
            for k in range(10):
                pid += 1
                pos = rng.choice(["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C"])
                sal = rng.randrange(3000, 11000, 100)
                proj = round(sal / 1000 * rng.uniform(3.8, 5.8), 1)
                name = f"Player {team} {k}"
                dk.append(f"{pos},{name} ({pid}),{name},{pid},{pos}/UTIL,{sal},"
                          f"{away}@{home} 11/02/2026 0{7 + g % 3}:00PM ET,{team},{proj}")
                rg.append(f"{name},{team},{pos},{sal},{proj},{proj*0.7:.1f},{proj*1.4:.1f},"
                          f"{rng.randint(12, 36)},{rng.uniform(0, 30):.1f},{rng.choice(['-', '-', '-', 'Q'])}")
    return "\n".join(dk), "\n".join(rg)


def test_prepare_and_lineups():
    params = default_params()
    dk, rg = synthetic_files()
    prep = prepare(dk, rg, params)
    assert len(prep["players"]) == 80 and not prep["merge"]["unmatched_dk"]
    lus = make_lineups(prep["players"], params, n_gpp=5, n_cash=2)
    assert len(lus["GPP"]) == 5 and len(lus["CASH"]) == 2 and len(lus["OPTIMAL"]) == 1
    assert all(lu["salary"] >= 49300 for lu in lus["GPP"])
    assert all(lu["own_sum"] <= 150 for lu in lus["GPP"])


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")
def test_build_twice_upserts(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db
    from dfs.pipeline import run_build
    dk, rg = synthetic_files(seed=2)
    a = run_build(dk, rg, n_gpp=3, n_cash=1)
    b = run_build(dk, rg, n_gpp=3, n_cash=1)
    assert a["slate_id"] == b["slate_id"] and a["run_id"] != b["run_id"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pool WHERE slate_id=%s", (a["slate_id"],))
        assert cur.fetchone()["n"] == 80
        cur.execute("SELECT count(DISTINCT run_id) AS n FROM lineups WHERE run_id IN (%s, %s)",
                    (a["run_id"], b["run_id"]))
        assert cur.fetchone()["n"] == 2
