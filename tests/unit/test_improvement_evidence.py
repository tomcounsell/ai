"""Tests for the improvement evidence writers (#3177, #3217).

The plan's user-visible criterion for lane 2 is that the correction detector
runs against real sessions *because something ticks it*. These tests cover both
halves: the durable row (``ImprovementEvidence.record_once`` and its dedup), and
the five observer adapters the reflection tick calls. Lane 5 (#3217) adds the
``lesson`` adapter (merged PR bodies through an injectable ``gh`` runner) and
the ``promise`` adapter (sampled outbound chat entries through an injectable
judge transport, metered under ``purpose="promise_detector"``).

They are deliberately unkind to the classifier. A regex cannot tell an
architectural rescue from a preference most of the time, and the honest answer
is ``unknown`` — a test that demanded confident labels would be pushing the
detector toward exactly the overclaiming the plan warns about.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py); production Redis
is never touched, and every row is written under a test-scoped ``project_key``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from models.improvement_evidence import (
    EVIDENCE_CLASSIFICATIONS,
    EVIDENCE_KINDS,
    ImprovementEvidence,
)
from reflections import improvement_collect

PK = "test-3177-evidence"


class TestRecordOnce:
    def test_writes_a_row(self):
        row = ImprovementEvidence.record_once(
            PK, "correction", source_session_id="s-1", text="no, i meant the other file"
        )
        assert row is not None
        assert row.kind == "correction"
        assert list(ImprovementEvidence.query.filter(project_key=PK, kind="correction"))

    def test_second_identical_observation_is_dropped(self):
        first = ImprovementEvidence.record_once(PK, "correction", source_session_id="s-dup")
        second = ImprovementEvidence.record_once(PK, "correction", source_session_id="s-dup")
        assert first is not None
        assert second is None

    def test_dedup_prefers_source_ref_when_present(self):
        assert (
            ImprovementEvidence.record_once(PK, "inspiration", source_ref="memory:abc") is not None
        )
        # Same ref, different session: still the same idea, still one row.
        assert (
            ImprovementEvidence.record_once(
                PK, "inspiration", source_ref="memory:abc", source_session_id="other"
            )
            is None
        )

    def test_different_kinds_do_not_collide(self):
        assert ImprovementEvidence.record_once(PK, "correction", source_ref="r-1") is not None
        assert ImprovementEvidence.record_once(PK, "shipped_work", source_ref="r-1") is not None

    def test_an_observation_with_no_identity_is_never_deduped(self):
        """Two anonymous observations are two observations, not one."""
        assert ImprovementEvidence.record_once(PK, "other", text="a") is not None
        assert ImprovementEvidence.record_once(PK, "other", text="b") is not None

    def test_unknown_kind_degrades_to_other_rather_than_raising(self):
        row = ImprovementEvidence.record_once(PK, "not-a-kind", source_ref="k-1")
        assert row is not None
        assert row.kind == "other"

    def test_unknown_classification_degrades_to_unknown(self):
        row = ImprovementEvidence.record_once(
            PK, "correction", classification="wildly-confident", source_ref="c-1"
        )
        assert row is not None
        assert row.classification == "unknown"

    def test_recent_returns_newest_first_and_respects_limit(self):
        for i in range(5):
            ImprovementEvidence.record_once(PK, "other", source_ref=f"n-{i}")
        rows = ImprovementEvidence.recent(PK, limit=3)
        assert len(rows) == 3
        stamps = [r.created_at for r in rows if r.created_at]
        assert stamps == sorted(stamps, reverse=True)

    def test_partition_is_scoped_to_project_key(self):
        ImprovementEvidence.record_once(PK, "other", source_ref="scoped-1")
        assert ImprovementEvidence.recent("test-3177-other-project", limit=10) == []


class TestRecentIsGenuinelyBounded:
    """``recent()`` must push a real range predicate down, not scan the partition.

    The defect this guards: ``created_at__gt=0`` against a
    ``SortedField(type=datetime)`` raises ``AttributeError: 'int' object has no
    attribute 'tzinfo'`` on every call. Wrapped in a bare ``except`` it degraded
    silently into an unbounded full-partition read that returned the same answer
    in a small test partition — invisible to any test that only checks results.
    So this checks the predicate itself, and that nothing swallows a failure.
    """

    def test_the_lower_bound_is_a_datetime_not_an_int(self):
        from datetime import datetime as _dt

        with patch.object(ImprovementEvidence, "query") as q:
            q.filter.return_value = []
            ImprovementEvidence.recent(PK, limit=7)

        kwargs = q.filter.call_args.kwargs
        assert isinstance(kwargs["created_at__gt"], _dt), (
            "an int bound raises inside popoto and turns the read unbounded"
        )
        assert kwargs["created_at__gt"].tzinfo is not None
        assert kwargs["limit"] == 7
        assert kwargs["project_key"] == PK

    def test_the_bounded_read_returns_the_newest_page_not_the_oldest(self):
        """The page has to come off the correct end of the sorted set.

        popoto applies ``limit`` as a real bound on the range read itself
        (``num=`` on the ``ZRANGEBYSCORE``/``ZREVRANGEBYSCORE`` call), so an
        ascending-direction read hydrates the *oldest* ``limit`` rows of the
        partition regardless of how large the partition is. Seeding 20 rows
        against ``limit=3`` makes that unambiguous: the ascending answer is
        always ``ord-2..ord-0``, while the descending, correct answer is
        ``ord-19..ord-17``. Asserting identities rather than "the stamps are
        in order" is the other half — the stamps are always in order, because
        ``recent()`` sorts them.
        """
        pk = "test-3177-ordering"
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(20):
            ImprovementEvidence.create(
                project_key=pk,
                created_at=base + timedelta(minutes=i),
                kind="other",
                classification="unknown",
                source_ref=f"ord-{i}",
            )

        rows = ImprovementEvidence.recent(pk, limit=3)

        assert [r.source_ref for r in rows] == ["ord-19", "ord-18", "ord-17"], (
            "an unordered bounded read pages from the oldest end of the partition"
        )

    def test_a_backend_failure_is_loud_not_swallowed(self):
        """A silent fallback here is what hid the defect for a whole review round.

        The bounded read fails and the *unbounded* one would succeed — which is
        exactly the shape of the retired ``except``: it converted a broken
        predicate into a full-partition scan that looked healthy. So the failure
        must propagate rather than be answered from the fallback.
        """

        def only_the_bounded_read_fails(**kwargs):
            if "created_at__gt" in kwargs:
                raise RuntimeError("sorted partition unavailable")
            return []

        with patch.object(ImprovementEvidence, "query") as q:
            q.filter.side_effect = only_the_bounded_read_fails
            with pytest.raises(RuntimeError):
                ImprovementEvidence.recent(PK)

    def test_an_empty_partition_returns_empty_rather_than_raising(self):
        assert ImprovementEvidence.recent("test-3177-never-written") == []


class TestClassifyCorrection:
    @pytest.mark.parametrize(
        "text",
        [
            "no, i meant the whole journey, not just that endpoint",
            "that's the wrong approach entirely",
            "you missed the point of this feature",
            "this is the wrong abstraction",
        ],
    )
    def test_architectural_phrasings(self, text):
        assert improvement_collect.classify_correction(text) == "architectural"

    @pytest.mark.parametrize(
        "text",
        ["i prefer snake_case here", "the wording is off", "nit: rename this variable"],
    )
    def test_preference_phrasings(self, text):
        assert improvement_collect.classify_correction(text) == "preference"

    @pytest.mark.parametrize(
        "text",
        ["also add a CSV export", "on second thought, let's do both"],
    )
    def test_scope_phrasings(self, text):
        assert improvement_collect.classify_correction(text) == "scope"

    @pytest.mark.parametrize(
        "text",
        ["actually, hold on", "i said tuesday", "please don't", "that's wrong"],
    )
    def test_ambiguous_phrasings_stay_unknown(self, text):
        """The common case. An honest 'unknown' beats a confident wrong label."""
        assert improvement_collect.classify_correction(text) == "unknown"

    def test_every_classification_is_a_declared_value(self):
        for text in ("wrong approach", "nit", "also add", "hello"):
            assert improvement_collect.classify_correction(text) in EVIDENCE_CLASSIFICATIONS


class _FakeSession:
    """A session stand-in that does NOT auto-invent attributes.

    Deliberately not a ``MagicMock``: a mock answers ``getattr(s, "log_path")``
    with a truthy auto-child, so a detector that re-gated on the writerless
    ``log_path`` would still pass every test here. This class raises
    ``AttributeError`` for anything unset, which is what makes the mutation
    check on that gate bite.
    """

    def __init__(self, name, turns=(), outbound=(), created_at=None):
        self.session_id = name
        self.id = name
        self.created_at = created_at
        self.chat_message_log = [
            {"direction": "in", "sender": "Tom", "content": t, "message_id": i, "ts": 1.0}
            for i, t in enumerate(turns)
        ] + [
            {"direction": "out", "sender": "valor", "content": t, "message_id": None, "ts": 2.0}
            for t in outbound
        ]


def _session(name, turns=(), outbound=(), created_at=None):
    """A session whose chat_message_log carries the given inbound/outbound text.

    ``chat_message_log`` is the field ``bridge/dispatch.py::_append_inbound_chat_log``
    writes on every inbound Telegram message — the correction detector's real
    session-side input. Entry shape is fixed by
    ``AgentSession.append_chat_log``: ``{direction, sender, content, message_id, ts}``.
    """
    return _FakeSession(
        name, turns=turns, outbound=outbound, created_at=created_at or datetime.now(UTC)
    )


class TestCollectCorrections:
    """The detector reads two fields that production actually writes.

    The defect these tests exist to prevent: keying the detector on a field
    with no production writer, which makes it structurally always zero and
    indistinguishable from "no corrections happened". Both inputs below have a
    named writer, asserted in ``TestDetectorInputsHaveProductionWriters``.
    """

    def test_writes_one_row_per_session_with_a_correction(self):
        sessions = [
            _session("sess-a", turns=["no, i meant the other approach"]),
            _session("sess-b", turns=["sounds good"]),
        ]
        with patch.object(improvement_collect, "_recent_sessions", return_value=sessions):
            written = improvement_collect.collect_corrections(PK, memories=[])

        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="correction"))
        assert [r.source_session_id for r in rows] == ["sess-a"]

    def test_a_session_contributes_one_row_however_many_matches(self):
        session = _session("sess-many", turns=["no, i meant X", "that's wrong", "i said Y"])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_corrections(PK, memories=[]) == 1

    def test_rerunning_the_tick_writes_nothing_new(self):
        """The tick re-reads an overlapping window by design; dedup makes that free."""
        session = _session("sess-idem", turns=["that's wrong"])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_corrections(PK, memories=[]) == 1
            assert improvement_collect.collect_corrections(PK, memories=[]) == 0

    def test_a_session_with_no_chat_log_is_skipped_not_fatal(self):
        """Most sessions are not conversational; an empty log is the common case."""
        good = _session("sess-good", turns=["that's wrong"])
        empty = _session("sess-empty")
        empty.chat_message_log = None

        with patch.object(improvement_collect, "_recent_sessions", return_value=[empty, good]):
            assert improvement_collect.collect_corrections(PK, memories=[]) == 1

    def test_outbound_lines_are_not_scanned(self):
        """A correction pattern in the agent's own reply is not a human correcting it."""
        session = _session("sess-agent", outbound=["actually, that's wrong of me"])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_corrections(PK, memories=[]) == 0

    def test_log_path_is_not_consulted_in_either_direction(self):
        """The retired input must not gate the detector.

        ``AgentSession.log_path``'s sole assigner, ``start_transcript``, has no
        production caller, so gating on it makes the detector structurally
        always zero — the writerless-field defect this whole feature exists to
        retire. Detection must be identical whether the field is set or absent,
        and ``_FakeSession`` raises ``AttributeError`` for it rather than
        auto-inventing a truthy value the way a ``MagicMock`` would.
        """
        session = _session("sess-logpath", turns=["that's wrong"])
        session.log_path = "/tmp/does-not-matter/transcript.txt"
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            # The correction is found in the chat log, not the transcript, and
            # the presence or absence of log_path changes nothing.
            assert improvement_collect.collect_corrections(PK, memories=[]) == 1

        no_path = _session("sess-nopath", turns=["that's wrong"])
        assert not hasattr(no_path, "log_path")
        with patch.object(improvement_collect, "_recent_sessions", return_value=[no_path]):
            assert improvement_collect.collect_corrections(PK, memories=[]) == 1

    def test_a_tom_sourced_memory_correction_is_recorded(self):
        """The source that fires where sessions were not created by the bridge."""
        memories = [
            _memory("m-corr", "actually, that is the wrong approach entirely"),
            _memory("m-calm", "looks good to me"),
        ]
        with patch.object(improvement_collect, "_recent_sessions", return_value=[]):
            assert improvement_collect.collect_corrections(PK, memories=memories) == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="correction"))
        assert [r.source_ref for r in rows] == ["memory:m-corr"]

    def test_memory_corrections_dedup_across_ticks(self):
        memories = [_memory("m-corr-idem", "that's wrong, i said the other one")]
        with patch.object(improvement_collect, "_recent_sessions", return_value=[]):
            assert improvement_collect.collect_corrections(PK, memories=memories) == 1
            assert improvement_collect.collect_corrections(PK, memories=memories) == 0

    def test_both_sources_contribute(self):
        sessions = [_session("sess-both", turns=["not what i asked for"])]
        memories = [_memory("m-both", "please stop doing it that way")]
        with patch.object(improvement_collect, "_recent_sessions", return_value=sessions):
            assert improvement_collect.collect_corrections(PK, memories=memories) == 2

    def test_classification_rides_along(self):
        session = _session(
            "sess-arch", turns=["that's wrong — it missed the point of the whole journey"]
        )
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            improvement_collect.collect_corrections(PK, memories=[])
        rows = list(
            ImprovementEvidence.query.filter(
                project_key=PK, kind="correction", classification="architectural"
            )
        )
        assert [r.source_session_id for r in rows] == ["sess-arch"]

    def test_no_memories_argument_falls_back_to_a_real_fetch(self):
        """The adapter is callable on its own; the shared fetch is an optimization."""
        with patch.object(
            improvement_collect, "human_memories", return_value=[_memory("m-fetch", "i said no")]
        ) as fetch:
            with patch.object(improvement_collect, "_recent_sessions", return_value=[]):
                assert improvement_collect.collect_corrections(PK) == 1
        fetch.assert_called_once_with(PK)


