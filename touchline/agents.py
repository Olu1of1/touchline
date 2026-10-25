"""The language-model agents, built on Microsoft Agent Framework.

Two agents speak with a language model, and both go through the same
Agent Framework workflow, with the Verifier in the loop:

    request ──► Draft (Agent Framework Agent) ──► Verify ──► spoken
                   ▲                                 │
                   └──── feedback: what was wrong ───┘   (one retry, then a safe fallback)

* Colour agent: the co-commentator. Gets a fact pack, drafts one or two sentences
  for a stoppage.
* Ask Touchline: answers a listener's question at any moment ("how many shots have
  they had since half-time?"). It has tools that query the match *as it stands at
  that moment*, and nothing later. Every tool result goes into a ledger, and the
  Verifier only accepts numbers and names that appear in the ledger.

The model is an Azure OpenAI deployment in Microsoft Foundry. Without one,
Ask Touchline falls back to an offline router over the same tools, so the app
still works.
"""

import asyncio
import json
import re
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler, tool

from .data import load_match
from .describe import nth
from .pipeline import PRE_MATCH, league_name_tokens, to_broadcast
from .state import MatchState
from .verify import verify

MAX_ATTEMPTS = 2

COLOUR_INSTRUCTIONS = """You are the co-commentator on a live football radio broadcast for blind and low-vision listeners.
The play-by-play commentator handles the action; you add context during stoppages.
Rules:
- Use ONLY the facts you are given. Never invent numbers, names, history, injuries or quotes.
- Every number you say must appear in the facts. If unsure, leave the number out.
- One or two short sentences, at most 35 words. Plain spoken English, no lists.
- Don't repeat what was just said. Say "the better chances" rather than "xG".
- If nothing is worth saying, reply exactly: SKIP"""

ASK_INSTRUCTIONS = """You answer questions from a blind or low-vision fan who is listening to a live football match.
You have tools that describe the match as it stands right now. Nothing after this moment is known.
Rules:
- Always call tools to get facts. Use ONLY numbers and names that the tools returned.
- Answer in one to three short spoken sentences, at most 50 words. No lists, no markdown.
- If the tools can't answer the question, say so briefly and offer what you can tell them.
- Say "the better chances" instead of "xG" or "expected goals" unless they ask for xG.
- Never predict the result as a certainty; win chances are percentages from the model."""


