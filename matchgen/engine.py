"""Event-level match engine.

A match is simulated as a sequence of possessions. Within a possession the ball
moves through passes, carries and take-ons; each action can be lost to the
opponent, end in a foul or stoppage, or (in the final third) create a chance.
Chances become shots with a location-based xG; a shot scores with probability
XG_TO_GOAL_SCALE * xG. Goal counts are therefore a sum of Bernoulli trials,
which reproduces the under-dispersion of real goal counts without being told to.

All locations are in the acting team's attacking perspective (see pitch.py).
"""

import math
import random

from . import config as C
from .pitch import (L, W, GOAL_Y, BOX_X, PEN_SPOT, clamp, reflect, mirror, in_box, zone_id, third, channel,
                    shot_xg, free_kick_xg)
from .world import STYLES, ROLE_GROUP, SHOOT_W, SHOOT_ROLE_BONUS, CREATE_W, CREATE_ROLE_BONUS

HALF = 45 * 60


class Side:
    def __init__(self, club, lineup, bench, players_by_id, is_home, params):
        self.club = club
        self.id = club["club_id"]
        self.is_home = is_home
        self.style = STYLES[club["style"]]
        self.on = [dict(p) for p in lineup]          # each: player_id, role, rx, ry, group + attrs
        self.bench = [dict(p) for p in bench]
        self.pid = players_by_id
        self.goals = 0
        self.reds = 0
        self.subs = 0
        self.yellows = {}
        self.sent_off = set()
        hf = params["home_factor"] if is_home else 1.0 / params["home_factor"]
        self.att = club["attack_rating"] * hf
        self.dfn = club["defence_rating"]
        q = (club["attack_rating"] / club["defence_rating"]) ** 0.5
        self.pass_q = 0.055 * math.log(q) / math.log(1.5) + (0.02 if club["style"] == "possession" else 0.0) \
            - (0.03 if club["style"] == "direct" else 0.0)
        self.press = self.style["press"]
        self.sub_plan = []

    def attack_eff(self):
        a = self.att
        if self.reds:
            a *= C.RED_CARD["attack_mult"] ** self.reds
        return a

    def defence_eff(self):
        d = self.dfn
        if self.reds:
            d *= C.RED_CARD["defence_mult"] ** self.reds
        return d

    def outfield(self):
        return [p for p in self.on if p["group"] != "GK"]

    def keeper(self):
        for p in self.on:
            if p["group"] == "GK":
                return p
        return self.on[0]


def _weighted(rng, items, weights):
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if r <= acc:
            return it
    return items[-1]


