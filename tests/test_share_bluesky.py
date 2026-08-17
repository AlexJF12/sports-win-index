"""Tests for share_bluesky.py — what's worth sharing, what goes out, the record.

Nothing here touches the network: the client is driven through a fake requests
session that records every XRPC call and replays canned responses.
"""

import json
import os
import struct

import pytest

import share_bluesky
from share_bluesky import (Bluesky, BlueskyError, compose, credentials,
                           finding, fingerprint, hook, image_post, lead_image,
                           pending, png_size, share, standing_of, trim)

BASE_URL = "https://alexjf12.github.io/sports-win-index"

# The four bullets a city-of-the-day post carries, worded exactly as
# city_of_the_day.write_summary writes them. NOTABLE is a month at the bottom
# of its own record; ORDINARY is a team having a completely unremarkable week,
# which is most mornings and the whole reason the gate exists.
NOTABLE = [
    {"label": "2026 so far",
     "value": "58-64, -32.7 weighted over 122 games; longest run 7 straight wins"},
    {"label": "Order of results", "value": "about as clumped as coin flips (+0.2)"},
    {"label": "Last 30 days", "value": "12-12, +0.0 weighted; longest run 3 straight wins"},
    {"label": "August 2026 through day 13",
     "value": "4-7, -6.8 weighted — the worst of the 11 on record"},
]
ORDINARY = [
    NOTABLE[0], NOTABLE[1], NOTABLE[2],
    {"label": "August 2026 through day 13",
     "value": "4-7, -6.8 weighted — the 7th-best of the 11 on record"},
]


# --- fixtures -----------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text or json.dumps(payload)
        self.content = b"x"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(self.text, response=self)

    def json(self):
        return self._payload


class FakeSession:
    """Answers the four calls the client makes, and keeps the transcript."""

    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}     # method -> (status, text)
        self.created = 0

    def post(self, url, headers=None, timeout=None, **kwargs):
        method = url.rsplit("/", 1)[-1]
        self.calls.append({"method": method, "headers": headers or {}, **kwargs})
        if method in self.failures:
            status, text = self.failures[method]
            return FakeResponse({}, status, text)
        if method == "com.atproto.server.createSession":
            return FakeResponse({"did": "did:plc:test", "accessJwt": "token"})
        if method == "com.atproto.repo.uploadBlob":
            return FakeResponse({"blob": {"$type": "blob", "ref": {"$link": "bafy"}}})
        if method == "com.atproto.repo.createRecord":
            self.created += 1
            return FakeResponse({"uri": f"at://did:plc:test/app.bsky.feed.post/rk{self.created}",
                                 "cid": f"cid{self.created}"})
        if method == "com.atproto.repo.deleteRecord":
            return FakeResponse({})
        raise AssertionError(f"unexpected call {method}")

    def of(self, method):
        return [c for c in self.calls if c["method"] == method]


def png(path, body=b"chart", size=None):
    """A file that mostly only has to exist. `size` writes a real IHDR, for the
    one thing that reads the header."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = b"\x89PNG\r\n\x1a\n"
    if size:
        head += b"\x00\x00\x00\x0d" + b"IHDR" + struct.pack(">II", *size)
    with open(path, "wb") as f:
        f.write(head + body)


def post(date="2026-08-13", kind="daily", title="City of the day — Baltimore",
         images=("daily-season.png",), archived=False, stats=None):
    return {
        "date": date, "kind": kind, "title": title, "dek": "Baltimore: Orioles/Ravens",
        "archived": archived,
        "sections": [{"heading": None, "subhead": None,
                      "stats": NOTABLE if stats is None else list(stats),
                      "table": None,
                      "images": [{"file": name, "caption": "c", "alt": f"alt {name}"}
                                 for name in images]}],
        "page": f"content/posts/{date}/{kind}.html",
    }


def manifest(*posts):
    return {"generated": "2026-08-13", "base_url": BASE_URL, "posts": list(posts)}


@pytest.fixture
def posts_dir(tmp_path):
    folder = tmp_path / "posts"
    folder.mkdir()
    return str(folder)


def place(posts_dir, post, body=b"chart", size=None):
    """Put a post's images where the manifest says they are."""
    for section in post["sections"]:
        for img in section["images"]:
            png(os.path.join(posts_dir, post["date"], img["file"]), body, size)
    return post


