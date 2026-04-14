---
status: docs_complete
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/964
last_comment_id:
---

# Memory System Status CLI

## Problem

Debugging memory-system behavior currently requires either ad-hoc Python scripting or running
`python -m tools.doctor`, which sweeps the entire system. Neither gives a fast, focused answer to
"is my memory system healthy right now?"

**Current behavior:**
- `python -m tools.memory_search inspect --stats` shows aggregate counts and confidence distribution
  but has no Redis connectivity check, no superseded-record count, and no category breakdown.
- `python -m tools.doctor` checks system-wide health (bridge, worker, disk, API keys) but does not
  introspect memory-specific internals like orphan index keys or the superseded-record ratio.
- There is no single CLI command that surfaces all memory-health signals in one place.

**Desired outcome:**
`python -m tools.memory_search status` prints a human-readable health summary in under one second.
`--json` makes it machine-consumable so `tools.doctor` can call it as a sub-check. `--deep` adds
slow checks (orphan scan, per-category confidence). `--project` scopes to a specific project key.

## Freshness Check

**Baseline commit:** 249de2c7
**Issue filed at:** 2026-04-15
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/memory_search/cli.py` — Five subcommands exist (search, save, inspect, stats, forget); no status subcommand. Still holds.
- `tools/memory_search/__init__.py` — `_resolve_project_key()` helper confirmed; `inspect(stats=True)` fetches all records but no category breakdown. Still holds.
- `models/memory.py:89` — `superseded_by = StringField(default="")` confirmed. EmbeddingField not present; #965 (vector recall) is open and unplanned.
- `scripts/popoto_index_cleanup.py` — `_count_orphans()` uses `POPOTO_REDIS_DB.smembers("{ModelName}:_all")` pattern; confirmed reusable without modification.
- `tools/doctor.py` — `_check_memory()` checks process RAM only, not memory-system internals. Downstream integration point is ready.

**Cited sibling issues/PRs re-checked:**
- #873 — closed 2026-04-10 (transition_status bug). Illustrates memory-system surprise; not blocking.
- #887 — session isolation bypass; not blocking.
- #965 — still open, unplanned. EmbeddingField not yet wired; status check must handle its absence gracefully.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** EmbeddingField is a forward-compat check only — the status subcommand should report "not configured" when the field is absent rather than raising an error.

## Prior Art

- **Issue #855** (Local doctor tool, merged 2026-04-10): Created `tools/doctor.py` with modular check pattern. The memory `status` subcommand should follow the same `_check_X() → CheckResult` pattern for easy integration into doctor's output.

No prior attempts to add a `memory_search status` subcommand.

## Architectural Impact

- **New dependencies:** None. Uses existing `models/memory.py`, `scripts/popoto_index_cleanup.py`, and Popoto's Redis handle.
- **Interface changes:** Additive only — new `status` subcommand added to `tools/memory_search/cli.py`; new `cmd_status()` handler; new `status()` function in `tools/memory_search/__init__.py`.
- **Coupling:** `tools/doctor.py` gains an optional call to `memory_search status --json`. Loose coupling — doctor treats it as a sub-process call, not a Python import.
- **Data ownership:** No change. Status reads from Redis; never writes.
- **Reversibility:** Fully reversible — delete the subcommand entry, `cmd_status()`, and `status()`. No migrations needed.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Memory model queries require Redis |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory-status-cli.md`

## Solution

### Key Elements

- **`status()` function** in `tools/memory_search/__init__.py`: pure-read diagnostic that returns a structured dict. Fast path (<1s): Redis ping, total count, category breakdown, superseded count, last-write timestamp, EmbeddingField detection. Deep path (behind `--deep`): orphan index count using `_count_orphans()` from `scripts/popoto_index_cleanup.py`, per-category confidence averages.
- **`cmd_status()` handler** in `tools/memory_search/cli.py`: formats the dict as human-readable text or `--json`. Exits with code 1 if Redis is unreachable.
- **`tools/doctor.py` integration**: existing doctor calls `memory_search status --json` as a sub-process and renders a summary line under a "Memory" section.

### Flow

```
python -m tools.memory_search status
  → cmd_status() in cli.py
    → status() in __init__.py
      → Redis ping (fail-fast: print error + exit 1 if down)
      → Memory.query.filter(project_key=...) — fetch all records
      → aggregate: total, by_category, superseded_count, avg_confidence, last_write
      → if --deep: _count_orphans(Memory) from popoto_index_cleanup
    → format output (human-readable or --json)
    → exit 0
```

