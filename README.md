# Team Wins

Pick your favorite team from each major US sport (MLB, NBA, NHL, NFL) and see how good your month — or your year — has been.

This repo is the whole system: a daily scraper, the score data itself, and a static web page that reads it. No servers, no database — **the repo is the database**.

## How it works

1. **Every morning (10:00 UTC)** a GitHub Actions workflow runs [`scrape_scores.py`](scrape_scores.py), which pulls the previous day's completed games for all four leagues from ESPN's public scoreboard API.
2. Final scores are appended to per-league, per-year CSVs in [`data/`](data/) (`data/{league}/{year}/{league}_scores_{year}.csv`), deduplicated by game id, and committed back to the repo along with a regenerated `data/manifest.json` (league → years on disk, so the browser knows which year files to fetch). Exhibitions (spring training, preseason) and postponed games are excluded; ties (NFL) are recorded with no winner.
3. **[`index.html`](index.html)** — the home page — is the **city index**: every city competes with one team per league, ranked over the best day, week, month, and year, with cumulative race charts, a per-city comparison picker, and a "best fandom" section (the top team in each league regardless of city). Clicking a city opens it in **[`my-teams.html`](my-teams.html)**, which shows any team selection's results: a headline total, a stat tile per team with a cumulative sparkline, and a game-by-game log with running totals. Team picks (including "None" per league), month/year scope, and scoring method are all selectable; picks live in the URL (shareable via the Share button) and persist in your browser. **[`blog.html`](blog.html)** is the feed of everything the analysis jobs have drawn, newest first, and **[`about.html`](about.html)** explains the whole thing.

Default teams live in [`my_teams.json`](my_teams.json); the full team list (ESPN abbreviations → names) is [`teams.json`](teams.json). Scraper details and design decisions are in [`PLAN.md`](PLAN.md).

## City of the day (the daily analysis)

Every morning after the scrape, **[`city_of_the_day.py`](city_of_the_day.py)** draws one fandom at random and renders its season into [`content/daily/`](content/daily):

- **`season.png`** — the group's cumulative weighted index this year against their own earlier seasons, day of year for day of year, so a bad start or a long climb shows up against the years that came before it
- **`form.png`** — the last 30 days as each team's running record against .500, one panel per team on a shared calendar, so every step up is a win and a run shows up as a climb; the panel heading carries the record, the weighted total and the longest run — the axis counts games, where an NFL win and an MLB win are the same step, so the heading is where the index reading lives — and the subtitle reads the order of results against chance (see streakiness below)
- **`summary.md`** — the numbers behind both, ready to paste

The draw is deliberately random rather than ranked. A detector-driven feed keeps circling back to whoever is having an extreme week; a random draw gets around the whole league, and an ordinary season is interesting once someone actually looks at it. Some care goes into the draw:

