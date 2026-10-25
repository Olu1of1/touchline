"""Calibration targets and tunable engine constants.

TARGETS are league-level rates for a modern top-flight English league, taken
from summary parameters fitted on historical xG data (2014-2025). They are the
yardstick the generator is tuned against; no match-level records are used or
redistributed.
"""

TARGETS = {
    "home_xg": 1.58,
    "away_xg": 1.27,
    "goals_per_game": 2.80,
    "home_win": 0.444,
    "draw": 0.238,
    "away_win": 0.318,
    "btts": 0.524,
    "over_2_5": 0.538,
}

# Goals relative to xG (xG overstated goals by 1.74% in the reference data).
XG_TO_GOAL_SCALE = 0.9826

# Reference goal-count model: goals ~ Binomial(n, xg/n) with n = 2*xg + 1.
# Implies var/mean = 1 - xg/n, i.e. goals are under-dispersed vs Poisson.
BINOMIAL_N_SLOPE = 2.0
BINOMIAL_N_INTERCEPT = 1.0

# ---------------------------------------------------------------------------
# Engine knobs set by calibrate.py (see VALIDATION.md for the fit).
# ---------------------------------------------------------------------------
CALIBRATED = {
    "chance_rate": 0.0640,      # base hazard of a final-third action creating a shot
    "home_factor": 1.168,       # multiplier on home attack (and 1/x on away attack)
    "strength_spread": 0.87,    # exponent applied to club attack/defence ratings
}

# Game-state effects (trailing teams push, leading teams sit deeper).
GAME_STATE = {
    "trailing_boost": 0.06,     # +6% chance rate per goal behind (max 2), scaled by match time
    "leading_drag": 0.16,       # -16% chance rate per goal ahead (max 2), scaled by match time
}

RED_CARD = {"attack_mult": 0.80, "defence_mult": 1.20}

# Penalties come from two routes: a created chance ending in a foul in the box (PENALTY_SHARE),
# and an open-play foul committed inside the box (FOUL_RATE x BOX_FOUL_FACTOR).
PENALTY_SHARE = 0.0065
BOX_FOUL_FACTOR = 0.08
PENALTY_XG = 0.76

# Fouls and cards
FOUL_RATE = 0.0180          # per open-play action, before style/zone adjustment
YELLOW_PER_FOUL = 0.18
STRAIGHT_RED_PER_FOUL = 0.0022