### Technical Approach

- Reuse `_resolve_project_key()` for project scoping, same pattern as all other subcommands.
- Category breakdown: iterate records and accumulate `metadata.get("category")` counts. Four known categories: correction, decision, pattern, surprise. Uncategorized records go under `"other"`.
- Superseded count: filter records where `superseded_by != ""`.
- Last-write timestamp: The `relevance` field stores a Unix timestamp float (confirmed live: `1776184081.54`). Use `max(getattr(r, "relevance", 0) for r in all_records)` → `datetime.fromtimestamp(...)`. Do NOT attempt UUID/AutoKeyField timestamp parsing — AutoKeyField is UUID4 (random), not timestamp-based.
- Shared record fetch: extract `_fetch_all_records(project_key)` helper in `__init__.py` to avoid duplicating the `Memory.query.filter(...)` call between `status()` and `inspect(stats=True)`. Both functions call this helper.
- Orphan detection: import `_count_orphans` from `scripts/popoto_index_cleanup` directly (no subprocess). Use `sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))` at the top of the `--deep` branch, then `from popoto_index_cleanup import _count_orphans`.
- EmbeddingField detection: `Memory._meta.fields.get("embedding")` — if `None`, report "not configured".
- All errors wrapped in `try/except`; status subcommand follows the existing fail-silent contract of the module but surfaces Redis-down as an explicit non-zero exit.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `status()` wraps all Redis/Popoto calls in try/except; on Redis-down, returns `{"error": "Redis unreachable: ...", "healthy": False}`.
- [ ] `cmd_status()` checks the `error` key and prints a clear message to stderr before exiting 1.
- [ ] Tests must assert: (a) exit code 1 when Redis mock raises `ConnectionError`, (b) error text visible on stderr.

### Empty/Invalid Input Handling
- [ ] `status()` with no memories in Redis returns zero counts gracefully (not a crash or KeyError).
- [ ] `--project` with an unknown project key returns zero counts (not an error).

### Error State Rendering
- [ ] Human-readable output renders correctly when Redis is down (shows error, not empty table).
- [ ] `--json` output when Redis is down emits `{"healthy": false, "error": "..."}` — not a stack trace.

## Test Impact

No existing tests affected — `tests/unit/test_memory_search_cli.py` does not yet exist (the issue specifically calls for creating it). All other memory unit tests (`test_memory_model.py`, `test_memory_retrieval.py`, etc.) are unaffected because the change is purely additive.

## Rabbit Holes

- **Running `rebuild_indexes()` inside status**: Do NOT. `_count_orphans()` is read-only; `rebuild_indexes()` mutates. Status must never mutate state.
- **Per-category confidence on the fast path**: requires iterating every record and computing act_rate — O(N). Gate behind `--deep`.
- **Wiring `tools.doctor` in this PR**: The doctor integration is downstream context from the issue but is explicitly NOT in scope for this PR. Add a TODO comment in doctor.py only.
- **`--watch` / `--loop` mode**: Out of scope. Status is a point-in-time snapshot.
- **Cross-project aggregate stats**: Out of scope. `--project` defaults to the current project; no all-projects rollup.

## Risks

### Risk 1: `relevance` field timestamp accuracy
**Impact:** `last_write` reflects relevance score update time, not creation time. Decay or boost operations could update `relevance` on old records, making them appear recent.
**Mitigation:** Document in a code comment that `last_write` is "last relevance update" not "last created". This is acceptable for health-check purposes.

### Risk 2: `_count_orphans` import path
**Impact:** `scripts/popoto_index_cleanup.py` is in `scripts/`, which may not be in the Python path during tests.
**Mitigation:** Use `sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))` inside the `--deep` branch before the import. This is a deliberate, committed approach — not a "check at build time" decision.

## Race Conditions

No race conditions identified. All operations are synchronous read-only queries against Redis. No shared mutable state is modified.

## No-Gos (Out of Scope)

- Doctor integration beyond a TODO comment
- `--watch` or continuous monitoring mode
- Cross-project aggregate stats
- Any mutation (no `rebuild_indexes`, no record deletion)
- Embedding/vector field status beyond "configured or not"

## Update System

No update system changes required — this feature is purely additive CLI logic. No new dependencies, no new config files, no migration steps.

## Agent Integration

The `status` subcommand is a CLI tool, not an MCP-exposed function. The agent accesses it via the `memory_search` MCP server if that server already wraps the CLI, or via bash tool calls. No `.mcp.json` changes needed — the existing memory_search MCP server (if wired) will pick up the new subcommand automatically through the CLI layer.

