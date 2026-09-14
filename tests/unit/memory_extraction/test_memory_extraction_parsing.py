"""Tests for agent/memory_extraction.py: LLM response parsing.

Covers categorized-observation parsing, JSON payload extraction,
post-merge learning extraction, and the persona-prompt guard.
Split out of the former ``tests/unit/test_memory_extraction.py`` monolith (#2879). The
``memory_extraction`` filename prefix is load-bearing: ``tests/conftest.py``
derives feature markers from the module basename via ``FEATURE_MAP``.
"""

import json

import pytest


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
