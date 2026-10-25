"""Invariant tests for the generator. Run: python -m pytest -q"""

import random

import pytest

from matchgen.engine import Side, simulate
from matchgen.pitch import L, W, shot_xg
from matchgen.season import build_world, pick_team, round_robin, build_fixtures


@pytest.fixture(scope="module")
def world():
    clubs, players, refs = build_world(2042)
    return clubs, players, refs


def _match(world, home, away, seed):
    clubs, players, _ = world
    cb = {c["club_id"]: c for c in clubs}
    pl = {p["player_id"]: p for p in players}
    rng = random.Random(seed)
    sides = []
    for cid, is_home in ((home, True), (away, False)):
        squad = [p for p in players if p["club_id"] == cid]
        xi, bench = pick_team(cb[cid], squad, set(), rng)
        sides.append(Side(cb[cid], xi, bench, pl, is_home, None or {"home_factor": 1.18}))
    return simulate("TEST", sides[0], sides[1], seed)


def test_world_shape(world):
    clubs, players, refs = world
    assert len(clubs) == 20
    assert len(players) == 500
    assert len({p["name"] for p in players}) == 500          # unique names
    assert all(sum(p["club_id"] == c["club_id"] for p in players) == 25 for c in clubs)


def test_fixtures_double_round_robin(world):
    clubs, _, refs = world
    fx = build_fixtures(clubs, refs, 2042)
    assert len(fx) == 380
    pairs = {(f["home_id"], f["away_id"]) for f in fx}
    assert len(pairs) == 380                                  # every ordered pair exactly once
    for mw in range(1, 39):
        teams = [t for f in fx if f["matchweek"] == mw for t in (f["home_id"], f["away_id"])]
        assert len(teams) == 20 and len(set(teams)) == 20      # each club once per matchweek


def test_score_matches_goal_events(world):
    for seed in range(5):
        sim = _match(world, "COR", "FER", seed)
        goals = [e for e in sim.events if e["type"] == "shot" and e["outcome"] == "goal"]
        assert sum(e["team_id"] == "COR" for e in goals) == sim.h.goals
        assert sum(e["team_id"] == "FER" for e in goals) == sim.a.goals
        assert sim.events[-1]["outcome"] == f"{sim.h.goals}-{sim.a.goals}"


def test_coordinates_in_bounds_and_clock_monotonic(world):
    sim = _match(world, "RAV", "DUN", 11)
    last = -1.0
    for e in sim.events:
        for k, hi in (("x", L), ("end_x", L), ("y", W), ("end_y", W)):
            if e[k] is not None:
                assert 0.0 <= e[k] <= hi
        assert e["clock_s"] >= last
        last = e["clock_s"]


def test_deterministic(world):
    a = _match(world, "NGH", "KES", 42)
    b = _match(world, "NGH", "KES", 42)
    assert [e["type"] for e in a.events] == [e["type"] for e in b.events]
    assert (a.h.goals, a.a.goals) == (b.h.goals, b.a.goals)


def test_xg_model_shape():
    assert shot_xg(99.5, 34) > shot_xg(94, 34) > shot_xg(85, 34) > shot_xg(75, 34)   # closer is better
    assert shot_xg(94, 34) > shot_xg(94, 12)                                       # central beats tight angle
    assert shot_xg(96, 34, header=True) < shot_xg(96, 34)                           # headers are harder
    assert 0.0 < shot_xg(75, 34) < 0.06


def test_eleven_or_fewer_on_pitch(world):
    sim = _match(world, "SAL", "IRO", 3)
    for side in (sim.h, sim.a):
        assert 7 <= len(side.on) <= 11
        assert len(side.on) == 11 - side.reds
        assert side.subs <= 5


def test_ball_continuity(world):
    """After a completed pass or a carry, the same team's next action starts where the ball ended up."""
    import math
    jumps, n = 0, 0
    for seed in range(8):
        ev = _match(world, "COR", "NGH", seed).events
        for prev, cur in zip(ev, ev[1:]):
            if prev["type"] in ("pass", "carry") and prev["team_id"] == cur["team_id"] and cur["x"] is not None \
                    and (prev["type"] == "carry" or prev["outcome"] == "complete"):
                n += 1
                jumps += math.hypot(cur["x"] - prev["end_x"], cur["y"] - prev["end_y"]) > 5.0
    assert jumps / n < 0.01


def test_assist_geometry(world):
    """Cut-backs come back from near the byline; through balls are played forward into the shot."""
    for seed in range(8):
        for e in _match(world, "KES", "ASH", seed).events:
            if e["type"] == "pass" and e["tags"] and "key_pass" in e["tags"] and not e["set_piece"]:
                if e["pass_type"] == "cutback":
                    assert e["x"] > e["end_x"]
                if e["pass_type"] == "through_ball":
                    assert e["x"] < e["end_x"]
