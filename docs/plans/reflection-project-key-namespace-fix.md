---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-25
tracking: https://github.com/tomcounsell/ai/issues/1171
last_comment_id:
revision_applied: true
revision_pass: 1
revision_addressed: [B1, C1, C2, C3, C4, N1]
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
2. **Port `.env` injection into `scripts/update/service.py::install_worker`** (per B1, see Implementation Note below). The standalone `scripts/install_worker.sh:95-131` already injects `.env` vars, but `/update --full` calls `scripts/update/service.py::install_worker()` (which only does template substitution, not env injection). Without this port, `/update --full` will write a worker plist that lacks `VALOR_PROJECT_KEY`. Live verification on this machine confirms the worker plist currently has only PATH/HOME/VALOR_LAUNCHD — exactly the failure mode B1 predicts.
3. Verify the bridge plist generator (`scripts/valor-service.sh:482-521`) injects `.env` vars into the plist `EnvironmentVariables` dict — it already does. The `bridge-watchdog` plist (in `valor-service.sh:573-609`) does NOT need it since the watchdog doesn't run reflections.
4. Re-run `./scripts/valor-service.sh install` AND run `python -m scripts.update.run --full` (or `./scripts/install_worker.sh` directly for one-shot) to bake the new env var into the live plists; restart bridge and worker.
5. Verify post-deploy: `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.worker.plist` returns `valor` (per process) and live Redis writes go to `valor:*`.
6. Clean up the one stale key: `default:sustainability:throttle_level` (delete via `r.delete(...)` — this is an ad-hoc string flag, NOT a Popoto-managed model key, so direct `r.delete` is correct per CLAUDE.md `feedback_never_raw_delete_popoto.md`).
7. **Memory record migration is now in scope** (per C1 revision): re-tag the 207 `default:*` and 4 `dm:*` Memory records to `valor:*` via Popoto-safe migration script (see Implementation Note below). Run BEFORE the worker restart in step 4 so post-restart recall queries find the records. The `dm` writer source is filed as a sibling investigation (out of scope here, but tracked).
8. Add the regression tests under Test Impact, including an empty-string defense test and an e2e default-project-key test.

**B1 Implementation Note (port .env injection into `scripts/update/service.py`):**
- Function to edit: `scripts/update/service.py::install_worker()` at line 196.
- After `plist_dst.write_text(plist_text)` at line 235 and BEFORE `launchctl bootstrap` at line 237, insert the env injection block adapted from `scripts/install_worker.sh:95-131`.
- Use `from dotenv import dotenv_values` and `import plistlib`.
- Read `.env` from `project_dir / ".env"` (which is the symlink to `~/Desktop/Valor/.env`).
- Open the freshly-written plist, `setdefault("EnvironmentVariables", {})`, then for each key in `dotenv_values(env_file)`: skip if `key in existing` (preserve the three placeholder vars `PATH`/`HOME`/`VALOR_LAUNCHD`), skip if `value is None`. Save the plist back via `plistlib.dump`.
- Wrap the entire block in `try/except Exception` and log a warning on failure (the worker should still start with a degraded plist; the regression test will catch the missing var).

**C1 Implementation Note (memory record migration):**
- Migration script template (run as a one-shot before worker restart):
  ```python
  from models.memory import Memory
  for old_pk in ("default", "dm"):
      records = list(Memory.query.filter(project_key=old_pk))
      print(f"Migrating {len(records)} records from {old_pk} → valor")
      for m in records:
          m.project_key = "valor"
          m.save()
  ```
- Run with a dry-run flag first (`--dry-run` prints counts only, no writes).
- NEVER use raw `r.delete`/`r.hset` — this is a Popoto-managed model, so use `Memory.query` and `m.save()` per CLAUDE.md `feedback_never_raw_delete_popoto.md`.
- The `dm` namespace leak source (4 records, growing) is out of scope here — track separately by grepping `cwd` references in `agent/memory_hook.py:83`, `tools/memory_search/__init__.py:48`, and `.claude/hooks/hook_utils/memory_bridge.py:140` for stale `dm` project lookups.

**C2 Implementation Note (empty-string defense):**
- The bare `os.environ.get("VALOR_PROJECT_KEY", "valor")` does NOT handle `VALOR_PROJECT_KEY=""` (empty string passes through). The plist injector at `install_worker.sh:122` uses `if value is not None`, so `""` would land in the plist if mis-configured.
- Replace each call site with: `_v = os.environ.get("VALOR_PROJECT_KEY", "").strip(); _pk = _v or "valor"`.
- Apply at `agent/sustainability.py:30-32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408`.
- Test with both `monkeypatch.setenv("VALOR_PROJECT_KEY", "")` and `monkeypatch.setenv("VALOR_PROJECT_KEY", "  ")` — both must resolve to `"valor"`.

