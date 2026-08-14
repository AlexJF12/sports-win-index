"""Tests for data_paths.py — the data/{league}/{year}/ layout helpers
shared by scrape_scores.py (writer) and aggregate_cities.py (reader)."""

import json

from data_paths import csv_path, list_years, write_manifest


def test_csv_path_layout(tmp_path):
    path = csv_path("mlb", 2026, str(tmp_path))
    assert path == str(tmp_path / "mlb" / "2026" / "mlb_scores_2026.csv")


def test_list_years_empty_when_no_files(tmp_path):
    assert list_years("mlb", str(tmp_path)) == []


def test_list_years_finds_and_sorts_year_dirs(tmp_path):
    for year in (2024, 2022, 2023):
        d = tmp_path / "mlb" / str(year)
        d.mkdir(parents=True)
        (d / f"mlb_scores_{year}.csv").write_text("date,league\n")
    assert list_years("mlb", str(tmp_path)) == [2022, 2023, 2024]


def test_list_years_ignores_other_leagues(tmp_path):
    for league, year in (("mlb", 2026), ("nba", 2025)):
        d = tmp_path / league / str(year)
        d.mkdir(parents=True)
        (d / f"{league}_scores_{year}.csv").write_text("date,league\n")
    assert list_years("mlb", str(tmp_path)) == [2026]
    assert list_years("nba", str(tmp_path)) == [2025]


def test_write_manifest_reflects_years_on_disk(tmp_path):
    d = tmp_path / "nfl" / "2023"
    d.mkdir(parents=True)
    (d / "nfl_scores_2023.csv").write_text("date,league\n")

    manifest = write_manifest(str(tmp_path))
    assert manifest["nfl"] == [2023]
    assert manifest["mlb"] == []

    with open(tmp_path / "manifest.json") as f:
        on_disk = json.load(f)
    assert on_disk == manifest
