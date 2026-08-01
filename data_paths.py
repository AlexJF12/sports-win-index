"""Shared helpers for the per-year CSV layout under data/.

Scores live at data/{league}/{year}/{league}_scores_{year}.csv — one file
per league per calendar year, split out from the old single
data/{league}_scores.csv layout so individual files stay a manageable size
as history is backfilled further back. Used by scrape_scores.py (writer),
aggregate_cities.py (reader), and the manifest that lets the browser (which
can't list a directory) know which year files exist.
"""

import glob
import json
import os

LEAGUES = ["mlb", "nba", "nhl", "nfl"]
DATA_DIR = "data"
MANIFEST_FILENAME = "manifest.json"


def csv_path(league: str, year: int, data_dir: str = DATA_DIR) -> str:
    return os.path.join(data_dir, league, str(year), f"{league}_scores_{year}.csv")


def list_years(league: str, data_dir: str = DATA_DIR) -> list[int]:
    """Years with an existing score file for this league, ascending."""
    pattern = os.path.join(data_dir, league, "*", f"{league}_scores_*.csv")
    years = []
    for path in glob.glob(pattern):
        year_dir = os.path.basename(os.path.dirname(path))
        if year_dir.isdigit():
            years.append(int(year_dir))
    return sorted(years)


def write_manifest(data_dir: str = DATA_DIR) -> dict:
    """Regenerate data/manifest.json: league -> sorted list of years on
    disk. The browser fetches this once to know which per-year CSVs to
    request, since it has no way to list a directory itself."""
    manifest = {league: list_years(league, data_dir) for league in LEAGUES}
    manifest_path = os.path.join(data_dir, MANIFEST_FILENAME)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return manifest