# ----------------------------------------------------------------- the model
def _azure_configured():
    return all(os.environ.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"))


def chat_client():
    """An Agent Framework chat client for Azure OpenAI in Microsoft Foundry, or None if it isn't configured."""
    from agent_framework.openai import OpenAIChatCompletionClient
    if _azure_configured():
        return OpenAIChatCompletionClient(model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
                                          azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                                          api_key=os.environ["AZURE_OPENAI_API_KEY"],
                                          api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    return None


def model_name():
    if _azure_configured():
        return f"azure-openai:{os.environ.get('AZURE_OPENAI_DEPLOYMENT')}"
    return None


def chat_client_configured():
    return _azure_configured()


def explain_error(exc):
    """Turn SDK errors into something a person can act on."""
    msg = str(exc)
    if "has no attribute 'choices'" in msg:
        return ("The model endpoint answered with something that isn't a model reply. "
                "Check AZURE_OPENAI_ENDPOINT points at your Azure OpenAI resource.")
    return msg[:200]


# ------------------------------------------------------- workflow messages
@dataclass
class Request:
    prompt: str
    base_facts: dict
    attempt: int = 1
    feedback: str = ""
    previous: str = ""


@dataclass
class Draft:
    text: str
    request: Request
    ledger: list = field(default_factory=list)


@dataclass
class Result:
    text: str
    verdict: str                  # accepted | rejected | skipped | error
    attempts: int
    problems: list
    drafts: list
    tools_used: list
    latency_s: float = 0.0


class DraftExecutor(Executor):
    """Runs the Agent Framework agent; on a retry, tells it exactly what the Verifier rejected."""

    def __init__(self, agent, ledger, executor_id="draft"):
        super().__init__(id=executor_id)
        self.agent = agent
        self.ledger = ledger

    @handler
    async def draft(self, req: Request, ctx: WorkflowContext[Draft]) -> None:
        prompt = req.prompt
        if req.feedback:
            prompt += (f"\n\nYour previous answer was: \"{req.previous}\"\nThe fact checker rejected it: {req.feedback}. "
                       "Answer again using only facts from the tools or the fact pack. Leave out anything you can't support.")
        try:
            resp = await self.agent.run(prompt)
            text = (resp.text or "").strip().strip('"')
        except Exception as exc:                    # network or quota problems: let the verifier fall back
            text = f"__error__:{explain_error(exc)}"
        await ctx.send_message(Draft(text=text, request=req, ledger=list(self.ledger)))


class VerifyExecutor(Executor):
    """The Verifier as a workflow step: accept, or send the draft back once with the reasons."""

    def __init__(self, league_names, executor_id="verify"):
        super().__init__(id=executor_id)
        self.league_names = league_names
        self.drafts = []

    @handler
    async def check(self, d: Draft, ctx: WorkflowContext[Request, Result]) -> None:
        tools_used = sorted({entry["tool"] for entry in d.ledger})
        if d.text.startswith("__error__:"):
            await ctx.yield_output(Result("", "error", d.request.attempt, [d.text[10:]], self.drafts, tools_used))
            return
        self.drafts.append(d.text)
        if not d.text or d.text.upper().startswith("SKIP"):
            await ctx.yield_output(Result("", "skipped", d.request.attempt, [], self.drafts, tools_used))
            return
        ok, problems = verify(d.text, {"facts": d.request.base_facts, "tools": [e["result"] for e in d.ledger]},
                              self.league_names)
        if ok:
            await ctx.yield_output(Result(d.text, "accepted", d.request.attempt, [], self.drafts, tools_used))
        elif d.request.attempt < MAX_ATTEMPTS:
            r = d.request
            await ctx.send_message(Request(r.prompt, r.base_facts, r.attempt + 1, "; ".join(problems), d.text))
        else:
            await ctx.yield_output(Result("", "rejected", d.request.attempt, problems, self.drafts, tools_used))


def run_verified(agent, ledger, prompt, base_facts, league_names):
    """Build and run the Draft → Verify workflow once. Synchronous wrapper for the pipeline and server."""
    draft = DraftExecutor(agent, ledger)
    check = VerifyExecutor(league_names)
    wf = WorkflowBuilder(start_executor=draft).add_edge(draft, check).add_edge(check, draft).build()
    t0 = time.time()
    res = asyncio.run(wf.run(Request(prompt, base_facts)))
    outs = res.get_outputs()
    out = outs[-1] if outs else Result("", "error", 0, ["no output"], [], [])
    out.latency_s = round(time.time() - t0, 2)
    return out


# ------------------------------------------------------------ colour agent
class AgentFrameworkColour:
    """Backend for ColourAgent: drafts with an Agent Framework agent, checked by the Verifier workflow."""

    def __init__(self, client=None):
        self.client = client or chat_client()
        self.name = f"agent-framework/{model_name() or 'custom'}"
        self.league_names = league_name_tokens()

    def colour(self, trigger, facts, recently_said):
        gap = float(os.environ.get("TOUCHLINE_MIN_CALL_INTERVAL", "0"))
        wait = getattr(self, "_last_call", 0.0) + gap - time.time()
        if wait > 0:                              # optional spacing between calls, for low rate limits
            time.sleep(wait)
        self._last_call = time.time()
        agent = Agent(self.client, COLOUR_INSTRUCTIONS, name="co_commentator")
        prompt = json.dumps({"moment": trigger, "facts": facts, "recently_said": list(recently_said)[-4:]},
                            ensure_ascii=False)
        r = run_verified(agent, [], prompt, facts, self.league_names)
        # the ColourAgent re-checks and falls back to templates; report what the workflow decided
        self.last = r
        if r.verdict == "error":            # surface Azure problems (wrong key, quota) in the colour log
            raise RuntimeError(r.problems[0] if r.problems else "model error")
        text = r.text if r.verdict == "accepted" else ("SKIP" if r.verdict == "skipped" else (r.drafts[-1] if r.drafts else ""))
        return text, r.latency_s


# -------------------------------------------------------------- live view
@lru_cache(maxsize=8)
def _bundle(match_id):
    b = load_match(match_id)
    return b, to_broadcast(b.events)


class LiveView:
    """The match exactly as it stands at broadcast time `t`: nothing after it is visible."""

    def __init__(self, match_id, t):
        self.bundle, all_events = _bundle(match_id)
        self.events = [e for e in all_events if e["clock_s"] <= t]
        self.state = MatchState(self.bundle)
        for e in self.events:
            self.state.update(e)
        self.t = t

    def team_id(self, team):
        s, q = self.state, (team or "").strip().lower()
        for cid in (s.home_id, s.away_id):
            ti = s.team(cid)
            if q in (cid.lower(), ti.short.lower(), ti.name.lower()) or (q and q in ti.name.lower()):
                return cid
        if q in ("home", "hosts"):
            return s.home_id
        if q in ("away", "visitors"):
            return s.away_id
        return None

    def minute_of(self, e):
        return int(e["display_clock"].split("+")[0].rstrip("'"))


class MatchTools:
    """Tools for Ask Touchline. Each call is logged in the ledger the Verifier checks against."""

    def __init__(self, view):
        self.v = view
        self.ledger = []

    def _log(self, name, result):
        self.ledger.append({"tool": name, "result": result})
        return json.dumps(result, ensure_ascii=False)

    # --- the tools ---------------------------------------------------------
    def score_and_clock(self):
        s, b = self.v.state, self.v.bundle
        h, a = s.score()
        phase = "before kick-off" if not s.started else "full-time" if s.finished else \
            "half-time" if (s.last_event and s.last_event["type"] == "period_end") else "in play"
        minute = self.v.minute_of(s.last_event) if s.started and s.last_event else 0
        return self._log("score_and_clock", {"home": b.home.short, "home_goals": h, "away": b.away.short,
                                             "away_goals": a, "minute": minute, "phase": phase})

    def team_stats(self, team: str, period: str = "match"):
        cid = self.v.team_id(team)
        if not cid:
            return self._log("team_stats", {"error": f"no team called {team} in this match"})
        evs = self._period(period)
        mine = [e for e in evs if e["team_id"] == cid]
        shots = [e for e in mine if e["type"] == "shot"]
        passes = [e for e in mine if e["type"] == "pass"]
        opp_passes = [e for e in evs if e["team_id"] and e["team_id"] != cid and e["type"] == "pass"]
        poss = round(100 * len(passes) / max(1, len(passes) + len(opp_passes)))
        res = {"team": self.v.state.team(cid).short, "period": period, "shots": len(shots),
               "shots_on_target": sum(e["outcome"] in ("goal", "saved") for e in shots),
               "goals": sum(e["outcome"] == "goal" for e in shots),
               "big_chances": sum((e["xg"] or 0) >= 0.3 for e in shots),
               "xg": round(sum(e["xg"] or 0 for e in shots), 2),
               "possession_percent": poss,
               "corners": sum(1 for e in passes if e.get("set_piece") == "corner"),
               "fouls": sum(e["type"] == "foul" for e in mine),
               "yellow_cards": sum(e["type"] == "card" and e["card"] == "yellow" for e in mine),
               "red_cards": sum(e["type"] == "card" and e["card"] in ("red", "second_yellow") for e in mine),
               "offsides": sum(e["type"] == "offside" for e in mine)}
        return self._log("team_stats", res)

    def goals_so_far(self):
        s = self.v.state
        out = [{"scorer": s.name(e["player_id"]), "team": s.team(e["team_id"]).short, "minute": self.v.minute_of(e),
                "assisted_by": s.name(e["recipient_id"]) if e.get("recipient_id") else None,
                "penalty": e.get("shot_type") == "penalty"}
               for e in self.v.events if e["type"] == "shot" and e["outcome"] == "goal"]
        return self._log("goals_so_far", {"goals": out, "count": len(out)})

    def player(self, name: str):
        s, b = self.v.state, self.v.bundle
        q = (name or "").strip().lower()
        hits = [pid for pid, p in b.players.items() if q and (q == p["short"].lower() or q in p["name"].lower())]
        if not hits:
            return self._log("player", {"error": f"no player called {name} in either squad today"})
        out = []
        for pid in hits[:3]:
            p = b.players[pid]
            mine = [e for e in self.v.events if e["player_id"] == pid]
            shots = [e for e in mine if e["type"] == "shot"]
            assists = [e for e in self.v.events if e["type"] == "shot" and e["outcome"] == "goal" and e.get("recipient_id") == pid]
            on = any(pid in s.on_pitch[c] for c in s.on_pitch)
            out.append({"name": p["name"], "team": s.team(p["club_id"]).short, "position": p["role"],
                        "on_the_pitch_now": on, "goals_today": sum(e["outcome"] == "goal" for e in shots),
                        "shots_today": len(shots), "assists_today": len(assists),
                        "passes_today": sum(e["type"] == "pass" for e in mine),
                        "booked_today": any(e["type"] == "card" for e in mine),
                        "season_goals_before_today": b.season_goals.get(pid, 0)})
        return self._log("player", {"players": out})

    def cards_and_substitutions(self):
        s = self.v.state
        cards = [{"player": s.name(e["player_id"]), "team": s.team(e["team_id"]).short, "card": e["card"],
                  "minute": self.v.minute_of(e)} for e in self.v.events if e["type"] == "card"]
        subs = [{"on": s.name(e["recipient_id"]), "off": s.name(e["player_id"]), "team": s.team(e["team_id"]).short,
                 "minute": self.v.minute_of(e)} for e in self.v.events if e["type"] == "substitution"]
        return self._log("cards_and_substitutions", {"cards": cards, "substitutions": subs})

    def win_chances(self):
        s = self.v.state
        w = [round(100 * x) for x in s.wp]
        return self._log("win_chances", {"home": s.bundle.home.short, "home_win_percent": w[0], "draw_percent": w[1],
                                         "away": s.bundle.away.short, "away_win_percent": w[2],
                                         "note": "model estimate from the score, time left and season form"})

    def league_context(self):
        b = self.v.bundle

        def team(ti):
            d = {"team": ti.short, "form_last_results": ti.form}
            if ti.position:
                d.update(position_before_today=ti.position, points_before_today=ti.points, played=ti.played)
            return d
        res = {"matchweek": b.matchweek, "venue": b.venue, "attendance": b.attendance,
               "home": team(b.home), "away": team(b.away)}
        if b.previous_meeting:
            pm = b.previous_meeting
            res["earlier_meeting_this_season"] = {"home": self.v.state.team(pm["home_id"]).short, "home_goals": pm["home_goals"],
                                                  "away": self.v.state.team(pm["away_id"]).short, "away_goals": pm["away_goals"]}
        return self._log("league_context", res)

    def recent_moments(self, minutes: int = 5):
        s = self.v.state
        minutes = max(1, min(int(minutes or 5), 30))
        now = self.v.events[-1]["match_clock_s"] if self.v.events else 0
        out = []
        for e in self.v.events:
            if now - e["match_clock_s"] > minutes * 60:
                continue
            m = self.v.minute_of(e)
            if e["type"] == "shot":
                out.append({"minute": m, "what": "goal" if e["outcome"] == "goal" else f"shot, {e['outcome'].replace('_', ' ')}",
                            "player": s.name(e["player_id"]), "team": s.team(e["team_id"]).short,
                            "big_chance": (e["xg"] or 0) >= 0.3})
            elif e["type"] in ("card", "substitution", "penalty_awarded"):
                out.append({"minute": m, "what": e["card"] + " card" if e["type"] == "card" else e["type"].replace("_", " "),
                            "player": s.name(e["player_id"]), "team": s.team(e["team_id"]).short})
            elif e["type"] == "pass" and e.get("set_piece") == "corner":
                out.append({"minute": m, "what": "corner", "team": s.team(e["team_id"]).short})
        return self._log("recent_moments", {"last_minutes": minutes, "moments": out[-12:]})

    def _period(self, period):
        p = (period or "match").lower().replace(" ", "_")
        evs = self.v.events
        if p in ("first_half", "1st_half", "first"):
            return [e for e in evs if e["period"] == 1]
        if p in ("second_half", "2nd_half", "second", "since_half_time", "since_half-time"):
            return [e for e in evs if e["period"] == 2]
        if p.startswith("last_"):
            try:
                mins = int(p.split("_")[1])
            except ValueError:
                mins = 10
            now = evs[-1]["match_clock_s"] if evs else 0
            return [e for e in evs if now - e["match_clock_s"] <= mins * 60]
        return evs

    def as_agent_tools(self):
        return [
            tool(self.score_and_clock, name="score_and_clock", description="The score, minute and phase right now."),
            tool(self.team_stats, name="team_stats",
                 description="Shots, shots on target, goals, big chances, xG, possession %, corners, fouls, cards and offsides "
                             "for one team. period: 'match', 'first_half', 'second_half', or 'last_N_minutes' e.g. 'last_10_minutes'."),
            tool(self.goals_so_far, name="goals_so_far", description="Every goal so far: scorer, team, minute, assist, penalty."),
            tool(self.player, name="player",
                 description="A player's match so far (goals, shots, assists, passes, booked, on the pitch) and goals this season before today."),
            tool(self.cards_and_substitutions, name="cards_and_substitutions", description="Cards and substitutions so far."),
            tool(self.win_chances, name="win_chances", description="Model's current chances of a home win, draw and away win."),
            tool(self.league_context, name="league_context",
                 description="Table position, points and recent form before today, venue, crowd, and the earlier meeting this season."),
            tool(self.recent_moments, name="recent_moments", description="Shots, goals, cards, subs and corners in the last N minutes."),
        ]


# ----------------------------------------------------------- ask touchline
class AskTouchline:
    def __init__(self, client="auto"):
        self.client = chat_client() if client == "auto" else client
        self.league_names = league_name_tokens()

    @property
    def backend(self):
        return f"agent-framework/{model_name() or 'custom'}" if self.client else "offline router"

    def ask(self, match_id, t, question):
        view = LiveView(match_id, t)
        tools = MatchTools(view)
        b = view.bundle
        base = {"home": b.home.short, "away": b.away.short, "home_name": b.home.name, "away_name": b.away.name}
        if self.client:
            agent = Agent(self.client, ASK_INSTRUCTIONS, name="ask_touchline", tools=tools.as_agent_tools())
            r = run_verified(agent, tools.ledger, question, base, self.league_names)
            if r.verdict == "accepted":
                return self._out(r.text, "accepted", r, tools)
            if r.verdict == "skipped":
                return self._out("Sorry, I can't answer that one.", "skipped", r, tools)
            fallback = offline_answer(question, MatchTools(view))
            return self._out(fallback, r.verdict + "_fallback", r, tools)
        t0 = time.time()
        text = offline_answer(question, tools)
        r = Result(text, "offline", 1, [], [text], sorted({e["tool"] for e in tools.ledger}), round(time.time() - t0, 3))
        return self._out(text, "offline", r, tools)

    def _out(self, text, verdict, r, tools):
        return {"answer": text, "verdict": verdict, "attempts": r.attempts, "problems": r.problems,
                "drafts": r.drafts, "tools_used": r.tools_used or sorted({e["tool"] for e in tools.ledger}),
                "latency_s": r.latency_s, "backend": self.backend}


def _n(k, word):
    """'1 shot', '3 shots', '1 goal this season'."""
    return f"{k} {word}" if k == 1 else f"{k} {word}s"


def offline_answer(question, tools):
    """A keyword router over the same tools, used when no language model is configured or a draft fails."""
    q = question.lower()
    v = tools.v
    sc = json.loads(tools.score_and_clock())
    if sc["phase"] == "before kick-off" and not any(k in q for k in ("table", "form", "position", "who are", "crowd")):
        return "The match hasn't kicked off yet. Ask me about the table or recent form."

    def team_in_q():
        for cid in (v.state.home_id, v.state.away_id):
            ti = v.state.team(cid)
            if ti.short.lower() in q or ti.name.lower() in q:
                return ti.short
        return None

    period = "second_half" if ("second half" in q or "since half" in q) else "first_half" if "first half" in q else "match"
    asks_scorers = any(k in q for k in ("who scored", "scorer", "scored")) and "how many" not in q
    if not asks_scorers and (re.search(r"\bscore\b", q) or any(k in q for k in (
            "what's happening", "whats happening", "who's winning", "who is winning", "how long", "what minute", "time is it"))):
        when = f"after {sc['minute']} minutes" if sc["phase"] == "in play" else sc["phase"]
        return f"{sc['home']} {sc['home_goals']}, {sc['away']} {sc['away_goals']}, {when}."
    if any(k in q for k in ("who scored", "goals", "scorer", "scored")) and "how many" not in q:
        g = json.loads(tools.goals_so_far())["goals"]
        if not g:
            return "No goals yet."
        return " ".join(f"{x['scorer']} for {x['team']} in the {nth(x['minute'])} minute{' from the spot' if x['penalty'] else ''}."
                        for x in g)
    if any(k in q for k in ("win", "chance", "likely", "favourite")):
        w = json.loads(tools.win_chances())
        return f"Right now the model gives {w['home']} {w['home_win_percent']} percent, the draw {w['draw_percent']}, " \
               f"and {w['away']} {w['away_win_percent']}."
    if any(k in q for k in ("card", "booked", "booking", "sent off", "substitut", "sub ")):
        c = json.loads(tools.cards_and_substitutions())
        cards = ", ".join(f"{x['player']} ({x['card'].replace('_', ' ')}, {nth(x['minute'])} minute)" for x in c["cards"]) or "none"
        return f"Cards so far: {cards}. Substitutions made: {len(c['substitutions'])}."
    if any(k in q for k in ("table", "position", "form", "league")):
        lc = json.loads(tools.league_context())
        h, a = lc["home"], lc["away"]
        if "position_before_today" in h:
            return (f"{h['team']} started the day {nth(h['position_before_today'])} on {h['points_before_today']} points, "
                    f"{a['team']} {nth(a['position_before_today'])} on {a['points_before_today']}.")
        return "It's the first matchweek, so there's no table yet."
    if any(k in q for k in ("shot", "possession", "corner", "stat", "attempt", "foul", "offside")):
        teams = [team_in_q()] if team_in_q() else [v.state.team(v.state.home_id).short, v.state.team(v.state.away_id).short]
        parts = []
        for tn in teams:
            st = json.loads(tools.team_stats(tn, period))
            parts.append(f"{st['team']} {_n(st['shots'], 'shot')}, {st['shots_on_target']} on target, "
                         f"{st['possession_percent']} percent of the ball, {_n(st['corners'], 'corner')}")
        when = {"second_half": " in the second half", "first_half": " in the first half"}.get(period, "")
        return f"So far{when}: " + "; ".join(parts) + "."
    for pid, p in v.bundle.players.items():
        if p["short"].lower() in q or p["name"].lower() in q:
            pl = json.loads(tools.player(p["short"]))["players"][0]
            return (f"{pl['name']} of {pl['team']}: {_n(pl['goals_today'], 'goal')} and {_n(pl['shots_today'], 'shot')} today, "
                    f"{_n(pl['season_goals_before_today'], 'goal')} this season before today. "
                    f"{'Still on the pitch.' if pl['on_the_pitch_now'] else 'Not on the pitch now.'}")
    return f"{sc['home']} {sc['home_goals']}, {sc['away']} {sc['away_goals']}, {sc['phase']}. " \
           "You can ask about shots, possession, who scored, cards, players, the table, or who's likely to win."
