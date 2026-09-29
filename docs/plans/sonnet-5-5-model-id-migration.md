---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-09-29
tracking: https://github.com/tomcounsell/ai/issues/3570
last_comment_id:
---

# Sonnet 5.5 model-ID migration

## Problem

`config/models.py` pins `SONNET = "claude-sonnet-4-5-20250929"`. Claude Sonnet 5.5 (`claude-sonnet-5-5`) is live, and the prompt-level guidance for it already shipped in 10d614a66. The runtime model ID was not moved, because a bare constant swap breaks every direct-API call site that uses it.

**Current behavior:**
Four tools call the model directly: `tools/test_judge`, `tools/documentation`, `tools/image_analysis`, `tools/image_tagging`. Each reads `content[0].text` and sizes `max_tokens` for a reply with no thinking. `test_judge` and `image_tagging` also run `json.loads` on the whole reply. A fifth consumer, `tests/integration/test_resume_reverification_llm.py`, drives `MODEL_REASONING` through `agent.llm.run_typed`. Its Anthropic leg uses PydanticAI's tool-output mode, which sends forced `tool_choice`. Sonnet 5.5 rejects that with a 400 (live probe, see Spike Results).

**Desired outcome:**
`SONNET` / `MODEL_REASONING` point at `claude-sonnet-5-5`, and `OPENROUTER_SONNET` / `MODEL_VISION` at the verified `anthropic/claude-sonnet-5.5`. Every call site that reaches a Sonnet 5.5 model:
- chooses its thinking mode on purpose,
- sizes `max_tokens` for thinking plus the reply,
- extracts text by block type,
- treats a truncated reply as a failure,
- parses the last JSON value.

The registry, alias, and price-table entries all carry the new model.

## Freshness Check

**Baseline commit:** `1defbad24` (main, 2026-09-29)
**Issue filed at:** 2026-09-29T02:16:37Z
**Disposition:** Minor drift. The issue's claims hold. Recon added a fifth consumer and a verified OpenRouter slug, both now written into the issue's `## Recon Summary`.

**File:line references re-verified:**
- `tools/test_judge/__init__.py:135`: `response.content[0].text` (SDK path, `max_tokens=1024`), whole-reply `json.loads` at :169. Still holds.
- `tools/documentation/__init__.py:130`: `result.get("content", [{}])[0].get("text", "")`, `max_tokens` 4096. Still holds.
- `tools/image_analysis/__init__.py:222`: same `content[0]` read, `max_tokens` 2048. Still holds.
- `tools/image_tagging/__init__.py:213`: same `content[0]` read, `max_tokens` 1024, whole-reply `json.loads` at :233. Still holds.
- `tools/paid_inference_meter.py:53`: `PRICE_TABLE` keyed `claude-sonnet-4-5`, exact-key lookup at :301. Still holds.
- `tools/email_cs/agents.py:123`: `tool_choice={"type": "any"}` on `MODEL_FAST`. Still holds.

**Cited sibling issues/PRs re-checked:**
- Commit 10d614a66 (Sonnet 5.5 prompting guidance for agents and skills) touched only `.claude/` prompt files. It changed no runtime model ID.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none. No plan mentions Sonnet 5.5 or `config/models.py` model constants.

## Prior Art

- **PR #3381**: "Fix delisted OPENROUTER_GEMMA4_FREE default, single-source ai-judge, add listing probe". Added `tests/unit/test_models.py`, which checks configured `OPENROUTER_*` ids against the public listing. Only the Gemma default hard-fails; other ids warn. This plan's new `OPENROUTER_SONNET` value is checked by that probe.
- **PR #1153** (issue #1099): added `get_model_context_window` and `_MODEL_ALIASES`. The `sonnet` alias resolves through `SONNET`, so this migration also corrects the context-window lookup for CLI sessions that run `--model sonnet`.
- **Issue #2975**: a nightly regression in `test_resume_reverification_llm.py::test_judge_discriminates_grounded_from_ungrounded` (closed). It was on the same test that this plan's NativeOutput change unblocks, but for an unrelated reason.
- No prior attempt at a Sonnet model-ID migration was found.

## Research

**Queries used:**
- Anthropic `GET /v1/models/claude-sonnet-5-5` (capabilities endpoint, live)
- OpenRouter public listing `https://openrouter.ai/api/v1/models` (live)
- Issue-cited docs: "What's new in Claude Sonnet 5.5" and "Prompting Claude Sonnet 5.5" (platform.claude.com)

