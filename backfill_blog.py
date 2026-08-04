#!/usr/bin/env python3
"""
Seed the blog from git history.

Before content/posts/ existed, every analysis run overwrote the same files, so
the only copy of last week's charts is the blob git kept. This walks the commit
log, exports each run's output folder into a scratch directory, and hands it to
the same builders publish_blog.py uses — so a backfilled post and a freshly
published one are the same thing.

Three layouts turn up in the log, and all three are read:

    content/<date>/     the original daily spotlight folders
    content/weekly/     the spotlight after it moved to Wednesdays
    content/daily/      city of the day
    content/streakiness/

Commits are replayed oldest first and posts are keyed on (date, kind), so when
a run was amended the last version of it wins — the same rule a re-run follows.

Usage:
    python backfill_blog.py                 # every run in the log
    python backfill_blog.py --dry-run       # list what it would publish
    python backfill_blog.py --since 2026-07-01
"""

import argparse
import logging
import os
import re
import subprocess
import tempfile

from publish_blog import (POSTS_DIR, RETAIN_DAYS, daily_post, publish,
                          spotlight_post, streakiness_post)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATED_DIR = re.compile(r"^content/\d{4}-\d{2}-\d{2}$")

# Which builder reads which folder. The dated folders are the spotlight's old
# home, so they get the spotlight builder too.
BUILDERS = {
    "content/daily": daily_post,
    "content/streakiness": streakiness_post,
    "content/weekly": spotlight_post,
}


def git(*args: str) -> str:
    return subprocess.run(("git", *args), capture_output=True, text=True,
                          check=True).stdout


def commits(since: str | None) -> list:
    """Every commit that touched content/, oldest first."""
    args = ["log", "--format=%H %cI", "--reverse"]
    if since:
        args.append(f"--since={since}")
    args += ["--", "content"]
    return [line.split(" ", 1) for line in git(*args).splitlines() if line]


def folders_at(sha: str) -> dict:
    """Run folders present in a commit, as {folder: [file, ...]}."""
    paths = git("ls-tree", "-r", "--name-only", sha, "--", "content").splitlines()
    found = {}
    for path in paths:
        folder, _, name = path.rpartition("/")
        if folder in BUILDERS or DATED_DIR.match(folder):
            found.setdefault(folder, []).append(name)
    # a folder is only a run once it has the file its builder keys on
    keyed = {"content/daily": "summary.md", "content/streakiness": "streakiness.json"}
    return {folder: names for folder, names in found.items()
            if keyed.get(folder, "findings.json") in names}


def export(sha: str, folder: str, names: list, into: str) -> str:
    """Write one commit's copy of a run folder to disk, so a builder can read it."""
    out = os.path.join(into, sha[:8], os.path.basename(folder))
    os.makedirs(out, exist_ok=True)
    for name in names:
        blob = subprocess.run(("git", "show", f"{sha}:{folder}/{name}"),
                              capture_output=True, check=True).stdout
        with open(os.path.join(out, name), "wb") as f:
            f.write(blob)
    return out


def build(folder: str, path: str):
    builder = BUILDERS.get(folder, spotlight_post)
    try:
        return builder(path)
    except (KeyError, ValueError) as err:
        log.warning("Skipping %s at %s: %s", folder, path, err)
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posts-dir", default=POSTS_DIR)
    parser.add_argument("--since", default=None,
                        help="Only replay commits after this date (any git date spelling).")
    parser.add_argument("--retain-days", type=int, default=RETAIN_DAYS)
    parser.add_argument("--dry-run", action="store_true",
                        help="List the posts that would be published and stop.")
    args = parser.parse_args()

    found = {}                       # (date, kind) -> post, last commit wins
    with tempfile.TemporaryDirectory() as scratch:
        for sha, when in commits(args.since):
            for folder, names in sorted(folders_at(sha).items()):
                path = export(sha, folder, names, scratch)
                post = build(folder, path)
                if not post:
                    continue
                key = (post["date"], post["kind"])
                if key in found:
                    log.info("%s %s superseded by %s (%s)", *key, sha[:8], when[:10])
                found[key] = post

            if args.dry_run:
                continue

        posts = [found[key] for key in sorted(found)]
        for post in posts:
            log.info("Found %s %s — %s", post["date"], post["kind"], post["title"])
        if args.dry_run:
            log.info("Dry run: %d posts, nothing written", len(posts))
            return

        # images are copied out of the scratch export, so publish inside the
        # with-block while the exported blobs still exist
        manifest = publish(posts, args.posts_dir, args.retain_days)

    log.info("Backfilled %d posts; manifest holds %d", len(posts),
             len(manifest["posts"]))


if __name__ == "__main__":
    main()
