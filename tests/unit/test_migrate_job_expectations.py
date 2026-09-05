"""Job goal-schema migration: ``promises`` -> ``expectations`` (#2708).

Real Popoto/Redis integration under a ``test-`` room-id prefix, per this
repo's testing philosophy — the storage layer is never mocked. The only
monkeypatching is the enumeration, used to scope a sweep to this test's own
rows and to force a per-row failure.

What the migration must guarantee, and what these tests pin:

* every pre-cutover ``promises`` entry survives as an inbound expectation
  with its id, timestamp, and ``removed_ts`` history intact — nothing is
  ever deleted;
* it is idempotent (a second pass writes nothing);
* one unreadable row costs only that row.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from models.job import Job
from scripts.update.migrations import MIGRATIONS, _migrate_job_promises_to_expectations


@pytest.fixture
def scratch_room_id():
    rid = f"test-jobmig-{uuid.uuid4().hex[:8]}|telegram:1"
    yield rid
    for job in Job.query.filter(room_id=rid):
        job.delete()


def _plant_legacy_promises(job: Job, promises: list[dict]) -> None:
    """Write the pre-cutover goal shape directly onto the record.

    Deliberately bypasses the model's expectation API: the point is to
    reproduce a row written by the code that shipped before the cutover.
    """
    data = json.loads(job.goal)
    data.pop("expectations", None)
    data["promises"] = promises
    job.goal = json.dumps(data)
    job.save()


_LEGACY_PAIR = [
    {
        "id": "promise-open",
        "ts": "2026-08-01T00:00:00+00:00",
        "text": "I'll report back after the deploy",
        "removed_ts": None,
    },
    {
        "id": "promise-discharged",
        "ts": "2026-08-02T00:00:00+00:00",
        "text": "already delivered",
        "removed_ts": "2026-08-03T04:05:06+00:00",
    },
]


class TestPromiseRewrite:
    def test_promises_become_inbound_expectations_verbatim(
        self, scratch_room_id, tmp_path: Path, monkeypatch
    ):
        job = Job.mint(scratch_room_id, "check the deploy")
        _plant_legacy_promises(job, _LEGACY_PAIR)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [job])

        assert _migrate_job_promises_to_expectations(tmp_path) is None

        # Read the STORED bytes, not the model accessor: `_goal_data()`
        # absorbs a promises key on every read, so asserting through
        # all_expectations() would pass even if the migration wrote nothing.
        fresh = Job.query.get(id=job.id, room_id=scratch_room_id)
        stored = json.loads(fresh.goal)
        assert "promises" not in stored, "the sweep must persist the converted form"
        history = stored["expectations"]
        assert [e["id"] for e in history] == ["promise-open", "promise-discharged"]
        # Verbatim: ids, timestamps and discharge history are carried across,
        # only the field name changes (text -> what).
        assert history[0]["ts"] == "2026-08-01T00:00:00+00:00"
        assert history[0]["what"] == "I'll report back after the deploy"
        assert history[0]["removed_ts"] is None
        assert history[1]["removed_ts"] == "2026-08-03T04:05:06+00:00"
        # Every entry takes the inbound shape: we owe the requester.
        assert {e["direction"] for e in history} == {"inbound"}
        assert {e["holder"] for e in history} == {"requester"}
        assert {e["owner"] for e in history} == {"pm"}

    def test_open_promise_restamps_the_index_flag_and_status(
        self, scratch_room_id, tmp_path: Path, monkeypatch
    ):
        job = Job.mint(scratch_room_id, "check the deploy")
        _plant_legacy_promises(job, _LEGACY_PAIR)
        # A pre-cutover row can be at rest while owing something; the
        # chokepoint's status projection has to correct that on rewrite.
        job.status = "at-rest"
        job.has_open_expectations = False
        job.save()
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [job])

        assert _migrate_job_promises_to_expectations(tmp_path) is None

        fresh = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert fresh.has_open_expectations is True
        assert fresh.status == "active"

    def test_fully_discharged_row_leaves_the_flag_false(
        self, scratch_room_id, tmp_path: Path, monkeypatch
    ):
        job = Job.mint(scratch_room_id, "settled")
        _plant_legacy_promises(job, [_LEGACY_PAIR[1]])
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [job])

        assert _migrate_job_promises_to_expectations(tmp_path) is None

        fresh = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert fresh.has_open_expectations is False
        assert len(fresh.all_expectations()) == 1  # history kept

    def test_is_idempotent(self, scratch_room_id, tmp_path: Path, monkeypatch):
        job = Job.mint(scratch_room_id, "check the deploy")
        _plant_legacy_promises(job, _LEGACY_PAIR)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [job])

        assert _migrate_job_promises_to_expectations(tmp_path) is None
        after_first = Job.query.get(id=job.id, room_id=scratch_room_id).goal
        assert "promises" not in json.loads(after_first)

        # The second pass has no promises key to act on, so it must not
        # rewrite any goal — pinned with a save-spy, since a byte-identical
        # rewrite would look the same from the outside. The spy's
        # ["last_active_at"]-scoped branch is dead tolerance: nothing in this
        # migration issues that scoped save, so the spy tolerates nothing —
        # a goal rewrite can never take that shape either way.
        saves = []
        orig_save = Job.save
        monkeypatch.setattr(
            Job,
            "save",
            lambda self, *a, **k: (
                None if k.get("update_fields") == ["last_active_at"] else saves.append(self.job_id)
            ),
        )

        assert _migrate_job_promises_to_expectations(tmp_path) is None

        monkeypatch.setattr(Job, "save", orig_save)
        assert saves == [], "a settled row must cost reads only"
        assert Job.query.get(id=job.id, room_id=scratch_room_id).goal == after_first

    def test_already_migrated_row_is_untouched(self, scratch_room_id, tmp_path: Path, monkeypatch):
        job = Job.mint(scratch_room_id, "post-cutover job")
        job.add_expectation("deliver the PR", direction="outbound", owner="lane-1")
        before = Job.query.get(id=job.id, room_id=scratch_room_id).goal
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [job])

        assert _migrate_job_promises_to_expectations(tmp_path) is None

        assert Job.query.get(id=job.id, room_id=scratch_room_id).goal == before

    def test_one_bad_row_costs_only_that_row(
        self, scratch_room_id, tmp_path: Path, monkeypatch, caplog
    ):
        """Per-row fault isolation: a raising re-fetch must not abort the
        sweep, and must not raise into /update."""
        import logging

        doomed = Job.mint(scratch_room_id, "doomed")
        _plant_legacy_promises(doomed, _LEGACY_PAIR)
        healthy = Job.mint(scratch_room_id, "healthy")
        _plant_legacy_promises(healthy, _LEGACY_PAIR)

        real_get = Job.query.get

        def boom(*args, **kwargs):
            if kwargs.get("id") == doomed.id:
                raise RuntimeError("row is unreadable")
            return real_get(*args, **kwargs)

        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [doomed, healthy])
        monkeypatch.setattr(Job.query, "get", boom)

        with caplog.at_level(logging.WARNING, logger="scripts.update.migrations"):
            assert _migrate_job_promises_to_expectations(tmp_path) is None

        assert "row is unreadable" in caplog.text
        monkeypatch.setattr(Job.query, "get", real_get)
        assert Job.query.get(id=healthy.id, room_id=scratch_room_id).open_expectations()

    def test_broken_enumeration_reports_an_error_string(self, tmp_path: Path, monkeypatch):
        """A wholesale failure is reported to /update, not raised."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert _migrate_job_promises_to_expectations(tmp_path) == "redis is unhappy"


class TestMigrationRegistration:
    def test_registered_in_migrations_dict(self):
        assert "job_promises_to_expectations" in MIGRATIONS
        fn, description = MIGRATIONS["job_promises_to_expectations"]
        assert fn is _migrate_job_promises_to_expectations
        assert "expectation" in description.lower()

    def test_every_migration_name_is_unique(self):
        assert len(MIGRATIONS) == len(set(MIGRATIONS))
