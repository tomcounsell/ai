"""Unit tests for post-session memory extraction and outcome detection.

#1925: every LLM call in agent/memory_extraction.py routes through the
shared ``_llm_call`` helper, which now delegates to ``agent.llm.run_typed``
instead of constructing ``anthropic.AsyncAnthropic`` directly. Tests that
exercise a specific site's LLM call mock ``run_typed`` at its
``agent.memory_extraction`` module-level import site -- no real network
call and no dependence on PydanticAI's internal Anthropic tool-calling wire
format.
"""

import json

import pytest


class TestExtractBigrams:
    """Test agent/memory_extraction.py _extract_bigrams()."""

    def test_extracts_unigrams(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("deploy rollback strategy")
        assert ("deploy",) in bigrams
        assert ("rollback",) in bigrams
        assert ("strategy",) in bigrams

    def test_extracts_bigrams(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("deploy rollback strategy")
        assert ("deploy", "rollback") in bigrams
        assert ("rollback", "strategy") in bigrams

    def test_filters_short_words(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("the big cat sat on a mat")
        # "the", "big", "cat", "sat" are all < 4 chars, filtered out
        assert ("the",) not in bigrams
        assert ("cat",) not in bigrams

    def test_empty_text(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("")
        assert len(bigrams) == 0

    def test_case_insensitive(self):
        from agent.memory_extraction import _extract_bigrams

        bigrams = _extract_bigrams("Deploy ROLLBACK Strategy")
        assert ("deploy",) in bigrams
        assert ("rollback",) in bigrams


class TestDetectOutcomes:
    """Test agent/memory_extraction.py detect_outcomes_async()."""

    @pytest.mark.asyncio
    async def test_empty_thoughts(self):
        from agent.memory_extraction import detect_outcomes_async

        result = await detect_outcomes_async([], "some response text")
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_response(self):
        from agent.memory_extraction import detect_outcomes_async

        result = await detect_outcomes_async([("key1", "deployment strategy")], "")
        assert result == {}

    @pytest.mark.asyncio
    async def test_fallback_always_deferred_on_overlap(self):
        """When the LLM judge is unavailable, the bigram-overlap fallback must
        never emit "acted" -- even when the thought and response share
        keywords. A cheap heuristic must not manufacture positive
        corroboration for the confidence-learning signal (precision over
        recall). Only the LLM judge may emit "acted"."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import detect_outcomes_async

        thoughts = [("key1", "deployment strategy uses blue green")]
        response = "We use a blue green deployment strategy with rollback"

        with patch(
            "agent.memory_extraction._judge_outcomes_llm",
            new=AsyncMock(return_value=None),
        ):
            result = await detect_outcomes_async(thoughts, response)

        assert result.get("key1") == "deferred"

    @pytest.mark.asyncio
    async def test_fallback_always_deferred_without_overlap(self):
        """The fallback must also never emit "dismissed" -- absence of
        keyword overlap is not evidence the memory was unused. Both
        directions resolve to the neutral "deferred" outcome."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import detect_outcomes_async

        thoughts = [("key1", "kubernetes helm charts")]
        response = "The database migration completed successfully with zero downtime"

        with patch(
            "agent.memory_extraction._judge_outcomes_llm",
            new=AsyncMock(return_value=None),
        ):
            result = await detect_outcomes_async(thoughts, response)

        assert result.get("key1") == "deferred"

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        from agent.memory_extraction import detect_outcomes_async

        # Bad inputs should not raise
        result = await detect_outcomes_async([("", "")], "test")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_used_outcome_not_remapped(self):
        """'used' outcome from LLM judge must survive coercion guard unchanged.

        Tests that the popoto v1.5.0 'used' outcome (consumed but did not drive
        the response) passes through detect_outcomes_async without being coerced
        to 'dismissed'.
        """
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import detect_outcomes_async

        memory_key = "test-memory-used-123"
        # Simulate _judge_outcomes_llm returning "used" for the memory
        mock_llm_result = {
            memory_key: {
                "outcome": "used",
                "reasoning": "Agent read the memory but did not use it to drive the response",
            }
        }

        with patch(
            "agent.memory_extraction._judge_outcomes_llm",
            new=AsyncMock(return_value=mock_llm_result),
        ):
            thoughts = [(memory_key, "deployment pipeline red-green canary strategy")]
            result = await detect_outcomes_async(thoughts, "The weather is nice today.")

        # "used" must not be coerced to "dismissed"
        assert result.get(memory_key) == "used", (
            f"Expected 'used' outcome to survive coercion guard, got: {result.get(memory_key)!r}"
        )


class TestRunPostSessionExtraction:
    """Test agent/memory_extraction.py run_post_session_extraction()."""

    @pytest.mark.asyncio
    async def test_short_response_skips(self):
        from agent.memory_extraction import extract_observations_async

        result = await extract_observations_async("test", "short")
        assert result == []

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        from agent.memory_extraction import run_post_session_extraction

        # Should not raise even with bad session
        await run_post_session_extraction("nonexistent", "some text")

    # --- Issue #2201: unparseable extraction output is dropped+counted,
    # never exploded into per-line records (the removed fallback). ---

    @pytest.mark.asyncio
    async def test_unparseable_llm_output_returns_empty_and_increments_fallback_counter(self):
        """Non-JSON, non-refusal Haiku output is dropped and counted.

        Guards issue #2201 end-to-end at the caller: `_parse_categorized_
        observations` returns [] for prose with no JSON-shaped substring,
        and `extract_observations_async` must resolve `project_key` BEFORE
        the not-parsed short-circuit so `fallback_dropped` always has a key
        to increment (the plan's Blocker 2 fix -- project_key resolution
        must not happen after the early return).
        """
        from unittest.mock import AsyncMock, patch

        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.memory_extraction import extract_observations_async

        project_key = "test-fallback-dropped-project"
        counter_key = f"{project_key}:memory-gate:fallback_dropped"
        before = int(POPOTO_REDIS_DB.get(counter_key) or 0)

        # Plain prose, no JSON substring, not a refusal, long enough to pass
        # every pre-LLM guard -- reaches the parser and falls through to
        # the unconditional `return []`.
        unparseable = (
            "Worker finished session in 12.4s and migrated three tables "
            "across the new API server without any structured takeaway."
        )
        mock_llm = AsyncMock(return_value=unparseable)
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(
                "sess-fallback-dropped", unparseable, project_key=project_key
            )

        assert result == []
        assert int(POPOTO_REDIS_DB.get(counter_key) or 0) == before + 1

    # --- Issue #1212: pre-LLM and post-LLM refusal/whitespace guards ---

    @pytest.mark.asyncio
    async def test_refusal_input_skips_llm_call(self):
        """Pre-LLM refusal-pattern guard: refusal-shaped input never calls Haiku."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        # 50+ chars so the length guard does not catch it; the refusal pattern
        # guard must catch it instead.
        refusal_input = (
            "There is no agent session response to analyze. "
            "Please provide the session output for me to extract observations."
        )
        assert len(refusal_input) >= 50

        mock_llm = AsyncMock(side_effect=AssertionError("_llm_call MUST NOT be invoked"))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await extract_observations_async("sess-refusal-pre", refusal_input)
        assert result == []
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_dominant_input_skips_llm_call(self):
        """Whitespace-dominance guard rejects <30% non-whitespace inputs.

        Locks in the _MIN_NON_WHITESPACE_RATIO=0.3 threshold by exercising
        BOTH sides of the boundary: ~25% non-whitespace must be rejected,
        ~35% non-whitespace must be accepted (would call Haiku).
        """
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        # 25% non-whitespace: content interleaved with whitespace so the
        # 50-char strip-based guard passes (the .strip() at the callsite
        # only trims edges; interior whitespace stays). 100 chars total,
        # 25 letters + 75 whitespace.
        rejected = ("a   " * 25)[:100]  # "a   a   a   ..." — interior-padded
        assert len(rejected) == 100
        non_ws = len(rejected) - rejected.count(" ")
        assert non_ws == 25, f"expected 25 non-ws chars, got {non_ws}"
        # Strip-length must exceed 50 so the 50-char guard does NOT catch it
        # — we want the whitespace-dominance guard to catch it instead.
        assert len(rejected.strip()) >= 50

        mock_llm_reject = AsyncMock(
            side_effect=AssertionError("rejected input MUST NOT call Haiku")
        )
        with patch("agent.memory_extraction._llm_call", mock_llm_reject):
            result = await extract_observations_async("sess-ws-low", rejected)
        assert result == []
        mock_llm_reject.assert_not_called()

        # 35% non-whitespace, similarly interleaved. Above threshold —
        # _llm_call MUST be invoked. We make it return "NONE" so extraction
        # completes without saving.
        # Pattern "abc      " (3 letters + 6 spaces, 9 chars block, 3/9 = 33.3%)
        # tweaked to land exactly 35%: use "abcd      " (4/10 = 40%) and
        # truncate. Easier: 35 letters + 65 spaces interleaved as 7 letters
        # per 20-char block (7/20 = 35%).
        block = "abcdefg" + (" " * 13)  # 7 + 13 = 20 chars, 35% non-ws
        accepted = (block * 5)[:100]
        assert len(accepted) == 100
        non_ws_accepted = len(accepted) - accepted.count(" ")
        assert non_ws_accepted == 35, f"expected 35 non-ws chars, got {non_ws_accepted}"
        assert len(accepted.strip()) >= 50

        mock_llm_accept = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm_accept),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async("sess-ws-ok", accepted)
        assert result == []  # NONE response means no observations
        mock_llm_accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_refusal_output_not_saved(self):
        """Post-LLM refusal-pattern filter: refusal output never reaches Memory.safe_save."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import extract_observations_async

        # Real-looking input passes all pre-LLM guards (length, refusal
        # patterns, whitespace ratio).
        real_input = (
            "Worker finished session sess-real-1234 in 12.4s. "
            "Migrated three tables and deployed the new API server. "
            "All tests pass on green."
        )
        assert len(real_input) >= 50

        # But the LLM mistakenly returns refusal text — this is the bug case
        # Tom flagged in issue #1212 comment IC_kwDOEYGa088AAAABAwQnJw where
        # 'low-content but above-threshold' input still produces refusal.
        refusal_output = "There is no agent session response to analyze."

        mock_llm = AsyncMock(return_value=refusal_output)
        mock_memory = MagicMock()
        mock_memory.safe_save = MagicMock(
            side_effect=AssertionError("Memory.safe_save MUST NOT be called on refusal output")
        )

        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
        ):
            result = await extract_observations_async("sess-post-refusal", real_input)

        assert result == []
        mock_llm.assert_called_once()  # the LLM WAS invoked
        mock_memory.safe_save.assert_not_called()  # but no save occurred

    # --- Issue #1822 Fix 2: trivial-session (turn_count + origin) gate ---

    @pytest.mark.asyncio
    async def test_cli_single_turn_skips_llm_call(self):
        """CLI-origin single-turn session (turn_count=1, not conversational) skips Haiku."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        # Long, real-shaped input that would otherwise pass every pre-LLM guard
        # (this is the /update-style case: ~2000 chars of skill docs).
        update_output = (
            "Running /update: pulled latest changes, synced dependencies, "
            "verified environment, restarted the bridge service. " * 20
        )
        assert len(update_output) >= 500

        mock_llm = AsyncMock(side_effect=AssertionError("trivial CLI session MUST NOT call Haiku"))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await extract_observations_async(
                "sess-cli-1turn",
                update_output,
                turn_count=1,
                is_conversational=False,
            )
        assert result == []
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_conversational_single_turn_still_extracts(self):
        """A substantive single-turn Telegram correction (conversational) STILL extracts."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        correction = (
            "Correction: never use em-dashes in published text — they are a "
            "vanilla-LLM tell. Substitute periods, colons, or parentheses instead."
        )
        assert len(correction) >= 50

        # is_conversational=True must defeat the turn_count<=1 skip → Haiku runs.
        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(
                "sess-tg-1turn",
                correction,
                turn_count=1,
                is_conversational=True,
            )
        assert result == []  # NONE → nothing saved, but the LLM WAS consulted
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_turn_count_is_noop(self):
        """turn_count=None (unknown) never skips — gate is backward-compatible."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        text = (
            "Worker finished session sess-xyz in 9.1s and deployed the new "
            "extraction filters across all three fixes."
        )
        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            # turn_count defaults to None, is_conversational defaults to True
            result = await extract_observations_async("sess-unknown", text)
        assert result == []
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_turn_cli_session_extracts(self):
        """A multi-turn CLI session (turn_count>=2) is NOT skipped even when non-conversational."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        text = (
            "Across the build we extended the refusal vocabulary, added the "
            "trivial-session gate, and shipped the scoping filter."
        )
        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(
                "sess-cli-multi",
                text,
                turn_count=3,
                is_conversational=False,
            )
        assert result == []
        mock_llm.assert_called_once()

    # --- Issue #2040: per-session cumulative cap ---

    _VALID_INPUT = (
        "Worker finished session in 12.4s. Migrated three tables and "
        "deployed the new API server. All tests pass on green."
    )

    @staticmethod
    def _seed_records(agent_id: str, count: int, *, superseded: bool = False) -> None:
        """Seed ``count`` Memory records under ``agent_id`` for cap tests."""
        from models.memory import Memory

        for i in range(count):
            Memory.safe_save(
                agent_id=agent_id,
                project_key="test",
                content=f"Observation number {i} about the session build.",
                importance=1.0,
                superseded_by="cleanup-junk-extraction" if superseded else "",
            )

    @pytest.mark.asyncio
    async def test_session_cap_blocks_after_threshold(self):
        """Session already at the cap (10 non-superseded records) short-circuits."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        session_id = "sess-cap-block"
        self._seed_records(f"extraction-{session_id}", 10)

        mock_llm = AsyncMock(side_effect=AssertionError("cap MUST block the Haiku call"))
        with patch("agent.memory_extraction._llm_call", mock_llm):
            result = await extract_observations_async(session_id, self._VALID_INPUT)

        assert result == []
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_cap_allows_below_threshold(self):
        """Below the cap, extraction proceeds normally (Haiku is called)."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        session_id = "sess-cap-below"
        self._seed_records(f"extraction-{session_id}", 5)

        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(session_id, self._VALID_INPUT)

        assert result == []  # NONE -> nothing saved
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_cap_ignores_superseded(self):
        """Superseded records never count toward the cap (self-healing)."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        session_id = "sess-cap-superseded"
        # cap-many superseded records + zero non-superseded.
        self._seed_records(f"extraction-{session_id}", 10, superseded=True)

        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(session_id, self._VALID_INPUT)

        assert result == []
        mock_llm.assert_called_once()  # superseded records don't block extraction

    @pytest.mark.asyncio
    async def test_session_cap_fail_open_on_query_error(self):
        """A raising Memory.query.filter fails open — extraction still proceeds."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async
        from models.memory import Memory

        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch.object(Memory.query, "filter", side_effect=RuntimeError("redis down")),
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async("sess-cap-query-fail", self._VALID_INPUT)

        assert result == []
        mock_llm.assert_called_once()  # fail-open: extraction proceeded unclamped

    @pytest.mark.asyncio
    async def test_session_cap_disabled_when_zero(self, monkeypatch):
        """Cap of 0 (via settings) disables enforcement entirely."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async
        from config.settings import settings

        monkeypatch.setattr(settings.features, "memory_extraction_session_cap", 0)

        session_id = "sess-cap-disabled"
        # Well over any positive cap — must NOT block when cap is disabled.
        self._seed_records(f"extraction-{session_id}", 15)

        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(session_id, self._VALID_INPUT)

        assert result == []
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_cap_empty_session_id_is_noop(self):
        """An empty/None session_id builds a degenerate agent_id; cap logic
        is a no-op (count 0) and extraction proceeds without crashing."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async("", self._VALID_INPUT)

        assert result == []
        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_cap_overshoot_batch_clamp(self):
        """The invariant regression test (issue #2040).

        Seeds current_count = cap - 1 (9) non-superseded refusal-shaped
        records, then runs ONE extraction call whose parsed observations
        yield >= per_call_cap (10). Asserts (a) non-superseded records for
        that agent_id end at <= cap (10), NOT 19 -- proving the per-batch
        clamp fired, not just the pre-LLM check -- and (b) feeding the
        resulting record set through the real audit's _layer1_supersede ->
        _layer2_signals produces NO agent-id-cluster candidate.
        """
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async
        from models.memory import Memory

        session_id = "sess-overshoot"
        agent_id = f"extraction-{session_id}"

        # Refusal-shaped so the audit's Layer 1 supersede predicate
        # (_looks_like_refusal) actually claims them -- makes assertion (b)
        # meaningful rather than trivially true (an un-superseded pool never
        # trips the agent-id-cluster signal regardless of size).
        for i in range(9):
            Memory.safe_save(
                agent_id=agent_id,
                project_key="test",
                content=f"there is no agent session response to analyze. seed {i}",
                importance=1.0,
            )

        # raw_text itself must NOT trip the post-LLM refusal filter (it
        # operates on the whole raw_text, not per-observation), so the parsed
        # observations are supplied directly via a patched parser.
        mock_llm = AsyncMock(return_value="DECISION: chose blue-green deployment")
        parsed = [
            (
                f"there is no agent session response to analyze. batch {i}",
                1.0,
                {"category": "decision"},
            )
            for i in range(10)  # >= per_call_cap
        ]

        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("agent.memory_extraction._parse_categorized_observations", return_value=parsed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            await extract_observations_async(session_id, self._VALID_INPUT, project_key="test")

        records = list(Memory.query.filter(agent_id=agent_id))
        non_superseded = [m for m in records if not (m.superseded_by or "")]
        assert len(non_superseded) <= 10, (
            f"expected non-superseded records clamped to <= cap (10), got "
            f"{len(non_superseded)} -- batch clamp did not fire"
        )

        from reflections.memory.memory_quality_audit import (
            _layer1_supersede,
            _layer2_signals,
        )

        superseded_count, _blocked, just_ids, just_agent_ids = _layer1_supersede(records)
        candidates = _layer2_signals(records, just_ids, just_agent_ids)
        cluster_candidates = [
            c for c in candidates if c["signal_name"].startswith("agent-id-cluster")
        ]
        assert cluster_candidates == [], (
            f"agent-id-cluster signal fired ({cluster_candidates}) -- the batch "
            f"clamp allowed an overshoot the audit flags as anomalous "
            f"(superseded_count={superseded_count})"
        )

    def test_session_cap_control_signal_fires_at_11(self):
        """Positive control: a genuine 11-record cluster still trips the
        audit's agent-id-cluster signal (bounds the clamp test above)."""
        from models.memory import Memory
        from reflections.memory.memory_quality_audit import (
            _layer1_supersede,
            _layer2_signals,
        )

        agent_id = "extraction-sess-control-11"
        for i in range(11):
            Memory.safe_save(
                agent_id=agent_id,
                project_key="test",
                content=f"there is no agent session response to analyze. record {i}",
                importance=1.0,
            )

        records = list(Memory.query.filter(agent_id=agent_id))
        _superseded, _blocked, just_ids, just_agent_ids = _layer1_supersede(records)
        candidates = _layer2_signals(records, just_ids, just_agent_ids)
        cluster_candidates = [
            c for c in candidates if c["signal_name"].startswith("agent-id-cluster")
        ]
        assert cluster_candidates != [], "expected a genuine 11-record cluster to trip the signal"

    def test_audit_signal_suppressed_at_cap(self):
        """Seeding exactly cap (10) non-superseded refusal-shaped records and
        running the real audit yields NO agent-id-cluster candidate -- 10 is
        not > AGENT_ID_CLUSTER_THRESHOLD (strictly-greater check)."""
        from models.memory import Memory
        from reflections.memory.memory_quality_audit import (
            _layer1_supersede,
            _layer2_signals,
        )

        agent_id = "extraction-sess-at-cap"
        for i in range(10):
            Memory.safe_save(
                agent_id=agent_id,
                project_key="test",
                content=f"there is no agent session response to analyze. record {i}",
                importance=1.0,
            )

        records = list(Memory.query.filter(agent_id=agent_id))
        _superseded, _blocked, just_ids, just_agent_ids = _layer1_supersede(records)
        candidates = _layer2_signals(records, just_ids, just_agent_ids)
        cluster_candidates = [
            c for c in candidates if c["signal_name"].startswith("agent-id-cluster")
        ]
        assert cluster_candidates == [], "10 superseded records must NOT trip the > 10 threshold"


class TestRefusalLLMComplement:
    """Test the optional LLM refusal-detector complement (issue #1829).

    Wraps (never replaces) the closed-vocab ``_looks_like_refusal`` check on
    the post-LLM extraction path. Gated behind ``MEMORY_REFUSAL_LLM_ENABLED``,
    default-OFF. Fail-open on any classifier error.
    """

    # A genuine-looking primary-extraction payload that passes the closed-vocab
    # refusal check and the "NONE" short-circuit, so extraction reaches the
    # point where the complement would fire if the flag is enabled. JSON-
    # shaped (issue #2201 removed the DECISION:-line-based fallback this
    # used to rely on) so `_parse_categorized_observations` yields ≥1
    # observation via the sanctioned JSON path.
    _PRIMARY_OUTPUT = json.dumps(
        [
            {
                "category": "decision",
                "observation": "chose blue-green deployment over rolling updates "
                "for zero-downtime releases",
            }
        ]
    )

    # Real-looking input passes all three pre-LLM guards (length, refusal
    # patterns, whitespace ratio) — copied from
    # TestRunPostSessionExtraction.test_refusal_output_not_saved.
    _REAL_INPUT = (
        "Worker finished session sess-real-1234 in 12.4s. "
        "Migrated three tables and deployed the new API server. "
        "All tests pass on green."
    )

    @pytest.mark.asyncio
    async def test_flag_off_complement_never_invoked(self, monkeypatch):
        """Flag OFF (default): exactly one Haiku call, complement never fires."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        monkeypatch.delenv("MEMORY_REFUSAL_LLM_ENABLED", raising=False)

        mock_llm = AsyncMock(return_value=self._PRIMARY_OUTPUT)
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            await extract_observations_async("sess-flag-off", self._REAL_INPUT, project_key="test")

        mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_on_refusal_verdict_returns_empty(self, monkeypatch):
        """Flag ON + complement returns REFUSAL: extraction returns []."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        mock_llm = AsyncMock(side_effect=[self._PRIMARY_OUTPUT, "REFUSAL"])
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(
                "sess-flag-on-refusal", self._REAL_INPUT, project_key="test"
            )

        assert result == []
        assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_flag_on_content_verdict_saves_observations(self, monkeypatch):
        """Flag ON + complement returns CONTENT: observations are saved."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import extract_observations_async

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        mock_llm = AsyncMock(side_effect=[self._PRIMARY_OUTPUT, "CONTENT"])
        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
        ):
            result = await extract_observations_async(
                "sess-flag-on-content", self._REAL_INPUT, project_key="test"
            )

        assert result != []
        assert mock_llm.call_count == 2
        mock_memory.safe_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_on_complement_timeout_fails_open(self, monkeypatch):
        """Flag ON + complement raises TimeoutError: fail-open, still saves,
        AND _record_extraction_error is invoked."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import extract_observations_async

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        mock_llm = AsyncMock(side_effect=[self._PRIMARY_OUTPUT, TimeoutError()])
        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")
        mock_record_error = MagicMock()

        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
            patch("agent.memory_extraction._record_extraction_error", mock_record_error),
        ):
            result = await extract_observations_async(
                "sess-flag-on-timeout", self._REAL_INPUT, project_key="test"
            )

        assert result != []  # fail-open: observations still saved
        assert mock_llm.call_count == 2
        mock_record_error.assert_called_once()
        assert mock_record_error.call_args[0][0] == "TimeoutError"

    @pytest.mark.asyncio
    async def test_flag_on_complement_generic_exception_fails_open(self, monkeypatch):
        """Flag ON + complement raises a generic Exception: fail-open, still
        saves, AND _record_extraction_error is invoked with the class name."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import extract_observations_async

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        mock_llm = AsyncMock(side_effect=[self._PRIMARY_OUTPUT, Exception("boom")])
        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")
        mock_record_error = MagicMock()

        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
            patch("agent.memory_extraction._record_extraction_error", mock_record_error),
        ):
            result = await extract_observations_async(
                "sess-flag-on-exception", self._REAL_INPUT, project_key="test"
            )

        assert result != []  # fail-open: observations still saved
        assert mock_llm.call_count == 2
        mock_record_error.assert_called_once()
        assert mock_record_error.call_args[0][0] == "Exception"

    @pytest.mark.asyncio
    async def test_flag_on_empty_extraction_never_reaches_complement(self, monkeypatch):
        """Flag ON but primary extraction returns NONE: the NONE short-circuit
        happens before the complement, so the complement is never reached."""
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import extract_observations_async

        monkeypatch.setenv("MEMORY_REFUSAL_LLM_ENABLED", "true")

        mock_llm = AsyncMock(return_value="NONE")
        with (
            patch("agent.memory_extraction._llm_call", mock_llm),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await extract_observations_async(
                "sess-flag-on-none", self._REAL_INPUT, project_key="test"
            )

        assert result == []
        mock_llm.assert_called_once()


class TestParseCategorizedObservations:
    """Test agent/memory_extraction.py _parse_categorized_observations()."""

    @pytest.mark.parametrize("category", ["correction", "decision", "pattern", "surprise"])
    def test_json_category_maps_to_correct_importance(self, category):
        """Category -> importance mapping, exercised via the sanctioned JSON
        path. Issue #2201 removed the line-based `CATEGORY: text` fallback
        that used to be the only place these mappings were tested -- the
        mapping itself (CATEGORY_IMPORTANCE) is unchanged, only how an
        LLM's raw text reaches it (JSON only, now).
        """
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": category,
                    "observation": f"a durable {category} observation about the deploy pipeline",
                }
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == CATEGORY_IMPORTANCE[category]

    def test_json_array_parses_multiple_items_in_order(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "correction",
                    "observation": "Redis SCAN is preferred over KEYS in production",
                },
                {
                    "category": "decision",
                    "observation": "chose ContextAssembler for memory search over raw queries",
                },
                {
                    "category": "pattern",
                    "observation": "all models use safe_save as their primary entry point",
                },
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 3
        assert result[0][1] == CATEGORY_IMPORTANCE["correction"]
        assert result[1][1] == CATEGORY_IMPORTANCE["decision"]
        assert result[2][1] == CATEGORY_IMPORTANCE["pattern"]

    def test_json_category_matching_is_case_insensitive(self):
        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "CORRECTION",
                    "observation": "Redis SCAN is preferred over KEYS in production "
                    "for large keyspaces",
                }
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][1] == CATEGORY_IMPORTANCE["correction"]

    # --- Issue #1822 Fix 3: scoping-boilerplate observations dropped ---

    def test_scoping_boilerplate_dropped_json_path(self):
        """An observation echoing session-scoping boilerplate is dropped (JSON path)."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "pattern",
                    "observation": "Valor AI agentic system scoped to isolated session "
                    "contexts (sdlc-local-96) with strict boundary enforcement",
                },
                {
                    "category": "decision",
                    "observation": "Chose blue-green deployment for zero-downtime rollout.",
                },
            ]
        )
        result = _parse_categorized_observations(raw)
        contents = [c for c, _, _ in result]
        assert all("sdlc-local-" not in c for c in contents)
        assert any("blue-green" in c for c in contents)
        assert len(result) == 1

    # --- Issue #2201: the line-splitting fallback is removed entirely.
    # Every one of the three fall-through cases (no JSON-shaped substring
    # found, json.loads raising, or a valid-JSON-parse with zero valid
    # observations) now returns [] and is counted as `fallback_dropped` by
    # the caller (extract_observations_async), never exploded into
    # per-line Memory records. ---

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(
                "The deployment uses blue-green strategy for zero downtime",
                id="plain_prose_no_json_substring",
            ),
            pytest.param(
                "CORRECTION: Redis SCAN is preferred over KEYS in production\n"
                "DECISION: chose ContextAssembler for memory search over raw queries",
                id="category_prefixed_lines_no_json_substring",
            ),
            pytest.param(
                "CORRECTION: Redis SCAN is preferred over KEYS in production\n"
                "Some uncategorized observation that should be dropped",
                id="mixed_categorized_and_uncategorized_lines",
            ),
            pytest.param("CORRECTION: short", id="short_content_after_category_prefix"),
        ],
    )
    def test_no_json_substring_returns_empty(self, raw):
        """Case (1): extract_json_payload finds no JSON-shaped substring."""
        from agent.memory_extraction import _parse_categorized_observations

        assert _parse_categorized_observations(raw) == []

    def test_json_loads_raises_returns_empty(self):
        """Case (2): a JSON-shaped substring is found but json.loads raises."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = '[{"category": "correction", broken json'
        assert _parse_categorized_observations(raw) == []

    def test_json_valid_but_zero_observations_returns_empty(self):
        """Case (3): valid JSON parses but yields zero valid observations."""
        from agent.memory_extraction import _parse_categorized_observations

        # "short" is < 10 chars, so the per-item length filter drops it,
        # leaving `results` empty -- this must NOT fall through to any
        # line-based parsing of raw_text.
        raw = json.dumps([{"category": "correction", "observation": "short"}])
        assert _parse_categorized_observations(raw) == []

    def test_empty_input(self):
        from agent.memory_extraction import _parse_categorized_observations

        assert _parse_categorized_observations("") == []

    def test_none_response(self):
        from agent.memory_extraction import _parse_categorized_observations

        assert _parse_categorized_observations("NONE") == []

    def test_json_array_parsing(self):
        """JSON array input is parsed with full metadata."""
        import json

        from agent.memory_extraction import CATEGORY_IMPORTANCE, _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "correction",
                    "observation": "Redis SCAN is preferred over KEYS in production",
                    "file_paths": ["bridge/telegram_bridge.py"],
                    "tags": ["redis", "performance"],
                }
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        content, importance, metadata = result[0]
        assert "Redis SCAN" in content
        assert importance == CATEGORY_IMPORTANCE["correction"]
        assert metadata["category"] == "correction"
        assert metadata["file_paths"] == ["bridge/telegram_bridge.py"]
        assert metadata["tags"] == ["redis", "performance"]

    def test_json_bare_dict_wrapped_in_list(self):
        """A single JSON object (not array) is handled gracefully."""
        import json

        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            {
                "category": "decision",
                "observation": "chose blue-green deployment over rolling updates",
                "file_paths": [],
                "tags": ["deployment"],
            }
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][2]["category"] == "decision"

    def test_returns_three_tuples(self):
        """All results are (content, importance, metadata) 3-tuples."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "correction",
                    "observation": "Redis SCAN is preferred over KEYS in production "
                    "for large keyspaces",
                }
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert len(result[0]) == 3

    # --- Issue #1212: tolerant JSON extraction + refusal-pattern filter ---

    def test_extracts_json_from_code_fence(self):
        """Code-fenced JSON (```json [...] ```) is extracted, not exploded."""
        from agent.memory_extraction import (
            CATEGORY_IMPORTANCE,
            _parse_categorized_observations,
        )

        raw = (
            "```json\n"
            '[{"category": "correction", '
            '"observation": "Redis SCAN is preferred over KEYS in production for large keyspaces", '
            '"file_paths": ["bridge/x.py"], "tags": ["redis"]}]\n'
            "```"
        )
        result = _parse_categorized_observations(raw)
        # Pre-fix bug: this used to produce 4-5 shrapnel rows from the line
        # fallback. The fix short-circuits to a single 3-tuple.
        assert len(result) == 1, f"Expected 1 observation, got {len(result)}: {result}"
        content, importance, metadata = result[0]
        assert "Redis SCAN" in content
        assert importance == CATEGORY_IMPORTANCE["correction"]
        assert metadata["category"] == "correction"
        assert metadata["tags"] == ["redis"]

    def test_extracts_json_from_prose_preamble(self):
        """JSON with a prose preamble is sliced and parsed."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = (
            "Here are the observations:\n"
            '[{"category": "decision", '
            '"observation": "chose blue-green deployment over rolling updates for zero-downtime", '
            '"file_paths": [], "tags": ["deployment"]}]'
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert result[0][2]["category"] == "decision"

    def test_refusal_text_returns_empty(self):
        """Refusal prose returns [] without explosion to line fallback."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = "There is no agent session response to analyze."
        assert _parse_categorized_observations(raw) == []

    def test_json_shrapnel_line_rejected(self):
        """Single-line JSON-syntax fragments are rejected by the predicate."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = '"tags": ["session-management", "context-handling"]'
        assert _parse_categorized_observations(raw) == []

    def test_json_path_short_circuits_after_extract(self):
        """Code-fenced JSON with 2 items returns exactly 2 tuples (no ghost rows)."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = (
            "```json\n"
            "[\n"
            '  {"category": "correction", '
            '"observation": "Redis SCAN is preferred over KEYS in production", '
            '"file_paths": [], "tags": []},\n'
            '  {"category": "decision", '
            '"observation": "chose blue-green over rolling updates for zero-downtime", '
            '"file_paths": [], "tags": []}\n'
            "]\n"
            "```"
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 2, (
            f"Expected exactly 2 observations (no fallback ghosts), got {len(result)}"
        )

    def test_legitimate_text_with_session_substring(self):
        """Narrowness regression — observations with 'session' / 'no novel' are kept.

        Locks in Risk 1 from the plan: future pattern additions must not
        widen to bare keywords. If this test fails after a pattern edit,
        the editor's addition was too broad.
        """
        from agent.memory_extraction import _looks_like_refusal

        legit = (
            "The dev session ended cleanly with no novel observations to flag — "
            "verified at session_executor.py:805"
        )
        assert _looks_like_refusal(legit) is False, (
            "Legitimate observation that mentions 'session' and 'no novel' must NOT be rejected. "
            "A pattern edit has accidentally widened to bare keywords."
        )

    # --- Fix A (#2016): JSON branch per-record filtering, recurrence of
    # #1497/#1786/#1931. The JSON branch previously applied _is_scoping_boilerplate
    # but not _looks_like_refusal, and fetched/`.lower()`d category before
    # type-guarding observation, letting shrapnel-shaped values and malformed
    # items slip past save-time and get re-flagged by the audit a day later. ---

    def test_json_branch_drops_shrapnel_shaped_observation(self):
        """A JSON item whose observation value is itself JSON-shrapnel-shaped is dropped."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            [
                {"category": "decision", "observation": '"category": "decision"'},
                {
                    "category": "decision",
                    "observation": "chose blue-green deployment over rolling updates",
                },
            ]
        )
        result = _parse_categorized_observations(raw)
        contents = [c for c, _, _ in result]
        assert all('"category"' not in c for c in contents)
        assert any("blue-green" in c for c in contents)
        assert len(result) == 1

    def test_json_branch_drops_refusal_phrase_observation(self):
        """A JSON item whose observation value contains a refusal phrase is dropped.

        Note: a full-phrase refusal match anywhere in raw_text also trips the
        whole-text short-circuit at the top of the function (pre-existing,
        substring-based), so this batch empties entirely rather than dropping
        just the offending item. The per-item filter added by Fix A is the
        defensive belt-and-suspenders layer documented on that short-circuit
        (direct/partial invocations that bypass the whole-text check still get
        filtered). What matters here: the refusal text never survives into
        the result.
        """
        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            [
                {
                    "category": "pattern",
                    "observation": "There is no agent session response to analyze.",
                },
                {
                    "category": "pattern",
                    "observation": "all Popoto models use safe_save as the primary entry point",
                },
            ]
        )
        result = _parse_categorized_observations(raw)
        contents = [c for c, _, _ in result]
        assert all("no agent session" not in c.lower() for c in contents)

    def test_json_branch_skips_non_string_observation_without_raising(self):
        """An item whose observation value is a dict/list is skipped; siblings survive."""
        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            [
                {"category": "decision", "observation": {"nested": "not a string"}},
                {
                    "category": "decision",
                    "observation": "chose blue-green deployment over rolling updates",
                },
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert "blue-green" in result[0][0]

    def test_json_branch_skips_null_category_without_raising(self):
        """An item whose category value is null is skipped without raising; siblings survive.

        This is the re-critique ordering fix: category is fetched and .lower()'d
        BEFORE observation is type-guarded in the original code. A null category
        would raise AttributeError on .lower(), which the surrounding
        except (json.JSONDecodeError, TypeError) does NOT catch — aborting the
        whole batch instead of just skipping the malformed item.
        """
        from agent.memory_extraction import _parse_categorized_observations

        raw = json.dumps(
            [
                {"category": None, "observation": "some observation text that is long enough"},
                {
                    "category": "decision",
                    "observation": "chose blue-green deployment over rolling updates",
                },
            ]
        )
        result = _parse_categorized_observations(raw)
        assert len(result) == 1
        assert "blue-green" in result[0][0]


class TestExtractJsonPayload:
    """Test agent/memory_extraction.py extract_json_payload() (issue #1212)."""

    def test_empty_returns_none(self):
        from agent.memory_extraction import extract_json_payload

        assert extract_json_payload("") is None

    def test_whitespace_returns_none(self):
        from agent.memory_extraction import extract_json_payload

        assert extract_json_payload("   \n\t  ") is None

    def test_garbage_returns_none(self):
        from agent.memory_extraction import extract_json_payload

        assert extract_json_payload("not json at all, just prose") is None

    def test_extracts_array_from_fence(self):
        from agent.memory_extraction import extract_json_payload

        raw = '```json\n[{"a": 1}]\n```'
        assert extract_json_payload(raw) == '[{"a": 1}]'

    def test_extracts_array_from_unlabeled_fence(self):
        from agent.memory_extraction import extract_json_payload

        raw = '```\n[{"a": 1}]\n```'
        assert extract_json_payload(raw) == '[{"a": 1}]'

    def test_extracts_array_with_preamble(self):
        from agent.memory_extraction import extract_json_payload

        raw = 'Here is the result:\n[{"a": 1}]'
        assert extract_json_payload(raw) == '[{"a": 1}]'

    def test_extracts_bare_object(self):
        from agent.memory_extraction import extract_json_payload

        raw = '{"a": 1}'
        assert extract_json_payload(raw) == '{"a": 1}'

    def test_pure_function_no_exceptions(self):
        """extract_json_payload is a pure function — never raises."""
        from agent.memory_extraction import extract_json_payload

        # Various corner cases that should all return None, not raise.
        for bad in ["[", "}", "[{", "{[", "```", "```json"]:
            try:
                result = extract_json_payload(bad)
            except Exception as e:
                raise AssertionError(f"extract_json_payload({bad!r}) raised: {e}") from e
            assert result is None or isinstance(result, str)


class TestLooksLikeRefusal:
    """Test agent/memory_extraction.py _looks_like_refusal() (issue #1212)."""

    def test_empty_returns_false(self):
        from agent.memory_extraction import _looks_like_refusal

        assert _looks_like_refusal("") is False

    def test_whitespace_returns_false(self):
        from agent.memory_extraction import _looks_like_refusal

        assert _looks_like_refusal("   \n  ") is False

    def test_canonical_refusal_returns_true(self):
        from agent.memory_extraction import _looks_like_refusal

        assert _looks_like_refusal("There is no agent session response to analyze.") is True

    def test_rationale_preamble_returns_true(self):
        from agent.memory_extraction import _looks_like_refusal

        raw = "**Rationale:** The response contains no novel observations to extract."
        assert _looks_like_refusal(raw) is True

    def test_json_shrapnel_returns_true(self):
        from agent.memory_extraction import _looks_like_refusal

        assert _looks_like_refusal('"tags": ["session-management", "context-handling"]') is True
        assert _looks_like_refusal('"category": "correction"') is True

    def test_legitimate_observation_returns_false(self):
        """Real-shaped observation must not trigger any pattern."""
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal("The deployment uses blue-green strategy for zero downtime")
            is False
        )

    def test_case_insensitive(self):
        from agent.memory_extraction import _looks_like_refusal

        assert _looks_like_refusal("THERE IS NO AGENT SESSION available") is True


