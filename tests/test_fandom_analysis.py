"""Tests for fandom_analysis.py (the daily spotlight scorer)."""

from datetime import date

from fandom_analysis import (analyze_group, group_games, mtd_window,
                             month_range, percentile, recency_share,
                             select_findings, slugify, tally, trailing_streak)

MLB_W = 365 / 162


def row(d, away, ascore, home, hscore, winner, gid="1", league="mlb"):
    return {"date": d, "league": league, "game_id": gid, "away_team": away,
            "away_score": str(ascore), "home_team": home, "home_score": str(hscore),
            "winner": winner, "status": "Final"}


def by_team_index(rows):
    by_team = {}
    for r in rows:
        for abbr in (r["away_team"], r["home_team"]):
            by_team.setdefault((r["league"], abbr), []).append(r)
    return by_team


GROUP = {"name": "Testville 1", "city": "Testville",
         "teams": [{"league": "mlb", "abbr": "STL", "nickname": "Cards"}]}


# --- windows -----------------------------------------------------------------

def test_mtd_window_caps_cutoff_at_month_length():
    assert mtd_window(2024, 2, 31) == ("20240201", "20240229")  # leap year
    assert mtd_window(2023, 2, 31) == ("20230201", "20230228")
    assert mtd_window(2026, 7, 15) == ("20260701", "20260715")


def test_month_range_excludes_end():
    months = list(month_range((2022, 11), (2023, 2)))
    assert months == [(2022, 11), (2022, 12), (2023, 1)]


# --- tallying ----------------------------------------------------------------

def test_group_games_and_tally():
    rows = [
        row("20260701", "STL", 5, "CHC", 3, "STL", gid="a"),
        row("20260702", "CHC", 9, "STL", 1, "CHC", gid="b"),
        row("20260710", "STL", 2, "MIL", 2, "", gid="c"),      # tie
        row("20260801", "STL", 4, "CHC", 0, "STL", gid="d"),   # outside window
    ]
    games = group_games(by_team_index(rows), GROUP, "20260701", "20260731")
    t = tally(games)
    assert (t["w"], t["l"], t["t"], t["games"]) == (1, 1, 1, 3)
    assert abs(t["weighted"]) < 0.01  # one win cancels one loss


# --- scoring pieces ----------------------------------------------------------

def test_percentile_midrank():
    assert percentile([1, 2, 3, 4], 5) == 1.0
    assert percentile([1, 2, 3, 4], 0) == 0.0
    assert percentile([1, 2, 3, 4], 3) == (2 + 0.5) / 4  # tie takes half rank


def test_recency_share_clips_and_zeroes_opposite_sign():
    assert recency_share(10.0, 5.0) == 0.5
    assert recency_share(10.0, 15.0) == 1.0   # week bigger than month: clip
    assert recency_share(10.0, -3.0) == 0.0   # week points the other way
    assert recency_share(0.0, 3.0) == 0.0


def test_trailing_streak():
    games = [{"result": r} for r in ["L", "W", "W", "W"]]
    assert trailing_streak(games) == {"type": "W", "length": 3}
    games = [{"result": r} for r in ["W", "L", "L"]]
    assert trailing_streak(games) == {"type": "L", "length": 2}
    assert trailing_streak([{"result": "T"}]) is None
    assert trailing_streak([]) is None


def test_slugify_collapses_punctuation():
    assert slugify("St. Louis 2") == "st-louis-2"


# --- selection ---------------------------------------------------------------

def test_select_findings_dedupes_city_and_ranks_by_score():
    cands = [
        {"city": "A", "name": "A 1", "score": 0.9},
        {"city": "A", "name": "A 2", "score": 0.8},   # same city, dropped
        {"city": "B", "name": "B 1", "score": 0.7},
        {"city": "C", "name": "C 1", "score": 0.6},
        {"city": "D", "name": "D 1", "score": 0.5},   # beyond top 3
    ]
    picked = select_findings(cands, 3)
    assert [c["name"] for c in picked] == ["A 1", "B 1", "C 1"]


# --- end to end on synthetic data --------------------------------------------

def make_history(monthly_records, year_month_pairs):
    """One game per day 1..N per month; monthly_records[i] = (wins, losses)."""
    rows = []
    gid = 0
    for (y, m), (w, l) in zip(year_month_pairs, monthly_records):
        for i in range(w + l):
            gid += 1
            winner = "STL" if i < w else "CHC"
            rows.append(row(f"{y:04d}{m:02d}{i + 1:02d}", "STL", 1, "CHC", 0,
                            winner, gid=str(gid)))
    return rows


def test_analyze_group_flags_a_historically_hot_month():
    # 14 mediocre months (7-7), then a current month running 13-1
    months = [(2022, m) for m in range(1, 13)] + [(2023, 1), (2023, 2)]
    rows = make_history([(7, 7)] * 14, months)
    rows += make_history([(13, 1)], [(2023, 3)])
    cand = analyze_group(by_team_index(rows), GROUP, date(2023, 3, 14))
    assert cand is not None
    assert cand["direction"] == "hot"
    assert cand["rank"] == 1                      # best month on record
    assert cand["since"] is None
    assert cand["percentile"] > 0.9
    assert cand["month_totals"]["w"] == 13
    # every game in a 14-day month within the last-7 window counts
    assert cand["last7"]["games"] == 7


def test_analyze_group_requires_history_and_games():
    # only 3 months of history: not enough to compare against
    months = [(2022, m) for m in (1, 2, 3)]
    rows = make_history([(7, 7)] * 3, months) + make_history([(10, 0)], [(2022, 4)])
    assert analyze_group(by_team_index(rows), GROUP, date(2022, 4, 14)) is None
