#!/usr/bin/env python3
"""
Turn each analysis run into a dated blog post.

The three analysis jobs write to fixed paths and overwrite them on the next
run — content/daily/season.png is always the newest morning's chart, and
yesterday's copy only survives in git history. That is the right shape for a
working tree and the wrong shape for a blog, so this copies each run's images
into an immutable dated folder and keeps the manifest blog.html reads:

    content/posts/2026-08-03/daily-season.png
    content/posts/2026-08-03/daily-games.png
    content/posts/index.json

One post per run, not one per image: the morning's city of the day is a post,
Wednesday's spotlight is a post carrying its three findings, Wednesday's
streakiness charts are a post. Each source is keyed on its own reference date,
read from the run's own output, so publishing on a Thursday re-files
Wednesday's weekly folder under Wednesday and changes nothing.

The headline and the numbers behind each post are parsed out of the summary.md
the analysis already writes, so there is one source of truth for the prose.

Every post also gets its own page — content/posts/<date>/<kind>.html — with the
headline in the title and the lead chart as its social card, because the feed
renders from JSON and a link into it unfurls as nothing and crawls as nothing.

Retention takes the pixels, not the post. After RETAIN_DAYS a post's images are
deleted (they are ~99% of the bytes, roughly 150 MB a year unchecked) while its
headline, its numbers and its page stay forever, at about 2 MB a year. A link
shared today still resolves in five years; it just stops showing charts.

Usage:
    python publish_blog.py                    # publish whatever is on disk
    python publish_blog.py --retain-days 30
    python publish_blog.py --no-prune         # keep images past the window
    python publish_blog.py --base-url https://example.com
"""

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import statistics
from datetime import date, datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DAILY_DIR = os.path.join("content", "daily")
STREAK_DIR = os.path.join("content", "streakiness")
WEEKLY_DIR = os.path.join("content", "weekly")
POSTS_DIR = os.path.join("content", "posts")
MANIFEST = "index.json"     # posts still inside the image window
ARCHIVE = "archive.json"    # everything older, text only
RETAIN_DAYS = 90            # how long a post keeps its images

# Absolute URLs for the per-post pages' canonical link and social card. GitHub
# Pages serves this repo from the owner's subdomain; --base-url overrides it if
# the site ever moves to its own domain.
BASE_URL = "https://alexjf12.github.io/sports-win-index"

# One row per kind: the badge the page shows, and the sort rank inside a date
# (a Wednesday carries all three, and the weekly reads lead).
KINDS = {
    "spotlight": {"label": "Out of the norm", "rank": 0},
    "streakiness": {"label": "Streakiness", "rank": 1},
    "daily": {"label": "City of the day", "rank": 2},
}

# What each chart shows, in a sentence — the caption under the image and its
# alt text. The images carry their own titles; this is the "why am I looking
# at this" line that a title inside a PNG can't be read out as.
DAILY_CAPTIONS = {
    "season.png": "The group's cumulative weighted index this year, drawn against "
                  "its own earlier seasons, day of year for day of year.",
    "form.png": "Each team's running record against .500 over the last 30 days, "
                "one panel per team, on a shared calendar.",
}
STREAK_CAPTIONS = {
    "season_vs_history.png": "This season's streak index for the ten fandoms "
                             "furthest from their own norm, their past seasons "
                             "behind them in gray.",
    "past_month.png": "The last 30 days game by game — the three streakiest "
                      "fandoms of the month, then the three steadiest.",
}
# spotlight images are <slug>_<suffix>.png
SPOTLIGHT_CAPTIONS = {
    "race": "The group against the whole field this month, cumulative weighted index.",
    "teams": "Per-team contribution — the full month against the last 7 days.",
    "history": "Every comparable month since 2022, this one picked out.",
    "timeline": "Game by game, with the streak picked out.",
    "bump": "Place in the year-to-date standings over the last 30 days.",
    "year": "Every season's cumulative index by day of year, this one in color.",
    "field": "All 88 city groups on this year's index, the group picked out.",
}

COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


# --- reading what the analysis wrote -----------------------------------------

BULLET_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.+)$")
HEADING_RE = re.compile(r"^##\s+(?:\d+\.\s*)?(.+)$")
TITLE_RE = re.compile(r"^#\s+(.+)$")
SUBHEAD_RE = re.compile(r"^\*(.+?)\*\s*(?:·\s*(.*))?$")
IMAGES_RE = re.compile(r"^Images:\s*(.+)$")
CODE_RE = re.compile(r"`([^`]+)`")


