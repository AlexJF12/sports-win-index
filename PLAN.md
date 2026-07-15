# Technical Plan: Daily Sports Scores Scraper

## Overview

A scheduled pipeline that scrapes final scores for NFL, NBA, MLB, and NHL games from the previous day, writes them to dated CSV files, and commits them to this repo. The CSVs are the data layer for a backend app that answers: *"How many wins have my teams had this month?"*

- **Cadence:** Daily at ~3:00 AM Eastern via GitHub Actions
- **Source:** plaintextsports.com (ESPN JSON as fallback — see Section 2)
- **Output:** One CSV per day containing all final games across the four leagues
- **Consumer:** Backend app aggregating wins-per-team-per-month

---

## 1. Architecture

```
GitHub Actions (cron, ~3am ET)
        │
        ▼
scrape.py ──► fetch yesterday's pages (4 leagues)
        │
        ▼
parse ──► normalize into common Game schema
        │
        ▼
write data/scores/YYYY-MM-DD.csv
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

### Primary: plaintextsports.com

- Serves scores as near-plain HTML with minimal markup — ideal for scraping.
- Date-based URLs, e.g. `https://plaintextsports.com/all/2026-07-01/` shows all leagues for that date; league pages like `/nba/2026-07-01/` also exist.
- No JS rendering required — `requests` + light parsing is enough. No Selenium/Playwright.

### Risks & fallback

- **Fragility:** The site is a hobby project with no API contract. Layout can change silently.
- **Fallback (build in v1.1):** ESPN's undocumented scoreboard JSON endpoints (`site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD`). Free, JSON, covers all four leagues. Parsing JSON beats parsing text, so this is a candidate to *become* the primary source if plaintextsports parsing proves brittle in practice — but v1 uses plain-text scraping per the requirement, with ESPN as the fallback path when a parse fails.

### Etiquette

- Single fetch per league per day (4–5 requests total). Well within polite limits.
- Set a descriptive User-Agent identifying this project; add a 1–2 s delay between requests.

---

## 3. Data Model

### CSV schema — `data/scores/YYYY-MM-DD.csv`

| column | type | example | notes |
|---|---|---|---|
| `game_date` | date | `2026-07-01` | The date the game was played (ET) |
| `league` | string | `MLB` | One of `NFL`, `NBA`, `MLB`, `NHL` |
| `away_team` | string | `New York Mets` | Canonical name from `teams.json` |
| `home_team` | string | `Philadelphia Phillies` | |
| `away_score` | int | `4` | |
| `home_score` | int | `6` | |
| `winner` | string | `Philadelphia Phillies` | Empty for ties (NFL can tie) |
| `status` | string | `final` | `final`, `final_ot`, `final_so` |
| `game_seq` | int | `1` | Game number for same matchup same day (MLB doubleheaders → `2`); default `1` |
| `scraped_at` | ISO 8601 | `2026-07-02T07:05:12Z` | Provenance |

Design notes:

- **`winner` is precomputed** so the backend's monthly-wins query is a trivial filter + count — no score comparison logic downstream.
- **Uniqueness key:** `(game_date, league, away_team, home_team, game_seq)`. The `game_seq` column makes MLB doubleheaders unambiguous and gives a future Postgres table a clean natural key.
- **Postponed/suspended games are skipped**, not written — the game will appear on its makeup date. `status` is therefore always a completed-game value.
- **Team names must be normalized** to a canonical form via a `teams.json` reference file (see 9.4). The scraper resolves whatever the source prints ("NY Mets", "Mets") to the canonical name. This file is also what the backend uses to let a user pick "their teams."
- One file per day (not per league) keeps the repo tidy; the `league` column makes filtering trivial.
- Only completed games are written. In-progress or scheduled games are skipped (shouldn't exist at 3am ET, but West Coast extra-inning games are why we run at 3, not midnight).
- **Zero-game days still write a CSV** containing only the header row — an explicit "no games" beats an ambiguous gap.

---

## 4. Scraper Design (`scraper/scrape.py`)

Python 3.12, dependencies: `requests`, `beautifulsoup4` (even "plain text" sites have enough HTML structure to warrant it).

### Flow

1. **Compute target date:** `yesterday = now(ET) - 1 day`, overridable via `--date YYYY-MM-DD`. Use `zoneinfo("America/New_York")` — never naive datetimes, since the Actions runner is UTC. `--date` with an empty value is treated the same as omitting it (this is what the workflow passes on scheduled runs).
2. **Fetch:** For each league in `[nfl, nba, mlb, nhl]`, GET the league's date page. Retry: plain loop, 3 attempts, 5-second sleep. Handle 404/empty as "no games" (off-season is normal — in July, NFL and NHL pages are empty; that's a valid zero-game result, not an error).
3. **Parse:** Extract matchup blocks → `(away, away_score, home, home_score, status)`. Detect OT/SO markers for NHL/NBA/NFL. Assign `game_seq` by order of appearance for repeated matchups.
4. **Normalize:** Resolve team names against `teams.json`. An unrecognized team name is a **hard failure** (fail loudly — it means the source format changed or a name variant is missing). Exception: All-Star/exhibition games, whose "teams" won't resolve, are skipped with a log line rather than failing (see Section 7).
5. **Validate:** Scores are non-negative ints; no duplicate `(matchup, game_seq)` keys; winner logic handles NFL ties.
6. **Write:** `data/scores/YYYY-MM-DD.csv`. Idempotent — rerunning overwrites the same file, so manual re-triggers and backfills are safe.
7. **Exit codes:** `0` success (including zero-game days), non-zero on parse/validation failure so the Actions run shows red.

