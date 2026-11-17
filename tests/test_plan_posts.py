"""Tests for plan_posts.py — which two posts a day carries, and in what order.

choose() takes the builder as an argument, so nothing here runs an analysis or
draws a chart: a builder is a function from a kind to a post, or to None.
"""

from plan_posts import ROTATION, choose, rotation


def published(*kinds, start=1):
    """A history of posts, two a day, in the order given."""
    return [{"date": f"2026-08-{start + n // 2:02d}", "kind": kind, "slot": n % 2 + 1}
            for n, kind in enumerate(kinds)]


def always(kind, so_far):
    return {"kind": kind, "cities": [kind]}


def test_a_day_is_two_posts_of_different_types():
    posts = choose([], always)
    assert [p["slot"] for p in posts] == [1, 2]
    assert posts[0]["kind"] != posts[1]["kind"]


def test_the_morning_post_is_never_the_type_of_last_nights():
    # hotcold is the type waiting longest, but it is also what went out last
    history = published("hotcold") + published(*[k for k in ROTATION if k != "hotcold"],
                                               start=2) + published("hotcold", start=9)
    assert rotation(history)[-1] == "hotcold"
    for last in ROTATION:
        posts = choose(published(last), always)
        assert posts[0]["kind"] != last


def test_two_weeks_never_repeat_a_type_back_to_back_and_use_all_six():
    history = []
    for day in range(1, 15):
        for post in choose(history, always):
            history.append({**post, "date": f"2026-08-{day:02d}"})
    kinds = [p["kind"] for p in history]
    assert len(kinds) == 28
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    assert set(kinds) == set(ROTATION)


def test_the_type_out_longest_goes_first():
    history = published("daily", "record", "hotcold", "h2h", "timemachine", "guess")
    assert [p["kind"] for p in choose(history, always)] == ["daily", "record"]


def test_a_type_with_nothing_to_say_is_passed_over():
    def build(kind, so_far):
        return None if kind == "record" else always(kind, so_far)

    history = published("daily", "record", "hotcold", "h2h", "timemachine", "guess")
    assert [p["kind"] for p in choose(history, build)] == ["daily", "hotcold"]


def test_the_banner_takes_the_evening_slot_on_its_days():
    banner = {"kind": "banner", "cities": ["Boston"]}
    posts = choose(published("guess"), always, forced=banner)
    assert [p["kind"] for p in posts] == ["daily", "banner"]
    assert posts[1]["slot"] == 2


def test_the_days_second_post_is_about_someone_else_when_it_can_be():
    def build(kind, so_far):
        return {"kind": kind, "cities": ["Los Angeles"] if kind in ("daily", "record")
                else [kind]}

    assert [p["kind"] for p in choose([], build)] == ["daily", "hotcold"]


def test_a_retelling_still_goes_out_when_it_is_all_there_is():
    def build(kind, so_far):
        return {"kind": kind, "cities": ["Los Angeles"]} if kind in ("daily", "record") \
            else None

    assert [p["kind"] for p in choose([], build)] == ["daily", "record"]


def test_a_replayed_date_keeps_the_types_it_published():
    history = published("daily", "record", "hotcold", "h2h", "timemachine", "guess")
    posts = choose(history, always, already=["timemachine", "h2h"])
    assert [p["kind"] for p in posts] == ["timemachine", "h2h"]