**Note on Option B and `default:` literal in code:**
- The fallback string `"default"` in `agent/sustainability.py:32`, `agent/session_pickup.py:180`, and `agent/agent_session_queue.py:1408` is no longer reachable in production with the env var set, but should still be kept as a safety net. Per C2 above, change to `"valor"` AND add the empty-string defense (one-line helper). This is a pure code change with no deploy implications.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/sustainability.py:circuit_health_gate` wraps the entire body in `try/except Exception` (line ~55-110) and logs without raising. **No new exception handlers introduced** by this fix. Existing handler is fine.
- [ ] `agent/sustainability.py:session_recovery_drip` same pattern. **No new exception handlers introduced.**
- [ ] If we change `_get_project_key()` to a shared helper (per Option C), the helper must catch `projects.json` read errors and fall back to a known constant — test that an unreadable `projects.json` returns the canonical fallback (not `"default"`).

### Empty/Invalid Input Handling
- [ ] Empty `VALOR_PROJECT_KEY` env var (e.g., `VALOR_PROJECT_KEY=""`): currently treated as "set" by `os.environ.get(..., "default")` returning `""`. **Fix codified in Solution → C2 Implementation Note**: replace each call site with `_v = os.environ.get("VALOR_PROJECT_KEY", "").strip(); _pk = _v or "valor"`. **Task 2 explicitly applies this; test added in Test Impact.** Verified with both `monkeypatch.setenv("VALOR_PROJECT_KEY", "")` and `monkeypatch.setenv("VALOR_PROJECT_KEY", "  ")`.
- [ ] None — env vars are always strings.

### Error State Rendering
- [ ] No user-visible output. Reflection failures log to `logs/worker.log` and never reach Telegram. Existing logging is sufficient.

## Test Impact

- [ ] `tests/unit/test_sustainability.py:434,496` — currently sets `VALOR_PROJECT_KEY=testproj` to override; UPDATE: assert that with no env override, the resolved project_key is `"valor"` (the canonical default), not `"default"`. Adds one new test case.
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py:56` — sets `VALOR_PROJECT_KEY=default`; UPDATE: change to `valor` so the test reflects the new canonical default.
- [ ] `tests/unit/test_memory_bridge.py:551,562,574,577` — tests `_get_project_key` env precedence; KEEP AS IS — these tests verify env-var precedence behavior which doesn't change.
- [ ] `tests/e2e/test_session_continuity.py:30` — hard-codes `project_key="valor"`; UPDATE per C3: add a NEW test case `test_default_project_key_when_unspecified` that constructs an `AgentSession` (or invokes the writer code path) WITHOUT explicit `project_key=` and asserts the persisted record has `project_key == "valor"`. Use `monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)` at the test scope so the env injection from the test runner does not mask the writer-side default. This is the e2e companion to `test_default_project_key_consistency.py`.
- [ ] **Add new test**: `tests/unit/test_sustainability_namespace.py` — assert that with `VALOR_PROJECT_KEY=valor` set, `circuit_health_gate` writes flags to `valor:sustainability:queue_paused`, and that `session_recovery_drip` queries `AgentSession.query.filter(project_key="valor")`. This is the regression test that catches future drift.
- [ ] **Add new test**: `tests/unit/test_default_project_key_consistency.py` — assert that `tools.agent_session_scheduler.DEFAULT_PROJECT_KEY == "valor"` matches the value of `VALOR_PROJECT_KEY` resolved by `agent.sustainability._get_project_key()` in a controlled env. Catches drift between writer and reader defaults.
- [ ] **Add new test (C2 empty-string defense)**: in `tests/unit/test_default_project_key_consistency.py`, add `test_empty_env_falls_back_to_valor` and `test_whitespace_env_falls_back_to_valor` cases. Set `VALOR_PROJECT_KEY=""` and `VALOR_PROJECT_KEY="  "` via `monkeypatch.setenv`; assert that `agent.sustainability._get_project_key()`, `agent.session_pickup`'s computed `_project_key`, and `agent.agent_session_queue`'s computed `_pk` ALL resolve to `"valor"`.
- [ ] **Add new test (B1 env injection regression)**: in `tests/unit/test_update_install_worker.py` (new file), call `scripts.update.service.install_worker(project_dir)` against a temp project_dir containing a stub plist + a stub `.env` with `VALOR_PROJECT_KEY=valor`, then read the destination plist and assert `EnvironmentVariables["VALOR_PROJECT_KEY"] == "valor"`. This catches the B1 bug at the unit level — without it, B1 can recur silently if a future refactor drops the injection block.

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

### Risk 4: Other launchd-managed services may also need the env var
**Impact:** If any launchd-managed service writes project-keyed Redis flags, it would also need `VALOR_PROJECT_KEY` in its plist. The watchdog plists do NOT inject `.env`.
**Mitigation (audit completed during critique):** Audited the full launchd surface:
- `monitoring/bridge_watchdog.py` and `monitoring/worker_watchdog.py` — clean (no `VALOR_PROJECT_KEY` references, no project-keyed Redis flag writes).
- `bridge/email_bridge.py` — derives `project_key` from `projects.json` directly (per `email_bridge.py:702`); does NOT use `VALOR_PROJECT_KEY` env var. Clean.
- `bridge/email_relay.py`, `bridge/email_dead_letter.py` — clean.
- `scripts/log_rotate.py` — clean (no Redis interaction).
- `scripts/update/run.py` — runs interactively; not a long-lived service.
- `com.valor.autoexperiment.plist`, `com.valor.nightly-tests.plist`, `com.valor.sdlc-reflection.plist`, `com.valor.log-rotate.plist` — these run scripts that don't currently use `VALOR_PROJECT_KEY`, but the autoexperiment and SDLC reflection schedulers DO write to AgentSession records. Verify their plists inject `.env` (handled by `scripts/install_*.sh` for each), and confirm via `PlistBuddy` post-deploy that `VALOR_PROJECT_KEY=valor` is present.
**Net result:** No code changes required for watchdogs or email bridge. Only the worker plist (via B1 fix) and the scheduled launchd plists need the env var injection — the scheduled plists currently DO inject `.env` if their respective `install_*.sh` scripts run during `/update`. **Verify this for `com.valor.autoexperiment.plist` and `com.valor.sdlc-reflection.plist` post-deploy** — add a Verification row.

