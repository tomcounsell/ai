---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1158
last_comment_id:
---

# Child Session Project Scope — enforce immutable project→repo pairing in `valor-session`

## Problem

A PM-role session running in project `cuttlefish` (`cwd=/Users/valorengels/src/cuttlefish`) invokes `valor-session create --role pm --message "Run SDLC on issue 290"` to spawn a child SDLC session. The child's `project_key` is correctly `cuttlefish`, but its `working_dir` is `/Users/valorengels/src/ai/.worktrees/sdlc-290` — rooted in the wrong repo. The worker then launches the child's `claude -p` subprocess with the wrong `cwd`, and every `git`, `gh`, and file operation lands in `ai/` instead of `cuttlefish/`.

**Root cause (broader than originally scoped):**

The CLI treats `project_key` and `working_dir` (the on-disk repo) as two independently-configurable inputs. They aren't. On this machine the pairing is set by `projects.json`: one `project_key` maps to one `working_directory`. Any code path that lets a caller supply a `working_dir` (or a `--working-dir` flag) *separately from* `project_key` can break the pairing.

**Governing design principle (applies plan-wide):**

> A project and a repo should not be provided separately. The local machine's configuration sets the pairing and that pairing cannot be broken. The name of the project determines the repo, and no logical path should be allowed to choose a different repo.

Translated into surface rules:

- `project_key` → (via `projects.json`) → repo path. That is the **only** resolution path.
- `resolve_project_key(...)` must raise a typed error on unknown/unset/unmatched key. No `"valor"` fallback. No `os.getcwd()` fallback. No "try parent, then env, then cwd" chain.
- The `--working-dir` flag on `valor-session create` and every `working_dir=` kwarg on session-creation public APIs get removed. If a caller needs a different repo, they pass a different `project_key`.

**Current behavior (evidence):**

In `tools/valor_session.py:cmd_create`, `project_key` and `working_dir` are resolved as two independent defaults that do not talk to each other:

- `working_dir` defaults to `_repo_root` — a module constant `Path(__file__).parent.parent` that always equals `/Users/valorengels/src/ai` regardless of caller (line 244).
- `project_key` is derived via `resolve_project_key(os.getcwd())`, which walks `projects.json` by longest-prefix match and falls back to `"valor"` silently when cwd matches no project (lines 168-176).

Concrete evidence (2026-04-24 Cuttlefish session tree, via `python -m tools.valor_session inspect --id <id> --json`):

- Parent PM `f0ea712a63e34be39006b085c50126f9`: `project_key=cuttlefish`, `working_dir=/Users/valorengels/src/cuttlefish` (correct — bridge-enqueued).
- Child PM `b84f11e034ab4ce88dc363e9365e5fe0`: `parent_agent_session_id=f0ea712…`, `sender_name=valor-session (pm)`, `project_key=cuttlefish`, `working_dir=/Users/valorengels/src/ai/.worktrees/sdlc-290` — **misrouted**.
- Second child `5a60ddf4440b4f02934a4da4e3686491`: same shape, `sdlc-291`.

**Desired outcome:**

- `valor-session create` has **no** `--working-dir` flag. Callers who want a different repo supply `--project-key`.
- `resolve_project_key(cwd)` raises `ProjectKeyResolutionError` on unmatched cwd. It does not return `"valor"`. It is no longer the only resolution surface — most callers should supply a key explicitly rather than relying on cwd inference.
- `_push_agent_session(...)` (the internal Redis-write primitive) still takes `working_dir` as a parameter because it writes raw model fields; but the only callers that compute `working_dir` do so by looking up `projects.json[project_key].working_directory` (or appending `.worktrees/{slug}` to it).
- `AgentSession.project_config` is populated on CLI-created sessions to match the bridge's behavior (PR #685).

## Freshness Check

**Baseline commit:** `46b2de03389dcdb38ad2c348e9b7e43365d3d8e9` (main, 2026-04-24)
**Issue filed at:** 2026-04-24T07:21:31Z
**Disposition:** Unchanged since original plan draft; principle change is human-direction only.