class TestDetectorInputsHaveProductionWriters:
    """Pin the writers. A detector reading a writerless field measures nothing.

    ``rework_rate`` was structurally always zero because
    ``AgentSession.rework_triggered`` had no production writer; retiring exactly
    that shape is why this feature exists. These are source-level assertions
    because the writers live in the bridge, which does not run under pytest.
    """

    def test_chat_message_log_has_a_bridge_writer(self):
        from pathlib import Path as _Path

        src = _Path("bridge/dispatch.py").read_text()
        assert "_append_inbound_chat_log" in src
        assert "append_chat_log(" in src
        # Called from the dispatch path, not merely defined.
        assert src.count("_append_inbound_chat_log") >= 2

    def test_human_sourced_memory_has_a_bridge_writer(self):
        from pathlib import Path as _Path

        src = _Path("bridge/telegram_bridge.py").read_text()
        assert "Memory.safe_save(" in src
        assert 'source="human"' in src

    def test_outbound_chat_message_log_has_bridge_writers(self):
        """The promise detector's input: ``direction="out"`` entries.

        The sole production writer of an outbound ``chat_message_log`` entry is
        ``bridge/telegram_relay.py::_append_outbound_chat_log``, called from the
        relay's send path. (``bridge/telegram_bridge.py`` writes
        ``direction="out"`` into the chat history store through
        ``store_message``, a different field on a different model.) A detector
        keyed on an entry direction nothing writes would be structurally always
        zero, so the writer is pinned here at source level, the same way the
        inbound writer is pinned above.
        """
        from pathlib import Path as _Path

        src = _Path("bridge/telegram_relay.py").read_text()
        assert "_append_outbound_chat_log" in src
        assert 'append_chat_log("out"' in src
        # Called from the send path, not merely defined.
        assert "to_thread(_append_outbound_chat_log" in src

    def test_the_retired_input_still_has_no_production_writer(self):
        """If ``start_transcript`` ever gains a caller this can be revisited."""
        import subprocess

        out = subprocess.run(
            ["/usr/bin/grep", "-rn", "start_transcript", "bridge/", "agent/", "worker/", "tools/"],
            capture_output=True,
            text=True,
        ).stdout
        callers = [
            line
            for line in out.splitlines()
            if "__pycache__" not in line and "def start_transcript" not in line
        ]
        assert callers == [], f"start_transcript gained a caller: {callers}"


