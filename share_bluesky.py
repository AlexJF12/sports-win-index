#!/usr/bin/env python3
"""
Share each new blog post to Bluesky, as soon as the post exists.

publish_blog.py files every morning's run into content/posts/<date>/ and
rewrites content/posts/index.json. This reads that manifest and posts what is
worth posting: the post's lead chart shown full size, one line naming the fandom
and one giving the number that makes it interesting, and a link to the post's
own page.

Two decisions are load-bearing, and both cut against the tidier version.

The chart goes out as an image embed rather than a link card. A card is neater —
the URL never shows as text — but its thumbnail renders a few hundred pixels
wide and cropped, and these charts are 1600px with 8pt axis labels, so the
picture arrives as a gray smudge. An images embed renders full width and opens
to full size on a tap. The two embeds are exclusive, so the link then has to
live in the text as a rich-text facet.

And not every post goes out. The daily is a city a morning in rotation, whether
or not anything happened to it; an account that posts an unremarkable team every
day teaches its followers to scroll past. So a daily has to clear a bar — a
month at the top or bottom of its own record, a long enough run, results outside
the band chance alone produces — measured with the analysis's own phrases and
thresholds. The weekly reads always go out. See finding(); --share-daily
overrides it either way.

What has already gone out is remembered in content/posts/bluesky.json, keyed on
the post's date and kind, alongside a fingerprint of what was shared — the text,
the URL, the alt, and the bytes of the lead image. A run that changes any of
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
    python share_bluesky.py --dry-run            # print what would go out
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
MAX_POSTS = 4              # a Wednesday carries three; the 4th would be a bug

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


def compose(hook: str, url: str) -> tuple[str, list]:
    """The post's text, and the facet that makes the URL in it a link.

    Facet offsets are UTF-8 *byte* offsets rather than character offsets. These
    headlines carry em dashes and the occasional emoji, both of which are more
    than one byte, so counting characters would hand Bluesky a link over the
    wrong slice of the string.
    """
    hook = trim(hook, TEXT_LIMIT - len(url) - 2)
    text = f"{hook}\n\n{url}" if hook else url
    start = len(text[:text.rindex(url)].encode())
    return text, [{"index": {"byteStart": start,
                             "byteEnd": start + len(url.encode())},
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

ORDINALS = {"best": 1, "second-best": 2, "third-best": 3,
            "fourth-best": 4, "fifth-best": 5, "sixth-best": 6}
# Two phrasings reach this. fandom_analysis.standing(), which the daily writes:
# "the worst of the 11", "the second-best of the 11", "the 7th-best of the 11".
# And the spotlight's own, which drops the article and can name the thing being
# ranked: "1st-worst July of the 5 since 2022", "2nd-best of 61 months".
STANDING_RE = re.compile(
    r"(?:the )?(?:(best|second-best|third-best|fourth-best|fifth-best|sixth-best|worst)"
    r"|(\d+)(?:st|nd|rd|th)-(best|worst))"
    r"(?:\s+\w+)? of (?:the )?(\d+)")
RUN_RE = re.compile(r"longest run (\d+) straight (?:wins|losses)")
MONTH_LABEL_RE = re.compile(r"^[A-Z][a-z]+ \d{4}(?: through day \d+)?$")
ORDER_LABEL = "Order of results"
OUT_OF_BAND = ("clumpier than chance", "more alternating than chance")


def standing_of(text: str) -> tuple[int, int] | None:
    """(place, field) out of "the 7th-best of the 11 on record"."""
    m = STANDING_RE.search(text)
    if not m:
        return None
    field = int(m.group(4))
    if m.group(2):
        n = int(m.group(2))
        # "1st-worst of the 5" is the 5th place, counted from the good end —
        # everything downstream compares against one end or the other, so both
        # spellings have to arrive on the same scale
        place = n if m.group(3) == "best" else field - n + 1
    elif m.group(1) == "worst":
        place = field
    else:
        place = ORDINALS[m.group(1)]
    return place, field


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
    stats = stats_of(post)

    for stat in stats:                     # this month against every past one
        if not MONTH_LABEL_RE.match(stat["label"]):
            continue
        place_field = standing_of(stat["value"])
        if not place_field:
            continue
        place, field = place_field
        if field >= MIN_FIELD and (place <= MONTH_EDGE or place > field - MONTH_EDGE):
            return f"{stat['label']}: {stat['value']}"

    for stat in stats:                     # a run long enough to be the story
        m = RUN_RE.search(stat["value"])
        if not m:
            continue
        if RECENT_LABEL_RE.match(stat["label"]):
            bar = LONG_RUN["recent"]
        elif SEASON_LABEL_RE.match(stat["label"]):
            bar = LONG_RUN["season"]
        else:
            continue
        if int(m.group(1)) >= bar:
            return f"{stat['label']}: {stat['value']}"

    for stat in stats:                     # wins and losses not arriving at random
        if stat["label"] == ORDER_LABEL and any(p in stat["value"]
                                                for p in OUT_OF_BAND):
            return f"The order of the results this year is {stat['value']}"

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
    sections = post.get("sections") or []
    lead = sections[0] if sections else {}

    if post["kind"] == "spotlight":
        # the spotlight's headline is already a sentence about a fandom —
        # "Raleigh is having its best year on record" — so it needs no help
        claim = lead.get("heading") or post.get("title") or ""
        stats = lead.get("stats") or []
        detail = f"{stats[0]['label']}: {stats[0]['value']}" if stats else ""
    elif post["kind"] == "daily":
        claim = post.get("dek") or post.get("title") or ""
        detail = finding(post) or first_stat(post)
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


def post_url(post: dict, base_url: str) -> str:
    page = post.get("page") or f"content/posts/{post['date']}/{post['kind']}.html"
    return f"{base_url.rstrip('/')}/{page}"


def key(post: dict) -> str:
    return f"{post['date']}/{post['kind']}"


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
            backfill: bool = False, share_daily: str = "notable") -> list[dict]:
    """The posts that should go out on this run, oldest first.

    Oldest first so a Wednesday's three land on the timeline in the order the
    blog lists them, and so an interrupted run resumes where it stopped rather
    than leaving a hole in the middle.
    """
    posts = manifest.get("posts") or []
    if not posts:
        return []
    base_url = manifest.get("base_url") or ""
    newest = max(p["date"] for p in posts)
    shared = state.get("shared") or {}

    out = []
    for post in sorted(posts, key=lambda p: (p["date"], p["kind"])):
        name = key(post)
        if not backfill and days_between(post["date"], newest) > max_age_days:
            continue
        if post.get("archived"):
            continue                       # its charts are gone; nothing to show
        if post["kind"] == "daily" and share_daily != "always":
            if share_daily == "never" or not finding(post):
                log.info("%s — %s: nothing in it worth a post, skipping",
                         name, post.get("dek") or post.get("title") or "")
                continue
        image = lead_image(post)
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

    if len(out) > max_posts:
        log.warning("%d posts are due but --max-posts is %d — sharing the "
                    "oldest %d, the rest on the next run",
                    len(out), max_posts, max_posts)
        out = out[:max_posts]
    return out


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
                        default="notable",
                        help="Which city-of-the-day posts go out: only the ones "
                             "with a finding in them (default), all of them, or "
                             "none. The weekly reads always go out.")
    parser.add_argument("--keep-superseded", action="store_true",
                        help="Leave the old share up when a post is updated, "
                             "instead of replacing it.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be shared. Needs no credentials "
                             "and writes nothing.")
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

    items = pending(manifest, state, args.posts_dir, args.max_age_days,
                    args.max_posts, args.backfill, args.share_daily)
    if not items:
        log.info("Nothing new to share")
        return 0

    if args.dry_run:
        for item in items:
            log.info("Would share %s%s", describe(item),
                     " (update)" if item["supersedes"] else "")
            for line in item["text"].splitlines():
                log.info("    | %s", line)
            log.info("    image %s%s", os.path.basename(item["image"]),
                     f" ({item['ratio']['width']}x{item['ratio']['height']})"
                     if item["ratio"] else "")
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
