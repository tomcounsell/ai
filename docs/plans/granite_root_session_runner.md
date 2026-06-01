---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-06-01
tracking: https://github.com/tomcounsell/ai/issues/1542
last_comment_id:
---

# Granite Root Session Runner — Production Cutover

## Problem

The repo has two execution substrates and only one is real. Every production session
(Telegram, email, `valor-session` CLI; pm/dev/teammate) runs through
`agent/sdk_client.py::get_response_via_harness()`. The granite-agent-loop (PR #1487,
merged) is a standalone PoC that nothing in production calls. We have decided to adopt
the granite loop as the way sessions run.

**Current behavior:** `worker/__main__.py` → `agent/session_executor.py` →
`agent/sdk_client.py::get_response_via_harness()` spawns a single `claude -p` subprocess
per turn and returns its result. The granite loop runs only via `scripts/granite_poc.py`.

**Desired outcome:** The granite-agent-loop is the **root runner** for *all* sessions.
No `session_type` value and no config setting can route a session around it. The
`sdk_client.py` execution path is *removed* (no fallback flag — NO-LEGACY-CODE). The only
deliberate configuration surface is **model-per-role**: the operator, PM, and Dev models
are each selectable via config/heuristics as parameters of the runner, never as an escape
hatch from it.

## Freshness Check

**Baseline commit:** `70198200`
**Issue filed at:** 2026-06-01T10:04:59Z (this session)
**Disposition:** Unchanged (issue filed minutes ago; no commits landed on main since;
recon performed at filing time via Explore agent and is current).

**Notes:** No file:line drift possible — the recon and the plan were authored in the same
session against the same `70198200` baseline.

## Prior Art

- **PR #1487** (merged): *PoC: granite4.1:3b drives dual Claude Code sessions over Max
  OAuth*. Shipped the three PoC modules and the assessment. This plan is its production
  successor. Verdict in the results doc: "proceed to production planning, with caveats."
- **Issue #1486** (closed): the PoC tracking issue. Defined the kill criteria
  (parse-error-rate ≤ 20%) the smoke gate now enforces.
- **`docs/features/harness-abstraction.md`**: the existing `claude -p` harness inside
  `sdk_client.py` that this PoC explicitly does NOT use. The cutover reconciles the two —
  the granite runner becomes the harness.
- No prior *failed* attempts at this cutover — it is greenfield-on-top-of-PoC. (The
  "Why Previous Fixes Failed" section is therefore omitted.)

## Research

External research (WebSearch, June 2026) surfaced two findings that materially reshape
the risk profile.

**Queries used:**
- "Claude Code CLI headless --print stream-json concurrent sessions Max subscription rate limit OAuth"
- "ollama concurrent requests parallel OLLAMA_NUM_PARALLEL single model serving multiple clients"

**Key findings:**

1. **[BILLING — TIME-CRITICAL] Subscription `claude -p` billing changes 2026-06-15.**
   Per Anthropic, *"Starting June 15, 2026, Agent SDK and `claude -p` usage on
   subscription plans will draw from a new monthly Agent SDK credit, separate from your
   interactive usage limits."* This directly undercuts the architecture's stated economic
   premise ("all Claude usage rides the Max subscription, zero per-request API cost").
   Two weeks from plan time, headless `claude -p` on the Max plan is metered against a
   *separate* credit pool. **The plan must not assume the OAuth path is free.** This is
   Open Question #1 and Risk #1. Source:
   https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code

2. **[CONCURRENCY] Server-side burst limiter on parallel Claude sessions.** Reports of
   bulk-spawning ~10 headless sessions back-to-back: the first 3–4 start, the rest fail
   with *"Server is temporarily limiting requests (not your usage limit) · Rate limited."*
   Production runs many concurrent sessions, and the granite runner spawns **two** `claude`
   subprocesses per logical session (PM + Dev) — doubling the process count against this
   limiter. Source:
   https://github.com/anthropics/claude-code/issues/53922

3. **[OLLAMA] Operator serialization.** `OLLAMA_NUM_PARALLEL` defaults to 1 (auto 4/1 by
   memory). A single granite operator instance serving all concurrent production sessions
   queues requests FIFO and becomes a throughput chokepoint. Mitigation levers:
   `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_QUEUE`, per-session operator instances. Source:
   https://docs.ollama.com/faq

## Data Flow

**Today (single substrate):**

