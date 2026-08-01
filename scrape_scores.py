#!/usr/bin/env python3
"""
Daily sports scores scraper.

Pulls yesterday's completed games for NFL, NBA, MLB, and NHL from ESPN's
public (unofficial) scoreboard API, and appends them to per-league CSVs.

Designed to run once a day via GitHub Actions, after the previous day's
games have finished. Safe to re-run: it dedupes on game_id before writing.

Usage:
    python scrape_scores.py                # scrapes "yesterday" in US/Eastern
    python scrape_scores.py --date 20260114  # scrape a specific date (YYYYMMDD)
"""

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from data_paths import DATA_DIR, csv_path, write_manifest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# sport/league path segments for the ESPN scoreboard endpoint
LEAGUES = {
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
}

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"

# All-Star Games / Pro Bowl use competition type "ALLSTAR" and field
# placeholder "teams" (Team Stars, AFC, ...) with isActive == False;
# best-on-best tournaments like the NHL's 4 Nations Face-Off use "QRR" and
# field real national teams (isActive == True) that still aren't franchise
# games. Neither should count as a win for any tracked team.
EXHIBITION_COMPETITION_TYPES = {"ALLSTAR", "QRR"}

# A handful of defunct franchises share an abbreviation with an unrelated
# current team, confirmed via ESPN's stable team id (which persists across a
# franchise's own relocations, but differs for a coincidental letter reuse):
# HOU also belonged to the Houston Oilers (a different, defunct franchise —
# the Oilers/Titans lineage is id 10, today's Texans are id 34); WPG also
# belonged to the original Winnipeg Jets (id 24, now the Utah franchise,
# different from today's Jets, id 28, ex-Atlanta Thrashers). Rather than risk
# merging that history into the wrong current team, these are excluded
# outright. league -> {abbreviation: id of the team that legitimately owns it}
COLLISION_ABBREVIATIONS = {
    "nfl": {"HOU": "34"},
    "nhl": {"WPG": "28"},
}

# Franchises that kept their identity but changed the abbreviation ESPN
# reports for them, so older games come back tagged with a dead abbreviation
# that teams.json doesn't know (a Chargers fan's pick would otherwise show no
# pre-2017 history). Keyed on ESPN's team id rather than the old abbreviation
# because the id is stable across a franchise's own rebrands and, unlike the
# letters, can't be confused with an unrelated team that later reused them —
# note NFL id 24 is the Chargers while NHL id 24 is the Coyotes, hence the
# per-league nesting. league -> {team id: the franchise's current abbreviation}
#
# Deliberately excluded: relocations where the nickname changed too, which are
# treated as a different team rather than the same one under a new name, and
# so keep their period-accurate dead tag (NHL Phoenix/Arizona Coyotes PHX+ARI
# now Utah, NHL Atlanta Thrashers ATL now the Winnipeg Jets). Those sit
# correctly tagged in the CSVs but aren't wired into teams.json/cities.json.
RENAMED_FRANCHISES = {
    "nfl": {
        "24": "LAC",  # San Diego -> Los Angeles Chargers (2017)
        "14": "LAR",  # St. Louis -> Los Angeles Rams (2016)
        "13": "LV",   # Oakland -> Las Vegas Raiders (2020)
    },
    "nba": {
        "17": "BKN",  # New Jersey -> Brooklyn Nets (2012)
    },
    "mlb": {
        "28": "MIA",  # Florida -> Miami Marlins (2012)
        "11": "ATH",  # Oakland Athletics -> Athletics (2025)
    },
}
CSV_FIELDS = [
    "date",
    "league",
    "game_id",
    "away_team",
    "away_score",
    "home_team",
    "home_score",
    "winner",
    "status",
]
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


def yesterday_eastern(reference: datetime | None = None) -> str:
    """Return yesterday's date as YYYYMMDD, using US/Eastern as the
    reference timezone since that's where most US sports scheduling
    logic (and ESPN's own date boundaries) roughly lines up."""
    now = reference or datetime.now(ZoneInfo("America/New_York"))
    yday = now - timedelta(days=1)
    return yday.strftime("%Y%m%d")


def validate_date(date: str) -> str:
    """Ensure the date is a real YYYYMMDD date so a typo'd backfill fails
    fast instead of writing rows keyed to a bad date."""
    try:
        # strptime alone accepts 7-digit strings like "2026071"
        if len(date) != 8:
            raise ValueError
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        raise SystemExit(f"Invalid date {date!r}: expected YYYYMMDD")
    return date


