#!/usr/bin/env python3
"""
Fandom spotlight: find city groups whose season is running outside their norm.

Five independent detectors run over every group in city_groups.json, so a
run's output isn't three variations on the same sentence:

    month       the month-to-date weighted index sits in the tails of the
                group's own history, along two comparison lanes — every month
                on record, and the same calendar month in previous years
                (July vs past Julys, so a baseball-only month is never judged
                against four-league months where the index swings harder)
    year        the year to date, measured at the same day of year, sits in
                the tails of the group's own past years — with the group's
                place among all 88 on this year's index saying whether that
                is a good place to be
    streak      the group's teams are on a long combined win/loss run,
                weighted by how rare a run that long is for them
    turnaround  the month flipped sign in the last 7 days — bad team, good
                week, or the reverse
    climb       the group moved several places in the year-to-date standings
                over the past week

Every detector emits a 0-1 score. Selection then enforces variety, because
month- and year-to-date totals move slowly between runs:

    - at most one group per city (the 24 New York permutations co-move)
    - a kind already picked this run is penalized, so the three cards differ
    - cities featured in recent runs are damped, ramping back to full over
      three weeks (see novelty_factor), read from the run log
    - a small date-seeded jitter shuffles near-ties, so two runs with
      identical data don't produce identical picks

Results overwrite content/weekly/: findings.json, a human-readable summary.md,
and history.json (the run log the cooldown reads). The GitHub Actions workflow
runs this weekly, on Wednesdays, after aggregate_cities.

Usage:
    python fandom_analysis.py                 # reference date = yesterday (ET)
    python fandom_analysis.py --date 20260716
    python fandom_analysis.py --kinds month,year
"""

import argparse
import calendar
import json
import logging
import os
import random
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aggregate_cities import LEAGUE_WEIGHT, index_by_team, load_scores

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
CONTENT_DIR = os.path.join("content", "weekly")
HISTORY_FILE = "history.json"
HISTORY_KEEP = 60           # runs retained in the cooldown log
HISTORY_YEARS = 10          # scores go back to 2010-01; this is how far back
                            # the detectors' comparison sets and their
                            # percentile claims reach, rolling off the
                            # reference date rather than anchored to a year
MIN_GAMES = 3               # months where the group played fewer games don't count
MIN_DAY = 4                 # too early in a month to call anything notable
MIN_HISTORY = 12            # the all-months lane needs at least this many months
MIN_CAL_HISTORY = 3         # the same-calendar-month lane needs this many prior years
MIN_STREAK = 5              # shorter runs aren't worth a graphic
MIN_CLIMB = 4               # places gained/lost in the year standings to qualify
MIN_YTD_GAMES = 20          # a year-to-date claim needs a season under it
MIN_YEARS = 2               # prior years needed for the year-vs-itself lane
TOP_N = 3
KINDS = ("month", "year", "streak", "turnaround", "climb")

# score multiplier by days since the city was last featured, ramping from a
# hard damp the next day to fully eligible three weeks later. The span covers
# a few runs at either cadence this is used at — weekly, or daily by hand.
NOVELTY_SPAN = 21
NOVELTY_FLOOR = 0.30
NOVELTY_LOOKBACK = 60
KIND_REPEAT_PENALTY = 0.55  # applied to a kind already chosen today
MAX_PER_KIND = 2            # never three cards with the same sentence shape


def month_range(start: tuple, end_exclusive: tuple):
    """Yield (year, month) from start up to but not including end_exclusive."""
    y, m = start
    while (y, m) < end_exclusive:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def group_games(by_team: dict, group: dict, lo: str, hi: str) -> list:
    """The group's games in [lo, hi] (YYYYMMDD strings), sorted by date then id.

    Each item: {date, league, abbr, nickname, result ('W'/'L'/'T'), weighted}.
    """
    games = []
    for team in group["teams"]:
        for row in by_team.get((team["league"], team["abbr"]), []):
            if not (lo <= row["date"] <= hi):
                continue
            if row["winner"] == team["abbr"]:
                result, weighted = "W", LEAGUE_WEIGHT[team["league"]]
            elif row["winner"] == "":
                result, weighted = "T", 0.0
            else:
                result, weighted = "L", -LEAGUE_WEIGHT[team["league"]]
            opp = row["home_team"] if row["away_team"] == team["abbr"] else row["away_team"]
            games.append({"date": row["date"], "league": team["league"],
                          "abbr": team["abbr"], "nickname": team["nickname"],
                          "opponent": opp, "result": result, "weighted": weighted,
                          "game_id": row["game_id"]})
    games.sort(key=lambda g: (g["date"], g["game_id"]))
    return games


def tally(games: list) -> dict:
    w = sum(1 for g in games if g["result"] == "W")
    l = sum(1 for g in games if g["result"] == "L")
    t = sum(1 for g in games if g["result"] == "T")
    return {"w": w, "l": l, "t": t, "games": len(games),
            "weighted": round(sum(g["weighted"] for g in games), 2)}