def parse_summary(text: str) -> tuple:
    """A summary.md as (title, sections).

    Both summaries the repo writes share a shape: a `#` title, then either one
    body (city of the day) or a run of `##` findings (the spotlight), each with
    a `*label*` subhead, `- **Label:** value` bullets, an optional table, and a
    trailing Images: line. Sections come back as dicts the post builders fill
    out with real image paths; a summary with no bullets at all — the "nothing
    notable today" spotlight — yields no sections.
    """
    title, sections = None, []
    current = None
    table = None

    def flush():
        nonlocal current, table
        if current is not None:
            if table and len(table) > 1:
                current["table"] = {"columns": table[0], "rows": table[2:]}
            if current["stats"] or current["images"] or current["table"]:
                sections.append(current)
        current, table = None, None

    def start(heading=None):
        return {"heading": heading, "subhead": None, "tag": None,
                "stats": [], "table": None, "images": []}

    for raw in text.splitlines():
        line = raw.strip()
        if m := TITLE_RE.match(line):
            title = m.group(1).strip()
            current = start()
            continue
        if m := HEADING_RE.match(line):
            flush()
            current = start(m.group(1).strip())
            continue
        if current is None:
            continue
        if m := SUBHEAD_RE.match(line):
            current["subhead"] = m.group(1).strip()
            tag = (m.group(2) or "").strip()
            current["tag"] = CODE_RE.sub(r"\1", tag) or None
            continue
        if m := BULLET_RE.match(line):
            current["stats"].append({"label": m.group(1).strip(),
                                     "value": m.group(2).strip()})
            continue
        if m := IMAGES_RE.match(line):
            current["images"] = CODE_RE.findall(m.group(1))
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            table = (table or []) + [cells]
            continue

    flush()
    return title, sections


def read(path: str):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def read_json(path: str):
    text = read(path)
    return json.loads(text) if text else None


def as_date(value: str) -> date:
    """Accept the two date spellings the analysis writes: 20260803, 2026-08-03."""
    value = value.strip()
    fmt = "%Y%m%d" if len(value) == 8 and value.isdigit() else "%Y-%m-%d"
    return datetime.strptime(value, fmt).date()


def pretty(day: date) -> str:
    return day.strftime("%B %-d, %Y")


# --- building the three kinds of post ----------------------------------------

def image(src_dir: str, name: str, prefix: str, caption: str) -> dict:
    """One image, named so three sources can share a post folder."""
    return {"src": os.path.join(src_dir, name), "file": f"{prefix}-{name}",
            "caption": caption, "alt": caption}


def name_images(post: dict) -> dict:
    """Give every image an alt that isn't a copy of its visible caption.

    The caption says what kind of chart it is; a screen reader that then hears
    the identical sentence as the alt text has learned nothing twice. The alt
    leads with whose chart it is — the group the section is about — so the
    subject arrives before the shape.
    """
    for section in post["sections"]:
        subject = section.get("subhead") or post.get("dek") or post["title"]
        for img in section["images"]:
            img["alt"] = f"{subject} — {img['caption'].rstrip('.')}"
    return post


def daily_post(src_dir: str = DAILY_DIR) -> dict | None:
    """The morning's city of the day: two charts and the numbers behind them."""
    text = read(os.path.join(src_dir, "summary.md"))
    if not text:
        return None
    title, sections = parse_summary(text)
    if not sections:
        return None
    body = sections[0]
    day = as_date(body["tag"]) if body["tag"] else None
    if day is None:                       # the date lives in the subhead's tag
        history = read_json(os.path.join(src_dir, "history.json")) or []
        if not history:
            return None
        day = as_date(history[-1]["date"])

    images = [image(src_dir, name, "daily", DAILY_CAPTIONS.get(name, ""))
              for name in body["images"]
              if os.path.exists(os.path.join(src_dir, name))]
    return name_images({
        "date": day.isoformat(),
        "kind": "daily",
        "title": title or "City of the day",
        "dek": body["subhead"] or "",
        "sections": [{"heading": None, "subhead": None, "stats": body["stats"],
                      "table": body["table"], "images": images}],
    })