### Repo layout

```
sports-win-index/
├── .github/workflows/scrape.yml
├── PLAN.md
├── README.md
├── my_teams.json
├── scraper/
│   ├── scrape.py
│   ├── parsers/           # one module per league if formats differ
│   ├── teams.json         # canonical team reference
│   └── requirements.txt
├── data/
│   └── scores/
│       └── 2026-07-01.csv
└── tests/
    ├── fixtures/
    └── golden/
```

---

## 5. GitHub Actions Workflow

### The DST wrinkle

Cron in Actions is **UTC only**. 3:00 AM ET is `07:00 UTC` during daylight time and `08:00 UTC` during standard time. Schedule at `08:00 UTC` year-round: runs at 3am EST / 4am EDT. One hour late half the year costs nothing. (The "exact" alternative — schedule both hours and have the script exit early when it isn't 3am ET — is overkill here.)

### `scrape.yml`

```yaml
name: Daily scores scrape
on:
  schedule:
    - cron: "0 8 * * *"     # 3am EST / 4am EDT
  workflow_dispatch:          # manual re-runs + backfills
    inputs:
      date:
        description: "Override date (YYYY-MM-DD), defaults to yesterday"
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
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r scraper/requirements.txt
      - name: Scrape
        run: |
          # scrape.py treats an empty --date as "yesterday ET" and prints the
          # resolved date as its last line for the commit step to pick up
          python scraper/scrape.py --date "${{ inputs.date }}" | tee scrape.log
          echo "TARGET_DATE=$(grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' scrape.log | head -1)" >> "$GITHUB_ENV"
      - name: Commit results
        run: |
          git config user.name "scores-bot"
          git config user.email "actions@github.com"
          git add data/scores/
          git diff --cached --quiet || git commit -m "Scores for ${TARGET_DATE}"
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

- `workflow_dispatch` with a date input gives **free backfill** — run it manually for any past date. The commit message uses the date the scraper actually resolved (from its log output), so backfill commits are labeled correctly rather than as "yesterday."
- The `git diff --cached --quiet ||` guard avoids empty commits when a rerun produces an identical file.
- The failure step opens a GitHub issue; otherwise a broken parser fails silently at 3am forever.
- **Scheduled workflow auto-disable:** GitHub disables cron on repos with no activity for 60 days. The daily commit itself counts as activity, so this self-sustains — but if the scraper fails for 60 straight days, the schedule dies. The failure-issue alerting is the mitigation: a human sees the issue long before day 60.

---

## 6. Backend Consumption ("my teams' wins this month")

The CSVs are designed so the backend query is dead simple:

1. Load all `data/scores/YYYY-MM-*.csv` for the current month (raw.githubusercontent.com URLs work fine, no auth needed on a public repo; use the Contents API if private).
2. Concatenate, filter `winner IN (user's teams)`, group by `winner`, count.

For v1 the backend can read CSVs directly on each request (a month is ≤ 31 small files, cacheable). If/when this grows: a second Actions step can upsert each day's rows into Supabase Postgres — same schema, `(game_date, league, away_team, home_team, game_seq)` as the natural key — and the app queries SQL instead. The CSV layer stays as the durable source of truth either way.

---

## 7. Edge Cases

- **Off-season:** July = no NFL/NHL. Empty league page → zero rows, not an error.
- **Postponed/suspended games:** Skipped entirely; the game appears on its makeup date. (Decided — see Section 3.)
- **Doubleheaders (MLB):** Same matchup twice in one day is legitimate — `game_seq` disambiguates.
- **NFL ties:** `winner` empty, both teams get no win. Backend counts wins only, so ties naturally don't count.
- **All-Star games / exhibitions:** Their "teams" won't resolve in `teams.json`, which conveniently catches them — this specific case is skip-with-log, not the usual hard-fail on unrecognized names.
- **Relocations/rebrands:** `teams.json` has an aliases array per team so historical names still resolve.

---

## 8. Build Order

1. `teams.json` — all 124 teams across 4 leagues (32 NFL + 30 NBA + 30 MLB + 32 NHL) with aliases.
2. Fetch + parse one league (MLB, since it's in season) for a known date; validate against actual scores.
3. Generalize to 4 leagues, add normalization + validation + CSV writer.
4. CLI with `--date` override; idempotency check.
5. GitHub Actions workflow + commit step + failure alerting.
6. Backfill the current month via `workflow_dispatch`.
7. (v1.1) ESPN JSON fallback path.

**Definition of done:** Workflow runs green on schedule for 3 consecutive days; a manually backfilled month of MLB data produces correct win counts spot-checked against standings.

---

## 9. Implementation Notes

### 9.1 Test fixtures (build these first)

Save raw HTML snapshots of real plaintextsports.com pages into `tests/fixtures/` and write all parser tests against them — never against the live site:

- One in-season page per league (use recent real dates; NFL will need a fall date — grab whatever's available and note the date in the filename, e.g. `mlb_2026-07-01.html`)
- One off-season/empty league page (NFL in July)
- One MLB doubleheader day
- One page containing an OT and/or shootout result (NHL)

Parser tests assert exact parsed output per fixture. When the site changes format, a fixture refresh + failing test tells us exactly what broke.

### 9.2 Golden output file

Create `tests/golden/2026-07-01.csv` by hand-verifying every game from that date against ESPN. An end-to-end test runs the full pipeline against the corresponding fixtures and asserts byte-for-byte match with the golden file (with `scraped_at` normalized or excluded from the comparison, since it varies per run). This is the definition of "correct" — not "output looks reasonable."

### 9.3 Constraints (do not over-build)

- No Selenium, Playwright, or any headless browser. `requests` + `beautifulsoup4` only.
- Use stdlib `csv`, `json`, `datetime`, `zoneinfo` — no pandas, no ORM.
- No retry frameworks (tenacity, backoff). A plain loop with 3 attempts and a 5-second sleep is sufficient.
- Prefer plain functions over classes unless state genuinely demands it.
- No config framework — constants at the top of `scrape.py` and CLI args are enough.
- Total dependency count stays at 2 (`requests`, `beautifulsoup4`) plus `pytest` for dev.

### 9.4 "My teams" config format

The backend reads `my_teams.json` at the repo root:

```json
{
  "teams": ["New York Mets", "New York Knicks", "New York Rangers", "Buffalo Bills"]
}
```

Values must be canonical names exactly as they appear in `teams.json`. The scraper doesn't read this file, but `teams.json` must be structured so this lookup is trivial:

```json
{
  "MLB": [
    {
      "canonical": "New York Mets",
      "abbreviation": "NYM",
      "aliases": ["NY Mets", "Mets"]
    }
  ]
}
```

Rules, enforced by a validation test:

- Every alias (and canonical name, and abbreviation) must be **unique within its league** — e.g. `"New York"` can't be a Mets alias because the Yankees share the city. Ambiguous short forms simply aren't aliases; if the source prints one, that's a hard parse failure to investigate, not something to guess at.
- Every entry in `my_teams.json` must resolve to a canonical name in `teams.json`.

### 9.5 Logging spec

A successful run prints exactly this shape to stdout (this is what gets eyeballed in the Actions log, and the first line's date is what the workflow's commit step extracts):

```
[2026-07-02T08:00:14Z] Scraping scores for 2026-07-01
  MLB: 15 games (15 final, 0 skipped)
  NBA: 0 games (off-season or no games)
  NHL: 0 games (off-season or no games)
  NFL: 0 games (off-season or no games)
Wrote data/scores/2026-07-01.csv (15 rows)
Done in 8.2s
```

Failures print the league, the URL fetched, and the first 500 chars of the unparseable content before exiting non-zero.

### 9.6 Verification checkpoints (pause here)

Work in stages and **stop for review at each checkpoint** rather than building end-to-end unreviewed:

1. **Checkpoint 1 — after `teams.json` + MLB parser:** Run against a real recent date, print the parsed games. *Human spot-checks 3–4 scores against ESPN before continuing.*
2. **Checkpoint 2 — after all four parsers + CSV writer:** Produce the golden file candidate for review and hand-verification.
3. **Checkpoint 3 — after Actions workflow:** Trigger via `workflow_dispatch` once manually; confirm the commit lands and the log matches the spec in 9.5. Only then let the schedule take over.

Do not proceed past a checkpoint without explicit sign-off.