### Risk 5 (B1, formerly blocker): `/update --full` does not inject `.env` into worker plist
**Impact:** `scripts/update/service.py::install_worker()` (called by `/update --full` via `run.py:820`) only does template substitution (`__PROJECT_DIR__`, `__HOME_DIR__`, `__SERVICE_LABEL__`). It does NOT call the env-injection block from `scripts/install_worker.sh:95-131`. After deploy via `/update --full`, the worker plist will have only PATH/HOME/VALOR_LAUNCHD — `VALOR_PROJECT_KEY` will be missing, the recovery code will fall back, and the bug persists. Live verification on this machine confirms exactly this state today.
**Mitigation:** Port the `.env` injection block into `scripts/update/service.py::install_worker()` (see Solution → B1 Implementation Note). Add a Verification row that asserts the env var lands in the worker plist AFTER `python -m scripts.update.run --full` (not after a manual `install_worker.sh` invocation). Add a unit test `test_update_install_worker.py` (per Test Impact) that catches a future refactor dropping the injection block.

## Race Conditions

No race conditions introduced. All affected operations are existing reflection ticks running on a fixed schedule under the worker's event loop. No new concurrent access patterns.

## No-Gos (Out of Scope)

- Migrating Memory records from `default:*`/`dm:*` to `valor:*` is **now in scope** (per C1 revision) — see Task 4.5. The remaining Memory-related items below stay out of scope.
- Refactoring `_get_project_key()` into a single shared helper across all subsystems — separate issue.
- Tracking down and fixing the `dm` writer leak source — sibling investigation issue (filed by Task 4.5); not patched in this plan.
- Per-machine or per-project recovery namespacing — codebase is single-project.
- Changing `config/memory_defaults.py:DEFAULT_PROJECT_KEY` from `"default"` to anything else — explicitly a separate concern (memory subsystem owns its own default; the migration in Task 4.5 handles existing records but does not touch the constant).

## Update System

The fix is delivered via `scripts/remote-update.sh` which calls `scripts/update/env_sync.py` to sync `.env` and then re-runs the bridge install. **Required check:** confirm `remote-update.sh` calls `install_worker.sh` (or equivalent worker plist regeneration) — if it does NOT, add it to the update skill, since the worker plist needs to be re-baked to pick up the new env var.

- [ ] Audit `scripts/remote-update.sh` for `install_worker.sh` invocation. If absent, add it.
- [ ] Audit `.claude/skills/update/SKILL.md` for the same.
- [ ] No new dependencies, services, or config files beyond the new env var line in `.env.example`.

## Agent Integration

No agent integration required — this is a worker/bridge internal change. The reflections are launchd-scheduled background tasks. The agent (PM/Dev sessions) does not invoke them.

## Documentation

### Feature Documentation
- [x] Update `docs/features/sustainable-self-healing.md` — replace any mention of `${VALOR_PROJECT_KEY:-default}` with `${VALOR_PROJECT_KEY:-valor}` (and update example `redis-cli` commands at lines 108, 113, 118, 123).
- [x] Update `docs/features/worker-hibernation.md` — same project_key references.
- [x] Update `docs/features/subconscious-memory.md` — update the project_key resolution table at lines 402-415 to reflect the canonical `valor` namespace and document the one-shot migration that ran (Task 4.5).
- [x] Update `docs/features/claude-code-memory.md` — same as above.
- [x] Add a brief entry to `docs/plans/memory-project-key-isolation.md` (or its archived form) noting that the recovery surface was patched in this plan and the residual `default`/`dm` Memory records were migrated to `valor`.

### External Documentation Site
None — this repo does not use Sphinx/MkDocs/RTD.

### Inline Documentation
- [x] Add a comment near `agent/sustainability.py:30` explaining why the fallback is `"valor"` (or `"default"`) — point at this plan/issue so future readers don't drift it back.
- [x] Update the docstring at the top of `agent/sustainability.py:14-18` (Redis key schema) to mention that `{project_key}` resolves to `valor` in production, set via `VALOR_PROJECT_KEY` env var injected by plist generators.

## Success Criteria