1. **Entry point:** bridge (Telegram/email) or `valor-session` CLI enqueues an
   `AgentSession` to Redis.
2. **Worker:** `worker/__main__.py` pops the session, `agent/session_executor.py`
   assembles context (persona via `_resolve_compose_args`, steering from
   `queued_steering_messages`, prior UUID, `SDLC_*` env, task-list id).
3. **Execution:** `sdk_client.get_response_via_harness()` spawns one `claude -p
   stream-json` subprocess, parses the stream, accumulates tokens/cost, persists UUID +
   turn count + exit code, returns the result string.
4. **Output:** result flows back through `messenger.py` → `output_router.py` (nudge loop)
   → `OutputHandler` (`TelegramRelayOutputHandler` / `FileOutputHandler`) → bridge/user.

**After cutover (granite root runner):**

1–2 unchanged (enqueue + executor context assembly).
3. **Execution:** the executor calls the **granite runner** instead. For PM/Dev SDLC work
   the runner drives the dual-session operator loop; for single-session work (see Solution)
   it runs a degenerate one-session mode. The runner owns subprocess lifecycle, UUID
   capture/persist, token/cost accumulation, turn/stop tracking, and operator-event
   handling.
4. **Output:** the runner emits through the **same** `OutputHandler` protocol +
   `output_router.py` nudge loop — output routing is NOT replaced, only the execution core.

## Architectural Impact

- **New dependencies:** `ollama` + a resident `granite4.1:3b` (already installed and
  smoke-gated by `/update`) become a *hard runtime dependency for all session execution*.
  Today ollama is only the summarizer; after cutover, if ollama is down, no session runs.
- **Interface changes:** the executor's call into execution changes from
  `get_response_via_harness(...)` to a new runner entry point with an equivalent contract
  (message in, result + side-effects out) plus model-per-role parameters. The 10 importer
  sites (see Recon) migrate to the new surface.
- **Coupling:** increases coupling to ollama; decreases coupling to `claude-agent-sdk`
  (already absent from the PoC). The operator becomes a new central component.
- **Data ownership:** unchanged — `AgentSession` remains the source of truth for UUID,
  tokens, turn count, exit code; the runner writes the same fields via the same helpers
  (lifted out of `sdk_client.py` into a shared module rather than reimplemented).
- **Reversibility:** LOW once the old path is deleted. This is the central tension with
  NO-LEGACY-CODE — see Risks #4 and the staged-cutover approach in Solution.

## Appetite

**Size:** Large

**Team:** Solo dev (lead orchestrator), async-specialist (subprocess/concurrency),
test-engineer (validation gates + chaos), code-reviewer, documentarian, PM (scope +
the two human-judgment Open Questions).

**Interactions:**
- PM check-ins: 2-3 (the billing-premise decision, single-session design sign-off,
  cutover go/no-go)
- Review rounds: 2+ (parity review before cutover; cutover review)

