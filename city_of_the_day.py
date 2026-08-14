#!/usr/bin/env python3
"""
City of the day: pick one fandom at random and draw its season.

Every morning this picks a single city group and draws its season three ways.
The pick is random, not ranked — a detector-driven feed keeps returning to
whoever is having an extreme week, while a fandom's ordinary season is
perfectly interesting once you look at it.

    - a city is drawn first, then a group inside it, so the 24 New York
      permutations don't win a quarter of the days
    - the draw is seeded by the date, so re-running a day reproduces it
    - cities drawn in the last COOLDOWN_DAYS are skipped, tracked in
      content/daily/history.json (a small file, not a folder scan)
    - a city only qualifies if its teams have played recently and it has
      earlier seasons to compare against, so the charts are never empty

Output lands in content/daily/, always at the same paths — the working tree
carries one day's images and git history carries the archive:

    season.png     cumulative weighted index this year against the same
                   group's earlier seasons, day of year for day of year
    month.png      this calendar month against the same month in every
                   earlier year, cumulative within the month (skipped when
                   too few earlier ones are on record)
    form.png       the last 30 days as each team's running games over .500,
                   one panel per team, with its record and longest run
    summary.md     the numbers behind all three, ready to paste
    history.json   the rolling record of who has been picked

Usage:
    python city_of_the_day.py                 # reference date = yesterday (ET)
    python city_of_the_day.py --date 20260802
    python city_of_the_day.py --city Detroit  # force a pick, ignoring the draw
"""

import argparse
import calendar
import json
import logging
import os
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aggregate_cities import index_by_team, load_scores
from fandom_analysis import (MONTH_NAMES, display_label, group_games,
                             month_series, mtd_window, pretty_date,
                             season_series, standing, tally)
from streakiness import longest_run, streak_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
OUT_DIR = os.path.join("content", "daily")
HISTORY_FILE = "history.json"
HISTORY_YEARS = 10          # how far back the two comparison charts reach.
                            # Scores go back to 2010, so this is a choice about
                            # how much past is useful, not what exists on disk
WINDOW_DAYS = 30            # the "recent games" window
MIN_RECENT_GAMES = 6        # fewer than this and the tile chart has nothing to say
MIN_SEASONS = 2             # earlier seasons needed for the comparison chart
MIN_SAME_MONTHS = 2         # past Marches needed before the month chart is worth drawing
MIN_MONTH_GAMES = 3         # a past month this thin isn't a comparison, it's noise
COOLDOWN_DAYS = 21          # don't draw the same city again this soon
HISTORY_KEEP = 120          # entries retained in history.json


def recent_by_team(by_team: dict, group: dict, ref: date) -> list:
    """Per team: the last WINDOW_DAYS of games, in order."""
    lo = ref - timedelta(days=WINDOW_DAYS - 1)
    games = group_games(by_team, group, lo.strftime("%Y%m%d"), ref.strftime("%Y%m%d"))
    rows = []
    for team in group["teams"]:
        played = [g for g in games
                  if g["abbr"] == team["abbr"] and g["league"] == team["league"]]
        if not played:
            continue                     # off-season: no row rather than an empty one
        results = [g["result"] for g in played]
        # the game list goes under "log": tally() owns the "games" key (a count)
        rows.append({**team, "log": played, "results": results,
                     **tally(played), "longest": longest_run(results),
                     "index": streak_index(results)})
    return rows


def same_months(by_team: dict, group: dict, ref: date) -> dict:
    """This calendar month against the same month in every earlier year.

    Past years run the full month, so their lines show how the month actually
    finished, but the ranking is made at the same day of the month: a August
    half played is not a worse August than four finished ones. A past month
    too thin to mean anything is left out rather than drawn as a near-flat
    line pretending to be a comparison.
    """
    length = calendar.monthrange(ref.year, ref.month)[1]
    lo, hi = mtd_window(ref.year, ref.month, ref.day)
    totals = tally(group_games(by_team, group, lo, hi))

    past = []
    for year in range(ref.year - HISTORY_YEARS, ref.year):
        p_lo, p_hi = mtd_window(year, ref.month, ref.day)
        to_date = tally(group_games(by_team, group, p_lo, p_hi))
        series = month_series(by_team, group, year, ref.month)
        if to_date["games"] < MIN_MONTH_GAMES or not series:
            continue
        past.append({"year": year, "series": series, "to_date": to_date,
                     "final": series[-1]["cum"]})

    # 1 is the best of the same-months, counting only the stretch played so far
    place = 1 + sum(1 for p in past if p["to_date"]["weighted"] > totals["weighted"])
    return {"month": ref.month, "name": MONTH_NAMES[ref.month], "cutoff": ref.day,
            "length": length, "in_progress": ref.day < length,
            "series": month_series(by_team, group, ref.year, ref.month, ref),
            **totals, "past": past, "place": place, "field": len(past) + 1}


