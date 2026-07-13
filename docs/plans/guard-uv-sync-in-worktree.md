---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2050
last_comment_id:
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
   and top-level `cwd`.
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
- **venv-health check** — a tiny CLI that imports the shared `.venv` dev extras
  and exits nonzero (listing what's missing) so lane-exit corruption is loud.
- **Convention note** — builder/dev agent briefs + CLAUDE.md state "scoped
  `uv pip install` only, never `uv sync`, from a worktree."

### Flow

Build subagent in a worktree → runs `uv sync` → **guard blocks with reason** →
subagent runs the scoped `uv pip install` instead → shared `.venv` intact →
lane exit runs `python -m tools.venv_health` → confirms `pytest`/`ruff`/`xdist`
importable → lane completes.

### Technical Approach

- **Guard (`validate_no_uv_sync_in_worktree.py`)**:
  - Copy the structure of `validate_no_inline_timeout.py`: `read_stdin()`,
    `block(reason)`, a `_run_hook()` path and a `_run_cli()` path (for tests).
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
  - `python -m tools.venv_health` imports `pytest`, `ruff` (module import or
    `shutil.which`), `xdist` (`import xdist`) against the running interpreter's
    environment; prints the missing set and exits 1 if any are absent, exits 0
    otherwise. Stdlib + already-installed deps only.
  - Wire a **fail-quiet, warn-only** call into the worktree cleanup path in
    `agent/worktree_manager.py` (lane exit) — a missing-extra logs a
    `logger.warning`, never raises. Also expose it as a `pyproject.toml`
    `[project.scripts]` entry (`valor-venv-health`) so a lane/skill can invoke it.
- **Convention:** add the one-line rule to `.claude/agents/builder.md` and
  `.claude/agents/dev.md`, and a short note under the worktree section of
  `CLAUDE.md` / the relevant doc.

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
  warning and the cleanup path still completes (no raise).

## Test Impact

No existing tests affected — this work is purely additive: a new validator file,
a new `tools/venv_health.py` module, one new line in `.claude/settings.json`, and
a warn-only call in worktree cleanup. No existing test asserts behavior for
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
sync there is always wrong). Provide an allowlist escape hatch marker comment
(mirroring `# timeout-guard: allow`) — e.g. a `# uv-sync-guard: allow` token in
the command — for the rare intentional case. Revisit the guard's block-vs-warn
posture when #2052 lands.

### Risk 2: `cd`-prefix detection misses an exotic command chain
**Impact:** A `uv sync` reached via an unusual chain (subshell, env var cwd)
slips past the guard.
**Mitigation:** The top-level `hook_input.cwd` is the primary signal and covers
the overwhelmingly common case (subagent CWD is the worktree). The `cd`-prefix
parse is a secondary catch. The lane-exit health check is the backstop that
makes any miss loud rather than silent.

## Race Conditions

No race conditions identified — the guard is a synchronous, single-invocation
PreToolUse hook that reads stdin and exits; it holds no shared state. The
health check is a read-only import probe. The underlying corruption the issue
describes is itself a cross-lane hazard, but this plan's mitigations (block +
warn) introduce no new concurrent state of their own.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2052] Per-worktree venv isolation via `UV_PROJECT_ENVIRONMENT`
  / per-slug venv provisioning in `create_worktree`. Heavier (disk + provisioning
  cost); filed as #2052. This plan ships only the guard + health check + convention.

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
  `agent/worktree_manager.py` at lane exit.
- Integration test: drive the validator end-to-end through the hook protocol
  (stdin JSON → block/allow) to confirm the agent's `uv sync` is actually
  intercepted, and that `valor-venv-health` runs from the CLI.