class MatchSim:
    def __init__(self, match_id, home, away, seed, params=None):
        self.rng = random.Random(seed)
        # Separate stream for cosmetic geometry (e.g. where an assist starts), so fixing presentation
        # never changes match outcomes, which are calibrated on the main stream.
        self.geo = random.Random(seed * 2654435761 % (2 ** 32) + 17)
        self.match_id = match_id
        self.params = dict(C.CALIBRATED)
        if params:
            self.params.update(params)
        self.h = home
        self.a = away
        self.events = []
        self.possessions = []
        self.eid = 0
        self.poss_id = 0
        self.period = 1
        self.t = 0.0                 # seconds into current period
        self.offset = 0.0            # elapsed seconds before the current period (continuous match clock)
        self.bip = 0.0               # ball-in-play seconds
        self.period_end = HALF
        self.cur_poss = None

    # ------------------------------------------------------------------ helpers
    def other(self, side):
        return self.a if side is self.h else self.h

    def clock(self):
        """Continuous elapsed match time in seconds (stoppage time included, never jumps backwards)."""
        return self.offset + self.t

    def minute_frac(self):
        return min(1.0, ((self.period - 1) * HALF + min(self.t, HALF)) / (90 * 60))

    def match_minute(self):
        """Football-convention minute (0-based): stoppage time stays at 45 / 90."""
        base = 0 if self.period == 1 else 45
        if self.t < HALF:
            return base + int(self.t // 60)
        return base + 45

    def display(self):
        s = int(self.t)
        base = 0 if self.period == 1 else 45
        if s < HALF:
            return f"{base + s // 60 + 1}'"
        return f"{base + 45}+{(s - HALF) // 60 + 1}'"

    def emit(self, side, player, etype, x=None, y=None, end_x=None, end_y=None, outcome=None, recipient=None,
             pass_type=None, body_part=None, shot_type=None, xg=None, set_piece=None, card=None, tags=None):
        self.eid += 1
        if x is not None:
            x, y = clamp(x, 0.0, L), clamp(y, 0.0, W)
        if end_x is not None:
            end_x, end_y = clamp(end_x, 0.0, L), clamp(end_y, 0.0, W)
        ev = {
            "match_id": self.match_id,
            "event_id": self.eid,
            "period": self.period,
            "clock_s": round(self.clock(), 1),
            "period_s": round(self.t, 1),
            "minute": self.match_minute(),
            "display_clock": self.display(),
            "team_id": side.id if side else None,
            "player_id": player["player_id"] if player else None,
            "type": etype,
            "outcome": outcome,
            "x": round(x, 1) if x is not None else None,
            "y": round(y, 1) if y is not None else None,
            "end_x": round(end_x, 1) if end_x is not None else None,
            "end_y": round(end_y, 1) if end_y is not None else None,
            "zone": zone_id(x, y) if x is not None else None,
            "third": third(x) if x is not None else None,
            "channel": channel(y) if y is not None else None,
            "recipient_id": recipient["player_id"] if recipient else None,
            "pass_type": pass_type,
            "body_part": body_part,
            "shot_type": shot_type,
            "xg": round(xg, 4) if xg is not None else None,
            "set_piece": set_piece,
            "card": card,
            "possession_id": self.cur_poss["possession_id"] if self.cur_poss else None,
            "possession_team_id": self.cur_poss["team_id"] if self.cur_poss else None,
            "score_home": self.h.goals,
            "score_away": self.a.goals,
            "tags": ";".join(tags) if tags else None,
        }
        self.events.append(ev)
        return ev

    def advance(self, secs, in_play=True):
        self.t += secs
        if in_play:
            self.bip += secs

    def pick_player(self, side, x, y, exclude=None, gk_ok=False):
        players, weights = [], []
        for p in side.on:
            if p is exclude:
                continue
            if p["group"] == "GK" and not (gk_ok and x < 22):
                continue
            dx = (x - p["rx"]) / 20.0
            dy = (y - p["ry"]) / 17.0
            players.append(p)
            weights.append(math.exp(-0.5 * (dx * dx + dy * dy)) + 0.01)
        if not players:
            return side.on[0]
        return _weighted(self.rng, players, weights)

    def pick_defender(self, side, ax, ay):
        """Defender of `side` nearest-ish to an attacking location given in the attacker's perspective."""
        x, y = mirror(ax, ay)
        return self.pick_player(side, x, y, gk_ok=True)

    def gs_mult(self, side):
        diff = side.goals - self.other(side).goals
        f = self.minute_frac()
        if diff < 0:
            return 1.0 + C.GAME_STATE["trailing_boost"] * min(-diff, 2) * f
        if diff > 0:
            return 1.0 - C.GAME_STATE["leading_drag"] * min(diff, 2) * f
        return 1.0

    # ------------------------------------------------------------- possessions
    def open_possession(self, side, start_type, x, y):
        self.poss_id += 1
        self.cur_poss = {
            "match_id": self.match_id, "possession_id": self.poss_id, "team_id": side.id, "period": self.period,
            "start_clock_s": round(self.clock(), 1), "start_type": start_type, "start_x": round(x, 1),
            "start_y": round(y, 1), "max_x": x, "passes": 0, "shots": 0, "xg": 0.0, "goal": False,
            "end_type": None, "end_clock_s": None, "_regain_clock": self.clock(),
        }

    def close_possession(self, end_type):
        p = self.cur_poss
        if not p:
            return
        p["end_type"] = end_type
        p["end_clock_s"] = round(self.clock(), 1)
        dur = p["end_clock_s"] - p["start_clock_s"]
        regain = p["start_type"] in ("interception", "tackle", "recovery", "keeper_claim", "loose_ball")
        if p["start_type"] in ("corner", "free_kick", "penalty", "throw_in_attacking"):
            pattern = "set_piece"
        elif regain and p["start_x"] >= 60:
            pattern = "high_turnover"
        elif regain and p["start_x"] < 55 and (p["max_x"] >= 80 or p["shots"]) and p.get("_fast", False):
            pattern = "counter_attack"
        elif p["passes"] >= 10:
            pattern = "sustained"
        elif p["max_x"] < 45:
            pattern = "build_up_lost"
        else:
            pattern = "open_play"
        p["pattern"] = pattern
        p["duration_s"] = round(dur, 1)
        p["max_x"] = round(p["max_x"], 1)
        p["xg"] = round(p["xg"], 4)
        out = {k: v for k, v in p.items() if not k.startswith("_")}
        self.possessions.append(out)
        self.cur_poss = None

    def note_x(self, x):
        p = self.cur_poss
        if p and x > p["max_x"]:
            p["max_x"] = x
            if x >= 80 and (self.clock() - p["_regain_clock"]) <= 15.0:
                p["_fast"] = True

    # ------------------------------------------------------------------- match
    def run(self):
        for period in (1, 2):
            if period == 2:
                self.offset = self.t
            self.period = period
            self.t = 0.0
            added = self.rng.choice([1, 1, 2, 2, 2, 3, 3, 4, 5]) if period == 1 else self.rng.choice([3, 4, 4, 5, 5, 6, 7, 8, 9])
            self.period_end = HALF + added * 60 + self.rng.uniform(0, 40)
            ko_side = self.h if period == 1 else self.a
            self.emit(None, None, "period_start", outcome=f"added_{added}")
            restart = (ko_side, "kickoff", 52.5, 34.0)
            while restart is not None:
                restart = self.play(*restart)
                if self.t >= self.period_end and restart is not None:
                    self.close_possession("period_end")
                    break
                if restart is not None:
                    self.maybe_substitute()
            if self.cur_poss is not None:
                self.close_possession("period_end")
            self.emit(None, None, "period_end")
        self.emit(None, None, "match_end", outcome=f"{self.h.goals}-{self.a.goals}")
        return self

    # --------------------------------------------------------------- dead ball
    def maybe_substitute(self):
        m = ((self.period - 1) * HALF + min(self.t, HALF)) / 60
        for side in (self.h, self.a):
            while side.sub_plan and m >= side.sub_plan[0] and side.subs < 5:
                side.sub_plan.pop(0)
                self.substitute(side)

    def substitute(self, side):
        diff = side.goals - self.other(side).goals
        outs, ws = [], []
        for p in side.on:
            if p["group"] == "GK":
                continue
            w = {"FWD": 1.6, "MID": 1.3, "DEF": 0.6}[p["group"]]
            if p["player_id"] in side.yellows:
                w *= 1.8
            if diff < 0 and p["group"] == "DEF":
                w *= 1.6
            outs.append(p)
            ws.append(w)
        if not outs:
            return
        off = _weighted(self.rng, outs, ws)
        want = "FWD" if (diff < 0 and off["group"] == "DEF" and self.rng.random() < 0.6) else off["group"]
        cands = [b for b in side.bench if b["group"] == want] or [b for b in side.bench if b["group"] != "GK"]
        if not cands:
            return
        on = max(cands, key=lambda b: b["overall"] + self.rng.random() * 6)
        side.bench.remove(on)
        on["role"], on["rx"], on["ry"] = off["role"], off["rx"], off["ry"]
        side.on[side.on.index(off)] = on
        side.subs += 1
        self.advance(self.rng.uniform(15, 30), in_play=False)
        self.emit(side, off, "substitution", recipient=on, outcome=off["role"])

    def foul(self, att_side, carrier, x, y):
        dfd = self.other(att_side)
        fouler = self.pick_defender(dfd, x, y)
        fx, fy = mirror(x, y)
        self.emit(dfd, fouler, "foul", fx, fy, recipient=carrier)
        disc = (fouler.get("discipline") or 70) / 70.0
        booked = 0.30 if fouler["player_id"] in dfd.yellows else 1.0   # booked players avoid a second
        r = self.rng.random()
        if r < C.STRAIGHT_RED_PER_FOUL / disc:
            self.send_off(dfd, fouler, "red", fx, fy)
        elif r < (C.STRAIGHT_RED_PER_FOUL + C.YELLOW_PER_FOUL * booked * (1.15 if x > 70 else 1.0)) / disc:
            n = dfd.yellows.get(fouler["player_id"], 0) + 1
            dfd.yellows[fouler["player_id"]] = n
            if n >= 2:
                self.send_off(dfd, fouler, "second_yellow", fx, fy)
            else:
                self.emit(dfd, fouler, "card", fx, fy, card="yellow")
                self.advance(self.rng.uniform(15, 30), in_play=False)

    def send_off(self, side, player, kind, x, y):
        self.emit(side, player, "card", x, y, card=kind)
        side.sent_off.add(player["player_id"])
        if player in side.on and len(side.on) > 7:
            side.on.remove(player)
            side.reds += 1
            if player["group"] == "GK":   # emergency keeper: swap an outfielder for the bench keeper
                gks = [b for b in side.bench if b["group"] == "GK"]
                if gks and side.subs < 5:
                    victim = max(side.outfield(), key=lambda p: p["rx"])
                    newgk = gks[0]
                    side.bench.remove(newgk)
                    newgk["role"], newgk["rx"], newgk["ry"] = "GK", 5, 34
                    side.on[side.on.index(victim)] = newgk
                    side.subs += 1
                    self.emit(side, victim, "substitution", recipient=newgk, outcome="emergency_keeper")
        self.advance(self.rng.uniform(40, 70), in_play=False)

    # ------------------------------------------------------------------ shots
    def take_shot(self, side, shooter, x, y, xg, body, shot_type, set_piece=None, assister=None, tags=None):
        dfd = self.other(side)
        gk = dfd.keeper()
        fin = ((shooter.get("finishing") or 50) - 65) / 30.0
        stop = ((gk.get("shot_stopping") or 70) - 75) / 30.0
        # skill effects are small and centred: the shooter pool skews to good finishers, so 0.99 recentres goals/xG
        p_goal = clamp(C.XG_TO_GOAL_SCALE * xg * 0.99 * (1 + 0.06 * fin) * (1 - 0.05 * stop), 0.002, 0.97)
        tags = list(tags or [])
        if xg >= 0.30:
            tags.append("big_chance")
        if assister is not None:
            tags.append("assisted")
        r = self.rng.random()
        if r < p_goal:
            outcome = "goal"
        else:
            r2 = self.rng.random()
            outcome = "blocked" if (r2 < 0.29 and shot_type != "penalty") else "saved" if r2 < 0.56 else \
                "post" if r2 < 0.58 else "off_target"
            if shot_type == "penalty" and outcome == "blocked":
                outcome = "saved"
        gx, gy = L, clamp(GOAL_Y + self.rng.gauss(0, 2.2), 30.5, 37.5)
        if outcome == "goal":
            side.goals += 1
            self.cur_poss["goal"] = True
        self.cur_poss["shots"] += 1
        self.cur_poss["xg"] += xg
        self.emit(side, shooter, "shot", x, y, gx, gy, outcome=outcome, recipient=assister, body_part=body,
                  shot_type=shot_type, xg=xg, set_piece=set_piece, tags=tags)
        self.advance(1.0)
        if outcome == "goal":
            self.close_possession("goal")
            self.advance(self.rng.uniform(45, 75), in_play=False)
            return (dfd, "kickoff", 52.5, 34.0)
        if outcome == "blocked":
            bx, by = x + self.rng.uniform(1, 4), y + self.rng.gauss(0, 3)
            blocker = self.pick_defender(dfd, bx, by)
            mx, my = mirror(bx, by)
            self.emit(dfd, blocker, "block", mx, my)
            r = self.rng.random()
            if r < 0.20:
                self.close_possession("corner")
                return (side, "corner", 104.0, 1.0 if y < GOAL_Y else 67.0)
            if r < 0.68:
                self.close_possession("lost")
                self.emit(dfd, blocker, "ball_recovery", mx, my)
                return (dfd, "recovery", mx, my)
            return (side, "loose_ball", clamp(bx - 6, 70, 100), by)   # continues same possession
        if outcome in ("saved", "post"):
            if outcome == "saved":
                self.emit(dfd, gk, "save", 3.0, clamp(L - gy, 30, 38), xg=xg)
            r = self.rng.random()
            if r < 0.55:
                self.close_possession("saved")
                return (dfd, "keeper_claim", 6.0, 34.0)
            if r < 0.70:
                self.close_possession("corner")
                return (side, "corner", 104.0, 1.0 if y < GOAL_Y else 67.0)
            return (side, "loose_ball", self.rng.uniform(90, 100), self.rng.uniform(24, 44))
        self.close_possession("off_target")
        self.advance(self.rng.uniform(25, 40), in_play=False)
        return (dfd, "goal_kick", 6.0, 34.0)

    def choose_shooter(self, side, exclude=None, header=False):
        ps, ws = [], []
        for p in side.on:
            if p is exclude or p["group"] == "GK":
                continue
            w = SHOOT_W[p["group"]] * SHOOT_ROLE_BONUS.get(p["role"], 1.0)
            w *= 1.0 + ((p.get("aerial" if header else "finishing") or 50) - 60) / 60.0
            if header and p["group"] == "DEF":
                w *= 1.8
            ps.append(p)
            ws.append(max(w, 0.02))
        return _weighted(self.rng, ps, ws)

    def choose_creator(self, side, exclude=None):
        ps, ws = [], []
        for p in side.on:
            if p is exclude or p["group"] == "GK":
                continue
            w = CREATE_W[p["group"]] * CREATE_ROLE_BONUS.get(p["role"], 1.0)
            w *= 1.0 + ((p.get("passing") or 60) - 65) / 50.0
            ps.append(p)
            ws.append(max(w, 0.02))
        return _weighted(self.rng, ps, ws)

    def assist_origin(self, kind, sx, sy, x, y):
        """Where the final pass starts, consistent with its type and the shot location (sx, sy).
        Stays near the carrier's position (x, y) where the geometry allows."""
        g = self.geo
        right = (y < GOAL_Y) if abs(y - GOAL_Y) > 4 else (g.random() < 0.5)
        if kind == "cross":        # from a wide area, level with or behind the shot
            px = clamp(max(x, 78.0) + g.uniform(-3, 3), 76, 101)
            py = g.uniform(2, 13) if right else g.uniform(55, 66)
        elif kind == "cutback":    # from near the byline, pulled back to a deeper shooter
            px = g.uniform(max(sx + 4, 99), 103.5)
            py = g.uniform(14, 22) if right else g.uniform(46, 54)
        elif kind == "through_ball":   # from behind the defensive line, played forward into the box
            px = clamp(min(x, sx - 12) + g.uniform(-3, 2), 58, sx - 9)
            py = clamp(sy + g.gauss(0, 9), 8, 60)
        else:                      # combination: short lay-off from just outside / beside the shooter
            px = clamp(sx - g.uniform(4, 13), 72, sx - 3)
            py = clamp(sy + g.gauss(0, 7), 6, 62)
        return px, py

    def award_penalty(self, side, victim, bx, by):
        """Defending side fouls `victim` at (bx, by) inside its own box: card decision, then the penalty."""
        rng = self.rng
        dfd = self.other(side)
        fouler = self.pick_defender(dfd, bx, by)
        fx, fy = mirror(bx, by)
        self.emit(dfd, fouler, "foul", fx, fy, recipient=victim, tags=["penalty_conceded"])
        if rng.random() < (0.35 if fouler["player_id"] not in dfd.yellows else 0.12):
            n = dfd.yellows.get(fouler["player_id"], 0) + 1
            dfd.yellows[fouler["player_id"]] = n
            if n >= 2:
                self.send_off(dfd, fouler, "second_yellow", fx, fy)
            else:
                self.emit(dfd, fouler, "card", fx, fy, card="yellow")
        elif rng.random() < 0.04:
            self.send_off(dfd, fouler, "red", fx, fy)
        self.emit(side, victim, "penalty_awarded", bx, by)
        self.advance(rng.uniform(60, 120), in_play=False)
        self.close_possession("penalty")
        return (side, "penalty", PEN_SPOT[0], PEN_SPOT[1])

    def create_chance(self, side, carrier, x, y, counter):
        """A chance has been created from (x, y). Build the final action and the shot."""
        rng = self.rng
        st = side.style
        if rng.random() < C.PENALTY_SHARE:
            # chance ends in a foul in the box
            bx, by = rng.uniform(BOX_X + 1, 103), rng.uniform(22, 46)
            return self.award_penalty(side, self.choose_shooter(side), bx, by)

        wide = y < 18 or y > 50
        tags = ["counter_attack"] if counter else []
        if wide and x > 78:
            kinds = ["cross", "cutback", "dribble_in"]
            kw = [0.45 + 0.35 * st["width"], 0.30, 0.15 + 0.3 * st["dribble"]]
        elif x < 84:
            kinds = ["through_ball", "combination", "long_shot"]
            kw = [0.25 + 0.25 * st["counter"] * (1.5 if counter else 1.0), 0.40, 1.50]
        else:
            kinds = ["through_ball", "combination", "dribble_in", "cutback", "long_shot"]
            kw = [0.25, 0.45, 0.15 + 0.3 * st["dribble"], 0.15, 0.35]
        kind = _weighted(rng, kinds, kw)

        header = False
        one_on_one = False
        if kind == "cross":
            header = rng.random() < 0.62
            sx, sy = rng.uniform(93, 101.5), clamp(rng.gauss(GOAL_Y, 5.5), 22, 46)
        elif kind == "cutback":
            sx, sy = rng.uniform(91, 97.5), clamp(rng.gauss(GOAL_Y, 4.5), 24, 44)
        elif kind == "through_ball":
            one_on_one = rng.random() < 0.55
            sx, sy = rng.uniform(90, 100), clamp(rng.gauss(GOAL_Y, 7), 18, 50)
        elif kind == "combination":
            sx, sy = rng.uniform(86, 98), clamp(rng.gauss(GOAL_Y, 8), 16, 52)
        elif kind == "dribble_in":
            sx, sy = rng.uniform(89, 100), clamp(rng.gauss(GOAL_Y + (8 if y > GOAL_Y else -8), 6), 14, 54)
        else:  # long shot
            u = rng.uniform(-4, 4)
            if x > 84.0:   # already near the box: step back out to shoot from somewhere in the 70-88 m band
                sx = 76.0 + (u + 4.0) / 8.0 * 12.4
            else:
                sx = reflect(x + u, 70.0, 88.4)
            sy = reflect(GOAL_Y + (y - GOAL_Y) * 0.6 + rng.gauss(0, 5), 12.0, 56.0)

        noise = rng.gauss(0.0, 0.90)
        xg = shot_xg(sx, sy, header=header, one_on_one=one_on_one, noise=noise)
        if counter:
            xg = clamp(xg * 1.12, 0.005, 0.95)

        if kind in ("long_shot", "dribble_in"):
            shooter = carrier if carrier["group"] != "GK" else self.choose_shooter(side)
            if kind == "dribble_in":
                dfd = self.other(side)
                beaten = self.pick_defender(dfd, sx, sy)
                self.emit(side, shooter, "take_on", x, y, sx, sy, outcome="success")
                self.advance(rng.uniform(1.5, 3.5))
            if kind == "long_shot" and math.hypot(sx - x, sy - y) > 5.0:
                self.emit(side, shooter, "carry", x, y, sx, sy)
            self.note_x(sx)
            return self.take_shot(side, shooter, sx, sy, xg, "right_foot" if rng.random() < 0.7 else "left_foot",
                                  "open_play", tags=tags + (["individual_play"] if kind == "dribble_in" else ["long_range"]))

        creator = carrier if (carrier["group"] != "GK" and rng.random() < 0.6) else self.choose_creator(side)
        shooter = self.choose_shooter(side, exclude=creator, header=header)
        ptype = {"cross": "cross", "cutback": "cutback", "through_ball": "through_ball", "combination": "short"}[kind]
        # main-stream draws kept (and used for timing) so outcomes match the calibrated engine
        px0, py0 = (x, y) if creator is carrier else (clamp(x + rng.uniform(-4, 4), 70, 102), clamp(y + rng.gauss(0, 6), 1, 67))
        px, py = self.assist_origin(kind, sx, sy, x, y)
        if math.hypot(px - x, py - y) > 5.0:
            if creator is carrier:
                self.emit(side, creator, "carry", x, y, px, py)
            else:   # the ball has to reach the creator first
                self.emit(side, carrier, "pass", x, y, px, py, outcome="complete", recipient=creator, pass_type="short")
                self.cur_poss["passes"] += 1
        self.emit(side, creator, "pass", px, py, sx, sy, outcome="complete", recipient=shooter, pass_type=ptype,
                  tags=["key_pass"] + tags)
        self.cur_poss["passes"] += 1
        self.note_x(sx)
        self.advance(1.2 + math.hypot(sx - px0, sy - py0) / 16.0)
        body = "head" if header else ("right_foot" if rng.random() < 0.68 else "left_foot")
        return self.take_shot(side, shooter, sx, sy, xg, body, "open_play", assister=creator,
                              tags=tags + (["one_on_one"] if one_on_one else []))

    # ------------------------------------------------------------ set pieces
    def corner(self, side, x, y):
        rng = self.rng
        taker = self.choose_creator(side)
        dfd = self.other(side)
        self.advance(rng.uniform(25, 45), in_play=False)
        if rng.random() < 0.18:   # short corner
            mate = self.pick_player(side, 96, y, exclude=taker)
            ex, ey = 96.0, clamp(y + (6 if y < GOAL_Y else -6), 1, 67)
            self.emit(side, taker, "pass", x, y, ex, ey, outcome="complete", recipient=mate, pass_type="short",
                      set_piece="corner")
            self.cur_poss["passes"] += 1
            self.advance(2.5)
            return ("continue", mate, ex, ey)
        tx, ty = rng.uniform(94, 102), clamp(rng.gauss(GOAL_Y, 5), 24, 44)
        r = rng.random()
        if r < 0.24 * (side.attack_eff() / dfd.defence_eff()) ** 0.15:
            header = rng.random() < 0.75
            shooter = self.choose_shooter(side, exclude=taker, header=header)
            self.emit(side, taker, "pass", x, y, tx, ty, outcome="complete", recipient=shooter, pass_type="cross",
                      set_piece="corner", tags=["key_pass"])
            self.cur_poss["passes"] += 1
            self.advance(2.0)
            xg = shot_xg(tx, ty, header=header, noise=rng.gauss(0, 0.5)) * 0.85
            return ("restart", self.take_shot(side, shooter, tx, ty, xg, "head" if header else "right_foot",
                                              "set_piece", set_piece="corner", assister=taker))
        self.emit(side, taker, "pass", x, y, tx, ty, outcome="incomplete", pass_type="cross", set_piece="corner")
        self.cur_poss["passes"] += 1
        self.advance(2.0)
        if r < 0.38:
            gk = dfd.keeper()
            self.emit(dfd, gk, "keeper_claim", *mirror(tx, ty))
            self.close_possession("keeper_claim")
            return ("restart", (dfd, "keeper_claim", 6.0, 34.0))
        clearer = self.pick_defender(dfd, tx, ty)
        cx, cy = mirror(tx, ty)
        ex, ey = cx + rng.uniform(15, 40), clamp(cy + rng.gauss(0, 12), 1, 67)
        self.emit(dfd, clearer, "clearance", cx, cy, ex, ey)
        self.advance(2.0)
        if rng.random() < 0.08:
            self.close_possession("corner")
            return ("restart", (side, "corner", 104.0, 1.0 if rng.random() < 0.5 else 67.0))
        if rng.random() < 0.45:   # attacking side recovers the second ball
            ax, ay = mirror(ex, ey)
            p = self.pick_player(side, ax, ay)
            self.emit(side, p, "ball_recovery", ax, ay)
            return ("continue", p, ax, ay)
        self.close_possession("lost")
        rec = self.pick_player(dfd, ex, ey)
        self.emit(dfd, rec, "ball_recovery", ex, ey)
        return ("restart", (dfd, "recovery", ex, ey))

    # ------------------------------------------------------------------ play
    def play(self, side, start, x, y):
        """Play one possession for `side` starting with restart `start` at (x, y). Returns the next restart."""
        rng = self.rng
        dfd = self.other(side)
        if start != "loose_ball" or self.cur_poss is None or self.cur_poss["team_id"] != side.id:
            if self.cur_poss is not None:
                self.close_possession("lost")
            self.open_possession(side, "throw_in_attacking" if (start == "throw_in" and x > 75) else start, x, y)

        st = side.style
        carrier = None
        counter_window_end = self.clock() + 15.0 if start in ("interception", "tackle", "recovery") else -1

        # restarts --------------------------------------------------------
        if start == "kickoff":
            taker = self.pick_player(side, 52, 34)
            mate = self.pick_player(side, 45, 34, exclude=taker)
            self.emit(side, taker, "pass", 52.5, 34, 44, 34 + rng.gauss(0, 6), outcome="complete", recipient=mate,
                      pass_type="short", set_piece="kickoff")
            self.cur_poss["passes"] += 1
            carrier, x, y = mate, 44.0, 34.0
            self.advance(2.0)
        elif start == "penalty":
            taker = max(side.outfield(), key=lambda p: (p.get("finishing") or 0) + (5 if p["group"] == "FWD" else 0))
            return self.take_shot(side, taker, x, y, C.PENALTY_XG, "right_foot", "penalty", set_piece="penalty")
        elif start == "corner":
            res = self.corner(side, x, y)
            if res[0] == "restart":
                return res[1]
            _, carrier, x, y = res
        elif start == "goal_kick" or start == "keeper_claim":
            gk = side.keeper()
            long = rng.random() < (0.25 + st["long_ball"] + 0.25 * dfd.press) * (1.0 if start == "goal_kick" else 0.6)
            if long:
                tx, ty = rng.uniform(50, 75), clamp(rng.gauss(34, 14), 3, 65)
            else:
                tx, ty = rng.uniform(12, 30), rng.choice([rng.uniform(5, 20), rng.uniform(48, 63)])
            ok = rng.random() < (0.48 + side.pass_q if long else 0.97)
            sp = "goal_kick" if start == "goal_kick" else None
            if ok:
                mate = self.pick_player(side, tx, ty)
                self.emit(side, gk, "pass", x, y, tx, ty, outcome="complete", recipient=mate,
                          pass_type="long" if long else "short", set_piece=sp)
                self.cur_poss["passes"] += 1
                carrier, x, y = mate, tx, ty
                self.advance(2.0 + (2.5 if long else 1.0))
            else:
                self.emit(side, gk, "pass", x, y, tx, ty, outcome="incomplete", pass_type="long", set_piece=sp)
                self.cur_poss["passes"] += 1
                self.advance(3.5)
                mx, my = mirror(tx, ty)
                won = self.pick_player(dfd, mx, my)
                self.emit(dfd, won, "interception", mx, my)
                self.close_possession("lost")
                return (dfd, "interception", mx, my)
        elif start == "free_kick":
            self.advance(rng.uniform(15, 35), in_play=False)
            if x > 76 and abs(y - GOAL_Y) < 16 and rng.random() < 0.30:
                taker = max(side.outfield(), key=lambda p: (p.get("finishing") or 0) + (p.get("passing") or 0))
                return self.take_shot(side, taker, x, y, free_kick_xg(x, y), "right_foot", "free_kick",
                                      set_piece="free_kick")
            if x > 72:
                # delivered like a corner
                res = self.corner(side, x, y)
                if res[0] == "restart":
                    return res[1]
                _, carrier, x, y = res
            else:
                carrier = self.pick_player(side, x, y, gk_ok=True)
        elif start in ("throw_in", "throw_in_attacking"):
            self.advance(rng.uniform(15, 30), in_play=False)
            carrier = self.pick_player(side, x, y)
            tx, ty = clamp(x + rng.gauss(2, 6), 2, 103), clamp(y + (8 if y < GOAL_Y else -8) + rng.gauss(0, 3), 2, 66)
            mate = self.pick_player(side, tx, ty, exclude=carrier)
            if rng.random() < 0.80:
                self.emit(side, carrier, "pass", x, y, tx, ty, outcome="complete", recipient=mate, pass_type="throw_in",
                          set_piece="throw_in")
                self.cur_poss["passes"] += 1
                carrier, x, y = mate, tx, ty
                self.advance(2.0)
            else:
                self.emit(side, carrier, "pass", x, y, tx, ty, outcome="incomplete", pass_type="throw_in",
                          set_piece="throw_in")
                self.cur_poss["passes"] += 1
                mx, my = mirror(tx, ty)
                won = self.pick_player(dfd, mx, my)
                self.emit(dfd, won, "interception", mx, my)
                self.advance(2.0)
                self.close_possession("lost")
                return (dfd, "interception", mx, my)
        else:  # interception, tackle, recovery, loose_ball
            carrier = self.pick_player(side, x, y, gk_ok=True)

        self.note_x(x)

        # open play loop ---------------------------------------------------
        while True:
            if self.t >= self.period_end:
                return None
            counter = self.clock() <= counter_window_end and self.cur_poss["start_x"] < 55
            # foul?
            boxed = in_box(x, y)
            fr = C.FOUL_RATE * (1.25 if 35 <= x < 75 else 0.9) * (0.75 + 0.5 * dfd.press)
            if boxed:
                fr *= C.BOX_FOUL_FACTOR      # defenders rarely risk a foul in their own box
            if rng.random() < fr:
                if boxed:
                    return self.award_penalty(side, carrier, x, y)
                self.foul(side, carrier, x, y)
                self.advance(rng.uniform(4, 10), in_play=False)
                if self.t >= self.period_end:
                    self.close_possession("period_end")
                    return None
                self.close_possession("foul_won")
                return (side, "free_kick", x, y)

            # chance creation in the final third
            if x >= 68:
                depth = (x - 68) / 37.0
                central = 1.0 - 0.55 * abs(y - GOAL_Y) / GOAL_Y
                h = self.params["chance_rate"] * (0.35 + 1.3 * depth) * central
                h *= side.attack_eff() * dfd.defence_eff() * self.gs_mult(side)
                if counter:
                    h *= 1.0 + 0.6 * st["counter"]
                if rng.random() < h:
                    return self.create_chance(side, carrier, x, y, counter)

            # choose an action
            r = rng.random()
            dribble_p = 0.08 + 0.18 * st["dribble"] + (0.06 if x > 60 else 0)
            if r < dribble_p and carrier["group"] != "GK":
                # take-on or progressive carry
                if rng.random() < 0.45:
                    dfdr = self.pick_defender(dfd, x + 3, y)
                    p_ok = 0.50 + ((carrier.get("dribbling") or 60) - 65) / 120.0 - 0.06 * dfd.press
                    ex, ey = clamp(x + rng.uniform(3, 9), 1, 104), clamp(y + rng.gauss(0, 4), 1, 67)
                    if rng.random() < p_ok:
                        self.emit(side, carrier, "take_on", x, y, ex, ey, outcome="success")
                        self.advance(rng.uniform(1.5, 3.0) * st["tempo"])
                        x, y = ex, ey
                        self.note_x(x)
                        continue
                    self.emit(side, carrier, "take_on", x, y, ex, ey, outcome="fail")
                    mx, my = mirror(x + 1.5, y)
                    self.emit(dfd, dfdr, "tackle", mx, my, outcome="won")
                    self.advance(1.5)
                    self.close_possession("lost")
                    return (dfd, "tackle", mx, my)
                ex = clamp(x + abs(rng.gauss(6, 5)), 1, 103)
                ey = reflect(y + rng.gauss(0, 5), 2.0, 66.0)
                self.emit(side, carrier, "carry", x, y, ex, ey)
                self.advance((2.0 + (ex - x) / 5.5) * st["tempo"])
                x, y = ex, ey
                self.note_x(x)
                continue

            # pass selection
            wide = y < 16 or y > 52
            if x > 76 and wide and rng.random() < 0.08 + 0.20 * st["width"]:
                ptype = "cross"
                ex, ey = rng.uniform(92, 102), clamp(rng.gauss(GOAL_Y, 6), 20, 48)
            elif x < 55 and rng.random() < st["long_ball"] * (1.0 + dfd.press * 0.6 if x < 35 else 1.0) * (1.6 if counter else 1.0):
                ptype = "long"
                ex, ey = clamp(x + rng.gauss(32, 8), 15, 100), clamp(rng.gauss(34, 18), 2, 66)
            elif x > 62 and rng.random() < 0.06 + 0.10 * st["counter"]:
                ptype = "through_ball"
                ex, ey = clamp(x + rng.uniform(10, 22), 70, 102), clamp(y + rng.gauss(0, 8), 8, 60)
            else:
                ptype = "short"
                fwd = st["fwd"] * (1.8 if counter else 1.0) - (2.0 if x > 85 else 0.0)
                ex = clamp(x + rng.gauss(fwd, 9.0), 1, 103)
                ey = reflect(y + 0.18 * (GOAL_Y - y) + rng.gauss(0, 11.0), 2.0, 66.0)
            dist = math.hypot(ex - x, ey - y)

            if ptype == "cross":
                p_ok = 0.24 + 0.04 * (side.attack_eff() - dfd.defence_eff())
            elif ptype == "through_ball":
                p_ok = 0.42 + side.pass_q
            else:
                p_ok = 0.99 - 0.0055 * dist + side.pass_q
                if ex > 70:
                    p_ok -= 0.05
                if in_box(ex, ey):
                    p_ok -= 0.12
                if x < 40:
                    p_ok -= 0.05 * dfd.press
                elif x < 70:
                    p_ok -= 0.025 * dfd.press
                if ptype == "long":
                    p_ok -= 0.18
            p_ok = clamp(p_ok, 0.12, 0.975)

            if ptype == "through_ball" and x > 68 and rng.random() < 0.09:
                self.emit(side, carrier, "pass", x, y, ex, ey, outcome="offside", pass_type=ptype)
                self.cur_poss["passes"] += 1
                self.emit(side, self.choose_shooter(side, exclude=carrier), "offside", ex, ey)
                self.advance(rng.uniform(12, 22), in_play=False)
                self.close_possession("offside")
                mx, my = mirror(ex, ey)
                return (dfd, "free_kick", mx, my)

            dur = (1.7 + dist / 13.0) * st["tempo"] * rng.uniform(0.8, 1.3)
            if rng.random() < p_ok:
                recv = self.pick_player(side, ex, ey, exclude=carrier, gk_ok=True)
                self.emit(side, carrier, "pass", x, y, ex, ey, outcome="complete", recipient=recv, pass_type=ptype)
                self.cur_poss["passes"] += 1
                self.advance(dur)
                carrier, x, y = recv, ex, ey
                self.note_x(x)
                continue

            # pass failed
            self.emit(side, carrier, "pass", x, y, ex, ey, outcome="incomplete", pass_type=ptype)
            self.cur_poss["passes"] += 1
            self.advance(dur)
            if ptype == "cross" or (ptype in ("long", "through_ball") and ex > 88):
                rr = rng.random()
                if rr < 0.22:
                    gk = dfd.keeper()
                    self.emit(dfd, gk, "keeper_claim", *mirror(ex, ey))
                    self.close_possession("keeper_claim")
                    return (dfd, "keeper_claim", 6.0, 34.0)
                clearer = self.pick_defender(dfd, ex, ey)
                cx, cy = mirror(ex, ey)
                fx, fy = cx + rng.uniform(12, 40), clamp(cy + rng.gauss(0, 14), 1, 67)
                self.emit(dfd, clearer, "clearance", cx, cy, fx, fy)
                self.advance(1.5)
                if rr < 0.32:
                    self.close_possession("corner")
                    return (side, "corner", 104.0, 1.0 if ey < GOAL_Y else 67.0)
                if rr < 0.62:
                    ax, ay = mirror(fx, fy)
                    if ax > 50:
                        p = self.pick_player(side, ax, ay)
                        self.emit(side, p, "ball_recovery", ax, ay)
                        carrier, x, y = p, ax, ay
                        continue
                if fy <= 1.5 or fy >= 66.5 or rng.random() < 0.15:
                    self.close_possession("out_of_play")
                    return (side, "throw_in", mirror(fx, fy)[0], 1.0 if mirror(fx, fy)[1] < GOAL_Y else 67.0)
                self.close_possession("lost")
                rec = self.pick_player(dfd, fx, fy)
                self.emit(dfd, rec, "ball_recovery", fx, fy)
                return (dfd, "recovery", fx, fy)

            # out of play or intercepted
            if rng.random() < 0.20:
                self.close_possession("out_of_play")
                if ex > 101:
                    self.advance(rng.uniform(25, 40), in_play=False)
                    return (dfd, "goal_kick", 6.0, 34.0)
                ox = clamp(ex, 2, 103)
                mx, my = mirror(ox, 1.0 if ey < GOAL_Y else 67.0)
                return (dfd, "throw_in", mx, my)
            f = rng.uniform(0.55, 1.0)
            ix, iy = x + (ex - x) * f, y + (ey - y) * f
            mx, my = mirror(ix, iy)
            won = self.pick_player(dfd, mx, my, gk_ok=True)
            kind = "interception" if rng.random() < 0.40 else "ball_recovery"
            self.emit(dfd, won, kind, mx, my)
            self.close_possession("lost")
            return (dfd, "interception" if kind == "interception" else "recovery", mx, my)


def simulate(match_id, home_side, away_side, seed, params=None):
    sim = MatchSim(match_id, home_side, away_side, seed, params)
    # substitution plan (minute marks); trailing sides tend to change earlier
    for side in (home_side, away_side):
        n = sim.rng.choices([2, 3, 4, 5], weights=[8, 25, 35, 32])[0]
        marks = sorted(sim.rng.uniform(52, 88) for _ in range(n))
        side.sub_plan = marks
    sim.run()
    return sim
