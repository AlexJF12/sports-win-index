"""Tests for the city-of-the-day form chart's data shape.

The drawing itself is plotnine's problem; what can quietly go wrong here is the
staircase behind it — the mapping from a team's game log onto a calendar where
most days have no game, some have two, and a tie has to leave the line alone.
"""

from datetime import date, timedelta

import pandas as pd

from render_daily import (field_alpha, form_frames, named_clause, run_label,
                          shape_reading, year_labels)

REF = date(2026, 8, 13)
DAYS = 30
START = REF - timedelta(days=DAYS - 1)


def game(day_offset, result):
    when = START + timedelta(days=day_offset)
    return {"date": when.strftime("%Y%m%d"), "result": result}


def team(nickname, log, league="mlb", longest=None, weighted=0.0):
    results = [g["result"] for g in log]
    return {"nickname": nickname, "league": league, "log": log,
            "w": results.count("W"), "l": results.count("L"),
            "weighted": weighted,
            "longest": longest or {"length": 1, "type": "W"}}


def prof(*teams, days=DAYS):
    return {"recent": {"days": days, "teams": list(teams)}}


def nets(line_df, label=None):
    rows = line_df if label is None else line_df[line_df["label"] == label]
    return list(rows["net"])


def test_the_line_holds_flat_across_days_with_no_game():
    fills, lines, ends, panels = form_frames(
        prof(team("Cards", [game(0, "W")])), REF)

    assert panels == 1
    # one win on the first day, then 29 days of nothing: the line stays at +1
    assert set(nets(lines)) == {0, 1}
    assert ends["net"].iloc[0] == 1
    # the fill covers every day from the win to the end of the window
    assert len(fills) == DAYS
    assert set(fills["sign"]) == {"up"}


def test_a_doubleheader_steps_twice_on_the_same_day():
    _, lines, ends, _ = form_frames(
        prof(team("Cards", [game(4, "W"), game(4, "W")])), REF)

    assert ends["net"].iloc[0] == 2
    assert 1 not in nets(lines)      # the intermediate value is never a day's value


def test_losses_fill_below_the_line_and_ties_do_not_move_it():
    fills, lines, ends, _ = form_frames(
        prof(team("Bears", [game(0, "L"), game(1, "T"), game(2, "L")],
                  league="nfl")), REF)

    assert ends["net"].iloc[0] == -2
    assert set(fills["sign"]) == {"down"}
    assert nets(lines)[:4] == [0, -1, -1, -1]   # start, then day one twice over


def test_a_day_back_at_500_draws_no_fill():
    fills, _, ends, _ = form_frames(
        prof(team("Cards", [game(0, "W"), game(1, "L")])), REF)

    assert ends["net"].iloc[0] == 0
    assert len(fills) == 1                      # only day one, at +1
    assert fills["x0"].iloc[0] == 0


def test_panels_keep_the_order_the_group_lists_its_teams_in():
    _, lines, _, panels = form_frames(
        prof(team("Cards", [game(0, "W")]),
             team("Bucks", [game(0, "L")], league="nba")), REF)

    assert panels == 2
    assert [c.split(" · ")[0] for c in lines["label"].cat.categories] == \
        ["Cards", "Bucks"]


def test_the_panel_heading_carries_the_record_the_weight_and_the_run():
    _, lines, _, _ = form_frames(
        prof(team("Cards", [game(0, "W"), game(1, "W")],
                  longest={"length": 2, "type": "W"}, weighted=4.5)), REF)

    assert lines["label"].cat.categories[0] == \
        "Cards · MLB · 2-0 · +4.5 weighted · longest run 2 wins"


def test_the_heading_weight_is_the_index_the_games_axis_cannot_show():
    """A 3-1 NFL month is +2 games and +42.9 weighted. The panel draws the
    first, so the heading has to carry the second."""
    _, lines, ends, _ = form_frames(
        prof(team("Bears", [game(0, "W"), game(3, "W"), game(10, "L"),
                            game(17, "W")], league="nfl", weighted=42.9)), REF)

    assert ends["net"].iloc[0] == 2                      # what the panel draws
    assert "+42.9 weighted" in lines["label"].cat.categories[0]


def test_run_label_survives_a_team_with_no_games():
    assert run_label({"length": 0, "type": None}) == "no games"


def test_the_shape_reading_only_claims_a_pattern_past_the_threshold():
    assert "clumpier" in shape_reading(2.4)
    assert "alternated" in shape_reading(-2.4)
    assert "coin flips" in shape_reading(1.9)
    assert "too few" in shape_reading(None)


# --- the past-year field on season.png and month.png -------------------------

def ends_frame(finals):
    return pd.DataFrame([{"year": str(2016 + i), "day": 300, "cum": v}
                         for i, v in enumerate(finals)])


def test_a_short_field_keeps_every_year_labelled():
    ends = ends_frame([10.0, -4.0, 30.0])
    assert list(year_labels(ends)["year"]) == ["2016", "2017", "2018"]
    assert named_clause([0] * 3) == ""


def test_a_deep_field_names_only_its_best_and_worst():
    """Ten labels land on top of each other at the right edge, so the field
    keeps its envelope named and drops the rest."""
    ends = ends_frame([10.0, -4.0, 30.0, 5.0, -20.0, 1.0, 2.0, 3.0, 4.0, 6.0])
    kept = year_labels(ends)

    assert list(kept["cum"]) == [30.0, -20.0]
    assert named_clause([0] * 10) == "; the best and worst are named"


def test_the_field_recedes_only_once_it_is_crowded():
    assert field_alpha(4) == 1.0
    assert field_alpha(10) < 1.0
