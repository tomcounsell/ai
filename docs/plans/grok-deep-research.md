# Grok Deep Research Tool

**Issue:** https://github.com/yudame/cuttlefish/issues/217
**Status:** Planning
**Branch:** `session/grok-deep-research`

## Summary

Add a standalone Grok deep research CLI tool (`apps/podcast/tools/grok_deep_research.py`) and a corresponding Claude Code skill file (`.claude/skills/grok-deep-research/SKILL.md`). The tool calls the xAI API (OpenAI-compatible endpoint at `api.x.ai/v1/chat/completions`) to automate Grok research that is currently manual (copy-paste from `x.com/i/grok`). This is the last remaining research source without CLI automation.

**Explicitly out of scope:** Podcast pipeline integration (no `run_grok_research()` service function in `research.py`, no task step wiring). That is a separate future issue. The tool must be designed with a compatible function signature so integration is trivial later.

## Prior Art

| File | Role |
|------|------|
| `apps/podcast/tools/perplexity_deep_research.py` | Primary reference implementation (cleanest pattern) |
| `apps/podcast/tools/gemini_deep_research.py` | Secondary reference (simpler, no async) |
| `.claude/skills/perplexity-deep-research/SKILL.md` | Skill file template (10-section structure) |
| `apps/podcast/services/research.py` | Shows `p2-grok` artifact naming (DO NOT modify) |
| `apps/podcast/tests/test_gemini_research.py` | Test pattern reference |

## Tasks

### 1. Create `apps/podcast/tools/grok_deep_research.py`

Follow the Perplexity tool as the primary template. The xAI API is OpenAI-compatible, so use `requests` directly (no SDK needed).

- [ ] **API key loading** -- `get_api_key()` function that reads `GROK_API_KEY` from environment, then walks up parent directories checking `.env` files. Same pattern as Perplexity's `get_api_key()`.

- [ ] **Core function** -- `run_grok_research(prompt, timeout=300, verbose=True, log_file=None) -> tuple[str | None, dict]`
  - Calls `POST https://api.x.ai/v1/chat/completions` with model `grok-3`
  - Request body: `{"model": "grok-3", "messages": [{"role": "user", "content": prompt}], "stream": false}`
  - Returns `(content_text, response_dict)` tuple matching Perplexity's signature
  - Retry logic: 3 attempts with exponential backoff (same as Perplexity)

- [ ] **Metadata extraction** -- `extract_metadata(result)` function returning:
  - `timestamp`, `model`, `usage` (prompt_tokens, completion_tokens, total_tokens), `elapsed_time`
  - Citations if the xAI response includes them (structure TBD based on actual API response)

- [ ] **Metadata sidecar** -- `save_metadata(meta, output_path)` writes `.meta.json` next to output file

- [ ] **Error handling** -- `_handle_error_response(response)` with specific messages for:
  - 401 (invalid/expired API key)
  - 429 (rate limit exceeded)
  - 500 (server error)
  - Other status codes (generic error with body)

- [ ] **CLI** -- `argparse`-based with these arguments:
  - Positional `prompt` (nargs="*")
  - `--file` / `-f` -- read prompt from file
  - `--output` / `-o` -- write results to file
  - `--timeout` / `-t` -- timeout in seconds (default: 300)
  - `--quiet` / `-q` -- minimal output
  - `--auto-save` / `--no-auto-save` -- automatic file saving
  - `--log-dir` -- directory for output/log files
  - `--show-cost` -- display cost breakdown (if pricing info available)

- [ ] **Auto-save** -- When no `--output` is specified, auto-save to timestamped files (`grok_output_YYYYMMDD_HHMMSS.md` and `grok_log_YYYYMMDD_HHMMSS.txt`)

- [ ] **Output format** -- Markdown header with date, model, prompt, then `---` separator, then content. Matches Perplexity output format.

**Key differences from Perplexity tool:**
- No async fire-and-poll mode (xAI API does not support it as of now)
- No `--reasoning-effort` flag (not a documented xAI parameter)
- Shorter default timeout (300s vs 600s) since xAI responses are typically faster
- Model: `grok-3` instead of `sonar-deep-research`
- API URL: `https://api.x.ai/v1/chat/completions` (OpenAI-compatible)
- Auth header: `Authorization: Bearer {api_key}` (same as OpenAI format)

### 2. Create `.claude/skills/grok-deep-research/SKILL.md`

Follow the Perplexity skill file structure (10 sections):

