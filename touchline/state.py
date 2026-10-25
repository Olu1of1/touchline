"""Causal match-state tracker: what is true right now, given the events so far.

Two coordinate frames are used:
  * attacking frame (as in the data): x=105 is the goal the acting team attacks.
  * listener frame: fixed for the whole match, home team attacking to the
    listener's RIGHT (x=105), away team to the LEFT. Teams do not swap ends in
    the listener frame; a constant mental map is easier to follow by ear.
"""

from dataclasses import dataclass, field

from . import winprob

L, W = 105.0, 68.0


def to_listener(x, y, team_is_home):
    if x is None:
        return None, None
    return (x, y) if team_is_home else (L - x, W - y)


@dataclass
class TeamStats:
    goals: int = 0
    shots: int = 0
    on_target: int = 0
    xg: float = 0.0
    big_chances: int = 0
    passes: int = 0
    passes_completed: int = 0
    corners: int = 0
    fouls: int = 0
    yellows: int = 0
    reds: int = 0
    offsides: int = 0
    saves: int = 0
    possession_s: float = 0.0
    last_shot_clock: float = None


@dataclass
class MatchState:
    bundle: object
    clock: float = 0.0
    period: int = 1
    display: str = "1'"
    minute: int = 0
    started: bool = False
    finished: bool = False
    poss_team: str = None
    poss_id: int = None
    poss_start_clock: float = 0.0
    poss_start_x: float = None          # attacking frame of the possessing team
    poss_start_type: str = None
    poss_max_x: float = 0.0
    poss_passes: int = 0
    ball_x: float = 52.5                # listener frame
    ball_y: float = 34.0
    ball_ax: float = 52.5               # attacking frame of the team in possession
    ball_ay: float = 34.0
    ball_player: str = None
    last_action: dict = None            # most recent on-ball action: type, player, recipient, team, pass_type
    last_event_clock: float = 0.0
    last_event: dict = None
    dead_ball: bool = True              # between a goal / whistle and the restart
    stats: dict = field(default_factory=dict)
    keepers: dict = field(default_factory=dict)
    on_pitch: dict = field(default_factory=dict)
    goals: list = field(default_factory=list)
    cards: list = field(default_factory=list)
    match_goals: dict = field(default_factory=dict)     # player_id -> goals this match
    wp: tuple = (0.0, 0.0, 0.0)
    wp_pre: tuple = (0.0, 0.0, 0.0)
    rates: tuple = (1.5, 1.2)
    _last_clock: float = 0.0

    def __post_init__(self):
        b = self.bundle
        self.home_id, self.away_id = b.home.club_id, b.away.club_id
        self.stats = {self.home_id: TeamStats(), self.away_id: TeamStats()}
        self.keepers = {self.home_id: b.home.keeper_id, self.away_id: b.away.keeper_id}
        self.on_pitch = {cid: set(b.starters[cid]) for cid in (self.home_id, self.away_id)}
        self.rates = winprob.pre_match_rates(b.home, b.away)
        self.wp = self.wp_pre = winprob.probabilities(*self.rates, 0, 0, 0.0)

    # ------------------------------------------------------------ helpers
    def team(self, club_id):
        return self.bundle.home if club_id == self.home_id else self.bundle.away

    def other(self, club_id):
        return self.away_id if club_id == self.home_id else self.home_id

    def is_home(self, club_id):
        return club_id == self.home_id

    def score(self):
        return self.stats[self.home_id].goals, self.stats[self.away_id].goals

    def name(self, pid, full=False):
        p = self.bundle.players.get(pid)
        if not p:
            return "a player"
        return p["name"] if full else p["short"]

    def possession_pct(self, club_id):
        h, a = self.stats[self.home_id].possession_s, self.stats[self.away_id].possession_s
        tot = h + a
        if tot <= 0:
            return 50
        return round(100 * self.stats[club_id].possession_s / tot)

    def danger(self):
        """0..1: how threatening the current ball position is for the team in possession."""
        x, y = self.ball_ax, self.ball_ay
        if x is None or self.poss_team is None:
            return 0.0
        d = max(0.0, (x - 60.0) / 45.0)
        central = 1.0 - min(1.0, abs(y - 34.0) / 34.0) * 0.5
        return round(min(1.0, d * d * central * 1.3), 3)

    def snapshot(self):
        h, a = self.score()
        sh, sa = self.stats[self.home_id], self.stats[self.away_id]
        return {
            "t": round(self.clock, 1), "clock": self.display, "period": self.period, "score": [h, a],
            "wp": [round(v, 3) for v in self.wp],
            "poss": self.poss_team, "ball": [round(self.ball_x, 1), round(self.ball_y, 1)],
            "danger": self.danger(),
            "possession_pct": [self.possession_pct(self.home_id), self.possession_pct(self.away_id)],
            "shots": [sh.shots, sa.shots], "on_target": [sh.on_target, sa.on_target],
            "xg": [round(sh.xg, 2), round(sa.xg, 2)], "corners": [sh.corners, sa.corners],
            "cards": [[sh.yellows, sh.reds], [sa.yellows, sa.reds]],
        }

    # --------------------------------------------------------------- update
    def update(self, e):
        """Advance the state with one event. Returns a dict of derived facts about the event."""
        info = {}
        t = e["clock_s"]                                  # broadcast time (includes the half-time break)
        t_match = e.get("match_clock_s", t)               # elapsed playing time, for win probability
        if self.poss_team is not None and 0 < t - self._last_clock < 20:
            self.stats[self.poss_team].possession_s += t - self._last_clock
        self._last_clock = t
        self.clock, self.period, self.display, self.minute = t, e["period"], e["display_clock"], e["minute"]
        typ, team, pid = e["type"], e["team_id"], e["player_id"]

        if typ == "period_start":
            self.started = True
        if typ in ("period_end", "match_end", "penalty_awarded") or (typ == "shot" and e["outcome"] == "goal"):
            self.dead_ball = True
        elif typ in ("pass", "carry", "take_on") and team:
            self.dead_ball = False
        if typ == "match_end":
            self.finished = True

        if e.get("possession_team_id") and e.get("possession_id") != self.poss_id:
            self.poss_id = e["possession_id"]
            self.poss_team = e["possession_team_id"]
            self.poss_start_clock = t
            self.poss_passes = 0
            self.poss_start_x = e["x"] if (e["x"] is not None and team == self.poss_team) else None
            self.poss_max_x = self.poss_start_x or 0.0
            info["new_possession"] = True

        if e["x"] is not None and team is not None:
            ex, ey = (e["end_x"], e["end_y"]) if e["end_x"] is not None and e.get("outcome") in (None, "complete", "success") \
                else (e["x"], e["y"])
            if typ == "shot":
                ex, ey = e["x"], e["y"]
            if team == self.poss_team:
                self.ball_ax, self.ball_ay = ex, ey
                if self.poss_start_x is None:
                    self.poss_start_x = e["x"]
                self.poss_max_x = max(self.poss_max_x, ex)
            elif typ in ("interception", "tackle", "ball_recovery", "keeper_claim"):
                # the ball has been won: the winning side now has it, in its own attacking frame
                self.poss_team = team
                self.ball_ax, self.ball_ay = ex, ey
            else:   # other defending actions: express in the possessing team's frame
                self.ball_ax, self.ball_ay = L - ex, W - ey
            self.ball_x, self.ball_y = to_listener(ex, ey, self.is_home(team))
            self.ball_player = e["recipient_id"] if (typ == "pass" and e.get("outcome") == "complete") else pid

        if team and typ in ("pass", "carry", "take_on", "interception", "tackle", "ball_recovery", "keeper_claim"):
            self.last_action = {"type": typ, "player": pid, "recipient": e.get("recipient_id"), "team": team,
                                "outcome": e.get("outcome"), "pass_type": e.get("pass_type"),
                                "dx": (e["end_x"] - e["x"]) if (e["end_x"] is not None and e["x"] is not None) else 0.0}
        self.last_event_clock = t
        self.last_event = e

        st = self.stats.get(team)
        if typ == "pass" and st:
            st.passes += 1
            st.passes_completed += e["outcome"] == "complete"
            if team == self.poss_team:
                self.poss_passes += 1
            if e.get("set_piece") == "corner":
                st.corners += 1
        elif typ == "shot":
            st.shots += 1
            st.xg += e["xg"] or 0.0
            st.last_shot_clock = t
            if e["outcome"] in ("goal", "saved"):
                st.on_target += 1
            if (e["xg"] or 0) >= 0.30:
                st.big_chances += 1
            if e["outcome"] == "goal":
                before = self.wp
                st.goals += 1
                self.match_goals[pid] = self.match_goals.get(pid, 0) + 1
                self.goals.append({"t": t, "clock": self.display, "team": team, "player": pid,
                                   "assist": e["recipient_id"], "score": list(self.score())})
                info["wp_before"] = before
        elif typ == "foul" and st:
            st.fouls += 1
        elif typ == "offside" and st:
            st.offsides += 1
        elif typ == "save" and st:
            st.saves += 1
        elif typ == "card" and st:
            if e["card"] == "yellow":
                st.yellows += 1
            else:
                st.reds += 1
                self.on_pitch[team].discard(pid)
                if self.keepers.get(team) == pid:
                    self.keepers[team] = None
            self.cards.append({"t": t, "team": team, "player": pid, "card": e["card"]})
        elif typ == "substitution":
            self.on_pitch[team].discard(pid)
            self.on_pitch[team].add(e["recipient_id"])
            if e.get("outcome") == "emergency_keeper" or self.keepers.get(team) == pid:
                self.keepers[team] = e["recipient_id"]

        h, a = self.score()
        reds = (self.stats[self.home_id].reds, self.stats[self.away_id].reds)
        self.wp = winprob.probabilities(*self.rates, h, a, t_match, *reds)
        return info
