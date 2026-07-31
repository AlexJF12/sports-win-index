#!/usr/bin/env python3
"""
Daily fandom spotlight: find the city groups having a historically notable month.

For every group in city_groups.json, computes the weighted index for the current
month *to date* and compares it against the same month-to-date window (same
day-of-month cutoff) in every historical month since the data begins (Jan 2022).
Comparing July 1-14 against 54 full months would make every mid-month look bad;
same-cutoff MTD vs MTD is apples-to-apples.

Each qualifying group gets an interestingness score in [0, 1]:

    extremity  how far the current month sits in the tails of the group's own
               history (2 * |percentile - 0.5|) — both tails count, a
               historically awful month is as postable as a historically great one
    recency    the share of the month's weighted total earned in the last 7 days,
               so a group that just caught fire outranks one coasting on an
               early-month streak

    score = extremity * (0.6 + 0.4 * recency)

The top N groups (at most one per city — the 12 New York permutations co-move)
are written to content/YYYY-MM-DD/findings.json with everything the chart
renderer (render_content.py) needs, plus a human-readable summary.md with
copy-pasteable stats. Runs in the daily GitHub Actions workflow right after
aggregate_cities.py.

Usage:
    python fandom_analysis.py                 # reference date = yesterday (ET)
    python fandom_analysis.py --date 20260716
"""

