import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

POSITIONS = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SG/SF", "SF/PF", "PF/C", "G", "F"]


def make_slate(n_games=4, per_team=9, seed=0):
    rng = random.Random(seed)
    players, k = [], 0
    for g in range(n_games):
        home, away = f"H{g}", f"A{g}"
        for team, opp in ((home, away), (away, home)):
            for _ in range(per_team):
                pos = (lambda x: {"G": "PG/SG", "F": "SF/PF"}.get(x, x))(rng.choice(POSITIONS))
                sal = rng.randrange(3000, 11500, 100)
                proj = max(5.0, sal / 1000 * rng.uniform(3.5, 6.0))
                k += 1
                players.append({
                    "key": k, "name": f"P{k}", "team": team, "opp": opp, "game": f"{away}@{home}",
                    "pos": pos, "salary": sal, "minutes": rng.uniform(12, 38),
                    "proj_adj": round(proj, 2), "floor": round(proj * 0.7, 2),
                    "ceil": round(proj * 1.4, 2), "own_pct": round(rng.uniform(0, 40), 1),
                    "value": round(proj / (sal / 1000), 2),
                })
    return players
