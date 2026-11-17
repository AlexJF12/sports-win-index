#!/usr/bin/env python3
"""
Share each new blog post to Bluesky, as soon as the post exists.

publish_blog.py files every morning's run into content/posts/<date>/ and
rewrites content/posts/index.json. This reads that manifest and posts what is
worth posting: the chart that shows the finding, full size, one line naming the fandom
and one giving the number that makes it interesting, and a link to the post's
own page.

Two decisions are load-bearing, and both cut against the tidier version.

The chart goes out as an image embed rather than a link card. A card is neater —
the URL never shows as text — but its thumbnail renders a few hundred pixels
wide and cropped, and these charts are 1600px with 8pt axis labels, so the
picture arrives as a gray smudge. An images embed renders full width and opens
to full size on a tap. The two embeds are exclusive, so the link then has to
live in the text as a rich-text facet.

One post goes out per run, and the workflow runs twice a day: plan_posts.py
files two posts every morning, each with a slot, and each run shares the oldest
one still waiting. A post is never shared straight after another of its type —
the planner already orders the day that way, and pending() checks it again
against the record. A guess-the-city post gets its answer as a reply a few
hours later. City of the day is one type among seven now, so it always goes
out; --share-daily notable brings back the old bar (see finding()).

What has already gone out is remembered in content/posts/bluesky.json, keyed on
the post's date and kind, alongside a fingerprint of what was shared — the text,
the URL, the alt, and the bytes of the chart. A run that changes any of
those is an *update*, and an updated post is shared again: Bluesky posts can't
be edited, so the superseded share is deleted and a fresh one takes its place
(--keep-superseded leaves the old one up). A run that changes none of them is a
no-op, which matters because publish_blog.py rewrites every page every morning
and almost all of that output is byte-identical.

Credentials come from the environment — BLUESKY_HANDLE and BLUESKY_APP_PASSWORD
(an app password from Settings → Privacy and security → App passwords, never the
account password). Until they are set, the placeholders below are what's there,
and a run that finds a placeholder logs what is missing and exits 0 rather than
failing: the daily scrape must not start breaking because the account for it
doesn't exist yet. --require-credentials turns that into an error once it does.

Usage:
    python share_bluesky.py                      # share today's posts
    python share_bluesky.py --dry-run            # print what would go out, check the login
    python share_bluesky.py --share-daily always # every morning, bar or no bar
    python share_bluesky.py --max-age-days 7     # a week's backlog, not a day's
    python share_bluesky.py --backfill --max-posts 5   # ignore the age window
"""

import argparse
import hashlib
import json
import logging
import os
import re
import struct
import sys
import time
from datetime import datetime, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

POSTS_DIR = os.path.join("content", "posts")
MANIFEST = "index.json"
STATE = "bluesky.json"        # what has been shared, and what it looked like

# --- fill these in once the account exists ------------------------------------
# The workflow passes them as environment variables (from repository secrets of
# the same name), so nothing here has to hold a real password. Editing the
# fallbacks below works too, for a local run — but an app password committed to
# a public repo is a compromised app password, so prefer the environment.
HANDLE = os.environ.get("BLUESKY_HANDLE") or "REPLACE-ME.bsky.social"
APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD") or "REPLACE-ME-APP-PASSWORD"
SERVICE = os.environ.get("BLUESKY_SERVICE") or "https://bsky.social"

# Anything still equal to one of these means "not set up yet", not "wrong".
PLACEHOLDERS = {"REPLACE-ME.bsky.social", "REPLACE-ME-APP-PASSWORD"}

# How far back a run will reach. The workflow runs minutes after the publisher,
# so the day's posts are the only ones in the window and a first run against a
# manifest full of history shares today's, not ninety days of it.
MAX_AGE_DAYS = 1
MAX_POSTS = 1              # one post a run, two runs a day: the morning's two
                           # posts go out hours apart, not back to back
REVEAL_AFTER_HOURS = 4     # how long a guess-the-city post stands before its
                           # answer is posted under it
