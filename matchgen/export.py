"""Write a simulated season to disk and derive the aggregate tables."""

import json
import os

import pandas as pd

from .pitch import L

EVENT_COLS = ["match_id", "event_id", "period", "clock_s", "period_s", "minute", "display_clock", "team_id",
              "player_id", "type", "outcome", "x", "y", "end_x", "end_y", "zone", "third", "channel",
              "recipient_id", "pass_type", "body_part", "shot_type", "xg", "set_piece", "card", "possession_id",
              "possession_team_id", "score_home", "score_away", "tags"]


def team_match_stats(matches, events, possessions):
    rows = []
    ev_by_match = dict(tuple(events.groupby("match_id")))
    pos_by_match = dict(tuple(possessions.groupby("match_id")))
    for m in matches.itertuples():
        em = ev_by_match[m.match_id]
        for side, opp, venue in ((m.home_id, m.away_id, "home"), (m.away_id, m.home_id, "away")):
            e = em[em.team_id == side]
            s = e[e.type == "shot"]
            p = e[e.type == "pass"]
            po = em[(em.team_id == opp) & (em.type == "pass")]
            gf = m.home_goals if venue == "home" else m.away_goals
            ga = m.away_goals if venue == "home" else m.home_goals
            # PPDA: opponent passes in their own 60% / our defensive actions in that area
            opp_build = int(((po.x < 0.6 * L)).sum())
            def_actions = int((e.type.isin(["tackle", "interception", "foul"]) & (e.x >= 0.4 * L)).sum())
            pos = pos_by_match[m.match_id]
            own_time = pos[pos.team_id == side].duration_s.sum()
            tot_time = pos.duration_s.sum()
            rows.append({
                "match_id": m.match_id, "matchweek": m.matchweek, "club_id": side, "opponent_id": opp, "venue": venue,
                "goals_for": gf, "goals_against": ga,
                "result": "W" if gf > ga else "L" if gf < ga else "D", "points": 3 if gf > ga else 1 if gf == ga else 0,
                "xg_for": round(s.xg.sum(), 3), "xg_against": round(m.away_xg if venue == "home" else m.home_xg, 3),
                "shots": len(s), "shots_on_target": int(s.outcome.isin(["goal", "saved"]).sum()),
                "big_chances": int(s.tags.fillna("").str.contains("big_chance").sum()),
                "passes": len(p), "passes_completed": int((p.outcome == "complete").sum()),
                "pass_accuracy": round((p.outcome == "complete").mean(), 3) if len(p) else None,
                "possession_pct": round(100 * len(p) / max(len(p) + len(po), 1), 1),
                "possession_time_pct": round(100 * own_time / tot_time, 1) if tot_time else None,
                "final_third_passes": int(((p.end_x >= 70) & (p.outcome == "complete")).sum()),
                "crosses": int((p.pass_type == "cross").sum()),
                "corners": int((p.set_piece == "corner").sum()),
                "fouls": int((e.type == "foul").sum()),
                "yellow_cards": int((e.card == "yellow").sum()),
                "red_cards": int(e.card.isin(["red", "second_yellow"]).sum()),
                "offsides": int((e.type == "offside").sum()),
                "tackles_won": int((e.type == "tackle").sum()),
                "interceptions": int((e.type == "interception").sum()),
                "clearances": int((e.type == "clearance").sum()),
                "saves": int((e.type == "save").sum()),
                "ppda": round(opp_build / def_actions, 2) if def_actions else None,
                "counter_attacks": int(((pos.team_id == side) & (pos.pattern == "counter_attack")).sum()),
                "high_turnovers": int(((pos.team_id == side) & (pos.pattern == "high_turnover")).sum()),
            })
    return pd.DataFrame(rows)


def player_match_stats(lineups, events):
    ev = events
    played = lineups[lineups.minutes > 0].copy()
    shots = ev[ev.type == "shot"]
    passes = ev[ev.type == "pass"]
    key = passes[passes.tags.fillna("").str.contains("key_pass")]
    shot_xg_by_assister = shots[shots.recipient_id.notna()].groupby(["match_id", "recipient_id"]).xg.sum()
    goal_assists = shots[(shots.outcome == "goal") & shots.recipient_id.notna()].groupby(["match_id", "recipient_id"]).size()

    def cnt(df, by="player_id"):
        return df.groupby(["match_id", by]).size()

    g = shots[shots.outcome == "goal"]
    agg = {
        "goals": cnt(g), "shots": cnt(shots), "shots_on_target": cnt(shots[shots.outcome.isin(["goal", "saved"])]),
        "xg": shots.groupby(["match_id", "player_id"]).xg.sum(), "passes": cnt(passes),
        "passes_completed": cnt(passes[passes.outcome == "complete"]), "key_passes": cnt(key),
        "take_ons_won": cnt(ev[(ev.type == "take_on") & (ev.outcome == "success")]),
        "tackles_won": cnt(ev[ev.type == "tackle"]), "interceptions": cnt(ev[ev.type == "interception"]),
        "recoveries": cnt(ev[ev.type == "ball_recovery"]), "clearances": cnt(ev[ev.type == "clearance"]),
        "blocks": cnt(ev[ev.type == "block"]), "fouls_committed": cnt(ev[ev.type == "foul"]),
        "fouls_won": cnt(ev[ev.type == "foul"], "recipient_id"), "saves": cnt(ev[ev.type == "save"]),
        "yellow_cards": cnt(ev[ev.card == "yellow"]), "red_cards": cnt(ev[ev.card.isin(["red", "second_yellow"])]),
    }
    idx = pd.MultiIndex.from_frame(played[["match_id", "player_id"]])
    for k, s in agg.items():
        s.index.names = ["match_id", "player_id"]
        played[k] = s.reindex(idx).fillna(0).values
    for k, s in (("assists", goal_assists), ("xa", shot_xg_by_assister)):
        s.index.names = ["match_id", "player_id"]
        played[k] = s.reindex(idx).fillna(0).values
    for c in agg:
        if c != "xg":
            played[c] = played[c].astype(int)
    played["assists"] = played["assists"].astype(int)
    played["xg"] = played["xg"].round(3)
    played["xa"] = played["xa"].round(3)
    return played.drop(columns=["on_clock_s", "off_clock_s"])


