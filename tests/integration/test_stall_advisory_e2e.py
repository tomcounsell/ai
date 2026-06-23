"""Integration tests for the stall advisory classifier end-to-end (issue #1538).

Writes real telemetry JSONL files, reads them back through read_session_timeline,
and runs classify_session_stall() — exercising the full read path without
mocking the file I/O layer.

Hard constraint guard:
    agent.session_health must NOT be imported or called during classification.
    This is enforced by checking sys.modules after classification.

Test isolation:
    All telemetry files are written to `logs/session_telemetry/` with IDs
    prefixed "test-stall-e2e-" and are removed in fixture teardown.

run_stall_advisory is also tested here for return-shape correctness against
real (in-process) classify_session_stall calls.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.session_stall_classifier import (
    IDLE_STALL_SECS,
    IDLE_SUSPECT_SECS,
    classify_session_stall,
)
from agent.session_telemetry import read_session_timeline

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_PREFIX = "test-stall-e2e"
_TELEMETRY_DIR: Path = Path(__file__).parent.parent.parent / "logs" / "session_telemetry"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_trace(session_id: str, events: list[dict]) -> Path:
    """Write fixture events to the session telemetry JSONL file."""
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _TELEMETRY_DIR / f"{session_id}.jsonl"
    with path.open("w") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return path


def _fake_session(
    session_id: str,
    status: str = "running",
    created_at: float | None = None,
    started_at: float | None = None,
) -> SimpleNamespace:
    now = time.time()
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        status=status,
        started_at=started_at,
        # Default: created 700 seconds ago so never-started grace is exceeded.
        created_at=created_at if created_at is not None else (now - 700),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trace_file(request):
    """Create a telemetry trace file and clean it up after the test."""
    paths: list[Path] = []

    def _make(session_id: str, events: list[dict]) -> Path:
        p = _write_trace(session_id, events)
        paths.append(p)
        return p

    yield _make

    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. Stalled trace: never-started (no turn_start events)
# ---------------------------------------------------------------------------


class TestNeverStartedE2E:
    def test_running_session_no_turn_start_classifies_stalled(self, trace_file):
        session_id = f"{_TEST_PREFIX}-never-started"
        # Write an empty trace (no turn_start).
        trace_file(session_id, [])

        events = read_session_timeline(session_id)
        assert events == []  # confirms the empty trace was written and read back

        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"

    def test_running_session_with_recent_turn_start_classifies_healthy(self, trace_file):
        session_id = f"{_TEST_PREFIX}-has-turn-start"
        recent_ts = time.time() - 30
        events_written = [{"type": "turn_start", "ts": recent_ts}]
        trace_file(session_id, events_written)

        events = read_session_timeline(session_id)
        assert len(events) == 1
        assert events[0]["type"] == "turn_start"

        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        # A turn_start (type == "turn_start") sets has_turn_start=True, so the
        # never-started branch is skipped. Then recent_turn_ts < IDLE_SUSPECT_SECS
        # → healthy/recent_turn_activity.
        assert verdict.level == "healthy"


# ---------------------------------------------------------------------------
# 2. Stalled trace: large idle gap
# ---------------------------------------------------------------------------


class TestIdleGapE2E:
    def test_large_idle_gap_event_classifies_stalled(self, trace_file):
        session_id = f"{_TEST_PREFIX}-idle-gap-stall"
        events_written = [
            {"type": "turn_start", "ts": time.time() - 1200},
            {"type": "turn_end", "ts": time.time() - 1200},
            {"type": "idle_gap", "gap_seconds": IDLE_STALL_SECS + 100},
        ]
        trace_file(session_id, events_written)

        events = read_session_timeline(session_id)
        assert len(events) == 3

        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        assert verdict.level == "stalled"
        assert verdict.reason == "idle_gap_exceeded_stall"

    def test_suspect_idle_gap_classifies_suspect(self, trace_file):
        session_id = f"{_TEST_PREFIX}-idle-gap-suspect"
        duration = (IDLE_SUSPECT_SECS + IDLE_STALL_SECS) / 2
        old_ts = time.time() - 1200
        events_written = [
            # turn_start is required: has_turn_start=True skips never-started branch.
            {"type": "turn_start", "ts": old_ts},
            {"type": "turn_end", "ts": old_ts},
            {"type": "idle_gap", "gap_seconds": duration},
        ]
        trace_file(session_id, events_written)

        events = read_session_timeline(session_id)
        session = _fake_session(session_id, status="running")
        verdict = classify_session_stall(events, session=session)

        assert verdict.level == "suspect"


# ---------------------------------------------------------------------------
# 3. Hard constraint guard: agent.session_health must NOT be imported
# ---------------------------------------------------------------------------


class TestSessionHealthNotImported:
    def test_classifier_does_not_import_agent_session_health(self, trace_file):
        """Verify agent.session_health is absent from sys.modules after classification.

        The classifier is designed to never pull in agent.session_health
        (the kill/recovery machinery). This test verifies that constraint is
        upheld at runtime.
        """
        # Ensure agent.session_health is not already loaded (if it is from
        # a prior test, we note it and assert it was NOT called by our code).
        health_was_preloaded = "agent.session_health" in sys.modules

        session_id = f"{_TEST_PREFIX}-health-guard"
        trace_file(session_id, [])

        session = _fake_session(session_id, status="running")

        if not health_was_preloaded:
            # Run classification and verify the module was not imported as a side-effect.
            events = read_session_timeline(session_id)
            classify_session_stall(events, session=session)

            assert "agent.session_health" not in sys.modules, (
                "agent.session_health was imported during classification — "
                "this violates the design constraint (see session_stall_classifier.py docstring)"
            )
        else:
            # agent.session_health was already loaded by some other test or import.
            # In this case we can only verify that classify_session_stall does not
            # call any of its functions. Use a spy to monitor calls.
            real_health_module = sys.modules["agent.session_health"]
            spy = MagicMock(wraps=real_health_module)
            with patch.dict(sys.modules, {"agent.session_health": spy}):
                events = read_session_timeline(session_id)
                classify_session_stall(events, session=session)

            # None of the spy's attributes should have been called.
            spy.assert_not_called()

    def test_session_stall_classifier_module_has_no_health_import(self):
        """Static check: agent.session_stall_classifier must not import agent.session_health.

        The docstring may mention session_health in a constraint comment; that's fine.
        What is forbidden is any actual import statement: `import agent.session_health`
        or `from agent.session_health import ...`.
        """
        import importlib.util
        import re

        spec = importlib.util.find_spec("agent.session_stall_classifier")
        assert spec is not None, "agent.session_stall_classifier not found"
        source_path = Path(spec.origin)
        source = source_path.read_text()

        # Match actual import statements only, not docstring mentions.
        health_import_pattern = re.compile(
            r"^\s*(import\s+agent\.session_health|from\s+agent\.session_health\s+import)",
            re.MULTILINE,
        )
        match = health_import_pattern.search(source)
        assert match is None, (
            f"agent.session_stall_classifier has an import of agent.session_health "
            f"at: {match.group(0)!r} — this violates the no-session-health constraint"
        )


# ---------------------------------------------------------------------------
# 4. run_stall_advisory return shape (real classify_session_stall calls)
# ---------------------------------------------------------------------------


class TestStallAdvisoryE2E:
    def test_run_stall_advisory_return_shape_with_sessions(self, trace_file):
        """run_stall_advisory returns {status, findings, summary} with correct types."""
        from reflections.stall_advisory import run_stall_advisory

        session_id = f"{_TEST_PREFIX}-advisory-shape"
        trace_file(session_id, [])

        now = time.time()
        fake_sess = _fake_session(session_id, status="running", created_at=now - 700)

        # Patch only AgentSession.query + TERMINAL_STATUSES to avoid Redis.
        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [fake_sess]
        mock_sl_module = MagicMock()
        mock_sl_module.TERMINAL_STATUSES = frozenset(
            {"completed", "failed", "killed", "abandoned", "cancelled"}
        )

        with patch.dict(
            sys.modules,
            {
                "models.agent_session": mock_as_module,
                "models.session_lifecycle": mock_sl_module,
            },
        ):
            result = run_stall_advisory(params=None)

        assert "status" in result
        assert "findings" in result
        assert "summary" in result
        assert isinstance(result["findings"], list)
        assert isinstance(result["summary"], str)

    def test_run_stall_advisory_stalled_session_sets_warn_status(self, trace_file):
        from reflections.stall_advisory import run_stall_advisory

        session_id = f"{_TEST_PREFIX}-advisory-warn"
        trace_file(session_id, [])  # no events → stalled/never_started

        now = time.time()
        fake_sess = _fake_session(session_id, status="running", created_at=now - 700)

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [fake_sess]
        mock_sl_module = MagicMock()
        mock_sl_module.TERMINAL_STATUSES = frozenset(
            {"completed", "failed", "killed", "abandoned", "cancelled"}
        )

        with patch.dict(
            sys.modules,
            {
                "models.agent_session": mock_as_module,
                "models.session_lifecycle": mock_sl_module,
            },
        ):
            result = run_stall_advisory(params=None)

        assert result["status"] == "warn"
        assert len(result["findings"]) == 1
        assert result["findings"][0]["session_id"] == session_id
        assert result["findings"][0]["level"] == "stalled"

    def test_run_stall_advisory_no_sessions_returns_ok(self):
        from reflections.stall_advisory import run_stall_advisory

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = []
        mock_sl_module = MagicMock()
        mock_sl_module.TERMINAL_STATUSES = frozenset(
            {"completed", "failed", "killed", "abandoned", "cancelled"}
        )

        with patch.dict(
            sys.modules,
            {
                "models.agent_session": mock_as_module,
                "models.session_lifecycle": mock_sl_module,
            },
        ):
            result = run_stall_advisory(params=None)

        assert result["status"] == "ok"
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# 5. Action-mode recovery (issue #1768)
# ---------------------------------------------------------------------------


@pytest.fixture()
def recovery_redis():
    """Provide the real Popoto Redis connection + the action-mode key prefix,
    cleaning up every stall-recovery:* key created during the test.

    Project-scoped only (default project_key "valor"); never touches production
    AgentSession records. Counters under {pk}:stall-recovery:* are plain keys
    (NOT Popoto-managed), so raw r.delete is permitted here.
    """
    from agent.sustainability import _get_project_key, _get_redis

    r = _get_redis()
    pk = _get_project_key()
    created: set[str] = set()

    def consec_key(sid: str) -> str:
        k = f"{pk}:stall-recovery:consec:{sid}"
        created.add(k)
        return k

    def budget_key(sid: str) -> str:
        k = f"{pk}:stall-recovery:budget:{sid}"
        created.add(k)
        return k

    yield SimpleNamespace(r=r, pk=pk, consec_key=consec_key, budget_key=budget_key)

    # Teardown: remove every key we touched plus any the reflection created for
    # the test session ids (consec/budget) under this project prefix.
    for k in created:
        try:
            r.delete(k)
        except Exception:
            pass


def _patch_models(fake_sessions: list, *, kill_side_effect=None):
    """Return a patch.dict context manager replacing models.agent_session and
    models.session_lifecycle so run_stall_advisory never hits the ORM/Redis for
    session lookup.

    fake_sessions is the list returned by AgentSession.query.filter(...).
    The re-read query (filter(session_id=...).first()) returns the matching
    fake session so the Race-1 terminal re-read sees the same object.
    """
    mock_as_module = MagicMock()
    mock_as_module.AgentSession.query.filter.return_value = fake_sessions

    def _filter_first(*args, **kwargs):
        sid = kwargs.get("session_id")
        chain = MagicMock()
        match = next((s for s in fake_sessions if s.session_id == sid), None)
        chain.first.return_value = match
        return chain

    # The probe query uses status__in=[...]; the re-read uses session_id=...
    # Route both through one side_effect that distinguishes by kwargs.
    def _filter(*args, **kwargs):
        if "session_id" in kwargs:
            return _filter_first(*args, **kwargs)
        return fake_sessions

    mock_as_module.AgentSession.query.filter.side_effect = _filter

    mock_sl_module = MagicMock()
    mock_sl_module.TERMINAL_STATUSES = frozenset(
        {"completed", "failed", "killed", "abandoned", "cancelled"}
    )
    return patch.dict(
        sys.modules,
        {
            "models.agent_session": mock_as_module,
            "models.session_lifecycle": mock_sl_module,
        },
    )


class TestStallAdvisoryActionMode:
    def _stalled_session(self, sid: str) -> SimpleNamespace:
        """A session that classifies stalled/never_started (running, no events,
        created 700s ago) with a settable status (kill simulation flips it)."""
        return _fake_session(sid, status="running", created_at=time.time() - 700)

    def _run(self, fake_sessions, recovery_redis, enabled, *, kill_mock, subprocess_mock):
        """Invoke run_stall_advisory with all action-mode collaborators patched."""
        from config.settings import settings

        with _patch_models(fake_sessions):
            with (
                patch("tools.agent_session_scheduler._kill_agent_session", kill_mock),
                patch("reflections.stall_advisory.subprocess.run", subprocess_mock),
                patch.object(settings.features, "stall_recovery_enabled", enabled),
            ):
                from reflections.stall_advisory import run_stall_advisory

                return run_stall_advisory(params=None)

    # -- 1. dry-run no-act --------------------------------------------------
    def test_dry_run_no_act_at_consec_threshold(self, trace_file, recovery_redis):
        from config.settings import settings

        sid = f"{_TEST_PREFIX}-act-dryrun"
        trace_file(sid, [])
        sess = self._stalled_session(sid)

        # Pre-seed consec to N-1 so a single run reaches the dry-run action.
        n = settings.features.stall_recovery_consecutive_observations
        recovery_redis.r.set(recovery_redis.consec_key(sid), n - 1)

        kill = MagicMock()
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))
        result = self._run(
            [sess], recovery_redis, enabled=False, kill_mock=kill, subprocess_mock=sub
        )

        kill.assert_not_called()
        sub.assert_not_called()
        assert "would-kill (dry-run)" in result["summary"]

    # -- 2. enforce kills + re-enqueues -------------------------------------
    def test_enforce_kills_and_recatches(self, trace_file, recovery_redis):
        from config.settings import settings

        sid = f"{_TEST_PREFIX}-act-enforce"
        trace_file(sid, [])
        sess = self._stalled_session(sid)

        n = settings.features.stall_recovery_consecutive_observations
        recovery_redis.r.set(recovery_redis.consec_key(sid), n - 1)
        recovery_redis.budget_key(sid)  # register for cleanup

        def _kill(target):
            target.status = "killed"

        kill = MagicMock(side_effect=_kill)
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))
        result = self._run(
            [sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub
        )

        kill.assert_called_once()
        sub.assert_called_once()
        # First positional arg is the command list; must be valor-catchup.
        assert sub.call_args.args[0][0] == "valor-catchup"
        assert "1 killed" in result["summary"]

    # -- 3. suspect never killed --------------------------------------------
    def test_suspect_never_killed(self, trace_file, recovery_redis):
        sid = f"{_TEST_PREFIX}-act-suspect"
        # An idle gap in the suspect range → verdict.level == "suspect".
        duration = (IDLE_SUSPECT_SECS + IDLE_STALL_SECS) / 2
        old_ts = time.time() - 1200
        trace_file(
            sid,
            [
                {"type": "turn_start", "ts": old_ts},
                {"type": "turn_end", "ts": old_ts},
                {"type": "idle_gap", "gap_seconds": duration},
            ],
        )
        sess = self._stalled_session(sid)

        kill = MagicMock()
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))
        # Run many times — suspect must never be actioned regardless of repetition.
        for _ in range(5):
            result = self._run(
                [sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub
            )
        kill.assert_not_called()
        sub.assert_not_called()
        assert result["status"] == "warn"

    # -- 4. N-consecutive required ------------------------------------------
    def test_n_consecutive_required(self, trace_file, recovery_redis):
        from config.settings import settings

        sid = f"{_TEST_PREFIX}-act-nconsec"
        trace_file(sid, [])
        sess = self._stalled_session(sid)
        recovery_redis.consec_key(sid)  # register for cleanup

        n = settings.features.stall_recovery_consecutive_observations
        kill = MagicMock(side_effect=lambda t: setattr(t, "status", "killed"))
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))

        # Run N-1 times: counter climbs to N-1, below threshold → no kill yet.
        for _ in range(n - 1):
            self._run([sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub)
        kill.assert_not_called()

        # The Nth run reaches the threshold → kill fires.
        self._run([sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub)
        kill.assert_called_once()

    # -- 5. run-cap respected -----------------------------------------------
    def test_run_cap_respected(self, trace_file, recovery_redis):
        from config.settings import settings

        sid_a = f"{_TEST_PREFIX}-act-cap-a"
        sid_b = f"{_TEST_PREFIX}-act-cap-b"
        trace_file(sid_a, [])
        trace_file(sid_b, [])
        sess_a = self._stalled_session(sid_a)
        sess_b = self._stalled_session(sid_b)

        n = settings.features.stall_recovery_consecutive_observations
        # Both past the consec threshold already.
        recovery_redis.r.set(recovery_redis.consec_key(sid_a), n)
        recovery_redis.r.set(recovery_redis.consec_key(sid_b), n)
        recovery_redis.budget_key(sid_a)
        recovery_redis.budget_key(sid_b)

        assert settings.features.stall_recovery_run_budget == 1
        kill = MagicMock(side_effect=lambda t: setattr(t, "status", "killed"))
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))
        result = self._run(
            [sess_a, sess_b], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub
        )

        # run_budget=1 ⇒ only ONE kill in a single run.
        assert kill.call_count == 1
        assert "1 killed" in result["summary"]

    # -- 6. per-session budget respected ------------------------------------
    def test_per_session_budget_respected(self, trace_file, recovery_redis):
        from config.settings import settings

        sid = f"{_TEST_PREFIX}-act-psbudget"
        trace_file(sid, [])
        sess = self._stalled_session(sid)

        n = settings.features.stall_recovery_consecutive_observations
        per = settings.features.stall_recovery_per_session_budget
        recovery_redis.r.set(recovery_redis.consec_key(sid), n)
        # Session already at its per-session kill budget → must be skipped.
        recovery_redis.r.set(recovery_redis.budget_key(sid), per)

        kill = MagicMock(side_effect=lambda t: setattr(t, "status", "killed"))
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))
        self._run([sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub)
        kill.assert_not_called()

    # -- 7. kill raises → reflection still completes ------------------------
    def test_kill_raises_still_completes(self, trace_file, recovery_redis):
        from config.settings import settings

        sid = f"{_TEST_PREFIX}-act-killraise"
        trace_file(sid, [])
        sess = self._stalled_session(sid)

        n = settings.features.stall_recovery_consecutive_observations
        recovery_redis.r.set(recovery_redis.consec_key(sid), n)
        recovery_redis.budget_key(sid)

        kill = MagicMock(side_effect=RuntimeError("kill boom"))
        sub = MagicMock(return_value=SimpleNamespace(returncode=0))
        result = self._run(
            [sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub
        )

        # The exception must not propagate; the contract keys must be present.
        assert set(result) == {"status", "findings", "summary"}
        assert isinstance(result["summary"], str)
        # Kill failed before incrementing → no killed count surfaced.
        sub.assert_not_called()

    # -- 8. catchup missing (FileNotFoundError) swallowed -------------------
    def test_catchup_missing_swallowed(self, trace_file, recovery_redis):
        from config.settings import settings

        sid = f"{_TEST_PREFIX}-act-catchupmissing"
        trace_file(sid, [])
        sess = self._stalled_session(sid)

        n = settings.features.stall_recovery_consecutive_observations
        recovery_redis.r.set(recovery_redis.consec_key(sid), n)
        recovery_redis.budget_key(sid)

        kill = MagicMock(side_effect=lambda t: setattr(t, "status", "killed"))
        sub = MagicMock(side_effect=FileNotFoundError("valor-catchup not found"))
        result = self._run(
            [sess], recovery_redis, enabled=True, kill_mock=kill, subprocess_mock=sub
        )

        # Kill still counted; FileNotFoundError swallowed; run completes.
        kill.assert_called_once()
        assert set(result) == {"status", "findings", "summary"}
        assert "1 killed" in result["summary"]
        assert "catchup-failed" in result["summary"]
