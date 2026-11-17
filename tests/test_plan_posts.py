"""Tests for plan_posts.py — which two posts a day carries, and in what order.

choose() takes drafts that are already written, so nothing here runs an
analysis or draws a chart: a draft is a kind, a score and the cities it names.
"""

from plan_posts import ROTATION, choose, posts_since, priority


def published(*kinds, start=1):
    """A history of posts, two a day, in the order given."""
    return [{"date": f"2026-08-{start + n // 2:02d}", "kind": kind, "slot": n % 2 + 1}
            for n, kind in enumerate(kinds)]


def drafts(scores: dict | None = None, **cities):
    scores = scores or {}
    return [{"kind": kind, "score": scores.get(kind, 0.5),
             "cities": cities.get(kind, [kind])} for kind in ROTATION]


def kinds(posts):
    return [p["kind"] for p in posts]


def test_a_day_is_two_posts_of_different_types():
    posts = choose([], drafts())
    assert [p["slot"] for p in posts] == [1, 2]
    assert posts[0]["kind"] != posts[1]["kind"]


def test_the_best_two_stories_go_out():
    assert set(kinds(choose([], drafts({"hotcold": 0.9, "guess": 0.8})))) == \
        {"hotcold", "guess"}


def test_the_morning_post_is_never_the_type_of_last_nights():
    for last in ROTATION:
        # even when last night's type is far and away today's best story
        posts = choose(published(last), drafts({last: 1.0}))
        assert posts[0]["kind"] != last


def test_a_big_story_of_last_nights_type_goes_out_in_the_evening():
    history = published("daily", "h2h", "timemachine", "guess", "daily", "record")
    posts = choose(history, [{"kind": "record", "score": 1.0, "cities": ["A"]},
                             {"kind": "guess", "score": 0.1, "cities": ["B"]}])
    assert kinds(posts) == ["guess", "record"]


def test_a_type_just_used_counts_for_less_than_one_that_has_waited():
    history = published("daily", "record", "h2h", "timemachine", "guess",
                        "daily", "hotcold", "record")
    fresh = {"kind": "h2h", "score": 0.5}          # six posts back
    recent = {"kind": "hotcold", "score": 0.9}     # two posts back
    assert posts_since(history, "h2h") == 6 and posts_since(history, "hotcold") == 2
    assert priority(fresh, history) > priority(recent, history)


def test_two_weeks_never_repeat_a_type_back_to_back_and_use_all_six():
    history = []
    for day in range(1, 15):
        for post in choose(history, drafts({"hotcold": 0.9, "record": 0.8})):
            history.append({**post, "date": f"2026-08-{day:02d}"})
    sequence = kinds(history)
    assert len(sequence) == 28
    assert all(a != b for a, b in zip(sequence, sequence[1:]))
    assert set(sequence) == set(ROTATION)
    # the best story leads, and still cannot take over the feed
    assert max(sequence.count(k) for k in ROTATION) <= 8


def test_the_banner_takes_the_evening_slot_on_its_days():
    banner = {"kind": "banner", "cities": ["Boston"]}
    posts = choose(published("guess"), drafts({"daily": 0.9}), forced=banner)
    assert kinds(posts) == ["daily", "banner"]
    assert posts[1]["slot"] == 2


def test_the_days_second_post_is_about_someone_else_when_it_can_be():
    both = drafts({"record": 0.9, "hotcold": 0.8, "guess": 0.6},
                  record=["Los Angeles"], hotcold=["Los Angeles", "Vancouver"])
    assert kinds(choose([], both)) == ["record", "guess"]


def test_a_retelling_still_goes_out_when_it_is_all_there_is():
    only = [{"kind": "record", "score": 0.9, "cities": ["Los Angeles"]},
            {"kind": "hotcold", "score": 0.8, "cities": ["Los Angeles"]}]
    assert kinds(choose([], only)) == ["record", "hotcold"]


def test_a_replayed_date_keeps_the_types_it_published():
    history = published("daily", "record", "hotcold", "h2h", "timemachine", "guess")
    posts = choose(history, drafts({"daily": 1.0}), already=["timemachine", "h2h"])
    assert kinds(posts) == ["timemachine", "h2h"]


def test_one_candidate_of_last_nights_type_is_held_rather_than_repeated():
    assert choose(published("guess"), [{"kind": "guess", "score": 1.0}]) == []