def mtd_window(year: int, month: int, cutoff_day: int) -> tuple:
    """(lo, hi) date strings for the month-to-date window, cutoff capped at
    the month's actual length (a 31st cutoff means the 28th in February)."""
    day = min(cutoff_day, calendar.monthrange(year, month)[1])
    return f"{year:04d}{month:02d}01", f"{year:04d}{month:02d}{day:02d}"


def shrunk_percentile(hist_values: list, current: float) -> float:
    """Midrank percentile of current among hist_values, shrunk toward 0.5 by
    half a pseudo-count so small comparison sets make weaker claims ("best of
    5 Julys" scores below "best of 55 months")."""
    below = sum(1 for v in hist_values if v < current)
    ties = sum(1 for v in hist_values if v == current)
    return (below + 0.5 * ties + 0.5) / (len(hist_values) + 1)


def recency_share(month_weighted: float, last7_weighted: float,
                  whole_month: bool = False) -> float:
    """Share of the month's weighted total earned in the last 7 days, in [0, 1].

    Zero when the week points the other way from the month (no 'this week'
    hook). Neutral (0.5) in the first week, when the last-7 window *is* the
    whole month and the ratio would be a meaningless constant 1.0.
    """
    if whole_month:
        return 0.5
    if month_weighted == 0:
        return 0.0
    return max(0.0, min(1.0, last7_weighted / month_weighted))


def magnitude(values: list, current: float) -> float:
    """How far current sits from the historical median, relative to the most
    extreme historical deviation. 1.0 = furthest out ever. Breaks the ties
    that rank alone leaves when the comparison set is small."""
    if not values:
        return 0.0
    med = statistics.median(values)
    spread = max((abs(v - med) for v in values), default=0.0)
    if spread == 0:
        return 1.0 if current != med else 0.0
    return min(1.0, abs(current - med) / spread)


def trailing_streak(games: list) -> dict | None:
    """Current W or L streak at the end of a game list (ties break it)."""
    if not games:
        return None
    kind = games[-1]["result"]
    if kind == "T":
        return None
    n = 0
    for g in reversed(games):
        if g["result"] != kind:
            break
        n += 1
    return {"type": kind, "length": n}


def longest_prior_run(games: list, kind: str, tail: int) -> int:
    """Longest run of `kind` in games, ignoring the trailing `tail` games."""
    best = run = 0
    for g in games[:len(games) - tail]:
        run = run + 1 if g["result"] == kind else 0
        best = max(best, run)
    return best


def last_run_reaching(games: list, kind: str, length: int, tail: int) -> str | None:
    """Date of the most recent game at which a run of `kind` reached `length`,
    ignoring the trailing `tail` games. None if it never happened."""
    run, last = 0, None
    for g in games[:len(games) - tail]:
        if g["result"] == kind:
            run += 1
            if run >= length:
                last = g["date"]
        else:
            run = 0
    return last


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def run_word(kind: str) -> str:
    """What a run of this result is called. Every chart and every summary in
    the repo needs it, so none of them spell the ternary out."""
    return "wins" if kind == "W" else "losses"


def display_label(group: dict) -> str:
    return f"{group['city']}: " + "/".join(t["nickname"] for t in group["teams"])


