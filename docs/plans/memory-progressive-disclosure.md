---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-05-01
tracking: https://github.com/tomcounsell/ai/issues/1178
related: https://github.com/tomcounsell/ai/issues/1247
last_comment_id: IC_kwDOEYGa088AAAABAfhwZQ
revision_applied: true
---

# Memory Progressive Disclosure + MCP memory_get / memory_search Tools

Tracks #1178.

## Problem

**Current behavior:**
Every recalled memory is injected as a full-text `<thought>{content}</thought>` block in both
the Claude Code hook path (`.claude/hooks/hook_utils/memory_bridge.py:250`) and the SDK agent
path (`agent/memory_hook.py:273`). Up to `MAX_THOUGHTS=3` full bodies are injected every
`WINDOW_SIZE=3` tool calls. Each body is typically 200–800 tokens, so a single recall cycle
can push 600–2400 tokens of context. Over a long session with many recall windows, this
steadily consumes a significant share of the context budget — especially when the injected
memory turns out not to be relevant to the task at hand.

Additionally, the agent has no way to actively query its own memory mid-task. The only
surface is the passive PostToolUse recall sweep; the rich `python -m tools.memory_search`
CLI with its `search`, `save`, `inspect`, and `forget` commands is entirely invisible to
running Claude sessions because no MCP server wraps it.

**Desired outcome:**
- Default injection is a compact stub: `<thought id="mem_xyz">[category] one-line title</thought>` — no body (~15–30 tokens per stub vs. 200–800 for full body).
- Agent can pull a full body via `memory_get(id)` MCP tool when a stub looks relevant.
- Agent can actively query memory via `memory_search(query, …)` MCP tool at any point.
- Token cost of the recall path drops by ≥5× on a representative conversation.

## Freshness Check

**Baseline commit:** `50552620267f9cfc88fc5316bd0805a63d5da0ab`
**Issue filed at:** 2026-04-26T16:34:51Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `.claude/hooks/hook_utils/memory_bridge.py:304` (issue cited) — the `_format_thought_blocks` helper is now at line 222; full-body injection at line 250. Still injects `<thought>{content}</thought>` — claim holds.
- `agent/memory_hook.py:50-155` (issue cited) — check_and_inject() is at lines 108–285; thought injection loop at line 273. Still full-body. Claim holds; line numbers drifted from drift in PR #1201.
- `config/personas/segments/work-patterns.md:21-36` (issue cited) — subconscious memory section exists and describes current passive `<thought>` pattern. No stub format documented yet. Claim holds.
- `config/mcp_library.json` — confirmed no memory MCP entry. 10 servers registered, none is `memory`. Claim holds.

**Cited sibling issues/PRs re-checked:**
- #1180 (Prefetch memories on UserPromptSubmit) — closed 2026-04-29, merged as PR #1201. This ADDED a prefetch path that fires at session start. It does NOT change the full-body injection format; this issue's work is additive on top of it.
- PR #593 (metadata-aware recall) — merged. Already in codebase; out of scope per issue.
- PR #959 (semantic dedup / `superseded_by`) — merged. Already in codebase; out of scope per issue.
- Issue #627 (memory recall hook performance) — closed 2026-04-02 as PR #864. Fixed import tax and deja-vu noise. Work here does NOT redo that fix.
- Issue #811 (memory project_key isolation) — closed 2026-04-07. Fixed `dm` namespace bug. Unrelated.