- No `bridge/telegram_bridge.py` change required.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/uv-sync-worktree-guard.md` describing the guard
  (what it blocks, the actionable message, the allowlist escape hatch) and the
  venv-health check.
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Module docstring on `validate_no_uv_sync_in_worktree.py` explaining the
  hook protocol, the worktree-detection logic, and the allowlist marker.
- [ ] Docstring on `tools/venv_health.py` listing the checked extras.
- [ ] One-line convention note in `.claude/agents/builder.md`, `.claude/agents/dev.md`,
  and the worktree section of `CLAUDE.md`: scoped `uv pip install` only, never
  `uv sync`, from a worktree.

## Success Criteria

- [ ] `uv sync` / `uv sync --frozen` from a worktree CWD is blocked by the
  PreToolUse guard with an actionable, scoped-install message — not by prompt
  instruction alone.
- [ ] The same command from the repo root (non-worktree) is allowed (no false positive).
- [ ] `uv pip install ...` from a worktree is allowed (only `uv sync` is blocked).
- [ ] `python -m tools.venv_health` exits 0 on a healthy `.venv` and 1 (listing
  missing extras) on a stripped one.
- [ ] Lane exit in `worktree_manager` runs the health check warn-only (logs on
  missing extra, never raises).
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

- **Builder (health-check + convention)**
  - Name: `health-builder`
  - Role: Implement `tools/venv_health.py`, its `[project.scripts]` entry, the
    warn-only lane-exit call in `worktree_manager`, and the convention notes.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `guard-tester`
  - Role: Unit tests for the guard (block/allow/fail-open) and health check;
    an integration test driving the hook protocol end-to-end.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `guard-docs`
  - Role: Feature doc + README index + convention notes.
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
- Detect a `uv sync` subcommand (tolerant of flags; not `uv pip install`); resolve
  effective dir from `cwd` + optional `cd <path> &&` prefix; block only when under
  `.worktrees/` or `.claude/worktrees/` (path-component match, not substring).
- Block message names the scoped `uv pip install --python <repo>/.venv/bin/python
  "<pkg>==<ver>"` alternative and why `uv sync` is destructive. Support a
  `# uv-sync-guard: allow` escape-hatch marker. Fail open on any parse error.
- Add the hook entry to the `matcher: "Bash"` PreToolUse array in `.claude/settings.json`.

### 2. Implement venv-health check + convention
- **Task ID**: build-health
- **Depends On**: none
- **Validates**: tests/unit/test_venv_health.py (create)
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/venv_health.py` (`main()`): probe-import `pytest`, `ruff`,
  `xdist`; print missing set; exit 1 if any missing else 0.
- Add `valor-venv-health = "tools.venv_health:main"` to `pyproject.toml [project.scripts]`.
- Add a warn-only call at the worktree cleanup lane-exit in `agent/worktree_manager.py`
  (log `logger.warning` on missing extras; never raise).
- Add the one-line "scoped `uv pip install` only, never `uv sync`, from a worktree"
  convention to `.claude/agents/builder.md`, `.claude/agents/dev.md`, and the
  CLAUDE.md worktree note.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-guard, build-health
- **Validates**: tests/unit/hooks/test_validate_no_uv_sync_in_worktree.py, tests/unit/test_venv_health.py
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Guard: block on `uv sync` under a worktree cwd; block on `cd .worktrees/x && uv sync --frozen`;
  allow from repo root; allow `uv pip install` from a worktree; allow with the escape marker;
  fail-open (exit 0) on malformed input.
- Health: exit 0 when extras importable; exit 1 + names missing when one is absent.
- Integration: drive the guard through the hook stdin/stdout protocol end-to-end.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-guard, build-health
- **Assigned To**: guard-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/uv-sync-worktree-guard.md`; add the README index entry.
- Verify the convention notes landed in the agent briefs + CLAUDE.md.

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
| Convention note present | `grep -rc "never .*uv sync.*from a worktree" .claude/agents/builder.md .claude/agents/dev.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Block vs. warn posture:** Ship the guard as a hard **block** (recommended —
   a full `uv sync` from a shared-venv worktree is always wrong today), or as a
   **warn-only** notice? Block is the minimum-bar per the issue; confirm.
2. **Health-check extras list:** Is `pytest, ruff, xdist` the right minimal set
   to probe, or should it also cover `pandas` / `mypy` (named in the issue) —
   accepting that those are heavier and slower to import?
3. **Escape-hatch marker:** Adopt `# uv-sync-guard: allow` as the per-command
   opt-out (mirroring `# timeout-guard: allow`), or omit the escape hatch
   entirely until #2052 makes an intentional worktree sync meaningful?
