"""Season orchestration: fixtures, team selection, suspensions, simulation and output.

Usage:
    python -m matchgen.season --seed 2042 --out data
"""

import argparse
import datetime as dt
import json
import os
import random
import time

from . import config as C
from .engine import Side, simulate
from .world import LEAGUE, FORMATIONS, ROLE_GROUP, REFEREE_NAMES, club_records, build_squads

KICKOFFS = [("Sat", "12:30"), ("Sat", "15:00"), ("Sat", "15:00"), ("Sat", "15:00"), ("Sat", "15:00"),
            ("Sat", "17:30"), ("Sun", "14:00"), ("Sun", "14:00"), ("Sun", "16:30"), ("Mon", "20:00")]
DAY_OFFSET = {"Sat": 0, "Sun": 1, "Mon": 2, "Tue": 3, "Wed": 4}
MIDWEEK_ROUNDS = {7, 15, 18, 24, 31}
SEASON_START = dt.date(2026, 8, 15)


def build_world(seed=2026, params=None):
    params = dict(C.CALIBRATED, **(params or {}))
    clubs = club_records(params["strength_spread"])
    players = build_squads(clubs, seed=seed)
    referees = [{"referee_id": f"R{i + 1:02d}", "name": n} for i, n in enumerate(REFEREE_NAMES)]
    return clubs, players, referees


def round_robin(club_ids, seed):
    """Double round robin (circle method); second half reversed venues, rounds shuffled within each half."""
    rng = random.Random(seed)
    ids = list(club_ids)
    rng.shuffle(ids)
    n = len(ids)
    rounds = []
    arr = ids[:]
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    second = [[(b, a) for a, b in rd] for rd in rounds]
    rng.shuffle(second)
    return rounds + second


def build_fixtures(clubs, referees, seed):
    rng = random.Random(seed + 1)
    rounds = round_robin([c["club_id"] for c in clubs], seed)
    fixtures = []
    date = SEASON_START
    mid = 1
    cap = {c["club_id"]: c["capacity"] for c in clubs}
    stadium = {c["club_id"]: c["stadium"] for c in clubs}
    for mw, pairs in enumerate(rounds, start=1):
        slots = KICKOFFS[:]
        rng.shuffle(slots)
        midweek = mw in MIDWEEK_ROUNDS
        for (home, away), (day, time_) in zip(pairs, slots):
            if midweek:
                day, time_ = ("Tue", "19:45") if rng.random() < 0.5 else ("Wed", "20:00")
            d = date + dt.timedelta(days=DAY_OFFSET[day])
            fixtures.append({
                "match_id": f"M{mid:03d}", "matchweek": mw, "date": d.isoformat(), "kickoff_local": time_,
                "home_id": home, "away_id": away, "venue": stadium[home], "referee_id": rng.choice(referees)["referee_id"],
                "attendance": int(cap[home] * min(1.0, rng.uniform(0.88, 1.0))),
            })
            mid += 1
        date += dt.timedelta(days=3 if (mw + 1) in MIDWEEK_ROUNDS or mw in MIDWEEK_ROUNDS else 7)
        if mw in (12, 26):          # international breaks
            date += dt.timedelta(days=7)
    return fixtures


ENGINE_KEYS = ("player_id", "overall", "finishing", "passing", "dribbling", "defending", "aerial", "shot_stopping",
               "discipline")


def _lineup_player(p, role, rx, ry):
    d = {k: p[k] for k in ENGINE_KEYS}
    d.update(role=role, rx=rx, ry=ry, group=ROLE_GROUP[role] if role in ROLE_GROUP else p["position_group"])
    return d


def pick_team(club, squad, unavailable, rng):
    """Starting XI by formation slot (first choice unless suspended or rotated) and a 9-man bench."""
    slots = FORMATIONS[club["formation"]]
    pool = [p for p in squad if p["player_id"] not in unavailable]
    used = set()
    xi = []
    for role, rx, ry in slots:
        cands = [p for p in pool if p["primary_role"] == role and p["player_id"] not in used]
        cands.sort(key=lambda p: (p["first_choice"], p["overall"]), reverse=True)
        if cands and cands[0]["first_choice"] and len(cands) > 1 and rng.random() < 0.07:
            cands = cands[1:] + cands[:1]           # rotation
        if not cands:
            grp = ROLE_GROUP[role]
            cands = sorted([p for p in pool if p["position_group"] == grp and p["player_id"] not in used],
                           key=lambda p: p["overall"], reverse=True)
        if not cands:
            cands = [p for p in pool if p["player_id"] not in used and p["position_group"] != "GK"]
        chosen = cands[0]
        used.add(chosen["player_id"])
        xi.append(_lineup_player(chosen, role, rx, ry))
    rest = [p for p in pool if p["player_id"] not in used]
    gks = [p for p in rest if p["position_group"] == "GK"][:1]
    others = sorted([p for p in rest if p["position_group"] != "GK"], key=lambda p: p["overall"], reverse=True)[:8]
    bench = [_lineup_player(p, p["primary_role"], 50, 34) for p in gks + others]
    return xi, bench


