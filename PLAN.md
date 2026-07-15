# Technical Plan: Daily Sports Scores Scraper

## Overview

A scheduled pipeline that pulls final scores for NFL, NBA, MLB, and NHL games from the previous day via ESPN's public scoreboard API, appends them to per-league CSV files, and commits them to this repo. The CSVs are the data layer for a backend app that answers: *"How many wins have my teams had this month?"*

- **Cadence:** Daily at 10:00 UTC (~5–6am US/Eastern depending on DST) via GitHub Actions
- **Source:** ESPN's unofficial scoreboard JSON endpoints
- **Output:** One append-only CSV per league (`data/nfl_scores.csv`, etc.), deduped on `game_id`
- **Consumer:** Backend app aggregating wins-per-team-per-month

A reference implementation of the scraper (`scrape_scores.py`) and workflow already exists; this plan describes that design plus the fixes required before it ships (Section 4.3).

---

## 1. Architecture

```
GitHub Actions (cron, 10:00 UTC)
        │
        ▼
scrape_scores.py ──► GET ESPN scoreboard JSON (4 leagues, dates=YYYYMMDD)
        │
        ▼
flatten completed games ──► flat score rows
        │
        ▼
append to data/{league}_scores.csv (dedupe on game_id)
        │
        ▼
git commit + push to main
        │
        ▼
Backend app reads CSVs (directly from repo, raw.githubusercontent.com,
or synced into Postgres/Supabase later)
```

Everything lives in one repo. No servers, no database required for v1 — the repo *is* the database.

---

## 2. Data Source

### ESPN scoreboard API

```
https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD
```

League path segments:

| league | sport | league slug |
|---|---|---|
| NFL | `football` | `nfl` |
| NBA | `basketball` | `nba` |
| MLB | `baseball` | `mlb` |
| NHL | `hockey` | `nhl` |

- Free, no auth, no JS rendering — a plain `requests.get` returns structured JSON.
- Each event carries a stable `id`, competitors with `homeAway`, `score`, `winner` flags, team abbreviations, and a status object (`status.type.state`, `status.type.completed`, `status.type.description`).
- Structured JSON means no parser to break when a page layout changes — the main failure mode is ESPN changing or retiring the endpoint.

### Risks & etiquette

- **Unofficial API:** No contract. It has been stable for years, but a schema change or removal is possible. The scraper's malformed-event handling logs and skips rather than crashing, and a whole-league fetch failure exits non-zero so the Actions run shows red.
- **Etiquette:** 4 requests per day with a 15 s timeout and 3-attempt retry (5 s backoff, linearly increasing). Well within polite limits.
- **Fallback (only if ever needed):** plaintextsports.com date pages could be scraped as a backup source. Not worth building unless ESPN actually breaks.

---

## 3. Data Model

### CSV schema — `data/{league}_scores.csv` (one file per league, append-only)

| column | type | example | notes |
|---|---|---|---|
| `date` | string | `20260701` | YYYYMMDD, the date scraped (ET reference) |
| `league` | string | `mlb` | `nfl`, `nba`, `mlb`, `nhl` |
| `game_id` | string | `401696234` | ESPN event id — the dedupe key |
| `away_team` | string | `NYM` | ESPN team abbreviation |
| `away_score` | int | `4` | |
| `home_team` | string | `PHI` | |
| `home_score` | int | `6` | |
| `winner` | string | `PHI` | Abbreviation; **empty for ties** (NFL can tie) |
| `status` | string | `Final` | ESPN status description (`Final`, `Final/OT`, …) |

Design notes:

- **`game_id` is the uniqueness key.** Reruns and backfills are safe: rows whose `game_id` already exists in the file are skipped. This also makes MLB doubleheaders a non-issue — each game has its own id.
- **`winner` is precomputed** so the backend's monthly-wins query is a trivial filter + count.
- **ESPN abbreviations are the canonical team identifiers.** No alias resolution needed — the source is already normalized. A small `teams.json` maps abbreviation → full display name per league, for the UI and for validating `my_teams.json` (see 9.3).
- **Only completed games are written.** In-progress, scheduled, postponed, and canceled games are skipped; a postponed game's makeup appears later under its own final status.
- **Append-only, one file per league** (not per day): the whole dataset is 4 files, each a few thousand rows per season. Month filtering is a prefix match on `date`.
- A zero-game day (off-season) simply appends nothing — no commit that day for that league.