This is a substrate replacement touching the worker's hot path. The cost is not coding
time but de-risking: parity with 12 responsibility clusters, validation gates, and an
irreversible deletion.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| ollama running | `curl -sf http://127.0.0.1:11434/api/tags >/dev/null` | Operator runtime |
| granite4.1:3b present | `ollama list \| grep -q granite4.1:3b` | Operator model |
| Claude OAuth logged in | `claude auth status \| grep -qi '"loggedIn": true'` | Max subscription path |
| Smoke gate passes | `python scripts/granite_smoke_test.py` | Operator dispatch ≤20% parse-error |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_root_session_runner.md`

## Solution

### Key Elements

- **`SessionRunner` (the root runner):** a single execution entry point the executor
  calls for every session, replacing `get_response_via_harness`. Internally it selects
  between **dual-session mode** (PM↔Dev operator loop) and **single-session mode**
  (one Claude session, operator used only for completion/operator-event detection). The
  mode is derived from the work shape, NOT exposed as a bypass toggle.
- **Parity layer:** the 12 production responsibility clusters `sdk_client.py` owns today,
  reimplemented on the runner: persona/system-prompt assembly + drift guards, hook
  injection (`build_hooks_config`), MCP/permission wiring, steering injection
  (`queued_steering_messages`), resume/UUID persistence, telemetry (tokens, cost,
  exit-code, context-usage, `SDLC_*` env), output routing via `OutputHandler`, turn/stop
  tracking, watchdog activity timestamps, circuit breaker, extended-thinking sentinel.
  Where a responsibility is generic (token accumulation, UUID persistence), it is **lifted
  into a shared module** both call during transition, not copy-pasted.
- **Model-per-role config:** operator/PM/Dev models resolved from config + heuristics at
  runner entry. A `default` triplet (`granite4.1:3b` / opus / sonnet) plus optional
  per-project / per-task overrides. There is deliberately **no** key that disables the
  runner.
- **Staged cutover (no permanent flag):** validation happens via the gates below *before*
  the switch; the flip to the runner and the deletion of the old execution path land
  together so no runtime bypass flag survives (resolves the NO-LEGACY-CODE vs.
  migration-safety tension).

### Flow

Bridge/CLI enqueues → Worker pops → Executor assembles context → **`SessionRunner.run()`**
→ (mode select) → drives 1 or 2 `claude -p` sessions under operator control → emits via
`OutputHandler` nudge loop → bridge/user.

### Technical Approach

- **Phase A — Parity build (behind the existing call site, not yet wired):** extract the
  generic side-effect helpers from `sdk_client.py` into a shared module; build the runner
  to call them; wire persona/hooks/MCP/steering/output into the operator loop. The runner
  must be a drop-in for `get_response_via_harness`'s contract.
- **Phase B — Validation gates (must pass before cutover):** the results-doc prerequisites
  — N≥10 varied-task runs (stable mean-turns/latency/parse-error), a ≥20-turn run
  exercising granite history truncation (`HISTORY_KEEP_LAST_N=8`), a real-subprocess chaos
  test (SIGKILL Dev mid-turn → `resume()`), and a concurrent-session test on Max OAuth
  (≥5 concurrent, watching for the burst limiter from Research #2).
- **Phase C — Single-session mode:** design + implement the degenerate path for
  conversational teammate/Telegram turns (Open Question #2). Likely "operator + one
  session," operator used only for `signal_done` / operator-event detection.
- **Phase D — Cutover + deletion:** rewire `session_executor.py` and the 10 importer sites
  to the runner; delete `get_response_via_harness` and its now-dead helpers; reconcile
  `harness-abstraction.md`.
- **Phase E — Model-per-role config:** config schema + heuristic resolution + tests.
- ollama tuning: set `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_QUEUE` appropriately or run
  per-session operator calls; verified under the Phase B concurrency test.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `sdk_client.py` and the PoC modules for `except Exception` blocks; each
  surviving handler in the runner must have a test asserting observable behavior (log,
  metric, or state change). The PoC's explicit-failure design (`GraniteRoutingError`,
  synthetic timeout/decode/broken_pipe events) must be preserved — no silent swallowing.
- [ ] Test the operator-down path: ollama unreachable → runner surfaces a clear failure
  to the session (Open Question #3 decides queue-vs-fail), never a silent hang.

### Empty/Invalid Input Handling
- [ ] Empty/whitespace task string → runner refuses with a clear error (mirror
  `scripts/granite_poc.py` arg guard).
- [ ] Empty Claude stream / immediate EOF → surfaced as `broken_pipe`/`decode_error`
  operator event, not a silent loop; assert the loop does not spin.

### Error State Rendering
- [ ] Operator-routing error, subprocess crash, and rate-limit (`Server is temporarily
  limiting requests`) each propagate a user-visible PM-persona message via the
  `OutputHandler`, not a raw stack trace (per `feedback_telegram_persona_always`).
- [ ] Verify the `claude --resume` recovery path renders a recovered state, not a fresh
  empty session, when a UUID was captured.

## Test Impact

- [ ] `tests/unit/test_claude_session.py` — UPDATE: extend for production env assembly
  (hooks, MCP, persona env) now that `ClaudeSession` carries production responsibilities.
- [ ] `tests/unit/test_granite_router.py` — UPDATE: add model-per-role parameterization
  cases.
- [ ] `tests/unit/test_granite_agent_loop.py` — REPLACE: rewrite for `SessionRunner` with
  both dual- and single-session modes and the lifted side-effect helpers.
- [ ] `tests/unit/granite_session_emulator.py` — UPDATE: emulator must produce the
  production output-routing + telemetry side effects the runner now triggers.
- [ ] Existing `sdk_client` execution tests (token accumulation, UUID persistence,
  stop-reason, turn count) — REPLACE: re-target at the lifted shared module / runner;
  delete only the bodies that tested the now-removed `get_response_via_harness` path.
- [ ] `tests/integration/test_claude_session_resume.py`,
  `tests/integration/test_granite_questions_game.py` — UPDATE: promote from gated PoC
  probes to part of the Phase B validation gate suite.
- [ ] `scripts/capture_persona_baseline.py` callers / persona snapshot tests — UPDATE: the
  persona loaders move with the parity layer; keep the baseline assertions pointed at the
  new home.

## Rabbit Holes

- **Rewriting the steering model now.** The granite doc flags `queued_steering_messages`
  for redesign. Do the *minimum* to preserve steering through the runner; a full steering
  redesign is a separate project.
- **Perfecting model-per-role heuristics.** Ship a config surface + a trivial default
  resolver. Sophisticated complexity-based model routing is a follow-up, not this cutover.
- **Generalizing beyond PM/Dev/teammate.** Don't invent new session topologies; match
  exactly what production runs today.
- **Chasing the ollama multi-GPU / batching frontier.** Tune the few env vars the
  concurrency gate proves necessary; stop there.

## Risks

### Risk 1: The economic premise expires 2026-06-15
**Impact:** The architecture exists largely to ride the Max subscription instead of
per-request API billing. After June 15, headless `claude -p` on subscription draws from a
separate, finite monthly Agent SDK credit. The cutover could trade API-key billing for a
*more constrained* credit pool, and a dual-session loop burns ~2× the headless usage of
the current single-subprocess path.
**Mitigation:** Resolve Open Question #1 before Phase D. Measure headless credit
consumption under the Phase B runs; if the new credit pool is more limiting than current
billing, the go/no-go decision changes. Do not delete the old path until this is settled.

### Risk 2: Server-side concurrency limiter throttles production
**Impact:** Doubling `claude` subprocess count per session (PM + Dev) against a burst
limiter that already throttles after ~3–4 concurrent sessions could starve real
production traffic.
**Mitigation:** Phase B concurrency gate (≥5 concurrent dual sessions) is a hard
prerequisite. If throttled, single-session mode for non-SDLC work materially reduces
process count; consider operator-mediated session pooling.

### Risk 3: ollama operator becomes a throughput chokepoint
**Impact:** One granite instance serializing all routing decisions adds queueing latency
under concurrent load.
**Mitigation:** Tune `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_QUEUE`; measure operator latency
under the concurrency gate; operator calls are ~1s so headroom exists, but verify.

### Risk 4: Irreversible cutover (NO-LEGACY-CODE vs. safety)
**Impact:** Deleting `get_response_via_harness` with no fallback means a latent runner bug
takes down all session execution with no quick revert.
**Mitigation:** Validation gates (Phase B) gate the switch; the flip + deletion land in a
reviewed PR; git revert of that single PR is the rollback. No permanent runtime bypass
flag (which the directive forbids) is introduced.

### Risk 5: Parity gaps in the 12 clusters
**Impact:** A missed responsibility (e.g., watchdog activity timestamps, circuit breaker)
silently degrades observability or resilience after cutover.
**Mitigation:** Parity checklist in Success Criteria; each cluster has a test before
Phase D; code-reviewer signs off on parity completeness.

## Race Conditions

### Race 1: UUID capture vs. crash-resume
**Location:** `agent/claude_session.py` (`_capture_session_id`, `resume()`).
**Trigger:** subprocess crashes before the `system/init` event yields a `session_id`.
**Data prerequisite:** a captured `session_id` must exist before `resume()` can preserve
context; otherwise it falls back to a fresh session.
**Mitigation:** existing `_scan_stderr_for_session_id()` fallback; the runner must persist
the UUID to `AgentSession` as soon as captured so a worker restart mid-session can resume.

### Race 2: Concurrent sessions sharing one operator
**Location:** `agent/granite_router.py` (ollama.chat calls).
**Trigger:** multiple worker sessions issue routing calls to one granite instance
simultaneously.
**Data prerequisite:** each routing call must carry its own history; no shared mutable
history across sessions.
**Mitigation:** runner instantiates per-session router state; ollama FIFO-queues at the
server; verified under the Phase B concurrency gate.

### Race 3: Steering injection at turn boundary
**Location:** executor steering read → runner turn input.
**Trigger:** a steering message arrives while a turn is mid-flight.
**State prerequisite:** steering must be injected at a turn boundary, not mid-stream.
**Mitigation:** preserve the executor's existing turn-boundary injection semantics; the
runner reads `queued_steering_messages` between operator turns.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1542] Full redesign of the `queued_steering_messages` steering model —
  this cutover preserves steering through the runner with minimal change; the redesign the
  granite doc anticipates is tracked under the parent cutover issue and will get its own
  slug once the runner lands.
- [ORDERED] Production deploy of the cutover across all bridge machines via `/update` +
  `/do-deploy` — blocked on the human go/no-go gate after Phase B validation and the
  Open Question #1 billing decision.
- [EXTERNAL] Confirming the post-2026-06-15 subscription Agent-SDK-credit economics — needs
  a human to read Anthropic's billing terms / observe the actual credit meter; the agent
  cannot determine pricing policy.

## Update System

- The granite runner makes `ollama` + `granite4.1:3b` a **hard dependency for session
  execution**, not just summarization. `/update` already pulls/smoke-tests an ollama model
  and the granite smoke gate exists — extend `scripts/update/` to (a) assert
  `granite4.1:3b` present and the smoke gate passes as a **blocking** verification step
  (today it is informational), and (b) set the chosen `OLLAMA_NUM_PARALLEL`/
  `OLLAMA_MAX_QUEUE` env for the worker service.
- The worker launchd plist may need the ollama env vars; reinstall via
  `./scripts/install_worker.sh` on cutover.
- No new secrets. Document the new hard dependency in the update skill so a machine without
  granite fails the green-light gate rather than crash-looping the worker.

## Agent Integration

- This is a **bridge/worker-internal** change to how sessions execute; it is not a new
  agent-invocable tool. No new MCP server, no `.mcp.json` change, no new
  `pyproject.toml [project.scripts]` entry.
- The bridge does not call the runner directly — it continues to enqueue `AgentSession`
  records; the worker is the sole executor and the only caller of the runner.
- Integration tests verify the agent's *existing* surfaces still work end-to-end through
  the new runner: a Telegram-origin PM session, an email-origin teammate session, and a
  `valor-session create` dev session each execute and deliver output via the runner.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-agent-loop.md`: remove "PoC / not wired into
  production" framing; it becomes the substrate doc. Document the runner, dual vs.
  single-session modes, and model-per-role config.
