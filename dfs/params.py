"""Starting parameter set. These were tuned from graded results; the live values
are stored append-only in the `params` table. Only the learner (measured bias)
or an explicit human decision may append a new version."""
import copy

DEFAULT_PARAMS = {
    "signals": {
        "vegas_baseline_total": 232.0,
        "pace_exponent": 0.5,
        "blowout_ml": 400,            # |fav_ml| >= this -> blowout game
        "blowout_starter_minutes": 27,  # minutes >= this -> blowout starter
        "blowout_starter_mult": 0.965,
        "blowout_bench_mult": 1.03,
        "close_ml": 150,              # |fav_ml| <= this -> close game
        "close_game_mult": 1.02,
        "b2b_mult": 0.9751,
        "questionable_default_mult": 0.88,
        "contingent_gpp_mult": 0.93,
        # Recent form: how far a projection may sit above the player's own recent DK points per
        # minute before it is pulled back. Judgement, not measurement - there are no graded slates
        # behind these yet, so they are capped conservatively and are the learner's first target.
        # The boost side is tighter than the cut side because RotoGrinders already chases hot
        # play, so the upside is the side more likely to double-count.
        "form_lookback_games": 10,
        "form_tolerance": 0.15,
        "form_max_cut": 0.85,
        "form_max_boost": 1.08,
        "form_min_games": 4,
        "form_min_minutes": 60,
        "form_max_staleness_days": 14,
    },
    "pool": {
        "eligible_min_salary": 5500,
        "eligible_min_minutes": 24,
    },
    "value_board": {
        "max_salary": 5500,      # sub-$5,500
        "min_minutes": 12,
        "starter_box": True,     # consensus starter
        "minutes_box": 24,       # projected minutes >= 24
        "value_box": 5.0,        # projected value >= 5.0
    },
    "GPP": {
        "ceil_weight": 0.40,
        "own_penalty": 0.12,
        "max_own_sum": 150,
        "max_from_team": 3,
        "min_uniques": 3,
        "min_salary": 49300,
        "max_per_game": 7,
    },
    "CASH": {
        "max_from_team": 4,
        "min_uniques": 2,
        "min_salary": 49500,
        "min_minutes": 24,
        "exclude_questionable": True,
        "max_per_game": 7,
    },
}

# BINDING: lessons from graded backtests. The learner must never move these on
# its own; a change requires a backtest showing a dominance result and a human
# appending a params row with the evidence in `reason`.
FROZEN_KEYS = {
    ("GPP", "own_penalty"),
    ("GPP", "max_own_sum"),
    ("GPP", "max_from_team"),
    ("CASH", "max_from_team"),
    ("pool", "eligible_min_salary"),
    ("pool", "eligible_min_minutes"),
}


def default_params() -> dict:
    return copy.deepcopy(DEFAULT_PARAMS)
