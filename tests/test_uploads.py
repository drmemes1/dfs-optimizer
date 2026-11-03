from pathlib import Path

import pytest

from dfs.sources.uploads import UploadError, map_rg_headers, merge, parse_dk_salaries, parse_rg_projections

FX = Path(__file__).parent / "fixtures"


def test_dk_salaries():
    dk = parse_dk_salaries((FX / "dk_salaries_sample.csv").read_text())
    assert len(dk["players"]) == 3
    luka = dk["players"][0]
    assert luka["draftable_id"] == 40000001 and luka["salary"] == 11200
    assert luka["game"] == "DAL@BOS" and luka["opp"] == "BOS" and luka["name_norm"] == "luka doncic"
    assert [g["game"] for g in dk["games"]] == ["DAL@BOS", "MEM@NYK"]
    assert dk["lock_time"].hour == 19 and str(dk["date"]) == "2026-11-02"


def test_rg_uses_fpts_not_prizepicks_and_dash_is_no_flag():
    rg = parse_rg_projections((FX / "rg_sample.csv").read_text())
    luka = rg[0]
    assert luka["proj_rg"] == 57.5           # not the pp_fpts column
    assert luka["rg_inj"] == ""              # "-" means no flag
    assert rg[1]["rg_inj"] == "Q"


def test_rg_refuses_without_fpts():
    with pytest.raises(UploadError, match="PrizePicks"):
        map_rg_headers(["name", "salary", "PrizePicks FPTS"])


def test_merge_by_normalized_name_dk_salary_wins():
    dk = parse_dk_salaries((FX / "dk_salaries_sample.csv").read_text())
    rg = parse_rg_projections((FX / "rg_sample.csv").read_text())
    m = merge(dk, rg)
    names = {p["name"]: p for p in m["players"]}
    assert set(names) == {"Luka Dončić", "Jaren Jackson Jr.", "Ja'Kobe Walter"}   # accents/Jr./apostrophe
    assert names["Jaren Jackson Jr."]["salary"] == 7600                           # DK authoritative
    assert m["salary_mismatch"] == [{"name": "Jaren Jackson Jr.", "dk": 7600, "rg": 7700}]
    assert m["unmatched_rg"] == ["Somebody Else"] and m["unmatched_dk"] == []


def test_dk_unreadable_game_info_fails_loudly():
    bad = ("Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n"
           "PG,X (1),X,1,PG/UTIL,5000,Postponed,BOS,10\n")
    with pytest.raises(UploadError, match="Game Info"):
        parse_dk_salaries(bad)
