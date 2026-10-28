#!/usr/bin/env python3
"""
The banner race: who is on course to hang this month's banner, four times a
month, and who hung it once the month is done.

banners.html hangs a banner in the rafters for every finished month — the city
group with the month's best weighted total — but it only ever shows the race
when somebody opens the page. This takes the same standings, measured the same
way, and turns them into a post on five days of the month:

    the 7th, 14th, 21st and 28th   "banner watch" — who leads, by how much,
                                   and the four cities chasing
    the last day                   the champion, published and shared on the
                                   1st once the month's final games are in

Each one carries the banner itself as its picture: the leader's (or the
champion's) banner drawn the way the page hangs it — month, year, city, total,
the running total in black and each team's share in its league color — beside
the top five cities in the race. That picture is what goes out on Bluesky.

The run is keyed on its reference date like every other analysis here, so a
day that isn't one of the five writes nothing and leaves the last run's folder
in place; publish_blog.py re-files that folder under its own date and changes
nothing. February has no 28th checkpoint in a common year: its 28th is the
last day, so the champion post takes that slot.

Usage:
    python banner_race.py                    # reference date = yesterday (ET)
    python banner_race.py --date 20261014    # replay a day
    python banner_race.py --date 20261009 --force   # a race post on any day
    python banner_race.py --no-images        # race.json only (skips matplotlib)
"""

import argparse
import calendar
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aggregate_cities import LEAGUE_WEIGHT, index_by_team, load_scores
from fandom_analysis import FIRST_YEAR, MONTH_NAMES, display_label, group_games

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
OUT_DIR = os.path.join("content", "banner")
DATA_FILE = "race.json"
IMAGE_FILE = "race.png"
CHECKPOINTS = (7, 14, 21, 28)   # days of the month the race is reported on
CONTENDERS = 5                  # cities shown in the race
LEVEL = 0.05                    # closer than this on points is a dead heat,
                                # decided on wins (banners.html uses the same)


# --- the standings -------------------------------------------------------------

def natural(name: str) -> list:
    """"New York 2" before "New York 10" — the page's localeCompare(numeric)."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def ym_of(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


def month_tallies(by_team: dict, groups: list, through: date) -> dict:
    """ym -> group name -> {w, l, t, games, weighted}, every month on record
    through `through`. One pass per group: a call per group per month would be
    two hundred months times eighty-eight groups of rescanning."""
    hi = through.strftime("%Y%m%d")
    out = {}
    for group in groups:
        for game in group_games(by_team, group, f"{FIRST_YEAR}0101", hi):
            row = out.setdefault(game["date"][:6], {}).setdefault(
                group["name"], {"w": 0, "l": 0, "t": 0, "games": 0, "weighted": 0.0})
            row[{"W": "w", "L": "l", "T": "t"}[game["result"]]] += 1
            row["games"] += 1
            row["weighted"] += game["weighted"]
    return out


def ranked(tallies: dict, groups: list) -> list:
    """One month's groups that played, best first: points, then wins, then
    name — the order banners.html hangs them in."""
    rows = [{"group": g, **tallies[g["name"]]} for g in groups if g["name"] in tallies]
    for row in rows:
        row["weighted"] = round(row["weighted"], 4)
    rows.sort(key=lambda r: (-r["weighted"], -r["w"], natural(r["group"]["name"])))
    return rows


def by_city(rows: list) -> list:
    """The best group in each city, in order. The banner goes to a city, so
    New York's second-best group isn't a contender — it's New York again."""
    seen, out = set(), []
    for row in rows:
        if row["group"]["city"] not in seen:
            seen.add(row["group"]["city"])
            out.append(row)
    return out


def champions(tallies: dict, groups: list, through_ym: str) -> dict:
    """ym -> champion row, for every month up to and including through_ym."""
    return {ym: rows[0] for ym in sorted(tallies) if ym <= through_ym
            if (rows := ranked(tallies[ym], groups))}


def team_series(by_team: dict, group: dict, ref: date) -> list:
    """Each team's running total by day of the month, day 0 through ref."""
    lo = ref.replace(day=1).strftime("%Y%m%d")
    games = group_games(by_team, group, lo, ref.strftime("%Y%m%d"))
    out = []
    for team in group["teams"]:
        daily = [0.0] * (ref.day + 1)
        w = l = t = 0
        for game in games:
            if (game["league"], game["abbr"]) != (team["league"], team["abbr"]):
                continue
            daily[int(game["date"][6:])] += game["weighted"]
            w += game["result"] == "W"
            l += game["result"] == "L"
            t += game["result"] == "T"
        cum, values = 0.0, []
        for d in daily:
            cum += d
            values.append(round(cum, 3))
        out.append({**team, "w": w, "l": l, "t": t, "weighted": round(cum, 4),
                    "played": w + l + t > 0, "series": values})
    return out


