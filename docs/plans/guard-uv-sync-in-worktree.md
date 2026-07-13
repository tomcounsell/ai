---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2050
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-13T07:10:05Z
---

# Guard: block `uv sync` from a worktree (protect the shared .venv)

## Problem

SDLC build/patch lanes run in git worktrees under `.worktrees/{slug}/` and
`.claude/worktrees/`. On developer/skills machines these worktrees do **not**
get their own Python environment — they share the single project `.venv` at the
repo root. A build subagent that "sets up dependencies" by running `uv sync`
(or `uv sync --frozen`) from inside a worktree resolves against that worktree's
`pyproject.toml`/lock and **rewrites the shared env**, dropping every package
not in the resolved set (`pytest`, `ruff`, `pytest-xdist`, `pandas`, `mypy`,
and any branch-only dep like `pydantic-ai` before it lands on main).

**Current behavior:**
- Worktrees share `<repo>/.venv`; `create_worktree` provisions no per-worktree venv.
- A subagent runs `uv sync [--frozen]` from a worktree; `uv` (exact by default)
  strips the shared env.
- Every concurrent `/do-sdlc` lane, the test runner, the lint gate, and the
  standalone worker then fail with `ModuleNotFoundError` — silently, until the
  next test or lint invocation.
- Observed **twice** on the #1925 PydanticAI lane (2026-07-12); repaired each
  time via a scoped `uv pip install --python <repo>/.venv/bin/python`. Other
  lanes (#1927, #1968) avoided it only because subagents were *told* not to run
  `uv sync` — instruction-as-mitigation, not a structural guardrail.

**Desired outcome:**
Running `uv sync` from a worktree is prevented **by construction** — a
PreToolUse hook blocks it with an actionable message pointing to the scoped,
additive alternative. A lane-exit health check verifies the shared `.venv`
still has its dev extras so any residual corruption can never pass silently.

## Freshness Check

**Baseline commit:** `6371180af1568ae5c7b1d34f156029542bbc1eba`
**Issue filed at:** 2026-07-13T05:00:04Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/worktree_manager.py:866` (`create_worktree`) — issue claims worktrees
  share the root `.venv` — **still holds**: the function runs only `git worktree
  add` + copies `.claude/settings.local.json`; no venv/`uv`/`UV_PROJECT_ENVIRONMENT` step.
- `.claude/settings.json:31` (`PreToolUse` `matcher: "Bash"` block) — issue
  claims a validator-hook precedent — **still holds**: five standalone Bash
  validators are wired there today.
- `.claude/hooks/validators/validate_no_inline_timeout.py` — **still holds** as
  the copy-from template for a Bash-command guard (reads stdin, inspects
  `tool_input.command`, blocks via `{"decision":"block","reason":...}` + exit 0).
- `.claude/hooks/validators/validate_file_contains.py:278` — confirms hook input
  carries a top-level `cwd` field (`payload.get("cwd")`) — **still holds**.
- `agent/worktree_manager.py:19` — `WORKTREES_DIR = ".worktrees"`; `.claude/worktrees/`
  also exists and is populated — **still holds**.
- `agent/worktree_manager.py:1370` (`cleanup_after_merge`) — the post-merge
  lane-exit path; the pinned call site for the warn-only health check (see
  Technical Approach). `remove_worktree` (line 991) is deliberately NOT used.

**Cited sibling issues/PRs re-checked:**
- #1925 (PydanticAI lane) — the recurrence source; not a blocker, historical evidence.
- #1927, #1968 — lanes that avoided the bug via instruction; historical context.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=2026-07-13T05:00:04Z -- agent/worktree_manager.py .claude/hooks/ .claude/settings.json` is empty).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Issue filed ~1h47m before plan time; recon added a `## Recon Summary`
to the issue body during Phase 0.

## Prior Art

