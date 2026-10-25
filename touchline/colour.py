"""Colour agent: the co-commentator. Adds context in stoppages and quiet spells.

For each moment it builds a fact pack from the live state (and only the live
state), then asks the language model for a line. The Verifier checks the line;
if it fails, or no model is configured, a template line built from the same
facts is used instead. Every decision is logged for the "under the hood" view.
"""

import random

from .describe import Line, nth
from .verify import verify

PRIORITY = {"prematch": 80, "half_time": 86, "full_time": 86, "substitution": 33, "quiet": 28, "stoppage": 30,
            "red_card": 40}
MAX_DELAY = {"prematch": 60, "half_time": 60, "full_time": 120, "substitution": 45, "quiet": 30, "stoppage": 12,
             "red_card": 40}
REASON_LINES = {
    "goal_kick": ["Goal kick to come.", "It'll be a goal kick."],
    "free_kick": ["{team} set themselves for the free kick.", "A pause while {team} take the free kick."],
    "booking": ["The referee has words.", "Play waits while the referee deals with that."],
    "offside": ["Free kick for offside.", "{team} will restart from the offside."],
    "keeper_has_it": ["The keeper takes his time.", "The keeper holds on to it."],
    "goal": ["{team} will restart from the centre circle.", "The players head back for the restart."],
    "substitution": [],
    "break": ["A short break in play.", "Play has stopped for a moment."],
}


