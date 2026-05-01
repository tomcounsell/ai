---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-05-01
tracking: https://github.com/tomcounsell/ai/issues/1178
last_comment_id:
---

# Memory Progressive Disclosure + MCP memory_get / memory_search Tools

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

**Notes:**
- The prefetch path (PR #1201) added a NEW call site for `_format_thought_blocks()` in `memory_bridge.prefetch()`. The plan must update that call site too — the issue body did not call it out because #1201 post-dated the issue.
- The Memory model has no `name` or `title` field; stub title generation will use: `(metadata.get("tags") or [""])[0]` prefix if tags exist, plus the first sentence of `content` truncated to 80 chars.

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
- **New registration**: `.claude.json` `mcpServers` block gets a `memory` entry pointing to the new server via `python -m mcp_servers.memory_server` (or a dedicated entry point).
- **Modified**: `_format_thought_blocks()` in `memory_bridge.py` and the inline formatting loop in `memory_hook.py` → replaced/wrapped with `_format_stub_blocks()`.
- **New helper**: `_format_stub_blocks()` (or equivalent name) replaces `_format_thought_blocks()` for the injection path; `_format_thought_blocks()` is retained as a utility for any callers that explicitly need full-body output.
- **Modified**: `config/personas/segments/work-patterns.md` — describe the new stub→fetch pattern.
- **Coupling**: MCP server imports `tools.memory_search.search` and `models.memory.Memory` directly. Both are already available in the project's `sys.path`. No new coupling is introduced that didn't already exist indirectly.
- **Reversibility**: Stub format change can be reverted by restoring `_format_thought_blocks()` calls. MCP server can be deregistered by removing the `.claude.json` entry. High reversibility.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1–2 (scope alignment on stub title generation heuristic; MCP registration in `.claude.json` vs `.mcp.json`)
- Review rounds: 1 (code review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `mcp` SDK installed | `python -c "import mcp; print(mcp.__version__)"` | FastMCP for new server |
| Redis reachable | `python -c "from models.memory import Memory; print('ok')"` | Memory model access in MCP server |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory-progressive-disclosure.md`

## Solution

### Key Elements

- **Stub format**: `<thought id="mem_xyz">[category] one-line title</thought>` — `id` attribute carries the memory_id, `[category]` is metadata.category (or "memory" if absent), one-line title is `content[:80].split(".")[0]`.
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

1. **`_format_stub_blocks()` in `memory_bridge.py`**: Mirror the signature of `_format_thought_blocks()`. For each record: extract `memory_id`, `metadata.get("category", "memory")`, and a title derived from `content` (first sentence, max 80 chars). Format as `<thought id="{memory_id}">[{category}] {title}</thought>`. Keep the sidecar `injected[]` entries identical in shape `{"memory_id": ..., "content": title_used}` so de-dup works. Leave `_format_thought_blocks()` intact for backward compat — just change the call site from `_format_thought_blocks` to `_format_stub_blocks` in `recall()`, `prefetch()`, and `check_and_inject()`.

2. **`mcp_servers/memory_server.py`**: 
   - `mcp = FastMCP("memory")`
   - `@mcp.tool() def memory_get(memory_id: str) -> dict`: loads `Memory.query.filter(memory_id=memory_id)`, returns `{content, category, tags, importance, source, metadata}` or `{"error": "not found"}`.
   - `@mcp.tool() def memory_search(query: str, category: str | None = None, tag: str | None = None, limit: int = 5) -> list[dict]`: calls `tools.memory_search.search(query, category=category, tag=tag, limit=limit)` and returns stubs `[{id, category, title, score}]`. No full bodies in search results — agent fetches those via `memory_get`.
   - Entry point: `if __name__ == "__main__": mcp.run()` (stdio transport default).

3. **`.claude.json` registration**: Direct edit to `~/.claude.json` adding the `memory` MCP server. Use `PYTHONPATH` env var so the server can import project modules when invoked by Claude Code.

4. **`config/mcp_library.json`**: Add entry documenting the memory server tools for reference.

5. **Token-cost benchmark test**: `tests/integration/test_memory_stub_injection.py` — create 3 mock Memory records with ~300-token content, run `_format_stub_blocks()` on them, measure token count vs `_format_thought_blocks()`, assert ≥5× reduction using `tiktoken`.

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
- [ ] Record with `content = ""` → stub generation falls back to `"[{category}]"` (no title fragment).

### Error State Rendering
- [ ] MCP tools return `{"error": str}` on failure — FastMCP serializes this as a valid tool response so the agent sees a description of what went wrong rather than a protocol error.

## Test Impact

- [ ] `tests/unit/test_memory_bridge.py::TestFormatThoughtBlocks` — UPDATE: tests of `_format_thought_blocks()` remain valid (function is kept); add parallel test class `TestFormatStubBlocks` for the new helper. Existing tests of `recall()` and `prefetch()` that assert `"<thought>"` in the result must be updated to assert stub format `<thought id=`.
- [ ] `tests/unit/test_memory_hook.py` (test classes asserting `<thought>` in `check_and_inject()` output) — UPDATE: assert stub format with `id=` attribute instead of full body.
- [ ] `tests/integration/test_memory_prefetch.py` — UPDATE: prefetch now returns stubs; update any assertions on thought content to check stub format.
- [ ] `tests/integration/test_memory_stub_injection.py` — CREATE: new benchmark test asserting ≥5× token reduction.

## Rabbit Holes

- **Embedding the full body in the stub**: Defeats the purpose. The stub must be compact — no body.
- **Generating titles using an LLM call during injection**: The injection path has a 15ms budget. LLM title generation would blow the latency SLA. Use the first-sentence heuristic only.
- **Changing the bloom filter, BM25 indexing, or RRF scoring**: Out of scope per the issue. The retrieval pipeline is not touched.
- **Bridge-side memory auto-recall for Telegram**: Out of scope per the issue. Only the Claude Code hook + SDK paths.
- **Versioning the stub format**: The stub format is internal to Claude Code sessions and transient. No versioning needed.
- **Registering the MCP server in `config/mcp_library.json` and then auto-installing it**: The library JSON is a reference catalogue. Actual registration is `~/.claude.json`. Keep them separate.

## Risks

### Risk 1: Stub-only injection reduces agent context enough to miss important memories
**Impact:** Agent never calls `memory_get` even when the stub is relevant — effectively a regression in memory utility.
**Mitigation:** The persona update explicitly tells the agent to check stub IDs and call `memory_get` when a stub looks relevant. Add an integration test that verifies a Claude Code session can retrieve stub content via the MCP tool. Also: the stub includes `[category]` which provides enough signal for the agent to decide whether to fetch.

### Risk 2: `~/.claude.json` modification requires manual step or restarts Claude Code
**Impact:** The MCP server is not available until the user restarts Claude Code.
**Mitigation:** Document the required restart in the plan. The builder should verify MCP tool availability in a live session as the final acceptance test.

### Risk 3: MCP server `PYTHONPATH` env var is brittle across machines
**Impact:** On a different machine (e.g., bridge machine with different user), the hard-coded path breaks.
**Mitigation:** Use `$HOME/src/ai` (shell expansion) or make the registration script resolve the path dynamically via `git rev-parse --show-toplevel`. Document this in the update system section.

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

The MCP server registration in `~/.claude.json` is per-machine and per-user. The update script (`scripts/remote-update.sh`) should include a step to:
1. Add the `memory` MCP server entry to `~/.claude.json` if not already present.
2. Verify the path resolves correctly on the target machine.

New `mcp_servers/` directory must be included in the `hatch.build.targets.wheel.packages` list in `pyproject.toml` if the package is ever installed as a wheel (currently not the case for local development). For now, the module is available via `PYTHONPATH`.

No new pip dependencies are introduced — `mcp>=1.8.0` is already in `pyproject.toml`.

## Agent Integration

This feature IS the agent integration. Specifically:

- **New CLI entry point** (optional): A `valor-memory-server` script could be added to `pyproject.toml [project.scripts]` pointing to `mcp_servers.memory_server:main` for convenience. Not strictly required if Claude Code invokes via `python -m mcp_servers.memory_server`.
- **`~/.claude.json` registration**: The `memory` MCP server entry makes `memory_get` and `memory_search` available as native Claude Code tools in all sessions. The builder must verify these appear in the tool list after restart.
- **Integration test**: `tests/integration/test_memory_mcp_server.py` — spawn the MCP server as a subprocess, make tool calls via the MCP protocol, verify correct responses. Use `mcp.client.stdio.stdio_client` from the SDK.
- **No bridge changes**: The bridge does not need to import or call the new code directly. Memory recall in bridge-spawned sessions happens through the hook and SDK paths already.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` with the new stub injection format, `memory_get` and `memory_search` tool descriptions, and the progressive disclosure pattern.
- [ ] Update `docs/features/claude-code-memory.md` with the new stub format and MCP tool integration.
- [ ] Add entry to `docs/features/README.md` index table for the MCP tools if not already present.
- [ ] Update `config/personas/segments/work-patterns.md` Subconscious Memory section to describe stub→fetch pattern and when to call `memory_get` vs `memory_search`.

## Success Criteria

- [ ] `<thought>` injections in both `memory_bridge.py` (`recall()` and `prefetch()`) and `agent/memory_hook.py` (`check_and_inject()`) use stub format: `<thought id="{memory_id}">[{category}] {title}</thought>`.
- [ ] New `mcp_servers/memory_server.py` exposes `memory_get` and `memory_search` tools via FastMCP stdio.
- [ ] MCP server registered in `~/.claude.json` under key `"memory"`.
- [ ] `config/mcp_library.json` has a `memory` entry documenting the tools.
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

- **Builder (persona+docs)**
  - Name: persona-builder
  - Role: Update `config/personas/segments/work-patterns.md` and `config/mcp_library.json` entry.
  - Agent Type: documentarian
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

### 1. Implement stub format helper and update injection call sites
- **Task ID**: build-stub-format
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_bridge.py`, `tests/unit/test_memory_hook.py`
- **Assigned To**: stub-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_format_stub_blocks(records, exclude_ids, max_results)` to `.claude/hooks/hook_utils/memory_bridge.py` alongside `_format_thought_blocks()`. Generates `<thought id="{memory_id}">[{category}] {title}</thought>` per record.
- Update `recall()` and `prefetch()` in `memory_bridge.py` to call `_format_stub_blocks` instead of `_format_thought_blocks`.
- Update the inline thought-formatting loop in `agent/memory_hook.py:check_and_inject()` to emit stub format.
- Title extraction logic: `content[:80].split(". ")[0]` truncated to 80 chars; fallback `"[{category}]"` if content is empty.

### 2. Implement MCP memory server
- **Task ID**: build-mcp-server
- **Depends On**: none
- **Validates**: `tests/integration/test_memory_mcp_server.py` (create)
- **Assigned To**: mcp-builder
- **Agent Type**: mcp-specialist
- **Parallel**: true
- Create `mcp_servers/__init__.py` and `mcp_servers/memory_server.py`.
- Implement `memory_get(memory_id: str) -> dict` — loads Memory record by ID, returns `{content, category, tags, importance, source, memory_id}` or `{"error": "not found"}`.
- Implement `memory_search(query: str, category: str | None = None, tag: str | None = None, limit: int = 5) -> list[dict]` — calls `tools.memory_search.search()`, returns list of stubs `{id, category, title, score}`.
- Both tools wrapped in `try/except`, fail-silent returning `{"error": ...}`.
- Register in `~/.claude.json` mcpServers: `"memory": {"type": "stdio", "command": "python", "args": ["-m", "mcp_servers.memory_server"], "env": {"PYTHONPATH": "/Users/tomcounsell/src/ai"}}`.
- Add `memory` entry to `config/mcp_library.json`.

### 3. Update test suite for stub format
- **Task ID**: build-tests
- **Depends On**: build-stub-format, build-mcp-server
- **Validates**: `tests/unit/test_memory_bridge.py`, `tests/unit/test_memory_hook.py`, `tests/integration/test_memory_prefetch.py`, `tests/integration/test_memory_stub_injection.py` (create), `tests/integration/test_memory_mcp_server.py` (create)
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `tests/unit/test_memory_bridge.py`: add `TestFormatStubBlocks` class; update `recall()` and `prefetch()` tests asserting `<thought id=` attribute.
- Update `tests/unit/test_memory_hook.py`: update `check_and_inject()` tests asserting stub format.
- Update `tests/integration/test_memory_prefetch.py`: assert stub format in prefetch output.
- Create `tests/integration/test_memory_stub_injection.py`: token benchmark comparing `_format_stub_blocks()` vs `_format_thought_blocks()` on 3 mock records with ~300-token content each; assert ≥5× token reduction using `tiktoken`.
- Create `tests/integration/test_memory_mcp_server.py`: spawn MCP server subprocess, call `memory_get` and `memory_search` via MCP stdio client, assert correct response shapes.

### 4. Update persona and documentation
- **Task ID**: build-docs
- **Depends On**: build-stub-format, build-mcp-server
- **Assigned To**: persona-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Update `config/personas/segments/work-patterns.md` Subconscious Memory section: describe stub injection, when to call `memory_get(id)`, when to call `memory_search(query)`.
- Update `docs/features/subconscious-memory.md`: add stub format description, MCP tools section.
- Update `docs/features/claude-code-memory.md`: update injection format section and add MCP tool section.
- Add/verify entry in `docs/features/README.md` index.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-docs
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_bridge.py tests/unit/test_memory_hook.py tests/integration/test_memory_prefetch.py tests/integration/test_memory_stub_injection.py -v`
- Run `pytest tests/integration/test_memory_mcp_server.py -v`
- Verify MCP server starts without error: `python -m mcp_servers.memory_server --help` or equivalent.
- Report pass/fail status for each success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_memory_bridge.py tests/unit/test_memory_hook.py -x -q` | exit code 0 |
| Stub format in recall | `python -c "from hook_utils.memory_bridge import _format_stub_blocks; print('ok')"` | output contains ok |
| MCP server imports | `python -c "import mcp_servers.memory_server; print('ok')"` | output contains ok |
| Token benchmark | `pytest tests/integration/test_memory_stub_injection.py -v` | exit code 0 |
| MCP integration test | `pytest tests/integration/test_memory_mcp_server.py -v` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **MCP registration location**: The issue body says "register in `config/mcp_library.json`" but that file is a reference catalogue, not a live registration. Actual Claude Code MCP registration is in `~/.claude.json`. Should the MCP server also be registered in a project-level `.mcp.json` so it automatically activates for all contributors, or should it remain user-level only? (Recommendation: user-level in `~/.claude.json` since it requires local Redis — the build step will add it there.)

2. **Stub title generation for long content**: Using `content[:80].split(". ")[0]` may cut mid-word for content with no early period. Should the title be capped at the nearest word boundary within 80 chars instead? (Likely yes — easy to implement; flagging for awareness.)