- [ ] After deploy, `redis-cli --scan --pattern 'valor:sustainability:*'` returns at least one key during the next circuit-pause event (synthetic test or natural occurrence).
- [ ] After deploy, `redis-cli --scan --pattern 'default:sustainability:*'` returns zero keys (one-time cleanup of stale state succeeded).
- [ ] `launchctl getenv VALOR_PROJECT_KEY` (or `ps eww $WORKER_PID | grep VALOR_PROJECT_KEY`) shows `valor` for the live worker process.
- [ ] B1 regression: after running `python -m scripts.update.run --full` (NOT `install_worker.sh` directly), the worker plist contains `VALOR_PROJECT_KEY=valor` per `PlistBuddy`. Proves the env injection block was successfully ported into `scripts/update/service.py::install_worker()`.
- [ ] Memory migration: `Memory.query.filter(project_key="default").count() == 0` AND `Memory.query.filter(project_key="dm").count() == 0` post-migration. The total count of `valor`-tagged Memory records increases by the migrated count.
- [ ] Synthetic test: write a `paused_circuit` AgentSession with `project_key="valor"`, set `valor:recovery:active` flag, run `session_recovery_drip` once, verify the session transitions to `pending`.
- [ ] All 8 reflections in `config/reflections.yaml` (or just the 4 broken ones) verified working via `tail -f logs/worker.log` over 5 minutes — no namespace-mismatch errors, expected log lines from each reflection's debug branch.
- [ ] Tests pass (`/do-test`) — including the new tests under Test Impact (`test_sustainability_namespace.py`, `test_default_project_key_consistency.py`, `test_update_install_worker.py`, plus the new e2e case in `test_session_continuity.py`).
- [x] Documentation updated (`/do-docs`).
- [ ] Issue #1171 closed by the implementation PR (`Closes #1171`).
- [ ] Sibling investigation issue filed for `dm` writer leak (out of scope to fix here, but tracked).
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
  - Role: Run `./scripts/valor-service.sh install` and exercise B1 fix path via `python -m scripts.update.run --full` (validates the new env injection). Restart bridge and worker. Delete stale `default:sustainability:throttle_level` via direct r.delete (top-level non-Popoto key). Migrate Memory records from `default` and `dm` to `valor` via Popoto-safe script (Task 4.5). Verify env var is live in worker process.
  - Agent Type: builder
  - Resume: true

- **Builder (update-skill-port + audit)**
  - Name: update-skill-builder
  - Role: **Port `.env` injection into `scripts/update/service.py::install_worker()`** (the B1 fix — central deliverable of this plan). Create `tests/unit/test_update_install_worker.py` regression test. Audit `scripts/remote-update.sh` and `.claude/skills/update/SKILL.md` for worker plist regeneration; both paths must produce equivalent plists post-fix.
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

### 2. Update fallback strings + empty-string defense + add regression tests
- **Task ID**: build-code-defense
- **Depends On**: none
- **Validates**: `tests/unit/test_sustainability_namespace.py`, `tests/unit/test_default_project_key_consistency.py` (both new); empty-string defense covered.
- **Informed By**: Spike-1, Spike-2, Test Impact, C2 Implementation Note
- **Assigned To**: recovery-code-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `agent/sustainability.py:30-32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408`: replace bare `os.environ.get("VALOR_PROJECT_KEY", "default")` with the empty-string-defensive form `_v = os.environ.get("VALOR_PROJECT_KEY", "").strip(); _pk = _v or "valor"` (per C2 Implementation Note). Add a comment pointing to issue #1171 with one sentence on why.
- Add docstring update at top of `agent/sustainability.py:14-18` with a note on env-var sourcing.
- Update `tests/unit/test_session_health_sibling_phantom_safety.py:56`: change `VALOR_PROJECT_KEY=default` to `VALOR_PROJECT_KEY=valor`.
- Create `tests/unit/test_sustainability_namespace.py` per Test Impact.
- Create `tests/unit/test_default_project_key_consistency.py` per Test Impact, including `test_empty_env_falls_back_to_valor` and `test_whitespace_env_falls_back_to_valor`.

### 3. Port .env injection into update/service.py + audit update skill (B1 fix)
- **Task ID**: build-update-skill
- **Depends On**: none
- **Validates**: `tests/unit/test_update_install_worker.py` passes; `python -m scripts.update.run --full` against a stub project results in worker plist with all .env vars; `grep -q install_worker scripts/remote-update.sh` (verify worker plist regen path).
- **Informed By**: Critique B1, Risk 5, B1 Implementation Note
- **Assigned To**: update-skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `scripts/update/service.py::install_worker()` at line 196: insert env-injection block after `plist_dst.write_text(plist_text)` (line 235) and before `launchctl bootstrap` (line 237). Use `from dotenv import dotenv_values` and `import plistlib`. Read `.env` from `project_dir / ".env"`. Open the plist, `setdefault("EnvironmentVariables", {})`, merge env vars (skip if `key in existing`, skip if `value is None`), save back via `plistlib.dump`. Wrap in `try/except Exception` with warning log.
- Create `tests/unit/test_update_install_worker.py` per Test Impact — call `install_worker()` against a temp project_dir with stub plist + stub `.env`, assert `VALOR_PROJECT_KEY=valor` lands in destination plist `EnvironmentVariables`.
- Verify `scripts/remote-update.sh` and `.claude/skills/update/SKILL.md` invoke the proper update flow (the run.py path is sufficient now that `install_worker()` injects env). If `remote-update.sh` calls `install_worker.sh` directly, it's still correct — both paths now inject env.

