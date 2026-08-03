# Team Wins

Pick your favorite team from each major US sport (MLB, NBA, NHL, NFL) and see how good your month — or your year — has been.

This repo is the whole system: a daily scraper, the score data itself, and a static web page that reads it. No servers, no database — **the repo is the database**.

## How it works

1. **Every morning (10:00 UTC)** a GitHub Actions workflow runs [`scrape_scores.py`](scrape_scores.py), which pulls the previous day's completed games for all four leagues from ESPN's public scoreboard API.
2. Final scores are appended to per-league, per-year CSVs in [`data/`](data/) (`data/{league}/{year}/{league}_scores_{year}.csv`), deduplicated by game id, and committed back to the repo along with a regenerated `data/manifest.json` (league → years on disk, so the browser knows which year files to fetch). Exhibitions (spring training, preseason) and postponed games are excluded; ties (NFL) are recorded with no winner.
3. **[`index.html`](index.html)** — the home page — is the **city index**: every city competes with one team per league, ranked over the best day, week, month, and year, with cumulative race charts, a per-city comparison picker, and a "best fandom" section (the top team in each league regardless of city). Clicking a city opens it in **[`my-teams.html`](my-teams.html)**, which shows any team selection's results: a headline total, a stat tile per team with a cumulative sparkline, and a game-by-game log with running totals. Team picks (including "None" per league), month/year scope, and scoring method are all selectable; picks live in the URL (shareable via the Share button) and persist in your browser. **[`about.html`](about.html)** explains the whole thing.

Default teams live in [`my_teams.json`](my_teams.json); the full team list (ESPN abbreviations → names) is [`teams.json`](teams.json). Scraper details and design decisions are in [`PLAN.md`](PLAN.md).

## City of the day (the daily analysis)

Every morning after the scrape, **[`city_of_the_day.py`](city_of_the_day.py)** draws one fandom at random and renders its season into [`content/daily/`](content/daily):

- **`season.png`** — the group's cumulative weighted index this year against their own earlier seasons, day of year for day of year, so a bad start or a long climb shows up against the years that came before it
- **`games.png`** — every game of the last 30 days as win/loss tiles, one row per team, with each team's record and longest run; the subtitle reads the order of results against chance (see streakiness below)
- **`summary.md`** — the numbers behind both, ready to paste

The draw is deliberately random rather than ranked. A detector-driven feed keeps circling back to whoever is having an extreme week; a random draw gets around the whole league, and an ordinary season is interesting once someone actually looks at it. Some care goes into the draw:

