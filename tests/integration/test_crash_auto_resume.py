"""Integration tests for crash-signature auto-resume reflection (issue #1539).

Verifies end-to-end behavior of run_crash_recovery():
1. Propose mode: signature extracted, session not resumed
2. Determinism guardrail: plateau/no-turn-start sessions blocked
3. Outcome attribution: single-credit idempotency
4. Auto-resume fires on eligible session (CRASH_AUTORESUME_ENABLED=1)

Test isolation: project_key="test-crash-auto-resume" namespaces all sessions
and signatures. Popoto cleanup via .delete() after each test.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession
from models.crash_signature import CrashSignature

pytestmark = pytest.mark.integration

# Project key used to namespace all test sessions and signatures.
_TEST_PROJECT = "test-crash-auto-resume"

# Telemetry directory (resolved from agent.session_telemetry at import-time to
# avoid re-importing inside helpers — any import failure is a test failure).
_TELEMETRY_DIR: Path = Path(__file__).parent.parent.parent / "logs" / "session_telemetry"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id: str,
    status: str,
    *,
    claude_session_uuid: str | None = None,
    crash_signature: str | None = None,
    crash_outcome_attributed: object | None = None,
) -> AgentSession:
    """Create a minimal AgentSession for testing."""
    session = AgentSession()
    session.session_id = session_id
    session.status = status
    session.project_key = _TEST_PROJECT
    if claude_session_uuid is not None:
        session.claude_session_uuid = claude_session_uuid
    if crash_signature is not None:
        session.crash_signature = crash_signature
    if crash_outcome_attributed is not None:
        session.crash_outcome_attributed = crash_outcome_attributed
    session.save()
    return session


def _write_telemetry(
    session_id: str,
    events: list[dict],
) -> Path:
    """Write fixture telemetry events to the session telemetry JSONL file.

    Returns the path to the written file.
    """
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _TELEMETRY_DIR / f"{session_id}.jsonl"
    with path.open("w") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return path


# #2147 service-isolation audit: this file uses dict fixtures only. The
# ``"signal_sent": "SIGTERM"`` entries below are inert strings inside trace
# fixtures parsed by the resume-policy logic — there is NO real ``os.kill`` /
# ``proc.terminate()`` anywhere in this module, so no path can target the
# launchd live worker and no assert_not_live_worker guard is required.
def _standard_trace(*, to_status: str = "failed") -> list[dict]:
    """Build a standard fixture trace: turn_start + idle_gap[medium] + status_transition."""
    return [
        {"type": "turn_start", "timestamp": 1717000000.0},
        {"type": "idle_gap", "gap_seconds": 400.0, "timestamp": 1717000600.0},
        {
            "type": "status_transition",
            "from": "running",
            "to": to_status,
            "timestamp": 1717001000.0,
            "kill": {"confirmed_dead": False, "signal_sent": "SIGTERM"},
        },
    ]


def _no_turn_start_trace(*, to_status: str = "failed") -> list[dict]:
    """Build a trace with NO turn_start — triggers NON_RESUMABLE_DETERMINISTIC guardrail."""
    return [
        {
            "type": "status_transition",
            "from": "running",
            "to": to_status,
            "timestamp": 1717001000.0,
            "kill": {"confirmed_dead": False, "signal_sent": "SIGTERM"},
        },
    ]


def _cleanup_sessions(*sessions: AgentSession) -> None:
    """Delete test sessions via Popoto ORM (never raw Redis)."""
    for s in sessions:
        try:
            s.delete()
        except Exception:
            pass


def _cleanup_signatures(*hashes: str) -> None:
    """Delete CrashSignature records by hash via Popoto ORM."""
    for h in hashes:
        record = CrashSignature.get_by_hash(h)
        if record is not None:
            try:
                record.delete()
            except Exception:
                pass


def _cleanup_telemetry(session_id: str) -> None:
    """Remove the telemetry JSONL file for a session."""
    path = _TELEMETRY_DIR / f"{session_id}.jsonl"
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


@contextlib.contextmanager
def _patch_settings(**overrides):
    """Patch config.settings.settings.features attributes for the test duration.

    run_crash_recovery() reads the enable flag and thresholds from the pydantic
    settings singleton (config.settings.settings.features) at run time — that
    singleton is instantiated at import, so tests cannot set env after import
    and must patch the object directly. This drives the REAL production config
    path, not a bypass. The lookback window is still env-read (set via
    patch.dict on os.environ alongside this).
    """
    from config.settings import settings

    with contextlib.ExitStack() as stack:
        for attr, value in overrides.items():
            stack.enter_context(patch.object(settings.features, attr, value))
        yield


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCrashAutoResume:
    """End-to-end tests for the crash-signature auto-resume reflection."""

    # ------------------------------------------------------------------
    # Test 1: propose mode — signature extracted, session NOT resumed
    # ------------------------------------------------------------------

    def test_propose_mode_no_resume(self, redis_test_db):
        """In propose mode (CRASH_AUTORESUME_ENABLED=0), the reflection extracts a
        signature and upserts it to the CrashSignature library, but does NOT
        transition the session from 'failed' to 'pending'."""
        session_id = "test-car-sess-propose"
        session = _make_session(
            session_id,
            "failed",
            claude_session_uuid="test-uuid-propose",
        )
        _write_telemetry(session_id, _standard_trace())

        try:
            with (
                _patch_settings(crash_autoresume_enabled=False),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"

            # Signature was extracted and upserted to the library
            extracted_finding = next(
                (
                    f
                    for f in result["findings"]
                    if f"session={session_id}" in f and "extracted:" in f
                ),
                None,
            )
            assert extracted_finding is not None, (
                f"Expected 'extracted:' finding for session {session_id!r}. "
                f"Findings: {result['findings']}"
            )

            # A CrashSignature record was upserted
            sigs_for_project = CrashSignature.all_for_project(_TEST_PROJECT)
            assert len(sigs_for_project) >= 1, "Expected at least one CrashSignature record"

            # Session is still 'failed' — not transitioned to pending
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions, f"Session {session_id!r} not found after reflection"
            assert sessions[0].status == "failed", (
                f"Expected session to stay 'failed' in propose mode, got {sessions[0].status!r}"
            )

            # Findings include a 'proposed:' entry (propose-mode log)
            proposed_finding = next(
                (
                    f
                    for f in result["findings"]
                    if f"session={session_id}" in f and "proposed:" in f
                ),
                None,
            )
            assert proposed_finding is not None, (
                f"Expected 'proposed:' finding for session {session_id!r}. "
                f"Findings: {result['findings']}"
            )

        finally:
            # Determine signature hash for cleanup
            sigs = CrashSignature.all_for_project(_TEST_PROJECT)
            sig_hashes = [s.signature_hash for s in sigs]
            _cleanup_sessions(session)
            _cleanup_signatures(*sig_hashes)
            _cleanup_telemetry(session_id)

    # ------------------------------------------------------------------
    # Test 2: determinism guardrail — plateau or no-turn-start sessions blocked
    # ------------------------------------------------------------------

    def test_determinism_guardrail_plateau(self, redis_test_db, caplog):
        """A session whose telemetry has no turn_start event and no other
        demonstrable progress must be classified as NON_RESUMABLE_DETERMINISTIC
        and NEVER transitioned to 'pending', even with
        CRASH_AUTORESUME_ENABLED=1. The reflection must emit [ESCALATE] log."""
        session_id = "test-car-sess-plateau"
        session = _make_session(
            session_id,
            "failed",
            claude_session_uuid="test-uuid-plateau",
        )
        # Trace with NO turn_start events (plateau sessions never start a turn).
        _write_telemetry(session_id, _no_turn_start_trace())

        try:
            with (
                _patch_settings(crash_autoresume_enabled=True),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
                caplog.at_level("WARNING", logger="reflections.crash_recovery"),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"

            # Session must still be 'failed'
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions, f"Session {session_id!r} not found after reflection"
            assert sessions[0].status == "failed", (
                f"Determinism guardrail failed: session transitioned to "
                f"{sessions[0].status!r}, expected 'failed'"
            )

            # CrashSignature record should be marked non-resumable
            sigs = CrashSignature.all_for_project(_TEST_PROJECT)
            if sigs:
                plateau_sig = next(
                    (s for s in sigs if s.signature_hash is not None),
                    None,
                )
                if plateau_sig is not None:
                    assert not plateau_sig.is_resumable, (
                        "Expected CrashSignature.resumable=False for plateau session"
                    )

            # [ESCALATE] must appear in the WARNING log for deterministic failures
            escalate_msgs = [r.message for r in caplog.records if "[ESCALATE]" in r.message]
            assert escalate_msgs, (
                f"Expected at least one [ESCALATE] log entry. "
                f"Captured records: {[r.message for r in caplog.records]}"
            )

            # Findings include 'escalated:' entry
            escalated_finding = next(
                (f for f in result["findings"] if "escalated:" in f),
                None,
            )
            assert escalated_finding is not None, (
                f"Expected 'escalated:' finding. Findings: {result['findings']}"
            )

        finally:
            sigs = CrashSignature.all_for_project(_TEST_PROJECT)
            sig_hashes = [s.signature_hash for s in sigs]
            _cleanup_sessions(session)
            _cleanup_signatures(*sig_hashes)
            _cleanup_telemetry(session_id)

    def test_determinism_guardrail_no_turn_start(self, redis_test_db):
        """A session whose telemetry trace has NO turn_start events must be classified
        as NON_RESUMABLE_DETERMINISTIC and NOT auto-resumed."""
        session_id = "test-car-sess-noturn"
        session = _make_session(
            session_id,
            "failed",
            claude_session_uuid="test-uuid-noturn",
        )
        # No turn_start in the trace
        _write_telemetry(session_id, _no_turn_start_trace())

        try:
            with (
                _patch_settings(crash_autoresume_enabled=True),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok"

            # Session must still be 'failed'
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions
            assert sessions[0].status == "failed", f"Expected 'failed', got {sessions[0].status!r}"

        finally:
            sigs = CrashSignature.all_for_project(_TEST_PROJECT)
            sig_hashes = [s.signature_hash for s in sigs]
            _cleanup_sessions(session)
            _cleanup_signatures(*sig_hashes)
            _cleanup_telemetry(session_id)

    # ------------------------------------------------------------------
    # Test 3: outcome attribution single-credit idempotency
    # ------------------------------------------------------------------

    def test_outcome_attribution_single_credit(self, redis_test_db):
        """Running run_crash_recovery() twice on a session that was already resumed
        (crash_signature set, crash_outcome_attributed=False) and has now completed
        must record exactly one 'recovered' outcome — never double-count."""
        from agent.crash_signature import extract_signature

        # Derive the signature hash for the standard trace so we can pre-create
        # the CrashSignature record that the 'resumed' session points to.
        trace = _standard_trace(to_status="completed")
        sig_key = extract_signature(trace)
        sig_hash = sig_key.hash

        # Pre-create the CrashSignature library record
        sig_record = CrashSignature.get_or_create_by_hash(
            sig_hash,
            human_form=sig_key.human_form,
            signature_class=sig_key.signature_class,
            resumable=sig_key.resumable,
        )
        sig_record.project_key = _TEST_PROJECT
        sig_record.save()

        # The 'resumed' session: has crash_signature set, attribution pending,
        # status='completed' (i.e., it recovered).
        resumed_id = "test-car-sess-resumed"
        resumed = _make_session(
            resumed_id,
            "completed",
            claude_session_uuid="test-uuid-resumed",
            crash_signature=sig_hash,
            crash_outcome_attributed=None,
        )

        try:
            with (
                _patch_settings(crash_autoresume_enabled=False),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result1 = run_crash_recovery()

            assert result1["status"] == "ok"

            # After first run: the CrashSignature should have 1 'recovered' outcome
            sig_after_first = CrashSignature.get_by_hash(sig_hash)
            assert sig_after_first is not None
            tallies_after_first = sig_after_first._load_tallies()
            strategy_bucket = tallies_after_first.get("auto_resume", {})
            assert strategy_bucket.get("recovered", 0) == 1, (
                f"Expected 1 'recovered' after first run. Tallies: {tallies_after_first}"
            )
            assert strategy_bucket.get("attempts", 0) == 1

            # After first run: crash_outcome_attributed should be truthy
            resumed_sessions = list(AgentSession.query.filter(session_id=resumed_id))
            assert resumed_sessions
            attributed = getattr(resumed_sessions[0], "crash_outcome_attributed", None)
            # Popoto stores bools as strings; treat any truthy form as attributed.
            attributed_str = str(attributed).strip().lower()
            assert attributed_str in {"true", "1", "yes"}, (
                f"Expected crash_outcome_attributed to be truthy, got {attributed!r}"
            )

            # Run again — must NOT double-count
            with (
                _patch_settings(crash_autoresume_enabled=False),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                result2 = run_crash_recovery()

            assert result2["status"] == "ok"

            sig_after_second = CrashSignature.get_by_hash(sig_hash)
            tallies_after_second = sig_after_second._load_tallies()
            strategy_bucket_2 = tallies_after_second.get("auto_resume", {})
            assert strategy_bucket_2.get("recovered", 0) == 1, (
                f"Expected still 1 'recovered' after second run (no double-count). "
                f"Tallies: {tallies_after_second}"
            )

        finally:
            _cleanup_sessions(resumed)
            _cleanup_signatures(sig_hash)

    # ------------------------------------------------------------------
    # Test 3b: confidence gate — below-threshold signature NOT auto-resumed
    # ------------------------------------------------------------------

    def test_below_confidence_not_auto_resumed(self, redis_test_db):
        """Demotion negative (demotion-gate model): with auto-resume enabled and
        a signature that HAS recorded failing attempts whose success ratio is
        below MIN_SUCCESS_RATIO, the reflection must NOT auto-resume the session.

        Under the demotion-gate model a zero-attempt signature is eligible
        (bootstrap), so the valid negative case is a signature that has demoted
        itself: occurrences >= MIN_OCCURRENCES AND attempts > 0 with
        ratio < MIN_SUCCESS_RATIO. Status stays terminal; a 'proposed:' finding
        appears; no auto-resume fires.

        This guards Blocker 1: the demotion gate (is_auto_eligible) must run
        before any resume. A resumable signature is necessary but NOT sufficient —
        once it accrues failing attempts it must demote out of eligibility.
        """
        from agent.crash_signature import extract_signature

        trace = _standard_trace(to_status="abandoned")
        sig_key = extract_signature(trace)
        sig_hash = sig_key.hash

        # Pre-warm the library so this signature has DEMOTED itself: enough
        # occurrences but a success ratio (1/3 ≈ 0.33) below the 0.7 threshold.
        sig_record = CrashSignature.get_or_create_by_hash(
            sig_hash,
            human_form=sig_key.human_form,
            signature_class=sig_key.signature_class,
            resumable=sig_key.resumable,
        )
        sig_record.project_key = _TEST_PROJECT
        sig_record.save()
        for i in range(3):
            sig_record.upsert_occurrence(
                f"warmup-below-{i}",
                terminal_status="abandoned",
                has_uuid=True,
                project_key=_TEST_PROJECT,
            )
        sig_record.record_outcome("auto_resume", recovered=True)
        sig_record.record_outcome("auto_resume", recovered=False)
        sig_record.record_outcome("auto_resume", recovered=False)

        session_id = "test-car-sess-below-conf"
        session = _make_session(
            session_id,
            "abandoned",
            claude_session_uuid="test-uuid-below-conf",
        )
        _write_telemetry(session_id, trace)

        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                ),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"

            # Session must still be 'abandoned' — the demotion gate blocked resume.
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions, f"Session {session_id!r} not found after reflection"
            assert sessions[0].status == "abandoned", (
                f"Confidence gate failed: session transitioned to {sessions[0].status!r}, "
                f"expected it to stay 'abandoned'. Findings: {result['findings']}"
            )

            # A 'proposed:' finding must appear (observed but not acted on).
            proposed_finding = next(
                (f for f in result["findings"] if "proposed:" in f and session_id in f),
                None,
            )
            assert proposed_finding is not None, (
                f"Expected a 'proposed:' finding for the below-confidence session. "
                f"Findings: {result['findings']}"
            )

            # The summary must report auto_resumed=0 (no resume fired).
            summary = result["summary"]
            assert "auto_resumed=0" in summary, (
                f"Expected auto_resumed=0 in summary, got: {summary!r}"
            )
            # And it must report at least one proposed.
            assert "proposed=0" not in summary, f"Expected proposed>0 in summary, got: {summary!r}"

        finally:
            sigs = CrashSignature.all_for_project(_TEST_PROJECT)
            sig_hashes = [s.signature_hash for s in sigs]
            _cleanup_sessions(session)
            _cleanup_signatures(*sig_hashes)
            _cleanup_telemetry(session_id)

    # ------------------------------------------------------------------
    # Test 4: auto-resume fires on an eligible session
    # ------------------------------------------------------------------

    def test_auto_resume_fires_on_eligible_session(self, redis_test_db):
        """With CRASH_AUTORESUME_ENABLED=1 and a pre-warmed CrashSignature library
        (occurrence_count >= MIN_OCCURRENCES, success_ratio >= MIN_SUCCESS_RATIO),
        the reflection transitions an eligible 'abandoned' session to 'pending' and
        stamps crash_signature on it."""
        from agent.crash_signature import extract_signature

        # Build the trace and derive signature hash
        trace = _standard_trace(to_status="abandoned")
        sig_key = extract_signature(trace)
        sig_hash = sig_key.hash

        # Pre-warm the library: enough occurrences + high success ratio
        sig_record = CrashSignature.get_or_create_by_hash(
            sig_hash,
            human_form=sig_key.human_form,
            signature_class=sig_key.signature_class,
            resumable=sig_key.resumable,
        )
        sig_record.project_key = _TEST_PROJECT
        sig_record.save()

        # Simulate 3 occurrences
        for i in range(3):
            sig_record.upsert_occurrence(
                f"warmup-session-{i}",
                terminal_status="abandoned",
                has_uuid=True,
                project_key=_TEST_PROJECT,
            )

        # Simulate 3 successful recoveries (ratio = 1.0 >= 0.7 threshold)
        for _ in range(3):
            sig_record.record_outcome("auto_resume", recovered=True)

        # The eligible session: abandoned, has claude_session_uuid
        session_id = "test-car-sess-eligible"
        _make_session(
            session_id,
            "abandoned",
            claude_session_uuid="test-uuid-eligible",
        )
        _write_telemetry(session_id, trace)

        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                ),
                # Machine-ownership gate (Gap 3b, added in this feature): the
                # reflection only resumes when THIS machine owns the session's
                # project. _TEST_PROJECT is not a real projects.json entry (and a
                # worktree may lack the gitignored projects.json symlink), so the
                # gate would otherwise fall to propose-only. Patch it True to
                # exercise the resume path this test asserts.
                patch(
                    "reflections.crash_recovery._machine_owns_project",
                    return_value=True,
                ),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"

            # Session should now be 'pending' (resumed)
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions, f"Session {session_id!r} not found after reflection"
            assert sessions[0].status == "pending", (
                f"Expected session to be 'pending' after auto-resume, "
                f"got {sessions[0].status!r}. Findings: {result['findings']}"
            )

            # crash_signature should be stamped on the session
            stored_sig = getattr(sessions[0], "crash_signature", None)
            assert stored_sig == sig_hash, (
                f"Expected crash_signature={sig_hash!r}, got {stored_sig!r}"
            )

        finally:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            _cleanup_sessions(*sessions)
            _cleanup_signatures(sig_hash)
            _cleanup_telemetry(session_id)

    # ------------------------------------------------------------------
    # Test 5: bootstrap — zero-attempt eligible signature IS auto-resumed
    # ------------------------------------------------------------------

    def test_bootstrap_auto_resume_zero_attempts(self, redis_test_db):
        """Cold-start proof (demotion-gate model): a resumable signature seen at
        least MIN_OCCURRENCES times with ZERO prior auto_resume attempts is
        auto-resumed when crash_autoresume_enabled=True via the REAL settings
        path. The session transitions terminal -> 'pending' with no human action.

        This is the case the previous promotion-gate deadlock made impossible
        (0 attempts -> ratio 0.0 -> never eligible -> never resumed). It proves
        the "zero-human-action auto-resume" Success Criterion is reachable
        through the documented FEATURES__CRASH_AUTORESUME_ENABLED config.
        """
        from agent.crash_signature import extract_signature

        trace = _standard_trace(to_status="abandoned")
        sig_key = extract_signature(trace)
        sig_hash = sig_key.hash

        # Pre-warm occurrences ONLY — no record_outcome, so zero attempts.
        sig_record = CrashSignature.get_or_create_by_hash(
            sig_hash,
            human_form=sig_key.human_form,
            signature_class=sig_key.signature_class,
            resumable=sig_key.resumable,
        )
        sig_record.project_key = _TEST_PROJECT
        sig_record.save()
        for i in range(3):
            sig_record.upsert_occurrence(
                f"bootstrap-warmup-{i}",
                terminal_status="abandoned",
                has_uuid=True,
                project_key=_TEST_PROJECT,
            )
        # Sanity: zero attempts recorded — this is the bootstrap precondition.
        assert sig_record.policy_confidence("auto_resume") == 0.0

        session_id = "test-car-sess-bootstrap"
        _make_session(
            session_id,
            "abandoned",
            claude_session_uuid="test-uuid-bootstrap",
        )
        _write_telemetry(session_id, trace)

        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                ),
                # Machine-ownership gate (Gap 3b): patch True so the bootstrap
                # resume path runs (see the eligible-session test above).
                patch(
                    "reflections.crash_recovery._machine_owns_project",
                    return_value=True,
                ),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"

            # Zero-human-action auto-resume: session transitioned to 'pending'.
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions, f"Session {session_id!r} not found after reflection"
            assert sessions[0].status == "pending", (
                f"Bootstrap auto-resume failed: expected 'pending', got "
                f"{sessions[0].status!r}. Findings: {result['findings']}"
            )

            # The run must report at least one auto-resume.
            assert "auto_resumed=0" not in result["summary"], (
                f"Expected auto_resumed>=1 in summary, got: {result['summary']!r}"
            )

        finally:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            _cleanup_sessions(*sessions)
            _cleanup_signatures(sig_hash)
            _cleanup_telemetry(session_id)


def _floor_trace(*, to_status: str = "failed") -> list[dict]:
    """Confirmed-dead clean-kill-to-`failed` trace: the known-transient tool-wedge
    shape the deterministic first-retry floor acts on (Gap 3a). turn_start is
    present so the classifier yields a resumable (non-deterministic) signature."""
    return [
        {"type": "turn_start", "timestamp": 1717000000.0},
        {
            "type": "status_transition",
            "from": "running",
            "to": to_status,
            "timestamp": 1717001000.0,
            "kill": {"confirmed_dead": True, "signal_sent": "SIGKILL"},
        },
    ]


@pytest.mark.integration
class TestDeterministicFloorAndConvergence:
    """End-to-end coverage for the deterministic first-retry floor (Gap 3a),
    its disable switch, the ownership gate on a floor-eligible session, and the
    failed-resume convergence guarantee (critique C1)."""

    def test_floor_resumes_cold_transient_signature(self, redis_test_db):
        """A confirmed-dead clean-kill-to-`failed` signature with ZERO statistical
        warm-up is auto-resumed by the deterministic floor: the cold library still
        self-heals the exact current failure mode."""
        from agent.crash_signature import extract_signature

        trace = _floor_trace()
        sig_hash = extract_signature(trace).hash
        session_id = "test-car-floor-resume"
        _make_session(session_id, "failed", claude_session_uuid="uuid-floor")
        _write_telemetry(session_id, trace)
        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                    crash_autoresume_deterministic_floor_attempts=1,
                ),
                patch(
                    "reflections.crash_recovery._machine_owns_project",
                    return_value=True,
                ),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions, f"Session {session_id!r} not found"
            assert sessions[0].status == "pending", (
                f"Floor resume failed: expected 'pending', got {sessions[0].status!r}. "
                f"Findings: {result['findings']}"
            )
            assert "auto_resumed=0" not in result["summary"], result["summary"]
        finally:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            _cleanup_sessions(*sessions)
            _cleanup_signatures(sig_hash)
            _cleanup_telemetry(session_id)

    def test_floor_disabled_proposes_cold_transient(self, redis_test_db):
        """With the floor set to 0, the same cold transient signature falls back
        to pure statistical gating and is proposed, not resumed."""
        from agent.crash_signature import extract_signature

        trace = _floor_trace()
        sig_hash = extract_signature(trace).hash
        session_id = "test-car-floor-disabled"
        _make_session(session_id, "failed", claude_session_uuid="uuid-floor-off")
        _write_telemetry(session_id, trace)
        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                    crash_autoresume_deterministic_floor_attempts=0,
                ),
                patch(
                    "reflections.crash_recovery._machine_owns_project",
                    return_value=True,
                ),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions[0].status == "failed", (
                f"Floor disabled but session was resumed: {sessions[0].status!r}. "
                f"Findings: {result['findings']}"
            )
            assert "auto_resumed=0" in result["summary"], result["summary"]
        finally:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            _cleanup_sessions(*sessions)
            _cleanup_signatures(sig_hash)
            _cleanup_telemetry(session_id)

    def test_non_owner_proposes_floor_eligible_session(self, redis_test_db):
        """The machine-ownership gate (Gap 3b) blocks a floor-eligible resume when
        this machine does not own the session's project — it proposes instead."""
        from agent.crash_signature import extract_signature

        trace = _floor_trace()
        sig_hash = extract_signature(trace).hash
        session_id = "test-car-floor-not-owner"
        _make_session(session_id, "failed", claude_session_uuid="uuid-not-owner")
        _write_telemetry(session_id, trace)
        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                    crash_autoresume_deterministic_floor_attempts=1,
                ),
                patch(
                    "reflections.crash_recovery._machine_owns_project",
                    return_value=False,
                ),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"
            sessions = list(AgentSession.query.filter(session_id=session_id))
            assert sessions[0].status == "failed", (
                f"Non-owner resumed the session: {sessions[0].status!r}. "
                f"Findings: {result['findings']}"
            )
            assert any("not-owner" in f for f in result["findings"]), result["findings"]
        finally:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            _cleanup_sessions(*sessions)
            _cleanup_signatures(sig_hash)
            _cleanup_telemetry(session_id)

    def test_failed_floor_resume_consumes_attempt_and_converges(self, redis_test_db):
        """Critique C1: a resume that fails every tick must still consume an attempt.
        Without this, floor eligibility (0 < 1) re-satisfied on every 300s tick would
        retry forever. After one failed floor resume the counter advances to 1, and
        the next run finds the floor no longer eligible → proposes, not retries."""
        from agent.crash_signature import extract_signature
        from tools.valor_session import ResumeResult

        trace = _floor_trace()
        sig_hash = extract_signature(trace).hash
        session_id = "test-car-floor-converge"
        _make_session(session_id, "failed", claude_session_uuid="uuid-converge")
        _write_telemetry(session_id, trace)

        failing = ResumeResult(success=False, session_id=session_id, error="missing uuid → refusal")
        try:
            with (
                _patch_settings(
                    crash_autoresume_enabled=True,
                    crash_autoresume_min_occurrences=3,
                    crash_autoresume_min_success_ratio=0.7,
                    crash_autoresume_deterministic_floor_attempts=1,
                    crash_autoresume_max_attempts=3,
                ),
                patch(
                    "reflections.crash_recovery._machine_owns_project",
                    return_value=True,
                ),
                patch("tools.valor_session.resume_session", return_value=failing),
                patch.dict(os.environ, {"CRASH_AUTORESUME_LOOKBACK_HOURS": "9999"}),
            ):
                from reflections.crash_recovery import run_crash_recovery

                first = run_crash_recovery()
                assert first["status"] == "ok", first
                # The failed resume consumed one attempt.
                s1 = list(AgentSession.query.filter(session_id=session_id))[0]
                assert str(getattr(s1, "auto_resume_attempts", "0")) == "1", (
                    f"Expected attempt consumed on failure, got "
                    f"{getattr(s1, 'auto_resume_attempts', None)!r}. {first['findings']}"
                )
                assert s1.status == "failed"

                # Second tick: floor no longer eligible (attempts 1 == floor 1),
                # signature still statistically cold → propose, no further retry.
                second = run_crash_recovery()
                assert second["status"] == "ok", second
                s2 = list(AgentSession.query.filter(session_id=session_id))[0]
                assert str(getattr(s2, "auto_resume_attempts", "0")) == "1", (
                    f"Converged run must NOT advance the counter again, got "
                    f"{getattr(s2, 'auto_resume_attempts', None)!r}. {second['findings']}"
                )
                assert "auto_resumed=0" in second["summary"], second["summary"]
        finally:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            _cleanup_sessions(*sessions)
            _cleanup_signatures(sig_hash)
            _cleanup_telemetry(session_id)
