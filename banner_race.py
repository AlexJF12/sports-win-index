#!/usr/bin/env python3
"""
The banner race: who is on course to hang this month's banner, four times a
month, and who hung it once the month is done.

banners.html hangs a banner in the rafters for every finished month — the city
group with the month's best weighted total — but it only ever shows the race
when somebody opens the page. This takes the same standings, measured the same
way, and turns them into a post on five days of the month:

    the 5th, 12th, 19th and 26th   "banner watch" — who leads, by how much,
                                   and the four cities chasing; shared on
                                   the 6th, 13th, 20th and 27th
    the last day                   the champion, published and shared on the
                                   1st once the month's final games are in

Each one carries the banner itself as its picture: the leader's (or the
champion's) banner drawn the way the page hangs it — month, year, city, total,
the running total in black and each team's share in its league color — beside
the top five cities in the race. That picture is what goes out on Bluesky.

The run is keyed on its reference date like every other analysis here, so a
day that isn't one of the five writes nothing and leaves the last run's folder
in place; publish_blog.py re-files that folder under its own date and changes
nothing. The last checkpoint is the 26th so that even February's falls before
its last day: every month gets four check-ins and a champion.

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
CHECKPOINTS = (5, 12, 19, 26)   # days of the month the race is reported on
CONTENDERS = 5                  # cities shown in the race
WEEK = 7                        # days behind each contender's trend arrow
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

    # who is moving: each contender's last seven days, kept inside the month
    week_lo = max(ref - timedelta(days=WEEK - 1), ref.replace(day=1)).strftime("%Y%m%d")
    standings = []
    for n, row in enumerate(cities[:CONTENDERS], 1):
        recent = group_games(by_team, row["group"], week_lo, ref.strftime("%Y%m%d"))
        standings.append({**entry(row, n),
                          "week": round(sum(g["weighted"] for g in recent), 4),
                          "back": round(leader["weighted"] - row["weighted"], 4)})

    # the year's rafters so far, this month's banner on the end once it is won
    won = {**past, ym: leader} if final else past
    rafters = [{"month": int(m[4:]), "city": c["group"]["city"],
                "weighted": round(c["weighted"], 4)}
               for m, c in sorted(won.items()) if m[:4] == ym[:4]]
    return {
        "reference_date": ref.isoformat(),
        "year": ref.year, "month": ref.month, "month_name": MONTH_NAMES[ref.month],
        "day": ref.day, "days": calendar.monthrange(ref.year, ref.month)[1],
        "final": final,
        "leader": {**entry(leader), "teams": series, "series": total},
        "runner_up": entry(runner_up) if runner_up else None,
        "margin": round(leader["weighted"] - runner_up["weighted"], 4) if runner_up else None,
        "standings": standings,
        "rafters": rafters,
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

# The page sets its banners in Oswald; a runner has no web fonts, so the two
# faces ship with the repo (both SIL Open Font License, see fonts/): Bebas Neue
# for the display type on the cloth, Barlow for everything that has to be read.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = {"display": "BebasNeue-Regular.ttf", "regular": "Barlow-Regular.ttf",
         "semi": "Barlow-SemiBold.ttf", "bold": "Barlow-Bold.ttf"}
SITE = "alexjf12.github.io/sports-win-index"


def signed(n: float, places: int = 1) -> str:
    n = 0.0 if abs(n) < 1e-9 else n
    return f"{n:+.{places}f}".replace("-", "−")


def record(r: dict) -> str:
    return f"{r['w']}-{r['l']}" + (f"-{r['t']}" if r["t"] else "")


def hex_rgb(color: str):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def render(data: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle

    faces = {k: os.path.join(FONT_DIR, v) for k, v in FONTS.items()}

    fig = plt.figure(figsize=(8, 5), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")
    renderer = fig.canvas.get_renderer()
    px_per_unit = fig.bbox.width / 16

    # --- the arena: house lights down, a warm wash from the catwalk, the brand
    # violet coming up off the floor, and two beams finding the banner
    yy, xx = np.mgrid[1:0:250j, 0:1:400j]
    base, mid = np.array(hex_rgb(ARENA)), np.array(hex_rgb(ARENA_2))
    img = base + (mid - base) * np.clip(1 - np.abs(yy - 0.45) / 0.55, 0, 1)[..., None]
    glow = np.exp(-(((xx - 0.21) / 0.28) ** 2 + ((yy - 1.0) / 0.45) ** 2))
    floor = np.exp(-(((xx - 0.62) / 0.5) ** 2 + ((yy + 0.05) / 0.32) ** 2))
    img += glow[..., None] * np.array([1.0, 0.9, 0.72]) * 0.13
    img += floor[..., None] * np.array([0.49, 0.25, 0.94]) * 0.24
    vignette = 1 - 0.35 * (((xx - 0.5) / 0.75) ** 2 + ((yy - 0.5) / 0.9) ** 2)
    ax.imshow(np.clip(img * vignette[..., None], 0, 1), extent=(0, 16, 0, 10),
              aspect="auto", zorder=0, interpolation="bicubic")
    for apex, foot in ((-1.5, (1.2, 4.6)), (8.2, (2.2, 5.6))):
        ax.add_patch(Polygon([(apex - 0.1, 10.2), (apex + 0.1, 10.2), (foot[1], 0.3),
                              (foot[0], 0.3)], color="#fff1d6", alpha=0.035, lw=0, zorder=0.5))

    def text(x, y, s, size, color, face="regular", ha="left", va="center", track=0, **kw):
        if track:   # letter-spaced caps, like the page's eyebrows
            s = (" " * track).join(s)
        return ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, zorder=6,
                       fontproperties=FontProperties(fname=faces[face]), **kw)

    def fitted(x, y, s, size, color, width, **kw):
        """Text shrunk to fit `width` units: "September" and "Minneapolis-St.
        Paul" get smaller, never clipped or run into the next thing."""
        t = text(x, y, s, size, color, **kw)
        drawn = t.get_window_extent(renderer).width
        if drawn > width * px_per_unit:
            t.set_fontsize(size * width * px_per_unit / drawn)
        return t

    leader, final = data["leader"], data["final"]
    month, city = data["month_name"], leader["city"]

    # --- the banner, hanging from its length of truss ---------------------------
    x0, x1, top_y, bottom_y, notch = 0.8, 6.0, 8.95, 0.42, 0.62
    cx = (x0 + x1) / 2
    tx0, tx1, t_lo, t_hi = x0 - 0.55, x1 + 0.55, 9.55, 9.82
    ax.add_patch(Rectangle((tx0, t_lo), tx1 - tx0, t_hi - t_lo, color="#16141d", zorder=1))
    xs_web = np.arange(tx0, tx1 + 1e-9, 0.27)
    ax.plot(np.repeat(xs_web, 1), [t_lo if i % 2 else t_hi for i in range(len(xs_web))],
            color=STEEL_HI, lw=0.9, zorder=1.1, solid_joinstyle="miter")
    for y in (t_lo, t_hi):
        ax.plot([tx0, tx1], [y, y], color="#8a8796", lw=1.6, zorder=1.2)
    ax.add_patch(Rectangle((tx0, t_lo - 0.09), tx1 - tx0, 0.09, color="#000000",
                           alpha=0.35, lw=0, zorder=1))
    for wx, lean in ((x0 + 0.9, 0.3), (x1 - 0.9, -0.3)):
        ax.plot([wx, wx + lean], [top_y + 0.16, t_lo], color="#c9c6d4", lw=0.8, zorder=1.5)
    ax.add_patch(FancyBboxPatch((x0 - 0.16, top_y + 0.04), x1 - x0 + 0.32, 0.13,
                                boxstyle="round,pad=0,rounding_size=0.065",
                                color="#8f8c9b", zorder=3.2))
    ax.add_patch(FancyBboxPatch((x0 - 0.12, top_y + 0.12), x1 - x0 + 0.24, 0.035,
                                boxstyle="round,pad=0,rounding_size=0.017",
                                color="#e2dfea", zorder=3.3))

    def outline(inset, dy=0.0, dx=0.0):
        return [(x0 + inset + dx, top_y - inset + dy), (x1 - inset + dx, top_y - inset + dy),
                (x1 - inset + dx, bottom_y + inset + dy),
                (cx + dx, bottom_y + notch + inset + dy), (x0 + inset + dx, bottom_y + inset + dy)]

    for spread, alpha in ((0.28, 0.10), (0.18, 0.16), (0.09, 0.22)):   # soft drop shadow
        ax.add_patch(Polygon(outline(-spread, dy=-0.16, dx=0.06), color="#000000",
                             alpha=alpha, lw=0, zorder=1.8))
    trim = Polygon(outline(0), color=PEWTER, lw=0, zorder=2)
    ax.add_patch(trim)
    sheen = np.linspace(0, 1, 64)[None, :] * np.ones((8, 1))
    trim_img = ax.imshow(
        np.array(hex_rgb(PEWTER_HI))
        + (np.array(hex_rgb(PEWTER_LO)) - np.array(hex_rgb(PEWTER_HI))) * sheen[..., None],
        extent=(x0, x1, bottom_y, top_y), aspect="auto", zorder=2.05,
        interpolation="bicubic")
    trim_img.set_clip_path(trim)
    felt = Polygon(outline(0.1), color=FELT, lw=0, zorder=2.1)
    ax.add_patch(felt)
    fy, fx = np.mgrid[1:0:200j, 0:1:120j]
    shade = (0.035 * fy                                                   # lit from above
             + 0.05 * np.exp(-((fx - fy * 0.55 - 0.05) / 0.12) ** 2)      # the sheen
             - 0.05 * (np.abs(fx - 0.5) * 2) ** 3)                        # edges falling away
    rng = np.random.default_rng(7)
    shade += rng.normal(0, 0.006, shade.shape)                            # a little nap
    felt_img = ax.imshow(np.clip(np.array(hex_rgb(FELT)) + shade[..., None] - 0.02, 0, 1),
                         extent=(x0, x1, bottom_y, top_y), aspect="auto", zorder=2.15,
                         interpolation="bicubic")
    felt_img.set_clip_path(felt)
    ax.add_patch(Polygon(outline(0.24), fill=False, ec=CLOTH_MUTED, lw=0.7,
                         ls=(0, (2.5, 2.5)), alpha=0.55, zorder=2.3))   # stitching

    text(cx, 8.48, "CHAMPIONS" if final else "LEADING", 9, CLOTH_MUTED, "semi",
         ha="center", track=3)
    ax.scatter([cx - 0.32, cx, cx + 0.32], [8.12] * 3, marker=(5, 1, 0), s=46,
               color=CLOTH_INK, lw=0, zorder=6)
    fitted(cx, 7.22, month.upper(), 60, CLOTH_INK, x1 - x0 - 1.0, face="display", ha="center")
    text(cx, 6.5, str(data["year"]), 22, CLOTH_MUTED, "display", ha="center")
    rule_y = 6.06
    if not final:
        ax.add_patch(FancyBboxPatch((cx - 0.88, 5.88), 1.76, 0.3,
                                    boxstyle="round,pad=0,rounding_size=0.15",
                                    color=CLOTH_INK, zorder=5))
        text(cx, 6.03, f"DAY {data['day']} OF {data['days']}", 7.5, FELT, "bold",
             ha="center", track=1)
        rule_y = 5.6
    for side in (-1, 1):
        ax.plot([cx + side * 0.18, cx + side * 1.7], [rule_y, rule_y], color=CLOTH_MUTED,
                lw=0.8, zorder=5)
    ax.scatter([cx], [rule_y], marker="D", s=10, color=CLOTH_MUTED, zorder=5)
    fitted(cx, rule_y - 0.52, city.upper(), 30, CLOTH_INK, x1 - x0 - 1.0, face="display",
           ha="center")
    text(cx, rule_y - 1.32, signed(leader["weighted"]), 38, CLOTH_INK, "display", ha="center")

    # the month so far: the group's running total in black over each team's share
    chart = fig.add_axes([(x0 + 0.85) / 16, 2.42 / 10, (x1 - x0 - 1.3) / 16, 1.3 / 10])
    chart.set_facecolor("none")
    days = data["days"]
    xs = list(range(len(leader["series"])))
    for team in leader["teams"]:
        if team["played"]:
            chart.plot(xs, team["series"], color=LEAGUE_COLOR[team["league"]], lw=1.4,
                       alpha=0.95, solid_capstyle="round", solid_joinstyle="round")
    chart.fill_between(xs, leader["series"], 0, color=CLOTH_INK, alpha=0.07, lw=0)
    chart.plot(xs, leader["series"], color=CLOTH_INK, lw=2.6, solid_capstyle="round",
               solid_joinstyle="round")
    chart.scatter([xs[-1]], [leader["series"][-1]], s=26, color=CLOTH_INK,
                  edgecolor=FELT, linewidth=1.4, zorder=5)
    chart.axhline(0, color=CLOTH_INK, alpha=0.28, lw=0.8, ls=(0, (3, 3)))
    if not final:   # the days still to play, so a half month never reads as a whole one
        chart.axvspan(xs[-1], days, color=CLOTH_INK, alpha=0.045, lw=0)
        chart.axvline(xs[-1], color=CLOTH_INK, alpha=0.3, lw=0.8, ls=(0, (2, 3)))
    chart.set_xlim(0, days)
    chart.set_xticks([1, 15, days])
    chart.tick_params(length=0, labelsize=6.5, colors=CLOTH_MUTED, pad=2)
    chart.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(3, integer=True))
    chart.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: "0" if v == 0 else signed(v, 0)))
    for label in chart.get_xticklabels() + chart.get_yticklabels():
        label.set_fontproperties(FontProperties(fname=faces["semi"], size=6.5))
    for spine in chart.spines.values():
        spine.set_visible(False)

    # the legend: a line per team, its record and its share
    for n, team in enumerate(leader["teams"]):
        y = 1.92 - n * 0.25
        ax.add_patch(FancyBboxPatch((x0 + 0.66, y - 0.065), 0.13, 0.13,
                                    boxstyle="round,pad=0,rounding_size=0.03",
                                    color=LEAGUE_COLOR[team["league"]], zorder=5))
        text(x0 + 0.9, y, team["league"].upper(), 7, CLOTH_MUTED, "semi")
        text(x0 + 1.32, y, team["nickname"], 8, CLOTH_INK, "semi")
        if team["played"]:
            text(x1 - 1.35, y, record(team), 7.5, CLOTH_MUTED, ha="right")
        text(x1 - 0.66, y, signed(team["weighted"]) if team["played"] else "off",
             8, CLOTH_INK if team["played"] else CLOTH_MUTED, "bold", ha="right")

    # --- the race beside it -------------------------------------------------------
    rx, rr = 7.05, 15.35
    ax.add_patch(Rectangle((rx, 9.05), 0.32, 0.05, color=ACCENT, lw=0, zorder=6))
    eyebrow = (f"FINAL  ·  {month.upper()} {data['year']}" if final
               else f"BANNER WATCH  ·  DAY {data['day']} OF {data['days']}")
    text(rx + 0.45, 9.08, eyebrow, 8.5, PEWTER, "semi", track=2)
    headline = (f"{city} takes the {month} banner" if final
                else f"{city} leads the {month} race").upper()
    fitted(rx - 0.03, 8.32, headline, 46, ARENA_INK, rr - rx, face="display")
    fitted(rx, 7.62, sub_line(data), 12, ARENA_MUTED, rr - rx)

    text(rx, 7.0, "FINAL STANDINGS" if final else "THE RACE", 8, PEWTER, "semi", track=2)
    text(rr, 7.0, "LAST 7 DAYS", 6.5, STEEL_HI, "semi", ha="right", track=2)
    rows = data["standings"]
    best = max([r["weighted"] for r in rows] + [1e-9])
    bar_x0, bar_x1 = rx + 4.15, rr - 2.4
    step = 0.8
    for n, row in enumerate(rows):
        y = 6.45 - n * step
        lead = n == 0
        if lead:   # the leader's row sits on a faint violet band
            ax.add_patch(FancyBboxPatch((rx - 0.15, y - step / 2 + 0.04), rr - rx + 0.3,
                                        step - 0.08, boxstyle="round,pad=0,rounding_size=0.08",
                                        color=ACCENT, alpha=0.12, lw=0, zorder=4))
        else:
            ax.plot([rx, rr], [y + step / 2, y + step / 2], color="#ffffff", alpha=0.07,
                    lw=0.6, zorder=4)
        text(rx + 0.22, y - 0.02, str(row["place"]), 30, ACCENT if lead else STEEL_HI,
             "display", ha="center")
        room = bar_x0 - rx - 0.85
        fitted(rx + 0.62, y + 0.12, row["city"], 13, ARENA_INK, room, face="bold")
        teams = row["label"].split(": ", 1)[-1]
        fitted(rx + 0.62, y - 0.17, f"{teams}  ·  {record(row)}", 7.5, ARENA_MUTED, room)

        # the bar, a groove the full width with the score filling it
        ax.add_patch(FancyBboxPatch((bar_x0, y - 0.07), bar_x1 - bar_x0, 0.14,
                                    boxstyle="round,pad=0,rounding_size=0.07",
                                    color="#ffffff", alpha=0.06, lw=0, zorder=5))
        length = (bar_x1 - bar_x0) * max(row["weighted"], 0) / best
        if length > 0.02:
            ax.add_patch(FancyBboxPatch((bar_x0, y - 0.07), length, 0.14,
                                        boxstyle="round,pad=0,rounding_size=0.07",
                                        color=ACCENT if lead else "#8b889a", lw=0, zorder=5.1))
        text(rr - 1.12, y, signed(row["weighted"]), 24,
             ARENA_INK if row["weighted"] >= 0 else LOSS, "display", ha="right")
        if not lead:
            text(bar_x1, y - 0.22, f"{signed(-row['back'])} back", 6.5, ARENA_MUTED,
                 "semi", ha="right")

        week = row["week"]
        up = week > 0.05
        flat = abs(week) <= 0.05
        color = ARENA_MUTED if flat else (WIN if up else LOSS)
        ax.scatter([rr - 0.88], [y], marker="o" if flat else ("^" if up else "v"),
                   s=16 if flat else 22, color=color, lw=0, zorder=6)
        text(rr, y, signed(week), 10, color, "bold", ha="right")

    # --- the year's rafters: who has hung a banner so far ---
    rafters = {r["month"]: r for r in data.get("rafters", [])}
    text(rx, 2.38, f"THE {data['year']} RAFTERS", 8, PEWTER, "semi", track=2)
    slot = (rr - rx) / 12
    pw, ph, pn = slot * 0.62, 0.62, 0.14
    for m in range(1, 13):
        px = rx + slot * (m - 0.5)
        top = 2.05
        shape = [(px - pw / 2, top), (px + pw / 2, top), (px + pw / 2, top - ph),
                 (px, top - ph + pn), (px - pw / 2, top - ph)]
        won = rafters.get(m)
        current = m == data["month"]
        ax.plot([px - pw / 2 - 0.04, px + pw / 2 + 0.04], [top + 0.03, top + 0.03],
                color="#8f8c9b", lw=1.6, solid_capstyle="round", zorder=5)
        if won:
            ax.add_patch(Polygon(shape, color=FELT, lw=0, zorder=5))
            if current:
                ax.add_patch(Polygon(shape, fill=False, ec=ACCENT, lw=1.6, zorder=5.2))
            text(px, top - 0.24, MONTH_NAMES[m][:3].upper(), 11, CLOTH_INK, "display",
                 ha="center")
            # staggered over two lines, so a name gets two slots of room
            fitted(px, 1.22 if m % 2 else 1.0, won["city"], 7.5,
                   ARENA_INK if current else ARENA_MUTED, slot * 1.9,
                   face="semi" if current else "regular", ha="center")
        else:
            ax.add_patch(Polygon(shape, fill=False, ec=ACCENT if current else STEEL,
                                 lw=1.0, ls=(0, (2, 2)) if current else "-", zorder=5))
            text(px, top - 0.24, MONTH_NAMES[m][:3].upper(), 11,
                 ACCENT if current else STEEL_HI, "display", ha="center")
            if current:
                text(px, 1.22 if m % 2 else 1.0, "in play", 7, ACCENT, "semi", ha="center")

    # --- the masthead's mark and the fine print ---
    for i, (h, color) in enumerate(((0.12, "#a5a49c"), (0.19, "#6f6e69"), (0.28, ACCENT))):
        ax.add_patch(FancyBboxPatch((rx + i * 0.12, 0.36), 0.08, h,
                                    boxstyle="round,pad=0,rounding_size=0.025",
                                    color=color, lw=0, zorder=6))
    text(rx + 0.45, 0.49, "BANNER MONTH", 13, ARENA_INK, "display", track=1)
    text(rr, 0.48, SITE, 7.5, ARENA_MUTED, "regular", ha="right")

    fig.savefig(path, dpi=200, facecolor=ARENA)
    plt.close(fig)


def sub_line(data: dict) -> str:
    """The line under the picture's headline: the margin, then what is left."""
    line = lead_line(data)
    if data["final"]:
        return f"{line}  ·  banner #{data['banner_number']} for {data['leader']['city']} since {data['since']}"
    left = data["days"] - data["day"]
    return f"{line}  ·  {left} day{'' if left == 1 else 's'} to play"


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