- a **city** is picked first, then a group inside it, so the 24 New York permutations take one city's worth of days instead of a quarter of the year
- the draw is **seeded by the date**, so replaying a day reproduces it exactly
- cities drawn in the last **21 days** are passed over, tracked in `content/daily/history.json` — a small file, not a folder scan
- a city only qualifies with at least 6 games in the last 30 days and 2 earlier seasons, so neither chart is ever empty (in midsummer that usually means the group's MLB team is the only row on `games.png`)

Force a pick with `--city Detroit`, replay a day with `--date 20260716`, or skip the plotnine import with `--no-images`.

> The files are overwritten in place, so the working tree holds one day's images and git history holds the archive — about 260 KB of new blobs a day, ~95 MB a year. Cutting to one image, or to weekdays only, halves that if it ever gets heavy.

## Streakiness (the weekly standing charts)

Winning percentage says how *often* a fandom wins. **[`streakiness.py`](streakiness.py)** asks how those wins *arrive* — in runs, or shuffled — and whether that is normal for the group. The measure is the Wald–Wolfowitz runs test over the sequence of games the group's teams actually played, sign-flipped so bigger means clumpier:

| Streak index | Reading |
|---|---|
| **+2 or more** | clumpier than chance — long heaters and long skids |
| **0** | exactly as clumped as coin flips at that win rate |
| **−2 or less** | more alternating than chance — wins and losses take turns |

Because the expected number of runs is conditioned on the group's actual win and loss counts, the index is close to independent of *how good* they are: a .500 season of five-game swings and a .500 season of win-loss-win-loss score at opposite ends. Two images live in [`content/streakiness/`](content/streakiness), always at the same two paths, so the repo carries two files rather than a growing pile:

- **`season_vs_history.png`** — this year's index for the ten city groups furthest from their own 2022–2025 norm, one group per city, their past seasons plotted behind them in gray
- **`past_month.png`** — the last 30 days game by game as win/loss tiles, for the three streakiest and three steadiest fandoms of the month

The season chart draws a gray band at ±2: with 88 groups measured, a couple of readings past it is what chance alone produces, so the band is where a claim starts being interesting. `streakiness.json` holds the numbers for all 88 groups. Where the daily draw looks at one fandom, this is the whole field at once, so the workflow refreshes it on Mondays (`python streakiness.py`, or `--date YYYYMMDD` to replay a day; `--no-images` skips plotnine).

One honest caveat: the sequence is the interleaved one a fan lives through — every team in the group, in order — so it carries schedule structure (three straight games against one opponent) as well as form. That is the experience being measured, not a claim about any single team.

## Out of the norm (the weekly spotlight)

Also on Mondays, [`fandom_analysis.py`](fandom_analysis.py) hunts for city groups whose season is running outside their own norm. Five independent detectors run over all 88 groups, so a run's output isn't three variations on one sentence:

| Detector | What it looks for | Compared against |
|---|---|---|
| **month** | the month-to-date **weighted index** sits in the tails | every month since 2022, and the same calendar month in past years |
| **year** | the year to date sits in the tails | the group's own past years at the same day of year, and all 88 groups this year |
| **streak** | the teams are on a long combined win/loss run | the longest run that group has had since 2022 |
| **turnaround** | the month flipped sign in the last 7 days | the same month's first three weeks |
| **climb** | the group moved several places in the year standings | where it stood a week ago |

The **year** detector answers the two questions separately: *is this unusual for them* (the year to date measured at the same day of year in every season since 2022 — a February comparison never runs against a full year) and *should anyone else care* (their place among all 88 groups on this year's index). A season can be a group's best ever and still sit mid-table, or middling for them and near the top; the headline states the first and the field chart shows the second. Its images are `*_year.png` — every season's cumulative index by day of year, with a dot on each past year at today's date — and `*_field.png`, the whole league sorted best to worst with the group picked out.

The **month** detector compares against the same month-to-date window (same day-of-month cutoff) along two lanes — **every month since 2022**, and **the same calendar month in previous years** (July vs past Julys, so a baseball-only month is never judged against four-league months where the index swings harder). Both tails count: a historically great month and a historically awful one are equally postable. Claims are kept honest three ways — percentiles are shrunk for small samples so "best of 5 Julys" claims less than "best of 55 months"; a lane is dropped when its claim contradicts the month's sign (no "best July on record" on a losing month); and a calendar claim is damped when the all-months lane says the month is thoroughly average.

Because these totals move slowly between runs, selection is tuned for variety: one group per city, at most two findings of the same kind, a cooldown on cities featured in recent runs (ramping back to full eligibility over three weeks, read from a run log rather than by scanning folders), and a date-seeded jitter that shuffles near-ties so two similar runs don't produce identical picks. The top 3 overwrite `content/weekly/`:

- `findings.json` — everything the renderer and the summary are built from
- `summary.md` — headlines plus copy-pasteable stats (record, weighted points, percentile, streaks, who drove it)
- `history.json` — the run log the cooldown reads
- two or three plotnine PNGs per finding, depending on the detector: `year` gets `*_year.png` + `*_field.png`; the month-scoped kinds get `*_race.png` (the group vs the whole field this month) and `*_teams.png` (per-team contribution, month vs last 7 days) plus `*_history.png`, `*_timeline.png`, or `*_bump.png`

Replay a run with `python fandom_analysis.py --date 20260716 && python render_content.py`; `--kinds`, `--compare`, `--top`, and `--no-novelty` narrow what a run considers. Only the **month** and **turnaround** detectors need a few days of games on the clock, so a run on the 1st of a month now returns year, streak and climb findings instead of the blank page it used to.

> Weekly, this costs roughly **1 MB** of images — ~50 MB/year — against ~200 MB/year when the same thing ran nightly. `--top 2` trims it if that matters.

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
python3 aggregate_cities.py              # rebuild data/city_rankings.json
python3 city_of_the_day.py               # rebuild content/daily/
python3 streakiness.py                   # rebuild content/streakiness/
python3 fandom_analysis.py && python3 render_content.py   # rebuild content/weekly/
python3 -m pytest tests/                 # test suite (offline, fixture-based)
```
