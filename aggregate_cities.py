#!/usr/bin/env python3
"""
City aggregations for the daily scores data.

Loads the hardcoded city groups from city_groups.json — one group per
combination of teams for metros with multiple teams in the same league
(e.g. Chicago 1 = Cubs, Chicago 2 = White Sox; regenerate the file with
generate_city_groups.py when cities.json changes) — and totals each
group's results over four periods anchored on a reference date (normally
yesterday, US/Eastern):

    day    the reference date itself
    week   Tuesday through Monday containing the reference date
           (Monday Night Football counts with the weekend)
    month  the calendar month of the reference date
    year   the calendar year of the reference date

Writes data/city_rankings.json for cities.html to render. Runs in the daily
GitHub Actions workflow right after the scraper.

Usage:
    python aggregate_cities.py                 # reference date = yesterday (ET)
    python aggregate_cities.py --date 20260716
"""

import argparse
import csv
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

LEAGUES = ["mlb", "nba", "nhl", "nfl"]
# a game's share of the year: 365 / regular-season games
LEAGUE_WEIGHT = {"mlb": 365 / 162, "nba": 365 / 82, "nhl": 365 / 82, "nfl": 365 / 17}
DATA_DIR = "data"
OUTPUT = os.path.join(DATA_DIR, "city_rankings.json")

def load_scores(data_dir: str) -> dict:
    """league -> list of game rows (completed games only, as scraped)."""
    scores = {}
    for league in LEAGUES:
        path = os.path.join(data_dir, f"{league}_scores.csv")
        if not os.path.exists(path):
            scores[league] = []
            continue
        with open(path, newline="") as f:
            scores[league] = list(csv.DictReader(f))
    return scores


def week_bounds(ref: date) -> tuple[date, date]:
    """Tuesday..Monday containing ref. Monday belongs to the preceding week."""
    start = ref - timedelta(days=(ref.weekday() - 1) % 7)  # Tuesday = weekday 1
    return start, start + timedelta(days=6)


def period_bounds(ref: date) -> dict:
    week_start, week_end = week_bounds(ref)
    return {
        "day": (ref, ref),
        "week": (week_start, week_end),
        "month": (ref.replace(day=1),
                  (ref.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)),
        "year": (ref.replace(month=1, day=1), ref.replace(month=12, day=31)),
    }


def totals(games_by_team: dict, group: dict, start: date, end: date) -> dict:
    lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    w = l = t = 0
    weighted = 0.0
    for team in group["teams"]:
        for row in games_by_team.get((team["league"], team["abbr"]), []):
            if not (lo <= row["date"] <= hi):
                continue
            if row["winner"] == team["abbr"]:
                w += 1
                weighted += LEAGUE_WEIGHT[team["league"]]
            elif row["winner"] == "":
                t += 1
            else:
                l += 1
                weighted -= LEAGUE_WEIGHT[team["league"]]
    return {"w": w, "l": l, "t": t, "weighted": round(weighted, 2)}


def daily_series(games_by_team: dict, group: dict, start: date, end: date) -> dict:
    """date -> [w, l, t, weighted] for days in range where the group played.
    Sparse; feeds the cumulative 'race' charts on cities.html."""
    lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    days = {}
    for team in group["teams"]:
        for row in games_by_team.get((team["league"], team["abbr"]), []):
            if not (lo <= row["date"] <= hi):
                continue
            d = days.setdefault(row["date"], [0, 0, 0, 0.0])
            if row["winner"] == team["abbr"]:
                d[0] += 1
                d[3] += LEAGUE_WEIGHT[team["league"]]
            elif row["winner"] == "":
                d[2] += 1
            else:
                d[1] += 1
                d[3] -= LEAGUE_WEIGHT[team["league"]]
    return {k: [v[0], v[1], v[2], round(v[3], 3)] for k, v in sorted(days.items())}


def index_by_team(scores: dict) -> dict:
    """(league, abbr) -> rows involving that team."""
    by_team = {}
    for league, rows in scores.items():
        for row in rows:
            for abbr in (row["away_team"], row["home_team"]):
                by_team.setdefault((league, abbr), []).append(row)
    return by_team


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--data-dir", default=DATA_DIR)
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    with open("city_groups.json") as f:
        groups = json.load(f)
    by_team = index_by_team(load_scores(args.data_dir))
    bounds = period_bounds(ref)

    for group in groups:
        for period, (start, end) in bounds.items():
            group[period] = totals(by_team, group, start, end)
        # per-day results across the year (covers week and month too)
        group["daily"] = daily_series(by_team, group, *bounds["year"])

    out = {
        "reference_date": ref.strftime("%Y%m%d"),
        "periods": {
            period: {"start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")}
            for period, (start, end) in bounds.items()
        },
        "groups": groups,
    }
    os.makedirs(os.path.dirname(OUTPUT) or ".", exist_ok=True)
    with open(os.path.join(args.data_dir, "city_rankings.json"), "w") as f:
        json.dump(out, f)

    for period in bounds:
        played = [g for g in groups if g[period]["w"] + g[period]["l"] + g[period]["t"] > 0]
        if played:
            best = max(played, key=lambda g: g[period]["weighted"])
            log.info("%s: best city %s (%.2f weighted, %d-%d)", period,
                     best["name"], best[period]["weighted"], best[period]["w"], best[period]["l"])
        else:
            log.info("%s: no games", period)
    log.info("Wrote %s (%d groups)", os.path.join(args.data_dir, "city_rankings.json"), len(groups))


if __name__ == "__main__":
    main()