---

## 4. Scraper Design (`scrape_scores.py`)

Python 3.12. Single dependency: `requests`. Stdlib `csv`, `argparse`, `logging`, `zoneinfo` for everything else.

### 4.1 Flow (as implemented in the reference script)

1. **Compute target date:** `--date YYYYMMDD` if given, else yesterday in `America/New_York`. Running at 10:00 UTC means every previous-day game (including West Coast extra innings) is long finished.
2. **Fetch:** For each league, GET the scoreboard with `dates=<target>`. Retry loop: 3 attempts, 15 s timeout, backoff of `5 × attempt` seconds. A league that fails all attempts is logged as an error and skipped; the run continues so partial success still writes what it can, then exits non-zero at the end.
3. **Flatten:** For each event, keep only completed games; extract home/away competitors, scores, winner, status description. A malformed event is logged and skipped, never fatal.
4. **Append:** Load existing `game_id`s from the league CSV, write only new rows, creating the file with a header if absent. Returns the count written.
5. **Exit codes:** `0` on success (including zero new rows); `1` if any league's fetch failed outright, so the Actions run shows red.

### 4.2 CLI

```
python scrape_scores.py                    # yesterday, US/Eastern
python scrape_scores.py --date 20260114    # specific date (backfill)
python scrape_scores.py --data-dir data    # output directory override
```

`--date` accepts one date per invocation; backfilling a month is a shell loop or repeated `workflow_dispatch` runs.

### 4.3 Required fixes before shipping the reference script

These are bugs/gaps in the current draft — fix them as step 1 of the build:

1. **Winner-on-tie bug.** `winner = home if home.get("winner") else away` credits the **away team** whenever the home team didn't win — including ties, where *neither* competitor has `winner: true`. NFL ties would wrongly count as away-team wins. Fix: check both flags; if neither is true, write `winner` as empty string. Ties then naturally count as zero wins downstream.
2. **Completed-game check.** `status.type.state == "post"` can include postponed/canceled events (ESPN marks some of these `post`). Use `status.type.completed is True` instead (optionally *and* `state == "post"`), so only genuinely finished games are written.
3. **Empty `--date` handling.** If the workflow ever passes `--date "${{ inputs.date }}"` with no input, the script receives an empty string and must treat it as "yesterday" (currently `args.date or yesterday_eastern()` handles `""` correctly since empty string is falsy — keep it that way, with a test).
4. **Date validation.** Reject `--date` values that aren't 8 digits / a real date, so a typo'd backfill fails fast instead of writing garbage rows keyed to a bad date.

### 4.4 Repo layout

```
sports-win-index/
├── .github/workflows/scrape.yml
├── PLAN.md
├── README.md
├── my_teams.json
├── scrape_scores.py
├── teams.json              # abbreviation → display name, per league
├── requirements.txt        # requests
├── data/
│   ├── mlb_scores.csv
│   ├── nba_scores.csv
│   ├── nfl_scores.csv
│   └── nhl_scores.csv
└── tests/
    ├── fixtures/           # saved ESPN JSON payloads
    └── golden/
```

---

## 5. GitHub Actions Workflow

10:00 UTC is comfortably after every league's last game ends, and DST only shifts the local run time between 5am and 6am ET — no dual-cron tricks needed.

### `scrape.yml`