class TestRecentSessions:
    """The window feeding the detector, exercised rather than patched out."""

    def test_returns_newest_first_and_respects_the_limit(self):
        from models.agent_session import AgentSession

        rows = [
            _session("old", created_at=datetime(2020, 1, 1, tzinfo=UTC)),
            _session("new", created_at=datetime(2030, 1, 1, tzinfo=UTC)),
            _session("mid", created_at=datetime(2025, 1, 1, tzinfo=UTC)),
        ]
        with patch.object(AgentSession, "query") as q:
            q.filter.return_value = rows
            got = improvement_collect._recent_sessions("pk", 2)
        assert [s.session_id for s in got] == ["new", "mid"]

    def test_a_tz_naive_created_at_does_not_raise(self):
        """popoto strips tzinfo on load; a hand-rolled sort key would TypeError."""
        from models.agent_session import AgentSession

        rows = [
            _session("aware", created_at=datetime(2030, 1, 1, tzinfo=UTC)),
            _session("naive", created_at=datetime(2029, 1, 1)),
            _session("absent"),
        ]
        rows[2].created_at = None
        with patch.object(AgentSession, "query") as q:
            q.filter.return_value = rows
            got = improvement_collect._recent_sessions("pk", 5)
        assert [s.session_id for s in got] == ["aware", "naive", "absent"]

    def test_a_failed_scan_is_not_fatal(self):
        from models.agent_session import AgentSession

        with patch.object(AgentSession, "query") as q:
            q.filter.side_effect = RuntimeError("redis down")
            assert improvement_collect._recent_sessions("pk", 5) == []


