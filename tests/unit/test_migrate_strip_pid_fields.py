"""The pid-field strip migration and its output capture (#2518, D1).

Two defects, one real and one insurance:

**The real one — discarded output.** ``run_pending_migrations`` threw the
migration's streams away on success, so "did the strip actually do anything?"
was answerable only by forensics over ``updated_at`` timestamps. A migration
whose output is discarded has a failure mode indistinguishable from its success
mode. Compounding it, ``logging.basicConfig`` with no ``stream=`` writes to
**stderr**: measured before the fix, stdout was 0 bytes and every ``WOULD
strip`` / ``DEFER`` line plus the final ``Stats:`` dict went to stderr. So a
capture that logged ``result.stdout`` alone would have faithfully recorded an
empty string while a ``grep -c 'result.stdout'`` verification row went green.

That is why the capture test below asserts the **captured text is non-empty and
contains the marker** ``Stats: {'total_records``. Asserting the presence of a
``result.stdout`` token would pass against the exact broken state this fixes.

**The insurance — the zero-record guard.** ``AgentSession.query.all()`` returns
0 rows with no exception if it lands inside popoto's index-rebuild window
(#1720), which is genuinely concurrent with ``/update`` Step 3.6. Reporting
success on an empty scan would record the migration complete having seen
nothing. Exit code **2** is deliberately distinct from the ``1`` used for
per-record errors so the two are separable in ``logs/update.log``.

REDIS SAFETY. ``_migrate_strip_pid_fields`` shells out with ``--apply``, and the
subprocess does NOT inherit the in-process ``POPOTO_REDIS_DB`` swap that the
autouse ``redis_test_db`` fixture performs — popoto resolves its connection from
``REDIS_URL`` at import time. Without an explicit ``REDIS_URL``, that subprocess
would delete-and-recreate every terminal AgentSession in the **production**
keyspace (db 0). Every test that spawns it therefore sets ``REDIS_URL`` to the
claimed test db via the ``redis_test_url`` fixture, and asserts it did so.
"""

from __future__ import annotations

import importlib
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import scripts._strip_migration as shared
import scripts.migrate_strip_pid_fields as strip
from models.agent_session import AgentSession

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROJECT_KEY = "test-strip-pid-fields"

#: The marker the capture must contain. Anchored on the opening of the stats
#: dict rather than on a whole line, so a future key addition does not silently
#: turn this assertion into a no-op.
_STATS_MARKER = "Stats: {'total_records"

#: The three scripts that delegate to the engine. Their docstrings point at the
#: engine's narrative rather than restating it, so they are policed alongside it.
STRIP_DELEGATES = (
    "scripts.migrate_strip_pid_fields",
    "scripts.migrate_strip_pty_fields",
    "scripts.migrate_schema_diet_fields",
)

#: The retracted claim, matched by shape rather than by an exact casing. See
#: ``TestDocstringCorrections.test_the_false_quiescence_claim_is_gone``.
QUIESCENCE_CLAIM = re.compile(r"never\s+writes?\s+terminal\s+rows", re.IGNORECASE)


@pytest.fixture
def cleanup_rows():
    yield
    try:
        for row in AgentSession.query.filter(project_key=_PROJECT_KEY):
            try:
                row.delete()
            except Exception:  # swallow-ok: row may already be gone
                pass
    except Exception:  # swallow-ok: teardown must not mask the assertion result
        pass


def _mk_row(status="completed"):
    return AgentSession.create(
        project_key=_PROJECT_KEY,
        status=status,
        priority="normal",
        session_id=f"{_PROJECT_KEY}_{time.time_ns()}",
        working_dir="/tmp/strip-pid",
        message_text="strip migration test",
        sender_name="Integ",
        chat_id=f"{_PROJECT_KEY}-chat",
        telegram_message_id=1,
    )