**Key findings:**
- `claude-sonnet-5-5` capabilities:
  - `max_input_tokens` is 1,000,000 and `max_tokens` is 128,000.
  - Only `adaptive` thinking is supported. `enabled` is not, so `budget_tokens` cannot be sent.
  - Effort levels are `low|medium|high|xhigh|max`.
  - `structured_outputs` is supported.
  - This sets the `MODEL_INFO` context window to 1,000,000.
- OpenRouter lists `anthropic/claude-sonnet-5.5` with a 1M context at $2 / $10 per Mtoken. That fixes the price-table row and the OpenRouter slug. The listing uses dotted ids, and the current `anthropic/claude-sonnet-4-5-20250929` is not listed; it survives only as an unlisted alias.
- From the prompting guide:
  - Put the JSON last and parse the last complete value.
  - Treat `stop_reason == "max_tokens"` as a failure.
  - Add "Think the problem through before you answer." when a JSON answer needs working out.

## Spike Results

### spike-1: Do the issue's failure modes reproduce against the live API?
- **Assumption**: "Default settings can emit a leading thinking block and consume `max_tokens`, and `thinking: disabled` 400s."
- **Method**: prototype (direct `/v1/messages` calls on `claude-sonnet-5-5`)
- **Finding**:
  - `disabled` returns 400: `"thinking.type.disabled" is not supported ... Use "thinking.type.between_tools"`.
  - `between_tools`, `adaptive` + `output_config.effort`, and `output_config.format` (json_schema) are all accepted.
  - A trivial judge prompt with default settings returns only a text block.
  - A multi-criterion judge prompt returns `[thinking(""), text]`, with 866 output tokens.
  - The same prompt at `max_tokens=300` returns `[thinking]` only, with `stop_reason: max_tokens`.
  - On the SDK path in `test_judge`, `response.content[0].text` on a `ThinkingBlock` raises `AttributeError`, and the generic handler reports it as "Unexpected error".
- **Confidence**: high
- **Impact on plan**: Extraction by block type and rejection of truncated replies are required, not defensive.

### spike-2: Does the OpenRouter fallback behave the same way?
- **Assumption**: "OpenRouter's Sonnet 5.5 returns thinking separately and can truncate."
- **Method**: prototype (OpenRouter chat completions, `anthropic/claude-sonnet-5.5`)
- **Finding**:
  - The slug works, and the response `model` is `anthropic/claude-sonnet-5.5`.
  - Thinking is on by default. It comes back as `message.reasoning`, separate from `content`.
  - At `max_tokens=1024`, 944 completion tokens were used.
  - At `max_tokens=300`, `content` is empty with `finish_reason: "length"` and `native_finish_reason: "max_tokens"`.
- **Confidence**: high
- **Impact on plan**: The OpenRouter path needs the same truncation check (`finish_reason == "length"`) and the same `max_tokens` headroom. Content extraction there is already block-agnostic.

### spike-3: Does `agent.llm.run_typed`'s Anthropic leg work on Sonnet 5.5?
- **Assumption**: "PydanticAI's default output mode is compatible."
- **Method**: prototype (`agent.memory_extraction._llm_call(model="claude-sonnet-5-5")`, then a bare PydanticAI `Agent`)
- **Finding**:
  - The leg fails with 400 `tool_choice: type "tool" and "any" are not supported for this model`.
  - `Agent(AnthropicModel(...), output_type=NativeOutput(R))` succeeds on both `claude-sonnet-5-5` and `claude-haiku-4-5-20251001` (pydantic-ai 2.51.0).
  - The Ollama leg already uses `stack.NativeOutput(output_type)`.
- **Confidence**: high for the tested output type; medium for every production output type (see Risk 1).
- **Impact on plan**: The Anthropic leg moves to `NativeOutput`. That is the only way the leg can serve a Sonnet 5.5 route.

## Data Flow

1. **Entry point**: a caller invokes `judge_test_result` / `generate_docs` / `analyze_image` / `tag_image` (CLI entry points or Python import).
2. **Key selection**: `ANTHROPIC_API_KEY` present means the Anthropic Messages API on `SONNET` / `MODEL_REASONING`. Otherwise the tool uses OpenRouter on `OPENROUTER_SONNET` / `MODEL_VISION`.
3. **Request**: the tool builds the body. New: an explicit `thinking` / `output_config.effort` choice, and `max_tokens` sized for thinking plus the reply.
4. **Reply extraction (new shared helper)**: the helper reads the raw response.
   - On the Anthropic path it raises on `stop_reason == "max_tokens"` and joins only `type == "text"` blocks.
   - On the OpenRouter path it raises on `finish_reason == "length"` and reads `choices[0].message.content`.
