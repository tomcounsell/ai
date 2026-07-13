---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2001
last_comment_id: 4952368338
---

# Phase 3: Codex Exec as Opt-in Dev-Lane Executor Within Eng Sessions

## Problem

Eng sessions have one developer lane: the top-level Claude PM spawns and later
continues a Claude `dev` Agent-tool subagent inside its own turn. The runner has
no supported way to compare that lane with another coding substrate without
changing the top-level session, bypassing persistence, or teaching the PM to
shell out directly.

**Current behavior:** Every top-level role is driven by
`HeadlessRoleDriver`, which constructs `ClaudeHarnessAdapter`. The PM prime
teaches `Agent(dev)` and `SendMessage`; `dev_agent_id` is recovered after each
turn from Claude's sidechain directory. `HarnessAdapter` exists, but Claude is
its only implementation and Codex cannot be selected for dev work.

**Desired outcome:** `valor-session create --role eng --dev-harness codex`
creates an eng session whose top-level PM remains Claude while developer work
runs through a worktree-scoped, resumable `codex exec` thread. The PM invokes a
session-scoped dev tool in the same blocking point where it invokes
`Agent(dev)` today. The Codex thread survives PM turns and worker restarts,
steering kills the whole process tree safely, usage is comparable across dev
lanes, and missing binary/auth/version fails with an actionable error. Without
the explicit flag, behavior and tool exposure are unchanged.

## Freshness Check

