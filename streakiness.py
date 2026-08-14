#!/usr/bin/env python3
"""
Streakiness: how clumped a fandom's wins and losses are, now vs their history.

Winning percentage says how *often* a fandom wins. This says how those wins
arrive — in runs, or shuffled. Both come from the same sequence of games, and
they are close to independent: a .500 fandom can live a season of five-game
heaters and five-game skids, or a season of win-loss-win-loss.

The measure is the Wald-Wolfowitz runs test on the group's game sequence, with
the sign flipped so that bigger = streakier:

    streak index = -(R - E[R]) / sd(R)

where R is the number of runs (a run = a maximal block of same results) in a
sequence with the group's actual win/loss counts. Because E[R] is conditioned
on those counts, the index is independent of *how good* the group is: it only
asks whether the order was clumpier than a shuffle of the same results.

    +2 or more   clumpier than chance — long heaters and long skids
     0           exactly as clumped as coin flips at that win rate
    -2 or less   more alternating than chance — wins and losses take turns

Two charts land in content/streakiness/, always at the same paths, so the repo
carries two images rather than a growing pile:

    season_vs_history.png   this year's streak index against the same group's
                            ten earlier seasons, one group per city, the
                            biggest departures from their own norm
    past_month.png          the last 30 days game by game, win/loss tiles, for
                            the streakiest and steadiest fandoms of the month

The sequence is the one a fan actually lives through: every game played by
every team in the group, in order. That includes schedule structure (a team
plays three straight against one opponent), which is part of the experience
but means the index is not a pure claim about a single team's form.

Usage:
    python streakiness.py                  # reference date = yesterday (ET)
    python streakiness.py --date 20260802
"""

import argparse
import json
import logging
import math
import os
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aggregate_cities import index_by_team, load_scores
from fandom_analysis import display_label, group_games

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
OUT_DIR = os.path.join("content", "streakiness")
HISTORY_YEARS = 10          # scores go back to 2010; this is how far back the
                            # standing charts reach, rolling off the reference
                            # date rather than anchored to a year
MONTH_DAYS = 30             # the "past month" window
MIN_SEASON_GAMES = 60       # below this a season's index is too noisy to plot
MIN_MONTH_GAMES = 15        # same, for the 30-day window
MIN_SEASONS = 3             # prior seasons needed before "vs history" means much
TOP_N = 5                   # rows per side of the season chart
CHANCE_BAND = 2.0           # |index| below this is ordinary sampling noise


# --- the measure -------------------------------------------------------------

def band_reading(index: float | None) -> str | None:
    """Which side of the chance band an index falls on — "clumpy",
    "alternating" or "chance", and None when there was too little to score.

    Everywhere the index gets put into words (a summary bullet, a chart
    subtitle, the blog's legend) has to agree about where the band starts, or
    one page says a season was streaky while the chart beside it says the
    order was ordinary. Callers classify here and only do the wording
    themselves.
    """
    if index is None:
        return None
    if index >= CHANCE_BAND:
        return "clumpy"
    if index <= -CHANCE_BAND:
        return "alternating"
    return "chance"


def streak_index(results: list) -> float | None:
    """Runs-test z for a list of 'W'/'L'/'T' results, sign-flipped so that
    positive = streakier than chance. Ties are dropped (they end a run without
    starting one, and there are only a handful of them in NFL history).

    None when either outcome appears fewer than twice, where the runs
    distribution is degenerate and the z-score is meaningless.
    """
    seq = [r for r in results if r in ("W", "L")]
    wins = seq.count("W")
    losses = seq.count("L")
    n = wins + losses
    if wins < 2 or losses < 2:
        return None
    runs = 1 + sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    expected = 1 + 2 * wins * losses / n
    variance = (2 * wins * losses * (2 * wins * losses - n)) / (n * n * (n - 1))
    if variance <= 0:
        return None
    return round(-(runs - expected) / math.sqrt(variance), 3)


def longest_run(results: list) -> dict:
    """The longest block of same result in the sequence."""
    best = {"type": "", "length": 0}
    kind, run = None, 0
    for r in results:
        run = run + 1 if r == kind else 1
        kind = r
        if r in ("W", "L") and run > best["length"]:
            best = {"type": r, "length": run}
    return best


# --- per-group measurement ---------------------------------------------------

def results_between(by_team: dict, group: dict, lo: date, hi: date) -> list:
    games = group_games(by_team, group, lo.strftime("%Y%m%d"), hi.strftime("%Y%m%d"))
    return [g["result"] for g in games]


