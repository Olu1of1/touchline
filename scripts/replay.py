"""Replay one synthetic match in the terminal, event by event.

Examples (run from the project root):
    python scripts/replay.py                     # list notable matches to try
    python scripts/replay.py M059                # key moments of Eskmouth 4-3 Dunhallow
    python scripts/replay.py M059 --all          # every event (~1,500 lines)
    python scripts/replay.py M059 --live 20      # play back at 20x real time
    python scripts/replay.py M059 --from 60 --to 75   # a window of the match

Coordinates in the data are in the acting team's attacking direction, so a
location is described relative to the goal that team is attacking.
"""

import argparse
import os
import sys
import time

import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

KEY_TYPES = {"shot", "card", "substitution", "penalty_awarded", "period_start", "period_end", "match_end", "offside"}


def where(x, y):
    """Plain-English location from the acting team's perspective."""
    if pd.isna(x):
        return ""
    side = "right" if y < 22.67 else "left" if y > 45.33 else "central"
    if x >= 99.5:
        depth = "in the six-yard box"
    elif x >= 88.5 and 13.84 <= y <= 54.16:
        depth = "inside the box"
    elif x >= 80:
        depth = "on the edge of the box" if side == "central" else "in the final third"
    elif x >= 70:
        depth = "about 30 yards out"
    elif x >= 52.5:
        depth = "in the opponent's half"
    elif x >= 35:
        depth = "in their own half"
    else:
        depth = "deep in their own half"
    if "box" in depth and side != "central":
        return f"{depth}, {side} side"
    return f"{side}, {depth}" if side != "central" else depth


def load():
    if not os.path.exists(os.path.join(ROOT, "events.csv.gz")):
        sys.exit("No data/ folder found. Run:  python -m matchgen.season --out data")
    ev = pd.read_csv(os.path.join(ROOT, "events.csv.gz"))
    matches = pd.read_csv(os.path.join(ROOT, "matches.csv"))
    clubs = pd.read_csv(os.path.join(ROOT, "clubs.csv")).set_index("club_id")
    players = pd.read_csv(os.path.join(ROOT, "players.csv")).set_index("player_id")
    return ev, matches, clubs, players


def notable(matches, clubs):
    m = matches.copy()
    m["total"] = m.home_goals + m.away_goals
    sign = (m.away_goals > m.home_goals).astype(int) - (m.home_goals > m.away_goals).astype(int)
    m["upset"] = (m.home_xg - m.away_xg) * sign
    name = clubs["short_name"]

    def line(r, note):
        return (f"  {r.match_id}  MW{r.matchweek:<2} {name[r.home_id]} {r.home_goals}-{r.away_goals} "
                f"{name[r.away_id]}   (xG {r.home_xg:.2f}-{r.away_xg:.2f})  {note}")

    print("Biggest xG upsets (the side that created far more lost):")
    for r in m.sort_values("upset", ascending=False).head(4).itertuples():
        print(line(r, ""))
    print("\nHighest-scoring games:")
    for r in m.sort_values("total", ascending=False).head(4).itertuples():
        print(line(r, ""))
    print("\nRun:  python scripts/replay.py <match_id>")


