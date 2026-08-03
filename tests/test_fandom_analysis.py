"""Tests for fandom_analysis.py (the daily spotlight scorer)."""

from datetime import date, timedelta

from fandom_analysis import (detect_climb, detect_month, detect_streak,
                             detect_turnaround, detect_year, group_context,
                             group_games, ytd_totals, ytd_window,
                             longest_prior_run, mtd_window, month_range,
                             novelty_factor, recency_share, select_findings,
                             shrunk_percentile, slugify, tally,
                             trailing_streak, ytd_rank_series)

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


def analyze_group(by_team, group, ref, lanes=("all", "calendar")):
    """The month detector, with its shared context built — the shape the tests
    were written against."""
    return detect_month(group_context(by_team, group, ref), lanes)


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

def cand(city, name, score, weighted=0.0, kind="month"):
    return {"city": city, "name": name, "score": score, "kind": kind,
            "month_totals": {"weighted": weighted}}


def test_select_findings_dedupes_city_and_ranks_by_score():
    cands = [
        cand("A", "A 1", 0.9),
        cand("A", "A 2", 0.85),  # same city, dropped
        cand("B", "B 1", 0.7),
        cand("C", "C 1", 0.5),
        cand("D", "D 1", 0.3),   # beyond top 3
    ]
    picked = select_findings(cands, 3, seed=None)
    assert [c["name"] for c in picked] == ["A 1", "B 1", "C 1"]


def test_select_findings_breaks_score_ties_by_swing_size():
    cands = [
        cand("A", "A 1", 0.8, weighted=+4.0),
        cand("B", "B 1", 0.8, weighted=-20.0),   # same score, bigger swing
    ]
    assert select_findings(cands, 1, seed=None)[0]["name"] == "B 1"


def test_select_findings_jitter_only_shuffles_near_ties():
    close = [cand("A", "A 1", 0.80), cand("B", "B 1", 0.79)]
    far = [cand("A", "A 1", 0.90), cand("B", "B 1", 0.50)]
    close_winners = {select_findings(close, 1, seed=str(d))[0]["name"]
                     for d in range(40)}
    far_winners = {select_findings(far, 1, seed=str(d))[0]["name"]
                   for d in range(40)}
    assert close_winners == {"A 1", "B 1"}, "near-ties should vary by day"
    assert far_winners == {"A 1"}, "a clear winner must not be jittered away"


def test_select_findings_caps_repeats_of_one_kind():
    cands = [cand("A", "A 1", 0.9), cand("B", "B 1", 0.8), cand("C", "C 1", 0.7),
             cand("D", "D 1", 0.2, kind="streak")]
    picked = select_findings(cands, 3, seed=None)
    assert [c["kind"] for c in picked] == ["month", "month", "streak"], (
        "a third month card should give way to the only other kind available")


def test_select_findings_cools_down_recently_featured_cities():
    cands = [cand("A", "A 1", 0.9), cand("B", "B 1", 0.7)]
    # A featured yesterday: 0.9 * 0.30 = 0.27, below B's 0.7
    assert select_findings(cands, 1, recent={"A": 1}, seed=None)[0]["name"] == "B 1"
    # A featured a week ago — the gap between two weekly runs: still damped
    assert select_findings(cands, 1, recent={"A": 7}, seed=None)[0]["name"] == "B 1"
    # A featured three weeks ago: no penalty, A wins again
    assert select_findings(cands, 1, recent={"A": 21}, seed=None)[0]["name"] == "A 1"


def test_novelty_factor_ramps_back_over_three_weeks():
    assert novelty_factor(1) < novelty_factor(7) < novelty_factor(20) < 1.0
    assert novelty_factor(21) == novelty_factor(99) == novelty_factor(None) == 1.0


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


# --- detectors: streak, turnaround, climb ------------------------------------

def test_longest_prior_run_ignores_the_live_tail():
    games = [{"result": r} for r in "WWWLWWWWW"]   # prior best 3, live run 5
    assert longest_prior_run(games, "W", tail=5) == 3


