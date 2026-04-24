---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1158
last_comment_id:
---

# Child Session Project Scope — `valor-session create` working_dir defaulting bug

## Problem

A PM-role session running in project `cuttlefish` (`cwd=/Users/valorengels/src/cuttlefish`) invokes `valor-session create --role pm --message "Run SDLC on issue 290"` to spawn a child SDLC session. The child's `project_key` is correctly `cuttlefish`, but its `working_dir` is `/Users/valorengels/src/ai/.worktrees/sdlc-290` — rooted in the wrong repo. The worker then launches the child's `claude -p` subprocess with the wrong `cwd`, and every `git`, `gh`, and file operation lands in `ai/` instead of `cuttlefish/`.

**Current behavior:**

In `tools/valor_session.py:cmd_create`, `project_key` and `working_dir` are resolved as two independent defaults that do not talk to each other:

- `working_dir` defaults to `_repo_root` — a module constant `Path(__file__).parent.parent` that always equals `/Users/valorengels/src/ai` regardless of caller (line 244).
- `project_key` is derived via `resolve_project_key(os.getcwd())`, which walks `projects.json` by longest-prefix match and falls back to `"valor"` silently when cwd matches no project (lines 168-176).

The bridge (`bridge/telegram_bridge.py:1218-1222`, `1972-1988`) does the right thing — it reads `working_directory` from the matched project and passes both that and the full project dict as `project_config=project` to `enqueue_agent_session`. The CLI does neither.

Concrete evidence (2026-04-24 Cuttlefish session tree, via `python -m tools.valor_session inspect --id <id> --json`):

- Parent PM `f0ea712a63e34be39006b085c50126f9`: `project_key=cuttlefish`, `working_dir=/Users/valorengels/src/cuttlefish` (correct — bridge-enqueued).
- Child PM `b84f11e034ab4ce88dc363e9365e5fe0`: `parent_agent_session_id=f0ea712…`, `sender_name=valor-session (pm)`, `project_key=cuttlefish`, `working_dir=/Users/valorengels/src/ai/.worktrees/sdlc-290` — **misrouted**.
- Second child `5a60ddf4440b4f02934a4da4e3686491`: same shape, `sdlc-291`.

**Desired outcome:**

- When `project_key` resolves to `X` (explicitly or via cwd match), `working_dir` defaults to `projects.json[X].working_directory`, not `_repo_root`.
- A cwd that matches no project, with no `--project-key` flag, fails loudly instead of silently coercing to `"valor"`.
- A `working_dir` that is not inside the declared `working_directory` of the resolved `project_key` is refused at creation.
- A child session spawned by `valor-session create --parent <id>` inherits `project_key` and `working_dir` from the parent `AgentSession` (unless explicitly overridden).

## Freshness Check

