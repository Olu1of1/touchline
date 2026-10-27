"""Who is on the pitch, and every touch of the ball, for the pitch view.

The synthetic league records events (who touched the ball, where), not tracking data. The player
draws all 22 players by estimating each one's position from their role and the ball, and pulls a
player to the exact spot whenever the data says they touched it. This module supplies the facts;
the estimate lives in web/app.js and is labelled as an estimate on the page.
"""

from functools import lru_cache

from .data import league, load_match
from .pipeline import HT_BREAK, PRE_MATCH, to_broadcast
from .state import to_listener

SKIP = {"substitution", "card", "period_start", "period_end", "match_end"}
FOLLOWS_BALL = {"carry", "take_on"}          # the player moves with the ball until the next touch


@lru_cache(maxsize=16)
def players_payload(match_id):
    b = load_match(match_id)
    lg = league()
    lu = lg["lineups"]
    lu = lu[(lu.match_id == match_id) & lu.on_clock_s.notna()]
    p2 = min((e["clock_s"] for e in b.events if e["period"] == 2), default=1e9)

    def bt(c):
        return round(float(c) + PRE_MATCH + (HT_BREAK if c >= p2 else 0.0), 1)

    rows = list(lu.itertuples())
    role = {r.player_id: r.role for r in rows if isinstance(r.role, str)}
    # a substitute takes the role of the player he replaced
    for r in rows:
        if r.player_id not in role:
            off = [o for o in rows if o.club_id == r.club_id and o.exit == "substituted"
                   and abs(float(o.off_clock_s) - float(r.on_clock_s)) < 0.5 and o.player_id in role]
            role[r.player_id] = role[off[0].player_id] if off else b.players[r.player_id]["role"]

    index, players = {}, []
    for r in rows:
        info = b.players[r.player_id]
        index[r.player_id] = len(players)
        players.append({"id": r.player_id, "team": 0 if r.club_id == b.home.club_id else 1,
                        "num": info["number"], "name": info["short"], "role": role[r.player_id],
                        "on": bt(r.on_clock_s), "off": bt(r.off_clock_s) if r.off_clock_s == r.off_clock_s else 1e9,
                        "exit": r.exit})

    touches = []
    for e in to_broadcast(b.events):
        pid = e.get("player_id")
        if e["type"] in SKIP or pid not in index or e.get("x") is None:
            continue
        home = e["team_id"] == b.home.club_id
        x, y = to_listener(e["x"], e["y"], home)
        follows = e["type"] in FOLLOWS_BALL and e.get("end_x") is not None
        ex, ey = to_listener(e["end_x"], e["end_y"], home) if follows else (x, y)
        touches.append([e["clock_s"], index[pid], round(x, 1), round(y, 1), round(ex, 1), round(ey, 1)])
    return {"players": players, "touches": touches}
