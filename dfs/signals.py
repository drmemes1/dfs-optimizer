"""Signal layer: multiplicative adjustments to the raw RotoGrinders projection.

proj_adj = proj_rg
  * (vegas_total / 232) ** pace_exponent
  * blowout_starter_mult  if |fav_ml| >= 400 and minutes >= 27
  * blowout_bench_mult    if |fav_ml| >= 400 and minutes <  27
  * close_game_mult       if |fav_ml| <= 150
  * b2b_mult              if team played yesterday
  * questionable multiplier (per-player override, else questionable_default_mult)
  * contingent_gpp_mult   if flagged contingent
Floor and ceiling get the same multiplier. value = proj_adj / (salary / 1000).
"""
import numpy as np

QUESTIONABLE = {"Q", "GTD", "D"}
OUT = {"O", "OUT", "INJ", "NA", "SUSP"}


def norm_status(status) -> str:
    s = (status or "").strip().upper()
    return "" if s in ("", "-", "NONE", "ACTIVE") else s  # RG uses "-" for no flag


def is_questionable(p) -> bool:
    return norm_status(p.get("inj_status")) in QUESTIONABLE


def is_out(p) -> bool:
    return norm_status(p.get("inj_status")) in OUT


def signal_multiplier(p: dict, sp: dict) -> tuple[float, list[str]]:
    """Return (multiplier, list of signal buckets that fired)."""
    mult, fired = 1.0, []
    total = p.get("vegas_total")
    if total:
        mult *= (float(total) / sp["vegas_baseline_total"]) ** sp["pace_exponent"]
    ml = p.get("fav_ml")
    minutes = float(p.get("minutes") or 0)
    if ml is not None:
        ml = abs(float(ml))
        if ml >= sp["blowout_ml"]:
            if minutes >= sp["blowout_starter_minutes"]:
                mult *= sp["blowout_starter_mult"]; fired.append("blowout_starter")
            else:
                mult *= sp["blowout_bench_mult"]; fired.append("blowout_bench")
        elif ml <= sp["close_ml"]:
            mult *= sp["close_game_mult"]; fired.append("close_game")
    if p.get("b2b"):
        mult *= sp["b2b_mult"]; fired.append("b2b")
    if is_questionable(p):
        q = p.get("questionable_mult")
        mult *= float(q) if q is not None else sp["questionable_default_mult"]
        fired.append("questionable")
    if p.get("contingent"):
        mult *= sp["contingent_gpp_mult"]; fired.append("contingent")
    return mult, fired


def apply_signals(players: list[dict], params: dict) -> list[dict]:
    """Adds proj_adj, floor, ceil, value, signals to each player (in place) and returns the list."""
    sp = params["signals"]
    for p in players:
        m, fired = signal_multiplier(p, sp)
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