- **No prior fix exists.** `gh issue list --state closed` / `gh pr list --state
  merged` for "uv sync / venv / worktree" surfaced only unrelated results
  (#881 stale hook comment; #1167 markitdown; #1224 embedding cleanup). No
  existing `uv sync` guard lives in the main tree (grep hits are only inside
  stale worktree copies).
- **Bash-validator precedent:** `validate_commit_message.py`,
  `validate_no_inline_timeout.py`, `validate_merge_guard.py`,
  `validate_no_raw_redis_delete.py`, `validate_design_system_sync.py` — the new
  guard mirrors this exact shape.

## Research

**Queries used:**
- `uv sync removes packages not in lockfile --inexact UV_PROJECT_ENVIRONMENT venv path`

**Key findings:**
- `uv sync` is **exact by default** and removes any package not in the lockfile
  from the active environment — this is precisely the stripping the issue
  reports. Source: https://docs.astral.sh/uv/concepts/projects/sync/
- `--inexact` retains extras, **but** uv issue #16231 shows `uv sync --inexact`
  can still delete and recreate `.venv` in some versions — so "just add
  `--inexact`" is not a safe fix. Source: https://github.com/astral-sh/uv/issues/16231
- The scoped alternative `uv pip install --python <repo>/.venv/bin/python
  "<pkg>==<ver>"` is additive and does not run project resolution, so it cannot
  strip the env — this is the message the guard should point to.
- `UV_PROJECT_ENVIRONMENT` sets the venv path (relative paths resolve against
  the workspace root) — the mechanism for the deferred per-worktree isolation
  (#2052). Source: https://docs.astral.sh/uv/concepts/projects/config/

## Data Flow

1. **Entry point:** A build/patch subagent (running with CWD under
   `.worktrees/{slug}/` or `.claude/worktrees/`) calls the `Bash` tool with a
   command containing `uv sync` (possibly `cd <worktree> && uv sync --frozen`).
2. **PreToolUse hook fires:** Claude Code invokes every `matcher: "Bash"`
   PreToolUse hook, passing JSON on stdin with `tool_name`, `tool_input.command`,
   and top-level `cwd`. Which `settings.json` supplies the hook list is governed
   by `$CLAUDE_PROJECT_DIR` (see Risk 3) — the guard must be present in the
   settings the running session actually loads for the hook to fire.
3. **Guard decision (`validate_no_uv_sync_in_worktree.py`):** parse the command
   for a `uv sync` invocation; determine the effective working directory from
   `cwd` **and** any `cd <path> &&` prefix in the command; if that directory is
   under `.worktrees/` or `.claude/worktrees/`, emit
   `{"decision":"block","reason": <actionable message>}` and exit 0. Otherwise
   exit 0 silently (allow).
4. **Output:** the subagent sees the block reason (scoped-install guidance) in
   place of the destructive `uv sync`; the shared `.venv` is untouched.
5. **Lane exit:** the venv-health check imports the dev extras against
   `<repo>/.venv`; missing modules surface a loud warning instead of a silent
   `ModuleNotFoundError` in a later lane.

## Architectural Impact

- **New dependencies:** none. The guard is stdlib-only (`json`, `re`, `sys`,
  `pathlib`), mirroring existing validators.
- **Interface changes:** one new hook entry in `.claude/settings.json` Bash
  matcher; one new small CLI (`python -m tools.venv_health`).
- **Coupling:** none added — the guard reads hook input; the health check reads
  its own repo-root `.venv`.
- **Reversibility:** trivial — delete the validator + its settings line + the
  health module.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — the guard and health check are stdlib-only and depend on no
external service.

## Solution

### Key Elements

- **uv-sync worktree guard** — a PreToolUse (Bash) validator that blocks
  `uv sync` / `uv sync --frozen` when the effective CWD is under a worktree.
- **Scoped-install guidance** — the block message names the exact additive
  alternative: `uv pip install --python <repo>/.venv/bin/python "<pkg>==<ver>"`.
- **venv-health check** — a tiny CLI that imports the shared `<repo>/.venv` dev
  extras and exits nonzero (listing what's missing) so lane-exit corruption is loud.
- **Escape hatch (decided):** a `# uv-sync-guard: allow` per-command marker (see
  Resolved Decisions) so a genuinely intentional worktree sync can opt out.

### Flow

Build subagent in a worktree → runs `uv sync` → **guard blocks with reason** →
subagent runs the scoped `uv pip install` instead → shared `.venv` intact →
lane exit (`cleanup_after_merge`) runs the health check → confirms
`pytest`/`ruff`/`xdist` importable in `<repo>/.venv` → lane completes.

### Technical Approach

- **Guard (`validate_no_uv_sync_in_worktree.py`)**:
  - Copy the structure of `validate_no_inline_timeout.py`: `read_stdin()`,
    `block(reason)`, a `_run_hook()` path and a `_run_cli()` path (for tests).
  - **CRITICAL — do NOT inherit the template's `"git commit"` pre-filter gate.**
    `validate_no_inline_timeout.py::_run_hook()` early-exits on any command that
    does not contain `"git commit"` (lines 211-212). Copying that gate verbatim
    would make this guard a silent no-op against `uv sync` (which is never a
    `git commit` command). The new `_run_hook()` keeps only the `tool_name ==
    "Bash"` guard and the `tool_input.command` extraction, then goes **straight
    to the `uv sync` token match** on `command` — there is no intermediate
    substring/subcommand gate before the `uv sync` detection.
  - Detect a `uv sync` invocation with a regex tolerant of flags and of a
    leading `uv run`-style prefix — match `uv` … `sync` as a subcommand token,
    not a bare substring (must not false-positive on `uv pip install` or a file
    literally named `uv sync`).
  - Determine the effective directory: start from `hook_input["cwd"]`; if the
    command contains a `cd <path> &&` (or `;`) prefix, resolve `<path>` against
    `cwd` and use that instead. A worktree match is: the resolved path contains
    a `.worktrees/` or `.claude/worktrees/` path component. Reuse the same
    normalization idea as `worktree_manager` (component match, not substring, so
    `.worktrees-backup` never matches).
  - Block message: name the exact scoped-install command and explain *why*
    (`uv sync` is exact-by-default and strips the shared `.venv` other lanes use).
  - **Fail-open on any parse error** — the guard must never crash a legitimate
    Bash call; an unparseable command exits 0 (allow), matching the other
    validators' fail-quiet posture.
- **Wiring:** add one `type: command` entry to the existing `matcher: "Bash"`
  PreToolUse array in `.claude/settings.json` (timeout 5), after
  `validate_no_raw_redis_delete.py`.
- **Health check (`tools/venv_health.py`)**:
  - `python -m tools.venv_health` verifies the dev extras (`pytest`, `ruff` via
    module import or `shutil.which`, `xdist`) are importable **in the shared
    `<repo>/.venv`** — not merely "the running interpreter," which is not
    guaranteed to be that venv (a lane could invoke the module under a different
    Python). The module therefore: (a) resolves `repo_root` — walk up from
    `__file__` to the directory containing `.venv`, or honor `AI_REPO_ROOT`;
    (b) computes the expected interpreter `repo_root / ".venv/bin/python"`;
    (c) compares it to `sys.executable` via `Path.resolve()`. If they match, it
    probe-imports in-process; if not, it dispatches the probe against
    `<repo>/.venv/bin/python` as a subprocess
    (`<repo>/.venv/bin/python -c "import pytest, xdist"`) so the exit code is a
    statement about the **shared** `.venv` specifically, not whatever interpreter
    happened to launch the module. Prints the missing set; exits 1 if any extra
    is absent, 0 otherwise. Stdlib + already-installed deps only.
  - Wire a **fail-quiet, warn-only** call at **two** points, both logging a
    `logger.warning` on a missing extra and never raising:
    1. **`cleanup_after_merge` (`agent/worktree_manager.py:1370`)** — the
       post-merge lane-exit path. Pin the call HERE, **not** in `remove_worktree`
       (line 991): `remove_worktree` also fires on kill/abandon and on the
       `("blocked", session_id)` in-use path, so wiring there would emit health
       noise on every session teardown. `cleanup_after_merge` fires once, on the
       terminal merge path, which is the meaningful "lane finished, verify the
       shared venv survived" moment. Add the check near the end of
       `cleanup_after_merge` (after the worktree/branch removal steps), recording
       nothing into the result dict beyond an optional log line.
    2. **SDLC stage transition after BUILD/PATCH** — a worktree with `Resume:
       true` is *retained* (not cleaned up) across the BUILD→TEST / PATCH→REVIEW
       handoff, so `cleanup_after_merge` has not run yet. Fire the same warn-only
       probe at that transition so a stripped shared `.venv` is caught **before**
       the next stage's test/lint gate fails with a bare `ModuleNotFoundError`.
       Locate the concrete transition seam during build (the stage-marker
       `completed` write for BUILD/PATCH in the SDLC substrate — `sdlc-tool
       stage-marker ... --status completed`); if no single clean seam exists,
       fall back to invoking `valor-venv-health` from the do-test / do-patch
       skill body at stage entry. Warn-only in both places.
  - Expose it as a `pyproject.toml [project.scripts]` entry
    (`valor-venv-health`) so a lane/skill can invoke it directly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The guard's command-parse path is wrapped so any exception → exit 0 (allow);
  a unit test feeds malformed JSON / a bizarre command and asserts exit 0 (no crash, no block).
- [ ] `venv_health` import probing catches `ImportError` per-module and records it
  (does not abort on the first missing module); test asserts all missing modules are reported.

### Empty/Invalid Input Handling
- [ ] Guard with empty stdin / missing `command` / missing `cwd` → exit 0 (allow). Unit test.
- [ ] Guard with `uv sync` but `cwd` at repo root (not a worktree) → exit 0 (allow). Unit test.

### Error State Rendering
- [ ] Block message is asserted to contain the scoped-install command and the
  worktree path, so the subagent sees actionable guidance (not a bare "blocked").
- [ ] `venv_health` warn-only wiring: test that a simulated missing extra logs a
  warning and `cleanup_after_merge` still completes (no raise).

## Test Impact

No existing tests affected — this work is purely additive: a new validator file,
a new `tools/venv_health.py` module, one new line in `.claude/settings.json`, and
a warn-only call in `cleanup_after_merge`. No existing test asserts behavior for
`uv sync` interception or venv health, so nothing needs UPDATE/DELETE/REPLACE.

## Rabbit Holes

- **Per-worktree venv isolation** — the robust fix (option 2) is real work
  (disk cost, provisioning time, `UV_PROJECT_ENVIRONMENT` plumbing). It is
  filed as #2052 and explicitly out of scope here. Do not start it.
- **Parsing arbitrary shell** — do not attempt a full shell parser for the
  `cd` prefix. Handle the common `cd <path> && ...` / `cd <path>; ...` shapes
  and otherwise fall back to `hook_input.cwd`. An occasional missed exotic
  chain is acceptable; the guard is defense-in-depth, not a security boundary.
- **Blocking all `uv` commands** — only `uv sync` strips the env. Do NOT block
  `uv pip install`, `uv run`, `uv lock`, etc. Over-blocking breaks legitimate
  scoped installs, which is the very alternative we point people to.

## Risks

### Risk 1: False positive on a legitimate `uv sync` from a worktree
**Impact:** A subagent that genuinely needs a full sync inside an isolated
worktree (post-#2052) is blocked.
**Mitigation:** Small blast radius today (worktrees are NOT isolated, so a full
sync there is always wrong). The `# uv-sync-guard: allow` escape-hatch marker
(mirroring `# timeout-guard: allow`; adopted per Resolved Decisions) covers the
rare intentional case. Revisit the guard's block-vs-warn posture when #2052 lands.

### Risk 2: `cd`-prefix detection misses an exotic command chain
**Impact:** A `uv sync` reached via an unusual chain (subshell, env var cwd)
slips past the guard.
**Mitigation:** The top-level `hook_input.cwd` is the primary signal and covers
the overwhelmingly common case (subagent CWD is the worktree). The `cd`-prefix
parse is a secondary catch. The lane-exit health check is the backstop that
makes any miss loud rather than silent.

### Risk 3: The hook never fires because the running session loaded a different `settings.json`
**Impact:** PreToolUse hooks come from the `settings.json` that Claude Code
resolves via `$CLAUDE_PROJECT_DIR`. A subagent running with CWD inside a
worktree can, depending on how the session was launched, load a **stale
`.claude/settings.json` copy** carried on the worktree's branch (one that
predates this guard) — or resolve `$CLAUDE_PROJECT_DIR` to the worktree rather
than the main checkout — in which case the new hook entry is simply absent and
`uv sync` is never intercepted. This is the exact failure mode that makes
"the guard is wired in main's settings.json" insufficient on its own.
**Mitigation:** The build MUST include a **real-dispatch integration test** that
exercises the *actual hook dispatch* (not just the validator in isolation):
create a worktree via `create_worktree()`, issue a `Bash` tool call whose `cwd`
is inside that worktree, and assert the guard fires and blocks (see Step 3 and
Agent Integration). The lane-exit health check is the second backstop: if the
hook did not fire and `uv sync` stripped the env, `cleanup_after_merge` /
the BUILD/PATCH-transition probe surfaces it loudly. Document the
`$CLAUDE_PROJECT_DIR` / stale-`settings.json` hazard in the feature doc so
operators know that adding the hook to main is necessary but relies on sessions
resolving `$CLAUDE_PROJECT_DIR` to the main checkout.

## Race Conditions

No race conditions identified — the guard is a synchronous, single-invocation
PreToolUse hook that reads stdin and exits; it holds no shared state. The
health check is a read-only import probe. The underlying corruption the issue
describes is itself a cross-lane hazard, but this plan's mitigations (block +
warn) introduce no new concurrent state of their own.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2052] Per-worktree venv isolation via `UV_PROJECT_ENVIRONMENT`
  / per-slug venv provisioning in `create_worktree`. Heavier (disk + provisioning
  cost); filed as #2052. This plan ships only the guard + health check.

## Update System

No update-system changes required beyond the standard propagation that already
covers `.claude/` and `pyproject.toml`:
- The new validator under `.claude/hooks/validators/` and the `.claude/settings.json`
  edit propagate to every machine through the existing `/update` `.claude/` sync —
  no new wiring in `scripts/update/` needed.
- The `valor-venv-health` `[project.scripts]` entry is installed by the normal
  `uv sync` / editable-install step on each machine (the same step that installs
  all other `valor-*` CLIs). No migration and no new dependency to propagate.

## Agent Integration

- The guard needs **no** MCP/`.mcp.json` surface — it is a PreToolUse hook that
  intercepts the agent's existing `Bash` tool calls.
- `tools/venv_health.py` is reachable via a `pyproject.toml [project.scripts]`
  entry (`valor-venv-health = "tools.venv_health:main"`) so a lane/skill can
  invoke it through the Bash tool, and via a direct import from
  `agent/worktree_manager.py` (`cleanup_after_merge`) at lane exit.
- **Real-dispatch integration test (required, per Risk 3):** create a worktree
  via `create_worktree()`, then drive a `Bash` tool call whose `cwd` is inside
  that worktree **through the actual hook dispatch** (not just the validator
  invoked directly), and assert `uv sync` is blocked while `uv pip install` and
  a root-cwd `uv sync` are allowed. This proves the hook actually fires from a
  worktree CWD — the failure mode a validator-only unit test cannot catch.
- No `bridge/telegram_bridge.py` change required.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/uv-sync-worktree-guard.md` describing the guard
  (what it blocks, the actionable message, the `# uv-sync-guard: allow` escape
  hatch) and the venv-health check.
