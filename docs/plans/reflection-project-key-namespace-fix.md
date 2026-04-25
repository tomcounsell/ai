---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-25
tracking: https://github.com/tomcounsell/ai/issues/1171
last_comment_id:
---

# Reflection project_key namespace fix

## Problem

Half of the 8 reflections that govern queue health and stalled-session recovery are operating on the wrong Redis namespace, so their effects never reach the AgentSession records they're meant to govern.

**Current behavior:**
- `circuit_health_gate` writes `queue_paused`, `worker:hibernating`, `recovery:active`, `worker:recovering` flags to `default:*` (because `agent/sustainability.py:32` falls back to `"default"` when `VALOR_PROJECT_KEY` env var is unset, and no plist injects it).
- `session_recovery_drip` reads the same `default:*` flags and queries `AgentSession.query.filter(project_key="default", ...)`. AgentSession records are tagged `project_key="valor"` (per `tools/agent_session_scheduler.py:71`), so the filter returns nothing.
- `agent/session_pickup.py:180` and `agent/agent_session_queue.py:1408` read `default:sustainability:queue_paused` and `default:worker:hibernating` to gate worker pops and write hibernation flags. They will never see the flags that another part of the system actually wrote.
- Result: paused sessions never get dripped back to `pending`, queue gating is silently disabled, hibernation never engages.

**Desired outcome:**
- All four broken reflections (`circuit-health-gate`, `session-recovery-drip`, plus the queue-pop and hibernation-write call sites that read the same flags) operate on the same Redis namespace as the `valor`-tagged AgentSession records they govern.
- The fix survives a fresh-machine install via `/update` — no manual plist tweaks required after deploy.
- Memory subsystem behavior is preserved (no surprise re-tagging of existing memories) or migrated explicitly.

## Freshness Check