class ColourAgent:
    def __init__(self, state, backend=None, seed=0, league_names=()):
        self.s = state
        self.backend = backend
        self.rng = random.Random(seed + 99)
        self.league_names = set(league_names)
        self.log = []
        self.last_at = -1e9
        self.used = {}          # template key -> last time used
        # Which moments use the model: all of them by default. To save calls, list just the big ones, e.g.
        # TOUCHLINE_LLM_MOMENTS=prematch,half_time,full_time,substitution (about 15 calls a match instead of ~150).
        import os
        spec = os.environ.get("TOUCHLINE_LLM_MOMENTS", "all")
        self.llm_moments = None if spec.strip() == "all" else {m.strip() for m in spec.split(",") if m.strip()}

    # ------------------------------------------------------------- facts
    def facts(self, trigger, extra=None):
        s, b = self.s, self.s.bundle
        h, a = s.score()
        sh, sa = s.stats[s.home_id], s.stats[s.away_id]

        def team(ti, goals):
            d = {"name": ti.short, "goals": goals}
            if ti.position:
                d.update(position_before=ti.position, points_before=ti.points)
            if ti.form:
                d.update(last_results=ti.form, wins_in_last_results=ti.form.count("W"),
                         number_of_last_results=len(ti.form))
            return d

        shown = int(s.display.split("+")[0].rstrip("'")) if s.started else 0
        f = {"minute": shown, "home": team(b.home, h), "away": team(b.away, a), "score": f"{h}-{a}",
             "matchweek": b.matchweek}
        if s.started:
            f["stats"] = {"possession_percent": [s.possession_pct(s.home_id), s.possession_pct(s.away_id)],
                          "shots": [sh.shots, sa.shots], "shots_on_target": [sh.on_target, sa.on_target],
                          "corners": [sh.corners, sa.corners]}
            gap = sh.xg - sa.xg
            if abs(gap) >= 0.4:
                f["better_chances"] = b.home.short if gap > 0 else b.away.short
            f["win_chance_percent"] = {"home": round(100 * s.wp[0]), "draw": round(100 * s.wp[1]),
                                       "away": round(100 * s.wp[2])}
        if s.goals:
            f["goals"] = [{"scorer": s.name(g["player"]), "team": s.team(g["team"]).short,
                           "minute": int(g["clock"].split("+")[0].rstrip("'"))} for g in s.goals]
        if extra:
            f.update(extra)
        return f

    def _prematch_extra(self):
        s, b = self.s, self.s.bundle
        f = {"venue": b.venue, "attendance": b.attendance,
             "win_chance_percent": {"home": round(100 * s.wp_pre[0]), "draw": round(100 * s.wp_pre[1]),
                                    "away": round(100 * s.wp_pre[2])}}
        scorers = sorted(((b.season_goals.get(p, 0), p) for cid in (s.home_id, s.away_id) for p in b.starters[cid]),
                         reverse=True)
        top = [{"player": s.name(p), "team": s.team(b.players[p]["club_id"]).short, "season_goals": g}
               for g, p in scorers[:2] if g >= 3]
        if top:
            f["players_to_watch"] = top
        if b.previous_meeting:
            pm = b.previous_meeting
            f["previous_meeting"] = {"home": s.team(pm["home_id"]).short, "away": s.team(pm["away_id"]).short,
                                     "home_goals": pm["home_goals"], "away_goals": pm["away_goals"]}
        return f

    # ------------------------------------------------------------ templates
    def template(self, trigger, f):
        hn, an = f["home"]["name"], f["away"]["name"]
        opts = []
        if trigger == "prematch":
            hp, ap = f["home"].get("position_before"), f["away"].get("position_before")
            if hp and ap:
                opts.append(f"{hn} start the day {nth(hp)} on {f['home']['points_before']} points; "
                            f"{an} are {nth(ap)} with {f['away']['points_before']}.")
            if f["home"].get("last_results") and f["away"].get("last_results"):
                opts.append(f"{hn} have won {f['home']['wins_in_last_results']} of their last "
                            f"{f['home']['number_of_last_results']}, {an} {f['away']['wins_in_last_results']} of their last "
                            f"{f['away']['number_of_last_results']}.")
            if f.get("players_to_watch"):
                p = f["players_to_watch"][0]
                opts.append(f"One to watch: {p['player']} of {p['team']}, {p['season_goals']} goals so far this season.")
            if f.get("previous_meeting"):
                pm = f["previous_meeting"]
                opts.append(f"When these two met earlier in the season, it finished {pm['home']} {pm['home_goals']}, "
                            f"{pm['away']} {pm['away_goals']}.")
            w = f["win_chance_percent"]
            fav = hn if w["home"] >= w["away"] else an
            opts.append(f"Our model makes {fav} favourites, with a {max(w['home'], w['away'])} percent chance of winning.")
            return " ".join(opts[:3]) if opts else None
        st = f.get("stats", {})
        if trigger == "substitution":
            on = f.get("on")
            if on and on.get("season_goals", 0) >= 2:
                return f"{on['name']} has {on['season_goals']} goals this season."
            return None
        if trigger in ("half_time", "full_time"):
            if st:
                opts.append(f"Shots: {hn} {st['shots'][0]}, {an} {st['shots'][1]}. "
                            f"On target: {st['shots_on_target'][0]} and {st['shots_on_target'][1]}.")
                poss = st["possession_percent"]
                lead = hn if poss[0] >= poss[1] else an
                opts.append(f"{lead} have had {max(poss)} percent of the ball.")
            if f.get("better_chances"):
                opts.append(f"{f['better_chances']} have had the better of the chances.")
            if trigger == "full_time" and "points_after" in f:
                pa = f["points_after"]
                opts.append(f"That result takes {hn} to {pa['home']} points and {an} to {pa['away']}.")
            return " ".join(opts[:3]) if opts else None
        if trigger in ("quiet", "stoppage"):
            facts_opts = []
            if st and f["minute"] >= 8:
                poss = st["possession_percent"]
                lead = hn if poss[0] >= poss[1] else an
                if max(poss) >= 53:
                    facts_opts.append(("poss", f"{lead} have had {max(poss)} percent of the ball so far."))
                if sum(st["shots"]) >= 3:
                    facts_opts.append(("shots", f"Shots so far: {hn} {st['shots'][0]}, {an} {st['shots'][1]}."))
                if sum(st["corners"]):
                    facts_opts.append(("corners", f"Corners: {hn} {st['corners'][0]}, {an} {st['corners'][1]}."))
            if f.get("better_chances"):
                facts_opts.append(("chances", f"{f['better_chances']} have created the better chances in this one."))
            if f.get("minutes_since_last_shot", 0) >= 6:
                facts_opts.append(("noshot", f"No shot from either side for {f['minutes_since_last_shot']} minutes now."))
            w = f.get("win_chance_percent")
            if w and f["minute"] >= 10:
                facts_opts.append(("wp", f"As it stands, {hn} have a {w['home']} percent chance of winning this, {an} {w['away']}."))
            if f["minute"] >= 5:
                h, a = f["home"]["goals"], f["away"]["goals"]
                facts_opts.append(("clock", f"{f['minute']} minutes gone. {hn} {h}, {an} {a}."))
            if f.get("goals"):
                g = f["goals"][-1]
                facts_opts.append((f"lastgoal:{g['scorer']}:{g['minute']}",
                                   f"{g['scorer']} with the last goal, in the {nth(g['minute'])} minute."))
            hp, ap = f["home"].get("position_before"), f["away"].get("position_before")
            if hp and ap:
                facts_opts.append(("table", f"{hn} came into this {nth(hp)} in the table, {an} {nth(ap)}."))
            if f["home"].get("last_results"):
                facts_opts.append(("form_h", f"{hn} had won {f['home']['wins_in_last_results']} of their last "
                                             f"{f['home']['number_of_last_results']} before today."))
            if f["away"].get("last_results"):
                facts_opts.append(("form_a", f"{an} had won {f['away']['wins_in_last_results']} of their last "
                                             f"{f['away']['number_of_last_results']} before today."))
            now_s = f["minute"] * 60
            def gap(k):          # static facts once a half; each goal recapped once; live stats every 6 min
                return 1e9 if k.startswith("lastgoal") else 2400 if k in ("table", "form_h", "form_a") else 360
            fresh = [(k, txt) for k, txt in facts_opts if now_s - self.used.get(k, -1e9) >= gap(k)]
            lead_in = ""
            if trigger == "stoppage" and f.get("first_in_stoppage", True):
                pool = REASON_LINES.get(f.get("reason"), [])
                if pool:
                    lead_in = self.rng.choice(pool).format(team=f.get("team", ""))
            if not fresh:
                return lead_in or None
            key, fact = self.rng.choice(fresh)
            self.used[key] = now_s
            return (lead_in + " " + fact).strip()
        if trigger == "red_card":
            return None
        return None

    # ---------------------------------------------------------------- make
    def make(self, trigger, t, recently_said=(), extra=None):
        f = self.facts(trigger, extra)
        if trigger == "prematch":
            f.update(self._prematch_extra())
        entry = {"t": round(t, 1), "trigger": trigger, "facts": f}
        text, latency, source = None, 0.0, "template"
        if self.backend and (self.llm_moments is None or trigger in self.llm_moments):
            try:
                proposed, latency = self.backend.colour(trigger, f, list(recently_said))
                entry["proposed"] = proposed
                wf = getattr(self.backend, "last", None)      # Agent Framework workflow details, if any
                if wf is not None:
                    entry["attempts"] = wf.attempts
                    entry["drafts"] = wf.drafts
                if proposed and proposed.upper() != "SKIP":
                    ok, problems = verify(proposed, f, self.league_names)
                    entry["verdict"] = "accepted" if ok else "rejected"
                    entry["problems"] = problems
                    if ok:
                        text, source = proposed, self.backend.name
                else:
                    entry["verdict"] = "skipped"
            except Exception as exc:
                entry["verdict"] = "error"
                entry["problems"] = [str(exc)[:200]]
        if text is None:
            text = self.template(trigger, f)
            if text:
                ok, problems = verify(text, f, self.league_names)
                entry["template_check"] = "passed" if ok else problems
                if not ok:
                    text = None
        entry["final"] = text
        entry["source"] = source if text else None
        entry["latency_s"] = round(latency, 2)
        self.log.append(entry)
        if not text:
            return []
        self.last_at = t
        return [Line(t + latency, text, f"colour_{trigger}", PRIORITY[trigger], MAX_DELAY[trigger],
                     excite=0.0, meta={"source": source})]