**Baseline commit:** `c8bef664746fe362fc677ea83cf61a1c5fa92e9e`
**Issue filed at:** `2026-07-10T06:26:32Z`
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_runner/runner.py:1314-1342` — the issue's former
  `runner.py:1249-1277` reference drifted; Dev identity is still captured by a
  post-turn sidechain scan and persisted as `dev_agent_id`.
- `models/agent_session.py:232-252` — the four-scalar Claude resume contract
  remains; it has no Codex dev-lane selection, thread, version, or turn-count
  fields.
- `agent/session_runner/router.py:22-29,61-69` — the schema intentionally has no
  external `dev` route because Claude Dev runs inline.
- `agent/session_runner/role_driver.py:417-470` — every top-level turn still
  constructs `ClaudeHarnessAdapter`; this is the invariant Phase 3 must retain.
- `.claude/commands/roles/prime-pm-role.md:13-37` — the PM still delegates
  implementation through `Agent(dev)` and is not taught a Codex tool.

**Cited sibling issues/PRs re-checked:**
- #1996 remains open as the three-phase umbrella; its Phase 3 delegates to this
  issue and is coordination context, not a competing implementation plan.
- #2000 is closed/completed by merged PR #2038. The `HarnessAdapter` protocol,
  normalized events, and schema-first Claude routing are now on main.
- #1925 is closed/completed. Its harness half shipped in #2038 and its
  two-transport rule is settled: Codex Dev is a harness subprocess, never a
  PydanticAI call.
- #1924 and #1928 are closed/completed. Their owner-approved topology is one
  top-level Claude PM with an in-turn, blocking Dev lane and persisted resume
  identity.

**Commits on main since issue filing that changed the premise:**
- `347882f2` (PR #2038) delivered the adapter seam and Claude schema routing —
  it clears the code prerequisite rather than solving the Codex lane.
- `1a23e1e8` pruned AgentSession fields but retained the Claude resume quartet;
  the new Codex fields must follow the nullable-field and migration conventions
  now on main.
- `e1ec8695` centralized settings literals; Codex version/sandbox/resume limits
  therefore belong in typed settings rather than adapter constants.

**Active plans overlapping this area:** `harness-cross-compat.md` is the parent
program plan and explicitly assigns Phase 3 to #2001. No other active plan
implements a Codex dev lane.

**Notes:** The issue's 2026-07-13 comment said the machine lacked Codex and auth.
That is now stale: `codex-cli 0.144.3` is installed and `codex login status`
reports ChatGPT authentication. The premise is unchanged and the human gate is
cleared.

## Prior Art

- **#1924 / #1928:** Established and then shipped the single-PM topology:
  developer execution blocks inside the PM turn, steering kills the process
  group, and continuation identity survives process restarts. The Codex lane
  must preserve these properties rather than recreate a second top-level role.
- **#2000 / PR #2038:** Extracted `HarnessAdapter`, `TurnRequest`, `TurnResult`,
  and normalized lifecycle events. This is the extension seam for
  `CodexHarnessAdapter`; Claude-specific behavior remains in `harness/claude.py`.
- **#1925:** Settled the architectural boundary: session-shaped work uses a CLI
  harness; non-session LLM calls use PydanticAI. Codex Dev belongs on the
  harness side.
- **No previous Codex implementation attempt:** Searches for closed issues and
  merged PRs found enabling work, but no prior Codex adapter or dev-lane build.

## Research

**Queries used:**
- `site:developers.openai.com/codex non-interactive mode codex exec resume json output-schema sandbox`
- `site:developers.openai.com/codex authentication CODEX_API_KEY codex login ChatGPT`
- `site:developers.openai.com/codex CLI reference exec approval sandbox`

**Key findings:**
- The current official [Codex non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
  defines `codex exec --json`, `thread.started`, `turn.*`, `item.*`,
  `--output-schema`, saved CLI auth, the exec-only `CODEX_API_KEY`, explicit
  sandboxing, and `codex exec resume <SESSION_ID>`. It also marks `--full-auto`
  deprecated and warns against broadly exposing API-key environment variables.
- `codex exec` defaults to read-only. This feature deliberately requests
  `workspace-write`; `danger-full-access` remains an explicit provisional
  setting and is never selected by the CLI flag.
- `--output-schema` accepts a file path and returns the schema-conforming JSON as
  the final agent-message text. Unlike Claude, Codex does not attach a parsed
  `structured_output` object to a terminal result event; the adapter must decode
  and validate the final text.

## Spike Results

### spike-1: Verify current Codex exec/resume behavior
- **Assumption**: A current authenticated CLI provides a stable thread id,
  JSONL lifecycle events, schema output, and an explicit resume-time sandbox.
- **Method**: prototype, read-only, two live turns on `codex-cli 0.144.3`
- **Finding**: Both turns emitted the same
  `thread_id=019f5a7e-339a-7323-bf14-1d30931abc86`; the final schema object was
  JSON text in `item.completed{type=agent_message}`; `turn.completed` carried
  usage. Resume accepted `--strict-config -c sandbox_mode="read-only"` and
  `--output-schema`. Input context grew from 14,429 to 28,894 tokens, confirming
  the need for a bounded-resume guard.
- **Confidence**: high
- **Impact on plan**: Parse final agent-message JSON, pin the verified minimum
  CLI to `0.144.3`, repeat explicit approval/sandbox/cwd globals on resume, and
  persist a turn count with a hard, actionable guard rather than silent rollover.

### spike-2: Choose the PM-facing integration mechanism
- **Assumption**: Codex can replace `Agent(dev)` without changing the top-level
  PM routing loop.
- **Method**: code-read across executor, runner, role driver, PM prime, process
  group steering, and MCP configuration
- **Finding**: A capability-gated synchronous MCP tool is the narrowest seam.
  It runs inside the Claude PM's existing process group, receives
  `AGENT_SESSION_ID`, returns Codex's final report to the PM in-turn, and leaves
  schema-first user/completion routing unchanged. A runner-managed `route: dev`
  would recreate a second relay state machine and duplicate steering, timeout,
  report-reinjection, and wrap-up logic.
- **Confidence**: high
- **Impact on plan**: Add a per-session MCP config and Codex-specific PM prime
  only for flagged sessions. The server re-checks the persisted capability on
  every call; teaching the PM or exposing a global tool is insufficient.

### spike-3: Map persistence, update, and test seams
- **Assumption**: Nullable AgentSession fields and an opt-in update module can
  carry the lane across restarts without a storage redesign.
- **Method**: code-read
- **Finding**: Additive nullable fields heal generically, but this repo still
  requires an idempotent registered migration. `_push_agent_session` and its
  manual recreation allowlist are the creation/preservation chokepoints.
  `scripts/update/npm_tools.py` is unconditional and does not reliably upgrade
  floating packages, so Codex needs its own internally opt-in update module.
- **Confidence**: high
- **Impact on plan**: Persist `dev_harness`, `codex_thread_id`, `codex_version`,
  and `codex_turn_count`; validate selection at CLI and queue boundaries; add a
  registered read-compatibility migration; create `scripts/update/codex_cli.py`.

## Data Flow

1. **Creation:** `valor-session create --role eng --dev-harness codex` validates
   the combination before filesystem/Redis side effects and stores the nullable
   capability on `AgentSession`. Omitted means the existing Claude Dev lane.
2. **Executor preflight:** `session_executor` always constructs the top-level
   Claude runner. For a flagged session only, it validates Codex binary,
   minimum version, auth, sandbox policy, and exact worktree before starting.
3. **Conditional PM surface:** the Claude harness receives a session-local MCP
   config and Codex-specific PM prime. Unflagged sessions receive neither. The
   prime replaces only the `Agent(dev)` instructions; research subagents and
   final StructuredOutput routing remain unchanged.
4. **PM delegation:** the PM invokes `codex_dev.run` with the literal developer
   instruction. The stateless MCP handler resolves `AGENT_SESSION_ID`, verifies
   `session_type=eng` and `dev_harness=codex`, acquires the session's dev-lane
   lease, and reads the persisted Codex context.
5. **First Codex turn:** `CodexHarnessAdapter` runs `codex` with global
   `-a never`, explicit sandbox and cwd, then `exec --json --color never
   --output-schema <temp-file> -`. The prompt is sent on stdin. It never uses
   `--ephemeral` or a shell command string.
6. **Immediate persistence:** on `thread.started`, the adapter emits
   `session.started`; the MCP handler synchronously persists the thread id,
   Codex version, and turn count before processing later events.
7. **Later turns:** the handler invokes `codex ... exec resume <thread_id>
   --json --output-schema <temp-file> -`, repeating approval/sandbox/cwd globals.
   A stable id is asserted; drift is logged and returned as an actionable error.
8. **Return to PM:** the adapter maps usage/failure into `TurnResult`, decodes
   the final schema-valid report, and the MCP tool returns it to the same Claude
   PM turn. The PM then routes `user`, `complete`, or continues normally.
9. **Steering/restart:** steering terminates the Claude process group, including
   its MCP server and Codex child. The already-persisted thread is reintroduced
   by the Codex PM prime on the next Claude resume. Worker restart follows the
   same persisted path.

## Architectural Impact

- **New dependencies:** the external `@openai/codex` CLI, installed only on
  opted-in machines. No Python SDK or API client is added.
- **Interface changes:** `HarnessAdapter` gains bounded error detail needed by
  non-Claude consumers; `TurnRequest`/Claude argv assembly gains a per-session
  MCP config input. `valor-session create` gains `--dev-harness codex`.
- **Coupling:** The new MCP server is the only component coupling PM tool use to
  AgentSession persistence. It depends on the harness protocol, not Codex
  internals outside `harness/codex.py`.
- **Data ownership:** `AgentSession` owns selection and continuity. The PM model
  cannot select/switch the harness, and Codex rollout files are not the source
  of application truth.
- **Reversibility:** Omit/disable the creation flag and update opt-in to return to
  the Claude lane. Nullable fields and the MCP server can remain dormant during
  rollback; no destructive migration is required.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer, test engineer, documentarian

**Interactions:**
- PM check-ins: 2-3
- Review rounds: 2+

The appetite is driven by a security-sensitive subprocess boundary, persistent
cross-process continuity, a new agent tool surface, update propagation, and a
real multi-turn validation—not by the adapter's raw line count.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Codex CLI 0.144.3+ | `python -c "import re,subprocess; s=subprocess.check_output(['codex','--version'],text=True); v=tuple(map(int,re.search(r'(\d+)\.(\d+)\.(\d+)',s).groups())); assert v >= (0,144,3)"` | Verified JSONL/schema/resume contract |
| Codex auth | `python -c "import os,subprocess; assert os.getenv('CODEX_API_KEY') or subprocess.run(['codex','login','status'],capture_output=True).returncode == 0"` | Headless turns cannot open an interactive login |
| Phase 2 seam | `python -c "from agent.session_runner.harness.base import HarnessAdapter,TurnRequest,TurnResult"` | Required adapter protocol is present |
| Git worktree | `git rev-parse --is-inside-work-tree` | Codex refuses non-repository roots by default |

Run with `python scripts/check_prerequisites.py docs/plans/codex-exec-dev-lane.md`.

## Solution

### Key Elements

- **Codex harness adapter:** Own argv/stdin construction, version/auth preflight,
  schema temp-file lifecycle, JSONL parsing, normalized events, usage, bounded
  stderr, and actionable native failures.
- **Immutable persisted capability:** `dev_harness` is creation-time-only;
  dedicated Codex thread/version/count fields stay separate from the Claude
  resume quartet and survive queue recreation.
- **Session-scoped MCP dev tool:** A stateless, capability-gated tool invokes the
  adapter inside the PM process group and persists the thread at first sight.
- **Conditional PM priming:** Only flagged eng sessions are told to use the tool
  instead of `Agent(dev)`; final user/complete routing remains Claude schema-first.
- **Opt-in provisioning and parity telemetry:** Typed settings, update install /
  version / auth checks, harness-dimensioned usage events, and a real E2E probe.

### Flow

`valor-session create --role eng --dev-harness codex` → **Claude PM starts** →
PM calls session-local Codex Dev tool → **Codex works in the same worktree** →
thread id persists immediately → report returns to PM → **PM routes to user or
continues** → later turn/restart resumes the same Codex thread.

### Technical Approach

- Implement `agent/session_runner/harness/codex.py` without generalizing
  `HeadlessRoleDriver`; top-level roles remain statically Claude.
- Build subprocess argv as a list, pass prompts on stdin, repeat global
  `-a never`, `-s <sandbox>`, and `-C <worktree>` before `exec` on first and
  resumed turns, and never pass `--ephemeral` or deprecated `--full-auto`.
- Decode Codex's final agent-message JSON into `structured_output`; preserve
  native `turn.failed`, stream `error`, malformed JSONL, missing terminal events,
  and nonzero exit detail in `TurnResult.error_detail` without logging secrets.
- Generate the output-schema file per turn in a secure temporary location and
  unlink it in `finally`, including cancellation/failure paths.
- Add a stateless FastMCP server under `mcp_servers/`. Resolve
  `AGENT_SESSION_ID` per call, enforce the persisted capability and eng type,
  bound execution with a timeout, and serialize each session's Codex turns with
  a crash-releasing/TTL-backed lease.
- Pass a generated MCP config only to flagged sessions and select a Codex PM
  prime variant. Runtime authorization remains mandatory even though the tool
  is hidden from unflagged sessions.
- Default sandbox to `workspace-write`; accept only `workspace-write` or
  `danger-full-access` in typed settings. The CLI flag never enables the latter.
- Default `codex_max_resumed_turns` to a provisional bounded value. At the
  limit, stop and return an actionable PM-visible error while preserving the
  old thread; never silently roll over or discard context.
- Auth precedence is saved CLI login or `CODEX_API_KEY` for the single exec
  subprocess. Do not add `OPENAI_API_KEY` fallback or inspect/log auth tokens.
- Pin the initial minimum version to the live-probed `0.144.3`. A version bump
  must update fixtures/probes before changing the gate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Test spawn failure, cancellation, timeout, malformed JSONL, schema decode,
  native `turn.failed`, stream `error`, nonzero exit, auth failure, and version
  failure; each must produce bounded observable error detail and cleanup.
- [ ] Audit every new `except Exception` branch. Fail-soft telemetry/cleanup
  branches must log; authorization, persistence, and adapter failures must
  return/raise a typed tool error rather than `pass`.

### Empty/Invalid Input Handling
- [ ] Reject empty/whitespace-only developer instructions before spawning Codex.
- [ ] Test hostile text (unicode, 10k characters, shell metacharacters, null
  bytes, mixed newlines) is sent on stdin and never interpolated into argv.
- [ ] Reject missing/invalid `AGENT_SESSION_ID`, non-eng sessions, unflagged
  sessions, malformed thread ids, invalid sandbox values, and exhausted turns.
- [ ] Verify empty/missing agent-message output terminates with an error instead
  of causing a PM/tool loop.

### Error State Rendering
- [ ] PM receives concise actionable messages for install, version, login,
  context-limit, busy-lane, and Codex-native failures; raw stack traces, tokens,
  and full stderr never reach chat.
- [ ] Update reports missing auth as a warning, while actual flagged execution
  fails fast before any Codex model turn.

## Test Impact

- [ ] `tests/unit/session_runner/test_harness_argv_golden.py` — UPDATE: prove
  unflagged Claude argv remains unchanged and flagged MCP config is additive.
- [ ] `tests/unit/session_runner/test_runner_dev_subagent.py` — UPDATE: keep the
  default Agent(dev) prime contract and add the Codex-prime selection contract.
- [ ] `tests/unit/test_session_executor_runner_dispatch.py` — UPDATE: prove every
  top-level path still constructs Claude and only flagged eng gets Codex Dev.
- [ ] `tests/unit/test_valor_session_cli.py` — UPDATE: parser, JSON output,
  eng-only selection, and pre-side-effect rejection.
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: persist/recreate Codex
  fields and restore the existing Claude resume fields in the manual allowlist.
- [ ] `tests/unit/test_settings.py` — UPDATE: Codex defaults, env overrides,
  sandbox validation, minimum version, and resume bound.
- [ ] `tests/unit/test_session_telemetry.py` — UPDATE: harness/dev-lane dimensions
  and separate PM-versus-Dev turn accounting.
- [ ] `tests/unit/session_runner/test_codex_adapter.py` — CREATE with recorded
  success/failure JSONL fixtures, argv/stdin, callbacks, cleanup, auth, version,
  cancellation, and schema cases.
- [ ] `tests/unit/session_runner/test_codex_dev_tool.py` — CREATE with capability,
  session lookup, lease, persistence-at-thread-start, restart, max-turn, and
  error-rendering cases.
- [ ] `tests/unit/test_update_codex_cli.py` — CREATE for opt-in install/upgrade,
  version validation, auth warning, and nonfatal disabled behavior.
- [ ] `tests/integration/test_codex_dev_lane.py` — CREATE for a provisioned live
  create → multi-turn → steer → process-restart → same-thread → complete probe.

## Rabbit Holes

- Do not expose a generic harness selector through `HeadlessRoleDriver`; that
  makes Codex reachable as a top-level bridge session and violates the owner
  decision.
- Do not reintroduce `route: dev` and a runner-managed two-role relay. The MCP
  tool preserves the already-shipped in-turn topology.
- Do not build a universal Codex/Claude event superset. Normalize only events
  consumed for lifecycle, persistence, liveness, usage, and failure.
- Do not emulate headless compaction or silently create replacement threads.
  The bounded guard is an explicit capability degradation.
- Do not register the Codex Dev MCP server globally or teach unflagged PMs to
  shell out to `codex exec`.

## Risks

### Risk 1: Codex leaks into top-level sessions
**Impact:** Bridge messaging, delivery, hooks, and PM continuity run on an
unapproved substrate.
**Mitigation:** Keep `HeadlessRoleDriver` statically Claude; conditionally pass
only a dev MCP config; gate every tool call against persisted eng capability;
add inverse grep and behavioral tests.

### Risk 2: CLI contract/version drift
**Impact:** JSONL parsing, schema output, resume flags, or safety policy silently
change after an upgrade.
**Mitigation:** Pin minimum `0.144.3`, use `--strict-config`, record fixtures,
fail on unknown terminal shapes, and make update validation explicit.

### Risk 3: Thread continuity is lost on crash or recreation
**Impact:** The next PM turn starts a new developer with missing context.
**Mitigation:** Persist on the synchronous `thread.started` callback, keep Codex
fields outside the Claude all-or-nothing quartet, include them in queue recreation,
and test crash boundaries.

### Risk 4: Concurrent calls corrupt one Codex thread
**Impact:** Two `exec resume` processes race on the same rollout and return
misordered work.
**Mitigation:** Acquire a session-scoped lease with timeout/TTL, re-read persisted
state after acquisition, increment count atomically, and release in `finally`.

### Risk 5: Credentials or untrusted prompt text escape
**Impact:** Secrets leak to logs/child processes, or a prompt becomes shell input.
**Mitigation:** Arg-list subprocesses, prompt on stdin, single-invocation
`CODEX_API_KEY`, bounded/scrubbed stderr, no auth-file reads, hostile-string tests.

### Risk 6: Context grows until a turn fails unpredictably
**Impact:** An otherwise healthy eng session stalls late in the build.
**Mitigation:** Persist and enforce a provisional maximum turn count; surface the
limit before spawning; never discard the resumable thread automatically.

## Race Conditions

### Race 1: Worker dies after Codex creates a thread but before turn completion
**Location:** `harness/codex.py` `thread.started` callback → MCP persistence
**Trigger:** Process death between first JSONL event and final result.
**Data prerequisite:** Valid `thread_id` and owning `AgentSession`.
**State prerequisite:** Persisted `dev_harness=codex`.
**Mitigation:** Synchronous inline `session.started` callback saves the handle
immediately; no deferred task or post-return persistence.

### Race 2: Steering kills Codex mid-turn
**Location:** Claude PM process group, MCP server, Codex child
**Trigger:** A steer arrives while the PM is blocked on the MCP call.
**Data prerequisite:** Thread id persisted if `thread.started` already fired.
**State prerequisite:** Codex must remain in the PM process group.
**Mitigation:** MCP adapter uses `start_new_session=False`; existing killpg reaches
the entire tree. Next PM resume reuses the saved thread; if no thread existed,
the next invocation starts the first thread cleanly.

### Race 3: Parallel PM tool calls resume the same thread
**Location:** Codex Dev MCP handler
**Trigger:** Claude schedules two tool calls or a stale worker overlaps takeover.
**Data prerequisite:** Latest thread id and turn count after lease acquisition.
**State prerequisite:** One live session owner.
**Mitigation:** Cross-process session lease with bounded acquisition and TTL;
re-read after lock; return a busy error instead of parallel resume.

### Race 4: Queue recreation drops continuation fields
**Location:** `agent/agent_session_queue.py` manual field allowlist
**Trigger:** Nudge/recreate fallback materializes a replacement session row.
**Data prerequisite:** Codex and existing Claude resume scalars.
**State prerequisite:** Fallback recreation path selected.
**Mitigation:** Expand the allowlist, add round-trip tests for both harnesses, and
never infer continuity from rollout files alone.

### Race 5: Resume bound is checked by two processes
**Location:** persisted `codex_turn_count`
**Trigger:** Overlapping calls both observe the last allowed value.
**Data prerequisite:** Current count.
**State prerequisite:** Lease ownership.
**Mitigation:** Check/increment only while holding the same dev-lane lease and
save with explicit update fields before spawn.

## No-Gos (Out of Scope)

- [EXTERNAL] Automatic harness selection by project, cost, latency, or
  capability remains an owner policy decision after comparative telemetry.
  This phase is manual-only and does not silently decide that product policy.
- [ORDERED] Removing Phase 2's prefix-regex routing fallback waits for the telemetry review scheduled one week after PR #2038 landed (2026-07-18); this phase files the tracking issue but must not remove the fallback.
- [SEPARATE-SLUG #1925] PydanticAI standardization for non-harness LLM calls is
  a separate completed workstream and is not reopened here.

## Update System

- Add typed provisional Codex settings: install opt-in (default false), package,
  minimum version `0.144.3`, model override, sandbox default, turn timeout, and
  maximum resumed turns. Environment overrides use the settings catalog rather
  than inline literals.
- Create `scripts/update/codex_cli.py` rather than adding Codex to unconditional
  `npm_tools`. On opted-in machines it installs/upgrades `@openai/codex`, checks
  semantic version, and warns when neither saved login nor `CODEX_API_KEY` is
  usable. Disabled machines return a clean skipped result.
- Wire the module/result into `scripts/update/run.py`; install/auth problems are
  warnings for general updates but execution remains fail-fast for flagged
  sessions.
- Add and register an idempotent migration in `scripts/update/migrations.py`
  that confirms the four nullable Codex fields read cleanly on legacy
  AgentSession rows. No raw Redis operations and no backfill/index rebuild.

## Agent Integration

- Add a stateless stdio FastMCP server for the Codex Dev tool. Its schema comes
  from typed function signatures and it resolves workspace/session context from
  each request environment rather than server-held state.
- Generate/pass the MCP config only for flagged eng sessions. Do not add it to
  global `.mcp.json` or `~/.claude.json`; unflagged PM/teammate sessions must not
  see the tool.
- Bound every tool call with a timeout, return deterministic auth/version/busy /
  native-failure errors, and revalidate the persisted capability on every call.
- Add integration coverage proving the Claude PM can invoke the tool, receives
  the Codex report, and still uses the existing StructuredOutput delivery path.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/codex-exec-dev-lane.md` covering selection, PM tool
  flow, persistence, steering, limits, telemetry, and rollback.
