"""Validation for teams.json and my_teams.json (plan 9.3)."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

LEAGUE_SIZES = {"nfl": 32, "nba": 30, "mlb": 30, "nhl": 32}


def load(name):
    with open(REPO_ROOT / name) as f:
        return json.load(f)


def test_teams_json_covers_all_leagues():
    teams = load("teams.json")
    assert set(teams) == set(LEAGUE_SIZES)
    for league, size in LEAGUE_SIZES.items():
        assert len(teams[league]) == size, f"{league}: expected {size} teams"


def test_teams_json_display_names_unique_within_league():
    teams = load("teams.json")
    for league, mapping in teams.items():
        names = list(mapping.values())
        assert len(names) == len(set(names)), f"duplicate display name in {league}"


def test_my_teams_resolve_against_teams_json():
    teams = load("teams.json")
    my_teams = load("my_teams.json")["teams"]
    assert my_teams, "my_teams.json must list at least one team"
    for entry in my_teams:
        league, abbr = entry["league"], entry["abbreviation"]
        assert league in teams, f"unknown league {league!r}"
        assert abbr in teams[league], f"{abbr!r} not a {league} abbreviation"


def test_fixture_abbreviations_resolve_against_teams_json():
    """Every team the scoreboard fixtures mention must exist in teams.json,
    so the backend can always map a scores row to a display name."""
    teams = load("teams.json")
    for path in FIXTURES.glob("*.json"):
        league = path.name.split("_")[0]
        with open(path) as f:
            payload = json.load(f)
        for event in payload.get("events", []):
            for competitor in event["competitions"][0]["competitors"]:
                abbr = competitor["team"]["abbreviation"]
                assert abbr in teams[league], (
                    f"{abbr!r} from {path.name} missing in teams.json[{league!r}]"
                )