def slugify(name: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in name.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def pretty_month(iso: str) -> str:
    y, m = iso.split("-")
    return f"{MONTH_NAMES[int(m)]} {y}"


ORDINALS = {1: "best", 2: "second-best", 3: "third-best", 4: "fourth-best",
            5: "fifth-best", 6: "sixth-best"}


def standing(place: int, field: int) -> str:
    """Where one month sits among the same months on record — the phrase the
    daily summary and the month chart's subtitle both need, so it lives here
    rather than in either of them."""
    if field < 2:
        return "the only one on record"
    if place >= field:
        return f"the worst of the {field}"
    # ordinal() rather than a bare "th": past 20 the suffix stops being "th",
    # and this reads "21th-best" the day the history window gets any deeper
    return f"the {ORDINALS.get(place, ordinal(place) + '-best')} of the {field}"


def pretty_date(yyyymmdd: str) -> str:
    d = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    return f"{MONTH_NAMES[d.month]} {d.day}, {d.year}"


# --- shared per-group context ------------------------------------------------

def group_context(by_team: dict, group: dict, ref: date) -> dict:
    """Everything the detectors share: this month's games, the last-7 split,
    per-team splits, and the month-by-month history at the same cutoff."""
    cutoff = ref.day
    lo, hi = mtd_window(ref.year, ref.month, cutoff)
    month_games = group_games(by_team, group, lo, hi)
    last7_lo = max(ref - timedelta(days=6), ref.replace(day=1))
    whole_month = last7_lo == ref.replace(day=1)
    last7_games = [g for g in month_games if g["date"] >= last7_lo.strftime("%Y%m%d")]
    early_games = [g for g in month_games if g["date"] < last7_lo.strftime("%Y%m%d")]

    hist = []
    for y, m in month_range((ref.year - HISTORY_YEARS, ref.month),
                            (ref.year, ref.month)):
        h_lo, h_hi = mtd_window(y, m, cutoff)
        totals = tally(group_games(by_team, group, h_lo, h_hi))
        if totals["games"] >= MIN_GAMES:
            hist.append({"month": f"{y:04d}-{m:02d}", "calendar_month": m, **totals})

    per_team = []
    for team in group["teams"]:
        def team_games(games):
            return [g for g in games
                    if g["abbr"] == team["abbr"] and g["league"] == team["league"]]
        t_month = team_games(month_games)
        per_team.append({**team, "month": tally(t_month),
                         "last7": tally(team_games(last7_games)),
                         "streak": trailing_streak(t_month)})

    return {
        "group": group, "ref": ref,
        "month_games": month_games, "last7_games": last7_games,
        "early_games": early_games, "whole_month": whole_month,
        "month_totals": tally(month_games), "last7": tally(last7_games),
        "history": hist, "per_team": per_team,
    }


def base_finding(ctx: dict, kind: str, score: float, **extra) -> dict:
    group = ctx["group"]
    return {
        "kind": kind, "score": round(score, 4),
        "name": group["name"], "city": group["city"],
        "label": display_label(group), "teams": group["teams"],
        "month_totals": ctx["month_totals"], "last7": ctx["last7"],
        "per_team": ctx["per_team"], "history": ctx["history"],
        **extra,
    }


# --- detector: month extremity ----------------------------------------------

def lane_stats(hist: list, current: float, recency: float) -> dict:
    """Score the current month against one comparison set (a "lane").

    "since" is the most recent comparison month at least as extreme as this
    one; None means this is the most extreme month in the set.
    """
    values = [h["weighted"] for h in hist]
    pctl = shrunk_percentile(values, current)
    direction = "hot" if pctl >= 0.5 else "cold"
    extremity = 2 * abs(pctl - 0.5)
    # extremity gates (a mid-pack month scores 0 no matter how big); magnitude
    # and recency modulate, so near-ties in a 4-month lane separate properly
    score = extremity * (0.5 + 0.3 * magnitude(values, current) + 0.2 * recency)
    if direction == "hot":
        rank = 1 + sum(1 for v in values if v > current)
        since = max((h["month"] for h in hist if h["weighted"] >= current), default=None)
    else:
        rank = 1 + sum(1 for v in values if v < current)
        since = max((h["month"] for h in hist if h["weighted"] <= current), default=None)
    return {"direction": direction, "score": round(score, 4),
            "percentile": round(pctl, 4), "rank": rank,
            "n_months": len(values), "since": since}


def detect_month(ctx: dict, lanes: tuple = ("all", "calendar")) -> dict | None:
    """The month-to-date index sits in the tails of the group's own history."""
    ref = ctx["ref"]
    if ctx["month_totals"]["games"] < MIN_GAMES:
        return None
    cur = ctx["month_totals"]["weighted"]
    hist = ctx["history"]

    def lane_recency(direction):
        sign = 1 if direction == "hot" else -1
        return recency_share(sign * cur, sign * ctx["last7"]["weighted"],
                             ctx["whole_month"])

    def build(subset):
        # direction depends only on the percentile, so resolve it first
        probe = lane_stats(subset, cur, 0.0)
        return lane_stats(subset, cur, lane_recency(probe["direction"]))

    def sign_ok(lane):
        # a "best July on record" that's still a losing month (or a "worst"
        # that's a winning one) is a hollow claim — the lane doesn't count
        return cur > 0 if lane["direction"] == "hot" else cur < 0

    comparisons = {}
    if "all" in lanes and len(hist) >= MIN_HISTORY:
        lane = build(hist)
        if sign_ok(lane):
            comparisons["all"] = lane
    cal_hist = [h for h in hist if h["calendar_month"] == ref.month]
    if "calendar" in lanes and len(cal_hist) >= MIN_CAL_HISTORY:
        lane = build(cal_hist)
        if sign_ok(lane):
            comparisons["calendar"] = lane
    if not comparisons:
        return None

    # "best July on record" on a month that is thoroughly average against the
    # group's whole history is a technicality, not a story — dampen the
    # calendar claim by how much the all-months lane backs it up
    if "calendar" in comparisons and "all" in comparisons:
        support = 2 * abs(comparisons["all"]["percentile"] - 0.5)
        comparisons["calendar"]["score"] = round(
            comparisons["calendar"]["score"] * (0.5 + 0.5 * support), 4)

    basis = max(comparisons, key=lambda k: comparisons[k]["score"])
    return base_finding(ctx, "month", comparisons[basis]["score"],
                        basis=basis, comparisons=comparisons,
                        **{k: v for k, v in comparisons[basis].items() if k != "score"})


# --- detector: combined streak ----------------------------------------------

def detect_streak(by_team: dict, ctx: dict) -> dict | None:
    """The group's teams are on a long combined run, scored by how unusual a
    run that long is for them."""
    group, ref = ctx["group"], ctx["ref"]
    all_games = group_games(by_team, group, "00000000", ref.strftime("%Y%m%d"))
    streak = trailing_streak(all_games)
    if not streak or streak["length"] < MIN_STREAK:
        return None
    # the run must be live: a team that last played weeks ago isn't a story
    if (ref - datetime.strptime(all_games[-1]["date"], "%Y%m%d").date()).days > 3:
        return None

    length, kind = streak["length"], streak["type"]
    prior_best = longest_prior_run(all_games, kind, length)
    since = last_run_reaching(all_games, kind, length, length)
    rarity = 1.0 if length > prior_best else max(0.0, 1 - (prior_best - length) / 4)
    score = min(1.0, 0.45 * min(1.0, (length - MIN_STREAK + 1) / 6) + 0.55 * rarity)

    games = all_games[-length:]
    context_games = all_games[max(0, len(all_games) - length - 12):]
    return base_finding(
        ctx, "streak", score,
        direction="hot" if kind == "W" else "cold",
        streak={"type": kind, "length": length, "prior_best": prior_best,
                "since": since, "record": length > prior_best,
                "start": games[0]["date"], "weighted": round(
                    sum(g["weighted"] for g in games), 2)},
        timeline=[{**g, "phase": "streak" if g in games else "before"}
                  for g in context_games],
    )


# --- detector: in-month turnaround ------------------------------------------

def detect_turnaround(ctx: dict) -> dict | None:
    """The month flipped sign in the last 7 days."""
    early, late = ctx["early_games"], ctx["last7_games"]
    if len(early) < 4 or len(late) < 3:
        return None
    e_tot, l_tot = tally(early), tally(late)
    pace_e = e_tot["weighted"] / len(early)
    pace_l = l_tot["weighted"] / len(late)
    if pace_e * pace_l >= 0:          # no sign flip: not a turnaround
        return None
    swing = abs(pace_l - pace_e)
    # 4.5 ≈ the full swing of an MLB game (all-loss pace to all-win pace)
    score = min(1.0, swing / 4.5)
    return base_finding(
        ctx, "turnaround", score,
        direction="hot" if pace_l > 0 else "cold",
        turnaround={"early": e_tot, "late": l_tot,
                    "pace_early": round(pace_e, 2), "pace_late": round(pace_l, 2),
                    "swing": round(swing, 2)},
        timeline=[{**g, "phase": "early"} for g in early]
                 + [{**g, "phase": "late"} for g in late],
    )


# --- cumulative series (shared with the daily draw) --------------------------

def season_series(by_team: dict, group: dict, year: int, through: date | None = None) -> list:
    """[{day of year, cumulative weighted index}] for each day the group played."""
    end = through or date(year, 12, 31)
    games = group_games(by_team, group, f"{year}0101", end.strftime("%Y%m%d"))
    series, cum = [], 0.0
    for game in games:
        cum += game["weighted"]
        day = datetime.strptime(game["date"], "%Y%m%d").date().timetuple().tm_yday
        series.append({"day": day, "cum": round(cum, 3)})
    # one point per day played: the last game of a day carries that day's total
    return list({point["day"]: point for point in series}.values())


def month_series(by_team: dict, group: dict, year: int, month: int,
                 through: date | None = None) -> list:
    """[{day of month, cumulative weighted index}] for each day the group
    played that month. `through` cuts a month still in progress short; without
    it the series runs to the end of the month, which is what a past year's
    line wants."""
    end = through or date(year, month, calendar.monthrange(year, month)[1])
    games = group_games(by_team, group, f"{year:04d}{month:02d}01",
                        end.strftime("%Y%m%d"))
    series, cum = [], 0.0
    for game in games:
        cum += game["weighted"]
        day = datetime.strptime(game["date"], "%Y%m%d").date().day
        series.append({"day": day, "cum": round(cum, 3)})
    # one point per day played: the last game of a day carries that day's total
    return list({point["day"]: point for point in series}.values())


# --- detector: year to date, against itself and the field --------------------

def ytd_window(year: int, ref: date) -> tuple:
    """(lo, hi) for January 1 through the same day of year as ref, capped at
    the year's length so a leap-day reference doesn't run past December 31."""
    day = min(ref.timetuple().tm_yday, date(year, 12, 31).timetuple().tm_yday)
    return f"{year:04d}0101", (date(year, 1, 1) + timedelta(days=day - 1)).strftime("%Y%m%d")


def ytd_totals(by_team: dict, groups: list, ref: date) -> dict:
    """group name -> weighted index for this year through ref."""
    lo, hi = ytd_window(ref.year, ref)
    return {g["name"]: tally(group_games(by_team, g, lo, hi))["weighted"]
            for g in groups}


def detect_year(ctx: dict, by_team: dict, field: dict) -> dict | None:
    """The year to date sits in the tails of the group's own past years — and
    the field says whether that is a good place to be."""
    group, ref = ctx["group"], ctx["ref"]
    lo, hi = ytd_window(ref.year, ref)
    current = tally(group_games(by_team, group, lo, hi))
    if current["games"] < MIN_YTD_GAMES:
        return None

    history = []
    for year in range(ref.year - HISTORY_YEARS, ref.year):
        y_lo, y_hi = ytd_window(year, ref)
        totals = tally(group_games(by_team, group, y_lo, y_hi))
        if totals["games"] >= MIN_YTD_GAMES:
            history.append({"year": year, **totals})
    if len(history) < MIN_YEARS:
        return None

    values = [h["weighted"] for h in history]
    cur = current["weighted"]
    pctl = shrunk_percentile(values, cur)
    direction = "hot" if pctl >= 0.5 else "cold"
    # the same hollow-claim guard the month detector uses: a "best year on
    # record" that is still a losing year isn't a story
    if (cur > 0) != (direction == "hot"):
        return None

    others = sorted(v for name, v in field.items() if name != group["name"])
    place = 1 + sum(1 for v in others if v > cur)
    field_pctl = sum(1 for v in others if v < cur) / len(others) if others else 0.5
    # being far from your own norm is the story; the league standing says
    # whether anyone else should care
    extremity = 2 * abs(pctl - 0.5)
    standing = 2 * abs(field_pctl - 0.5)
    score = extremity * (0.5 + 0.3 * magnitude(values, cur) + 0.2 * standing)

    if direction == "hot":
        rank = 1 + sum(1 for v in values if v > cur)
        since = max((h["year"] for h in history if h["weighted"] >= cur), default=None)
    else:
        rank = 1 + sum(1 for v in values if v < cur)
        since = max((h["year"] for h in history if h["weighted"] <= cur), default=None)

    return base_finding(
        ctx, "year", score, direction=direction,
        year={"totals": current, "rank": rank, "n_years": len(values) + 1,
              "percentile": round(pctl, 4), "since": since,
              "place": place, "field": len(others) + 1,
              "field_percentile": round(field_pctl, 4)},
        year_history=history,
        year_series=[{"year": year,
                      "series": season_series(by_team, group, year,
                                              ref if year == ref.year else None)}
                     for year in [h["year"] for h in history] + [ref.year]],
        field_values=sorted(field.values(), reverse=True),
    )


# --- detector: year-standings climb -----------------------------------------

def ytd_rank_series(by_team: dict, groups: list, ref: date, days: int = 30) -> dict:
    """group name -> {date: [rank, cumulative weighted]} over the last `days`,
    ranked among all groups on the year-to-date weighted index."""
    start = date(ref.year, 1, 1)
    lo, hi = start.strftime("%Y%m%d"), ref.strftime("%Y%m%d")
    deltas = {}
    for group in groups:
        by_day = {}
        for g in group_games(by_team, group, lo, hi):
            by_day[g["date"]] = by_day.get(g["date"], 0.0) + g["weighted"]
        deltas[group["name"]] = by_day

    cum = {name: 0.0 for name in deltas}
    series = {name: {} for name in deltas}
    keep_from = ref - timedelta(days=days - 1)
    for i in range((ref - start).days + 1):
        d = start + timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        for name in cum:
            cum[name] += deltas[name].get(ds, 0.0)
        if d >= keep_from:
            for rank, name in enumerate(sorted(cum, key=lambda n: -cum[n]), 1):
                series[name][ds] = [rank, round(cum[name], 2)]
    return series


def detect_climb(ctx: dict, ranks: dict) -> dict | None:
    """The group moved several places in the year standings this week."""
    ref = ctx["ref"]
    name = ctx["group"]["name"]
    series = ranks.get(name, {})
    today = ref.strftime("%Y%m%d")
    week_ago = (ref - timedelta(days=7)).strftime("%Y%m%d")
    if today not in series or week_ago not in series:
        return None
    if not ctx["last7_games"]:        # didn't play: the move isn't theirs
        return None
    new_rank, new_cum = series[today]
    old_rank, old_cum = series[week_ago]
    delta = old_rank - new_rank       # positive = climbed
    if abs(delta) < MIN_CLIMB:
        return None
    # a shuffle at the bottom of an 88-group table is noise; the same move
    # near the top is a story
    field = len(ranks)
    position = max(0.25, 1 - (min(old_rank, new_rank) - 1) / field)
    score = min(1.0, abs(delta) / 12) * position
    return base_finding(
        ctx, "climb", score,
        direction="hot" if delta > 0 else "cold",
        climb={"from": old_rank, "to": new_rank, "delta": delta,
               "field": len(ranks), "ytd": new_cum, "ytd_week_ago": old_cum},
        rank_series=[{"date": d, "rank": v[0], "ytd": v[1]}
                     for d, v in sorted(series.items())],
    )


# --- selection ---------------------------------------------------------------

def load_run_history(path: str) -> list:
    """The record of past runs: [{date, cities: [...]}], oldest first."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Ignoring unreadable %s (%s)", path, e)
        return []


def recent_features(history: list, ref: date,
                    lookback: int = NOVELTY_LOOKBACK) -> dict:
    """city -> days since it was last featured. Runs of this date are skipped:
    a replay must not push away the cities its own first run picked."""
    seen = {}
    for entry in history:
        try:
            when = date.fromisoformat(entry["date"])
        except (KeyError, ValueError):
            continue
        days = (ref - when).days
        if not 0 < days <= lookback:
            continue
        for city in entry.get("cities", []):
            seen[city] = min(seen.get(city, days), days)
    return seen


def novelty_factor(days_ago: int | None) -> float:
    if days_ago is None or days_ago >= NOVELTY_SPAN:
        return 1.0
    ramp = (days_ago - 1) / (NOVELTY_SPAN - 1)
    return round(NOVELTY_FLOOR + (1.0 - NOVELTY_FLOOR) * ramp, 4)


def select_findings(candidates: list, top_n: int, recent: dict | None = None,
                    seed: str | None = None) -> list:
    """Highest scores first, one group per city, kinds spread out, cities
    featured recently pushed down, near-ties shuffled by a date-seeded jitter.

    Passing seed=None disables the jitter entirely (deterministic selection).
    """
    recent = recent or {}
    rng = random.Random(seed)
    pool = []
    for cand in sorted(candidates, key=lambda c: c["name"]):   # deterministic order
        cand = dict(cand)
        cand["novelty"] = novelty_factor(recent.get(cand["city"]))
        cand["jitter"] = 1.0 if seed is None else round(rng.uniform(0.92, 1.08), 4)
        cand["final_score"] = round(cand["score"] * cand["novelty"] * cand["jitter"], 4)
        pool.append(cand)

    picked, cities = [], set()
    kinds: dict = {}
    while pool and len(picked) < top_n:
        eligible = [c for c in pool if kinds.get(c["kind"], 0) < MAX_PER_KIND]
        if not eligible:                      # only over-used kinds left
            eligible = pool
        best = max(eligible, key=lambda c: (
            c["final_score"] * (KIND_REPEAT_PENALTY if c["kind"] in kinds else 1.0),
            abs(c["month_totals"]["weighted"]),   # break exact ties on swing size
        ))
        pool.remove(best)
        if best["city"] in cities:
            continue
        cities.add(best["city"])
        kinds[best["kind"]] = kinds.get(best["kind"], 0) + 1
        picked.append(best)
    return picked


# --- copy --------------------------------------------------------------------

def headline(f: dict, ref: date) -> str:
    city, month_name = f["city"], MONTH_NAMES[ref.month]
    if f["kind"] == "month":
        adj = "best" if f["direction"] == "hot" else "worst"
        # "on record" is honest because every chart's caption states that the
        # the window is a rolling ten years, and summary.md gives the
        # sample size, so the headline says "on record" rather than a year
        if f["basis"] == "calendar":
            if f["rank"] == 1:
                return f"{city} is having its {adj} {month_name} on record"
            return f"{city} is having its {adj} {month_name} since {f['since'][:4]}"
        if f["rank"] == 1:
            return f"{city} is having its {adj} month on record"
        return f"{city} is having its {adj} month since {pretty_month(f['since'])}"

    if f["kind"] == "year":
        y = f["year"]
        adj = "best" if f["direction"] == "hot" else "worst"
        if y["rank"] == 1:
            run = f"{city} is having its {adj} year on record"
        else:
            run = f"{city} is having its {adj} year since {y['since']}"
        return f"{run} — {ordinal(y['place'])} of {y['field']} this year"

    if f["kind"] == "streak":
        s = f["streak"]
        verb = "won" if s["type"] == "W" else "lost"
        run = f"{city}'s teams have {verb} {s['length']} straight"
        if s["record"]:
            return f"{run} — their longest run on record"
        if s["since"]:
            return f"{run} — their longest since {pretty_date(s['since'])}"
        return run

    if f["kind"] == "turnaround":
        t = f["turnaround"]
        e, l = t["early"], t["late"]
        if f["direction"] == "hot":
            return (f"{city} has flipped its {month_name}: {e['w']}-{e['l']} "
                    f"before this week, {l['w']}-{l['l']} since")
        return (f"{city}'s {month_name} has come apart: {e['w']}-{e['l']} "
                f"before this week, {l['w']}-{l['l']} since")

    c = f["climb"]
    verb = "climbed" if c["delta"] > 0 else "fallen"
    return (f"{city} has {verb} {abs(c['delta'])} places in the {ref.year} "
            f"standings this week ({ordinal(c['from'])} → {ordinal(c['to'])} "
            f"of {c['field']})")


def lane_claim(lane: dict, basis: str, ref: date) -> str:
    adj = "best" if lane["direction"] == "hot" else "worst"
    if basis == "calendar":
        return (f"{ordinal(lane['rank'])}-{adj} {MONTH_NAMES[ref.month]} "
                f"of the {lane['n_months'] + 1} on record")
    return (f"{ordinal(lane['rank'])}-{adj} of {lane['n_months'] + 1} months "
            f"on record ({ordinal(round(lane['percentile'] * 100))} percentile)")


def summary_lines(f: dict, ref: date) -> list:
    m, l7 = f["month_totals"], f["last7"]
    tie = f" ({m['t']} ties)" if m["t"] else ""
    month_line = (f"- **This month (through {MONTH_NAMES[ref.month]} {ref.day}):** "
                  f"{m['w']}-{m['l']}{tie}, {m['weighted']:+.1f} weighted")
    lines = []

    if f["kind"] == "month":
        lines.append(month_line + " — " + lane_claim(f, f["basis"], ref))
        other = "all" if f["basis"] == "calendar" else "calendar"
        if other in f["comparisons"]:
            label = ("vs all months" if other == "all"
                     else f"vs past {MONTH_NAMES[ref.month]}s")
            lines.append(f"- **{label}:** " + lane_claim(f["comparisons"][other], other, ref))
    elif f["kind"] == "year":
        y, t = f["year"], f["year"]["totals"]
        adj = "best" if f["direction"] == "hot" else "worst"
        lines.append(f"- **{ref.year} so far (through {MONTH_NAMES[ref.month]} "
                     f"{ref.day}):** {t['w']}-{t['l']}, {t['weighted']:+.1f} weighted "
                     f"— {ordinal(y['rank'])}-{adj} of the {y['n_years']} years "
                     f"on record, at the same point")
        lines.append(f"- **Against the field:** {ordinal(y['place'])} of "
                     f"{y['field']} city groups on the year "
                     f"({ordinal(round(y['field_percentile'] * 100))} percentile)")
        if m["games"]:      # a year finding can land between the group's seasons
            lines.append(month_line)
    elif f["kind"] == "streak":
        s = f["streak"]
        word = run_word(s["type"])
        prior = ("no run this long on record" if s["record"]
                 else f"previous best {s['prior_best']}")
        lines.append(f"- **The run:** {s['length']} straight {word} since "
                     f"{pretty_date(s['start'])}, {s['weighted']:+.1f} weighted ({prior})")
        lines.append(month_line)
    elif f["kind"] == "turnaround":
        t = f["turnaround"]
        lines.append(f"- **Before this week:** {t['early']['w']}-{t['early']['l']}, "
                     f"{t['early']['weighted']:+.1f} weighted "
                     f"({t['pace_early']:+.2f} per game)")
        lines.append(f"- **Since:** {t['late']['w']}-{t['late']['l']}, "
                     f"{t['late']['weighted']:+.1f} weighted "
                     f"({t['pace_late']:+.2f} per game)")
        lines.append(month_line)
    else:
        c = f["climb"]
        lines.append(f"- **Year standings:** {ordinal(c['from'])} → "
                     f"{ordinal(c['to'])} of {c['field']} "
                     f"({c['ytd_week_ago']:+.1f} → {c['ytd']:+.1f} weighted)")
        lines.append(month_line)

    if l7["games"]:
        lines.append(f"- **Last 7 days:** {l7['w']}-{l7['l']}, "
                     f"{l7['weighted']:+.1f} weighted")
    drivers = sorted((t for t in f["per_team"] if t["last7"]["games"]),
                     key=lambda t: abs(t["last7"]["weighted"]), reverse=True)
    if drivers:
        d = drivers[0]
        lines.append(f"- **Driving it this week:** {d['nickname']} "
                     f"({d['last7']['w']}-{d['last7']['l']}, {d['last7']['weighted']:+.1f})")
    streaks = [t for t in f["per_team"] if t["streak"] and t["streak"]["length"] >= 3]
    if streaks:
        lines.append("- **Active streaks:** " + ", ".join(
            f"{t['nickname']} {t['streak']['type']}{t['streak']['length']}" for t in streaks))
    return lines


KIND_ICON = {"month": {"hot": "🔥", "cold": "🥶"},
             "year": {"hot": "🏆", "cold": "🧊"},
             "streak": {"hot": "📈", "cold": "📉"},
             "turnaround": {"hot": "🔄", "cold": "🔄"},
             "climb": {"hot": "⬆️", "cold": "⬇️"}}


def write_summary(path: str, findings: list, ref: date) -> None:
    out = [f"# Fandom spotlight — {ref.isoformat()}", ""]
    if not findings:
        out.append("Nothing notable today (too early in the month, or nothing "
                   "in the tails).")
    for i, f in enumerate(findings, 1):
        icon = KIND_ICON[f["kind"]][f["direction"]]
        out += [f"## {i}. {icon} {f['headline']}",
                f"*{f['label']}* · `{f['kind']}`", ""]
        out += summary_lines(f, ref)
        out += ["", "Images: " + " · ".join(f"`{img}`" for img in f["images"]), ""]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


# --- race series (context for the field chart) -------------------------------

def race_series(by_team: dict, groups: list, ref: date) -> dict:
    """Cumulative weighted index by day of month for every group (for the
    highlight-vs-field race chart)."""
    lo, hi = mtd_window(ref.year, ref.month, ref.day)
    days = [f"{ref.year:04d}{ref.month:02d}{d:02d}" for d in range(1, ref.day + 1)]
    series = {}
    for group in groups:
        by_day = {}
        for g in group_games(by_team, group, lo, hi):
            by_day[g["date"]] = by_day.get(g["date"], 0.0) + g["weighted"]
        cum, out = 0.0, []
        for d in days:
            cum += by_day.get(d, 0.0)
            out.append(round(cum, 3))
        series[group["name"]] = out
    return {"days": [int(d[-2:]) for d in days], "series": series}


IMAGES = {"month": ("race", "history", "teams"),
          # no per-team card here: that chart is month-scoped, and a year
          # finding can land on a group whose season is already over
          "year": ("year", "field"),
          "streak": ("race", "timeline", "teams"),
          "turnaround": ("race", "timeline", "teams"),
          "climb": ("race", "bump", "teams")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--out-dir", default=CONTENT_DIR)
    parser.add_argument("--top", type=int, default=TOP_N)
    parser.add_argument("--kinds", default=",".join(KINDS),
                        help=f"Comma-separated detectors to run: {', '.join(KINDS)}.")
    parser.add_argument("--compare", choices=["both", "all", "calendar"], default="both",
                        help="Month-detector lanes: every month on record, only the same "
                             "calendar month in previous years, or both (best story wins).")
    parser.add_argument("--no-novelty", action="store_true",
                        help="Ignore which cities were featured on recent runs.")
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    unknown = set(kinds) - set(KINDS)
    if unknown:
        parser.error(f"unknown detector(s): {', '.join(sorted(unknown))}")

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    history_path = os.path.join(out_dir, HISTORY_FILE)
    history = load_run_history(history_path)

    def write(findings, race=None):
        out = {"reference_date": ref.strftime("%Y%m%d"),
               "month": f"{ref.year:04d}-{ref.month:02d}",
               "cutoff_day": ref.day,
               "race": race or {"days": [], "series": {}},
               "findings": findings}
        with open(os.path.join(out_dir, "findings.json"), "w") as fh:
            json.dump(out, fh)
        write_summary(os.path.join(out_dir, "summary.md"), findings, ref)
        # the run log replaces reading back dated folders: the output folder is
        # overwritten every run, so the cooldown needs its own memory
        kept = [h for h in history if h.get("date") != ref.isoformat()]
        kept.append({"date": ref.isoformat(),
                     "cities": [f["city"] for f in findings]})
        with open(history_path, "w") as fh:
            json.dump(sorted(kept, key=lambda h: h["date"])[-HISTORY_KEEP:], fh, indent=1)
            fh.write("\n")

    # The month-based detectors need a few days of games before a
    # month-to-date total means anything; the year and streak lanes don't
    # care what day of the month it is, so the run no longer stops here.
    early = ref.day < MIN_DAY
    if early:
        log.info("Day %d of the month — skipping the month and turnaround "
                 "detectors (min day %d).", ref.day, MIN_DAY)

    with open("city_groups.json") as f:
        groups = json.load(f)
    by_team = index_by_team(load_scores(args.data_dir))
    lanes = ("all", "calendar") if args.compare == "both" else (args.compare,)
    ranks = ytd_rank_series(by_team, groups, ref) if "climb" in kinds else {}
    field = ytd_totals(by_team, groups, ref) if "year" in kinds else {}

    candidates = []
    for group in groups:
        ctx = group_context(by_team, group, ref)
        found = []
        if "month" in kinds and not early:
            found.append(detect_month(ctx, lanes))
        if "year" in kinds:
            found.append(detect_year(ctx, by_team, field))
        if "streak" in kinds:
            found.append(detect_streak(by_team, ctx))
        if "turnaround" in kinds and not early:
            found.append(detect_turnaround(ctx))
        if "climb" in kinds:
            found.append(detect_climb(ctx, ranks))
        candidates += [f for f in found if f]

    by_kind = {k: sum(1 for c in candidates if c["kind"] == k) for k in kinds}
    log.info("Scored %d candidates across %d groups (%s).", len(candidates), len(groups),
             ", ".join(f"{k}: {n}" for k, n in by_kind.items()))

    recent = {} if args.no_novelty else recent_features(history, ref)
    if recent:
        log.info("Cooling down recently featured: %s",
                 ", ".join(f"{c} ({d}d)" for c, d in sorted(recent.items(),
                                                            key=lambda kv: kv[1])))
    findings = select_findings(candidates, args.top, recent, seed=ref.isoformat())
    for f in findings:
        f["headline"] = headline(f, ref)
        f["slug"] = slugify(f["name"])
        f["images"] = [f"{f['slug']}_{img}.png" for img in IMAGES[f["kind"]]]
        log.info("%-11s %-12s %s (score %.3f x novelty %.2f -> %.3f)",
                 f["kind"], f["slug"], f["headline"], f["score"],
                 f["novelty"], f["final_score"])

    write(findings, race_series(by_team, groups, ref))
    log.info("Wrote %s (%d findings)", out_dir, len(findings))


if __name__ == "__main__":
    main()