def entry(row: dict, place: int | None = None) -> dict:
    out = {"name": row["group"]["name"], "city": row["group"]["city"],
           "label": display_label(row["group"]),
           "w": row["w"], "l": row["l"], "t": row["t"], "games": row["games"],
           "weighted": row["weighted"]}
    return {"place": place, **out} if place is not None else out


def is_feature_day(ref: date) -> bool:
    return ref.day in CHECKPOINTS or is_last_day(ref)


def is_last_day(ref: date) -> bool:
    return ref.day == calendar.monthrange(ref.year, ref.month)[1]


def race(by_team: dict, groups: list, ref: date) -> dict | None:
    """The month through ref, as the post and the picture need it."""
    tallies = month_tallies(by_team, groups, ref)
    ym = ym_of(ref)
    rows = ranked(tallies.get(ym, {}), groups)
    if not rows:
        return None
    final = is_last_day(ref)
    leader = rows[0]
    runner_up = next((r for r in rows if r["group"]["city"] != leader["group"]["city"]), None)
    cities = by_city(rows)

    # banners the leader's city already has, from the months before this one
    past = champions(tallies, groups, ym)
    past.pop(ym, None)
    earlier = sum(1 for c in past.values() if c["group"]["city"] == leader["group"]["city"])
    last_ym = ym_of(ref.replace(day=1) - timedelta(days=1))
    previous = past.get(last_ym)

    series = team_series(by_team, leader["group"], ref)
    total = [round(sum(t["series"][d] for t in series), 3) for d in range(ref.day + 1)]
    return {
        "reference_date": ref.isoformat(),
        "year": ref.year, "month": ref.month, "month_name": MONTH_NAMES[ref.month],
        "day": ref.day, "days": calendar.monthrange(ref.year, ref.month)[1],
        "final": final,
        "leader": {**entry(leader), "teams": series, "series": total},
        "runner_up": entry(runner_up) if runner_up else None,
        "margin": round(leader["weighted"] - runner_up["weighted"], 4) if runner_up else None,
        "standings": [entry(r, n) for n, r in enumerate(cities[:CONTENDERS], 1)],
        "cities_in_race": len(cities),
        # the banner this would be: #1 is the city's first since the record began
        "banner_number": earlier + 1,
        "since": FIRST_YEAR,
        "previous": ({"month_name": MONTH_NAMES[int(last_ym[4:])], "year": int(last_ym[:4]),
                      "city": previous["group"]["city"],
                      "weighted": round(previous["weighted"], 4)} if previous else None),
    }


# --- the picture ---------------------------------------------------------------

# banners.html's palette: the arena is lit the same in both themes, the cloth is
# ivory felt with charcoal ink and pewter trim, and the leagues keep their colors
ARENA, ARENA_2 = "#0c0a12", "#17121f"
ARENA_INK, ARENA_MUTED = "#f4f1ea", "#a49fb2"
STEEL, STEEL_HI = "#474552", "#6a6876"
FELT, FELT_DEEP = "#f3eee2", "#ddd5c2"
CLOTH_INK, CLOTH_MUTED = "#1c1c21", "#6d675b"
PEWTER, PEWTER_HI, PEWTER_LO = "#b4b8c0", "#eef0f3", "#737882"
ACCENT = "#9d6bff"
WIN, LOSS = "#4fd16a", "#ff6b6b"
LEAGUE_COLOR = {"mlb": "#2f7fd6", "nba": "#e0702c", "nhl": "#159c74", "nfl": "#d04a88"}
FONT = "DejaVu Sans"


def signed(n: float, places: int = 1) -> str:
    n = 0.0 if abs(n) < 1e-9 else n
    return f"{n:+.{places}f}"


def record(r: dict) -> str:
    return f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r["t"] else "")


