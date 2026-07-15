"""Tests for scrape_scores.py, run entirely against saved ESPN JSON
fixtures in tests/fixtures/ — never against the live API.

Fixture inventory (real payloads, edge case each was chosen for):
    mlb_20260710.json  full 14-game slate + one Postponed game whose
                       status.type.state is "post" (the case the
                       completed-check fix exists for)
    nba_20260115.json  NBA in-season slate
    nhl_20260122.json  contains Final/OT and Final/SO games
    nfl_20220911.json  contains the IND-HOU 20-20 tie
    nfl_20260710.json  off-season, empty events array
    mlb_20260425.json  COL@NYM postponed
    mlb_20260426.json  COL@NYM doubleheader (the makeup)
    mlb_20260226.json  spring training day (season.type == 1, all skipped)
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scrape_scores import (
    CSV_FIELDS,
    append_rows,
    flatten_completed_games,
    validate_date,
    yesterday_eastern,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    with open(FIXTURES / name) as f:
        return json.load(f)


# --- flatten_completed_games -------------------------------------------------

def test_full_slate_winners_match_scores():
    rows = flatten_completed_games(load_fixture("mlb_20260710.json"), "mlb", "20260710")
    assert len(rows) == 14  # 15 events, one postponed
    for row in rows:
        assert row["date"] == "20260710"
        assert row["league"] == "mlb"
        assert row["away_score"] >= 0 and row["home_score"] >= 0
        expected_winner = (
            row["home_team"] if row["home_score"] > row["away_score"] else row["away_team"]
        )
        assert row["winner"] == expected_winner
    assert len({r["game_id"] for r in rows}) == 14


def test_postponed_game_with_post_state_is_skipped():
    # The MIL@PIT postponement has status.type.state == "post" but
    # completed == false — checking state alone would have written it.
    rows = flatten_completed_games(load_fixture("mlb_20260710.json"), "mlb", "20260710")
    matchups = {(r["away_team"], r["home_team"]) for r in rows}
    assert ("MIL", "PIT") not in matchups
    assert ("PIT", "MIL") not in matchups


def test_postponed_game_skipped_col_nym():
    rows = flatten_completed_games(load_fixture("mlb_20260425.json"), "mlb", "20260425")
    assert len(rows) == 14  # 15 events, COL@NYM postponed
    for row in rows:
        assert {row["away_team"], row["home_team"]} != {"COL", "NYM"}


def test_doubleheader_produces_two_rows():
    rows = flatten_completed_games(load_fixture("mlb_20260426.json"), "mlb", "20260426")
    dh = [r for r in rows if {r["away_team"], r["home_team"]} == {"COL", "NYM"}]
    assert len(dh) == 2
    assert dh[0]["game_id"] != dh[1]["game_id"]


def test_nfl_tie_has_empty_winner():
    rows = flatten_completed_games(load_fixture("nfl_20220911.json"), "nfl", "20220911")
    tie = [r for r in rows if {r["away_team"], r["home_team"]} == {"IND", "HOU"}]
    assert len(tie) == 1
    assert tie[0]["away_score"] == 20 and tie[0]["home_score"] == 20
    assert tie[0]["winner"] == ""
    # every other game that day had a winner
    assert all(r["winner"] for r in rows if r is not tie[0])


def test_ot_and_so_markers_preserved():
    rows = flatten_completed_games(load_fixture("nhl_20260122.json"), "nhl", "20260122")
    statuses = {r["status"] for r in rows}
    assert "Final/OT" in statuses
    assert "Final/SO" in statuses
    assert "Final" in statuses


def test_nba_slate():
    rows = flatten_completed_games(load_fixture("nba_20260115.json"), "nba", "20260115")
    assert len(rows) == 9
    assert all(r["winner"] for r in rows)


def test_preseason_games_are_skipped():
    # 16 completed spring-training games, all season.type == 1 — exhibitions
    # must not count as wins
    payload = load_fixture("mlb_20260226.json")
    assert len(payload["events"]) == 16
    assert flatten_completed_games(payload, "mlb", "20260226") == []


def test_off_season_empty():
    assert flatten_completed_games(load_fixture("nfl_20260710.json"), "nfl", "20260710") == []


def test_malformed_events_are_skipped_not_fatal():
    events = load_fixture("mlb_20260710.json")["events"]
    good_event = next(
        e for e in events
        if e["competitions"][0]["status"]["type"].get("completed")
    )
    payload = {
        "events": [
            {},  # no competitions at all
            {"competitions": [{"status": {"type": {"completed": True}}, "competitors": []}]},
            good_event,
        ]
    }
    rows = flatten_completed_games(payload, "mlb", "20260710")
    assert len(rows) == 1


def test_no_events_key():
    assert flatten_completed_games({}, "mlb", "20260710") == []


# --- append_rows / dedupe ----------------------------------------------------

SAMPLE_ROWS = [
    {
        "date": "20260710", "league": "mlb", "game_id": "1",
        "away_team": "AAA", "away_score": 1, "home_team": "BBB", "home_score": 2,
        "winner": "BBB", "status": "Final",
    },
    {
        "date": "20260710", "league": "mlb", "game_id": "2",
        "away_team": "CCC", "away_score": 3, "home_team": "DDD", "home_score": 0,
        "winner": "CCC", "status": "Final",
    },
]


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_append_creates_file_with_header(tmp_path):
    path = tmp_path / "mlb_scores.csv"
    assert append_rows(str(path), SAMPLE_ROWS) == 2
    rows = read_csv(path)
    assert len(rows) == 2
    assert list(rows[0].keys()) == CSV_FIELDS


def test_rerun_is_idempotent(tmp_path):
    path = tmp_path / "mlb_scores.csv"
    append_rows(str(path), SAMPLE_ROWS)
    assert append_rows(str(path), SAMPLE_ROWS) == 0
    assert len(read_csv(path)) == 2


def test_appends_only_new_game_ids(tmp_path):
    path = tmp_path / "mlb_scores.csv"
    append_rows(str(path), SAMPLE_ROWS[:1])
    assert append_rows(str(path), SAMPLE_ROWS) == 1  # game_id 1 already present
    assert [r["game_id"] for r in read_csv(path)] == ["1", "2"]


def test_empty_rows_write_nothing(tmp_path):
    path = tmp_path / "mlb_scores.csv"
    assert append_rows(str(path), []) == 0
    assert not path.exists()


# --- date handling -----------------------------------------------------------

def test_validate_date_accepts_yyyymmdd():
    assert validate_date("20260710") == "20260710"


@pytest.mark.parametrize("bad", ["2026-07-10", "20261301", "20260732", "yesterday", "2026071"])
def test_validate_date_rejects_bad_input(bad):
    with pytest.raises(SystemExit):
        validate_date(bad)


def test_yesterday_eastern():
    reference = datetime(2026, 7, 15, 5, 0, tzinfo=ZoneInfo("America/New_York"))
    assert yesterday_eastern(reference) == "20260714"


def test_yesterday_eastern_crosses_month_boundary():
    reference = datetime(2026, 7, 1, 5, 0, tzinfo=ZoneInfo("America/New_York"))
    assert yesterday_eastern(reference) == "20260630"
