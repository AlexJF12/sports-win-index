"""Tests for share_bluesky.py — what gets shared, what doesn't, and the record.

Nothing here touches the network: the client is driven through a fake requests
session that records every XRPC call and replays canned responses.
"""

import json
import os

import pytest

import share_bluesky
from share_bluesky import (Bluesky, BlueskyError, credentials, fingerprint,
                           lead_image, link_card, pending, share)

BASE_URL = "https://alexjf12.github.io/sports-win-index"


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


def png(path, body=b"chart"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + body)


def post(date="2026-08-13", kind="daily", title="City of the day — Baltimore",
         images=("daily-season.png",), archived=False):
    return {
        "date": date, "kind": kind, "title": title, "dek": "Baltimore: Orioles/Ravens",
        "archived": archived,
        "sections": [{"heading": None, "subhead": None, "stats": [], "table": None,
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


def place(posts_dir, post, body=b"chart"):
    """Put a post's images where the manifest says they are."""
    for section in post["sections"]:
        for img in section["images"]:
            png(os.path.join(posts_dir, post["date"], img["file"]), body)
    return post


# --- choosing what to share ---------------------------------------------------

def test_shares_a_post_it_has_never_seen(posts_dir):
    p = place(posts_dir, post())
    due = pending(manifest(p), {}, posts_dir)
    assert [d["key"] for d in due] == ["2026-08-13/daily"]
    assert due[0]["url"] == f"{BASE_URL}/content/posts/2026-08-13/daily.html"
    assert due[0]["image"].endswith("2026-08-13/daily-season.png")
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
    state = {"shared": {"2026-08-13/daily": {
        "uri": "at://x", "fingerprint": pending(manifest(p), {}, posts_dir)[0]["fingerprint"]}}}
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


def test_a_reworded_headline_counts_as_an_update(posts_dir):
    p = place(posts_dir, post())
    before = fingerprint(p, "u", None)
    p["title"] = "City of the day — Baltimore (revised)"
    assert fingerprint(p, "u", None) != before


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


# --- the record that goes out -------------------------------------------------

def test_the_post_is_a_link_card_with_no_text():
    record = link_card("https://example.com/p.html", "Headline", "Dek",
                       {"$type": "blob"})
    assert record["text"] == ""
    assert record["embed"]["$type"] == "app.bsky.embed.external"
    external = record["embed"]["external"]
    assert external["uri"] == "https://example.com/p.html"
    assert external["title"] == "Headline"
    assert external["thumb"] == {"$type": "blob"}
    assert record["createdAt"].endswith("Z")


def test_a_card_without_a_thumb_has_no_thumb_key():
    assert "thumb" not in link_card("u", "t", "d")["embed"]["external"]


# --- posting ------------------------------------------------------------------

def client(session):
    c = Bluesky("handle.bsky.social", "app-pass", "https://pds.test", session)
    c.login()
    return c


def test_share_uploads_then_creates_and_records_it(posts_dir):
    p = place(posts_dir, post())
    due = pending(manifest(p), {}, posts_dir)
    session = FakeSession()
    state = share(due, client(session), {})

    assert len(session.of("com.atproto.repo.uploadBlob")) == 1
    created = session.of("com.atproto.repo.createRecord")[0]["json"]
    assert created["collection"] == "app.bsky.feed.post"
    assert created["record"]["embed"]["external"]["thumb"] == {"$type": "blob",
                                                               "ref": {"$link": "bafy"}}
    assert created["record"]["text"] == ""

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
