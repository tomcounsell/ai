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

No external services or secrets required — this work has no external dependencies beyond the repo venv.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv | `~/src/ai/.venv/bin/python --version` | Interpreter present for test runs |

## Solution

### Key Elements

- **Lazy back-edge import**: `Memory` moves from module top level to its single function-level use site in `agent/session_health.py`, so importing the `agent` package never reaches back into a half-initialized `models.memory`.
- **Loud provider failure**: The bare `except Exception: pass` in `apply_defaults` becomes a logged warning naming the failure, so a missing provider is visible in logs instead of silent.
- **Import-order regression test**: A subprocess test that imports `models.memory` first and asserts a provider is configured, locking the fix against reintroduction.
- **Preserved graceful degradation**: Missing `OPENAI_API_KEY` remains a legitimate, already-logged warning path inside `configure_embedding_provider()`; only the accidental-swallowing layer changes.

### Flow

**Starting point** → fresh process imports `models.memory` first → `apply_defaults()` runs → provider import executes `agent/__init__` chain → chain no longer touches half-initialized `models.memory` → `configure_embedding_provider()` installs the corpus-matched `OpenAIProvider` → `Memory` defines normally → **End state**: `get_default_provider()` returns the provider regardless of import order.

### Technical Approach

- Cut the cycle at its narrowest point: `agent/session_health.py:43` (`from models.memory import Memory`) is the only module-level use, consumed once at line 6201 inside the class-set orphan cleanup routine. Move that import to the top of the enclosing function. `AgentSession` (line 42, `models.agent_session`) stays at module level; verify at build time that `models.agent_session` does not itself import `models.memory` at module scope, or the cycle simply relocates.
- Replace `except Exception: pass` in `config/memory_defaults.py:247-253` with `except Exception as e: logger.warning(...)` naming `apply_defaults` and the embedding provider, preserving the non-blocking intent (a provider failure must never prevent the `Memory` model from defining) while making it observable.
- Keep provider configuration inside `apply_defaults` (per spike-2: worker/CLI entrypoints depend on the implicit path). Do not move setup to explicit entrypoint calls.
- Optional hardening, only if build finds a second import-time path into `models.memory` from the `agent` package: relocate `configure_embedding_provider` to a package-neutral home (e.g. `config/embedding_provider.py`) and update the two call sites (`config/memory_defaults.py`, `scripts/spike/hybrid_retrieval_spikes.py`). Build decides; the lazy import is the primary fix either way.
- Regression test runs the issue's controlled comparison as a subprocess: import `models.memory` first, assert `get_default_provider()` is not `None`. Subprocess isolation matters because import order is process-global state.
- Verify the reverse order still works (`import agent` first) and that the full memory test files pass unchanged.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `except Exception: pass` block at `config/memory_defaults.py:252` is the failure site of this bug: add the subprocess regression test asserting the provider is configured (observable state change), plus a test asserting a genuine configuration failure emits a log record instead of passing silently.
- [ ] `configure_embedding_provider()` already logs warnings on its own failure paths; no new handlers are introduced there.

### Empty/Invalid Input Handling
- [ ] Not applicable: the changed functions take no user input. `configure_embedding_provider()` with a missing `OPENAI_API_KEY` (empty/unset) already returns `None` with a logged warning; the regression test documents that this intentional path still degrades gracefully rather than raising.
- [ ] If the feature involves agent output processing, verify empty output does not trigger silent loops — not applicable, no agent output processing in scope.

### Error State Rendering
- [ ] No user-visible output in scope; the observable error surface is the log record from `apply_defaults` on provider failure, covered by the log-assertion test above.

## Test Impact

No existing tests affected — the change only converts a silently-swallowed ImportError into a working provider configuration plus a logged failure; existing memory tests import through the normal path and assert behavior unaffected by import order.

## Rabbit Holes

