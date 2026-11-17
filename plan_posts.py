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

Which two: the best stories. Every rotating type is drafted each morning
(numbers only — no charts yet) and comes back with a score for how much of a
story it is today: a 17-game losing run is one, a 4-game run is not. That
score is scaled by how long the type has been off the timeline — a type that
went out two posts ago counts for a third of itself, one that has waited six
posts or more counts in full — so a big story can jump the queue, a quiet type
still comes round, and no type can take over the feed. The top two go out,
never the previous post's type, and where it can be helped not two posts
about the same city. Only those two are then drawn.

Nothing in the choice is random, so replaying a date picks the same two — and
a date that already has its two posts is rebuilt as the same two types,
whatever the scores would say now.

Usage:
    python plan_posts.py                  # reference date = yesterday (ET)
    python plan_posts.py --date 20261115
    python plan_posts.py --dry-run        # say which two; no charts, nothing filed
"""

import argparse
import json
import logging
import os
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


FULL_VOICE = 6      # posts a type has to sit out before its score counts in full
DEFAULT_SCORE = 0.4


def in_order(posts: list) -> list:
    """Published posts oldest first, in the order they went out."""
    return sorted(posts, key=publish_blog.sort_key)


def posts_since(history: list, kind: str) -> int | None:
    """How many posts ago this type last went out (1 = it was the last post),
    or None if it never has."""
    for back, post in enumerate(reversed(history), 1):
        if post["kind"] == kind:
            return back
    return None


def priority(post: dict, history: list) -> tuple:
    """Story score, scaled by how long the type has been off the timeline.
    Ties go to the type that has waited longer, then to ROTATION's order."""
    since = posts_since(history, post["kind"])
    waited = FULL_VOICE if since is None else since
    voice = min(1.0, waited / FULL_VOICE)
    return (round(post.get("score", DEFAULT_SCORE) * voice, 4),
            float("inf") if since is None else since,
            -ROTATION.index(post["kind"]))


def choose(history: list, drafts: list, forced: dict | None = None,
           already: list = ()) -> list:
    """The day's posts, in slot order, out of the drafts that have something
    to say.

    `forced` is a post that must go out today (the banner race); it takes the
    last slot. `already` is the kinds a replayed date published the first
    time, which are kept in that order.
    """
    previous = history[-1]["kind"] if history else None
    by_kind = {post["kind"]: post for post in drafts}
    if already:
        queue = [by_kind[kind] for kind in already if kind in by_kind]
    else:
        queue = sorted(drafts, key=lambda post: priority(post, history), reverse=True)

    open_slots = SLOTS - (1 if forced else 0)
    picked, spare = [], []
    for post in queue:
        if len(picked) == open_slots:
            break
        told = {c for p in picked + ([forced] if forced else [])
                for c in p.get("cities") or []}
        if told & set(post.get("cities") or []) and not already:
            # a 13-game run is a record-watch post and the top of hot & cold
            # on the same morning; one of them can wait a day
            spare.append(post)
            continue
        picked.append(post)
    picked += spare[:open_slots - len(picked)]

    # the order within the day is where the no-repeat rule is kept: whichever
    # of the two is last night's type goes second
    if len(picked) > 1 and picked[0]["kind"] == previous:
        picked.reverse()
    if picked and picked[0]["kind"] == previous:
        # one candidate, and it is last night's type: nothing this morning
        # beats a repeat
        log.warning("Only %s has anything to say and it went out last — "
                    "holding the slot", previous)
        picked = []
    if forced:
        picked.append(forced)
    for slot, post in enumerate(picked, 1):
        post["slot"] = slot
    return picked


def run(script: str, *args: str) -> bool:
    done = subprocess.run([sys.executable, script, *args])
    if done.returncode:
        log.warning("%s exited %d", script, done.returncode)
    return done.returncode == 0


def on_date(post: dict | None, ref: date) -> dict | None:
    """A folder builder reads whatever is on disk; a run that wrote nothing
    leaves last time's output there, and that is not today's post."""
    return post if post and post["date"] == ref.isoformat() else None


# The two analysis scripts remember which city they featured, so they don't
# feature it again next week. Drafting runs both every morning; only a draft
# that is actually posted should be remembered.
MEMORY = {"daily": os.path.join("content", "daily", "history.json"),
          "record": os.path.join("content", "weekly", "history.json")}