LINK_SHOWN = 32            # characters of the link that appear in the text

MAX_BLOB_BYTES = 1_000_000  # what the PDS accepts for one blob
MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


# --- the Bluesky end of it ----------------------------------------------------

class BlueskyError(RuntimeError):
    pass


class Bluesky:
    """The four XRPC calls this needs: log in, upload a blob, write a record,
    delete a record. Small enough that a dependency for it would cost more than
    it saves, and `requests` is already here for the scraper."""

    COLLECTION = "app.bsky.feed.post"

    def __init__(self, handle: str, password: str, service: str = SERVICE,
                 session=None):
        self.handle = handle
        self.password = password
        self.service = service.rstrip("/")
        self.http = session or requests.Session()
        self.did = None
        self.token = None

    # -- plumbing --
    def _call(self, method: str, headers: dict = None, **kwargs) -> dict:
        url = f"{self.service}/xrpc/{method}"
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.http.post(url, headers=headers or {},
                                      timeout=REQUEST_TIMEOUT, **kwargs)
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except requests.RequestException as exc:
                # A rejected password or a malformed record fails the same way
                # on every retry; only wait out the ones that might pass.
                status = getattr(exc.response, "status_code", None)
                if status is not None and status < 500 and status != 429:
                    raise BlueskyError(f"{method} failed ({status}): "
                                       f"{exc.response.text[:300]}") from exc
                last_error = exc
                log.warning("Attempt %d/%d failed for %s: %s",
                            attempt, MAX_RETRIES, method, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        raise BlueskyError(f"{method} failed after {MAX_RETRIES} attempts") from last_error

    def _auth(self) -> dict:
        if not self.token:
            raise BlueskyError("Not logged in")
        return {"Authorization": f"Bearer {self.token}"}

    # -- the calls --
    def login(self) -> str:
        data = self._call("com.atproto.server.createSession",
                          json={"identifier": self.handle, "password": self.password})
        self.did, self.token = data["did"], data["accessJwt"]
        log.info("Signed in to Bluesky as %s (%s)", self.handle, self.did)
        return self.did

    def upload(self, path: str) -> dict:
        """One image as a blob reference to embed in a record."""
        mime = MIME_TYPES.get(os.path.splitext(path)[1].lower(), "image/png")
        with open(path, "rb") as f:
            body = f.read()
        if len(body) > MAX_BLOB_BYTES:
            raise BlueskyError(f"{path} is {len(body)} bytes, over the "
                               f"{MAX_BLOB_BYTES} the PDS accepts")
        data = self._call("com.atproto.repo.uploadBlob", data=body,
                          headers={**self._auth(), "Content-Type": mime})
        return data["blob"]

    def create(self, record: dict) -> dict:
        return self._call("com.atproto.repo.createRecord", headers=self._auth(),
                          json={"repo": self.did, "collection": self.COLLECTION,
                                "record": record})

    def delete(self, uri: str) -> None:
        """Take down a share that has been superseded. at://<did>/<coll>/<rkey>."""
        rkey = uri.rsplit("/", 1)[-1]
        self._call("com.atproto.repo.deleteRecord", headers=self._auth(),
                   json={"repo": self.did, "collection": self.COLLECTION, "rkey": rkey})


def image_post(text: str, facets: list, blob: dict, alt: str,
               ratio: dict = None) -> dict:
    """A post that is the chart, big, over a line of text and the link.

    The other shape available is a link card, which is tidier — the URL never
    appears as text — and it is the wrong one. A card's thumbnail renders a few
    hundred pixels wide and cropped, and these charts are 1600px with 8pt axis
    labels: the picture arrives as a gray smudge. An images embed renders
    full-width and opens to full size on a tap, which is the only version of
    these charts anyone can actually read on a phone. The cost is that the two
    embeds are exclusive, so the link has to live in the text as a facet.

    One image and not four. The embed takes up to four, and two or more render
    as a grid at half width or less — which would undo the thing this format
    was chosen for.
    """
    image = {"alt": alt, "image": blob}
    if ratio:
        # lets the client reserve the right box before the blob loads, instead
        # of guessing square and cropping a 1600x920 chart to fit
        image["aspectRatio"] = ratio
    return {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "langs": ["en"],
        "facets": facets,
        "embed": {"$type": "app.bsky.embed.images", "images": [image]},
    }


TEXT_LIMIT = 290    # the cap is 300 graphemes; len() counts code points, and
                    # the gap between the two is the headroom


def shown(url: str) -> str:
    """The link as it appears in the text: no scheme, cut short — the way the
    Bluesky app itself displays a long URL. The facet still carries the whole
    address; a 75-character URL spelled out would take a quarter of the post."""
    bare = re.sub(r"^https?://", "", url)
    return bare if len(bare) <= LINK_SHOWN else bare[:LINK_SHOWN - 3] + "..."


def compose(hook: str, url: str) -> tuple[str, list]:
    """The post's text, and the facet that makes the link in it a link.

    Facet offsets are UTF-8 *byte* offsets rather than character offsets. These
    headlines carry em dashes and the occasional emoji, both of which are more
    than one byte, so counting characters would hand Bluesky a link over the
    wrong slice of the string.
    """
    link = shown(url)
    hook = trim(hook, TEXT_LIMIT - len(link) - 2)
    text = f"{hook}\n\n{link}" if hook else link
    start = len(text[:text.rindex(link)].encode())
    return text, [{"index": {"byteStart": start,
                             "byteEnd": start + len(link.encode())},
                   "features": [{"$type": "app.bsky.richtext.facet#link",
                                 "uri": url}]}]


def trim(text: str, room: int) -> str:
    """Cut to length at a word boundary — a stat cut mid-number reads as a typo."""
    if room <= 1:
        return ""
    if len(text) <= room:
        return text
    cut = text[:room - 1]
    space = cut.rfind(" ")
    if space > room // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"


def png_size(path: str) -> dict | None:
    """A PNG's pixel dimensions, straight out of its IHDR — 8 bytes of header,
    rather than a Pillow dependency to read them."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return {"width": width, "height": height}


# --- is there anything in this post? ------------------------------------------

# The daily post exists for the blog's sake: a city a morning, in rotation,
# whether or not anything happened to it. A timeline is not a blog. An account
# that posts an unremarkable team every single day teaches the people following
# it to scroll past its name, and then the one morning something *is* remarkable
# gets scrolled past too. So the weekly reads always go out — the spotlight only
# exists at all when a detector fired, and the streakiness pair is the whole
# field at once — and a daily has to earn it.
#
# Every bar below is the analysis's own. The standing is the phrase
# fandom_analysis.standing() already writes; "clumpier than chance" is
# streakiness.band_reading() already saying the order of results sits outside
# the band chance alone produces. Nothing here re-derives a number, so a change
# to how the analysis measures carries through instead of drifting away from it.

# The best or the worst on record, and nothing in between. "Their best August
# ever" is a sentence somebody repeats; "their 2nd-best August" is a sentence
# nobody finishes — and out of the eleven years on record, top-or-bottom-two
# would fire on better than a third of mornings by chance alone.
MONTH_EDGE = 1
MIN_FIELD = 5        # a field deep enough for "on record" to mean something

# A run is only remarkable against the window it happened in. Six straight
# inside thirty days is most of the month going one way; six straight across a
# 230-game season is a fortnight nobody noticed, and measuring both against one
# bar is what made the first version of this gate pass eleven of twelve posts.
LONG_RUN = {"recent": 6, "season": 10}
RECENT_LABEL_RE = re.compile(r"^Last \d+ days$")
SEASON_LABEL_RE = re.compile(r"^\d{4} so far$")

WORD_PLACES = {"": 1, "second-": 2, "third-": 3, "fourth-": 4, "fifth-": 5, "sixth-": 6}
# fandom_analysis.standing() and the spotlight's lane_claim() both count from
# the nearer end, in words to sixth and digits after, and say a tie as one:
# "the worst of the 17", "tied for the worst of the 17", "the second-worst of
# the 17", "the 7th-best of the 17", "the worst September of the 17 on
# record", "the 10th-worst of 200 months". A tie with the edge is still the
# edge — "tied for the worst" is as much a headline as "the worst".
STANDING_RE = re.compile(
    r"(?:the )?(?:(second-|third-|fourth-|fifth-|sixth-)?(best|worst)"
    r"|(\d+)(?:st|nd|rd|th)-(best|worst))"
    r"(?:\s+\w+)? of (?:the )?(\d+)")
RUN_RE = re.compile(r"longest run (\d+) straight (?:wins|losses)")
MONTH_LABEL_RE = re.compile(r"^[A-Z][a-z]+ \d{4}(?: through day \d+)?$")
ORDER_LABEL = "Order of results"
OUT_OF_BAND = ("clumpier than chance", "more alternating than chance")


def standing_of(text: str) -> tuple[int, int] | None:
    """(place, field) out of "the 7th-best of the 11 on record", with place
    counted from the good end."""
    m = STANDING_RE.search(text)
    if not m:
        return None
    field = int(m.group(5))
    if m.group(3):
        n, side = int(m.group(3)), m.group(4)
    else:
        n, side = WORD_PLACES[m.group(1) or ""], m.group(2)
    # "the second-worst of the 17" is 16th from the good end — everything
    # downstream compares against one end or the other, so both spellings
    # have to arrive on the same scale
    return (n if side == "best" else field - n + 1), field


def stats_of(post: dict) -> list:
    return [stat for section in post.get("sections") or []
            for stat in section.get("stats") or []]


def finding(post: dict) -> str | None:
    """What makes this morning worth a stranger's attention, in the analysis's
    own words — or None when the honest answer is nothing.

    Checked strongest first, because whatever comes back is also the line the
    post leads with: a month at the edge of its own record beats a long run,
    and a long run beats results that merely arrived in an odd order.
    """
    found = finding_and_chart(post)
    return found[0] if found else None


def finding_and_chart(post: dict) -> tuple[str, str] | None:
    """finding(), plus which of the daily's charts shows it: "month" (this
    month against the same stretch of every earlier one), "form" (the last 30
    days), or "season" (the year so far against earlier years).

    The chart has to be the one the sentence is about. "The worst September
    of the 11" over the season chart reads as wrong the moment somebody spots
    a year that finished lower — the season chart measures something else.
    """
    stats = stats_of(post)

    for stat in stats:                     # this month against every past one
        if not MONTH_LABEL_RE.match(stat["label"]):
            continue
        place_field = standing_of(stat["value"])
        if not place_field:
            continue
        place, field = place_field
        if field >= MIN_FIELD and (place <= MONTH_EDGE or place > field - MONTH_EDGE):
            return f"{stat['label']}: {stat['value']}", "month"

    for stat in stats:                     # a run long enough to be the story
        m = RUN_RE.search(stat["value"])
        if not m:
            continue
        if RECENT_LABEL_RE.match(stat["label"]):
            bar, chart = LONG_RUN["recent"], "form"
        elif SEASON_LABEL_RE.match(stat["label"]):
            bar, chart = LONG_RUN["season"], "season"
        else:
            continue
        if int(m.group(1)) >= bar:
            return f"{stat['label']}: {stat['value']}", chart

    for stat in stats:                     # wins and losses not arriving at random
        if stat["label"] == ORDER_LABEL and any(p in stat["value"]
                                                for p in OUT_OF_BAND):
            return f"The order of the results this year is {stat['value']}", "season"

    return None


def first_stat(post: dict) -> str:
    """The lead number, for a post that doesn't need a gate to justify it."""
    stats = stats_of(post)
    return f"{stats[0]['label']}: {stats[0]['value']}" if stats else ""


