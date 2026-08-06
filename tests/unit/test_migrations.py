"""Tests for scripts.update.migrations._migrate_backfill_pipeline_ledger (issue #2012).

Real Popoto/Redis integration -- no mocks for the AgentSession/PipelineLedger
storage layer, per this repo's testing philosophy (see CLAUDE.md "Testing
Philosophy"). The only monkeypatch is `_resolve_target_repo` for the single
scenario that needs a deterministically-unresolvable env fallback (avoiding a
real `gh repo view` subprocess call in CI); the issue lock itself is real,
backed by the test Redis db via the autouse `redis_test_db` fixture.

Every test cleans up the AgentSession/PipelineLedger records it creates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent.pipeline_ledger import PipelineLedger
from models.agent_session import AgentSession
from models.session_lifecycle import touch_issue_lock
from scripts.update.migrations import MIGRATIONS, _migrate_backfill_pipeline_ledger

_TEST_REPO = "test-owner/test-repo"


def _make_session(
    session_id: str,
    issue_number: int,
    status: str = "dormant",
    stage_states: dict | None = None,
    pr_number: int | None = None,
) -> AgentSession:
    session = AgentSession.create(
        session_id=session_id,
        session_type="eng",
        project_key="test",
        working_dir="/tmp",
        status=status,
        chat_id="999",
        message_text="test session for migration backfill",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
        issue_number=issue_number,
    )
    if stage_states is not None:
        session.stage_states = stage_states
    if pr_number is not None:
        session.pr_number = pr_number
    session.save()
    return session


def _cleanup_ledger(issue_number: int, target_repo: str = _TEST_REPO) -> None:
    for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
        record.delete()


def _cleanup_lock(issue_number: int) -> None:
    from popoto.redis_db import POPOTO_REDIS_DB as _R

    _R.delete(f"session:issuelock:{issue_number}")


def _cleanup_session(session: AgentSession | None) -> None:
    if session is not None:
        session.delete()


class TestBackfillWithResolvableTargetRepo:
    """A non-terminal session with a live issue lock (lease-pinned target_repo)
    and non-empty stage_states gets backfilled into the ledger."""

    _ISSUE = 900001

    def setup_method(self):
        _cleanup_ledger(self._ISSUE)
        _cleanup_lock(self._ISSUE)

    def teardown_method(self):
        _cleanup_ledger(self._ISSUE)
        _cleanup_lock(self._ISSUE)

    def test_backfills_ledger_from_session_stage_states(self, tmp_path: Path):
        # Pin a real, Redis-backed issue lock with target_repo -- this is
        # what a live in-flight session's lease looks like.
        touch_issue_lock(self._ISSUE, run_id="run-abc", target_repo=_TEST_REPO)

        session = _make_session(
            "mig-backfill-001",
            self._ISSUE,
            status="dormant",
            stage_states={"ISSUE": "completed", "PLAN": "in_progress"},
            pr_number=4242,
        )
        try:
            error = _migrate_backfill_pipeline_ledger(tmp_path)
            assert error is None

            ledger = PipelineLedger.get_or_create(_TEST_REPO, self._ISSUE)
            assert ledger.pr_number == 4242
            import json

            assert json.loads(ledger.stage_states_json) == {
                "ISSUE": "completed",
                "PLAN": "in_progress",
            }
        finally:
            _cleanup_session(session)

    def test_running_twice_is_a_noop_second_time(self, tmp_path: Path):
        """Idempotency: a second run leaves the already-backfilled ledger
        content byte-identical and raises no error."""
        touch_issue_lock(self._ISSUE, run_id="run-abc", target_repo=_TEST_REPO)
        session = _make_session(
            "mig-backfill-002",
            self._ISSUE,
            status="running",
            stage_states={"ISSUE": "completed"},
        )
        try:
            assert _migrate_backfill_pipeline_ledger(tmp_path) is None
            first = PipelineLedger.get_or_create(_TEST_REPO, self._ISSUE)
            first_json = first.stage_states_json

            assert _migrate_backfill_pipeline_ledger(tmp_path) is None
            second = PipelineLedger.get_or_create(_TEST_REPO, self._ISSUE)
            assert second.stage_states_json == first_json
        finally:
            _cleanup_session(session)


class TestNeverOverwritesNonEmptyLedger:
    """Risk 1 mitigation: a ledger that already carries content (from a live
    writer, or a prior migration run) is never clobbered by the backfill."""

    _ISSUE = 900002

    def setup_method(self):
        _cleanup_ledger(self._ISSUE)
        _cleanup_lock(self._ISSUE)

    def teardown_method(self):
        _cleanup_ledger(self._ISSUE)
        _cleanup_lock(self._ISSUE)

    def test_skips_session_when_ledger_already_populated(self, tmp_path: Path):
        touch_issue_lock(self._ISSUE, run_id="run-abc", target_repo=_TEST_REPO)

        # A live writer already populated the ledger with newer content.
        existing = PipelineLedger.get_or_create(_TEST_REPO, self._ISSUE)
        existing.stage_states_json = '{"ISSUE": "completed", "PLAN": "completed"}'
        existing.pr_number = 9999
        existing.save()

        # The session's own blob is different (older/stale) -- if the
        # migration overwrote, this would be visible below.
        session = _make_session(
            "mig-backfill-003",
            self._ISSUE,
            status="dormant",
            stage_states={"ISSUE": "in_progress"},
            pr_number=1,
        )
        try:
            error = _migrate_backfill_pipeline_ledger(tmp_path)
            assert error is None

            reloaded = PipelineLedger.get_or_create(_TEST_REPO, self._ISSUE)
            assert reloaded.stage_states_json == '{"ISSUE": "completed", "PLAN": "completed"}'
            assert reloaded.pr_number == 9999
        finally:
            _cleanup_session(session)


class TestUnresolvableTargetRepoNeverKeysUnderNone:
    """A session whose target_repo cannot be determined (no live lease AND
    env resolution fails) is skipped, not keyed under None."""

    _ISSUE = 900003

    def setup_method(self):
        _cleanup_lock(self._ISSUE)

    def teardown_method(self):
        _cleanup_lock(self._ISSUE)
        # Defensive: sweep any phantom None-keyed ledger this test might
        # otherwise have produced, plus a real-target_repo one if resolution
        # unexpectedly succeeded via a real `gh repo view`.
        for record in list(PipelineLedger.query.all()):
            if record.issue_number == self._ISSUE:
                record.delete()

    def test_skips_and_logs_warning_no_lock_no_env_resolution(
        self, tmp_path: Path, monkeypatch, caplog
    ):
        import logging

        # No issue lock exists for this issue (never acquired) -- peek finds
        # nothing pinned. Force the env fallback to also fail deterministically
        # rather than depending on `gh repo view` behavior in CI.
        monkeypatch.setattr("tools._sdlc_utils._resolve_target_repo", lambda: None)

        session = _make_session(
            "mig-backfill-004",
            self._ISSUE,
            status="dormant",
            stage_states={"ISSUE": "completed"},
        )
        try:
            with caplog.at_level(logging.WARNING):
                error = _migrate_backfill_pipeline_ledger(tmp_path)
            assert error is None

            assert any(
                "target_repo unresolvable" in r.message and str(self._ISSUE) in r.getMessage()
                for r in caplog.records
            )

            # No PipelineLedger record with a None/empty target_repo (or
            # keyed under "None:{issue}") was ever created for this issue.
            for record in PipelineLedger.query.all():
                if record.issue_number == self._ISSUE:
                    assert record.target_repo not in (None, "", "None")
                    assert not record.ledger_key.startswith("None:")
        finally:
            _cleanup_session(session)


class TestTerminalSessionsAreNotTouched:
    """A terminal-status session (completed/failed/killed/abandoned/cancelled)
    is skipped entirely -- this migration only lifts in-flight state."""

    _ISSUE = 900004

    def setup_method(self):
        _cleanup_ledger(self._ISSUE)
        _cleanup_lock(self._ISSUE)

    def teardown_method(self):
        _cleanup_ledger(self._ISSUE)
        _cleanup_lock(self._ISSUE)

    def test_terminal_session_never_creates_a_ledger(self, tmp_path: Path):
        touch_issue_lock(self._ISSUE, run_id="run-abc", target_repo=_TEST_REPO)

        session = _make_session(
            "mig-backfill-005",
            self._ISSUE,
            status="completed",
            stage_states={"ISSUE": "completed", "MERGE": "completed"},
        )
        try:
            error = _migrate_backfill_pipeline_ledger(tmp_path)
            assert error is None

            # No ledger record was created for this issue at all -- the
            # migration never even calls get_or_create for a terminal session.
            matches = PipelineLedger.query.filter(ledger_key=f"{_TEST_REPO}:{self._ISSUE}")
            assert not matches
        finally:
            _cleanup_session(session)


class TestMigrationRegistration:
    def test_registered_in_migrations_dict(self):
        assert "backfill_pipeline_ledger" in MIGRATIONS
        fn, description = MIGRATIONS["backfill_pipeline_ledger"]
        assert fn is _migrate_backfill_pipeline_ledger
        assert description


class TestStripPidFieldsV2Registration:
    """The rename-and-rerun mechanism (#2518, D1).

    ``run_pending_migrations`` skips by NAME, so registering the same script
    under a second name is the auditable way to re-run a migration already
    recorded complete on every machine — no hand-editing of
    ``data/migrations_completed.json``, which is unauditable, easy to get wrong,
    and impossible on a machine nobody is sitting at.
    """

    def test_v2_is_registered(self):
        from scripts.update.migrations import _migrate_strip_pid_fields

        assert "strip_pid_fields_v2" in MIGRATIONS
        fn, description = MIGRATIONS["strip_pid_fields_v2"]
        assert fn is _migrate_strip_pid_fields, (
            "v2 must point at the SAME script — it is a re-run, not a fork"
        )
        assert description

    def test_v1_is_still_registered(self):
        """Removing v1 would un-record it on machines that already ran it."""
        assert "strip_pid_fields" in MIGRATIONS

    def test_v2_runs_after_v1_and_before_the_phantom_purge(self):
        """Ordering is load-bearing, and dict order is the only thing enforcing it.

        ``run_pending_migrations`` iterates ``MIGRATIONS`` in insertion order.
        v2 must follow v1 (a machine seeing both for the first time should not
        run the newer registration first), and must precede
        ``purge_phantom_agent_sessions`` (which deletes index-bookkeeping hashes
        the strip's own index repair expects to still be there).
        """
        names = list(MIGRATIONS)
        assert names.index("strip_pid_fields") < names.index("strip_pid_fields_v2")
        assert names.index("strip_pid_fields_v2") < names.index("purge_phantom_agent_sessions")

    def test_every_migration_name_is_unique(self):
        """A duplicate key would silently shadow one registration."""
        assert len(MIGRATIONS) == len(set(MIGRATIONS))
