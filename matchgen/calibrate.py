"""Fit the three free engine knobs to the league targets.

    chance_rate      -> total xG per game (2.85, i.e. 1.58 + 1.27)
    home_factor      -> home/away xG split (1.58 vs 1.27)
    strength_spread  -> result mix (H/D/A), the draw rate in particular

Everything else in VALIDATION.md (goals, BTTS, over 2.5, goal dispersion,
scorelines) is NOT fitted and so is a genuine out-of-fit check.

Usage:  python -m matchgen.calibrate --seasons 6
"""

import argparse
import json
from multiprocessing import Pool

from . import config as C
from .metrics import league_metrics
from .season import run_season

CAL_SEEDS = list(range(9001, 9100))


def _one(args):
    seed, params = args
    s = run_season(seed, params=params, keep_events=False)
    return s["matches"]


def evaluate(params, n_seasons, procs=2):
    with Pool(procs) as pool:
        out = pool.map(_one, [(sd, params) for sd in CAL_SEEDS[:n_seasons]])
    matches = [m for season in out for m in season]
    return league_metrics(matches), matches


def fit(n_seasons=4, rounds=4, verbose=True):
    T = C.TARGETS
    params = dict(C.CALIBRATED)
    tgt_total = T["home_xg"] + T["away_xg"]
    tgt_ratio = T["home_xg"] / T["away_xg"]
    for r in range(rounds):
        m, _ = evaluate(params, n_seasons)
        if verbose:
            print(f"round {r}: {json.dumps({k: round(v, 4) for k, v in params.items()})}")
            print("   ", {k: round(v, 3) for k, v in m.items()})
        total = m["home_xg"] + m["away_xg"]
        ratio = m["home_xg"] / m["away_xg"]
        params["chance_rate"] *= (tgt_total / total) ** 1.1
        params["home_factor"] *= (tgt_ratio / ratio) ** 0.5
        # draws too frequent -> widen gaps between sides; too rare -> narrow them
        params["strength_spread"] = max(0.4, params["strength_spread"] * (1 + 2.5 * (m["draw"] - T["draw"])))
    return params


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=4)
    a = ap.parse_args()
    p = fit(a.seasons, a.rounds)
    print("fitted:", json.dumps({k: round(v, 4) for k, v in p.items()}))
