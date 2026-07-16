# Team Wins

Pick your favorite team from each major US sport (MLB, NBA, NHL, NFL) and see how good your month — or your year — has been.

This repo is the whole system: a daily scraper, the score data itself, and a static web page that reads it. No servers, no database — **the repo is the database**.

## How it works

1. **Every morning (10:00 UTC)** a GitHub Actions workflow runs [`scrape_scores.py`](scrape_scores.py), which pulls the previous day's completed games for all four leagues from ESPN's public scoreboard API.
2. Final scores are appended to per-league CSVs in [`data/`](data/) (`mlb_scores.csv`, `nba_scores.csv`, `nhl_scores.csv`, `nfl_scores.csv`), deduplicated by game id, and committed back to the repo. Exhibitions (spring training, preseason) and postponed games are excluded; ties (NFL) are recorded with no winner.
3. **[`index.html`](index.html)** is a static page that fetches those CSVs and shows your teams' results: a headline total, a stat tile per team with a cumulative sparkline, and a game-by-game log with running totals. Team picks (including "None" per league), month/year scope, and scoring method are all selectable; picks persist in your browser.

Default teams live in [`my_teams.json`](my_teams.json); the full team list (ESPN abbreviations → names) is [`teams.json`](teams.json). Scraper details and design decisions are in [`PLAN.md`](PLAN.md).

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