# --- the gate: is there anything in this post? --------------------------------

def test_an_ordinary_morning_is_not_worth_a_post():
    assert finding(post(stats=ORDINARY)) is None


def test_the_worst_month_on_record_is():
    assert "the worst of the 11" in finding(post(stats=NOTABLE))


def test_the_best_month_on_record_is():
    stats = ORDINARY[:3] + [{"label": "August 2026 through day 13",
                             "value": "18-4, +31.2 weighted — the best of the 11 on record"}]
    assert "the best of the 11" in finding(post(stats=stats))


def test_second_best_is_not_a_headline():
    stats = ORDINARY[:3] + [{"label": "August 2026 through day 13",
                             "value": "12-6, +8.0 weighted — the second-best of the 11 on record"}]
    assert finding(post(stats=stats)) is None


def test_a_shallow_record_does_not_count_as_on_record():
    stats = ORDINARY[:3] + [{"label": "August 2026 through day 13",
                             "value": "4-7, -6.8 weighted — the worst of the 3 on record"}]
    assert finding(post(stats=stats)) is None


def test_a_long_run_inside_the_window_is_the_story():
    stats = [ORDINARY[0], ORDINARY[1],
             {"label": "Last 30 days",
              "value": "8-16, -18.0 weighted; longest run 6 straight losses"},
             ORDINARY[3]]
    assert "6 straight losses" in finding(post(stats=stats))


def test_the_same_run_across_a_whole_season_is_not():
    stats = [{"label": "2026 so far",
              "value": "78-77, +8.8 weighted over 155 games; longest run 7 straight wins"},
             ORDINARY[1], ORDINARY[2], ORDINARY[3]]
    assert finding(post(stats=stats)) is None


def test_a_very_long_season_run_is():
    stats = [{"label": "2026 so far",
              "value": "61-108, -152.1 weighted over 169 games; longest run 16 straight losses"},
             ORDINARY[1], ORDINARY[2], ORDINARY[3]]
    assert "16 straight losses" in finding(post(stats=stats))


def test_results_outside_the_chance_band_are():
    stats = [ORDINARY[0],
             {"label": "Order of results", "value": "clumpier than chance (+3.4)"},
             ORDINARY[2], ORDINARY[3]]
    assert "clumpier than chance" in finding(post(stats=stats))


def test_inside_the_band_is_not():
    assert finding(post(stats=ORDINARY)) is None


def test_a_post_with_no_numbers_at_all_has_no_finding():
    assert finding(post(stats=[])) is None


@pytest.mark.parametrize("text,expected", [
    ("the best of the 11 on record", (1, 11)),
    ("the worst of the 11 on record", (11, 11)),
    ("the second-best of the 11 on record", (2, 11)),
    ("the 7th-best of the 11 on record", (7, 11)),
    # the spotlight's phrasing: no article, and it may name what it ranks
    ("2nd-best of 61 months on record", (2, 61)),
    ("1st-worst July of the 5 since 2022", (5, 5)),
    ("1st-best of the 11 years on record, at the same point", (1, 11)),
    ("the only one on record", None),
    ("nothing standing-shaped here", None),
])
def test_standing_phrases_parse(text, expected):
    assert standing_of(text) == expected


def test_the_weekly_reads_never_need_a_finding(posts_dir):
    for kind in ("spotlight", "streakiness"):
        p = place(posts_dir, post(kind=kind, stats=[], images=(f"{kind}-a.png",)))
        assert len(pending(manifest(p), {}, posts_dir)) == 1


def test_an_ordinary_daily_is_skipped(posts_dir):
    p = place(posts_dir, post(stats=ORDINARY))
    assert pending(manifest(p), {}, posts_dir) == []


def test_share_daily_always_posts_it_anyway(posts_dir):
    p = place(posts_dir, post(stats=ORDINARY))
    assert len(pending(manifest(p), {}, posts_dir, share_daily="always")) == 1


def test_share_daily_never_skips_even_a_notable_one(posts_dir):
    p = place(posts_dir, post(stats=NOTABLE))
    assert pending(manifest(p), {}, posts_dir, share_daily="never") == []


# --- the hook -----------------------------------------------------------------