- [ ] Reconcile `docs/features/harness-abstraction.md` and
  `docs/features/pm-dev-session-architecture.md` with the new substrate.
- [ ] Update `docs/features/README.md` index row.
- [ ] Create `docs/infra/granite_root_session_runner.md` (new hard dependency, ollama
  tuning, rollback).

### Inline Documentation
- [ ] Docstrings on the `SessionRunner` entry point and the lifted shared side-effect
  module documenting the parity contract with the old `get_response_via_harness`.

## Success Criteria

- [ ] Every production session (Telegram, email, `valor-session` CLI; pm/dev/teammate)
  executes through the granite runner; no `session_type`/config value routes around it
  (grep confirms `get_response_via_harness` has no remaining callers).
- [ ] `get_response_via_harness` and its dead helpers are deleted (NO-LEGACY-CODE); no
  fallback flag exists.
- [ ] Operator/PM/Dev models are each config/heuristic-selectable, with no setting that
  disables the runner.
- [ ] All 12 parity clusters have a working equivalent + a test (persona+drift guards,
  hooks, MCP/permissions, steering, resume/UUID, telemetry, output routing, turn/stop,
  watchdog activity, circuit breaker, thinking sentinel).
- [ ] Phase B gates pass: N≥10 varied-task runs, ≥20-turn truncation run, real-subprocess
  chaos test, ≥5-concurrent Max-OAuth test (no production-starving throttle).