def _memory(memory_id, content, source="human", reference="", superseded_by=""):
    m = MagicMock()
    m.memory_id = memory_id
    m.content = content
    m.source = source
    m.reference = reference
    m.superseded_by = superseded_by
    m.created_at = datetime.now(UTC)
    return m


class TestCollectInspirations:
    def test_records_tom_sourced_memories(self):
        records = [
            _memory("m1", "read this: https://example.com/paper", reference="https://example.com"),
            _memory("m2", "an agent's own observation", source="agent"),
        ]
        with patch("tools.memory_search.fetch_all_records", return_value=records):
            written = improvement_collect.collect_inspirations(PK)

        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="inspiration"))
        assert [r.source_ref for r in rows] == ["memory:m1"]
        assert rows[0].detail == "https://example.com"

    def test_preserves_the_original_text(self):
        body = "worth looking at Meta Muse 1.3 for cheap tokens"
        with patch("tools.memory_search.fetch_all_records", return_value=[_memory("m3", body)]):
            improvement_collect.collect_inspirations(PK)
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="inspiration"))
        assert any(r.text == body for r in rows)

    def test_superseded_memories_are_skipped(self):
        """A consolidated memory is represented by its replacement; both would double-count."""
        records = [_memory("m4", "old idea", superseded_by="m5")]
        with patch("tools.memory_search.fetch_all_records", return_value=records):
            assert improvement_collect.collect_inspirations(PK) == 0

    def test_rerunning_the_tick_writes_nothing_new(self):
        records = [_memory("m6", "an idea")]
        with patch("tools.memory_search.fetch_all_records", return_value=records):
            assert improvement_collect.collect_inspirations(PK) == 1
            assert improvement_collect.collect_inspirations(PK) == 0

    def test_uses_enumeration_not_search(self):
        """search() is a relevance-ranked top-N with no source parameter.

        An adapter built on it would silently under-read, which is the Risk 2
        failure this whole plan exists to prevent. Pin the seam.
        """
        with patch("tools.memory_search.fetch_all_records", return_value=[]) as fetch:
            with patch("tools.memory_search.search") as search:
                improvement_collect.collect_inspirations(PK)
        fetch.assert_called_once_with(PK)
        search.assert_not_called()

    def test_a_broken_memory_backend_is_not_fatal(self):
        with patch("tools.memory_search.fetch_all_records", side_effect=RuntimeError("redis down")):
            assert improvement_collect.collect_inspirations(PK) == 0


class TestCollectExpectationCoverage:
    def test_writes_a_coverage_row_even_with_no_jobs(self):
        with patch("models.job.Job.with_open_expectations", return_value=[]):
            written = improvement_collect.collect_expectation_coverage(PK)
        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="shipped_work"))
        assert any("coverage" in (r.source_ref or "") for r in rows)

    def test_records_a_liveness_row_per_gone_owner(self):
        job = MagicMock()
        job.room_id = f"{PK}|someone"
        job.job_id = "job-1"
        job.goal_is_corrupt.return_value = False
        job.open_expectations.return_value = [
            {"id": "e1", "owner": "session/lane-a", "what": "ship the thing"}
        ]

        with (
            patch("models.job.Job.with_open_expectations", return_value=[job]),
            patch(
                "reflections.expectation_reconciler._owner_is_gone",
                return_value=True,
            ),
        ):
            written = improvement_collect.collect_expectation_coverage(PK)

        assert written == 2  # one liveness row, one coverage row
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="owner_liveness"))
        assert rows and rows[0].source_ref == "expectation:job-1:e1"

    def test_a_live_owner_records_no_liveness_row(self):
        job = MagicMock()
        job.room_id = f"{PK}|someone"
        job.job_id = "job-2"
        job.goal_is_corrupt.return_value = False
        job.open_expectations.return_value = [{"id": "e2", "owner": "session/lane-b", "what": "x"}]

        with (
            patch("models.job.Job.with_open_expectations", return_value=[job]),
            patch("reflections.expectation_reconciler._owner_is_gone", return_value=False),
        ):
            written = improvement_collect.collect_expectation_coverage(PK)

        assert written == 1  # coverage row only
        assert not list(ImprovementEvidence.query.filter(project_key=PK, kind="owner_liveness"))

    def test_coverage_text_carries_the_denominator(self):
        """A rate with no denominator is what makes 'fewer rescues' unreadable."""
        job = MagicMock()
        job.room_id = f"{PK}|someone"
        job.job_id = "job-3"
        job.goal_is_corrupt.return_value = False
        job.open_expectations.return_value = [
            {"id": "e3", "owner": "session/lane-c", "what": "x"},
            {"id": "e4", "owner": "session/lane-d", "what": "y"},
        ]

        with (
            patch("models.job.Job.with_open_expectations", return_value=[job]),
            patch("reflections.expectation_reconciler._owner_is_gone", return_value=None),
        ):
            improvement_collect.collect_expectation_coverage(PK)

        rows = [
            r
            for r in ImprovementEvidence.query.filter(project_key=PK, kind="shipped_work")
            if (r.source_ref or "").startswith("coverage:")
        ]
        assert rows
        assert "scanned=2" in (rows[0].detail or "")
        assert "unknown=2" in (rows[0].detail or "")