def test_a_daily_leads_with_the_fandom_then_the_finding():
    claim, detail = hook(post(stats=NOTABLE)).splitlines()
    assert claim == "Baltimore: Orioles/Ravens"
    assert "the worst of the 11 on record" in detail


def test_a_daily_never_leads_with_the_filing_label():
    assert "City of the day" not in hook(post(stats=NOTABLE))


def test_a_spotlight_leads_with_its_headline():
    p = post(kind="spotlight", stats=[{"label": "2026 so far", "value": "45-17, +124.6 weighted"}])
    p["sections"][0]["heading"] = "Raleigh is having its best year on record"
    claim, detail = hook(p).splitlines()
    assert claim == "Raleigh is having its best year on record"
    assert "+124.6 weighted" in detail


def test_streakiness_leads_with_the_streakiest_fandom():
    p = post(kind="streakiness", title="Streakiness — how the wins arrive",
             stats=[{"label": "City groups measured", "value": "88"},
                    {"label": "Streakiest of the last 30 days",
                     "value": "Miami — +3.3, longest run 12 losses"}])
    assert "Miami — +3.3" in hook(p)


def test_a_post_with_nothing_to_say_still_yields_a_line():
    assert hook(post(kind="spotlight", stats=[])) != ""


# --- the record that goes out -------------------------------------------------

def test_the_chart_is_a_full_size_image_not_a_card():
    record = image_post("hook\n\nhttps://x.test/p", [], {"$type": "blob"}, "alt text")
    assert record["embed"]["$type"] == "app.bsky.embed.images"
    assert len(record["embed"]["images"]) == 1
    assert record["embed"]["images"][0]["alt"] == "alt text"
    assert record["text"].startswith("hook")
    assert record["createdAt"].endswith("Z")


def test_the_aspect_ratio_rides_along_when_it_is_known():
    with_ratio = image_post("t", [], {}, "a", {"width": 1600, "height": 920})
    assert with_ratio["embed"]["images"][0]["aspectRatio"] == {"width": 1600, "height": 920}
    assert "aspectRatio" not in image_post("t", [], {}, "a")["embed"]["images"][0]


def test_the_url_is_linked_at_utf8_byte_offsets():
    # the em dash is three bytes, so a facet counted in characters lands short
    url = "https://x.test/p.html"
    text, facets = compose("Baltimore — worst August on record", url)
    span = facets[0]["index"]
    assert text.encode()[span["byteStart"]:span["byteEnd"]].decode() == url
    assert facets[0]["features"][0]["uri"] == url
    assert facets[0]["features"][0]["$type"] == "app.bsky.richtext.facet#link"


def test_an_emoji_headline_still_links_the_right_bytes():
    url = "https://x.test/p.html"
    text, facets = compose("🧊 New York is having its worst year on record", url)
    span = facets[0]["index"]
    assert text.encode()[span["byteStart"]:span["byteEnd"]].decode() == url


def test_the_hook_is_trimmed_to_fit_around_the_url():
    url = "https://x.test/" + "p" * 60 + ".html"
    text, facets = compose("word " * 100, url)
    assert len(text) <= share_bluesky.TEXT_LIMIT
    assert text.endswith(url)
    assert "…" in text
    span = facets[0]["index"]
    assert text.encode()[span["byteStart"]:span["byteEnd"]].decode() == url


def test_a_hook_that_fits_is_left_alone():
    text, _ = compose("Short hook", "https://x.test/p")
    assert text == "Short hook\n\nhttps://x.test/p"


def test_trim_cuts_at_a_word_boundary():
    assert trim("the quick brown fox jumps", 20) == "the quick brown…"
    assert trim("unchanged", 20) == "unchanged"


def test_png_size_reads_the_header(tmp_path):
    path = str(tmp_path / "chart.png")
    png(path, size=(1600, 920))
    assert png_size(path) == {"width": 1600, "height": 920}


def test_png_size_gives_up_quietly_on_something_that_is_not_one(tmp_path):
    path = str(tmp_path / "nope.png")
    png(path)                                  # signature but no IHDR
    assert png_size(path) is None


# --- choosing what to share ---------------------------------------------------

