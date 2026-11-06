import json
import time
from datetime import date
from pathlib import Path

from dfs.sources import dk

FIX = Path(__file__).parent / "fixtures"
# Reconstructed from the live 2026-11-05 lobby (see the spec's findings table): a 7-game 7pm
# slate at $1,033,857 against a 2-game 9pm slate at $1,029,974, 0.38% apart.
NEAR_TIE = json.loads((FIX / "dk_lobby_near_tie_2026-11-05.json").read_text())
LIVE = json.loads((FIX / "dk_lobby_2026-11-05.json").read_text())


def test_picks_the_seven_game_seven_pm_slate_not_the_richer_two_game_late_one():
    """The two top groups were 0.38% apart in prize money and the runner-up was a 2-game 9pm
    slate. Prize pool alone cannot express 'main draw'."""
    got = dk.pick_main_slate(NEAR_TIE, date=date(2026, 11, 6))
    assert got is not None
    assert got["draft_group_id"] == 156184
    assert got["game_count"] == 7
    assert got["start"].astimezone(dk.ET).hour == 19


def test_does_not_return_another_days_slate():
    """2026-11-05's only Classic group is the 2-game 9pm one, which is not a main draw."""
    assert dk.pick_main_slate(NEAR_TIE, date=date(2026, 11, 5)) is None


def test_returns_none_when_no_group_starts_in_the_main_window():
    assert dk.pick_main_slate(NEAR_TIE, date=date(2030, 1, 1)) is None


def test_override_wins_over_ranking():
    groups = dk.classic_groups(NEAR_TIE)
    target = min(groups, key=lambda g: g["prize_pool"])
    got = dk.pick_main_slate(NEAR_TIE, date=date(2026, 11, 6),
                             override_group_id=target["draft_group_id"])
    assert got["draft_group_id"] == target["draft_group_id"]


def test_override_for_a_group_not_in_the_lobby_returns_none():
    assert dk.pick_main_slate(NEAR_TIE, date=date(2026, 11, 6), override_group_id=1) is None


def test_showdown_and_other_game_types_are_ignored():
    """The fixture carries a GameTypeId 96 group at 7pm with a $9M pool."""
    got = dk.pick_main_slate(NEAR_TIE, date=date(2026, 11, 6))
    assert got["draft_group_id"] != 156999


def test_more_games_beats_more_money_inside_the_window():
    lobby = {"DraftGroups": [
        {"DraftGroupId": 1, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
         "GameCount": 8},
        {"DraftGroupId": 2, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:30:00.0000000",
         "GameCount": 3},
    ], "Contests": [{"dg": 1, "po": 10}, {"dg": 2, "po": 5_000_000}]}
    assert dk.pick_main_slate(lobby, date=date(2026, 11, 6))["draft_group_id"] == 1


def test_prize_pool_breaks_a_tie_on_game_count():
    lobby = {"DraftGroups": [
        {"DraftGroupId": 1, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
         "GameCount": 5},
        {"DraftGroupId": 2, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:30:00.0000000",
         "GameCount": 5},
    ], "Contests": [{"dg": 1, "po": 100}, {"dg": 2, "po": 900}]}
    assert dk.pick_main_slate(lobby, date=date(2026, 11, 6))["draft_group_id"] == 2


def test_a_naive_start_time_is_read_as_eastern_not_as_the_servers_clock(monkeypatch):
    """StartDateEst has no offset. Vercel runs in UTC and this Mac runs in ET, so a naive time
    must be pinned to Eastern explicitly or the window check moves with the server."""
    lobby = {"DraftGroups": [
        {"DraftGroupId": 7, "GameTypeId": 70, "StartDateEst": "2026-11-06T19:00:00.0000000",
         "GameCount": 6}], "Contests": [{"dg": 7, "po": 500}]}
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        got = dk.pick_main_slate(lobby, date=date(2026, 11, 6))
        assert got is not None, "a 7pm ET start must be found even when the server clock is UTC"
        assert got["draft_group_id"] == 7
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_the_real_captured_lobby_yields_the_seven_game_seven_pm_slate():
    """Shape fidelity against an unedited DraftKings response."""
    got = dk.pick_main_slate(LIVE, date=date(2026, 11, 6))
    assert got is not None
    assert got["game_count"] == 7
    assert got["start"].astimezone(dk.ET).hour == 19