### 4. Validate code changes
- **Task ID**: validate-code
- **Depends On**: build-code-defense, build-env-vault, build-update-skill
- **Assigned To**: namespace-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py tests/unit/test_session_health_sibling_phantom_safety.py tests/unit/test_update_install_worker.py -v` — must pass.
- Run `python -m ruff check agent/sustainability.py agent/session_pickup.py agent/agent_session_queue.py scripts/update/service.py` — must pass.

### 4.5. Memory record migration (C1 in-scope)
- **Task ID**: build-memory-migration
- **Depends On**: validate-code
- **Validates**: `Memory.query.filter(project_key="default").count() == 0 AND Memory.query.filter(project_key="dm").count() == 0 AND Memory.query.filter(project_key="valor").count() >= prior_total`
- **Informed By**: Spike-1, C1 Implementation Note, Risk 1
- **Assigned To**: deploy-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement migration script at `scripts/migrate_memory_project_key.py` with `--dry-run` flag (default true) per C1 Implementation Note.
- Run `python scripts/migrate_memory_project_key.py --dry-run` first; capture before/after counts in PR description.
- Run `python scripts/migrate_memory_project_key.py` (live) BEFORE the worker restart in Task 5 so post-restart recall queries find the migrated records.
- Use Popoto-only operations: `Memory.query.filter(project_key=old_pk)` to read, `m.project_key = "valor"; m.save()` to write. NEVER raw Redis on Popoto-managed keys.
- File a sibling investigation issue tracking the `dm` writer leak source (out of scope to fix here, but record the leak as documented for follow-up).

### 5. Deploy + cleanup stale state
- **Task ID**: build-deploy
- **Depends On**: build-memory-migration
- **Validates**: `launchctl getenv VALOR_PROJECT_KEY` (manual) and `redis-cli --scan --pattern 'default:sustainability:*' | wc -l` returns 0
- **Informed By**: Spike-2, Risk 5 (B1)
- **Assigned To**: deploy-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `./scripts/valor-service.sh install`. Confirm plist injection log line shows new var count for the bridge plist.
- Run `python -m scripts.update.run --full` (this exercises the B1 fix path). Confirm worker plist injection log line shows new var count.
- Alternative one-shot: run `./scripts/install_worker.sh` directly (both paths must produce equivalent plists post-fix).
- Run `./scripts/valor-service.sh restart`.
- Verify env var via `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.worker.plist` returns `valor`.
- Verify same for bridge plist: `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.bridge.plist`.
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
- Update memory subsystem docs (`docs/features/subconscious-memory.md`, `docs/features/claude-code-memory.md`) noting the one-shot migration that ran (Task 4.5) and the new canonical `valor` namespace for memories.
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
| Tests pass | `pytest tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py tests/unit/test_session_health_sibling_phantom_safety.py tests/unit/test_update_install_worker.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/sustainability.py agent/session_pickup.py agent/agent_session_queue.py scripts/update/service.py tests/unit/test_sustainability_namespace.py tests/unit/test_default_project_key_consistency.py tests/unit/test_update_install_worker.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sustainability.py agent/session_pickup.py agent/agent_session_queue.py scripts/update/service.py` | exit code 0 |
| Env var in worker plist | `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.worker.plist` | output `valor` |
| Env var in bridge plist | `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.bridge.plist` | output `valor` |
| Env var lands via `/update --full` (B1 regression) | After `python -m scripts.update.run --full`, run `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.worker.plist` | output `valor` (proves B1 fix) |
| Env var in autoexperiment plist | `/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:VALOR_PROJECT_KEY" ~/Library/LaunchAgents/com.valor.autoexperiment.plist 2>/dev/null` | output `valor` (or "Print: Entry, ":EnvironmentVariables:VALOR_PROJECT_KEY", Does Not Exist" if plist not installed — acceptable; the relevant launchd-managed scheduler is the worker) |
| No stale default flags | `[ "$(redis-cli --scan --pattern 'default:sustainability:*' \| wc -l \| tr -d ' ')" -eq 0 ]` | exit code 0 |
| No `default`-tagged Memory records (post-migration) | `python -c "from models.memory import Memory; print(len(list(Memory.query.filter(project_key='default'))))"` | output `0` |
| No `dm`-tagged Memory records (post-migration) | `python -c "from models.memory import Memory; print(len(list(Memory.query.filter(project_key='dm'))))"` | output `0` |
| .env has new var | `grep -c VALOR_PROJECT_KEY ~/Desktop/Valor/.env` | output >= 1 |
| .env.example has new var | `grep -c VALOR_PROJECT_KEY .env.example` | output >= 1 |

## Critique Results

**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor
**Findings**: 6 total (1 blocker, 4 concerns, 1 nit)
**Verdict**: NEEDS REVISION — one blocker invalidates the plan's central deploy claim.

### Blockers