def read_text(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def restore(path: str, text: str | None) -> None:
    if text is None:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w") as f:
        f.write(text)


def keep_finding(slug: str, ref: date, src_dir: str = os.path.join("content", "weekly")) -> None:
    """Cut the hunt's output down to the one finding being posted, so only its
    charts are drawn and only its city is remembered as featured."""
    path = os.path.join(src_dir, "findings.json")
    with open(path) as f:
        data = json.load(f)
    data["findings"] = [f for f in data["findings"] if f["slug"] == slug]
    with open(path, "w") as f:
        json.dump(data, f)
    history = json.loads(read_text(MEMORY["record"]) or "[]")
    for entry in history:
        if entry.get("date") == ref.isoformat():
            entry["cities"] = [f["city"] for f in data["findings"]]
    with open(MEMORY["record"], "w") as f:
        json.dump(history, f, indent=1)
        f.write("\n")


class Builder:
    """Drafts a post of any type (numbers and words, no charts), and then
    draws the ones that were picked. Scripts for the three types that have
    one, post_types for the rest; scores are loaded once, and only if needed."""

    def __init__(self, ref: date, published: list):
        self.ref, self.published = ref, published
        self.stamp = ref.strftime("%Y%m%d")
        self._scores = None

    def n(self, kind: str) -> int:
        """How many posts of this type came before — it turns the wording."""
        return sum(1 for p in self.published
                   if p["kind"] == kind and p["date"] < self.ref.isoformat())

    def scores(self) -> tuple:
        if self._scores is None:
            from aggregate_cities import index_by_team, load_scores
            with open("city_groups.json") as f:
                groups = json.load(f)
            self._scores = index_by_team(load_scores("data")), groups
        return self._scores

    def draft(self, kind: str) -> dict | None:
        return self.build(kind, images=False)

    def draw(self, draft: dict) -> dict | None:
        kind = draft["kind"]
        if kind == "record":
            keep_finding(draft["finding"], self.ref)
            run("render_content.py")
            return on_date(record_post(n=self.n(kind)), self.ref)
        return self.build(kind, images=True)

    def build(self, kind: str, images: bool) -> dict | None:
        no_images = [] if images else ["--no-images"]
        if kind == "daily":
            run("city_of_the_day.py", "--date", self.stamp, *no_images)
            return on_date(daily_post(n=self.n(kind)), self.ref)
        if kind == "record":
            # three candidates; record_post takes the best story among them
            if not run("fandom_analysis.py", "--date", self.stamp, "--top", "3"):
                return None
            return on_date(record_post(n=self.n(kind)), self.ref)
        if kind == "banner":
            run("banner_race.py", "--date", self.stamp, *no_images)
            return on_date(banner_post(n=self.n(kind)), self.ref)
        import post_types
        by_team, groups = self.scores()
        return post_types.BUILDERS[kind](by_team, groups, self.ref, self.published,
                                         images=images)


def plan(ref: date, posts_dir: str = POSTS_DIR, images: bool = True) -> list:
    published = load_manifest(posts_dir)["posts"]
    history = [p for p in in_order(published) if p["date"] < ref.isoformat()]
    today = [p for p in in_order(published)
             if p["date"] == ref.isoformat() and p.get("slot")]
    already = [p["kind"] for p in today if p["kind"] in ROTATION]
    builder = Builder(ref, published)
    memory = {kind: read_text(path) for kind, path in MEMORY.items()}

    drafts = []
    for kind in already or ROTATION:
        draft = builder.draft(kind)
        if draft is None:
            log.info("Nothing to say for %s today", kind)
            continue
        log.info("Draft — %-11s %.2f  %s", kind, draft.get("score", DEFAULT_SCORE),
                 draft["title"])
        drafts.append(draft)
    forced = builder.build("banner", images=images)
    picked = choose(history, drafts, forced, already)

    chosen = {post["kind"] for post in picked}
    for kind, text in memory.items():
        if kind not in chosen:
            restore(MEMORY[kind], text)        # drafted, not posted: forget it
    if not images:
        return picked
    posts = []
    for draft in picked:
        post = draft if draft is forced else builder.draw(draft)
        if post is None:
            log.warning("%s drafted but could not be drawn", draft["kind"])
            continue
        posts.append({**post, "slot": draft["slot"]})
    return posts


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