def measure(by_team: dict, group: dict, ref: date) -> dict:
    """This season, the past 30 days, and every completed prior season."""
    season = results_between(by_team, group, date(ref.year, 1, 1), ref)
    month = results_between(by_team, group, ref - timedelta(days=MONTH_DAYS - 1), ref)

    history = []
    for year in range(ref.year - HISTORY_YEARS, ref.year):
        past = results_between(by_team, group, date(year, 1, 1), date(year, 12, 31))
        if len(past) >= MIN_SEASON_GAMES:
            idx = streak_index(past)
            if idx is not None:
                history.append({"year": year, "games": len(past), "index": idx})

    return {
        "name": group["name"], "city": group["city"], "label": display_label(group),
        "season": {"year": ref.year, "games": len(season),
                   "wins": season.count("W"), "losses": season.count("L"),
                   "index": streak_index(season), "longest": longest_run(season)},
        "month": {"games": len(month),
                  "wins": month.count("W"), "losses": month.count("L"),
                  "index": streak_index(month), "longest": longest_run(month),
                  "results": month},
        "history": history,
    }


def usual(row: dict) -> float:
    """The group's own middle season — what 'normal' means for them."""
    return statistics.median(h["index"] for h in row["history"])


def one_per_city(rows: list, key) -> list:
    """Keep the strongest row per city. The 24 New York permutations share
    most of their games, so without this every chart is a New York chart."""
    best = {}
    for row in sorted(rows, key=lambda r: r["name"]):      # deterministic order
        if row["city"] not in best or key(row) > key(best[row["city"]]):
            best[row["city"]] = row
    return sorted(best.values(), key=key, reverse=True)


def season_panel(rows: list, top_n: int = TOP_N) -> list:
    """Groups whose season departs most from their own history, both ways."""
    eligible = [r for r in rows
                if r["season"]["index"] is not None
                and r["season"]["games"] >= MIN_SEASON_GAMES
                and len(r["history"]) >= MIN_SEASONS]
    ranked = one_per_city(eligible, key=lambda r: abs(r["season"]["index"] - usual(r)))
    picked = ranked[:top_n * 2]
    return sorted(picked, key=lambda r: r["season"]["index"])


def month_panel(rows: list, per_side: int = 3) -> list:
    """The streakiest and the steadiest fandoms of the past 30 days."""
    eligible = [r for r in rows
                if r["month"]["index"] is not None
                and r["month"]["games"] >= MIN_MONTH_GAMES]
    ranked = one_per_city(eligible, key=lambda r: r["month"]["index"])
    if len(ranked) <= per_side * 2:      # early in a season the halves would overlap
        return ranked
    return ranked[:per_side] + ranked[-per_side:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--out-dir", default=OUT_DIR)
    parser.add_argument("--no-images", action="store_true",
                        help="Write streakiness.json only (skips the plotnine import).")
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    with open("city_groups.json") as f:
        groups = json.load(f)
    by_team = index_by_team(load_scores(args.data_dir))
    rows = [measure(by_team, group, ref) for group in groups]

    season = season_panel(rows)
    month = month_panel(rows)
    os.makedirs(args.out_dir, exist_ok=True)

    payload = {
        "reference_date": ref.strftime("%Y%m%d"),
        "window_days": MONTH_DAYS,
        "measured": [{k: v for k, v in r.items() if k != "month"}
                     | {"month": {k: v for k, v in r["month"].items() if k != "results"}}
                     for r in rows],
        "season_panel": [r["name"] for r in season],
        "month_panel": [r["name"] for r in month],
    }
    with open(os.path.join(args.out_dir, "streakiness.json"), "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
        f.write("\n")

    for r in season[-3:][::-1]:
        log.info("season: %-14s %+.2f (usual %+.2f, %d games)", r["name"],
                 r["season"]["index"], usual(r), r["season"]["games"])
    for r in month[:3]:
        log.info("month:  %-14s %+.2f (%d games, longest run %d%s)", r["name"],
                 r["month"]["index"], r["month"]["games"],
                 r["month"]["longest"]["length"], r["month"]["longest"]["type"])

    if args.no_images:
        log.info("Wrote %s (no images)", args.out_dir)
        return

    import render_streakiness                      # imports plotnine
    render_streakiness.render_season(season, ref, os.path.join(args.out_dir,
                                                               "season_vs_history.png"))
    render_streakiness.render_month(month, ref, os.path.join(args.out_dir,
                                                             "past_month.png"))
    log.info("Wrote %s (%d groups measured)", args.out_dir, len(rows))


if __name__ == "__main__":
    main()
