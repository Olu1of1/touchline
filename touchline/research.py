"""Touchline Research: a season-wide stats desk for broadcasters, analysts and curious fans.

Ask a question in plain English ("Which teams have been luckiest?", "Top scorers
with fewer than 10 shots?"). An Agent Framework agent picks tools over the whole
season, every number it says is checked by the Verifier against what the tools
returned, and the answer arrives with its evidence: a table, a chart and the
list of tool calls.

Each tool returns a *result card*: a title, columns, rows and a suggested chart.
The page renders the card; the agent writes the short spoken or written answer.
"""

import json
import math
import os
import re
import time
from functools import lru_cache

import pandas as pd
from agent_framework import Agent, tool

from .agents import chat_client, model_name, run_verified
from .data import DATA_DIR, league
from .pipeline import league_name_tokens
from .winprob import _pois

RESEARCH_INSTRUCTIONS = """You are the research desk for a football broadcast. Producers, commentators and fans ask
about the whole season of a fictional league (the Synthetic Football League, 20 clubs, 38 matchweeks).
Rules:
- Always call tools to get facts. Use ONLY numbers and names the tools returned.
- Answer in two to four plain sentences: lead with the direct answer, then one line of context.
- The page shows the full table and chart from your last tool call, so don't list every row.
- Flag small samples honestly (e.g. a per-90 figure from few minutes, a rate from few shots).
- Say "expected goals (xG)" the first time you mention xG.
- If the tools can't answer, say what you can answer instead."""

PLAYER_STATS = {
    "goals": "goals", "assists": "assists", "xg": "xg", "xa": "xa", "shots": "shots",
    "shots_on_target": "shots_on_target", "key_passes": "key_passes", "goals_minus_xg": None,
    "goal_contributions": None, "xg_per90": None, "goals_per90": None, "tackles_won": "tackles_won",
    "interceptions": "interceptions", "recoveries": "recoveries", "take_ons_won": "take_ons_won",
    "yellow_cards": "yellow_cards", "red_cards": "red_cards", "minutes": "minutes", "saves": "saves",
    "passes_completed": "passes_completed",
}
LABELS = {"xg": "xG", "xa": "xA", "xg_per90": "xG per 90", "goals_per90": "goals per 90", "goals_minus_xg": "goals minus xG",
          "xg_for": "xG for", "xg_against": "xG against", "xg_difference": "xG difference", "ppda": "PPDA",
          "possession_pct": "possession %", "points_minus_expected": "points above expected", "pass_accuracy": "pass accuracy %"}


def label_of(stat):
    return LABELS.get(stat, stat.replace("_", " "))


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


TEAM_STATS = ["points", "goals_for", "goals_against", "goal_difference", "xg_for", "xg_against", "xg_difference",
              "points_minus_expected", "expected_points", "possession_pct", "ppda", "counter_attacks", "high_turnovers",
              "shots", "shots_on_target", "corners", "fouls", "yellow_cards", "red_cards", "pass_accuracy"]


# ----------------------------------------------------------------- data
@lru_cache(maxsize=1)
def season():
    lg = league()
    tms = pd.read_csv(os.path.join(DATA_DIR, "team_match_stats.csv"))
    m = lg["matches"].copy()
    xp_h, xp_a = [], []
    for r in m.itertuples():                  # expected points from each match's xG (Poisson goals)
        ph, pa = _pois(r.home_xg, 10), _pois(r.away_xg, 10)
        win = sum(ph[i] * pa[j] for i in range(11) for j in range(11) if i > j)
        draw = sum(ph[i] * pa[i] for i in range(11))
        loss = 1 - win - draw
        xp_h.append(3 * win + draw)
        xp_a.append(3 * loss + draw)
    m["xpts_home"], m["xpts_away"] = xp_h, xp_a
    return {"lg": lg, "tms": tms, "matches": m}


def clubs():
    return season()["lg"]["clubs"]


def club_id(name):
    q = (name or "").strip().lower()
    c = clubs()
    for cid, r in c.iterrows():
        if q in (cid.lower(), r["short_name"].lower(), r["name"].lower()):
            return cid
    for cid, r in c.iterrows():
        if q and (q in r["name"].lower() or r["short_name"].lower() in q):
            return cid
    return None


def short(cid):
    return clubs().loc[cid, "short_name"]


