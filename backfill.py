#!/usr/bin/env python3
"""One-off historical backfill driver — not part of the daily pipeline.

ESPN's scoreboard endpoint is date-scoped (no range query), so covering a
year means one request per calendar day. This walks every day of a given
year for a given league and flushes to that league's year CSV
(data/{league}/{year}/) every CHUNK_DAYS days, rather than batching a whole
year in memory — a run covering many years takes over an hour, and an
interruption (timeout, network drop, ctrl-c) should lose at most one chunk,
not the whole year. A league/year only counts as done once every day in it
has been processed without a fatal error, tracked via a `.complete` marker
file (not just the CSV's existence, which a partial run would also leave
behind) — --skip-existing (the default) checks that marker, and a resumed
run simply redoes the whole year, relying on append_rows' game_id dedupe to
make that safe and cheap.

Usage:
    python backfill.py --start-year 2010 --end-year 2021
    python backfill.py --start-year 2010 --end-year 2021 --leagues mlb nba
"""

import argparse
import logging
import os
import time
from datetime import date, timedelta

from data_paths import DATA_DIR, csv_path, write_manifest
from scrape_scores import LEAGUES, append_rows, fetch_scoreboard, flatten_completed_games

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REQUEST_DELAY_SECONDS = 0.1  # light politeness pacing for a multi-thousand-request run
CHUNK_DAYS = 30  # flush to disk this often instead of batching a whole year in memory


def dates_in_year(year: int):
    d = date(year, 1, 1)
    end = date(year, 12, 31)
    while d <= end:
        yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


def chunked(iterable, size):
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def complete_marker(league: str, year: int, data_dir: str) -> str:
    return os.path.join(os.path.dirname(csv_path(league, year, data_dir)), ".complete")


def backfill_year(league: str, sport: str, league_slug: str, year: int, data_dir: str) -> int:
    path = csv_path(league, year, data_dir)
    total_found = 0
    total_written = 0
    failed_dates = []
    for chunk in chunked(dates_in_year(year), CHUNK_DAYS):
        rows = []
        for d in chunk:
            try:
                payload = fetch_scoreboard(sport, league_slug, d)
            except RuntimeError as exc:
                log.error("Giving up on %s %s after retries: %s", league, d, exc)
                failed_dates.append(d)
                continue
            rows.extend(flatten_completed_games(payload, league, d))
            time.sleep(REQUEST_DELAY_SECONDS)
        total_found += len(rows)
        total_written += append_rows(path, rows)
        log.info("  %s %d: through %s — %d/%d new row(s) so far",
                  league.upper(), year, chunk[-1], total_written, total_found)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(complete_marker(league, year, data_dir), "w") as f:
        f.write("")

    log.info(
        "%s %d: %d completed game(s) found, %d new row(s) written to %s%s",
        league.upper(), year, total_found, total_written, path,
        f" ({len(failed_dates)} date(s) failed: {failed_dates})" if failed_dates else "",
    )
    return total_written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES.keys()),
                         choices=list(LEAGUES.keys()))
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    args = parser.parse_args()

    total_written = 0
    for year in range(args.start_year, args.end_year + 1):
        for league in args.leagues:
            if args.skip_existing and os.path.exists(complete_marker(league, year, args.data_dir)):
                log.info("Skipping %s %d — already marked complete", league.upper(), year)
                continue
            sport, league_slug = LEAGUES[league]
            total_written += backfill_year(league, sport, league_slug, year, args.data_dir)
        write_manifest(args.data_dir)
        log.info("=== %d complete across requested leagues ===", year)

    log.info("Backfill done. %d total new row(s) written.", total_written)


if __name__ == "__main__":
    main()