**Commits on main since issue was filed (touching referenced files):**
- `5df74838` — refactor: unify project_key resolution into resolve_project_key (#1242). Changed how `_get_project_key()` works in `memory_bridge.py` and `check_and_inject()` in `memory_hook.py`. This is a refactor; does NOT change the thought injection format. The plan's modification targets are unaffected in substance.
- `6a835319` — feat: inline `<private>` tag for memory-ingestion exclusion (#1235). Added exclusion logic to ingestion path. Does not touch thought formatting. Irrelevant.
- `f0aae2b4` — fix: relevance threshold for memory recall (#1220). Tuned `RRF_MIN_SCORE`. Does not touch formatting. Irrelevant.
- `f683cc2f` — feat: prefetch memories on UserPromptSubmit (#1201). Added `prefetch()` function to `memory_bridge.py`. Creates a new call site that also calls `_format_thought_blocks()` — this call site also needs the stub format change.

**Active plans in `docs/plans/` overlapping this area:**
- `memory-hook-performance.md` (status: Ready, tracking #627) — already merged as PR #864. Plan status is stale but the underlying work is done. No overlap with this plan.
- `memory-project-key-isolation.md` (status: Planning, tracking #811) — issue #811 was closed 2026-04-07. Plan is orphaned/stale. No overlap.
- `sdlc-1247.md` (status: Planning, tracking #1247) — **directly relevant**. Consolidates all docs hygiene into a single `reflections/docs_auditor.py` substrate. Three consequences for this plan: (1) the separate "persona-builder" BUILD step for feature docs is eliminated — docs updates are the DOCS SDLC stage's job, and `/do-docs` will use the unified substrate after #1247 ships; (2) `documentation-audit` (currently commits docs directly to main) is removed in Phase 3 of #1247, eliminating the seesaw risk where that reflection could overwrite docs created by this plan; (3) new docs created by this plan (`docs/features/subconscious-memory.md`, `docs/features/claude-code-memory.md`) will be picked up and validated by the unified auditor's rotation once #1247 ships. No hard ordering dependency — the DOCS SDLC stage will work correctly whether #1247 has shipped or not; it just uses a more capable substrate if it has.

**Notes:**
- The prefetch path (PR #1201) added a NEW call site for `_format_thought_blocks()` in `memory_bridge.prefetch()`. The plan must update that call site too — the issue body did not call it out because #1201 post-dated the issue.
- The Memory model has no `name` or `title` field; this plan adds a `title: str | None` field populated asynchronously by a local LLM after save (see **Title Generation** subsection). Stub rendering reads `memory.title` directly — no truncation heuristic in the hot path.

## Prior Art

- **PR #522 (memory search tool)** — Shipped `tools/memory_search/__init__.py` with `search()`, `save()`, `inspect()`, `forget()`. CLI-accessible; no MCP exposure. This plan wraps it into an MCP server.
- **PR #864 (memory hook performance)** — Fixed the 344ms import tax by moving imports inside the function body. Fully shipped. This plan does not touch those optimizations.
- **PR #1201 (prefetch)** — Added UserPromptSubmit prefetch path. Relevant as a new call site for the format change.
- **Issue #620 (dynamic MCP roadmap)** — Closed 2026-04-15. Included "dynamic MCP" as a roadmap item. This plan makes it concrete for memory specifically.
- No prior attempts to add a memory MCP server were found.

## Research

**Queries used:**
- "MCP server Python stdio memory search get tools implementation 2026"
- "Claude Code MCP server progressive disclosure memory stub injection additionalContext 2026"
- "claude-mem progressive disclosure memory stubs additionalContext token reduction"

**Key findings:**
- [claude-mem progressive disclosure docs](https://docs.claude-mem.ai/progressive-disclosure): 3-layer stub→context→detail approach. Layer 1 (index) = compact stub with title + token cost hint = ~50–100 tokens per result. Layer 2 = timeline context. Layer 3 = full body on demand. Official benchmarks: 11–18× savings for code navigation, 4–8× for file comprehension. This validates the ≥5× target in the issue.
- [Model Context Protocol build-server docs](https://modelcontextprotocol.io/docs/develop/build-server): FastMCP `@app.tool()` decorator pattern infers schema from Python type hints. Stdio is the right transport for local Claude Code sessions (client spawns server as child process). `mcp>=1.8.0` is already in `pyproject.toml`.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk): `mcp` package v1.27.0 is installed at `/opt/homebrew/lib/python3.14/site-packages`. No additional installation needed.

These findings confirm:
1. The stub format approach has empirical backing (claude-mem) and realistic ≥5× targets.
2. The MCP server can be built with the already-installed `mcp` SDK using `FastMCP`.
3. Stdio transport is correct for Claude Code integration; the server lives in `mcp_servers/`.

## Data Flow

### Current (full-body injection)
1. **PostToolUse hook** (`post_tool_use.py`) → calls `memory_bridge.recall(session_id, tool_name, tool_input, cwd)`
2. **`recall()`** → accumulates tool buffer, every 3rd call: bloom → BM25+RRF → `_format_thought_blocks()` → full-body `<thought>{content}</thought>`
3. **additionalContext** string → injected into Claude's next system turn
4. **UserPromptSubmit hook** → calls `memory_bridge.prefetch()` → same `_format_thought_blocks()` → full-body
5. **SDK agent path** (`agent/memory_hook.py:check_and_inject()`) → same pattern, in-process, full-body `<thought>{content}</thought>`

### After this change
1. **PostToolUse hook** → `recall()` → `_format_stub_blocks()` → compact `<thought id="mem_xyz">[category] one-line title</thought>`
2. **additionalContext** → Claude sees stub, may call `memory_get("mem_xyz")` via MCP if relevant
3. **`memory_get(id)`** MCP tool → loads `Memory` by `memory_id` → returns `{content, category, tags, importance, metadata}`
4. **`memory_search(query)`** MCP tool → calls `tools.memory_search.search(query, …)` → returns list of stubs
5. **UserPromptSubmit hook** → same stub format (prefetch path)
6. **SDK agent path** (`check_and_inject()`) → same stub format

## Architectural Impact

- **New module**: `mcp_servers/memory_server.py` — self-contained FastMCP server exposing `memory_get` and `memory_search`.
- **New module**: `tools/memory_search/title_generator.py` — async title generation worker calling local Ollama LLM.
- **New registration**: `.claude.json` `mcpServers` block gets a `memory` entry pointing to the new server via `python -m mcp_servers.memory_server`. Verified idempotently on every `/update` run.
- **Schema change**: `Memory` Popoto model gains a `title: str | None` field. Existing records have `title=None` until backfilled by `scripts/backfill_memory_titles.py` or written to by the async worker.
- **Modified**: `_format_thought_blocks()` in `memory_bridge.py` and the inline formatting loop in `memory_hook.py` → replaced/wrapped with `_format_stub_blocks()`.
- **New helper**: `_format_stub_blocks()` (or equivalent name) replaces `_format_thought_blocks()` for the injection path; `_format_thought_blocks()` is retained as a utility for any callers that explicitly need full-body output.
- **Modified**: `models/memory.py::Memory.safe_save()` calls `generate_title_async()` after the underlying save returns. **Per critique B2**, this is the model-level chokepoint covering all 6 writer paths (telegram bridge, hook ingest, post-session extraction, post-merge learning, knowledge indexer, consolidation merges) — not the CLI-only `tools/memory_search/__init__.py::save()`. Hooking at the caller layer would leave ~90% of memories without titles in production.
- **Modified**: `config/settings.py` — adds `OLLAMA_HOST`, `MEMORY_TITLE_MODEL`, `MEMORY_TITLE_TIMEOUT_S`.
- **Modified**: `config/personas/segments/work-patterns.md` — describe the new stub→fetch pattern.
- **New optional system dependency**: Ollama runtime (`brew install ollama` + `ollama pull llama3.2:3b`). Absent → title-gen fails silently, stubs render as category-only.
- **Coupling**: MCP server imports `tools.memory_search.search` and `models.memory.Memory` directly. Both are already available in the project's `sys.path`. Title generator imports `models.memory.Memory` and uses HTTP to Ollama. No new package coupling.
- **Reversibility**: Stub format change can be reverted by restoring `_format_thought_blocks()` calls. MCP server can be deregistered by removing the `.claude.json` entry (the `/update` step then needs to be commented out to prevent re-installation). The `Memory.title` field is additive — leaving it null on revert is harmless. High reversibility.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 — both prior open questions (MCP registration scope, title generation strategy) resolved during plan iteration.
- Review rounds: 1 (code review)

**Scope expansion note:** Resolution of Open Question #2 added a `Memory.title` Popoto field, an async title-generator module, an Ollama dependency, and a one-time backfill script. Resolution of #1 added an idempotent verification step to `scripts/update/run.py`. The Medium appetite still holds because each new piece is independently small and well-contained.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `mcp` SDK installed | `python -c "import mcp; print(mcp.__version__)"` | FastMCP for new server |
| Redis reachable | `python -c "from models.memory import Memory; print('ok')"` | Memory model access in MCP server |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory-progressive-disclosure.md`

## Solution

### Key Elements

- **Stub format**: `<thought id="mem_xyz">[category] one-line title</thought>` — `id` attribute carries the memory_id, `[category]` is metadata.category (or "memory" if absent), one-line title is `memory.title` (populated asynchronously by a local LLM after save). If title is null (race: stub fires before async title-gen completes), emit `<thought id="mem_xyz">[category]</thought>` with no title fragment.
- **`_format_stub_blocks()` helper**: New function in `memory_bridge.py`. Same signature and return contract as `_format_thought_blocks()` (returns `(stubs: list[str], new_entries: list[dict])`). `memory_hook.py` gets an equivalent inline change.
- **`mcp_servers/memory_server.py`**: FastMCP server with two tools — `memory_get(memory_id: str)` returning full content + metadata, and `memory_search(query: str, category: str | None, tag: str | None, limit: int)` returning a list of stubs.
- **Registration**: New entry in `~/.claude.json` `mcpServers` block: `"memory": {"type": "stdio", "command": "python", "args": ["-m", "mcp_servers.memory_server"], "env": {"PYTHONPATH": "/Users/tomcounsell/src/ai"}}`. This is a user-level registration in `~/.claude.json`, not a project-level `.mcp.json`, because the memory server depends on local Redis and should be available in ALL Claude Code sessions on this machine.
- **`config/mcp_library.json`**: Add a `memory` server entry documenting the tool surface for reference (follows existing server catalogue pattern).
- **Persona update**: `config/personas/segments/work-patterns.md` — extend the Subconscious Memory section to describe stub injection + on-demand fetch + active search.

### Flow

**Passive path (PostToolUse):**
Tool call N (multiple of 3) → bloom gate → BM25+RRF → **`_format_stub_blocks()`** → `<thought id="m1">[correction] Don't use raw Redis on Popoto keys</thought>` injected → Claude sees stub → if relevant, calls `memory_get("m1")` → receives full body.

**Active path (MCP tool):**
Claude mid-task → `memory_search("redis deletion pattern")` → MCP server calls `tools.memory_search.search(query, limit=5)` → returns 5 stubs → Claude requests bodies for relevant ones via `memory_get`.

### Technical Approach

1. **`_format_stub_blocks()` in `memory_bridge.py`**: Mirror the signature of `_format_thought_blocks()`. For each record: extract `memory_id`, `metadata.get("category", "memory")`, and `memory.title` (populated by the async title-gen worker — see Title Generation below). Render the **agent-visible `<thought>` block** as `<thought id="{memory_id}">[{category}] {title}</thought>`, or `<thought id="{memory_id}">[{category}]</thought>` if `title` is null. **Critical (per critique B1):** the sidecar `injected[]` entry MUST keep the full `content` string — `{"memory_id": ..., "content": record.content}` — exactly as `_format_thought_blocks()` does today (`memory_bridge.py:251`). The agent-visible `<thought>` is the only thing that becomes a stub; the internal sidecar continues to store full content because `agent/memory_extraction.py::detect_outcomes_async` (called at session end) consumes `injected_thoughts` as `list[tuple[memory_id, full_content]]` and runs LLM-judged + bigram-overlap comparison against the response. Stripping content there would collapse the act/dismissed signal that drives `dismissal_count`, importance decay, and `act_rate` ranking. Leave `_format_thought_blocks()` intact for backward compat — just change the call site from `_format_thought_blocks` to `_format_stub_blocks` in `recall()`, `prefetch()`, and `check_and_inject()`. **No truncation, no string slicing in the hot path.**

2. **Title Generation** (`tools/memory_search/title_generator.py`):
   - `generate_title_async(memory_id: str, content: str) -> None`: fire-and-forget. Spawns a daemon thread (or asyncio task if running in an event loop) that calls the project's canonical local LLM via Ollama HTTP API (`http://localhost:11434/api/generate`) using `OLLAMA_LOCAL_MODEL` (currently `gemma4:e2b` per `config/models.py:112` — same model used by `bridge/routing.py` for work/ignore + terminus classification). Prompt: `"Generate a single descriptive title (max 12 words, no quotes, no period) for this memory: {content}"`. On success, loads the Memory record and saves `memory.title = result.strip()`. On failure (Ollama down, timeout >5s, model error), logs at DEBUG and returns silently — title stays null and stubs render as `[{category}]`.
   - **Critique B2 fix:** Reuse `OLLAMA_LOCAL_MODEL` rather than introducing a forked `MEMORY_TITLE_MODEL` constant. `scripts/update/run.py:751-774` actively prunes superseded models, so a forked default would be uninstalled by the next `/update`. If a future plan needs a different model for titles specifically, add the override there — not here.
   - **Critique C4 fix:** Backfill script must call `agent.memory_extraction.strip_private(content)` before sending to the local LLM, so legacy records that contain `<private>` segments don't leak via the local model invocation.
   - **Critique C1 mitigation (write amplification):** `Memory.title` is a non-indexed scalar field. Writing `title` should NOT trigger BM25 or embedding re-indexing. The build step verifies this — if Popoto's default save behavior re-indexes on any field write, the title-gen worker must use a partial-update path (`Memory.query.filter(memory_id=mid).update(title=t)` if available) or an out-of-band Redis HSET on the model hash. Test: write title 1000× and assert no spike in `bm25:*` or embedding key writes.
   - Hook point: `models.memory.Memory.safe_save()` calls `generate_title_async(memory.memory_id, content)` immediately after the underlying save returns. **B2 fix:** placing the hook at this single chokepoint covers ALL 6 production writer paths (telegram bridge ingest, claude-code hook ingest, post-session memory extraction, post-merge learning, knowledge indexer, memory-dedup consolidation merges) — hooking at `tools/memory_search/__init__.py::save()` would only cover CLI saves, leaving ~90% of memories without titles. The async hook MUST be guarded by `if not getattr(self, 'title', None) and self.content:` so re-saves of existing records (e.g., dedup merges, importance updates) don't re-trigger title generation. Save returns to caller without waiting.
   - Backfill: one-time script `scripts/backfill_memory_titles.py` iterates all `Memory.query.all()` records with `title is None`, calls `strip_private()` then `generate_title_async` for each, sleeps briefly between batches to avoid swamping the local LLM. Run once after deploy; idempotent.
   - Configuration: `OLLAMA_HOST` (default `http://localhost:11434`), `MEMORY_TITLE_TIMEOUT_S` (default `5`) in `config/settings.py`. Model is `OLLAMA_LOCAL_MODEL` from `config/models.py` (no override).
   - The Memory model gains a `title: str | None = None` Popoto field. Existing records have `title=None` until backfilled or the async worker runs.

3. **`mcp_servers/memory_server.py`**: 
   - `mcp = FastMCP("memory")`
   - `@mcp.tool() def memory_get(memory_id: str) -> dict`: loads `Memory.query.filter(memory_id=memory_id)`, returns `{content, category, tags, importance, source, title, metadata}` or `{"error": "not found"}`.
   - `@mcp.tool() def memory_search(query: str, category: str | None = None, tag: str | None = None, limit: int = 5) -> list[dict]`: calls `tools.memory_search.search(query, category=category, tag=tag, limit=limit)` and returns stubs `[{id, category, title, score}]`. No full bodies in search results — agent fetches those via `memory_get`.
   - Entry point: `if __name__ == "__main__": mcp.run()` (stdio transport default).

4. **`.claude.json` registration**: Direct edit to `~/.claude.json` adding the `memory` MCP server. Use `PYTHONPATH` env var so the server can import project modules when invoked by Claude Code. **Verified on every update run** by `scripts/update/run.py` — see Update System section.

5. **`config/mcp_library.json`**: Add entry documenting the memory server tools for reference.

6. **Token-cost benchmark test**: `tests/integration/test_memory_stub_injection.py` — create 3 mock Memory records with ~300-token content and pre-populated titles, run `_format_stub_blocks()` on them, measure token count vs `_format_thought_blocks()`, assert ≥5× reduction using `tiktoken`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_format_stub_blocks()` must handle records with empty `content` (skip the record, same as `_format_thought_blocks()`).
- [ ] `memory_get()` in MCP server must handle: invalid memory_id (not found), Memory import failure (Redis down), unexpected field shapes — each returns `{"error": "..."}` rather than raising.
- [ ] `memory_search()` in MCP server must handle: empty query (return `[]`), `tools.memory_search.search()` exception (return `{"error": "..."}`) — fail-safe not fail-fast per existing pattern.
- [ ] Both MCP tools wrap their bodies in `try/except` and return structured errors, not Python exceptions — MCP protocol requires well-formed responses.
- [ ] `_format_stub_blocks()` fallback: if `metadata` is None or has no `category`, default to `"memory"`.

### Empty/Invalid Input Handling
- [ ] `memory_get("")` → return `{"error": "memory_id required"}`.
- [ ] `memory_search("")` → return `[]` (mirrors `tools.memory_search.search("")` behavior).
- [ ] Record with `title is None` → stub generation falls back to `<thought id="...">[{category}]</thought>` (no title fragment).
- [ ] Record with `content = ""` → `generate_title_async` is still called but the local LLM returns empty/whitespace; title stays None; stub falls back to category-only.
- [ ] Local LLM (Ollama) unreachable → `generate_title_async` fails silently; memory saves succeed; stubs render as category-only until next backfill run.

### Error State Rendering
- [ ] MCP tools return `{"error": str}` on failure — FastMCP serializes this as a valid tool response so the agent sees a description of what went wrong rather than a protocol error.

## Test Impact

- [ ] `tests/unit/test_memory_bridge.py::TestFormatThoughtBlocks` — UPDATE: tests of `_format_thought_blocks()` remain valid (function is kept); add parallel test class `TestFormatStubBlocks` for the new helper. Existing tests of `recall()` and `prefetch()` that assert `"<thought>"` in the result must be updated to assert stub format `<thought id=`.
- [ ] `tests/unit/test_memory_hook.py` (test classes asserting `<thought>` in `check_and_inject()` output) — UPDATE: assert stub format with `id=` attribute instead of full body.
- [ ] `tests/integration/test_memory_prefetch.py` — UPDATE: prefetch now returns stubs; update any assertions on thought content to check stub format.
- [ ] `tests/integration/test_memory_stub_injection.py` — CREATE: new benchmark test asserting ≥5× token reduction.
- [ ] `tests/unit/test_memory_title_generator.py` — CREATE: cover async Ollama call, silent failure on Ollama down, timeout, and empty content.
- [ ] `tests/unit/test_memory_safe_save.py` (or equivalent for `models.memory.Memory.safe_save`) — UPDATE/CREATE: assert `generate_title_async` is invoked exactly once after `Memory.safe_save()` for new records, and NOT invoked for re-saves where `self.title` is already set or `self.content` is empty. Use a mock so the test does not actually hit Ollama. Verify the hook fires for at least 3 of the 6 writer paths (extraction, post-merge learning, hook ingest) via integration test.
- [ ] Existing tests of `scripts/update/run.py` (if any) — UPDATE: assert the new MCP-verification step is idempotent.

## Rabbit Holes

- **Embedding the full body in the stub**: Defeats the purpose. The stub must be compact — no body.
- **Generating titles synchronously during stub injection**: The injection path has a 15ms budget. LLM title generation would blow the latency SLA. Title generation runs **asynchronously at save time** so the title is already on the record by the time stubs render. The injection path only reads `memory.title`.
- **Truncating content to derive a title in the hot path**: Original plan used `content[:80].split(". ")[0]`. Replaced with async LLM-generated `memory.title` field per resolved Open Question #2.
- **Changing the bloom filter, BM25 indexing, or RRF scoring**: Out of scope per the issue. The retrieval pipeline is not touched.
- **Bridge-side memory auto-recall for Telegram**: Out of scope per the issue. Only the Claude Code hook + SDK paths.
- **Versioning the stub format**: The stub format is internal to Claude Code sessions and transient. No versioning needed.
- **Registering the MCP server in `config/mcp_library.json` and then auto-installing it**: The library JSON is a reference catalogue. Actual registration is `~/.claude.json`. Keep them separate.

## Risks

### Risk 1: Stub-only injection reduces agent context enough to miss important memories
**Impact:** Agent never calls `memory_get` even when the stub is relevant — effectively a regression in memory utility.
**Mitigation:** The persona update explicitly tells the agent to check stub IDs and call `memory_get` when a stub looks relevant. Add an integration test that verifies a Claude Code session can retrieve stub content via the MCP tool. Also: the stub includes `[category]` which provides enough signal for the agent to decide whether to fetch.

### Risk 2: `~/.claude.json` modification requires manual step or restart of Claude Code
**Impact:** The MCP server is not available until Claude Code is restarted after first registration.
**Mitigation:** The update script (`scripts/update/run.py`) installs the entry idempotently on every run, so the first `/update` after deploying this feature wires it up automatically. Document the required restart in the plan release notes. The builder verifies MCP tool availability in a live session as the final acceptance test.

### Risk 3: MCP server `PYTHONPATH` env var is brittle across machines
**Impact:** On a different machine (e.g., bridge machine with different user), a hard-coded path breaks.
**Mitigation:** The update-script step resolves the repo root dynamically via `git rev-parse --show-toplevel` and writes the resolved absolute path into `~/.claude.json`. Self-heals across machines automatically.

### Risk 5: Local LLM (Ollama) absent or slow → titles never populate
**Impact:** Stubs render as `[{category}]` only (no title fragment) until Ollama is available. Functional regression vs. truncated-title baseline (less informative stubs).
**Mitigation:** Update script warns (non-fatal) if Ollama is not running. Backfill script can be re-run after Ollama is installed. The stub remains useful even title-less because `[category]` already conveys signal — agent can still decide whether to call `memory_get`. Graceful degradation.

### Risk 6: Async title-gen race — stub renders before title is populated
**Impact:** A memory saved seconds ago may be recalled before the async title-gen worker writes back. First-recall stubs would lack titles.
**Mitigation:** Acceptable. The stub falls back to `[{category}]`-only and the agent can still call `memory_get` for full content. Subsequent recalls (after the worker completes) get the title. No data loss; just temporarily less informative.

### Risk 4: `memory_get()` exposes all Memory fields including internal fields
**Impact:** Minor over-exposure; not a security risk on a local stdio server.
**Mitigation:** Return only the useful fields: `{content, category, tags, importance, source, metadata}`. Exclude Popoto internals (`bm25`, `embedding`, `bloom`, `relevance`, `confidence`).

## Race Conditions

No race conditions identified. The MCP server is a stdio singleton process per Claude Code session; all memory reads are read-only (no writes). The existing Popoto ORM Redis access patterns (thread-safe by construction) apply.

## No-Gos (Out of Scope)

- Telegram bridge auto-recall (separate concern, separate issue)
- Changes to bloom filter, BM25 indexing, or category-weighted re-ranking
- Changes to memory consolidation / `superseded_by`
- Changes to memory creation paths (extraction, post-merge learning, intentional saves)
- `memory_save()` MCP tool — intentional saves already have `python -m tools.memory_search save`; adding a write path via MCP would require careful importance scoring logic and is a separate concern
- Semantic (embedding-based) search in the MCP server — the existing BM25+RRF pipeline is sufficient and fast; embedding search adds latency and cost
- MCP tool for `memory_forget` — destructive operations should not be available in the passive recall path

## Update System

The MCP server registration in `~/.claude.json` is per-machine and per-user. **Verified on every `/update` run** (idempotent self-healing — drift, manual edits, or fresh-machine setup all converge to the correct state).

`scripts/update/run.py` gains a new step (e.g., `Step 4.7: Verify memory MCP registration`). The script's existing CLI surface is `--full | --cron | --verify | --json | --quiet` (no `--dry-run` or `--only` flags exist). The new step runs in **all three modes** (`--full`, `--cron`, `--verify`) — `--verify` is read-only (reports drift without writing); `--full` and `--cron` write the corrected entry if missing.

1. Read `~/.claude.json` (atomic: backup → parse → tmp → rename per **Critique C3** — that file is 5400+ lines of Claude Code state and a partial write would brick all sessions).
2. Check `mcpServers.memory` exists with the correct shape: `{"type": "stdio", "command": "python", "args": ["-m", "mcp_servers.memory_server"], "env": {"PYTHONPATH": "<repo root resolved via git rev-parse --show-toplevel>"}}`.
3. If missing or drifted: in `--full`/`--cron`, write the corrected entry atomically; in `--verify`, log the drift and return non-zero exit only when called via `--verify` directly.
4. Idempotent — no-op if already correct. Failure is logged but does not block the rest of `/update` (memory MCP is convenience, not critical-path).

`scripts/remote-update.sh` calls `scripts/update/run.py`, so this is automatically covered on remote update runs.

The same step optionally pings Ollama (`curl -s http://localhost:11434/api/tags`) and warns (non-fatal) if `llama3.2:3b` is not pulled — the title-gen async worker will fail-silent without it but stubs still render as category-only.

New `mcp_servers/` directory must be included in the `hatch.build.targets.wheel.packages` list in `pyproject.toml` if the package is ever installed as a wheel (currently not the case for local development). For now, the module is available via `PYTHONPATH`.

**New optional dependency:** Ollama (local LLM runtime) for title generation. Ollama is best-installed via Homebrew (`brew install ollama`) — the update script does NOT install it (system-level package). If Ollama is absent, title generation fails silently and stubs render as category-only — graceful degradation, not a hard requirement.

No new pip dependencies are introduced — `mcp>=1.8.0` is already in `pyproject.toml`. Ollama HTTP calls use stdlib `urllib` or existing `httpx` (no new package).

## Agent Integration

This feature IS the agent integration. Specifically:

- **New CLI entry point** (optional): A `valor-memory-server` script could be added to `pyproject.toml [project.scripts]` pointing to `mcp_servers.memory_server:main` for convenience. Not strictly required if Claude Code invokes via `python -m mcp_servers.memory_server`.
- **`~/.claude.json` registration**: The `memory` MCP server entry makes `memory_get` and `memory_search` available as native Claude Code tools in all sessions. The builder must verify these appear in the tool list after restart.
- **Integration test**: `tests/integration/test_memory_mcp_server.py` — spawn the MCP server as a subprocess, make tool calls via the MCP protocol, verify correct responses. Use `mcp.client.stdio.stdio_client` from the SDK.
- **No bridge changes**: The bridge does not need to import or call the new code directly. Memory recall in bridge-spawned sessions happens through the hook and SDK paths already.

## Documentation

All documentation updates are handled by the DOCS SDLC stage via `/do-docs` (which uses the unified substrate from #1247 when available). The builder does NOT write these docs directly.

- [ ] `docs/features/subconscious-memory.md` — add stub injection format, `memory_get`/`memory_search` tool descriptions, progressive disclosure pattern.
- [ ] `docs/features/claude-code-memory.md` — update injection format section, add MCP tool section.
- [ ] `docs/features/README.md` — add/verify index entry for MCP memory tools.
- [ ] `config/personas/segments/work-patterns.md` — extend Subconscious Memory section: stub injection, when to call `memory_get(id)` vs `memory_search(query)`.

## Success Criteria

- [ ] `<thought>` injections in both `memory_bridge.py` (`recall()` and `prefetch()`) and `agent/memory_hook.py` (`check_and_inject()`) use stub format: `<thought id="{memory_id}">[{category}] {title}</thought>`.
- [ ] New `mcp_servers/memory_server.py` exposes `memory_get` and `memory_search` tools via FastMCP stdio.
- [ ] MCP server registered in `~/.claude.json` under key `"memory"` and **verified idempotently on every `/update` run** (re-installs if missing or drifted).
- [ ] `config/mcp_library.json` has a `memory` entry documenting the tools.
- [ ] `Memory` Popoto model has a `title: str | None` field.
- [ ] `tools/memory_search/title_generator.py` exists and is invoked async by `tools.memory_search.save()` after every memory save (caller does not block).
- [ ] `scripts/backfill_memory_titles.py` exists and runs once to populate titles for pre-existing records (idempotent — skips records that already have a title).
- [ ] Agent in a live Claude Code session can call `memory_search` and `memory_get` and receive correctly-shaped responses (verified manually post-restart).
- [ ] `config/personas/segments/work-patterns.md` updated to describe stub → fetch pattern.
- [ ] `tests/integration/test_memory_stub_injection.py` demonstrates ≥5× token reduction via `tiktoken`.
- [ ] All existing tests pass (updated for stub format).
- [ ] Documentation updated for both `subconscious-memory.md` and `claude-code-memory.md`.

## Team Orchestration

### Team Members

- **Builder (stub-format)**
  - Name: stub-builder
  - Role: Implement `_format_stub_blocks()` in `memory_bridge.py` and equivalent in `memory_hook.py`; update all call sites.
  - Agent Type: builder
  - Resume: true

- **Builder (mcp-server)**
  - Name: mcp-builder
  - Role: Implement `mcp_servers/memory_server.py` FastMCP server, register in `~/.claude.json`, add to `config/mcp_library.json`.
  - Agent Type: mcp-specialist
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Write token-benchmark test and MCP integration tests; update affected existing tests.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Run full test suite, verify MCP tool availability in live session.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Add `title` field to Memory model + async title generator
- **Task ID**: build-title-gen
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_title_generator.py` (create)
- **Assigned To**: stub-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `title: str | None = None` field to `models.memory.Memory` Popoto model. **Confirm via test that writing `title` does NOT trigger BM25 or embedding re-indexing (Critique C1).** If it does, switch to a direct Redis HSET on the model hash or use Popoto's partial-update path.
- Create `tools/memory_search/title_generator.py` with `generate_title_async(memory_id: str, content: str) -> None`. Spawns daemon thread (or asyncio task if loop present) calling Ollama HTTP `POST /api/generate` with `OLLAMA_LOCAL_MODEL` from `config/models.py` (currently `gemma4:e2b` — same canonical model used by `bridge/routing.py`). On success, loads Memory by id and saves `title`. On any exception/timeout, logs at DEBUG and returns silently.
- Wire `models/memory.py::Memory.safe_save()` (the model-level chokepoint per B2) to call `generate_title_async(memory.memory_id, content)` after the underlying save. Guard with `if not self.title and self.content:` so re-saves of existing records don't re-trigger title generation. This catches all 6 writer paths automatically.
- Add `OLLAMA_HOST`, `MEMORY_TITLE_TIMEOUT_S` to `config/settings.py`. Do NOT add a `MEMORY_TITLE_MODEL` — reuse `OLLAMA_LOCAL_MODEL`.
- Add `scripts/backfill_memory_titles.py` for one-time backfill (iterates `Memory.query.all()` filter `title is None`, calls `agent.memory_extraction.strip_private()` on content first, then calls `generate_title_async`, brief sleep between batches).

### 2. Implement stub format helper and update injection call sites
- **Task ID**: build-stub-format
- **Depends On**: build-title-gen
- **Validates**: `tests/unit/test_memory_bridge.py`, `tests/unit/test_memory_hook.py`
- **Assigned To**: stub-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_format_stub_blocks(records, exclude_ids, max_results)` to `.claude/hooks/hook_utils/memory_bridge.py` alongside `_format_thought_blocks()`. Reads `record.title` (no truncation/slicing). Renders `<thought id="{memory_id}">[{category}] {title}</thought>` if title is non-empty, else `<thought id="{memory_id}">[{category}]</thought>`.
- Update `recall()` and `prefetch()` in `memory_bridge.py` to call `_format_stub_blocks` instead of `_format_thought_blocks`.
- Update the inline thought-formatting loop in `agent/memory_hook.py:check_and_inject()` to emit stub format.
- Sidecar `injected[]` entries: `{"memory_id": ..., "content": title or ""}` (empty string when title is null) so de-dup works.

### 3. Implement MCP memory server
- **Task ID**: build-mcp-server
- **Depends On**: build-title-gen
- **Validates**: `tests/integration/test_memory_mcp_server.py` (create)
- **Assigned To**: mcp-builder
- **Agent Type**: mcp-specialist
- **Parallel**: true (parallel with build-stub-format)
- Create `mcp_servers/__init__.py` and `mcp_servers/memory_server.py`.
- Implement `memory_get(memory_id: str) -> dict` — loads Memory record by ID, returns `{content, category, tags, importance, source, title, memory_id}` or `{"error": "not found"}`.
- Implement `memory_search(query: str, category: str | None = None, tag: str | None = None, limit: int = 5) -> list[dict]` — calls `tools.memory_search.search()`, returns list of stubs `{id, category, title, score}`.
- Both tools wrapped in `try/except`, fail-silent returning `{"error": ...}`.
- Register in `~/.claude.json` mcpServers: `"memory": {"type": "stdio", "command": "python", "args": ["-m", "mcp_servers.memory_server"], "env": {"PYTHONPATH": "<repo root>"}}`.
- Add `memory` entry to `config/mcp_library.json`.

### 4. Add update-script verification step
- **Task ID**: build-update-step
- **Depends On**: build-mcp-server
- **Validates**: `tests/unit/test_update_run.py` (extend existing test of `scripts/update/run.py`)
- **Assigned To**: stub-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `scripts/update/run.py` with a new step (e.g., `Step 4.7: Verify memory MCP registration`). Runs in `--full`, `--cron`, and `--verify` modes (no `--dry-run`/`--only` flags exist on this script — those flags must NOT be cited).
- The step reads `~/.claude.json` **atomically (backup → parse → tmp → rename)** per Critique C3, checks `mcpServers.memory` exists with the correct shape (resolving `PYTHONPATH` via `git rev-parse --show-toplevel`), and writes the corrected entry if missing or drifted.
- In `--verify` mode: read-only; report drift; exit non-zero only if drift found.
- In `--full` / `--cron` modes: write the corrected entry atomically.
- Idempotent — no-op if already correct. Failure logged but does not block the rest of `/update`.
- Optionally pings Ollama (`http://localhost:11434/api/tags`) and warns (non-fatal) if the canonical model `gemma4:e2b` is not pulled.

### 5. Update test suite for stub format
- **Task ID**: build-tests
- **Depends On**: build-stub-format, build-mcp-server, build-update-step
- **Validates**: `tests/unit/test_memory_bridge.py`, `tests/unit/test_memory_hook.py`, `tests/integration/test_memory_prefetch.py`, `tests/integration/test_memory_stub_injection.py` (create), `tests/integration/test_memory_mcp_server.py` (create), `tests/unit/test_memory_title_generator.py` (create)
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `tests/unit/test_memory_bridge.py`: add `TestFormatStubBlocks` class; update `recall()` and `prefetch()` tests asserting `<thought id=` attribute. Cover both title-present and title-null cases.
- Update `tests/unit/test_memory_hook.py`: update `check_and_inject()` tests asserting stub format.
- Update `tests/integration/test_memory_prefetch.py`: assert stub format in prefetch output.
- Create `tests/unit/test_memory_title_generator.py`: mock Ollama HTTP endpoint; verify `generate_title_async` writes `memory.title`. Cover Ollama-down (silent fail), timeout, and empty-content cases.
- Create `tests/integration/test_memory_stub_injection.py`: token benchmark comparing `_format_stub_blocks()` (with pre-set titles) vs `_format_thought_blocks()` on 3 mock records with ~300-token content each; assert ≥5× token reduction using `tiktoken`.
- Create `tests/integration/test_memory_mcp_server.py`: spawn MCP server subprocess, call `memory_get` and `memory_search` via MCP stdio client, assert correct response shapes including `title` field.

### 4. Documentation (DOCS SDLC stage — not a BUILD task)

Documentation updates (`docs/features/subconscious-memory.md`, `docs/features/claude-code-memory.md`, `docs/features/README.md`, `config/personas/segments/work-patterns.md`) are handled by the DOCS SDLC stage via `/do-docs` after BUILD completes. See the Documentation section above. No builder agent is assigned here — the unified substrate from #1247 (or the current `/do-docs` if #1247 hasn't shipped yet) covers this automatically.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_bridge.py tests/unit/test_memory_hook.py tests/unit/test_memory_title_generator.py tests/integration/test_memory_prefetch.py tests/integration/test_memory_stub_injection.py -v`
- Run `pytest tests/integration/test_memory_mcp_server.py -v`
- Verify MCP server starts without error: `python -m mcp_servers.memory_server --help` or equivalent.
- Run `python scripts/update/run.py --verify` (read-only mode; no `--dry-run` flag exists) and verify the new MCP-verification step reports `memory MCP registration: ok` after a clean install. Then delete the `mcpServers.memory` entry from `~/.claude.json` and run `python scripts/update/run.py --cron`; re-read the JSON to confirm the entry was atomically restored. A second `--cron` run is a no-op.
- Report pass/fail status for each success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_memory_bridge.py tests/unit/test_memory_hook.py tests/unit/test_memory_title_generator.py -x -q` | exit code 0 |
| Stub format in recall | `python -c "from hook_utils.memory_bridge import _format_stub_blocks; print('ok')"` | output contains ok |
| MCP server imports | `python -c "import mcp_servers.memory_server; print('ok')"` | output contains ok |
| Title generator imports | `python -c "from tools.memory_search.title_generator import generate_title_async; print('ok')"` | output contains ok |
| Memory model has `title` | `python -c "from models.memory import Memory; assert 'title' in Memory._fields; print('ok')"` | output contains ok |
| Update step idempotent (read-only check) | `python scripts/update/run.py --verify` | exit code 0; output reports `memory MCP registration: ok` |
| Update step writes when missing | After deleting the entry: `python scripts/update/run.py --cron`; then re-read `~/.claude.json` | entry restored; second run reports no-op |
| MCP entry present | `python -c "import json,os; assert 'memory' in json.load(open(os.path.expanduser('~/.claude.json')))['mcpServers']; print('ok')"` | output contains ok |
| Token benchmark | `pytest tests/integration/test_memory_stub_injection.py -v` | exit code 0 |
| MCP integration test | `pytest tests/integration/test_memory_mcp_server.py -v` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Verdict:** NEEDS-REVISION twice (cycle 1: 3 blockers + 4 concerns; cycle 2: 2 blockers + 5 concerns including 1 architectural). Both rounds of revisions applied below; ready for cycle-3 re-critique.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist | B1: Stub-only sidecar `injected[]` breaks `agent/memory_extraction.py:937-979` outcome detection — bigram overlap of 12-word title vs response collapses act/dismissed signal, balloons `dismissal_count`, decays importance prematurely. | `_format_stub_blocks` (Technical Approach #1) | Sidecar continues to store `record.content` (full); only the agent-visible `<thought>` becomes a stub. Internal de-dup contract unchanged. |
| BLOCKER | Operator | B2: Forked title model `llama3.2:3b` conflicts with project canonical `OLLAMA_LOCAL_MODEL=gemma4:e2b` (`config/models.py:112`); `scripts/update/run.py:751-774` would prune it on next `/update`. | Title Generation (Technical Approach #2) | Reuse `OLLAMA_LOCAL_MODEL`. No `MEMORY_TITLE_MODEL` constant added. |
| BLOCKER | Skeptic | B3: Verification table cited `--dry-run --only verify-memory-mcp` flags that don't exist on `scripts/update/run.py` (only `--full \| --cron \| --verify \| --json \| --quiet`). | Verification table + Update System | New step runs in all three modes; `--verify` is read-only; `--full`/`--cron` write atomically. |
| CONCERN | Operator | C1: `Memory.title` write may re-trigger BM25/embedding re-indexing → write amplification on every save. | BUILD task #1 (build-title-gen) | Test asserts no `bm25:*` / embedding key writes during 1000× title updates; switch to direct HSET if Popoto re-indexes. |
| CONCERN | Adversary | C3: User-level `~/.claude.json` write must be atomic — file is 5400+ lines of Claude Code state; partial write bricks all sessions. | Update System | Backup → parse → tmp → rename pattern in the new step. |
| CONCERN | Adversary | C4: Backfill script must `strip_private()` before sending content to local LLM, or legacy `<private>` segments leak. | BUILD task #1 + Title Generation | `strip_private()` call added to backfill loop. |
| CONCERN | Skeptic | Risk 6 — async title-gen race (stub renders before title written): user accepted as graceful degradation. | Risks (existing R6) | No change; documented as acceptable. |
| BLOCKER (cycle 2) | Skeptic | B1: Step 6 validation invocation still cited `--dry-run` (regression on cycle-1 B3 fix — only the Verification table was corrected). | BUILD task #6 (validate-all) | Replaced with `--verify` (read-only mode) plus a `--cron` round-trip (delete entry → re-run → confirm restored). |
| BLOCKER (cycle 2) | Architect | B2: Title-gen hooked at `tools/memory_search/__init__.py::save()` would only cover CLI saves; 6 production writer paths use `Memory.safe_save` directly and would never get titles. | Architectural Impact + Title Generation + BUILD task #1 | Hook moved to `models/memory.py::Memory.safe_save()` — the model-level chokepoint. Guarded with `if not self.title and self.content:` to skip re-saves. |
| CONCERN (cycle 2) | Operator | C1: Use canonical `ollama` Python lib instead of raw HTTP for consistency with `bridge/routing.py`. | Title Generation | Implementation Note: builder may swap `urllib.request` for `ollama.chat()` if the lib is already a transitive dep; otherwise raw HTTP is acceptable (no new pip dep). |
| CONCERN (cycle 2) | Adversary | C2: `confirm_access()` preservation — Memory model has `AccessTrackerMixin`; title writes must not bump access count or expire timer. | BUILD task #1 | Implementation Note: `update_fields=["title"]` should bypass access tracking; verify with test. |
| CONCERN (cycle 2) | Operator | C3: MCP cold-start latency — every Claude Code session spawns the server fresh; document expected cold-start cost. | Verification + Risks | Implementation Note: add a benchmark assertion (`< 500ms` cold start to first tool call) to integration test. |
| CONCERN (cycle 2) | Adversary | C4: `~/.claude.json` fcntl lock vs concurrent Claude Code session writes — atomic rename insufficient if Claude Code itself rewrites the file mid-update. | Update System | Implementation Note: acquire fcntl-style advisory lock before backup-parse-tmp-rename; release on success/failure. |
| CONCERN (cycle 2) | Operator | C5: `update_fields=["title"]` may not be supported by Popoto — clarify wording: if not supported, fall back to direct Redis HSET on the model hash. | BUILD task #1 | Implementation Note: build step probes Popoto for partial-update support; HSET fallback documented. |

---

## Open Questions

~~0. **Docs consolidation impact (#1247)**: Plan needed to be updated in light of #1247's unified auditor substrate.~~
**Resolved:** persona-builder BUILD step removed; docs work deferred to DOCS SDLC stage. No hard ordering dependency on #1247.

~~1. **MCP registration location**: user-level vs project-level.~~
**Resolved:** User-level only in `~/.claude.json` (requires local Redis; not portable to other contributors). The update script (`scripts/update/run.py`) verifies the `memory` MCP entry is present in `~/.claude.json` on every run and installs it if missing — idempotent self-healing. See **Update System** section for the new step.

~~2. **Stub title generation for long content**: cutting mid-word.~~
**Resolved:** Don't truncate at all. Memory records gain a `title` field populated by a local LLM. On save, title generation runs as a fire-and-forget async task so the caller is not blocked. Stub rendering reads `memory.title`; if the title hasn't been generated yet (race: stub fires before async title-gen completes), fall back to `[{category}]` with no title fragment rather than truncating content. See **Title Generation** subsection under **Solution → Technical Approach** for details.
