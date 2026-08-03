#!/usr/bin/env python3
"""
The two city-of-the-day charts. Called by city_of_the_day.py.

    season.png   cumulative weighted index this year against the same group's
                 earlier seasons, day of year for day of year
    games.png    the last 30 days as win/loss tiles, one row per team

Palette and theme come from render_content.py so every image in the repo looks
like the same publication: earlier seasons in gray, this one in color.
"""

import textwrap

import pandas as pd
from plotnine import (aes, element_blank, element_text, geom_hline, geom_line,
                      geom_point, geom_text, geom_tile, ggplot, labs,
                      scale_fill_manual, scale_x_continuous, scale_y_discrete,
                      theme)

from render_content import (BASELINE, CAPTION, COLD, FIELD, HOT, INK, INK_2,
                            MONTH_STARTS, MONTH_TICKS, MUTED, spotlight_theme)

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


def render_games(prof: dict, ref, path: str) -> None:
    """The last 30 days, game by game, one row per team that played."""
    teams = prof["recent"]["teams"]
    rows, ends, order = [], [], []
    for team in teams:
        label = f"{team['nickname']}\n{team['league'].upper()}"
        order.append(label)
        for n, result in enumerate(team["results"], 1):
            rows.append({"label": label, "n": n,
                         "outcome": {"W": "Win", "L": "Loss"}.get(result, "Tie")})
        run = team["longest"]
        word = "wins" if run["type"] == "W" else "losses"
        ends.append({"label": label, "n": len(team["results"]) + 1.6,
                     "text": f"{team['w']}-{team['l']}   longest run: "
                             f"{run['length']} {word}"})
    df, end_df = pd.DataFrame(rows), pd.DataFrame(ends)
    for frame in (df, end_df):
        frame["label"] = pd.Categorical(frame["label"], categories=order[::-1])

    r = prof["recent"]
    tie = f", {r['t']} tied" if r["t"] else ""
    index = r["index"]
    if index is None:
        shape = "too few of both results to read the order"
    elif index >= 2:
        shape = f"the results arrived clumpier than chance ({index:+.1f})"
    elif index <= -2:
        shape = f"the results alternated more than chance ({index:+.1f})"
    else:
        shape = f"the order is about what coin flips produce ({index:+.1f})"

    p = (
        ggplot()
        + geom_tile(df, aes("n", "label", fill="outcome"), width=0.86, height=0.4)
        + geom_text(end_df, aes("n", "label", label="text"), ha="left",
                    color=INK_2, size=7.5)
        + scale_fill_manual(values={"Win": HOT, "Loss": COLD, "Tie": MUTED},
                            breaks=["Win", "Loss"])
        + scale_x_continuous(limits=(0.4, df["n"].max() + 10), expand=(0, 0))
        + scale_y_discrete(drop=False)
        + labs(title=textwrap.fill(f"The last {r['days']} days in {prof['city']}",
                                   TITLE_WRAP),
               subtitle=textwrap.fill(
                   f"{r['w']}-{r['l']}{tie}, {r['weighted']:+.1f} weighted — {shape}. "
                   f"One tile per game, in order, through "
                   f"{ref.strftime('%B %-d, %Y')}.", SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        # in midsummer only the MLB team is playing, so the panel has to
        # look composed at one row as well as at four
        + theme(figure_size=(8, 2.1 + 0.45 * len(teams)),
                axis_title=element_blank(), axis_text_x=element_blank(),
                axis_text_y=element_text(size=8, linespacing=1.3),
                panel_grid_major=element_blank(), panel_grid_minor=element_blank())
    )
    p.save(path, verbose=False)
