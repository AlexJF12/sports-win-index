#!/usr/bin/env python3
"""
Render the daily fandom-spotlight findings as social-ready PNGs.

Reads content/YYYY-MM-DD/findings.json (written by fandom_analysis.py) and
produces three charts per finding, next to the JSON. Every finding gets the
race and teams charts; the middle one depends on what the detector found:

    <slug>_race.png     cumulative weighted index this month, all 88 groups as
                        a gray field with the featured group highlighted
    <slug>_teams.png    per-team weighted contribution, full month vs the last
                        7 days — the image that explains what happened this week

    <slug>_history.png  (month) the group's month-to-date weighted index for
                        every month on record — or every July on record when
                        the calendar lane won — current month highlighted
    <slug>_timeline.png (streak, turnaround) game by game, result-colored, with
                        the streak or the last-7-day flip picked out
    <slug>_bump.png     (climb) place in the year-to-date standings over the
                        last 30 days, featured group against the field

Charts are plotnine (grammar of graphics), 1600x900 at 2x, light surface.

Usage:
    python render_content.py                 # newest folder under content/
    python render_content.py --date 2026-07-16
"""

import argparse
import json
import logging
import os
import textwrap
from datetime import datetime

import pandas as pd
from plotnine import (aes, annotate, coord_flip, element_blank, expand_limits,
                      geom_col, geom_hline, geom_line, geom_point, geom_segment,
                      geom_step, geom_text, geom_vline, ggplot, labs,
                      position_dodge, scale_color_manual, scale_fill_manual,
                      scale_size_manual, scale_x_continuous, scale_x_date,
                      scale_y_reverse, theme)

from chart_theme import (BASELINE, CAPTION, COLD, FIELD, HOT, INK, INK_2,
                         MONTH_STARTS, MONTH_TICKS, MUTED, field_alpha,
                         spotlight_theme, spread_labels)
from fandom_analysis import pretty_month, run_word

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

CONTENT_DIR = os.path.join("content", "weekly")

TITLE_WRAP = 58      # characters before the title wraps to a second line
SUBTITLE_WRAP = 84


def accent(finding):
    return HOT if finding["direction"] == "hot" else COLD


def titles(finding, subtitle, title=None):
    """Wrap a chart's title and subtitle so neither runs off the canvas.

    The headline is the default, but only the lead chart of a finding should
    carry it: a finding renders two or three charts, and stacked on the blog
    the same sentence three times reads like a machine wrote the page. The
    supporting charts pass their own title saying what *they* add, the way the
    per-team chart always has ("Who's sinking Los Angeles"). Every subtitle
    names the group, so a chart lifted out on its own still stands up.
    """
    return {"title": textwrap.fill(title or finding["headline"], TITLE_WRAP),
            "subtitle": textwrap.fill(subtitle, SUBTITLE_WRAP)}