- a **city** is picked first, then a group inside it, so the 24 New York permutations take one city's worth of days instead of a quarter of the year
- the draw is **seeded by the date**, so replaying a day reproduces it exactly
- cities drawn in the last **21 days** are passed over, tracked in `content/daily/history.json` — a small file, not a folder scan
- a city only qualifies with at least 6 games in the last 30 days and 2 earlier seasons, so neither chart is ever empty (in midsummer that usually means the group's MLB team is the only panel on `form.png`)

Force a pick with `--city Detroit`, replay a day with `--date 20260716`, or skip the plotnine import with `--no-images`.

> The files are overwritten in place — the working tree holds one day's images. The archive is [`content/posts/`](content/posts), filed by [the blog](#the-blog-bloghtml), which keeps the charts for 90 days and the numbers for good. Git history holds every version of both: about 260 KB of new blobs a day, ~95 MB a year. Cutting to one image, or to weekdays only, halves that if it ever gets heavy.

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

The season chart draws a gray band at ±2: with 88 groups measured, a couple of readings past it is what chance alone produces, so the band is where a claim starts being interesting. `streakiness.json` holds the numbers for all 88 groups. Where the daily draw looks at one fandom, this is the whole field at once, so the workflow refreshes it on Wednesdays (`python streakiness.py`, or `--date YYYYMMDD` to replay a day; `--no-images` skips plotnine).

One honest caveat: the sequence is the interleaved one a fan lives through — every team in the group, in order — so it carries schedule structure (three straight games against one opponent) as well as form. That is the experience being measured, not a claim about any single team.

## Out of the norm (the weekly spotlight)

Also on Wednesdays, [`fandom_analysis.py`](fandom_analysis.py) hunts for city groups whose season is running outside their own norm. Five independent detectors run over all 88 groups, so a run's output isn't three variations on one sentence:

| Detector | What it looks for | Compared against |
|---|---|---|
| **month** | the month-to-date **weighted index** sits in the tails | every month since 2022, and the same calendar month in past years |
| **year** | the year to date sits in the tails | the group's own past years at the same day of year, and all 88 groups this year |
| **streak** | the teams are on a long combined win/loss run | the longest run that group has had since 2022 |
| **turnaround** | the month flipped sign in the last 7 days | the same month's first three weeks |
| **climb** | the group moved several places in the year standings | where it stood a week ago |

The **year** detector answers the two questions separately: *is this unusual for them* (the year to date measured at the same day of year in every season since 2022 — a February comparison never runs against a full year) and *should anyone else care* (their place among all 88 groups on this year's index). A season can be a group's best ever and still sit mid-table, or middling for them and near the top; the headline states the first and the field chart shows the second. Its images are `*_year.png` — every season's cumulative index by day of year, with a dot on each past year at today's date — and `*_field.png`, the whole league sorted best to worst with the group picked out. Only the **lead** chart of a finding carries the headline; the supporting ones are titled for what they add ("Where New York sits in the field", "Who's sinking Los Angeles"), because on [the blog](#the-blog-bloghtml) they stack, and the same sentence three times reads like a machine wrote the page. Every subtitle still names the group, so a chart lifted out on its own stands up.

The **month** detector compares against the same month-to-date window (same day-of-month cutoff) along two lanes — **every month since 2022**, and **the same calendar month in previous years** (July vs past Julys, so a baseball-only month is never judged against four-league months where the index swings harder). Both tails count: a historically great month and a historically awful one are equally postable. Claims are kept honest three ways — percentiles are shrunk for small samples so "best of 5 Julys" claims less than "best of 55 months"; a lane is dropped when its claim contradicts the month's sign (no "best July on record" on a losing month); and a calendar claim is damped when the all-months lane says the month is thoroughly average.

Because these totals move slowly between runs, selection is tuned for variety: one group per city, at most two findings of the same kind, a cooldown on cities featured in recent runs (ramping back to full eligibility over three weeks, read from a run log rather than by scanning folders), and a date-seeded jitter that shuffles near-ties so two similar runs don't produce identical picks. The top 3 overwrite `content/weekly/`:

- `findings.json` — everything the renderer and the summary are built from
- `summary.md` — headlines plus copy-pasteable stats (record, weighted points, percentile, streaks, who drove it)
- `history.json` — the run log the cooldown reads
- two or three plotnine PNGs per finding, depending on the detector: `year` gets `*_year.png` + `*_field.png`; the month-scoped kinds get `*_race.png` (the group vs the whole field this month) and `*_teams.png` (per-team contribution, month vs last 7 days) plus `*_history.png`, `*_timeline.png`, or `*_bump.png`

Replay a run with `python fandom_analysis.py --date 20260716 && python render_content.py`; `--kinds`, `--compare`, `--top`, and `--no-novelty` narrow what a run considers. Only the **month** and **turnaround** detectors need a few days of games on the clock, so a run on the 1st of a month now returns year, streak and climb findings instead of the blank page it used to.

> Weekly, this costs roughly **1 MB** of images — ~50 MB/year — against ~200 MB/year when the same thing ran nightly. `--top 2` trims it if that matters.

## The blog ([`blog.html`](blog.html))

Every image above lives at a fixed path and is overwritten by the next run, which is the right shape for a working tree and the wrong shape for reading. [`publish_blog.py`](publish_blog.py) files each run's output into a dated folder and writes the manifest the page reads:

    content/posts/2026-08-03/daily-season.png
    content/posts/2026-08-03/daily-form.png
    content/posts/index.json

**One post per run, not one per image.** The morning's city of the day is a post; Wednesday's spotlight is a post carrying its three findings; Wednesday's streakiness charts are a post. The feed is reverse-chronological, so a Wednesday shows all three and an ordinary Tuesday shows one. Chips filter it to a single kind.

Each post is a headline, a collapsed **the numbers** panel — the same records, percentiles and per-team tables the run wrote to `summary.md` — and then the charts. The numbers sit *with* the claim rather than below the images, because a stat you meet two screens after the sentence it supports is a stat you read twice.

`summary.md` is the only source of that prose: the publisher parses it rather than restating it, so an edit to the analysis's wording carries straight through to the blog. Image alt text is built separately, leading with whose chart it is, so a screen reader isn't read the visible caption twice.

A spotlight carries three findings at two or three charts each, and showing all of them makes a post four screens tall. So a **multi-section post shows each finding's lead chart** and folds the rest behind "2 more charts"; a single-section post — a morning, the streakiness pair — is short enough to show whole. Charts are dense, so clicking one opens it fitted to the screen, and clicking again goes to actual size with pan, which is the only way to read a 1600px chart on a phone.

### Every post is a page

The feed renders from JSON in the browser, which means a link into it unfurls as nothing in a chat and crawls as nothing. So each post also gets a real page — `content/posts/2026-08-02/spotlight.html` — with the headline in `<title>`, the numbers and every chart in the markup rather than assembled at runtime, a canonical URL, and the lead chart as its `og:image` card. No script, one shared stylesheet ([`blog.css`](blog.css), the same one the feed uses, so the two can't drift). The badge on each card in the feed is the permalink; `#day-2026-08-02` and `#post-2026-08-02-spotlight` still work for scrolling within the feed.

Absolute URLs need to know where the site lives — `--base-url`, defaulting to the GitHub Pages address. Change it if the site ever moves to its own domain.

The kind filter lives in the query string (`?kind=spotlight`), so a filtered view survives a reload and the back button does what it should — the same URL-is-the-state rule the [my teams](my-teams.html) view follows. A deep link renders as many batches as it takes to reach its post, pulling in the archive if that's where the post lives.

Each source is keyed on the reference date in its own output, so running the publisher on a Thursday re-files Wednesday's weekly folder under Wednesday and changes nothing. Re-running a day replaces that day's post rather than adding one, and a replayed run that no longer draws an image deletes the stale copy.

One failure mode is worth naming: `city_of_the_day.py` writes `summary.md` before it renders, so a run that dies in plotnine leaves today's prose beside yesterday's PNGs. The publisher compares each image against the one the previous post of that kind published and drops anything byte-identical — every chart carries its own date, so an unchanged image means it was never redrawn. A post left with no images isn't published at all, and the day the render is fixed the post appears normally.

### Retention takes the pixels, not the post

After **90 days** a post's images are deleted (`--retain-days` to change the window, `--no-prune` to keep them). Everything else stays **forever**: the headline, the numbers, and the post's own page, which now reads "the charts aged out" and keeps the record. A link shared today still resolves in five years — it just stops showing charts.

The arithmetic is why. At ~260 KB a morning and ~1 MB a Wednesday, images run ~147 MB/year and the window holds them near 35 MB; the record they leave behind is ~4.7 KB of page and ~1.6 KB of JSON per post, about **3 MB/year**. Deleting a folder outright would have bought nothing and broken every link into it.

So the manifest is two files: `index.json` holds the posts still inside the window, and `archive.json` holds the rest. The feed loads only the index and fetches the archive when you ask for older posts — the first paint stays the same size in year five as in week one. Archived posts render as text cards: headline, subhead, the numbers, and a line saying where the charts went.

> Pruning bounds the *checkout*, not history — the image blobs stay in git, so clone size still grows at the full rate.

[`backfill_blog.py`](backfill_blog.py) seeds the blog from git history: it walks the commit log, exports each run's folder to a scratch directory, and hands it to the same builders, so a backfilled post and a fresh one are identical. It reads all three layouts the log contains, including the original `content/<date>/` spotlight folders. `--dry-run` lists what it would publish.

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
python3 publish_blog.py                  # file the newest runs into content/posts/
python3 publish_blog.py --retain-days 1  # see what an archived post looks like
python3 backfill_blog.py --dry-run       # what the git log would add to the blog
python3 -m pytest tests/                 # test suite (offline, fixture-based)
```