@contextmanager
def _improvement_enabled(flag: bool):
    """Flip ``ImprovementSettings.enabled`` for the duration of a block."""
    from config.settings import settings

    previous = settings.improvement.enabled
    settings.improvement.enabled = flag
    try:
        yield
    finally:
        settings.improvement.enabled = previous


class TestKillSwitch:
    """``enabled=False`` must mean what config/settings.py and .env.example say.

    A documented switch that gates nothing is worse than no switch: the operator
    reads "off" while a 15-minute writer runs against production Redis.
    """

    def test_disabled_writes_nothing(self):
        session = _session("sess-killswitch", turns=["that's wrong"])
        with (
            _improvement_enabled(False),
            patch.dict(os.environ, {"VALOR_PROJECT_KEY": PK}),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
            patch.object(improvement_collect, "human_memories", return_value=[]),
            patch("models.job.Job.with_open_expectations", return_value=[]),
        ):
            before = len(list(ImprovementEvidence.query.filter(project_key=PK)))
            result = improvement_collect.run_improvement_collect()
            after = len(list(ImprovementEvidence.query.filter(project_key=PK)))

        assert result["status"] == "skipped"
        assert result["counts"] == {}
        assert "disabled" in result["summary"]
        assert after == before

    def test_enabled_writes(self):
        session = _session("sess-killswitch-on", turns=["that's wrong"])
        with (
            _improvement_enabled(True),
            patch.dict(os.environ, {"VALOR_PROJECT_KEY": PK}),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
            patch.object(improvement_collect, "human_memories", return_value=[]),
            patch("models.job.Job.with_open_expectations", return_value=[]),
        ):
            before = len(list(ImprovementEvidence.query.filter(project_key=PK)))
            result = improvement_collect.run_improvement_collect()
            after = len(list(ImprovementEvidence.query.filter(project_key=PK)))

        assert result["status"] == "success"
        assert result["counts"]["corrections"] == 1
        assert after > before

    def test_the_documented_default_is_off(self):
        from config.settings import ImprovementSettings

        assert ImprovementSettings().enabled is False


ADAPTER_NAMES = ("corrections", "inspirations", "expectation_coverage", "lessons", "promises")

_ADAPTER_FUNCS = {
    "corrections": "collect_corrections",
    "inspirations": "collect_inspirations",
    "expectation_coverage": "collect_expectation_coverage",
    "lessons": "collect_lessons",
    "promises": "collect_promises",
}


