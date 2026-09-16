---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3310
last_comment_id:
---

# apply-defaults embedding provider import cycle

## Problem

Any process whose first import is `models.memory` silently runs with no embedding provider for its entire lifetime. Memory retrieval in that process degrades to BM25 lexical ranking with no query-embedding signal, and nothing logs the condition, so the degradation is invisible until someone notices recall quality differences between processes.

**Current behavior:**

`config/memory_defaults.py::apply_defaults()` configures the embedding provider inside a bare `except Exception: pass`. When `models.memory` is imported first in a fresh process, the inner `from agent.embedding_provider import configure_embedding_provider` executes the `agent/__init__.py` package init, which eagerly imports `agent_session_queue`, which imports `session_executor`, which imports `session_health`, which does `from models.memory import Memory` back into the half-initialized `models.memory` module. The resulting `ImportError` is swallowed, and `get_default_provider()` returns `None` forever:

```
.venv/bin/python -c "import models.memory; from popoto.fields.embedding_field import get_default_provider; print(get_default_provider())"
# -> None
```

**Desired outcome:**

Import order no longer determines whether the embedding provider is configured. Importing `models.memory` first in a fresh process yields the corpus-matched `OpenAIProvider`, and any genuine provider-configuration failure is logged instead of swallowed.

## Freshness Check