def month_drawable(prof: dict) -> bool:
    """Enough of both sides to be a comparison rather than a lone line."""
    m = prof["month"]
    return len(m["past"]) >= MIN_SAME_MONTHS and m["games"] >= MIN_MONTH_GAMES


def profile(by_team: dict, group: dict, ref: date) -> dict:
    """Everything both charts and the summary are built from."""
    season_games = group_games(by_team, group, f"{ref.year}0101", ref.strftime("%Y%m%d"))
    season_results = [g["result"] for g in season_games]
    recent = recent_by_team(by_team, group, ref)
    recent_games = sorted((g for team in recent for g in team["log"]),
                          key=lambda g: (g["date"], g["game_id"]))
    recent_results = [g["result"] for g in recent_games]

    past = []
    for year in range(ref.year - HISTORY_YEARS, ref.year):
        series = season_series(by_team, group, year)
        if series:
            past.append({"year": year, "series": series,
                         "final": series[-1]["cum"]})

    return {
        "name": group["name"], "city": group["city"], "label": display_label(group),
        "teams": group["teams"],
        "season": {"year": ref.year, "series": season_series(by_team, group, ref.year, ref),
                   **tally(season_games), "index": streak_index(season_results),
                   "longest": longest_run(season_results)},
        "past_seasons": past,
        "month": same_months(by_team, group, ref),
        "recent": {"days": WINDOW_DAYS, **tally(recent_games),
                   "index": streak_index(recent_results),
                   "longest": longest_run(recent_results),
                   "first_date": recent_games[0]["date"] if recent_games else None,
                   "teams": recent},
    }


def qualifies(prof: dict) -> bool:
    return (prof["recent"]["games"] >= MIN_RECENT_GAMES
            and len(prof["past_seasons"]) >= MIN_SEASONS
            and len(prof["season"]["series"]) >= 2)


# --- the draw ----------------------------------------------------------------

