#!/usr/bin/env python3
"""Aggregation layer over the per-league score CSVs (plan section 6).

Answers the question the app is built around: *"How many wins have my
teams had this month?"* Pure functions over the CSV rows, so they're
trivial to test — the web server in ``app.py`` is a thin wrapper on top.

The CSVs are the source of truth. ``winner`` is precomputed by the
scraper (empty for ties), so a monthly win count is just a filtered
count over one league's file — no score comparison needed here.
"""

import csv
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
LEAGUES = ("nfl", "nba", "mlb", "nhl")


def csv_path(league: str, data_dir=DATA_DIR) -> Path:
    return Path(data_dir) / f"{league}_scores.csv"


def load_rows(league: str, data_dir=DATA_DIR) -> list[dict]:
    """Read one league's score rows. Missing file (off-season league that
    has never been scraped) is not an error — it's simply zero games."""
    path = csv_path(league, data_dir)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def available_months(data_dir=DATA_DIR) -> list[str]:
    """Every YYYYMM that appears across all league CSVs, newest first."""
    months: set[str] = set()
    for league in LEAGUES:
        for row in load_rows(league, data_dir):
            date = row.get("date", "")
            if len(date) >= 6:
                months.add(date[:6])
    return sorted(months, reverse=True)


def monthly_wins(league: str, team: str, month: str, data_dir=DATA_DIR) -> int:
    """Count games ``team`` won in ``league`` during ``month`` (YYYYMM).

    A win is a row whose ``winner`` equals the team abbreviation and whose
    ``date`` falls in the month. Ties (empty ``winner``) never match, so
    they correctly count as zero wins.
    """
    if not team:
        return 0
    return sum(
        1
        for row in load_rows(league, data_dir)
        if row.get("winner") == team and row.get("date", "").startswith(month)
    )


def wins_summary(selections: dict[str, str], month: str, data_dir=DATA_DIR) -> dict:
    """Compute the monthly win count for each selected team.

    ``selections`` maps league -> team abbreviation (leagues with no pick
    are skipped). Returns a per-team breakdown plus the combined total —
    the "how good has your month been" number.
    """
    teams = load_team_names(data_dir)
    breakdown = []
    total = 0
    for league in LEAGUES:
        team = selections.get(league)
        if not team:
            continue
        wins = monthly_wins(league, team, month, data_dir)
        total += wins
        breakdown.append({
            "league": league,
            "abbreviation": team,
            "name": teams.get(league, {}).get(team, team),
            "wins": wins,
        })
    return {"month": month, "total": total, "teams": breakdown}


def load_team_names(data_dir=DATA_DIR) -> dict:
    """abbreviation -> display name, per league (from teams.json)."""
    with open(REPO_ROOT / "teams.json") as f:
        return json.load(f)


def load_default_selections() -> dict[str, str]:
    """The picks in my_teams.json, as a league -> abbreviation map."""
    with open(REPO_ROOT / "my_teams.json") as f:
        entries = json.load(f).get("teams", [])
    return {e["league"]: e["abbreviation"] for e in entries}
