#!/usr/bin/env python3
"""
The two city-of-the-day charts. Called by city_of_the_day.py.

    season.png   cumulative weighted index this year against the same group's
                 earlier seasons, day of year for day of year
    form.png     the last 30 days as each team's running games over .500

Palette and theme come from render_content.py so every image in the repo looks
like the same publication: earlier seasons in gray, this one in color.
"""

import logging
import textwrap
from datetime import timedelta

import pandas as pd
from plotnine import (aes, element_blank, element_rect, element_text,
                      facet_wrap, geom_hline, geom_line, geom_path, geom_point,
                      geom_rect, geom_text, ggplot, labs, scale_fill_manual,
                      scale_x_continuous, scale_y_continuous, theme)

from render_content import (BASELINE, CAPTION, COLD, FIELD, HOT, INK, INK_2,
                            MONTH_STARTS, MONTH_TICKS, MUTED, SURFACE,
                            spotlight_theme)

log = logging.getLogger(__name__)

TITLE_WRAP = 40
SUBTITLE_WRAP = 68


def accent(value: float) -> str:
    return HOT if value >= 0 else COLD


def render_season(prof: dict, ref, path: str) -> None:
    """This year's cumulative index against the group's earlier seasons."""
    past = pd.DataFrame([{"year": str(p["year"]), **point}
                         for p in prof["past_seasons"] for point in p["series"]])
    now = pd.DataFrame(prof["season"]["series"])
    end = now.iloc[-1]
    color = accent(end["cum"])
    ends = (past.sort_values("day").groupby("year", observed=True).tail(1))

    p = (
        ggplot()
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_line(past, aes("day", "cum", group="year"), color=FIELD, size=0.7)
        + geom_text(ends, aes("day", "cum", label="year"), ha="left", va="center",
                    nudge_x=4, color=MUTED, size=7.5)
        + geom_line(now, aes("day", "cum"), color=color, size=1.5, lineend="round")
        + geom_point(now.tail(1), aes("day", "cum"), color=color, size=3, stroke=0)
        + geom_text(now.tail(1), aes("day", "cum",
                                     label=f"'  {ref.year}: {end['cum']:+.1f}'"),
                    ha="left", va="center", color=INK, size=9, fontweight="bold")
        + scale_x_continuous(breaks=MONTH_STARTS, labels=MONTH_TICKS,
                             expand=(0.01, 0, 0.09, 0))
        + labs(title=textwrap.fill(f"{prof['city']}'s {ref.year}, against "
                                   f"{prof['city']}'s other years", TITLE_WRAP),
               subtitle=textwrap.fill(
                   f"{prof['label']} — every game since January 1 added up, "
                   f"through {ref.strftime('%B %-d')}. Earlier seasons run to the "
                   "end of their year.", SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 4.6), axis_title=element_blank())
    )
    p.save(path, verbose=False)


def shape_reading(index: float | None) -> str:
    """How the order of the results sits against chance, in a phrase."""
    if index is None:
        return "too few of both results to read the order"
    if index >= 2:
        return f"the results arrived clumpier than chance ({index:+.1f})"
    if index <= -2:
        return f"the results alternated more than chance ({index:+.1f})"
    return f"the order is about what coin flips produce ({index:+.1f})"


def run_label(run: dict) -> str:
    if not run["length"]:
        return "no games"
    word = "wins" if run["type"] == "W" else "losses"
    return f"longest run {run['length']} {word}"


def form_frames(prof: dict, ref) -> tuple:
    """The staircase behind the chart: for each team, its running games over
    .500 on every day of the window.

    The series runs day by day rather than game by game so the x axis is real
    time — an off day holds the line flat, a doubleheader steps twice, and the
    rows of a four-team city line up on the same calendar. A day's value is
    where the team stood once that day's games were in.
    """
    days = prof["recent"]["days"]
    start = ref - timedelta(days=days - 1)
    step_for = {"W": 1, "L": -1, "T": 0}
    fills, lines, ends, order = [], [], [], []

    for team in prof["recent"]["teams"]:
        label = (f"{team['nickname']} · {team['league'].upper()} · "
                 f"{team['w']}-{team['l']} · {run_label(team['longest'])}")
        order.append(label)
        by_day = {}
        for game in team["log"]:
            offset = (pd.Timestamp(game["date"]).date() - start).days
            by_day.setdefault(offset, []).append(game["result"])

        net = 0
        lines.append({"label": label, "x": 0, "net": 0})   # start the line at .500
        for offset in range(days):
            for result in by_day.get(offset, []):
                net += step_for[result]
            if net:                        # one filled step per day off .500
                fills.append({"label": label, "x0": offset, "x1": offset + 1,
                              "net": net, "sign": "up" if net > 0 else "down"})
            # two points per day trace the tread and the riser of the stair
            lines.append({"label": label, "x": offset, "net": net})
            lines.append({"label": label, "x": offset + 1, "net": net})
        ends.append({"label": label, "x": days, "net": net})

    frames = [pd.DataFrame(rows) for rows in (fills, lines, ends)]
    for frame in frames:
        if not frame.empty:
            frame["label"] = pd.Categorical(frame["label"], categories=order)
    return (*frames, len(order))


