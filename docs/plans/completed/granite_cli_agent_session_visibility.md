---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1571
last_comment_id:
revision_applied: true
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

- **New dependencies**: CLI imports `AgentSession` (`models.agent_session`),
  `finalize_session` (`models.session_lifecycle`), `SessionType`
  (`config.enums`), plus stdlib `os`/`uuid` for `working_dir` resolution and the
  `local-`-prefixed `session_id`. No new external deps.
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
  rather than mislabeled as `dev`. `valor_session list --role granite` is served
  by the raw `session_type == role_filter` match (`tools/valor_session.py:1003`),
  so the enum member alone is sufficient — no `_role_to_session_type` edit needed.

### Flow

Operator runs `valor-granite-loop` → CLI creates AgentSession (running) →
Container.run() drives PTYs → CLI finalizes session (completed/failed) → results
JSON written → run visible in dashboard / `valor-session list` for its lifetime
and after.

### Technical Approach

- **Session id ordering wrinkle + recovery-safety prefix.** `Container.run()`
  mints the `session_id` internally and only returns it inside `ContainerResult`.
  The AgentSession must exist *before* `run()` so the run is visible *during*
  execution (the whole point — orphan-reaper coverage and live dashboard).
  Resolution: the CLI mints the `session_id` and creates the AgentSession with it
  before calling `run()`. **The CLI MUST mint `session_id = "local-" +
  uuid.uuid4().hex[:12]`** (keep the 12-hex body for the existing shape; prepend
  the `local-` prefix). This is a *correctness requirement*, not cosmetic: the
  worker's startup recovery (`_recover_interrupted_agent_sessions_startup`,
  `agent/session_health.py:538`) discriminates sessions by
  `is_local = entry.session_id.startswith("local")` — NOT by `session_type`. A
  bare-hex `session_id` falls through to the bridge recovery path
  (session_health.py:629), which re-queues `running` → `pending`
  (`priority="high"`) and the worker would then execute the granite record as a
  bridge session against stale context. A `local-` prefix routes the record
  through the `elif is_local` branch (line 587): since
  `is_local and session_type == SessionType.DEV` is False for a granite session,
  it skips the dev re-queue (line 545) and lands on the safe **abandon** path
  (line 594, `finalize_session(..., "abandoned")`). Adding `SessionType.GRANITE`
  alone does NOT protect the record — the prefix is the load-bearing guard. The
  container's internally-minted id is logged/returned as before; the CLI does NOT
  thread its id into the container (the container's id is an internal trace
  artifact; the AgentSession is the canonical visibility record). The stdout
  summary prints BOTH ids, **labeled** (see "Labeled IDs" below).
- **session type (explicit, never defaulted).** Add `SessionType.GRANITE =
  "granite"` in `config/enums.py`. **`create_local` defaults `session_type` to
  `SESSION_TYPE_DEV` (`models/agent_session.py:1375`)** — omitting the kwarg
  silently registers a `dev` record, defeating the new enum and the `list --role
  granite` criterion. The CLI MUST pass `session_type=SessionType.GRANITE`
  explicitly, and Task 3 MUST assert `session.session_type == "granite"` on the
  created record (not merely that `create_local` was called). Confirm no
  exhaustive `match`/branch on `SessionType` elsewhere breaks on a new member —
  recon found the switch sites (`agent/hooks/pre_tool_use.py`,
  `agent/session_executor.py`) all use `== PM` / `== TEAMMATE` / `== DEV`
  equality checks with sensible defaults, so a new member falls through safely.
- **project_key / working_dir (resolved before create).** Use
  `project_key="valor"` (this repo). `working_dir` is a *required* `create_local`
  field; when `--cwd` is None the container's `cwd` is a fresh tempdir created
  lazily inside container construction, so it is NOT safe to read before `run()`.
  Resolve a concrete, existing directory independently: `working_dir = args.cwd
  or os.getcwd()`, and assert `working_dir and os.path.isdir(working_dir)` before
  the `create_local` call. Do NOT derive `working_dir` from the container's
  sandbox tempdir.
- **finalize mapping.** `pm_complete` / `pm_user` → `completed`; everything else
  (`pm_max_turns`, `dev_hang`, `pm_hang`, `startup_unresolved`, `exception`) →
  `failed`, with the `exit_reason` (or exception repr) as the `reason` string.
