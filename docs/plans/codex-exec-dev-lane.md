---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/2001
last_comment_id: 5087021248
reconciled: 2026-09-07
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

**Reconciled:** 2026-09-07
**Baseline commit:** `d5633bb238e4ff5a061342eeb0f62c540dc8d12a`
**Disposition:** Update dependencies and durability contracts before implementation.

- #1996 Phases 1 and 2 are complete; #2000 shipped the adapter seam via #2038.
  `agent/session_runner/harness/base.py` exposes synchronous live callbacks,
  normalized results, and the existing `ExitReason`. There is still no
  `agent/session_runner/harness/codex.py` on main.
- [PR #3213](https://github.com/tomcounsell/ai/pull/3213) is open/unmerged at
  head `671fc1c874bffd5c8f45a551801477829e5f008e`. It provides 57 native
  skills (14 project, 43 general), AGENTS.md, scoped installation, and drift
  validation. Its reported offline validation does not prove headless SDLC
  execution or runtime enforcement. Landing it is a pilot prerequisite, not
  a prerequisite to isolated adapter development.
- [#2494's owner decisions](https://github.com/tomcounsell/ai/issues/2494#issuecomment-5142251045)
  supersede the original issue sketch: no AgentRun model; execution records
  stay on AgentSession. Promises are PM-authored/discharged Job goal versions,
  not a separate obligation ledger.
- `models/agent_session.py` now owns `exec_pid`, `pid_create_time`,
  `exec_cwd`, `exec_harness`, and append-only `spawn_history`.
  `live_fence` selects its newest record. Codex must not replace the live
  Claude PM fence while both processes exist.
- The [durability plan](durability-room-job-agentrun.md) records shipped M1,
  Room foundations (#2622), and Job foundations (#2631).
  `bridge/room_inbox.py` still declares shadow append/routing; the live intake
  calls these alongside existing dispatch. Authoritative inbox dispatch and
  legacy-path retirement remain separately gated #2494 releases.
- Top-level Claude-only execution and blocking developer delegation remain
  the scope constraints from #1924/#1928/#2001. Non-harness classifiers remain
  outside this feature; do not reverse #2494's later granite/PydanticAI decision.
- The earlier 0.144.3 probe is evidence for that exact build and host only.
  Binary, login, worktree, skill discovery, and current CLI behavior must be
  checked on the selected worker; this reconciliation did not run live probes.

The reconciliation changes this plan only. It does not mark implementation,
deployment, fault-injection, or the durability cutover as complete.

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
  `--output-schema`. Input context grew from 14,429 to 28,894 tokens across these two turns.
  This supports a conservative provisional guard; it does not establish that
  current Codex builds lack compaction.
- **Confidence**: high
- **Impact on plan**: Parse final agent-message JSON, use `0.144.3` as the recorded baseline pending the worker contract gate, repeat explicit approval/sandbox/cwd globals on resume, and
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
   instruction. The MCP handler, with durable state owned by AgentSession, resolves `AGENT_SESSION_ID`, verifies
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
8. **Return to PM:** the adapter maps usage/failure into `TurnResult` and
   decodes the final schema-valid report. The handler persists the invocation
   outcome and artifact references before returning it to the same Claude PM
   turn; an interrupted return can be recovered by invocation id. The PM then routes `user`, `complete`, or continues normally.
9. **Steering/restart:** steering terminates and reaps the owned process tree.
   The next PM turn first reconciles the durable invocation: return a stored
   result, inspect interrupted work, or resume the exact saved thread. Worker
   restart follows the same path; process-group membership and orphan handling
   are verified by the ownership gate below.

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
- **Reversibility:** Disable new Codex selections; existing Codex lanes retain
  their worktrees, transcripts, reports, and handles for drain or explicit
  recovery. Never reinterpret a Codex handle as a Claude handle or silently
  change an active lane. Provisioning rollback must not uninstall a CLI needed
  by retained sessions.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer, test engineer, documentarian

**Interactions:**
- PM check-ins: 2-3
- Review rounds: 2+

The appetite is driven by a security-sensitive subprocess boundary, persistent
cross-process continuity, a new agent tool surface, update propagation, and a
real multi-turn validation—not by the adapter's raw line count.

## Reliability Contracts and Release Gates

### Skills and enforcement

Land #3213 before the live pilot and record the installed skill manifest revision.
Run its collection check and exercise discovery in the actual worker worktree,
including user-installed general skills. Inventory required SDLC tools, credentials,
resources, and interactive-only procedures. A required missing capability blocks
that operation with an actionable error; do not assume desktop tools are available
in headless exec.

For every mandatory Claude-hook invariant used by this lane, name its executable
Codex-side enforcement point and a negative test. Use the shared SDLC/tool boundary
or an equivalent restrictive runtime policy. A final acceptance check is sufficient
only for reversible output validation; prohibited writes must be prevented before
they happen. Skill prose is not enforcement. Optional enrichment/telemetry may
fail observably; authorization and required validation may not fail open.

### PM ownership and durable dev invocations

Keep AgentSession's existing live execution fence and spawn history owned by the
Claude PM. Do not call `stamp_execution_spawn` on that record for the concurrent
Codex child. Record dev invocation identity separately in nullable, non-indexed
AgentSession-owned state, with atomic ORM updates; do not introduce AgentRun or a
second top-level session. The schema gate must specify storage, retention, and
queue-recreation behavior before builders add fields.

Each invocation records a stable request id, parent session/owner generation,
request fingerprint, state, thread id when known, child pid/create-time/pgid,
worktree, CLI version, timestamps, and outcome/artifact references. The PM retries
or retrieves using the same request id. A different payload under that id is an
error. Persist intent before spawn and the validated result before MCP return;
acknowledge PM consumption durably so restart can reinject an unconsumed report.
Retain completed reports until consumption and the job's recovery/retention policy
permit cleanup. Keep request content out of telemetry.

Use the PM child/process-group handle as the primary cancellation authority and
child identity as recovery evidence. `(pid, create_time)` is best-effort detection
on macOS, not a race-free guarantee. Confirm death/reap before takeover or worktree
cleanup. Test descendants that change process groups; `start_new_session=False`
alone is not proof that every descendant is covered.

The dev-lane lease needs an ownership token/generation, renewal while running,
bounded acquisition, and compare-owner release. Expiry alone never authorizes a
second writer: reconcile and reap the previous child first. Old owners cannot
publish results or release a successor's lease. Implement through the repository's
ORM-supported atomic operations and preserve existing index/repair conventions.

Thread-id persistence is a durability barrier. If it fails, stop/reap the child
and leave the invocation visibly interrupted. A crash before the first handle is
saved is uncertain execution, not evidence that nothing happened. Reconcile the
worktree and retained local run evidence before retry; never choose `resume --last`
or silently manufacture a replacement thread. Preserve Codex session storage as
well as the id, and fail explicitly if it or the worktree is missing. No portable
cross-harness or cross-machine resume is promised by storing an id alone.

A crash after side effects but before result persistence cannot guarantee exactly
once execution. Recovery must inspect artifacts and external side-effect evidence,
then resume/reconcile or request operator action. Do not blindly replay the task.

### Completion and rollout

`turn.completed`, exit 0, and schema-valid JSON establish turn success only.
The PM accepts artifact/test evidence before reporting development completion.
User delivery is successful only after the existing delivery path confirms it;
failed delivery remains outstanding. Active children, interrupted invocations,
unconsumed reports, and unsaved work block destructive worktree cleanup.

Two release gates apply:

1. **Supervised pilot (#2001):** translated skills available; required enforcement
   proven; adapter/ownership/recovery tests pass; one manually selected worker
   completes the live lifecycle. This may precede authoritative Room dispatch.
2. **End-to-end reliability and expansion (#2494 + #1996):** all intake/recovery
   paths use the authoritative durable inbox and Job binding, with pending-message
   retention/backpressure, idempotent recovery, confirmed delivery, and legacy-path
   retirement validated under #2494. Shadow parity is a prerequisite, not the
   durability barrier. This plan does not implement that shared cutover.

Before collecting canary results, record a matched Claude-dev/Codex-dev workload,
sample size, observation window, and acceptable completion-rate difference. Use
identical artifact acceptance criteria. Report accepted completion, restart recovery,
manual interventions, duplicate execution, silent loss, latency, and usage by
harness and CLI/skill revision. Unknown subscription cost stays unknown, not zero.
Zero observed silent loss, concurrent writers, or destructive cleanup is a hard
release gate, not a claim of zero future probability. Fault-injection coverage is
mandatory even if ordinary canary jobs all succeed. A missing recovery-monitor
heartbeat must itself alert through a live production caller.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Codex CLI 0.144.3+ | `python -c "import re,subprocess; s=subprocess.check_output(['codex','--version'],text=True); v=tuple(map(int,re.search(r'(\d+)\.(\d+)\.(\d+)',s).groups())); assert v >= (0,144,3)"` | Preliminary floor; exact-build probe required |
| Codex auth | `python -c "import os,subprocess; assert os.getenv('CODEX_API_KEY') or subprocess.run(['codex','login','status'],capture_output=True).returncode == 0"` | Headless turns cannot open an interactive login |
| Phase 2 seam | `python -c "from agent.session_runner.harness.base import HarnessAdapter,TurnRequest,TurnResult"` | Required adapter protocol is present |
| Git worktree | `git rev-parse --is-inside-work-tree` | Codex refuses non-repository roots by default |

Run with `python scripts/check_prerequisites.py docs/plans/codex-exec-dev-lane.md`.
The numeric floor and credential-presence check are preliminary only: the worker
contract gate must probe the exact selected CLI build, usable auth, first/resume
schema and sandbox behavior, process ownership, and required skill/tool discovery.
Record the validated version in typed settings; a newer version is not automatically
compatible merely because it exceeds the floor.

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
  a renewable owner-fenced lease under the reliability contract above.
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
- Use `0.144.3` as the historical probe baseline. Set the rollout version gate
  from the worker contract probe; validate upgrades before enabling them.
- Persist invocation intent/result and PM-consumption state through the same
  lease and generation checks used for thread continuity. Treat persistence
  failure as a blocked/interrupted invocation, never successful tool completion.

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
  The bounded guard is a conservative policy pending current-version probes;
  do not claim two observed turns prove compaction is unavailable.
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
**Mitigation:** Validate the selected CLI build and strict-configuration flags, record fixtures,
fail on unknown terminal shapes, and make update validation explicit.

### Risk 3: Thread continuity is lost on crash or recreation
**Impact:** The next PM turn starts a new developer with missing context.
**Mitigation:** Persist on the synchronous `thread.started` callback, keep Codex
fields outside the Claude all-or-nothing quartet, include them in queue recreation,
and test crash boundaries.

### Risk 4: Concurrent calls corrupt one Codex thread
**Impact:** Two `exec resume` processes race on the same rollout and return
misordered work.
**Mitigation:** Acquire a renewable owner-fenced lease, re-read persisted state, increment
count atomically, and compare ownership before release. Expiry requires child
reconciliation/reap before takeover; a stale owner cannot publish.

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
the entire tree. Next PM resume reconciles the saved invocation before continuing its thread.
Without a saved handle, inspect interrupted work and local run evidence; absence
of a handle does not authorize an automatic fresh execution.

### Race 3: Parallel PM tool calls resume the same thread
**Location:** Codex Dev MCP handler
**Trigger:** Claude schedules two tool calls or a stale worker overlaps takeover.
**Data prerequisite:** Latest thread id and turn count after lease acquisition.
**State prerequisite:** One live session owner.
**Mitigation:** Renewable cross-process lease with owner generation and bounded acquisition;
re-read after lock; reconcile/reap before takeover; stale-owner writes and releases
are rejected. Return a busy error instead of parallel resume.

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

### Race 6: Codex finishes but the PM never receives the report
**Location:** result persistence → MCP return → PM consumption
**Mitigation:** Persist report/artifact references before return; retry with the
same invocation id retrieves the result. PM restart reinjects unconsumed results.
A death before persistence triggers evidence reconciliation, not blind replay.

### Race 7: Child registration overwrites the PM fence
**Location:** AgentSession live execution fields and spawn history
**Mitigation:** Keep the PM fence unchanged throughout the dev call. Store child
identity in dev invocation state; assert both identities remain recoverable.
Worktree cleanup waits for child death and durable result reconciliation.

## No-Gos (Out of Scope)

- [EXTERNAL] Automatic harness selection by project, cost, latency, or
  capability remains an owner policy decision after comparative telemetry.
  This phase is manual-only and does not silently decide that product policy.
- [ORDERED] Prefix-regex fallback removal remains deferred to its telemetry
  review. The original 2026-07-18 review date is overdue; find or file its tracking
  issue during implementation, and do not remove the fallback in this phase.
- [SEPARATE-SLUG #1925] PydanticAI standardization for non-harness LLM calls is
  a separate completed workstream and is not reopened here.

## Update System

- Add typed provisional Codex settings: install opt-in (default false), package,
  validated CLI version gate (historical probe baseline `0.144.3`), model override,
  sandbox default, turn timeout, and
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
  that confirms nullable Codex continuity and invocation fields read cleanly on legacy
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

- [ ] Document skill installation/discovery and the mandatory-invariant enforcement
  matrix, invocation recovery/retention, and the separate pilot/expansion gates.

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
- [ ] Existing or newly filed tracking issue covers the overdue prefix-fallback review;
  automatic-selection policy remains explicitly unresolved.
- [ ] Tests pass (`/do-test`) and documentation is updated (`/do-docs`).

- [ ] Required skill/tool discovery and negative enforcement tests pass headlessly.
- [ ] PM fence remains intact while Codex runs; child ownership and reap are proven.
- [ ] Durable invocation deduplication, stored-report recovery, and missing-transcript
  handling pass; stale lease owners cannot write, release, or overlap a successor.
- [ ] Interrupted/unconsumed work blocks destructive cleanup; delivery failure stays
  visibly outstanding.
- [ ] Every fault below passes for the supported worker build. Canary criteria are
  declared before evaluation; pilot success is distinguished from the separately
  gated #2494 authoritative-inbox rollout and broad reliability claim.

## Fault-Injection Acceptance Matrix

| Injected fault | Required outcome |
|---|---|
| Worker dies during Codex execution | Worktree/session storage/known handle survive; one recovery owner reaps or resumes. |
| Death before thread-id persistence | Invocation remains uncertain; evidence reconciled before any new execution. |
| Redis fails at intent/handle/result persistence | No success response; child stopped when persistence safety is lost; work retained. |
| PM dies after Codex result persistence | Same invocation returns stored report; no duplicate development. |
| Side effect succeeds before result persistence | Inspect effect/artifact evidence; no blind replay or exactly-once claim. |
| Stop and unrelated steering arrive together | Stop executes; unrelated instructions remain pending. |
| Lease expires while child runs; old owner returns | No second writer; old owner cannot release lease or publish over successor. |
| PID reused or descendant changes process group | No unrelated process signalled; owned descendants accounted for before cleanup. |
| Missing transcript/worktree or mismatched thread | Explicit recovery error; original handle/evidence preserved. |
| Auth/quota/native failure, malformed stream, missing terminal, invalid schema | Typed visible failure with bounded retry; no false completion. |
| PM exits cleanly with unfinished child/unconsumed report | Outstanding work detected; worktree cleanup blocked. |
| Delivery fails after accepted development | Delivery remains outstanding through the shared delivery path. |
| Monitor caller stops running | Missing monitor heartbeat detected, not interpreted as health. |

Add these cases to the adapter/tool unit suites and live integration suite below;
record which were simulated and which were exercised against real subprocesses.

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

### 0. Revalidate the worker contract and settle the persistence schema
- **Task ID**: validate-codex-contract
- **Depends On**: none
- **Assigned To**: codex-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Probe the selected worker CLI/auth, first/resume schema/sandbox flags, cancellation,
  and session-storage behavior. Update version/config requirements from evidence.
- Specify AgentSession-owned invocation storage, atomic lease/generation operations,
  retention, PM fence preservation, and queue recreation before schema changes.
- Inventory mandatory hook invariants and name executable enforcement points.
- Record unresolved capabilities as explicit build gates, not assumed equivalence.
  Isolated adapter work can precede #3213 merge; pilot validation cannot.


### 1. Implement the Codex harness adapter
- **Task ID**: build-codex-adapter
- **Depends On**: validate-codex-contract
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
- **Depends On**: validate-codex-contract
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
- Add the gated AgentSession-owned invocation schema and atomic state transitions;
  preserve the PM live fence and implement retention and cleanup eligibility.
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
  in the server, and map auth/native errors deterministically. Durable invocation state lives
  on AgentSession, not in server memory.
- Add runtime capability gating, session lease, immediate thread persistence,
  resume/count guard, conditional MCP config, and Codex PM prime selection.
- Keep Codex as a child of the existing PM process group and prove steering /
  cancellation cleanup, including descendants and orphan recovery.
- Implement durable request ids, intent-before-spawn, result-before-return,
  report retrieval/PM consumption, lease renewal/fenced takeover, and evidence
  reconciliation. Enforce required SDLC invariants at executable boundaries.

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
- After #3213 lands, verify installed manifest/discovery and mandatory tool
  enforcement on the selected worker. Run every fault-injection matrix row.
- Run the live lifecycle and a predeclared matched canary; record thread continuity,
  stored-report recovery, restart, steer, cleanup, delivery, and telemetry evidence.
  Report missing integrations as untested, not passing.

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
- Find the existing prefix-fallback telemetry-review issue or file the required
  follow-up if absent; its original 2026-07-18 review date is overdue, not a future
  schedule. Link the disposition from the plan; do not remove the fallback.
- Record automatic selection as unresolved and #2494 authoritative inbox/legacy
  retirement as an expansion gate. Do not duplicate those changes in this build.

### 8. Final validation
- **Task ID**: validate-codex-dev-lane
- **Depends On**: document-codex-dev-lane
- **Assigned To**: codex-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all machine-readable checks, targeted/full affected suites, prerequisites,
  update dry-run, docs validators, and the provisioned smoke evidence.
- Verify every success criterion and report pass/fail without repairing code.
- Distinguish #2001 supervised-pilot acceptance from full-program reliability;
  do not claim the latter or recommend broad expansion until #2494's authoritative
  cutover and the matched canary gates have independent evidence.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Prerequisites | `python scripts/check_prerequisites.py docs/plans/codex-exec-dev-lane.md` | exit code 0 |
| Codex adapter tests | `./scripts/pytest-clean.sh -q tests/unit/session_runner/test_codex_adapter.py` | exit code 0 |
| Dev tool tests | `./scripts/pytest-clean.sh -q tests/unit/session_runner/test_codex_dev_tool.py` | exit code 0 |
| Selection/persistence tests | `./scripts/pytest-clean.sh -q tests/unit/test_valor_session_cli.py tests/unit/test_agent_session_queue.py tests/unit/test_session_executor_runner_dispatch.py` | exit code 0 |
| Settings/update tests | `./scripts/pytest-clean.sh -q tests/unit/test_settings.py tests/unit/test_update_codex_cli.py` | exit code 0 |
| Telemetry tests | `./scripts/pytest-clean.sh -q tests/unit/test_session_telemetry.py tests/integration/test_session_telemetry_e2e.py` | exit code 0 |
| Existing runner regression | `./scripts/pytest-clean.sh -q tests/unit/session_runner/test_runner_dev_subagent.py tests/unit/session_runner/test_runner_resume.py tests/unit/session_runner/test_harness_argv_golden.py` | exit code 0 |
| Live Codex lifecycle | `./scripts/pytest-clean.sh -q -m codex_live tests/integration/test_codex_dev_lane.py` | exit code 0 |
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
