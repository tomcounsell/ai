"""Tests for agent/memory_extraction.py: refusal and scoping filters.

Covers the refusal detector, its narrowness guards, and the
scoping-boilerplate filters.
Split out of the former ``tests/unit/test_memory_extraction.py`` monolith (#2879). The
``memory_extraction`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import json

import pytest


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