- **double-finalize safety.** `container.run()` returns `exit_reason="exception"`
  WITHOUT raising on spawn failure, so the post-run finalize marks the session
  `failed` first; if a later line (e.g. the results-JSON `OSError` path) then
  raises, the `except Exception` block would finalize a second time, and
  `finalize_session` RAISES on terminal→different-terminal when
  `reject_from_terminal=True` (the default, `models/session_lifecycle.py:225,299`).
  The except-block finalize MUST pass `reject_from_terminal=False` so a repeat
  `failed` finalize is a no-op instead of a raise.
- **None-guard before finalize.** If the guarded `create_local` fails, `session`
  is `None`. Every finalize call MUST be wrapped in an explicit `if session is not
  None:` check — relying on the best-effort `try/except` to swallow an
  `AttributeError` on `None` obscures intent. The best-effort guard stays inside
  the `if session is not None:` branch.
- **failure isolation + defined operator signal.** Wrap the AgentSession create
  and finalize in best-effort guards: a Redis/Popoto failure must NOT change the
  CLI's exit code or prevent the results JSON from being written — the visibility
  record is additive, not load-bearing for the PoC's primary output. The
  observable contract on persistence failure: **emit exactly one stderr line
  `granite session not recorded: <reason>` and proceed.** stdout and the results
  JSON are unchanged; the warning is stderr-only. This is the operator signal on a
  Redis-less dev machine (the PoC's stated home) — not silent, not noisy.
- **labeled IDs.** The stdout summary prints both ids, labeled, so operators know
  which to feed `valor-session`: `{"session_id": <container>, "agent_session_id":
  <local-...>, ...}`. `agent_session_id` (the `local-`-prefixed record) is the
  operational id for `valor-session steer/kill`; the container `session_id` is a
  trace artifact. Document this distinction in `granite-pty-production.md`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The CLI's existing `except Exception` around `container.run()` (cli.py:107)
      must finalize the AgentSession `failed` before returning exit code 4 — add a
      test asserting the session reaches `failed` when `run()` raises.
- [ ] The new AgentSession create/finalize guards swallow Popoto/Redis errors and
      emit exactly one stderr line `granite session not recorded: <reason>` —
      assert the line fires on stderr only (stdout + results JSON unchanged) and
      the CLI still returns the correct exit code when session persistence is
      patched to raise.
- [ ] Double-finalize: when the post-run path finalizes `failed` and the
      `except` block then finalizes again with `reject_from_terminal=False`, the
      second call is a no-op, not a raise — assert no exception escapes.

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

**There IS a concurrent writer: the worker's startup recovery.** On any machine
where the worker also runs, the worker's
`_recover_interrupted_agent_sessions_startup()` scans all `running` AgentSession
records on startup. If the granite CLI is SIGKILLed between create and finalize —
or a worker restarts mid-run — the worker sees a stale `running` granite record
and tries to recover it. Recovery discriminates by the **`session_id` string
prefix** (`is_local = entry.session_id.startswith("local")`,
`agent/session_health.py:538`), NOT by `session_type`.

**Hazard:** a bare-hex `session_id` (the container's `uuid.uuid4().hex[:12]`
shape) is NOT `is_local`, so it falls through to the bridge recovery path
(line 629), which re-queues `running` → `pending` (`priority="high"`); the worker
then executes the granite record as a bridge session against stale context —
mis-execution.

**Mitigation (load-bearing):** the CLI mints `session_id = "local-" +
uuid.uuid4().hex[:12]`. The `local-` prefix makes the record `is_local`. Because
a granite session is not `SessionType.DEV`, it skips the local-dev re-queue
branch (line 545) and lands on the `elif is_local` **abandon** path (line 587 →
594), which finalizes the orphaned record `abandoned` rather than re-queuing it.
No granite record is ever executed by the worker. Adding `SessionType.GRANITE`
does NOT by itself protect the record — the prefix is the guard. The terminal
finalize race (recovery abandons a record the CLI is about to finalize) is benign:
`finalize_session` is called with `reject_from_terminal=False` in the CLI's
except path (see Technical Approach), so a CLI finalize landing after a recovery
abandon is a no-op, not a raise.

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

- [x] `valor-granite-loop` creates a `running` AgentSession (session_id prefixed
      `local-`, session_type `granite`) before the container runs and finalizes it
      `completed`/`failed` on exit.
- [ ] The granite `session_id` starts with `local-` so worker startup recovery
      routes an orphaned `running` record to the abandon path (never re-queues it
      as a bridge session).
- [ ] A granite run appears as a terminal record in `curl -s
      localhost:8500/dashboard.json` after it exits (visibility while running is
      best-effort — short PoC runs may finish before a dashboard poll).
- [ ] `python -m tools.valor_session list --role granite` lists granite sessions.
- [ ] `SessionType.GRANITE` exists and no existing `SessionType` consumer breaks.
- [ ] Session persistence failures are guarded — a Redis outage emits exactly one
      stderr line `granite session not recorded: <reason>` and does not change the
      CLI exit code or block the results JSON write.
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
- Do NOT edit `_role_to_session_type` in `tools/valor_session.py`: the `list --role granite` criterion is served by the raw `session_type == role_filter` match at `tools/valor_session.py:1003`, not by `_role_to_session_type` (line 383, which only gates the `create --role` path — itself constrained to `choices=["pm","dev","teammate"]` and never used to create granite sessions). The enum member alone satisfies the success criterion.

### 2. Wire AgentSession create/finalize into the CLI

- **Task ID**: build-cli-session
- **Depends On**: build-session-type
- **Validates**: tests/unit/granite_container/test_cli.py
- **Assigned To**: granite-cli-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/granite_interactive_tui_poc/cli.py:main`, mint `session_id = "local-" + uuid.uuid4().hex[:12]` (the `local-` prefix is REQUIRED — it routes worker startup recovery to the safe abandon path; see Race Conditions). Resolve `working_dir = args.cwd or os.getcwd()` and assert `working_dir and os.path.isdir(working_dir)`. Then create a `running` AgentSession via `AgentSession.create_local(session_id=session_id, session_type=SessionType.GRANITE, project_key="valor", working_dir=working_dir)` BEFORE constructing/running the container — wrapped in a best-effort guard that, on failure, sets `session=None`, emits exactly one stderr line `granite session not recorded: <reason>`, and proceeds.
- Pass `session_type=SessionType.GRANITE` **explicitly** (never rely on the `create_local` default, which is `dev`).
- After `container.run()` returns, `if session is not None:` finalize — `finalize_session(session, "completed", reason=exit_reason)` for `pm_complete`/`pm_user`, else `"failed"` — guarded.
- In the `except Exception` block, `if session is not None:` finalize the session `failed` with the exception repr, passing `reject_from_terminal=False` (so a repeat finalize after the post-run path is a no-op, not a raise), before returning exit code 4 — guarded.
- Every finalize call is wrapped in `if session is not None:` with the best-effort `try/except Exception: logger.warning(...)` inside that branch.
- Add a labeled `agent_session_id` to the stdout one-line summary alongside the container `session_id`: `{"session_id": <container>, "agent_session_id": <local-...>, ...}`. `agent_session_id` is the id for `valor-session` operations.
- Update the CLI module docstring to reflect the new lifecycle.

### 3. Update tests

- **Task ID**: build-tests
- **Depends On**: build-cli-session
- **Validates**: tests/unit/granite_container/test_cli.py
- **Assigned To**: granite-cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Patch the session model so unit tests don't need live Redis (or split session-lifecycle assertions into an integration test).
- Assert: running session created before `run()`; `session_id` starts with `local-` (recovery-safety prefix); `session.session_type == "granite"` on the created record (not merely that `create_local` was called); finalized `completed` for clean exits and `failed` for hangs/exceptions; empty `--user-message` writes no session.
- Assert the persistence-failure path: patch `create_local` to raise, then confirm (a) the CLI exit code is unchanged, (b) stdout and the results JSON are unchanged, and (c) exactly one stderr line `granite session not recorded: <reason>` is emitted (stderr only).
- Assert the double-finalize path: when the post-run finalize marks `failed` and a later error triggers the `except` block, the second finalize (with `reject_from_terminal=False`) does not raise.

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
| granite enum member exists | `python -c "from config.enums import SessionType; assert SessionType.GRANITE == 'granite'"` | exit code 0 |
| local- prefix minted in CLI | `grep -n '"local-"' tools/granite_interactive_tui_poc/cli.py` | output contains local- |
| Lint clean | `python -m ruff check tools/granite_interactive_tui_poc/ config/enums.py` | exit code 0 |

## Critique Results

**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 9 total (1 blocker, 6 concerns, 2 nits)
**Verdict**: NEEDS REVISION

### Blockers

#### Worker startup recovery mis-executes a CLI-created `running` granite session
- **Severity**: BLOCKER
- **Critics**: Operator, Adversary (x2 findings), corroborated by prior-pass structural note
- **Location**: Race Conditions section / `agent/session_health.py:629`
- **Finding**: The plan's Race Conditions section asserts "no concurrent writer ...
  single synchronous process." This is **false** on any machine where the worker
  also runs. `_recover_interrupted_agent_sessions_startup()` discriminates sessions
  by `is_local = entry.session_id.startswith("local")` (session_health.py:538), NOT
  by `session_type`. A granite session created with `session_id = uuid.uuid4().hex[:12]`
  does not start with `"local"`, so it falls through to the **bridge path** (line 629),
  which re-queues `running` -> `pending` (`priority="high"`) and the worker then picks
  it up and **executes it as a bridge session** — replaying the granite container
  against stale context. If the CLI is SIGKILLed between create and finalize (or a
  worker restarts mid-run), this guarantees mis-execution.
- **Suggestion**: Mint the granite `session_id` with a `local-` prefix
  (e.g. `f"local-granite-{uuid.uuid4().hex[:12]}"`) so the existing `is_local` guard
  fires. A `local` granite session still falls through line 545 (`is_local and == DEV`
  is False) into line 587 (`elif is_local`) and is safely **abandoned** rather than
  re-queued. Then correct the Race Conditions section: the writer IS concurrent (the
  worker's startup recovery), and the `local-` prefix is the mitigation.
- **Implementation Note**: The discriminator is a **string prefix on `session_id`**
  (`session_health.py:538`), not a `session_type` check — adding `SessionType.GRANITE`
  alone does NOT protect the record. Required change in Task 2: the CLI must mint
  `session_id = "local-" + uuid.uuid4().hex[:12]` (keep the 12-hex body for the existing
  shape; the `local-` prefix routes recovery to the abandon path at line 587). Update
  the Race Conditions section to drop the "no concurrent writer" claim and document the
  `local-` prefix as the recovery-safety guard.

### Concerns

#### Double-finalize raises inside the except-block guard
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Step 2 — finalize in `except Exception`
- **Finding**: `container.run()` returns `exit_reason="exception"` WITHOUT raising on
  spawn failure, so the post-run finalize marks the session `failed` first. If a later
  line (e.g. the results-JSON `OSError` path) then raises, the `except Exception` block
  finalizes a second time; `finalize_session` RAISES on terminal->different-terminal
  with `reject_from_terminal=True`.
- **Suggestion**: Make the except-block finalize unconditionally safe.
- **Implementation Note**: `finalize_session(session, "failed", reason="exception", reject_from_terminal=False)`
  — the kwarg already exists in the signature; passing `False` makes a repeat `failed`
  finalize a no-op instead of a raise, regardless of guard scope.

#### `working_dir` may be unresolved before `create_local`
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Step 2 — `create_local(working_dir=<resolved cwd>)`
- **Finding**: When `--cwd` is None the container's `cwd` is a fresh tempdir created
  inside container construction. `working_dir` is a required `create_local` field;
  passing None/empty stores a corrupt value or raises.
- **Suggestion**: Resolve a concrete, existing directory before `create_local`.
- **Implementation Note**: Compute `working_dir = args.cwd or os.getcwd()` (repo root)
  and assert `working_dir and os.path.isdir(working_dir)` before the `create_local`
  call; do not derive it from the container's lazily-created sandbox tempdir.

#### Guard must short-circuit finalize when create failed (session is None)
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Step 2 — best-effort guards
- **Finding**: If `create_local` is guarded and fails, `session` is `None`; downstream
  `finalize_session(session, ...)` then runs on `None` and relies on the guard to
  swallow an `AttributeError`, obscuring intent.
- **Suggestion**: Explicit None-check before every finalize.
- **Implementation Note**: `if session is not None:` wraps each finalize call; the
  best-effort `try/except Exception: logger.warning(...)` stays inside that branch.

#### `create_local` defaults `session_type` to `dev` — silent mislabel risk
- **Severity**: CONCERN
- **Critics**: Archaeologist
- **Location**: Step 2 — `create_local(...)`
- **Finding**: `create_local`'s default is `session_type=SESSION_TYPE_DEV`; omitting
  `session_type=SessionType.GRANITE` silently registers a `dev` record, defeating the
  new enum and the `list --role granite` criterion.
- **Suggestion**: Pass `session_type=SessionType.GRANITE` explicitly and assert it in
  the test (the task already lists the kwarg; add the assertion).
- **Implementation Note**: Test in Task 3 must assert `session.session_type == "granite"`
  on the created record, not just that `create_local` was called.

#### Redis-unavailable behavior is an undefined operator experience
- **Severity**: CONCERN
- **Critics**: User
- **Location**: Success Criteria — "Session persistence failures are guarded"
- **Finding**: "Best-effort, no exit-code change" is an implementation note, not an
  observable contract. On a Redis-less dev machine (the PoC's stated home) the operator
  gets undefined output — silent, or noisy warnings on every run.
- **Suggestion**: Specify the observable signal.
- **Implementation Note**: Define it as: on persistence failure, emit exactly one
  `stderr` warning line (`granite session not recorded: <reason>`) and proceed; assert
  in Task 3 that stdout/results JSON are unchanged and the line is on stderr only.

#### Two distinct IDs (container vs AgentSession) confuse the operator
- **Severity**: CONCERN
- **Critics**: Archaeologist, User, Simplifier (NIT-elevated by consensus)
- **Location**: Technical Approach — "Session id ordering wrinkle"; stdout summary
- **Finding**: The stdout summary prints `result.session_id` (container's internal id)
  AND `agent_session_id`. Operators reaching for `valor-session steer/kill` need the
  `agent_session_id`; printing both unlabeled forces them to know the distinction.
- **Suggestion**: Label both IDs in stdout and the docs; call out `agent_session_id`
  as the operational id for `valor-session`.
- **Implementation Note**: Print `{"session_id": <container>, "agent_session_id": <local-...>, ...}`
  with a one-line docs note in `granite-pty-production.md` stating `agent_session_id`
  is the id for `valor-session` operations. (Note: if the `local-` prefix fix lands,
  `agent_session_id` IS the canonical record; the container id remains a trace artifact.)

### Nits

#### `_role_to_session_type` edit serves no stated success criterion
- **Severity**: NIT (Simplifier filed CONCERN; structural check downgrades — harmless)
- **Critics**: Simplifier, structural cross-reference check
- **Location**: Task 1 — `_role_to_session_type` edit in `tools/valor_session.py`
- **Finding**: `list --role granite` (success criterion) is served entirely by the raw
  `session_type == role_filter` match at `tools/valor_session.py:1003`. The
  `_role_to_session_type` map (line 383) only gates the `create --role` path, which is
  itself constrained by `choices=["pm","dev","teammate"]` (line 1288) — and the plan
  never asks to create granite sessions via `valor-session create`. The map edit is
  harmless but dead scope.
- **Suggestion**: Optional — drop the `_role_to_session_type` edit from Task 1; keep
  only the enum member. Leave `choices=["pm","dev","teammate"]` on `create` unchanged.

#### "Appears while running" is hard to verify for short PoC runs
- **Severity**: NIT
- **Critics**: User
- **Location**: Success Criteria — dashboard "while running"
- **Finding**: Short granite runs may finish before a dashboard check; the "while
  running" criterion is only demonstrable under artificially slow conditions.
- **Suggestion**: Treat the terminal-record visibility as the verifiable user value;
  keep "while running" as best-effort, not a hard gate.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-5 sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` resolve; no cycles (1<-2<-3<-4<-5) |
| File paths exist | PASS | 14/14 referenced paths exist |
| Prerequisites met | PASS | Redis reachable (Popoto backend) |
| Cross-references | CONCERN | `list --role granite` criterion served by raw filter (valor_session.py:1003), not by the Task 1 `_role_to_session_type` edit — that edit is dead scope (NIT) |

### Verdict

**NEEDS REVISION** — 1 blocker must be resolved before build.

The blocker (worker startup recovery mis-executing a CLI-created `running` granite
session, because recovery discriminates on the `session_id.startswith("local")` prefix
rather than `session_type`) is a correctness/safety defect that the plan's Race
Conditions section actively contradicts ("no concurrent writer"). The fix is small —
mint the granite `session_id` with a `local-` prefix so the existing recovery guard
routes it to the abandon path, and correct the Race Conditions section — but it changes
the plan's central design claim and must be applied before build. The six concerns
fold into the same revision pass (guard ordering, `working_dir` resolution,
`reject_from_terminal=False`, explicit `session_type`, defined Redis-failure UX, labeled
IDs). The two nits are optional cleanups.

<!-- Verdict recorded via sdlc-tool verdict record --stage CRITIQUE --verdict "NEEDS REVISION" --issue-number 1571 -->

---

## Resolved Questions

1. **Session type: new `granite` vs. reuse `dev`?** RESOLVED — new
   `SessionType.GRANITE`. This is now load-bearing: the critique fix requires a
   self-describing type for `list --role granite` and for distinguishing granite
   PoC runs from real dev work in the dashboard. (Note: the recovery-safety guard
   is the `local-` `session_id` prefix, NOT the session type — see Race
   Conditions.)
2. **Should the orphan reaper actually protect CLI-spawned PTYs?** RESOLVED —
   deferred / out of scope. This plan scopes to the AgentSession record + finalize
   (visibility) plus the `local-` prefix that makes orphaned records abandon
   safely. Full heartbeat-based reaper self-protection for CLI-spawned `claude`
   PTYs remains a distinct, larger change (see No-Gos).