class TestRefusalPatternsNarrowness:
    """Regression test for the upstream/downstream predicate-narrowness invariant.

    ``_looks_like_refusal`` is shared by the extractor (write gate) and the
    memory-quality audit Layer 1 (cleanup gate via direct import — see issue
    #1231 plan). A pattern broadening that quietly rejected legitimate
    observations resembling refusal phrases would silently drop real memory
    content.

    Each case below is a real-shape observation that mentions refusal-adjacent
    phrasing without being a refusal. All must return False.
    """

    def test_observation_about_no_novel_observations_is_not_refusal(self):
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal(
                "Session ended with no novel observations to flag — extractor "
                "ran cleanly and Haiku returned an empty array."
            )
            is False
        )

    def test_observation_mentioning_session_word_is_not_refusal(self):
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal(
                "The session lifecycle has 13 states defined in docs/features/session-lifecycle.md."
            )
            is False
        )

    def test_observation_about_agent_session_field_is_not_refusal(self):
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal(
                "AgentSession.session_type='eng' triggers worktree creation "
                "via worktree_manager.py during enqueue."
            )
            is False
        )

    def test_observation_describing_rationale_field_is_not_refusal(self):
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal(
                "memory-dedup writes superseded_by_rationale alongside "
                "superseded_by so future readers know why a record was merged."
            )
            is False
        )

    def test_observation_about_provided_session_input_is_not_refusal(self):
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal(
                "When the user provides the session ID via reply-to, the "
                "bridge resumes the original session context."
            )
            is False
        )

    def test_quoted_key_in_prose_is_not_refusal(self):
        """Quoted JSON-style key inside English prose must not trip _JSON_SHRAPNEL_RE.

        The regex anchors on ``^"key": ...$`` (full single line). Embedding
        the same text mid-sentence breaks the anchor.
        """
        from agent.memory_extraction import _looks_like_refusal

        assert (
            _looks_like_refusal(
                'The metadata dict carries "category": "correction" alongside '
                "the file_paths list, written by the JSON-path extractor."
            )
            is False
        )

    def test_multi_line_block_starting_with_quoted_key_is_not_refusal(self):
        """Multi-line content where line 1 looks like JSON shrapnel — overall
        block is not a single anchored line and must not match.
        """
        from agent.memory_extraction import _looks_like_refusal

        text = (
            '"category": "correction"\n'
            "This was the recorded category for the post-merge learning record "
            "captured during the #1231 build."
        )
        assert _looks_like_refusal(text) is False