- [ ] Single-session (conversational) mode specified, implemented, and tested.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (parity layer)** — Name: `parity-builder` — Role: lift shared side-effect
  helpers + reimplement the 12 clusters on the runner — Agent Type: builder — Resume: true
- **Builder (runner core)** — Name: `runner-builder` — Role: `SessionRunner` entry point,
  dual/single mode select, model-per-role config — Agent Type: async-specialist —
  Resume: true
- **Test engineer (validation gates)** — Name: `gate-engineer` — Role: Phase B gates
  (N≥10, 20-turn, chaos, concurrency) — Agent Type: test-engineer — Resume: true
- **Builder (cutover)** — Name: `cutover-builder` — Role: rewire executor + 10 importer
  sites, delete old path — Agent Type: builder — Resume: true
- **Validator** — Name: `parity-validator` — Role: verify parity completeness + no
  remaining callers of the old path — Agent Type: validator — Resume: true
- **Documentarian** — Name: `docs-writer` — Role: feature + infra docs — Agent Type:
  documentarian — Resume: true

### Step by Step Tasks

### 1. Lift shared side-effect helpers
- **Task ID**: build-shared-helpers
- **Depends On**: none
- **Validates**: tests/unit/test_session_side_effects.py (create)
- **Assigned To**: parity-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract token/cost accumulation, UUID persistence, turn/stop tracking, exit-code
  persistence, `SDLC_*` env extraction, watchdog activity timestamps from `sdk_client.py`
  into a shared module both the old path and the runner call.