- [ ] Update `docs/features/harness-adapter.md`,
  `docs/features/headless-session-runner.md`, and
  `docs/features/eng-session-architecture.md`.
- [ ] Add the feature to `docs/features/README.md`.

### Infrastructure Documentation
- [ ] Maintain `docs/infra/harness-cross-compat.md` with dependency, auth,
  sandbox, update opt-in, multi-machine propagation, and rollback rules.

### Inline Documentation
- [ ] Document the global-before-`exec` argv ordering, Codex-vs-Claude schema
  difference, first-event persistence contract, and why the adapter remains out
  of `HeadlessRoleDriver`.

## Success Criteria

- [ ] `harness/codex.py` implements the Phase 2 protocol with recorded JSONL
  fixtures, explicit safety flags, schema cleanup, stable resume, and actionable
  failure detail.
- [ ] `valor-session create --role eng --dev-harness codex` is the only selection
  path; invalid roles reject before side effects; selection cannot change after
  creation.
- [ ] A provisioned eng session completes create → first Codex turn → resumed
  turn → PM steer/preempt → worker restart → same thread id → PM completion.
- [ ] Default eng and every teammate/top-level path remain Claude and do not
  receive the Codex MCP tool.
- [ ] Codex thread/version/count survive queue recreation and process restart;
  existing Claude resume scalars also survive that allowlist path.
