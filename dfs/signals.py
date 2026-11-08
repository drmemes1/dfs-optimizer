"""Signal layer: multiplicative adjustments to the raw RotoGrinders projection.

proj_adj = proj_rg
  * (vegas_total / 232) ** pace_exponent
  * blowout_starter_mult  if |fav_ml| >= 400 and minutes >= 27
  * blowout_bench_mult    if |fav_ml| >= 400 and minutes <  27
  * close_game_mult       if |fav_ml| <= 150
  * b2b_mult              if team played yesterday
  * questionable multiplier (per-player override, else questionable_default_mult)
  * contingent_gpp_mult   if flagged contingent
  * m_form                 pulls a projection back toward the player's recent DK points per
                           minute when it requires more than form_tolerance above it
Floor and ceiling get the same multiplier. value = proj_adj / (salary / 1000).
"""
import numpy as np

QUESTIONABLE = {"Q", "GTD", "D"}
OUT = {"O", "OUT", "INJ", "NA", "SUSP", "NOPROJ"}  # NOPROJ: dropped from the latest projections


def norm_status(status) -> str:
    s = (status or "").strip().upper()
    return "" if s in ("", "-", "NONE", "ACTIVE") else s  # RG uses "-" for no flag


def is_questionable(p) -> bool:
    return norm_status(p.get("inj_status")) in QUESTIONABLE


def is_out(p) -> bool:
    return norm_status(p.get("inj_status")) in OUT


SIGNAL_COLUMNS = ["m_pace", "m_blowout", "m_close", "m_b2b", "m_questionable", "m_contingent",
                  "m_form"]


def form_multiplier(p: dict, sp: dict) -> float:
    """Pull a projection toward the player's recent DK points per minute when it requires an
    implausible jump. Returns 1.0 - no adjustment - whenever the signal has nothing to say.

    This is a skepticism filter, not a hot-streak chase: RotoGrinders already weights recent play,
    so the new information is how far out on a limb a projection sits, not that a player is hot."""
    # Role: the recent rate describes the job a player had, so it says nothing about a job that
    # is changing. `contingent` only covers sub-$5,500 players whose teammate is *unresolved*, so
    # it misses the ordinary case - a starter ruled out and his backup promoted - which is
    # precisely when a jump is justified and a cut does the most damage.
    if p.get("contingent") or p.get("role_change"):
        return 1.0
    # Both of those flags come from the lineup pages. When neither page loaded, every player looks
    # role-stable, so the exclusion would silently disappear on the nights we already know we are
    # blind. Say nothing instead.
    if not p.get("role_known"):
        return 1.0
    rate = float(p.get("recent_rate") or 0)
    games = int(p.get("recent_games") or 0)
    rec_min = float(p.get("recent_minutes") or 0)
    minutes = float(p.get("minutes") or 0)
    proj = float(p.get("proj_rg") or 0)
    if rate <= 0 or minutes <= 0 or proj <= 0:
        return 1.0
    if games < sp["form_min_games"]:
        return 1.0
    if rec_min / games < sp["form_min_minutes_per_game"]:
        return 1.0
    last, slate = p.get("recent_last_date"), p.get("slate_date")
    if last and slate and (slate - last).days > sp["form_max_staleness_days"]:
        return 1.0                      # back from a long absence; the rate is a different player
    implied = proj / minutes
    jump = implied / rate - 1.0
    tol = sp["form_tolerance"]
    if jump > tol:
        return max(sp["form_max_cut"], (1 + tol) * rate / implied)
    if jump < -tol:
        return min(sp["form_max_boost"], (1 - tol) * rate / implied)
    return 1.0


