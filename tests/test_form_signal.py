"""The recent-form multiplier. Pure: no database, no clock, no network."""
import datetime

from dfs.params import default_params
from dfs.signals import form_multiplier

SP = default_params()["signals"]
SLATE = datetime.date(2026, 11, 6)


def mk(proj=32.0, minutes=32.0, rate=1.0, games=10, mins_total=300.0, last_days=1, **kw):
    """A player with full information: the lineup pages loaded and his role is stable. Those are
    the conditions under which the signal is allowed to speak at all."""
    return {"proj_rg": proj, "minutes": minutes, "recent_rate": rate, "recent_games": games,
            "recent_minutes": mins_total,
            "recent_last_date": SLATE - datetime.timedelta(days=last_days),
            "slate_date": SLATE, "role_known": True, "role_change": False, **kw}


def test_a_projection_inside_tolerance_is_untouched():
    """The common case must be a no-op."""
    assert form_multiplier(mk(proj=33.0, minutes=32.0, rate=1.0), SP) == 1.0


def test_a_projection_above_the_recent_rate_is_pulled_to_the_edge_of_plausible():
    """implied 1.25 vs a rate of 1.0 is a 25% jump: past tolerance, but not far enough to hit the
    cap, so the uncapped formula applies and lands the implied rate exactly at tolerance."""
    m = form_multiplier(mk(proj=40.0, minutes=32.0, rate=1.0), SP)
    assert m < 1.0
    assert abs(m - (1 + SP["form_tolerance"]) / 1.25) < 1e-9
    assert abs((40.0 * m / 32.0) - (1 + SP["form_tolerance"])) < 1e-9, "pulled to the edge, not to the rate"


def test_the_cut_is_capped():
    """One signal must never halve a player."""
    assert form_multiplier(mk(proj=200.0, minutes=32.0, rate=1.0), SP) == SP["form_max_cut"]


def test_a_projection_far_below_the_recent_rate_nudges_up_and_is_capped_tighter():
    assert form_multiplier(mk(proj=8.0, minutes=32.0, rate=1.0), SP) == SP["form_max_boost"]
    assert SP["form_max_boost"] < 1 / SP["form_max_cut"], "the boost side is deliberately tighter"


def test_too_few_games_says_nothing():
    assert form_multiplier(mk(proj=60.0, games=SP["form_min_games"] - 1), SP) == 1.0


def test_the_minutes_floor_is_a_boundary_not_a_cliff():
    """Exactly at the floor the signal speaks; a minute under it says nothing."""
    floor = SP["form_min_minutes_per_game"]
    assert form_multiplier(mk(proj=40.0, minutes=32.0, rate=1.0, games=10,
                              mins_total=floor * 10), SP) < 1.0
    assert form_multiplier(mk(proj=40.0, minutes=32.0, rate=1.0, games=10,
                              mins_total=(floor - 1) * 10), SP) == 1.0


def test_a_contingent_player_is_never_adjusted():
    """His role is changing because someone ahead of him is out, so the recent rate describes a
    job he no longer has. Without this the signal would punish exactly the cheap plays the pool
    rule exists to surface."""
    assert form_multiplier(mk(proj=60.0, rate=0.5, contingent=True), SP) == 1.0


def test_a_stale_last_game_says_nothing():
    """Back from injury: the recent rate describes a different player."""
    assert form_multiplier(mk(proj=60.0, last_days=20), SP) == 1.0


def test_zero_minutes_or_zero_rate_cannot_divide_by_zero():
    assert form_multiplier(mk(proj=40.0, minutes=0.0), SP) == 1.0
    assert form_multiplier(mk(proj=40.0, rate=0.0), SP) == 1.0


def test_missing_history_says_nothing():
    assert form_multiplier({"proj_rg": 40.0, "minutes": 30.0}, SP) == 1.0


def test_form_is_a_stored_signal_column():
    from dfs.signals import SIGNAL_COLUMNS
    assert "m_form" in SIGNAL_COLUMNS, "stored per build so grading can measure it"


# ---------------------------------------------------------------- role change (review Critical 1)

def test_a_promoted_backup_is_not_cut_when_a_positional_teammate_is_out():
    """The case the exclusion exists for, with the reviewer's numbers: a $6,200 backup who has
    been playing 14 minutes a night at 0.75 DK/min, whose starting centre is ruled OUT, and whom
    RotoGrinders correctly re-projects at 34 points in 32 minutes.

    `contingent` does not cover him - it requires salary < $5,500 AND an unresolved tag - so
    without `role_change` the signal cuts a correct projection by 15% on the night the edge is
    largest."""
    p = mk(proj=34.0, minutes=32.0, rate=0.75, games=10, mins_total=300.0, role_change=True)
    assert form_multiplier(p, SP) == 1.0
    # and the reviewer's exact player, 14 minutes a night, is excluded by the minutes floor too
    assert form_multiplier(mk(proj=34.0, minutes=32.0, rate=0.75, games=10,
                              mins_total=140.0, role_change=False), SP) == 1.0


def test_a_rotation_player_is_still_cut_when_no_teammate_is_out():
    """Without a role change the signal must still do its job, or the fix is just a switch-off.
    This player gets 30 minutes a night, so he clears the minutes floor the promoted backup in
    the test above does not - the two exclusions are independent."""
    p = mk(proj=34.0, minutes=32.0, rate=0.75, games=10, mins_total=300.0, role_change=False)
    assert form_multiplier(p, SP) < 1.0


def test_the_signal_stands_down_when_the_lineup_sources_did_not_load():
    """Review Critical 2: contingent and role_change both come from the lineup pages. When both
    are down every player looks role-stable, so the exclusion silently disappears on exactly the
    nights the system already knows it is flying blind."""
    p = mk(proj=60.0, rate=0.5, role_known=False)
    assert form_multiplier(p, SP) == 1.0


# ---------------------------------------------------------------- minutes floor (review Important 6)

def test_a_garbage_time_rate_is_excluded_by_the_per_game_minutes_floor():
    """Ten 8-minute blowout appearances total 80 minutes, which cleared the old 60-minute
    lookback total. Per game it is 8 minutes, which is not a rotation role."""
    p = mk(proj=10.0, minutes=14.0, rate=1.0, games=10, mins_total=80.0)
    assert form_multiplier(p, SP) == 1.0


def test_a_real_rotation_player_still_clears_the_minutes_floor():
    p = mk(proj=40.0, minutes=32.0, rate=1.0, games=10, mins_total=300.0)
    assert form_multiplier(p, SP) < 1.0
