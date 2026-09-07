"""Tests for the improvement evidence writers (#3177).

The plan's user-visible criterion for lane 2 is that the correction detector
runs against real sessions *because something ticks it*. These tests cover both
halves: the durable row (``ImprovementEvidence.record_once`` and its dedup), and
the three observer adapters the reflection tick calls.

They are deliberately unkind to the classifier. A regex cannot tell an
architectural rescue from a preference most of the time, and the honest answer
is ``unknown`` — a test that demanded confident labels would be pushing the
detector toward exactly the overclaiming the plan warns about.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py); production Redis
is never touched, and every row is written under a test-scoped ``project_key``.
"""

from __future__ import annotations

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
            patch("config.memory_defaults.DEFAULT_PROJECT_KEY", PK),
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
            patch("config.memory_defaults.DEFAULT_PROJECT_KEY", PK),
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


class TestRunImprovementCollect:
    def test_returns_a_reflection_result_dict(self):
        with (
            _improvement_enabled(True),
            patch.object(improvement_collect, "human_memories", return_value=[]),
            patch.object(improvement_collect, "collect_corrections", return_value=2),
            patch.object(improvement_collect, "collect_inspirations", return_value=1),
            patch.object(improvement_collect, "collect_expectation_coverage", return_value=1),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"] == {
            "corrections": 2,
            "inspirations": 1,
            "expectation_coverage": 1,
        }
        assert "4 new evidence row" in result["summary"]
        assert result["findings"] == []
        assert isinstance(result["duration"], float)

    def test_the_memory_partition_is_enumerated_once_per_tick(self):
        """Both adapters read the same partition; two fetches would double the cost."""
        with (
            _improvement_enabled(True),
            patch.object(improvement_collect, "human_memories", return_value=[]) as fetch,
            patch.object(improvement_collect, "collect_expectation_coverage", return_value=0),
            patch.object(improvement_collect, "_recent_sessions", return_value=[]),
        ):
            improvement_collect.run_improvement_collect()
        assert fetch.call_count == 1

    def test_one_broken_adapter_degrades_the_tick_rather_than_ending_it(self):
        with (
            _improvement_enabled(True),
            patch.object(improvement_collect, "human_memories", return_value=[]),
            patch.object(
                improvement_collect, "collect_corrections", side_effect=RuntimeError("boom")
            ),
            patch.object(improvement_collect, "collect_inspirations", return_value=3),
            patch.object(improvement_collect, "collect_expectation_coverage", return_value=0),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "success"
        assert result["counts"]["corrections"] == 0
        assert result["counts"]["inspirations"] == 3
        assert any("corrections-failed" in f for f in result["findings"])

    def test_all_three_failing_is_an_error(self):
        with (
            _improvement_enabled(True),
            patch.object(improvement_collect, "human_memories", return_value=[]),
            patch.object(improvement_collect, "collect_corrections", side_effect=RuntimeError("a")),
            patch.object(
                improvement_collect, "collect_inspirations", side_effect=RuntimeError("b")
            ),
            patch.object(
                improvement_collect, "collect_expectation_coverage", side_effect=RuntimeError("c")
            ),
        ):
            result = improvement_collect.run_improvement_collect()

        assert result["status"] == "error"
        assert len(result["findings"]) == 3


class TestVocabulariesStayInSync:
    def test_adapters_only_emit_declared_kinds(self):
        for kind in ("correction", "inspiration", "shipped_work", "owner_liveness"):
            assert kind in EVIDENCE_KINDS