**Baseline commit:** `46b2de03389dcdb38ad2c348e9b7e43365d3d8e9` (main, 2026-04-24)
**Issue filed at:** 2026-04-24T07:21:31Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/valor_session.py:82` — `_repo_root = Path(__file__).parent.parent` — still holds (verified).
- `tools/valor_session.py:121-176` — `resolve_project_key()` with silent `"valor"` fallback at 168-176 — still holds (verified).
- `tools/valor_session.py:244` — `working_dir = args.working_dir or str(_repo_root)` — still holds (verified).
- `tools/valor_session.py:270-276` — `get_or_create_worktree(Path(working_dir), slug)` — still holds, now at lines 270-276 (verified).
- `tools/valor_session.py:278-283` — `project_key` resolution via `explicit_key` or `resolve_project_key(os.getcwd())` — still holds (verified).
- `bridge/telegram_bridge.py:1218-1222` — `project.get("working_directory", DEFAULTS.get("working_directory", ""))` — still holds (verified).
- `bridge/telegram_bridge.py:1972-1988` — `dispatch_telegram_session(..., project_config=project, ...)` — confirmed; bridge also passes the full dict.
- `agent/sdk_client.py:1410` — `cwd=str(self.working_dir)` — still holds (verified).
- `agent/reflection_scheduler.py:394-399` — secondary caller of `resolve_project_key(str(project_root))` — still holds. `project_root = ~/src/ai` reliably matches the `valor` project key in `projects.json`, so the try/except around the import is the only fallback it actually uses; the `"valor"` fallback inside `resolve_project_key` is NOT load-bearing here.

**Cited sibling issues/PRs re-checked:**
- #1157 — OPEN (`bug: user_prompt_submit hook creates phantom local-* AgentSession twins for worker-spawned PM sessions`). Separate root cause (hook, not CLI). This plan's fix mitigates #1157's symptom (phantom inheriting `project_key=valor` because cwd was mis-rooted) but does not fix twin creation itself.
- #887 — CLOSED 2026-04-10 (`Session isolation bypass: PM sessions created via valor-session create operate in main checkout instead of a worktree`). Introduced the `--slug` flag and the worktree call at line 274. It assumed `working_dir` was already correctly scoped — this bug sits upstream of that assumption.
- #1109 — CLOSED 2026-04-22 (`PM sessions silently fail when created without --slug`). Same file, different bug. Added auto-slug derivation from "issue #N".
- #397 — CLOSED 2026-04-04 via PR #685. Eliminated `_project_configs` parallel registry and added `project_config: DictField` to `AgentSession`. This is **directly relevant** — the model already has a field for carrying the full project dict; the bridge already populates it; the CLI does not.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-04-24T07:21:31Z -- tools/valor_session.py bridge/telegram_bridge.py agent/sdk_client.py agent/reflection_scheduler.py agent/worktree_manager.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:** None found referencing `valor_session`, `project_key`, or `working_dir` defaulting.

**Notes:** The issue body's Solution Sketch is accurate. One material finding from freshness re-verification: `AgentSession.project_config` (added in PR #685) is already a durable field on the model, and the bridge already populates it. The CLI not only fails to derive `working_dir` from the project — it also fails to attach `project_config` to the enqueued session. Fix (1) should include passing the full project dict through `_push_agent_session(project_config=...)` for parity with the bridge.

## Prior Art

- **PR #685** (merged 2026-04-04): `Eliminate _project_configs registry, propagate config through AgentSession` — added `project_config: DictField` to `AgentSession` so the full project dict travels with the session. Closed #397. Directly relevant: the model already supports durable project context, but `tools/valor_session.py:cmd_create` never calls `_push_agent_session(project_config=...)`.
- **#887** (closed 2026-04-10): Added `--slug` to `valor-session create` plus the `get_or_create_worktree` call at line 274. Did not address `working_dir` defaulting.
- **#1109** (closed 2026-04-22): Auto-derive slug from "issue #N"; truncation fix. Same file, different bug.
- **#397** (closed 2026-04-04): Broad audit that produced PR #685. Identified project config as the source of truth, but the audit's scope was bridge→session→execution; CLI enqueue path was not in scope.
- **#1157** (open): Phantom `local-*` twin sessions from the `user_prompt_submit` Claude Code hook. Shares the Cuttlefish session-tree evidence but is a different root cause. This plan's fix reduces #1157's collateral damage but does not resolve it.

No prior attempt has addressed this CLI-level defaulting bug. The mechanism to fix it (`project_config` DictField, `projects.json` schema, `bridge.routing.load_config()`) is already in place.

## Research

No relevant external findings — proceeding with codebase context and training data. The fix is purely internal: CLI defaulting logic, `projects.json` lookup, and worktree path composition. No external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

1. **Parent PM runs** `valor-session create --role pm --message "Run SDLC on issue 290"` from `cwd=/Users/valorengels/src/cuttlefish`, no `--working-dir`, no `--project-key`.
2. **`cmd_create` enters** (`tools/valor_session.py:195`):
   a. `working_dir = args.working_dir or str(_repo_root)` at line 244 → `working_dir=/Users/valorengels/src/ai` (wrong).
   b. `slug` auto-derived from "issue 290" → `sdlc-290`.
   c. `get_or_create_worktree(Path(working_dir), slug)` at line 274 → creates `/Users/valorengels/src/ai/.worktrees/sdlc-290/`, reassigns `working_dir` to that path.
   d. `project_key` resolution at lines 278-283: `explicit_key=None`, falls to `resolve_project_key(os.getcwd())` → matches `~/src/cuttlefish` in `projects.json` → returns `"cuttlefish"`.
3. **`_push_agent_session`** called at line 286 with `project_key="cuttlefish"`, `working_dir="/Users/valorengels/src/ai/.worktrees/sdlc-290"`, no `project_config`.
4. **Redis write:** `AgentSession` record stores mismatched `project_key` and `working_dir`, `project_config=None`.
5. **Worker picks up** the session. `sdk_client.py:1410` sets `cwd=self.working_dir` → subprocess launches in `ai/` repo with agent scope tagged `cuttlefish`. Every `git`, `gh`, `Edit`, `Read` lands in `ai/`. Disaster.

**Fixed flow:**

1. Same parent invocation.
2. `cmd_create` resolves `project_key` **first** (from `--project-key`, else parent, else cwd).
3. Load `projects.json` via `bridge.routing.load_config()`; look up `projects[project_key].working_directory` as the default for `working_dir` if `--working-dir` was not supplied.
4. `get_or_create_worktree(Path(working_dir), slug)` now uses the correct `working_dir` base.
5. Before enqueue, assert `working_dir` is equal to or descendant of `projects[project_key].working_directory` (or its `.worktrees/` subtree).
6. Pass the full project dict as `project_config=project` to `_push_agent_session` for parity with the bridge.

## Architectural Impact

- **New dependencies**: None. `bridge.routing.load_config()` is already imported inside `resolve_project_key()`.
- **Interface changes**:
  - `resolve_project_key(cwd)` becomes stricter: raises `ProjectKeyResolutionError` (or a specific `ValueError`) instead of silently returning `"valor"`. This is a contract change with one production consumer (`agent/reflection_scheduler.py:397`) and several test consumers.
  - `cmd_create` gains a new precedence chain: `--working-dir` > `--parent`'s `working_dir` > `projects.json[project_key].working_directory` > fail.
- **Coupling**: `tools/valor_session.py` gains a soft dependency on the schema of `projects.json` (already depended on via `resolve_project_key`). No new coupling.
- **Data ownership**: `AgentSession.project_config` is already the canonical carrier. This plan ensures the CLI populates it — matching the bridge — rather than introducing a new owner.
- **Reversibility**: Changes are confined to one file (`tools/valor_session.py`) plus test updates. Revert is a single `git revert`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified in the issue)
- Review rounds: 1 (standard PR review)

Solo dev work — the fix is ~40 lines changed in one file plus test updates. The bottleneck is test coverage and the `resolve_project_key` contract change.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `projects.json` readable | `python -c "from bridge.routing import load_config; assert load_config().get('projects')"` | CLI must be able to look up per-project `working_directory` |
| `AgentSession.project_config` field exists | `python -c "from models.agent_session import AgentSession; assert 'project_config' in AgentSession._fields"` | Field added in PR #685; confirms the model supports durable project dicts |
| `agent/worktree_manager.py:get_or_create_worktree` signature unchanged | `python -c "import inspect; from agent.worktree_manager import get_or_create_worktree; sig = inspect.signature(get_or_create_worktree); assert 'repo_root' in sig.parameters and 'slug' in sig.parameters"` | Worktree creation must accept a `repo_root` override |

Run all checks: `python scripts/check_prerequisites.py docs/plans/child-session-project-scope.md`

## Solution

### Key Elements

- **`project_key` resolution** runs first, before `working_dir` defaulting. Explicit flag > parent inheritance > cwd match > error.
- **`working_dir` defaulting** reads `projects.json[project_key].working_directory` when `--working-dir` is not supplied, replacing the hardcoded `_repo_root`.
- **`resolve_project_key` contract change**: no silent fallback. Raises on unmatched cwd when no explicit key is provided. Secondary caller (`agent/reflection_scheduler.py`) is audited and updated defensively.
- **Consistency guard**: `working_dir` must be a descendant of the project's declared `working_directory` (or that directory's `.worktrees/` subtree). Refuse on mismatch.
- **`project_config` propagation**: the CLI passes the full project dict to `_push_agent_session(project_config=...)`, matching the bridge (`bridge/telegram_bridge.py:1987`).
- **Parent inheritance (belt-and-suspenders)**: when `--parent <id>` is supplied, load the parent `AgentSession` and default `project_key` and `working_dir` from it.

### Flow

**Parent PM** (in project X) → runs `valor-session create --role pm --message "..."` → `cmd_create` resolves `project_key=X` → looks up `projects.json[X].working_directory` → that becomes the base for the worktree path → `get_or_create_worktree(base, slug)` creates `<X>/.worktrees/{slug}/` → consistency guard passes → session enqueued with `project_key=X`, `working_dir=<X>/.worktrees/{slug}/`, `project_config=<full dict for X>`.

### Technical Approach

Five surgical changes to `tools/valor_session.py`, plus two small changes elsewhere:

1. **Introduce `_resolve_project_working_directory(project_key)` helper.** Load `projects.json` via `bridge.routing.load_config()`, return `projects[project_key]["working_directory"]` (expanded with `Path.expanduser()`). Raise `ValueError` if the key is not in `projects.json` or has no `working_directory`.

2. **Reorder `cmd_create` resolution to: project_key → working_dir → worktree.** Move the `project_key` block (currently at lines 278-283) above the `working_dir` assignment (currently at line 244). This is the primary structural change. New order:
   - Determine `project_key` from: `--project-key` flag (explicit) > parent `AgentSession.project_key` (if `--parent`) > `resolve_project_key(os.getcwd())` (strict, no fallback).
   - Load project dict once: `project = bridge.routing.load_config()["projects"][project_key]`.
   - Determine `working_dir` from: `--working-dir` flag (explicit) > parent `AgentSession.working_dir` (if `--parent`, and the parent's `working_dir` is consistent with the resolved `project_key`) > `project["working_directory"]` (default).
   - If `slug` is set, call `get_or_create_worktree(Path(working_dir), slug)` and reassign `working_dir` to the worktree path.

3. **Add `_assert_working_dir_consistent(working_dir, project_key, project)` guard.** After all resolution, assert that `Path(working_dir).resolve()` is equal to or a descendant of `Path(project["working_directory"]).expanduser().resolve()`. Allow `.worktrees/*` subdirectories of the project root. Raise with a clear message on mismatch. Skip this check only if `--working-dir` was explicitly provided AND the caller has opted into an override via a new `--allow-external-working-dir` flag (rare; covers debugging and cross-repo spikes).

4. **Remove the silent `"valor"` fallback in `resolve_project_key()`.** Change signature to raise `ProjectKeyResolutionError(cwd, reason)` on no-match. Preserve the existing `try/except` around `load_config()` by letting it propagate a distinct `ProjectsConfigUnavailableError`. The error message must name the cwd, list available project keys, and suggest `--project-key` explicitly.

5. **Audit `agent/reflection_scheduler.py:394-399` for the contract change.** The existing code wraps the `resolve_project_key` import in a try/except catching any `Exception` and falling back to `os.environ.get("PROJECT_KEY", "valor")`. This already insulates it from the new exception — but the behavior should be made explicit: catch the specific new exception type and log a warning so we can see if reflection scheduler ever hits the no-match path. The reflection scheduler passes `project_root=~/src/ai`, which always matches the `valor` project in `projects.json`, so this is defensive only.

6. **Populate `project_config` at enqueue.** In `cmd_create`'s call to `_push_agent_session(...)`, pass `project_config=project` (the full dict loaded in step 2). Matches the bridge (`bridge/telegram_bridge.py:1987`).

7. **Parent inheritance (belt-and-suspenders).** When `--parent <id>` is provided, call `AgentSession.query.filter(id=parent_id).first()` to load the parent. If found:
   - Default `project_key` to `parent.project_key` unless `--project-key` is explicit.
   - Default `working_dir` to `parent.working_dir` unless `--working-dir` is explicit AND the parent's `working_dir` is consistent with the resolved `project_key` (so a cross-project parent-child spawn fails loud).

No changes needed to `AgentSession` model, `bridge/telegram_bridge.py`, `agent/sdk_client.py`, or `agent/worktree_manager.py`.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `tools/valor_session.py:cmd_create` currently has a broad `try/except Exception` that catches any error and returns exit code 1. Verify the new `ProjectKeyResolutionError` and `WorkingDirInconsistentError` raise paths produce clear stderr messages BEFORE being caught — tests must assert the stderr content, not just the exit code.
- [ ] `agent/reflection_scheduler.py:394-399` has `try: from tools.valor_session import resolve_project_key; ... except Exception: project_key = ...`. Add a test that simulates `resolve_project_key` raising the new exception and asserts the scheduler falls back gracefully (via `PROJECT_KEY` env var or `"valor"`). The scheduler should log a warning, not crash.

### Empty/Invalid Input Handling

- [ ] Test `cmd_create` when `--project-key` refers to a key not in `projects.json` → exit 1, clear error naming the missing key.
- [ ] Test `cmd_create` when `cwd` matches no project and no `--project-key` → exit 1 with suggestion.
- [ ] Test `cmd_create` when `projects.json` is unloadable (e.g., path missing) → exit 1 with error citing the file path.
- [ ] Test `cmd_create` when `--parent <id>` points to a nonexistent session → exit 1.

### Error State Rendering

- [ ] All new error paths must write to stderr (preserving stdout for `--json` output). Assert `stdout_capture.getvalue() == ""` on error.
- [ ] Error messages must include: the attempted value, the list of valid project keys, and a suggested remediation (e.g., "pass `--project-key <key>` or run from inside one of: [ai, cuttlefish, popoto, ...]").

## Test Impact

- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback` (class with `test_*_returns_valor` tests at lines 125-149, 177-190) — **REPLACE**: Flip assertions from `assert result == "valor"` to `with pytest.raises(ProjectKeyResolutionError): resolve_project_key(...)`. Keep stderr capture assertions where they verify error message content.
- [ ] `tests/unit/test_valor_session_project_key.py::test_empty_projects_returns_valor` — **REPLACE**: empty projects dict + unmatched cwd should raise, not return `"valor"`.
- [ ] `tests/unit/test_valor_session_project_key.py::test_load_config_exception_returns_valor` — **UPDATE**: `load_config` raising should still produce a fallback-equivalent behavior (different exception type, `ProjectsConfigUnavailableError`), not `"valor"`. Rewrite to assert the new exception type.
- [ ] `tests/unit/test_valor_session_project_key.py::test_project_missing_working_directory_skipped` (line 151) — **UPDATE**: no longer returns `"valor"` as a fallback when no match exists; rewrite to assert the raised error path.
- [ ] `tests/unit/test_valor_session_project_key.py::test_fallback_warning_goes_to_stderr_not_stdout` (line 177) — **UPDATE**: rewrite to assert the new exception's error message appears on stderr via the CLI caller, not the helper.
- [ ] `tests/unit/test_valor_session_project_key.py::TestProjectKeyFlagOverride::test_explicit_flag_bypasses_resolution` (line 199) — **UPDATE** if needed: still valid but may need to verify no-exception behavior when the explicit key is used.
- [ ] `tests/unit/test_pm_session_auto_slug.py` — **UPDATE**: existing `cmd_create` tests at lines 71, 105, 135, 162 must be verified against the new resolution order. Add a mock for `bridge.routing.load_config` returning a valid `projects.json` dict so tests don't require the real file.
- [ ] `tests/unit/test_pm_session_refuse_no_issue.py` — **UPDATE**: same `load_config` mocking requirement.
- [ ] `tests/unit/test_valor_session_cli.py` — **UPDATE**: any test that invokes `cmd_create` end-to-end must mock `load_config` to avoid depending on machine-specific `projects.json`.
- [ ] `tests/integration/test_parent_child_round_trip.py` — **UPDATE**: verify parent-child inheritance of `project_key` and `working_dir`. Add an assertion that a child's `working_dir` is rooted inside the parent's project.
- [ ] **ADD** `tests/unit/test_valor_session_working_dir_resolution.py` — new file covering:
  - `--project-key cuttlefish` from cwd `/Users/valorengels/src/ai` produces `working_dir` rooted at the cuttlefish project, not `ai/`.
  - `--working-dir /arbitrary/path` with `--project-key cuttlefish` refuses unless `--allow-external-working-dir` is set.
  - `--parent <id>` inherits `project_key` and `working_dir` from the parent `AgentSession`.
  - Enqueue path writes `project_config` as a populated dict matching `projects.json[project_key]`.
  - All four acceptance criteria from issue #1158 covered.
- [ ] **ADD** `tests/integration/test_valor_session_cross_project_spawn.py` — new file covering the full three-level PM chain: simulate a parent PM in project X calling `valor-session create` twice (one bare, one with explicit `--project-key`), verify both child sessions have consistent `project_key` and `working_dir` rooted inside project X.

## Rabbit Holes

- **Refactoring `resolve_project_key` into a generic project-config helper.** Tempting because the CLI and the bridge do similar work, but they serve different contracts. Leave them separate.
- **Auditing every caller of `projects.json` for silent fallbacks.** Out of scope. This plan touches the CLI and its one production consumer (reflection scheduler). Other callers (`bridge.routing`, `agent.agent_session_queue._push_agent_session`'s `project_key=` argument) are already safe because the bridge and worker paths originate from a validated project dict.
- **Migrating the phantom `local-*` twin sessions from #1157.** Tempting because the evidence overlaps. Out of scope — #1157 is a hook-level duplication bug, not a CLI defaulting bug. This plan's fix will mitigate the `project_key=valor` symptom of #1157's phantoms but will NOT fix phantom creation.
- **Cleaning up orphaned worktrees under `ai/.worktrees/sdlc-*` that belong to cross-project sessions.** Out of scope for this PR — handle as a one-time manual cleanup after the fix ships, or as a separate chore.
- **Generalizing the consistency guard to cover all `AgentSession` creation paths.** The bridge is already correct. Adding a guard at `_push_agent_session` would be defense-in-depth but would require a way to inject/access the project dict inside that function. Out of scope.
- **Changing `projects.json` schema or `AgentSession` fields.** Neither is needed. Fix is entirely in the CLI layer.

## Risks

### Risk 1: `resolve_project_key` contract change breaks unknown callers

**Impact:** Code outside the two known callers (`tools/valor_session.py:cmd_create`, `agent/reflection_scheduler.py:397`) may depend on the silent `"valor"` fallback. If any such caller exists, the new exception will surface as an uncaught crash.

**Mitigation:** `grep -rn "resolve_project_key" --include="*.py"` confirms only the two known callers in production code. Tests and CLI utilities are the only other touches. Before landing, rerun the grep on main and manually review each hit. If a new caller is found, catch the new exception explicitly at that call site or add a default-key argument (e.g., `resolve_project_key(cwd, default=None)`).

### Risk 2: Worktree creation happens before the consistency guard

**Impact:** If the guard fires AFTER `get_or_create_worktree()` was called with the wrong base, we leave a stale worktree on disk.

**Mitigation:** Run the consistency guard BEFORE the worktree call. The reordered resolution in the technical approach places the guard between `working_dir` resolution and `get_or_create_worktree`. If this order is preserved in implementation, no orphans are created.

### Risk 3: Parent inheritance breaks sessions that intentionally cross projects

**Impact:** A legitimate use case (rare) where a PM in project X spawns a dev session targeting project Y will be refused by the consistency guard if it also inherits from the parent.

**Mitigation:** Parent inheritance is a default, not a requirement. Explicit `--project-key` and `--working-dir` flags always override the parent. Document this clearly in the CLI help and in `docs/features/session-isolation.md`.

### Risk 4: `projects.json` unreadable in a subprocess context

**Impact:** If the CLI runs in a context where `bridge.routing.load_config()` fails (e.g., launchd agent without Desktop TCC access, `VALOR_LAUNCHD=1` missing), we lose the ability to default `working_dir` from `project_key`.

**Mitigation:** The existing `load_config()` fallback chain (env var → Desktop → `config/projects.json`) already handles this. The CLI must propagate the `ProjectsConfigUnavailableError` with a clear message. In practice, PM sessions run inside the worker process which has already loaded config successfully, so this is mostly a theoretical risk.

## Race Conditions

No race conditions identified — `cmd_create` is a synchronous CLI entry point that performs one enqueue to Redis via `asyncio.run()`. There is no shared mutable state and no cross-process coordination during session creation. The only async boundary is `_push_agent_session`, which is idempotent at the Redis layer (duplicate writes overwrite harmlessly under Popoto's `async_create`).

## No-Gos (Out of Scope)

- Fixing issue #1157 (phantom `local-*` twins from the `user_prompt_submit` hook). Different root cause.
- Modifying `AgentSession` schema or adding new model fields. `project_config` already exists from PR #685.
- Changing `bridge/telegram_bridge.py`. The bridge already behaves correctly.
- Migrating existing orphaned worktrees under `ai/.worktrees/sdlc-*`. One-time manual cleanup, tracked separately.
- Updating PM skill prompts (`config/personas/project-manager.md`) to always pass `--project-key`. The fix makes this unnecessary; prompts can rely on auto-resolution.
- Generalizing the consistency guard to `_push_agent_session`. Defense-in-depth; not required.
- Changes to `agent/worktree_manager.py`. Worktree creation is agnostic to `project_key`.

## Update System

No update system changes required — this feature is purely internal. The fix lives entirely inside `tools/valor_session.py` with one defensive touch in `agent/reflection_scheduler.py`. No new dependencies, no new config files, no migration steps. The `/update` skill (`scripts/remote-update.sh`) requires no changes.

## Agent Integration

No agent integration required — this is a CLI-layer bug in `tools/valor_session.py`. The CLI is invoked from within worker-spawned sessions via subprocess, not via the MCP tool surface. The fix does not add, remove, or modify any MCP tools. `.mcp.json` is unchanged.

The fix does affect **what the PM agent experiences** when it runs `python -m tools.valor_session create --role pm ...` inside its subprocess — child sessions will now be correctly scoped to the parent's project. That behavior is already the documented expectation in `config/personas/project-manager.md`; this plan makes the implementation match.

Integration test coverage (see Test Impact) includes an end-to-end three-level PM chain that verifies agent-facing behavior.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-isolation.md` to document the `project_key → working_dir` derivation rule and the consistency guard. Add a new subsection "CLI-level project scope resolution" covering the precedence chain (`--project-key` > `--parent` > cwd match) and the `working_dir` derivation from `projects.json`.
- [ ] Update `CLAUDE.md` "Quick Commands" table entry for `valor-session create --role pm/dev` to note that `project_key` and `working_dir` are derived from `projects.json` when not specified, and that unmatched cwd fails loud.

### Inline Documentation

- [ ] Update the module docstring in `tools/valor_session.py` (lines 23-31) to reflect the new resolution order and the removal of the silent `"valor"` fallback.
- [ ] Add a docstring to the new `_resolve_project_working_directory` helper.
- [ ] Update the docstring for `resolve_project_key` to document the new raised exception types.

### External Documentation Site

Not applicable — this repo does not publish external docs.

## Success Criteria

- [ ] A parent PM session running with `cwd=/Users/valorengels/src/cuttlefish` that invokes `valor-session create --role pm --message "Run SDLC on issue 290"` (no `--working-dir`, no `--project-key`) produces a child `AgentSession` with `project_key=cuttlefish` AND `working_dir` rooted under `/Users/valorengels/src/cuttlefish`.
- [ ] When `--project-key cuttlefish` is passed explicitly from a cwd of `/Users/valorengels/src/ai`, the resulting session's `working_dir` is rooted under `/Users/valorengels/src/cuttlefish`, not the ai repo.
- [ ] A `valor-session create` invocation from a cwd that matches no project in `projects.json`, with no `--project-key` flag, exits non-zero with a clear error message naming the cwd and listing valid project keys (not silently defaulting to `"valor"`).
- [ ] Session creation refuses a `working_dir` that is not inside the declared `working_directory` of the resolved `project_key` (unless `--allow-external-working-dir` is explicitly provided).
- [ ] Regression test: three-level PM chain — parent PM in project X → child PM via `valor-session create` → grandchild via another `valor-session create` — all levels carry consistent `project_key` and `working_dir` rooted inside X.
- [ ] `AgentSession.project_config` is populated on CLI-created sessions with the full project dict from `projects.json[project_key]`, matching the bridge's behavior.
- [ ] `agent/reflection_scheduler.py` handles the new `ProjectKeyResolutionError` gracefully (falls back to `PROJECT_KEY` env var or logs a warning).
- [ ] All updated unit tests pass (`pytest tests/unit/test_valor_session_project_key.py tests/unit/test_pm_session_auto_slug.py tests/unit/test_pm_session_refuse_no_issue.py tests/unit/test_valor_session_cli.py`).
- [ ] New tests pass (`pytest tests/unit/test_valor_session_working_dir_resolution.py tests/integration/test_valor_session_cross_project_spawn.py`).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Solo dev work via `/do-build`. No formal team members required — this is a ~40-line surgical fix with test updates in a single module. The builder agent handles both the implementation and the test changes; a single validator pass verifies the acceptance criteria.

### Team Members

- **Builder (valor-session)**
  - Name: `valor-session-builder`
  - Role: Implement the resolution reorder, the consistency guard, and the `resolve_project_key` contract change in `tools/valor_session.py`; update `agent/reflection_scheduler.py` defensively; update tests.
  - Agent Type: builder
  - Resume: true

- **Validator (valor-session)**
  - Name: `valor-session-validator`
  - Role: Verify all acceptance criteria, run the test suite, inspect the new error messages.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `_resolve_project_working_directory` helper and new error types

- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session_working_dir_resolution.py` (create) — tests for the helper directly
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `ProjectKeyResolutionError` and `ProjectsConfigUnavailableError` exception classes to `tools/valor_session.py`.
- Add `_resolve_project_working_directory(project_key: str) -> Path` helper that loads `projects.json`, returns the expanded `working_directory` for the given key, and raises `ProjectKeyResolutionError` on missing key.
- Unit-test the helper in isolation.

### 2. Reorder `cmd_create` resolution: project_key → project dict → working_dir → worktree → consistency guard

- **Task ID**: build-cmd-create
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_valor_session_working_dir_resolution.py`, `tests/unit/test_valor_session_project_key.py` (updated), `tests/unit/test_pm_session_auto_slug.py` (updated)
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: false
- Move the `project_key` resolution block above the `working_dir` assignment in `cmd_create`.
- Load the project dict once: `project = load_config()["projects"][project_key]`.
- Change `working_dir = args.working_dir or str(_repo_root)` to `working_dir = args.working_dir or str(_resolve_project_working_directory(project_key))`.
- Keep the `get_or_create_worktree(Path(working_dir), slug)` call but ensure it runs AFTER `working_dir` resolution AND AFTER the consistency guard.
- Pass `project_config=project` through to `_push_agent_session`.

### 3. Add consistency guard and `--allow-external-working-dir` escape hatch

- **Task ID**: build-guard
- **Depends On**: build-cmd-create
- **Validates**: `tests/unit/test_valor_session_working_dir_resolution.py` (new assertions)
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_assert_working_dir_consistent(working_dir, project_key, project)` helper that raises `WorkingDirInconsistentError` on mismatch.
- Add `--allow-external-working-dir` flag to the `create` subparser; when set, skip the guard.
- Call the guard between `working_dir` defaulting and `get_or_create_worktree` (so worktrees aren't created under an inconsistent base).

### 4. Remove silent `"valor"` fallback in `resolve_project_key`

- **Task ID**: build-resolve
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_valor_session_project_key.py` (replaced tests)
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the `return "valor"` paths in `resolve_project_key` with `raise ProjectKeyResolutionError(cwd, reason, available_keys)`.
- Replace the `except Exception as e: print(...); return "valor"` at line 142-147 with `raise ProjectsConfigUnavailableError(e)`.
- Update the function docstring.

### 5. Add parent inheritance to `cmd_create`

- **Task ID**: build-parent-inherit
- **Depends On**: build-cmd-create
- **Validates**: `tests/integration/test_parent_child_round_trip.py` (updated)
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: false
- When `--parent <id>` is provided, load the parent `AgentSession` via `AgentSession.query.filter(id=parent_id).first()`.
- Default `project_key` to `parent.project_key` unless `--project-key` is explicit.
- Default `working_dir` to `parent.working_dir` unless `--working-dir` is explicit.
- Emit a stderr notice: `"  Inherited from parent: project_key={key}, working_dir={dir}"`.

### 6. Audit and update `agent/reflection_scheduler.py`

- **Task ID**: build-reflection-audit
- **Depends On**: build-resolve
- **Validates**: existing reflection scheduler tests; add one new test for the no-match path
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Change the `except Exception` at lines 398-399 to catch `ProjectKeyResolutionError` and `ProjectsConfigUnavailableError` explicitly.
- Log a warning when the fallback path fires.
- Keep the `os.environ.get("PROJECT_KEY", "valor")` fallback for defensive reasons.

### 7. Update existing tests and add new ones

- **Task ID**: build-tests
- **Depends On**: build-cmd-create, build-guard, build-resolve, build-parent-inherit
- **Validates**: full test run
- **Informed By**: —
- **Assigned To**: valor-session-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Execute all items in the `## Test Impact` section.
- Run `pytest tests/unit/test_valor_session* tests/unit/test_pm_session* tests/integration/test_parent_child_round_trip.py tests/integration/test_valor_session_cross_project_spawn.py` and verify pass.
- Run the full unit suite to catch collateral damage: `pytest tests/unit/ -n auto`.

### 8. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: valor-session-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with the CLI resolution rules.
- Update the module docstring and helper docstrings in `tools/valor_session.py`.
- Update `CLAUDE.md` Quick Commands table entries for `valor-session create`.

### 9. Final Validation

- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: valor-session-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all commands in the Verification table below.
- Verify every item in `## Success Criteria`.
- Verify no occurrences of the old silent fallback: `grep -n 'return "valor"' tools/valor_session.py` must be empty (or only match explicit test cases).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_valor_session_project_key.py tests/unit/test_pm_session_auto_slug.py tests/unit/test_pm_session_refuse_no_issue.py tests/unit/test_valor_session_cli.py tests/unit/test_valor_session_working_dir_resolution.py tests/integration/test_parent_child_round_trip.py tests/integration/test_valor_session_cross_project_spawn.py -x -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -n auto -q` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_session.py agent/reflection_scheduler.py` | exit code 0 |
| Silent fallback removed | `grep -n 'return "valor"' tools/valor_session.py` | exit code 1 (no match in production code) |
| New helper exported | `python -c "from tools.valor_session import _resolve_project_working_directory, ProjectKeyResolutionError"` | exit code 0 |
| Consistency guard runs | `python -c "from tools.valor_session import _assert_working_dir_consistent"` | exit code 0 |
| `project_config` is populated on CLI sessions | `python -c "from models.agent_session import AgentSession; s = AgentSession.query.filter(sender_name='valor-session (pm)').first(); assert s is None or s.project_config" ` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Worktree re-resolution after `working_dir` correction.** After fix (1), `working_dir` is derived from `projects.json[project_key].working_directory`, so the subsequent `get_or_create_worktree(Path(working_dir), slug)` call automatically creates the worktree under the correct repo. Confirmed safe — no additional re-resolution logic needed. **Resolved in plan.**

2. **Test harness for a PM subprocess calling `valor-session create` with an arbitrary cwd.** The existing `tests/unit/test_pm_session_auto_slug.py` and `tests/unit/test_valor_session_project_key.py` already exercise `cmd_create` in-process with `os.getcwd()` patches. Adding `tests/unit/test_valor_session_working_dir_resolution.py` extends this pattern for the new resolution order; no new harness machinery required. **Resolved in plan.**

3. **Audit `agent/reflection_scheduler.py` before changing `resolve_project_key` contract.** The reflection scheduler passes `project_root=~/src/ai`, which always matches the `valor` project in `projects.json`. Its existing try/except around the import already insulates it from exceptions. Task 6 makes the exception handling explicit and adds a log warning. **Resolved in plan.**

4. **Should the consistency guard cover `--working-dir` overrides too?** The plan adds `--allow-external-working-dir` as an explicit escape hatch. Without the flag, even a user-supplied `--working-dir` is checked against the resolved `project_key`. Is this too strict for debugging / cross-project spikes? **Needs human input.** Alternative: make the guard a warning (not an error) when `--working-dir` is explicit.

5. **Should parent inheritance take priority over `os.getcwd()` resolution, or vice versa?** The plan puts `--parent` second in the precedence chain (`--project-key` > `--parent` > cwd). An argument could be made that cwd should win for a CLI user who `cd`'d somewhere deliberately. However, when invoked from a worker-spawned subprocess, the cwd is the parent's `working_dir` — so `--parent`-first and `cwd`-first converge in practice, and `--parent`-first is more explicit. **Proposed resolution in plan; flag for human confirmation.**