### 2. Build the runner core + model-per-role config
- **Task ID**: build-runner
- **Depends On**: build-shared-helpers
- **Validates**: tests/unit/test_granite_agent_loop.py (replace), tests/unit/test_granite_router.py
- **Assigned To**: runner-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- `SessionRunner.run()` entry point with the `get_response_via_harness` contract; mode
  select; operator/PM/Dev model resolution from config/heuristics with a default triplet.

### 3. Wire parity layer into the runner
- **Task ID**: build-parity
- **Depends On**: build-runner
- **Validates**: tests/unit/test_claude_session.py, tests/unit/granite_session_emulator.py
- **Assigned To**: parity-builder
- **Agent Type**: builder
- **Parallel**: false
- Persona+drift guards, hooks, MCP/permissions, steering injection, output routing via
  `OutputHandler`, circuit breaker, thinking sentinel.

### 4. Single-session mode
- **Task ID**: build-single-session
- **Depends On**: build-parity
- **Informed By**: Open Question #2 resolution
- **Assigned To**: runner-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Degenerate one-session path for conversational teammate/Telegram turns.

### 5. Phase B validation gates
- **Task ID**: build-gates
- **Depends On**: build-parity
- **Validates**: tests/integration/test_granite_questions_game.py, tests/integration/test_claude_session_resume.py, new concurrency + truncation + chaos tests
- **Assigned To**: gate-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- N≥10 runs, ≥20-turn truncation, SIGKILL chaos, ≥5-concurrent Max-OAuth.

### 6. Cutover + delete old path
- **Task ID**: build-cutover
- **Depends On**: build-single-session, build-gates
- **Validates**: full suite; grep shows no `get_response_via_harness` callers
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewire executor + 10 importers; delete `get_response_via_harness` + dead helpers;
  reconcile harness-abstraction doc.

### 7. Parity + cutover validation
- **Task ID**: validate-cutover
- **Depends On**: build-cutover
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify 12 parity clusters covered, no bypass path, no remaining old-path callers.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: build-cutover
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Feature doc, infra doc, README index, harness/pm-dev reconciliation.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-cutover, document-feature
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm success criteria + docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Old path deleted | `grep -rn "def get_response_via_harness" agent/` | exit code 1 |
| No old-path callers | `grep -rn "get_response_via_harness" agent/ worker/ bridge/ monitoring/ scripts/` | exit code 1 |
| Smoke gate passes | `python scripts/granite_smoke_test.py` | output contains "Parse error rate:   0" |
| No bypass flag | `grep -rni "use_granite\|granite_enabled\|legacy_runner\|use_sdk_client" agent/ config/` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **[BILLING — decide before Phase D] Does the 2026-06-15 subscription billing change
   invalidate the economic premise?** Headless `claude -p` on subscription will draw from
   a separate monthly Agent SDK credit. A dual-session runner uses ~2× headless usage. Is
   the new credit pool still cheaper/preferable to the current API-key billing, given
   production volume? This may change the go/no-go for the whole cutover.
2. **How should a single conversational turn map onto a dual-session runner?** The directive
   forbids bypassing the runner, but a teammate email reply has no Dev to drive. Confirm
   the "operator + one session" degenerate mode is acceptable, vs. some other topology.
3. **Operator-down failure behavior.** If ollama/granite is unavailable, should sessions
   queue and wait, or hard-fail with a user-visible message? (No fallback to the old path
   is permitted per the directive.)
4. **Model-per-role config granularity.** Per-project, per-task-complexity, or a single
   global triplet to start? (Affects the config schema in Phase E.)