class TestExtendedRefusalPatterns1822:
    """Issue #1822 Fix 1: the 7 new refusal phrasings each return True.

    Each phrase is a representative of one new ``_REFUSAL_PATTERNS`` entry
    (annotated with its originating Memory ID in the source). Haiku rephrased
    its refusal in these distinct ways and they escaped the #1212 vocabulary,
    landing as high-confidence noise records.
    """

    # (memory_id, representative refusal phrasing)
    NEW_REFUSALS = [
        ("0208f60d", "The session response contains only metadata about tool availability."),
        (
            "b0b24ef7",
            "The session response contains only system metadata about agent modes and permissions.",
        ),
        (
            "517ccf5",
            "The session response contains procedural documentation and instructions, "
            "not observations.",
        ),
        (
            "9fd6006a",
            "The response does not contain any substantive observations worth saving.",
        ),
        ("1a572475", "The session response does not contain any extractable signal."),
        ("868869", "There are no substantive observations to extract from this session."),
        ("8f2c9d5c", "The session response appears to contain only setup boilerplate."),
    ]

    @pytest.mark.parametrize("memory_id,phrase", NEW_REFUSALS, ids=[m for m, _ in NEW_REFUSALS])
    def test_new_refusal_phrasing_detected(self, memory_id, phrase):
        from agent.memory_extraction import _looks_like_refusal

        assert _looks_like_refusal(phrase) is True, f"missed refusal from {memory_id}"

    def test_new_patterns_are_full_phrases_not_keywords(self):
        """Narrowness invariant: every new pattern is multi-word (no bare keyword)."""
        from agent.memory_extraction import _REFUSAL_PATTERNS

        for pattern in _REFUSAL_PATTERNS:
            assert " " in pattern or pattern.startswith("**"), (
                f"refusal pattern {pattern!r} is a bare keyword — too broad"
            )


