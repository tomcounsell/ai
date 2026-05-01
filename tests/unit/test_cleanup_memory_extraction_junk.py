"""Unit tests for scripts/cleanup_memory_extraction_junk.py (issue #1212)."""

from unittest.mock import MagicMock, patch


def _make_record(memory_id, agent_id, content, superseded_by=""):
    """Build a Memory-shaped MagicMock for cleanup tests.

    Sets attributes that the cleanup script reads. Tracks save() calls so
    tests can assert behavior without touching Redis.
    """
    record = MagicMock()
    record.memory_id = memory_id
    record.agent_id = agent_id
    record.content = content
    record.superseded_by = superseded_by
    record.superseded_by_rationale = ""
    # Default save() returns True (success); tests override per-record.
    record.save = MagicMock(return_value=True)
    return record


class TestRunCleanup:
    """Test scripts/cleanup_memory_extraction_junk.py::run_cleanup()."""

    def test_dry_run_does_not_modify(self, capsys):
        """Default dry_run mode prints candidates without touching Redis."""
        from scripts.cleanup_memory_extraction_junk import run_cleanup

        junk = _make_record(
            "mem-junk-1",
            "extraction-sess-001",
            "There is no agent session response to analyze.",
        )

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [junk]
            counts = run_cleanup(dry_run=True)

        # No save() call in dry-run mode.
        junk.save.assert_not_called()
        assert counts["total"] == 1
        assert counts["superseded"] == 0
        assert counts["blocked"] == 0
        captured = capsys.readouterr()
        assert "would-supersede" in captured.out

    def test_apply_marks_superseded(self):
        """Apply mode sets superseded_by and superseded_by_rationale."""
        from scripts.cleanup_memory_extraction_junk import (
            CLEANUP_RATIONALE,
            CLEANUP_SUPERSEDED_BY_VALUE,
            run_cleanup,
        )

        junk = _make_record(
            "mem-junk-2",
            "extraction-sess-002",
            '"tags": ["session-management"]',  # JSON shrapnel
        )

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [junk]
            counts = run_cleanup(dry_run=False)

        assert junk.superseded_by == CLEANUP_SUPERSEDED_BY_VALUE
        assert junk.superseded_by_rationale == CLEANUP_RATIONALE
        junk.save.assert_called_once()
        assert counts["total"] == 1
        assert counts["superseded"] == 1
        assert counts["blocked"] == 0

    def test_skips_already_superseded(self):
        """Records with non-empty superseded_by are not re-touched."""
        from scripts.cleanup_memory_extraction_junk import run_cleanup

        already = _make_record(
            "mem-already-1",
            "extraction-sess-003",
            "There is no agent session response.",
            superseded_by="some-prior-cleanup",  # non-empty
        )

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [already]
            counts = run_cleanup(dry_run=False)

        already.save.assert_not_called()
        assert counts["total"] == 0

    def test_skips_non_extraction_agent_id(self):
        """Records whose agent_id is not 'extraction-*' are untouched.

        Protects human Telegram saves (agent_id='telegram-…'), post-merge
        learnings (agent_id='post-merge'), and intentional saves
        (agent_id='intentional-…') from this cleanup, even if their content
        accidentally trips the refusal predicate.
        """
        from scripts.cleanup_memory_extraction_junk import run_cleanup

        # Content matches a refusal pattern, but agent_id is wrong.
        protected = _make_record(
            "mem-protected-1",
            "post-merge",
            "There is no agent session response to analyze.",
        )
        protected2 = _make_record(
            "mem-protected-2",
            "intentional-tom",
            '"tags": ["redis"]',
        )

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [protected, protected2]
            counts = run_cleanup(dry_run=False)

        protected.save.assert_not_called()
        protected2.save.assert_not_called()
        assert counts["total"] == 0

    def test_skips_legitimate_extraction_records(self):
        """Real extraction observations (not refusal/shrapnel) are untouched.

        A record with agent_id='extraction-*' and superseded_by=='' but
        legitimate content must NOT be marked superseded. This is the
        cross-check that the predicate's narrowness applies in cleanup too.
        """
        from scripts.cleanup_memory_extraction_junk import run_cleanup

        legit = _make_record(
            "mem-legit-1",
            "extraction-sess-004",
            (
                "The dev session ended cleanly with no novel observations to flag — "
                "verified at session_executor.py:805"
            ),
        )

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [legit]
            counts = run_cleanup(dry_run=False)

        legit.save.assert_not_called()
        assert counts["total"] == 0

    def test_per_record_save_failure_does_not_block(self):
        """One save() raising does not abort the loop; remaining records still processed."""
        from scripts.cleanup_memory_extraction_junk import run_cleanup

        crashing = _make_record(
            "mem-crash-1",
            "extraction-sess-005",
            "There is no agent session response.",
        )
        crashing.save = MagicMock(side_effect=Exception("simulated Redis error"))

        ok = _make_record(
            "mem-ok-1",
            "extraction-sess-006",
            '"category": "decision"',  # JSON shrapnel
        )

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [crashing, ok]
            counts = run_cleanup(dry_run=False)

        # Both records were processed; the second one succeeded.
        crashing.save.assert_called_once()
        ok.save.assert_called_once()
        assert counts["total"] == 2
        assert counts["superseded"] == 1
        # The crashing record is neither superseded nor 'blocked' (which is
        # WriteFilter veto, not exception). It is just an error count not
        # captured separately — total - superseded - blocked covers it.

    def test_writefilter_blocked_save_counted(self):
        """save() returning False (WriteFilter veto) is counted as 'blocked'."""
        from scripts.cleanup_memory_extraction_junk import run_cleanup

        blocked = _make_record(
            "mem-blocked-1",
            "extraction-sess-007",
            "There is no agent session response.",
        )
        blocked.save = MagicMock(return_value=False)

        with patch("models.memory.Memory") as mock_memory_cls:
            mock_memory_cls.query.all.return_value = [blocked]
            counts = run_cleanup(dry_run=False)

        blocked.save.assert_called_once()
        assert counts["total"] == 1
        assert counts["superseded"] == 0
        assert counts["blocked"] == 1

    def test_main_apply_flag_runs_apply_mode(self, capsys):
        """`python -m ... --apply` triggers apply mode (not dry-run)."""
        from scripts import cleanup_memory_extraction_junk as mod

        captured = {}

        def fake_run(*, dry_run=True):
            captured["dry_run"] = dry_run
            return {"total": 0, "superseded": 0, "blocked": 0}

        with patch.object(mod, "run_cleanup", side_effect=fake_run):
            exit_code = mod.main(["--apply"])

        assert exit_code == 0
        assert captured["dry_run"] is False

    def test_main_default_is_dry_run(self):
        """No flag = dry-run by default."""
        from scripts import cleanup_memory_extraction_junk as mod

        captured = {}

        def fake_run(*, dry_run=True):
            captured["dry_run"] = dry_run
            return {"total": 0, "superseded": 0, "blocked": 0}

        with patch.object(mod, "run_cleanup", side_effect=fake_run):
            exit_code = mod.main([])

        assert exit_code == 0
        assert captured["dry_run"] is True
