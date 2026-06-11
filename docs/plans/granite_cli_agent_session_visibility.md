---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1571
last_comment_id:
---

# Granite CLI session visibility (dashboard, valor-session list, watchdog)

## Problem

`valor-granite-loop` (the standalone CLI entry point for the granite interactive
TUI, `tools.granite_interactive_tui_poc.cli:main`) drives a real interactive
Claude Code session through a PTY but never creates an `AgentSession` record.
The container mints its own ephemeral `session_id` internally
(`uuid.uuid4().hex[:12]`) and never persists it to Popoto.

**Current behavior:**
- A granite run started via `valor-granite-loop` is invisible to
  `curl localhost:8500/dashboard.json` (sessions list).
- It is invisible to `python -m tools.valor_session list`.
- The cross-process orphan reaper (#1271) sees the real `claude` PTY processes
  the run spawns but has no AgentSession + no heartbeat to gate them with — so
  those PTYs are neither protected (could be reaped mid-run) nor tracked.

**Desired outcome:**
A granite run started via the CLI creates an `AgentSession` record at startup
(status `running`), so it shows up in the dashboard and `valor-session list`,
and is finalized (`completed` / `failed`) when the run exits. Visibility and
watchdog coverage match the PM/Dev session paths.

## Freshness Check

**Baseline commit:** 6985a58c
**Issue filed at:** 2026-06-05T06:35:52Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/granite_interactive_tui_poc/cli.py:95-103` — CLI constructs `Container`
  directly and calls `container.run()` with no AgentSession — still holds.
- `agent/granite_container/container.py:559` — `session_id = uuid.uuid4().hex[:12]`
  is minted inside `ContainerResult`, never persisted — still holds.
- `ui/app.py:402` — dashboard serializes any `session_type` with no filter —
  still holds (a persisted granite session would appear automatically).
- `tools/valor_session.py:383` — `_role_to_session_type` maps pm/dev/teammate
  only; no granite entry — still holds.
- `agent/session_health.py:53` — `_CLAUDE_CMDLINE_RE` matches the bundled claude
  binary and gates by worker heartbeat — still holds.

**Cited sibling issues/PRs re-checked:**
- #1546 — PoC issue — closed 2026-06-05.
- #1271 — cross-process orphan reaper — referenced via code, still live.
- #1612 / #1572 — granite **production cutover** — merged 2026-06-11, AFTER this
  issue was filed.

**Commits on main since issue was filed (touching referenced files):**
- `09313109` Granite PTY production cutover (#1612) — **changed the landscape**:
  the production/bridge path now runs through `BridgeAdapter`, which is HANDED an
  existing `AgentSession` (the worker creates it) — `agent/granite_container/bridge_adapter.py:131`.
  The bridge/worker path therefore already has session coverage. The remaining
  gap is the **standalone `valor-granite-loop` CLI path only**.
- `00282b5e` PoC #1546 (#1570) — introduced the CLI.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Drift is narrowing, not invalidating — the issue's fix premise still
holds, but scope is now explicitly the CLI path. The production path is out of
scope (already covered).

## Prior Art

- **#1546 / PR #1570**: PoC — granite operator drives a real interactive Claude
  Code session via PTY. Introduced `valor-granite-loop`. Deliberately narrow:
  "does not wire to the bridge, does not dispatch child sessions" — and, as this
  issue documents, does not create an AgentSession. Outcome: shipped; this gap
  was a known consequence of the narrow scope.
- **#1572 / PR #1612**: Granite PTY production cutover + bounded slot pool. The
  worker now executes granite sessions via `BridgeAdapter`, which receives an
  already-created `AgentSession`. Outcome: shipped; closes the visibility gap for
  the production path but NOT the standalone CLI path.

No prior failed attempts to fix the CLI-path gap — this is the first fix.

## Data Flow

1. **Entry point**: operator runs `valor-granite-loop --user-message "..."`.
2. **CLI** (`tools/granite_interactive_tui_poc/cli.py:main`): parses args,
   constructs `Container(user_message=..., cwd=..., max_turns=...)`.
   **(NEW)** create an `AgentSession` (status `running`) here, before `run()`.
3. **Container.run()** (`agent/granite_container/container.py:552`): spawns PM/Dev
   `claude` PTYs via PTYPool, loops turns, returns a `ContainerResult` with its
   internal `session_id`, `exit_reason`, and byte/parse stats.
4. **CLI** (post-run): **(NEW)** finalize the AgentSession — `completed` on a
   clean `exit_reason` (`pm_complete`/`pm_user`), `failed` otherwise; on an
   exception in `container.run()`, finalize `failed` in the `except` block.
   Then write the results JSON and print the one-line summary (unchanged).
5. **Output**: results JSON file + stdout summary (unchanged); the AgentSession
   record is now visible to dashboard / `valor-session list` / watchdog.

## Architectural Impact

- **New dependencies**: CLI imports `AgentSession` (`models.agent_session`) and
  `finalize_session` (`models.session_lifecycle`). No new external deps.
- **Interface changes**: none to `Container` — the wiring lives entirely in the
  CLI wrapper. The container keeps minting its internal `session_id`; the CLI
  reuses that value as the AgentSession `session_id` (see Technical Approach for
  the ordering wrinkle).
- **Coupling**: small increase — the CLI now touches the session model. This is
  the same coupling the PM/Dev/bridge paths already have; it is the point of the
  fix.
- **Data ownership**: the CLI now owns one AgentSession record per run.
- **Reversibility**: trivial — revert the create/finalize calls.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto backend) | `python -c "from models.agent_session import AgentSession; AgentSession.query.all()"` | AgentSession persistence |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_cli_agent_session_visibility.md`

## Solution

### Key Elements

- **AgentSession creation in the CLI**: before `container.run()`, create a
  persisted session via the `create_local` factory with a `granite`-flavored
  session type, status `running`.
- **Terminal finalization in the CLI**: after `run()` (and in the exception
  handler), call `finalize_session(...)` mapping `exit_reason` to `completed` /
  `failed`.
- **`granite` session type**: add a `GRANITE = "granite"` member to the
  `SessionType` StrEnum so granite runs are a distinct, self-describing type
  rather than mislabeled as `dev` — and wire it into the one place that needs an
  explicit map (`valor_session list`'s `_role_to_session_type`).

### Flow

Operator runs `valor-granite-loop` → CLI creates AgentSession (running) →
Container.run() drives PTYs → CLI finalizes session (completed/failed) → results
JSON written → run visible in dashboard / `valor-session list` for its lifetime
and after.

### Technical Approach

- **Session id ordering wrinkle.** `Container.run()` mints the `session_id`
  internally and only returns it inside `ContainerResult`. The AgentSession must
  exist *before* `run()` so the run is visible *during* execution (the whole
  point — orphan-reaper coverage and live dashboard). Resolution: the CLI mints
  the `session_id` (same `uuid.uuid4().hex[:12]` shape) and creates the
  AgentSession with it before calling `run()`. The container's internally-minted
  id is logged/returned as before; the CLI does NOT need to thread its id into
  the container for this fix (the container's id is an internal trace artifact;
  the AgentSession is the canonical visibility record). The CLI's one-line stdout
  summary keeps printing `result.session_id` (container's) for backward compat
  and additionally prints the `agent_session_id`.
- **session type.** Add `SessionType.GRANITE = "granite"` in `config/enums.py`.
  Use it via `create_local(session_type=SessionType.GRANITE, ...)`. Confirm no
  exhaustive `match`/branch on `SessionType` elsewhere breaks on a new member —
  recon found the switch sites (`agent/hooks/pre_tool_use.py`,
  `agent/session_executor.py`) all use `== PM` / `== TEAMMATE` / `== DEV`
  equality checks with sensible defaults, so a new member falls through safely.
- **project_key / working_dir.** Use `project_key="valor"` (this repo) and
  `working_dir` = the container's resolved `cwd` (or the repo root when `--cwd`
  is the default sandbox). These satisfy `create_local`'s required fields and the
  dashboard's `project_name` resolution.
- **finalize mapping.** `pm_complete` / `pm_user` → `completed`; everything else
  (`pm_max_turns`, `dev_hang`, `pm_hang`, `startup_unresolved`, `exception`) →
  `failed`, with the `exit_reason` (or exception repr) as the `reason` string.
- **failure isolation.** Wrap the AgentSession create and finalize in
  best-effort guards: a Redis/Popoto failure must NOT change the CLI's exit code
  or prevent the results JSON from being written — the visibility record is
  additive, not load-bearing for the PoC's primary output. Log a warning on
  failure and continue.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The CLI's existing `except Exception` around `container.run()` (cli.py:107)
      must finalize the AgentSession `failed` before returning exit code 4 — add a
      test asserting the session reaches `failed` when `run()` raises.
- [ ] The new AgentSession create/finalize guards swallow Popoto/Redis errors;
      each must log a `logger.warning` — assert the warning fires (and the CLI
      still returns the correct exit code) when session persistence is patched to
      raise.

### Empty/Invalid Input Handling
- [ ] Empty `--user-message` returns 5 *before* any AgentSession is created —
      assert no session record is written in that path (unchanged early return).

### Error State Rendering
- [ ] On a `failed` exit (`dev_hang`, exception), assert the session status is
      `failed` and the CLI still writes results JSON + prints the summary.

## Test Impact

- [ ] `tests/unit/granite_container/test_cli.py` — UPDATE: the suite patches
      `Container.run` and asserts exit codes / JSON shape. Add assertions that
      (a) a `running` AgentSession is created before `run()`, (b) it is finalized
      `completed` for `pm_complete`/`pm_user` and `failed` otherwise, and (c) the
      `granite` session type is used. Patch the session model layer so these unit
      tests do not require a live Redis (or mark the new cases `integration` if
      they do). The existing exit-code/JSON assertions stay green unchanged.
- [ ] `tests/unit/test_session_executor_granite.py` — REVIEW (likely no change):
      covers the production/executor path, not the CLI. Confirm a new
      `SessionType.GRANITE` member does not break any exhaustive assertion there.

## Rabbit Holes

- **Threading the CLI-minted id into `Container`.** Do not refactor `Container`
  to accept an injected `session_id` for this fix — the AgentSession is the
  canonical record; the container's internal id is a trace artifact. Reconciling
  the two ids is scope creep.
- **Adding granite to the bridge/worker path.** Already handled by #1612 — out of
  scope.
- **Heartbeat plumbing for the CLI run.** The orphan reaper gates by worker
  heartbeat; the standalone CLI is not the worker. Adding a heartbeat writer to
  the CLI is a larger change — creating the AgentSession (so the PTYs are at
  least *visible* and the record is finalized on exit) is the appetite-sized fix.
  Full reaper self-protection for CLI-spawned PTYs is a separate concern.

## Risks

### Risk 1: New `SessionType.GRANITE` breaks an exhaustive consumer
**Impact:** A `match` statement or dict-lookup keyed on session type with no
default could raise on the new member.
**Mitigation:** Recon found all switch sites use equality checks with defaults.
The build task greps for `SessionType` / `session_type ==` consumers and confirms
each handles an unknown member gracefully before shipping.

### Risk 2: Session persistence failure degrades the PoC's primary output
**Impact:** If AgentSession create/finalize raised unguarded, a Redis outage
would break `valor-granite-loop` entirely.
**Mitigation:** Best-effort guards around create/finalize; failures log a warning
and never alter the exit code or block the results JSON write.

## Race Conditions

No race conditions identified — the CLI is a single synchronous process. The
AgentSession is created before `run()` and finalized after it returns; there is
no concurrent writer to this record (the standalone CLI is not the worker, and
each run mints a fresh `session_id`).

## No-Gos (Out of Scope)

- [ORDERED] Bridge/worker granite path session coverage — already shipped in
  #1612 (production cutover); blocked by nothing, simply already done.
- Heartbeat-based orphan-reaper self-protection for CLI-spawned `claude` PTYs —
  the appetite-sized fix is the AgentSession record + finalize; full reaper
  integration for a non-worker process is a distinct, larger change. Filing a
  follow-up is not warranted unless the CLI moves toward production use.

## Update System

No update system changes required — this is a purely internal code change to an
existing CLI entry point. No new dependencies, config files, or migration steps.
`valor-granite-loop` is already declared in `pyproject.toml [project.scripts]`.

## Agent Integration

No agent integration required — `valor-granite-loop` is an operator CLI invoked
directly, not through the bridge or an MCP server. The fix is internal to that
CLI. The new AgentSession record is consumed by existing surfaces (dashboard,
`valor-session list`, watchdog) that already read all sessions; no new wiring is
needed for the agent to "see" it.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` (or the granite PoC doc)
      to note that `valor-granite-loop` now creates and finalizes an AgentSession
      (session type `granite`), making CLI runs visible to dashboard /
      `valor-session list` / watchdog.

### Inline Documentation
- [ ] Update the module docstring in `tools/granite_interactive_tui_poc/cli.py`
      (currently states "never creates a session record" semantics) to reflect
      the new AgentSession lifecycle.
- [ ] Docstring note on the `SessionType.GRANITE` member in `config/enums.py`.

## Success Criteria

- [ ] `valor-granite-loop` creates a `running` AgentSession before the container
      runs and finalizes it `completed`/`failed` on exit.
- [ ] A granite run appears in `curl -s localhost:8500/dashboard.json` sessions
      list while running and as a terminal record after.
- [ ] `python -m tools.valor_session list --role granite` lists granite sessions.
- [ ] `SessionType.GRANITE` exists and no existing `SessionType` consumer breaks.
- [ ] Session persistence failures are guarded — a Redis outage does not change
      the CLI exit code or block the results JSON.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (granite-cli)**
  - Name: granite-cli-builder
  - Role: Wire AgentSession create/finalize into the CLI; add `SessionType.GRANITE`; update `_role_to_session_type`.
  - Agent Type: builder
  - Resume: true

- **Validator (granite-cli)**
  - Name: granite-cli-validator
  - Role: Verify session lifecycle, exit-code preservation, guard behavior, and that no SessionType consumer breaks.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the granite session type

- **Task ID**: build-session-type
- **Depends On**: none
- **Validates**: tests/unit/test_session_executor_granite.py
- **Assigned To**: granite-cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `GRANITE = "granite"` to `SessionType` in `config/enums.py` with a docstring note.
- Grep all `SessionType` / `session_type ==` consumers; confirm each handles an unknown member with a default (no exhaustive `match` without a fall-through).
- Add `"granite": SessionType.GRANITE` to `_role_to_session_type` in `tools/valor_session.py`.

### 2. Wire AgentSession create/finalize into the CLI

- **Task ID**: build-cli-session
- **Depends On**: build-session-type
- **Validates**: tests/unit/granite_container/test_cli.py
- **Assigned To**: granite-cli-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/granite_interactive_tui_poc/cli.py:main`, mint a `session_id`, then create a `running` AgentSession via `AgentSession.create_local(session_type=SessionType.GRANITE, project_key="valor", working_dir=<resolved cwd>, session_id=...)` BEFORE constructing/running the container — wrapped in a best-effort guard.
- After `container.run()` returns, `finalize_session(session, "completed", reason=exit_reason)` for `pm_complete`/`pm_user`, else `"failed"` — guarded.
- In the `except Exception` block, finalize the session `failed` with the exception repr before returning exit code 4 — guarded.
- Add the `agent_session_id` to the stdout one-line summary; keep `result.session_id` for backward compat.
- Update the CLI module docstring to reflect the new lifecycle.

### 3. Update tests

- **Task ID**: build-tests
- **Depends On**: build-cli-session
- **Validates**: tests/unit/granite_container/test_cli.py
- **Assigned To**: granite-cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Patch the session model so unit tests don't need live Redis (or split session-lifecycle assertions into an integration test).
- Assert: running session created before `run()`; finalized `completed` for clean exits and `failed` for hangs/exceptions; `granite` type used; empty `--user-message` writes no session; persistence-failure path logs a warning and preserves the exit code.

### 4. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: granite-cli-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` to document the CLI AgentSession lifecycle.

### 5. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: granite-cli-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the granite CLI test suite and the session-executor granite tests.
- Confirm all success criteria; verify no SessionType consumer regressed.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| CLI tests pass | `pytest tests/unit/granite_container/test_cli.py -q` | exit code 0 |
| Granite executor tests pass | `pytest tests/unit/test_session_executor_granite.py -q` | exit code 0 |
| granite type wired into list | `grep -n 'granite' tools/valor_session.py` | output contains granite |
| granite enum member exists | `python -c "from config.enums import SessionType; assert SessionType.GRANITE == 'granite'"` | exit code 0 |
| Lint clean | `python -m ruff check tools/granite_interactive_tui_poc/ config/enums.py tools/valor_session.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Session type: new `granite` vs. reuse `dev`?** The issue offers both. This
   plan proposes a new `SessionType.GRANITE` member (self-describing, no
   mislabeling, list-filterable). Reusing `dev` is a smaller diff but conflates
   granite PoC runs with real dev work in the dashboard. Confirm the new-type
   direction, or say to reuse `dev`.
2. **Should the orphan reaper actually protect CLI-spawned PTYs?** This plan
   deliberately scopes to the AgentSession record + finalize (visibility), and
   leaves full heartbeat-based reaper self-protection for CLI-spawned `claude`
   PTYs out of scope. Is that acceptable, or is reaper protection part of the ask?