#### B1. `/update --full` will not inject `VALOR_PROJECT_KEY` into the worker plist
- **Severity**: BLOCKER
- **Critics**: Skeptic, Operator, Archaeologist
- **Location**: Solution → Recommended path step 2; Risk 2; Update System
- **Finding**: The plan asserts "the bridge plist generator at `scripts/valor-service.sh:482-521` already injects all `.env` vars... `/update` re-runs both generators." This is false for the worker. `scripts/update/service.py:196-240` (`install_worker()`, called by `run.py:820` during `/update --full`) only does `__PROJECT_DIR__`/`__HOME_DIR__`/`__SERVICE_LABEL__` string substitution. The `.env` injection logic ONLY lives in the standalone `scripts/install_worker.sh:95-131`, which `/update` does not invoke. After deploy via `/update`, the worker plist will still have just `PATH/HOME/VALOR_LAUNCHD` — `VALOR_PROJECT_KEY` will be missing, the recovery code will fall back to `"default"`, and the bug persists. Live verification of this machine confirms: `PlistBuddy ~/Library/LaunchAgents/com.valor.worker.plist :EnvironmentVariables` returns only those three keys, despite the bridge plist having 30+ vars. This is the spec'd "Risk 3" already in real life on the canonical machine.
- **Suggestion**: Either (a) port the `.env` injection block from `scripts/install_worker.sh:95-131` into `scripts/update/service.py::install_worker()` so `/update --full` injects on every run, or (b) make `run.py` invoke `scripts/install_worker.sh` directly instead of its own minimal install path. Add a Verification entry that asserts `VALOR_PROJECT_KEY` lands in the worker plist *after running `/update --full`*, not after a manual `install_worker.sh` invocation. Until this is fixed, the plan's "Recommended path step 2: no script changes needed" is wrong.
- **Implementation Note**: `scripts/update/service.py:196` is the function to edit. Copy the `dotenv_values()` + `plistlib.load`/`dump` + `existing.setdefault("EnvironmentVariables", {})` block from `scripts/install_worker.sh:95-131` and execute it after `plist_dst.write_text(plist_text)` on line 235 but BEFORE the `launchctl bootstrap` call on line 237. The injection must NOT clobber `PATH`/`HOME`/`VALOR_LAUNCHD` — use the `if key not in existing` guard exactly as the shell version does. Also add a Task before deploy that updates `service.py`, and add a Verification row asserting `python scripts/update/run.py --full` (not `install_worker.sh`) results in `VALOR_PROJECT_KEY=valor` in the plist.

### Concerns

#### C1. Memory namespace migration deferral leaves a known-broken half-system after deploy
- **Severity**: CONCERN
- **Critics**: User, Adversary
- **Location**: Open Question 2; Risk 1; No-Gos
- **Finding**: Spike-1 found 198 records tagged `default` and 230 tagged `valor`; live re-check during this critique shows the split is now valor=241, default=207, dm=4 — and the `dm` namespace is still bleeding (plan said dm=3, now dm=4). Setting `VALOR_PROJECT_KEY=valor` env-wide doesn't just shift future writes — it makes all future memory recall queries miss the 207 `default`-tagged records and the 4 `dm`-tagged records entirely. From a user-facing standpoint, that means roughly 45% of the agent's existing memory becomes unreachable the moment the worker restarts. The plan's recommendation "(c) defer to a follow-up" treats this as a clean separation, but the user-visible effect is "memory regression on day 0 of deploy." A bridge-internal namespace fix should not silently degrade memory recall. The `dm` namespace leak (plan said it was historical, but it's still growing) also implies a third resolution path the plan hasn't located.
- **Suggestion**: Either (a) include the migration in this plan as a single Popoto-safe step (re-tag `default` + `dm` records to `valor` before the worker restart), or (b) downgrade the Open Question 2 default recommendation from "(c) defer" to "(a) migrate as part of this plan" since the migration is small (~30 lines) and the user-facing cost of deferring is large. Also: track down the `dm` writer before it leaks any further; that's not in scope for this plan but should be filed as a sibling issue immediately.
- **Implementation Note**: Migration script template — `from models.memory import Memory; for m in Memory.query.filter(project_key="default"): m.project_key = "valor"; m.save()` (and same for `dm`). Run with a count assertion before/after and a dry-run flag. NEVER use raw `r.delete`/`r.hset` per CLAUDE.md `feedback_never_raw_delete_popoto.md`. Run BEFORE the worker restart in Task 5 so recall queries during the post-restart settling window find the records. The `dm` leak source is likely `_get_project_key()` in the memory-bridge subprocess path with a `cwd` that matches a now-removed `dm` project — grep for `cwd` references against `projects.json` lookups in `agent/memory_hook.py:83`, `tools/memory_search/__init__.py:48`, and `.claude/hooks/hook_utils/memory_bridge.py:140`.

#### C2. Empty-string `VALOR_PROJECT_KEY` defensive check is described in Failure Path Test Strategy but not in any task
- **Severity**: CONCERN
- **Critics**: Adversary, Consistency Auditor
- **Location**: Failure Path Test Strategy → Empty/Invalid Input Handling vs Task 2
- **Finding**: The Failure Path Test Strategy says "Empty `VALOR_PROJECT_KEY` env var: currently treated as 'set' by `os.environ.get(..., 'default')` returning `''`. Add a defensive check: if value is empty after strip, treat as unset." But Task 2 (`build-code-defense`) only describes flipping `"default"` → `"valor"` and updating the docstring. There is no task to actually add the empty-string defensive check, and no test in Test Impact verifies it. Setting `VALOR_PROJECT_KEY=` (empty string) in `.env` is a real failure mode — the plist injector at `install_worker.sh:122` does `if key not in existing and value is not None` which would inject the empty string, leading to writes against `:sustainability:queue_paused` (no project prefix). This is not just empty — it would cross-contaminate any namespace that has a leading colon convention.
- **Suggestion**: Either add an explicit step under Task 2 ("If `os.environ.get('VALOR_PROJECT_KEY', '').strip()` is empty, fall through to fallback `'valor'`") and add a corresponding test case in `test_default_project_key_consistency.py`, or remove the empty-string handling from the Failure Path section so the plan and tasks are consistent. Don't leave it as orphaned guidance.
- **Implementation Note**: Implement via a one-liner helper: `def _get_project_key() -> str: v = os.environ.get("VALOR_PROJECT_KEY", "").strip(); return v or "valor"`. Apply identically at `agent/sustainability.py:30-32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408`. Test with `monkeypatch.setenv("VALOR_PROJECT_KEY", "")` and `monkeypatch.setenv("VALOR_PROJECT_KEY", "  ")` — both must resolve to `"valor"`.

