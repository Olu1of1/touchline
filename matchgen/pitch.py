"""Pitch geometry, zones and the shot xG model.

Coordinate convention (used everywhere in the output data):
  * Pitch is 105 m x 68 m.
  * Every event's x/y is given from the perspective of the team performing it,
    attacking left-to-right: x=0 is that team's own goal line, x=105 the
    opponent's goal line.
  * y=0 is the acting team's RIGHT touchline, y=68 its LEFT touchline
    (as seen when attacking towards x=105). Goal centre is y=34.
"""

import math

L = 105.0
W = 68.0
GOAL_Y = 34.0
GOAL_HALF = 3.66
BOX_X = L - 16.5          # 88.5
BOX_Y0 = GOAL_Y - 20.16   # 13.84
BOX_Y1 = GOAL_Y + 20.16   # 54.16
SIX_X = L - 5.5
PEN_SPOT = (L - 11.0, GOAL_Y)

THIRDS = ("defensive", "middle", "attacking")
CHANNELS = ("right", "centre", "left")


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def reflect(v, lo, hi):
    """Bounce a value back inside [lo, hi] instead of piling it up on the boundary."""
    if v < lo:
        v = lo + (lo - v)
    elif v > hi:
        v = hi - (v - hi)
    return clamp(v, lo, hi)


def mirror(x, y):
    """Convert a location to the other team's perspective."""
    return L - x, W - y


def in_box(x, y):
    return x >= BOX_X and BOX_Y0 <= y <= BOX_Y1


def third(x):
    return THIRDS[0] if x < 35 else THIRDS[1] if x < 70 else THIRDS[2]


def channel(y):
    return CHANNELS[0] if y < 22.67 else CHANNELS[1] if y < 45.33 else CHANNELS[2]


def zone_id(x, y):
    """18 zones: 6 bands along the pitch x 3 channels. 0 = own right corner, 17 = attacking left."""
    band = min(5, int(x // 17.5))
    ch = 0 if y < 22.67 else 1 if y < 45.33 else 2
    return band * 3 + ch


def goal_distance(x, y):
    return math.hypot(L - x, GOAL_Y - y)


def goal_angle(x, y):
    """Angle (radians) subtended by the goal mouth at (x, y)."""
    dx = max(L - x, 0.05)
    a1 = math.atan2(GOAL_Y + GOAL_HALF - y, dx)
    a2 = math.atan2(GOAL_Y - GOAL_HALF - y, dx)
    return abs(a1 - a2)


# Logistic xG model on distance and angle (open play, foot).
XG_B0 = -1.78
XG_DIST = -0.115
XG_ANGLE = 1.60
XG_HEADER = -0.95
XG_ONE_ON_ONE = 0.80


def shot_xg(x, y, header=False, one_on_one=False, noise=0.0):
    d = goal_distance(x, y)
    a = goal_angle(x, y)
    z = XG_B0 + XG_DIST * d + XG_ANGLE * a + noise
    if header:
        z += XG_HEADER
    if one_on_one:
        z += XG_ONE_ON_ONE
    p = 1.0 / (1.0 + math.exp(-z))
    return clamp(p, 0.005, 0.95)


def free_kick_xg(x, y):
    d = goal_distance(x, y)
    return clamp(0.11 - 0.0028 * (d - 17.0), 0.02, 0.11)