- [ ] `/update` supports opt-in install/upgrade and version/auth validation.
- [ ] Telemetry distinguishes `harness=claude|codex`, PM turns, Dev turns,
  usage, failure, resume, and guard exhaustion without secret-bearing payloads.
- [ ] Ordered tracking issue filed for the 2026-07-18 prefix-fallback telemetry review;
  automatic-selection policy remains explicitly unresolved.
- [ ] Tests pass (`/do-test`) and documentation is updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent coordinates specialists and does not
build directly.

### Team Members

- **Builder (Codex adapter)**
  - Name: `codex-adapter-builder`
  - Role: Adapter, event parsing, subprocess safety, fixtures
  - Agent Type: builder
  - Resume: true
- **Builder (persistence and MCP lane)**
  - Name: `codex-lane-builder`
  - Role: AgentSession/CLI/queue persistence, MCP tool, conditional priming
  - Agent Type: builder
  - Resume: true
- **Builder (provisioning and telemetry)**
  - Name: `codex-ops-builder`
  - Role: Settings, update module, migration, telemetry
  - Agent Type: builder
  - Resume: true
- **Test engineer**
  - Name: `codex-test-engineer`
  - Role: Failure matrix, cross-process races, live E2E
  - Agent Type: test-engineer
  - Resume: true