- [ ] In that doc, add a **hook-resolution note**: hook firing depends on which
  `settings.json` the session loads (governed by `$CLAUDE_PROJECT_DIR`); a stale
  `.claude/settings.json` carried on a worktree branch can shadow the guard, so
  the lane-exit health check is the required backstop (cross-reference Risk 3).
- [ ] (Optional polish) The doc MAY note the human-facing convention "prefer
  scoped `uv pip install` over `uv sync` from a worktree" — but the guard is the
  structural fix; do NOT re-add that instruction to `builder.md` / `dev.md` /
  `CLAUDE.md` as a mitigation, since instruction-as-mitigation is exactly what
  this plan replaces.
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Module docstring on `validate_no_uv_sync_in_worktree.py` explaining the
  hook protocol, the worktree-detection logic, and the allowlist marker.
- [ ] Docstring on `tools/venv_health.py` listing the checked extras and the
  shared-`.venv` resolution logic.

## Success Criteria

- [ ] `uv sync` / `uv sync --frozen` from a worktree CWD is blocked by the
  PreToolUse guard with an actionable, scoped-install message — not by prompt
  instruction alone.
- [ ] The same command from the repo root (non-worktree) is allowed (no false positive).
- [ ] `uv pip install ...` from a worktree is allowed (only `uv sync` is blocked).
- [ ] A real-dispatch integration test confirms the hook fires from a worktree
  CWD created via `create_worktree()` (not just the validator in isolation).
