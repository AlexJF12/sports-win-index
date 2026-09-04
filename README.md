# Fandom Pulse

Pick your favorite team from each major US sport (MLB, NBA, NHL, NFL) and see how good your month — or your year — has been.

Every league keeps standings; nobody keeps the reading a fan actually feels, which is whether *this* month is a good one to follow your teams, by the only benchmark that means anything — the months that came before it. Fandom Pulse is that reading, taken fresh every morning.

This repo is the whole system: a daily scraper, the score data itself, and a static web page that reads it. No servers, no database — **the repo is the database**.

## How it works

1. **Every morning (10:00 UTC)** a GitHub Actions workflow runs [`scrape_scores.py`](scrape_scores.py), which pulls the previous day's completed games for all four leagues from ESPN's public scoreboard API.
2. Final scores are appended to per-league, per-year CSVs in [`data/`](data/) (`data/{league}/{year}/{league}_scores_{year}.csv`), deduplicated by game id, and committed back to the repo along with a regenerated `data/manifest.json` (league → years on disk, so the browser knows which year files to fetch). Exhibitions (spring training, preseason) and postponed games are excluded; ties (NFL) are recorded with no winner.
3. **[`index.html`](index.html)** — the home page — is the **city index**: every city competes with one team per league, ranked over the best day, week, month, and year, with cumulative race charts, a per-city comparison picker, and a "best fandom" section (the top team in each league regardless of city). Clicking a city opens it in **[`my-teams.html`](my-teams.html)**, which shows any team selection's results: a headline total, a stat tile per team with a cumulative sparkline, and a game-by-game log with running totals. Team picks (including "None" per league), month/year scope, and scoring method are all selectable; picks live in the URL (shareable via the Share button) and persist in your browser. **[`blog.html`](blog.html)** is the feed of everything the analysis jobs have drawn, newest first, and **[`about.html`](about.html)** explains the whole thing.

Score data goes back to **January 2010**. The analysis jobs each compare against a rolling **10-year** window (`HISTORY_YEARS`, one per job) rather than the whole archive — deep enough that a claim about a group's own history means something, recent enough that it is still the same franchise.

Default teams live in [`my_teams.json`](my_teams.json); the full team list (ESPN abbreviations → names) is [`teams.json`](teams.json). Scraper details and design decisions are in [`PLAN.md`](PLAN.md).

## City of the day (the daily analysis)

Every morning after the scrape, **[`city_of_the_day.py`](city_of_the_day.py)** draws one fandom at random and renders its season into [`content/daily/`](content/daily):

- **`season.png`** — the group's cumulative weighted index this year against their own **last 10 seasons**, day of year for day of year, so a bad start or a long climb shows up against the years that came before it. Every past year is labelled at its line's end; seasons that finish within a hair of each other would overprint, so colliding labels are nudged apart by a line of type — the label moves, the line does not
- **`month.png`** — this calendar month against the same month in each of the **last 10 years** (March 2026 against March 2025, 2024, … back to 2016), added up day by day within the month; earlier years run the full month in gray, and while the current one is still being played a dashed line marks today with a dot on each earlier year showing where *it* stood on the same date — a half-played March is never ranked against ten finished ones. Skipped when fewer than two earlier same-months are on record
- **`form.png`** — the last 30 days as each team's running record against .500, one panel per team on a shared calendar, so every step up is a win and a run shows up as a climb; the panel heading carries the record, the weighted total and the longest run — the axis counts games, where an NFL win and an MLB win are the same step, so the heading is where the index reading lives — and the subtitle reads the order of results against chance (see streakiness below)
- **`summary.md`** — the numbers behind all three, ready to paste

The draw is deliberately random rather than ranked. A detector-driven feed keeps circling back to whoever is having an extreme week; a random draw gets around the whole league, and an ordinary season is interesting once someone actually looks at it. Some care goes into the draw:

