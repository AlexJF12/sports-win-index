#!/usr/bin/env python3
"""
One-time generator for city_groups.json.

Expands cities.json (metro -> teams per league) into the full, hardcoded
list of city combination groups: for metros with multiple teams in the same
league, one group per combination (cartesian product), numbered in
deterministic sorted order — e.g. Chicago 1 = Cubs, Chicago 2 = White Sox.

The daily pipeline (aggregate_cities.py) reads city_groups.json directly and
never recomputes this. Re-run this script only when cities.json or team names
change; a test asserts the checked-in file matches what this would generate.
"""

import itertools
import json

LEAGUES = ["mlb", "nba", "nhl", "nfl"]

# Multi-word location prefixes that a simple first-word strip would break
MULTIWORD_LOCATIONS = [
    "New York", "Los Angeles", "San Francisco", "San Jose", "Tampa Bay",
    "Green Bay", "New England", "Las Vegas", "St. Louis", "San Diego",
    "San Antonio", "New Orleans", "Oklahoma City", "Golden State",
    "Kansas City", "New Jersey",
]


def nickname(display_name: str) -> str:
    """'Boston Red Sox' -> 'Red Sox', 'Vegas Golden Knights' -> 'Golden Knights',
    'Athletics' -> 'Athletics'."""
    for loc in MULTIWORD_LOCATIONS:
        if display_name.startswith(loc + " "):
            return display_name[len(loc) + 1:]
    parts = display_name.split(" ", 1)
    return parts[1] if len(parts) == 2 else display_name


def build_groups(cities: dict, teams: dict) -> list[dict]:
    """One group per combination of same-league teams within a city.
    A city with a single combination keeps its plain name; multiples are
    numbered 'City 1', 'City 2', ... in deterministic (sorted) order."""
    groups = []
    for city, leagues in cities.items():
        league_slots = [
            [(league, abbr) for abbr in sorted(leagues[league])]
            for league in LEAGUES if league in leagues
        ]
        combos = list(itertools.product(*league_slots))
        for i, combo in enumerate(combos, start=1):
            groups.append({
                "name": city if len(combos) == 1 else f"{city} {i}",
                "city": city,
                "teams": [
                    {
                        "league": league,
                        "abbr": abbr,
                        "nickname": nickname(teams[league][abbr]),
                    }
                    for league, abbr in combo
                ],
            })
    return groups


def main():
    with open("teams.json") as f:
        teams = json.load(f)
    with open("cities.json") as f:
        cities = json.load(f)
    groups = build_groups(cities, teams)
    with open("city_groups.json", "w") as f:
        json.dump(groups, f, indent=2)
        f.write("\n")
    print(f"Wrote city_groups.json ({len(groups)} groups)")


if __name__ == "__main__":
    main()