def test_shares_a_post_it_has_never_seen(posts_dir):
    p = place(posts_dir, post())
    due = pending(manifest(p), {}, posts_dir)
    assert [d["key"] for d in due] == ["2026-08-13/daily"]
    assert due[0]["url"] == f"{BASE_URL}/content/posts/2026-08-13/daily.html"
    assert due[0]["image"].endswith("2026-08-13/daily-season.png")
    assert due[0]["alt"] == "alt daily-season.png"
    assert due[0]["supersedes"] is None


def test_shares_the_first_image_only(posts_dir):
    p = place(posts_dir, post(images=("daily-season.png", "daily-month.png")))
    due = pending(manifest(p), {}, posts_dir)
    assert len(due) == 1
    assert due[0]["image"].endswith("daily-season.png")


def test_the_lead_of_a_multi_section_post_is_the_first_sections_first_chart():
    p = post(kind="spotlight", images=("spotlight-a_race.png",))
    p["sections"] += [{"images": [{"file": "spotlight-b_race.png", "alt": "b"}]}]
    assert lead_image(p)["file"] == "spotlight-a_race.png"


def test_a_section_with_no_charts_is_stepped_over():
    p = post(images=())
    p["sections"] += [{"images": [{"file": "spotlight-b_race.png", "alt": "b"}]}]
    assert lead_image(p)["file"] == "spotlight-b_race.png"


def test_an_unchanged_post_is_not_shared_twice(posts_dir):
    p = place(posts_dir, post())
    mark = pending(manifest(p), {}, posts_dir)[0]["fingerprint"]
    state = {"shared": {"2026-08-13/daily": {"uri": "at://x", "fingerprint": mark}}}
    assert pending(manifest(p), state, posts_dir) == []


def test_an_updated_post_is_shared_again(posts_dir):
    p = place(posts_dir, post())
    state = {"shared": {"2026-08-13/daily": {"uri": "at://old", "fingerprint": "stale"}}}
    due = pending(manifest(p), state, posts_dir)
    assert len(due) == 1
    assert due[0]["supersedes"]["uri"] == "at://old"


def test_a_redrawn_chart_counts_as_an_update(posts_dir):
    p = place(posts_dir, post())
    first = pending(manifest(p), {}, posts_dir)[0]["fingerprint"]
    place(posts_dir, p, body=b"redrawn")          # same post, new pixels
    assert pending(manifest(p), {}, posts_dir)[0]["fingerprint"] != first


def test_reworded_text_counts_as_an_update():
    before = fingerprint("hook", "u", "alt", None)
    assert fingerprint("hook, revised", "u", "alt", None) != before
    assert fingerprint("hook", "u", "different alt", None) != before


def test_older_posts_are_out_of_the_window(posts_dir):
    new = place(posts_dir, post(date="2026-08-13"))
    old = place(posts_dir, post(date="2026-07-30"))
    assert [d["key"] for d in pending(manifest(new, old), {}, posts_dir)] == \
        ["2026-08-13/daily"]
    # ...unless asked for
    assert len(pending(manifest(new, old), {}, posts_dir, backfill=True)) == 2


def test_a_wednesday_shares_all_three_oldest_kind_first(posts_dir):
    day = "2026-08-12"
    posts = [place(posts_dir, post(date=day, kind=kind, images=(f"{kind}-a.png",)))
             for kind in ("daily", "spotlight", "streakiness")]
    due = pending(manifest(*posts), {}, posts_dir)
    assert [d["post"]["kind"] for d in due] == ["daily", "spotlight", "streakiness"]


def test_max_posts_caps_a_run(posts_dir):
    posts = [place(posts_dir, post(date="2026-08-13", kind=kind,
                                   images=(f"{kind}-a.png",)))
             for kind in ("daily", "spotlight", "streakiness")]
    assert len(pending(manifest(*posts), {}, posts_dir, max_posts=2)) == 2


def test_an_archived_post_has_nothing_to_show(posts_dir):
    p = post(archived=True)
    p["sections"][0]["images"] = []
    assert pending(manifest(p), {}, posts_dir) == []


def test_a_missing_image_is_skipped_not_crashed(posts_dir):
    p = post()                                     # in the manifest, never written
    assert pending(manifest(p), {}, posts_dir) == []


def test_no_posts_at_all(posts_dir):
    assert pending(manifest(), {}, posts_dir) == []


# --- posting ------------------------------------------------------------------

