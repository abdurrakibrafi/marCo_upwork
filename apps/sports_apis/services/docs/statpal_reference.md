# StatPal API — Integration Reference (for Agentic AI / Claude Code)

> **Purpose of this file:** This is a condensed, structured reference of the StatPal sports-data API
> (`https://statpal.io/api/...`), written so that an agentic coding assistant (Claude Code, Cursor,
> etc.) working inside this repo can read it once and know exactly which endpoint to call, what
> params it needs, and what shape the JSON response has — without having to re-read the full raw
> API docs every time.
>
> **Project context:** This API powers the **ShopPassport / marCo** live-scores platform
> (Django + Celery + Django Channels WebSocket broadcasting). Parsing logic lives in
> `apps/event/tasks.py`. Containers: `shoppassport_backend`, `shoppassport_celery`,
> `shoppassport_celery_beat`, `shoppassport_db`, `shoppassport_redis`.

---

## 0. Auth & conventions (applies to every endpoint)

- Auth is a query param, **not a header**: `?access_key=YOUR_KEY`
- Key format: uuid4 (e.g. `my uuid from .env file`) — **store in env var, never hardcode**.
- All requests: `Accept: application/json` (image endpoints use `image/png, application/json`).
- Dates in responses are `DD.MM.YYYY` (StatPal migration note: watch for this vs ISO — already caused a bug once in `_map_status()` / date parsing, see project history).
- `day_offset_token` pattern (`d-7` … `d-1`, `d1` … `d7`) is used for "daily" endpoints across NBA, NHL, NFL, MLB, Handball, Volleyball, Tennis, Cricket-adjacent. **Valid tokens only — `d0` is invalid, `d+1` is invalid, must be `d1`.** (This bit us once on the NBA daily endpoint.)
- Response root key names differ by sport/endpoint (`livescores`, `livescore`, `scores`, `standings`, `odds`, `fixtures`, `results`, `statistics`, `team`, `player`, `coach`) — don't assume a single top-level key across sports.
- Nested objects are inconsistent about **single object vs array** when there's one match/league (e.g. `match` can be a dict OR a list of dicts depending on how many results exist). **Always normalize to a list before iterating using `isinstance(data, list)`** — this class of bug already caused a `ValueError` from tuple-unpacking in the `fetches` loop in `tasks.py`.
- Empty/unknown numeric fields often come back as `""` (empty string) rather than `null` or `0`. Cast defensively using safe conversion helpers (e.g., `int(val) if val else 0`).

---

## 1. Soccer — `/api/v2/soccer/...`

