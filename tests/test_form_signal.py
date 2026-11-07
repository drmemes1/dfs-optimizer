"""The recent-form multiplier. Pure: no database, no clock, no network."""
import datetime

from dfs.params import default_params
from dfs.signals import form_multiplier

SP = default_params()["signals"]
SLATE = datetime.date(2026, 11, 6)


def mk(proj=32.0, minutes=32.0, rate=1.0, games=10, mins_total=300.0, last_days=1, **kw):
    return {"proj_rg": proj, "minutes": minutes, "recent_rate": rate, "recent_games": games,
            "recent_minutes": mins_total,
            "recent_last_date": SLATE - datetime.timedelta(days=last_days),
            "slate_date": SLATE, **kw}


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


def test_too_few_minutes_says_nothing():
    """A freak 20-point, 8-minute night is not a rate."""
    assert form_multiplier(mk(proj=5.0, mins_total=SP["form_min_minutes"] - 1, rate=3.0), SP) == 1.0


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
