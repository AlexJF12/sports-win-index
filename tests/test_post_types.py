"""Tests for post_types.py, on a league small enough to read: three one-team
cities playing each other. No charts are drawn (images=False)."""

from datetime import date

from post_types import (active_runs, guess_post, h2h_post, hotcold_post,
                        pick_pair, timemachine_post)
from share_bluesky import TEXT_LIMIT, compose

GROUPS = [{"name": city, "city": city,
           "teams": [{"league": "nba", "abbr": abbr, "nickname": nick}]}
          for city, abbr, nick in (("Ames", "AME", "Aces"), ("Bend", "BEN", "Bees"),
                                   ("Cody", "COD", "Cubs"))]
REF = date(2026, 11, 15)


def game(day, n, home, away, winner):
    return {"date": day, "game_id": f"{day}{n}", "home_team": home, "away_team": away,
            "home_score": "101", "away_score": "99", "winner": winner}


def league(years=(2024, 2025, 2026)):
    """Every November day of each year: Ames beats Bend, then Bend beats Cody —
    so Ames always wins, Cody always loses, and Bend splits."""
    rows = []
    for year in years:
        for day in range(1, 16 if year == 2026 else 31):
            stamp = f"{year}11{day:02d}"
            rows += [game(stamp, 1, "AME", "BEN", "AME"), game(stamp, 2, "BEN", "COD", "BEN")]
    by_team = {}
    for row in rows:
        for abbr in (row["home_team"], row["away_team"]):
            by_team.setdefault(("nba", abbr), []).append(row)
    return by_team


def fits(post):
    text, _ = compose(post["hook"], "https://alexjf12.github.io/sports-win-index/"
                                    f"content/posts/{post['date']}/{post['kind']}.html")
    return len(text) <= TEXT_LIMIT and "…" not in text


def test_active_runs_are_one_per_city_longest_first():
    runs = active_runs(league(), GROUPS, REF)
    assert [(r["city"], r["length"]) for r in runs["W"]] == [("Ames", 15)]
    assert [(r["city"], r["length"]) for r in runs["L"]] == [("Cody", 15)]


def test_hot_and_cold_names_both_ends_and_asks_a_question():
    post = hotcold_post(league(), GROUPS, REF, images=False)
    assert post["title"] == "Ames has won 15 straight. Cody has lost 15 straight."
    assert post["cities"] == ["Ames", "Cody"]
    assert post["hook"].endswith("?") and fits(post)


def test_head_to_head_picks_two_cities_that_just_played():
    post = h2h_post(league(), GROUPS, REF, images=False)
    assert post["cities"] == ["Ames", "Bend"]          # the leader is named first
    assert "Aces 101, Bees 99" in post["hook"] and fits(post)


def test_head_to_head_does_not_repeat_a_recent_pair():
    before = [{"date": "2026-11-10", "kind": "h2h", "cities": ["Bend", "Ames"]}]
    post = h2h_post(league(), GROUPS, REF, before, images=False)
    assert set(post["cities"]) == {"Bend", "Cody"}


def test_with_no_game_between_them_the_closest_neighbors_are_compared():
    rows = [{"group": g, "weighted": w, "place": n}
            for n, (g, w) in enumerate(zip(GROUPS, (40.0, 12.0, 10.0)), 1)]
    a, b, meeting = pick_pair(rows, [], set())
    assert (a["group"]["city"], b["group"]["city"], meeting) == ("Bend", "Cody", None)


def test_time_machine_goes_back_to_the_same_month_and_skips_recent_years(monkeypatch):
    monkeypatch.setattr("post_types.MIN_BANNER_FIELD", 3)     # a three-city league
    by_team = league()
    first = timemachine_post(by_team, GROUPS, REF, images=False)
    assert first["title"] == f"November {first['year']}: the banner went to Ames"
    assert first["year"] in (2024, 2025) and fits(first)
    again = timemachine_post(by_team, GROUPS, REF,
                             [{"date": "2026-11-10", "kind": "timemachine",
                               "year": first["year"]}], images=False)
    assert again["year"] != first["year"]


def test_guess_the_city_keeps_the_answer_out_of_everything_shared():
    post = guess_post(league(), GROUPS, REF, images=False, exclude=("Ames",))
    city = post["cities"][0]
    assert city != "Ames"
    assert post["answer"]["label"].startswith(city)
    shown = " ".join([post["title"], post["dek"], post["hook"]]
                     + [s["value"] for s in post["sections"][0]["stats"]]
                     + [i["caption"] for i in post["sections"][0]["images"]])
    assert city not in shown and fits(post)


def test_a_game_by_a_team_outside_the_compared_group_is_not_the_reason():
    # Ames's standings group is its Aces; a game by some other Ames team
    # against Bend must not be cited over a chart that leaves that team out
    rows = [{"group": g, "weighted": w, "place": n}
            for n, (g, w) in enumerate(zip(GROUPS, (40.0, 30.0, 29.0)), 1)]
    other = {"league": "nba", "cities": ("Ames", "Bend"),
             "row": game("20261115", 9, "AM2", "BEN", "AM2")}
    a, b, meeting = pick_pair(rows, [other], set())
    assert meeting is None and (a["group"]["city"], b["group"]["city"]) == ("Bend", "Cody")
