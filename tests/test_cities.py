"""Validation for cities.json (city -> league -> [abbreviations])."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(name):
    with open(REPO_ROOT / name) as f:
        return json.load(f)


def test_cities_resolve_against_teams_json():
    teams = load("teams.json")
    cities = load("cities.json")
    assert cities, "cities.json must list at least one city"
    for city, by_league in cities.items():
        assert by_league, f"{city!r} lists no teams"
        for league, abbrs in by_league.items():
            assert league in teams, f"{city!r}: unknown league {league!r}"
            assert abbrs, f"{city!r}: empty team list for {league!r}"
            for abbr in abbrs:
                assert abbr in teams[league], (
                    f"{city!r}: {abbr!r} not a {league} abbreviation"
                )


def test_cities_cover_every_team_exactly_once():
    """Each team belongs to exactly one city, and no team is left out,
    so the city picker can reach the whole league."""
    teams = load("teams.json")
    cities = load("cities.json")
    for league, mapping in teams.items():
        assigned = [
            abbr
            for by_league in cities.values()
            for abbr in by_league.get(league, [])
        ]
        assert len(assigned) == len(set(assigned)), (
            f"{league}: a team appears in more than one city"
        )
        assert set(assigned) == set(mapping), (
            f"{league}: mismatch — missing {set(mapping) - set(assigned)}, "
            f"extra {set(assigned) - set(mapping)}"
        )
