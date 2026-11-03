import os

import pytest

from dfs.names import name_norm
from dfs.prefs import resolve, split_names


def _p(key, name, team, status=""):
    return {"key": key, "name": name, "name_norm": name_norm(name), "team": team, "inj_status": status}


POOL = [_p(1, "Jalen Brunson", "NYK"), _p(2, "Luguentz Dort", "ATL"), _p(3, "Jalen Williams", "OKC", "O"),
        _p(4, "Jalen Williams", "DEN"), _p(5, "Nikola Jokić", "DEN"), _p(6, "Kelly Oubre Jr.", "PHI")]


def test_split_names():
    assert split_names("Jalen Brunson, Lu Dort; nikola jokic\nJalen Brunson") == ["Jalen Brunson", "Lu Dort", "nikola jokic"]


def test_resolve_typed_names():
    r = resolve(POOL, "jalen brunson, Lu Dort, Kelly Oubre", "Nikola Jokic, Jalen Wiliams")
    assert r["locks"] == [1, 2, 6] and r["excludes"] == [5]          # accents, nickname, Jr. all handled
    assert r["unmatched"] == [{"name": "Jalen Wiliams", "list": "exclude"}]   # typo is reported, not ignored


def test_ambiguous_name_needs_team_and_out_players_cant_be_locked():
    r = resolve(POOL, "Jalen Williams", "")
    assert r["locks"] == [] and "ambiguous" in r["unmatched"][0]["why"]
    r = resolve(POOL, "Jalen Williams (DEN)", "")
    assert r["locks"] == [4]
    r = resolve(POOL, "Jalen Williams (OKC)", "")
    assert r["locks"] == [] and r["cant_lock"][0]["status"] == "O"


def test_conflict_exclude_wins():
    r = resolve(POOL, "Jalen Brunson", "Jalen Brunson")
    assert r["locks"] == [] and r["excludes"] == [1] and r["conflicts"] == ["Jalen Brunson"]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="no TEST_DATABASE_URL")
def test_standing_lists_apply_to_later_builds(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    from dfs import pipeline
    from tests.test_pipeline import synthetic_files
    dk, rg = synthetic_files(seed=31, n_games=4)
    dk = dk.replace("11/02/2026", "12/05/2026")
    off = lambda: {"rotowire": None, "rg": None, "errors": {}, "raw": {}}  # noqa: E731
    first = pipeline.run_build(dk, rg, n_gpp=2, n_cash=1, fetch=off, exclude_names="Player H0X 1, Somebody Gone",
                               remember=True)
    assert [u["name"] for u in first["names"]["unmatched"]] == ["Somebody Gone"]   # typed: could be a typo
    again = pipeline.run_rebuild(first["slate_id"], n_gpp=3, n_cash=1)      # nothing typed: standing list applies
    assert len(again["names"]["excludes"]) == 1
    assert again["names"]["not_on_slate"] == ["Somebody Gone"] and not again["names"]["unmatched"]
    from dfs import db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT players FROM lineups WHERE run_id=%s", (again["run_id"],))
        assert all("Player H0X 1" not in {x["name"] for x in r["players"]} for r in cur.fetchall())
