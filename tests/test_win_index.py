"""Tests for the monthly-wins aggregation (win_index.py, plan section 6)."""

import csv

import win_index

CSV_FIELDS = [
    "date", "league", "game_id", "away_team", "away_score",
    "home_team", "home_score", "winner", "status",
]


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def row(date, away, home, winner, status="Final"):
    return {
        "date": date, "league": "mlb", "game_id": date + winner,
        "away_team": away, "away_score": 0, "home_team": home,
        "home_score": 0, "winner": winner, "status": status,
    }


def test_monthly_wins_counts_only_matching_month_and_team(tmp_path):
    write_csv(tmp_path / "mlb_scores.csv", [
        row("20260705", "NYM", "ATL", "NYM"),
        row("20260706", "NYM", "ATL", "NYM"),
        row("20260708", "KC", "NYM", "NYM"),
        row("20260601", "NYM", "PHI", "NYM"),   # different month
        row("20260707", "NYM", "PHI", "PHI"),   # a loss
    ])
    assert win_index.monthly_wins("mlb", "NYM", "202607", tmp_path) == 3
    assert win_index.monthly_wins("mlb", "NYM", "202606", tmp_path) == 1
    assert win_index.monthly_wins("mlb", "PHI", "202607", tmp_path) == 1


def test_ties_and_empty_team_count_as_zero(tmp_path):
    write_csv(tmp_path / "nfl_scores.csv", [
        {**row("20261123", "GB", "DET", ""), "league": "nfl"},  # tie: empty winner
    ])
    assert win_index.monthly_wins("nfl", "", "202611", tmp_path) == 0
    assert win_index.monthly_wins("nfl", "GB", "202611", tmp_path) == 0


def test_missing_league_file_is_zero_not_error(tmp_path):
    assert win_index.monthly_wins("nhl", "NYR", "202607", tmp_path) == 0
    assert win_index.load_rows("nhl", tmp_path) == []


def test_available_months_newest_first(tmp_path):
    write_csv(tmp_path / "mlb_scores.csv", [
        row("20260705", "NYM", "ATL", "NYM"),
        row("20260812", "NYM", "ATL", "NYM"),
        row("20260601", "NYM", "ATL", "NYM"),
    ])
    assert win_index.available_months(tmp_path) == ["202608", "202607", "202606"]


def test_wins_summary_totals_across_leagues(tmp_path):
    write_csv(tmp_path / "mlb_scores.csv", [
        row("20260705", "NYM", "ATL", "NYM"),
        row("20260706", "NYM", "ATL", "NYM"),
    ])
    write_csv(tmp_path / "nba_scores.csv", [
        {**row("20260710", "NY", "BOS", "NY"), "league": "nba"},
    ])
    summary = win_index.wins_summary({"mlb": "NYM", "nba": "NY"}, "202607", tmp_path)
    assert summary["total"] == 3
    assert summary["month"] == "202607"
    by_league = {t["league"]: t["wins"] for t in summary["teams"]}
    assert by_league == {"mlb": 2, "nba": 1}
    # display name resolves from teams.json
    names = {t["league"]: t["name"] for t in summary["teams"]}
    assert names["mlb"] == "New York Mets"


def test_real_data_nym_july(tmp_path):
    """Against the committed MLB data, the Mets have 4 July 2026 wins."""
    assert win_index.monthly_wins("mlb", "NYM", "202607") == 4
