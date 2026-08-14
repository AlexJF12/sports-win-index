#!/usr/bin/env python3
"""
Share each new blog post to Bluesky, as soon as the post exists.

publish_blog.py files every morning's run into content/posts/<date>/ and
rewrites content/posts/index.json. This reads that manifest and posts anything
it hasn't posted before: the post's lead chart and a link to the post's own
page, and nothing else. There is no post text — the chart and the link *are*
the post, which is why each share goes out as a link card (the one embed shape
that carries a picture and a clickable link at the same time). The card's
headline is the post's own headline, the same string the page already serves as
its <title> and og:title.

What has already gone out is remembered in content/posts/bluesky.json, keyed on
the post's date and kind, alongside a fingerprint of what was shared — the
headline, the dek, the URL and the bytes of the lead image. A run that changes
any of those is an *update*, and an updated post is shared again: Bluesky posts
can't be edited, so the superseded share is deleted and a fresh one takes its
place (--keep-superseded leaves the old one up). A run that changes none of them
is a no-op, which matters because publish_blog.py rewrites every page every
morning and almost all of that output is byte-identical.

Credentials come from the environment — BLUESKY_HANDLE and BLUESKY_APP_PASSWORD
(an app password from Settings → Privacy and security → App passwords, never the
account password). Until they are set, the placeholders below are what's there,
and a run that finds a placeholder logs what is missing and exits 0 rather than
failing: the daily scrape must not start breaking because the account for it
doesn't exist yet. --require-credentials turns that into an error once it does.

Usage:
    python share_bluesky.py                      # share today's posts
    python share_bluesky.py --dry-run            # print what would go out
    python share_bluesky.py --max-age-days 7     # a week's backlog, not a day's
    python share_bluesky.py --backfill --max-posts 5   # ignore the age window
"""

import argparse
import hashlib
import json
import logging
import os
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


def link_card(url: str, title: str, description: str, thumb: dict = None) -> dict:
    """A post that is a picture and a link and nothing else.

    A Bluesky record carries at most one embed, and only the external embed
    holds a URL and an image together — an images embed would show the chart
    with no way to reach the post, and putting the URL in the text is text.
    So the share is a link card: the chart as its thumbnail, the post's own
    headline and dek as the card's, and an empty post body above it.
    """
    external = {"uri": url, "title": title, "description": description}
    if thumb:
        external["thumb"] = thumb
    return {
        "$type": "app.bsky.feed.post",
        "text": "",
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "embed": {"$type": "app.bsky.embed.external", "external": external},
    }


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


def fingerprint(post: dict, url: str, image_path: str | None) -> str:
    """What a share is made of, as one hash.

    Everything that reaches Bluesky and nothing that doesn't: the link, the
    card's two lines, and the pixels of the chart. The publisher rewrites every
    page every morning, so comparing anything looser than this would re-share
    the whole window daily.
    """
    h = hashlib.sha256()
    for part in (url, post.get("title") or "", post.get("dek") or ""):
        h.update(part.encode())
        h.update(b"\0")
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def pending(manifest: dict, state: dict, posts_dir: str = POSTS_DIR,
            max_age_days: int = MAX_AGE_DAYS, max_posts: int = MAX_POSTS,
            backfill: bool = False) -> list[dict]:
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
        mark = fingerprint(post, url, path)
        before = shared.get(name)
        if before and before.get("fingerprint") == mark:
            continue                       # unchanged since it was shared
        out.append({"post": post, "key": name, "url": url, "image": path,
                    "alt": image.get("alt") or "", "fingerprint": mark,
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
        post = item["post"]
        old = item["supersedes"]
        thumb = client.upload(item["image"])
        record = link_card(item["url"], post.get("title") or "Team Wins",
                           post.get("dek") or "", thumb)
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
                    args.max_posts, args.backfill)
    if not items:
        log.info("Nothing new to share")
        return 0

    if args.dry_run:
        for item in items:
            log.info("Would share %s%s", describe(item),
                     " (update)" if item["supersedes"] else "")
            log.info("    %s  ←  %s", item["url"], item["image"])
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
