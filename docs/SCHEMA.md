# Data schema

All files live in `data/`. IDs: clubs use 3-letter codes (`COR`), players `P0001`, matches `M001`, referees `R01`.

## Coordinate system

- Pitch is **105 m × 68 m**.
- Every event's `x`/`y` is given **from the perspective of the team performing it, attacking left to right**: `x = 0` is that team's own goal line, `x = 105` the opponent's goal line.
- `y = 0` is the acting team's **right** touchline, `y = 68` its **left** touchline. Goal centre is `y = 34`.
- To put both teams on one picture, flip the away team (or the defending team): `x' = 105 − x`, `y' = 68 − y`.
- `zone` is one of 18 areas (6 bands along the pitch × 3 channels): `zone = band*3 + channel`, band 0 = own goal end, channel 0 = right. `third` (defensive / middle / attacking) and `channel` (right / centre / left) are given as words too.

## Files

| File | Rows | What it is |
|---|---|---|
| `league.json` | — | League metadata, generator parameters, coordinate note |
| `clubs.csv` | 20 | Club name, code, city, stadium, capacity, playing style, formation, colours |
| `players.csv` | 500 | 25-man squads with role, nationality, age, attributes (0–100) |
| `referees.csv` | 14 | Referee names |
| `matches.csv` | 380 | Fixtures and results: date, kickoff, venue, referee, attendance, score, half-time score, xG, ball-in-play time |
| `lineups.csv` | ~15,200 | Every matchday squad member: starter flag, role, time on/off, minutes, how they left |
| `events.csv.gz` | ~570,000 | **Event stream** (see below) |
| `possessions.csv.gz` | ~100,000 | One row per possession with start/end, passes, xG and a pattern label |
| `team_match_stats.csv` | 760 | Per club per match: goals, xG, shots, possession, PPDA, counters, high turnovers and more |
| `player_match_stats.csv` | ~10,500 | Per player per match: minutes, goals, assists, xG, xA, passes, defensive actions, cards |
| `standings.csv` | 20 | Final table with xG for/against |
| `standings_by_matchweek.csv` | 760 | Table after every matchweek (for "race" narratives) |
| `suspensions.csv` | small | Bans triggered by red cards or yellow accumulation (5 and 10) |
| `ground_truth_club_ratings.csv` | 20 | **Hidden** true attack/defence multipliers used by the engine. Keep away from your app; use it to test whether your analysis recovers who is genuinely good vs lucky. |

## `events.csv.gz`

| Column | Meaning |
|---|---|
| `match_id`, `event_id` | Match and sequence number within the match (ordered) |
| `period` | 1 or 2 |
| `clock_s` | Continuous elapsed match time in seconds, stoppage time included. Never goes backwards. Use this to replay. |
| `period_s` | Seconds since the start of the period |
| `minute` | Football minute, 0-based (stoppage time stays at 45 / 90) |
| `display_clock` | Broadcast style, e.g. `23'`, `45+2'`, `90+4'` |
| `team_id`, `player_id` | Who performed the event (empty for period markers) |
| `type` | Event type (table below) |
| `outcome` | Result of the event where relevant |
| `x`, `y`, `end_x`, `end_y` | Start / end location (acting team's perspective) |
| `zone`, `third`, `channel` | Location buckets for `x`/`y` |
| `recipient_id` | Pass receiver; on shots, the assister; on fouls, the player fouled; on substitutions, the player coming on |
| `pass_type` | `short`, `long`, `cross`, `cutback`, `through_ball`, `throw_in` |
| `body_part` | `right_foot`, `left_foot`, `head` |
| `shot_type` | `open_play`, `set_piece`, `free_kick`, `penalty` |
| `xg` | Expected goals of a shot (also on `save`) |
| `set_piece` | `kickoff`, `corner`, `free_kick`, `throw_in`, `goal_kick`, `penalty` when the action is a restart |
| `card` | `yellow`, `second_yellow`, `red` |
| `possession_id`, `possession_team_id` | Possession the event belongs to (joins to `possessions`) |
| `score_home`, `score_away` | Score **after** the event |
| `tags` | `;`-separated qualifiers: `key_pass`, `assisted`, `big_chance` (xG ≥ 0.30), `one_on_one`, `counter_attack`, `individual_play`, `long_range`, `penalty_conceded` |

### Event types

| `type` | `outcome` values | Notes |
|---|---|---|
| `pass` | `complete`, `incomplete`, `offside` | Includes crosses, set-piece deliveries, goal kicks, throw-ins |
| `carry` | — | Progressive run with the ball |
| `take_on` | `success`, `fail` | Dribble at an opponent |
| `shot` | `goal`, `saved`, `blocked`, `off_target`, `post` | Goals are shots with outcome `goal` |
| `save` | — | Goalkeeper save (performed by the defending side) |
| `block` | — | Defender blocks a shot |
| `tackle` | `won` | Wins the ball from a take-on |
| `interception` | — | Reads a pass |
| `ball_recovery` | — | Collects a loose ball |
| `clearance` | — | Defensive clearance |
| `keeper_claim` | — | Goalkeeper catches a cross or through ball |
| `foul` | — | `recipient_id` is the player fouled |
| `card` | — | See `card` column |
| `penalty_awarded` | — | Followed by the penalty `shot` |
| `offside` | — | Player caught offside |
| `substitution` | role / `emergency_keeper` | `player_id` off, `recipient_id` on |
| `period_start` | `added_N` | N = minutes of added time announced |
| `period_end`, `match_end` | final score on `match_end` | |

## `possessions.csv.gz`

`start_type`: `kickoff`, `interception`, `tackle`, `recovery`, `keeper_claim`, `goal_kick`, `throw_in`, `throw_in_attacking`, `free_kick`, `corner`, `penalty`, `loose_ball`.

`pattern`:
- `counter_attack` — ball won in own half, reached the final 25 m within 15 s
- `high_turnover` — ball won 60 m+ up the pitch
- `set_piece` — started from a corner, attacking free kick, penalty or attacking throw
- `sustained` — 10+ passes
- `build_up_lost` — never got past 45 m
- `open_play` — everything else

Other columns: `start_clock_s`, `end_clock_s`, `duration_s`, `start_x`, `start_y`, `max_x`, `passes`, `shots`, `xg`, `goal`, `end_type`.

## `team_match_stats.csv` (selected)

`possession_pct` is pass share; `possession_time_pct` is share of possession time. `ppda` = opponent passes in their own 60% of the pitch ÷ our tackles, interceptions and fouls in that area (lower = more intense pressing). `counter_attacks` and `high_turnovers` count possessions with those patterns.
