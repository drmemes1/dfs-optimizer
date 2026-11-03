import base64
import os
from collections import Counter
from datetime import datetime, timezone

import pytest

from dfs.params import default_params
from dfs.pipeline import build_all, clean_overrides, prepare
from tests.test_pipeline import synthetic_files

NO_PAGES = lambda: {"rotowire": None, "rg": None, "errors": {"rotowire": "offline test", "rg": "offline test"}, "raw": {}}  # noqa: E731
BEFORE_TIP = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)   # 3pm ET, before every game


def test_overrides_locks_excludes_cost_and_consensus():
    params = default_params()
    dk, rg = synthetic_files(seed=4, n_games=5)
    players = prepare(dk, rg, params)["players"]
    base, _, _ = build_all(players, params, n_gpp=6, n_cash=1)
    top = base["GPP"][0]
    drop = top["players"][0]["key"]
    cheap_bench = min((p for p in players if p["salary"] < 4000), key=lambda p: p["proj_adj"])
    res, costs, used = build_all(players, params, n_gpp=6, n_cash=1,
                                 overrides=clean_overrides({"GPP": {"max_per_game": 4, "max_from_team": 2}}),
                                 locks=[cheap_bench["key"]], excludes=[drop])
    assert used["GPP"]["max_per_game"] == 4 and used["GPP"]["max_from_team"] == 2
    for lu in res["GPP"]:
        keys = [x["key"] for x in lu["players"]]
        assert cheap_bench["key"] in keys and drop not in keys
        by = {p["key"]: p for p in players}
        assert max(Counter(by[k]["game"] for k in keys).values()) <= 4
        assert max(Counter(by[k]["team"] for k in keys).values()) <= 2
    assert costs["GPP"]["proj"] < 0                      # constraints cost projection, and we say how much
    cons = res["CONSENSUS"][0]
    exposures = sorted((x["exposure"] for x in cons["players"]), reverse=True)
    assert exposures[0] >= 0.5                           # built from the most-picked players


pytestmark_db = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")


@pytestmark_db
def test_refresh_check_swap_entered_and_raw_archive(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, pipeline
    dk, rg = synthetic_files(seed=11, n_games=4)
    dk = dk.replace("11/02/2026", "11/09/2026")       # its own slate
    raw = ("﻿" + dk).encode("utf-8")
    dk_text, dk_raw = pipeline.decode_upload(base64.b64encode(raw).decode(), None)
    out = pipeline.run_build(dk_text, rg, n_gpp=3, n_cash=1, dk_raw=dk_raw, dk_name="DKSalaries.csv", fetch=NO_PAGES)
    sid = out["slate_id"]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT raw, sha256 FROM source_snapshots WHERE slate_id=%s AND source='dk_salaries_csv'", (sid,))
        r = cur.fetchone()
        assert bytes(r["raw"]) == raw                  # exact bytes, BOM included
        cur.execute("SELECT id, players FROM lineups WHERE slate_id=%s AND mode='GPP' AND rank=1", (sid,))
        lu = cur.fetchone()
    victim = lu["players"][2]
    # late news: the victim is now OUT in a fresh RotoGrinders export
    lines = rg.splitlines()
    lines = [ln if not ln.startswith(victim["name"] + ",") else ",".join(ln.split(",")[:-1] + ["O"]) for ln in lines]
    pipeline.run_refresh("\n".join(lines), slate_id=sid, fetch=NO_PAGES, now=BEFORE_TIP)
    with db.connect() as conn:
        res = pipeline.check(conn, sid, now=BEFORE_TIP)
        f = [x for x in res["flags"] if x["lineup_id"] == lu["id"] and x["key"] == victim["key"]]
        assert f and f[0]["status"] == "O" and not f[0]["locked"] and f[0]["suggestions"]
        sub = f[0]["suggestions"][0]
        assert sub["salary"] <= f[0]["salary_room"]
        out = pipeline.swap(conn, lu["id"], victim["key"], sub["key"], now=BEFORE_TIP)
        assert out["salary"] <= 50000
        with pytest.raises(Exception, match="started"):   # after tip, DK has him locked
            pipeline.swap(conn, lu["id"], sub["key"], victim["key"], now=datetime(2026, 11, 10, tzinfo=timezone.utc))
        pipeline.set_entered(conn, lu["id"], True)
        with conn.cursor() as cur:
            cur.execute("SELECT entered, edited, players_original, players FROM lineups WHERE id=%s", (lu["id"],))
            row = cur.fetchone()
        assert row["entered"] and row["edited"]
        assert victim["key"] in {x["key"] for x in row["players_original"]}
        assert victim["key"] not in {x["key"] for x in row["players"]}
