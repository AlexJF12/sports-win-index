#!/usr/bin/env python3
"""
Render the daily fandom-spotlight findings as social-ready PNGs.

Reads content/YYYY-MM-DD/findings.json (written by fandom_analysis.py) and
produces three charts per finding, next to the JSON:

    <slug>_race.png     cumulative weighted index this month, all 88 groups as
                        a gray field with the featured group highlighted
    <slug>_history.png  the group's month-to-date weighted index for every
                        month since 2022, current month highlighted — the
                        "best month since X" receipt
    <slug>_teams.png    per-team weighted contribution, full month vs the last
                        7 days — the image that explains what happened this week

Charts are plotnine (grammar of graphics), 1600x900 at 2x, light surface.

Usage:
    python render_content.py                 # newest folder under content/
    python render_content.py --date 2026-07-16
"""

import argparse
import json
import logging
import os

import pandas as pd
from plotnine import (aes, annotate, coord_flip, element_blank, element_line,
                      element_rect, element_text, expand_limits, geom_col,
                      geom_hline, geom_line, geom_point, geom_segment,
                      geom_text, ggplot, guides, labs, position_dodge,
                      scale_color_manual, scale_fill_manual,
                      scale_x_continuous, scale_x_date, theme, theme_minimal)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

CONTENT_DIR = "content"

# Palette (validated light-mode set: blue/red diverging pair, ink/grid tokens)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
HOT = "#2a78d6"     # blue pole — historically good
COLD = "#e34948"    # red pole — historically bad
FIELD = "#c3c2b7"   # de-emphasized context lines

CAPTION = "weighted index: every game = 365 ÷ season length (MLB ±2.25, NBA/NHL ±4.45, NFL ±21.5)"
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def spotlight_theme():
    return theme_minimal(base_size=11) + theme(
        figure_size=(8, 4.5),
        dpi=200,
        plot_background=element_rect(fill=SURFACE, color=None),
        panel_background=element_rect(fill=SURFACE, color=None),
        panel_grid_major=element_line(color=GRID, size=0.4),
        panel_grid_minor=element_blank(),
        axis_ticks=element_blank(),
        text=element_text(color=INK_2),
        axis_text=element_text(color=MUTED, size=8),
        axis_title=element_text(color=INK_2, size=9),
        plot_title=element_text(color=INK, size=14, weight="bold", ha="left"),
        plot_subtitle=element_text(color=INK_2, size=10, ha="left"),
        plot_caption=element_text(color=MUTED, size=6.5, ha="right"),
        plot_margin=0.03,
        legend_position="top",
        legend_direction="horizontal",
        legend_title=element_blank(),
        legend_text=element_text(color=INK_2, size=9),
        legend_background=element_rect(fill=SURFACE, color=SURFACE),
        legend_frame=element_blank(),
        legend_key=element_rect(fill=SURFACE, color=SURFACE),
    )


def accent(finding):
    return HOT if finding["direction"] == "hot" else COLD


def month_name(data):
    y, m = data["month"].split("-")
    return f"{MONTHS[int(m)]} {y}"


