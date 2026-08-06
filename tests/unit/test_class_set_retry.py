"""Unit tests for tools/class_set_retry.py (issues #1720, #2550).

The retired design was 5 attempts x 200ms, a constant sized against a spike
measured at 150 sessions. It exhausted silently: a cap-exhausted read returns the
same ``None`` as a genuine miss, so a live session was reported "not found" with
nothing logged. These tests cover the two properties that replaced it — a
wall-clock budget, and an exhaustion that is never silent.
"""

from __future__ import annotations

import logging
import time

from tools.class_set_retry import class_set_retry_attempts, log_class_set_exhaustion


class TestClassSetRetryAttempts:
    def test_first_attempt_always_yields(self):
        """Retry is an addition to the read, never a precondition for it."""
        assert list(class_set_retry_attempts(budget_s=0.0, backoff_s=0.2)) == [1]

    def test_attempts_are_one_based_and_contiguous(self):
        attempts = list(class_set_retry_attempts(budget_s=0.25, backoff_s=0.05))
        assert attempts == list(range(1, len(attempts) + 1))
        assert len(attempts) > 1

    def test_stops_within_the_budget(self):
        """The bound is wall-clock, so a bigger keyspace is a config change."""
        start = time.monotonic()
        list(class_set_retry_attempts(budget_s=0.3, backoff_s=0.05))
        elapsed = time.monotonic() - start
        assert elapsed <= 0.3 + 0.05

    def test_a_larger_budget_buys_more_attempts(self):
        """The #2550 sizing fix: the cap scales without touching code."""
        small = list(class_set_retry_attempts(budget_s=0.1, backoff_s=0.02))
        large = list(class_set_retry_attempts(budget_s=0.5, backoff_s=0.02))
        assert len(large) > len(small)

    def test_no_sleep_when_the_first_read_hits(self):
        """A found-on-first-try read must cost nothing."""
        start = time.monotonic()
        for _ in class_set_retry_attempts(budget_s=5.0, backoff_s=0.5):
            break
        assert time.monotonic() - start < 0.1

    def test_defaults_come_from_settings(self):
        from config.settings import settings

        assert settings.timeouts.class_set_retry_budget_s > 0
        assert settings.timeouts.class_set_retry_backoff_s > 0
        assert list(class_set_retry_attempts())


class TestLogClassSetExhaustion:
    """Exhaustion must be loud. Silence is the defect (#2550)."""

    def test_logs_at_warning_not_debug(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_class_set_exhaustion(logging.getLogger("t"), "_find_session", "sess-1", 4)
        assert caplog.records
        assert all(r.levelno == logging.WARNING for r in caplog.records)

    def test_unproven_case_names_the_ambiguity(self, caplog):
        """Without a direct-key hit, "stale index" is a guess and must read as one."""
        with caplog.at_level(logging.WARNING):
            log_class_set_exhaustion(logging.getLogger("t"), "_find_session_by_id", "sess-1", 4)
        msg = caplog.text
        assert "indistinguishable from genuine absence" in msg
        assert "sess-1" in msg
        assert "TIMEOUTS__CLASS_SET_RETRY_BUDGET_S" in msg

    def test_fallback_hit_is_reported_as_proof_not_a_guess(self, caplog):
        """A direct key lookup skips the class set, so a hit proves it was rebuilding."""
        with caplog.at_level(logging.WARNING):
            log_class_set_exhaustion(
                logging.getLogger("t"),
                "_find_session",
                "sess-1",
                4,
                resolved_by_fallback=True,
            )
        msg = caplog.text
        assert "the class set was mid-rebuild" in msg
        assert "indistinguishable" not in msg

    def test_message_carries_the_attempt_count_and_budget(self, caplog):
        """ "I retried N times and gave up" is the line the issue says was missing."""
        with caplog.at_level(logging.WARNING):
            log_class_set_exhaustion(logging.getLogger("t"), "_find_session", "sess-1", 7)
        assert "7 attempt(s)" in caplog.text