def describe(e, home_id, clubs, players):
    team = clubs["short_name"].get(e.team_id, "") if pd.notna(e.team_id) else ""
    who = players["name"].get(e.player_id, "") if pd.notna(e.player_id) else ""
    to = players["name"].get(e.recipient_id, "") if pd.notna(e.recipient_id) else ""
    loc = where(e.x, e.y)
    t = e.type
    if t == "period_start":
        return f"--- {'First' if e.period == 1 else 'Second'} half underway ---"
    if t == "period_end":
        return f"--- {'Half-time' if e.period == 1 else 'Full-time'} ---"
    if t == "match_end":
        return f"=== FINAL SCORE {e.outcome} ==="
    if t == "shot":
        kind = {"penalty": "Penalty", "free_kick": "Direct free kick"}.get(e.shot_type, "Header" if e.body_part == "head" else "Shot")
        assist = f" (set up by {to})" if to else ""
        res = {"goal": "GOAL!", "saved": "saved", "blocked": "blocked", "off_target": "off target", "post": "hits the post!"}[e.outcome]
        flag = "  <<< BIG CHANCE" if isinstance(e.tags, str) and "big_chance" in e.tags and e.outcome != "goal" else ""
        score = f"   [{e.score_home}-{e.score_away}]" if e.outcome == "goal" else ""
        return f"{kind} by {who} ({team}) {loc}{assist}: {res}  xG {e.xg:.2f}{score}{flag}"
    if t == "card":
        card = {"yellow": "Yellow card", "second_yellow": "SECOND YELLOW - sent off", "red": "RED CARD"}[e.card]
        return f"{card}: {who} ({team})"
    if t == "substitution":
        return f"Substitution {team}: {to} on, {who} off"
    if t == "penalty_awarded":
        return f"PENALTY to {team}! {who} brought down"
    if t == "offside":
        return f"Offside: {who} ({team})"
    if t == "pass":
        pt = e.pass_type.replace("_", " ")
        res = "" if e.outcome == "complete" else f" ({e.outcome})"
        dest = f" to {to}" if to else ""
        return f"{who} ({team}) {pt} pass{dest}, from {loc}{res}"
    if t == "foul":
        return f"Foul by {who} ({team}) on {to}"
    return f"{t.replace('_', ' ').capitalize()}: {who} ({team}) {loc}".rstrip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("match_id", nargs="?")
    ap.add_argument("--all", action="store_true", help="show every event, not just key moments")
    ap.add_argument("--live", type=float, default=0, help="play back at N x real time (e.g. 20)")
    ap.add_argument("--from", dest="start", type=int, default=0, help="start minute")
    ap.add_argument("--to", dest="end", type=int, default=200, help="end minute")
    a = ap.parse_args()

    ev, matches, clubs, players = load()
    if not a.match_id:
        notable(matches, clubs)
        return
    mid = a.match_id.upper()
    if mid not in set(matches.match_id):
        sys.exit(f"Unknown match {mid}. Match IDs run M001-M380.")
    m = matches[matches.match_id == mid].iloc[0]
    e = ev[ev.match_id == mid].sort_values("event_id")
    e = e[(e.minute >= a.start) & (e.minute <= a.end)]
    if not a.all:
        keep = e.type.isin(KEY_TYPES) | (e.tags.fillna("").str.contains("key_pass"))
        e = e[keep]

    h, aw = clubs.loc[m.home_id], clubs.loc[m.away_id]
    print(f"\n{h['name']} vs {aw['name']}  |  Matchweek {m.matchweek}, {m.date} {m.kickoff_local}, "
          f"{m.venue} (att. {m.attendance:,})")
    print(f"Styles: {h['style']} {h['formation']} vs {aw['style']} {aw['formation']}\n")

    last = None
    for r in e.itertuples():
        if a.live and last is not None:
            time.sleep(max(0.0, (r.clock_s - last) / a.live))
        last = r.clock_s
        print(f"{r.display_clock:>7}  {describe(r, m.home_id, clubs, players)}", flush=True)

    allm = ev[ev.match_id == mid]
    for cid in (m.home_id, m.away_id):
        s = allm[(allm.team_id == cid) & (allm.type == "shot")]
        p = allm[(allm.team_id == cid) & (allm.type == "pass")]
        print(f"  {clubs.loc[cid, 'short_name']:<14} shots {len(s):>2} (on target {s.outcome.isin(['goal', 'saved']).sum()}), "
              f"xG {s.xg.sum():.2f}, passes {len(p)} ({100 * (p.outcome == 'complete').mean():.0f}%)")


if __name__ == "__main__":
    main()