- a **city** is picked first, then a group inside it, so the 24 New York permutations take one city's worth of days instead of a quarter of the year
- the draw is **seeded by the date**, so replaying a day reproduces it exactly
- cities drawn in the last **21 days** are passed over, tracked in `content/daily/history.json` — a small file, not a folder scan
- a city only qualifies with at least 6 games in the last 30 days and 2 earlier seasons, so the charts are never empty (in midsummer that usually means the group's MLB team is the only panel on `form.png`)

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

- **`season_vs_history.png`** — this year's index for the ten city groups furthest from their own ten-season norm, one group per city, their past seasons plotted behind them in gray
- **`past_month.png`** — the last 30 days game by game as win/loss tiles, for the three streakiest and three steadiest fandoms of the month

The season chart draws a gray band at ±2: with 88 groups measured, a couple of readings past it is what chance alone produces, so the band is where a claim starts being interesting. `streakiness.json` holds the numbers for all 88 groups. Where the daily draw looks at one fandom, this is the whole field at once, so the workflow refreshes it on Wednesdays (`python streakiness.py`, or `--date YYYYMMDD` to replay a day; `--no-images` skips plotnine).

One honest caveat: the sequence is the interleaved one a fan lives through — every team in the group, in order — so it carries schedule structure (three straight games against one opponent) as well as form. That is the experience being measured, not a claim about any single team.

## Out of the norm (the weekly spotlight)

Also on Wednesdays, [`fandom_analysis.py`](fandom_analysis.py) hunts for city groups whose season is running outside their own norm. Five independent detectors run over all 88 groups, so a run's output isn't three variations on one sentence:

| Detector | What it looks for | Compared against |
|---|---|---|
| **month** | the month-to-date **weighted index** sits in the tails | every month in the last 10 years, and the same calendar month in past years |
| **year** | the year to date sits in the tails | the group's own past years at the same day of year, and all 88 groups this year |
| **streak** | the teams are on a long combined win/loss run | the longest run that group has had on record |
| **turnaround** | the month flipped sign in the last 7 days | the same month's first three weeks |
| **climb** | the group moved several places in the year standings | where it stood a week ago |

The **year** detector answers the two questions separately: *is this unusual for them* (the year to date measured at the same day of year in each of the last 10 seasons — a February comparison never runs against a full year) and *should anyone else care* (their place among all 88 groups on this year's index). A season can be a group's best ever and still sit mid-table, or middling for them and near the top; the headline states the first and the field chart shows the second. Its images are `*_year.png` — every season's cumulative index by day of year, with a dot on each past year at today's date — and `*_field.png`, the whole league sorted best to worst with the group picked out. Only the **lead** chart of a finding carries the headline; the supporting ones are titled for what they add ("Where New York sits in the field", "Who's sinking Los Angeles"), because on [the blog](#the-blog-bloghtml) they stack, and the same sentence three times reads like a machine wrote the page. Every subtitle still names the group, so a chart lifted out on its own stands up.

The **month** detector compares against the same month-to-date window (same day-of-month cutoff) along two lanes — **every month in the last 10 years**, and **the same calendar month in previous years** (July vs past Julys, so a baseball-only month is never judged against four-league months where the index swings harder). Both tails count: a historically great month and a historically awful one are equally postable. Claims are kept honest three ways — percentiles are shrunk for small samples so "best of 5 Julys" claims less than "best of 61 months"; a lane is dropped when its claim contradicts the month's sign (no "best July on record" on a losing month); and a calendar claim is damped when the all-months lane says the month is thoroughly average.

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
    content/posts/2026-08-03/daily-month.png
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

### Every post goes to Bluesky

[`share_bluesky.py`](share_bluesky.py) reads the manifest the publisher just wrote and announces what's in it, on its own schedule ([`bluesky.yml`](.github/workflows/bluesky.yml)) rather than inside the scrape.

**Publishing and announcing want opposite clocks.** The scrape runs at 10:00 UTC because that's comfortably after every league's last game of the previous day — and 10:00 UTC is 5–6am Eastern, close to the worst hour of the day to post anything. So the site still updates at dawn and the timeline hears about it at **22:00 UTC**: 6pm Eastern in summer, 5pm in winter (3pm / 2pm Pacific). After work, before first pitch, inside the evening window where US social traffic peaks and a sports audience is already thinking about sports.

> Cron is UTC and doesn't follow DST, so the Eastern hour drifts by one across the year. That's inside the window either way, and it's one line to change. Treat the hour as a starting hypothesis: after a few weeks of posts the account's own numbers beat any rule of thumb.

**The chart goes out full size, not as a link card.** A card is the tidier shape — the URL never appears as text — and it's the wrong one. A card's thumbnail renders a few hundred pixels wide and cropped, and these charts are 1600×920 with 8pt axis labels, so the picture lands as a gray smudge and the work in it is invisible. An images embed renders full width in the timeline and opens to full size on a tap. The two embeds are mutually exclusive, so the link lives in the post text instead, as a rich-text facet — offsets counted in UTF-8 *bytes*, since these headlines carry em dashes and the odd emoji and a facet counted in characters would land on the wrong slice.

Above the chart go two lines: **the fandom, then the number that makes it worth a stranger's attention.** "City of the day — Baltimore" is how the blog files a post; what somebody actually stops for is

> Baltimore: Orioles/Ravens
> August 2026 through day 13: 4-7, -6.8 weighted — the worst of the 11 on record

Both strings were already in the manifest. Only one of them was worth leading with. The image carries the alt text `publish_blog.py` already builds, which the link card had no field for and was throwing away.

### Not every post is worth posting

The daily is a city a morning, in rotation, whether or not anything happened to it — that's right for a blog and wrong for a timeline. An account that posts an unremarkable team every single day teaches the people following it to scroll past its name, and then the one morning something *is* remarkable gets scrolled past too. So the weekly reads always go out (a spotlight only exists at all when a detector fired, and the streakiness pair is the whole field at once) and **a daily has to clear a bar**:

| Signal | Bar | Why there |
|---|---|---|
| This month against every past one | **best or worst on record**, out of ≥5 | "their best August ever" is a sentence someone repeats; "their 2nd-best August" is one nobody finishes — and top-*two* of eleven fires on better than a third of mornings by chance |
| Longest run, last 30 days | **6 straight** | most of a month going one way |
| Longest run, full season | **10 straight** | six straight across 230 games is a fortnight nobody noticed |
| Order of results | **outside the chance band** | `streakiness.py`'s own "clumpier than chance" / "more alternating than chance" |

Every bar is the analysis's own phrasing and the analysis's own threshold — `fandom_analysis.standing()`, `streakiness.CHANCE_BAND` — parsed back out of the manifest rather than re-derived, so a change to how the analysis measures carries through instead of drifting away from it. Whichever signal fires is also the line the post leads with, since the reason it's worth sharing is the reason it's worth reading.

Measured against the blog as it stands, that's **12 shares across 16 days instead of 20** — 4 of 12 dailies, all 5 spotlights, all 3 streakiness runs, about five posts a week. `--share-daily always` restores the old behavior; `--share-daily never` leaves the timeline to the weeklies.

> The month signal is the strongest one and it only landed in `city_of_the_day.py` recently, so most posts already in the manifest don't carry that bullet and are judged on runs alone. New posts get all four.

### The record

What has gone out is remembered in `content/posts/bluesky.json`, keyed on date and kind, next to a fingerprint of what was shared: the text, the URL, the alt, and the bytes of the chart. That precision is the point — `publish_blog.py` rewrites every page every morning and almost all of it comes out byte-identical, so anything looser would re-share the whole 90-day window daily. A run that changes one of those four is an update, and **an updated post is shared again**: Bluesky posts can't be edited, so the superseded share is deleted and a fresh one replaces it (`--keep-superseded` leaves the old one up). Archived posts, whose charts are gone, are never shared.

A run reaches back one day from the newest post (`--max-age-days`) and shares at most four (`--max-posts`, since a Wednesday carries three) — which is what keeps the first run against a manifest full of history from posting ninety days of backlog. `--backfill` lifts the window when that's what you want. The state file rides along in the same commit as the post it refers to.

**Setting it up.** Until the account exists, `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD` are placeholders at the top of the script, and a run that finds a placeholder says what's missing and exits 0 — the daily scrape must not start failing because the account for it doesn't exist yet. To finish it:

1. Create the Bluesky account and note its handle. **`fandompulse.bsky.social`** is the one to try first — it is the site's name and nothing else, which is what a reader has to be able to type from memory after seeing one chart in a timeline. If it's gone, `fandompulsehq`, `thefandompulse` and `pulseoffandom` are the fallbacks in that order; avoid anything with a sport or a league in it, since the account covers all four. Once the site has its own domain the handle can move to it (Bluesky verifies handles by DNS), and the `.bsky.social` name stays claimed either way.
2. In Bluesky: **Settings → Privacy and security → App passwords → Add App Password**. Use that, never the account password — it can be revoked on its own and can't change the account's email or password.
3. In this repo: **Settings → Secrets and variables → Actions → New repository secret**, twice — `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD`.
4. Run the **Share to Bluesky** workflow by hand with `dry_run` on to see what it would post, then off to post it. After that the nightly schedule carries it.

A hand-started run fails loudly when the secrets are missing; the nightly one exits quietly, so an account that doesn't exist yet isn't a red X every evening. Because the two workflows are separate, a Bluesky outage costs a share and never the day's scores — the post stays pending and goes out the next evening. Nothing is ever posted from a local run unless you set both environment variables yourself; `--dry-run` needs no credentials at all.

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

## The look

Five hand-written pages and one generated one, and no build step to share a stylesheet between them — so the brand is a small block of CSS copied into each page's `<style>`, plus [`blog.css`](blog.css) for the blog and the post pages. It is short on purpose, and it is the only part that must stay in step across files.

- **The bar.** Every page opens with the same sticky top bar: the wordmark on the left (three ascending bars, the tallest in the accent violet — the same shape as the favicon, and the same three `rect`s that `publish_blog.py` writes into a post page), the five destinations on the right as pills, the current one filled. The page's own `h1` sits below it, so the brand and the page name never compete for the same line.
- **The accent.** `--accent` (`#7d3ff0` light, `#9d6bff` dark) marks *the thing you chose or the thing that won*: the pressed segment of a metric toggle, today's box in the week strip, the rule down the champion card, the active blog filter, every focus ring. It is deliberately not green or red — those two are spoken for by wins and losses, and an accent that borrows either would read as a score.
- **Everything else is unchanged.** Same warm paper and near-black surfaces, same type scale, same charts. Headline numbers just got tabular figures and tighter tracking, cards got 14px corners, and anything clickable got a 0.15s transition — dropped entirely under `prefers-reduced-motion`.

Changing the mark means changing four things together: the `.wordmark` rules and the inline `<svg>` in each of the four hand-written pages, the same pair in `blog.css` and `publish_blog.MARK`, the `<link rel="icon">` in every page, and `publish_blog.FAVICON`. Post pages already on disk pick it up on the next `publish_blog.py` run, which rewrites all of them.

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
python3 share_bluesky.py --dry-run       # what would go out to Bluesky (no credentials needed)
python3 share_bluesky.py                 # share it (needs $BLUESKY_HANDLE, $BLUESKY_APP_PASSWORD)
python3 backfill_blog.py --dry-run       # what the git log would add to the blog
python3 -m pytest tests/                 # test suite (offline, fixture-based)
```
