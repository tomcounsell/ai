"""The orphan reaper's ownership resolution, against REAL Redis rows (#2518, Job 4).

``AgentSession.find_live_session_by_pid`` is the forward scan that decides
whether a ``claude -p`` process the reaper found is an orphan or a live
session's harness. It is the highest-risk change in PR #2516 — and every one of
the 40+ reaper tests mocks it away with ``patch.object(..., return_value=...)``.
Those tests prove the gates AROUND the scan; none proves the scan itself
resolves anything.

**The canary assertion has never existed:** write a genuinely live session to
Redis, stamp its execution fence, put its pid in the process table, and assert
the reaper leaves it alone. If the scan silently returned ``None`` for every
row — an index drift, a bad status cohort, a phantom filter that drops
everything — every mocked test would still be green and the reaper would
SIGTERM live harnesses in production.

So in this file ``find_live_session_by_pid`` is NEVER patched. Rows are created
through the ORM and resolved through the real ``status`` index.

What IS faked, and why:

* the process table (``psutil.process_iter``) — the reaper must see a specific
  pid with a specific ``create_time`` and ``ppid == 1``, which cannot be
  arranged with a real process on demand, and process-timing tests are
  parallel-hostile under ``-n auto --dist=loadfile``.

Hygiene (repo CLAUDE.md): every row uses a ``test-fence-scan`` ``project_key``
and is deleted through the ORM scoped by that key. No raw Redis, no bulk
unscoped operations.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import psutil
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from agent import session_health
from models.agent_session import AgentSession

_PROJECT_KEY = "test-fence-scan"

#: A cmdline the reaper's signature match recognizes as a harness.
_CLAUDE_CMD = [
    "/usr/local/lib/node_modules/@anthropic-ai/claude-code/claude_agent_sdk/_bundled/claude",
    "-p",
]


@pytest.fixture(autouse=True)
def _cleanup_rows():
    """Delete every row this module created, through the ORM, scoped by key."""
    saved_orphans = set(session_health._pending_sigkill_orphans)
    session_health._pending_sigkill_orphans.clear()
    yield
    session_health._pending_sigkill_orphans.clear()
    session_health._pending_sigkill_orphans.update(saved_orphans)
    try:
        for row in AgentSession.query.filter(project_key=_PROJECT_KEY):
            try:
                row.delete()
            except Exception:  # swallow-ok: row may already be gone
                pass
    except Exception:  # swallow-ok: teardown must not mask the assertion result
        pass


def _mk_row(*, status, pid, create_time, heartbeat_age_s=5, suffix=""):
    """Create a real AgentSession in Redis with a stamped execution fence."""
    now_ms = int(time.time() * 1_000_000)
    row = AgentSession.create(
        project_key=_PROJECT_KEY,
        status=status,
        priority="normal",
        session_id=f"{_PROJECT_KEY}_{status}{suffix}_{now_ms}",
        working_dir="/tmp/fence-scan",
        message_text="forward-scan integration",
        sender_name="Integ",
        chat_id=f"{_PROJECT_KEY}-chat",
        telegram_message_id=1,
        started_at=datetime.now(UTC) - timedelta(seconds=600),
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=heartbeat_age_s),
    )
    # Stamp the fence through the production writer, not by hand.
    row.stamp_execution_spawn(
        pid=pid,
        create_time=create_time,
        cwd="/tmp/fence-scan",
        harness="claude",
        generation=1,
    )
    return AgentSession.get_by_id(row.id)


def _fake_proc(*, pid, create_time, ppid=1, cmdline=None):
    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.info = {
        "pid": pid,
        "ppid": ppid,
        "cmdline": cmdline or _CLAUDE_CMD,
        "create_time": create_time,
    }
    proc.ppid.return_value = ppid
    proc.cmdline.return_value = cmdline or _CLAUDE_CMD
    proc.create_time.return_value = create_time
    proc.children.return_value = []
    proc.parent.return_value = None
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _reap(proc):
    """Run the reaper over exactly one process, with the scan UNMOCKED."""
    with (
        patch.object(psutil, "process_iter", return_value=[proc]),
        patch.object(session_health, "_psutil_process_for_pid", return_value=proc),
    ):
        return session_health._reap_orphan_session_processes()


# ---------------------------------------------------------------------------
# The canary assertion
# ---------------------------------------------------------------------------


class TestLiveSessionIsNotReaped:
    def test_live_running_session_with_a_stamped_fence_is_protected(self):
        """THE canary assertion: a live session's harness survives the reaper.

        Everything here is real except the process table: a ``running`` row in
        Redis, a fresh heartbeat, a fence stamped by the production writer, and
        ownership resolved through the real non-terminal status index.
        """
        pid, ct = 981_001, 1_700_000_100.5
        _mk_row(status="running", pid=pid, create_time=ct)
        proc = _fake_proc(pid=pid, create_time=ct)

        killed = _reap(proc)

        assert killed == 0, "a live session's harness must never be reaped"
        proc.terminate.assert_not_called()
        assert pid not in {p for p, _ in session_health._pending_sigkill_orphans}

    def test_the_scan_resolves_the_row_it_is_protecting(self):
        """Guards the test above against passing for the wrong reason.

        If the scan returned None and some unrelated gate happened to skip the
        process, ``killed == 0`` would still hold. This asserts the resolution
        itself.
        """
        pid, ct = 981_002, 1_700_000_200.5
        row = _mk_row(status="running", pid=pid, create_time=ct)

        found = AgentSession.find_live_session_by_pid(pid, ct)

        assert found is not None, "the forward scan must resolve a real stamped row"
        assert found.id == row.id
        assert session_health._session_is_alive(found) is True

    @pytest.mark.parametrize(
        "offset,status",
        list(enumerate(["pending", "active", "dormant", "waiting_for_children", "paused"])),
    )
    def test_every_non_terminal_status_cohort_resolves(self, offset, status):
        """The scan iterates the whole non-terminal set, not just ``running``."""
        pid, ct = 981_010 + offset, 1_700_000_300.5
        row = _mk_row(status=status, pid=pid, create_time=ct, suffix=status)

        found = AgentSession.find_live_session_by_pid(pid, ct)

        assert found is not None and found.id == row.id


# ---------------------------------------------------------------------------
# Genuine orphans still get reaped
# ---------------------------------------------------------------------------


class TestOrphansAreStillReaped:
    def test_process_with_no_owning_row_is_reaped(self):
        """The scan must return None for a pid no live row claims."""
        pid, ct = 982_001, 1_700_000_400.5
        assert AgentSession.find_live_session_by_pid(pid, ct) is None

        proc = _fake_proc(pid=pid, create_time=ct)
        killed = _reap(proc)

        assert killed == 1
        proc.terminate.assert_called_once()

    def test_terminal_row_does_not_protect_its_pid(self):
        """A completed session's row is outside the scan — its pid is an orphan.

        Post-#2516 the scan iterates NON_TERMINAL_STATUSES, so a terminal row
        can never be returned. This is the positive statement of that fact.
        """
        pid, ct = 982_002, 1_700_000_500.5
        _mk_row(status="completed", pid=pid, create_time=ct)

        assert AgentSession.find_live_session_by_pid(pid, ct) is None

        proc = _fake_proc(pid=pid, create_time=ct)
        assert _reap(proc) == 1

    def test_recycled_pid_does_not_inherit_the_rows_protection(self):
        """A live row holding a STALE exec_pid must not shield an unrelated process.

        The row is genuinely live and genuinely claims this pid — but the
        process now holding it started at a different time, so it is not the
        harness the row spawned.
        """
        pid = 982_003
        _mk_row(status="running", pid=pid, create_time=1_700_000_600.5)

        proc = _fake_proc(pid=pid, create_time=1_700_009_999.5)  # unrelated occupant
        killed = _reap(proc)

        assert killed == 1, (
            "a stale exec_pid must not confer ownership of whatever process later occupies that pid"
        )
        proc.terminate.assert_called_once()

    def test_stale_heartbeat_row_does_not_protect_its_harness(self):
        """Ownership resolves, but ``_session_is_alive`` still gates the skip."""
        pid, ct = 982_004, 1_700_000_700.5
        _mk_row(status="running", pid=pid, create_time=ct, heartbeat_age_s=2 * 3600)

        assert AgentSession.find_live_session_by_pid(pid, ct) is not None
        proc = _fake_proc(pid=pid, create_time=ct)
        assert _reap(proc) == 1


# ---------------------------------------------------------------------------
# Race 5: two non-terminal rows claiming one pid
# ---------------------------------------------------------------------------


class TestDuplicateFencePidResolution:
    """``NON_TERMINAL_STATUSES`` is a ``frozenset``.

    Its iteration order varies per process under hash randomization, so before
    #2518 a stale ``dormant`` row and a live ``running`` row both carrying
    ``exec_pid=P`` resolved nondeterministically across restarts. The fence
    must decide instead — which is why both directions are asserted here rather
    than "the running one wins".
    """

    def test_the_fence_decides_which_row_owns_the_pid(self):
        pid = 983_001
        dormant_ct = 1_700_000_800.5
        running_ct = 1_700_000_900.5
        dormant = _mk_row(status="dormant", pid=pid, create_time=dormant_ct, suffix="-dormant")
        running = _mk_row(status="running", pid=pid, create_time=running_ct, suffix="-running")

        # Observed identity matches the RUNNING row's fence.
        assert AgentSession.find_live_session_by_pid(pid, running_ct).id == running.id
        # …and the symmetric case resolves the other way. If scan ORDER decided,
        # one of these two assertions would fail on every run.
        assert AgentSession.find_live_session_by_pid(pid, dormant_ct).id == dormant.id

    def test_neither_row_owns_a_third_process_on_the_same_pid(self):
        pid = 983_002
        _mk_row(status="dormant", pid=pid, create_time=1_700_001_000.5, suffix="-d")
        _mk_row(status="running", pid=pid, create_time=1_700_001_100.5, suffix="-r")

        assert AgentSession.find_live_session_by_pid(pid, 1_700_009_999.5) is None

    def test_unfenced_lookup_across_duplicates_warns_instead_of_resolving_silently(self, caplog):
        """Without an observed ``create_time`` the pid-only fallback IS ambiguous.

        That path is retained so legacy rows still resolve, but it must not
        resolve a collision silently — a nondeterministic ownership decision
        that nobody can see is the sharpest failure mode here.
        """
        pid = 983_003
        _mk_row(status="dormant", pid=pid, create_time=1_700_001_200.5, suffix="-d")
        _mk_row(status="running", pid=pid, create_time=1_700_001_300.5, suffix="-r")

        with caplog.at_level(logging.WARNING, logger="models.agent_session"):
            found = AgentSession.find_live_session_by_pid(pid, None)

        assert found is not None
        assert any(
            f"claiming pid={pid}" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        ), "a duplicate-pid collision must be logged, not resolved in silence"


# ---------------------------------------------------------------------------
# A blinded cohort must fail toward PROTECTED
# ---------------------------------------------------------------------------


class TestBlindedCohortDoesNotUnprotectLiveSessions:
    def test_a_raising_status_cohort_leaves_other_cohorts_resolvable(self, caplog):
        """One poisoned cohort must not blind the whole scan.

        Only the failing cohort is faked; every other status still goes to real
        Redis. If the scan aborted on the first error, the live row would stop
        resolving and its harness would be reaped as an unowned orphan.
        """
        pid, ct = 984_001, 1_700_001_400.5
        row = _mk_row(status="running", pid=pid, create_time=ct)

        real_query = AgentSession.query

        class PoisonedCohort:
            """Delegates to the real query except for one status.

            ``redis.exceptions.ConnectionError`` is the realistic shape here: a
            transient backend failure while one status index is being read. It
            is a ``RedisError``, which is what the narrowed ``except`` in
            ``find_live_session_by_pid`` catches.
            """

            def filter(self, **kwargs):
                if kwargs.get("status") == "dormant":
                    raise RedisConnectionError("cohort index unreadable")
                return real_query.filter(**kwargs)

        with (
            patch.object(AgentSession, "query", PoisonedCohort()),
            caplog.at_level(logging.WARNING, logger="models.agent_session"),
        ):
            found = AgentSession.find_live_session_by_pid(pid, ct)

        assert found is not None and found.id == row.id, (
            "a blinded cohort must fail toward PROTECTED — returning None here "
            "would let a live session's subprocess be reaped as an orphan"
        )
        assert any("scan failed for status=dormant" in r.getMessage() for r in caplog.records), (
            "the WARNING is the only signal a cohort went blind; it must not be swallowed"
        )

    def test_the_reaper_spares_a_live_harness_even_with_a_blinded_cohort(self):
        """End-to-end: the protection survives a partially-unreadable index."""
        pid, ct = 984_002, 1_700_001_500.5
        _mk_row(status="running", pid=pid, create_time=ct)

        real_query = AgentSession.query

        class PoisonedCohort:
            def filter(self, **kwargs):
                if kwargs.get("status") == "pending":
                    raise RedisConnectionError("cohort index unreadable")
                return real_query.filter(**kwargs)

        proc = _fake_proc(pid=pid, create_time=ct)
        with patch.object(AgentSession, "query", PoisonedCohort()):
            killed = _reap(proc)

        assert killed == 0
        proc.terminate.assert_not_called()


# ---------------------------------------------------------------------------
# Legacy rows still resolve
# ---------------------------------------------------------------------------


class TestLegacyRowsStillResolve:
    def test_a_row_with_no_recorded_create_time_still_protects_its_pid(self):
        """Fenced matching is a REFINEMENT of the pid match, never a broadening.

        A pre-fence row records a pid and no ``create_time``. Requiring a match
        it cannot supply would silently stop protecting every legacy session at
        once, so the pid-only fallback is retained.
        """
        pid = 985_001
        row = _mk_row(status="running", pid=pid, create_time=None)

        found = AgentSession.find_live_session_by_pid(pid, 1_700_001_600.5)
        assert found is not None and found.id == row.id

        proc = _fake_proc(pid=pid, create_time=1_700_001_600.5)
        assert _reap(proc) == 0

    def test_an_unreadable_observed_create_time_falls_back_to_pid_only(self):
        """``create_time`` 0.0 from psutil is coerced to None by the caller.

        Passing 0.0 through would mismatch every recorded fence and unprotect
        every live session at once, which is why the reaper passes
        ``create_time or None``.
        """
        pid, ct = 985_002, 1_700_001_700.5
        row = _mk_row(status="running", pid=pid, create_time=ct)

        assert AgentSession.find_live_session_by_pid(pid, None).id == row.id

        # The reaper's own coercion: a 0.0 reading must not unprotect the row.
        proc = _fake_proc(pid=pid, create_time=0.0)
        assert _reap(proc) == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
