"""Tests for city_of_the_day.py (the daily draw and the profile it draws)."""

from datetime import date, timedelta

from city_of_the_day import (cooling_off, draw, month_drawable, profile,
                             qualifies, recent_by_team, same_months,
                             season_series, standing)

MLB_W = 365 / 162


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


def group(name, city, teams):
    return {"name": name, "city": city,
            "teams": [{"league": lg, "abbr": ab, "nickname": nick}
                      for lg, ab, nick in teams]}


TWO_TEAM = group("Testville 1", "Testville",
                 [("mlb", "STL", "Cards"), ("nba", "MIL", "Bucks")])


def season(abbr, league, year, results, start_day=1, gid_prefix=""):
    """One game per day from `start_day` of `year`, alternating home and away."""
    rows = []
    for i, res in enumerate(results):
        d = date(year, 1, 1).toordinal() + start_day - 1 + i
        ds = date.fromordinal(d).strftime("%Y%m%d")
        winner = {"W": abbr, "L": "OPP", "T": ""}[res]
        rows.append(row(ds, abbr, "OPP", winner, gid=f"{gid_prefix}{year}-{i}",
                        league=league))
    return rows


# --- the profile -------------------------------------------------------------

def test_season_series_accumulates_and_keeps_one_point_per_day():
    rows = season("STL", "mlb", 2026, list("WWL"))
    rows += [row("20260101", "STL", "OPP", "STL", gid="same-day")]  # doubleheader
    series = season_series(by_team_index(rows), TWO_TEAM, 2026)

    assert [p["day"] for p in series] == [1, 2, 3]
    assert series[0]["cum"] == round(2 * MLB_W, 3)      # both of day one's games
    assert series[-1]["cum"] == round(2 * MLB_W, 3)     # +W then -L cancels out


def test_recent_by_team_skips_teams_that_have_not_played():
    rows = season("STL", "mlb", 2026, list("WWLW"), start_day=205)   # late July
    recent = recent_by_team(by_team_index(rows), TWO_TEAM, date(2026, 8, 2))

    assert [t["nickname"] for t in recent] == ["Cards"]     # no Bucks row
    assert recent[0]["games"] == 4                          # tally's count
    assert len(recent[0]["log"]) == 4                       # the games themselves
    assert recent[0]["longest"] == {"type": "W", "length": 2}


def test_recent_window_excludes_games_older_than_30_days():
    rows = (season("STL", "mlb", 2026, list("WW"), start_day=180)     # late June
            + season("STL", "mlb", 2026, list("LL"), start_day=210))  # late July
    prof = profile(by_team_index(rows), TWO_TEAM, date(2026, 8, 2))

    assert prof["season"]["games"] == 4
    assert prof["recent"]["games"] == 2
    assert prof["recent"]["l"] == 2


def test_qualifies_needs_recent_games_and_earlier_seasons():
    rows = season("STL", "mlb", 2026, list("WWLWLWLW"), start_day=205)
    thin = profile(by_team_index(rows), TWO_TEAM, date(2026, 8, 2))
    assert not qualifies(thin)                       # no history to compare against

    rows += season("STL", "mlb", 2025, list("WL"), gid_prefix="a")
    rows += season("STL", "mlb", 2024, list("WL"), gid_prefix="b")
    assert qualifies(profile(by_team_index(rows), TWO_TEAM, date(2026, 8, 2)))


# --- this month against the same month in earlier years ----------------------

def march(abbr, year, results, start_day=1, league="mlb"):
    """Games on consecutive days of March, one per day."""
    rows = []
    for i, res in enumerate(results):
        d = date(year, 3, start_day + i).strftime("%Y%m%d")
        rows.append(row(d, abbr, "OPP", {"W": abbr, "L": "OPP"}[res],
                        gid=f"m{year}-{i}", league=league))
    return rows


ONE_TEAM = group("Marchville 1", "Marchville", [("mlb", "STL", "Cards")])


def test_same_months_compares_earlier_years_at_the_same_day_of_month():
    """March 2026 is judged on its first 10 days, so 2025's flawless second
    half must not count against it — only 2025's own first 10 days do."""
    rows = march("STL", 2026, list("WWWWWWWWWW"))
    rows += march("STL", 2025, list("LLLLL")) + march("STL", 2025, list("WWWWWWWWWW"), start_day=15)
    rows += march("STL", 2024, list("WWWWW"))
    m = same_months(by_team_index(rows), ONE_TEAM, date(2026, 3, 10))

    assert [p["year"] for p in m["past"]] == [2024, 2025]
    assert m["place"] == 1                       # best of the three, to date
    assert m["field"] == 3
    # the ranking uses the same 10 days, but the drawn line runs the full month
    assert m["past"][1]["to_date"]["w"] == 0     # 2025 was 0-5 through March 10
    assert m["past"][1]["series"][-1]["day"] == 24