def streakiness_post(src_dir: str = STREAK_DIR) -> dict | None:
    """The two standing charts, with the field's extremes as the numbers."""
    data = read_json(os.path.join(src_dir, "streakiness.json"))
    if not data:
        return None
    day = as_date(data["reference_date"])
    by_name = {r["name"]: r for r in data["measured"]}
    season = [by_name[n] for n in data.get("season_panel", []) if n in by_name]
    month = [by_name[n] for n in data.get("month_panel", []) if n in by_name]

    stats = [{"label": "City groups measured", "value": f"{len(data['measured'])}"}]
    if season:
        # the chart ranks by distance from the group's own median season, so
        # the headline number has to be measured the same way
        def norm(row):
            return statistics.median(h["index"] for h in row["history"])

        far = max(season, key=lambda r: abs(r["season"]["index"] - norm(r)))
        stats.append({"label": "Furthest from its own norm this year",
                      "value": f"{far['label']} — streak index "
                               f"{far['season']['index']:+.1f} over "
                               f"{far['season']['games']} games, against a usual "
                               f"{norm(far):+.1f}"})
    if month:
        top, bottom = month[0], month[-1]
        window = data.get("window_days", 30)
        stats += [
            {"label": f"Streakiest of the last {window} days",
             "value": f"{top['label']} — {top['month']['index']:+.1f}, longest run "
                      f"{top['month']['longest']['length']} "
                      f"{'wins' if top['month']['longest']['type'] == 'W' else 'losses'}"},
            {"label": f"Steadiest of the last {window} days",
             "value": f"{bottom['label']} — {bottom['month']['index']:+.1f}, wins and "
                      "losses taking turns"},
        ]
    stats.append({"label": "Reading the index",
                  "value": "+2 or more is clumpier than chance, −2 or less more "
                           "alternating than chance, 0 exactly as clumped as coin flips"})

    images = [image(src_dir, name, "streak", caption)
              for name, caption in STREAK_CAPTIONS.items()
              if os.path.exists(os.path.join(src_dir, name))]
    if not images:
        return None
    return name_images({
        "date": day.isoformat(),
        "kind": "streakiness",
        "title": "Streakiness — not how often they win, but how the wins arrive",
        "dek": f"All {len(data['measured'])} city groups, through {pretty(day)}",
        "sections": [{"heading": None, "subhead": None, "stats": stats,
                      "table": None, "images": images}],
    })


def spotlight_post(src_dir: str = WEEKLY_DIR) -> dict | None:
    """The weekly out-of-the-norm run: one post, one section per finding."""
    findings = read_json(os.path.join(src_dir, "findings.json"))
    text = read(os.path.join(src_dir, "summary.md"))
    if not findings or not text:
        return None
    day = as_date(findings["reference_date"])
    _, parsed = parse_summary(text)
    if not parsed or not findings["findings"]:
        return None                        # a run that found nothing isn't a post

    # findings.json owns the images, summary.md owns the prose; both are written
    # from the same ordered list, so pair them by position and let the label
    # confirm it rather than trusting order blindly.
    sections = []
    for n, finding in enumerate(findings["findings"]):
        body = next((s for s in parsed if s["subhead"] == finding["label"]
                     and s["tag"] == finding.get("kind")), None)
        if body is None:
            body = parsed[n] if n < len(parsed) else {"stats": [], "table": None}
        # the first spotlight runs listed their images only in summary.md
        images = []
        for name in finding.get("images") or body.get("images", []):
            if not os.path.exists(os.path.join(src_dir, name)):
                continue
            suffix = name.rsplit("_", 1)[-1].removesuffix(".png")
            images.append(image(src_dir, name, "spotlight",
                                SPOTLIGHT_CAPTIONS.get(suffix, "")))
        sections.append({"heading": finding["headline"], "subhead": finding["label"],
                         "stats": body["stats"], "table": body.get("table"),
                         "images": images})

    count = COUNT_WORDS.get(len(sections), str(len(sections)))
    noun = "fandom" if len(sections) == 1 else "fandoms"
    return name_images({
        "date": day.isoformat(),
        "kind": "spotlight",
        "title": f"Out of the norm — {count} {noun} off their own script",
        "dek": f"The weekly hunt across all 88 city groups, through {pretty(day)}",
        "sections": sections,
    })


# --- filing posts into content/posts -----------------------------------------

def digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def drop_stale(post: dict, posts_dir: str, published: list) -> dict:
    """Refuse to publish images the run didn't actually draw.

    An analysis that falls over between writing summary.md and rendering its
    charts leaves the previous run's images sitting at the same paths, and the
    post would then pair today's prose with yesterday's picture. A chart always
    carries its own date, so an image byte-identical to the one the last post
    of this kind published is last run's — not this one's.
    """
    previous = next((p for p in sorted(published, key=sort_key, reverse=True)
                     if p["kind"] == post["kind"] and p["date"] < post["date"]), None)
    if previous is None:
        return post
    folder = os.path.join(posts_dir, previous["date"])
    seen = set()
    for section in previous["sections"]:
        for img in section["images"]:
            path = os.path.join(folder, img["file"])
            if os.path.exists(path):
                seen.add(digest(path))

    for section in post["sections"]:
        kept = []
        for img in section["images"]:
            if digest(img["src"]) in seen:
                log.warning("%s: %s is unchanged since %s — the run wrote a summary "
                            "but no new chart, so it is left out",
                            post["date"], os.path.basename(img["src"]), previous["date"])
                continue
            kept.append(img)
        section["images"] = kept
    return post


def load_manifest(posts_dir: str) -> dict:
    """Every post the blog knows about, recent and archived, newest first."""
    recent = read_json(os.path.join(posts_dir, MANIFEST)) or {}
    old = read_json(os.path.join(posts_dir, ARCHIVE)) or {}
    posts = (recent.get("posts") or []) + (old.get("posts") or [])
    posts.sort(key=sort_key, reverse=True)
    return {"posts": posts}


def sort_key(post: dict) -> tuple:
    return (post["date"], -KINDS[post["kind"]]["rank"])


def file_post(post: dict, posts_dir: str) -> dict:
    """Copy a post's images into content/posts/<date>/ and strip source paths."""
    folder = os.path.join(posts_dir, post["date"])
    os.makedirs(folder, exist_ok=True)
    prefix = {"daily": "daily", "streakiness": "streak", "spotlight": "spotlight"}[post["kind"]]

    kept = set()
    for section in post["sections"]:
        for img in section["images"]:
            shutil.copyfile(img["src"], os.path.join(folder, img["file"]))
            kept.add(img["file"])
            del img["src"]

    # a replayed run can drop an image (a detector that no longer fires), and a
    # dated folder is meant to be exactly what the post shows
    for name in sorted(os.listdir(folder)):
        if name.startswith(f"{prefix}-") and name not in kept:
            os.remove(os.path.join(folder, name))
            log.info("Removed stale %s", os.path.join(post["date"], name))
    return post


# --- the permanent page for one post -----------------------------------------

def page_path(post: dict) -> str:
    """Where a post's own page lives, relative to the site root."""
    return f"content/posts/{post['date']}/{post['kind']}.html"


def summarize(post: dict) -> str:
    """A meta description: the dek, then as many numbers as fit in ~200 chars."""
    parts = [post.get("dek") or ""]
    for section in post["sections"]:
        for stat in section.get("stats") or []:
            parts.append(f"{stat['label']}: {stat['value']}")
    text = " · ".join(p for p in parts if p)
    return text if len(text) <= 200 else text[:197].rstrip(" ·") + "…"


def tag(name: str, content: str, attr: str = "name") -> str:
    return f'<meta {attr}="{name}" content="{esc(content)}">'


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def stats_html(section: dict, indent: str = "    ") -> list:
    """The numbers, open on a post's own page — it is the destination, not a
    card in a feed, so there is nothing to scan past."""
    out = []
    if section.get("stats"):
        out.append(f"{indent}<dl>")
        for stat in section["stats"]:
            out.append(f"{indent}  <div><dt>{esc(stat['label'])}</dt>"
                       f"<dd>{esc(stat['value'])}</dd></div>")
        out.append(f"{indent}</dl>")
    table = section.get("table")
    if table:
        head = "".join(f"<th>{esc(c)}</th>" for c in table["columns"])
        rows = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                       for row in table["rows"])
        out.append(f'{indent}<div class="table-wrap"><table><thead><tr>{head}</tr>'
                   f"</thead><tbody>{rows}</tbody></table></div>")
    return out