def hook(post: dict) -> str:
    """The post's text: the claim, then the number under it.

    Two lines, because a timeline is scanned rather than read — the first line
    has to be a thing that happened, not a filing label. "City of the day —
    Baltimore" is how the blog indexes a post; "Baltimore: Orioles/Ravens"
    followed by the month that put them at the bottom of eleven years is what
    somebody stops for. Both are already in the manifest; only one of them is
    worth leading with.
    """
    if post.get("hook"):
        # the newer post types write their own, next to the numbers
        return post["hook"]

    sections = post.get("sections") or []
    lead = sections[0] if sections else {}

    if post["kind"] == "spotlight":
        # the spotlight's headline is already a sentence about a fandom —
        # "Raleigh is having its best year on record" — so it needs no help
        claim = lead.get("heading") or post.get("title") or ""
        stats = lead.get("stats") or []
        detail = f"{stats[0]['label']}: {stats[0]['value']}" if stats else ""
    elif post["kind"] == "daily":
        claim = "City of the day — " + (post.get("dek") or post.get("title") or "")
        detail = finding(post) or first_stat(post)
    elif post["kind"] == "banner":
        # "Boston wins the October 2026 banner", then the margin and the count —
        # the dek is already that sentence, and the picture is the banner itself
        claim = post.get("title") or ""
        detail = post.get("dek") or first_stat(post)
        if "banner watch" in claim:
            detail += "\nCan anyone catch them?"
    else:
        claim = post.get("title") or ""
        detail = next((f"{s['label']}: {s['value']}" for s in stats_of(post)
                       if s["label"].startswith("Streakiest")), first_stat(post))

    return "\n".join(line for line in (claim, detail) if line)


