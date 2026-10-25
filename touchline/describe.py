"""Describer: turns events into short, spatial lines a listener can picture.

Locations are always described from the point of view of the team on the ball
(radio convention: "left wing", "edge of the box", "25 yards out"). The stereo
position of each line uses the fixed listener frame instead (home team attacks
to the right), so the words and the panning never contradict each other.
"""

import math
import random
from dataclasses import dataclass, field

L, W, GOAL_Y = 105.0, 68.0, 34.0
M_TO_YD = 1.0936


@dataclass
class Line:
    t: float                 # match clock when the line becomes available
    text: str
    kind: str
    priority: int            # 0-100
    max_delay: float = 6.0   # drop if it cannot start within this many seconds
    interrupt: bool = False  # may cut off whatever is being said
    excite: float = 0.0      # 0 calm .. 2 shouting
    team: str = None
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------- geography
def side_of(y):
    if y < 14:
        return "wide on the right"
    if y < 25:
        return "right side"
    if y <= 43:
        return "central"
    if y <= 54:
        return "left side"
    return "wide on the left"


def flank(y):
    return "right" if y < GOAL_Y else "left"


def yards(x, y):
    return int(round(math.hypot(L - x, GOAL_Y - y) * M_TO_YD))


def zone(x, y):
    """Where the ball is, for the team attacking towards x = 105."""
    s = side_of(y)
    if x >= 99.5 and 24.8 <= y <= 43.2:
        return "in the six-yard box"
    if x >= 88.5 and 13.8 <= y <= 54.2:
        if s == "central":
            return "inside the box"
        return f"inside the box, {s}" if s.endswith("side") else f"inside the box, {s.replace('wide on the ', '')} side"
    if x >= 83:
        if s == "central":
            return "on the edge of the box"
        return f"{s}, level with the box" if s.startswith("wide") else f"{s}, just outside the box"
    if x >= 70:
        return f"{s}, about {yards(x, y)} yards out" if s != "central" else f"{yards(x, y)} yards out, central"
    if x >= 52.5:
        return f"{s}, in the opposition half" if s != "central" else "in the opposition half"
    if x >= 35:
        return f"{s}, just inside their own half" if s != "central" else "around halfway, in their own half"
    if x >= 12:
        return f"{s}, deep in their own half" if s != "central" else "deep in their own half"
    return "in their own penalty area"


def shot_spot(x, y):
    yd = yards(x, y)
    off = y - GOAL_Y
    if abs(off) < 4:
        angle = "straight on"
    elif abs(off) < 11:
        angle = "slightly to the left" if off > 0 else "slightly to the right"
    else:
        angle = "from a tight angle on the left" if off > 0 else "from a tight angle on the right"
    if angle.startswith("from"):
        return f"{yd} yards out, {angle[5:]}"
    return f"{yd} yards out, {angle}"


def nth(n):
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def minute_phrase(display):
    """23' -> '23 minutes'; 45+2' -> 'two minutes into added time'."""
    d = display.rstrip("'")
    if "+" in d:
        base, add = d.split("+")
        words = {"1": "a minute", "2": "two minutes", "3": "three minutes", "4": "four minutes", "5": "five minutes"}
        return f"{words.get(add, add + ' minutes')} into added time"
    return "1 minute" if d == "1" else f"{d} minutes"