def render_page(post: dict, base_url: str) -> str:
    """A post as a standalone HTML page.

    The feed renders from JSON in the browser, which means a link to a post
    unfurls as nothing and crawls as nothing. This is the same post as a real
    page: the headline in the title, the lead chart as the social card, the
    charts and the numbers in the markup rather than assembled at runtime. It
    outlives its images — once they age out the page keeps the record and says
    so, so a link shared today still resolves in two years.
    """
    kind = KINDS[post["kind"]]["label"]
    images = [img for section in post["sections"] for img in section["images"]]
    url = f"{base_url}/{page_path(post)}"
    title = f"{post['title']} — Team Wins"
    root = "../../../"        # content/posts/<date>/ back to the site root

    head = [
        "<!DOCTYPE html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(title)}</title>",
        tag("description", summarize(post)),
        f'<link rel="canonical" href="{esc(url)}">',
        tag("og:type", "article", "property"),
        tag("og:site_name", "Team Wins", "property"),
        tag("og:title", post["title"], "property"),
        tag("og:description", summarize(post), "property"),
        tag("og:url", url, "property"),
        tag("article:published_time", post["date"], "property"),
    ]
    if images:
        card = f"{base_url}/content/posts/{post['date']}/{images[0]['file']}"
        head += [tag("og:image", card, "property"),
                 tag("og:image:alt", images[0]["alt"], "property"),
                 tag("twitter:card", "summary_large_image"),
                 tag("twitter:image", card)]
    else:
        head.append(tag("twitter:card", "summary"))
    head += [
        '<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/'
        '2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>'
        "🏆</text></svg>\">",
        f'<link rel="stylesheet" href="{root}blog.css">',
        "</head>", "<body>", "<main>",
        "  <header>", "    <h1>Team Wins</h1>", "    <nav>",
        f'      <a href="{root}index.html">city index</a>',
        f'      <a href="{root}my-teams.html">my teams</a>',
        f'      <a href="{root}groups.html">city groups</a>',
        f'      <a href="{root}blog.html">blog</a>',
        f'      <a href="{root}about.html">about</a>',
        "    </nav>", "  </header>",
        f'  <p class="as-of"><a href="{root}blog.html">← every post</a></p>',
        '  <article class="post post-page">',
        f'    <span class="badge" data-kind="{post["kind"]}">{esc(kind)}</span>',
        f'    <p class="dateline"><time datetime="{post["date"]}">'
        f"{esc(pretty(as_date(post['date'])))}</time></p>",
        f'    <h2>{esc(post["title"])}</h2>',
    ]
    if post.get("dek"):
        head.append(f'    <p class="dek">{esc(post["dek"])}</p>')

    body = []
    for section in post["sections"]:
        body.append('    <section class="finding">')
        if section.get("heading"):
            body.append(f'      <h3>{esc(section["heading"])}</h3>')
        if section.get("subhead"):
            body.append(f'      <p class="subhead">{esc(section["subhead"])}</p>')
        body += stats_html(section, "      ")
        for img in section["images"]:
            body += [
                "      <figure>",
                f'        <a href="{esc(img["file"])}" title="Open the full-size chart">'
                f'<img src="{esc(img["file"])}" alt="{esc(img["alt"])}" '
                'loading="lazy" decoding="async"></a>',
                f'        <figcaption>{esc(img["caption"])}</figcaption>',
                "      </figure>",
            ]
        body.append("    </section>")

    if post.get("archived"):
        body.append('    <p class="note archived-note">The charts from this post have '
                    "aged out of the image window and were deleted from the "
                    "repository. The numbers above are the whole record; the images "
                    "are still in git history.</p>")

    return "\n".join(head + body + ["  </article>", "</main>", "</body>", "</html>", ""])


def write_page(post: dict, posts_dir: str, base_url: str) -> None:
    folder = os.path.join(posts_dir, post["date"])
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{post['kind']}.html"), "w") as f:
        f.write(render_page(post, base_url))
    post["page"] = page_path(post)


def archive_post(post: dict, posts_dir: str) -> dict:
    """Age a post out of the image window without losing the post.

    Deleting the folder would break every link ever shared to it, so only the
    pixels go: the images are ~99% of the bytes and the headline, the numbers
    and the page itself cost a couple of KB a post — a permanent record for
    about 2 MB a year.
    """
    folder = os.path.join(posts_dir, post["date"])
    gone = 0
    for section in post["sections"]:
        for img in section["images"]:
            path = os.path.join(folder, img["file"])
            if os.path.exists(path):
                os.remove(path)
                gone += 1
        section["images"] = []
    post["archived"] = True
    if gone:
        log.info("Archived %s %s — %d image%s removed, the record stays",
                 post["date"], post["kind"], gone, "" if gone == 1 else "s")
    return post


