#!/usr/bin/env python3
"""
The two streakiness charts. Called by streakiness.py, which does the counting.

    season_vs_history.png   dot plot: this season's streak index against the
                            same group's 2022-2025 seasons
    past_month.png          win/loss tiles, one row per fandom, last 30 days

Palette and theme come from render_content.py so every image in the repo looks
like the same publication. Gray is the neutral here (prior seasons, the chance
band) and blue the highlighted current season; on the tile chart blue and red
keep their usual meaning — win and loss.
"""

import textwrap

import pandas as pd
from plotnine import (aes, coord_flip, element_blank, element_text, geom_hline,
                      geom_point, geom_segment, geom_text, geom_tile, ggplot,
                      labs, scale_color_manual, scale_fill_manual,
                      scale_x_continuous, scale_y_discrete, theme)

from render_content import (BASELINE, COLD, GRID, HOT, INK, INK_2, MUTED,
                            spotlight_theme)

CHANCE_BAND = 2.0    # |index| below this is ordinary sampling noise
TITLE_WRAP = 40      # characters before the headline wraps (bold 14pt)
SUBTITLE_WRAP = 68
LABEL_WRAP = 34
CAPTION = ("streak index: Wald-Wolfowitz runs test over the sequence of games the "
           "group's teams actually played, sign-flipped so bigger = clumpier · "
           "records begin January 2022")


def group_label(label: str) -> str:
    """'New York: Mets/Nets/Islanders/Jets' onto two lines for an axis tick.
    Nicknames are spaced out before shortening so a long four-team group loses
    its last team rather than collapsing to a bare ellipsis."""
    city, teams = label.split(": ", 1)
    spaced = teams.replace("/", " / ")
    return f"{city}\n{textwrap.shorten(spaced, LABEL_WRAP, placeholder='…')}"


def render_season(panel: list, ref, path: str) -> None:
    """Each group's season so far against its own past seasons."""
    first_year = min(h["year"] for r in panel for h in r["history"])
    now_series = f"{ref.year} so far"
    past_series = f"{first_year}–{ref.year - 1} seasons"

    current, history = [], []
    for r in panel:
        label = group_label(r["label"])
        current.append({"label": label, "index": r["season"]["index"],
                        "series": now_series})
        for h in r["history"]:
            history.append({"label": label, "index": h["index"],
                            "series": past_series})
    df, hist = pd.DataFrame(current), pd.DataFrame(history)
    order = df["label"].tolist()          # panel arrives sorted by index
    for frame in (df, hist):
        frame["label"] = pd.Categorical(frame["label"], categories=order)

    span = (hist.groupby("label", observed=True)["index"]
                .agg(["min", "max"]).reset_index())
    limit = max(df["index"].abs().max(), hist["index"].abs().max()) * 1.3

    p = (
        ggplot()
        # the band is drawn per row rather than as one rect: the x scale here
        # is discrete, so a rect spanning it can't be positioned numerically
        + geom_segment(df, aes(x="label", xend="label"), y=-CHANCE_BAND,
                       yend=CHANCE_BAND, color=GRID, size=13)
        + geom_hline(yintercept=0, color=BASELINE, size=0.4)
        + geom_segment(span, aes(x="label", xend="label", y="min", yend="max"),
                       color=MUTED, size=0.5, alpha=0.45)
        + geom_point(hist, aes("label", "index", color="series"), size=2.2, stroke=0)
        + geom_point(df, aes("label", "index", color="series"), size=4.0, stroke=0)
        + geom_text(df, aes("label", "index",
                            label="index.map(lambda v: f'{v:+.1f}')"),
                    nudge_y=0.5, color=INK, size=8, fontweight="bold")
        + scale_color_manual(values={now_series: HOT, past_series: MUTED},
                             breaks=[now_series, past_series])
        + coord_flip(ylim=(-limit, limit))
        + labs(title=textwrap.fill("A few fandoms are living a season that doesn't "
                                   "feel like their others", TITLE_WRAP),
               subtitle=textwrap.fill(
                   f"Streak index through {ref.strftime('%B %-d, %Y')} against the "
                   "same city group's earlier seasons. Left: wins and losses take "
                   "turns. Right: they arrive in runs. Zero is exactly as clumped "
                   "as coin flips; the gray band is the range chance alone "
                   "produces.", SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 5.6),         # ten two-line row labels need the room
                axis_title=element_blank(),
                axis_text_y=element_text(size=8, linespacing=1.3),
                panel_grid_major_y=element_blank())
    )
    p.save(path, verbose=False)


def render_month(panel: list, ref, path: str) -> None:
    """The past 30 days as tiles: one row per fandom, one tile per game."""
    rows, ends, order = [], [], []
    for i, r in enumerate(panel):
        label = group_label(r["label"])
        if i == len(panel) // 2:                 # a blank row splits the halves
            order.append(" ")
        order.append(label)
        for n, result in enumerate(r["month"]["results"], 1):
            rows.append({"label": label, "n": n,
                         "outcome": {"W": "Win", "L": "Loss"}.get(result, "Tie")})
        run = r["month"]["longest"]
        word = "wins" if run["type"] == "W" else "losses"
        ends.append({"label": label, "n": r["month"]["games"] + 1.6,
                     "text": f"{r['month']['wins']}-{r['month']['losses']}   "
                             f"longest run: {run['length']} {word}"})
    df, end_df = pd.DataFrame(rows), pd.DataFrame(ends)
    for frame in (df, end_df):
        frame["label"] = pd.Categorical(frame["label"], categories=order[::-1])

    p = (
        ggplot()
        + geom_tile(df, aes("n", "label", fill="outcome"), width=0.86, height=0.6)
        + geom_text(end_df, aes("n", "label", label="text"), ha="left",
                    color=INK_2, size=7)
        + scale_fill_manual(values={"Win": HOT, "Loss": COLD, "Tie": MUTED},
                            breaks=["Win", "Loss"])
        + scale_x_continuous(limits=(0.4, df["n"].max() + 13), expand=(0, 0))
        # drop=False keeps the empty spacer level, which is the gap between halves
        + scale_y_discrete(drop=False)
        + labs(title=textwrap.fill("Same month, different weather", TITLE_WRAP),
               subtitle=textwrap.fill(
                   "Every game these fandoms played in the last 30 days, in order — "
                   "the three streakiest of the month, then the three steadiest, "
                   f"through {ref.strftime('%B %-d, %Y')}.", SUBTITLE_WRAP),
               caption=CAPTION)
        + spotlight_theme()
        + theme(figure_size=(8, 5.0),
                axis_title=element_blank(), axis_text_x=element_blank(),
                axis_text_y=element_text(size=8, linespacing=1.3),
                panel_grid_major=element_blank(), panel_grid_minor=element_blank())
    )
    p.save(path, verbose=False)