def standings(matches, clubs):
    rows = []
    table = {c["club_id"]: dict(club_id=c["club_id"], played=0, won=0, drawn=0, lost=0, goals_for=0,
                                goals_against=0, points=0, xg_for=0.0, xg_against=0.0) for c in clubs}
    by_mw = []
    for mw, grp in matches.sort_values("matchweek").groupby("matchweek"):
        for m in grp.itertuples():
            for side, gf, ga, xf, xa in ((m.home_id, m.home_goals, m.away_goals, m.home_xg, m.away_xg),
                                         (m.away_id, m.away_goals, m.home_goals, m.away_xg, m.home_xg)):
                t = table[side]
                t["played"] += 1
                t["goals_for"] += gf
                t["goals_against"] += ga
                t["xg_for"] += xf
                t["xg_against"] += xa
                if gf > ga:
                    t["won"] += 1
                    t["points"] += 3
                elif gf == ga:
                    t["drawn"] += 1
                    t["points"] += 1
                else:
                    t["lost"] += 1
        snap = sorted(table.values(), key=lambda t: (-t["points"], -(t["goals_for"] - t["goals_against"]),
                                                     -t["goals_for"], t["club_id"]))
        for pos, t in enumerate(snap, start=1):
            by_mw.append({"matchweek": mw, "position": pos, **t, "goal_difference": t["goals_for"] - t["goals_against"],
                          "xg_for": round(t["xg_for"], 2), "xg_against": round(t["xg_against"], 2)})
    df = pd.DataFrame(by_mw)
    final = df[df.matchweek == df.matchweek.max()].drop(columns="matchweek")
    return final, df


def write_season(season, out):
    os.makedirs(out, exist_ok=True)
    clubs = pd.DataFrame(season["clubs"])
    truth_cols = ["club_id", "attack_rating", "defence_rating"]
    clubs.drop(columns=["attack_rating", "defence_rating"]).to_csv(os.path.join(out, "clubs.csv"), index=False)
    clubs[truth_cols].to_csv(os.path.join(out, "ground_truth_club_ratings.csv"), index=False)
    players = pd.DataFrame(season["players"])
    players.to_csv(os.path.join(out, "players.csv"), index=False)
    pd.DataFrame(season["referees"]).to_csv(os.path.join(out, "referees.csv"), index=False)
    matches = pd.DataFrame(season["matches"])
    matches.to_csv(os.path.join(out, "matches.csv"), index=False)
    lineups = pd.DataFrame(season["lineups"])
    lineups.to_csv(os.path.join(out, "lineups.csv"), index=False)
    events = pd.DataFrame(season["events"])[EVENT_COLS]
    events.to_csv(os.path.join(out, "events.csv.gz"), index=False, compression="gzip")
    poss = pd.DataFrame(season["possessions"])
    poss.to_csv(os.path.join(out, "possessions.csv.gz"), index=False, compression="gzip")
    pd.DataFrame(season["suspensions"]).to_csv(os.path.join(out, "suspensions.csv"), index=False)

    tms = team_match_stats(matches, events, poss)
    tms.to_csv(os.path.join(out, "team_match_stats.csv"), index=False)
    pms = player_match_stats(lineups, events)
    pms.to_csv(os.path.join(out, "player_match_stats.csv"), index=False)
    final, by_mw = standings(matches, season["clubs"])
    names = {c["club_id"]: c["name"] for c in season["clubs"]}
    final.insert(1, "club", final.club_id.map(names))
    final.to_csv(os.path.join(out, "standings.csv"), index=False)
    by_mw.to_csv(os.path.join(out, "standings_by_matchweek.csv"), index=False)

    meta = dict(season["league"])
    meta.update({"generator_params": season["params"], "files": sorted(os.listdir(out)) + ["league.json"],
                 "coordinate_system": "105x68 m; each event in the acting team's attacking direction (x=0 own goal "
                                      "line, x=105 opponent goal line; y=0 acting team's right touchline)"})
    with open(os.path.join(out, "league.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return {"events": len(events), "possessions": len(poss), "matches": len(matches)}