def publish(posts: list, posts_dir: str = POSTS_DIR, retain_days: int = RETAIN_DAYS,
            prune: bool = True, base_url: str = BASE_URL) -> dict:
    """File every post, write its page, and age the old ones out of their images."""
    os.makedirs(posts_dir, exist_ok=True)
    kept = load_manifest(posts_dir)["posts"]

    filed = []
    for post in posts:
        # a post is compared against what came before it, so stale-image
        # detection reads the manifest as it stood before this post
        drop_stale(post, posts_dir, kept)
        if not any(section["images"] for section in post["sections"]):
            log.warning("Nothing drawn for %s %s — leaving the post alone",
                        post["date"], post["kind"])
            continue
        file_post(post, posts_dir)
        filed.append(post)
        log.info("Published %s %s — %s", post["date"], post["kind"], post["title"])

    # a re-run replaces its own entry; a run that published nothing leaves the
    # entry that is already there
    kept = [p for p in kept
            if not any(p["date"] == new["date"] and p["kind"] == new["kind"]
                       for new in filed)] + filed
    kept.sort(key=sort_key, reverse=True)

    # rebuild alt text for every post, not just today's: a post filed by an
    # older version of this script keeps whatever alt text that version wrote,
    # and re-deriving it here means the whole archive picks up an improvement
    # on the next morning's run instead of only new posts getting it
    kept = [name_images(p) for p in kept]

    recent, archived = kept, []
    if prune and kept:
        newest = as_date(kept[0]["date"])
        recent = [p for p in kept
                  if (newest - as_date(p["date"])).days <= retain_days]
        archived = [archive_post(p, posts_dir) for p in kept[len(recent):]]

    # every page is rewritten every run, so a change to the template reaches
    # posts that were filed before it existed. Identical output is a no-op to git.
    for post in recent + archived:
        write_page(post, posts_dir, base_url)

    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "retain_days": retain_days, "base_url": base_url,
           "archive": ARCHIVE if archived else None,
           "archived_count": len(archived), "posts": recent}
    with open(os.path.join(posts_dir, MANIFEST), "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    if archived or os.path.exists(os.path.join(posts_dir, ARCHIVE)):
        with open(os.path.join(posts_dir, ARCHIVE), "w") as f:
            json.dump({"posts": archived}, f, indent=1)
            f.write("\n")
    return {**out, "posts": recent + archived}


def collect(daily_dir=DAILY_DIR, streak_dir=STREAK_DIR, weekly_dir=WEEKLY_DIR) -> list:
    """Whatever the three analysis folders currently hold, as posts."""
    builders = ((daily_post, daily_dir), (streakiness_post, streak_dir),
                (spotlight_post, weekly_dir))
    posts = []
    for build, folder in builders:
        try:
            post = build(folder)
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            log.warning("Skipping %s: %s", folder, err)
            continue
        if post:
            posts.append(post)
        else:
            log.info("Nothing to publish from %s", folder)
    return posts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-dir", default=DAILY_DIR)
    parser.add_argument("--streak-dir", default=STREAK_DIR)
    parser.add_argument("--weekly-dir", default=WEEKLY_DIR)
    parser.add_argument("--posts-dir", default=POSTS_DIR)
    parser.add_argument("--retain-days", type=int, default=RETAIN_DAYS,
                        help=f"How far back the blog lists (default {RETAIN_DAYS}).")
    parser.add_argument("--no-prune", action="store_true",
                        help="Keep images on posts that have aged out of the window.")
    parser.add_argument("--base-url", default=BASE_URL,
                        help="Site root, for each post page's canonical URL and "
                             f"social card (default {BASE_URL}).")
    args = parser.parse_args()

    posts = collect(args.daily_dir, args.streak_dir, args.weekly_dir)
    manifest = publish(posts, args.posts_dir, args.retain_days, not args.no_prune,
                       args.base_url)
    log.info("Manifest: %d posts (%d archived), newest %s", len(manifest["posts"]),
             manifest["archived_count"],
             manifest["posts"][0]["date"] if manifest["posts"] else "—")


if __name__ == "__main__":
    main()