def _r(v, d=2):
    if hasattr(v, "item"):          # numpy scalars -> plain Python, so results serialise as JSON
        v = v.item()
    if isinstance(v, float):
        return round(v, d)
    return v


def card(tool_name, title, columns, rows, chart=None, note=None, summary=None):
    out = {"tool": tool_name, "title": title, "columns": columns,
           "rows": [[_r(v) for v in row] for row in rows]}
    if chart:
        out["chart"] = chart
    if note:
        out["note"] = note
    if summary:
        out["summary"] = summary
    return out


# ---------------------------------------------------------------- tools
class SeasonTools:
    """Season-wide tools. Every result is logged in the ledger the Verifier checks against."""

    def __init__(self):
        self.ledger = []

    def _log(self, result):
        self.ledger.append({"tool": result["tool"], "result": result})
        return json.dumps(result, ensure_ascii=False)

    def league_table(self, up_to_matchweek: int = 38):
        tb = season()["lg"]["table_by_mw"]
        mw = max(1, min(38, int(up_to_matchweek or 38)))
        t = tb[tb.matchweek == mw].sort_values("position")
        rows = [[int(r.position), short(r.club_id), int(r.played), int(r.won), int(r.drawn), int(r.lost),
                 int(r.goals_for), int(r.goals_against), int(r.goal_difference), int(r.points),
                 round(r.xg_for, 1), round(r.xg_against, 1)] for r in t.itertuples()]
        return self._log(card("league_table", f"League table after matchweek {mw}",
                              ["Pos", "Club", "P", "W", "D", "L", "GF", "GA", "GD", "Pts", "xG for", "xG against"], rows,
                              chart={"type": "bar", "x": "Club", "y": "Pts"}))

    def top_players(self, stat: str = "goals", limit: int = 10, club: str = "", position: str = "",
                    min_minutes: int = 0):
        stat = (stat or "goals").lower().replace(" ", "_")
        if stat not in PLAYER_STATS:
            return self._log({"tool": "top_players", "error": f"unknown stat {stat}",
                              "available": list(PLAYER_STATS)})
        pms = season()["lg"]["pms"]
        pl = season()["lg"]["players"]
        g = pms.groupby("player_id")[["minutes", "goals", "assists", "xg", "xa", "shots", "shots_on_target",
                                     "key_passes", "tackles_won", "interceptions", "recoveries", "take_ons_won",
                                     "yellow_cards", "red_cards", "saves", "passes_completed"]].sum()
        g["apps"] = pms.groupby("player_id").size()
        g = g.join(pl[["name", "club_id", "position_group"]])
        g["goals_minus_xg"] = g.goals - g.xg
        g["goal_contributions"] = g.goals + g.assists
        g["xg_per90"] = g.xg / g.minutes.clip(lower=1) * 90
        g["goals_per90"] = g.goals / g.minutes.clip(lower=1) * 90
        note = None
        if stat.endswith("per90") and not min_minutes:
            min_minutes = 900
            note = "Per-90 figures only include players with at least 900 minutes, to avoid tiny samples."
        if min_minutes:
            g = g[g.minutes >= int(min_minutes)]
        if club:
            cid = club_id(club)
            if not cid:
                return self._log({"tool": "top_players", "error": f"no club called {club}"})
            g = g[g.club_id == cid]
        if position:
            pos = position.upper()[:3]
            pos = {"STR": "FWD", "ATT": "FWD", "FOR": "FWD", "MID": "MID", "DEF": "DEF", "GOA": "GK", "KEE": "GK"}.get(pos, pos)
            g = g[g.position_group == pos]
        limit = max(1, min(int(limit or 10), 25))
        g = g.sort_values([stat, "minutes"], ascending=[False, True]).head(limit)
        label = label_of(stat)
        rows = [[r.name, short(r.club_id), r.position_group, int(r.apps), int(r.minutes), getattr(r, stat),
                 int(r.goals), round(r.xg, 1), int(r.assists), int(r.shots)] for r in g.itertuples()]
        return self._log(card("top_players", f"Top {len(rows)} by {label}" + (f", {short(club_id(club))}" if club else ""),
                              ["Player", "Club", "Pos", "Apps", "Minutes", label[0].upper() + label[1:], "Goals", "xG", "Assists", "Shots"],
                              rows, chart={"type": "bar", "x": "Player", "y": label[0].upper() + label[1:]}, note=note))

    def player_profile(self, name: str):
        pl = season()["lg"]["players"]
        q = (name or "").strip().lower()
        hits = pl[pl.name.str.lower().str.contains(re.escape(q))] if q else pl.iloc[0:0]
        if not len(hits):
            hits = pl[pl.short_name.str.lower() == q]
        if not len(hits):
            return self._log({"tool": "player_profile", "error": f"no player called {name}"})
        goals = season()["lg"]["pms"].groupby("player_id")[["goals", "minutes"]].sum()
        ranked = sorted(hits.index, key=lambda i: (-goals.goals.get(i, 0), -goals.minutes.get(i, 0)))
        pid = ranked[0]
        others = [pl.loc[i, "name"] + f" ({short(pl.loc[i, 'club_id'])})" for i in ranked[1:4]]
        p = pl.loc[pid]
        pms = season()["lg"]["pms"]
        mine = pms[pms.player_id == pid].merge(season()["matches"][["match_id", "matchweek", "home_id", "away_id"]],
                                               on="match_id")
        tot = mine[["minutes", "goals", "assists", "xg", "xa", "shots", "shots_on_target", "key_passes",
                    "tackles_won", "interceptions", "yellow_cards", "red_cards"]].sum()
        by_mw = mine.sort_values("matchweek")
        rows = []
        for r in by_mw.itertuples():
            opp = r.away_id if r.home_id == p.club_id else r.home_id
            rows.append([int(r.matchweek), short(opp), "H" if r.home_id == p.club_id else "A", round(r.minutes),
                         int(r.goals), int(r.assists), round(r.xg, 2), int(r.shots)])
        summary = {"player": p["name"], "club": short(p.club_id), "position": p.primary_role, "age": int(p.age),
                   "nationality": p.nationality, "appearances": int(len(mine)), "minutes": int(tot.minutes),
                   "goals": int(tot.goals), "assists": int(tot.assists), "xg": round(tot.xg, 1), "xa": round(tot.xa, 1),
                   "shots": int(tot.shots), "shots_on_target": int(tot.shots_on_target), "key_passes": int(tot.key_passes),
                   "goals_minus_xg": round(tot.goals - tot.xg, 1), "yellow_cards": int(tot.yellow_cards),
                   "red_cards": int(tot.red_cards)}
        if others:
            summary["also_matching"] = others
        return self._log(card("player_profile", f"{p['name']} ({short(p.club_id)}), match by match",
                              ["MW", "Opponent", "Venue", "Minutes", "Goals", "Assists", "xG", "Shots"], rows,
                              chart={"type": "bar", "x": "MW", "y": "xG"}, summary=summary))

    def team_profile(self, club: str):
        cid = club_id(club)
        if not cid:
            return self._log({"tool": "team_profile", "error": f"no club called {club}"})
        tms = season()["tms"]
        mine = tms[tms.club_id == cid]
        m = season()["matches"]
        xp = sum(m[m.home_id == cid].xpts_home) + sum(m[m.away_id == cid].xpts_away)
        tb = season()["lg"]["table_by_mw"]
        fin = tb[(tb.matchweek == 38) & (tb.club_id == cid)].iloc[0]
        home, away = mine[mine.venue == "home"], mine[mine.venue == "away"]
        c = clubs().loc[cid]
        pms = season()["lg"]["pms"]
        pl = season()["lg"]["players"]
        g = pms[pms.club_id == cid].groupby("player_id").goals.sum().sort_values(ascending=False).head(3)
        summary = {"club": c["name"], "short": c["short_name"], "style": c["style"], "formation": c["formation"],
                   "final_position": int(fin.position), "points": int(fin.points), "expected_points": round(xp, 1),
                   "points_minus_expected": round(fin.points - xp, 1), "goals_for": int(fin.goals_for),
                   "goals_against": int(fin.goals_against), "xg_for": round(fin.xg_for, 1),
                   "xg_against": round(fin.xg_against, 1), "possession_pct": round(mine.possession_pct.mean(), 1),
                   "ppda": round(mine.ppda.mean(), 1), "home_points": int(home.points.sum()),
                   "away_points": int(away.points.sum()),
                   "top_scorers": [{"player": pl.loc[pid, "name"], "goals": int(v)} for pid, v in g.items()]}
        rows = [["Home", len(home), int(home.points.sum()), int(home.goals_for.sum()), int(home.goals_against.sum()),
                 round(home.xg_for.sum(), 1), round(home.xg_against.sum(), 1), round(home.possession_pct.mean(), 1)],
                ["Away", len(away), int(away.points.sum()), int(away.goals_for.sum()), int(away.goals_against.sum()),
                 round(away.xg_for.sum(), 1), round(away.xg_against.sum(), 1), round(away.possession_pct.mean(), 1)]]
        return self._log(card("team_profile", f"{c['name']}: home and away",
                              ["Venue", "Played", "Points", "GF", "GA", "xG for", "xG against", "Possession %"], rows,
                              chart={"type": "bar", "x": "Venue", "y": "Points"}, summary=summary))

    def rank_teams(self, stat: str = "points"):
        stat = (stat or "points").lower().replace(" ", "_").replace("%", "_pct")
        if stat not in TEAM_STATS:
            return self._log({"tool": "rank_teams", "error": f"unknown stat {stat}", "available": TEAM_STATS})
        tms = season()["tms"]
        m = season()["matches"]
        agg = tms.groupby("club_id").agg(points=("points", "sum"), goals_for=("goals_for", "sum"),
                                         goals_against=("goals_against", "sum"), xg_for=("xg_for", "sum"),
                                         xg_against=("xg_against", "sum"), possession_pct=("possession_pct", "mean"),
                                         ppda=("ppda", "mean"), counter_attacks=("counter_attacks", "sum"),
                                         high_turnovers=("high_turnovers", "sum"), shots=("shots", "sum"),
                                         shots_on_target=("shots_on_target", "sum"), corners=("corners", "sum"),
                                         fouls=("fouls", "sum"), yellow_cards=("yellow_cards", "sum"),
                                         red_cards=("red_cards", "sum"), pass_accuracy=("pass_accuracy", "mean"))
        xp = pd.concat([m[["home_id", "xpts_home"]].rename(columns={"home_id": "club_id", "xpts_home": "xp"}),
                        m[["away_id", "xpts_away"]].rename(columns={"away_id": "club_id", "xpts_away": "xp"})])
        agg["expected_points"] = xp.groupby("club_id").xp.sum()
        agg["points_minus_expected"] = agg.points - agg.expected_points
        agg["goal_difference"] = agg.goals_for - agg.goals_against
        agg["xg_difference"] = agg.xg_for - agg.xg_against
        agg["pass_accuracy"] = agg.pass_accuracy * 100
        asc = stat in ("ppda", "goals_against", "xg_against")          # lower is better
        agg = agg.sort_values(stat, ascending=asc)
        label = label_of(stat)
        rows = [[i + 1, short(cid), round(getattr(r, stat), 1), int(r.points), round(r.expected_points, 1),
                 int(r.goals_for), int(r.goals_against), round(r.xg_for, 1), round(r.xg_against, 1)]
                for i, (cid, r) in enumerate(agg.iterrows())]
        diverging = stat in ("points_minus_expected", "goal_difference", "xg_difference")
        note = None
        if stat == "points_minus_expected":
            note = ("Expected points: each match's xG turned into win, draw and loss chances, then points. "
                    "Above zero means more points than the chances created and conceded would usually earn.")
        if stat == "ppda":
            note = "PPDA: opponent passes per defensive action. Lower means more intense pressing."
        return self._log(card("rank_teams", f"Clubs ranked by {label}",
                              ["Rank", "Club", label[0].upper() + label[1:], "Pts", "xPts", "GF", "GA", "xG for", "xG against"],
                              rows, chart={"type": "diverging" if diverging else "bar", "x": "Club", "y": label[0].upper() + label[1:]},
                              note=note))

    def head_to_head(self, club_a: str, club_b: str):
        a, b = club_id(club_a), club_id(club_b)
        if not (a and b):
            return self._log({"tool": "head_to_head", "error": "need two clubs from this league"})
        m = season()["matches"]
        mm = m[((m.home_id == a) & (m.away_id == b)) | ((m.home_id == b) & (m.away_id == a))].sort_values("matchweek")
        rows = [[int(r.matchweek), r.date, short(r.home_id), int(r.home_goals), int(r.away_goals), short(r.away_id),
                 round(r.home_xg, 2), round(r.away_xg, 2), r.match_id] for r in mm.itertuples()]
        return self._log(card("head_to_head", f"{short(a)} v {short(b)} this season",
                              ["MW", "Date", "Home", "Home goals", "Away goals", "Away", "Home xG", "Away xG", "Match"], rows))

    def results(self, club: str = "", matchweek: int = 0):
        m = season()["matches"]
        if club:
            cid = club_id(club)
            if not cid:
                return self._log({"tool": "results", "error": f"no club called {club}"})
            m = m[(m.home_id == cid) | (m.away_id == cid)]
        if matchweek:
            m = m[m.matchweek == int(matchweek)]
        m = m.sort_values(["matchweek", "date"]).tail(38)
        rows = [[int(r.matchweek), short(r.home_id), int(r.home_goals), int(r.away_goals), short(r.away_id),
                 round(r.home_xg, 2), round(r.away_xg, 2), r.match_id] for r in m.itertuples()]
        title = f"{short(club_id(club))} results" if club else f"Matchweek {matchweek} results" if matchweek else "Results"
        return self._log(card("results", title, ["MW", "Home", "HG", "AG", "Away", "Home xG", "Away xG", "Match"], rows))

    def position_history(self, clubs_list: str = ""):
        tb = season()["lg"]["table_by_mw"]
        names = [c.strip() for c in re.split(r",| and ", clubs_list or "") if c.strip()]
        ids = [club_id(n) for n in names if club_id(n)]
        if not ids:
            fin = tb[tb.matchweek == 38].sort_values("position")
            ids = list(fin.club_id.head(4))
        ids = ids[:4]
        piv = tb[tb.club_id.isin(ids)].pivot(index="matchweek", columns="club_id", values="position")
        pts = tb[tb.club_id.isin(ids)].pivot(index="matchweek", columns="club_id", values="points")
        rows = [[int(mw)] + [int(piv.loc[mw, c]) for c in ids] + [int(pts.loc[mw, c]) for c in ids] for mw in piv.index]
        cols = ["MW"] + [f"{short(c)} position" for c in ids] + [f"{short(c)} points" for c in ids]
        return self._log(card("position_history", "League position by matchweek: " + ", ".join(short(c) for c in ids),
                              cols, rows, chart={"type": "line", "x": "MW", "series": [f"{short(c)} position" for c in ids],
                                                 "invert": True}))

    def as_agent_tools(self):
        return [
            tool(self.league_table, name="league_table", description="The league table after any matchweek (1-38)."),
            tool(self.top_players, name="top_players",
                 description="Player leaderboard. stat: " + ", ".join(PLAYER_STATS) +
                             ". Optional club, position (forward/midfielder/defender/goalkeeper), min_minutes, limit."),
            tool(self.player_profile, name="player_profile", description="A player's season totals and match-by-match record."),
            tool(self.team_profile, name="team_profile",
                 description="A club's season: final position, points vs expected points, home/away split, style, top scorers."),
            tool(self.rank_teams, name="rank_teams",
                 description="Rank all 20 clubs by a season stat: " + ", ".join(TEAM_STATS) +
                             ". points_minus_expected measures luck (points above what their chances usually earn)."),
            tool(self.head_to_head, name="head_to_head", description="Both meetings between two clubs this season."),
            tool(self.results, name="results", description="Results for one club, or for one matchweek."),
            tool(self.position_history, name="position_history",
                 description="League position by matchweek for up to 4 clubs (comma-separated). Empty = the final top 4."),
        ]