def test_detect_streak_needs_a_live_long_run_and_flags_records():
    # 4 wins then 6 losses, most recent game the day before the reference date
    rows = make_history([(4, 6)], [(2026, 7)])
    by_team = by_team_index(rows)
    ctx = group_context(by_team, GROUP, date(2026, 7, 10))
    f = detect_streak(by_team, ctx)
    assert f["kind"] == "streak" and f["direction"] == "cold"
    assert f["streak"]["length"] == 6
    assert f["streak"]["record"] is True      # no prior 6-game skid
    assert f["timeline"][-1]["phase"] == "streak"

    # same games, but the reference date is two weeks later: the run is stale
    stale = group_context(by_team, GROUP, date(2026, 7, 24))
    assert detect_streak(by_team, stale) is None


def test_detect_streak_ignores_short_runs():
    rows = make_history([(6, 3)], [(2026, 7)])    # trailing run of 3
    by_team = by_team_index(rows)
    assert detect_streak(by_team, group_context(by_team, GROUP, date(2026, 7, 10))) is None


def test_detect_turnaround_requires_a_sign_flip():
    # ref Jul 14, so "last 7 days" is Jul 8-14: 0-7 before it, 7-0 since
    rows = [row(f"202607{d:02d}", "STL", 0, "CHC", 1, "CHC", gid=f"a{d}")
            for d in range(1, 8)]
    rows += [row(f"202607{d:02d}", "STL", 1, "CHC", 0, "STL", gid=f"b{d}")
             for d in range(8, 15)]
    ctx = group_context(by_team_index(rows), GROUP, date(2026, 7, 14))
    f = detect_turnaround(ctx)
    assert f["direction"] == "hot"
    assert (f["turnaround"]["early"]["w"], f["turnaround"]["early"]["l"]) == (0, 7)
    assert (f["turnaround"]["late"]["w"], f["turnaround"]["late"]["l"]) == (7, 0)
    assert f["turnaround"]["pace_early"] < 0 < f["turnaround"]["pace_late"]

    # a month that is bad throughout has no turnaround to report
    flat = [row(f"202607{d:02d}", "STL", 0, "CHC", 1, "CHC", gid=f"c{d}")
            for d in range(1, 15)]
    assert detect_turnaround(group_context(by_team_index(flat), GROUP,
                                           date(2026, 7, 14))) is None


def climb_fixture():
    """Nine one-team groups. T0 goes 0-7 in the first week (dead last), then
    sweeps a doubleheader every day of the second week; T1..T8 have staggered
    first-week records and don't play again."""
    groups = [{"name": f"City{i} 1", "city": f"City{i}",
               "teams": [{"league": "mlb", "abbr": f"T{i}", "nickname": f"N{i}"}]}
              for i in range(9)]
    rows = []
    for d in range(1, 8):                       # week 1
        rows.append(row(f"202607{d:02d}", "T0", 0, "OPP", 1, "OPP", gid=f"a0{d}"))
        for i in range(1, 9):
            won = d <= (i % 8)                  # T_i wins i of 7 (T8 wins none)
            rows.append(row(f"202607{d:02d}", f"T{i}", int(won), "OPP",
                            int(not won), f"T{i}" if won else "OPP", gid=f"a{i}{d}"))
    for d in range(8, 15):                      # week 2: T0 only, twice a day
        for g in (1, 2):
            rows.append(row(f"202607{d:02d}", "T0", 1, "OPP", 0, "T0",
                            gid=f"b{d}{g}"))
    return groups, by_team_index(rows)


def test_detect_climb_reports_a_real_move_up_the_standings():
    groups, by_team = climb_fixture()
    ref = date(2026, 7, 14)
    ranks = ytd_rank_series(by_team, groups, ref)
    f = detect_climb(group_context(by_team, groups[0], ref), ranks)
    assert f["kind"] == "climb" and f["direction"] == "hot"
    assert f["climb"]["field"] == 9
    assert f["climb"]["from"] >= 8          # bottom of the table a week ago
    assert f["climb"]["delta"] >= 4         # and well up the table now
    assert f["climb"]["to"] == f["climb"]["from"] - f["climb"]["delta"]
    assert f["rank_series"][-1]["rank"] == f["climb"]["to"]


def test_detect_climb_ignores_small_moves_and_idle_groups():
    groups, by_team = climb_fixture()
    ref = date(2026, 7, 14)
    ranks = ytd_rank_series(by_team, groups, ref)
    # T1..T8 haven't played since July 7: any rank change is someone else's
    # doing, so none of them is a story
    for group in groups[1:]:
        assert detect_climb(group_context(by_team, group, ref), ranks) is None