class TestScopingBoilerplate1822:
    """Issue #1822 Fix 3: session-scoping boilerplate detection + narrowness."""

    def test_sdlc_local_marker_detected(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert (
            _is_scoping_boilerplate(
                "Valor AI agentic system scoped to isolated session contexts "
                "(sdlc-local-96) with strict boundary enforcement"
            )
            is True
        )

    def test_scoped_to_isolated_session_marker_detected(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert (
            _is_scoping_boilerplate("This session is scoped to isolated session contexts.") is True
        )

    def test_case_insensitive(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert _is_scoping_boilerplate("SDLC-LOCAL-42 boundary preamble") is True

    def test_empty_returns_false(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert _is_scoping_boilerplate("") is False
        assert _is_scoping_boilerplate("   ") is False


class TestScopingMarkersNarrowness:
    """Issue #1822 Fix 3: legitimate observations mentioning sessions/scope are NOT dropped.

    Mirrors ``TestRefusalPatternsNarrowness``. The markers are narrow by
    construction (only evidenced substrings); an unevidenced marker would
    silently drop real content. All cases below must return False.
    """

    def test_observation_mentioning_session_scope_is_not_boilerplate(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert (
            _is_scoping_boilerplate(
                "Sessions are scoped by Telegram thread ID; reply-to resumes the "
                "original session context."
            )
            is False
        )

    def test_observation_about_local_dev_is_not_boilerplate(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert (
            _is_scoping_boilerplate(
                "Local CLI sessions run via create_local and lack a Telegram origin."
            )
            is False
        )

    def test_observation_mentioning_sdlc_is_not_boilerplate(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        assert (
            _is_scoping_boilerplate(
                "The SDLC pipeline stages are Plan, Critique, Build, Test, Patch, "
                "Review, Docs, Merge."
            )
            is False
        )

    def test_observation_mentioning_boundary_is_not_boilerplate(self):
        from agent.memory_extraction import _is_scoping_boilerplate

        # "scope boundary" was deliberately NOT added as a marker (unconfirmed).
        assert (
            _is_scoping_boilerplate(
                "The turn-count gate sits at the scope boundary of extraction, "
                "before the try block."
            )
            is False
        )


class TestExtractPostMergeLearning:
    """Test agent/memory_extraction.py extract_post_merge_learning()."""

    @pytest.mark.asyncio
    async def test_empty_title_returns_none(self):
        from agent.memory_extraction import extract_post_merge_learning

        result = await extract_post_merge_learning("", "body", "diff")
        assert result is None

    @pytest.mark.asyncio
    async def test_never_crashes(self):
        """Extraction should never raise, regardless of API key availability."""
        from agent.memory_extraction import extract_post_merge_learning

        # Should not raise under any circumstances
        result = await extract_post_merge_learning(
            "Add memory search tool",
            "Implements save/search/inspect/forget",
            "tools/memory_search/__init__.py",
        )
        # Result is either None (no API key / no takeaway) or a dict with memory_id
        assert result is None or (isinstance(result, dict) and "memory_id" in result)

    @pytest.mark.asyncio
    async def test_post_merge_prompt_format(self):
        """Verify the prompt template formats correctly."""
        from agent.memory_extraction import POST_MERGE_EXTRACTION_PROMPT

        formatted = POST_MERGE_EXTRACTION_PROMPT.format(
            title="Add feature X",
            body="Description of the PR",
            diff_summary="file1.py, file2.py",
        )
        assert "Add feature X" in formatted
        assert "Description of the PR" in formatted
        assert "file1.py, file2.py" in formatted

    def test_post_merge_prompt_requests_structured_json(self):
        """Verify the prompt asks for structured JSON with metadata fields."""
        from agent.memory_extraction import POST_MERGE_EXTRACTION_PROMPT

        assert "category" in POST_MERGE_EXTRACTION_PROMPT
        assert "tags" in POST_MERGE_EXTRACTION_PROMPT
        assert "file_paths" in POST_MERGE_EXTRACTION_PROMPT
        assert "JSON" in POST_MERGE_EXTRACTION_PROMPT


class TestPostMergeJsonParsing:
    """Test JSON parsing in extract_post_merge_learning().

    #1925: _llm_call now routes through agent.llm.run_typed. These tests
    mock run_typed directly at its module-level import site in
    agent.memory_extraction, returning an ExtractionResult(text=...) whose
    .text carries the same raw string extract_post_merge_learning's
    json.loads-tolerant parser used to receive from the raw Anthropic
    response -- the parsing logic under test is unchanged.
    """

    @pytest.mark.asyncio
    async def test_json_response_extracts_metadata(self):
        """When Haiku returns JSON, metadata is parsed and passed to safe_save."""
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import ExtractionResult, extract_post_merge_learning

        json_response = json.dumps(
            {
                "observation": "Post-query re-ranking is safer than pre-query filtering",
                "category": "decision",
                "tags": ["memory", "recall"],
                "file_paths": ["agent/memory_hook.py"],
            }
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=json_response))
        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
            patch("config.project_key_resolver.resolve_project_key", return_value="test"),
        ):
            result = await extract_post_merge_learning(
                "Add recall weights", "Description", "agent/memory_hook.py"
            )

        assert result is not None
        # Verify safe_save was called with metadata
        call_kwargs = mock_memory.safe_save.call_args[1]
        assert call_kwargs["metadata"]["category"] == "decision"
        assert call_kwargs["metadata"]["tags"] == ["memory", "recall"]
        assert call_kwargs["metadata"]["file_paths"] == ["agent/memory_hook.py"]

    @pytest.mark.asyncio
    async def test_non_json_response_uses_default_metadata(self):
        """When Haiku returns plain text, default metadata is used."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import ExtractionResult, extract_post_merge_learning

        mock_run_typed = AsyncMock(
            return_value=ExtractionResult(
                text="Post-query re-ranking is safer than pre-query filtering"
            )
        )
        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
            patch("config.project_key_resolver.resolve_project_key", return_value="test"),
        ):
            result = await extract_post_merge_learning(
                "Add recall weights", "Description", "diff summary"
            )

        assert result is not None
        call_kwargs = mock_memory.safe_save.call_args[1]
        assert call_kwargs["metadata"]["category"] == "decision"

    @pytest.mark.asyncio
    async def test_json_short_observation_falls_back_to_raw(self):
        """When JSON observation is too short, falls back to raw text."""
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.memory_extraction import ExtractionResult, extract_post_merge_learning

        json_response = json.dumps(
            {"observation": "short", "category": "pattern", "tags": [], "file_paths": []}
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=json_response))
        mock_memory = MagicMock()
        mock_memory.safe_save.return_value = MagicMock(memory_id="test-id")

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_AGENT", "agent"),
            patch("config.project_key_resolver.resolve_project_key", return_value="test"),
        ):
            result = await extract_post_merge_learning("Add recall weights", "Description", "diff")

        assert result is not None
        # Should have used the raw JSON text since observation was too short
        call_kwargs = mock_memory.safe_save.call_args[1]
        assert json_response[:100] in call_kwargs["content"]


class TestPersistOutcomeMetadata:
    """Test agent/memory_extraction.py _persist_outcome_metadata()."""

    def test_dismissed_increments_count(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert m.metadata["dismissal_count"] == 1
        assert m.metadata["last_outcome"] == "dismissed"
        m.save.assert_called_once()

    def test_acted_resets_dismissal_count(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": 2, "last_outcome": "dismissed"}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"})

        assert m.metadata["dismissal_count"] == 0
        assert m.metadata["last_outcome"] == "acted"

    def test_threshold_breach_decays_importance(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import DISMISSAL_DECAY_THRESHOLD

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        # Should have decayed importance and reset count
        assert m.importance < 2.0
        assert m.metadata["dismissal_count"] == 0

    def test_importance_floor(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import (
            DISMISSAL_DECAY_THRESHOLD,
            MIN_IMPORTANCE_FLOOR,
        )

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1}
        m.importance = 0.1  # already below floor

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert m.importance >= MIN_IMPORTANCE_FLOOR

    def test_save_failure_does_not_crash(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0
        m.save.side_effect = Exception("Redis connection error")

        # Should not raise
        _persist_outcome_metadata([m], {"mem1": "dismissed"})

    def test_none_metadata_defaults_to_empty_dict(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = None
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert m.metadata["dismissal_count"] == 1

    def test_deferred_leaves_dismissal_count_unchanged(self):
        """The 'deferred' outcome (fallback-unavailable or orphaned-sidecar
        resolution) must be a no-op with respect to dismissal_count -- it
        neither resets it (would manufacture a false positive) nor
        increments it (would manufacture a false negative)."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": 2}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "deferred"})

        assert m.metadata["dismissal_count"] == 2
        assert m.metadata["last_outcome"] == "deferred"
        m.save.assert_called_once()


class TestJudgeOutcomesLlm:
    """Test agent/memory_extraction.py _judge_outcomes_llm().

    #1925: _llm_call now routes through agent.llm.run_typed. These tests
    mock run_typed at its module-level import site in agent.memory_extraction
    -- no real network call and no dependence on PydanticAI's internal
    Anthropic tool-calling wire format. Each test still exercises the real
    json.loads-based parsing in _judge_outcomes_llm via ExtractionResult.text.
    """

    @pytest.mark.asyncio
    async def test_parses_valid_llm_response(self):
        import json
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        llm_response = json.dumps(
            [
                {
                    "index": 0,
                    "outcome": "acted",
                    "reasoning": "Response used the deployment strategy.",
                },
                {"index": 1, "outcome": "dismissed", "reasoning": "No relationship found."},
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "use blue-green deployment"), ("key2", "kubernetes config")],
                "We deployed using blue-green strategy.",
            )

        assert result is not None
        assert result["key1"]["outcome"] == "acted"
        assert result["key2"]["outcome"] == "dismissed"
        assert "deployment" in result["key1"]["reasoning"]

    @pytest.mark.asyncio
    async def test_echoed_maps_to_dismissed(self):
        import json
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        llm_response = json.dumps(
            [
                {
                    "index": 0,
                    "outcome": "echoed",
                    "reasoning": "Keywords overlap but no causal link.",
                },
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "redis connection pooling")],
                "Redis connections are managed via pooling.",
            )

        assert result is not None
        assert result["key1"]["outcome"] == "dismissed"

    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self):
        from unittest.mock import AsyncMock, patch

        from agent.llm import LLMCallError
        from agent.memory_extraction import _judge_outcomes_llm

        mock_run_typed = AsyncMock(side_effect=LLMCallError("simulated API failure"))
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("agent.memory_extraction.run_typed", mock_run_typed),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "some thought")],
                "some response",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        from unittest.mock import patch

        from agent.memory_extraction import _judge_outcomes_llm

        with patch("utils.api_keys.get_anthropic_api_key", return_value=None):
            result = await _judge_outcomes_llm(
                [("key1", "some thought")],
                "some response",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fills_missing_thoughts(self):
        """Thoughts not covered by LLM response get dismissed by default."""
        import json
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        # LLM only returns judgment for index 0, not index 1
        llm_response = json.dumps(
            [
                {"index": 0, "outcome": "acted", "reasoning": "Influenced the response."},
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "thought one"), ("key2", "thought two")],
                "response text",
            )

        assert result is not None
        assert result["key1"]["outcome"] == "acted"
        assert result["key2"]["outcome"] == "dismissed"

    @pytest.mark.asyncio
    async def test_caps_at_max_thoughts(self):
        """Only first 5 thoughts are sent to the LLM."""
        import json
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import (
            _OUTCOME_MAX_THOUGHTS,
            ExtractionResult,
            _judge_outcomes_llm,
        )

        # Create 7 thoughts
        thoughts = [(f"key{i}", f"thought number {i} with enough text") for i in range(7)]

        llm_response = json.dumps(
            [
                {"index": i, "outcome": "acted", "reasoning": "yes"}
                for i in range(_OUTCOME_MAX_THOUGHTS)
            ]
        )

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text=llm_response))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(thoughts, "response text")

        assert result is not None
        # Only the first 5 should be in the result
        assert len(result) == _OUTCOME_MAX_THOUGHTS
        assert "key5" not in result
        assert "key6" not in result

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        from unittest.mock import AsyncMock, patch

        from agent.memory_extraction import ExtractionResult, _judge_outcomes_llm

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text="not valid json at all"))
        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        ):
            result = await _judge_outcomes_llm(
                [("key1", "thought")],
                "response",
            )

        assert result is None


