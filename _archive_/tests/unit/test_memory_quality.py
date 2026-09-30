"""Unit tests for the shared junk-definition heuristics (agent/memory_quality.py).

Covers the Phase-1 (issue #2200) success criteria for
`docs/plans/memory-telemetry-baseline.md`: ack-only detection, fragment
detection, durable full-fact classification, and the deterministic
None/empty/whitespace disposition. Also covers the Phase-2 (issue #2201)
write-gate predicate `gate_reason` / `MIN_CONTENT_LENGTH` and the
best-effort `_increment_gate_counter` helper. Pure-function tests -- no
Redis, no popoto, no network (the counter test mocks the Redis handle).

Marker: sdlc (feature tests for issue #2200 / #2201).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.memory_quality import (
    MIN_CONTENT_LENGTH,
    classify_content,
    gate_reason,
    is_ack_only,
    is_fragment,
)

pytestmark = pytest.mark.sdlc


class TestClassifyContentAckOnly:
    @pytest.mark.parametrize(
        "content",
        ["Yup", "Ahhh", "ok", "thanks", "Yeah", "Ohhh", "Thank you", "no thanks"],
    )
    def test_ack_only_utterances_classified_as_ack_only(self, content):
        assert classify_content(content) == "ack_only"
        assert is_ack_only(content) is True

    @pytest.mark.parametrize("content", ["Yup", "Ahhh", "ok"])
    def test_is_ack_only_true_for_bare_acknowledgements(self, content):
        assert is_ack_only(content) is True


class TestClassifyContentFragment:
    @pytest.mark.parametrize(
        "content",
        [
            "includes:",
            "The deployment process:",
            "The list is (unclosed",
            "Config has [one, two",
            "-",
            "1.",
        ],
    )
    def test_fragment_content_classified_as_fragment(self, content):
        assert classify_content(content) == "fragment"
        assert is_fragment(content) is True

    def test_dangling_colon_with_no_body_is_fragment(self):
        assert is_fragment("includes:") is True

    def test_multiline_colon_with_body_is_not_dangling(self):
        content = "Steps:\n- one\n- two"
        assert is_fragment(content) is False


class TestClassifyContentDurable:
    @pytest.mark.parametrize(
        "content",
        [
            "The deployment uses blue-green rollout with automated rollback "
            "on error rate spikes above 5%.",
            "User prefers black formatting over ruff lint enforcement.",
            "The worker restarts the bridge after code changes to agent/.",
        ],
    )
    def test_full_fact_sentences_classified_as_durable(self, content):
        assert classify_content(content) == "durable"
        assert is_ack_only(content) is False
        assert is_fragment(content) is False


class TestClassifyContentEdgeCases:
    @pytest.mark.parametrize("content", ["", None, "   ", "\n\t  "])
    def test_empty_none_whitespace_classify_as_fragment_without_raising(self, content):
        assert classify_content(content) == "fragment"

    def test_is_ack_only_false_for_none_and_empty(self):
        assert is_ack_only(None) is False
        assert is_ack_only("") is False
        assert is_ack_only("   ") is False

    def test_is_fragment_true_for_none_and_empty(self):
        assert is_fragment(None) is True
        assert is_fragment("") is True
        assert is_fragment("   ") is True

    def test_no_module_level_heavy_imports(self):
        """agent/memory_quality.py must stay a dependency-light leaf."""
        import agent.memory_quality as mq

        source = open(mq.__file__).read()
        for banned in ("import redis", "import popoto", "from redis", "from popoto", "from models"):
            assert banned not in source


class TestGateReasonTaxonomy:
    """Phase-2 (issue #2201) write-gate predicate.

    Oracle values verified directly against the frozen `classify_content`
    (see that function's docstring/tests above) -- `gate_reason` composes
    it and must never alter its three-bucket output.
    """

    def test_ack_only_maps_to_ack(self):
        assert gate_reason("Yup") == "ack"

    def test_dangling_colon_maps_to_fragment(self):
        # "includes:" has no trailing body -- classify_content says fragment.
        assert gate_reason("includes:") == "fragment"

    def test_bare_list_marker_no_body_maps_to_fragment(self):
        # "1." alone (no body) is a bare list marker -- fragment, not short.
        assert gate_reason("1.") == "fragment"

    def test_list_marker_with_body_below_floor_maps_to_short(self):
        # "1. Concurrency" (14 chars) has a body, so the bare-marker regex
        # does NOT match -- classify_content calls it "durable". Only the
        # length floor (15) catches it, so it must be "short", not "fragment".
        assert len("1. Concurrency") == 14
        assert classify_content("1. Concurrency") == "durable"
        assert gate_reason("1. Concurrency") == "short"

    def test_below_floor_durable_content_maps_to_short(self):
        assert len("deploy fri") == 10
        assert gate_reason("deploy fri") == "short"

    @pytest.mark.parametrize("content", [None, "", "   "])
    def test_none_empty_whitespace_maps_to_fragment(self, content):
        assert gate_reason(content) == "fragment"

    def test_at_floor_durable_content_persists(self):
        # 17 chars, >= MIN_CONTENT_LENGTH (15) -- persists (None = no gate reason).
        assert len("Deploy on Fridays") == 17
        assert gate_reason("Deploy on Fridays") is None

    def test_taxonomy_is_exactly_three_reasons(self):
        """fragment must never be folded into short -- three distinct counters."""
        reasons = {
            gate_reason("Yup"),
            gate_reason("includes:"),
            gate_reason("deploy fri"),
        }
        assert reasons == {"ack", "fragment", "short"}


class TestMinContentLength:
    def test_min_content_length_is_fifteen(self):
        assert MIN_CONTENT_LENGTH == 15

    def test_classify_content_has_no_length_awareness(self):
        """classify_content stays frozen -- the length floor is write-gate-only."""
        # "1. Concurrency" is short (14 chars) yet classify_content still
        # reports "durable" -- proves the length floor lives only in
        # gate_reason, never inside classify_content (baseline integrity).
        assert classify_content("1. Concurrency") == "durable"


class TestIncrementGateCounterNeverRaises:
    def test_raising_redis_client_does_not_propagate(self):
        from models.memory_gate import _increment_gate_counter

        class _RaisingRedis:
            def incr(self, *_args, **_kwargs):
                raise ConnectionError("redis unreachable")

        with patch("popoto.redis_db.POPOTO_REDIS_DB", _RaisingRedis()):
            # Must not raise -- best-effort telemetry only.
            _increment_gate_counter("test-project", "ack")


class TestMemorySaveContentGate:
    """Memory.save() content gate (issue #2201) -- exercised against real
    Redis via the Memory model, not mocked, per this repo's testing
    philosophy. Each test uses a unique project_key so counter deltas are
    unambiguous even when the suite runs repeatedly against a shared Redis.
    """

    def test_insert_rejects_ack_only_and_increments_ack_counter(self):
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.memory import Memory

        project_key = "test-gate-ack-project"
        counter_key = f"{project_key}:memory-gate:ack"
        before = int(POPOTO_REDIS_DB.get(counter_key) or 0)

        m = Memory(
            agent_id="test-gate-agent", project_key=project_key, content="Yup", importance=5.0
        )
        result = m.save()

        assert result is False
        assert int(POPOTO_REDIS_DB.get(counter_key) or 0) == before + 1

    def test_insert_rejects_fragment_and_increments_fragment_counter(self):
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.memory import Memory

        project_key = "test-gate-fragment-project"
        counter_key = f"{project_key}:memory-gate:fragment"
        before = int(POPOTO_REDIS_DB.get(counter_key) or 0)

        m = Memory(
            agent_id="test-gate-agent",
            project_key=project_key,
            content="includes:",
            importance=5.0,
        )
        result = m.save()

        assert result is False
        assert int(POPOTO_REDIS_DB.get(counter_key) or 0) == before + 1

    def test_insert_rejects_below_floor_and_increments_short_counter(self):
        from popoto.redis_db import POPOTO_REDIS_DB

        from models.memory import Memory

        project_key = "test-gate-short-project"
        counter_key = f"{project_key}:memory-gate:short"
        before = int(POPOTO_REDIS_DB.get(counter_key) or 0)

        m = Memory(
            agent_id="test-gate-agent",
            project_key=project_key,
            content="1. Concurrency",  # 14 chars, durable under classify_content
            importance=5.0,
        )
        result = m.save()

        assert result is False
        assert int(POPOTO_REDIS_DB.get(counter_key) or 0) == before + 1

    def test_insert_persists_durable_content_above_floor(self):
        from models.memory import Memory

        project_key = "test-gate-durable-project"
        m = Memory(
            agent_id="test-gate-agent",
            project_key=project_key,
            content="Deploy on Fridays only after the smoke suite is green",
            importance=5.0,
        )
        result = m.save()

        assert result is not False
        reloaded = Memory.query.filter(memory_id=m.memory_id)
        assert len(reloaded) == 1

    def test_update_resave_of_junk_content_is_never_gated(self):
        """Guards the outcome/metadata re-save at memory_extraction.py:1343.

        A record that is already persisted (an UPDATE, not an INSERT) must
        never be dropped by the content gate, even if its content has
        degraded to (or always was) below-floor/ack-only junk -- otherwise
        the outcome/dismissal_count/last_outcome write is silently lost.
        """
        from models.memory import Memory

        project_key = "test-gate-update-project"
        m = Memory(
            agent_id="test-gate-agent",
            project_key=project_key,
            content="Durable content for the update re-save gating test",
            importance=2.0,
            source="agent",
        )
        insert_result = m.save()
        assert insert_result is not False

        # Simulate the record degrading to legacy junk and an outcome-loop
        # metadata re-save on the SAME already-persisted key.
        m.content = "no"  # ack-only -- would gate a fresh INSERT
        m.metadata = {"last_outcome": "acted"}
        update_result = m.save()

        assert update_result is not False, "Update re-save must never be content-gated"
        reloaded = Memory.query.filter(memory_id=m.memory_id)
        assert len(reloaded) == 1
        assert reloaded[0].content == "no"
        assert reloaded[0].metadata.get("last_outcome") == "acted"
