"""Tests for publish_blog.py (summary parsing, post building, retention)."""

import json
import os
from datetime import date, timedelta

import pytest

from publish_blog import (collect, daily_post, parse_summary, publish,
                          spotlight_post, streakiness_post)

DAILY_SUMMARY = """# City of the day — Philadelphia
*Philadelphia: Phillies/76ers/Flyers/Eagles* · 2026-08-03

- **2026 so far:** 120-111, -9.4 weighted over 231 games; longest run 7 straight wins
- **Order of results:** about as clumped as coin flips (-0.6)

| Team | Last 30 days | Weighted | Longest run |
|---|---|---|---|
| Phillies | 10-14 | -9.0 | 4 straight losses |

Images: `season.png` · `games.png`
"""

WEEKLY_SUMMARY = """# Fandom spotlight — 2026-08-02

## 1. 🧊 New York is having its worst year on record
*New York: Mets/Nets/Islanders/Jets* · `year`

- **2026 so far (through August 2):** 78-130, -206.7 weighted
- **Against the field:** 85th of 88 city groups on the year

Images: `new-york-4_year.png` · `new-york-4_field.png`

## 2. 🏆 San Antonio is having its best year on record
*San Antonio: Spurs* · `year`

- **2026 so far (through August 2):** 51-21, +133.5 weighted

Images: `san-antonio_year.png`
"""

EMPTY_SUMMARY = """# Fandom spotlight — 2026-08-01

Nothing notable today (too early in the month, or nothing in the tails).
"""


def write(folder, name, text):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w") as f:
        f.write(text)


