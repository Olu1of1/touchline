"""League-level metrics computed from simulated matches, comparable with config.TARGETS."""

import math
from collections import defaultdict

from . import config as C


def league_metrics(matches):
    n = len(matches)
    hg = [m["home_goals"] for m in matches]
    ag = [m["away_goals"] for m in matches]
    hx = [m["home_xg"] for m in matches]
    ax = [m["away_xg"] for m in matches]
    return {
        "matches": n,
        "home_xg": sum(hx) / n,
        "away_xg": sum(ax) / n,
        "goals_per_game": (sum(hg) + sum(ag)) / n,
        "home_win": sum(m["result"] == "H" for m in matches) / n,
        "draw": sum(m["result"] == "D" for m in matches) / n,
        "away_win": sum(m["result"] == "A" for m in matches) / n,
        "btts": sum(h > 0 and a > 0 for h, a in zip(hg, ag)) / n,
        "over_2_5": sum(h + a > 2 for h, a in zip(hg, ag)) / n,
        "goals_per_xg": (sum(hg) + sum(ag)) / max(sum(hx) + sum(ax), 1e-9),
    }


def dispersion_by_xg(matches, edges=(0.0, 0.6, 1.0, 1.4, 1.9, 2.6, 9.9)):
    """Goal var/mean for team-matches binned by that team's xG, vs the reference Binomial(2xG+1) model."""
    bins = defaultdict(list)
    for m in matches:
        for g, x in ((m["home_goals"], m["home_xg"]), (m["away_goals"], m["away_xg"])):
            for lo, hi in zip(edges[:-1], edges[1:]):
                if lo <= x < hi:
                    bins[(lo, hi)].append((g, x))
                    break
    rows = []
    for (lo, hi), vals in sorted(bins.items()):
        if len(vals) < 30:
            continue
        gs = [v[0] for v in vals]
        xs = [v[1] for v in vals]
        mg = sum(gs) / len(gs)
        vg = sum((g - mg) ** 2 for g in gs) / (len(gs) - 1)
        # Reference model: mix of Binomial(2x+1, scaled x/n) over the bin's xGs -> mean of conditional variances
        # plus the variance of conditional means.
        means, cvars = [], []
        for x in xs:
            lam = x * C.XG_TO_GOAL_SCALE
            nn = C.BINOMIAL_N_SLOPE * x + C.BINOMIAL_N_INTERCEPT
            p = min(lam / nn, 0.999)
            means.append(nn * p)
            cvars.append(nn * p * (1 - p))
        mm = sum(means) / len(means)
        ref_var = sum(cvars) / len(cvars) + sum((u - mm) ** 2 for u in means) / len(means)
        rows.append({"xg_bin": f"{lo:.1f}-{hi:.1f}", "team_matches": len(vals), "mean_xg": sum(xs) / len(xs),
                     "mean_goals": mg, "var_over_mean": vg / mg if mg > 0 else float("nan"),
                     "reference_var_over_mean": ref_var / mm if mm > 0 else float("nan"),
                     "poisson_var_over_mean": 1.0})
    return rows


def scoreline_table(matches, top=12):
    from collections import Counter
    c = Counter(f"{m['home_goals']}-{m['away_goals']}" for m in matches)
    n = len(matches)
    return [(s, k / n) for s, k in c.most_common(top)]


def model_scoreline_probs(matches, max_goals=10):
    """Average predicted probability of each scoreline under the reference model
    (Binomial(2xG+1) margins + Dixon-Coles rho = -0.06), fed with each match's simulated xG."""
    rho = -0.06
    acc = defaultdict(float)
    for m in matches:
        def pmf(x):
            lam = x * C.XG_TO_GOAL_SCALE
            nn = C.BINOMIAL_N_SLOPE * x + C.BINOMIAL_N_INTERCEPT
            p = min(lam / nn, 0.999)
            # non-integer n: use the continuous generalisation via gamma functions
            out = []
            for k in range(max_goals + 1):
                if k > nn + 1e-9:
                    out.append(0.0)
                    continue
                lc = math.lgamma(nn + 1) - math.lgamma(k + 1) - math.lgamma(nn - k + 1)
                out.append(math.exp(lc + k * math.log(max(p, 1e-12)) + (nn - k) * math.log(max(1 - p, 1e-12))))
            s = sum(out)
            return [o / s for o in out]
        ph, pa = pmf(m["home_xg"]), pmf(m["away_xg"])
        lh, la = m["home_xg"] * C.XG_TO_GOAL_SCALE, m["away_xg"] * C.XG_TO_GOAL_SCALE
        tot = 0.0
        grid = {}
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                tau = 1.0
                if i == 0 and j == 0:
                    tau = 1 - lh * la * rho
                elif i == 0 and j == 1:
                    tau = 1 + lh * rho
                elif i == 1 and j == 0:
                    tau = 1 + la * rho
                elif i == 1 and j == 1:
                    tau = 1 - rho
                v = ph[i] * pa[j] * max(tau, 0.0)
                grid[(i, j)] = v
                tot += v
        for k, v in grid.items():
            acc[k] += v / tot
    n = len(matches)
    return {f"{i}-{j}": v / n for (i, j), v in acc.items()}