# ------------------------------------------------------------- the desk
class ResearchDesk:
    def __init__(self, client="auto"):
        self.client = chat_client() if client == "auto" else client
        self.league_names = league_name_tokens()

    @property
    def backend(self):
        return f"agent-framework/{model_name() or 'custom'}" if self.client else "offline router"

    def ask(self, question, history=()):
        tools = SeasonTools()
        base = {"league": "Synthetic Football League", "clubs": list(clubs()["short_name"]), "matchweeks": 38}
        t0 = time.time()
        if self.client:
            prompt = question
            if history:
                recent = "\n".join(f"Q: {h['q']}\nA: {h['a']}" for h in list(history)[-3:])
                prompt = f"Earlier in this conversation:\n{recent}\n\nNew question: {question}"
            agent = Agent(self.client, RESEARCH_INSTRUCTIONS, name="research_desk", tools=tools.as_agent_tools())
            r = run_verified(agent, tools.ledger, prompt, base, self.league_names)
            if r.verdict == "accepted":
                return self._out(r.text, "accepted", r.attempts, r.problems, r.drafts, tools, r.latency_s)
            fallback_tools = SeasonTools()
            text = offline_research(question, fallback_tools)
            return self._out(text, f"{r.verdict}_fallback", r.attempts, r.problems, r.drafts, fallback_tools, r.latency_s)
        text = offline_research(question, tools)
        return self._out(text, "offline", 1, [], [text], tools, round(time.time() - t0, 3))

    def _out(self, text, verdict, attempts, problems, drafts, tools, latency):
        cards = [e["result"] for e in tools.ledger if "rows" in e["result"]]
        return {"answer": text, "verdict": verdict, "attempts": attempts, "problems": problems, "drafts": drafts,
                "tools_used": [e["tool"] for e in tools.ledger], "cards": cards[-2:], "latency_s": latency,
                "backend": self.backend}


