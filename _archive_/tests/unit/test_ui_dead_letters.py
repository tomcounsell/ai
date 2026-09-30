"""The dead-letter dashboard tile (#3183).

The tile is the surface a human reads before deciding to replay anything, so
it has to render on an empty namespace and on a Redis that is down. Counts
come from the advisory hash rather than a census, so the tile costs two reads
no matter how many rows exist.
"""

import pytest

from ui.data.dead_letters import get_dead_letter_counts


class FakeRedis:
    def __init__(self, hash_data=None, fail=False):
        self.hash_data = hash_data or {}
        self.fail = fail

    def hgetall(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.hash_data


@pytest.fixture
def redis(monkeypatch):
    holder = {}

    def _install(hash_data=None, fail=False):
        holder["client"] = FakeRedis(hash_data, fail)
        monkeypatch.setattr("utils.redis_client.text_redis", lambda: holder["client"])

    _install()
    return _install


class TestEmptyNamespace:
    def test_every_stage_renders_at_zero(self, redis):
        """A stage that vanishes from the tile reads as a stage nobody watches."""
        from bridge import dead_letters

        data = get_dead_letter_counts()
        assert data["total"] == 0
        assert {row["stage"] for row in data["rows"]} == set(dead_letters.STAGES)
        assert all(row["count"] == 0 for row in data["rows"])


class TestSeeded:
    def test_counts_come_back_per_stage(self, redis):
        redis({"telegram_send": "3", "outbox_parse": "12"})
        data = get_dead_letter_counts()
        by_stage = {row["stage"]: row["count"] for row in data["rows"]}
        assert by_stage["outbox_parse"] == 12
        assert by_stage["telegram_send"] == 3
        assert data["total"] == 15

    def test_the_busiest_stage_sorts_first(self, redis):
        redis({"telegram_send": "3", "outbox_parse": "12"})
        rows = get_dead_letter_counts()["rows"]
        assert rows[0]["stage"] == "outbox_parse"

    def test_the_cap_is_reported(self, redis):
        from bridge import dead_letters

        assert get_dead_letter_counts()["cap"] == dead_letters.DEAD_LETTER_STAGE_CAP


class TestDegraded:
    def test_a_redis_failure_renders_zeros_rather_than_crashing(self, redis):
        redis(fail=True)
        data = get_dead_letter_counts()
        assert data["total"] == 0
        assert data["rows"], "the stage list is static; it renders without Redis"
