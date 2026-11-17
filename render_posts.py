#!/usr/bin/env python3
"""
The charts for the four post types in post_types.py. One picture each, in the
same palette and theme as every other chart in the repo (chart_theme.py): blue
for the good pole, red for the bad one, gray for context.
"""

import textwrap

import pandas as pd
from plotnine import (aes, coord_flip, element_blank, element_text, geom_col, geom_hline,
                      geom_line, geom_point, geom_text, ggplot, labs,
                      scale_fill_identity, scale_x_continuous, scale_x_discrete,
                      scale_y_continuous, theme)

from chart_theme import (BASELINE, CAPTION, COLD, FIELD, HOT, INK, INK_2,
                         MONTH_STARTS, MONTH_TICKS, MUTED, accent, clear_of,
                         field_alpha, spotlight_theme, spread_labels)

TITLE_WRAP = 44
SUBTITLE_WRAP = 72
RUN_CAPTION = "a run is every game by every team in the group, in the order played"


def render_hotcold(hot: list, cold: list, ref, path: str) -> None:
    """Winning runs to the right in blue, losing runs to the left in red."""
    rows = ([{"city": r["city"], "n": r["length"], "fill": HOT,
              "text": f"{r['city']}  W{r['length']}", "ha": "left"} for r in hot]
            + [{"city": r["city"], "n": -r["length"], "fill": COLD,
                "text": f"L{r['length']}  {r['city']}", "ha": "right"} for r in cold])
    df = pd.DataFrame(rows)
    # a city can hold one of each (two groups), so the bar's identity is its row
    df["row"] = [f"{n:02d}" for n in range(len(df))]
    order = list(df.sort_values("n")["row"])
    reach = max(abs(df["n"]))
    right, left = df[df["n"] > 0], df[df["n"] < 0]
    p = (
        ggplot(df, aes("row", "n"))
        + geom_col(aes(fill="fill"), width=0.62)
        + geom_hline(yintercept=0, color=BASELINE, size=0.5)
        + geom_text(right, aes(label="text"), ha="left", nudge_y=reach * 0.03,
                    color=INK, size=9, fontweight="bold")
        + geom_text(left, aes(label="text"), ha="right", nudge_y=-reach * 0.03,
                    color=INK, size=9, fontweight="bold")
        + scale_fill_identity()
        + scale_x_discrete(limits=order)
        + scale_y_continuous(limits=(-reach * 1.75, reach * 1.75),
                             breaks=[], labels=[])
        + coord_flip()
        + labs(title="Who's hot, who's not",
               subtitle=textwrap.fill(
                   f"The longest active winning and losing runs, one per city, "
                   f"through {ref.strftime('%B %-d, %Y')}.", SUBTITLE_WRAP),
               caption=RUN_CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 4.6), axis_title=element_blank(),
                axis_text=element_blank(), panel_grid_major=element_blank())
    )
    p.save(path, verbose=False)


def render_h2h(series: list, ref, path: str) -> None:
    """Two cities' years on one axis. The leader takes the blue; the other is
    ink — two cities are not a good pole and a bad one."""
    colors = [HOT, INK_2]
    frames, ends = [], []
    for row, color in zip(series, colors):
        df = pd.DataFrame(row["series"])
        df["city"], df["color"] = row["city"], color
        frames.append(df)
        last = df.iloc[-1]
        ends.append({"day": last["day"], "cum": last["cum"], "color": color,
                     "label": f"  {row['city']} {last['cum']:+.1f}"})
    ends = pd.DataFrame(ends)
    both = pd.concat(frames)
    span = both["cum"].max() - both["cum"].min()
    # the two end labels sit at the same x; keep them a line apart
    if abs(ends.loc[0, "cum"] - ends.loc[1, "cum"]) < span * 0.06:
        hi = ends["cum"].idxmax()
        ends["label_y"] = ends["cum"]
        ends.loc[hi, "label_y"] += span * 0.03
        ends.loc[1 - hi, "label_y"] -= span * 0.03
    else:
        ends["label_y"] = ends["cum"]

    p = ggplot() + geom_hline(yintercept=0, color=BASELINE, size=0.4)
    for df, color in zip(frames, colors):
        p = (p + geom_line(df, aes("day", "cum"), color=color, size=1.4, lineend="round")
             + geom_point(df.tail(1), aes("day", "cum"), color=color, size=3, stroke=0))
    for _, end in ends.iterrows():
        p = p + geom_text(pd.DataFrame([end]), aes("day", "label_y", label="label"),
                          ha="left", va="center", color=end["color"], size=9,
                          fontweight="bold")
    a, b = series[0]["city"], series[1]["city"]
    p = (
        p
        + scale_x_continuous(breaks=MONTH_STARTS, labels=MONTH_TICKS,
                             limits=(1, max(both["day"].max() + 75, 120)),
                             expand=(0.01, 0, 0.01, 0))
        + labs(title=textwrap.fill(f"{a} vs {b}, {ref.year} so far", TITLE_WRAP),
               subtitle=textwrap.fill(
                   f"Every game by each city's teams since January 1 added up, "
                   f"through {ref.strftime('%B %-d')}.", SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 4.6), axis_title=element_blank())
    )
    p.save(path, verbose=False)