class TestComputeActRate:
    """Test agent/memory_extraction.py compute_act_rate()."""

    def test_empty_history(self):
        from agent.memory_extraction import compute_act_rate

        assert compute_act_rate([]) is None

    def test_all_acted(self):
        from agent.memory_extraction import compute_act_rate

        history = [{"outcome": "acted"}, {"outcome": "acted"}]
        assert compute_act_rate(history) == 1.0

    def test_all_dismissed(self):
        from agent.memory_extraction import compute_act_rate

        history = [{"outcome": "dismissed"}, {"outcome": "dismissed"}]
        assert compute_act_rate(history) == 0.0

    def test_mixed(self):
        from agent.memory_extraction import compute_act_rate

        history = [
            {"outcome": "acted"},
            {"outcome": "dismissed"},
            {"outcome": "acted"},
            {"outcome": "dismissed"},
        ]
        assert compute_act_rate(history) == 0.5

    def test_single_entry(self):
        from agent.memory_extraction import compute_act_rate

        assert compute_act_rate([{"outcome": "acted"}]) == 1.0
        assert compute_act_rate([{"outcome": "dismissed"}]) == 0.0


class TestOutcomeHistory:
    """Test outcome_history persistence in _persist_outcome_metadata()."""

    def test_appends_to_outcome_history(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"}, {"mem1": "Response used the strategy"})

        history = m.metadata["outcome_history"]
        assert len(history) == 1
        assert history[0]["outcome"] == "acted"
        assert history[0]["reasoning"] == "Response used the strategy"
        assert "ts" in history[0]

    def test_caps_at_max_history(self):
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import MAX_OUTCOME_HISTORY

        m = MagicMock()
        m.memory_id = "mem1"
        # Pre-fill with MAX entries
        m.metadata = {
            "outcome_history": [
                {"outcome": "dismissed", "reasoning": "", "ts": i}
                for i in range(MAX_OUTCOME_HISTORY)
            ]
        }
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"}, {"mem1": "new entry"})

        history = m.metadata["outcome_history"]
        assert len(history) == MAX_OUTCOME_HISTORY
        # The newest entry should be last
        assert history[-1]["outcome"] == "acted"
        assert history[-1]["reasoning"] == "new entry"
        # The oldest entry (ts=0) should have been dropped
        assert history[0]["ts"] == 1

    def test_backward_compatible_no_history(self):
        """Old memories without outcome_history get it initialized."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"dismissal_count": 1, "last_outcome": "dismissed"}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "dismissed"})

        assert "outcome_history" in m.metadata
        assert len(m.metadata["outcome_history"]) == 1
        assert m.metadata["outcome_history"][0]["outcome"] == "dismissed"

    def test_reasoning_defaults_to_empty_string(self):
        """When no reasoning_map provided, reasoning is empty string."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"})

        history = m.metadata["outcome_history"]
        assert history[0]["reasoning"] == ""

    def test_corrupted_history_gets_reset(self):
        """Non-list outcome_history is replaced with fresh list."""
        from unittest.mock import MagicMock

        from agent.memory_extraction import _persist_outcome_metadata

        m = MagicMock()
        m.memory_id = "mem1"
        m.metadata = {"outcome_history": "corrupted"}
        m.importance = 2.0

        _persist_outcome_metadata([m], {"mem1": "acted"})

        history = m.metadata["outcome_history"]
        assert isinstance(history, list)
        assert len(history) == 1


