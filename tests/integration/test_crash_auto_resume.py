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
    startup_failure_kind: str | None = None,
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
    if startup_failure_kind is not None:
        session.startup_failure_kind = startup_failure_kind
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
            with patch.dict(
                os.environ,
                {
                    "CRASH_AUTORESUME_ENABLED": "0",
                    "CRASH_AUTORESUME_LOOKBACK_HOURS": "9999",
                },
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
        """A session with startup_failure_kind='plateau' must be classified as
        NON_RESUMABLE_DETERMINISTIC and NEVER transitioned to 'pending', even
        with CRASH_AUTORESUME_ENABLED=1. The reflection must emit [ESCALATE] log."""
        session_id = "test-car-sess-plateau"
        session = _make_session(
            session_id,
            "failed",
            claude_session_uuid="test-uuid-plateau",
            startup_failure_kind="plateau",
        )
        # Trace with NO turn_start events (plateau sessions never start a turn).
        _write_telemetry(session_id, _no_turn_start_trace())

        try:
            with (
                patch.dict(
                    os.environ,
                    {
                        "CRASH_AUTORESUME_ENABLED": "1",
                        "CRASH_AUTORESUME_LOOKBACK_HOURS": "9999",
                    },
                ),
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
            with patch.dict(
                os.environ,
                {
                    "CRASH_AUTORESUME_ENABLED": "1",
                    "CRASH_AUTORESUME_LOOKBACK_HOURS": "9999",
                },
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
            env = {
                "CRASH_AUTORESUME_ENABLED": "0",
                "CRASH_AUTORESUME_LOOKBACK_HOURS": "9999",
            }

            with patch.dict(os.environ, env):
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
            with patch.dict(os.environ, env):
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
        """With CRASH_AUTORESUME_ENABLED=1 but a signature that has NOT cleared the
        confidence gate (first occurrence: occurrence_count < MIN_OCCURRENCES, and
        zero recorded outcomes so success ratio is 0.0), the reflection must NOT
        auto-resume the session. Status stays terminal; a 'proposed:' finding
        appears; no auto-resume fires.

        This guards Blocker 1: the confidence gate (is_auto_eligible) must run
        before any resume. A resumable signature is necessary but NOT sufficient —
        it must also be statistically warranted.
        """
        trace = _standard_trace(to_status="abandoned")

        session_id = "test-car-sess-below-conf"
        session = _make_session(
            session_id,
            "abandoned",
            claude_session_uuid="test-uuid-below-conf",
        )
        _write_telemetry(session_id, trace)

        try:
            with patch.dict(
                os.environ,
                {
                    "CRASH_AUTORESUME_ENABLED": "1",
                    "CRASH_AUTORESUME_LOOKBACK_HOURS": "9999",
                    "CRASH_AUTORESUME_MIN_OCCURRENCES": "3",
                    "CRASH_AUTORESUME_MIN_SUCCESS_RATIO": "0.7",
                },
            ):
                from reflections.crash_recovery import run_crash_recovery

                result = run_crash_recovery()

            assert result["status"] == "ok", f"Unexpected error: {result}"

            # Session must still be 'abandoned' — the confidence gate blocked resume.
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
            with patch.dict(
                os.environ,
                {
                    "CRASH_AUTORESUME_ENABLED": "1",
                    "CRASH_AUTORESUME_LOOKBACK_HOURS": "9999",
                    "CRASH_AUTORESUME_MIN_OCCURRENCES": "3",
                    "CRASH_AUTORESUME_MIN_SUCCESS_RATIO": "0.7",
                },
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