def render_race(data, finding, path):
    """Featured group vs the field: cumulative weighted index this month."""
    days = data["race"]["days"]
    rows = []
    for name, series in data["race"]["series"].items():
        for d, v in zip(days, series):
            rows.append({"name": name, "day": d, "cum": v,
                         "featured": name == finding["name"]})
    df = pd.DataFrame(rows)
    field, feat = df[~df["featured"]], df[df["featured"]]
    end = feat[feat["day"] == days[-1]].iloc[0]

    p = (
        ggplot()
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_line(field, aes("day", "cum", group="name"),
                    color=FIELD, size=0.4, alpha=0.45)
        + geom_line(feat, aes("day", "cum"), color=accent(finding), size=1.4,
                    lineend="round")
        + geom_point(feat[feat["day"] == days[-1]], aes("day", "cum"),
                     color=accent(finding), size=2.5, stroke=0.7, fill=accent(finding))
        + annotate("text", x=end["day"] + 0.4, y=end["cum"],
                   label=f"{end['cum']:+.1f}", ha="left", va="center",
                   color=INK, size=10, fontweight="bold")
        + scale_x_continuous(breaks=[d for d in days if d % 7 == 1],
                             expand=(0.01, 0, 0.06, 1.2))
        + labs(title=finding["headline"],
               subtitle=f"{finding['label']} vs every other city group — "
                        f"cumulative weighted index, {month_name(data)}",
               x=f"day of {month_name(data).split(' ')[0]}",
               caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def render_history(data, finding, path):
    """The comparison set behind the headline, current month highlighted: every
    month since 2022, or — when the calendar lane won — only this calendar
    month across years (July vs past Julys)."""
    hist = pd.DataFrame(finding["history"])
    calendar_only = finding.get("basis") == "calendar"
    if calendar_only:
        hist = hist[hist["calendar_month"] == int(data["month"].split("-")[1])]
    hist["when"] = pd.to_datetime(hist["month"] + "-01")
    hist["current"] = False
    cur = pd.DataFrame([{
        "month": data["month"],
        "when": pd.to_datetime(data["month"] + "-01"),
        "weighted": finding["month_totals"]["weighted"],
        "current": True,
    }])
    df = pd.concat([hist[["month", "when", "weighted", "current"]], cur])
    df["sign"] = df["weighted"].map(lambda v: "up" if v >= 0 else "down")
    ac = accent(finding)
    past, current = df[~df["current"]], df[df["current"]]
    if calendar_only:
        # a handful of same-month years: lollipops (stem + dot), year ticks
        month_word = month_name(data).split(" ")[0]
        subtitle = (f"{finding['label']} — weighted index through day "
                    f"{data['cutoff_day']} of every {month_word} since 2022")
        x_scale = scale_x_date(breaks=sorted(df["when"]), date_labels="%Y",
                               expand=(0.08, 0, 0.02, 110))
        layers = [
            geom_segment(past, aes(x="when", xend="when", y=0, yend="weighted",
                                   color="sign"), size=2.0, alpha=0.35),
            geom_point(past, aes("when", "weighted", color="sign"),
                       size=3.5, alpha=0.35),
            geom_segment(current, aes(x="when", xend="when", y=0, yend="weighted"),
                         color=ac, size=2.4),
            geom_point(current, aes("when", "weighted"), color=ac, size=5),
        ]
    else:
        subtitle = (f"{finding['label']} — weighted index through day "
                    f"{data['cutoff_day']} of every month since 2022")
        x_scale = scale_x_date(date_breaks="6 months", date_labels="%b %Y",
                               expand=(0.02, 0, 0.02, 130))
        layers = [
            geom_segment(past, aes(x="when", xend="when", y=0, yend="weighted",
                                   color="sign"), size=1.6, alpha=0.35,
                         lineend="round"),
            geom_segment(current, aes(x="when", xend="when", y=0, yend="weighted"),
                         color=ac, size=2.2, lineend="round"),
            geom_point(current, aes("when", "weighted"), color=ac, size=3),
        ]

    p = ggplot(df, aes("when", "weighted")) + geom_hline(yintercept=0, color=BASELINE, size=0.4)
    for layer in layers:
        p = p + layer
    p = (
        p
        + annotate("text", x=cur["when"].iloc[0], y=cur["weighted"].iloc[0],
                   label=f"  {cur['weighted'].iloc[0]:+.1f}", ha="left",
                   va="bottom" if cur["weighted"].iloc[0] >= 0 else "top",
                   color=INK, size=10, fontweight="bold")
        + scale_color_manual(values={"up": HOT, "down": COLD}, guide=None)
        + x_scale
        + expand_limits(y=[v * 1.15 for v in (df["weighted"].min(), df["weighted"].max())])
        + labs(title=finding["headline"], subtitle=subtitle, caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title_x=element_blank(), axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def render_teams(data, finding, path):
    """Who did it: per-team weighted contribution, month vs last 7 days."""
    rows = []
    for t in finding["per_team"]:
        if not t["month"]["games"]:     # off-season team, nothing to show
            continue
        team_label = f"{t['nickname']} ({t['month']['w']}-{t['month']['l']})"
        rows.append({"team": team_label, "period": "Full month",
                     "weighted": t["month"]["weighted"]})
        rows.append({"team": team_label, "period": "Last 7 days",
                     "weighted": t["last7"]["weighted"]})
    df = pd.DataFrame(rows)
    # keep group order: biggest absolute month contribution at the top after flip
    order = (df[df["period"] == "Full month"]
             .sort_values("weighted", key=abs)["team"].tolist())
    df["team"] = pd.Categorical(df["team"], categories=order)
    df["period"] = pd.Categorical(df["period"], categories=["Full month", "Last 7 days"])
    labels = df.copy()
    labels["ha"] = labels["weighted"].map(lambda v: "left" if v >= 0 else "right")
    pad = max(df["weighted"].abs().max(), 1) * 0.03
    labels["y"] = labels["weighted"] + labels["weighted"].map(
        lambda v: pad if v >= 0 else -pad)

    title = (f"Who's carrying {finding['city']}" if finding["direction"] == "hot"
             else f"Who's sinking {finding['city']}")
    # thin marks even when few teams are in season (July = MLB only)
    n_teams = df["team"].nunique()
    bar_w = {1: 0.16, 2: 0.3}.get(n_teams, 0.4)
    p = (
        ggplot(df, aes("team", "weighted", fill="period"))
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_col(position=position_dodge(width=bar_w * 2.4), width=bar_w)
        + geom_text(labels, aes("team", "y", label="weighted.map(lambda v: f'{v:+.1f}')",
                                ha="ha", group="period"),
                    position=position_dodge(width=bar_w * 2.4), color=INK_2, size=8)
        + scale_fill_manual(values={"Full month": accent(finding),
                                    "Last 7 days": BASELINE})
        + expand_limits(y=[v * 1.18 for v in (min(df["weighted"].min(), 0),
                                              max(df["weighted"].max(), 0))])
        + coord_flip()
        + labs(title=title,
               subtitle=f"{finding['label']} — weighted contribution, {month_name(data)}",
               caption=CAPTION)
        + spotlight_theme()
        + theme(panel_grid_major_y=element_blank(),
                axis_title_x=element_blank(), axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Content folder date (YYYY-MM-DD). Defaults to the newest folder.")
    parser.add_argument("--content-dir", default=CONTENT_DIR)
    args = parser.parse_args()

    if args.date:
        d = args.date
        if len(d) == 8 and d.isdigit():        # accept YYYYMMDD too
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        folder = os.path.join(args.content_dir, d)
    else:
        dated = sorted(d for d in os.listdir(args.content_dir)
                       if os.path.isdir(os.path.join(args.content_dir, d)))
        if not dated:
            raise SystemExit(f"No dated folders under {args.content_dir}/")
        folder = os.path.join(args.content_dir, dated[-1])

    with open(os.path.join(folder, "findings.json")) as f:
        data = json.load(f)
    if not data["findings"]:
        log.info("No findings in %s — nothing to render.", folder)
        return

    for finding in data["findings"]:
        slug = finding["slug"]
        render_race(data, finding, os.path.join(folder, f"{slug}_race.png"))
        render_history(data, finding, os.path.join(folder, f"{slug}_history.png"))
        render_teams(data, finding, os.path.join(folder, f"{slug}_teams.png"))
        log.info("Rendered %s (%s)", slug, finding["headline"])
    log.info("Done: %s", folder)


if __name__ == "__main__":
    main()