@contextmanager
def _adapters(real=(), **outcomes):
    """Patch every adapter at once, except the ones named in ``real``.

    ``outcomes`` maps adapter name to either an int (the count it returns),
    an exception instance (what it raises), or a callable (used as the
    replacement). Unnamed adapters return 0.
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(_improvement_enabled(True))
        stack.enter_context(patch.object(improvement_collect, "human_memories", return_value=[]))
        for name, func in _ADAPTER_FUNCS.items():
            if name in real:
                continue
            outcome = outcomes.get(name, 0)
            if isinstance(outcome, Exception) or callable(outcome):
                kwargs = {"side_effect": outcome}
            else:
                kwargs = {"return_value": outcome}
            stack.enter_context(patch.object(improvement_collect, func, **kwargs))
        yield


class TestRunImprovementCollect:
    def test_returns_a_reflection_result_dict(self):
        with _adapters(corrections=2, inspirations=1, expectation_coverage=1, lessons=1):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"] == {
            "corrections": 2,
            "inspirations": 1,
            "expectation_coverage": 1,
            "lessons": 1,
            "promises": 0,
        }
        assert "5 new evidence row" in result["summary"]
        assert result["findings"] == []
        assert result["failed"] == []
        assert result["skipped"] == []
        assert isinstance(result["duration"], float)

    def test_the_tick_runs_exactly_five_adapters(self):
        assert improvement_collect.ADAPTER_NAMES == ADAPTER_NAMES

    def test_the_memory_partition_is_enumerated_once_per_tick(self):
        """Both adapters read the same partition; two fetches would double the cost."""
        with (
            _improvement_enabled(True),
            patch.object(improvement_collect, "human_memories", return_value=[]) as fetch,
            patch.object(improvement_collect, "collect_expectation_coverage", return_value=0),
            patch.object(improvement_collect, "collect_lessons", return_value=0),
            patch.object(improvement_collect, "collect_promises", return_value=0),
            patch.object(improvement_collect, "_recent_sessions", return_value=[]),
        ):
            improvement_collect.run_improvement_collect()
        assert fetch.call_count == 1

    def test_one_broken_adapter_degrades_the_tick_rather_than_ending_it(self):
        with _adapters(corrections=RuntimeError("boom"), inspirations=3):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"]["corrections"] == 0
        assert result["counts"]["inspirations"] == 3
        assert any("corrections-failed" in f for f in result["findings"])
        assert result["failed"] == ["corrections"]

    def test_all_failed_is_error(self):
        """Every adapter failing is the one shape that is an error."""
        with _adapters(**{name: RuntimeError(name) for name in ADAPTER_NAMES}):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "error"
        assert sorted(result["failed"]) == sorted(ADAPTER_NAMES)
        assert len(result["findings"]) == len(ADAPTER_NAMES)

    def test_all_skipped_is_success(self):
        """A skip is a rule declining, never a failure: five skips is a healthy tick."""

        def _skip(name):
            def adapter(project_key, **kwargs):
                skipped = kwargs.get("skipped")
                if skipped is not None:
                    skipped.append(f"{name}-skipped: by rule")
                return 0

            return adapter

        with _adapters(**{name: _skip(name) for name in ADAPTER_NAMES}):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["failed"] == []
        assert len(result["skipped"]) == len(ADAPTER_NAMES)

    def test_a_detector_that_is_off_is_a_skip_not_a_failure(self):
        """The promise detector defaults off; the tick must read that as a skip."""
        with (
            _adapters(real=("promises",)),
            _promise_detector_enabled(False),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["failed"] == []
        assert any(s.startswith("promises-skipped") for s in result["skipped"])


class TestVocabulariesStayInSync:
    def test_adapters_only_emit_declared_kinds(self):
        for kind in (
            "correction",
            "inspiration",
            "shipped_work",
            "owner_liveness",
            "lesson",
            "promise",
        ):
            assert kind in EVIDENCE_KINDS


# --- lane 5 (#3217): lessons -------------------------------------------------


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _pr(number, title, body, merged_at="2026-09-10T12:00:00Z"):
    return {"number": number, "title": title, "body": body, "mergedAt": merged_at}


def _gh_runner(prs):
    """A runner that answers any ``gh`` invocation with the given PR list."""
    import json

    calls: list[list[str]] = []

    def runner(args):
        calls.append(list(args))
        return _completed(json.dumps(prs))

    runner.calls = calls
    return runner


class TestCollectLessons:
    """Merged PR bodies, the seven prefixes, one ``lesson`` row per line."""

    def test_writes_one_row_per_prefixed_line(self):
        body = (
            "Summary of the change.\n"
            "- lesson: run the guard from the worktree\n"
            "- pattern: mutation-check each guard\n"
            "- unrelated: this line has no recognized prefix\n"
        )
        runner = _gh_runner([_pr(101, "Fix the thing", body)])
        written = improvement_collect.collect_lessons(PK, runner=runner)

        assert written == 2
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="lesson"))
        assert sorted(r.text for r in rows) == [
            "- lesson: run the guard from the worktree",
            "- pattern: mutation-check each guard",
        ]
        assert all(r.source_ref.startswith("pr:101:") for r in rows)
        assert all(len(r.source_ref.split(":")[2]) == 16 for r in rows)

    @pytest.mark.parametrize(
        "prefix",
        [
            "- lesson:",
            "- pattern:",
            "- note:",
            "- convention:",
            "- learning:",
            "- reminder:",
            "- caveat:",
        ],
    )
    def test_every_one_of_the_seven_prefixes_is_scraped(self, prefix):
        runner = _gh_runner([_pr(7, "t", f"{prefix} something worth keeping")])
        assert improvement_collect.collect_lessons(PK, runner=runner) == 1

    def test_prefix_match_is_case_insensitive_and_whitespace_tolerant(self):
        runner = _gh_runner([_pr(8, "t", "   - Lesson: indented and capitalized")])
        assert improvement_collect.collect_lessons(PK, runner=runner) == 1

    def test_detail_carries_title_and_stage_guess_and_observed_at_is_merged_at(self):
        import json

        runner = _gh_runner(
            [_pr(9, "Tighten the do-test gate", "- lesson: pytest needs the worktree PYTHONPATH")]
        )
        improvement_collect.collect_lessons(PK, runner=runner)
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="lesson"))
        detail = json.loads(rows[0].detail)
        assert detail["title"] == "Tighten the do-test gate"
        assert detail["stage_guess"] == "do-test"
        assert rows[0].observed_at.replace(tzinfo=UTC) == datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def test_stage_guess_is_unknown_when_nothing_matches(self):
        import json

        runner = _gh_runner([_pr(10, "zzz", "- note: qqq")])
        improvement_collect.collect_lessons(PK, runner=runner)
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="lesson"))
        assert json.loads(rows[0].detail)["stage_guess"] == "unknown"

    def test_rerunning_the_tick_writes_nothing_new(self):
        runner = _gh_runner([_pr(11, "t", "- caveat: dedup on source_ref")])
        assert improvement_collect.collect_lessons(PK, runner=runner) == 1
        assert improvement_collect.collect_lessons(PK, runner=runner) == 0

    def test_a_none_body_or_no_prefixed_lines_yields_zero_rows(self):
        runner = _gh_runner([_pr(12, "t", None), _pr(13, "t", "no prefixed lines here")])
        assert improvement_collect.collect_lessons(PK, runner=runner) == 0

    def test_since_defaults_to_fourteen_days_and_then_to_the_newest_lesson(self):
        runner = _gh_runner([])
        improvement_collect.collect_lessons(PK, runner=runner)
        (args,) = runner.calls
        search = args[args.index("--search") + 1]
        expected = (datetime.now(UTC) - timedelta(days=14)).date().isoformat()
        assert search == f"merged:>={expected}"
        assert args[:4] == ["pr", "list", "--state", "merged"]
        assert "number,title,body,mergedAt" in args

        ImprovementEvidence.record_once(
            PK,
            "lesson",
            source_ref="pr:1:abc",
            text="- lesson: x",
            observed_at=datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
        )
        runner = _gh_runner([])
        improvement_collect.collect_lessons(PK, runner=runner)
        (args,) = runner.calls
        assert args[args.index("--search") + 1] == "merged:>=2026-09-03"

    def test_a_raising_runner_is_a_warning_and_a_findings_entry_never_a_raise(self, caplog):
        def runner(args):
            raise RuntimeError("gh exploded")

        findings: list[str] = []
        with caplog.at_level("WARNING", logger="reflections.improvement_collect"):
            written = improvement_collect.collect_lessons(PK, runner=runner, findings=findings)

        assert written == 0
        assert findings == ["lessons-gh-failed: gh exploded"]
        assert any("gh exploded" in rec.getMessage() for rec in caplog.records)

    def test_a_non_zero_gh_exit_or_bad_json_yields_zero_rows(self):
        assert (
            improvement_collect.collect_lessons(PK, runner=lambda a: _completed("", 1, "nope")) == 0
        )
        assert improvement_collect.collect_lessons(PK, runner=lambda a: _completed("not json")) == 0
        assert improvement_collect.collect_lessons(PK, runner=lambda a: None) == 0

    def test_a_raising_runner_leaves_the_other_adapters_untouched_in_the_tick(self):
        def runner(args):
            raise RuntimeError("gh exploded")

        with (
            _adapters(real=("lessons",), inspirations=2),
            patch.object(improvement_collect, "_default_gh_runner", return_value=runner),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"]["inspirations"] == 2
        assert result["counts"]["lessons"] == 0
        assert any("lessons-gh-failed" in f for f in result["findings"])
        assert result["failed"] == []

    def test_the_default_runner_strips_the_token_env_and_asks_for_the_project_repo(self):
        """The keyring auth answers; a stale GITHUB_TOKEN in the environment must not."""
        import os

        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["env"] = kwargs.get("env")
            return _completed("[]")

        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "stale", "GH_TOKEN": "stale"}),
            patch("subprocess.run", side_effect=fake_run),
            patch.object(improvement_collect, "_project_repository", return_value="org/repo"),
        ):
            improvement_collect.collect_lessons(PK)

        assert seen["argv"][0] == "gh"
        assert "GITHUB_TOKEN" not in seen["env"] and "GH_TOKEN" not in seen["env"]
        assert seen["argv"][seen["argv"].index("--repo") + 1] == "org/repo"


# --- lane 5 (#3217): promises ------------------------------------------------


@contextmanager
def _promise_detector_enabled(flag: bool, model: str | None = None):
    from config.settings import settings

    previous = (
        settings.improvement.promise_detector_enabled,
        settings.improvement.cheap_inference_model,
    )
    settings.improvement.promise_detector_enabled = flag
    if model is not None:
        settings.improvement.cheap_inference_model = model
    try:
        yield
    finally:
        (
            settings.improvement.promise_detector_enabled,
            settings.improvement.cheap_inference_model,
        ) = previous


def _yes(span="I will have it done by Friday", confidence=0.9):
    import json

    return json.dumps({"answer": "yes", "span": span, "confidence": confidence})


def _no():
    import json

    return json.dumps({"answer": "no", "span": "", "confidence": 0.8})


def _transport(replies):
    """A judge transport that answers from a queue and records every prompt."""
    replies = list(replies)
    prompts: list[str] = []

    def transport(prompt):
        prompts.append(prompt)
        return replies.pop(0)

    transport.prompts = prompts
    return transport


@pytest.fixture
def promise_env():
    """Detector on, meter patched to accept, judged-set cleared, sessions patched by the test."""
    from tools import paid_inference_meter as meter

    reservation = meter.Reservation("res-1", PK, 1, "promise_detector", None, "2026-09-15")
    with (
        _improvement_enabled(True),
        _promise_detector_enabled(True),
        patch.object(meter, "reserve", return_value=reservation) as reserve,
        patch.object(meter, "settle") as settle,
        patch.object(meter, "release") as release,
    ):
        improvement_collect._clear_judged(PK)
        yield {"reserve": reserve, "settle": settle, "release": release}


class TestCollectPromises:
    """Sampled outbound entries, one cheap yes/no judge call each, metered."""

    def test_a_yes_writes_a_promise_row_with_span_and_confidence(self, promise_env):
        session = _session("sess-p1", outbound=["I will have it done by Friday, guaranteed."])
        transport = _transport([_yes()])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            written = improvement_collect.collect_promises(PK, transport=transport)

        assert written == 1
        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="promise"))
        assert rows[0].text == "I will have it done by Friday, guaranteed."
        assert rows[0].detail == "I will have it done by Friday"
        assert float(rows[0].confidence) == 0.9
        assert rows[0].source_session_id == "sess-p1"
        assert rows[0].source_ref.startswith("promise:sess-p1:")

    def test_the_prompt_quotes_the_charter_paragraph_and_the_question(self, promise_env):
        session = _session("sess-p2", outbound=["Working on it now."])
        transport = _transport([_no()])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            improvement_collect.collect_promises(PK, transport=transport)

        (prompt,) = transport.prompts
        assert "Valor makes no promises." in prompt
        assert "Optimism is not control." in prompt
        assert (
            "Does this message guarantee delivery, future effort, future communication, "
            "or an outcome the sender does not control, without qualification?"
        ) in prompt
        assert "Working on it now." in prompt

    def test_a_no_writes_nothing_and_is_not_rejudged_next_tick(self, promise_env):
        session = _session("sess-p3", outbound=["Working on it now."])
        transport = _transport([_no(), _no()])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_promises(PK, transport=transport) == 0
            assert improvement_collect.collect_promises(PK, transport=transport) == 0
        assert len(transport.prompts) == 1

    def test_inbound_entries_are_never_judged(self, promise_env):
        session = _session("sess-p4", turns=["can you promise me it ships?"])
        transport = _transport([_yes()])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            assert improvement_collect.collect_promises(PK, transport=transport) == 0
        assert transport.prompts == []
        promise_env["reserve"].assert_not_called()

    def test_empty_log_and_empty_content_yield_zero_rows_and_no_spend(self, promise_env):
        empty = _session("sess-p5")
        empty.chat_message_log = None
        blank = _session("sess-p6", outbound=["", "   "])
        transport = _transport([])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[empty, blank]):
            assert improvement_collect.collect_promises(PK, transport=transport) == 0
        assert transport.prompts == []
        promise_env["reserve"].assert_not_called()

    def test_samples_at_most_the_cap_newest_first(self, promise_env):
        cap = improvement_collect.PROMISE_SAMPLE_PER_TICK
        session = _session("sess-p7", outbound=[f"msg {i}" for i in range(cap + 5)])
        for i, entry in enumerate(e for e in session.chat_message_log if e["direction"] == "out"):
            entry["ts"] = float(i)
        transport = _transport([_no()] * cap)
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            improvement_collect.collect_promises(PK, transport=transport)

        assert len(transport.prompts) == cap
        assert f"msg {cap + 4}" in transport.prompts[0]
        assert not any("msg 0" in p for p in transport.prompts)

    def test_unparseable_judge_output_is_zero_rows_plus_a_findings_entry(self, promise_env):
        session = _session("sess-p8", outbound=["I promise."])
        transport = _transport(["definitely maybe"])
        findings: list[str] = []
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            written = improvement_collect.collect_promises(
                PK, transport=transport, findings=findings
            )

        assert written == 0
        assert len(findings) == 1
        assert findings[0].startswith("promises-judge-unparseable")
        assert not list(ImprovementEvidence.query.filter(project_key=PK, kind="promise"))

    def test_a_raising_transport_is_a_warning_and_a_findings_entry_never_a_raise(
        self, promise_env, caplog
    ):
        session = _session("sess-p9", outbound=["I promise."])

        def transport(prompt):
            raise RuntimeError("judge exploded")

        findings: list[str] = []
        with (
            caplog.at_level("WARNING", logger="reflections.improvement_collect"),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
        ):
            written = improvement_collect.collect_promises(
                PK, transport=transport, findings=findings
            )

        assert written == 0
        assert findings == ["promises-judge-failed: judge exploded"]
        assert any("judge exploded" in rec.getMessage() for rec in caplog.records)
        # The reservation is not left dangling.
        assert promise_env["settle"].called or promise_env["release"].called

    def test_a_raising_transport_leaves_the_other_adapters_untouched_in_the_tick(self, promise_env):
        session = _session("sess-p10", outbound=["I promise."])

        def transport(prompt):
            raise RuntimeError("judge exploded")

        with (
            _adapters(real=("promises",), corrections=4),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
            patch("tools.improvement_eligibility.is_open_source", return_value=True),
            patch.object(improvement_collect, "_openrouter_judge", return_value=transport),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"]["corrections"] == 4
        assert result["counts"]["promises"] == 0
        assert any("promises-judge-failed" in f for f in result["findings"])
        assert result["failed"] == []

    def test_the_meter_is_reserved_under_the_promise_detector_purpose_and_settled(
        self, promise_env
    ):
        session = _session("sess-p11", outbound=["I promise."])
        transport = _transport([_no()])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[session]):
            improvement_collect.collect_promises(PK, transport=transport)

        promise_env["reserve"].assert_called_once()
        _, kwargs = promise_env["reserve"].call_args
        assert kwargs["purpose"] == "promise_detector"
        promise_env["settle"].assert_called_once()
        assert promise_env["settle"].call_args.kwargs["metering"] == "unknown"

    def test_a_meter_refusal_writes_nothing_and_records_a_skip(self, promise_env):
        from tools import paid_inference_meter as meter

        session = _session("sess-p12", outbound=["I promise."])
        transport = _transport([_yes()])
        skipped: list[str] = []
        with (
            patch.object(meter, "reserve", return_value=meter.Refusal(meter.REFUSAL_EXHAUSTED)),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
        ):
            written = improvement_collect.collect_promises(PK, transport=transport, skipped=skipped)

        assert written == 0
        assert skipped == ["promises-skipped: unit 2 unavailable"]
        assert transport.prompts == []

    @pytest.mark.parametrize("enabled, detector", [(False, True), (True, False)])
    def test_either_gate_off_is_a_skip_never_a_failure(self, enabled, detector):
        session = _session("sess-p13", outbound=["I promise."])
        transport = _transport([_yes()])
        skipped: list[str] = []
        with (
            _improvement_enabled(enabled),
            _promise_detector_enabled(detector),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
        ):
            written = improvement_collect.collect_promises(PK, transport=transport, skipped=skipped)

        assert written == 0
        assert len(skipped) == 1 and skipped[0].startswith("promises-skipped")
        assert transport.prompts == []

    def test_the_default_transport_is_refused_on_a_client_project(self, promise_env):
        """Charter §7: the judge leaves the machine, so eligibility is checked at the call site."""
        session = _session("sess-p14", outbound=["I promise."])
        skipped: list[str] = []
        with (
            patch("tools.improvement_eligibility.is_open_source", return_value=False),
            patch.object(improvement_collect, "_recent_sessions", return_value=[session]),
            patch.object(improvement_collect, "_openrouter_judge") as judge,
        ):
            written = improvement_collect.collect_promises(PK, skipped=skipped)

        assert written == 0
        assert skipped == ["promises-skipped: project is not open source (charter §7)"]
        judge.assert_not_called()
        promise_env["reserve"].assert_not_called()

    def test_the_default_model_falls_back_to_the_free_gemma(self, promise_env):
        from config.models import OPENROUTER_GEMMA4_FREE

        with _promise_detector_enabled(True, model=""):
            assert improvement_collect._judge_model() == OPENROUTER_GEMMA4_FREE
        with _promise_detector_enabled(True, model="meta/muse-spark-1.3"):
            assert improvement_collect._judge_model() == "meta/muse-spark-1.3"

    def test_dedup_is_per_session_and_entry_hash(self, promise_env):
        """The same words in two sessions are two observations; a repeat in one is one."""
        a = _session("sess-p15a", outbound=["I promise.", "I promise."])
        b = _session("sess-p15b", outbound=["I promise."])
        transport = _transport([_yes(), _yes(), _yes()])
        with patch.object(improvement_collect, "_recent_sessions", return_value=[a, b]):
            written = improvement_collect.collect_promises(PK, transport=transport)

        assert written == 2
        assert len(transport.prompts) == 2