class TestPersonaPromptContainsIntentionalMemory:
    """Verify the base persona prompt includes intentional memory instructions."""

    def test_persona_has_intentional_memory_section(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "## Intentional Memory" in content

    def test_persona_has_save_examples(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "memory_search save" in content
        assert "importance 8.0" in content or "--importance 8.0" in content

    def test_persona_has_trigger_categories(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "User corrections" in content or "user corrections" in content.lower()
        assert "remember this" in content.lower()
        assert "Architectural decisions" in content or "architectural decisions" in content.lower()

    def test_persona_has_when_not_to_save(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "When NOT to Save" in content

    def test_persona_has_when_to_search(self):
        import pathlib

        persona_path = pathlib.Path("config/personas/segments/work-patterns.md")
        content = persona_path.read_text()
        assert "When to Search" in content
        assert "--category correction" in content
        assert "--tag" in content


# -----------------------------------------------------------------------------
# Hotfix #1055 — event-loop safety guards for async Anthropic extraction.
# -----------------------------------------------------------------------------


class TestEventLoopSafety:
    """Verify memory_extraction never blocks the worker event loop (hotfix #1055).

    #1925: the AsyncAnthropic construction, ``async with`` httpx cleanup,
    outer ``asyncio.wait_for(hard_timeout)``, and shared semaphore slot all
    moved into ``agent.llm.run_typed`` -- ``agent/memory_extraction.py`` no
    longer touches ``anthropic.AsyncAnthropic`` directly, so there is no
    hung-socket mechanism left in *this* module to reproduce with a
    cooperative-hang stub. That mechanism is exercised directly against
    ``run_typed`` in ``tests/unit/test_llm_wrapper.py``
    (``TestHardTimeoutBound``). These tests instead verify the two things
    that are still this module's responsibility: (1) ``_llm_call``
    translates a hard-timeout ``LLMCallError`` back into ``TimeoutError`` so
    every call site's existing ``except TimeoutError:`` branch still fires,
    and (2) each call site's fail-safe default, logging, and analytics
    counter are preserved.
    """

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_extract_observations(self, caplog):
        """extract_observations_async returns [] and logs on a hard-timeout LLMCallError."""
        import logging
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        timeout_error = LLMCallError("run_typed exceeded hard_timeout of 35.0s")
        timeout_error.__cause__ = TimeoutError()
        mock_run_typed = AsyncMock(side_effect=timeout_error)

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_observations_async(
                "sess-test",
                "A" * 200,  # >50 chars to pass the short-circuit guard
                project_key="test-proj",
            )

        assert result == [], "extract_observations_async must return [] on timeout"
        assert any("hard timeout" in rec.message.lower() for rec in caplog.records), (
            "must log WARNING with 'hard timeout' wording"
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_detect_outcomes(self):
        """detect_outcomes_async (via _judge_outcomes_llm) falls back gracefully on timeout."""
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        timeout_error = LLMCallError("run_typed exceeded hard_timeout of 35.0s")
        timeout_error.__cause__ = TimeoutError()
        mock_run_typed = AsyncMock(side_effect=timeout_error)

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            # detect_outcomes_async first tries LLM; on LLM timeout, falls back
            # to bigram. We want to confirm: (a) the TimeoutError does NOT
            # propagate, (b) the counter is recorded, (c) the fallback still
            # returns something sensible.
            result = await ext.detect_outcomes_async(
                [("key1", "some thought content text goes here")],
                "some response text that mentions different topics entirely",
            )

        assert isinstance(result, dict), "detect_outcomes_async must never raise on timeout"
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_hard_timeout_caught_and_logged_post_merge_learning(self, caplog):
        """extract_post_merge_learning returns None and logs on a hard-timeout LLMCallError."""
        import logging
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        caplog.set_level(logging.WARNING, logger="agent.memory_extraction")

        timeout_error = LLMCallError("run_typed exceeded hard_timeout of 35.0s")
        timeout_error.__cause__ = TimeoutError()
        mock_run_typed = AsyncMock(side_effect=timeout_error)

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_post_merge_learning(
                "PR Title",
                "PR body content",
                "diff summary",
            )

        assert result is None, "extract_post_merge_learning must return None on timeout"
        assert any("hard timeout" in rec.message.lower() for rec in caplog.records), (
            "must log WARNING with 'hard timeout' wording"
        )
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "timeouterror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=timeouterror (got {recorded})"

    @pytest.mark.asyncio
    async def test_llm_call_forwards_tightened_constants_to_run_typed(self):
        """_EXTRACTION_SDK_TIMEOUT / _EXTRACTION_HARD_TIMEOUT are read at call
        time (not captured) and forwarded to run_typed -- the double-timeout
        mechanism itself now lives in run_typed (see
        tests/unit/test_llm_wrapper.py::TestHardTimeoutBound); this test
        guards the forwarding, which is still _llm_call's responsibility."""
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.memory_extraction import ExtractionResult

        mock_run_typed = AsyncMock(return_value=ExtractionResult(text="NONE"))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch.object(ext, "_EXTRACTION_SDK_TIMEOUT", 0.05),
            patch.object(ext, "_EXTRACTION_HARD_TIMEOUT", 0.1),
        ):
            await ext._llm_call(
                model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "x"}]
            )

        call_kwargs = mock_run_typed.call_args.kwargs
        assert call_kwargs["sdk_timeout"] == 0.05
        assert call_kwargs["hard_timeout"] == 0.1

    @pytest.mark.asyncio
    async def test_non_timeout_llm_call_error_caught_and_logged(self):
        """A non-timeout LLMCallError (provider error, exhausted schema retry) is
        caught by the outer except Exception and the counter is recorded.

        error_class is now "llmcallerror" rather than the raw SDK exception
        name (e.g. the old "apitimeouterror") -- an accepted analytics-only
        drift from routing every failure through the wrapper's translated
        exception type (see the plan's Rabbit Holes: per-site counters need
        not survive byte-for-byte)."""
        from unittest.mock import AsyncMock, patch

        import agent.memory_extraction as ext
        from agent.llm import LLMCallError

        mock_run_typed = AsyncMock(side_effect=LLMCallError("simulated provider error"))

        recorded = []

        def _stub_record(name, value, tags):
            recorded.append((name, tags))

        with (
            patch("agent.memory_extraction.run_typed", mock_run_typed),
            patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
            patch("analytics.collector.record_metric", side_effect=_stub_record),
        ):
            result = await ext.extract_observations_async(
                "sess-api-timeout",
                "A" * 200,
                project_key="test-proj",
            )

        assert result == [], "a provider error must not crash extract_observations_async"
        assert any(
            name == "memory.extraction.error" and tags.get("error_class") == "llmcallerror"
            for name, tags in recorded
        ), f"must emit memory.extraction.error with error_class=llmcallerror (got {recorded})"

    def test_no_direct_anthropic_client_grep_canary(self):
        """Regression canary: no direct anthropic.Anthropic( or
        anthropic.AsyncAnthropic( construction in memory_extraction -- both
        the sync client (hotfix #1055) and, since #1925, the async client
        construction itself now live exclusively in agent.llm.run_typed."""
        import subprocess

        for pattern, label in (
            ("anthropic\\.Anthropic(", "sync anthropic.Anthropic("),
            ("anthropic\\.AsyncAnthropic(", "direct anthropic.AsyncAnthropic("),
        ):
            result = subprocess.run(
                ["grep", "-n", pattern, "agent/memory_extraction.py"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1, (
                f"No {label} calls allowed in agent/memory_extraction.py — "
                "route through agent.llm.run_typed via _llm_call (see #1925). "
                f"Offending lines:\n{result.stdout}"
            )


def test_extract_post_merge_learning_runs_inside_asyncio_run(monkeypatch):
    """Guards the .claude hook subprocess call path (hotfix #1055, nit 3).

    Hook at .claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()
    calls asyncio.run(extract_post_merge_learning(...)) inside a short-lived
    subprocess. Routing the LLM call through agent.llm.run_typed (#1925)
    must NOT introduce a nested ``asyncio.run()`` (which raises RuntimeError:
    This event loop is already running). This test runs the function via
    asyncio.run with run_typed mocked and asserts no such error is raised.

    See docs/plans/agent_wiki.md:157 for the regression class this guards.
    """
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.memory_extraction import ExtractionResult, extract_post_merge_learning

    json_response = json.dumps(
        {
            "observation": "Use dependency injection for testability in hooks",
            "category": "pattern",
            "tags": ["testing", "hooks"],
            "file_paths": ["hooks/example.py"],
        }
    )

    # Also mock Memory.safe_save so we don't touch Redis from a subprocess-like test
    mock_memory_module = MagicMock()
    mock_memory_module.safe_save.return_value = MagicMock(memory_id="mock-mem-id")

    with (
        patch(
            "agent.memory_extraction.run_typed",
            AsyncMock(return_value=ExtractionResult(text=json_response)),
        ),
        patch("utils.api_keys.get_anthropic_api_key", return_value="fake-key"),
        patch("models.memory.Memory", mock_memory_module),
        patch("models.memory.SOURCE_AGENT", "agent"),
    ):
        # asyncio.run is the entry point used by .claude/hooks/hook_utils/memory_bridge.py.
        # If run_typed's internal event-loop usage nested inside asyncio.run, this
        # would raise "RuntimeError: This event loop is already running".
        result = asyncio.run(
            extract_post_merge_learning(
                "PR title",
                "PR body content longer than twenty chars",
                "files_changed.py",
            )
        )

    # Result may be a dict (memory saved) or None — the critical assertion is that
    # asyncio.run did not raise. Accept either outcome.
    assert result is None or isinstance(result, dict)


def test_session_cap_default_within_audit_threshold():
    """Invariant guard (issue #2040): the shipped default must stay
    <= AGENT_ID_CLUSTER_THRESHOLD or the audit's agent-id-cluster signal
    re-arms. Fails loudly if a future bump raises the cap above the
    threshold."""
    from config.settings import Settings
    from reflections.memory.memory_quality_audit import AGENT_ID_CLUSTER_THRESHOLD

    assert Settings().features.memory_extraction_session_cap <= AGENT_ID_CLUSTER_THRESHOLD