| Endpoint | Method | Path (Base: https://statpal.io) | Notes |
|---|---|---|---|
| Leagues | GET | `/api/v2/soccer/leagues` | List of leagues by sport |
| Seasons | GET | `/api/v2/soccer/leagues/seasons` | Seasons per league |
| Live matches | GET | `/api/v2/soccer/matches/live` | Root key `live_matches`, has `updated_ts` |
| Recent/upcoming | GET | `/api/v2/soccer/matches/daily?offset=-1` | `offset` integer used here, not `day_offset_token` (soccer is the exception) |
| Matches by league | GET | `/api/v2/soccer/leagues/{league-id}/matches` | Grouped by `week[]` |
| Match details/stats | GET | `/api/v2/soccer/leagues/{league-id}/matches/stats` | Full lineups, subs, team/player stats, event_summary (goals/cards/VAR) |
| Standings | GET | `/api/v2/soccer/leagues/{league-id}/standings` | Per team: overall/home/away splits |
| League stats | GET | `/api/v2/soccer/leagues/{league-id}/stats` | Full squad stats per team |
| Team | GET | `/api/v2/soccer/teams/{team_id}` | Squad, transfers, trophies, league_stats incl. scoring-minute buckets |
| Player | GET | `/api/v2/soccer/players/{player_id}` | Club + national stats, career totals |
| Coach | GET | `/api/v2/soccer/coaches/{coach_id}` | Career + trophies |
| Image | GET | `/api/v2/soccer/images?type=team` | Returns raw base64 PNG, not JSON-wrapped |

**Known project gotchas:** numeric minute status misclassification in `_map_status()`; `main_id`/`fallback_id_1/2/3` — StatPal gives fallback IDs for cross-referencing with other providers (API-Sports migration), use `main_id` as canonical.

---

## 2. NBA — `/api/v1/nba/...`

| Endpoint | Path | Notes |
|---|---|---|
| Live scores | `/api/v1/nba/livescores` | quarters `q1..q4` + `ot` per team |
| Recent/upcoming | `/api/v1/nba/daily/{day_offset_token}` | **valid tokens only (d-7..d-1, d1..d7), no `d0`** |
| Full schedule | `/api/v1/nba/season-schedule` | full season, `status` incl. "After Over Time" |
| Standings | `/api/v1/nba/standings` | nested `league[].division[].team[]` |
| Team roster | `/api/v1/nba/rosters/{team_abbr}` | includes base64 team image |
| Team stats | `/api/v1/nba/team-stats/{team_abbr}` | `category[]` = "Game", "Shooting", etc, each with `player[]` |

---

## 3. Cricket — `/api/v1/cricket/...`

| Endpoint | Path | Notes |
|---|---|---|
| Live scores | `/api/v1/cricket/livescores` | root `scores.category[]`, each has one `match` (object, not array) with `inning[]`, `commentaries`, `lineups`, `matchinfo` |
| Upcoming schedule | `/api/v1/cricket/upcoming-schedule` | root `fixtures.category[]` |
| Tournament list | `/api/v1/cricket/tour-list` | gives `schedule_uri`, `squad_uri`, `standings_uri` per tour — cricket doesn't expose numeric league IDs the same way as other sports, uses `tournament_type`/`tournament_id` pair |
| Matches by tournament | `/api/v1/cricket/season-schedule/{tournament_type}/{tournament_id}` | `match[]` array under `category` |

**Known project gotcha:** live/fixture processing order previously overwrote live statuses — cricket's `inning`/`match` nesting is the most irregular of all sports here (test matches vs limited-overs have different shapes); always check `match.type` (`TEST`/`ODI`/`T20`) before assuming field presence.

---

## 4. NHL — `/api/v1/nhl/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/nhl/livescores` | 15s | Root `livescores`, events split into `firstperiod/secondperiod/thirdperiod/overtime/penalties`, each `event` can be object or array |
| Recent/upcoming | `/api/v1/nhl/daily/{day_offset_token}` | 12h | **Follow valid token patterns only** |
| Full schedule | `/api/v1/nhl/season-schedule` | 1h | Includes `team_stats`, `goalkeeper_stats` |
| Standings | `/api/v1/nhl/standings` | 1h | `league[].division[].team[]` |
| Team roster | `/api/v1/nhl/rosters/{team_abbr}` | 1h | Grouped by `position[]` (e.g. Centers) |
| Team stats | `/api/v1/nhl/team-stats/{team_abbr}` | 1h | `team` field is odd: `[name_string, {player:[...]}]` — a 2-element array mixing a string and object. Parse carefully. |
| Injuries | `/api/v1/nhl/injuries/{team_abbr}` | 1h | |
| Pre-game odds | `/api/v1/nhl/odds` | 30m | multi-bookmaker |

Team abbreviations (NHL): `ana atl bos buf car cbj cgy chi col dal det edm fla la min mtl nj nsh nyi nyr ott phi phx pit sj stl tb tor van wsh`

---

## 5. NFL — `/api/v1/nfl/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/nfl/livescores` | 15s | Huge payload: team_stats, passing, rushing, receiving, fumbles, interceptions, defensive, kick/punt returns, kicking, punting — all split home/away |
| Live play-by-play | `/api/v1/nfl/live-plays` | 15s | `playbyplay.drive[].play[]` |
| Full schedule | `/api/v1/nfl/season-schedule` | 1h | Nested `stage[].week[].matches.match` |
| Standings | `/api/v1/nfl/standings` | 1h | `league[].division[].team[]`, includes `conference_record`/`division_record` |
| Team rosters | `/api/v1/nfl/rosters/{team_abbr}` | 3h | Grouped by Offense/Defense/Special Teams/IR/Practice Squad |
| Injuries | `/api/v1/nfl/injuries/{team_abbr}` | 3h | |
| Team stats | `/api/v1/nfl/team-stats/{team_abbr}` | 3h | `category[]`: Passing/Rushing/Downs/Returning/Kicking, each with `team` + `opponents` |
| Player stats | `/api/v1/nfl/player-stats/{team_abbr}` | 3h | `category[].player[]` |
| League career stats | `/api/v1/nfl/league-stats/nfl-career` | 3h | only `stat_type=nfl-career` supported |
| Pre-game odds | `/api/v1/nfl/odds` | 30m | includes `rotation_home`/`rotation_away` |

Team abbreviations (NFL): `ari atl bal buf car chi cin cle dal den det gb hou ind jac kc mia min no nyg nyj oak phi pit sd sea sf stl tb ten wsh`

---

## 6. MLB — `/api/v1/mlb/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/mlb/livescores` | 15s | innings `in1..in9`, `starting_pitchers`, `outs` |
| Recent/upcoming | `/api/v1/mlb/daily/{day_offset_token}` | 12h | |
| Full schedule | `/api/v1/mlb/season-schedule` | 1h | |
| Standings | `/api/v1/mlb/standings` | 1h | `league[].division[].team[]`, has `runs_diff`, `current_streak` |
| Team roster | `/api/v1/mlb/rosters/{team_abbr}` | 1h | Grouped by Pitchers/Catchers/etc |
| Team stats | `/api/v1/mlb/team-stats/{team_abbr}` | 1h | `category[]`: Batting etc |
| Injuries | `/api/v1/mlb/injuries/{team_abbr}` | 1h | |
| League stats | `/api/v1/mlb/league-stats/{stat_type}` | 1h | `stat_type` ∈ `{mlb,nl,al}_{player,team}_{batting,fielding,pitching}` (18 combos) |
| Pre-game odds | `/api/v1/mlb/odds` | 30m | |

Team abbreviations (MLB): `ari atl bal bos chc chw cin cle col det fla hou kan laa lad mil min nym nyy oak phi pit sdg sea sfo stl tam tex tor was`

---

## 7. Formula 1 — `/api/v1/f1/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/f1/livescores` | 15s | Root `livescore` (singular!). Has `race`, `qualification[]`, `last_practice`, `second_practice`, `first_practice`, each with `results.driver[]` |
| Season schedule | `/api/v1/f1/schedule` | 30m | Root `fixtures` |
| Season results | `/api/v1/f1/results` | 30m | Root `results` |
| Team standings | `/api/v1/f1/team-standings` | 1 day | Root `standings.teams.team[]` |
| Driver standings | `/api/v1/f1/driver-standings` | 1 day | Root `standings.drivers.driver[]` |

---

## 8. Handball — `/api/v1/handball/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/handball/livescores` | 15s | Root `livescores.tournament[]`, score fields `t1`/`t2` (halves) |
| Recent/upcoming | `/api/v1/handball/daily/{day_offset_token}` | 12h | |
| Matches by league | `/api/v1/handball/season-schedule/{league_id}` | 1h | `league_id` is an **integer** path param, not string |
| Standings by league | `/api/v1/handball/standings/{league_id}` | 1h | |
| Pre-match odds | `/api/v1/handball/odds` | 30m | |

---

## 9. Volleyball — `/api/v1/volleyball/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/volleyball/livescores` | 15s | Set scores `s1..s5`, `totalscore` = sets won |
| Recent/upcoming | `/api/v1/volleyball/daily/{day_offset_token}` | 12h | |
| Matches by league | `/api/v1/volleyball/season-schedule/{league_id}` | 1h | integer `league_id` |
| Standings by league | `/api/v1/volleyball/standings/{league_id}` | 1h | |
| Pre-match odds | `/api/v1/volleyball/odds` | 30m | includes Over/Under totals in some bookmakers |

---

## 10. Golf — `/api/v1/golf/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/golf/livescores` | 15s | Root `livescore` (singular). `player[]` has `rounds.round[]` and hole-by-hole `stats.rounds.round[].hole[]` |
| Schedule | `/api/v1/golf/schedule` | 1h | Root `fixtures`, season + series (PGA) level |

**Known project gotcha:** golf crash traced to `get_or_create_precise_entity()` signature mismatch — golf has no team concept (player-only, no `team_id`), so any shared entity-resolution helper across sports must branch for individual-sport vs team-sport payloads instead of assuming a team always exists.

---

## 11. Horse Racing — `/api/v1/horse-racing/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live by country | `/api/v1/horse-racing/live/{country}` | 15s | `country` ∈ `uk usa sa france` |
| Schedule by country | `/api/v1/horse-racing/schedule/{country}` | 30m | same country enum |

Structure: `tournament[].race[].runners.horse[]` and `.results.horse[]`, plus `.wagers.wager`.
No team/day-offset model — everything keyed by country + race id.

---

## 12. Tennis — `/api/v1/tennis/...`

| Endpoint | Path | Update freq | Notes |
|---|---|---|---|
| Live scores | `/api/v1/tennis/livescores` | 15s | `tournament[].match` (singles = 2 `player[]`, doubles adds `dp1`/`dp2` partner IDs) |
| Live stats | `/api/v1/tennis/livestats` | 15s | `player[].stats.period[].type[].stat[]` — deep nesting |
| Recent/upcoming | `/api/v1/tennis/daily/{day_offset_token}` | 12h | |
| Tournament list | `/api/v1/tennis/tournament-list/{atp\|wta}` | 12h | Gives `id` needed for tournament matches endpoint |
| Matches by tournament | `/api/v1/tennis/tournament/{tournament_id}` | 1h | `tournament_id` comes from tournament-list, integer |
| Standings | `/api/v1/tennis/standings/{atp\|wta}` | 1h | Root `standings.player[]`, has `rank`/`points`/`movement` |
| Pre-match odds | `/api/v1/tennis/odds` | 30m | |

Doubles matches: player `name` is `"Surname1/ Surname2"`, with `dp1`/`dp2` = partner player IDs. Use safe dict `.get('dp1')` lookups to infer doubles games vs explicit flags.

---

## 13. Cross-sport integration checklist for this repo

When wiring a new sport (or fixing a broken one) into `apps/event/tasks.py`:

1. **Normalize `match`/`event`/`inning` fields to lists** before iterating — StatPal collapses single-item arrays to bare objects. Use `isinstance(data, list)`.
2. **Confirm the root JSON key** for that endpoint (`livescores` vs `livescore` vs `scores` — inconsistent across sports, see tables above).
3. **Confirm `day_offset_token` validity** (`d-7`..`d-1`, `d1`..`d7`; no `d0`) before building Celery Beat schedules per sport.
4. **Branch entity resolution by sport type** (team-sport vs individual-sport vs country/race-keyed like horse racing) rather than assuming every sport has a `team_id`.
5. **Cast empty-string numeric fields defensively** (`""` instead of `null`/`0`) via a clean utility function.
6. **Use `main_id` as canonical match id** where StatPal also supplies `fallback_id_1/2/3` (soccer, NHL) for cross-referencing against the old API-Sports/StatPal migration data.
7. Publish to the existing WebSocket layer at `ws://localhost:8005/ws/scores/live/` using the established `_publish()` call after each successful parse, same as other sports.