def png(folder, name, body=b""):
    """A file that only has to exist — nothing here opens it as an image. The
    body varies where a test needs two runs to have drawn different charts."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + body)


def daily_dir(tmp_path, summary=DAILY_SUMMARY, name="daily", body=b""):
    folder = str(tmp_path / name)
    write(folder, "summary.md", summary)
    png(folder, "season.png", body)
    png(folder, "games.png", body)
    return folder


def weekly_dir(tmp_path, summary=WEEKLY_SUMMARY, images=True):
    folder = str(tmp_path / "weekly")
    write(folder, "summary.md", summary)
    findings = {
        "reference_date": "20260802",
        "findings": [
            {"kind": "year", "label": "New York: Mets/Nets/Islanders/Jets",
             "headline": "New York is having its worst year on record",
             "slug": "new-york-4",
             "images": ["new-york-4_year.png", "new-york-4_field.png"]},
            {"kind": "year", "label": "San Antonio: Spurs",
             "headline": "San Antonio is having its best year on record",
             "slug": "san-antonio", "images": ["san-antonio_year.png"]},
        ],
    }
    write(folder, "findings.json", json.dumps(findings))
    if images:
        for finding in findings["findings"]:
            for name in finding["images"]:
                png(folder, name)
    return folder


def streak_dir(tmp_path):
    folder = str(tmp_path / "streakiness")
    payload = {
        "reference_date": "20260802",
        "window_days": 30,
        "measured": [
            {"name": "A 1", "city": "A", "label": "A: Aces",
             "season": {"index": 2.5, "games": 200},
             "month": {"index": 3.3, "longest": {"length": 12, "type": "L"}},
             "history": [{"year": 2024, "index": -0.5}, {"year": 2025, "index": -0.9}]},
            {"name": "B 1", "city": "B", "label": "B: Bees",
             "season": {"index": -0.2, "games": 190},
             "month": {"index": -2.1, "longest": {"length": 3, "type": "W"}},
             "history": [{"year": 2024, "index": -0.1}, {"year": 2025, "index": 0.1}]},
        ],
        "season_panel": ["A 1", "B 1"],
        "month_panel": ["A 1", "B 1"],
    }
    write(folder, "streakiness.json", json.dumps(payload))
    png(folder, "season_vs_history.png")
    png(folder, "past_month.png")
    return folder


# --- parsing what the analysis wrote -----------------------------------------

def test_parses_a_daily_summary_into_one_section():
    title, sections = parse_summary(DAILY_SUMMARY)
    assert title == "City of the day — Philadelphia"
    assert len(sections) == 1
    body = sections[0]
    assert body["subhead"] == "Philadelphia: Phillies/76ers/Flyers/Eagles"
    assert body["tag"] == "2026-08-03"
    assert body["stats"][0]["label"] == "2026 so far"
    assert body["table"]["columns"][0] == "Team"
    assert body["table"]["rows"] == [["Phillies", "10-14", "-9.0", "4 straight losses"]]
    assert body["images"] == ["season.png", "games.png"]


def test_parses_a_spotlight_summary_into_one_section_per_finding():
    title, sections = parse_summary(WEEKLY_SUMMARY)
    assert title.startswith("Fandom spotlight")
    assert len(sections) == 2
    assert sections[0]["heading"].endswith("worst year on record")   # the "1. " is dropped
    assert sections[0]["tag"] == "year"                              # backticks stripped
    assert len(sections[0]["stats"]) == 2


def test_a_summary_with_no_findings_has_no_sections():
    _, sections = parse_summary(EMPTY_SUMMARY)
    assert sections == []


# --- building posts ----------------------------------------------------------

def test_daily_post_reads_its_date_from_the_summary(tmp_path):
    post = daily_post(daily_dir(tmp_path))
    assert post["date"] == "2026-08-03"
    assert post["kind"] == "daily"
    assert post["title"] == "City of the day — Philadelphia"
    assert [i["file"] for i in post["sections"][0]["images"]] == [
        "daily-season.png", "daily-games.png"]
    assert all(i["alt"] for i in post["sections"][0]["images"])


def test_daily_post_skips_images_that_were_not_drawn(tmp_path):
    folder = daily_dir(tmp_path)
    os.remove(os.path.join(folder, "games.png"))       # --no-images run
    post = daily_post(folder)
    assert [i["file"] for i in post["sections"][0]["images"]] == ["daily-season.png"]


def test_spotlight_post_pairs_findings_with_their_prose(tmp_path):
    post = spotlight_post(weekly_dir(tmp_path))
    assert post["date"] == "2026-08-02"
    assert post["title"] == "Out of the norm — two fandoms off their own script"
    first, second = post["sections"]
    assert first["heading"].startswith("New York")
    assert first["stats"][1]["label"] == "Against the field"
    assert [i["file"] for i in second["images"]] == ["spotlight-san-antonio_year.png"]


def test_a_spotlight_run_that_found_nothing_is_not_a_post(tmp_path):
    folder = weekly_dir(tmp_path, summary=EMPTY_SUMMARY)
    write(folder, "findings.json", json.dumps({"reference_date": "20260801",
                                               "findings": []}))
    assert spotlight_post(folder) is None


def test_streakiness_post_measures_distance_from_the_groups_own_norm(tmp_path):
    post = streakiness_post(streak_dir(tmp_path))
    assert post["date"] == "2026-08-02"
    stats = {s["label"]: s["value"] for s in post["sections"][0]["stats"]}
    # A is +2.5 against a usual -0.7; B barely moved, so A is the one named
    assert "A: Aces" in stats["Furthest from its own norm this year"]
    assert "A: Aces" in stats["Streakiest of the last 30 days"]
    assert "B: Bees" in stats["Steadiest of the last 30 days"]


def test_missing_folders_are_skipped_not_fatal(tmp_path):
    assert collect(str(tmp_path / "nope"), str(tmp_path / "nope"),
                   str(tmp_path / "nope")) == []


# --- filing and retention ----------------------------------------------------

def test_publish_copies_images_and_writes_a_manifest(tmp_path):
    posts_dir = str(tmp_path / "posts")
    posts = collect(daily_dir(tmp_path), streak_dir(tmp_path), weekly_dir(tmp_path))
    manifest = publish(posts, posts_dir)

    assert [p["kind"] for p in manifest["posts"]] == ["daily", "spotlight", "streakiness"]
    assert os.path.exists(os.path.join(posts_dir, "2026-08-03", "daily-season.png"))
    assert os.path.exists(os.path.join(posts_dir, "2026-08-02", "streak-past_month.png"))
    assert os.path.exists(os.path.join(posts_dir, "2026-08-02",
                                       "spotlight-new-york-4_year.png"))
    with open(os.path.join(posts_dir, "index.json")) as f:
        assert json.load(f)["posts"][0]["date"] == "2026-08-03"
    # the manifest describes a post by the file it copied, not where it came from
    assert "src" not in manifest["posts"][0]["sections"][0]["images"][0]


def test_republishing_a_date_replaces_it_rather_than_duplicating(tmp_path):
    posts_dir = str(tmp_path / "posts")
    publish(collect(daily_dir(tmp_path), str(tmp_path / "x"), str(tmp_path / "x")),
            posts_dir)
    manifest = publish(collect(daily_dir(tmp_path), str(tmp_path / "x"),
                               str(tmp_path / "x")), posts_dir)
    assert len(manifest["posts"]) == 1


def test_a_replayed_run_drops_images_it_no_longer_draws(tmp_path):
    posts_dir = str(tmp_path / "posts")
    folder = weekly_dir(tmp_path)
    publish([spotlight_post(folder)], posts_dir)
    assert os.path.exists(os.path.join(posts_dir, "2026-08-02",
                                       "spotlight-san-antonio_year.png"))

    findings = {"reference_date": "20260802",
                "findings": [{"kind": "year", "label": "New York: Mets/Nets/Islanders/Jets",
                              "headline": "New York is having its worst year on record",
                              "slug": "new-york-4",
                              "images": ["new-york-4_year.png"]}]}
    write(folder, "findings.json", json.dumps(findings))
    publish([spotlight_post(folder)], posts_dir)
    assert not os.path.exists(os.path.join(posts_dir, "2026-08-02",
                                           "spotlight-san-antonio_year.png"))


def aged_out(tmp_path, days=100):
    """A daily post 100 days older than the fixture's own date."""
    old = (date(2026, 8, 3) - timedelta(days=days)).isoformat()
    folder = daily_dir(tmp_path, summary=DAILY_SUMMARY.replace("2026-08-03", old),
                       name="old", body=b"an older morning")
    return old, daily_post(folder)