**Baseline commit:** `b39ba285d2f1c25d978148df56abb1c79c623257`
**Issue filed at:** 2026-04-25T15:34:59Z (~6 hours before plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sustainability.py:32` — `os.environ.get("VALOR_PROJECT_KEY", "default")` — still holds
- `agent/session_pickup.py:180` — same fallback pattern — still holds
- `agent/agent_session_queue.py:1408` — same fallback pattern — still holds
- `tools/agent_session_scheduler.py:71` — `DEFAULT_PROJECT_KEY = "valor"` — still holds
- `config/memory_defaults.py:45` — `DEFAULT_PROJECT_KEY = "default"` — still holds
- `tests/e2e/test_session_continuity.py:30` — hard-codes `"valor"` — still holds

**Cited sibling issues/PRs re-checked:**
- #811 — closed 2026-04-07 (memory project_key isolation fix)
- PR #820 — merged 2026-04-07 (cwd threading + DEFAULT_PROJECT_KEY="dm"→"default")
- #773 — closed 2026-04-09 (sustainable self-healing: introduced the broken reflections)

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none — recent plans (telegram-cross-chat-stitching, markitdown-ingestion, progress-detector-tweaks, design-md-integration, child-session-project-scope, waiting-for-children, phantom-pm-twin-dedupe, tts-debrief) touch unrelated subsystems.

**Notes:** Issue claims fully validated. Live Redis confirms `default:sustainability:throttle_level` exists; no `valor:sustainability:*` keys.

## Prior Art

- **#811 / PR #820** — Memory subsystem suffered the analogous bug: `_get_project_key()` fell through to `DEFAULT_PROJECT_KEY = "dm"`, mislabeling 2,376 records. Fix: change `DEFAULT_PROJECT_KEY` to `"default"` and thread `cwd` through hook entry points so cwd-match wins. **Lesson:** the same `_get_project_key()` pattern is duplicated across `memory_bridge.py`, `memory_hook.py`, `memory_extraction.py`, `tools/memory_search/__init__.py`, `agent/sustainability.py`, `agent/session_pickup.py`, `agent/agent_session_queue.py`. The recovery surface was not patched at the time.
- **#773** — Introduced `circuit_health_gate` and `session_recovery_drip` with the `os.environ.get("VALOR_PROJECT_KEY", "default")` pattern. The issue assumed env var would be set. It is not.
- **PR #788** — Derived `project_key` from `projects.json` for `valor-session` CLI, establishing `projects.json` as a project-key registry. The recovery code does not consult this registry.
- **PR #832** — Project-keyed worker serialization. Reinforces that `project_key` is the partitioning axis but does not unify the resolution path.
- **PR #1164** — Enforce immutable project→repo pairing via `projects.json`. Strengthens `projects.json` as source of truth.

## Spike Results

### spike-1: Live memory record namespace distribution
- **Assumption**: "Memory records are written under a single consistent project_key."
- **Method**: code-read (run `Memory.query.all()` and count by `project_key`)
- **Finding**: First 500 records split as `valor: 230`, `default: 198`, `dm: 3`. Memories are already split roughly 50/50 between `valor` and `default` namespaces. SDK-spawned sessions (no env var, no cwd) write to `default`; bridge-injected sessions (cwd matches projects.json) write to `valor`. Recall queries against either namespace miss roughly half the relevant memories. **This is a parallel namespace bug in the memory subsystem.**
- **Confidence**: high
- **Impact on plan**: Choosing env propagation as the fix shifts ALL memory writes from `default` to `valor`. This is a behavior change for memory recall that needs explicit handling — either accept the shift and migrate the 198 `default:*` records to `valor`, or scope the env var more narrowly.

### spike-2: Leftover state in `default:*` top-level
- **Assumption**: "Several `default:*` flags exist in live Redis from broken reflection writes."
- **Method**: code-read (`redis-cli --scan --pattern 'default:*'`)
- **Finding**: Only one key: `default:sustainability:throttle_level`. The hibernation/queue-pause flags have TTLs and have already expired. No `default:recovery:active`, `default:worker:hibernating`, `default:worker:recovering`. Migration footprint for sustainability flags is minimal (1 key, deletable).
- **Confidence**: high
- **Impact on plan**: No migration script needed for sustainability flags — a single `redis-cli DEL default:sustainability:throttle_level` (or `r.delete(...)` via Popoto) suffices. Memory record migration (spike-1) is a larger separate concern.

## Data Flow

This is a multi-component data flow involving the worker process, ReflectionScheduler, and Redis.

1. **Entry point**: Anthropic API call returns 529/overloaded → `bridge.resilience.CircuitBreaker` for `anthropic` transitions to `OPEN`.
2. **Worker process** (`worker/__main__.py:353`) hosts `ReflectionScheduler`. Every 60s it ticks `circuit_health_gate`.
3. **`circuit_health_gate`** (`agent/sustainability.py:42`) reads `bridge.health.get_health()['anthropic']`, sees `OPEN`, computes `pk = os.environ.get("VALOR_PROJECT_KEY", "default")`, sets `f"{pk}:sustainability:queue_paused"` and `f"{pk}:worker:hibernating"` in Redis.
4. **Worker pop loop** (`agent/session_pickup.py:170`) computes its own `_project_key = os.environ.get("VALOR_PROJECT_KEY", "default")`, reads `f"{_project_key}:sustainability:queue_paused"`. **If both fall back to `"default"`, the keys agree** — but session writers (`tools/agent_session_scheduler.py:71`) use a different default (`"valor"`), so AgentSession records are tagged `project_key="valor"` and the session_recovery_drip query never matches.
5. **`session_recovery_drip`** (`agent/sustainability.py:118`) every 30s reads `f"{pk}:recovery:active"` and queries `AgentSession.query.filter(project_key=pk, status="paused_circuit")`. With `pk="default"`, the query returns empty. With `pk="valor"`, the query returns the actual paused sessions.
6. **Output**: when working correctly, one paused session per tick is transitioned `paused_circuit` → `pending` and the worker resumes it.

The break is at step 4-5: the project_key resolved by recovery code (`"default"`) does not match the project_key tagged on AgentSession records (`"valor"`).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #820 (#811) | Fixed `_get_project_key()` in `memory_bridge.py` by changing `DEFAULT_PROJECT_KEY = "dm" → "default"` and threading `cwd` through hook entry points. | Scoped to memory subsystem only. Did not propagate the fix to the recovery surface (`agent/sustainability.py`, `agent/session_pickup.py`, `agent/agent_session_queue.py`), which uses an identical-shaped fallback that does not consult `projects.json`. |
| PR #832 | Project-keyed worker serialization. | Tagged AgentSession records with `project_key` but did not unify how OTHER code resolves `project_key` for direct Redis writes. |
| Initial #773 implementation | Introduced sustainability/hibernation reflections with `os.environ.get("VALOR_PROJECT_KEY", "default")`. | Assumed `VALOR_PROJECT_KEY` would be set in the worker environment. No plist generator injects it. |

**Root cause pattern:** This codebase has at least three distinct `project_key` resolution paths (Popoto AgentSession field set at write time; memory subsystem env-or-cwd-or-default; recovery subsystem env-or-default), each with different fallbacks. They drift when only one is updated. The cure is not "patch the fallback" — it is "every resolution path must agree on the canonical value for this codebase."

## Architectural Impact

- **New dependencies**: None (purely internal).
- **Interface changes**: Possibly none if we go with env-only fix; one helper function signature if we extract a shared `_resolve_project_key()` utility.
- **Coupling**: Decreased — pulling resolution into a single shared helper reduces drift risk between memory and recovery subsystems.
- **Data ownership**: No changes — `projects.json` remains the registry; AgentSession.project_key remains the partitioning field.
- **Reversibility**: Fully reversible. The only persistent effect is shifting writes to `valor:*` namespace; old `default:*` keys can be deleted manually.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (resolve the three open questions before build)
- Review rounds: 1 (post-build PR review by Valor/Tom)

This is a tightly scoped fix touching ~5 files and 1-2 plist generators. The complexity is in the design decision (which resolution strategy), not the implementation. Once the strategy is chosen, the build is hours not days.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Live Redis running | `redis-cli ping` | Required to verify migration step works on real state |
| Worker process is restartable | `./scripts/valor-service.sh worker-status` | Required to redeploy worker plist with new env var |
| `~/Desktop/Valor/.env` exists and is writable | `test -w ~/Desktop/Valor/.env && echo OK` | Required if Option B (env-level propagation) is chosen |
| `~/Desktop/Valor/projects.json` exists | `test -f ~/Desktop/Valor/projects.json && echo OK` | Required if Option C (config-driven resolution) is chosen |

## Solution

### Key Elements

This plan presents three resolution strategies. **Recommended: Option B (env propagation).** The choice is the substance of the open question and should be confirmed by Tom before build.

- **Option A — Code-level alignment**: Change every `os.environ.get("VALOR_PROJECT_KEY", "default")` site to `... "valor"`. Smallest deploy footprint (just `git pull`), but multiplies the number of places that hard-code the canonical project name and conflicts with `config/memory_defaults.py:45` which keeps `"default"` for memory.
- **Option B — Env-level propagation (recommended)**: Add `VALOR_PROJECT_KEY=valor` to `~/Desktop/Valor/.env`. The bridge plist generator at `scripts/valor-service.sh:482-521` already injects all `.env` vars into the plist. Worker plist generator at `scripts/install_worker.sh:88-131` does the same. `/update` re-runs both generators. Code stays unchanged; the canonical project name lives in one config file.
- **Option C — Config-driven resolution**: Refactor recovery code to call `_get_project_key(cwd=os.getcwd())` like the memory subsystem does. Resolution becomes self-aware via `projects.json` regardless of env var. Largest refactor; highest principal-of-least-surprise alignment but adds a `projects.json` read to every reflection tick.

### Flow

**Worker startup** → reads injected `VALOR_PROJECT_KEY=valor` from plist EnvironmentVariables → `circuit_health_gate` writes flags to `valor:sustainability:*` → `session_recovery_drip` queries AgentSession by `project_key="valor"` → matches actual paused sessions → drips them back to `pending`.

### Technical Approach

**Recommended path (Option B):**

1. Add `VALOR_PROJECT_KEY=valor` to `~/Desktop/Valor/.env` and to `.env.example` (with explanatory comment per Plan Requirements: secrets policy in CLAUDE.md).
2. Verify both plist generators (`scripts/valor-service.sh` for bridge, `scripts/install_worker.sh` for worker) inject `.env` vars into the plist `EnvironmentVariables` dict — they already do, no script changes needed. The `bridge-watchdog` plist (in `valor-service.sh:573-609`) does NOT need it since the watchdog doesn't run reflections.
3. Re-run `./scripts/valor-service.sh install` and `./scripts/install_worker.sh` to bake the new env var into the live plists; restart bridge and worker.
4. Verify post-restart: `launchctl getenv VALOR_PROJECT_KEY` (per process) and live Redis writes go to `valor:*`.
5. Clean up the one stale key: `default:sustainability:throttle_level` (delete via `r.delete(...)`).
6. Memory subsystem: addressed in **Open Question 2** — defer to Tom's call on whether to migrate the 198 `default:*` Memory records to `valor:*` as part of this plan or split into a follow-up.
7. Add a regression test that asserts the env var is set in launchd-managed processes (or a unit test that asserts `_get_project_key()` returns the expected value in the production environment).

**Note on Option B and `default:` literal in code:**
- The fallback string `"default"` in `agent/sustainability.py:32`, `agent/session_pickup.py:180`, and `agent/agent_session_queue.py:1408` is no longer reachable in production with the env var set, but should still be kept as a safety net. Optionally change it to `"valor"` so the fallback is also correct (defense in depth). This is a pure code change with no deploy implications.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/sustainability.py:circuit_health_gate` wraps the entire body in `try/except Exception` (line ~55-110) and logs without raising. **No new exception handlers introduced** by this fix. Existing handler is fine.
- [ ] `agent/sustainability.py:session_recovery_drip` same pattern. **No new exception handlers introduced.**
- [ ] If we change `_get_project_key()` to a shared helper (per Option C), the helper must catch `projects.json` read errors and fall back to a known constant — test that an unreadable `projects.json` returns the canonical fallback (not `"default"`).

### Empty/Invalid Input Handling
- [ ] Empty `VALOR_PROJECT_KEY` env var (e.g., `VALOR_PROJECT_KEY=""`): currently treated as "set" by `os.environ.get(..., "default")` returning `""`. Add a defensive check: if value is empty after strip, treat as unset. Test with `monkeypatch.setenv("VALOR_PROJECT_KEY", "")`.
- [ ] None — env vars are always strings.

### Error State Rendering
- [ ] No user-visible output. Reflection failures log to `logs/worker.log` and never reach Telegram. Existing logging is sufficient.

## Test Impact

- [ ] `tests/unit/test_sustainability.py:434,496` — currently sets `VALOR_PROJECT_KEY=testproj` to override; UPDATE: assert that with no env override, the resolved project_key is `"valor"` (the canonical default), not `"default"`. Adds one new test case.
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py:56` — sets `VALOR_PROJECT_KEY=default`; UPDATE: change to `valor` so the test reflects the new canonical default.
- [ ] `tests/unit/test_memory_bridge.py:551,562,574,577` — tests `_get_project_key` env precedence; KEEP AS IS — these tests verify env-var precedence behavior which doesn't change.
- [ ] `tests/e2e/test_session_continuity.py:30` — hard-codes `project_key="valor"`; KEEP AS IS — this is now the canonical value.
- [ ] **Add new test**: `tests/unit/test_sustainability_namespace.py` — assert that with `VALOR_PROJECT_KEY=valor` set, `circuit_health_gate` writes flags to `valor:sustainability:queue_paused`, and that `session_recovery_drip` queries `AgentSession.query.filter(project_key="valor")`. This is the regression test that catches future drift.
- [ ] **Add new test**: `tests/unit/test_default_project_key_consistency.py` — assert that `tools.agent_session_scheduler.DEFAULT_PROJECT_KEY == "valor"` matches the value of `VALOR_PROJECT_KEY` resolved by `agent.sustainability._get_project_key()` in a controlled env. Catches drift between writer and reader defaults.

## Rabbit Holes

- **Rewriting `_get_project_key()` as a single shared helper across memory + recovery subsystems**: tempting but expands scope from a small bug fix to a refactor touching 9+ files and changing import graphs. Defer to a follow-up if/when a third subsystem needs the same resolution.
- **Migrating all 198 `default:*` Memory records to `valor:*`**: this is the bigger sibling concern surfaced by spike-1. It deserves its own issue and migration plan (with dry-run, with confidence check on memory recall behavior). Do not bundle it into this fix unless Tom decides otherwise (Open Question 2).
- **Per-machine project_key configuration**: this codebase currently deploys as `valor` on a single machine. Generalizing to per-machine project_keys is multi-deployment infrastructure work, out of scope here.
- **Rewriting reflections to be project-agnostic** (recovering all sessions regardless of project_key): real concern if the bridge ever serves multiple projects, but the bridge is currently single-project so this would be premature.

## Risks

### Risk 1: Memory subsystem behavior shift after `VALOR_PROJECT_KEY=valor` is set in env
**Impact:** All memory hooks and SDK-path memory writes that previously fell through to `DEFAULT_PROJECT_KEY = "default"` will now resolve to `"valor"`. Recall queries from sessions that were finding `default`-tagged memories will start missing them. This is the spike-1 finding.
**Mitigation:** Either (a) decide explicitly to migrate the 198 `default:*` records to `valor:*` (run a one-shot script using Popoto's `Memory` model — never raw Redis on Popoto-managed keys per CLAUDE.md), OR (b) scope the env var more narrowly so memory subsystem keeps using `config/memory_defaults.py` while recovery subsystem gets a different env var (e.g., rename to `VALOR_RECOVERY_PROJECT_KEY` — uglier but bounded). Tom decides via Open Question 2.

### Risk 2: Plist on disk does not get regenerated
**Impact:** `~/Desktop/Valor/.env` is the source of truth, but the actual env vars come from the plist's baked `EnvironmentVariables` dict. Adding `VALOR_PROJECT_KEY=valor` to `.env` has no effect until the plist is rebuilt via `./scripts/valor-service.sh install` or `./scripts/install_worker.sh`.
**Mitigation:** The `/update` skill (specifically `scripts/remote-update.sh`) already runs the install scripts on each machine. **Verify** that `scripts/remote-update.sh` calls both `valor-service.sh install` AND `install_worker.sh` on update — if it only calls one, the worker plist won't pick up the new var until manual re-install.

### Risk 3: Live worker plist on this machine is missing other env vars
**Impact:** The current `~/Library/LaunchAgents/com.valor.worker.plist` only contains PATH/HOME/VALOR_LAUNCHD — none of the other 30+ vars from `.env`. Either the install script's env injection is broken on this machine, or the plist was installed before the injection code was added. Adding `VALOR_PROJECT_KEY` to `.env` won't help if the injection isn't running.
**Mitigation:** Before declaring the fix done, manually `./scripts/install_worker.sh` and verify `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables" ~/Library/LaunchAgents/com.valor.worker.plist` shows VALOR_PROJECT_KEY=valor. If the injection is broken, that's a separate sub-bug to fix as part of this plan.

### Risk 4: Bridge-watchdog and worker-watchdog plists also write Redis flags
**Impact:** If the watchdog processes write any `{project_key}:*` flags, they would also need the env var. Their plists do NOT inject `.env`.
**Mitigation:** Audit `monitoring/bridge_watchdog.py` and `monitoring/worker_watchdog.py` for any Redis writes that use `os.environ.get("VALOR_PROJECT_KEY", ...)`. If found, either add env var to those plists or refactor those writes.

## Race Conditions

No race conditions introduced. All affected operations are existing reflection ticks running on a fixed schedule under the worker's event loop. No new concurrent access patterns.

## No-Gos (Out of Scope)

- Migrating Memory records from `default:*` to `valor:*` (Risk 1) — separate issue if Tom chooses to defer.
- Refactoring `_get_project_key()` into a single shared helper across all subsystems — separate issue.
- Per-machine or per-project recovery namespacing — codebase is single-project.
- Fixing the broken plist injection on this machine if it turns out to be a deeper bug (Risk 3) — escalate as a separate finding if encountered.
- Changing `config/memory_defaults.py:DEFAULT_PROJECT_KEY` from `"default"` to anything else — explicitly a separate concern (memory subsystem owns its own default).

## Update System

The fix is delivered via `scripts/remote-update.sh` which calls `scripts/update/env_sync.py` to sync `.env` and then re-runs the bridge install. **Required check:** confirm `remote-update.sh` calls `install_worker.sh` (or equivalent worker plist regeneration) — if it does NOT, add it to the update skill, since the worker plist needs to be re-baked to pick up the new env var.

- [ ] Audit `scripts/remote-update.sh` for `install_worker.sh` invocation. If absent, add it.
- [ ] Audit `.claude/skills/update/SKILL.md` for the same.
- [ ] No new dependencies, services, or config files beyond the new env var line in `.env.example`.

## Agent Integration

No agent integration required — this is a worker/bridge internal change. The reflections are launchd-scheduled background tasks. The agent (PM/Dev sessions) does not invoke them.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sustainable-self-healing.md` — replace any mention of `${VALOR_PROJECT_KEY:-default}` with `${VALOR_PROJECT_KEY:-valor}` (and update example `redis-cli` commands at lines 108, 113, 118, 123).
- [ ] Update `docs/features/worker-hibernation.md` — same project_key references.
- [ ] Update `docs/features/subconscious-memory.md` — IF Open Question 2 resolves to "migrate memory namespace too," update the project_key resolution table at lines 402-415.
- [ ] Update `docs/features/claude-code-memory.md` — same condition as above.
- [ ] Add a brief entry to `docs/plans/memory-project-key-isolation.md` (or its archived form) noting that the recovery surface was patched in this plan.

### External Documentation Site
None — this repo does not use Sphinx/MkDocs/RTD.

### Inline Documentation
- [ ] Add a comment near `agent/sustainability.py:30` explaining why the fallback is `"valor"` (or `"default"`) — point at this plan/issue so future readers don't drift it back.
- [ ] Update the docstring at the top of `agent/sustainability.py:14-18` (Redis key schema) to mention that `{project_key}` resolves to `valor` in production, set via `VALOR_PROJECT_KEY` env var injected by plist generators.

## Success Criteria

- [ ] After deploy, `redis-cli --scan --pattern 'valor:sustainability:*'` returns at least one key during the next circuit-pause event (synthetic test or natural occurrence).
- [ ] After deploy, `redis-cli --scan --pattern 'default:sustainability:*'` returns zero keys (one-time cleanup of stale state succeeded).
- [ ] `launchctl getenv VALOR_PROJECT_KEY` (or `ps eww $WORKER_PID | grep VALOR_PROJECT_KEY`) shows `valor` for the live worker process.
- [ ] Synthetic test: write a `paused_circuit` AgentSession with `project_key="valor"`, set `valor:recovery:active` flag, run `session_recovery_drip` once, verify the session transitions to `pending`.
- [ ] All 8 reflections in `config/reflections.yaml` (or just the 4 broken ones) verified working via `tail -f logs/worker.log` over 5 minutes — no namespace-mismatch errors, expected log lines from each reflection's debug branch.
- [ ] Tests pass (`/do-test`) — including the two new tests under Test Impact.
- [ ] Documentation updated (`/do-docs`).
- [ ] Issue #1171 closed by the implementation PR (`Closes #1171`).
- [ ] No new xfail/xpass tests (no related xfails exist; nothing to convert).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (env-and-config)**
  - Name: env-config-builder
  - Role: Add `VALOR_PROJECT_KEY=valor` to `.env` (vault) and `.env.example`. Update `.env.example` comment per CLAUDE.md secrets policy.
  - Agent Type: builder
  - Resume: true

- **Builder (code-and-tests)**
  - Name: recovery-code-builder
  - Role: Update fallback strings in `agent/sustainability.py:32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408` from `"default"` to `"valor"` (defense in depth). Add the two new regression tests under Test Impact. Update `tests/unit/test_session_health_sibling_phantom_safety.py:56`. Update inline docstrings.
  - Agent Type: builder
  - Resume: true

- **Builder (deploy-and-cleanup)**
  - Name: deploy-cleanup-builder
  - Role: Run `./scripts/valor-service.sh install` and `./scripts/install_worker.sh` to re-bake plists. Restart bridge and worker. Delete stale `default:sustainability:throttle_level` via Popoto-safe Redis call. Verify env var is live in worker process.
  - Agent Type: builder
  - Resume: true

- **Builder (update-skill-audit)**
  - Name: update-skill-builder
  - Role: Audit `scripts/remote-update.sh` and `.claude/skills/update/SKILL.md` for worker plist regeneration. Add the call if absent.
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: docs-builder
  - Role: Update `docs/features/sustainable-self-healing.md`, `docs/features/worker-hibernation.md`, and conditionally the memory docs. Update inline comments/docstrings per Documentation section.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: namespace-validator
  - Role: Run synthetic recovery test, verify all success criteria, confirm no `default:sustainability:*` keys, confirm worker process has `VALOR_PROJECT_KEY=valor`.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update env vault
- **Task ID**: build-env-vault
- **Depends On**: none
- **Validates**: `grep -q VALOR_PROJECT_KEY ~/Desktop/Valor/.env && grep -q VALOR_PROJECT_KEY .env.example`
- **Informed By**: Solution → Recommended path step 1
- **Assigned To**: env-config-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `VALOR_PROJECT_KEY=valor` to `~/Desktop/Valor/.env`
- Add `VALOR_PROJECT_KEY=valor` to `.env.example` with a one-line comment above explaining "Project namespace prefix used by recovery reflections; matches projects.json key for this codebase."

### 2. Update fallback strings + add regression tests
- **Task ID**: build-code-defense
- **Depends On**: none
- **Validates**: `tests/unit/test_sustainability_namespace.py`, `tests/unit/test_default_project_key_consistency.py` (both new)
- **Informed By**: Spike-1, Spike-2, Test Impact
- **Assigned To**: recovery-code-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `agent/sustainability.py:32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408`: change `"default"` fallback to `"valor"`. Add a comment pointing to issue #1171 with one sentence on why.
- Add docstring update at top of `agent/sustainability.py:14-18` with a note on env-var sourcing.
- Update `tests/unit/test_session_health_sibling_phantom_safety.py:56`: change `VALOR_PROJECT_KEY=default` to `VALOR_PROJECT_KEY=valor`.
- Create `tests/unit/test_sustainability_namespace.py` per Test Impact.
- Create `tests/unit/test_default_project_key_consistency.py` per Test Impact.

### 3. Audit update skill
- **Task ID**: build-update-skill
- **Depends On**: none
- **Validates**: `grep -q install_worker.sh scripts/remote-update.sh && grep -q install_worker.sh .claude/skills/update/SKILL.md`
- **Informed By**: Risk 2, Risk 3
- **Assigned To**: update-skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `scripts/remote-update.sh` and `.claude/skills/update/SKILL.md`. Verify worker plist regeneration is invoked. If absent, add an appropriate call.

### 4. Validate code changes
- **Task ID**: validate-code
- **Depends On**: build-code-defense, build-env-vault
- **Assigned To**: namespace-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py tests/unit/test_session_health_sibling_phantom_safety.py -v` — must pass.
- Run `python -m ruff check agent/sustainability.py agent/session_pickup.py agent/agent_session_queue.py` — must pass.

### 5. Deploy + cleanup stale state
- **Task ID**: build-deploy
- **Depends On**: validate-code, build-update-skill
- **Validates**: `launchctl getenv VALOR_PROJECT_KEY` (manual) and `redis-cli --scan --pattern 'default:sustainability:*' | wc -l` returns 0
- **Informed By**: Spike-2, Risk 3
- **Assigned To**: deploy-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `./scripts/valor-service.sh install`. Confirm plist injection log line shows new var count.
- Run `./scripts/install_worker.sh`. Confirm same.
- Run `./scripts/valor-service.sh restart`.
- Verify env var via `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.worker.plist` returns `valor`.
- Delete stale key: `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.delete('default:sustainability:throttle_level')"`. (Note: this is a top-level non-Popoto-managed key, so direct `r.delete` is appropriate per CLAUDE.md — the rule against raw Redis applies to **Popoto-managed model keys**, not ad-hoc string flags.)

### 6. Synthetic recovery test
- **Task ID**: validate-recovery
- **Depends On**: build-deploy
- **Assigned To**: namespace-validator
- **Agent Type**: validator
- **Parallel**: false
- Create a `paused_circuit` AgentSession with `project_key="valor"` via Popoto.
- Set `valor:recovery:active` flag in Redis with TTL 60s.
- Manually invoke `python -c "from agent.sustainability import session_recovery_drip; session_recovery_drip()"`.
- Verify session transitions to `pending` via `valor-session status --id <id>`.
- Clean up the test session.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-recovery
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sustainable-self-healing.md`, `docs/features/worker-hibernation.md` per Documentation section.
- IF Open Question 2 resolves to "migrate memories too," update memory docs accordingly; otherwise note explicitly that memory namespace migration is deferred to follow-up.
- Update inline docstrings.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: namespace-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all checks in `## Verification` table.
- Verify all `## Success Criteria` checkboxes.
- Tail `logs/worker.log` for 5 minutes; report any namespace-mismatch errors.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py tests/unit/test_session_health_sibling_phantom_safety.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/sustainability.py agent/session_pickup.py agent/agent_session_queue.py tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sustainability.py agent/session_pickup.py agent/agent_session_queue.py` | exit code 0 |
| Env var in worker plist | `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.worker.plist` | output contains `valor` |
| Env var in bridge plist | `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.bridge.plist` | output contains `valor` |
| No stale default flags | `redis-cli --scan --pattern 'default:sustainability:*' \| wc -l` | output > -1 (any count, but should be 0 — verify post-cleanup) |
| .env has new var | `grep -c VALOR_PROJECT_KEY ~/Desktop/Valor/.env` | output > 0 |
| .env.example has new var | `grep -c VALOR_PROJECT_KEY .env.example` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Which resolution strategy do we adopt?** Recommended **Option B (env-level propagation)** per the rationale in Solution → Key Elements. Options A (code-level alignment) and C (config-driven via projects.json) are also viable. Picking the wrong one wastes about a half-day of rework, so worth confirming before build.

2. **Memory subsystem namespace migration — in or out of scope?** Spike-1 found 198 Memory records currently tagged `project_key="default"` and 230 tagged `project_key="valor"`. Setting `VALOR_PROJECT_KEY=valor` env-wide will shift future writes to `valor`, splitting recall behavior. Three sub-options:
   - **(a)** Migrate the 198 `default:*` records to `valor:*` as part of this plan (one-shot Popoto-safe script, ~30 lines).
   - **(b)** Leave them in `default` and accept that some old memories become unreachable (they were largely created during the broken-namespace period anyway).
   - **(c)** Defer to a follow-up issue with its own dry-run-first plan.
   Recommendation: **(c)** — keep this plan tight; file a follow-up.

3. **Should the `"default"` fallback strings in `agent/sustainability.py:32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408` be changed to `"valor"`?** With Option B, the env var is always set in production, so the fallback is unreachable there — but it IS reached during local pytest runs and ad-hoc Python invocations without env. Changing to `"valor"` is defense-in-depth at zero cost. Recommendation: **yes**, include in this fix.
