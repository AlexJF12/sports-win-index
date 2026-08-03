"""Tests for streakiness.py (the runs-test measure and the two panels)."""

from datetime import date

from streakiness import (longest_run, measure, month_panel, one_per_city,
                         season_panel, streak_index, usual)


def row(d, away, home, winner, gid, league="mlb"):
    return {"date": d, "league": league, "game_id": gid, "away_team": away,
            "away_score": "1", "home_team": home, "home_score": "0",
            "winner": winner, "status": "Final"}


def by_team_index(rows):
    by_team = {}
    for r in rows:
        for abbr in (r["away_team"], r["home_team"]):
            by_team.setdefault((r["league"], abbr), []).append(r)
    return by_team


GROUP = {"name": "Testville 1", "city": "Testville",
         "teams": [{"league": "mlb", "abbr": "STL", "nickname": "Cards"}]}


def games(results, start=date(2026, 7, 1)):
    """One STL game per day, with the given results."""
    rows = []
    for i, r in enumerate(results):
        d = (start.toordinal() + i)
        ds = date.fromordinal(d).strftime("%Y%m%d")
        winner = {"W": "STL", "L": "CHC", "T": ""}[r]
        rows.append(row(ds, "STL", "CHC", winner, gid=str(i)))
    return rows


# --- the measure -------------------------------------------------------------

def test_alternating_sequence_is_negative():
    """W-L-W-L is the least streaky order there is."""
    assert streak_index(list("WLWLWLWLWL")) < -2


def test_blocked_sequence_is_positive():
    """The same record in two blocks is the streakiest order there is."""
    assert streak_index(list("WWWWWLLLLL")) > 2


def test_index_ignores_win_rate():
    """Same shape, different records: both read as one clean flip."""
    balanced = streak_index(list("WWWWWLLLLL"))
    lopsided = streak_index(list("WWWWWWWWLL"))
    assert balanced > 2 and lopsided > 2


def test_degenerate_sequences_have_no_index():
    assert streak_index(list("WWWWWW")) is None      # never lost
    assert streak_index(list("WLLLLL")) is None      # one win only
    assert streak_index([]) is None


def test_ties_are_dropped_not_counted_as_a_result():
    assert streak_index(list("WWTTLL")) == streak_index(list("WWLL"))


def test_longest_run_takes_the_longest_block():
    assert longest_run(list("WWLLLWW")) == {"type": "L", "length": 3}
    assert longest_run(list("WWLLLWWW")) == {"type": "L", "length": 3}  # ties: first
    assert longest_run([]) == {"type": "", "length": 0}


def test_longest_run_is_not_broken_by_a_tie_free_sequence():
    assert longest_run(list("LLLL")) == {"type": "L", "length": 4}


# --- measurement over real game rows -----------------------------------------

def test_measure_splits_season_month_and_history():
    rows = (games(list("WWWWLLLL"), start=date(2026, 7, 26))     # last 30 days
            + games(list("WLWLWLWL"), start=date(2026, 1, 6))    # earlier in 2026
            + games(list("WWLL"), start=date(2025, 5, 1)))       # a prior season
    m = measure(by_team_index(rows), GROUP, date(2026, 8, 2))

    assert m["season"]["games"] == 16          # both 2026 stretches
    assert m["month"]["games"] == 8            # only the July 26+ stretch
    assert m["month"]["results"] == list("WWWWLLLL")
    assert m["month"]["index"] > 2
    assert m["history"] == []                  # 4 games in 2025 is under the floor


def test_measure_month_window_is_30_days_not_the_calendar_month():
    rows = games(list("WWLL"), start=date(2026, 7, 8))   # 26 days before the ref
    m = measure(by_team_index(rows), GROUP, date(2026, 8, 2))
    assert m["month"]["games"] == 4


# --- panel selection ---------------------------------------------------------

def measured(name, city, season_index, history, month_index=0.0, month_games=20):
    return {"name": name, "city": city, "label": f"{city}: Team",
            "season": {"year": 2026, "games": 120, "wins": 60, "losses": 60,
                       "index": season_index, "longest": {"type": "W", "length": 4}},
            "month": {"games": month_games, "wins": 10, "losses": 10,
                      "index": month_index, "longest": {"type": "W", "length": 3},
                      "results": list("WL") * (month_games // 2)},
            "history": [{"year": 2022 + i, "games": 120, "index": v}
                        for i, v in enumerate(history)]}


def test_usual_is_the_median_past_season():
    assert usual(measured("A 1", "A", 0.0, [-1.0, 0.5, 3.0])) == 0.5


def test_one_per_city_keeps_only_the_strongest_permutation():
    rows = [measured("New York 1", "New York", 1.0, [0, 0, 0]),
            measured("New York 2", "New York", 2.5, [0, 0, 0]),
            measured("Boston", "Boston", 0.5, [0, 0, 0])]
    kept = one_per_city(rows, key=lambda r: r["season"]["index"])
    assert [r["name"] for r in kept] == ["New York 2", "Boston"]


def test_season_panel_ranks_by_distance_from_the_group_s_own_normal():
    rows = [
        # a +2.0 season is unremarkable for a group that always runs streaky
        measured("Always 1", "Always", 2.0, [1.9, 2.0, 2.1]),   # distance 0.0
        # the same +2.0 is a departure for a group that never has
        measured("Never 1", "Never", 2.0, [-0.5, -0.4, -0.3]),
        measured("Middle 1", "Middle", 0.6, [0.1, 0.2, 0.3]),
    ]
    panel = season_panel(rows, top_n=1)      # returned sorted by index, for the chart
    assert [r["name"] for r in panel] == ["Middle 1", "Never 1"]


def test_season_panel_skips_groups_without_enough_history():
    rows = [measured("Thin 1", "Thin", 3.0, [0.0]),
            measured("Deep 1", "Deep", 1.0, [0.0, 0.0, 0.0])]
    assert [r["name"] for r in season_panel(rows, top_n=2)] == ["Deep 1"]


def test_month_panel_takes_both_extremes():
    rows = [measured(f"City{i} 1", f"City{i}", 0.0, [0, 0, 0], month_index=i)
            for i in range(-4, 5)]
    panel = month_panel(rows, per_side=2)
    assert [r["month"]["index"] for r in panel] == [4, 3, -3, -4]


def test_month_panel_skips_groups_that_barely_played():
    rows = [measured("Quiet 1", "Quiet", 0.0, [0, 0, 0], month_index=9,
                     month_games=4),
            measured("Busy 1", "Busy", 0.0, [0, 0, 0], month_index=1)]
    assert [r["name"] for r in month_panel(rows, per_side=1)] == ["Busy 1"]