# --- what to share ------------------------------------------------------------

def read_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def lead_image(post: dict) -> dict | None:
    """The post's first image — the same one its page uses as its social card,
    read the same way, so the share and the unfurl can't disagree."""
    for section in post.get("sections") or []:
        for img in section.get("images") or []:
            return img
    return None


def share_image(post: dict) -> dict | None:
    """The chart a share carries: for a daily with a finding, the chart that
    finding is about (daily-month.png for a month claim, and so on); for
    everything else, the lead image."""
    if post["kind"] == "daily":
        found = finding_and_chart(post)
        if found:
            suffix = f"-{found[1]}.png"
            for section in post.get("sections") or []:
                for img in section.get("images") or []:
                    if img["file"].endswith(suffix):
                        return img
    return lead_image(post)


def post_url(post: dict, base_url: str) -> str:
    page = post.get("page") or f"content/posts/{post['date']}/{post['kind']}.html"
    return f"{base_url.rstrip('/')}/{page}"


def key(post: dict) -> str:
    return f"{post['date']}/{post['kind']}"


def last_kind(state: dict) -> str | None:
    """The type of the post that went out most recently."""
    shared = state.get("shared") or {}
    if not shared:
        return None
    newest = max(shared, key=lambda name: shared[name].get("shared_at") or "")
    return newest.split("/", 1)[1]