def client(session):
    c = Bluesky("handle.bsky.social", "app-pass", "https://pds.test", session)
    c.login()
    return c


def test_share_uploads_then_creates_and_records_it(posts_dir):
    p = place(posts_dir, post(), size=(1600, 920))
    due = pending(manifest(p), {}, posts_dir)
    session = FakeSession()
    state = share(due, client(session), {})

    assert len(session.of("com.atproto.repo.uploadBlob")) == 1
    created = session.of("com.atproto.repo.createRecord")[0]["json"]
    assert created["collection"] == "app.bsky.feed.post"
    record = created["record"]
    image = record["embed"]["images"][0]
    assert image["image"] == {"$type": "blob", "ref": {"$link": "bafy"}}
    assert image["alt"] == "alt daily-season.png"
    assert image["aspectRatio"] == {"width": 1600, "height": 920}
    assert "Baltimore: Orioles/Ravens" in record["text"]
    assert record["facets"][0]["features"][0]["uri"].endswith("daily.html")

    entry = state["shared"]["2026-08-13/daily"]
    assert entry["uri"] == "at://did:plc:test/app.bsky.feed.post/rk1"
    assert entry["fingerprint"] == due[0]["fingerprint"]


def test_an_update_replaces_the_share_it_supersedes(posts_dir):
    p = place(posts_dir, post())
    state = {"shared": {"2026-08-13/daily": {"uri": "at://did:plc:test/app.bsky.feed.post/old",
                                             "fingerprint": "stale"}}}
    due = pending(manifest(p), state, posts_dir)
    session = FakeSession()
    share(due, client(session), state)

    deleted = session.of("com.atproto.repo.deleteRecord")
    assert len(deleted) == 1
    assert deleted[0]["json"]["rkey"] == "old"
    assert state["shared"]["2026-08-13/daily"]["uri"].endswith("rk1")


def test_keep_superseded_leaves_the_old_share_up(posts_dir):
    p = place(posts_dir, post())
    state = {"shared": {"2026-08-13/daily": {"uri": "at://x/y/old", "fingerprint": "s"}}}
    session = FakeSession()
    share(pending(manifest(p), state, posts_dir), client(session), state,
          keep_superseded=True)
    assert session.of("com.atproto.repo.deleteRecord") == []


def test_a_failed_delete_does_not_lose_the_new_share(posts_dir):
    p = place(posts_dir, post())
    state = {"shared": {"2026-08-13/daily": {"uri": "at://x/y/old", "fingerprint": "s"}}}
    session = FakeSession(failures={"com.atproto.repo.deleteRecord": (400, "gone")})
    share(pending(manifest(p), state, posts_dir), client(session), state)
    assert state["shared"]["2026-08-13/daily"]["uri"].endswith("rk1")


def test_what_did_go_out_is_recorded_when_a_later_post_fails(posts_dir, monkeypatch):
    posts = [place(posts_dir, post(date="2026-08-13", kind=kind,
                                   images=(f"{kind}-a.png",)))
             for kind in ("daily", "spotlight")]
    due = pending(manifest(*posts), {}, posts_dir)
    session = FakeSession()
    c = client(session)

    calls = {"n": 0}
    real = c.create

    def flaky(record):
        calls["n"] += 1
        if calls["n"] > 1:
            raise BlueskyError("boom")
        return real(record)

    monkeypatch.setattr(c, "create", flaky)
    state = {}
    with pytest.raises(BlueskyError):
        share(due, c, state)
    assert list(state["shared"]) == ["2026-08-13/daily"]


def test_a_rejected_password_is_not_retried():
    session = FakeSession(failures={"com.atproto.server.createSession":
                                    (401, "Invalid identifier or password")})
    with pytest.raises(BlueskyError, match="401"):
        Bluesky("h", "bad", "https://pds.test", session).login()
    assert len(session.of("com.atproto.server.createSession")) == 1


def test_a_server_error_is_retried(monkeypatch):
    monkeypatch.setattr(share_bluesky.time, "sleep", lambda *_: None)
    session = FakeSession(failures={"com.atproto.server.createSession": (503, "down")})
    with pytest.raises(BlueskyError):
        Bluesky("h", "p", "https://pds.test", session).login()
    assert len(session.of("com.atproto.server.createSession")) == share_bluesky.MAX_RETRIES