def month_name(data):
    return pretty_month(data["month"])


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
        + labs(**titles(finding, f"{finding['label']} vs every other city group — "
                                 f"cumulative weighted index, {month_name(data)}"),
               x=f"day of {month_name(data).split(' ')[0]}",
               caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def render_history(data, finding, path):
    """The comparison set behind the headline, current month highlighted: every
    month on record, or — when the calendar lane won — only this calendar
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
        first = min(hist["month"])[:4]
        title = f"Every {month_word} {finding['city']} has had since {first}"
        subtitle = (f"{finding['label']} — weighted index through day "
                    f"{data['cutoff_day']} of every {month_word} since {first}")
        # multiplicative room on the right: an additive pad measured in days
        # is a rounding error once the window is a decade wide, and the
        # current month's value label sits out there
        x_scale = scale_x_date(breaks=sorted(df["when"]), date_labels="%Y",
                               expand=(0.08, 0, 0.10, 0))
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
        first = min(hist["month"])[:4]
        title = f"Every month {finding['city']} has had since {first}"
        subtitle = (f"{finding['label']} — weighted index through day "
                    f"{data['cutoff_day']} of every month since {first}")
        # "%b %Y" every six months collides into unreadable overprints once
        # the window passes a few years; past that the year alone is enough
        span_years = df["when"].dt.year.nunique()
        ticks = ({"date_breaks": "1 year", "date_labels": "%Y"} if span_years > 4
                 else {"date_breaks": "6 months", "date_labels": "%b %Y"})
        x_scale = scale_x_date(expand=(0.02, 0, 0.10, 0), **ticks)
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
        + labs(**titles(finding, subtitle, title), caption=CAPTION)
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
    if not rows:        # every team is between seasons: nothing to draw
        log.warning("%s: no games this month, skipping the teams chart",
                    finding["slug"])
        return
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
        + labs(title=textwrap.fill(title, TITLE_WRAP),
               subtitle=textwrap.fill(f"{finding['label']} — weighted contribution, "
                                      f"{month_name(data)}", SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        + theme(panel_grid_major_y=element_blank(),
                axis_title_x=element_blank(), axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def render_timeline(data, finding, path):
    """Game by game: cumulative weighted index with result-colored points, the
    story's phase (the streak, or this week's flip) picked out from the rest."""
    games = finding["timeline"]
    lead = "streak" if finding["kind"] == "streak" else "late"
    df = pd.DataFrame([{**g, "n": i + 1} for i, g in enumerate(games)])
    df["cum"] = df["weighted"].cumsum().round(3)
    df["lead"] = df["phase"] == lead
    df["outcome"] = df["result"].map({"W": "Win", "L": "Loss", "T": "Tie"})
    ac = accent(finding)
    lead_df, rest_df = df[df["lead"]], df[~df["lead"]]
    # the phase line must connect to the preceding game, or it floats
    if not rest_df.empty and not lead_df.empty:
        lead_df = pd.concat([rest_df.tail(1), lead_df])

    if finding["kind"] == "streak":
        s = finding["streak"]
        word = run_word(s["type"])
        title = f"Where the run sits in {finding['city']}'s season"
        subtitle = (f"{finding['label']} — last {len(df)} games; the "
                    f"{s['length']} straight {word} in color")
    else:
        t = finding["turnaround"]
        title = f"The week that turned {finding['city']}'s month"
        subtitle = (f"{finding['label']} — every game this month; the last 7 "
                    f"days ({t['late']['w']}-{t['late']['l']}) in color")

    p = (
        ggplot()
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_step(rest_df, aes("n", "cum"), color=FIELD, size=1.0)
        + geom_step(lead_df, aes("n", "cum"), color=ac, size=1.6)
        + geom_point(df, aes("n", "cum", color="outcome", size="lead"), stroke=0)
        + geom_text(df.tail(1), aes("n", "cum", label="cum.map(lambda v: f'{v:+.1f}')"),
                    nudge_x=0.5, ha="left", va="center", color=INK, size=10,
                    fontweight="bold")
        + scale_color_manual(values={"Win": HOT, "Loss": COLD, "Tie": MUTED},
                             breaks=["Win", "Loss"])
        + scale_size_manual(values={True: 3.2, False: 1.8}, guide=None)
        + scale_x_continuous(expand=(0.02, 0, 0.08, 0.8))
        + labs(**titles(finding, subtitle, title), x="game", caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def render_bump(data, finding, path):
    """Place in the year-to-date standings over the last 30 days."""
    rows = [{"date": pd.to_datetime(r["date"]), "rank": r["rank"]}
            for r in finding["rank_series"]]
    df = pd.DataFrame(rows)
    ac = accent(finding)
    c = finding["climb"]
    week_ago = df.iloc[-1]["date"] - pd.Timedelta(days=7)

    p = (
        ggplot(df, aes("date", "rank"))
        + geom_line(color=ac, size=1.4, lineend="round")
        + geom_point(df[df["date"] >= week_ago], color=ac, size=2.2, stroke=0)
        + geom_point(df.tail(1), color=ac, size=4)
        + annotate("text", x=df.iloc[-1]["date"] + pd.Timedelta(days=1),
                   y=df.iloc[-1]["rank"], label=f"#{c['to']}", ha="left",
                   va="center", color=INK, size=10, fontweight="bold")
        + scale_y_reverse(expand=(0.10, 0))
        + scale_x_date(date_breaks="1 week", date_labels="%b %-d",
                       expand=(0.02, 0, 0.02, 2.5))
        # a lower rank number is a better place, so to < from is a climb
        + labs(**titles(finding, f"{finding['label']} — place among all {c['field']} "
                                 f"city groups on the {data['month'][:4]} weighted "
                                 f"index, last {len(df)} days",
                        f"{finding['city']} "
                        f"{'climbing' if c['to'] < c['from'] else 'sliding'} "
                        f"through the year standings"),
               caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title_x=element_blank(), axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


def render_year(data, finding, path):
    """This year against the group's own past years, day of year for day of
    year, with the same point in each year marked."""
    current = int(data["month"][:4])
    cutoff = (datetime.strptime(data["reference_date"], "%Y%m%d")
              .date().timetuple().tm_yday)
    rows = [{"year": str(entry["year"]), "day": point["day"], "cum": point["cum"],
             "current": entry["year"] == current}
            for entry in finding["year_series"] for point in entry["series"]]
    df = pd.DataFrame(rows)
    past, now = df[~df["current"]], df[df["current"]]
    ends = past.sort_values("day").groupby("year", observed=True).tail(1)
    ends = spread_labels(ends, df["cum"].max() - df["cum"].min())
    # where each past year stood on this same date — the comparison the
    # headline is actually making. Pinned to the cutoff rather than to each
    # year's last game before it, so they line up on one date
    marks = (past[past["day"] <= cutoff].sort_values("day")
                 .groupby("year", observed=True).tail(1).assign(day=cutoff))
    end = now.iloc[-1]
    ac = accent(finding)

    p = (
        ggplot()
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_vline(xintercept=cutoff, color=BASELINE, size=0.4, linetype="dashed")
        + geom_line(past, aes("day", "cum", group="year"), color=FIELD, size=0.7,
                    alpha=field_alpha(past["year"].nunique()))
        + geom_point(marks, aes("day", "cum"), color=MUTED, size=2.2, stroke=0)
        + geom_text(ends, aes("day", "label_y", label="year"), ha="left",
                    va="center", nudge_x=4, color=MUTED, size=7.5)
        + geom_line(now, aes("day", "cum"), color=ac, size=1.5, lineend="round")
        + geom_point(now.tail(1), aes("day", "cum"), color=ac, size=3, stroke=0)
        + annotate("text", x=end["day"] + 4, y=end["cum"],
                   label=f"{current}: {end['cum']:+.1f}", ha="left", va="center",
                   color=INK, size=9, fontweight="bold")
        + scale_x_continuous(breaks=MONTH_STARTS, labels=MONTH_TICKS,
                             expand=(0.01, 0, 0.09, 0))
        + labs(**titles(finding, f"{finding['label']} — every game since January 1 "
                                 f"added up. The dots mark where each year stood on "
                                 f"this date."),
               caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title=element_blank())
    )
    p.save(path, verbose=False)


def render_field(data, finding, path):
    """Where this year's index sits among all the city groups."""
    values = finding["field_values"]
    df = pd.DataFrame({"place": range(1, len(values) + 1), "weighted": values})
    place = finding["year"]["place"]
    mine = pd.DataFrame([{"place": place,
                          "weighted": finding["year"]["totals"]["weighted"]}])
    ac = accent(finding)

    p = (
        ggplot()
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_point(df, aes("place", "weighted"), color=FIELD, size=2, stroke=0)
        + geom_point(mine, aes("place", "weighted"), color=ac, size=4.5, stroke=0)
        + annotate("text", x=place + len(values) * 0.02,
                   y=mine["weighted"].iloc[0],
                   label=f"#{place} of {finding['year']['field']}", ha="left",
                   va="center", color=INK, size=9, fontweight="bold")
        + scale_x_continuous(breaks=[1, 25, 50, 75, len(values)],
                             expand=(0.02, 0, 0.10, 0))
        + labs(**titles(finding, f"{finding['label']} — every city group's "
                                 f"{data['month'][:4]} weighted index, best to "
                                 f"worst. Metros with several teams appear once "
                                 f"per combination.",
                        f"Where {finding['city']} sits in the field"),
               x="place on the year", caption=CAPTION)
        + spotlight_theme()
        + theme(axis_title_y=element_blank())
    )
    p.save(path, verbose=False)