def signal_components(p: dict, sp: dict) -> dict:
    """Each signal's multiplier on its own (1.0 when it did not fire)."""
    c = {k: 1.0 for k in SIGNAL_COLUMNS}
    total = p.get("vegas_total")
    if total:
        c["m_pace"] = (float(total) / sp["vegas_baseline_total"]) ** sp["pace_exponent"]
    ml = p.get("fav_ml")
    minutes = float(p.get("minutes") or 0)
    if ml is not None:
        ml = abs(float(ml))
        if ml >= sp["blowout_ml"]:
            c["m_blowout"] = sp["blowout_starter_mult"] if minutes >= sp["blowout_starter_minutes"] \
                else sp["blowout_bench_mult"]
        elif ml <= sp["close_ml"]:
            c["m_close"] = sp["close_game_mult"]
    if p.get("b2b"):
        c["m_b2b"] = sp["b2b_mult"]
    if is_questionable(p):
        q = p.get("questionable_mult")
        c["m_questionable"] = float(q) if q is not None else sp["questionable_default_mult"]
    if p.get("contingent"):
        c["m_contingent"] = sp["contingent_gpp_mult"]
    c["m_form"] = form_multiplier(p, sp)
    return c


def signal_multiplier(p: dict, sp: dict) -> tuple[float, list[str]]:
    """Return (combined multiplier, list of signal buckets that fired)."""
    c = signal_components(p, sp)
    mult = 1.0
    for v in c.values():
        mult *= v
    fired = []
    if c["m_blowout"] != 1.0:
        minutes = float(p.get("minutes") or 0)
        fired.append("blowout_starter" if minutes >= sp["blowout_starter_minutes"] else "blowout_bench")
    if c["m_close"] != 1.0:
        fired.append("close_game")
    if p.get("b2b"):
        fired.append("b2b")
    if is_questionable(p):
        fired.append("questionable")
    if p.get("contingent"):
        fired.append("contingent")
    if c["m_form"] != 1.0:
        fired.append("form_cut" if c["m_form"] < 1.0 else "form_boost")
    return mult, fired


def apply_signals(players: list[dict], params: dict) -> list[dict]:
    """Adds proj_adj, floor, ceil, value, signals to each player (in place) and returns the list."""
    sp = params["signals"]
    for p in players:
        m, fired = signal_multiplier(p, sp)
        for k, v in signal_components(p, sp).items():
            p[k] = round(v, 5)
        p["signal_mult"] = round(m, 5)
        p["signals"] = fired
        p["proj_adj"] = round(float(p.get("proj_rg") or 0) * m, 3)
        p["floor"] = round(float(p.get("floor_rg") or 0) * m, 3)
        p["ceil"] = round(float(p.get("ceil_rg") or 0) * m, 3)
        sal = float(p.get("salary") or 0)
        p["value"] = round(p["proj_adj"] / (sal / 1000), 3) if sal > 0 else 0.0
    return players


def estimate_missing_ownership(players: list[dict]) -> list[dict]:
    """RotoGrinders shows pOWN 0% for games whose ownership hasn't published.
    For any game where every player shows 0, estimate from the slate's value curve
    (least squares of own% on value and salary among published players)."""
    by_game: dict[str, list[dict]] = {}
    for p in players:
        by_game.setdefault(p.get("game") or "", []).append(p)
    unpublished = {g for g, ps in by_game.items() if all(not (q.get("own_pct") or 0) for q in ps)}
    if not unpublished:
        return players
    fit = [p for p in players if (p.get("game") or "") not in unpublished and (p.get("own_pct") or 0) > 0]
    if len(fit) >= 10:
        X = np.array([[1.0, p["value"], p["salary"] / 1000] for p in fit])
        y = np.array([p["own_pct"] for p in fit])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    else:
        coef = np.array([-12.0, 3.0, 0.8])  # crude prior: ~0% at value 3, ~20% at value 6 for $7K
    for g in unpublished:
        for p in by_game[g]:
            est = coef[0] + coef[1] * p["value"] + coef[2] * p["salary"] / 1000
            p["own_pct"] = round(float(min(60.0, max(0.1, est))), 2)
            p["own_estimated"] = True
    return players
