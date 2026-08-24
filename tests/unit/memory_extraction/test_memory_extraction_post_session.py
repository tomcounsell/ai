"""Tests for agent/memory_extraction.py: post-session extraction.
Split out of the former ``tests/unit/test_memory_extraction.py`` monolith (#2879). The
``memory_extraction`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import pytest


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


def test_session_cap_default_within_audit_threshold():
    """Invariant guard (issue #2040): the shipped default must stay
    <= AGENT_ID_CLUSTER_THRESHOLD or the audit's agent-id-cluster signal
    re-arms. Fails loudly if a future bump raises the cap above the
    threshold."""
    from config.settings import Settings
    from reflections.memory.memory_quality_audit import AGENT_ID_CLUSTER_THRESHOLD

    assert Settings().features.memory_extraction_session_cap <= AGENT_ID_CLUSTER_THRESHOLD