import argparse
import calendar
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aggregate_cities import LEAGUE_WEIGHT, index_by_team, load_scores

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
CONTENT_DIR = "content"
HISTORY_START = (2022, 1)   # first month of score data
MIN_GAMES = 3               # months where the group played fewer games don't count
MIN_DAY = 4                 # too early in a month to call anything notable
MIN_HISTORY = 12            # need at least this many comparable months
TOP_N = 3


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
            games.append({"date": row["date"], "league": team["league"],
                          "abbr": team["abbr"], "nickname": team["nickname"],
                          "result": result, "weighted": weighted,
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


def percentile(hist_values: list, current: float) -> float:
    """Midrank percentile of current among hist_values, in [0, 1]."""
    below = sum(1 for v in hist_values if v < current)
    ties = sum(1 for v in hist_values if v == current)
    return (below + 0.5 * ties) / len(hist_values)


def recency_share(month_weighted: float, last7_weighted: float) -> float:
    """Share of the month's weighted total earned in the last 7 days, in [0, 1].
    Zero when the week points the other way from the month (no 'this week' hook)."""
    if month_weighted == 0:
        return 0.0
    return max(0.0, min(1.0, last7_weighted / month_weighted))


def trailing_streak(games: list) -> dict | None:
    """Current W or L streak at the end of a team's game list (ties break it)."""
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


def select_findings(candidates: list, top_n: int) -> list:
    """Highest scores first, at most one group per city."""
    seen_cities = set()
    picked = []
    for cand in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if cand["city"] in seen_cities:
            continue
        seen_cities.add(cand["city"])
        picked.append(cand)
        if len(picked) == top_n:
            break
    return picked


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def display_label(group: dict) -> str:
    return f"{group['city']}: " + "/".join(t["nickname"] for t in group["teams"])


def analyze_group(by_team: dict, group: dict, ref: date) -> dict | None:
    """Score one group's current month against its own history. None if it
    doesn't qualify (too few games, too little history)."""
    cutoff = ref.day
    lo, hi = mtd_window(ref.year, ref.month, cutoff)
    month_games = group_games(by_team, group, lo, hi)
    month_totals = tally(month_games)
    if month_totals["games"] < MIN_GAMES:
        return None

    hist = []
    for y, m in month_range(HISTORY_START, (ref.year, ref.month)):
        h_lo, h_hi = mtd_window(y, m, cutoff)
        totals = tally(group_games(by_team, group, h_lo, h_hi))
        if totals["games"] >= MIN_GAMES:
            hist.append({"month": f"{y:04d}-{m:02d}", "calendar_month": m, **totals})
    if len(hist) < MIN_HISTORY:
        return None

    hist_values = [h["weighted"] for h in hist]
    cur = month_totals["weighted"]
    pctl = percentile(hist_values, cur)
    direction = "hot" if pctl >= 0.5 else "cold"

    last7_lo = max(ref - timedelta(days=6), ref.replace(day=1))
    last7_games = [g for g in month_games if g["date"] >= last7_lo.strftime("%Y%m%d")]
    last7 = tally(last7_games)
    share = recency_share(cur if direction == "hot" else -cur,
                          last7["weighted"] if direction == "hot" else -last7["weighted"])

    extremity = 2 * abs(pctl - 0.5)
    score = extremity * (0.6 + 0.4 * share)

    # "best/worst month since X": the most recent historical month at least as
    # extreme as this one; None means this is the most extreme month on record.
    if direction == "hot":
        rank = 1 + sum(1 for v in hist_values if v > cur)
        since = max((h["month"] for h in hist if h["weighted"] >= cur), default=None)
    else:
        rank = 1 + sum(1 for v in hist_values if v < cur)
        since = max((h["month"] for h in hist if h["weighted"] <= cur), default=None)

    same_cal = [h["weighted"] for h in hist if h["calendar_month"] == ref.month]
    if direction == "hot":
        cal_rank = 1 + sum(1 for v in same_cal if v > cur)
    else:
        cal_rank = 1 + sum(1 for v in same_cal if v < cur)

    per_team = []
    for team in group["teams"]:
        t_games = [g for g in month_games if g["abbr"] == team["abbr"] and g["league"] == team["league"]]
        t7_games = [g for g in last7_games if g["abbr"] == team["abbr"] and g["league"] == team["league"]]
        per_team.append({**team, "month": tally(t_games), "last7": tally(t7_games),
                         "streak": trailing_streak(t_games)})

    return {
        "name": group["name"], "city": group["city"], "label": display_label(group),
        "teams": group["teams"],
        "direction": direction, "score": round(score, 4),
        "percentile": round(pctl, 4), "rank": rank, "n_months": len(hist),
        "calendar_rank": cal_rank, "calendar_n": len(same_cal) + 1,
        "since": since,
        "month_totals": month_totals, "last7": last7,
        "per_team": per_team,
        "history": hist,
    }


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


MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def pretty_month(iso: str) -> str:
    y, m = iso.split("-")
    return f"{MONTH_NAMES[int(m)]} {y}"


def headline(f: dict, ref: date) -> str:
    month_name = MONTH_NAMES[ref.month]
    adj = "best" if f["direction"] == "hot" else "worst"
    if f["rank"] == 1:
        return f"{f['city']} is having its {adj} month on record"
    if f["calendar_rank"] == 1:
        return f"{f['city']} is having its {adj} {month_name} on record"
    return f"{f['city']} is having its {adj} month since {pretty_month(f['since'])}"


def summary_lines(f: dict, ref: date) -> list:
    m, l7 = f["month_totals"], f["last7"]
    tie = f" ({m['t']} ties)" if m["t"] else ""
    lines = [
        f"- **This month (through {MONTH_NAMES[ref.month]} {ref.day}):** "
        f"{m['w']}-{m['l']}{tie}, {m['weighted']:+.1f} weighted — "
        f"{ordinal(f['rank'])}-{'best' if f['direction'] == 'hot' else 'worst'} "
        f"of {f['n_months'] + 1} months since 2022 "
        f"({ordinal(round(f['percentile'] * 100))} percentile)",
        f"- **Last 7 days:** {l7['w']}-{l7['l']}, {l7['weighted']:+.1f} weighted",
    ]
    drivers = sorted((t for t in f["per_team"] if t["last7"]["games"]),
                     key=lambda t: abs(t["last7"]["weighted"]), reverse=True)
    if drivers:
        d = drivers[0]
        lines.append(f"- **Driving it this week:** {d['nickname']} "
                     f"({d['last7']['w']}-{d['last7']['l']}, {d['last7']['weighted']:+.1f})")
    streaks = [t for t in f["per_team"]
               if t["streak"] and t["streak"]["length"] >= 3]
    if streaks:
        lines.append("- **Active streaks:** " + ", ".join(
            f"{t['nickname']} {t['streak']['type']}{t['streak']['length']}" for t in streaks))
    return lines


def write_summary(path: str, findings: list, ref: date) -> None:
    icon = {"hot": "🔥", "cold": "🥶"}
    out = [f"# Fandom spotlight — {ref.isoformat()}", ""]
    if not findings:
        out.append("Nothing notable today (too early in the month, or no group in the tails).")
    for i, f in enumerate(findings, 1):
        out += [f"## {i}. {icon[f['direction']]} {f['headline']}",
                f"*{f['label']}*", ""]
        out += summary_lines(f, ref)
        slug = f["slug"]
        out += ["", f"Images: `{slug}_race.png` · `{slug}_history.png` · `{slug}_teams.png`", ""]
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def slugify(name: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in name.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--out-dir", default=CONTENT_DIR)
    parser.add_argument("--top", type=int, default=TOP_N)
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    out_dir = os.path.join(args.out_dir, ref.isoformat())
    os.makedirs(out_dir, exist_ok=True)

    if ref.day < MIN_DAY:
        log.info("Day %d of the month — too early to compare (min day %d).", ref.day, MIN_DAY)
        write_summary(os.path.join(out_dir, "summary.md"), [], ref)
        with open(os.path.join(out_dir, "findings.json"), "w") as fh:
            json.dump({"reference_date": ref.strftime("%Y%m%d"), "findings": []}, fh)
        return

    with open("city_groups.json") as f:
        groups = json.load(f)
    by_team = index_by_team(load_scores(args.data_dir))

    candidates = []
    for group in groups:
        cand = analyze_group(by_team, group, ref)
        if cand:
            candidates.append(cand)
    log.info("Scored %d of %d groups.", len(candidates), len(groups))

    findings = select_findings(candidates, args.top)
    for f in findings:
        f["headline"] = headline(f, ref)
        f["slug"] = slugify(f["name"])
        log.info("%s: %s (score %.3f, %s, %d-%d, %+.1f weighted)",
                 f["slug"], f["headline"], f["score"], f["direction"],
                 f["month_totals"]["w"], f["month_totals"]["l"],
                 f["month_totals"]["weighted"])

    out = {
        "reference_date": ref.strftime("%Y%m%d"),
        "month": f"{ref.year:04d}-{ref.month:02d}",
        "cutoff_day": ref.day,
        "race": race_series(by_team, groups, ref),
        "findings": findings,
    }
    with open(os.path.join(out_dir, "findings.json"), "w") as fh:
        json.dump(out, fh)
    write_summary(os.path.join(out_dir, "summary.md"), findings, ref)
    log.info("Wrote %s (%d findings)", out_dir, len(findings))


if __name__ == "__main__":
    main()
