"""Tests for banner_race.py, and the banner post it feeds publish_blog.py and
share_bluesky.py."""

import json
import os
from datetime import date

from banner_race import (by_city, is_feature_day, lead_line, main, race,
                         ranked)
from publish_blog import banner_post
from share_bluesky import hook

MLB_W = 365 / 162


def row(d, away, home, winner, gid, league="mlb"):
    return {"date": d, "league": league, "game_id": gid, "away_team": away,
            "away_score": "1", "home_team": home, "home_score": "0",
            "winner": winner, "status": "Final"}


def by_team_index(rows):
    by_team = {}
    for r in rows:
        for abbr in (r["away_team"], r["home_team"]):
            by_team.setdefault((r["league"], abbr), []).append(r)
    return by_team


def group(name, city, abbr, nickname):
    return {"name": name, "city": city,
            "teams": [{"league": "mlb", "abbr": abbr, "nickname": nickname}]}


def games(abbr, year, month, results):
    """One game a day from the 1st, against a team in no group."""
    return [row(f"{year:04d}{month:02d}{i + 1:02d}", abbr, "OPP",
                {"W": abbr, "L": "OPP", "T": ""}[res], f"{abbr}-{year}{month}-{i}")
            for i, res in enumerate(results)]


GROUPS = [group("Boston 1", "Boston", "BOS", "Red Sox"),
          group("New York 1", "New York", "NYY", "Yankees"),
          group("New York 2", "New York", "NYM", "Mets"),
          group("Denver 1", "Denver", "COL", "Rockies")]


# --- when it runs ---------------------------------------------------------------

def test_the_four_checkpoints_and_the_last_day_are_feature_days():
    assert [d for d in range(1, 32) if is_feature_day(date(2026, 10, d))] \
        == [5, 12, 19, 26, 31]


def test_february_gets_all_four_checkpoints_and_a_champion():
    assert [d for d in range(1, 29) if is_feature_day(date(2026, 2, d))] \
        == [5, 12, 19, 26, 28]
    assert race_for(date(2026, 2, 28))["final"]
    assert not race_for(date(2028, 2, 28))["final"]    # a leap year plays on


def race_for(ref):
    rows = games("BOS", ref.year, ref.month, "W" * ref.day)
    return race(by_team_index(rows), GROUPS[:1], ref)


def test_an_off_day_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["banner_race.py", "--date", "20261022",
                                     "--out-dir", str(tmp_path / "banner")])
    main()
    assert not (tmp_path / "banner").exists()


# --- the standings --------------------------------------------------------------

def test_points_then_wins_then_name_decide_the_order():
    tallies = {"Boston 1": {"w": 2, "l": 0, "t": 0, "games": 2, "weighted": 4.5},
               "New York 2": {"w": 3, "l": 1, "t": 0, "games": 4, "weighted": 4.5},
               "New York 1": {"w": 3, "l": 1, "t": 0, "games": 4, "weighted": 4.5},
               "Denver 1": {"w": 0, "l": 1, "t": 0, "games": 1, "weighted": -2.25}}
    assert [r["group"]["name"] for r in ranked(tallies, GROUPS)] \
        == ["New York 1", "New York 2", "Boston 1", "Denver 1"]


def test_a_city_is_a_contender_once():
    tallies = {"New York 1": {"w": 3, "l": 0, "t": 0, "games": 3, "weighted": 6.8},
               "New York 2": {"w": 2, "l": 0, "t": 0, "games": 2, "weighted": 4.5},
               "Boston 1": {"w": 1, "l": 0, "t": 0, "games": 1, "weighted": 2.3}}
    assert [r["group"]["city"] for r in by_city(ranked(tallies, GROUPS))] \
        == ["New York", "Boston"]


def test_the_race_names_the_leader_the_chaser_from_another_city_and_the_margin():
    rows = (games("NYY", 2026, 10, "WWWWWWW") + games("NYM", 2026, 10, "WWWWWWL")
            + games("BOS", 2026, 10, "WWWWWLL") + games("COL", 2026, 10, "LLLLLLL"))
    data = race(by_team_index(rows), GROUPS, date(2026, 10, 7))
    assert data["leader"]["name"] == "New York 1"
    assert data["runner_up"]["city"] == "Boston"     # not New York's other group
    assert round(data["margin"], 2) == round(4 * MLB_W, 2)
    assert [s["city"] for s in data["standings"]] == ["New York", "Boston", "Denver"]
    assert not data["final"] and data["day"] == 7 and data["days"] == 31
    # the banner's chart: day 0 through the reference date
    assert len(data["leader"]["series"]) == 8
    assert data["leader"]["series"][-1] == round(7 * MLB_W, 3)


def test_the_banner_count_includes_the_months_already_won():
    rows = (games("BOS", 2026, 8, "WWW") + games("COL", 2026, 8, "L")
            + games("BOS", 2026, 9, "L") + games("COL", 2026, 9, "W")
            + games("BOS", 2026, 10, "W" * 31))
    data = race(by_team_index(rows), GROUPS, date(2026, 10, 31))
    assert data["final"]
    assert data["banner_number"] == 2         # August, then this one
    assert data["previous"]["city"] == "Denver"
    # the year's rafters, this month's banner on the end once it is won
    assert [(r["month"], r["city"]) for r in data["rafters"]] \
        == [(8, "Boston"), (9, "Denver"), (10, "Boston")]
    # the trend arrow: the last seven days, all wins
    assert round(data["standings"][0]["week"], 2) == round(7 * MLB_W, 2)


def test_a_dead_heat_is_called_on_wins():
    data = {"final": False, "margin": 0.0, "runner_up": {"city": "Denver"}}
    assert lead_line(data) == "Level with Denver on points, ahead on wins"


# --- the post and the share -----------------------------------------------------

def write_race(folder, ref, final):
    rows = (games("BOS", ref.year, ref.month, "W" * ref.day)
            + games("COL", ref.year, ref.month, "W" * (ref.day - 2) + "LL"))
    data = race(by_team_index(rows), GROUPS, ref)
    assert data["final"] == final
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "race.json"), "w") as f:
        json.dump(data, f)
    with open(os.path.join(folder, "race.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")


def test_a_checkpoint_post_is_the_banner_watch(tmp_path):
    write_race(str(tmp_path), date(2026, 10, 12), final=False)
    post = banner_post(str(tmp_path))
    assert post["kind"] == "banner" and post["date"] == "2026-10-12"
    assert post["title"] == "October banner watch — Boston leads at day 12"
    assert "ahead of Denver, with 19 days to play" in post["dek"]
    assert [img["file"] for img in post["sections"][0]["images"]] == ["banner-race.png"]
    assert [r[1] for r in post["sections"][0]["table"]["rows"]] == ["Boston", "Denver"]


def test_the_last_day_post_names_the_champion_and_the_count(tmp_path):
    write_race(str(tmp_path), date(2026, 9, 30), final=True)
    post = banner_post(str(tmp_path))
    assert post["title"] == "Boston wins the September 2026 banner"
    assert "clear of Denver — banner #1 for Boston since 2010" in post["dek"]
    lines = hook(post).splitlines()
    assert lines == [post["title"], post["dek"]]


def test_no_picture_no_post(tmp_path):
    write_race(str(tmp_path), date(2026, 10, 12), final=False)
    os.remove(tmp_path / "race.png")
    assert banner_post(str(tmp_path)) is None