```yaml
name: Daily Scores Scrape

on:
  schedule:
    # 10:00 UTC daily (~5-6am US/Eastern depending on DST) —
    # comfortably after every league's last game of the previous day finishes.
    - cron: "0 10 * * *"
  workflow_dispatch:
    inputs:
      date:
        description: "Override date (YYYYMMDD), defaults to yesterday"
        required: false

permissions:
  contents: write
  issues: write               # for failure alerting

concurrency:
  group: scrape               # a backfill and the nightly run must not push concurrently
  cancel-in-progress: false

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install requests

      - name: Run scraper
        run: python scrape_scores.py --date "${{ inputs.date }}"

      - name: Commit and push updated CSVs
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/*.csv
          git diff --cached --quiet && echo "No changes to commit" && exit 0
          git commit -m "Add scores for ${{ inputs.date || 'yesterday' }} ($(date -u +%Y-%m-%dT%H:%MZ))"
          git pull --rebase origin main   # tolerate pushes that landed mid-run
          git push

      - name: Alert on failure
        if: failure()
        uses: actions/github-script@v7
        with:
          script: |
            await github.rest.issues.create({
              owner: context.repo.owner,
              repo: context.repo.repo,
              title: `Scrape failed: ${new Date().toISOString().slice(0, 10)}`,
              body: `Run: ${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`,
            });
```

Notes:

- The `date` input on `workflow_dispatch` gives **free backfill** — run it manually for any past date. On scheduled runs the input is empty and the script defaults to yesterday (see fix 4.3.3).
- The `git diff --cached --quiet && exit 0` guard skips the commit on zero-change days (off-season, or a rerun that found nothing new).
- `git pull --rebase` before push plus the `concurrency` group keep a manual backfill and the nightly run from clobbering each other.
- The failure step opens a GitHub issue; otherwise a broken scraper fails silently at 5am forever.
- **Scheduled workflow auto-disable:** GitHub disables cron on repos with no activity for 60 days. The daily commit itself counts as activity, so this self-sustains — but if the scraper fails for 60 straight days, the schedule dies. The failure-issue alerting is the mitigation: a human sees the issue long before day 60.

---

## 6. Backend Consumption ("my teams' wins this month")

The CSVs are designed so the backend query is dead simple:

1. Fetch the relevant league CSVs (at most 4 files total; raw.githubusercontent.com URLs work fine, no auth needed on a public repo; use the Contents API if private).
2. Filter `date` by month prefix (`date.startswith("202607")`), filter `winner IN (user's team abbreviations)`, group by `winner`, count.

For v1 the backend can read the CSVs directly on each request (4 small files, cacheable). If/when this grows: a second Actions step can upsert each day's rows into Supabase Postgres — same schema, `game_id` as the primary key — and the app queries SQL instead. The CSV layer stays as the durable source of truth either way.

---

## 7. Edge Cases

- **Off-season:** July = no NFL/NHL. ESPN returns an empty `events` array → zero rows, no error, no commit for that league.
- **Postponed/suspended/canceled games:** Excluded by the completed check (4.3.2); the makeup game appears later with its own final status.
- **Doubleheaders (MLB):** Two distinct `game_id`s — handled automatically by the dedupe key.
- **NFL ties:** `winner` empty (after fix 4.3.1); ties naturally count as zero wins.
- **All-Star games / exhibitions:** These appear in ESPN's feed with their own pseudo-team abbreviations (e.g. All-Star squads). They're harmless to keep — no user's team abbreviation ever matches them — so v1 writes them as-is rather than maintaining an exclusion list. Revisit only if they pollute a UI listing.
- **Abbreviation collisions across leagues:** The same abbreviation can appear in two leagues (e.g. `NY`-prefixed teams). Per-league files plus the `league` column keep them distinct; `my_teams.json` entries are `(league, abbreviation)` pairs, not bare abbreviations (see 9.3).

---

## 8. Build Order

1. Commit the reference `scrape_scores.py` with the four fixes from 4.3, plus `requirements.txt`.
2. Save real ESPN JSON payloads as test fixtures; write tests for flattening (ties, OT, postponed, malformed events, doubleheaders) and for append/dedupe.
3. `teams.json` (abbreviation → display name for all 124 teams: 32 NFL + 30 NBA + 30 MLB + 32 NHL) and `my_teams.json` + validation test.
4. Run locally against yesterday's real date; spot-check output against ESPN's site.
5. Commit the Actions workflow; trigger once via `workflow_dispatch`; confirm the commit lands.
6. Backfill the current month via repeated `workflow_dispatch` runs (or a local shell loop + one commit).

**Definition of done:** Workflow runs green on schedule for 3 consecutive days; a backfilled month of MLB data produces correct win counts spot-checked against standings.

---

## 9. Implementation Notes

### 9.1 Test fixtures (build these first)

Save raw ESPN scoreboard JSON responses into `tests/fixtures/` and write all tests against them — never against the live API:

- One in-season payload per league (name files by league + date, e.g. `mlb_20260701.json`; NFL needs a fall date)
- One off-season/empty payload (`events: []`)
- One MLB doubleheader day
- One payload containing an OT and/or shootout result (NHL)
- One payload containing a postponed game and an NFL tie (edit a real payload by hand if no natural example is handy, and note that in the filename)

Tests assert exact flattened rows per fixture. If ESPN changes its schema, a fixture refresh + failing test tells us exactly what broke.

### 9.2 Golden output file

Create `tests/golden/mlb_20260701.csv` by hand-verifying every game from that date against ESPN's site. An end-to-end test runs fetch-flatten-append against the corresponding fixture and asserts exact match with the golden file. This is the definition of "correct" — not "output looks reasonable."

### 9.3 "My teams" config format

The backend reads `my_teams.json` at the repo root. Entries are league-qualified to avoid cross-league abbreviation collisions:

```json
{
  "teams": [
    { "league": "mlb", "abbreviation": "NYM" },
    { "league": "nba", "abbreviation": "NYK" },
    { "league": "nhl", "abbreviation": "NYR" },
    { "league": "nfl", "abbreviation": "BUF" }
  ]
}
```

`teams.json` maps every abbreviation to a display name:

```json
{
  "mlb": { "NYM": "New York Mets", "PHI": "Philadelphia Phillies" }
}
```

A validation test checks that every `my_teams.json` entry resolves against `teams.json`, and that `teams.json` abbreviations are unique within each league.

### 9.4 Constraints (do not over-build)

- Total dependency count stays at 1 (`requests`) plus `pytest` for dev. No pandas, no ORM, no bs4 — the source is JSON.
- No retry frameworks (tenacity, backoff). The existing plain retry loop is sufficient.
- Prefer plain functions over classes unless state genuinely demands it.
- No config framework — module-level constants and CLI args are enough.

### 9.5 Logging spec

The script uses stdlib `logging` at INFO. A successful run's Actions log looks like:

```
2026-07-02 10:00:14 [INFO] Scraping scores for date=20260701
2026-07-02 10:00:16 [INFO] MLB: 15 completed game(s) found, 15 new row(s) written to data/mlb_scores.csv
2026-07-02 10:00:17 [INFO] NBA: 0 completed game(s) found, 0 new row(s) written to data/nba_scores.csv
2026-07-02 10:00:18 [INFO] NHL: 0 completed game(s) found, 0 new row(s) written to data/nhl_scores.csv
2026-07-02 10:00:19 [INFO] NFL: 0 completed game(s) found, 0 new row(s) written to data/nfl_scores.csv
2026-07-02 10:00:19 [INFO] Done. 15 total new row(s) written across all leagues.
```

Malformed events log a WARNING with the league and error; a league that fails all fetch attempts logs an ERROR with the URL, and the run exits `1` after finishing the remaining leagues.

### 9.6 Verification checkpoints (pause here)

Work in stages and **stop for review at each checkpoint** rather than building end-to-end unreviewed:

1. **Checkpoint 1 — after the 4.3 fixes + fixture tests:** Run against a real recent date, print the flattened rows. *Human spot-checks 3–4 scores against ESPN before continuing.*
2. **Checkpoint 2 — after teams.json + golden file:** Hand-verify the golden CSV.
3. **Checkpoint 3 — after Actions workflow:** Trigger via `workflow_dispatch` once manually; confirm the commit lands and the log matches 9.5. Only then let the schedule take over.

Do not proceed past a checkpoint without explicit sign-off.
