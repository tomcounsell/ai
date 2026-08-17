"""Job model (Task 13, docs/plans/durability-room-job-agentrun.md).

Job = a responsibility to complete something end to end. Schema ratified
one-shot by the Task 5 schema gate: KeyField set {id, room_id}; two
low-cardinality IndexedFields, ``status`` (active/at-rest) and the derived
``has_open_expectations`` (Schema Gate Amendment 2); recency
``SortedField(partition_by="room_id")``; ``goal`` as an append-only-versioned
plain field carrying the expectation entries. Never hard-closed; rest by age
unless an expectation is open; any steer revives it.

These tests hit real Redis (repo convention) under a ``test-`` room-id
prefix and delete every record via the ORM afterward.
"""

import json
import uuid

import pytest

from models.job import (
    GOAL_PLACEHOLDER_PREFIX,
    JOB_RECENT_OVERFETCH,
    Job,
    mint_placeholder_goal,
)


@pytest.fixture
def scratch_room_id():
    """Unique test- prefixed room id; ORM-scoped cleanup afterward."""
    rid = f"test-jobroom-{uuid.uuid4().hex[:8]}|telegram:1"
    yield rid
    for job in Job.query.filter(room_id=rid):
        job.delete()


class TestPlaceholderGoal:
    def test_placeholder_is_mechanical_first_20_chars(self):
        text = "please fix the flaky test in tests/unit/test_foo.py"
        assert mint_placeholder_goal(text) == f"handle user message '{text[:20]}…'"

    def test_short_message_gets_no_ellipsis(self):
        assert mint_placeholder_goal("hi") == "handle user message 'hi'"

    def test_empty_message_still_yields_a_goal(self):
        # goal is never null — even an empty trigger mints a valid placeholder
        assert mint_placeholder_goal("").startswith(GOAL_PLACEHOLDER_PREFIX)


class TestMint:
    def test_mint_creates_active_job_with_placeholder_goal(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "please fix the flaky test")

        assert job.room_id == scratch_room_id
        assert job.status == "active"
        assert job.current_goal() == mint_placeholder_goal("please fix the flaky test")
        assert job.goal_is_placeholder()

    def test_goal_versioning_appends_never_overwrites(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "please fix the flaky test")
        job.append_goal_version("Fix the flaky test and open a PR", author="pm")

        assert job.current_goal() == "Fix the flaky test and open a PR"
        assert not job.goal_is_placeholder()
        versions = job.goal_versions()
        assert len(versions) == 2
        assert versions[0]["author"] == "router"
        assert versions[1]["author"] == "pm"

    def test_goal_round_trips_through_redis(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "please fix the flaky test")
        job.append_goal_version("Authored goal", author="pm")

        reloaded = Job.query.filter(room_id=scratch_room_id)[0]
        assert reloaded.current_goal() == "Authored goal"
        assert len(reloaded.goal_versions()) == 2


class TestExpectations:
    """The single obligation primitive: (direction, holder, owner, what)."""

    def test_inbound_expectation_defaults_to_the_requester_shape(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        eid = job.add_expectation("I'll report back after the deploy finishes")

        open_ = job.open_expectations()
        assert len(open_) == 1
        entry = open_[0]
        assert entry["id"] == eid
        assert entry["what"] == "I'll report back after the deploy finishes"
        assert entry["direction"] == "inbound"
        assert entry["holder"] == "requester"
        assert entry["owner"] == "pm"

    def test_outbound_expectation_records_holder_and_owner(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "ship the lane")
        job.add_expectation(
            "deliver the migration PR",
            direction="outbound",
            holder="sdlc-local-7",
            owner="session/job-expectations",
        )

        entry = job.open_expectations()[0]
        assert entry["direction"] == "outbound"
        assert entry["holder"] == "sdlc-local-7"
        assert entry["owner"] == "session/job-expectations"

    def test_placeholder_marker_round_trips(self, scratch_room_id):
        """The spawn-time null-fallback marks its mechanical entry so the PM
        prime's refine nudge can key on it (provenance-derived, #2708)."""
        job = Job.mint(scratch_room_id, "ship the lane")
        job.add_expectation(
            "handle spawn instruction",
            direction="outbound",
            holder="pm",
            owner="lane-1",
            placeholder=True,
        )

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.open_expectations()[0]["placeholder"] is True

    def test_discharge_is_append_only(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        eid = job.add_expectation("I'll report back")
        assert job.discharge_expectation(eid) is True

        assert job.open_expectations() == []
        # Append-only: the discharged entry stays, with removed_ts set.
        history = job.all_expectations()
        assert len(history) == 1
        assert history[0]["removed_ts"] is not None

    def test_discharge_unknown_expectation_returns_false(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        assert job.discharge_expectation("nope") is False

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"what": "  "}, "what"),
            ({"what": "deliver", "direction": "outbound", "holder": "pm", "owner": ""}, "owner"),
            ({"what": "deliver", "direction": "sideways"}, "direction"),
        ],
    )
    def test_unownable_or_unaddressed_expectation_is_rejected(
        self, scratch_room_id, kwargs, fragment
    ):
        """An expectation with no owner or no text is unreconcilable — worse
        than none, so it is refused at the write rather than stored."""
        job = Job.mint(scratch_room_id, "check the deploy")

        with pytest.raises(ValueError, match=fragment):
            job.add_expectation(**kwargs)

        assert job.open_expectations() == []