def run_season(seed=2026, params=None, progress=False, keep_events=True):
    params = dict(C.CALIBRATED, **(params or {}))
    clubs, players, referees = build_world(seed, params)
    club_by_id = {c["club_id"]: c for c in clubs}
    squads = {c["club_id"]: [p for p in players if p["club_id"] == c["club_id"]] for c in clubs}
    pid_lookup = {p["player_id"]: p for p in players}
    fixtures = build_fixtures(clubs, referees, seed)
    rng = random.Random(seed + 7)

    bans = {}                  # player_id -> matches remaining
    yellow_count = {}          # season yellows (bans at 5 and 10)
    matches, lineups, events, possessions, discipline = [], [], [], [], []
    t0 = time.time()
    for i, fx in enumerate(fixtures):
        sides = {}
        for role in ("home", "away"):
            cid = fx[f"{role}_id"]
            unavailable = {pid for pid, n in bans.items() if n > 0 and pid_lookup[pid]["club_id"] == cid}
            xi, bench = pick_team(club_by_id[cid], squads[cid], unavailable, rng)
            sides[role] = (xi, bench, unavailable)
        home = Side(club_by_id[fx["home_id"]], sides["home"][0], sides["home"][1], pid_lookup, True, params)
        away = Side(club_by_id[fx["away_id"]], sides["away"][0], sides["away"][1], pid_lookup, False, params)
        sim = simulate(fx["match_id"], home, away, seed=seed * 100003 + i * 7919, params=params)

        # suspensions served this match
        for side_role in ("home", "away"):
            for pid in sides[side_role][2]:
                bans[pid] -= 1
                if bans[pid] <= 0:
                    del bans[pid]

        # lineups and minutes from events
        lineups.extend(lineup_rows(fx, sim, sides))

        # new discipline -> bans
        for ev in sim.events:
            if ev["type"] == "card":
                pid = ev["player_id"]
                if ev["card"] == "yellow":
                    yellow_count[pid] = yellow_count.get(pid, 0) + 1
                    if yellow_count[pid] in (5, 10):
                        bans[pid] = bans.get(pid, 0) + (1 if yellow_count[pid] == 5 else 2)
                        discipline.append({"player_id": pid, "match_id": fx["match_id"], "reason": f"{yellow_count[pid]}_yellows",
                                           "matches_banned": 1 if yellow_count[pid] == 5 else 2})
                else:
                    n = 1 if ev["card"] == "second_yellow" else 3
                    bans[pid] = bans.get(pid, 0) + n
                    discipline.append({"player_id": pid, "match_id": fx["match_id"], "reason": ev["card"], "matches_banned": n})

        ht = [e for e in sim.events if e["type"] == "period_end"][0]
        matches.append({
            **fx, "home_goals": home.goals, "away_goals": away.goals,
            "ht_home_goals": ht["score_home"], "ht_away_goals": ht["score_away"],
            "home_xg": round(sum(e["xg"] or 0 for e in sim.events if e["type"] == "shot" and e["team_id"] == home.id), 3),
            "away_xg": round(sum(e["xg"] or 0 for e in sim.events if e["type"] == "shot" and e["team_id"] == away.id), 3),
            "result": "H" if home.goals > away.goals else "A" if away.goals > home.goals else "D",
            "ball_in_play_s": round(sim.bip),
            "duration_s": round(sim.events[-1]["clock_s"]),
        })
        if keep_events:
            events.extend(sim.events)
            possessions.extend(sim.possessions)
        if progress and (i + 1) % 38 == 0:
            print(f"  {i + 1}/380 matches  ({time.time() - t0:.0f}s)", flush=True)
    return {
        "league": LEAGUE, "params": params, "clubs": clubs, "players": players, "referees": referees,
        "matches": matches, "lineups": lineups, "events": events, "possessions": possessions,
        "suspensions": discipline,
    }


def lineup_rows(fx, sim, sides):
    rows = []
    end = sim.events[-1]["clock_s"]
    for role, side in (("home", sim.h), ("away", sim.a)):
        xi, bench, _ = sides[role]
        on_at, off_at, how_off = {}, {}, {}
        for p in xi:
            on_at[p["player_id"]] = 0.0
        for ev in sim.events:
            if ev["team_id"] != side.id:
                continue
            if ev["type"] == "substitution":
                off_at[ev["player_id"]] = ev["clock_s"]
                how_off[ev["player_id"]] = "substituted"
                on_at[ev["recipient_id"]] = ev["clock_s"]
            elif ev["type"] == "card" and ev["card"] in ("red", "second_yellow"):
                off_at[ev["player_id"]] = ev["clock_s"]
                how_off[ev["player_id"]] = "sent_off"
        for p, starter in [(p, True) for p in xi] + [(p, False) for p in bench]:
            pid = p["player_id"]
            st = on_at.get(pid)
            en = off_at.get(pid, end) if st is not None else None
            rows.append({
                "match_id": fx["match_id"], "club_id": side.id, "player_id": pid, "starter": starter,
                "role": p["role"] if starter else None,
                "on_clock_s": st, "off_clock_s": en if st is not None else None,
                "minutes": round((en - st) / 60.0, 1) if st is not None else 0.0,
                "exit": how_off.get(pid, "full_time" if st is not None else "unused"),
            })
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a fictional, calibrated football season.")
    ap.add_argument("--seed", type=int, default=2042)
    ap.add_argument("--out", default="data")
    args = ap.parse_args(argv)
    from .export import write_season
    print(f"Simulating season with seed {args.seed} ...")
    season = run_season(args.seed, progress=True)
    write_season(season, args.out)
    print(f"Done. Files in {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