- [ ] **Frontmatter** -- name, description
- [ ] **Overview** -- What Grok research provides: real-time developments, recent news, practitioner perspectives, social/regional insights
- [ ] **Prerequisites** -- `GROK_API_KEY` in `.env`, Python 3.x with `requests` and `python-dotenv`
- [ ] **API Key Setup** -- Check command, setup instructions, link to xAI API docs
- [ ] **Complete Automation Workflow** -- Step-by-step: verify key, prepare prompt, run research, monitor, output files
- [ ] **CLI Options** -- Table of all arguments
- [ ] **API Details** -- Endpoint, request/response format
- [ ] **Error Handling** -- Table of errors and solutions
- [ ] **Integration with Podcast Workflow** -- Example commands using `pending-episodes` paths, note that service integration is future work
- [ ] **Comparison Table** -- Add Grok column to the existing Perplexity/Gemini/GPT-Researcher comparison
- [ ] **Best Practices** -- 5-8 tips specific to Grok usage

### 3. Create `apps/podcast/tests/test_grok_research.py`

Follow the `test_gemini_research.py` pattern (mock-based, no live API calls):

- [ ] **Fixtures** -- `podcast`, `episode` fixtures using Django model factories (same pattern as Gemini tests)

- [ ] **Tool-level unit tests** (`TestGrokResearch`):
  - [ ] `test_api_key_missing` -- `get_api_key()` returns None when env var unset, `run_grok_research()` returns `(None, {})`
  - [ ] `test_auth_error_401` -- Mock 401 response, verify returns `(None, {})`
  - [ ] `test_rate_limit_429` -- Mock 429 response, verify returns `(None, {})`
  - [ ] `test_server_error_500` -- Mock 500 response, verify returns `(None, {})`
  - [ ] `test_successful_response` -- Mock 200 response with valid chat completions JSON, verify content extraction and metadata
  - [ ] `test_timeout_handling` -- Mock `requests.exceptions.Timeout`, verify graceful handling with retries

- [ ] **Metadata tests**:
  - [ ] `test_extract_metadata` -- Verify metadata extraction from a sample response dict
  - [ ] `test_save_metadata_creates_sidecar` -- Verify `.meta.json` file creation

### 4. Update `.env.example`

- [ ] Add `GROK_API_KEY=` with comment, in the "AI Integration" section after `ANTHROPIC_VERSION`:
  ```
  # xAI Grok API (for automated Grok research)
  GROK_API_KEY=
  ```

## No-Gos

- **DO NOT** modify `apps/podcast/services/research.py` -- service integration is a separate future issue
- **DO NOT** add async/fire-and-poll mode -- xAI API does not support it; keep the tool synchronous-only
- **DO NOT** use any SDK or library beyond `requests` and `python-dotenv` -- the xAI API is OpenAI-compatible
- **DO NOT** add Django dependencies to the tool -- it must work standalone as a CLI script
- **DO NOT** add `--reasoning-effort` flag -- not a documented xAI API parameter

## Failure Path Test Strategy

All failure paths are tested via mocking (no live API calls):

1. **Missing API key** -- `get_api_key()` returns None -> tool prints error and returns `(None, {})`
2. **Auth failure (401)** -- Mocked response -> specific error message mentioning key regeneration
3. **Rate limit (429)** -- Mocked response -> specific error message about waiting
4. **Server error (500)** -- Mocked response -> specific error message about retrying
5. **Timeout** -- Mocked `requests.exceptions.Timeout` -> retry logic fires, eventually returns `(None, {})`
6. **Malformed response** -- Mock 200 with invalid JSON -> `JSONDecodeError` caught, returns `(None, {})`

## Test Impact

No existing tests affected -- this is a greenfield feature adding a new tool file, a new skill file, and a new test file. No existing code is modified except `.env.example` (adding one line).

## Documentation

- [ ] Create `.claude/skills/grok-deep-research/SKILL.md` (covered in Task 2 above -- this IS the documentation deliverable)
- [ ] Update the comparison table in `.claude/skills/perplexity-deep-research/SKILL.md` to add a Grok column

## Update System

No update system changes required -- this feature adds a standalone CLI tool and skill file with no deployment or update script implications. The only env change (`GROK_API_KEY`) is optional and the tool handles its absence gracefully.

## Agent Integration

No agent integration changes required -- the skill file (`.claude/skills/grok-deep-research/SKILL.md`) is automatically discovered by Claude Code. No MCP server changes, no bridge changes, no `.mcp.json` modifications needed. The tool is invoked directly via the CLI as documented in the skill file.

## Rabbit Holes

- **xAI search/grounding features** -- The xAI API may support web search grounding via tool use. If it does, consider enabling it. If the API docs are unclear or the feature is beta, skip it and use plain chat completions. Do not spend more than 15 minutes investigating this.
- **Cost tracking** -- xAI pricing may not be publicly documented yet. Implement the `calculate_cost()` / `format_cost()` functions with placeholder rates and a TODO comment. Do not block the build on finding exact pricing.
- **Model selection** -- Use `grok-3` as the default model. If the API rejects it, fall back to whatever model the error message suggests. Do not add model selection flags unless the API docs clearly list multiple research-capable models.
