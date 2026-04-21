"""Unit tests for ``tools.email_history``.

Uses live local Redis via the xdist-aware ``redis_test_url`` fixture (shared
with the popoto ``redis_test_db`` autouse fixture — both use the same
per-worker db, so ``pytest -n auto`` is safe).
"""

from __future__ import annotations

import json
import time

import pytest
import redis

from bridge.email_bridge import HISTORY_MSG_KEY, HISTORY_SET_KEY, HISTORY_THREADS_KEY


@pytest.fixture
def redis_url_env(monkeypatch, redis_test_url):
    """Point ``REDIS_URL`` at the xdist-aware test db for the duration of the test."""
    monkeypatch.setenv("REDIS_URL", redis_test_url)
    return redis_test_url


@pytest.fixture
def r(redis_url_env):
    """Return a decoded Redis client on the xdist-aware test db."""
    client = redis.Redis.from_url(redis_url_env, decode_responses=True)
    # The popoto autouse fixture already flushed this db before the test, so we
    # inherit a clean state.
    yield client
    client.close()


def _seed_message(r, message_id, ts, subject="Sub", body="body text", from_addr="a@x.com"):
    set_key = HISTORY_SET_KEY.format(mailbox="INBOX")
    msg_key = HISTORY_MSG_KEY.format(message_id=message_id)
    r.set(
        msg_key,
        json.dumps(
            {
                "from_addr": from_addr,
                "subject": subject,
                "body": body,
                "timestamp": ts,
                "message_id": message_id,
                "in_reply_to": "",
            }
        ),
    )
    r.zadd(set_key, {message_id: ts})


class TestGetRecentEmails:
    def test_empty_cache_returns_empty(self, r):
        from tools.email_history import get_recent_emails

        result = get_recent_emails(limit=5)
        assert result == {"messages": [], "count": 0, "mailbox": "INBOX"}

    def test_returns_newest_first_within_limit(self, r):
        from tools.email_history import get_recent_emails

        now = time.time()
        _seed_message(r, "<m-1@x>", now - 30, subject="old")
        _seed_message(r, "<m-2@x>", now - 10, subject="new")
        _seed_message(r, "<m-3@x>", now - 20, subject="mid")

        result = get_recent_emails(limit=2)
        subjects = [m["subject"] for m in result["messages"]]
        assert subjects == ["new", "mid"]

    def test_skips_missing_blobs(self, r):
        from tools.email_history import get_recent_emails

        set_key = HISTORY_SET_KEY.format(mailbox="INBOX")
        # Orphan entry — in the set but no blob
        r.zadd(set_key, {"<orphan@x>": time.time()})
        _seed_message(r, "<real@x>", time.time() - 5, subject="hi")

        result = get_recent_emails(limit=5)
        # Only the real message should come through
        assert result["count"] == 1
        assert result["messages"][0]["message_id"] == "<real@x>"

    def test_non_inbox_mailbox_rejected(self, r):
        from tools.email_history import get_recent_emails

        result = get_recent_emails(mailbox="SENT", limit=5)
        assert "error" in result
        assert "INBOX" in result["error"]

    def test_since_ts_filter(self, r):
        from tools.email_history import get_recent_emails

        now = time.time()
        _seed_message(r, "<old@x>", now - 1000, subject="old")
        _seed_message(r, "<new@x>", now - 10, subject="new")

        result = get_recent_emails(limit=10, since_ts=now - 100)
        subjects = [m["subject"] for m in result["messages"]]
        assert subjects == ["new"]


class TestSearchHistory:
    def test_empty_query_errors(self, r):
        from tools.email_history import search_history

        result = search_history(query="", max_results=5)
        assert "error" in result

    def test_substring_match_on_subject_or_body(self, r):
        from tools.email_history import search_history

        now = time.time()
        _seed_message(r, "<m-1@x>", now - 10, subject="Deployment done", body="all good")
        _seed_message(r, "<m-2@x>", now - 20, subject="Unrelated", body="check deployment logs")
        _seed_message(r, "<m-3@x>", now - 30, subject="Lunch", body="tomorrow at 12")

        result = search_history(query="deploy", max_results=10)
        ids = sorted(m["message_id"] for m in result["results"])
        assert ids == ["<m-1@x>", "<m-2@x>"]

    def test_age_filter(self, r):
        from tools.email_history import search_history

        now = time.time()
        _seed_message(r, "<ancient@x>", now - 20 * 86400, body="deploy")
        _seed_message(r, "<recent@x>", now - 86400, body="deploy")

        result = search_history(query="deploy", max_age_days=7)
        ids = [m["message_id"] for m in result["results"]]
        assert ids == ["<recent@x>"]

    def test_search_hydrates_via_single_mget(self, r, monkeypatch):
        """Regression for PR #1094 review: search_history must batch-hydrate
        via a single MGET rather than one GET per candidate msgid.
        """
        from tools.email_history import search_history

        now = time.time()
        for i in range(5):
            _seed_message(r, f"<m-{i}@x>", now - i, subject=f"deploy-{i}", body="match")

        # Count the GETs/MGETs executed on this Redis instance.
        import tools.email_history as eh

        original_mget = eh._hydrate_many
        mget_calls: list[int] = []

        def counting_mget(client, msgids):
            mget_calls.append(len(msgids))
            return original_mget(client, msgids)

        monkeypatch.setattr(eh, "_hydrate_many", counting_mget)

        result = search_history(query="match", max_results=10)
        # All 5 candidates must be hydrated in exactly one batch.
        assert len(mget_calls) == 1
        assert mget_calls[0] == 5
        assert len(result["results"]) == 5


class TestListThreads:
    def test_empty_returns_empty(self, r):
        from tools.email_history import list_threads

        result = list_threads()
        assert result == {"threads": [], "count": 0}

    def test_returns_sorted_by_last_ts_desc(self, r):
        from tools.email_history import list_threads

        now = time.time()
        r.hset(
            HISTORY_THREADS_KEY,
            "<root-a@x>",
            json.dumps(
                {
                    "root": "<root-a@x>",
                    "subject": "Thread A",
                    "message_count": 2,
                    "last_ts": now - 100,
                    "participants": ["alice@x"],
                }
            ),
        )
        r.hset(
            HISTORY_THREADS_KEY,
            "<root-b@x>",
            json.dumps(
                {
                    "root": "<root-b@x>",
                    "subject": "Thread B",
                    "message_count": 1,
                    "last_ts": now - 10,
                    "participants": ["bob@x"],
                }
            ),
        )

        result = list_threads()
        subjects = [t["subject"] for t in result["threads"]]
        assert subjects == ["Thread B", "Thread A"]

    def test_skips_malformed_entries(self, r):
        from tools.email_history import list_threads

        r.hset(HISTORY_THREADS_KEY, "<bad@x>", "not-json")
        r.hset(
            HISTORY_THREADS_KEY,
            "<good@x>",
            json.dumps(
                {
                    "root": "<good@x>",
                    "subject": "Good thread",
                    "message_count": 1,
                    "last_ts": time.time(),
                    "participants": [],
                }
            ),
        )

        result = list_threads()
        assert result["count"] == 1
        assert result["threads"][0]["subject"] == "Good thread"
