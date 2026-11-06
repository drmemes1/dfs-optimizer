"""The wrong-slate guard, exercised through the real pipeline rather than a stub.

The RotoGrinders projections page is slate-scoped, so a read can return a different slate's
players. Without a guard, merge() drops every unmatched DraftKings player and save_pool() then
sets the whole pool to proj_adj=0 / NOPROJ and persists it — which at T-20, with money entered,
reads as "every player flagged, no fitting replacement"."""
import os

import pytest

from tests.test_pipeline import synthetic_files

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")

NO_PAGES = lambda: {"rotowire": None, "rg": None, "errors": {}, "raw": {}}  # noqa: E731


def _other_slate_rg(n=40):
    """Valid RotoGrinders CSV for players who are not on our slate at all."""
    rows = ["name,team,pos,salary,fpts,floor,ceil,minutes,proj_own,inj"]
    for i in range(n):
        rows.append(f"Stranger {i},ZZZ,PG,7000,35.0,24.5,49.0,32,10.0,-")
    return "\n".join(rows)


def test_run_build_refuses_a_different_slates_projections(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import pipeline
    dk, _ = synthetic_files(seed=21, n_games=4)
    dk = dk.replace("11/02/2026", "04/02/2027")
    with pytest.raises(pipeline.SlateMismatch, match="different slate"):
        pipeline.run_build(dk, _other_slate_rg(), n_gpp=2, n_cash=1, fetch=NO_PAGES)


def test_run_build_reports_coverage_on_a_good_slate(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import pipeline
    dk, rg = synthetic_files(seed=22, n_games=4)
    dk = dk.replace("11/02/2026", "04/05/2027")
    out = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)
    assert out["dk_coverage"] > 0.95, "the synthetic files match exactly"


def test_refresh_falls_back_to_stored_projections_and_does_not_poison_the_pool(monkeypatch):
    """C1 regression. A wrong-slate read at T-20 must degrade loudly, not wreck the slate."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import db, pipeline
    dk, rg = synthetic_files(seed=23, n_games=4)
    dk = dk.replace("11/02/2026", "04/08/2027")
    sid = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)["slate_id"]

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pool WHERE slate_id=%s AND proj_adj > 0", (sid,))
        before = cur.fetchone()["n"]
    assert before > 50

    out = pipeline.run_refresh(rg_text=_other_slate_rg(), slate_id=sid, fetch=NO_PAGES)
    assert out.get("degraded"), "the fallback must be reported, not silent"

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pool WHERE slate_id=%s AND proj_adj > 0", (sid,))
        after = cur.fetchone()["n"]
        cur.execute("""SELECT raw FROM source_snapshots WHERE slate_id=%s
                       AND source='rg_projections_csv' ORDER BY id DESC LIMIT 1""", (sid,))
        newest = bytes(cur.fetchone()["raw"]).decode("utf-8-sig")
    assert after == before, "the pool must not be zeroed by a wrong-slate read"
    assert "Stranger" not in newest, "the wrong-slate text must not become the slate's projections"


def test_a_good_refresh_still_updates_and_is_not_marked_degraded(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import pipeline
    dk, rg = synthetic_files(seed=24, n_games=4)
    dk = dk.replace("11/02/2026", "04/11/2027")
    sid = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=NO_PAGES)["slate_id"]
    out = pipeline.run_refresh(rg_text=rg, slate_id=sid, fetch=NO_PAGES)
    assert not out.get("degraded")
