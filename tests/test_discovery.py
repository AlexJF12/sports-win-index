"""Tests for discovery.py — which hashtags a share carries, and which team it
calls by its full name. Runs against the real city_groups.json and teams.json:
the nickname collisions they hold (two Cardinals, two Kings, two Panthers) are
the cases that matter."""

import pytest

from discovery import discover, full_name, in_season, load, tag_of, teams_in

CITIES = load()


def post(kind="daily", date="2026-11-15", **extra):
    return {"kind": kind, "date": date, **extra}


def nicks(teams):
    return [t["nickname"] for t in teams]


# --- finding the teams --------------------------------------------------------

def test_a_city_label_gives_its_teams_in_order():
    assert nicks(teams_in("Los Angeles: Dodgers/Lakers/Ducks/Rams", CITIES)) == \
        ["Dodgers", "Lakers", "Ducks", "Rams"]


def test_a_nickname_without_its_city_named_is_not_a_team():
    # "Kings" alone could be Sacramento or Los Angeles
    assert teams_in("The Kings won again", CITIES) == []


def test_the_city_named_settles_a_shared_nickname():
    assert [t["full"] for t in teams_in("Sacramento: Kings", CITIES)] == ["Sacramento Kings"]
    assert [t["full"] for t in teams_in("Los Angeles: Kings", CITIES)] == ["Los Angeles Kings"]


def test_a_nickname_two_named_cities_share_is_left_out():
    text = "St. Louis or Phoenix? Cardinals 6, Suns 5"
    assert nicks(teams_in(text, CITIES)) == ["Suns"]


def test_the_posts_own_cities_count_as_named():
    assert nicks(teams_in("The Heat are 5-0", CITIES, ["Miami"])) == ["Heat"]


def test_a_word_inside_another_is_not_a_team():
    assert teams_in("Boston: Celtics", CITIES)[0]["nickname"] == "Celtics"
    assert teams_in("Boston: Celticsfans", CITIES) == []


def test_tags_are_squeezed_to_letters_and_digits():
    assert tag_of("Trail Blazers") == "TrailBlazers"
    assert tag_of("St. Louis") == "StLouis"
    assert tag_of("76ers") == "76ers"


@pytest.mark.parametrize("league,month,playing", [
    ("mlb", 11, False), ("mlb", 7, True), ("nfl", 1, True), ("nfl", 6, False),
    ("nba", 11, True), ("nhl", 8, False)])
def test_seasons(league, month, playing):
    from datetime import date
    assert in_season(league, date(2026, month, 1)) is playing


# --- the hashtags -------------------------------------------------------------

def test_a_november_city_post_tags_the_teams_playing():
    found = discover(post(), "City of the day — Los Angeles: Dodgers/Lakers/Ducks/Rams")
    # the Dodgers are out of season: the lead tag is the first team playing
    assert found["inline"] == ["Lakers", "NBA", "NHL"]
    assert found["hidden"] == ["LosAngeles", "Dodgers", "Ducks", "Rams"]


def test_a_one_league_city_gets_one_league():
    found = discover(post(), "Portland: Trail Blazers\n2026 so far: 4-9")
    assert found["inline"] == ["TrailBlazers", "NBA"]


def test_out_of_season_teams_still_get_a_team_tag_but_no_league():
    found = discover(post(date="2026-12-15"), "San Diego: Padres")
    assert found["inline"] == ["Padres"]


def test_a_guess_post_never_names_its_city():
    hook = "Guess the city 🕵️\n• Two teams (NBA/NHL)\n• 40-30 this year"
    found = discover(post("guess", cities=["Seattle"], title="Guess the city: two teams"),
                     hook)
    assert found == {"inline": ["NBA", "NHL"], "hidden": [], "named": None}


def test_a_post_about_a_whole_city_tags_the_city_and_its_leagues():
    found = discover(post("banner"), "November banner watch — Los Angeles leads at day 12\n"
                                     "Los Angeles is 14-1, ahead of Boston")
    assert found["inline"] == ["LosAngeles", "NFL", "NBA"]
    assert found["hidden"] == ["Boston"]


def test_a_post_about_no_team_gets_nothing():
    found = discover(post("banner"), "Who takes December?")
    assert found == {"inline": [], "hidden": [], "named": None}


def test_never_more_than_three_inline_or_eight_hidden():
    text = ("New York: Mets/Nets/Devils/Giants · Los Angeles: Dodgers/Lakers/Kings/Rams"
            " · Boston: Red Sox/Celtics/Bruins/Patriots")
    found = discover(post(), text)
    assert len(found["inline"]) == 3 and len(found["hidden"]) == 8
    assert not {t.lower() for t in found["inline"]} & {t.lower() for t in found["hidden"]}


# --- the full name ------------------------------------------------------------

def team(text, nick):
    return next(t for t in teams_in(text, CITIES) if t["nickname"] == nick)


@pytest.mark.parametrize("text,nick,named", [
    ("Los Angeles: the Lakers are 5-0 this week", "Lakers",
     "Los Angeles: the Los Angeles Lakers are 5-0 this week"),
    ("Seattle: Mariners/Kraken/Seahawks", "Mariners", "Seattle Mariners/Kraken/Seahawks"),
    ("🔥 Los Angeles: 13 straight wins (Lakers 6, Ducks 5)", "Lakers",
     "🔥 Los Angeles: 13 straight wins (Los Angeles Lakers 6, Ducks 5)"),
    ("The Warriors? No: San Francisco: 49ers/Giants", "49ers",
     "The Warriors? No: San Francisco 49ers/Giants"),
])
def test_one_team_is_called_by_its_full_name(text, nick, named):
    assert full_name(text, team(text, nick)) == named


def test_a_full_name_that_is_not_city_plus_nickname_is_not_forced_into_a_label():
    # "Raleigh Hurricanes" would be wrong; they are the Carolina Hurricanes
    text = "Raleigh: Hurricanes"
    assert full_name(text, team(text, "Hurricanes")) is None


def test_a_bracket_right_after_its_city_is_left_alone():
    text = "Seattle (Mariners, Kraken, Seahawks)"
    assert full_name(text, team(text, "Mariners")) is None


def test_discover_names_the_first_team_it_can_and_only_one():
    found = discover(post(), "City of the day — Seattle: Mariners/Kraken/Seahawks")
    # the Kraken lead the tags but don't fit anywhere; the Mariners do
    assert found["named"] == "City of the day — Seattle Mariners/Kraken/Seahawks"


def test_a_text_that_already_says_it_is_unchanged():
    found = discover(post(), "The Boston Celtics are 5-0. Boston: Celtics/Bruins")
    assert found["named"] is None