def render(data: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle

    fig = plt.figure(figsize=(8, 5), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")

    # the house lights are down: a dark vertical wash, a warm glow from the
    # catwalk and the brand violet coming up off the floor
    yy, xx = np.mgrid[0:1:200j, 0:1:320j]
    base = np.array([int(ARENA[i:i + 2], 16) for i in (1, 3, 5)]) / 255
    mid = np.array([int(ARENA_2[i:i + 2], 16) for i in (1, 3, 5)]) / 255
    wash = 1 - np.abs(yy - 0.45) / 0.55
    img = base + (mid - base) * np.clip(wash, 0, 1)[..., None]
    top = np.exp(-(((xx - 0.22) / 0.30) ** 2 + ((yy - 0.0) / 0.35) ** 2))
    floor = np.exp(-(((xx - 0.6) / 0.45) ** 2 + ((yy - 1.05) / 0.35) ** 2))
    img = img + top[..., None] * np.array([1.0, 0.92, 0.75]) * 0.16
    img = img + floor[..., None] * np.array([0.49, 0.25, 0.94]) * 0.20
    ax.imshow(np.clip(img, 0, 1), extent=(0, 16, 0, 10), aspect="auto", zorder=0)

    def text(x, y, s, size, color, weight="normal", ha="left", va="center", spacing=0, **kw):
        if spacing:
            s = (" " * spacing).join(s)       # letter-spaced caps, like the page's eyebrows
        return ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha,
                       va=va, family=FONT, zorder=6, **kw)

    renderer = fig.canvas.get_renderer()

    def fitted(x, y, s, size, color, width, **kw):
        """Text shrunk to fit `width` units: "September" and "Minneapolis-St.
        Paul" get smaller, never clipped or run into the next thing."""
        t = text(x, y, s, size, color, **kw)
        room = width * fig.bbox.width / 16
        drawn = t.get_window_extent(renderer).width
        if drawn > room:
            t.set_fontsize(size * room / drawn)
        return t

    leader = data["leader"]
    final = data["final"]
    month = data["month_name"]

    # --- the banner, hanging from its length of truss ---
    x0, x1, top_y, bottom_y, notch = 0.75, 6.05, 9.05, 0.35, 0.62
    cx = (x0 + x1) / 2
    ax.add_patch(Rectangle((x0 - 0.45, 9.62), x1 - x0 + 0.9, 0.22, color="#1c1a24", zorder=1))
    for x in np.arange(x0 - 0.45, x1 + 0.45, 0.16):
        ax.plot([x, x + 0.08], [9.62, 9.84], color=STEEL, lw=0.8, zorder=1.1)
        ax.plot([x + 0.08, x + 0.16], [9.84, 9.62], color=STEEL, lw=0.8, zorder=1.1)
    ax.plot([x0 - 0.45, x1 + 0.45], [9.84, 9.84], color=STEEL_HI, lw=1.2, zorder=1.2)
    ax.plot([x0 - 0.45, x1 + 0.45], [9.62, 9.62], color=STEEL_HI, lw=1.2, zorder=1.2)
    for wx, lean in ((x0 + 0.85, 0.25), (x1 - 0.85, -0.25)):
        ax.plot([wx, wx + lean], [9.18, 9.62], color="#b9b6c4", lw=0.9, zorder=1.5)
    ax.add_patch(FancyBboxPatch((x0 - 0.12, 9.08), x1 - x0 + 0.24, 0.1,
                                boxstyle="round,pad=0,rounding_size=0.05",
                                color="#9b98a6", zorder=3))

    def cloth(inset, color, z):
        return Polygon([(x0 + inset, top_y - inset), (x1 - inset, top_y - inset),
                        (x1 - inset, bottom_y + inset), (cx, bottom_y + notch + inset),
                        (x0 + inset, bottom_y + inset)], closed=True, color=color, zorder=z)

    ax.add_patch(Polygon([(x0 + 0.08, top_y - 0.1), (x1 + 0.08, top_y - 0.1),
                          (x1 + 0.08, bottom_y - 0.12), (cx + 0.08, bottom_y + notch - 0.12),
                          (x0 + 0.08, bottom_y - 0.12)], closed=True, color="#000000",
                         alpha=0.45, zorder=1.8))                       # drop shadow
    ax.add_patch(cloth(0, PEWTER, 2))
    ax.add_patch(cloth(0.05, PEWTER_LO, 2.1))
    ax.add_patch(cloth(0.09, FELT, 2.2))
    # the stitched line just inside the trim
    s = 0.24
    ax.add_patch(Polygon([(x0 + s, top_y - s), (x1 - s, top_y - s), (x1 - s, bottom_y + s),
                          (cx, bottom_y + notch + s), (x0 + s, bottom_y + s)], closed=True,
                         fill=False, ec=CLOTH_MUTED, lw=0.6, ls=(0, (3, 3)), alpha=0.5,
                         zorder=2.3))

    text(cx, 8.62, "CHAMPIONS" if final else "LEADING", 8, CLOTH_MUTED, "bold",
         ha="center", spacing=1)
    text(cx, 8.22, "★ ★ ★", 10, CLOTH_INK, ha="center")
    fitted(cx, 7.5, month.upper(), 30, CLOTH_INK, x1 - x0 - 0.8, weight="bold", ha="center")
    text(cx, 6.82, str(data["year"]), 13, CLOTH_MUTED, "bold", ha="center")
    rule_y = 6.38
    if not final:
        tag = f"DAY {data['day']} OF {data['days']}"
        ax.add_patch(FancyBboxPatch((cx - 0.85, 6.3), 1.7, 0.3,
                                    boxstyle="round,pad=0,rounding_size=0.15",
                                    color=CLOTH_INK, zorder=5))
        text(cx, 6.45, tag, 6.5, FELT, "bold", ha="center")
        rule_y = 6.05
    ax.plot([cx - 1.6, cx + 1.6], [rule_y, rule_y], color=CLOTH_MUTED, lw=0.8, zorder=5)
    city = leader["city"]
    fitted(cx, rule_y - 0.5, city.upper(), 17, CLOTH_INK, x1 - x0 - 1.0, weight="bold",
           ha="center")
    text(cx, rule_y - 1.12, signed(leader["weighted"]), 20, CLOTH_INK, "bold", ha="center")

    # the month so far: the group's running total in black over each team's share
    chart = fig.add_axes([(x0 + 0.8) / 16, 2.45 / 10, (x1 - x0 - 1.35) / 16, 1.85 / 10])
    chart.set_facecolor("none")
    days = data["days"]
    xs = list(range(len(leader["series"])))
    for team in leader["teams"]:
        if team["played"]:
            chart.plot(xs, team["series"], color=LEAGUE_COLOR[team["league"]], lw=1.3,
                       alpha=0.9, solid_capstyle="round")
    chart.fill_between(xs, leader["series"], 0, color=CLOTH_INK, alpha=0.08, lw=0)
    chart.plot(xs, leader["series"], color=CLOTH_INK, lw=2.4, solid_capstyle="round")
    chart.scatter([xs[-1]], [leader["series"][-1]], s=22, color=CLOTH_INK,
                  edgecolor=FELT, linewidth=1.2, zorder=5)
    chart.axhline(0, color=CLOTH_INK, alpha=0.3, lw=0.8, ls=(0, (3, 3)))
    if not final:   # the days still to play, so a half month never reads as a whole one
        chart.axvspan(xs[-1], days, color=CLOTH_INK, alpha=0.05, lw=0)
        chart.axvline(xs[-1], color=CLOTH_INK, alpha=0.3, lw=0.8, ls=(0, (2, 3)))
    chart.set_xlim(0, days)
    chart.set_xticks([1, 15, days])
    chart.tick_params(length=0, labelsize=6, colors=CLOTH_MUTED, pad=2)
    chart.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3, integer=True))
    chart.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: "0" if v == 0 else f"{v:+.0f}"))
    for spine in chart.spines.values():
        spine.set_visible(False)

    # the legend: a line per team, its record and its share
    for n, team in enumerate(leader["teams"]):
        y = 1.98 - n * 0.25
        ax.add_patch(Rectangle((x0 + 0.62, y - 0.07), 0.14, 0.14,
                               color=LEAGUE_COLOR[team["league"]], zorder=5))
        text(x0 + 0.9, y, f"{team['league'].upper()}  {team['nickname']}", 7, CLOTH_INK)
        if team["played"]:
            text(x1 - 1.35, y, record(team), 7, CLOTH_MUTED, ha="right")
        text(x1 - 0.62, y, signed(team["weighted"]) if team["played"] else "—", 7,
             CLOTH_INK, "bold", ha="right")

    # --- the race beside it ---
    rx, rw = 7.0, 8.4
    eyebrow = (f"FINAL  ·  {month.upper()} {data['year']}" if final
               else f"BANNER WATCH  ·  DAY {data['day']} OF {data['days']}")
    text(rx, 9.05, eyebrow, 8.5, PEWTER, "bold")
    headline = (f"{city} takes the {month} banner" if final
                else f"{city} leads the {month} race")
    fitted(rx, 8.35, headline, 21, ARENA_INK, rw, weight="bold")
    text(rx, 7.68, lead_line(data), 9.5, ARENA_MUTED)

    text(rx, 6.95, "FINAL STANDINGS" if final else "THE RACE", 7.5, PEWTER, "bold", spacing=1)
    rows = data["standings"]
    best = max([abs(r["weighted"]) for r in rows] + [1e-9])
    bar_x = rx + 4.4
    bar_w = rx + rw - 1.5 - bar_x          # clear of the value printed after it
    for n, row in enumerate(rows):
        y = 6.3 - n * 0.98
        lead = n == 0
        ax.plot([rx, rx + rw], [y + 0.49, y + 0.49], color="#ffffff", alpha=0.09, lw=0.6)
        text(rx + 0.05, y, str(row["place"]), 15, ACCENT if lead else STEEL_HI, "bold")
        room = bar_x - rx - 0.8
        fitted(rx + 0.6, y + 0.13, row["city"], 12, ARENA_INK, room, weight="bold")
        teams = row["label"].split(": ", 1)[-1]
        fitted(rx + 0.6, y - 0.22, f"{teams}  ·  {record(row)}", 6.5, ARENA_MUTED, room)
        length = bar_w * max(row["weighted"], 0) / best
        ax.add_patch(FancyBboxPatch((bar_x, y - 0.11), max(length, 0.02), 0.22,
                                    boxstyle="round,pad=0,rounding_size=0.08",
                                    color=ACCENT if lead else STEEL_HI, zorder=5))
        text(rx + rw, y, signed(row["weighted"]), 12,
             ARENA_INK if row["weighted"] >= 0 else LOSS, "bold", ha="right")

    note = (f"Banner #{data['banner_number']} for {city} since {data['since']}" if final
            else f"{data['days'] - data['day']} days to play  ·  "
                 f"{data['cities_in_race']} cities in the race")
    text(rx, 1.12, note, 9, PEWTER_HI, "bold")
    text(rx, 0.55, "Banner Month  ·  weighted index: a win is worth MLB ±2.25, "
                   "NBA/NHL ±3.17, NFL ±6.96", 6.5, ARENA_MUTED)

    fig.savefig(path, dpi=200, facecolor=ARENA)
    plt.close(fig)