def test_a_finished_month_is_not_marked_in_progress():
    rows = march("STL", 2026, list("WWWWW")) + march("STL", 2025, list("LLLLL"))
    m = same_months(by_team_index(rows), ONE_TEAM, date(2026, 3, 31))

    assert m["in_progress"] is False
    assert m["length"] == 31


def test_a_month_still_being_played_is_marked_in_progress():
    rows = march("STL", 2026, list("WWWWW")) + march("STL", 2025, list("LLLLL"))
    m = same_months(by_team_index(rows), ONE_TEAM, date(2026, 3, 10))

    assert m["in_progress"] is True
    assert m["cutoff"] == 10


def test_earlier_months_too_thin_to_compare_are_left_out():
    rows = march("STL", 2026, list("WWWWW"))
    rows += march("STL", 2025, list("WW"))          # two games: not a comparison
    rows += march("STL", 2024, list("WWWW"))
    m = same_months(by_team_index(rows), ONE_TEAM, date(2026, 3, 10))

    assert [p["year"] for p in m["past"]] == [2024]


def test_the_month_chart_is_skipped_without_two_earlier_months():
    rows = march("STL", 2026, list("WWWWW")) + march("STL", 2025, list("WWWW"))
    prof = profile(by_team_index(rows), ONE_TEAM, date(2026, 3, 10))
    assert not month_drawable(prof)

    rows += march("STL", 2024, list("WWWW"))
    assert month_drawable(profile(by_team_index(rows), ONE_TEAM, date(2026, 3, 10)))


def test_a_month_with_no_games_of_its_own_is_not_drawable():
    """A drawn city can be out of season this month even though it qualified
    on its last 30 days, which straddle the month boundary."""
    rows = march("STL", 2025, list("WWWW")) + march("STL", 2024, list("WWWW"))
    prof = profile(by_team_index(rows), ONE_TEAM, date(2026, 3, 10))

    assert prof["month"]["games"] == 0
    assert not month_drawable(prof)


def test_standing_reads_the_ends_of_the_field_by_name():
    assert standing(1, 5) == "the best of the 5"
    assert standing(5, 5) == "the worst of the 5"
    assert standing(2, 5) == "the second-best of the 5"
    assert standing(1, 1) == "the only one on record"


# --- the draw ----------------------------------------------------------------

def playable(city, abbr):
    """A one-team group with enough history and recent games to qualify."""
    g = group(city, city, [("mlb", abbr, f"{city} Nine")])
    rows = season(abbr, "mlb", 2026, list("WWLWLWLW"), start_day=205)
    for year in (2024, 2025):
        rows += season(abbr, "mlb", year, list("WLWL"), gid_prefix=f"{year}")
    return g, rows


def world(*cities):
    groups, rows = [], []
    for city, abbr in cities:
        g, r = playable(city, abbr)
        groups.append(g)
        rows += r
    return groups, by_team_index(rows)


REF = date(2026, 8, 2)
CITIES = (("Aville", "AAA"), ("Bton", "BBB"), ("Cburg", "CCC"))


def test_draw_is_reproducible_for_a_date():
    groups, by_team = world(*CITIES)
    first = draw(groups, by_team, REF, [])
    again = draw(groups, by_team, REF, [])
    assert first["city"] == again["city"]


def test_draw_moves_on_over_time():
    groups, by_team = world(*CITIES)
    picks = {draw(groups, by_team, date(2026, 8, d), [])["city"] for d in range(1, 20)}
    assert len(picks) > 1


def test_draw_skips_cities_on_cooldown():
    groups, by_team = world(*CITIES)
    picked = draw(groups, by_team, REF, [])["city"]
    history = [{"date": (REF - timedelta(days=1)).isoformat(),
                "city": picked, "group": picked}]
    assert draw(groups, by_team, REF, history)["city"] != picked


def test_draw_ignores_the_cooldown_rather_than_skipping_a_day():
    groups, by_team = world(*CITIES)
    history = [{"date": (REF - timedelta(days=2)).isoformat(),
                "city": city, "group": city} for city, _ in CITIES]
    assert draw(groups, by_team, REF, history) is not None


def test_draw_returns_nothing_when_no_city_has_recent_games():
    g = group("Quiet", "Quiet", [("mlb", "QQQ", "Quiets")])
    rows = season("QQQ", "mlb", 2026, list("WL"), start_day=10)      # January only
    assert draw([g], by_team_index(rows), REF, []) is None


def test_cooling_off_ignores_todays_own_entry():
    history = [{"date": REF.isoformat(), "city": "Aville", "group": "Aville"}]
    assert cooling_off(history, REF) == set()