class TestStatusProjection:
    """``status`` is chokepoint-maintained: an open expectation ⇒ active."""

    def test_recording_an_expectation_forces_active(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.mark_at_rest()
        assert job.status == "at-rest"

        job.add_expectation("I'll report back")

        assert job.status == "active"
        assert Job.query.get(id=job.id, room_id=scratch_room_id).status == "active"

    def test_discharge_does_not_force_rest(self, scratch_room_id):
        """Rest is age-derived, never an instant consequence of discharge."""
        job = Job.mint(scratch_room_id, "check the deploy")
        eid = job.add_expectation("I'll report back")
        job.discharge_expectation(eid)

        assert job.status == "active"


class TestLifecycle:
    def test_rest_and_revive_never_hard_closed(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.mark_at_rest()
        assert job.status == "at-rest"

        job.revive()
        assert job.status == "active"

    def test_sweep_to_rest_transitions_stale_active_jobs(self, scratch_room_id):
        stale = _age_out(Job.mint(scratch_room_id, "old thread"))
        fresh = Job.mint(scratch_room_id, "current thread")

        rested = Job.sweep_to_rest()

        assert rested >= 1
        by_id = {j.job_id: j for j in Job.query.filter(room_id=scratch_room_id)}
        assert by_id[stale.job_id].status == "at-rest"
        assert by_id[fresh.job_id].status == "active"

    def test_open_expectation_blocks_rest_but_an_empty_one_still_ages_out(self, scratch_room_id):
        """Under-recording degrades to today's behavior (rest by age), never
        to a false "done": only a *recorded* open expectation pins a Job open.
        """
        owing = Job.mint(scratch_room_id, "owes a lane")
        owing.add_expectation("deliver the PR", direction="outbound", holder="pm", owner="lane-1")
        _age_out(owing)
        idle = _age_out(Job.mint(scratch_room_id, "nothing recorded"))

        Job.sweep_to_rest()

        by_id = {j.job_id: j for j in Job.query.filter(room_id=scratch_room_id)}
        assert by_id[owing.job_id].status == "active"
        assert by_id[idle.job_id].status == "at-rest"

    def test_at_rest_with_open_expectations_query(self, scratch_room_id):
        resting = Job.mint(scratch_room_id, "check the deploy")
        resting.add_expectation("I'll report back")
        # Reaching at-rest with an open expectation is an invariant violation
        # by construction now — force it to prove the alarm still fires.
        resting.mark_at_rest()

        clean = Job.mint(scratch_room_id, "another thing")
        clean.mark_at_rest()

        flagged = Job.at_rest_with_open_expectations()
        flagged_ids = {j.job_id for j in flagged}
        assert resting.job_id in flagged_ids
        assert clean.job_id not in flagged_ids


def _age_out(job: Job) -> Job:
    """Backdate ``last_active_at`` past the rest threshold."""
    import time
    from datetime import UTC, datetime

    from models.job import JOB_AT_REST_AGE_SECONDS

    job.last_active_at = datetime.fromtimestamp(time.time() - JOB_AT_REST_AGE_SECONDS - 60, tz=UTC)
    job.save()
    return job


class TestGoalSelfHeal:
    """``_goal_data()`` is total: no goal shape can make it raise."""

    @pytest.mark.parametrize("raw", [None, "", "not json at all", "[]", '{"versions": null}'])
    def test_malformed_goal_reads_as_empty(self, scratch_room_id, raw):
        job = Job.mint(scratch_room_id, "check the deploy")
        job.goal = raw
        job.save()

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.open_expectations() == []
        assert reloaded.all_expectations() == []
        assert reloaded.goal_versions() == []
        assert reloaded.current_goal() == ""

    def test_malformed_entries_cannot_lose_the_goal_write(self, scratch_room_id):
        """Derivation happens before the save, so a junk entry costs a wrong
        flag at worst — never the goal bytes."""
        job = Job.mint(scratch_room_id, "check the deploy")
        job._write_goal_data({"versions": [], "expectations": ["junk", None, 7]})

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert json.loads(reloaded.goal)["expectations"] == ["junk", None, 7]
        assert reloaded.has_open_expectations is False

    def test_stray_promises_key_is_absorbed_on_every_read(self, scratch_room_id):
        """Race 4: old code that lands a ``promises`` write after the migration
        must not become invisible. The merge is per-read, not one-shot, and
        preserves ids, timestamps, and discharge history."""
        job = Job.mint(scratch_room_id, "check the deploy")
        data = json.loads(job.goal)
        data["promises"] = [
            {
                "id": "legacy1",
                "ts": "2026-08-01T00:00:00+00:00",
                "text": "I'll report back",
                "removed_ts": None,
            },
            {
                "id": "legacy2",
                "ts": "2026-08-02T00:00:00+00:00",
                "text": "already delivered",
                "removed_ts": "2026-08-03T00:00:00+00:00",
            },
        ]
        job.goal = json.dumps(data)
        job.save()

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        history = reloaded.all_expectations()

        assert [e["id"] for e in history] == ["legacy1", "legacy2"]
        assert [e["what"] for e in history] == ["I'll report back", "already delivered"]
        assert {e["direction"] for e in history} == {"inbound"}
        assert history[0]["holder"] == "requester"
        assert history[0]["owner"] == "pm"
        assert history[0]["ts"] == "2026-08-01T00:00:00+00:00"
        assert history[1]["removed_ts"] == "2026-08-03T00:00:00+00:00"
        assert [e["id"] for e in reloaded.open_expectations()] == ["legacy1"]

    def test_absorption_converges_without_duplicating(self, scratch_room_id):
        """Absorbed entries survive as expectations and are never doubled —
        neither by repeated reads nor by the write that persists the merge."""
        job = Job.mint(scratch_room_id, "check the deploy")
        data = json.loads(job.goal)
        data["promises"] = [
            {"id": "legacy1", "ts": "2026-08-01T00:00:00+00:00", "text": "x", "removed_ts": None}
        ]
        job.goal = json.dumps(data)
        job.save()

        # Repeated reads before any write must not accumulate copies.
        job = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert len(job.all_expectations()) == 1
        assert len(job.all_expectations()) == 1

        job.add_expectation("a second obligation")

        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        ids = [e["id"] for e in reloaded.all_expectations()]
        assert ids.count("legacy1") == 1
        assert len(ids) == 2
        # The obligation survived the conversion; only the retired key is gone.
        assert "legacy1" in ids
        assert "promises" not in json.loads(reloaded.goal)


class TestOpenExpectationIndex:
    """The derived ``has_open_expectations`` flag that bounds the at-rest alarm.

    The flag is a projection of ``goal``, so what matters is that it tracks
    every expectation mutation and that a stale flag can never change the answer.
    """

    def test_flag_tracks_expectation_lifecycle(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "check the deploy")
        assert job.has_open_expectations is False

        expectation_id = job.add_expectation("I'll report back")
        assert job.has_open_expectations is True
        assert Job.query.get(id=job.id, room_id=scratch_room_id).has_open_expectations is True

        job.discharge_expectation(expectation_id)
        assert job.has_open_expectations is False
        assert Job.query.get(id=job.id, room_id=scratch_room_id).has_open_expectations is False

    def test_flag_stays_true_while_any_expectation_is_open(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "two things")
        first = job.add_expectation("one")
        job.add_expectation("two")

        job.discharge_expectation(first)

        assert job.has_open_expectations is True
        assert job.open_expectations()

    def test_flag_is_a_real_bool_not_a_truthy_string(self, scratch_room_id):
        """``IndexedField(type=bool)`` is load-bearing.

        Without the declared type the value round-trips as the string
        ``"False"``, which is truthy, so every Job would look obligation-owing.
        """
        job = Job.mint(scratch_room_id, "no expectations here")
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.has_open_expectations is False
        assert not reloaded.has_open_expectations

    def test_alarm_hydrates_only_the_flagged_set(self, scratch_room_id):
        """The bound is the point: work must not scale with the at-rest set."""
        owing = Job.mint(scratch_room_id, "owes a reply")
        owing.add_expectation("I'll report back")
        owing.mark_at_rest()
        for i in range(12):
            noise = Job.mint(scratch_room_id, f"settled {i}")
            noise.mark_at_rest()

        import json as _json

        calls = {"n": 0}
        real_loads = _json.loads

        def counting_loads(*args, **kwargs):
            calls["n"] += 1
            return real_loads(*args, **kwargs)

        _json.loads = counting_loads
        try:
            flagged = Job.at_rest_with_open_expectations()
        finally:
            _json.loads = real_loads

        assert {j.job_id for j in flagged} >= {owing.job_id}
        assert calls["n"] < 12, f"alarm parsed {calls['n']} goals; it must not scan the at-rest set"

    def test_stale_flag_never_produces_a_wrong_answer(self, scratch_room_id):
        """A flag that says True with no open expectation must be filtered out.

        ``goal`` stays authoritative, so the re-verification against
        ``open_expectations()`` is what keeps a drifted projection harmless.
        """
        job = Job.mint(scratch_room_id, "settled but mislabelled")
        job.mark_at_rest()
        job.has_open_expectations = True
        job.save()

        flagged = Job.at_rest_with_open_expectations()

        assert job.job_id not in {j.job_id for j in flagged}

    def test_backfill_stamps_a_row_the_flag_missed(self, scratch_room_id):
        """Legacy rows predate the field and index as nothing until stamped."""
        job = Job.mint(scratch_room_id, "owes a reply")
        job.add_expectation("I'll report back")
        job.mark_at_rest()
        goal_before = job.goal

        # Simulate the legacy shape: expectation in the goal, flag never derived.
        job.has_open_expectations = False
        job.save()
        assert job.job_id not in {j.job_id for j in Job.at_rest_with_open_expectations()}

        stamped = Job.backfill_open_expectations_index()

        assert stamped >= 1
        assert job.job_id in {j.job_id for j in Job.at_rest_with_open_expectations()}
        # Write scope: the backfill's write must never touch goal.
        reloaded = Job.query.get(id=job.id, room_id=job.room_id)
        assert reloaded.goal == goal_before

    def test_backfill_is_idempotent(self, scratch_room_id):
        job = Job.mint(scratch_room_id, "owes a reply")
        job.add_expectation("I'll report back")
        job.mark_at_rest()

        Job.backfill_open_expectations_index()
        second = Job.backfill_open_expectations_index()

        assert second == 0, "a settled population must need no rewrites"

    def test_backfill_does_not_clobber_a_concurrent_expectation(self, scratch_room_id, monkeypatch):
        """Red-state proof (#2647): a bare save() in the backfill loop clobbers a
        concurrent expectation write. This must fail with expectations_survived=0
        on a bare save() and pass on the scoped one.

        A sequential "mutate, then call the method" test cannot reproduce the
        race, because QueryBuilder.__iter__ hydrates at call time — so the
        enumeration must be forced stale via monkeypatch to hold a snapshot
        across the concurrent mutation.
        """
        job = Job.mint(scratch_room_id, "owes a reply")
        snap = Job.query.get(id=job.id, room_id=job.room_id)
        snap.has_open_expectations = True
        snap.save()  # creates the flag-vs-goal disagreement that makes the write fire

        live = Job.query.get(id=job.id, room_id=job.room_id)
        live.add_expectation("expectation A")

        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])
        Job.backfill_open_expectations_index()

        reloaded = Job.query.get(id=job.id, room_id=job.room_id)
        survived = 1 if reloaded.open_expectations() else 0
        assert survived == 1, "the concurrent expectation must survive the backfill"

    def test_backfill_write_is_scoped_to_the_flag(self, scratch_room_id, monkeypatch):
        """Write-scope pin: the backfill's save() must list only
        ``has_open_expectations``, never a bare save(). Pinned directly with a
        save-spy rather than inferred from side effects, so a future edit that
        drops update_fields= or widens the list is caught here rather than only
        by a one-shot manual Verification grep.
        """
        snap = Job.mint(scratch_room_id, "owes a reply")  # hydrated before any entry exists
        live = Job.query.get(id=snap.id, room_id=snap.room_id)
        live.add_expectation("expectation A")

        # Drift the stored flag with a scoped write so the backfill's write fires.
        drift = Job.query.get(id=snap.id, room_id=snap.room_id)
        drift.has_open_expectations = False
        drift.save(update_fields=["has_open_expectations"])

        last_active_before = Job.query.get(id=snap.id, room_id=snap.room_id).last_active_at

        calls = []
        orig_save = Job.save

        def spy(self, *args, **kwargs):
            calls.append(kwargs.get("update_fields"))
            return orig_save(self, *args, **kwargs)

        monkeypatch.setattr(Job, "save", spy)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])
        stamped = Job.backfill_open_expectations_index()

        assert stamped == 1
        assert calls == [["has_open_expectations"]]
        reloaded = Job.query.get(id=snap.id, room_id=snap.room_id)
        assert reloaded.open_expectations()
        assert reloaded.last_active_at == last_active_before

    def test_backfill_per_row_failure_is_logged_and_siblings_still_stamped(
        self, scratch_room_id, monkeypatch
    ):
        """Fail-open contract: a re-fetch that raises for one row must not stop
        the sweep from stamping its siblings, and must not raise into the
        maintenance path. This pins the re-fetch's placement inside the
        per-row try.
        """
        j1 = Job.mint(scratch_room_id, "first")
        j1.add_expectation("expectation A")
        j2 = Job.mint(scratch_room_id, "second")
        j2.add_expectation("expectation B")

        # Drift both rows into flag-vs-goal disagreement so the write fires.
        for j in (j1, j2):
            d = Job.query.get(id=j.id, room_id=j.room_id)
            d.has_open_expectations = False
            d.save(update_fields=["has_open_expectations"])

        real_get = Job.query.get

        def boom(*args, **kwargs):
            if kwargs.get("id") == j1.id:
                raise RuntimeError("boom")
            return real_get(*args, **kwargs)

        monkeypatch.setattr(Job.query, "get", boom)
        monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [j1, j2])

        stamped = Job.backfill_open_expectations_index()

        assert stamped == 1
        assert Job.query.get(id=j2.id, room_id=j2.room_id).has_open_expectations is True

    def test_backfill_whole_loop_failure_returns_zero(self, scratch_room_id, monkeypatch):
        """Fail-open contract: a broken enumeration returns 0 rather than
        raising into repair_indexes()."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert Job.backfill_open_expectations_index() == 0

    def test_alarm_never_raises_into_the_health_cycle(self, scratch_room_id, monkeypatch):
        """Fail-open contract: a broken query returns [] rather than raising."""

        def boom(*args, **kwargs):
            raise RuntimeError("redis is unhappy")

        monkeypatch.setattr(Job.query, "filter", boom)

        assert Job.at_rest_with_open_expectations() == []


def _partition_key(room_id: str) -> str:
    """The ``last_active_at`` sorted-set key for a Room — derived, never built.

    ``DB_key.clean()`` escapes ``:`` and ``/``, and every real room_id contains
    a colon, so a hand-built key reads a set that does not exist (spike-3).
    """
    from popoto import SortedField

    return SortedField.get_sortedset_db_key(Job, "last_active_at", room_id).redis_key


def _scores(room_id: str) -> list[tuple[bytes, float]]:
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB.zrevrange(_partition_key(room_id), 0, -1, withscores=True)


@pytest.fixture
def count_hash_reads(monkeypatch):
    """Count hash reads (the pipelined HGETALL/HMGET hydration path).

    This is the acceptance instrument for the bound: an unbounded range read
    hydrates every Job in the Room, a bounded one hydrates ``limit`` of them,
    and only a read counter tells the two apart — a "fix" that measures
    unchanged is the named failure mode.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    counter = {"n": 0}

    def wrap(obj, name):
        real = getattr(obj, name)

        def counting(*args, **kwargs):
            counter["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(obj, name, counting)

    real_pipeline = POPOTO_REDIS_DB.pipeline

    def counting_pipeline(*args, **kwargs):
        pipe = real_pipeline(*args, **kwargs)
        for name in ("hgetall", "hmget"):
            wrap(pipe, name)
        return pipe

    monkeypatch.setattr(POPOTO_REDIS_DB, "pipeline", counting_pipeline)
    for name in ("hgetall", "hmget"):
        wrap(POPOTO_REDIS_DB, name)
    return counter


class TestRecencyLookup:
    """The bind-or-mint candidate lookup: bounded, newest-first, fail-open."""

    @pytest.mark.parametrize("population", [7, 30])
    def test_recent_for_room_returns_newest_first_capped(self, scratch_room_id, population):
        """Ordering parity with the old hydrate-all-then-sort implementation.

        Timestamps are distinct (mint stamps microsecond ``_now()``): under
        equal scores Python's stable sort and Redis' reverse-lex tiebreak pick
        different subsets at the limit boundary (spike-4), so a constant-stamped
        fixture would assert a coincidence rather than the contract.
        """
        jobs = [Job.mint(scratch_room_id, f"task {i}") for i in range(population)]

        recent = Job.recent_for_room(scratch_room_id, limit=5)

        assert len(recent) == 5
        assert [j.job_id for j in recent] == [j.job_id for j in reversed(jobs[-5:])]

    def test_hydration_scales_with_limit_not_room_size(self, scratch_room_id, count_hash_reads):
        """The bound itself. At 30 Jobs the old path cost 2N=60 hash reads for a
        top-5 answer (popoto's QueryBuilder hydrates twice, #2639); the bounded
        path costs at most one per over-fetched member."""
        for i in range(30):
            Job.mint(scratch_room_id, f"task {i}")
        limit = 5
        count_hash_reads["n"] = 0

        recent = Job.recent_for_room(scratch_room_id, limit=limit)

        assert len(recent) == limit
        ceiling = 2 * limit + JOB_RECENT_OVERFETCH
        assert count_hash_reads["n"] <= limit + JOB_RECENT_OVERFETCH
        assert count_hash_reads["n"] < ceiling, (
            f"{count_hash_reads['n']} hash reads for a top-{limit} answer over 30 Jobs — "
            "the range read is unbounded"
        )

    def test_gone_hash_member_is_dropped_and_the_window_still_fills(
        self, scratch_room_id, monkeypatch
    ):
        """A member whose hash is gone (transient orphan, reaped by the daily
        guarded repair) is dropped by ``skip_none``; the over-fetch window keeps
        the answer full while live rows exist."""
        from popoto.redis_db import POPOTO_REDIS_DB

        jobs = [Job.mint(scratch_room_id, f"task {i}") for i in range(8)]
        real_zrevrange = POPOTO_REDIS_DB.zrevrange
        orphan = b"Job:test-orphan-no-such-hash:nowhere"

        def with_orphan(key, start, end, **kwargs):
            """Splice a gone-hash member in at the head, honoring the caller's
            window width so the orphan displaces a live member rather than
            widening the read."""
            members = real_zrevrange(key, start, end, **kwargs)
            spliced = [orphan, *members]
            width = end - start + 1 if end >= 0 else len(spliced)
            return spliced[:width]

        monkeypatch.setattr(POPOTO_REDIS_DB, "zrevrange", with_orphan)

        recent = Job.recent_for_room(scratch_room_id, limit=5)

        assert len(recent) == 5
        assert [j.job_id for j in recent] == [j.job_id for j in reversed(jobs[-5:])]

    def test_recent_for_room_empty_room_is_empty(self):
        assert Job.recent_for_room("test-jobroom-none|telegram:0") == []

    def test_zero_limit_short_circuits_before_hydrating(self, scratch_room_id, count_hash_reads):
        for i in range(3):
            Job.mint(scratch_room_id, f"task {i}")
        count_hash_reads["n"] = 0

        assert Job.recent_for_room(scratch_room_id, limit=0) == []
        assert count_hash_reads["n"] == 0

    def test_read_failure_fails_open_with_a_warning(self, scratch_room_id, caplog):
        """Fail-open contract: the candidate lookup never raises into the
        router — it returns [] and the caller mints."""
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        Job.mint(scratch_room_id, "task")

        def boom(*args, **kwargs):
            raise ConnectionError("redis is unhappy")

        original = POPOTO_REDIS_DB.zrevrange
        POPOTO_REDIS_DB.zrevrange = boom
        try:
            with caplog.at_level(logging.WARNING, logger="models.job"):
                assert Job.recent_for_room(scratch_room_id, limit=5) == []
        finally:
            POPOTO_REDIS_DB.zrevrange = original

        assert "recent_for_room failed" in caplog.text


class TestScorePurity:
    """Sorted-set scores must be pure UTC epochs, on every write path.

    popoto 1.8.0 decodes datetimes without tzinfo, so a reloaded Job's
    ``last_active_at`` is naive and the next save would score it as
    ``naive.timestamp()`` — local time. On a UTC+07 host that buries a Job
    active seconds ago seven hours in the past, which is what made a live
    ``last_active_at__gte=now-1h`` filter return zero rows.
    """

    def test_resave_after_reload_keeps_the_score_a_utc_epoch(self, scratch_room_id):
        import time

        job = Job.mint(scratch_room_id, "check the deploy")
        reloaded = Job.query.get(id=job.id, room_id=scratch_room_id)
        assert reloaded.last_active_at.tzinfo is None, "popoto still decodes naive (premise)"

        reloaded.add_expectation("I'll report back")

        [(_member, score)] = _scores(scratch_room_id)
        assert abs(score - time.time()) < 60, (
            f"score {score} is {time.time() - score:.0f}s off wall clock — local-time skew is back"
        )

    def test_recency_filter_finds_a_just_resaved_job(self, scratch_room_id):
        """The user-visible half of the same defect (spike-2's live failure)."""
        from datetime import UTC, datetime, timedelta

        job = Job.mint(scratch_room_id, "check the deploy")
        Job.query.get(id=job.id, room_id=scratch_room_id).add_expectation("I'll report back")

        cutoff = datetime.now(tz=UTC) - timedelta(hours=1)
        found = Job.query.filter(room_id=scratch_room_id, last_active_at__gte=cutoff)

        assert [j.job_id for j in found] == [job.job_id]

    def test_reattach_preserves_the_instant_and_is_idempotent(self, scratch_room_id):
        """The override attaches the tzinfo the value already meant; it must
        never re-stamp ``_now()`` — that would refresh recency on every
        unrelated write and resurrect idle Jobs past rest-by-age."""
        job = Job.mint(scratch_room_id, "check the deploy")
        instant = job.last_active_at

        job.last_active_at = instant.replace(tzinfo=None)
        job.save()

        assert job.last_active_at == instant
        job.save()
        assert job.last_active_at == instant

    def test_scoped_save_excluding_the_field_leaves_the_score_untouched(self, scratch_room_id):
        """``backfill_open_expectations_index`` saves
        ``update_fields=["has_open_expectations"]`` under a docstring invariant
        that it never writes recency. The override must honor that scope rather
        than reaching into the SortedField path behind its back."""
        job = Job.mint(scratch_room_id, "owes a reply")
        [(_member, score_before)] = _scores(scratch_room_id)

        job.last_active_at = job.last_active_at.replace(tzinfo=None)
        job.has_open_expectations = True
        job.save(update_fields=["has_open_expectations"])

        assert job.last_active_at.tzinfo is None, "the guard let an out-of-scope write through"
        assert _scores(scratch_room_id) == [(_member, score_before)]

    def test_scoped_save_naming_the_field_still_reattaches(self, scratch_room_id):
        """The skew backfill's write path: a scoped save that *names*
        ``last_active_at`` must reattach UTC, or it would write the very skew it
        exists to repair."""
        from datetime import UTC

        job = Job.mint(scratch_room_id, "owes a reply")
        [(member, score_before)] = _scores(scratch_room_id)

        job.last_active_at = job.last_active_at.replace(tzinfo=None)
        job.save(update_fields=["last_active_at"])

        assert job.last_active_at.tzinfo is UTC
        assert _scores(scratch_room_id) == [(member, score_before)]


class TestGuardedRepair:
    """Risk 2 (#2207): Job never enters the generic rebuild_indexes sweep."""

    def test_job_listed_in_guarded_elsewhere(self):
        from scripts.popoto_index_cleanup import _GUARDED_ELSEWHERE

        assert "Job" in _GUARDED_ELSEWHERE

    def test_repair_quarantines_identity_less_hash_and_its_index_member(self, scratch_room_id):
        """Leg 1 deletes the phantom hash; leg 2 must clear its $IndexF
        membership too — the raw delete bypasses on_delete's SREM and
        popoto's rebuild_indexes() never enumerates $IndexF sets, so
        without leg 2 the member leaks permanently."""
        from popoto.redis_db import POPOTO_REDIS_DB

        status_index_key = "$IndexF:Job:status:active"
        job = Job.mint(scratch_room_id, "real job")
        phantom_key = f"Job:test-phantom-{uuid.uuid4().hex[:8]}"
        POPOTO_REDIS_DB.hset(phantom_key, mapping={"status": "active"})
        # Simulate what on_save would have done for the phantom: index it.
        POPOTO_REDIS_DB.sadd(status_index_key, phantom_key)
        try:
            quarantined, _rebuilt = Job.repair_indexes()
            assert quarantined >= 1
            assert POPOTO_REDIS_DB.exists(phantom_key) == 0
            # The leak: the phantom's index membership must be gone too.
            assert not POPOTO_REDIS_DB.sismember(status_index_key, phantom_key)
            # The real record survives — reachable AND re-indexed.
            assert any(j.job_id == job.job_id for j in Job.query.filter(room_id=scratch_room_id))
            fresh = Job.query.filter(room_id=scratch_room_id)[0]
            assert fresh.status == "active"
            assert list(Job.query.filter(status="active"))  # index serves queries again
        finally:
            POPOTO_REDIS_DB.delete(phantom_key)
            POPOTO_REDIS_DB.srem(status_index_key, phantom_key)

    def test_stale_member_scan_is_pipelined_across_batch_boundary(
        self, scratch_room_id, monkeypatch, caplog
    ):
        """Leg 2's existence checks must be pipelined, not one round trip per
        member: a bloated index (this repo has seen millions of stale
        pointers) turns the daily maintenance path into a multi-hour hang.

        Mirrors ``AgentSession.repair_indexes``' batching. The member count
        straddles the 5000 batch boundary so the multi-batch loop is exercised,
        and every member is stale (no backing hash) so the count is exact.
        """
        import logging

        from popoto.redis_db import POPOTO_REDIS_DB

        status_index_key = "$IndexF:Job:status:active"
        Job.mint(scratch_room_id, "real job")
        member_count = 5001  # one past a single batch
        stale_members = [f"Job:test-stale-{uuid.uuid4().hex[:8]}-{i}" for i in range(member_count)]
        POPOTO_REDIS_DB.sadd(status_index_key, *stale_members)

        direct_exists_calls = 0
        real_exists = POPOTO_REDIS_DB.exists

        def counting_exists(*args, **kwargs):
            nonlocal direct_exists_calls
            direct_exists_calls += 1
            return real_exists(*args, **kwargs)

        monkeypatch.setattr(POPOTO_REDIS_DB, "exists", counting_exists)
        try:
            with caplog.at_level(logging.WARNING, logger="models.job"):
                Job.repair_indexes()

            # Behavior is identical to the unbatched scan: every stale member
            # is counted, and the whole index key is cleared and rebuilt.
            assert f"cleared {member_count} stale $IndexF member(s)" in caplog.text
            for member in stale_members[:3]:
                assert not POPOTO_REDIS_DB.sismember(status_index_key, member)
            # ...but no per-member round trip: the scan issues zero direct
            # exists() calls, it batches them into pipelines instead.
            assert direct_exists_calls < member_count // 10
        finally:
            monkeypatch.setattr(POPOTO_REDIS_DB, "exists", real_exists)
            POPOTO_REDIS_DB.srem(status_index_key, *stale_members)


class TestDriftCoverage:
    """Risk 3: drift detection must not silently narrow — Job registers."""

    def test_job_registered_in_drift_coverage(self):
        from agent.index_drift import covered_model_names

        assert "Job" in covered_model_names()

    def test_job_exported_from_models_package(self):
        import models

        assert "Job" in models.__all__