def test_posts_outside_the_window_are_pruned(tmp_path):
    posts_dir = str(tmp_path / "posts")
    old, post = aged_out(tmp_path)
    publish([post], posts_dir)
    assert os.path.isdir(os.path.join(posts_dir, old))

    manifest = publish([daily_post(daily_dir(tmp_path))], posts_dir, retain_days=90)
    assert [p["date"] for p in manifest["posts"]] == ["2026-08-03"]
    assert not os.path.isdir(os.path.join(posts_dir, old))


def test_no_prune_keeps_everything(tmp_path):
    posts_dir = str(tmp_path / "posts")
    _, post = aged_out(tmp_path)
    publish([post], posts_dir, prune=False)
    manifest = publish([daily_post(daily_dir(tmp_path))], posts_dir, prune=False)
    assert len(manifest["posts"]) == 2


@pytest.mark.parametrize("spelling", ["20260802", "2026-08-02"])
def test_both_date_spellings_land_on_the_same_day(tmp_path, spelling):
    folder = streak_dir(tmp_path)
    with open(os.path.join(folder, "streakiness.json")) as f:
        payload = json.load(f)
    payload["reference_date"] = spelling
    write(folder, "streakiness.json", json.dumps(payload))
    assert streakiness_post(folder)["date"] == "2026-08-02"


# --- an analysis that fell over halfway ---------------------------------------

def test_a_summary_without_fresh_images_does_not_repost_yesterdays_charts(tmp_path):
    """city_of_the_day.py writes summary.md before it renders. If the render
    dies, today's prose is on disk next to yesterday's PNGs."""
    posts_dir = str(tmp_path / "posts")
    folder = daily_dir(tmp_path)
    publish([daily_post(folder)], posts_dir)

    tomorrow = DAILY_SUMMARY.replace("2026-08-03", "2026-08-04").replace(
        "Philadelphia", "St. Louis")
    write(folder, "summary.md", tomorrow)          # images left untouched
    manifest = publish([daily_post(folder)], posts_dir)

    assert [p["date"] for p in manifest["posts"]] == ["2026-08-03"]
    assert not os.path.isdir(os.path.join(posts_dir, "2026-08-04"))


def test_a_partial_render_publishes_only_the_chart_that_was_drawn(tmp_path):
    posts_dir = str(tmp_path / "posts")
    folder = daily_dir(tmp_path)
    publish([daily_post(folder)], posts_dir)

    write(folder, "summary.md", DAILY_SUMMARY.replace("2026-08-03", "2026-08-04"))
    with open(os.path.join(folder, "season.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nredrawn")       # only this one got redrawn
    manifest = publish([daily_post(folder)], posts_dir)

    fresh = next(p for p in manifest["posts"] if p["date"] == "2026-08-04")
    assert [i["file"] for i in fresh["sections"][0]["images"]] == ["daily-season.png"]


def test_a_weekly_folder_republished_under_its_own_date_is_not_treated_as_stale(tmp_path):
    """The weekly folders sit unchanged from Wednesday to Wednesday, and the
    publisher runs every morning — re-filing the same date must be a no-op,
    not a staleness verdict."""
    posts_dir = str(tmp_path / "posts")
    folder = weekly_dir(tmp_path)
    publish([spotlight_post(folder)], posts_dir)
    manifest = publish([spotlight_post(folder)], posts_dir)

    assert len(manifest["posts"]) == 1
    images = [i["file"] for s in manifest["posts"][0]["sections"] for i in s["images"]]
    assert len(images) == 3
    assert os.path.exists(os.path.join(posts_dir, "2026-08-02", images[0]))
