"""Runs the agent team over one match and produces a commentary timeline.

The event stream is consumed strictly in order, exactly as it would arrive
live. The output is a timeline of utterances (when to speak, what, how
excitedly, from which side of the stereo field), plus a ball track and state
snapshots the player uses for on-demand questions ("what's the score?").

    python -m touchline.pipeline M059            # print the commentary script
    python -m touchline.pipeline M059 --stats    # coverage and timing metrics
"""

import argparse
import json
import os
import time

from .colour import ColourAgent
from .data import league, load_match
from .describe import Describer, Line, minute_phrase
from .director import Director
from .llm import get_backend
from .state import MatchState

PRE_MATCH = 45.0       # seconds of broadcast before kick-off
HT_BREAK = 50.0        # seconds of broadcast for the half-time break
QUIET_COLOUR_EVERY = 240.0
KEY_KINDS = {"goal", "big_chance", "penalty", "red"}


def league_name_tokens():
    lg = league()
    toks = set()
    for n in list(lg["players"]["name"]) + list(lg["clubs"]["name"]) + list(lg["clubs"]["short_name"]):
        toks.update(n.split())
    return toks


def to_broadcast(events):
    """Add the pre-match build-up and a half-time break to the clock. Keeps the playing clock separately."""
    out = []
    for e in events:
        e = dict(e)
        e["match_clock_s"] = e["clock_s"]
        e["clock_s"] = round(e["clock_s"] + PRE_MATCH + (HT_BREAK if e["period"] == 2 else 0.0), 2)
        out.append(e)
    return out