**File:line references re-verified:**
- `tools/valor_session.py:82` — `_repo_root = Path(__file__).parent.parent` — still holds. **Planned for removal** from session-creation defaulting (kept only if still needed for sys.path bootstrap at line 83).
- `tools/valor_session.py:121-176` — `resolve_project_key()` with silent `"valor"` fallback at 141-147 and 168-176 — still holds. **Planned for removal of both fallback branches.**
- `tools/valor_session.py:244` — `working_dir = args.working_dir or str(_repo_root)` — still holds. **Planned for removal in full**; `working_dir` derives from project lookup only.
- `tools/valor_session.py:270-276` — `get_or_create_worktree(Path(working_dir), slug)` — still holds. **Planned to keep**, but `working_dir` passed in is now always the project-declared root.
- `tools/valor_session.py:278-283` — `project_key` resolution via `explicit_key` or `resolve_project_key(os.getcwd())` — still holds. **Planned to reorder above `working_dir`**, since `project_key` is the only input.
- `tools/valor_session.py:1047` — `create_parser.add_argument("--working-dir", ...)` — still holds. **Planned for deletion.**
- `bridge/telegram_bridge.py:1218-1222` — `project.get("working_directory", DEFAULTS.get("working_directory", ""))` — still holds. This is the bridge doing the right thing (deriving working_directory from the matched project dict). **No change needed here.**
- `bridge/telegram_bridge.py:1972-1988` — `dispatch_telegram_session(..., project_config=project, ...)` — confirmed; bridge also passes the full dict. **No change needed.**
- `agent/sdk_client.py:3107-3108` — `ValorAgent(working_dir=working_dir, ...)` — still holds. This is internal plumbing (session→subprocess) that reads `AgentSession.working_dir` from Redis; not a user surface. **No change needed** beyond what propagates from the CLI fix.
- `agent/reflection_scheduler.py:394-399` — secondary caller of `resolve_project_key(str(project_root))` — still holds. `project_root = ~/src/ai` reliably matches the `valor` project key in `projects.json`, so the fallback was defensive-only. **Planned: make exception handling explicit and specific.**
- `tools/sdlc_session_ensure.py:122-127` — also calls `resolve_project_key(os.getcwd())` **and** passes `working_dir=os.getcwd()` to `AgentSession.create_local`. This is a call site the original plan missed. **Planned update**: drop `working_dir=os.getcwd()`, derive from `projects.json[project_key].working_directory` instead (same rule as the CLI).
- `tools/agent_session_scheduler.py:303-311, 326-327, 338, 566` — already correctly derives `working_dir` from `projects.json[project_key].working_directory` (line 311), with parent-override (line 327). **No user-supplied path override.** This is the *model* we want; no change required.

**Cited sibling issues/PRs re-checked:**
- #1157 — OPEN (`bug: user_prompt_submit hook creates phantom local-* AgentSession twins for worker-spawned PM sessions`). Separate root cause (hook, not CLI). This plan's fix mitigates #1157's symptom (phantom inheriting `project_key=valor` because cwd was mis-rooted) but does not fix twin creation itself.
- #887 — CLOSED 2026-04-10 (`Session isolation bypass: PM sessions created via valor-session create operate in main checkout instead of a worktree`). Introduced the `--slug` flag and the worktree call at line 274. It assumed `working_dir` was already correctly scoped — this bug sits upstream of that assumption.
- #1109 — CLOSED 2026-04-22 (`PM sessions silently fail when created without --slug`). Same file, different bug. Added auto-slug derivation from "issue #N".
- #397 — CLOSED 2026-04-04 via PR #685. Eliminated `_project_configs` parallel registry and added `project_config: DictField` to `AgentSession`. Directly relevant — the model already has a field for carrying the full project dict; the bridge already populates it; the CLI does not.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None found referencing `valor_session`, `project_key`, or `working_dir` defaulting.

**Notes:** The issue body's Solution Sketch listed three fixes; this revision supersedes that sketch. The principle is stricter: rather than "derive `working_dir` from `project_key` when not supplied," it is "there is no separate `working_dir` input at all — always derive."

## Prior Art

- **PR #685** (merged 2026-04-04): added `project_config: DictField` to `AgentSession` so the full project dict travels with the session. Directly relevant: the model already supports durable project context; the CLI just doesn't use it.
- **#887** (closed 2026-04-10): added `--slug` to `valor-session create` plus the `get_or_create_worktree` call at line 274. Did not address `working_dir` defaulting.
- **#1109** (closed 2026-04-22): auto-derive slug from "issue #N"; truncation fix. Same file, different bug.
- **#397** (closed 2026-04-04): broad audit that produced PR #685.
- **#1157** (open): phantom `local-*` twin sessions from the `user_prompt_submit` Claude Code hook. Shares the Cuttlefish session-tree evidence but is a different root cause.
- **`tools/agent_session_scheduler.py`** (current shape): already follows the immutable-pairing principle — `working_dir` is looked up from `_load_projects_config().get("projects", {}).get(project_key, {}).get("working_directory", ...)`. Use this as the reference pattern.

No prior attempt has addressed this CLI-level defaulting bug. The mechanism to fix it (`project_config` DictField, `projects.json` schema, `bridge.routing.load_config()`) is already in place.

## Research

No relevant external findings — proceeding with codebase context and training data. The fix is purely internal: CLI flag surgery, `projects.json` lookup, worktree path composition, and tightening one helper's contract. No external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

**Current (broken):**

1. Parent PM runs `valor-session create --role pm --message "Run SDLC on issue 290"` from `cwd=/Users/valorengels/src/cuttlefish`, no `--working-dir`, no `--project-key`.
2. `cmd_create` enters (`tools/valor_session.py:195`):
   a. `working_dir = args.working_dir or str(_repo_root)` at line 244 → `working_dir=/Users/valorengels/src/ai` (wrong).
   b. `slug` auto-derived → `sdlc-290`.
   c. `get_or_create_worktree(Path(working_dir), slug)` at line 274 → creates `/Users/valorengels/src/ai/.worktrees/sdlc-290/`, reassigns `working_dir` to that path.
   d. `project_key` resolution at lines 278-283: explicit_key=None → `resolve_project_key(os.getcwd())` → returns `"cuttlefish"`.