def lead_line(data: dict) -> str:
    """"Leads Denver by +6.8" — or, level on points, the wins tiebreak that
    decides it. The same wording banners.html uses for the live month."""
    r, margin = data["runner_up"], data["margin"]
    if r is None:
        return "Unopposed so far" if not data["final"] else "Unopposed"
    if margin < LEVEL:
        return (f"Level with {r['city']} on points, ahead on wins" if not data["final"]
                else f"Level with {r['city']} on points, won on wins")
    return (f"Finished {signed(margin)} clear of {r['city']}" if data["final"]
            else f"Leads {r['city']} by {signed(margin)}")


# --- running it ----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--force", action="store_true",
                        help="Write a race post even on a day that isn't a checkpoint.")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--out-dir", default=OUT_DIR)
    parser.add_argument("--no-images", action="store_true",
                        help=f"Write {DATA_FILE} only (skips the matplotlib import).")
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    if not (is_feature_day(ref) or args.force):
        log.info("%s is not a banner checkpoint (days %s and the last) — nothing to write",
                 ref.isoformat(), ", ".join(map(str, CHECKPOINTS)))
        return

    with open("city_groups.json") as f:
        groups = json.load(f)
    by_team = index_by_team(load_scores(args.data_dir))
    data = race(by_team, groups, ref)
    if data is None:
        log.warning("Nobody has played in %s %d yet — leaving %s alone",
                    MONTH_NAMES[ref.month], ref.year, args.out_dir)
        return

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, DATA_FILE), "w") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    log.info("%s %d, day %d of %d: %s %s at %s%s", data["month_name"], data["year"],
             data["day"], data["days"], data["leader"]["city"],
             "wins" if data["final"] else "leads", signed(data["leader"]["weighted"]),
             f" — {lead_line(data)}" if data["runner_up"] else "")

    image = os.path.join(args.out_dir, IMAGE_FILE)
    if args.no_images:
        # a race.json beside last checkpoint's picture would publish the two together
        if os.path.exists(image):
            os.remove(image)
        return
    render(data, image)
    log.info("Wrote %s", args.out_dir)


if __name__ == "__main__":
    main()
