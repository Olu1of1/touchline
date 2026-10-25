"""Pick the most representative season to publish.

A single season is noisy (draw rate alone can swing by +/-4 pp). Rather than
publish an unusual one by accident, we simulate candidate seeds and publish the
one whose result mix, goal rates and xG are closest to the reference. The rule
is fixed in code so the choice is reproducible, not hand-picked.

Usage: python -m matchgen.pick_seed --start 2026 --n 20
"""

import argparse
from multiprocessing import Pool

from . import config as C
from .metrics import league_metrics
from .season import run_season

KEYS = ["home_win", "draw", "away_win", "btts", "over_2_5"]


def score(m):
    T = C.TARGETS
    d = sum(((m[k] - T[k]) / 0.025) ** 2 for k in KEYS)
    d += ((m["goals_per_game"] - T["goals_per_game"]) / 0.12) ** 2
    d += ((m["home_xg"] - T["home_xg"]) / 0.08) ** 2 + ((m["away_xg"] - T["away_xg"]) / 0.08) ** 2
    return d


def _one(seed):
    return seed, league_metrics(run_season(seed, keep_events=False)["matches"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2026)
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    with Pool(2) as p:
        res = p.map(_one, range(a.start, a.start + a.n))
    res.sort(key=lambda r: score(r[1]))
    for seed, m in res[:5]:
        print(seed, round(score(m), 2), {k: round(m[k], 3) for k in KEYS + ["goals_per_game", "home_xg", "away_xg"]})
    print("best seed:", res[0][0])