# ------------------------------------------------------------------ describer
class Describer:
    def __init__(self, state, seed=0):
        self.s = state
        self.rng = random.Random(seed)
        self.counter_called = None       # possession id already called as a break
        self.high_turnover_called = None
        self.last_regain = None          # (possession id, clock, start x)

    def pick(self, *options):
        return self.rng.choice(options)

    def tname(self, club_id):
        return self.s.team(club_id).short

    def score_line(self):
        b = self.s.bundle
        h, a = self.s.score()
        return f"{b.home.short} {h}, {b.away.short} {a}"

    # ------------------------------------------------------------ events
    def on_event(self, e, info):
        s = self.s
        typ = e["type"]
        team = e["team_id"]
        pid = e["player_id"]
        t = e["clock_s"]
        out = []
        who = s.name(pid) if pid else None

        if typ == "period_start":
            b = s.bundle
            if e["period"] == 1:
                out.append(Line(t, f"And we're under way. {b.home.short} attack to your right, {b.away.short} to your left.",
                                "kickoff", 90, 5, excite=0.6))
            else:
                out.append(Line(t, f"The second half is under way. {self.score_line()}. {b.home.short} still attacking to your right.",
                                "kickoff", 90, 5, excite=0.5))
        elif typ == "period_end":
            if e["period"] == 1:
                out.append(Line(t, f"The referee blows for half-time. {self.score_line()}.", "half_time", 95, 6, interrupt=True))
            else:
                out.append(Line(t, f"Full-time! {self.score_line()}.", "full_time", 100, 6, interrupt=True, excite=1.0))
        elif typ == "shot":
            out += self._shot(e, info)
        elif typ == "penalty_awarded":
            out.append(Line(t, self.pick(f"Penalty! {who} is brought down in the box!",
                                         f"The referee points to the spot! Penalty to {self.tname(team)}!"),
                            "penalty", 95, 3, interrupt=True, excite=1.8, team=team))
        elif typ == "card":
            c = e["card"]
            if c == "yellow":
                out.append(Line(t, self.pick(f"Yellow card for {who} of {self.tname(team)}.",
                                             f"{who} goes into the book.", f"{who} is booked."),
                                "yellow", 60, 15, team=team))
            else:
                reason = "A second yellow, and" if c == "second_yellow" else "A straight red, and"
                left = 11 - s.stats[team].reds
                out.append(Line(t, f"Red card! {reason} {who} is off. {self.tname(team)} are down to {left} men.",
                                "red", 92, 4, interrupt=True, excite=1.5, team=team))
        elif typ == "substitution":
            on = s.name(e["recipient_id"])
            out.append(Line(t, self.pick(f"Change for {self.tname(team)}: {on} comes on, {who} goes off.",
                                         f"{self.tname(team)} make a substitution. {on} replaces {who}."),
                            "substitution", 45, 60, team=team, meta={"on": e["recipient_id"], "off": pid}))
        elif typ == "offside":
            out.append(Line(t, self.pick(f"Flag's up, {who} is offside.", f"Offside against {who}."),
                            "offside", 40, 4, team=team))
        elif typ == "foul":
            fouled_team = s.other(team)
            ax, ay = L - e["x"], W - e["y"]           # location in the fouled team's attacking frame
            if ax >= 70:
                out.append(Line(t, f"Foul by {who}. Free kick to {self.tname(fouled_team)}, {shot_spot(ax, ay)}.",
                                "free_kick", 58, 8, team=fouled_team, excite=0.5))
        elif typ == "pass":
            out += self._pass(e)
        elif typ == "take_on" and e["outcome"] == "success" and e["x"] >= 75:
            out.append(Line(t, self.pick(f"{who} goes past his man!", f"{who} beats the defender!", f"Lovely skill from {who}!"),
                            "take_on", 48, 2.5, team=team, excite=0.9))
        elif typ in ("interception", "tackle", "ball_recovery") and team:
            x = e["x"]
            if x is not None and x >= 62 and self.high_turnover_called != e.get("event_id"):
                self.high_turnover_called = e.get("event_id")
                out.append(Line(t, self.pick(f"Won back high up the pitch by {who}!", f"{who} pinches it in the opposition half!"),
                                "turnover", 62, 2.5, team=team, excite=0.9))
            self.last_regain = {"team": team, "t": t, "x": x, "called": False}
        elif typ == "keeper_claim":
            out.append(Line(t, self.pick(f"{who} comes and claims it.", f"Safe hands from {who}."), "claim", 35, 2.5, team=team))

        out += self._counter_check(e)
        return out

    def _counter_check(self, e):
        """Call a break live: ball won in own half, in the final third within 12 seconds."""
        s, r = self.s, self.last_regain
        if not r or e["x"] is None or e["team_id"] is None:
            return []
        if s.poss_team != r["team"]:
            self.last_regain = None
            return []
        if r["called"] or r["x"] is None or r["x"] >= 55 or e["team_id"] != r["team"]:
            return []
        if e["clock_s"] - r["t"] <= 12 and s.ball_ax >= 70:
            r["called"] = True
            tn = self.tname(r["team"])
            return [Line(e["clock_s"], self.pick(f"{tn} break!", f"Here come {tn} on the counter!", f"{tn} are away on the break!"),
                         "counter", 78, 2.0, interrupt=False, excite=1.4, team=r["team"])]
        if e["clock_s"] - r["t"] > 12:
            self.last_regain = None
        return []

    def _pass(self, e):
        s = self.s
        t, team = e["clock_s"], e["team_id"]
        who = s.name(e["player_id"])
        tags = e.get("tags") or ""
        sp = e.get("set_piece")
        if sp == "corner":
            return [Line(t, self.pick(f"Corner to {self.tname(team)}, from the {flank(e['y'])}.",
                                      f"{self.tname(team)} have a corner on the {flank(e['y'])}. {who} to take."),
                         "corner", 55, 5, team=team, excite=0.4)]
        if "key_pass" in tags:
            return []          # folded into the shot line
        if sp in ("throw_in", "goal_kick", "free_kick", "kickoff"):
            if sp == "kickoff":
                return []
            z = zone(e["x"], e["y"])
            text = {"throw_in": self.pick(f"Throw-in to {self.tname(team)}, {z}.", f"{self.tname(team)} throw, {z}."),
                    "goal_kick": self.pick(f"Goal kick for {self.tname(team)}.", f"{who} takes the goal kick."),
                    "free_kick": f"{who} takes the free kick, {z}."}[sp]
            return [Line(t, text, "restart", 32, 3.0, team=team)]
        pt = e.get("pass_type")
        if pt == "cross":
            return [Line(t, self.pick(f"{who} crosses from the {flank(e['y'])}.", f"Ball in from the {flank(e['y'])} by {who}."),
                         "cross", 50, 2.0, team=team, excite=0.7)]
        if pt == "through_ball" and e.get("outcome") == "complete" and (e.get("end_x") or 0) >= 85:
            to = s.name(e["recipient_id"])
            return [Line(t, f"Through ball, {to} is in!", "through_ball", 64, 2.0, team=team, excite=1.2)]
        if pt == "long" and e.get("outcome") == "complete" and (e.get("end_x") or 0) >= 75:
            to = s.name(e["recipient_id"])
            return [Line(t, f"Long ball forward from {who}, picked out {to}.", "long_ball", 42, 2.5, team=team, excite=0.4)]
        return []

    def _shot(self, e, info):
        s = self.s
        t, team, pid = e["clock_s"], e["team_id"], e["player_id"]
        who = s.name(pid)
        full = s.name(pid, full=True)
        xg = e["xg"] or 0.0
        out = e["outcome"]
        kind = e.get("shot_type")
        gk_id = s.keepers.get(s.other(team))
        gk = s.name(gk_id) if gk_id else "the keeper"
        spot = shot_spot(e["x"], e["y"])
        assist = s.name(e["recipient_id"]) if e.get("recipient_id") else None
        header = e.get("body_part") == "head"
        verb = "heads" if header else "shoots"

        if kind == "penalty":
            if out == "goal":
                lines = [Line(t, f"{who} steps up... and scores! Goal for {self.tname(team)}!", "goal", 100, 3, True, 2.0, team)]
            else:
                lines = [Line(t, f"{who} steps up... {'saved by ' + gk + '!' if out == 'saved' else 'and misses!'}",
                              "big_chance", 92, 3, True, 2.0, team)]
        elif out == "goal":
            how = {
                "head": f"a header from {spot}",
            }.get(e.get("body_part"), f"from {spot}")
            setup = f", set up by {assist}" if assist else ""
            lines = [Line(t, self.pick(f"GOAL! {full} scores for {self.tname(team)}! {how.capitalize()}{setup}.",
                                       f"It's in! {full}, {how}{setup}! Goal for {self.tname(team)}!"),
                          "goal", 100, 3, True, 2.0, team, meta={"xg": xg})]
        else:
            res = {"saved": self.pick(f"saved by {gk}", f"{gk} saves", f"{gk} gets down to stop it"),
                   "blocked": self.pick("blocked", "charged down", "blocked by a defender"),
                   "off_target": self.pick("wide", "over the bar", "off target"),
                   "post": "off the post!"}[out]
            setup = f" from {assist}'s pass" if assist else ""
            if xg >= 0.25:
                text = self.pick(f"Big chance! {who} {verb}{setup}, {spot}... {res}!",
                                 f"Great chance for {who}{setup}, {spot}... {res}!")
                lines = [Line(t, text, "big_chance", 85, 3, True, 1.8, team, meta={"xg": xg})]
            elif xg < 0.04:
                lines = [Line(t, self.pick(f"{who} tries his luck from {yards(e['x'], e['y'])} yards. {res.capitalize()}.",
                                           f"Speculative from {who}, {res}."),
                              "shot", 52, 3, False, 0.6, team, meta={"xg": xg})]
            else:
                lines = [Line(t, f"{who} {verb}{setup}, {spot}. {res.capitalize()}.", "shot", 70, 3, False, 1.1, team,
                              meta={"xg": xg})]

        if out == "goal":
            lines.append(Line(t + 0.1, f"{self.score_line()}.", "score", 90, 20, team=team))
            n = s.bundle.season_goals.get(pid, 0) + s.match_goals.get(pid, 0)
            follow = []
            if s.match_goals.get(pid, 0) == 3:
                follow.append(f"That's a hat-trick for {who}!")
            elif s.match_goals.get(pid, 0) == 2:
                follow.append(f"His second of the game.")
            if n >= 2:
                follow.append(f"{who}'s {nth(n)} goal of the season.")
            elif n == 1:
                follow.append(f"His first goal of the season.")
            wp0 = info.get("wp_before")
            if wp0:
                i = 0 if s.is_home(team) else 2
                before, after = round(100 * wp0[i]), round(100 * s.wp[i])
                if after - before >= 8:
                    follow.append(f"{self.tname(team)}'s chances of winning go from {before} to {after} percent.")
            if follow:
                lines.append(Line(t + 0.2, " ".join(follow), "goal_context", 65, 40, team=team,
                                  meta={"facts": {"season_goals": n, "wp": [round(100 * v) for v in s.wp]}}))
        return lines

    # ------------------------------------------------------------ pull lines
    def ball_line(self, last_text, last_at, now, last_poss_team):
        """Where the ball is and how it got there. Returns None if there is nothing new to say."""
        s = self.s
        if s.poss_team is None or not s.started or s.finished:
            return None
        team = self.tname(s.poss_team)
        z = zone(s.ball_ax, s.ball_ay)
        act = s.last_action if (s.last_action and s.last_action["team"] == s.poss_team) else None
        nm = lambda pid: s.name(pid) if pid and s.bundle.players.get(pid, {}).get("club_id") == s.poss_team else None
        p = nm(s.ball_player)
        text = None
        if act:
            a, r = nm(act["player"]), nm(act["recipient"])
            if act["type"] == "pass" and act["outcome"] == "complete" and r:
                if act["pass_type"] == "long":
                    text = self.pick(f"Long ball from {a}, {r} brings it down, {z}.", f"{a} goes long, {r} collects, {z}.")
                elif a and a not in (last_text or ""):
                    text = self.pick(f"{a} finds {r}, {z}.", f"{a} to {r}, {z}.", f"{r} takes it from {a}, {z}.")
                else:
                    text = self.pick(f"{r}, {z}.", f"{r} has it, {z}.")
            elif act["type"] == "carry" and a:
                text = self.pick(f"{a} drives forward, {z}.", f"{a} carries it on, {z}.") if act["dx"] > 4 else f"{a} on the ball, {z}."
            elif act["type"] in ("interception", "tackle", "ball_recovery") and a:
                text = self.pick(f"{a} wins it for {team}, {z}.", f"Won back by {a}, {z}.", f"{a} steps in and takes it, {z}.")
            elif act["type"] == "keeper_claim" and a:
                text = f"{a} has it in his hands."
        if text is None:
            text = f"{p}, {z}." if p else f"{team} in possession, {z}."
        if last_poss_team != s.poss_team and team not in text:
            text = self.pick(f"{team} have it. ", f"{team} now. ", f"{team} in possession. ") + text[0].upper() + text[1:]
        same = last_text and (text == last_text or (z in last_text and (p or team) in last_text))
        if same:
            if now - last_at < 9:
                return None
            text = self.pick(f"Still {team}, {z}.", f"{team} keeping the ball, {z}.", f"{team} patient, {z}.")
            if text == last_text:
                return None
        return text
