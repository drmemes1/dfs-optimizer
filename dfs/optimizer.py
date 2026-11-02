"""DraftKings NBA Classic lineup optimizer: MILP over players x slots, solved with
scipy.optimize.milp (HiGHS).

Rules: 8 slots PG, SG, SF, PF, C, G (PG|SG), F (SF|PF), UTIL (any). Salary cap 50,000.
At most 7 players from any one game, which enforces the 2+ games rule.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
SALARY_CAP = 50000
_SLOT_ELIG = {
    "PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
    "G": {"PG", "SG"}, "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"},
}


def eligible_slots(pos: str) -> list[str]:
    """Slots a DK position string like 'PF/C' can fill."""
    positions = {x.strip().upper() for x in (pos or "").split("/") if x.strip()}
    return [s for s in SLOTS if positions & _SLOT_ELIG[s]]


@dataclass
class Settings:
    mode: str = "GPP"                 # GPP | CASH | OPTIMAL
    ceil_weight: float = 0.40
    own_penalty: float = 0.12
    max_own_sum: float | None = None
    max_from_team: int = 3
    max_per_game: int = 7
    min_uniques: int = 3
    min_salary: int = 49300
    max_salary: int = SALARY_CAP
    locks: list = field(default_factory=list)      # player keys
    excludes: list = field(default_factory=list)
    max_exposure: float | None = None  # fraction of n lineups, e.g. 0.6

    @classmethod
    def from_params(cls, params: dict, mode: str, **overrides) -> "Settings":
        mp = params.get(mode, {}) if mode in ("GPP", "CASH") else {}
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in mp.items() if k in known}
        kw.update(overrides)
        return cls(mode=mode, **kw)


def objective_score(p: dict, s: Settings) -> float:
    proj = float(p.get("proj_adj") or 0)
    if s.mode == "GPP":
        return proj + s.ceil_weight * (float(p.get("ceil") or proj) - proj) \
            - s.own_penalty * float(p.get("own_pct") or 0)
    if s.mode == "CASH":
        return 0.7 * proj + 0.3 * float(p.get("floor") or proj)
    if s.mode == "ACTUAL":  # hindsight-perfect lineup
        return float(p.get("dk_pts") or 0)
    return proj  # OPTIMAL: pure projection


def _key(p):
    return p.get("key") or p.get("player_id") or p.get("dk_player_id") or p["name"]


def solve_one(players: list[dict], s: Settings, prior: list[list] = (), banned=frozenset()):
    """Solve one lineup. Returns list of (player, slot) or None if infeasible."""
    excl = set(s.excludes) | set(banned)
    pool = [p for p in players if _key(p) not in excl and eligible_slots(p.get("pos"))]
    pairs = [(i, sl) for i, p in enumerate(pool) for sl in eligible_slots(p["pos"])]
    if not pairs:
        return None
    n = len(pairs)
    c = np.array([-objective_score(pool[i], s) for i, _ in pairs])

    rows, lo, hi = [], [], []

    def add(coefs: dict, l, h):
        rows.append(coefs); lo.append(l); hi.append(h)

    by_slot, by_player = {}, {}
    for j, (i, sl) in enumerate(pairs):
        by_slot.setdefault(sl, []).append(j)
        by_player.setdefault(i, []).append(j)
    for sl in SLOTS:
        if sl not in by_slot:
            return None
        add({j: 1 for j in by_slot[sl]}, 1, 1)               # one player per slot
    for i, js in by_player.items():                          # each player at most once
        locked = _key(pool[i]) in set(s.locks)
        add({j: 1 for j in js}, 1 if locked else 0, 1)
    add({j: pool[i]["salary"] for j, (i, _) in enumerate(pairs)}, s.min_salary, s.max_salary)
    for attr, cap in (("team", s.max_from_team), ("game", s.max_per_game)):
        groups = {}
        for j, (i, _) in enumerate(pairs):
            groups.setdefault(pool[i].get(attr), []).append(j)
        for js in groups.values():
            add({j: 1 for j in js}, 0, cap)
    if s.max_own_sum is not None:
        add({j: float(pool[i].get("own_pct") or 0) for j, (i, _) in enumerate(pairs)}, 0, s.max_own_sum)
    for lineup_keys in prior:                                # multi-lineup uniqueness
        lk = set(lineup_keys)
        js = [j for j, (i, _) in enumerate(pairs) if _key(pool[i]) in lk]
        if js:
            add({j: 1 for j in js}, 0, len(SLOTS) - s.min_uniques)

    A = lil_matrix((len(rows), n))
    for r, coefs in enumerate(rows):
        for j, v in coefs.items():
            A[r, j] = v
    res = milp(c, constraints=LinearConstraint(A.tocsr(), lo, hi),
               integrality=np.ones(n), bounds=Bounds(0, 1),
               options={"time_limit": 30})
    if res.x is None or res.status not in (0,):
        return None
    chosen = [pairs[j] for j in range(n) if res.x[j] > 0.5]
    order = {sl: k for k, sl in enumerate(SLOTS)}
    return sorted([(pool[i], sl) for i, sl in chosen], key=lambda t: order[t[1]])


def summarize(lineup, s: Settings) -> dict:
    ps = [p for p, _ in lineup]
    return {
        "mode": s.mode,
        "salary": sum(p["salary"] for p in ps),
        "proj": round(sum(float(p.get("proj_adj") or 0) for p in ps), 2),
        "ceil": round(sum(float(p.get("ceil") or 0) for p in ps), 2),
        "floor": round(sum(float(p.get("floor") or 0) for p in ps), 2),
        "own_sum": round(sum(float(p.get("own_pct") or 0) for p in ps), 2),
        "objective": round(sum(objective_score(p, s) for p in ps), 3),
        "players": [{"slot": sl, "key": _key(p), "name": p["name"], "pos": p["pos"],
                     "team": p.get("team"), "salary": p["salary"],
                     "proj": p.get("proj_adj"), "own": p.get("own_pct"),
                     "draftable_id": p.get("draftable_id")} for p, sl in lineup],
    }


def build_lineups(players: list[dict], s: Settings, n: int = 1) -> list[dict]:
    """Generate up to n lineups, each differing from every earlier one by >= min_uniques."""
    out, prior, counts = [], [], {}
    for _ in range(n):
        banned = set()
        if s.max_exposure is not None and n > 1:
            cap = max(1, math.floor(s.max_exposure * n))
            banned = {k for k, c in counts.items() if c >= cap and k not in s.locks}
        lu = solve_one(players, s, prior, banned)
        if lu is None:
            break
        keys = [_key(p) for p, _ in lu]
        prior.append(keys)
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
        out.append(summarize(lu, s))
    return out
