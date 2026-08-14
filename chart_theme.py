#!/usr/bin/env python3
"""
The look every chart in the repo shares: palette, theme, and the few layout
helpers more than one renderer needs.

This exists so the three renderers can look like one publication without
importing each other. It used to live in render_content.py, which meant that
drawing a city-of-the-day chart pulled in the whole weekly spotlight renderer
to get a hex code.

The palette is a validated light-mode set: a blue/red diverging pair against
warm-gray ink and grid tokens. Blue is the good pole and red the bad one
everywhere they appear, and gray is always context — earlier seasons, the
rest of the field, the band chance alone produces.
"""

import textwrap

from plotnine import (element_blank, element_line, element_rect, element_text,
                      theme, theme_minimal)

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

# day of year each month starts on, in a non-leap year, for the year charts
MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
MONTH_TICKS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CAPTION = ("weighted index: every game = 365 ÷ season length "
           "(MLB ±2.25, NBA/NHL ±4.45, NFL ±21.5) · records begin January 2010")

CROWDED_FIELD = 4       # more context lines than this and the field recedes
LABEL_GAP = 0.035       # minimum space between end labels, as a share of the
                        # y range — about one line of type at the label's size


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


def accent(value: float) -> str:
    """The pole a number belongs to."""
    return HOT if value >= 0 else COLD


def wrap(text: str, width: int) -> str:
    return textwrap.fill(text, width)


def spread_labels(ends, span: float, value: str = "cum", gap: float = LABEL_GAP):
    """Every line keeps its end label; the ones that would print on top of
    each other get nudged apart.

    Seasons finish in clusters — four of Baltimore's ten land within a few
    points of each other — and two labels at the same height do not read as a
    crowded pair, they overprint into glyph soup. So walk them from the top
    and push each one clear of the last by a minimum gap. A label is an
    identifier, not a mark: the line's own end still shows the exact value,
    and because the nudge only ever preserves the order it was sorted in, a
    reader can still tell which label belongs to which line by rank.

    Returns the frame with a `label_y` column to draw the text at.
    """
    ends = ends.sort_values(value, ascending=False).copy()
    min_gap = max(span, 1e-9) * gap
    placed = []
    for y in ends[value]:
        if placed and placed[-1] - y < min_gap:
            y = placed[-1] - min_gap
        placed.append(y)
    ends["label_y"] = placed
    return ends


def field_alpha(n: int) -> float:
    """Four context lines can be solid; ten have to recede or they compete
    with the one line the chart is actually about."""
    return 1.0 if n <= CROWDED_FIELD else 0.6