#### C3. `tests/e2e/test_session_continuity.py:30` is left "as-is" but its semantics flipped
- **Severity**: CONCERN
- **Critics**: Skeptic, Consistency Auditor
- **Location**: Test Impact → row 4
- **Finding**: The plan says `test_session_continuity.py:30` should be "KEEP AS IS — this is now the canonical value." But the test is asserting that `start_transcript(project_key="valor", ...)` results in a session with `project_key == "valor"`. This is a tautology — it asserts that what was passed in is what was stored. It does NOT verify that `"valor"` is the canonical project_key in the absence of explicit override. The plan's claim that this test "would not catch a `'default'`-side regression" (from issue body) remains true after the fix. Leaving the test untouched does not provide regression coverage for the namespace alignment guarantee. The new tests in `tests/unit/test_default_project_key_consistency.py` cover this, but the e2e test should at minimum get an additional case that calls `start_transcript()` WITHOUT `project_key=` and asserts the resulting session is `valor` — to catch a future regression at the e2e level where the writer's default could drift.
- **Suggestion**: Add to Test Impact: `tests/e2e/test_session_continuity.py` — UPDATE to add one new test case `test_default_project_key_when_unspecified` that calls `start_transcript(session_id=..., chat_id=..., sender=...)` (no `project_key`), and asserts the persisted `AgentSession.project_key == "valor"`. This is the e2e companion to `test_default_project_key_consistency.py` and catches drift at the integration layer.
- **Implementation Note**: The test must run with `VALOR_PROJECT_KEY` UNSET to verify the fallback (use `monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)` at the test scope). Otherwise the env injection from the test runner's environment will mask the writer-side default. Also verify that `start_transcript()` actually delegates to `tools/agent_session_scheduler.DEFAULT_PROJECT_KEY` and not a different default — check `bridge/session_transcript.py` to confirm the resolution path before writing the assertion.

#### C4. Watchdog and email bridge plists are not audited but were not enumerated either
- **Severity**: CONCERN
- **Critics**: Operator, Adversary
- **Location**: Risk 4
- **Finding**: Plan correctly identifies that watchdog plists "do NOT inject `.env`" and asks for an audit of `monitoring/bridge_watchdog.py` and `monitoring/worker_watchdog.py`. Verified during critique: neither file references `VALOR_PROJECT_KEY` or any project-keyed Redis namespace, so the watchdogs are clean. BUT — the plan does not enumerate `com.valor.email` (the email bridge) which IS a launchd service and DOES interact with Redis (per CLAUDE.md "email relay (bundled into the email bridge process) drains the queue over SMTP"). If the email relay reads or writes any project-keyed Redis flag, it has the same namespace mismatch. Also: `com.valor.update` (cron) and `com.valor.log-rotate` are launchd services not on the Risk 4 audit list. The plan's "audit list" is incomplete.
- **Suggestion**: Expand the audit instruction to: `grep -rn "VALOR_PROJECT_KEY" monitoring/ scripts/email_relay.py scripts/log_rotate.py scripts/update/ 2>/dev/null` — enumerate ALL launchd-managed entrypoints, not just the watchdogs. Update Risk 4 with the full list. If any service reads project_key, either add the env var to that plist's generator OR refactor that code path to be project-key-agnostic.
- **Implementation Note**: Specifically check `bridge/email_bridge.py` (or wherever the email bridge entrypoint lives — `grep -l "valor.email" scripts/ bridge/`) for any `os.environ.get("VALOR_PROJECT_KEY"` calls. The audit takes 60 seconds. Bake the result into the plan as a one-line claim ("audited X, Y, Z; only watchdogs are clean") rather than leaving the open mitigation question.

### Nits