def render_timemachine(top: list, month: str, year: int, path: str) -> None:
    """That month's top five, the banner winner in blue."""
    df = pd.DataFrame(top)
    df["fill"] = [HOT] + [FIELD] * (len(df) - 1)
    df["text"] = [f"  {r['weighted']:+.1f}  ({r['record']})" for r in top]
    order = list(df["city"])[::-1]
    reach = df["weighted"].max()
    p = (
        ggplot(df, aes("city", "weighted"))
        + geom_col(aes(fill="fill"), width=0.62)
        + geom_text(aes(label="text"), ha="left", color=INK, size=9)
        + scale_fill_identity()
        + scale_x_discrete(limits=order)
        + scale_y_continuous(limits=(0, reach * 1.3), breaks=[], labels=[])
        + coord_flip()
        + labs(title=f"{month} {year}: {top[0]['city']}'s banner",
               subtitle="The top five cities that month on the weighted index, "
                        "with their records.",
               caption=CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 4.2), axis_title=element_blank(),
                axis_text_x=element_blank(), panel_grid_major=element_blank(),
                axis_text_y=element_text(color=INK, size=10, weight="bold"))
    )
    p.save(path, verbose=False)


def render_guess(prof: dict, ref, path: str) -> None:
    """render_daily.render_season with the name taken off: the same lines, and
    nothing on the image that says whose they are."""
    past = pd.DataFrame([{"year": str(p["year"]), **point}
                         for p in prof["past_seasons"] for point in p["series"]])
    now = pd.DataFrame(prof["season"]["series"])
    end = now.iloc[-1]
    color = accent(end["cum"])
    ends = past.sort_values("day").groupby("year", observed=True).tail(1)
    reach = pd.concat([past["cum"], now["cum"]])
    ends = spread_labels(ends, reach.max() - reach.min())
    ends = clear_of(ends, end["day"], end["cum"], reach.max() - reach.min(),
                    before=15, after=45)
    n = len(prof["teams"])
    p = (
        ggplot()
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_line(past, aes("day", "cum", group="year"), color=FIELD, size=0.7,
                    alpha=field_alpha(len(prof["past_seasons"])))
        + geom_text(ends, aes("day", "label_y", label="year"), ha="left",
                    va="center", nudge_x=4, color=MUTED, size=7.5)
        + geom_line(now, aes("day", "cum"), color=color, size=1.5, lineend="round")
        + geom_point(now.tail(1), aes("day", "cum"), color=color, size=3, stroke=0)
        + geom_text(now.tail(1), aes("day", "cum",
                                     label=f"'  {ref.year}: {end['cum']:+.1f}'"),
                    ha="left", va="center", color=INK, size=9, fontweight="bold")
        + scale_x_continuous(breaks=MONTH_STARTS, labels=MONTH_TICKS,
                             expand=(0.01, 0, 0.09, 0))
        + labs(title="Guess the city",
               subtitle=textwrap.fill(
                   f"One city, {n} team{'' if n == 1 else 's'}: its {ref.year} "
                   f"through {ref.strftime('%B %-d')}, against its own "
                   f"{len(prof['past_seasons'])} earlier seasons in gray.",
                   SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 4.6), axis_title=element_blank())
    )
    p.save(path, verbose=False)