def fingerprint(text: str, url: str, alt: str, image_path: str | None) -> str:
    """What a share is made of, as one hash.

    Everything that reaches Bluesky and nothing that doesn't: the text, the
    link, the alt, and the pixels of the chart. The publisher rewrites every
    page every morning, so comparing anything looser than this would re-share
    the whole window daily.
    """
    h = hashlib.sha256()
    for part in (text, url, alt):
        h.update(part.encode())
        h.update(b"\0")
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def pending(manifest: dict, state: dict, posts_dir: str = POSTS_DIR,
            max_age_days: int = MAX_AGE_DAYS, max_posts: int = MAX_POSTS,
            backfill: bool = False, share_daily: str = "always") -> list[dict]:
    """The posts that should go out on this run, oldest first — the morning
    slot before the evening one — and never one of the same type as the post
    before it. plan_posts.py already orders the day that way; this is the
    check at the door, for the days a run is missed and the queue is not the
    one that was planned. A post held back for that reason stays pending and
    goes out after the next post of another type.
    """
    posts = manifest.get("posts") or []
    if not posts:
        return []
    base_url = manifest.get("base_url") or ""
    newest = max(p["date"] for p in posts)
    shared = state.get("shared") or {}

    out = []
    for post in sorted(posts, key=lambda p: (p["date"], p.get("slot") or 0, p["kind"])):
        name = key(post)
        if not backfill and days_between(post["date"], newest) > max_age_days:
            continue
        if post.get("archived"):
            continue                       # its charts are gone; nothing to show
        if not post.get("slot") and not backfill:
            continue                       # filed before the two-a-day plan, and
                                           # shared or passed over under its rules
        if post["kind"] == "daily" and share_daily != "always":
            if share_daily == "never" or not finding(post):
                log.info("%s — %s: nothing in it worth a post, skipping",
                         name, post.get("dek") or post.get("title") or "")
                continue
        image = share_image(post)
        if not image:
            log.info("%s has no chart — nothing to share", name)
            continue

        path = os.path.join(posts_dir, post["date"], image["file"])
        if not os.path.exists(path):
            log.warning("%s: %s is in the manifest but not on disk — skipping",
                        name, image["file"])
            continue

        url = post_url(post, base_url)
        alt = image.get("alt") or ""
        text, facets = compose(hook(post), url)
        mark = fingerprint(text, url, alt, path)
        before = shared.get(name)
        if before and before.get("fingerprint") == mark:
            continue                       # unchanged since it was shared
        out.append({"post": post, "key": name, "url": url, "image": path,
                    "alt": alt, "text": text, "facets": facets,
                    "ratio": png_size(path), "fingerprint": mark,
                    "supersedes": before})

    # a post being re-shared is replacing itself, not following itself
    settled = {"shared": {name: entry for name, entry in shared.items()
                          if name not in {item["key"] for item in out}}}
    previous, queue = last_kind(settled), []
    for item in out:
        if item["post"]["kind"] == previous:
            log.info("%s is the same type as the post before it — held for a "
                     "later run", item["key"])
            continue
        queue.append(item)
        previous = item["post"]["kind"]
    if len(queue) > max_posts:
        log.info("%d posts are due and --max-posts is %d — the rest go out "
                 "on the next run", len(queue), max_posts)
        queue = queue[:max_posts]
    return queue


