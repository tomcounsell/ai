---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/260
last_comment_id:
---

# YouTube Search Tool

## Problem

The agent currently has no way to search YouTube. When a user asks "find me a video about X" or needs to research video content, the agent cannot help. The existing YouTube infrastructure in `tools/link_analysis/` only handles video metadata retrieval for known video IDs — it cannot discover videos by topic.

**Current behavior:**
The agent can fetch metadata for a YouTube video given a direct URL, but cannot search YouTube to find relevant videos by query.

**Desired outcome:**
The agent can search YouTube by query string and return structured results (title, URL, duration, view count, uploader, description) without requiring a Google API key, using the already-installed `yt-dlp` package.

## Freshness Check

**Baseline commit:** `74162e0796f30c8ea002f4f8e15c01765d53d642`
**Issue filed at:** 2026-03-05T13:49:25Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/link_analysis/__init__.py:99` — `get_youtube_video_info()` — still holds, function present at line 99
- `pyproject.toml:13` — `yt-dlp>=2024.1.0` dependency — still holds

**Cited sibling issues/PRs re-checked:**
- #734 — closed 2026-04-06, fixed YouTube transcription — not related to search

**Commits on main since issue was filed (touching referenced files):**
- `92811099` Bump deps: anthropic — irrelevant (pyproject.toml change, not yt-dlp)
- `3accddaa` Remove dead SQLite deps — irrelevant
- Other pyproject.toml changes — all dependency bumps, none touching yt-dlp or link_analysis

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** No drift. The `yt-dlp` dependency and `tools/link_analysis/` code are unchanged since issue filing.

## Prior Art

- **Issue #734 / PR #736**: Fixed YouTube transcription with caption-based primary path — related to YouTube video processing pipeline but not to search functionality. No search was attempted.
- No prior issues or PRs attempted YouTube search functionality.

## Data Flow

1. **Entry point**: User asks agent to search YouTube (e.g., "find YouTube videos about Python async")
2. **CLI invocation**: Agent calls `valor-youtube-search "Python async"` (or `valor-youtube-search --limit 5 "Python async"`)
3. **Search function**: `youtube_search()` in `tools/youtube_search/__init__.py` invokes `yt-dlp --dump-json --flat-playlist "ytsearchN:query"`
4. **yt-dlp**: Queries YouTube's search API internally, returns JSON metadata per result
5. **Output**: Structured results printed to stdout as formatted text (title, URL, duration, views, uploader, description snippet)

## Architectural Impact

- **New dependencies**: None — `yt-dlp` is already installed
- **Interface changes**: New CLI entry point `valor-youtube-search` added to `pyproject.toml`
- **Coupling**: Low — new standalone tool module, no changes to existing code
- **Data ownership**: No change — results are ephemeral, returned to caller
- **Reversibility**: Trivial — remove the tool directory and pyproject.toml entry

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — `yt-dlp` is already a project dependency and requires no API keys.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `yt-dlp` installed | `python -c "import yt_dlp; print(yt_dlp.version.__version__)"` | YouTube search backend |

## Solution

### Key Elements

- **YouTube search module** (`tools/youtube_search/`): Python module that wraps `yt-dlp`'s search capability using the Python API (not subprocess) for reliability
- **CLI entry point** (`valor-youtube-search`): Command-line interface registered in `pyproject.toml` for agent invocation
- **Structured output**: Returns title, URL, duration, view count, uploader, and description snippet per result

### Flow

**Agent receives search request** → calls `valor-youtube-search "query"` → yt-dlp searches YouTube → results parsed → structured text output to stdout

### Technical Approach

- Use `yt-dlp` Python API (`yt_dlp.YoutubeDL`) directly instead of subprocess — more reliable, better error handling, no shell escaping issues
- Use `extract_info()` with `download=False` to get metadata only
- Search URL format: `ytsearchN:query` where N is the result limit (default 5)
- Use `flat_playlist=True` in yt-dlp options to avoid extracting full video info (faster, metadata-only)
- Return results as formatted text for CLI, with a Python API (`youtube_search()` / `youtube_search_sync()`) for programmatic use
- Timeout: 30 seconds per search (consistent with existing `get_youtube_video_info` timeout pattern)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Test that network errors (timeout, DNS failure) return a clear error message to stderr and exit code 1
- [ ] Test that yt-dlp extraction errors (e.g., YouTube blocking) are caught and reported
- No existing `except Exception: pass` blocks in scope — this is greenfield code

### Empty/Invalid Input Handling
- [ ] Empty query string prints usage and exits with code 1
- [ ] Query returning zero results prints "No results found" message
- [ ] Whitespace-only query treated as empty

### Error State Rendering
- [ ] All error paths print to stderr (not stdout) so agent can distinguish errors from results
- [ ] Non-zero exit code on any failure

## Test Impact

No existing tests affected — this is a greenfield feature with no prior test coverage. The new tool is a standalone module that does not modify any existing code or interfaces.

## Rabbit Holes

- **Video downloading**: The tool is search-only. Do not add download capability — that is a separate concern handled by `tools/link_analysis/download_youtube_audio()`
- **Caching search results**: Not worth it for v1. YouTube results change frequently and caching adds complexity
- **Thumbnail URLs / rich media**: Keep output text-based. Image embedding is out of scope
- **Pagination**: yt-dlp's `ytsearchN:` handles result count directly. Do not build cursor-based pagination

## Risks

### Risk 1: YouTube rate limiting / blocking
**Impact:** Search fails intermittently or returns empty results
**Mitigation:** Use `--flat-playlist` for lightweight requests; add clear error messages so the agent can report the issue to the user rather than silently failing

### Risk 2: yt-dlp search format changes
**Impact:** Search breaks after yt-dlp update
**Mitigation:** Pin `yt-dlp>=2024.1.0` (already done); the `ytsearchN:` syntax has been stable for years

## Race Conditions

No race conditions identified — all operations are synchronous, single-threaded, and stateless. Each search is an independent request with no shared mutable state.

## No-Gos (Out of Scope)

- Video downloading or streaming
- Thumbnail/image display
- Search result caching or persistence
- Search filters (date, duration, etc.) — can be added later if needed
- MCP server creation — the CLI entry point is sufficient for agent access (no `mcp_servers/` directory exists in this repo)

## Update System

No update system changes required — `yt-dlp` is already a dependency. The new CLI entry point will be available after `pip install -e .` which is part of the standard update flow.

## Agent Integration

- New CLI entry point `valor-youtube-search` registered in `pyproject.toml` `[project.scripts]` — agent invokes it like existing tools (`valor-search`, `valor-fetch`)
- No MCP server needed — CLI tools are directly callable by the agent via Bash
- No bridge changes needed — this is a standalone tool
- Integration test: verify `valor-youtube-search "test query"` returns structured output with expected fields

## Documentation

- [ ] Create `docs/features/youtube-search.md` describing the YouTube search capability
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add `valor-youtube-search` to the Quick Commands table in `CLAUDE.md`

## Success Criteria

- [ ] `valor-youtube-search "python tutorial"` returns structured results with title, URL, duration, view count, uploader
- [ ] `valor-youtube-search --limit 3 "python tutorial"` limits results to 3
- [ ] Empty query prints usage and exits with code 1
- [ ] Network/extraction errors print to stderr and exit with code 1
- [ ] Unit tests pass covering search function, CLI args, and error handling
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (youtube-search)**
  - Name: search-builder
  - Role: Implement the YouTube search tool module and CLI entry point
  - Agent Type: builder
  - Resume: true

- **Validator (youtube-search)**
  - Name: search-validator
  - Role: Verify the tool works end-to-end and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create YouTube search module
- **Task ID**: build-youtube-search
- **Depends On**: none
- **Validates**: tests/unit/test_youtube_search.py (create)
- **Assigned To**: search-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/youtube_search/__init__.py` with:
  - `youtube_search(query: str, limit: int = 5) -> list[dict]` — async function using `yt_dlp.YoutubeDL` with `extract_info(f"ytsearch{limit}:{query}", download=False)` and `flat_playlist=True`
  - `youtube_search_sync(query: str, limit: int = 5) -> list[dict]` — sync wrapper
  - Each result dict: `{title, url, duration, view_count, uploader, description, video_id, upload_date}`
  - 30-second timeout, clear error handling
