"""Tests for fandom_analysis.py (the daily spotlight scorer)."""

from datetime import date

from fandom_analysis import (analyze_group, group_games, mtd_window,
                             month_range, recency_share, select_findings,
                             shrunk_percentile, slugify, tally,
                             trailing_streak)

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

def test_shrunk_percentile_midrank_with_pseudo_count():
    assert shrunk_percentile([1, 2, 3, 4], 5) == 4.5 / 5   # never fully 1.0
    assert shrunk_percentile([1, 2, 3, 4], 0) == 0.5 / 5   # never fully 0.0
    assert shrunk_percentile([1, 2, 3, 4], 3) == (2 + 0.5 + 0.5) / 5  # tie: half rank


def test_shrunk_percentile_small_samples_claim_less():
    # "best of 4" must be a weaker claim than "best of 50"
    assert shrunk_percentile([1] * 4, 2) < shrunk_percentile([1] * 50, 2)


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

def cand(city, name, score, weighted=0.0):
    return {"city": city, "name": name, "score": score,
            "month_totals": {"weighted": weighted}}


def test_select_findings_dedupes_city_and_ranks_by_score():
    cands = [
        cand("A", "A 1", 0.9),
        cand("A", "A 2", 0.8),   # same city, dropped
        cand("B", "B 1", 0.7),
        cand("C", "C 1", 0.6),
        cand("D", "D 1", 0.5),   # beyond top 3
    ]
    picked = select_findings(cands, 3)
    assert [c["name"] for c in picked] == ["A 1", "B 1", "C 1"]


def test_select_findings_breaks_score_ties_by_swing_size():
    cands = [
        cand("A", "A 1", 0.8, weighted=+4.0),
        cand("B", "B 1", 0.8, weighted=-20.0),   # same score, bigger swing
    ]
    assert [c["name"] for c in select_findings(cands, 1)] == ["B 1"]


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
    assert cand["basis"] == "all"       # only one prior March: no calendar lane
    assert cand["direction"] == "hot"
    assert cand["rank"] == 1                      # best month on record
    assert cand["since"] is None
    assert cand["percentile"] > 0.9
    assert cand["month_totals"]["w"] == 13
    # every game in a 14-day month within the last-7 window counts
    assert cand["last7"]["games"] == 7


def test_analyze_group_calendar_lane_wins_for_a_record_july():
    # Non-July months alternate hot/cold so the current month is mid-pack
    # overall, but past Julys were all terrible and this one is great:
    # the same-calendar-month lane should carry the story.
    months, records = [], []
    for y in (2022, 2023, 2024):
        for m in range(1, 13):
            months.append((y, m))
            records.append((2, 12) if m == 7 else
                           (12, 2) if m % 2 else (2, 12))
    rows = make_history(records, months)
    # current July: 8-6 with all six losses first, so the last 7 days are 7-0
    rows += [row(f"202507{d:02d}", "STL", 0, "CHC", 1, "CHC", gid=f"c{d}")
             for d in range(1, 7)]
    rows += [row(f"202507{d:02d}", "STL", 1, "CHC", 0, "STL", gid=f"c{d}")
             for d in range(7, 15)]
    cand = analyze_group(by_team_index(rows), GROUP, date(2025, 7, 14))
    assert cand is not None
    assert set(cand["comparisons"]) == {"all", "calendar"}
    assert cand["basis"] == "calendar"
    assert cand["direction"] == "hot"
    assert cand["rank"] == 1            # best July on record
    assert cand["since"] is None
    assert cand["comparisons"]["calendar"]["n_months"] == 3


def test_analyze_group_drops_hollow_calendar_claims():
    # Past Julys were awful, and this July is *less* awful but still losing:
    # "best July on record" on a losing month is hollow, so the calendar lane
    # must be discarded and the all-months lane (mildly cold) carries it.
    months, records = [], []
    for y in (2022, 2023, 2024):
        for m in range(1, 13):
            months.append((y, m))
            records.append((2, 12) if m == 7 else (7, 7))
    rows = make_history(records, months)
    rows += make_history([(6, 8)], [(2025, 7)])   # -4.5 weighted: still losing
    cand = analyze_group(by_team_index(rows), GROUP, date(2025, 7, 14))
    assert cand is not None
    assert "calendar" not in cand["comparisons"]
    assert cand["basis"] == "all"
    assert cand["direction"] == "cold"


def test_analyze_group_requires_history_and_games():
    # only 3 months of history: not enough to compare against
    months = [(2022, m) for m in (1, 2, 3)]
    rows = make_history([(7, 7)] * 3, months) + make_history([(10, 0)], [(2022, 4)])
    assert analyze_group(by_team_index(rows), GROUP, date(2022, 4, 14)) is None