def days_between(earlier: str, later: str) -> int:
    fmt = "%Y-%m-%d"
    return (datetime.strptime(later, fmt) - datetime.strptime(earlier, fmt)).days


def describe(item: dict) -> str:
    return f"{item['key']} — {item['post'].get('title') or ''}"


def share(items: list[dict], client: Bluesky, state: dict,
          keep_superseded: bool = False) -> dict:
    """Post each one, recording it before moving on to the next.

    State is updated per post rather than at the end: a run that dies halfway
    through a Wednesday has still shared two posts, and the record has to say so
    or the next run shares them twice.
    """
    shared = state.setdefault("shared", {})
    for item in items:
        old = item["supersedes"]
        blob = client.upload(item["image"])
        record = image_post(item["text"], item["facets"], blob, item["alt"],
                            item["ratio"])
        created = client.create(record)

        if old and old.get("uri") and not keep_superseded:
            try:
                client.delete(old["uri"])
                log.info("Removed the superseded share of %s", item["key"])
            except BlueskyError as exc:
                # The new share is already up; a stale twin is worth less than
                # the run's remaining posts.
                log.warning("Could not remove the old share of %s: %s",
                            item["key"], exc)

        shared[item["key"]] = {
            "uri": created.get("uri"), "cid": created.get("cid"),
            "url": item["url"], "fingerprint": item["fingerprint"],
            "shared_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        log.info("Shared %s%s", describe(item), " (update)" if old else "")
    return state


def dead_shares(state: dict, posts_dir: str = POSTS_DIR) -> list[str]:
    """The shares whose link no longer leads anywhere: the page they point at
    is gone from the site. Archiving keeps a post's page, so this only ever
    fires on a post that was deleted outright."""
    return [name for name in sorted(state.get("shared") or {})
            if not os.path.exists(os.path.join(posts_dir, f"{name}.html"))]


def prune(names: list[str], client: Bluesky, state: dict) -> dict:
    """Take each dead share down and forget it, one at a time, so a run that
    dies halfway leaves a record that matches the account."""
    shared = state.get("shared") or {}
    for name in names:
        uri = shared[name].get("uri")
        if uri:
            client.delete(uri)
        del shared[name]
        log.info("Deleted the share of %s — its page is gone", name)
    return state


def reply_post(text: str, parent: dict) -> dict:
    """A plain reply directly under one of our own posts."""
    ref = {"uri": parent["uri"], "cid": parent["cid"]}
    return {"$type": "app.bsky.feed.post", "text": text,
            "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "langs": ["en"], "reply": {"root": ref, "parent": ref}}


def due_reveals(manifest: dict, state: dict, now: datetime | None = None) -> list[dict]:
    """Guess-the-city shares old enough to be answered, and not answered yet."""
    now = now or datetime.now(timezone.utc)
    posts = {key(p): p for p in manifest.get("posts") or []}
    out = []
    for name, entry in sorted((state.get("shared") or {}).items()):
        answer = (posts.get(name) or {}).get("answer")
        if not answer or entry.get("reveal") or not entry.get("uri"):
            continue
        at = datetime.strptime(entry["shared_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        if (now - at).total_seconds() >= REVEAL_AFTER_HOURS * 3600:
            out.append({"key": name, "entry": entry,
                        "text": trim(f"The answer: {answer['label']}. "
                                     f"{answer['detail']}.", TEXT_LIMIT)})
    return out


def reveal(items: list[dict], client: Bluesky) -> None:
    for item in items:
        created = client.create(reply_post(item["text"], item["entry"]))
        item["entry"]["reveal"] = created.get("uri")
        log.info("Answered %s", item["key"])


def write_state(state: dict, path: str) -> None:
    state["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
        f.write("\n")


def credentials(handle: str, password: str) -> tuple[str, str] | None:
    """The handle and app password, or None while they're still placeholders."""
    missing = [name for name, value in (("BLUESKY_HANDLE", handle),
                                        ("BLUESKY_APP_PASSWORD", password))
               if not value or value in PLACEHOLDERS]
    if missing:
        log.warning("Bluesky is not set up yet — %s still unset. Create the "
                    "account, make an app password, and add both as repository "
                    "secrets; nothing is shared until then.", " and ".join(missing))
        return None
    return handle, password


def check_login(handle: str, password: str, service: str = SERVICE,
                session=None) -> int:
    """A dry run's other half: sign in and post nothing.

    Listing what would go out says nothing about whether it *can* — a mistyped
    handle or a revoked app password only shows up at createSession — so when
    credentials are set, a dry run signs in too. That is the one call here that
    changes nothing on the account. Placeholders skip it, since a dry run needs
    no credentials; a rejected login is the run's failure.
    """
    creds = credentials(handle, password)
    if creds is None:
        log.info("Skipped the sign-in check — no credentials to check")
        return 0
    try:
        Bluesky(*creds, service=service, session=session).login()
    except BlueskyError as exc:
        log.error("Bluesky sign-in failed — nothing would go out: %s", exc)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts-dir", default=POSTS_DIR)
    parser.add_argument("--state", default=None,
                        help=f"Where the record of past shares lives "
                             f"(default <posts-dir>/{STATE}).")
    parser.add_argument("--handle", default=HANDLE,
                        help="Bluesky handle (default $BLUESKY_HANDLE).")
    parser.add_argument("--service", default=SERVICE,
                        help=f"PDS to post to (default {SERVICE}).")
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS,
                        help=f"How far behind the newest post to reach back "
                             f"(default {MAX_AGE_DAYS}).")
    parser.add_argument("--max-posts", type=int, default=MAX_POSTS,
                        help=f"Ceiling on one run's shares (default {MAX_POSTS}).")
    parser.add_argument("--backfill", action="store_true",
                        help="Ignore the age window and consider every post in "
                             "the manifest (--max-posts still applies).")
    parser.add_argument("--share-daily", choices=("notable", "always", "never"),
                        default="always",
                        help="Which city-of-the-day posts go out: all of them "
                             "(default — it is one of the seven types in the "
                             "rotation), only the ones with a finding in them, "
                             "or none.")
    parser.add_argument("--keep-superseded", action="store_true",
                        help="Leave the old share up when a post is updated, "
                             "instead of replacing it.")
    parser.add_argument("--prune-dead", action="store_true",
                        help="Instead of sharing, delete every past share whose "
                             "page no longer exists on the site. Deleting a "
                             "Bluesky post is permanent; --dry-run lists them.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be shared and post nothing. "
                             "Needs no credentials, but signs in to check them "
                             "when they are set.")
    parser.add_argument("--require-credentials", action="store_true",
                        help="Fail instead of exiting quietly when the handle "
                             "or app password is still a placeholder.")
    args = parser.parse_args()

    state_path = args.state or os.path.join(args.posts_dir, STATE)
    manifest = read_json(os.path.join(args.posts_dir, MANIFEST))
    if not manifest:
        log.error("No manifest at %s — run publish_blog.py first",
                  os.path.join(args.posts_dir, MANIFEST))
        return 1
    state = read_json(state_path) or {}

    if args.prune_dead:
        dead = dead_shares(state, args.posts_dir)
        if args.dry_run:
            for name in dead:
                log.info("Would delete the share of %s (%s)", name,
                         state["shared"][name].get("url"))
            log.info("%d dead share%s", len(dead), "" if len(dead) == 1 else "s")
            return check_login(args.handle, APP_PASSWORD, args.service)
        if not dead:
            log.info("No dead shares")
            return 0
        creds = credentials(args.handle, APP_PASSWORD)
        if creds is None:
            return 1
        client = Bluesky(*creds, service=args.service)
        before = len(state["shared"])
        try:
            client.login()
            prune(dead, client, state)
        finally:
            if len(state["shared"]) != before:
                write_state(state, state_path)
        log.info("Deleted %d dead share%s from Bluesky", len(dead),
                 "" if len(dead) == 1 else "s")
        return 0

    items = pending(manifest, state, args.posts_dir, args.max_age_days,
                    args.max_posts, args.backfill, args.share_daily)

    reveals = due_reveals(manifest, state)

    if args.dry_run:
        for item in reveals:
            log.info("Would answer %s: %s", item["key"], item["text"])
        for item in items:
            log.info("Would share %s%s", describe(item),
                     " (update)" if item["supersedes"] else "")
            for line in item["text"].splitlines():
                log.info("    | %s", line)
            log.info("    image %s%s", os.path.basename(item["image"]),
                     f" ({item['ratio']['width']}x{item['ratio']['height']})"
                     if item["ratio"] else "")
        if not items:
            log.info("Nothing new to share")
        return check_login(args.handle, APP_PASSWORD, args.service)

    if not items and not reveals:
        log.info("Nothing new to share")
        return 0

    creds = credentials(args.handle, APP_PASSWORD)
    if creds is None:
        # The posts stay pending, so the first run after the secrets land shares
        # that day's normally.
        return 1 if args.require_credentials else 0

    client = Bluesky(*creds, service=args.service)
    before = json.dumps(state.get("shared") or {}, sort_keys=True)
    try:
        client.login()
        reveal(reveals, client)
        share(items, client, state, args.keep_superseded)
    finally:
        # Whatever did go out is recorded even if a later one threw; a run that
        # never got past the login leaves the file untouched.
        if json.dumps(state.get("shared") or {}, sort_keys=True) != before:
            write_state(state, state_path)
    log.info("Shared %d post%s to Bluesky", len(items), "" if len(items) == 1 else "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