def render_form(prof: dict, ref, path: str) -> None:
    """The last 30 days as form: each team's running games over .500.

    The reader's question — how did the month actually go — is a shape over
    time with a natural baseline, so this is a line against .500 rather than a
    strip of colored tiles. The staircase carries the same sequence the tiles
    did (every step up is a win, every step down a loss) and adds the dates,
    the size of the swings, and where the runs fell; one panel per team keeps
    the two colors meaning above and below .500 and nothing else, so the fill
    never has to stand in for identity.
    """
    fill_df, line_df, end_df, n_teams = form_frames(prof, ref)
    if not n_teams:     # a forced --city whose teams are all between seasons
        log.warning("%s has played nobody in the window, skipping the form chart",
                    prof["city"])
        return
    r = prof["recent"]
    days = r["days"]
    tie = f", {r['t']} tied" if r["t"] else ""

    # one scale for every panel, in games, so a four-team city's rows are read
    # against each other and not each against its own private ruler. The range
    # is the data's, padded to at least a game either side of .500 — a team
    # that never strayed far gets a flat line, not a dramatic-looking panel
    lo = min(-1, int(line_df["net"].min()))
    hi = max(1, int(line_df["net"].max()))
    span = hi - lo
    step = 1 if span <= 6 else 2 if span <= 12 else 3 if span <= 18 else 5
    y_breaks = [v for v in range(lo, hi + 1) if v % step == 0]
    x_breaks = [b for b in (0, 7, 14, 21, days - 1) if b < days]
    x_labels = [(ref - timedelta(days=days - 1 - b)).strftime("%b %-d")
                for b in x_breaks]

    p = (
        ggplot()
        # 0.55 keeps the two poles apart where it counts — mixed into the
        # surface they are #88b3e7 and #ee9a99, ΔE 11.1 protan and 17.0 to
        # normal vision — without letting a month spent above .500 land as a
        # saturated slab. Position carries the same reading regardless: the
        # fill is above or below a drawn, labelled baseline
        + geom_rect(fill_df, aes(xmin="x0", xmax="x1", ymin=0, ymax="net",
                                 fill="sign"), alpha=0.55)
        + geom_hline(yintercept=0, color=BASELINE, size=0.5)
        + geom_path(line_df, aes("x", "net", group="label"), color=INK_2, size=0.8)
        + geom_point(end_df, aes("x", "net"), color=INK, size=2.4, stroke=0)
        + scale_fill_manual(values={"up": HOT, "down": COLD}, guide=None)
        + scale_x_continuous(breaks=x_breaks, labels=x_labels,
                             limits=(0, days), expand=(0.01, 0, 0.02, 0))
        + scale_y_continuous(breaks=y_breaks, limits=(lo, hi),
                             labels=[f"{v:+d}" if v else "0" for v in y_breaks])
        + facet_wrap("label", ncol=1)
        + labs(title=textwrap.fill(f"The last {days} days in {prof['city']}",
                                   TITLE_WRAP),
               subtitle=textwrap.fill(
                   f"{r['w']}-{r['l']}{tie}, {r['weighted']:+.1f} weighted — "
                   f"{shape_reading(r['index'])}. Each row is one team's record "
                   f"against .500 through {ref.strftime('%B %-d, %Y')}: every "
                   f"step up is a win, every step down a loss.", SUBTITLE_WRAP),
               y="games over .500", caption=CAPTION)
        + spotlight_theme()
        # in midsummer only the MLB team is playing, so the figure has to look
        # composed at one panel as well as at four
        + theme(figure_size=(8, 2.2 + 1.05 * n_teams),
                axis_title_x=element_blank(),
                axis_title_y=element_text(color=MUTED, size=8),
                panel_grid_major_x=element_blank(),
                panel_spacing=0.05,
                strip_background=element_rect(fill=SURFACE, color=SURFACE),
                strip_text=element_text(color=INK_2, size=8.5, ha="left"))
    )
    p.save(path, verbose=False)