#### N1. Verification table row for "No stale default flags" has wrong expected value
- **Severity**: NIT
- **Critics**: Consistency Auditor
- **Location**: Verification table, line 402
- **Finding**: The row reads `output > -1 (any count, but should be 0 — verify post-cleanup)`. `wc -l` always returns >= 0 on success, so `> -1` is trivially true and the assertion is meaningless. The parenthetical correctly states the real expectation (should be 0).
- **Suggestion**: Replace `output > -1` with `output == 0` (or equivalently `[ "$(redis-cli --scan --pattern 'default:sustainability:*' | wc -l)" -eq 0 ]`). This is the only verification row that doesn't enforce its own description.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-8 sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` references map to valid task IDs; no circular deps |
| File paths exist | PASS | All cited files verified on disk: `agent/sustainability.py`, `agent/session_pickup.py`, `agent/agent_session_queue.py`, `tools/agent_session_scheduler.py`, `config/memory_defaults.py`, `tests/unit/test_sustainability.py`, `tests/unit/test_session_health_sibling_phantom_safety.py`, `tests/e2e/test_session_continuity.py`, `scripts/valor-service.sh`, `scripts/install_worker.sh`, `scripts/remote-update.sh`, `monitoring/bridge_watchdog.py`, `monitoring/worker_watchdog.py`, all docs/features/* |
| Prerequisites met | PASS | `redis-cli ping` → PONG; vault `.env` exists; vault `projects.json` exists; worker is loaded |
| Cross-references | PASS | Success Criteria each map to ≥1 task; No-Gos do not appear in Solution; Rabbit Holes do not appear in tasks |

### Verdict

**NEEDS REVISION** — 1 blocker (B1) must be resolved before build. The blocker invalidates the central deployment claim of the recommended Option B. Once B1 is addressed (port `.env` injection into `scripts/update/service.py::install_worker`), and the four concerns are either patched into the plan or explicitly accepted, this is a small, well-scoped fix that is otherwise ready.

### Revision Pass 1 (applied 2026-04-25)

**Revisions applied to the plan in response to the critique:**

- **B1 (BLOCKER)** — Resolved. Solution → Technical Approach now includes a B1 Implementation Note describing how to port `.env` injection into `scripts/update/service.py::install_worker()`. Task 3 (`build-update-skill`) explicitly applies the fix. Task 4 validates it. Verification table includes a regression row asserting the env var lands via `python -m scripts.update.run --full`. Test Impact adds `tests/unit/test_update_install_worker.py` to catch future regressions. Risk 5 promoted from B1 with full impact/mitigation detail.
- **C1 (CONCERN — memory migration)** — Resolved. Downgraded recommendation from "(c) defer" to "(a) migrate as part of this plan." Solution → Technical Approach now includes a C1 Implementation Note with the Popoto-safe migration script template. New Task 4.5 (`build-memory-migration`) runs the migration BEFORE the worker restart. Verification rows added for `default`-tagged and `dm`-tagged Memory record counts (both must be 0 post-migration). Open Question 2 marked RESOLVED with the new disposition. The `dm` writer leak source is tracked as a sibling investigation (out of scope here).
- **C2 (CONCERN — empty-string defense)** — Resolved. Solution → Technical Approach now includes a C2 Implementation Note specifying the helper form `_v = os.environ.get("VALOR_PROJECT_KEY", "").strip(); _pk = _v or "valor"`. Task 2 (`build-code-defense`) explicitly applies it at all three call sites. Test Impact adds `test_empty_env_falls_back_to_valor` and `test_whitespace_env_falls_back_to_valor` cases. Failure Path Test Strategy section updated to point at the codified Implementation Note.
- **C3 (CONCERN — e2e default-project-key test)** — Resolved. Test Impact updated for `tests/e2e/test_session_continuity.py` from "KEEP AS IS" to "UPDATE: add `test_default_project_key_when_unspecified` case" — constructs an AgentSession without explicit `project_key=`, with `monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)`, asserts the persisted record has `project_key == "valor"`.
- **C4 (CONCERN — broader launchd audit)** — Resolved. Risk 4 expanded with the full audit results: watchdogs are clean, email bridge derives project_key from `projects.json` directly (no env var dependency), email_relay/email_dead_letter clean, log_rotate clean, scheduled launchd plists (`autoexperiment`, `nightly-tests`, `sdlc-reflection`, `log-rotate`) audited — no code changes required outside the worker. Verification table includes a row for the autoexperiment plist as a representative scheduled service.
- **N1 (NIT — verification table)** — Resolved. Replaced `output > -1` with proper boolean expression `[ "$(redis-cli --scan --pattern 'default:sustainability:*' | wc -l | tr -d ' ')" -eq 0 ]` (exit code 0). Other verification rows tightened to remove ambiguity ("output contains" → "output `valor`"; "output > 0" → "output >= 1").

**New Verdict (post-revision):** READY TO BUILD — all critique findings addressed in plan text; no concerns remaining unless new ones surface in the next critique cycle.

---

## Open Questions

All three open questions have been resolved during the revision pass; recording resolutions inline so the plan is build-ready.

1. **Which resolution strategy do we adopt?** **RESOLVED: Option B (env-level propagation)** is now the canonical path. With B1 patched (port `.env` injection into `scripts/update/service.py::install_worker`), Option B deploys cleanly via `/update --full`. Options A (code-level alignment) and C (config-driven via projects.json) remain viable but are out of scope for this fix.

2. **Memory subsystem namespace migration — in or out of scope?** **RESOLVED: in scope (C1 revision, downgraded from "(c) defer")**. Setting `VALOR_PROJECT_KEY=valor` env-wide shifts future memory writes from `default` to `valor`, splitting recall behavior. Live re-check during critique showed `valor=241, default=207, dm=4`. Deferring leaves ~45% of existing memory unreachable on day 0 of deploy. Migration is small (~30 lines) and Popoto-safe. **Task 4.5 (`build-memory-migration`)** runs the migration BEFORE the worker restart. The `dm` writer leak source is filed as a sibling investigation issue (out of scope here).

3. **Should the `"default"` fallback strings in `agent/sustainability.py:32`, `agent/session_pickup.py:180`, `agent/agent_session_queue.py:1408` be changed to `"valor"`?** **RESOLVED: yes, AND also add empty-string defense (per C2)**. Replace with the helper form `_v = os.environ.get("VALOR_PROJECT_KEY", "").strip(); _pk = _v or "valor"`. Defense in depth at zero cost. Test cases for empty-string and whitespace-only added to Test Impact.