3. `_push_agent_session` called with `project_key="cuttlefish"`, `working_dir="/Users/valorengels/src/ai/.worktrees/sdlc-290"`, no `project_config`. Mismatch written to Redis.
4. Worker picks up session. `sdk_client.py` sets subprocess cwd to the mismatched `working_dir`. Every `git`, `gh`, `Edit` lands in `ai/`.

**Fixed:**

1. Same parent invocation.
2. `cmd_create` resolves `project_key` **first and only** (from `--project-key`, else parent `AgentSession.project_key`, else `resolve_project_key(os.getcwd())`). On unmatched cwd with no `--project-key`, raises `ProjectKeyResolutionError` — no silent coercion.
3. Load `project = bridge.routing.load_config()["projects"][project_key]` once. `repo_root = Path(project["working_directory"]).expanduser()`.
4. If `slug` provided or auto-derived: `working_dir = get_or_create_worktree(repo_root, slug)`. Else: `working_dir = repo_root`.
5. Pass `working_dir`, `project_key`, and `project_config=project` to `_push_agent_session`. There is no path by which a caller-supplied `working_dir` reaches the model.

## Architectural Impact

- **Interface changes (public surface):**
  - `valor-session create` **loses** the `--working-dir` flag entirely.
  - `resolve_project_key(cwd)` contract changes: raises `ProjectKeyResolutionError` on unmatched cwd, and `ProjectsConfigUnavailableError` when `load_config()` itself raises. No silent fallback.