5. **Parsing (JSON tools only)**: the helper returns the last complete JSON value in the text, with code fences tolerated.
6. **Output**: the tool's existing result dict. A truncated or unparseable reply becomes the tool's existing `{"error": ...}` shape, with a message naming truncation.

Separately: `run_typed` → router → `agent/llm/backends/anthropic.py::call` → PydanticAI `Agent(AnthropicModel(route.model), output_type=NativeOutput(output_type))` → validated instance.

## Architectural Impact

- **New dependencies**: none. There is one new internal module for reply extraction and parsing.
- **Interface changes**: none public. Tool return shapes are unchanged. Truncation now surfaces as an `error` string where it used to be an empty or garbled result.
- **Coupling**: lower. Four copies of `content[0]` plus fence-stripping become one helper.
- **Data ownership**: unchanged.
- **Reversibility**: easy. The constant values and the leg's `output_type` wrapper are one-line reverts. The helper is additive.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (effort-level choices, if the live smoke disagrees with the defaults below)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Live smoke of each tool on Sonnet 5.5 |
| `OPENROUTER_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | Live smoke of the OpenRouter fallback slug |

## Solution

### Key Elements

- **Model registry (`config/models.py`)**:
  - `SONNET = "claude-sonnet-5-5"`, with the comment updated.
  - `OPENROUTER_SONNET = "anthropic/claude-sonnet-5.5"`, with a comment citing the listing verification.
  - `MODEL_INFO[SONNET]` becomes "Claude Sonnet 5.5" with `context_window: 1_000_000`.
  - `_MODEL_ALIASES["sonnet"]` follows the constant automatically.
  - Delete the unused `SONNET_4` constant ("kept for reference during migration"; no consumers).
- **Reply helper (new `tools/llm_reply.py`)**: one place for the rules this issue asks for.
  - `ReplyTruncated` exception.
  - `anthropic_text(response)`: accepts an SDK `Message` or a raw dict. It joins `text` blocks and raises `ReplyTruncated` on `stop_reason == "max_tokens"`.
  - `openrouter_text(result)`: raises on `finish_reason == "length"` and returns `choices[0].message.content`.
  - `parse_last_json(text)`: strips code fences, then scans candidate `{`/`[` start positions from the end with `json.JSONDecoder().raw_decode`. It returns the last complete value and raises `ValueError` when there is none.
- **Per-site thinking choices** (both paths get the same `max_tokens`):

  | Tool | Anthropic request | `max_tokens` | Why |
  |------|-------------------|--------------|-----|
  | `test_judge` | `thinking: {"type": "adaptive"}`, `output_config: {"effort": "medium"}` | 8192 | Multi-criterion judgment benefits from up-front thinking (spike-1: 866 tokens on a hard case) |
  | `image_tagging` | `thinking: {"type": "adaptive"}`, `output_config: {"effort": "low"}` | 4096 | Structured JSON over a perception task; light thinking, JSON last |
  | `documentation` | `thinking: {"type": "between_tools"}` | 8192 | Prose generation; no multi-step reasoning. 8192 covers OpenRouter, where thinking stays on by default |
  | `image_analysis` | `thinking: {"type": "between_tools"}` | 4096 | Descriptive output; latency matters. Headroom for the OpenRouter default thinking |

- **Prompt tweak for the JSON tools**: replace "Only output valid JSON, nothing else." with an instruction to think the problem through and end the reply with the JSON object. Per the Sonnet 5.5 guide, this is the pattern `parse_last_json` expects.
- **`agent/llm/backends/anthropic.py`**: `agent_kwargs = {"output_type": stack.NativeOutput(output_type)}`, mirroring the Ollama leg, so no forced `tool_choice` is ever sent.
- **Metering**: add `"claude-sonnet-5-5": {"usd_per_mtoken_in": 2.0, "usd_per_mtoken_out": 10.0}` to `PRICE_TABLE`, citing the OpenRouter listing. Bump `PRICE_TABLE_RETRIEVED_AT`. The `claude-sonnet-4-5` row stays, because it is still a correct price for that model.
- **`tools/email_cs/agents.py`**: add a comment at the `tool_choice={"type": "any"}` site. It says forced tool choice is rejected by Sonnet 5.5, and that a move off Haiku needs `tool_choice: auto` plus strict tools and a recheck of the escalation gate.

### Flow

Caller → tool builds request (thinking choice, sized `max_tokens`) → Anthropic or OpenRouter → `llm_reply.*_text` (truncation check, text blocks only) → [JSON tools] `parse_last_json` → existing result dict, or `{"error": "...truncated..."}`

### Technical Approach

- `test_judge` uses the `anthropic` SDK, so `anthropic_text` must read SDK block objects (`block.type == "text"` → `block.text`) as well as dicts. Handle both with attribute-or-key access in one function.
- The Anthropic API version header stays `2023-06-01`. `thinking` and `output_config` are body fields, verified accepted in spike-1.
- Don't send `reasoning` params on OpenRouter. Spike-2 shows the defaults work. Truncation is caught by `finish_reason`.
- `parse_last_json` must handle nested objects: scanning start positions from the end and `raw_decode`-ing each finds the outermost complete value that ends last. It prefers a value whose end reaches the end of the stripped text, and otherwise takes the last one that decodes.
- The `ReplyTruncated` message names the `max_tokens` value, so the `error` string is diagnosable.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `test_judge` has `except Exception as e: return {"error": f"Unexpected error: ..."}`. Add a `ReplyTruncated` branch ahead of it that returns a truncation-specific error. A unit test asserts the error names truncation.
- [ ] `image_tagging` / `image_analysis` / `documentation` broad handlers: truncation surfaces as an explicit `error` value, never as empty content passed downstream.

### Empty/Invalid Input Handling
- [ ] `anthropic_text` with no text blocks (a thinking block only, `stop_reason: end_turn`) returns `""`. Callers keep their existing "No response" error.
- [ ] `parse_last_json` on `""`, whitespace, prose only, or a truncated `{"a": ` raises `ValueError`.
- [ ] `openrouter_text` with empty `choices` raises or returns `""`, matching each tool's existing "No response from model" path.

### Error State Rendering
- [ ] A truncated reply yields `{"error": "Reply truncated at max_tokens=N ..."}` from each tool, covered by a unit test on the helper plus one per JSON tool using a canned response dict.

## Test Impact

- [ ] `tests/unit/test_harness_context_usage_log.py::test_alias_and_full_id_both_resolve[sonnet-claude-sonnet-4-5-20250929]`: UPDATE the parametrized full id to `claude-sonnet-5-5`.
- [ ] `tests/unit/test_llm_backend_anthropic.py`: UPDATE any assertion or fake that relies on tool-output mode (FunctionModel answers) so it answers in native-output form. Existing slot, timeout, and cancel tests keep their assertions.
- [ ] `tests/unit/test_llm_wrapper.py`: UPDATE only if a test inspects the Anthropic leg's `Agent` kwargs. Otherwise unchanged; the leg is faked at the `call` boundary.
- [ ] `tests/unit/test_paid_inference_meter.py`: unchanged. It asserts the `claude-sonnet-4-5` row, which stays. ADD a case for `claude-sonnet-5-5` pricing.
- [ ] `tests/tools/test_test_judge.py`: unchanged assertions. These are live tests on `anthropic_api_key` and now exercise Sonnet 5.5; they must pass.
- [ ] `tests/tools/test_image_analysis.py`: unchanged assertions. These are live tests on `openrouter_api_key` and now exercise `anthropic/claude-sonnet-5.5`; they must pass.
- [ ] `tests/integration/test_resume_reverification_llm.py`: unchanged. It passes once the Anthropic leg uses `NativeOutput` (today it would 400 on Sonnet 5.5).
- [ ] New `tests/unit/test_llm_reply.py`: CREATE (see Success Criteria).

## Rabbit Holes

- **An effort-level eval sweep across all four tools.** The issue raises it as an open question. The live smoke per tool, plus the existing live `tests/tools/test_test_judge.py` cases, is the bar. A tuned sweep is its own project.
- **Structured outputs (`output_config.format`) for the JSON tools.** It works on the direct API (spike-1), but `test_judge`'s `criteria_results` has dynamic keys and the OpenRouter path has no equivalent guarantee. Last-JSON parsing covers both paths with one mechanism.
- **Re-pointing `OPENROUTER_HAIKU` / `OPENROUTER_OPUS` or other unlisted ids.** Those are different models; `test_models.py` already warns on them.
- **The `.opencode/` mirror's `sonnet` mapping.** `scripts/sync_claude_to_opencode.py` → `anthropic/claude-sonnet-4-5` belongs to the OpenCode harness, not the direct API.

## Risks

### Risk 1: `NativeOutput` changes behavior for the production Haiku sites on the Anthropic leg
**Impact:** A site whose output schema uses JSON-schema features that Anthropic structured outputs rejects, or that validates differently in native mode, starts raising `LLMCallError(validation|transport)`. The wrapper's fallback or fail-safe then answers where Haiku answered before.
**Mitigation:**
- The builder enumerates every declared `Backend.ANTHROPIC` site's output type (`agent.llm.tasks.declared_sites()`).
- It runs one live Haiku call per distinct output type through the leg with `NativeOutput`.
- It runs `tests/unit/test_llm_*.py` and the `tests/integration/test_*_llm.py` suites.
- Any output type that fails blocks the build and is reported, never special-cased by model name.

### Risk 2: Chosen effort levels are too low or too high
**Impact:** Judge accuracy drops (too low), or latency and cost rise (too high).
**Mitigation:** Live smoke per tool on a non-trivial input (AC). The existing live `test_test_judge.py` cases, including strict and lenient strictness, must pass. Effort is a single literal per tool, easy to retune.

### Risk 3: Raised `max_tokens` increases worst-case cost
**Impact:** At most 8192 output tokens per call at $10/Mtoken, about $0.08 worst case per call. That is below the Sonnet 4.5 worst case at the old token cap × $15.
**Mitigation:** None needed. Sonnet 5.5 output is cheaper per token than 4.5.

## Race Conditions

No race conditions identified. Every change is a synchronous request/response transformation or a constant. The Anthropic leg's slot, timeout, and cancellation handling are untouched; only the `output_type` wrapper passed to `Agent` changes.

## No-Gos (Out of Scope)

Nothing deferred. Every relevant item is in scope for this plan. The effort sweep, structured outputs, other OpenRouter ids, and the OpenCode mirror are excluded as Rabbit Holes (not needed to meet the acceptance criteria), not deferred work.

## Update System

No update system changes required. The change is constants, one internal helper module, and call-site edits. There are no new dependencies, config files, env keys, or migrations. Running services pick up the new constants on the normal `/update` restart.

## Agent Integration

No agent integration required. The four tools keep their existing entry points: the `pyproject.toml [project.scripts]` CLIs and direct imports, with unchanged signatures and return shapes. `run_typed` callers are unaffected apart from the leg's output mode.

## Documentation

- [ ] Update `docs/features/nonharness-llm-wrapper.md`: the Anthropic leg now passes `stack.NativeOutput(output_type)`. State why (Sonnet 5.5 rejects forced `tool_choice`), next to the existing Ollama-leg rationale.
- [ ] Create `docs/features/direct-api-reply-handling.md`: the `tools/llm_reply.py` contract (text blocks only, truncation is failure, last JSON value) and the per-tool thinking/effort/`max_tokens` table.
- [ ] Add `direct-api-reply-handling.md` to the `docs/features/README.md` index table.
- [ ] Update `docs/features/config-architecture.md` wherever it names the Sonnet model or version.

## Success Criteria

- [ ] `SONNET == "claude-sonnet-5-5"` and `MODEL_REASONING == SONNET`. `MODEL_INFO[SONNET]["name"] == "Claude Sonnet 5.5"` with context window 1,000,000. `get_model_context_window("sonnet") == 1_000_000`.
- [ ] `OPENROUTER_SONNET == "anthropic/claude-sonnet-5.5"`, with a comment citing the listing check. `tests/unit/test_models.py::test_other_openrouter_ids_warn_when_unlisted` emits no warning for it.
- [ ] `PRICE_TABLE["claude-sonnet-5-5"]` is present at $2 / $10.
- [ ] No request in `tools/` or `agent/llm/` sends `thinking: {"type": "disabled"}`, `budget_tokens`, or a forced `tool_choice` to a Sonnet 5.5 model.
- [ ] None of the four tools reads `content[0]`. All go through `tools/llm_reply.py`.
- [ ] `test_judge` and `image_tagging` parse with `parse_last_json` and fail on truncation. `tests/unit/test_llm_reply.py` covers:
  - reasoning prose before the JSON
  - a leading thinking block (dict and SDK-object forms)
  - an Anthropic `stop_reason: max_tokens` reply
  - an OpenRouter `finish_reason: length` reply
  - fenced JSON
  - nested JSON
  - no JSON
- [ ] Each of the four tools runs once live on Sonnet 5.5 with a non-trivial input and returns a correct, complete result, with output recorded in the PR. The OpenRouter fallback runs once for one tool.
- [ ] `tests/integration/test_resume_reverification_llm.py` passes on `MODEL_REASONING = claude-sonnet-5-5`.
- [ ] `tools/email_cs/agents.py` carries the forced-tool-choice incompatibility comment.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models-and-helper)**
  - Name: models-builder
  - Role: `config/models.py`, `tools/llm_reply.py`, the four tools, the price table, and the email_cs comment
  - Agent Type: builder
  - Resume: true

- **Builder (anthropic-leg)**
  - Name: leg-builder
  - Role: `agent/llm/backends/anthropic.py` `NativeOutput` switch plus the Risk 1 live verification across declared Anthropic sites
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: migration-validator
  - Role: verify the acceptance criteria, live smokes, and the grep-based anti-criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: migration-docs
  - Role: documentation tasks
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Reply helper and tests
- **Task ID**: build-reply-helper
- **Depends On**: none
- **Validates**: tests/unit/test_llm_reply.py (create)
- **Informed By**: spike-1 (thinking-only truncated reply), spike-2 (OpenRouter `finish_reason: length`)
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/llm_reply.py` with `ReplyTruncated`, `anthropic_text`, `openrouter_text`, and `parse_last_json` per Technical Approach.
- Create `tests/unit/test_llm_reply.py` covering every case listed in Success Criteria.

### 2. Model registry, call sites, metering
- **Task ID**: build-models
- **Depends On**: build-reply-helper
- **Validates**: tests/unit/test_harness_context_usage_log.py, tests/unit/test_paid_inference_meter.py, tests/unit/test_models.py, tests/tools/test_test_judge.py, tests/tools/test_image_analysis.py
- **Informed By**: spike-1, spike-2, Research (capabilities, listing price)
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `config/models.py` (`SONNET`, `OPENROUTER_SONNET`, `MODEL_INFO`, delete `SONNET_4`, update comments).
- Rewire the four tools: add the per-site thinking and `max_tokens` from the Solution table (both paths), route extraction through `tools/llm_reply.py`, apply `parse_last_json` in `test_judge` and `image_tagging`, adjust the JSON prompt ending, and add a `ReplyTruncated` error branch.
- Add the `claude-sonnet-5-5` row and the new retrieved-at date to `PRICE_TABLE`, plus a meter test case.
- Add the comment in `tools/email_cs/agents.py`.
- Update the `test_harness_context_usage_log.py` parametrization.

### 3. Anthropic leg native output
- **Task ID**: build-anthropic-leg
- **Depends On**: none
- **Validates**: tests/unit/test_llm_backend_anthropic.py, tests/unit/test_llm_wrapper.py, tests/integration/test_resume_reverification_llm.py
- **Informed By**: spike-3
- **Assigned To**: leg-builder
- **Agent Type**: builder
- **Parallel**: true
- Wrap `output_type` in `stack.NativeOutput(...)` in `agent/llm/backends/anthropic.py`, and update the module docstring.
- Enumerate the declared `Backend.ANTHROPIC` sites' output types and run one live Haiku call per distinct type through the leg. Report any failure (Risk 1) rather than special-casing it.
- Update `tests/unit/test_llm_backend_anthropic.py` fakes if they assume tool-output mode.

### 4. Validate
- **Task ID**: validate-migration
- **Depends On**: build-models, build-anthropic-leg
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table.
- Run the live smoke for each tool on Sonnet 5.5 plus one OpenRouter fallback call, and capture the outputs for the PR body.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-migration
- **Assigned To**: migration-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Complete every item in `## Documentation`.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the Verification table and confirm every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reply helper tests | `scripts/pytest-clean.sh tests/unit/test_llm_reply.py -q` | exit code 0 |
| Registry and meter tests | `scripts/pytest-clean.sh tests/unit/test_harness_context_usage_log.py tests/unit/test_paid_inference_meter.py tests/unit/test_llm_backend_anthropic.py tests/unit/test_llm_wrapper.py -q` | exit code 0 |
| Sonnet constant | `.venv/bin/python -c "import config.models as m; print(m.SONNET, m.OPENROUTER_SONNET, m.get_model_context_window('sonnet'))"` | output contains claude-sonnet-5-5 anthropic/claude-sonnet-5.5 1000000 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No content[0] reads in the four tools | `grep -rnE "content\"?,? ?\[\{\}\]\)\[0\]\|content\[0\]" tools/test_judge tools/documentation tools/image_analysis tools/image_tagging \| wc -l` | match count == 0 |
| No disabled thinking or forced tool choice on Sonnet paths | `grep -rnE "\"disabled\"\|budget_tokens" tools/test_judge tools/documentation tools/image_analysis tools/image_tagging agent/llm \| wc -l` | match count == 0 |
| Anthropic leg uses native output | `grep -c "NativeOutput(output_type)" agent/llm/backends/anthropic.py` | output > 0 |
| SONNET_4 removed | `grep -rn "SONNET_4" --include=*.py config tools agent \| wc -l` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The NativeOutput leg switch reaches every Anthropic-backed run_typed site, including 3s no-retry bridge gates (read_the_room RTR_SDK_TIMEOUT=3.0, context_recall sdk_timeout=3.0). Strict structured outputs add first-request grammar-compile latency, and Risk 1 checks validation only. | pending | For each Backend.ANTHROPIC DeclaredSite, time a cold and a warm call with its output type under NativeOutput. A cold call at or above the site's sdk_timeout blocks the build and goes to the PM. Also flag output types with dict[...] fields (strict mode requires additionalProperties false). |
| CONCERN | Risk & Robustness | max_tokens goes from 1024 to 8192 with thinking on, but test_judge keeps its OpenRouter timeout=60 (tools/test_judge/__init__.py:148) and sets no SDK client timeout, so a long thinking reply on that path becomes a timeout error. | pending | Raise timeout=60 to 120 to match the other three tools, and pass timeout=120 to anthropic.Anthropic(...). 8192 is below the SDK non-streaming guard. |
| CONCERN | History & Consistency | The Verification greps use a backslash-escaped pipe inside grep -E, which macOS grep treats as a literal pipe: the content[0] check prints 0 today against 4 known reads, so it passes on the known-bad baseline. The forced-tool_choice anti-criterion has no check. | pending | Use -e alternation: grep -rnE -e 'content"?, ?\[\{\}\]\)\[0\]' -e 'content\[0\]' <four tool dirs> (expect 4 on 1defbad24, then 0). Add -e tool_choice to the disabled/budget_tokens grep over the four tools and agent/llm. |
| CONCERN | History & Consistency | Test Impact says test_llm_wrapper.py is faked at the call boundary, but _install_function_model swaps AnthropicModel under the real leg, and _tool_response answers via info.output_tools[0], which is empty under NativeOutput. test_llm_stack_compat.py and test_llm_stack_degraded_start.py use the same helper and are not listed. | pending | Mark all four FunctionModel test files UPDATE and add them to the Verification pytest row. Under NativeOutput, answer ModelResponse(parts=[TextPart(content=json.dumps(args))]); info.output_tools is [] and info.output_object carries the schema. |
| NIT | Scope & Value | None of the four migrated tools meters spend. PRICE_TABLE is read only via record_receipt from tools/cross_vendor_judge.py, so the new row is harmless but off-path. | pending | Keep it as a one-line addition with its test. |
| NIT | Scope & Value | No production run_typed route uses MODEL_REASONING, so the leg switch is forward capability whose blast radius lands on the Haiku sites. Open Question 2 surfaces this. | pending | Record the PM answer to Open Question 2 in the plan before build. |

---

## Open Questions

1. **Effort defaults.** The plan picks `medium` for `test_judge`, `low` for `image_tagging`, and `between_tools` for `documentation` and `image_analysis`, validated by a live smoke rather than an eval sweep. Is that bar acceptable, or do you want a small scored sweep before merge?
2. **Anthropic leg scope.** The plan switches the `run_typed` Anthropic leg to `NativeOutput` for every model, Haiku production sites included, gated on a live per-output-type check. That is the only way the leg can serve Sonnet 5.5. The narrower alternative is to leave the leg alone and pin the one integration test to Haiku, which leaves `run_typed` unable to reach any Sonnet 5.5 route. The plan recommends the switch.