- [ ] `python -m tools.venv_health` exits 0 on a healthy shared `<repo>/.venv`
  and 1 (listing missing extras) on a stripped one, probing the repo `.venv`
  even when launched under a different interpreter.
- [ ] `cleanup_after_merge` runs the health check warn-only (logs on missing
  extra, never raises); the BUILD/PATCH transition also fires the warn-only probe.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `.claude/settings.json` references `validate_no_uv_sync_in_worktree.py`.

## Team Orchestration

The lead agent orchestrates; it deploys the members below and coordinates.

### Team Members

- **Builder (guard)**
  - Name: `guard-builder`
  - Role: Implement `validate_no_uv_sync_in_worktree.py` + wire it into `.claude/settings.json`.
  - Agent Type: builder
  - Domain: security/untrusted-input (hook that inspects agent-issued commands — fail-open, no over-block)
  - Resume: true

- **Builder (health-check)**
  - Name: `health-builder`
  - Role: Implement `tools/venv_health.py` (shared-`.venv` resolution), its
    `[project.scripts]` entry, the warn-only `cleanup_after_merge` call, and the
    BUILD/PATCH-transition warn-only probe.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `guard-tester`
  - Role: Unit tests for the guard (block/allow/fail-open) and health check;
    the real-dispatch integration test driving the hook from a worktree CWD.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `guard-docs`
  - Role: Feature doc (incl. hook-resolution note) + README index.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `guard-validator`
  - Role: Verify all success criteria and Verification rows.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement the uv-sync worktree guard
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/hooks/test_validate_no_uv_sync_in_worktree.py (create)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_no_uv_sync_in_worktree.py` mirroring
  `validate_no_inline_timeout.py` (read stdin, `_run_hook()` + `_run_cli()`, `block()`).
- **Do NOT copy the template's `"git commit"` pre-filter gate** — `_run_hook()`
  keeps only the `tool_name == "Bash"` check + command extraction, then matches
  `uv sync` directly (see Technical Approach BLOCKER note).
- Detect a `uv sync` subcommand (tolerant of flags; not `uv pip install`); resolve
  effective dir from `cwd` + optional `cd <path> &&` prefix; block only when under
  `.worktrees/` or `.claude/worktrees/` (path-component match, not substring).
- Block message names the scoped `uv pip install --python <repo>/.venv/bin/python
  "<pkg>==<ver>"` alternative and why `uv sync` is destructive. Support the
  `# uv-sync-guard: allow` escape-hatch marker. Fail open on any parse error.