# ------------------------------------------------------- offline router
def _nth(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _clubs_in(q):
    found = []
    for cid, r in clubs().iterrows():
        for nm in (r["name"].lower(), r["short_name"].lower()):
            i = q.find(nm)
            if i >= 0:
                found.append((i, cid))
                break
    return [c for _, c in sorted(found)]


def _player_in(q):
    pl = season()["lg"]["players"]
    for pid, r in pl.iterrows():
        if r["name"].lower() in q:
            return r["name"]
    for pid, r in pl.iterrows():
        sn = r["short_name"].lower()
        if len(sn) > 3 and re.search(rf"\b{re.escape(sn)}\b", q):
            return r["short_name"]          # player_profile picks the most prominent player with this name
    return None


def offline_research(question, tools):
    """Keyword routing over the same tools. Used without a model, or when the model's drafts fail verification."""
    q = question.lower()
    cl = _clubs_in(q)
    mw = re.search(r"(?:matchweek|week|mw)\s*(\d{1,2})", q)
    mwn = int(mw.group(1)) if mw else None

    if len(cl) >= 2 and any(k in q for k in (" v ", " vs", "against", "head to head", "head-to-head", "met", "meet")):
        res = json.loads(tools.head_to_head(short(cl[0]), short(cl[1])))
        if not res["rows"]:
            return f"{short(cl[0])} and {short(cl[1])} didn't meet this season."
        parts = [f"matchweek {r[0]}: {r[2]} {r[3]}, {r[5]} {r[4]}" for r in res["rows"]]
        return f"They met {len(parts)} times. " + "; ".join(parts) + "."
    if any(k in q for k in ("lucky", "luck", "overperform", "over-perform", "underperform", "expected points", "deserve")):
        res = json.loads(tools.rank_teams("points_minus_expected"))
        top, bot = res["rows"][0], res["rows"][-1]
        return (f"{top[1]} were the luckiest: {top[3]} points from chances that would usually earn about {top[4]}, "
                f"so {top[2]:.1f} more than expected. {bot[1]} were the unluckiest, {abs(bot[2]):.1f} points short: "
                f"{bot[3]} points from chances worth about {bot[4]}.")
    if any(k in q for k in ("title race", "position history", "over the season", "race for")):
        res = json.loads(tools.position_history(", ".join(short(c) for c in cl)))
        names = [c.replace(" position", "") for c in res["columns"][1:1 + len(res["chart"]["series"])]]
        weeks_top = {n: sum(1 for r in res["rows"] if r[1 + i] == 1) for i, n in enumerate(names)}
        leader = max(weeks_top, key=weeks_top.get)
        final = next(n for i, n in enumerate(names) if res["rows"][-1][1 + i] == 1) if any(
            res["rows"][-1][1 + i] == 1 for i in range(len(names))) else None
        changes = sum(1 for a, b in zip(res["rows"], res["rows"][1:])
                      if [a[1 + i] for i in range(len(names))].index(min(a[1:1 + len(names)])) !=
                      [b[1 + i] for i in range(len(names))].index(min(b[1:1 + len(names)])))
        text = f"{leader} spent {weeks_top[leader]} of 38 matchweeks top of the table"
        others = [f"{n} {w}" for n, w in weeks_top.items() if n != leader and w]
        if others:
            text += f" ({', '.join(others)})"
        text += f". The lead changed hands {changes} times"
        return text + (f", and {final} finished top." if final else ".")
    if any(k in q for k in ("table", "standings", "league position", "who won the league", "champions", "relegat")):
        res = json.loads(tools.league_table(mwn or 38))
        r = res["rows"]
        if "relegat" in q:
            return f"Relegated: {r[17][1]} ({r[17][9]} points), {r[18][1]} ({r[18][9]}) and {r[19][1]} ({r[19][9]})."
        done = (mwn or 38) == 38
        if r[0][9] == r[1][9]:
            verb = "won the league" if done else "lead"
            if r[0][8] != r[1][8]:
                return (f"{r[0][1]} {verb} on goal difference after matchweek {mwn or 38}: level with {r[1][1]} on "
                        f"{r[0][9]} points, but {r[0][8]:+} against {r[1][8]:+}.")
            if r[0][6] != r[1][6]:
                return (f"{r[0][1]} {verb} on goals scored after matchweek {mwn or 38}: level with {r[1][1]} on "
                        f"{r[0][9]} points and a goal difference of {r[0][8]:+}, but with {r[0][6]} goals to {r[1][6]}.")
            return f"{r[0][1]} and {r[1][1]} are level on points, goal difference and goals after matchweek {mwn or 38}."
        return (f"{r[0][1]} {'won the league' if done else 'top the table after matchweek ' + str(mwn)} with {r[0][9]} points, "
                f"ahead of {r[1][1]} on {r[1][9]}.")
    for stat, words in (("ppda", ("press", "ppda")), ("possession_pct", ("possession",)),
                        ("counter_attacks", ("counter",)), ("xg_difference", ("xg difference", "xgd")),
                        ("goals_against", ("best defence", "fewest goals conceded", "conceded")),
                        ("yellow_cards", ("dirtiest", "most cards", "yellow cards"))):
        if any(w in q for w in words) and not _player_in(q) and ("team" in q or "club" in q or "which" in q or not cl):
            res = json.loads(tools.rank_teams(stat))
            r = res["rows"][0]
            col = res["columns"][2]
            col = col if col.startswith(("xG", "PPDA")) else col[0].lower() + col[1:]
            return f"{r[1]} lead the league on {col}: {r[2]}. The full ranking is below."
    pstat = None
    for stat, words in (("assists", ("assist",)), ("xg_per90", ("per 90", "per90")), ("goals_minus_xg", ("finisher", "clinical")),
                        ("xg", ("xg", "expected goals")), ("key_passes", ("key pass", "creator", "creative")),
                        ("tackles_won", ("tackle",)), ("interceptions", ("intercept",)), ("saves", ("saves", "goalkeeper")),
                        ("goals", ("scorer", "goals", "golden boot", "scored"))):
        if any(w in q for w in words):
            pstat = stat
            break
    pname = _player_in(q)
    if pname and not (pstat and any(k in q for k in ("top", "most", "best", "leading"))):
        res = json.loads(tools.player_profile(pname))
        s = res["summary"]
        also = f" There's also {', '.join(s['also_matching'])}." if s.get("also_matching") else ""
        return (f"{s['player']} ({s['club']}, {s['position']}): {plural(s['goals'], 'goal')} and "
                f"{plural(s['assists'], 'assist')} in {s['appearances']} appearances, from {s['xg']} expected goals (xG) "
                f"on {plural(s['shots'], 'shot')}.{also}")
    if pstat:
        club = short(cl[0]) if cl else ""
        res = json.loads(tools.top_players(pstat, 10, club))
        r = res["rows"][0]
        label = res["columns"][5]
        label = label if label.startswith("xG") else label[0].lower() + label[1:]
        extra = f" {res['note']}" if res.get("note") else ""
        return f"{r[0]} ({r[1]}) leads on {label} with {r[5]}, ahead of {res['rows'][1][0]} on {res['rows'][1][5]}.{extra}"
    if cl:
        res = json.loads(tools.team_profile(short(cl[0])))
        s = res["summary"]
        return (f"{s['club']} finished {_nth(s['final_position'])} with {s['points']} points, "
                f"{s['points_minus_expected']:+} against expected points. They scored {s['goals_for']} from "
                f"{s['xg_for']} xG and conceded {s['goals_against']} from {s['xg_against']}.")
    res = json.loads(tools.league_table(38))
    r = res["rows"]
    return (f"I'm not sure what you're after, so here's the final table: {r[0][1]} won the league with {r[0][9]} points. "
            "Try asking about top scorers, the luckiest teams, a club, a player, or a head-to-head.")