RENDERERS = {"race": render_race, "history": render_history,
             "teams": render_teams, "timeline": render_timeline,
             "bump": render_bump, "year": render_year, "field": render_field}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Content folder date (YYYY-MM-DD). Defaults to the newest folder.")
    parser.add_argument("--content-dir", default=CONTENT_DIR)
    args = parser.parse_args()

    if os.path.exists(os.path.join(args.content_dir, "findings.json")):
        folder = args.content_dir              # a run folder, written in place
    elif args.date:
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

    for finding in data["findings"]:
        for image in finding["images"]:
            kind = image.rsplit("_", 1)[-1].removesuffix(".png")
            RENDERERS[kind](data, finding, os.path.join(folder, image))
        log.info("Rendered %s (%s)", finding["slug"], finding["headline"])

    # the folder is written in place, so last run's cards would otherwise
    # pile up next to this one's
    wanted = {image for f in data["findings"] for image in f["images"]}
    for name in sorted(os.listdir(folder)):
        if name.endswith(".png") and name not in wanted:
            os.remove(os.path.join(folder, name))
            log.info("Removed stale %s", name)
    if not data["findings"]:
        log.info("No findings in %s — nothing to render.", folder)
    log.info("Done: %s", folder)


if __name__ == "__main__":
    main()
