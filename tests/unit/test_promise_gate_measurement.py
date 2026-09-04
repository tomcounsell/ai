"""Unit coverage for tools/promise_gate_measurement.py (issue #3093 review, #3027).

This tool is the recorded entry criterion for the deferred #3035 phase-4
decision, so a silent bug in its percentile math or legacy-row filter would
corrupt that decision rather than fail loudly. Focused coverage only: the
pure-function edge cases the review called out, not a rewrite of the tool.
"""

import json

from tools.promise_gate_measurement import (
    _is_promise_gate_row,
    _percentile,
    find_contradictions,
    latency_by_source_and_transport,
)


class TestPercentile:
    def test_empty_input_returns_none_and_stays_json_serializable(self):
        assert _percentile([], 0.50) is None
        # NaN would serialize to a bare `NaN` token, which is not valid JSON.
        assert json.dumps({"p50": _percentile([], 0.50)}) == '{"p50": null}'

    def test_single_value_returns_that_value_for_any_percentile(self):
        assert _percentile([42.0], 0.50) == 42.0
        assert _percentile([42.0], 0.99) == 42.0
        assert _percentile([42.0], 0.0) == 42.0

    def test_many_values_interpolates(self):
        values = [float(v) for v in range(1, 101)]  # 1..100, already sorted
        assert _percentile(values, 0.50) == 50.5
        assert _percentile(values, 0.0) == 1.0
        assert _percentile(values, 1.0) == 100.0


class TestIsPromiseGateRow:
    def test_legacy_row_with_no_kind_field_matched_by_source_prefix(self):
        row = {"source": "promise_gate_drafter_llm", "elapsed_ms": 100}
        assert _is_promise_gate_row(row) is True

    def test_legacy_row_with_no_kind_field_matched_by_exact_source(self):
        row = {"source": "terminal_flush", "elapsed_ms": 5}
        assert _is_promise_gate_row(row) is True

    def test_legacy_row_with_no_kind_field_and_unrelated_source_excluded(self):
        row = {"source": "read_the_room", "elapsed_ms": 5}
        assert _is_promise_gate_row(row) is False

    def test_explicit_kind_promise_gate_included_regardless_of_source(self):
        row = {"kind": "promise_gate", "source": "anything"}
        assert _is_promise_gate_row(row) is True

    def test_explicit_other_kind_excluded_regardless_of_source(self):
        row = {"kind": "read_the_room", "source": "promise_gate_drafter"}
        assert _is_promise_gate_row(row) is False