- **Code reviewer**
  - Name: `codex-security-reviewer`
  - Role: Top-level isolation, subprocess/auth safety, concurrency review
  - Agent Type: code-reviewer
  - Resume: true
- **Documentarian**
  - Name: `codex-documentarian`
  - Role: Feature/infra/index documentation and ordered telemetry-review issue
  - Agent Type: documentarian
  - Resume: true
- **Validator**
  - Name: `codex-validator`
  - Role: Machine-readable checks and real lifecycle probe
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement the Codex harness adapter
- **Task ID**: build-codex-adapter
- **Depends On**: none
- **Validates**: `tests/unit/session_runner/test_codex_adapter.py` (create)
- **Informed By**: spike-1
- **Assigned To**: codex-adapter-builder
- **Agent Type**: builder
- **Parallel**: true
- **Domain: security** — build subprocesses from arg lists, keep prompt text on
  stdin, scrub secret-shaped stderr, and confine schema paths to secure temp
  storage.
- Add binary/version/auth preflight, first/resume argv, JSONL normalization,
  schema decoding, bounded error detail, cleanup, and cancellation semantics.
- Capture real success/failure fixtures without committing rollout/auth data.

### 2. Add immutable selection and persistent Codex continuity
- **Task ID**: build-codex-persistence
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session_cli.py`,
  `tests/unit/test_agent_session_queue.py`, `tests/unit/test_agent_session.py`
- **Informed By**: spike-3
- **Assigned To**: codex-lane-builder
- **Agent Type**: builder
- **Parallel**: true
- **Domain: redis-data** — use nullable non-indexed fields, application-level
  enum/bound validation, an idempotent registered migration, and ORM methods
  only; no raw Redis mutation.
- Add `dev_harness`, `codex_thread_id`, `codex_version`, `codex_turn_count`, CLI
  validation, queue persistence/recreation, and migration registration.
- Restore the existing Claude resume scalars to the recreation allowlist while
  touching that chokepoint and add a regression test.

### 3. Build the session-scoped Codex Dev MCP lane
- **Task ID**: build-codex-dev-tool
- **Depends On**: build-codex-adapter, build-codex-persistence
- **Validates**: `tests/unit/session_runner/test_codex_dev_tool.py` (create),
  `tests/unit/session_runner/test_runner_dev_subagent.py`,
  `tests/unit/session_runner/test_harness_argv_golden.py`
- **Informed By**: spike-2
- **Assigned To**: codex-lane-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain: mcp-tool** — derive schema from type hints, resolve context per
  request, bound every call with `asyncio.wait_for`, never retain session state
  in the server, and map auth/native errors deterministically.
- Add runtime capability gating, session lease, immediate thread persistence,
  resume/count guard, conditional MCP config, and Codex PM prime selection.
- Keep Codex as a child of the existing PM process group and prove steering /
  cancellation cleanup.

### 4. Add typed settings, update provisioning, migration, and telemetry
- **Task ID**: build-codex-ops
- **Depends On**: build-codex-persistence
- **Validates**: `tests/unit/test_settings.py`,
  `tests/unit/test_update_codex_cli.py` (create),
  `tests/unit/test_session_telemetry.py`
- **Informed By**: spike-1, spike-3
- **Assigned To**: codex-ops-builder
- **Agent Type**: builder
- **Parallel**: true
- Add provisional typed knobs and the internally opt-in Codex update module.
- Emit separate PM/Dev harness telemetry with usage, resume, failure, and guard
  events; never log prompts, credentials, or raw unbounded stderr.

### 5. Exercise failure paths and the real lifecycle
- **Task ID**: test-codex-dev-lane
- **Depends On**: build-codex-dev-tool, build-codex-ops
- **Validates**: all Test Impact files, `tests/integration/test_codex_dev_lane.py`
- **Assigned To**: codex-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Domain: async** — retain task references, cancel/await children on shutdown,
  treat `CancelledError` as expected, and timeout every external wait/lease.
- Execute the hostile-input and native-failure matrix.
- On this provisioned machine, run the acceptance lifecycle and record stable
  thread id, restart, steer, cleanup, and telemetry evidence.

### 6. Security and architecture review
- **Task ID**: review-codex-boundary
- **Depends On**: test-codex-dev-lane
- **Assigned To**: codex-security-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify Codex is unreachable top-level/unflagged, approval/sandbox policy is
  explicit on resume, prompt text never becomes shell input, secrets are
  scrubbed, and leases/process groups cannot strand work.
- Reject global MCP registration, auth-file reads, silent thread rollover, or
  raw exit-reason literals outside the existing taxonomy.

### 7. Document the feature and file the ordered telemetry-review issue
- **Task ID**: document-codex-dev-lane
- **Depends On**: review-codex-boundary
- **Assigned To**: codex-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create/update the Documentation section's feature and infra files and index.
- Reconcile Phase 3 wording in `docs/plans/harness-cross-compat.md` with this
  issue's dev-lane-only mechanism.
- File the prefix-fallback telemetry review issue for 2026-07-18 and link it
  from #2001; record automatic selection as an unresolved owner policy.

### 8. Final validation
- **Task ID**: validate-codex-dev-lane
- **Depends On**: document-codex-dev-lane
- **Assigned To**: codex-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all machine-readable checks, targeted/full affected suites, prerequisites,
  update dry-run, docs validators, and the provisioned smoke evidence.
- Verify every success criterion and report pass/fail without repairing code.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Prerequisites | `python scripts/check_prerequisites.py docs/plans/codex-exec-dev-lane.md` | exit code 0 |
| Codex adapter tests | `pytest -q tests/unit/session_runner/test_codex_adapter.py` | exit code 0 |
| Dev tool tests | `pytest -q tests/unit/session_runner/test_codex_dev_tool.py` | exit code 0 |
| Selection/persistence tests | `pytest -q tests/unit/test_valor_session_cli.py tests/unit/test_agent_session_queue.py tests/unit/test_session_executor_runner_dispatch.py` | exit code 0 |
| Settings/update tests | `pytest -q tests/unit/test_settings.py tests/unit/test_update_codex_cli.py` | exit code 0 |
| Telemetry tests | `pytest -q tests/unit/test_session_telemetry.py tests/integration/test_session_telemetry_e2e.py` | exit code 0 |
| Existing runner regression | `pytest -q tests/unit/session_runner/test_runner_dev_subagent.py tests/unit/session_runner/test_runner_resume.py tests/unit/session_runner/test_harness_argv_golden.py` | exit code 0 |
| Live Codex lifecycle | `pytest -q -m codex_live tests/integration/test_codex_dev_lane.py` | exit code 0 |
| Top-level remains Claude | `rg -n 'CodexHarnessAdapter' agent/session_runner/role_driver.py` | exit code 1 |
| No ephemeral threads | `rg -n -- '--ephemeral|--full-auto' agent/session_runner/harness/codex.py` | exit code 1 |
| No global Codex MCP | `rg -n 'codex.dev|codex_dev' .mcp.json .claude/settings.json config/mcp_library.json` | exit code 1 |
| Prefix fallback retained | `python -c "from agent.session_runner.router import PREFIX_TOKEN_RE; assert PREFIX_TOKEN_RE"` | exit code 0 |
| Migration registered | `python -c "from scripts.update.migrations import MIGRATIONS; assert any('codex' in k.lower() for k in MIGRATIONS)"` | exit code 0 |
| Feature docs present | `test -f docs/features/codex-exec-dev-lane.md && test -f docs/infra/harness-cross-compat.md` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_runner mcp_servers models/agent_session.py tools/valor_session.py scripts/update tests/unit/session_runner` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_runner mcp_servers models/agent_session.py tools/valor_session.py scripts/update tests/unit/session_runner` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
