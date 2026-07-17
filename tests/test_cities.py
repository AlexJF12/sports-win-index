"""Tests for cities.json and aggregate_cities.py."""

import json
from datetime import date
from pathlib import Path

from aggregate_cities import (
    build_groups,
    nickname,
    period_bounds,
    totals,
    week_bounds,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(name):
    with open(REPO_ROOT / name) as f:
        return json.load(f)


# --- cities.json completeness ------------------------------------------------

def test_every_team_mapped_exactly_once():
    teams = load("teams.json")
    cities = load("cities.json")
    for league in ["mlb", "nba", "nhl", "nfl"]:
        mapped = [a for city in cities.values() for a in city.get(league, [])]
        assert sorted(mapped) == sorted(teams[league].keys()), (
            f"{league}: mapped {len(mapped)} teams, "
            f"missing {set(teams[league]) - set(mapped)}, "
            f"extra {set(mapped) - set(teams[league])}"
        )
        assert len(mapped) == len(set(mapped)), f"{league}: duplicate mapping"


# --- group generation --------------------------------------------------------

def test_chicago_splits_on_mlb():
    groups = build_groups(load("cities.json"), load("teams.json"))
    chicago = [g for g in groups if g["city"] == "Chicago"]
    assert [g["name"] for g in chicago] == ["Chicago 1", "Chicago 2"]
    mlb = {g["name"]: next(t["abbr"] for t in g["teams"] if t["league"] == "mlb")
           for g in chicago}
    assert mlb == {"Chicago 1": "CHC", "Chicago 2": "CHW"}
    # the non-MLB teams are identical across the two groups
    for g in chicago:
        others = {t["league"]: t["abbr"] for t in g["teams"] if t["league"] != "mlb"}
        assert others == {"nba": "CHI", "nhl": "CHI", "nfl": "CHI"}


def test_combination_counts():
    groups = build_groups(load("cities.json"), load("teams.json"))
    by_city = {}
    for g in groups:
        by_city[g["city"]] = by_city.get(g["city"], 0) + 1
    assert by_city["New York"] == 24      # 2 MLB x 2 NBA x 3 NHL x 2 NFL
    assert by_city["Los Angeles"] == 16   # 2 x 2 x 2 x 2
    assert by_city["Chicago"] == 2
    assert by_city["Washington"] == 1
    assert by_city["Green Bay"] == 1


def test_single_combo_city_keeps_plain_name():
    groups = build_groups(load("cities.json"), load("teams.json"))
    washington = [g for g in groups if g["city"] == "Washington"]
    assert [g["name"] for g in washington] == ["Washington"]


def test_nickname():
    assert nickname("Boston Red Sox") == "Red Sox"
    assert nickname("Vegas Golden Knights") == "Golden Knights"
    assert nickname("Portland Trail Blazers") == "Trail Blazers"
    assert nickname("Columbus Blue Jackets") == "Blue Jackets"
    assert nickname("St. Louis Cardinals") == "Cardinals"
    assert nickname("Oklahoma City Thunder") == "Thunder"
    assert nickname("Athletics") == "Athletics"
    assert nickname("LA Clippers") == "Clippers"
    assert nickname("Utah Mammoth") == "Mammoth"


# --- period bounds -----------------------------------------------------------

def test_week_is_tuesday_through_monday():
    # 2026-07-16 is a Thursday -> week is Tue Jul 14 .. Mon Jul 20
    assert week_bounds(date(2026, 7, 16)) == (date(2026, 7, 14), date(2026, 7, 20))
    # a Tuesday starts its own week
    assert week_bounds(date(2026, 7, 14)) == (date(2026, 7, 14), date(2026, 7, 20))
    # a Monday belongs to the week that started 6 days earlier (MNF rule)
    assert week_bounds(date(2026, 7, 20)) == (date(2026, 7, 14), date(2026, 7, 20))


def test_month_and_year_bounds():
    bounds = period_bounds(date(2026, 2, 15))
    assert bounds["month"] == (date(2026, 2, 1), date(2026, 2, 28))
    assert bounds["year"] == (date(2026, 1, 1), date(2026, 12, 31))
    assert period_bounds(date(2026, 12, 31))["month"] == (date(2026, 12, 1), date(2026, 12, 31))


# --- totals ------------------------------------------------------------------

def make_row(d, league, away, ascore, home, hscore, winner):
    return {"date": d, "league": league, "away_team": away, "away_score": ascore,
            "home_team": home, "home_score": hscore, "winner": winner, "status": "Final"}


def test_totals_weighted_and_ties():
    group = {"teams": [{"league": "nfl", "abbr": "CHI"}, {"league": "mlb", "abbr": "CHC"}]}
    rows_nfl = [
        make_row("20260101", "nfl", "CHI", 20, "GB", 17, "CHI"),   # win  +21.47
        make_row("20260108", "nfl", "CHI", 20, "GB", 20, ""),      # tie   0
    ]
    rows_mlb = [
        make_row("20260104", "mlb", "CHC", 2, "STL", 5, "STL"),    # loss -2.25
        make_row("20260201", "mlb", "CHC", 9, "STL", 1, "CHC"),    # outside range
    ]
    by_team = {("nfl", "CHI"): rows_nfl, ("mlb", "CHC"): rows_mlb}
    t = totals(by_team, group, date(2026, 1, 1), date(2026, 1, 31))
    assert (t["w"], t["l"], t["t"]) == (1, 1, 1)
    assert t["weighted"] == round(365 / 17 - 365 / 162, 2)