**Baseline commit:** `1d82a3f0ff4677fce144f15f574d3f5e8fbc0ce5`
**Issue filed at:** 2026-09-14T10:30:27Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/memory.py:24` — `apply_defaults()` at import time — still holds
- `config/memory_defaults.py:249` — `from agent.embedding_provider import configure_embedding_provider` — still holds
- `config/memory_defaults.py:252-253` — `except Exception: pass` — still holds
- `agent/__init__.py:3` — eager `agent_session_queue` import — still holds
- `agent/agent_session_queue.py:49` — `from agent.session_executor import _execute_agent_session` — still holds
- `agent/session_executor.py:17` — `from agent.session_health import HEARTBEAT_WRITE_INTERVAL` — still holds
- `agent/session_health.py:43` — `from models.memory import Memory` — still holds
- `agent/embedding_provider.py:37` — `configure_embedding_provider()` with stdlib-only module imports — still holds

**Cited sibling issues/PRs re-checked:**
- #3177 — still OPEN (recursive self-improvement controller epic)
- #3216 — lane 4 context where the bug resurfaced; its PR #3309 MERGED 2026-09-14 without touching the import chain or root cause
- #1904 — closed; graceful-degradation philosophy for embedding failures, relevant precedent

**Commits on main since issue was filed (touching referenced files):**
- `f02683282` Improvement controller lane 3 (#3315) — touched `agent/session_health.py` (+26 lines, appended `any_worker_alive()` at line ~5462) — irrelevant to the import chain; line-43 import untouched

**Active plans in `docs/plans/` overlapping this area:** none — recent plans cover pytest guards, worktree dispatch, forum topics, and improvement-controller lanes; no active plan touches the memory provider wiring.

**Notes:** Bug reproduced against the baseline: `import models.memory` first prints `None`. The issue's controlled comparison still holds verbatim.

## Prior Art

- **Issue #1904**: Embedding timeout silently drops Memory records in safe_save -- Established the graceful-degradation philosophy (persist without a vector, backfill later via the `memory-embedding-backfill` reflection) that this fix must preserve. Closed.
- **Issue #965**: Add vector-similarity (semantic) recall as fourth RRF signal on Memory -- The recall path this bug silently disables in affected processes. Closed.
- **PR #3309** (lane 4, #3216): Frozen evaluation inputs -- Merged without touching the provider wiring; its evaluation arms inherit whichever provider the parent's import order happened to produce, which is the concrete downstream harm.
- **Workarounds in tree**: `scripts/spike/hybrid_retrieval_spikes.py:20-25` imports `agent` first and re-runs `configure_embedding_provider()`; `tools/memory_eval/provider_gate.py` refuses to rely on the implicit provider at all. Both paper over the cause; neither fixes it.

No prior fix attempt for this exact cycle exists, so there is no **Why Previous Fixes Failed** section.

## Research

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

### spike-1: Can apply_defaults reach configure_embedding_provider without executing agent/__init__?
- **Assumption**: "`apply_defaults` can reach `agent.embedding_provider` without triggering `agent/__init__` via a normal import"
- **Method**: code-read
- **Finding**: False for normal imports. Python always executes a parent package's `__init__.py` before a submodule import, so any `from agent.embedding_provider import ...` statement executes the eager `agent_session_queue` chain. Reaching the module without the package init would require `importlib` file-path loading under a distinct module name, which creates dual module identity for the same file. The cut must therefore be elsewhere: the back-edge (`session_health` -> `models.memory`) or the module's package home.
- **Confidence**: high
- **Impact on plan**: The plan's primary cut is a lazy import of `Memory` at its single function-level use site in `session_health.py`, optionally plus relocating `configure_embedding_provider` out of the `agent` package. Path-loading tricks are explicitly excluded.

### spike-2: Which entrypoints depend on the implicit apply_defaults provider path?
- **Assumption**: "All processes get their provider through `apply_defaults`, so the fix must keep configuring the provider there"
- **Method**: code-read
- **Finding**: The bridge configures its provider directly (`bridge/telegram_bridge.py:3434` via `popoto.configure`), independent of `apply_defaults`. Worker and CLI processes rely on the implicit `apply_defaults` path. Removing provider setup from `apply_defaults` in favor of explicit entrypoint calls would require touching every worker/CLI entrypoint, a larger and riskier change.
- **Confidence**: medium
- **Impact on plan**: Keep configuring the provider inside `apply_defaults`; fix the cycle and the silent swallow in place. Build verifies the full entrypoint list before committing to this shape.

## Data Flow

1. **Entry point**: Any process whose first import is `models.memory` (worker subprocesses, CLI tools, evaluation arms inheriting a parent's import order).
2. **`models/memory.py:24`**: `apply_defaults()` runs at module import time, before the `Memory` class is defined.
3. **`config/memory_defaults.py:249`**: `from agent.embedding_provider import configure_embedding_provider` starts executing the `agent` package init.
4. **`agent/__init__.py` -> `agent_session_queue.py:49` -> `session_executor.py:17` -> `session_health.py:43`**: The eager chain lands on `from models.memory import Memory`, but `models.memory` is half-initialized (`Memory` not yet defined), so Python raises `ImportError`.
5. **`config/memory_defaults.py:252`**: `except Exception: pass` swallows the `ImportError`. `Memory` later defines normally, but the provider was never configured.
6. **Output**: `get_default_provider()` returns `None` for the life of the process. Saves persist without vectors (graceful path), and retrieval ranks by BM25 only with no embedding signal.

After the fix, step 4 no longer raises (lazy back-edge import), step 3 configures the corpus-matched `OpenAIProvider`, and step 5 logs loudly on genuine failure instead of swallowing.

## Architectural Impact

- **New dependencies**: None. The fix rearranges existing imports; no new packages, services, or libraries.
- **Interface changes**: None public. `configure_embedding_provider()` keeps its signature and return contract. If the module moves out of the `agent` package, a re-export shim at the old location keeps the two existing importers (`config/memory_defaults.py`, `scripts/spike/hybrid_retrieval_spikes.py`) working during transition, or both call sites are updated in the same change.
- **Coupling**: Decreases. `agent/session_health.py` no longer depends on `models.memory` at module-import time, weakening the `agent` <-> `models` import-time coupling that caused this bug class.
- **Data ownership**: Unchanged. Popoto's process-wide provider registry remains the owner; this fix only ensures it actually gets populated.
- **Reversibility**: Trivially reversible. A lazy import can be moved back to module top level, and the logging change is additive.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (bug with a verified root cause and a narrow fix shape)
- Review rounds: 1 (standard PR review)

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| (none) | `python scripts/check_prerequisites.py docs/plans/apply-defaults-embedding-provider-import-cycle.md` | Confirm no env or service gates before building |

## Solution

Filling in next.

## Failure Path Test Strategy

Filling in next.

## Test Impact

No existing tests affected — the change only converts a silently-swallowed ImportError into a working provider configuration plus a logged failure; existing memory tests import through the normal path and assert behavior unaffected by import order.

## Rabbit Holes

Filling in next.

## Risks

Filling in next.

## Race Conditions

No race conditions identified — all operations are synchronous and single-threaded at import time.

## No-Gos (Out of Scope)

Filling in next.

## Update System

No update system changes required — this fix is purely internal to import wiring.

## Agent Integration

No agent integration required — no new CLI entry point and no bridge changes; the provider becomes correctly configured wherever `models.memory` is imported.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` configuration notes with the import-order guarantee
- [ ] Add entry to `docs/features/README.md` index table if a new feature doc is created

## Success Criteria

Filling in next.

## Team Orchestration

Filling in next.

## Step by Step Tasks

Filling in next.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_memory_model.py -q` | exit code 0 |
| Import-order regression | `.venv/bin/python -c "import models.memory; from popoto.fields.embedding_field import get_default_provider; print(get_default_provider())"` | output contains OpenAIProvider |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Filling in next.