class TestZeroRecordGuard:
    """A zero-record scan forks on a raw SCAN: blinded fails closed, empty passes.

    The SCAN result is faked in both directions (#2543). Driving a real index
    rebuild from a unit test would make the branch taken depend on a race.
    """

    def _empty_query(self):
        return SimpleNamespace(all=lambda: iter([]))

    def test_zero_records_logs_the_distinct_message(self, caplog):
        with (
            patch.object(AgentSession, "query", self._empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
            caplog.at_level(logging.ERROR, logger=strip.__name__),
        ):
            stats = strip.migrate(apply=False)

        assert stats["total_records"] == 0
        messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("ZERO RECORDS SCANNED" in m for m in messages), (
            f"the guard must say WHY it refused, not just exit; got {messages}"
        )

    def test_a_blinded_scan_exits_two(self, monkeypatch):
        """Exit 2, not 1 — a blinded scan must be separable from a record error.

        ``run_pending_migrations`` refuses to record any non-zero exit, so
        either code prevents completion; the distinction is for the operator
        reading ``logs/update.log``.
        """
        monkeypatch.setattr("sys.argv", ["migrate_strip_pid_fields.py"])
        with (
            patch.object(AgentSession, "query", self._empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
        ):
            assert strip.main() == 2

    def test_a_genuinely_empty_keyspace_exits_zero(self, monkeypatch):
        """A fresh install has nothing to strip, so the migration IS complete (#2543).

        Before this, the guard could not tell an empty keyspace from a blinded
        one and failed closed on both — so every ``/update`` on a fresh machine
        reran the migration and failed, forever.
        """
        monkeypatch.setattr("sys.argv", ["migrate_strip_pid_fields.py"])
        with (
            patch.object(AgentSession, "query", self._empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (0, True)),
        ):
            assert strip.main() == 0

    def test_a_truncated_scan_cannot_prove_emptiness(self, monkeypatch):
        """``exhaustive=False`` means the count is a partial undercount — fail closed.

        A truncated SCAN that happened to return 0 must never be read as proof
        the keyspace is empty.
        """
        monkeypatch.setattr("sys.argv", ["migrate_strip_pid_fields.py"])
        with (
            patch.object(AgentSession, "query", self._empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (0, False)),
        ):
            assert strip.main() == 2

    def test_a_raising_scan_fails_closed(self, monkeypatch):
        """If the SCAN itself errors we know nothing, so we must not record success."""

        def _boom():
            raise RuntimeError("scan exploded")

        monkeypatch.setattr("sys.argv", ["migrate_strip_pid_fields.py"])
        with (
            patch.object(AgentSession, "query", self._empty_query()),
            patch.object(shared, "agent_session_hash_count", _boom),
        ):
            assert strip.main() == 2

    def test_zero_records_does_not_touch_indexes(self):
        """The guard returns BEFORE the index sweep — a blinded scan sweeps nothing.

        Patched on ``clean_indexes``, the call the script actually makes. This
        assertion previously named ``repair_indexes``, which hotfix ``369d782c8``
        had already replaced — so it passed vacuously against any implementation.
        """
        with (
            patch.object(AgentSession, "query", self._empty_query()),
            patch.object(shared, "agent_session_hash_count", lambda: (4006, True)),
            patch.object(AgentSession, "clean_indexes") as sweep,
        ):
            strip.migrate(apply=True)
        sweep.assert_not_called()

    def test_the_measured_window_is_documented_in_code(self):
        """The guard's justification must stay reachable from the code it guards.

        This was insurance against a theoretical race until the window was
        measured (#2549): driving ``rebuild_indexes()`` against a concurrent
        poller, 91.8% of scans saw zero rows on a populated 4006-row keyspace.
        An operator reading the guard needs that evidence, and the successor
        who wonders whether the fork can be simplified away needs it more.

        The markers must live wherever the guard lives, which since #2524 is the
        shared engine — the cost of consolidation is that the documentation must
        follow the code, not stay behind in one of the three callers.
        """
        import inspect

        src = inspect.getsource(shared.run_strip_migration)
        assert "#2549" in src, "the observed-window evidence must stay cited"
        assert "#2543" in src, "the empty-keyspace fork must stay attributed"
        assert "ACCEPTED CONSEQUENCE" not in src, (
            "the permanent fresh-install failure is fixed, so its accepted-consequence "
            "note must not survive as stale documentation"
        )


class TestPerRecordErrorHandling:
    def test_a_record_level_exception_increments_errors(self, cleanup_rows):
        row = _mk_row()

        with patch.object(strip, "_raw_field_names", side_effect=RuntimeError("hkeys exploded")):
            stats = strip.migrate(apply=False)

        assert stats["errors"] >= 1
        assert stats["total_records"] >= 1
        assert row is not None

    def test_a_record_level_exception_yields_a_nonzero_exit(self, monkeypatch, cleanup_rows):
        """Non-zero means ``run_pending_migrations`` does not record completion.

        A migration that errored on records but reported success would be
        skipped by name forever, leaving those records stale on every machine.
        """
        _mk_row()
        monkeypatch.setattr("sys.argv", ["migrate_strip_pid_fields.py"])

        with patch.object(strip, "_raw_field_names", side_effect=RuntimeError("hkeys exploded")):
            assert strip.main() == 1

    def test_one_bad_record_does_not_abort_the_scan(self, cleanup_rows):
        """Per-record isolation: the rest of the keyspace is still processed."""
        _mk_row()
        _mk_row()
        calls = {"n": 0}

        def _flaky(instance):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first record explodes")
            return set()

        with patch.object(strip, "_raw_field_names", side_effect=_flaky):
            stats = strip.migrate(apply=False)

        assert stats["errors"] == 1
        assert stats["clean"] >= 1, "the scan continued past the failing record"


class TestLogsGoToStdout:
    """The stderr bug, pinned at the source.

    Without ``stream=sys.stdout`` here, the capture in
    ``_migrate_strip_pid_fields`` records an empty string and the Task 12 gate
    artifact — the thing a human reads before authorizing the fleet rollout —
    is blank.
    """

    def test_basicconfig_targets_stdout(self):
        import inspect

        src = inspect.getsource(strip)
        assert "stream=sys.stdout" in src

    def test_the_record_actually_lands_on_stdout_when_run_as_a_script(
        self, monkeypatch, redis_test_url, cleanup_rows
    ):
        """Assert the observed streams, not the source text.

        ``basicConfig`` is a no-op when handlers already exist, so the
        source-level check above can be true while the effective stream is
        stderr. In-process the answer is unobservable — pytest owns the root
        logger's handlers — so this runs the script the way ``/update`` runs it
        and reads the two streams apart.

        Dry run (no ``--apply``): this only needs to observe where the log lines
        go, and there is no reason to write.
        """
        import subprocess
        import sys

        _mk_row()  # a populated keyspace, so the zero-record guard does not fire
        monkeypatch.setenv("REDIS_URL", redis_test_url)

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "migrate_strip_pid_fields.py")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _STATS_MARKER in result.stdout, (
            "the migration's own record must be on STDOUT. Measured before the "
            f"fix: stdout 0 bytes, everything on stderr. Got stdout={result.stdout!r}"
        )
        assert _STATS_MARKER not in result.stderr, (
            "the record must not be duplicated onto stderr — the wrapper logs "
            "both streams, so a duplicate would double every line in update.log"
        )


class TestOutputIsCapturedNonEmpty:
    """The Verification row: assert the captured TEXT, never a source token.

    ``grep -c 'result.stdout'`` goes green against the exact broken state (a
    capture that faithfully records an empty string), which is why the check
    lives here and not in a grep.
    """

    def _run_capture(self, monkeypatch, redis_test_url, caplog):
        from scripts.update import migrations as migrations_mod

        # REDIS SAFETY: the helper shells out with --apply. Point the subprocess
        # at the claimed test db; without this it rewrites production rows.
        monkeypatch.setenv("REDIS_URL", redis_test_url)
        assert not redis_test_url.rstrip("/").endswith("/0"), (
            "refusing to run --apply against db 0 (production keyspace)"
        )

        with caplog.at_level(logging.INFO, logger=migrations_mod.__name__):
            error = migrations_mod._migrate_strip_pid_fields(REPO_ROOT)

        captured = "\n".join(
            r.getMessage()
            for r in caplog.records
            if "[migration:strip_pid_fields]" in r.getMessage()
        )
        return error, captured

    def test_captured_output_is_non_empty_and_carries_the_stats_marker(
        self, monkeypatch, redis_test_url, caplog, cleanup_rows
    ):
        _mk_row()  # a populated keyspace, so the zero-record guard does not fire

        error, captured = self._run_capture(monkeypatch, redis_test_url, caplog)

        assert error is None, f"migration failed: {error}"
        assert captured.strip(), (
            "the capture recorded NOTHING. This is the exact failure the fix "
            "targets: output discarded, or logged from the wrong stream."
        )
        assert _STATS_MARKER in captured, (
            f"the captured text must contain {_STATS_MARKER!r} — that line is "
            f"the record of what the migration did. Got:\n{captured}"
        )

    def test_the_subprocess_ran_against_the_test_db_not_production(
        self, monkeypatch, redis_test_url, caplog, cleanup_rows
    ):
        """Proves the REDIS_URL guard is load-bearing, not decoration.

        ``REDIS_URL`` is unset on this machine by default, so an unguarded
        ``--apply`` subprocess connects to db 0 and rewrites production
        AgentSession rows. Comparing the count the subprocess REPORTS against
        the count visible in the claimed test db is the only in-test evidence
        that the isolation actually held.
        """
        _mk_row()
        expected = len(list(AgentSession.query.all()))

        _, captured = self._run_capture(monkeypatch, redis_test_url, caplog)

        assert f"'total_records': {expected}" in captured, (
            f"the subprocess scanned a different keyspace than this test's "
            f"(expected {expected} records). If it reported a larger number it "
            f"connected to db 0 and just rewrote production rows. Got:\n{captured}"
        )

    def test_the_capture_records_the_run_mode_too(
        self, monkeypatch, redis_test_url, caplog, cleanup_rows
    ):
        """Provenance is worthless if it does not say whether it applied anything."""
        _mk_row()
        _, captured = self._run_capture(monkeypatch, redis_test_url, caplog)
        assert "APPLY" in captured, (
            "the wrapper runs with --apply; the log must record that, not leave "
            "a reader guessing whether this was a dry run"
        )

    def test_capture_happens_on_the_success_path_not_only_on_failure(
        self, monkeypatch, redis_test_url, caplog, cleanup_rows
    ):
        """The original code returned output ONLY on a non-zero exit.

        That is precisely why a successful-but-blind run left no record.
        """
        _mk_row()
        error, captured = self._run_capture(monkeypatch, redis_test_url, caplog)
        assert error is None, "this run must SUCCEED, or the test proves nothing"
        assert captured.strip()

    # Both-streams capture and the both-tails failure string used to be asserted
    # here as source-token greps -- the exact anti-pattern this file's module
    # docstring warns about, and one that goes green against a capture faithfully
    # recording an empty string. Since #2524 they are covered functionally, against
    # real subprocesses, by
    # tests/unit/test_strip_migration_shared.py::TestSharedSubprocessRunner.


class TestGuardedIndexRepair:
    """``clean_indexes()`` — neither the raw rebuild nor the repair wrapper (D6).

    The raw rebuild tears down and reconstructs every index, opening the #1720
    class-set window where ``query.all()`` returns 0 with no exception, and it
    currently fails outright on pre-existing phantom index metadata (#2536).
    The repair wrapper is rejected for the same reason — it calls the raw
    rebuild internally. Hotfix ``369d782c8`` settled this on ``clean_indexes()``,
    the documented production-safe orphan sweep; the per-record delete+save
    already maintains every index atomically, so the trailing call is defensive.

    These assertions named ``repair_indexes`` until #2524 — a method the script
    had already stopped calling, so they passed against any implementation.
    """

    def test_raw_rebuild_is_not_called_anywhere_in_the_script(self):
        """Covers the engine too — that is where the sweep call now lives."""
        import inspect

        assert "rebuild_indexes" not in inspect.getsource(strip)
        assert "rebuild_indexes" not in inspect.getsource(shared)

    def test_sweep_runs_only_when_something_was_stripped(self, cleanup_rows):
        """A clean keyspace is a no-op — no index churn for nothing."""
        _mk_row()

        with patch.object(AgentSession, "clean_indexes") as sweep:
            stats = strip.migrate(apply=True)

        assert stats["stripped"] == 0
        sweep.assert_not_called()

    def test_sweep_failure_does_not_raise(self, cleanup_rows):
        """The sweep is best-effort; the strip itself already committed.

        The ``side_effect`` is installed on ``clean_indexes`` — the method the
        script calls. Injected on ``repair_indexes`` (as this test did before
        #2524) it never fired, so the test proved nothing.
        """
        _mk_row()

        with (
            patch.object(AgentSession, "clean_indexes", side_effect=RuntimeError("boom")) as sweep,
            patch.object(strip, "_raw_field_names", return_value={"claude_pid"}),
            patch("popoto.redis_db.POPOTO_REDIS_DB"),
            patch("popoto.Model.save"),
        ):
            # Must not raise.
            stats = strip.migrate(apply=True)

        assert stats["stripped"] >= 1
        assert sweep.called, "the failing sweep must actually have been invoked"


class TestDocstringCorrections:
    """The safety narrative must not assert a property that is false.

    It claimed "the worker never writes terminal rows". Observation contradicts
    it: ``cleanup_corrupted_agent_sessions`` re-saves every hydrated record,
    terminal ones included, and ``/update`` invokes it at Step 5.5. The
    atomicity argument holds on its own; the quiescence claim never did.

    Asserted against the ENGINE's docstring since #2524. The narrative used to
    be duplicated between engine and delegate, which is the same drift hazard
    this consolidation exists to close, only relocated -- so the delegate now
    keeps a pointer and the engine holds the one copy these assertions guard.
    """

    def test_the_false_quiescence_claim_is_gone(self):
        """Matched by shape, not by one capitalization the text never had.

        The retracted clause shipped lowercase and mid-sentence -- "(the worker
        never writes terminal rows)" (``984d3bb7f:scripts/migrate_strip_pty_fields.py``).
        An assertion pinned to "The worker never writes terminal rows" matched no
        string that ever existed, so pasting the original clause back verbatim
        read green (#2564). The pattern below catches any subject making the
        claim, in any casing, across whatever whitespace a reflow introduces.

        Checked across the engine AND all three delegates: the clause coming
        back in a delegate is the same retraction undone.
        """
        for module_name in (shared.__name__, *STRIP_DELEGATES):
            doc = importlib.import_module(module_name).__doc__ or ""
            assert not QUIESCENCE_CLAIM.search(doc), (
                f"{module_name} restates the retracted quiescence claim. Terminal rows "
                "are re-saved by cleanup_corrupted_agent_sessions; the safety property "
                "is pipeline atomicity, not quiescence."
            )

    def test_the_actual_terminal_row_writer_is_named(self):
        assert "cleanup_corrupted_agent_sessions" in (shared.__doc__ or "")

    def test_the_ttl_ageout_claim_is_corrected(self):
        """Deferred ``is_ledger`` rows are re-saved continuously, refreshing TTL."""
        assert "Deferred rows do not age out" in (shared.__doc__ or "")

    def test_no_delegate_duplicates_the_narrative(self):
        """Two copies are free to drift; one is not. Enforced across ALL THREE.

        Policing only the pid delegate would let the same clause live in three
        files while the assertion read green, which is the drift this
        consolidation exists to close.
        """
        for module_name in STRIP_DELEGATES:
            doc = importlib.import_module(module_name).__doc__ or ""
            assert "cleanup_corrupted_agent_sessions" not in doc, (
                f"{module_name} restates the engine's safety narrative instead of pointing at it"
            )
            assert "_strip_migration" in doc, f"{module_name} must point at the engine"

    def test_the_retracted_self_certified_claim_is_absent(self):
        import inspect

        # Read the ENGINE: the delegate is now ~30 lines of pointer text, so a
        # retracted claim coming back would come back where the narrative lives.
        src = inspect.getsource(shared)
        for retracted in ("self-certif", "never stripped"):
            assert retracted not in src.lower()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