class TestLatencyBySourceAndTransport:
    def test_rows_missing_elapsed_ms_are_excluded_not_zeroed(self):
        rows = [
            {"source": "promise_gate_drafter_llm", "transport": "telegram", "elapsed_ms": 100},
            # No elapsed_ms at all -- must be excluded, not treated as 0.
            {"source": "promise_gate_drafter_llm", "transport": "telegram"},
            # Non-numeric elapsed_ms -- also excluded.
            {
                "source": "promise_gate_drafter_llm",
                "transport": "telegram",
                "elapsed_ms": "not-a-number",
            },
            {"source": "promise_gate_drafter_llm", "transport": "telegram", "elapsed_ms": 300},
        ]

        buckets = latency_by_source_and_transport(rows)

        assert len(buckets) == 1
        bucket = buckets[0]
        assert bucket.count == 2
        assert bucket.values_ms == [100.0, 300.0]
        # If the excluded rows were counted as zero, p50 would drop toward 0
        # instead of landing between the two real samples.
        percentiles = bucket.percentiles()
        assert percentiles["p50"] == 200.0

    def test_groups_by_source_and_transport_pair(self):
        rows = [
            {"source": "promise_gate_drafter_llm", "transport": "telegram", "elapsed_ms": 100},
            {"source": "promise_gate_drafter_llm", "transport": "email", "elapsed_ms": 50},
            {"source": "promise_gate_drafter", "transport": "telegram", "elapsed_ms": 1},
        ]

        buckets = latency_by_source_and_transport(rows)

        keys = {(b.source, b.transport) for b in buckets}
        assert keys == {
            ("promise_gate_drafter_llm", "telegram"),
            ("promise_gate_drafter_llm", "email"),
            ("promise_gate_drafter", "telegram"),
        }

    def test_missing_source_and_transport_fall_back_to_unknown(self):
        rows = [{"elapsed_ms": 10}]

        buckets = latency_by_source_and_transport(rows)

        assert len(buckets) == 1
        assert buckets[0].source == "unknown"
        assert buckets[0].transport == "unknown"

    def test_rows_missing_queue_wait_ms_are_excluded_not_zeroed(self):
        rows = [
            {
                "source": "promise_gate_drafter_llm",
                "transport": "telegram",
                "elapsed_ms": 100,
                "queue_wait_ms": 10,
            },
            # No queue_wait_ms at all -- must be excluded, not treated as 0.
            {"source": "promise_gate_drafter_llm", "transport": "telegram", "elapsed_ms": 200},
            # Non-numeric queue_wait_ms -- also excluded.
            {
                "source": "promise_gate_drafter_llm",
                "transport": "telegram",
                "elapsed_ms": 300,
                "queue_wait_ms": "not-a-number",
            },
            {
                "source": "promise_gate_drafter_llm",
                "transport": "telegram",
                "elapsed_ms": 400,
                "queue_wait_ms": 30,
            },
        ]

        buckets = latency_by_source_and_transport(rows)

        assert len(buckets) == 1
        bucket = buckets[0]
        # elapsed_ms bucket keeps all 4 rows -- exclusion is per-field.
        assert bucket.count == 4
        # queue_wait_ms bucket only keeps the 2 rows carrying a numeric value.
        assert bucket.queue_wait_count == 2
        assert bucket.queue_wait_values_ms == [10.0, 30.0]
        percentiles = bucket.queue_wait_percentiles()
        assert percentiles["p50"] == 20.0

    def test_row_missing_elapsed_ms_but_carrying_queue_wait_ms_still_counted(self):
        rows = [
            {"source": "promise_gate_drafter_timeout", "transport": "telegram", "queue_wait_ms": 5},
        ]

        buckets = latency_by_source_and_transport(rows)

        assert len(buckets) == 1
        bucket = buckets[0]
        assert bucket.count == 0
        assert bucket.queue_wait_count == 1
        assert bucket.queue_wait_values_ms == [5.0]


class TestFindContradictionsNegationGuard:
    """A negation must not let a positive-reading keyword flag an honest
    non-delivered disposition -- regression guard for the review finding
    that "not done" (containing "done") and "not delivered" (containing
    "delivered") false-positived on the positive leg."""

    def test_not_done_does_not_flag_blocked_disposition(self):
        entries = [
            {
                "item": "test on stage server",
                "disposition": "blocked",
                "evidence": "blocked on the key, not done yet",
            }
        ]

        assert find_contradictions(entries) == []

    def test_not_delivered_does_not_flag_not_started_disposition(self):
        entries = [
            {
                "item": "ship the package",
                "disposition": "not_started",
                "evidence": "not delivered -- waiting on the vendor",
            }
        ]

        assert find_contradictions(entries) == []

    def test_unnegated_positive_keyword_still_flags(self):
        """The guard must not swallow genuine contradictions: an
        unqualified positive-reading evidence string on a non-delivered
        disposition still flags."""
        entries = [
            {
                "item": "merge to main",
                "disposition": "blocked",
                "evidence": "already merged and shipped",
            }
        ]

        flags = find_contradictions(entries)

        assert len(flags) == 1
        assert flags[0].item == "merge to main"

    def test_mixed_evidence_with_distant_negative_keyword_still_flags(self):
        """The negation guard is scoped to a window around the matched
        positive keyword, not the whole string: an unrelated negative
        clause earlier in the string ("failed at first") must not
        suppress a genuine, later contradiction ("now merged and
        shipped")."""
        entries = [
            {
                "item": "deploy",
                "disposition": "blocked",
                "evidence": "the deploy failed at first but it is now merged and shipped",
            }
        ]

        flags = find_contradictions(entries)

        assert len(flags) == 1
        assert flags[0].item == "deploy"

    def test_delivered_leg_negation_guard_symmetric_with_positive_leg(self):
        """The ``delivered`` leg gets the same scoped-negation treatment as
        the positive leg: "did not hit any blockers" is good news, not a
        genuine failure, because the negation's object is the bad outcome
        rather than the disposition itself."""
        entries = [
            {
                "item": "release",
                "disposition": "delivered",
                "evidence": "delivered clean, did not hit any blockers",
            }
        ]

        assert find_contradictions(entries) == []