def test_analyze_group_requires_history_and_games():
    # only 3 months of history: not enough to compare against
    months = [(2022, m) for m in (1, 2, 3)]
    rows = make_history([(7, 7)] * 3, months) + make_history([(10, 0)], [(2022, 4)])
    assert analyze_group(by_team_index(rows), GROUP, date(2022, 4, 14)) is None


# --- the year detector -------------------------------------------------------

def year_rows(records, team="STL", opp="CHC"):
    """{year: (wins, losses)} -> one game a day from January 1 of each year."""
    rows, gid = [], 0
    for year, (w, l) in records.items():
        for i in range(w + l):
            gid += 1
            d = date(year, 1, 1) + timedelta(days=i)
            winner = team if i < w else opp
            rows.append(row(d.strftime("%Y%m%d"), team, 1, opp, 0, winner,
                            gid=str(gid)))
    return rows


def year_context(rows, ref, group=GROUP):
    return group_context(by_team_index(rows), group, ref)


def test_detect_year_flags_a_season_far_from_the_group_s_own_past():
    rows = year_rows({2022: (30, 30), 2023: (28, 32), 2024: (31, 29),
                      2025: (30, 30), 2026: (55, 5)})
    by_team = by_team_index(rows)
    ref = date(2026, 3, 2)                       # after all 60 games of each year
    field = ytd_totals(by_team, [GROUP], ref)
    f = detect_year(year_context(rows, ref), by_team, field)

    assert f["kind"] == "year" and f["direction"] == "hot"
    assert f["year"]["rank"] == 1                # best of the five
    assert f["year"]["n_years"] == 5
    assert f["year"]["since"] is None            # nothing this good before
    assert f["score"] > 0.5


def test_detect_year_measures_every_year_at_the_same_day_of_year():
    """A big current year still loses to a past year that was bigger *by March*."""
    rows = year_rows({2022: (60, 0), 2023: (30, 30), 2024: (30, 30),
                      2026: (40, 20)})
    by_team = by_team_index(rows)
    ref = date(2026, 3, 2)
    f = detect_year(year_context(rows, ref), by_team,
                    ytd_totals(by_team, [GROUP], ref))
    assert f["year"]["rank"] == 2 and f["year"]["since"] == 2022


def test_detect_year_needs_a_full_enough_season_and_prior_years():
    thin = year_rows({2025: (30, 30), 2026: (6, 4)})       # 10 games this year
    by_team = by_team_index(thin)
    ref = date(2026, 3, 2)
    assert detect_year(year_context(thin, ref), by_team,
                       ytd_totals(by_team, [GROUP], ref)) is None

    lonely = year_rows({2025: (30, 30), 2026: (40, 20)})   # only one prior year
    by_team = by_team_index(lonely)
    assert detect_year(year_context(lonely, ref), by_team,
                       ytd_totals(by_team, [GROUP], ref)) is None


def test_detect_year_drops_hollow_claims():
    """'Best year on record' on a losing year is not a story."""
    rows = year_rows({2022: (10, 50), 2023: (12, 48), 2024: (11, 49),
                      2026: (25, 35)})
    by_team = by_team_index(rows)
    ref = date(2026, 3, 2)
    assert detect_year(year_context(rows, ref), by_team,
                       ytd_totals(by_team, [GROUP], ref)) is None


def test_detect_year_records_its_place_in_the_field():
    rows = year_rows({2022: (30, 30), 2023: (30, 30), 2026: (50, 10)})
    rows += year_rows({2026: (58, 2)}, team="MIL", opp="DET")
    groups = [GROUP, {"name": "Otherville 1", "city": "Otherville",
                      "teams": [{"league": "mlb", "abbr": "MIL",
                                 "nickname": "Brew"}]}]
    by_team = by_team_index(rows)
    ref = date(2026, 3, 2)
    f = detect_year(year_context(rows, ref), by_team,
                    ytd_totals(by_team, groups, ref))
    assert (f["year"]["place"], f["year"]["field"]) == (2, 2)


def test_ytd_window_stops_at_the_same_day_of_year():
    assert ytd_window(2024, date(2026, 3, 1)) == ("20240101", "20240229")
    assert ytd_window(2026, date(2026, 3, 1)) == ("20260101", "20260301")