- Rewriting `agent/__init__.py` to be lazy across the board: a large, behavior-risky refactor when the cycle has exactly one back-edge. Cut the back-edge instead.
- `importlib` file-path loading of `agent/embedding_provider.py` to dodge the package init: creates dual module identity and fragile path coupling. Excluded by spike-1.
- Narrowing `except Exception` to `except ImportError`: changes nothing, since the swallowed exception already is an `ImportError`. Dropped during recon.
- Moving provider setup to explicit entrypoint calls: requires auditing every worker/CLI entrypoint and leaves any missed one silently providerless. Keep the implicit `apply_defaults` path (spike-2).
- Chasing BM25-only quality deltas in eval harnesses: the eval-arm inheritance problem (#3216) is fixed as a consequence; re-tuning retrieval is a separate project.

## Risks

### Risk 1: A second import-time path from `agent` into `models.memory` exists
**Impact:** The lazy import fixes the known chain but the cycle persists through another edge, and the regression test fails.
**Mitigation:** Build runs the regression test first as a red-state proof; if it still fails, enumerate remaining import-time edges with a traceback and apply the optional module-relocation hardening.

### Risk 2: `models.agent_session` also reaches `models.memory` at import time
**Impact:** `session_health.py` still imports `AgentSession` at module level, so the cycle relocates rather than breaks.
**Mitigation:** Build verifies `models/agent_session.py` module-level imports before committing to the lazy-import shape; if it reaches `models.memory`, both model imports go lazy or the relocation option is taken.

### Risk 3: Noisy warnings on legitimately providerless processes
**Impact:** Replacing the silent pass with a warning could log on every import in environments without `OPENAI_API_KEY`, where providerless operation is intended.
**Mitigation:** Keep the warning at `warning` level with a one-line message, matching the tone of the existing missing-key warning inside `configure_embedding_provider()`; the missing-key path itself stays quiet-by-design beyond its single warning.

## Race Conditions

No race conditions identified — all operations are synchronous and single-threaded at import time.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this fix is purely internal to import wiring.

## Agent Integration

No agent integration required — no new CLI entry point and no bridge changes; the provider becomes correctly configured wherever `models.memory` is imported.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` configuration notes with the import-order guarantee
- [ ] Add entry to `docs/features/README.md` index table if a new feature doc is created

## Success Criteria

- [x] Fresh process importing `models.memory` first configures the corpus-matched provider (`get_default_provider()` is not `None`)
- [x] Reverse order (`import agent` first) still configures the provider
- [x] A genuine provider-configuration failure emits a log record instead of passing silently
- [x] Missing-`OPENAI_API_KEY` processes still degrade gracefully without raising
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (import-cycle-fix)**
  - Name: cycle-builder
  - Role: Apply the lazy import, the loud failure, and the regression test
  - Agent Type: builder
  - Resume: true

- **Validator (import-cycle-fix)**
  - Name: cycle-validator
  - Role: Verify both import orders, the log assertion, and the memory test files
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `test-engineer` - Test implementation and strategy
- `documentarian` - Documentation updates
- `plan-maker` - Planning subagent
- `frontend-tester` - Browser testing

**Domain expertise (no dedicated agent — prompt a Tier 1 agent):**
There is no standing pool of "specialist" agents. For domain-specific work
(async/concurrency, Redis/Popoto data, security/untrusted-input, debugging,
MCP-tool/API integration, conversational-UX/testing), assign a `builder` (or
`code-reviewer` for review-only work), add a `Domain: <tag>` line to the task,
and paste the matching rules from [`DOMAIN_FRAMING.md`](DOMAIN_FRAMING.md) into
the task's assignment. For broad recon use the built-in `Explore` /
`general-purpose` agents.

**Service Agents (domain-specific task delegation):**
- `linear`, `notion`, `sentry`, `stripe`, `render` — portable agents that wrap a
  SaaS/MCP integration; available in any repo via the synced `~/.claude/agents/`.

## Step by Step Tasks

### 1. Red-state regression test
- **Task ID**: build-regression-test
- **Depends On**: none
- **Validates**: new subprocess test failing on current main (create)
- **Informed By**: spike-1 (normal imports always execute the package init, so the test must use a real subprocess with `models.memory` imported first)
- **Assigned To**: cycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Write the regression test FIRST and confirm it fails on the unfixed tree: fresh subprocess imports `models.memory` first, asserts `get_default_provider()` is not `None`
- Assert the reverse order (`import agent` first) also yields a provider, in the same test module

### 2. Break the back-edge and make failure loud
- **Task ID**: build-cycle-fix
- **Depends On**: build-regression-test
- **Validates**: new subprocess test (passing), tests/unit/test_memory_model.py, tests/unit/test_memory_distill_backfill.py
- **Informed By**: spike-1 (lazy back-edge import is the primary cut), spike-2 (keep configuring inside `apply_defaults`)
- **Assigned To**: cycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Move `from models.memory import Memory` in `agent/session_health.py:43` to the top of the enclosing function at line ~6201; first verify `models/agent_session.py` has no module-level import of `models.memory`
- Replace `except Exception: pass` in `config/memory_defaults.py:247-253` with a logged warning naming `apply_defaults` and the embedding provider
- If the regression test still fails (second import-time edge per Risk 1), relocate `configure_embedding_provider` to a package-neutral module and update both call sites
- Add a log-assertion test for genuine configuration failure and confirm the missing-key path still degrades gracefully

### 3. Validation
- **Task ID**: validate-cycle-fix
- **Depends On**: build-cycle-fix
- **Assigned To**: cycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new regression test plus the full memory test files and report pass/fail
- Manually run both import orders from the issue's controlled comparison and confirm provider output

### N-1. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-cycle-fix
- **Assigned To**: cycle-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` configuration notes with the import-order guarantee
- Add entry to documentation index if a new feature doc is created

### N. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_memory_model.py -q` | exit code 0 |
| Import-order regression | `.venv/bin/python -c "import models.memory; from popoto.fields.embedding_field import get_default_provider; print(get_default_provider())"` | output contains OpenAIProvider |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | Consolidated Critic | Regression test home path unnamed in task 1; name it (e.g. tests/unit/test_memory_import_order.py) so builder and validator agree | pending | n/a (NIT) |
| NIT | Consolidated Critic | Verification table lists only test_memory_model.py while task 2 also validates test_memory_distill_backfill.py; add the backfill file to the table | pending | n/a (NIT) |
| NIT | Consolidated Critic | Regression test assumes OPENAI_API_KEY reachable via env or repo .env fallback; note the dependency or skip gracefully when no key resolves | pending | n/a (NIT) |

---

## Open Questions

1. Lane-4 evaluation arms (#3216, PR #3309) inherited whichever provider the parent's import order produced. After this fix lands, should those frozen evaluation inputs be re-validated, or do the recorded results stand?

Key assumptions (no input needed unless challenged):
- The lazy back-edge import is sufficient; module relocation is a build-time fallback, not a design choice for review.
- The new warning log on provider failure is acceptable in providerless environments, matching the existing missing-key warning tone.