No changes to `bridge/telegram_bridge.py`. No new MCP server registration.

## Documentation

- [x] Update `CLAUDE.md` command table: add `python -m tools.memory_search status` row with description "Check memory system health (Redis, counts, superseded ratio)"
- [x] Update `docs/features/subconscious-memory.md`: add a "Health Checks" subsection that references the new `status` subcommand
- [x] Add entry to `docs/features/README.md` index table if memory-status is considered a discrete feature (or append to the existing subconscious-memory row)
- [x] Update `docs/features/memory-search-tool.md`: add `status()` API and CLI usage
- [x] Update `docs/tools-reference.md`: add `status` to Memory Search section

## Success Criteria

- [ ] `python -m tools.memory_search status` prints a human-readable health summary in <1s for typical memory sizes
- [ ] `--json` flag emits `{"healthy": true/false, "redis": {...}, "total": N, "by_category": {...}, "superseded": N, "avg_confidence": 0.NN, "last_write": "...", "embedding_field": "configured|not_configured"}` structure
- [ ] `--deep` flag adds `orphan_index_count` to the output
- [ ] `--project <name>` scopes all counts to the specified project
- [ ] Redis-down exits with code 1 and prints a human-readable error to stderr
- [ ] `CLAUDE.md` command table updated
- [ ] `tests/unit/test_memory_search_cli.py` created with coverage for: happy path, Redis-down, empty project, `--json`, `--deep`
- [ ] Tests pass (`pytest tests/unit/test_memory_search_cli.py -v`)
- [ ] Lint and format clean (`python -m ruff check . && python -m ruff format --check .`)

## Team Orchestration

### Team Members

- **Builder (memory-status)**
  - Name: status-builder
  - Role: Implement `status()` in `__init__.py`, `cmd_status()` in `cli.py`, wire argparse subcommand
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Create `tests/unit/test_memory_search_cli.py` covering all acceptance criteria
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Run tests, lint, format, verify CLI output matches spec
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update CLAUDE.md and docs/features/subconscious-memory.md
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Implement status() and cmd_status()
- **Task ID**: build-status
- **Depends On**: none
- **Validates**: tests/unit/test_memory_search_cli.py (create)
- **Assigned To**: status-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `status()` function to `tools/memory_search/__init__.py` following the fail-silent contract
- Add `cmd_status()` handler to `tools/memory_search/cli.py`
- Wire `status` subcommand in the argparse parser with `--json`, `--deep`, `--project` flags
- Import `_count_orphans` from `scripts/popoto_index_cleanup` inside the `--deep` path (guarded)
- Check `Memory._meta.fields.get("embedding")` for EmbeddingField detection

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-status
- **Validates**: tests/unit/test_memory_search_cli.py
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_memory_search_cli.py`
- Cover: happy path (mocked Redis), Redis-down (ConnectionError), empty project, `--json`, `--deep`, `--project`
- Assert exit code 0 on success, exit code 1 on Redis-down
- Assert stderr output when Redis is down

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `CLAUDE.md` command table with `python -m tools.memory_search status` row
- Add "Health Checks" subsection to `docs/features/subconscious-memory.md`
- Update `docs/features/README.md` index if applicable

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_search_cli.py -v`
- Run `python -m ruff check . && python -m ruff format --check .`
- Manually invoke `python -m tools.memory_search status` and `--json` and verify output format
- Confirm CLAUDE.md and docs updates are present

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_search_cli.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Status subcommand exists | `python -m tools.memory_search status --help` | exit code 0 |
| JSON flag works | `python -m tools.memory_search status --json` | output contains `healthy` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist | AutoKeyField is UUID4 (random), not timestamp-based — UUID parsing for `last_write` is wrong | Plan revision | Use `max(getattr(r, "relevance", 0) for r in records)` → `datetime.fromtimestamp(...)` |
| CONCERN | Skeptic | `_count_orphans` import path leaves "check at build time" decision open | Plan revision | Committed to `sys.path.insert(0, ...)` approach in `--deep` branch |
| CONCERN | Simplifier | `status()` duplicates `inspect(stats=True)` record-fetching | Plan revision | Extract shared `_fetch_all_records(project_key)` helper |
| NIT | Operator | `avg_confidence` scope (overall vs per-category) was ambiguous | Plan revision | Fast path: overall only; `--deep` adds per-category breakdown |

---

## Open Questions

None — scope is well-defined, all references verified, no business decisions required.
