"""Tests for C3 (#1817): Popoto ghost index-member handling.

A "ghost" member is an index/class-set entry whose backing Redis hash has
already expired (or been deleted) but whose entry in the model's secondary
index survives -- Redis SETs/ZSETs have no per-member TTL, so an index
member can outlive the hash it points to.

Two things are covered here:

1. ``query.filter()``/``.all()`` never attach a ghost's absent data to a
   result -- popoto's own hydration path silently skips any index member
   whose HGETALL comes back empty. This is existing library behavior; the
   tests below lock it in as a regression guard for this repo's usage.
2. ``models.ghost_reconcile.reconcile_ghost_members()`` -- our reconcile-on-read
   helper -- actually removes the ghost from the index (via the production-safe
   ``Model.clean_indexes()``), and respects its rate limit.

Seeding a ghost (hash gone, index member present) cannot be produced through
the ORM's own ``delete()`` (which removes both) or through TTL expiry
(too slow for a test) -- so these tests reach into
``popoto.redis_db.POPOTO_REDIS_DB`` directly to delete only the hash, mirroring
the seed technique already used by ``tests/integration/test_updated_at_heal.py``.
"""

import time

from models.dedup import DedupRecord
from models.ghost_reconcile import _last_reconciled, reconcile_ghost_members


def _delete_hash_only(instance) -> None:
    """Delete only the backing Redis hash for a Popoto instance, leaving its
    index/class-set membership intact -- reproduces a TTL-expired ghost."""
    import popoto.redis_db as rdb

    rdb.POPOTO_REDIS_DB.delete(instance._redis_key)


class TestQueryFilterDropsGhostMembers:
    """query.filter()/.all() must silently skip ghost members, never raise
    or return a malformed record for one."""

    def setup_method(self):
        _last_reconciled.clear()

    def teardown_method(self):
        for record in DedupRecord.query.all():
            if str(record.chat_id).startswith("test_ghost"):
                record.delete()
        _last_reconciled.clear()

    def test_filter_returns_only_live_records(self, redis_test_db):
        """One ghost + one live member: filter() returns only the live one."""
        ghost = DedupRecord.create(chat_id="test_ghost_a", message_ids={"1"})
        live = DedupRecord.create(chat_id="test_ghost_b", message_ids={"2"})

        _delete_hash_only(ghost)

        results = list(DedupRecord.query.filter())
        chat_ids = {r.chat_id for r in results}

        assert chat_ids == {"test_ghost_b"}, (
            f"Expected only the live record, got {chat_ids!r} "
            "(ghost member should be silently dropped, not raised or returned empty)"
        )

        live.delete()

    def test_filter_empty_when_all_members_are_ghosts(self, redis_test_db):
        """All members ghosts: filter() returns an empty list, no error."""
        ghost1 = DedupRecord.create(chat_id="test_ghost_c", message_ids={"1"})
        ghost2 = DedupRecord.create(chat_id="test_ghost_d", message_ids={"2"})

        _delete_hash_only(ghost1)
        _delete_hash_only(ghost2)

        results = list(DedupRecord.query.filter())
        assert results == [], f"Expected no records (all ghosts), got {results!r}"


class TestReconcileGhostMembers:
    """reconcile_ghost_members() removes ghost index entries via
    Model.clean_indexes() and respects its rate limit."""

    def setup_method(self):
        _last_reconciled.clear()

    def teardown_method(self):
        for record in DedupRecord.query.all():
            if str(record.chat_id).startswith("test_ghost"):
                record.delete()
        _last_reconciled.clear()

    def test_reconcile_removes_ghost_from_index(self, redis_test_db):
        """After reconcile, the ghost's class-set membership is gone."""
        ghost = DedupRecord.create(chat_id="test_ghost_e", message_ids={"1"})
        _delete_hash_only(ghost)

        removed = reconcile_ghost_members(DedupRecord, min_interval=0)

        assert removed >= 1, f"Expected at least 1 orphan removed, got {removed}"

        raw_keys = DedupRecord.query.keys()
        assert ghost._redis_key not in raw_keys, (
            "Ghost's index membership should have been removed by clean_indexes()"
        )

    def test_reconcile_is_rate_limited(self, redis_test_db):
        """A second call within min_interval is a no-op (returns 0)."""
        ghost = DedupRecord.create(chat_id="test_ghost_f", message_ids={"1"})
        _delete_hash_only(ghost)

        first = reconcile_ghost_members(DedupRecord, min_interval=60)
        assert first >= 1

        # Seed another ghost immediately -- should NOT be reconciled yet.
        ghost2 = DedupRecord.create(chat_id="test_ghost_g", message_ids={"1"})
        _delete_hash_only(ghost2)

        second = reconcile_ghost_members(DedupRecord, min_interval=60)
        assert second == 0, "Second call within min_interval must be a no-op"

    def test_reconcile_never_raises_on_clean_indexes_failure(self, redis_test_db, monkeypatch):
        """A clean_indexes() exception is swallowed -- reconcile is best-effort."""

        def _boom(cls):
            raise RuntimeError("simulated Redis failure")

        monkeypatch.setattr(DedupRecord, "clean_indexes", classmethod(_boom))

        # Must not raise.
        result = reconcile_ghost_members(DedupRecord, min_interval=0)
        assert result == 0

    def test_reconcile_skips_when_recently_run(self, redis_test_db):
        """Directly seeding _last_reconciled proves the rate-limit clock is honored."""
        _last_reconciled[DedupRecord._meta.model_name] = time.time()

        ghost = DedupRecord.create(chat_id="test_ghost_h", message_ids={"1"})
        _delete_hash_only(ghost)

        result = reconcile_ghost_members(DedupRecord, min_interval=60)
        assert result == 0