def fetch_scoreboard(sport: str, league: str, date: str) -> dict:
    url = BASE_URL.format(sport=sport, league=league)
    params = {"dates": date}

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_error = exc
            log.warning(
                "Attempt %d/%d failed for %s (%s): %s",
                attempt, MAX_RETRIES, league, date, exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(f"Failed to fetch {league} scoreboard for {date}") from last_error


def flatten_completed_games(payload: dict, league: str, date: str) -> list[dict]:
    """Extract only finished, real franchise games as flat score rows. Skips
    in-progress/scheduled/postponed games since their scores aren't final,
    exhibitions (spring training, preseason, All-Star Games/Pro Bowl,
    tournaments like the 4 Nations Face-Off), and games involving a defunct
    franchise whose abbreviation collides with an unrelated current team."""
    rows = []
    for event in payload.get("events", []):
        try:
            # Exhibitions (MLB spring training, NFL preseason) shouldn't
            # count as wins. ESPN: 1=preseason, 2=regular, 3=postseason.
            if event.get("season", {}).get("type") == 1:
                continue

            comp = event["competitions"][0]
            status = comp["status"]["type"]

            # "completed" excludes postponed/canceled events, which ESPN can
            # also mark with state == "post"
            if status.get("completed") is not True:
                continue

            if comp.get("type", {}).get("abbreviation") in EXHIBITION_COMPETITION_TYPES:
                continue

            competitors = comp["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")

            # Placeholder teams (draft-style All-Star squads, conference
            # teams in older Pro Bowl formats, national teams) never carry a
            # real franchise "name" (e.g. "Rams", "Lions") — team.isActive
            # looks like the same signal but isn't: ESPN reports it based on
            # the team's CURRENT branding, so a real team's OWN historical
            # games go isActive=False once that franchise later relocates or
            # renames (confirmed for the Rams/Chargers/Raiders/Commanders/
            # Nets/Hornets/Pelicans/Athletics/Marlins), which would silently
            # drop that team's entire pre-rebrand history if used here.
            if any(not c["team"].get("name") for c in (home, away)):
                continue

            away_abbr = away["team"]["abbreviation"].strip()
            home_abbr = home["team"]["abbreviation"].strip()

            collisions = COLLISION_ABBREVIATIONS.get(league, {})
            if any(
                abbr in collisions and team_id != collisions[abbr]
                for abbr, team_id in (
                    (away_abbr, away["team"].get("id")),
                    (home_abbr, home["team"].get("id")),
                )
            ):
                continue

            # Re-tag a renamed franchise's older games under the abbreviation
            # it goes by today, so they attach to the team a fan can pick.
            # Done after the collision check, which is defined in terms of the
            # raw abbreviation ESPN actually reported.
            renames = RENAMED_FRANCHISES.get(league, {})
            away_abbr = renames.get(away["team"].get("id"), away_abbr)
            home_abbr = renames.get(home["team"].get("id"), home_abbr)

            # Neither side has winner=true on a tie (NFL) — leave winner empty
            if home.get("winner") is True:
                winner = home_abbr
            elif away.get("winner") is True:
                winner = away_abbr
            else:
                winner = ""

            rows.append({
                "date": date,
                "league": league,
                "game_id": event["id"],
                "away_team": away_abbr,
                "away_score": int(away["score"]),
                "home_team": home_abbr,
                "home_score": int(home["score"]),
                "winner": winner,
                # "detail" carries OT/SO markers ("Final/OT"); "description"
                # is just "Final" for those games
                "status": status.get("detail") or status.get("description", ""),
            })
        except (KeyError, IndexError, StopIteration, ValueError) as exc:
            log.warning("Skipping malformed event in %s: %s", league, exc)
            continue

    return rows


def load_existing_game_ids(csv_path: str) -> set[str]:
    if not os.path.exists(csv_path):
        return set()
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        return {row["game_id"] for row in reader}


def append_rows(csv_path: str, rows: list[dict]) -> int:
    """Append new rows to the CSV, skipping any game_id already present.
    Returns the number of rows actually written.

    Also dedupes within `rows` itself, keeping the first occurrence of a
    given game_id — the daily scraper only ever passes one day's rows, where
    this can't come up, but a caller batching multiple days before a single
    call (as backfill.py's chunked writes do) can otherwise write the same
    game twice: ESPN's scoreboard endpoint occasionally lists an extra-innings
    or otherwise late-running game again under a following day's query, and
    load_existing_game_ids alone only catches a row already committed to
    disk, not a duplicate still sitting in the same in-memory batch."""
    if not rows:
        return 0

    existing_ids = load_existing_game_ids(csv_path)
    new_rows = []
    seen_in_batch = set()
    for r in rows:
        if r["game_id"] in existing_ids or r["game_id"] in seen_in_batch:
            continue
        seen_in_batch.add(r["game_id"])
        new_rows.append(r)

    if not new_rows:
        return 0

    file_exists = os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(new_rows)

    return len(new_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="Date to scrape as YYYYMMDD. Defaults to yesterday (US/Eastern).",
        default=None,
    )
    parser.add_argument(
        "--data-dir",
        help=f"Directory to write CSVs into (default: {DATA_DIR})",
        default=DATA_DIR,
    )
    args = parser.parse_args()

    # An empty --date (e.g. from a scheduled Actions run with no input)
    # falls through to yesterday, same as omitting the flag.
    date = validate_date(args.date or yesterday_eastern())
    log.info("Scraping scores for date=%s", date)

    total_written = 0
    any_failures = False

    for league, (sport, league_slug) in LEAGUES.items():
        try:
            payload = fetch_scoreboard(sport, league_slug, date)
        except RuntimeError as exc:
            log.error("Giving up on %s: %s", league, exc)
            any_failures = True
            continue

        rows = flatten_completed_games(payload, league, date)
        path = csv_path(league, int(date[:4]), args.data_dir)
        written = append_rows(path, rows)
        total_written += written

        log.info(
            "%s: %d completed game(s) found, %d new row(s) written to %s",
            league.upper(), len(rows), written, path,
        )

    write_manifest(args.data_dir)
    log.info("Done. %d total new row(s) written across all leagues.", total_written)

    # Non-zero exit if any league failed outright, so the Actions run shows red
    # and you notice, but partial success across leagues still writes what it can.
    if any_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