- **Internal/private surface unchanged:** `_push_agent_session(..., working_dir=...)` keeps `working_dir` as a kwarg because it writes the field to Redis — but every caller now computes that value from the project dict, not from user input.
- **Coupling:** `tools/valor_session.py` and `tools/sdlc_session_ensure.py` both gain an explicit dependency on `projects.json[project_key].working_directory`. They already depend on `bridge.routing.load_config()` indirectly via `resolve_project_key`; this just makes the dependency direct.
- **Data ownership:** `AgentSession.project_config` is the canonical carrier (PR #685). The CLI now populates it.
- **Reversibility:** Changes are confined to `tools/valor_session.py`, `tools/sdlc_session_ensure.py`, `agent/reflection_scheduler.py` (defensive), and tests. Revert is `git revert`.

## Appetite

**Size:** Medium (revised up from Small — the original ~40-line patch is no longer accurate).

**Team:** Solo dev.

**Interactions:**
- PM check-ins: 0 (scope is fully specified).
- Review rounds: 1.

The work removes a CLI flag, tightens one helper's contract, migrates one adjacent caller (`sdlc_session_ensure.py`), and rewrites a non-trivial test file. Realistic size is ~150-200 lines changed across production code plus test rewrites. Not "Small" anymore. Still small enough for one PR and one validator pass.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `projects.json` readable | `python -c "from bridge.routing import load_config; assert load_config().get('projects')"` | CLI must be able to look up per-project `working_directory` |
| `AgentSession.project_config` field exists | `python -c "from models.agent_session import AgentSession; assert 'project_config' in AgentSession._fields"` | Added in PR #685 |
| `agent/worktree_manager.get_or_create_worktree` signature unchanged | `python -c "import inspect; from agent.worktree_manager import get_or_create_worktree; sig = inspect.signature(get_or_create_worktree); assert 'repo_root' in sig.parameters and 'slug' in sig.parameters"` | Worktree creation accepts a repo_root override |

Run all checks: `python scripts/check_prerequisites.py docs/plans/child-session-project-scope.md`

## Solution

### Key Elements

- **Remove `--working-dir`** from `tools/valor_session.py`'s `create` subparser. No flag, no namespace attribute, no code path reads `args.working_dir`.
- **Remove the `working_dir = args.working_dir or str(_repo_root)` line** (currently line 244). There is no defaulting chain; `working_dir` is derived, not supplied.
- **Tighten `resolve_project_key`** to raise `ProjectKeyResolutionError` (on unmatched cwd / empty projects / missing `working_directory`) and `ProjectsConfigUnavailableError` (on `load_config` failure). Error messages name the cwd, list available keys, and suggest `--project-key`.
- **Add `_resolve_project_working_directory(project_key: str) -> Path`** — loads `projects.json`, returns expanded `working_directory` for the key. Raises `ProjectKeyResolutionError` on missing key or missing/empty `working_directory`.
- **Reorder `cmd_create`**: `project_key` → project dict → `repo_root` → `slug` → `working_dir = (worktree or repo_root)` → enqueue with `project_config`.
- **Parent inheritance:** when `--parent <id>` is supplied, default `project_key` to `parent.project_key`. There is no separate `working_dir` inheritance — once `project_key` is set, `working_dir` follows.
- **Migrate `tools/sdlc_session_ensure.py`** to derive `working_dir` from `projects.json[project_key].working_directory` instead of passing `os.getcwd()`.
- **Make `agent/reflection_scheduler.py` exception handling explicit** — catch the new typed errors specifically and log a warning.
- **Populate `project_config`** on CLI enqueue for parity with the bridge (`bridge/telegram_bridge.py:1987`).

### Flow

Parent PM (in project X) runs `valor-session create --role pm --message "..."` → `cmd_create` resolves `project_key=X` (explicit, parent-inherited, or cwd-matched; raises if unmatched) → `project = load_config()["projects"][X]` → `repo_root = Path(project["working_directory"])` → if slug: `working_dir = get_or_create_worktree(repo_root, slug)` else `working_dir = repo_root` → `_push_agent_session(..., project_key=X, working_dir=working_dir, project_config=project)`.

### Technical Approach

Concrete edits to `tools/valor_session.py`:

1. **Delete line 244** (`working_dir = args.working_dir or str(_repo_root)`).
2. **Delete line 1047** (`create_parser.add_argument("--working-dir", ...)`).
3. **Delete** the module-docstring mention of `--working-dir` (implicit in line 23-31 rewrite).
4. **Rewrite `resolve_project_key` (lines 121-176):**
   - Replace `except Exception as e: print(..., file=sys.stderr); return "valor"` (141-147) with `raise ProjectsConfigUnavailableError(f"could not load projects.json: {e}") from e`.
   - Replace the trailing `print(...); return "valor"` (168-176) with `raise ProjectKeyResolutionError(cwd=cwd, available_keys=sorted(projects.keys()))`.
   - Update docstring.
5. **Add exception classes** near the top of `tools/valor_session.py`:
   - `class ProjectKeyResolutionError(ValueError)` with `__init__(self, cwd, available_keys)` producing a message like `"cwd {cwd!r} does not match any project in projects.json. Available keys: {available_keys}. Pass --project-key <key> explicitly."`.
   - `class ProjectsConfigUnavailableError(RuntimeError)`.
6. **Add `_resolve_project_working_directory(project_key: str) -> Path`** helper that wraps `load_config()` and returns `Path(projects[project_key]["working_directory"]).expanduser()`. Raises `ProjectKeyResolutionError` if `project_key` is not in `projects` or the key has no `working_directory`.
7. **Reorder the body of `cmd_create` (lines 195-337):**
   - Compute `project_key` first (explicit flag > parent lookup > `resolve_project_key(os.getcwd())`).
   - Compute `repo_root = _resolve_project_working_directory(project_key)`.
   - Resolve `slug` (PM auto-derivation logic unchanged).
   - If `slug`: `working_dir = str(get_or_create_worktree(repo_root, slug))`. Else: `working_dir = str(repo_root)`.
   - Call `_push_agent_session(..., working_dir=working_dir, project_key=project_key, project_config=project)`.
8. **Add parent-inheritance block** before the `project_key` resolution: if `args.parent`, look up `AgentSession.get_by_id(args.parent)` (or `.query.filter(agent_session_id=args.parent).first()`), and if found without `--project-key`, set `project_key = parent.project_key`. Emit a stderr notice.
9. **Delete the `create_parser.add_argument("--working-dir", ...)` line (1047)** and any associated help text in the module docstring (lines 23-31).

Concrete edits to `tools/sdlc_session_ensure.py`:

10. Replace `working_dir=os.getcwd()` (line 127) with `working_dir=str(_resolve_project_working_directory(project_key))` where `project_key` is the resolved key on line 126. Catch `ProjectKeyResolutionError` / `ProjectsConfigUnavailableError` and bail out of ensure_session rather than creating a mismatched session.

Concrete edits to `agent/reflection_scheduler.py`:

11. Replace the blanket `except Exception: project_key = os.environ.get("PROJECT_KEY", "valor")` (lines 398-399) with explicit `except (ProjectKeyResolutionError, ProjectsConfigUnavailableError) as e: logger.warning("reflection scheduler could not resolve project_key via projects.json: %s", e); project_key = os.environ.get("PROJECT_KEY", "valor")`. The fallback stays (this is not the enforcement surface — the CLI is), but the exception path is no longer silent.

No changes needed to `AgentSession` model, `bridge/telegram_bridge.py`, `agent/sdk_client.py`, `agent/worktree_manager.py`, or `tools/agent_session_scheduler.py` (already correct).

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `tools/valor_session.py:cmd_create` has a broad `try/except Exception` that catches any error and returns exit code 1. Verify both new exceptions produce clear stderr messages **before** being caught — tests must assert stderr content, not just exit code.
- [ ] `agent/reflection_scheduler.py:394-399` now catches two specific exception types. Add a test that simulates each raising and asserts the scheduler falls back gracefully and logs a warning.
- [ ] `tools/sdlc_session_ensure.py:ensure_session` — on `ProjectKeyResolutionError`, it should return `{}` (no session created) rather than coercing to a wrong project.

### Empty/Invalid Input Handling

- [ ] `cmd_create` when `--project-key` refers to a key not in `projects.json` → exit 1, stderr names the missing key and lists available keys.
- [ ] `cmd_create` when cwd matches no project and no `--project-key` → exit 1 with same hint.
- [ ] `cmd_create` when `projects.json` is unloadable → exit 1 with `ProjectsConfigUnavailableError` message.
- [ ] `cmd_create` when `--parent <id>` points to a nonexistent session → exit 1.

### Error State Rendering

- [ ] All new error paths write to stderr, preserving stdout for `--json`. Assert `stdout_capture.getvalue() == ""` on error.
- [ ] Error messages include: attempted value, list of valid project keys, suggested remediation (`pass --project-key <key>`).

## Test Impact

Test files rewritten to match the new surface. Counts: **5 existing files affected, 1 new file added, net disposition REPLACE-heavy.**

- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback::test_no_match_returns_valor` (line 111) — **REPLACE**: assert `with pytest.raises(ProjectKeyResolutionError): resolve_project_key(...)` and that the exception message contains the cwd and the available keys list.
- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback::test_empty_projects_returns_valor` (line 131) — **REPLACE**: empty projects + any cwd → raises `ProjectKeyResolutionError`.
- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback::test_load_config_exception_returns_valor` (line 141) — **REPLACE**: `load_config` raising → `ProjectsConfigUnavailableError` (distinct from `ProjectKeyResolutionError`).
- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback::test_project_missing_working_directory_skipped` (line 151) — **REPLACE**: when no project matches → raises; when the one matching project has an empty `working_directory` AND cwd is exactly that path, the helper `_resolve_project_working_directory` also raises.
- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback::test_project_empty_working_directory_skipped` (line 164) — **REPLACE**: same treatment as above.
- [ ] `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback::test_fallback_warning_goes_to_stderr_not_stdout` (line 177) — **REPLACE**: rewrite to assert that when `cmd_create` catches the new exception, the CLI's stderr is non-empty and stdout is clean. (The helper itself no longer prints.)
- [ ] `tests/unit/test_valor_session_project_key.py::TestProjectKeyFlagOverride::test_explicit_flag_bypasses_resolution` (line 199) — **UPDATE**: still valid; extend to also assert that the test namespace no longer has a `working_dir` attribute (the flag is gone).
- [ ] `tests/unit/test_pm_session_auto_slug.py` — multiple tests use `_make_args(..., working_dir=None, ...)` at lines 36, and reference auto-derived working_dir in lines 77 etc. — **UPDATE**: drop `working_dir` from `_make_args` defaults; mock `bridge.routing.load_config` to return a test `projects.json` including a `"valor"` key with `working_directory` pointing at `tmp_path`; assert the derived `working_dir` passed to `fake_push` equals the worktree path under that `tmp_path`.
- [ ] `tests/unit/test_pm_session_refuse_no_issue.py` (line 32: `working_dir=None`) — **UPDATE**: drop the attribute; add `load_config` mock.
- [ ] `tests/unit/test_valor_session_cli.py` — three `monkeypatch.setattr(valor_session, "resolve_project_key", lambda cwd: "test-1148")` stubs (lines 76, 102, 121). **UPDATE**: keep the stubs but also mock `tools.valor_session._resolve_project_working_directory` to return a `Path`, because `cmd_create` will now call it right after `resolve_project_key`. Drop any `working_dir` namespace attribute from `_make_args` helpers.
- [ ] `tests/integration/test_parent_child_round_trip.py` (if it exists — verify) — **UPDATE**: assert child `working_dir` is rooted inside the parent's project (via the projects.json lookup), not by direct parent.working_dir inheritance.
- [ ] **ADD** `tests/unit/test_valor_session_working_dir_resolution.py` — new file covering:
  - `--project-key cuttlefish` from cwd `/Users/valorengels/src/ai` produces `working_dir` rooted at the cuttlefish project (via mocked `load_config`).
  - There is **no** `--working-dir` flag — `argparse` rejects it (`SystemExit`) and stderr mentions unrecognized arguments.
  - `--parent <id>` inherits `project_key` from the parent `AgentSession`; `working_dir` is re-derived from the inherited `project_key`, not copied from the parent.
  - Enqueue path writes `project_config` as a populated dict matching `projects.json[project_key]`.
  - Acceptance criteria from issue #1158 covered.
  - **Anti-regression grep checks** (in-test subprocess): `grep -n -- '--working-dir' tools/valor_session.py` returns no match; `grep -nE 'default\s*=\s*"valor"' tools/valor_session.py` returns no match inside `resolve_project_key`; `grep -n 'return "valor"' tools/valor_session.py` returns no match.

**Tests confirmed to remain UPDATE (not DELETE/REPLACE) because they exercise the internal `working_dir` field on `AgentSession`, not the CLI surface:**
- `tests/unit/test_agent_session.py`, `test_agent_session_hierarchy.py`, `test_agent_session_queue_async.py`, `test_sdk_client.py`, `test_harness_streaming.py`, `test_hook_user_prompt_submit.py`, `test_session_model_routing.py`, `test_cross_repo_gh_resolution.py`, `test_bridge_dispatch_contract.py`, `test_recovery_respawn_safety.py`, `test_health_check_recovery_finalization.py` — these pass `working_dir=` to the `AgentSession` model or to internal harness functions. That surface is **not** being removed; only the CLI flag and the CLI-level defaulting go away. No change required unless the test relies on the silent `"valor"` fallback (none do on inspection).

## Rabbit Holes

- **Generalizing the pairing enforcement to `_push_agent_session`.** Tempting, but `_push_agent_session` is an internal primitive called by scheduler, scheduler-like code, recovery, and tests. Adding a `project_key`→`working_dir` consistency guard there risks breaking re-enqueue/recovery paths that legitimately copy a worktree path from a parent session. The principle is enforced at the *CLI* and at `sdlc_session_ensure`; the bridge is already correct by construction. Leave the primitive alone.
- **Removing `working_dir` from `AgentSession` as a model field.** Out of scope and harmful — the worker needs the resolved path (incl. worktree sub-path) as the subprocess cwd. `working_dir` on the model is a derived cache, not a user input. Keep it.
- **Fixing #1157** (phantom `local-*` twins). Different root cause (hook, not CLI). This plan mitigates a symptom; does not fix twin creation.
- **Auditing every `working_dir=` kwarg in the codebase.** The call-site audit in this plan covers session-creation public APIs (CLI, `sdlc_session_ensure`, scheduler). Internal plumbing (`sdk_client`, `session_executor`, recovery) that already reads `AgentSession.working_dir` from Redis is out of scope.
- **Cleaning up orphaned worktrees under `ai/.worktrees/sdlc-*` from past misrouted sessions.** One-time manual cleanup, separate chore.
- **Changing `projects.json` schema or `AgentSession` fields.** Neither needed.

## Risks

### Risk 1: `resolve_project_key` contract change breaks unknown callers

**Impact:** Code outside the two known callers (`tools/valor_session.py:cmd_create`, `tools/sdlc_session_ensure.py:ensure_session`, `agent/reflection_scheduler.py`) may depend on the silent `"valor"` fallback.

**Mitigation:** `grep -rn "resolve_project_key" --include="*.py"` on main (run during freshness check above) shows only these three production callers. `sdlc_session_ensure` gets updated; `reflection_scheduler` already wraps the call in try/except with its own fallback. If a new caller appears, the CLI tests will catch it because the unit test suite imports from `tools.valor_session` directly.

### Risk 2: Worktree creation happens before project resolution

**Impact:** If the refactored ordering accidentally runs `get_or_create_worktree` before `project_key` is resolved, we can still create worktrees under the wrong base.

**Mitigation:** Step-by-step enforces the order: `project_key` → `repo_root` → `slug` → worktree. Test `test_valor_session_working_dir_resolution.py` asserts that `load_config` is consulted before `get_or_create_worktree` is called (via mock call-order assertion).

### Risk 3: Removing `--working-dir` breaks scripts or documentation

**Impact:** A shell script, CI job, or agent skill that passes `--working-dir` will now fail with "unrecognized arguments."

**Mitigation:** `grep -rn "\-\-working-dir" --include="*.py" --include="*.sh" --include="*.md"` in the repo. Build phase updates any call site that passed `--working-dir` (audit explicitly). This is the *intended* behavior of the principle — legitimate cross-project use should supply `--project-key`.

### Risk 4: `projects.json` unreadable in a subprocess context

**Impact:** If `bridge.routing.load_config()` fails (e.g., launchd agent without Desktop TCC access), we lose the ability to create sessions at all.

**Mitigation:** This is the correct failure mode under the principle — without `projects.json`, there is no defined pairing, so session creation must refuse. The existing `load_config()` fallback chain (env var → Desktop → `config/projects.json`) already handles all realistic cases. The new `ProjectsConfigUnavailableError` carries a clear message.

## Race Conditions

No race conditions identified — `cmd_create` is a synchronous CLI entry point performing one enqueue to Redis via `asyncio.run()`. No shared mutable state, no cross-process coordination during session creation.

## No-Gos (Out of Scope)

- **No `--working-dir` flag** on `valor-session create` (removed).
- **No `--allow-external-working-dir`** (or similarly named) escape-hatch flag — forbidden by the governing principle.
- **No path-based override** of `project_key`→`working_dir` anywhere in session creation.
- **No silent fallback** inside `resolve_project_key` — must raise on unmatched cwd, empty projects, or load failure.
- **No `default="valor"`** anywhere in `resolve_project_key` or `_resolve_project_working_directory`.
- **No `os.getcwd()` fallback** after `resolve_project_key` fails — the caller must fail, not guess.
- **No parent `working_dir` inheritance via copy** — parent inheritance only copies `project_key`; `working_dir` is re-derived from the inherited key.
- Fixing #1157 (phantom twins) — different root cause.
- Modifying `AgentSession` schema or adding new model fields.
- Changing `bridge/telegram_bridge.py` (already correct).
- Generalizing the pairing guard to `_push_agent_session` (internal primitive, not a user surface).
- Migrating existing orphaned worktrees under `ai/.worktrees/sdlc-*` — one-time manual cleanup, tracked separately.

## Update System

No update system changes required — this is purely internal. The fix lives in `tools/valor_session.py`, `tools/sdlc_session_ensure.py`, and `agent/reflection_scheduler.py`. No new dependencies, no new config files, no migration steps. The `/update` skill requires no changes.

One soft interaction: any shell script or automation that previously passed `--working-dir` will need an edit before/after this lands. The build phase's grep audit catches those in-repo; out-of-repo callers (if any) would surface as argparse errors on next invocation.

## Agent Integration

No agent integration required — the CLI is invoked from within worker-spawned sessions via subprocess, not via the MCP tool surface. `.mcp.json` is unchanged. No MCP tools added, removed, or modified.

The fix does affect **what the PM agent experiences** when it runs `python -m tools.valor_session create --role pm ...` inside its subprocess — child sessions will now be correctly scoped to the parent's project, and passing a path instead of a key will fail loudly rather than silently misroute. This matches the documented expectation in `config/personas/project-manager.md`; no prompt change required, but the behavior shift is worth noting in feature docs.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-isolation.md`: add a "CLI-level project scope resolution" subsection documenting (a) removal of `--working-dir`, (b) the precedence chain `--project-key` > `--parent` > cwd-match, (c) the hard-error behavior on unmatched cwd.
- [ ] Update `CLAUDE.md` Quick Commands entries for `valor-session create --role pm/dev` — note that `project_key` determines `working_dir` via `projects.json`, and there is no `--working-dir` flag.

### Inline Documentation

- [ ] Rewrite the module docstring in `tools/valor_session.py` (lines 23-31) to describe the new resolution rule: one input (`project_key`), two sources (explicit flag or cwd-match), no fallback, no `--working-dir`.
- [ ] Add docstring to `_resolve_project_working_directory`.
- [ ] Update `resolve_project_key` docstring to document the raised exception types.

### External Documentation Site

Not applicable — this repo does not publish external docs.

## Success Criteria

- [ ] A parent PM session running with `cwd=/Users/valorengels/src/cuttlefish` that invokes `valor-session create --role pm --message "Run SDLC on issue 290"` (no `--project-key`) produces a child `AgentSession` with `project_key=cuttlefish` AND `working_dir` rooted under `/Users/valorengels/src/cuttlefish`.
- [ ] When `--project-key cuttlefish` is passed explicitly from a cwd of `/Users/valorengels/src/ai`, the resulting session's `working_dir` is rooted under the cuttlefish project, not the ai repo.
- [ ] A `valor-session create` invocation from a cwd that matches no project, with no `--project-key`, exits non-zero with an error naming the cwd and listing valid keys (not silently defaulting).
- [ ] `valor-session create --working-dir /any/path --role pm --message "..."` exits with argparse error (unrecognized argument).
- [ ] Regression test: three-level PM chain in project X → all levels carry consistent `project_key` and `working_dir` rooted inside X.
- [ ] `AgentSession.project_config` is populated on CLI-created sessions.
- [ ] `agent/reflection_scheduler.py` handles the new typed exceptions with an explicit catch and a logged warning.
- [ ] `tools/sdlc_session_ensure.py` derives `working_dir` from `projects.json`, not `os.getcwd()`.
- [ ] All updated tests pass: `pytest tests/unit/test_valor_session_project_key.py tests/unit/test_pm_session_auto_slug.py tests/unit/test_pm_session_refuse_no_issue.py tests/unit/test_valor_session_cli.py tests/unit/test_valor_session_working_dir_resolution.py`.
- [ ] `grep -n 'return "valor"' tools/valor_session.py` matches nothing (except explicit test-only strings).
- [ ] `grep -n -- '--working-dir' tools/valor_session.py` matches nothing.

## Team Orchestration

Solo dev work via `/do-build`. One builder, one validator, one pass.

### Team Members

- **Builder (valor-session)**
  - Name: `valor-session-builder`
  - Role: Implement flag removal, contract tightening, call-site migration, and test rewrites.
  - Agent Type: builder
  - Resume: true

- **Validator (valor-session)**
  - Name: `valor-session-validator`
  - Role: Verify acceptance criteria, run tests, confirm the no-gos section (grep checks).
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add typed exceptions and `_resolve_project_working_directory` helper

- **Task ID**: build-exceptions
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session_working_dir_resolution.py` (new) — unit tests for the helper directly.
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `ProjectKeyResolutionError(ValueError)` and `ProjectsConfigUnavailableError(RuntimeError)` near the top of `tools/valor_session.py`.
- Add `_resolve_project_working_directory(project_key: str) -> Path` helper that calls `load_config()` and returns the expanded path, or raises the typed errors.

### 2. Tighten `resolve_project_key` — remove both silent fallbacks

- **Task ID**: build-resolve
- **Depends On**: build-exceptions
- **Validates**: rewritten `tests/unit/test_valor_session_project_key.py::TestResolveProjectKeyFallback` tests.
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `except Exception: ... return "valor"` (lines 141-147) with `raise ProjectsConfigUnavailableError(...) from e`.
- Replace final `print(...); return "valor"` (lines 168-176) with `raise ProjectKeyResolutionError(cwd=cwd, available_keys=sorted(projects.keys()))`.
- Rewrite docstring.

### 3. Remove `--working-dir` flag and reorder `cmd_create`

- **Task ID**: build-cmd-create
- **Depends On**: build-exceptions, build-resolve
- **Validates**: `tests/unit/test_valor_session_working_dir_resolution.py`, updated `test_valor_session_project_key.py`, updated `test_pm_session_auto_slug.py`, updated `test_pm_session_refuse_no_issue.py`.
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `create_parser.add_argument("--working-dir", ...)` (line 1047).
- Delete `working_dir = args.working_dir or str(_repo_root)` (line 244).
- Reorder: resolve `project_key` first → `project = load_config()["projects"][project_key]` → `repo_root = _resolve_project_working_directory(project_key)` → resolve `slug` (auto-derive logic unchanged) → `working_dir = str(get_or_create_worktree(repo_root, slug)) if slug else str(repo_root)` → `_push_agent_session(..., project_config=project)`.
- Update the module docstring (lines 23-31).

### 4. Add parent inheritance for `project_key` only

- **Task ID**: build-parent-inherit
- **Depends On**: build-cmd-create
- **Validates**: new test in `test_valor_session_working_dir_resolution.py`.
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: false
- When `args.parent` is set and `args.project_key` is not set, load parent via `AgentSession.get_by_id(args.parent)` or equivalent; if found, set `project_key = parent.project_key`.
- Emit stderr notice: `f"  Inherited project_key={project_key} from parent {parent.agent_session_id}"`.
- Do NOT copy `parent.working_dir`. `working_dir` is always re-derived.

### 5. Migrate `tools/sdlc_session_ensure.py` to the same rule

- **Task ID**: build-sdlc-ensure
- **Depends On**: build-exceptions
- **Validates**: add/update one test for `ensure_session`'s project-key path.
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `working_dir=os.getcwd()` (line 127) with a lookup via `_resolve_project_working_directory(project_key)`.
- On `ProjectKeyResolutionError` / `ProjectsConfigUnavailableError`, return `{}` (no session created) and log a debug message.

### 6. Update `agent/reflection_scheduler.py` exception handling

- **Task ID**: build-reflection-audit
- **Depends On**: build-resolve
- **Validates**: existing reflection tests + add one new test for the no-match path.
- **Assigned To**: valor-session-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace blanket `except Exception:` (lines 398-399) with specific catches: `except (ProjectKeyResolutionError, ProjectsConfigUnavailableError) as e: logger.warning(...); project_key = os.environ.get("PROJECT_KEY", "valor")`.

### 7. Rewrite tests

- **Task ID**: build-tests
- **Depends On**: build-cmd-create, build-parent-inherit, build-resolve, build-sdlc-ensure
- **Validates**: full test run.
- **Assigned To**: valor-session-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Execute all items in `## Test Impact`.
- Add anti-regression grep assertions to the new test file.
- Run `pytest tests/unit/test_valor_session* tests/unit/test_pm_session*` and full unit suite.

### 8. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: valor-session-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` and `CLAUDE.md` Quick Commands.
- Update module and helper docstrings.

### 9. Final validation

- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: valor-session-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all checks in the Verification table.
- Verify every item in Success Criteria.
- Confirm no `--working-dir`, no `return "valor"`, no `default="valor"` remain in `tools/valor_session.py` production code.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_valor_session_project_key.py tests/unit/test_pm_session_auto_slug.py tests/unit/test_pm_session_refuse_no_issue.py tests/unit/test_valor_session_cli.py tests/unit/test_valor_session_working_dir_resolution.py -x -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -n auto -q` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_session.py tools/sdlc_session_ensure.py agent/reflection_scheduler.py` | exit code 0 |
| No silent fallback | `grep -n 'return "valor"' tools/valor_session.py` | exit code 1 (no match) |
| No `--working-dir` flag | `grep -n -- '--working-dir' tools/valor_session.py` | exit code 1 (no match) |
| No default="valor" in resolver | `grep -nE 'default\s*=\s*"valor"' tools/valor_session.py` | exit code 1 (no match) |
| New exceptions exported | `python -c "from tools.valor_session import ProjectKeyResolutionError, ProjectsConfigUnavailableError, _resolve_project_working_directory"` | exit code 0 |
| `project_config` populated on CLI sessions | `python -c "from models.agent_session import AgentSession; s = next((x for x in AgentSession.query.all() if (x.sender_name or '').startswith('valor-session')), None); assert s is None or s.project_config"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Worktree re-resolution after `working_dir` correction.** After the refactor, `working_dir` is derived from `projects.json[project_key].working_directory`, so `get_or_create_worktree(repo_root, slug)` automatically creates the worktree under the correct repo. **RESOLVED in plan.**

2. **Test harness for a PM subprocess calling `valor-session create` with an arbitrary cwd.** The existing unit tests already exercise `cmd_create` in-process with `os.getcwd()` patches. Adding `tests/unit/test_valor_session_working_dir_resolution.py` extends this pattern. **RESOLVED in plan.**

3. **Audit `agent/reflection_scheduler.py` before changing `resolve_project_key` contract.** Confirmed: the reflection scheduler passes `project_root=~/src/ai`, which always matches `valor` in `projects.json`; fallback is defensive-only. Task 6 makes the exception handling explicit. **RESOLVED in plan.**

4. **Consistency-guard strictness on explicit `--working-dir` (hard error vs warning)?** **RESOLVED — Valor's decision (2026-04-24):** Neither. No overrides allowed. The `--working-dir` flag is refactored out entirely. Do NOT add an `--allow-external-working-dir` (or any similarly named) escape-hatch flag. If a caller needs a different repo, they pass a different `--project-key`.

5. **Parent inheritance priority vs `os.getcwd()` in the precedence chain?** **RESOLVED — Valor's decision (2026-04-24):** `project_key` is the only input. There is no separate `working_dir` precedence chain. Parent inheritance only copies `project_key`; `working_dir` is always re-derived from the (possibly inherited) key. If a concrete case arises where `project_key` alone cannot work, it must be raised as a new open question — never silently fixed with a fallback.
