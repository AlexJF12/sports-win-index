"""Tests for hooks.py — the words that go to Bluesky.

Three things are held for every variant of every type: it fits without being
trimmed, it never says "weighted", and the wording turns from one post of a
type to the next.
"""

import pytest

import hooks
from share_bluesky import TEXT_LIMIT, compose

URL = "https://alexjf12.github.io/sports-win-index/content/posts/2026-11-15/timemachine.html"
# the longest names on the map, so a variant that fits these fits anything
LONG, LONG_2 = "Salt Lake City", "Oklahoma City"
# the longest foot discovery.py can write: the longest team tag and two leagues
TAGS = ["GoldenKnights", "NBA", "NHL"]
LABEL = "Minnesota: Timberwolves/Golden Knights/Trail Blazers/Diamondbacks"

FACTS = {"date": "2026-11-15", "year": 2026, "city": LONG, "label": LABEL.replace("Minnesota", LONG),
         "season": {"w": 182, "l": 125, "t": 1, "games": 308},
         "recent": {"w": 17, "l": 12, "t": 0, "games": 29,
                    "longest": {"type": "W", "length": 6}},
         "month": {"w": 10, "l": 13, "t": 0, "games": 23, "name": "September",
                   "in_progress": True, "place": 16, "worse": 1, "field": 17}}


def team(nick, w, l, streak=None):
    row = {"w": w, "l": l, "t": 0, "games": w + l, "weighted": 3.0 * (w - l)}
    return {"nickname": nick, "week": row, "month": row, "streak": streak}


def finding(kind, direction="cold", **extra):
    return {"kind": kind, "direction": direction, "city": LONG,
            "headline": f"{LONG}'s teams have lost 17 straight — their longest since "
                        f"September 30, 2015",
            "month_totals": {"w": 10, "l": 13, "t": 0, "games": 23, "weighted": -9.0},
            "per_team": [team("Timberwolves", 0, 4, {"type": "L", "length": 12}),
                         team("Golden Knights", 1, 3, {"type": "L", "length": 5})],
            **extra}


FINDINGS = [
    finding("streak", streak={"type": "L", "length": 17, "record": False, "prior_best": 18}),
    finding("streak", "hot", streak={"type": "W", "length": 17, "record": True,
                                     "prior_best": 12}),
    finding("year", year={"rank": 1, "totals": {"w": 113, "l": 179, "t": 0}}),
    finding("month", rank=1, basis="all"),
    finding("turnaround", "hot"),
    finding("climb", climb={"from": 31, "to": 37, "field": 88}),
]
RUN = {"city": LONG, "type": "W", "length": 13}
COLD = {"city": LONG_2, "type": "L", "length": 17}
ROW_A = {"group": {"city": LONG}, "w": 182, "l": 125, "t": 1, "place": 11}
ROW_B = {"group": {"city": LONG_2}, "w": 133, "l": 103, "t": 0, "place": 13}
CHAMP = {"w": 22, "l": 7, "t": 0}
RACE = {"leader": {"city": LONG, "w": 14, "l": 1, "t": 0},
        "runner_up": {"city": LONG_2, "w": 10, "l": 3, "t": 0},
        "month_name": "September", "month": 9, "year": 2026, "day": 12, "days": 30,
        "final": False, "banner_number": 27, "since": 2010}

WRITERS = {
    "daily": lambda n: hooks.daily_hook(FACTS, n),
    "hotcold": lambda n: hooks.hotcold_hook(RUN, COLD, "Timberwolves 6, Golden Knights 5, "
                                            "Diamondbacks 2", "Thunder", n),
    "h2h": lambda n: hooks.h2h_hook(ROW_A, ROW_B, 49, 2026,
                                    "Golden Knights 6, Thunder 5", {"games": 3, "a": 2, "b": 1},
                                    "Sep 30", n),
    "timemachine": lambda n: hooks.timemachine_hook(
        LONG, "September", 2018, "8 years ago", CHAMP,
        {"nickname": "Timberwolves", "w": 12, "l": 3}, (39, 48), n),
    "guess": lambda n: hooks.guess_hook(
        ["Four teams (MLB/NBA/NHL/NFL)", "164-147-1 this year, 10th of 49 cities",
         "Best year on record: 2024. Worst: 2011"], n),
    "banner": lambda n: hooks.banner_hook(RACE, n),
    "banner won": lambda n: hooks.banner_hook({**RACE, "final": True}, n),
    **{f"record {f['kind']} {f['direction']}": (lambda n, f=f: hooks.record_hook(f, n))
       for f in FINDINGS},
}


@pytest.mark.parametrize("name", WRITERS)
def test_every_variant_fits_untrimmed_with_hashtags_and_speaks_plainly(name):
    for n in range(12):
        text, _ = compose(WRITERS[name](n), URL, TAGS)
        assert len(text) <= TEXT_LIMIT and "…" not in text, (name, n, text)
        assert "weighted" not in text


@pytest.mark.parametrize("name", WRITERS)
def test_the_wording_turns_from_one_post_to_the_next(name):
    said = [WRITERS[name](n) for n in range(5)]
    assert all(a != b for a, b in zip(said, said[1:]))
    assert len(set(said)) >= 3


def test_a_month_in_the_middle_of_its_record_is_not_ranked_out_loud():
    middle = {**FACTS, "month": {**FACTS["month"], "place": 8, "worse": 8}}
    assert "on record" not in hooks.daily_hook(middle)
    assert "second-worst of the 17 on record" in hooks.daily_hook(FACTS)


def test_a_small_sample_turnaround_is_not_a_story():
    quick = {**finding("turnaround", "hot"),
             "month_totals": {"w": 3, "l": 4, "t": 0, "games": 7}}
    assert hooks.record_score(quick) < 0.5 < hooks.record_score(FINDINGS[1])


def test_the_same_two_cities_again_is_less_of_a_story():
    assert hooks.hotcold_score(RUN, COLD, [LONG, LONG_2]) < \
        hooks.hotcold_score(RUN, COLD, [LONG]) < hooks.hotcold_score(RUN, COLD)


def test_two_cities_near_the_top_outrank_two_near_the_bottom():
    low_a, low_b = {**ROW_A, "place": 41}, {**ROW_B, "place": 44}
    assert hooks.h2h_score(ROW_A, ROW_B, 49, True) > hooks.h2h_score(low_a, low_b, 49, True)