def build(match_id, backend="auto", seed=0, until=None):
    """Build the commentary. `until` (broadcast seconds) truncates the event stream, which tests use to
    prove the agents are causal: commentary up to a moment never changes when later events are removed."""
    t_start = time.time()
    if backend == "auto":
        backend = get_backend()
    b = load_match(match_id)
    s = MatchState(b)
    d = Describer(s, seed=seed + int(match_id[1:]))
    colour = ColourAgent(s, backend, seed=seed, league_names=league_name_tokens())
    dr = Director(s, d)
    events = to_broadcast(b.events)
    if until is not None:
        events = [e for e in events if e["clock_s"] <= until]

    # ---- pre-match
    s.clock = 0.0
    welcome = (f"Welcome to {b.venue}, where {b.home.name} take on {b.away.name} in matchweek {b.matchweek}. "
               f"A crowd of {b.attendance:,} here today.")
    dr.offer([Line(1.0, welcome, "welcome", 90, 30)])
    dr.offer(colour.make("prematch", 2.0))

    ball, snaps = [], []
    last_snap = -1e9
    said = lambda: [u.text for u in dr.timeline[-4:]]

    for e in events:
        t = e["clock_s"]
        prev = s.last_event_clock
        # play has stopped (no events for 8 s): let the co-commentator fill the gap
        if s.started and not s.finished and s.last_event and t - prev >= 8:
            reason, rteam = stoppage_reason(s)
            extra = {"reason": reason}
            if rteam:
                extra["team"] = s.team(rteam).short
            # one line early in the stoppage, then another every ~22 s while it lasts
            at = prev + 3.0
            first = True
            while at < t - 4.0:
                if (at - colour.last_at >= 20) and (s.minute >= 2 or reason == "goal"):
                    dr.advance(at)
                    extra["first_in_stoppage"] = first
                    dr.offer(colour.make("stoppage", at, said(), extra=dict(extra)))
                    first = False
                at += 22.0
        dr.advance(t)
        info = s.update(e)
        lines = d.on_event(e, info)
        typ = e["type"]

        if typ == "substitution":
            on = e["recipient_id"]
            lines += colour.make("substitution", t + 1.0, said(), extra={
                "on": {"name": s.name(on), "season_goals": b.season_goals.get(on, 0),
                       "season_assists": b.season_assists.get(on, 0)},
                "off": {"name": s.name(e["player_id"])}, "team": s.team(e["team_id"]).short})
        elif typ == "period_end" and e["period"] == 1:
            lines += colour.make("half_time", t + 6.0, said())
        elif typ == "match_end":
            h, a = s.score()
            ph = b.home.points + (3 if h > a else 1 if h == a else 0)
            pa = b.away.points + (3 if a > h else 1 if h == a else 0)
            lines += colour.make("full_time", t + 6.0, said(), extra={"points_after": {"home": ph, "away": pa}})
        elif (info.get("new_possession") and t - colour.last_at >= QUIET_COLOUR_EVERY and s.danger() < 0.1
              and e.get("set_piece") in ("goal_kick", "throw_in")):
            since = min((t - st.last_shot_clock) for st in s.stats.values() if st.last_shot_clock is not None) \
                if any(st.last_shot_clock is not None for st in s.stats.values()) else t - PRE_MATCH
            lines += colour.make("quiet", t, said(), extra={"minutes_since_last_shot": int(since // 60)})
        dr.offer(lines)

        if e["x"] is not None:
            ball.append([round(t, 1), round(s.ball_x, 1), round(s.ball_y, 1), 0 if s.poss_team == s.home_id else 1])
        if t - last_snap >= 5 or typ in ("shot", "card", "period_end", "match_end", "substitution"):
            snaps.append(s.snapshot())
            last_snap = t

    end = events[-1]["clock_s"]
    dr.flush(end + 200)
    timeline = [dict(u.__dict__) for u in dr.timeline]
    return {
        "match": match_header(b, s),
        "timeline": timeline,
        "ball": ball,
        "snapshots": snaps,
        "segments": highlights(dr.timeline, snaps, b, end),
        "colour_log": colour.log,
        "metrics": metrics(dr, events, end),
        "backend": backend.name if backend else "templates (offline)",
        "build_seconds": round(time.time() - t_start, 2),
        "constants": {"pre_match_s": PRE_MATCH, "half_time_break_s": HT_BREAK},
    }


def stoppage_reason(s):
    """Why play has stopped, judged from the last event (and which team restarts)."""
    e = s.last_event
    typ, team = e["type"], e["team_id"]
    if typ == "shot":
        if e["outcome"] == "goal":
            return "goal", s.other(team)
        if e["outcome"] in ("off_target", "post"):
            return "goal_kick", s.other(team)
        return "keeper_has_it", s.other(team)
    if typ == "foul":
        return "free_kick", s.other(team)
    if typ == "card":
        return "booking", None
    if typ == "offside":
        return "offside", s.other(team)
    if typ == "substitution":
        return "substitution", None
    if typ in ("save", "keeper_claim"):
        return "keeper_has_it", team
    return "break", None


def match_header(b, s):
    return {"match_id": b.match_id, "matchweek": b.matchweek, "date": b.date, "kickoff": b.kickoff, "venue": b.venue,
            "attendance": b.attendance,
            "home": {"id": b.home.club_id, "name": b.home.name, "short": b.home.short, "style": b.home.style,
                     "formation": b.home.formation},
            "away": {"id": b.away.club_id, "name": b.away.name, "short": b.away.short, "style": b.away.style,
                     "formation": b.away.formation},
            "final_score": list(s.score())}


def _snap_at(snaps, t):
    best = snaps[0]
    for sn in snaps:
        if sn["t"] <= t:
            best = sn
        else:
            break
    return best


def highlights(timeline, snaps, b, end):
    """Windows around the key moments, merged, each with a short bridge line for highlights mode."""
    wins = [(0.0, PRE_MATCH + 12)]
    for u in timeline:
        if u.kind in KEY_KINDS or (u.kind == "shot" and u.meta.get("xg", 0) >= 0.12) or u.kind in ("half_time", "full_time"):
            pre = 20.0 if u.kind in KEY_KINDS else 8.0
            post = 22.0 if u.kind == "goal" else 10.0
            if u.kind == "half_time":
                post = 45.0
            if u.kind == "full_time":
                post = 90.0
            wins.append((max(0.0, u.start - pre), min(end + 200, u.end + post)))
    wins.sort()
    merged = []
    for a, z in wins:
        if merged and a <= merged[-1][1] + 4:
            merged[-1][1] = max(merged[-1][1], z)
        else:
            merged.append([a, z])
    segs = []
    for i, (a, z) in enumerate(merged):
        bridge = None
        if i > 0:
            sn = _snap_at(snaps, a)
            h, aw = sn["score"]
            score = f"still {h} each" if h == aw and h else "still goalless" if h == aw == 0 else \
                f"{b.home.short} {h}, {b.away.short} {aw}"
            bridge = f"On to {minute_phrase(sn['clock'])}. {score.capitalize() if score[0].islower() else score}."
        segs.append({"start": round(a, 1), "end": round(z, 1), "bridge": bridge})
    return segs


def metrics(dr, events, end):
    tl = dr.timeline
    shots = [e for e in events if e["type"] == "shot"]
    goals = [e for e in shots if e["outcome"] == "goal"]
    shot_lines = [u for u in tl if u.kind in ("shot", "big_chance", "goal")]
    goal_lines = [u for u in tl if u.kind == "goal"]
    lat = [u.start - u.born for u in goal_lines]
    spoken = sum(((u.cut_at or u.end) - u.start) for u in tl if u.start >= 0)
    # longest silence while the ball is in play (between kick-off and full-time, excluding the break)
    kick = next(e["clock_s"] for e in events if e["type"] == "period_start")
    ht = next((e["clock_s"] for e in events if e["type"] == "period_end"), end + 1)
    starts = [e["clock_s"] for e in events if e["type"] == "period_start"]
    k2 = starts[1] if len(starts) > 1 else ht
    ft = events[-1]["clock_s"]
    ivs = sorted((u.start, u.cut_at or u.end) for u in tl)
    gaps, cur = [], kick
    for a, z in ivs:
        if a > cur:
            lo, hi = cur, a
            if not (lo >= ht and hi <= k2) and lo < ft:
                gaps.append(min(hi, ft) - lo)
        cur = max(cur, z)
    dropped = {}
    for ln in dr.dropped:
        dropped[ln.kind] = dropped.get(ln.kind, 0) + 1
    by_kind = {}
    for u in tl:
        by_kind[u.kind] = by_kind.get(u.kind, 0) + 1
    return {
        "events": len(events), "utterances": len(tl), "words": sum(len(u.text.split()) for u in tl),
        "airtime_pct": round(100 * spoken / (ft - kick + PRE_MATCH), 1),
        "shots": len(shots), "shots_narrated": len(shot_lines),
        "goals": len(goals), "goals_announced": len(goal_lines),
        "goal_latency_max_s": round(max(lat), 2) if lat else None,
        "longest_silence_in_play_s": round(max(gaps), 1) if gaps else 0.0,
        "interruptions": dr.interrupted, "dropped_by_kind": dropped, "spoken_by_kind": by_kind,
    }


def cache_path(match_id, backend_name):
    tag = "offline" if backend_name.startswith("templates") else backend_name.replace(":", "_").replace("/", "_")
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache", "timelines")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{match_id}_{tag}.json")


SHIPPED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "timelines")


def get_timeline(match_id, backend="auto"):
    """Pre-built timeline if one ships with the repo; otherwise build now (cached).

    Building with the language model makes ~150 sequential calls (a live match spreads them over 95 minutes),
    so on-demand builds use templates unless TOUCHLINE_LIVE_LLM=1. Use `python -m touchline.prebuild` to
    build model-voiced timelines ahead of time.
    """
    shipped = os.path.join(SHIPPED, f"{match_id}.json")
    if os.path.exists(shipped):
        with open(shipped, encoding="utf-8") as f:
            return json.load(f)
    if backend == "auto":
        backend = get_backend() if os.environ.get("TOUCHLINE_LIVE_LLM") == "1" else None
    name = backend.name if backend else "templates (offline)"
    p = cache_path(match_id, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    tl = build(match_id, backend)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(tl, f, ensure_ascii=False)
    return tl


def main():
    ap = argparse.ArgumentParser(description="Print the commentary script for a match.")
    ap.add_argument("match_id")
    ap.add_argument("--stats", action="store_true", help="print coverage and timing metrics only")
    ap.add_argument("--offline", action="store_true", help="ignore any configured language model")
    ap.add_argument("--from", dest="start", type=float, default=0, help="start minute")
    ap.add_argument("--to", dest="end", type=float, default=200, help="end minute")
    a = ap.parse_args()
    tl = build(a.match_id.upper(), backend=None if a.offline else "auto")
    if a.stats:
        print(json.dumps(tl["metrics"], indent=2))
        print("colour:", [(c["trigger"], c.get("verdict", "template"), c["source"]) for c in tl["colour_log"]])
        return
    m = tl["match"]
    print(f"\n{m['home']['name']} v {m['away']['name']}  ({tl['backend']})\n")
    for u in tl["timeline"]:
        mins = u["start"] / 60
        if not (a.start <= mins <= a.end):
            continue
        cut = "  [cut off]" if u["cut_at"] else ""
        tag = {"goal": "!!", "big_chance": "! ", "colour": "~ "}.get(u["kind"].split("_")[0] if u["kind"].startswith("colour") else u["kind"], "  ")
        print(f"{int(u['start'] // 60):>3}:{int(u['start'] % 60):02d} {tag} {u['text']}{cut}")


if __name__ == "__main__":
    main()