def load_history(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Ignoring unreadable %s (%s)", path, e)
        return []


def cooling_off(history: list, ref: date) -> set:
    """Cities drawn recently enough that repeating them would feel like a rerun."""
    recent = set()
    for entry in history:
        try:
            when = date.fromisoformat(entry["date"])
        except (KeyError, ValueError):
            continue
        # strictly earlier days: a rerun of this date must not shut out the
        # city its own first run recorded, or the draw stops reproducing
        if 0 < (ref - when).days < COOLDOWN_DAYS:
            recent.add(entry["city"])
    return recent


def draw(groups: list, by_team: dict, ref: date, history: list,
         seed: str | None = None) -> dict | None:
    """Draw a city, then a group inside it. Cities are drawn uniformly so that
    a metro with 24 permutations gets one city's worth of days, not 24. Cities
    on cooldown, or whose charts would be empty, are passed over; if every city
    is on cooldown the cooldown is dropped rather than the day."""
    by_city = {}
    for group in groups:
        by_city.setdefault(group["city"], []).append(group)

    rng = random.Random(seed if seed is not None else ref.isoformat())
    order = sorted(by_city)                      # deterministic before shuffling
    rng.shuffle(order)
    skip = cooling_off(history, ref)
    for cities in (order, order):                # second pass ignores the cooldown
        for city in cities:
            if city in skip:
                continue
            for group in sorted(by_city[city], key=lambda g: g["name"]):
                prof = profile(by_team, group, ref)
                if qualifies(prof):
                    return prof
        skip = set()
    return None


# --- copy --------------------------------------------------------------------

def reading(index: float | None) -> str:
    if index is None:
        return "not enough of both results to score"
    if index >= 2:
        return f"clumpier than chance ({index:+.1f})"
    if index <= -2:
        return f"more alternating than chance ({index:+.1f})"
    return f"about as clumped as coin flips ({index:+.1f})"


def run_phrase(run: dict) -> str:
    if not run["length"]:
        return "no games"
    word = "wins" if run["type"] == "W" else "losses"
    return f"{run['length']} straight {word}"


def write_summary(path: str, prof: dict, ref: date) -> None:
    s, r, m = prof["season"], prof["recent"], prof["month"]
    finals = ", ".join(f"{p['year']} {p['final']:+.1f}" for p in prof["past_seasons"])
    out = [
        f"# City of the day — {prof['city']}",
        f"*{prof['label']}* · {ref.isoformat()}",
        "",
        f"- **{ref.year} so far:** {s['w']}-{s['l']}, {s['weighted']:+.1f} weighted "
        f"over {s['games']} games; longest run {run_phrase(s['longest'])}",
        f"- **Order of results:** {reading(s['index'])}",
        f"- **Earlier seasons (full-year weighted):** {finals}",
        f"- **Last {r['days']} days:** {r['w']}-{r['l']}, {r['weighted']:+.1f} weighted; "
        f"longest run {run_phrase(r['longest'])}",
    ]
    if r["first_date"]:
        out.append(f"- **Window opens:** {pretty_date(r['first_date'])}")
    if month_drawable(prof):
        same = ", ".join(f"{p['year']} {p['to_date']['weighted']:+.1f}"
                         for p in m["past"])
        # "through day 15" rather than "the 15th" (no ordinal suffixes to get
        # wrong on the 1st) and no plural of the month name to get wrong either
        through = f" through day {m['cutoff']}" if m["in_progress"] else ""
        out.append(f"- **{m['name']} {ref.year}{through}:** {m['w']}-{m['l']}, "
                   f"{m['weighted']:+.1f} weighted — "
                   f"{standing(m['place'], m['field'])} on record")
        out.append(f"- **Same {m['name']} in earlier years"
                   f"{' (same stretch)' if m['in_progress'] else ''}:** {same}")
    out.append("")
    out.append("| Team | Last 30 days | Weighted | Longest run |")
    out.append("|---|---|---|---|")
    for team in r["teams"]:
        out.append(f"| {team['nickname']} | {team['w']}-{team['l']} | "
                   f"{team['weighted']:+.1f} | {run_phrase(team['longest'])} |")
    # month.png is skipped in a month with too little history behind it, and
    # the publisher drops an image the run did not draw
    out += ["", "Images: `season.png` · `month.png` · `form.png`", ""]
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--city", default=None,
                        help="Draw this city instead of a random one.")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--out-dir", default=OUT_DIR)
    parser.add_argument("--no-images", action="store_true",
                        help="Write summary.md only (skips the plotnine import).")
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    with open("city_groups.json") as f:
        groups = json.load(f)
    by_team = index_by_team(load_scores(args.data_dir))
    os.makedirs(args.out_dir, exist_ok=True)
    history_path = os.path.join(args.out_dir, HISTORY_FILE)
    history = load_history(history_path)

    if args.city:
        picked = [g for g in groups if g["city"].lower() == args.city.lower()]
        if not picked:
            raise SystemExit(f"No city group for {args.city!r}")
        prof = profile(by_team, picked[0], ref)
        if not qualifies(prof):
            log.warning("%s has too little recent data for a full pair of charts.",
                        args.city)
    else:
        prof = draw(groups, by_team, ref, history)
        if prof is None:
            log.warning("No city has enough recent games to draw — leaving %s alone.",
                        args.out_dir)
            return

    log.info("%s — %s (%d-%d this year, %d games in the last %d days, index %s)",
             prof["city"], prof["label"], prof["season"]["w"], prof["season"]["l"],
             prof["recent"]["games"], WINDOW_DAYS, prof["season"]["index"])

    write_summary(os.path.join(args.out_dir, "summary.md"), prof, ref)
    history = [h for h in history if h.get("date") != ref.isoformat()]
    history.append({"date": ref.isoformat(), "city": prof["city"],
                    "group": prof["name"]})
    history = sorted(history, key=lambda h: h["date"])[-HISTORY_KEEP:]
    with open(history_path, "w") as f:
        json.dump(history, f, indent=1)
        f.write("\n")

    if args.no_images:
        log.info("Wrote %s (no images)", args.out_dir)
        return

    import render_daily
    render_daily.render_season(prof, ref, os.path.join(args.out_dir, "season.png"))
    if month_drawable(prof):
        render_daily.render_month(prof, ref, os.path.join(args.out_dir, "month.png"))
    else:
        log.info("Only %d comparable %ss on record — skipping the month chart",
                 len(prof["month"]["past"]), prof["month"]["name"])
    render_daily.render_form(prof, ref, os.path.join(args.out_dir, "form.png"))
    log.info("Wrote %s", args.out_dir)


if __name__ == "__main__":
    main()