- Add the hook entry to the `matcher: "Bash"` PreToolUse array in `.claude/settings.json`.

### 2. Implement venv-health check + wiring
- **Task ID**: build-health
- **Depends On**: none
- **Validates**: tests/unit/test_venv_health.py (create)
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/venv_health.py` (`main()`): resolve `repo_root`, compute
  `repo_root / ".venv/bin/python"`, compare to `sys.executable`; probe-import
  `pytest`, `ruff`, `xdist` in-process when they match, else dispatch the probe
  against `<repo>/.venv/bin/python` via subprocess; print missing set; exit 1 if
  any missing else 0.
- Add `valor-venv-health = "tools.venv_health:main"` to `pyproject.toml [project.scripts]`.
- Add a warn-only call in `cleanup_after_merge` (`agent/worktree_manager.py:1370`),
  near the end, after worktree/branch removal (log `logger.warning` on missing
  extras; never raise). Do NOT wire it into `remove_worktree`.
- Add the second warn-only probe at the BUILD/PATCH stage-transition seam (or, if
  no clean seam, `valor-venv-health` invoked from the do-test / do-patch skill body).

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-guard, build-health
- **Validates**: tests/unit/hooks/test_validate_no_uv_sync_in_worktree.py, tests/unit/test_venv_health.py
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Guard unit: block on `uv sync` under a worktree cwd; block on `cd .worktrees/x && uv sync --frozen`;
  allow from repo root; allow `uv pip install` from a worktree; allow with the escape marker;
  fail-open (exit 0) on malformed input.
- **Real-dispatch integration test (per Risk 3):** create a worktree via
  `create_worktree()`, drive a `Bash` tool call with `cwd` inside the worktree
  through the actual hook dispatch, assert `uv sync` is blocked there while a
  root-cwd `uv sync` and a worktree `uv pip install` are allowed.
- Health: exit 0 when extras importable in `<repo>/.venv`; exit 1 + names missing
  when one is absent; probes the repo `.venv` even under a different interpreter.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-guard, build-health
- **Assigned To**: guard-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/uv-sync-worktree-guard.md` (incl. the `$CLAUDE_PROJECT_DIR`
  / stale-`settings.json` hook-resolution note); add the README index entry.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; confirm every Success Criterion; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/hooks/test_validate_no_uv_sync_in_worktree.py tests/unit/test_venv_health.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/validators/validate_no_uv_sync_in_worktree.py tools/venv_health.py` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/validators/validate_no_uv_sync_in_worktree.py tools/venv_health.py` | exit code 0 |
