"""Loads the synthetic league and assembles a causal 'match bundle'.

A bundle contains only what a broadcaster would know at kick-off (squads,
lineups, table and form before this match) plus the event stream, which the
pipeline consumes in order.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd

DATA_DIR = os.environ.get("TOUCHLINE_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))


@lru_cache(maxsize=1)
def league():
    d = DATA_DIR
    if not os.path.exists(os.path.join(d, "events.csv.gz")):
        raise FileNotFoundError(f"No league data in {os.path.abspath(d)}. Run: python -m matchgen.season --out data")
    return {
        "events": pd.read_csv(os.path.join(d, "events.csv.gz")),
        "matches": pd.read_csv(os.path.join(d, "matches.csv")),
        "clubs": pd.read_csv(os.path.join(d, "clubs.csv")).set_index("club_id"),
        "players": pd.read_csv(os.path.join(d, "players.csv")).set_index("player_id"),
        "lineups": pd.read_csv(os.path.join(d, "lineups.csv")),
        "table_by_mw": pd.read_csv(os.path.join(d, "standings_by_matchweek.csv")),
        "pms": pd.read_csv(os.path.join(d, "player_match_stats.csv")),
    }


@dataclass
class TeamInfo:
    club_id: str
    name: str
    short: str
    style: str
    formation: str
    position: int = None        # league position before kick-off
    points: int = 0
    played: int = 0
    form: str = ""              # last five results before this match, oldest first, e.g. "WDLWW"
    xg_for_pg: float = None     # season-to-date xG per game before this match
    xg_against_pg: float = None
    goals_for: int = 0
    goals_against: int = 0
    keeper_id: str = None


@dataclass
class MatchBundle:
    match_id: str
    matchweek: int
    date: str
    kickoff: str
    venue: str
    attendance: int
    referee_id: str
    home: TeamInfo
    away: TeamInfo
    events: list
    players: dict                       # player_id -> {name, short, club_id, role, number}
    season_goals: dict = field(default_factory=dict)   # player_id -> goals before this match
    season_assists: dict = field(default_factory=dict)
    starters: dict = field(default_factory=dict)       # club_id -> [player_id]
    previous_meeting: dict = None


def _team(lg, club_id, mw, match_id):
    c = lg["clubs"].loc[club_id]
    t = TeamInfo(club_id, c["name"], c["short_name"], c["style"], c["formation"])
    tb = lg["table_by_mw"]
    if mw > 1:
        row = tb[(tb.matchweek == mw - 1) & (tb.club_id == club_id)].iloc[0]
        t.position, t.points, t.played = int(row.position), int(row.points), int(row.played)
        t.goals_for, t.goals_against = int(row.goals_for), int(row.goals_against)
        if row.played:
            t.xg_for_pg, t.xg_against_pg = row.xg_for / row.played, row.xg_against / row.played
    m = lg["matches"]
    prev = m[((m.home_id == club_id) | (m.away_id == club_id)) & (m.matchweek < mw)].sort_values("matchweek").tail(5)
    res = []
    for r in prev.itertuples():
        gf, ga = (r.home_goals, r.away_goals) if r.home_id == club_id else (r.away_goals, r.home_goals)
        res.append("W" if gf > ga else "L" if gf < ga else "D")
    t.form = "".join(res)
    lu = lg["lineups"]
    gk = lu[(lu.match_id == match_id) & (lu.club_id == club_id) & lu.starter & (lu.role == "GK")]
    t.keeper_id = gk.player_id.iloc[0] if len(gk) else None
    return t


def load_match(match_id):
    lg = league()
    m = lg["matches"].set_index("match_id").loc[match_id]
    mw = int(m.matchweek)
    ev = lg["events"][lg["events"].match_id == match_id].sort_values("event_id")
    ev = ev.astype(object).where(ev.notna(), None).to_dict("records")
    lu = lg["lineups"][lg["lineups"].match_id == match_id]
    pl = lg["players"]
    players = {}
    for r in lu.itertuples():
        p = pl.loc[r.player_id]
        players[r.player_id] = {"name": p["name"], "short": p["short_name"], "club_id": r.club_id,
                                "role": r.role if isinstance(r.role, str) else p["primary_role"],
                                "number": int(p["shirt_number"]), "group": p["position_group"]}
    # two players with the same surname in one match: say their full names
    counts = {}
    for v in players.values():
        counts[v["short"]] = counts.get(v["short"], 0) + 1
    for v in players.values():
        if counts[v["short"]] > 1:
            v["short"] = v["name"]
    starters = {cid: list(lu[(lu.club_id == cid) & lu.starter].player_id) for cid in (m.home_id, m.away_id)}

    # season-to-date scoring, strictly before this matchweek
    pms = lg["pms"]
    before = pms[pms.match_id.isin(lg["matches"][lg["matches"].matchweek < mw].match_id)]
    g = before.groupby("player_id")[["goals", "assists"]].sum()
    season_goals = {pid: int(g.goals.get(pid, 0)) for pid in players}
    season_assists = {pid: int(g.assists.get(pid, 0)) for pid in players}

    mm = lg["matches"]
    pm = mm[(mm.matchweek < mw) & (((mm.home_id == m.home_id) & (mm.away_id == m.away_id)) |
                                   ((mm.home_id == m.away_id) & (mm.away_id == m.home_id)))]
    prev_meeting = None
    if len(pm):
        r = pm.iloc[-1]
        prev_meeting = {"home_id": r.home_id, "away_id": r.away_id, "home_goals": int(r.home_goals),
                        "away_goals": int(r.away_goals), "matchweek": int(r.matchweek)}

    return MatchBundle(
        match_id=match_id, matchweek=mw, date=m.date, kickoff=m.kickoff_local, venue=m.venue,
        attendance=int(m.attendance), referee_id=m.referee_id,
        home=_team(lg, m.home_id, mw, match_id), away=_team(lg, m.away_id, mw, match_id),
        events=ev, players=players, season_goals=season_goals, season_assists=season_assists,
        starters=starters, previous_meeting=prev_meeting,
    )


def match_list():
    lg = league()
    m = lg["matches"].copy()
    names = lg["clubs"]["short_name"]
    out = []
    for r in m.itertuples():
        out.append({"match_id": r.match_id, "matchweek": int(r.matchweek), "date": r.date,
                    "home": names[r.home_id], "away": names[r.away_id], "home_id": r.home_id, "away_id": r.away_id})
    return out
