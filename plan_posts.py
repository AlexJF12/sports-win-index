#!/usr/bin/env python3
"""
Pick the day's two posts, build them, and file them.

Two posts every day, from seven types, and a post is never the same type as
the post before it. That one rule covers both halves of a day and the seam
between days: the morning post differs from last night's, and the evening post
differs from the morning's.

    daily        city of the day
    record       record watch — one fandom off its own script
    hotcold      the longest active winning and losing runs
    h2h          two cities head to head
    timemachine  this month in an earlier year
    guess        guess the city
    banner       the banner race — only on its own days (the 5th, 12th, 19th,
                 26th and the last day of the month), when it takes the
                 evening slot

Which two: the six rotating types are ordered by how long ago each last went
out, longest first, and the first that isn't the previous post's type and has
something to say gets the slot. A type with nothing today (no record fell, no
city qualifies) is passed over rather than padded. Nothing here is random, so
replaying a date picks the same two — and a date that already has its two
posts is rebuilt as the same two types, whatever the rotation would say now.

Usage:
    python plan_posts.py                  # reference date = yesterday (ET)
    python plan_posts.py --date 20261115
    python plan_posts.py --dry-run        # say which two; no charts, nothing filed
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import publish_blog
from publish_blog import (BASE_URL, POSTS_DIR, RETAIN_DAYS, banner_post, daily_post,
                          load_manifest, publish, record_post)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

ROTATION = ["daily", "record", "hotcold", "h2h", "timemachine", "guess"]
SLOTS = 2


def in_order(posts: list) -> list:
    """Published posts oldest first, in the order they went out."""
    return sorted(posts, key=publish_blog.sort_key)


def rotation(history: list) -> list:
    """The rotating types, the one that went out longest ago first. A type
    that has never gone out leads, in ROTATION's order."""
    last = {}
    for n, post in enumerate(history):
        last[post["kind"]] = n
    return sorted(ROTATION, key=lambda kind: (last.get(kind, -1), ROTATION.index(kind)))


def choose(history: list, build, forced: dict | None = None,
           already: list = ()) -> list:
    """The day's posts, in slot order.

    `build(kind, so_far)` returns a post or None. `forced` is a post that must
    go out today (the banner race); it takes the last slot. `already` is the
    kinds a replayed date published the first time, which are kept.
    """
    previous = history[-1]["kind"] if history else None
    order = list(already) or rotation(history)
    posts, spare = [], None
    open_slots = SLOTS - (1 if forced else 0)
    for kind in order:
        if len(posts) == open_slots:
            break
        if kind == previous or (forced and kind == forced["kind"]):
            continue
        post = build(kind, posts)
        if post is None:
            log.info("Nothing to say for %s today — passing it over", kind)
            continue
        told = {c for p in posts + ([forced] if forced else [])
                for c in p.get("cities") or []}
        if told & set(post.get("cities") or []) and not already:
            # a 13-game run is a record-watch post and the top of hot & cold
            # on the same morning; one of them can wait a day
            log.info("%s would retell %s — keeping it in reserve", kind,
                     ", ".join(sorted(told & set(post["cities"]))))
            spare = spare or post
            continue
        posts.append(post)
        previous = kind
    if len(posts) < open_slots and spare:
        posts.append(spare)
    if forced:
        posts.append(forced)
    for slot, post in enumerate(posts, 1):
        post["slot"] = slot
    return posts


def run(script: str, *args: str) -> bool:
    done = subprocess.run([sys.executable, script, *args])
    if done.returncode:
        log.warning("%s exited %d", script, done.returncode)
    return done.returncode == 0


def on_date(post: dict | None, ref: date) -> dict | None:
    """A folder builder reads whatever is on disk; a run that wrote nothing
    leaves last time's output there, and that is not today's post."""
    return post if post and post["date"] == ref.isoformat() else None


def builder(ref: date, published: list, images: bool = True):
    """build(kind, so_far) for the real thing: scripts for the three types
    that have one, post_types for the rest. Scores are loaded once, lazily."""
    stamp = ref.strftime("%Y%m%d")
    no_images = [] if images else ["--no-images"]
    loaded = {}

    def scores():
        if not loaded:
            from aggregate_cities import index_by_team, load_scores
            with open("city_groups.json") as f:
                loaded["groups"] = json.load(f)
            loaded["by_team"] = index_by_team(load_scores("data"))
        return loaded["by_team"], loaded["groups"]

    def build(kind: str, so_far: list) -> dict | None:
        if kind == "daily":
            run("city_of_the_day.py", "--date", stamp, *no_images)
            return on_date(daily_post(), ref)
        if kind == "record":
            # one finding, not three: the run's own history then remembers
            # exactly the city that was posted, and steers off it next time
            if not run("fandom_analysis.py", "--date", stamp, "--top", "1"):
                return None
            if images:
                run("render_content.py")
            return on_date(record_post(), ref)
        if kind == "banner":
            run("banner_race.py", "--date", stamp, *no_images)
            return on_date(banner_post(), ref)
        import post_types
        by_team, groups = scores()
        extra = {}
        if kind == "guess":      # never the city the day's other post is about
            extra["exclude"] = tuple(c for p in so_far for c in p.get("cities") or [])
        return post_types.BUILDERS[kind](by_team, groups, ref, published,
                                         images=images, **extra)

    return build


def plan(ref: date, posts_dir: str = POSTS_DIR, images: bool = True) -> list:
    published = load_manifest(posts_dir)["posts"]
    history = [p for p in in_order(published) if p["date"] < ref.isoformat()]
    today = [p for p in in_order(published)
             if p["date"] == ref.isoformat() and p.get("slot")]
    build = builder(ref, published, images)
    forced = build("banner", [])
    already = [p["kind"] for p in today if p["kind"] in ROTATION]
    return choose(history, build, forced, already)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="Reference date as YYYYMMDD. Defaults to yesterday (US/Eastern).")
    parser.add_argument("--posts-dir", default=POSTS_DIR)
    parser.add_argument("--retain-days", type=int, default=RETAIN_DAYS)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Work out the two posts without drawing charts or filing them "
                             "(the analysis scripts still write their summaries).")
    args = parser.parse_args()

    if args.date:
        ref = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        ref = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()

    posts = plan(ref, args.posts_dir, images=not args.dry_run)
    for post in posts:
        log.info("Slot %d — %s: %s", post["slot"], post["kind"], post["title"])
    if len(posts) < SLOTS:
        log.warning("Only %d post%s for %s", len(posts), "" if len(posts) == 1 else "s", ref)
    if args.dry_run:
        return 0
    publish(posts, args.posts_dir, args.retain_days, base_url=args.base_url)
    return 0 if posts else 1


if __name__ == "__main__":
    sys.exit(main())