- Create `tools/youtube_search/cli.py` with:
  - `main()` CLI entry point: parse `--limit N` flag and positional query argument
  - Print formatted results to stdout
  - Print errors to stderr, exit code 1 on failure
- Add `valor-youtube-search = "tools.youtube_search.cli:main"` to `pyproject.toml` `[project.scripts]`
- Create `tools/youtube_search/manifest.json` per tool standard
- Create `tools/youtube_search/README.md` per tool standard
- Run `pip install -e .` to register the new entry point

### 2. Create tests
- **Task ID**: build-tests
- **Depends On**: build-youtube-search
- **Validates**: tests/unit/test_youtube_search.py
- **Assigned To**: search-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_youtube_search.py` with:
  - `test_search_returns_results` — real search, verify result structure
  - `test_search_limit` — verify limit parameter works
  - `test_search_empty_query` — verify empty query handling
  - `test_search_result_fields` — verify each result has required fields
  - `test_cli_usage_on_empty_args` — verify CLI prints usage on no args
- Mark integration-dependent tests with `@pytest.mark.integration` if they require network

### 3. Validate
- **Task ID**: validate-youtube-search
- **Depends On**: build-tests
- **Assigned To**: search-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `valor-youtube-search "python tutorial"` and verify output structure
- Run `valor-youtube-search --limit 2 "machine learning"` and verify result count
- Run `pytest tests/unit/test_youtube_search.py -v` and verify all pass
- Run `python -m ruff check tools/youtube_search/` and verify lint clean

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-youtube-search
- **Assigned To**: search-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/youtube-search.md`
- Add entry to `docs/features/README.md` index table
- Add `valor-youtube-search` to Quick Commands in `CLAUDE.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: search-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/test_youtube_search.py -v`
- Verify documentation files exist
- Verify CLI entry point works

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_youtube_search.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/youtube_search/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/youtube_search/` | exit code 0 |
| CLI works | `valor-youtube-search "test" 2>&1` | output contains "http" |
| CLI limit flag | `valor-youtube-search --limit 1 "test" 2>&1 \| head -20` | exit code 0 |
| CLI empty args | `valor-youtube-search 2>&1; echo $?` | output contains "Usage" |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions — the issue is well-defined, `yt-dlp` is already a dependency, and the implementation pattern follows existing tool conventions (`valor-search`, `valor-fetch`).