| Guard wired | `grep -c "validate_no_uv_sync_in_worktree.py" .claude/settings.json` | output > 0 |
| Guard blocks worktree sync | `printf '%s' '{"tool_name":"Bash","cwd":"/x/.worktrees/s","tool_input":{"command":"uv sync --frozen"}}' \| python .claude/hooks/validators/validate_no_uv_sync_in_worktree.py` | output contains block |
| Guard allows root sync | `printf '%s' '{"tool_name":"Bash","cwd":"/x","tool_input":{"command":"uv sync"}}' \| python .claude/hooks/validators/validate_no_uv_sync_in_worktree.py` | output does not contain block |
| Guard does not block uv pip install | `printf '%s' '{"tool_name":"Bash","cwd":"/x/.worktrees/s","tool_input":{"command":"uv pip install foo"}}' \| python .claude/hooks/validators/validate_no_uv_sync_in_worktree.py` | output does not contain block |
| Health CLI present | `python -m tools.venv_health; echo done` | output contains done |

## Resolved Decisions

Answers folded in from critique (Open Questions closed before build):

1. **Block vs. warn posture — DECIDED: hard block.** A full `uv sync` from a
   shared-`.venv` worktree is always wrong today (worktrees are not isolated).
   The guard blocks; block-vs-warn is revisited only when #2052 lands.
