# Team Wins

Pick your favorite team from each major US sport (MLB, NBA, NHL, NFL) and see how good your month — or your year — has been.

This repo is the whole system: a daily scraper, the score data itself, and a static web page that reads it. No servers, no database — **the repo is the database**.

## How it works

1. **Every morning (10:00 UTC)** a GitHub Actions workflow runs [`scrape_scores.py`](scrape_scores.py), which pulls the previous day's completed games for all four leagues from ESPN's public scoreboard API.
2. Final scores are appended to per-league, per-year CSVs in [`data/`](data/) (`data/{league}/{year}/{league}_scores_{year}.csv`), deduplicated by game id, and committed back to the repo along with a regenerated `data/manifest.json` (league → years on disk, so the browser knows which year files to fetch). Exhibitions (spring training, preseason) and postponed games are excluded; ties (NFL) are recorded with no winner.
3. **[`index.html`](index.html)** — the home page — is the **city index**: every city competes with one team per league, ranked over the best day, week, month, and year, with cumulative race charts, a per-city comparison picker, and a "best fandom" section (the top team in each league regardless of city). Clicking a city opens it in **[`my-teams.html`](my-teams.html)**, which shows any team selection's results: a headline total, a stat tile per team with a cumulative sparkline, and a game-by-game log with running totals. Team picks (including "None" per league), month/year scope, and scoring method are all selectable; picks live in the URL (shareable via the Share button) and persist in your browser. **[`about.html`](about.html)** explains the whole thing.

Default teams live in [`my_teams.json`](my_teams.json); the full team list (ESPN abbreviations → names) is [`teams.json`](teams.json). Scraper details and design decisions are in [`PLAN.md`](PLAN.md).

## Fandom spotlight (daily social content)

After the scrape, the workflow runs [`fandom_analysis.py`](fandom_analysis.py), which hunts for a postable story about any city group. Four independent detectors run over all 88 groups, so a day's output isn't three variations on one sentence:

| Detector | What it looks for |
|---|---|
| **month** | the month-to-date **weighted index** sits in the tails of the group's own history |
| **streak** | the group's teams are on a long combined win/loss run |
| **turnaround** | the month flipped sign in the last 7 days — bad team, good week, or the reverse |
| **climb** | the group moved several places in the year-to-date standings this week |

The **month** detector compares against the same month-to-date window (same day-of-month cutoff) along two lanes — **every month since 2022**, and **the same calendar month in previous years** (July vs past Julys, so a baseball-only month is never judged against four-league months where the index swings harder). Both tails count: a historically great month and a historically awful one are equally postable. Claims are kept honest three ways — percentiles are shrunk for small samples so "best of 5 Julys" claims less than "best of 55 months"; a lane is dropped when its claim contradicts the month's sign (no "best July on record" on a losing month); and a calendar claim is damped when the all-months lane says the month is thoroughly average.

Because this runs *every day* and month-to-date totals barely move overnight, selection is tuned for variety: one group per city, at most two findings of the same kind, a cooldown on cities featured in the last few days (read back from previous `findings.json` files), and a date-seeded jitter that shuffles near-ties so two similar days don't produce identical picks. The top 3 land in `content/YYYY-MM-DD/`:

- `findings.json` — everything the renderer and the summary are built from
- `summary.md` — headlines plus copy-pasteable stats (record, weighted points, percentile, streaks, who drove it)
- three plotnine PNGs per finding: `*_race.png` (the group vs the whole field this month) and `*_teams.png` (per-team contribution, month vs last 7 days), plus one that depends on the detector — `*_history.png`, `*_timeline.png`, or `*_bump.png`

Browse the day's folder, pick what's worth posting. Backfill or replay a day with `python fandom_analysis.py --date 20260716 && python render_content.py --date 20260716`; `--kinds`, `--compare`, `--top`, and `--no-novelty` narrow what a run considers.

> Each day costs roughly **1 MB** of images (~200 MB/year). If that gets heavy, prune old `content/` folders — nothing else reads them except the novelty cooldown, which only looks back 10 days.

## The three scoring methods

The page can score your teams' games three ways:

| Method | Win | Loss | Tie |
|---|---|---|---|
| **Wins** | +1 | 0 | 0 |
| **Net W−L** | +1 | −1 | 0 |
| **Weighted** | +365 ÷ season length | −365 ÷ season length | 0 |

- **Wins** — the simple count. How many times did your teams win this month?
- **Net W−L** — wins minus losses. A 4–7 stretch shows as −3, so a losing month actually *looks* like a losing month.
- **Weighted** — every game is worth its share of the year, `365 ÷ regular-season games`, so a game in a short season counts for more:

  | League | Season games | Points per game |
  |---|---|---|
  | MLB | 162 | ±2.25 |
  | NBA | 82 | ±4.45 |
  | NHL | 82 | ±4.45 |
  | NFL | 17 | ±21.47 |

  The idea: one NFL Sunday carries about as much of a season's weight as three weeks of baseball. A Commanders win (+21.5) can cancel out a rough Nationals homestand — and a bad Wizards season can sink your whole year.

## Running the page locally

```
python3 -m http.server
```

from the repo root, then open http://localhost:8000. (Or enable GitHub Pages — Settings → Pages → deploy from `main`, root — and it updates automatically as the nightly workflow commits new scores.)

## Development

```
pip install -r requirements.txt          # requests
python3 scrape_scores.py                 # scrape yesterday (US/Eastern)
python3 scrape_scores.py --date 20260601 # backfill a specific date
python3 -m pytest tests/                 # test suite (offline, fixture-based)
```