def test_an_oversized_image_is_refused(tmp_path):
    path = str(tmp_path / "huge.png")
    png(path, b"x" * share_bluesky.MAX_BLOB_BYTES)
    with pytest.raises(BlueskyError, match="over the"):
        client(FakeSession()).upload(path)


def test_the_blob_carries_the_right_mime_type(tmp_path):
    path = str(tmp_path / "chart.png")
    png(path)
    session = FakeSession()
    client(session).upload(path)
    assert session.of("com.atproto.repo.uploadBlob")[0]["headers"]["Content-Type"] \
        == "image/png"


# --- credentials --------------------------------------------------------------

def test_placeholders_read_as_not_set_up_yet():
    assert credentials("REPLACE-ME.bsky.social", "REPLACE-ME-APP-PASSWORD") is None
    assert credentials("", "") is None
    assert credentials("teamwins.bsky.social", "REPLACE-ME-APP-PASSWORD") is None


def test_real_credentials_are_passed_through():
    assert credentials("teamwins.bsky.social", "abcd-efgh") == \
        ("teamwins.bsky.social", "abcd-efgh")


# --- the CLI ------------------------------------------------------------------

def run(monkeypatch, posts_dir, *args):
    monkeypatch.setattr("sys.argv", ["share_bluesky.py", "--posts-dir", posts_dir,
                                     *args])
    return share_bluesky.main()


def write_manifest(posts_dir, data):
    with open(os.path.join(posts_dir, "index.json"), "w") as f:
        json.dump(data, f)


def test_dry_run_needs_no_credentials_and_writes_nothing(posts_dir, monkeypatch):
    write_manifest(posts_dir, manifest(place(posts_dir, post())))
    assert run(monkeypatch, posts_dir, "--dry-run") == 0
    assert not os.path.exists(os.path.join(posts_dir, "bluesky.json"))


def test_a_run_without_credentials_exits_quietly(posts_dir, monkeypatch):
    write_manifest(posts_dir, manifest(place(posts_dir, post())))
    monkeypatch.setattr(share_bluesky, "APP_PASSWORD", "REPLACE-ME-APP-PASSWORD")
    assert run(monkeypatch, posts_dir) == 0


def test_require_credentials_makes_that_an_error(posts_dir, monkeypatch):
    write_manifest(posts_dir, manifest(place(posts_dir, post())))
    monkeypatch.setattr(share_bluesky, "APP_PASSWORD", "REPLACE-ME-APP-PASSWORD")
    assert run(monkeypatch, posts_dir, "--require-credentials") == 1


def test_nothing_due_is_success_even_without_credentials(posts_dir, monkeypatch):
    write_manifest(posts_dir, manifest())
    assert run(monkeypatch, posts_dir, "--require-credentials") == 0


def test_an_ordinary_morning_alone_is_not_worth_signing_in_for(posts_dir, monkeypatch):
    write_manifest(posts_dir, manifest(place(posts_dir, post(stats=ORDINARY))))
    monkeypatch.setattr(share_bluesky, "APP_PASSWORD", "REPLACE-ME-APP-PASSWORD")
    assert run(monkeypatch, posts_dir, "--require-credentials") == 0


def test_a_missing_manifest_is_an_error(posts_dir, monkeypatch):
    assert run(monkeypatch, posts_dir) == 1


def test_end_to_end_writes_the_state_file(posts_dir, monkeypatch):
    write_manifest(posts_dir, manifest(place(posts_dir, post())))
    session = FakeSession()
    monkeypatch.setattr(share_bluesky, "APP_PASSWORD", "abcd-efgh-ijkl-mnop")
    monkeypatch.setattr(share_bluesky.requests, "Session", lambda: session)
    assert run(monkeypatch, posts_dir, "--handle", "teamwins.bsky.social") == 0

    with open(os.path.join(posts_dir, "bluesky.json")) as f:
        state = json.load(f)
    assert "2026-08-13/daily" in state["shared"]
    assert state["updated"].endswith("Z")

    # and a second run with the same manifest shares nothing
    session.calls.clear()
    assert run(monkeypatch, posts_dir, "--handle", "teamwins.bsky.social") == 0
    assert session.of("com.atproto.repo.createRecord") == []