2. **Health-check extras list — DECIDED: `pytest, ruff, xdist`.** The minimal,
   fast set that gates the test/lint pipeline. `pandas` / `mypy` are heavier and
   not required to detect the stripping the issue reports; excluded to keep the
   probe fast.
3. **Escape-hatch marker — DECIDED: adopt `# uv-sync-guard: allow`.** This
   resolves the former Open Question that contradicted the already-committed
   build/test tasks (which implement and test the marker). Mirrors
   `# timeout-guard: allow`; gives the rare intentional worktree sync an opt-out
   without weakening the default block.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | plan-critique | Guard must not inherit template's `"git commit"` pre-filter gate (silent no-op) | Technical Approach + Step 1 | `_run_hook()` matches `uv sync` directly; no `git commit` gate |
| CONCERN | plan-critique | Hook-firing from worktree CWD unverified | Risk 3 + Agent Integration + Step 3 | Real-dispatch integration test via `create_worktree()`; `$CLAUDE_PROJECT_DIR` doc note |
| CONCERN | plan-critique | Health-check call site under-specified | Technical Approach + Step 2 | Pinned to `cleanup_after_merge:1370`, not `remove_worktree`; + BUILD/PATCH-transition probe |
| CONCERN | plan-critique | `venv_health` probes running interpreter, not `<repo>/.venv` | Technical Approach + Step 2 | Resolve `repo_root`, compare `sys.executable` to `.venv/bin/python`, subprocess-dispatch otherwise |
| CONCERN | plan-critique | OQ#3 escape-hatch contradicted committed tasks | Resolved Decisions #3 | Adopted the marker; Open Question deleted |
| NIT | plan-critique | Convention prose re-adds instruction-as-mitigation | Documentation (optional polish) | Removed builder.md/dev.md/CLAUDE.md convention tasks; optional note in feature doc only |
