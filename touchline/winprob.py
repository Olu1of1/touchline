"""In-play win probability.

Before kick-off each side's goal rate comes from season-to-date xG (attack vs
the opponent's defence), shrunk towards the league average when few games have
been played. During the match, the remaining goals for each side follow a
Poisson distribution over the time left, adjusted for red cards and the score.
Only information available at that moment is used.
"""

import math

LEAGUE_HOME_XG = 1.58
LEAGUE_AWAY_XG = 1.27
PRIOR_GAMES = 5.0
MATCH_SECONDS = 96 * 60          # typical length incl. stoppage time
RED_ATTACK = 0.80
RED_DEFENCE = 1.20


def pre_match_rates(home, away):
    """Expected goals for (home, away) over a full match."""
    avg = (LEAGUE_HOME_XG + LEAGUE_AWAY_XG) / 2

    def shrink(per_game, played):
        if per_game is None:
            return avg
        return (per_game * played + avg * PRIOR_GAMES) / (played + PRIOR_GAMES)

    h_att = shrink(home.xg_for_pg, home.played) / avg
    h_def = shrink(home.xg_against_pg, home.played) / avg
    a_att = shrink(away.xg_for_pg, away.played) / avg
    a_def = shrink(away.xg_against_pg, away.played) / avg
    return LEAGUE_HOME_XG * h_att * a_def, LEAGUE_AWAY_XG * a_att * h_def


def _pois(lam, kmax=12):
    p = [math.exp(-lam)]
    for k in range(1, kmax + 1):
        p.append(p[-1] * lam / k)
    return p


def probabilities(rate_h, rate_a, goals_h, goals_a, clock_s, reds_h=0, reds_a=0):
    """Return (P home win, P draw, P away win) given the state at clock_s."""
    left = max(0.0, 1.0 - clock_s / MATCH_SECONDS)
    lh = rate_h * left * (RED_ATTACK ** reds_h) * (RED_DEFENCE ** reds_a)
    la = rate_a * left * (RED_ATTACK ** reds_a) * (RED_DEFENCE ** reds_h)
    # score effects: the trailing side pushes, the leading side sits deeper
    diff = goals_h - goals_a
    if diff > 0:
        lh, la = lh * 0.88, la * 1.06
    elif diff < 0:
        lh, la = lh * 1.06, la * 0.88
    ph, pa = _pois(lh), _pois(la)
    win = draw = loss = 0.0
    for i, a in enumerate(ph):
        for j, b in enumerate(pa):
            fh, fa = goals_h + i, goals_a + j
            p = a * b
            if fh > fa:
                win += p
            elif fh == fa:
                draw += p
            else:
                loss += p
    s = win + draw + loss
    return win / s, draw / s, loss / s
