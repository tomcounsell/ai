---
status: Cancelled
type: feature
appetite: Large
owner: Valor
created: 2026-06-01
tracking: https://github.com/tomcounsell/ai/issues/1542
last_comment_id:
revision_applied: true
---

# Granite Root Session Runner — Production Cutover

> **CANCELLED (2026-06-02).** This plan and its tracking issue #1542 are closed. The
> cutover rested on the PR #1487 PoC, which drives Claude via headless `claude -p` and
> *deliberately avoided* driving the real interactive Claude Code session (TUI) — which was
> the entire point. A `-p` harness driven by granite is not meaningfully different from the
> existing `sdk_client.py` harness. Superseded by a new PoC issue validating a local
> operator driving a **real interactive Claude Code session via PTY** (no `claude -p`),
> with PM/Dev/teammate personas primed via a first-message slash command, under Max OAuth.

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

## Three-LLM Role Taxonomy

The architecture runs **three distinct LLMs** with non-overlapping jobs (aligned with the
original goals in issue #1486). Naming them consistently everywhere — code, docs, dashboard
— is a hard requirement of this plan, because the PoC's loose "PM/Dev/operator" language has
been a source of confusion.

| Role | LLM | What it does | Resumable? |
|---|---|---|---|
| **Operator** | `granite4.1:3b` (local, via ollama) | Routes between the two Claude sessions — "always in the middle." Translates driver→worker prompts and worker→driver summaries; handles operator events (multiple-choice, timeout, crash). | **No** — stateless ollama calls; history lives in-process. There is no `/resume` for the operator. |
| **Driver** (= PM) | **Opus**, a Claude Code session | The directing "human": plans, reviews, decides what the worker should do next. Drives, does not implement. | Yes — `claude --resume <uuid>` |
| **Worker** (= Dev) | **Sonnet**, a Claude Code session | Does the actual implementation work the driver directs. | Yes — `claude --resume <uuid>` |

- Vocabulary contract: **driver ≡ PM/Opus**, **worker ≡ Dev/Sonnet**, **operator ≡ granite**.
  These three terms are used consistently in the runner, the docs, and the dashboard.
- The **operator is not a Claude Code session** — it is a local routing model. Only the
  driver and worker have resumable Claude session UUIDs.
- **One logical `AgentSession` → two Claude subprocesses (driver + worker).** This differs
  from today's model, where a PM `AgentSession` spawns a *separate* Dev `AgentSession` via
  the `valor_session` CLI. Under the granite loop both Claude sessions are subprocesses of a
  single `GraniteAgentLoop`/`AgentSession`. **Consequence:** the `AgentSession` record must
  store **two** Claude session UUIDs (driver + worker), not the single
  `claude_session_uuid` it has today — see *Dashboard & data model* in Solution.

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
  (message in, result + side-effects out) plus model-per-role parameters. **Migration
  surface (verified by grep at plan time, baseline `70198200`):** 4 runtime callers of
  `get_response_via_harness` — `agent/session_executor.py`, `agent/session_completion.py`,
  `monitoring/session_watchdog.py`, `worker/idle_sweeper.py` — plus the **`agent/__init__.py`
  re-export** (lines 36 & 52) that must be removed; 13 modules import `agent.sdk_client`
  more broadly; and **19 test files** reference `get_response_via_harness` (enumerated in
  Test Impact) and must each be re-targeted or deleted.
- **Coupling:** increases coupling to ollama; decreases coupling to `claude-agent-sdk`
  (already absent from the PoC). The operator becomes a new central component.
- **Data ownership:** `AgentSession` remains the source of truth for tokens, turn count,
  and exit code (same helpers, lifted into a shared module). One change: it now stores
  **two** Claude session UUIDs (driver + worker) instead of the single `claude_session_uuid`,
  because one logical session runs two Claude subprocesses (see Three-LLM Role Taxonomy).
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

### Turn-loop ownership, steering & per-turn output (resolves BLOCKER: impedance mismatch)

Today `get_response_via_harness()` is a **single-turn primitive**: the executor owns the
outer session loop (`session_executor.py` reads `queued_steering_messages`, calls the
harness once at `:1703`, delivers one result, then loops for the next inbound/steering
message). The granite loop is a **multi-turn orchestrator** that owns its own
`for turn in range(1, max_turns+1)` PM↔Dev routing loop (`granite_agent_loop.py:209`) and
today emits only a final JSONL trace. These are two different loops; the cutover must
reconcile them, not pretend the runner is a per-turn drop-in.

**Decision — topology (b), scoped:** `SessionRunner.run()` owns the **inner operator loop**
(PM↔Dev routing for one inbound message → completion); the executor keeps the **outer
session loop** (cross-message conversation, session lifecycle). Concretely:

- The runner is **not** a per-turn primitive. The executor calls `SessionRunner.run(message,
  ...)` once per inbound message; the runner internally drives the operator loop to
  `signal_done`/completion and returns the final payload — exactly where the executor's
  outer loop resumes for the next message/steering.
- **Steering:** the runner re-reads `queued_steering_messages` at each operator turn
  boundary (between PM↔Dev hops), so a steering message injected mid-orchestration is
  honored without waiting for the whole task to finish; the executor's existing pre-call
  steering read is preserved for between-message steering. (See updated Race 3.)
- **Per-turn output:** the runner emits each PM-facing delta through the `OutputHandler` as
  it is produced — not only the final payload. The PoC's JSONL-only trace is insufficient
  for production; preserving today's per-turn delivery + nudge-loop behavior requires the
  runner to push deltas, not batch.
- The PoC's internal loop and final-only trace are therefore **extended** (turn-boundary
  steering reads + per-turn `OutputHandler` emission), not reused as-is. This is build
  task 3 (parity) scope.

### Dashboard & data model — two Claude sessions per AgentSession

Today `AgentSession.claude_session_uuid` (`models/agent_session.py:198`) holds a *single*
Claude UUID, and the session modal renders one `/resume <uuid>` copy chip
(`ui/templates/_partials/session_modal_content.html:63`; the `copyResumeCommand` JS at
`:250`). The granite loop runs **two** Claude subprocesses, so the model and the modal both
change:

- **Data model:** `AgentSession` gains two nullable UUID fields — one for the **worker**
  (Dev/Sonnet) and one for the **driver** (PM/Opus) — each populated by the runner as the
  corresponding `ClaudeSession` captures its `session_id`. Adding nullable fields needs no
  extra back-compat code — `_heal_descriptor_pollution` walks fields generically
  ([[feedback_field_backcompat_heal]], issues #1099/#1172). The legacy single
  `claude_session_uuid` is migrated to the worker field and retired (NO-LEGACY-CODE), not
  kept in parallel.
- **Modal (per the requested UX):** render **two** resume chips — the **worker (Dev)
  featured as the primary/main resume** (the session you'd most want to jump into), with the
  **driver (PM) as a clearly-labeled secondary chip below it**. Each chip carries its own
  UUID; `copyResumeCommand` already copies `/resume <uuid>` from `data-uuid`, so it
  generalizes to two chips with distinct `data-uuid`s. Label them "worker" / "driver" so the
  operator's absence of a resume is not mistaken for a missing session.
- **Degenerate cases:** single-session (conversational) mode has only a worker session → one
  chip, as today. Legacy/pre-cutover rows with only `claude_session_uuid` → one chip.
- The **operator (granite) has no resume chip** — it is not a Claude session; surface its
  liveness elsewhere (e.g. an operator-latency line in Timing & Liveness) rather than a
  resume command.

### Technical Approach

- **Phase A — Parity build (behind the existing call site, not yet wired):** extract the
  generic side-effect helpers from `sdk_client.py` into a shared module; build the runner
  to call them; wire persona/hooks/MCP/steering/output into the operator loop. The runner
  matches `get_response_via_harness`'s *result + side-effect* contract but at a coarser
  granularity (one inbound-message-to-completion, not one turn) — see "Turn-loop
  ownership" above; the executor call site changes from per-turn to per-message.
- **Phase B — Validation gates (must pass before cutover):** the results-doc prerequisites
  — N≥10 varied-task runs (stable mean-turns/latency/parse-error), a ≥20-turn run
  exercising granite history truncation (`HISTORY_KEEP_LAST_N=8`), a real-subprocess chaos
  test (SIGKILL Dev mid-turn → `resume()`), and a concurrency test on Max OAuth at
  **production-representative load, counting `claude` subprocesses (2 per dual session)**,
  asserting the burst-limiter error is caught + surfaced retriably. This phase also
  **measures headless `claude -p` credit consumption per session** so the billing go/no-go
  (Open Question #1) is data-driven, not speculative.
- **Phase C — Single-session mode (decided design):** one `claude -p` session + operator,
  for conversational teammate/Telegram turns. The PoC starts BOTH pm+dev unconditionally
  (`granite_agent_loop.py:141-173`), so this is net-new topology, not a trivial flag. **The
  lone session has no PM "TASK COMPLETE" phrase** (the PoC's completion signal,
  `:300`), so completion is detected by the operator inspecting the single session's
  `result` event for end-of-turn semantics (the stream-json `result`/`success` event is
  the natural boundary), with operator tools reduced to `probe_session` +
  `signal_done` (no `extract_dev_prompt`/`summarize_for_pm` — there is no second session to
  route to). Latency cost of routing a conversational reply through the operator is an
  accepted tradeoff measured in Phase B; the directive forbids bypassing the runner.
- **Phase D — Cutover + deletion:** rewire the 4 runtime callers (`session_executor.py`,
  `session_completion.py`, `session_watchdog.py`, `idle_sweeper.py`), remove the
  `agent/__init__.py` re-export, and re-target/delete all 19 referencing test files; delete
  `get_response_via_harness` and its now-dead helpers; reconcile `harness-abstraction.md`.
- **Phase E — Model-per-role config:** config schema + heuristic resolution + tests.
- **Phase F — Dashboard & data model:** add the two nullable Claude-UUID fields to
  `AgentSession` (driver + worker), have the runner persist each, migrate/retire the legacy
  `claude_session_uuid`, and update the session modal to render two resume chips (worker
  primary, driver secondary). See *Dashboard & data model* above.
- ollama tuning: set `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_QUEUE` appropriately or run
  per-session operator calls; verified under the Phase B concurrency test.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `sdk_client.py` and the PoC modules for `except Exception` blocks; each
  surviving handler in the runner must have a test asserting observable behavior (log,
  metric, or state change). The PoC's explicit-failure design (`GraniteRoutingError`,
  synthetic timeout/decode/broken_pipe events) must be preserved — no silent swallowing.
- [ ] Test the operator-down path (decided behavior): ollama unreachable → runner
  **re-enqueues the `AgentSession` with bounded retries (e.g. 3, backoff)**; on exhaustion
  it marks the session failed and surfaces a user-visible PM-persona message via
  `OutputHandler` (per `feedback_telegram_persona_always`). Never a silent hang, never a
  fallback to the old path (forbidden by the directive). Assert both the retry and the
  terminal-failure rendering.

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
- [ ] `tests/integration/test_claude_session_resume.py`,
  `tests/integration/test_granite_questions_game.py` — UPDATE: promote from gated PoC
  probes to part of the Phase B validation gate suite.
- [ ] `scripts/capture_persona_baseline.py` callers / persona snapshot tests — UPDATE: the
  persona loaders move with the parity layer; keep the baseline assertions pointed at the
  new home.

**Full `get_response_via_harness` test surface (19 files — verified by grep at baseline
`70198200`).** The cutover grep gate excludes `tests/`, so each of these must be explicitly
re-targeted (at the lifted shared module / runner) or deleted; none can be left dangling:
- [ ] `tests/unit/test_sdk_client.py` — REPLACE: re-target side-effect assertions at the shared module; delete bodies testing the removed harness entry point.
- [ ] `tests/unit/test_sdk_client_harness_counters.py` — REPLACE: turn/stop counters now live in the shared module.
- [ ] `tests/unit/test_sdk_client_image_sentinel.py` — REPLACE: image fallback moves into the runner subprocess path.
- [ ] `tests/unit/test_harness_model_coverage.py` — REPLACE: assert model-per-role resolution instead of single-model harness arg.
- [ ] `tests/unit/test_harness_streaming.py` — REPLACE: stream parsing now in `ClaudeSession`.
- [ ] `tests/unit/test_harness_token_capture.py` — REPLACE: token capture via shared module.
- [ ] `tests/unit/test_harness_thinking_block_sentinel.py` — REPLACE: sentinel moves into the runner.
- [ ] `tests/unit/test_harness_retry.py` — REPLACE: retry/circuit-breaker behavior in the runner.
- [ ] `tests/unit/test_completion_runner_two_pass.py` — UPDATE: completion now driven by operator `signal_done`, not the two-pass harness path.
- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE: per-turn `OutputHandler` delivery now emitted by the runner.
- [ ] `tests/unit/test_session_completion.py` — UPDATE: `session_completion.py` caller migrates to the runner.
- [ ] `tests/unit/test_session_model_routing.py` — UPDATE: model routing now via model-per-role config.
- [ ] `tests/integration/test_harness_env_pm_injection.py` — UPDATE: PM env injection now assembled by the parity layer.
- [ ] `tests/integration/test_harness_no_op_contract.py` — UPDATE: re-express the no-op contract against the runner.
- [ ] `tests/integration/test_harness_resume.py` — UPDATE: resume now via `ClaudeSession.resume()`.
- [ ] `tests/integration/test_pm_final_delivery.py` — UPDATE: final delivery now from the runner's completion payload.
- [ ] `tests/integration/test_session_finalization_decoupled.py` — UPDATE: finalization path migrates.
- [ ] `tests/integration/test_session_spawning.py` — UPDATE: spawning now instantiates `SessionRunner`.
- [ ] `tests/e2e/conftest.py` — UPDATE: e2e fixtures that stub `get_response_via_harness` must stub the runner entry point instead.
- [ ] `agent/__init__.py` (not a test, but in the deletion surface) — UPDATE: remove the `get_response_via_harness` re-export (lines 36 & 52) so the symbol deletion does not break imports.

**Dashboard & data-model tests:**
- [ ] `models/` AgentSession tests covering UUID persistence — UPDATE: assert both the driver and worker UUID fields persist; assert the legacy `claude_session_uuid` migrates to the worker field.
- [ ] Dashboard modal rendering tests (whatever currently exercises `session_modal_content.html` / `ui/app.py` session detail) — UPDATE: assert two resume chips render with distinct `data-uuid`s for a granite dual-session, worker chip primary; one chip for single-session/legacy rows. Add a test if none exists (greenfield for dual-chip rendering).

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
**Mitigation:** Phase B concurrency gate is a hard prerequisite and must count
**`claude` subprocesses, not logical sessions** — a dual-mode session spawns 2 processes,
so N concurrent logical sessions = 2N processes against the ~3–4-session burst limiter.
The gate runs at **production-representative concurrency** (derive from peak concurrent
`AgentSession` count on the dashboard, not an arbitrary ≥5), and must assert that the
burst-limiter error string (`Server is temporarily limiting requests`) is caught and
surfaced as a **retriable PM-persona message**, not a hard session failure. Single-session
mode (which halves process count for non-SDLC work) is the primary lever and is coupled to
this gate's outcome.

### Risk 3: ollama operator becomes a throughput chokepoint
**Impact:** One granite instance serializing all routing decisions adds queueing latency
under concurrent load.
**Mitigation:** The Phase B concurrency gate reports operator p50/p99 latency under load;
`OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_QUEUE` (and per-session operator instances if needed) are
tuned **from that measurement**, not pre-judged.

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

### Race 3: Steering injection at operator turn boundary
**Location:** `SessionRunner` operator loop ↔ `queued_steering_messages`.
**Trigger:** a steering message arrives while the operator is mid-orchestration (between
PM↔Dev hops) or mid-stream within a hop.
**State prerequisite:** steering must be injected at an operator turn boundary, not
mid-stream into a live subprocess.
**Mitigation:** per the Turn-loop ownership decision, the runner re-reads
`queued_steering_messages` at each operator turn boundary and injects before the next hop;
the executor's pre-call read handles between-message steering. Steering is never written
mid-stream to a subprocess stdin.

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
  `docs/features/pm-dev-session-architecture.md` with the new substrate, using the
  driver/worker/operator vocabulary from the Three-LLM Role Taxonomy.
- [ ] Document the dashboard change (two resume chips per granite session, worker primary)
  in the relevant dashboard/UI doc; note the operator has no resume.
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
- [ ] `AgentSession` persists both driver and worker Claude UUIDs; the legacy single
  `claude_session_uuid` is migrated and retired.
- [ ] The session modal renders two resume chips for a granite dual-session — worker (Dev)
  primary, driver (PM) secondary — and one chip for single-session/legacy rows; the operator
  has no resume chip.
- [ ] driver/worker/operator vocabulary is used consistently across the runner code, the
  reconciled docs, and the dashboard (no leftover ambiguous "PM session = the worker"
  phrasing).
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
- **Builder (cutover)** — Name: `cutover-builder` — Role: rewire the 4 runtime callers +
  `agent/__init__.py` re-export + 19 test files, delete old path — Agent Type: builder —
  Resume: true
- **Builder (dashboard & data model)** — Name: `dashboard-builder` — Role: add the two
  Claude-UUID fields to `AgentSession`; update the session modal to render two resume chips
  (worker primary, driver secondary) — Agent Type: builder — Resume: true
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
- Add two nullable Claude-UUID fields to `AgentSession` (driver + worker); the runner
  persists each as its `ClaudeSession` captures `session_id`. Migrate the legacy
  `claude_session_uuid` to the worker field (NO-LEGACY-CODE).

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
- **Informed By**: Phase C decided design (operator + one session; completion via
  `result`-event detection; tools reduced to `probe_session`/`signal_done`)
- **Assigned To**: runner-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- One-session path for conversational teammate/Telegram turns; operator detects completion
  from the lone session's stream-json `result` event (no PM "TASK COMPLETE" phrase exists).

### 5. Phase B validation gates
- **Task ID**: build-gates
- **Depends On**: build-parity
- **Validates**: tests/integration/test_granite_questions_game.py, tests/integration/test_claude_session_resume.py, new concurrency + truncation + chaos tests
- **Assigned To**: gate-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- N≥10 runs, ≥20-turn truncation, SIGKILL chaos, concurrency at production-representative
  load counting `claude` subprocesses (2 per dual session) + assert burst-limiter error is
  caught/retried.
- **Measure headless `claude -p` credit consumption per session** (feeds the human billing
  go/no-go, Open Question #1 — the agent cannot read Anthropic pricing policy, so it
  supplies the consumption data, not the verdict).

### 6. Dashboard & data model (dual resume)
- **Task ID**: build-dashboard
- **Depends On**: build-runner
- **Validates**: AgentSession UUID-persistence tests; session-modal rendering tests
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true
- Render two resume chips in `ui/templates/_partials/session_modal_content.html` — worker
  (Dev) primary, driver (PM) secondary, each with its own `data-uuid`; one chip for
  single-session/legacy rows; no chip for the operator. Confirm `copyResumeCommand`
  generalizes to two chips.

### 7. Cutover + delete old path
- **Task ID**: build-cutover
- **Depends On**: build-single-session, build-gates
- **Gated By (HUMAN)**: Open Question #1 — the billing go/no-go decision (informed by the
  task-5 credit measurement) MUST be answered "go" before this task starts. No code in
  tasks 1-5 is wasted if the answer is "no-go" (they leave the old path intact); this task
  is the irreversible point.
- **Validates**: full suite (incl. all 19 re-targeted test files); grep shows no
  `get_response_via_harness` references anywhere (incl. `tests/` and `agent/__init__.py`)
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewire the 4 runtime callers (`session_executor.py`, `session_completion.py`,
  `session_watchdog.py`, `idle_sweeper.py`); remove the `agent/__init__.py` re-export;
  re-target/delete all 19 test files (Test Impact); delete `get_response_via_harness` +
  dead helpers; reconcile harness-abstraction doc.

### 8. Parity + cutover validation
- **Task ID**: validate-cutover
- **Depends On**: build-cutover
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify 12 parity clusters covered, no bypass path, no remaining old-path callers.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-cutover, build-dashboard
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Feature doc, infra doc, README index, harness/pm-dev reconciliation.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-cutover, build-dashboard, document-feature
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
| No old-path refs anywhere | `grep -rn "get_response_via_harness" agent/ worker/ bridge/ monitoring/ scripts/ tests/ ui/` | exit code 1 |
| Smoke gate ≤20% (kill criterion) | `python scripts/granite_smoke_test.py && python -c "import json;assert json.load(open('logs/granite_smoke_results.json'))['parse_error_rate']<=0.20"` | exit code 0 |
| Modal renders two resume chips | `grep -c "copyResumeCommand" ui/templates/_partials/session_modal_content.html` | output > 1 |
| AgentSession has dual UUID fields | `grep -cE "driver.*_uuid|worker.*_uuid" models/agent_session.py` | output > 1 |
| No bypass flag | `grep -rni "use_granite\|granite_enabled\|legacy_runner\|use_sdk_client" agent/ config/` | exit code 1 |

## Critique Results

**Verdict: NEEDS REVISION** — 2 BLOCKERs (turn-loop ownership impedance mismatch; under-counted migration surface) must be resolved before build. Structural validators all PASS.

**Revision applied (all 8 findings addressed):** BLOCKER-1 → new Solution subsection "Turn-loop ownership, steering & per-turn output" (runner owns inner operator loop, executor keeps outer; turn-boundary steering re-reads; per-turn `OutputHandler` emission) + Race 3 + Phase A reworded. BLOCKER-2 → Architectural Impact + Test Impact corrected to the grep-verified surface (4 runtime callers, `agent/__init__.py` re-export, 13 module importers, 19 test files enumerated) + Task 6 + Verification grep now include `tests/`. CONCERN single-session → Phase C decided design (operator+one-session, `result`-event completion). CONCERN operator-down → decided (bounded re-enqueue → fail with PM-persona message). CONCERN billing → explicit HUMAN gate on Task 6 + credit measurement in Task 5. CONCERN concurrency → gate counts subprocesses at production-representative load + asserts burst-limiter error caught. NIT smoke-gate → Verification parses `parse_error_rate <= 0.20`. NIT Risk-3 → pre-judged "headroom exists" dropped. Residual human Open Questions: #1 (billing go/no-go, external) and #4 (model-per-role granularity).

**Second revision pass (post-review feedback, #1542):** Added a **Three-LLM Role Taxonomy** (operator=granite / driver=PM-Opus / worker=Dev-Sonnet) aligned with issue #1486 to fix role ambiguity, and added **Dashboard & data model** scope that the first draft omitted — `AgentSession` now stores two Claude UUIDs (driver + worker), and the session modal renders two resume chips (worker primary, driver secondary; operator has no resume). Wired through Technical Approach (Phase F), Test Impact, Documentation, Team, Tasks (new build-dashboard), Success Criteria, and Verification.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | **Turn-loop ownership impedance mismatch.** `get_response_via_harness()` is a *single-turn primitive* called inside the executor's own turn loop (`session_executor.py:1484` reads `queued_steering_messages`, then calls the harness once per turn at `:1703`, returning one result string). The granite loop (`granite_agent_loop.py:209` `for turn in range(1, max_turns+1)`) is a *multi-turn orchestrator* that owns its own loop up to `max_turns`. The plan calls `SessionRunner.run()` "a drop-in for `get_response_via_harness`'s contract" but never says where the turn loop lives after cutover. If the runner keeps its internal loop, the executor's per-turn steering injection (Race 3) and per-turn output delivery break; if it collapses to single-turn, the operator/PM↔Dev orchestration is gone. | ADDRESSED — new Solution subsection "Turn-loop ownership, steering & per-turn output" + Race 3 + Phase A | Decide and document the loop topology: either (a) `SessionRunner.run()` returns after ONE operator turn and the executor keeps driving the outer loop + steering reads (preserves `session_executor.py:1484` steering semantics), or (b) the runner owns the full multi-turn loop and must internally re-read `queued_steering_messages` at each turn boundary and emit each turn's delta through `OutputHandler` (not just the final payload). The PoC does neither — it loops internally AND only emits a JSONL trace at the end. |
| BLOCKER | Archaeologist, Operator | **Migration surface is under-counted; "10 importer sites" is wrong.** Grep shows `get_response_via_harness` has runtime callers the plan lists, but `sdk_client.py` itself has ~16 importing modules and ~20 test files call `get_response_via_harness` directly. The plan's Test Impact enumerates only ~7 items and folds all harness tests into one "REPLACE" line. Deleting the symbol breaks `agent/__init__.py` (re-export), `tests/unit/test_sdk_client.py`, `test_harness_*` (10+ files), and `tests/integration/test_harness_*` / `test_session_*`. | Test Impact (UPDATE) + Cutover task 6 | The cutover grep gate (`grep -rn "get_response_via_harness" agent/ worker/ bridge/ monitoring/ scripts/`) intentionally omits `tests/`, so deletion will leave ~20 red test files the gate does not catch. Add `tests/` to the migration inventory and either re-target or delete each: `test_sdk_client.py`, `test_harness_{model_coverage,streaming,token_capture,thinking_block_sentinel,retry}.py`, `test_sdk_client_{harness_counters,image_sentinel}.py`, `test_completion_runner_two_pass.py`, and the 6 integration `test_harness_*`/`test_session_*`/`test_pm_final_delivery.py`. Also fix `agent/__init__.py` re-export. |
| CONCERN | Skeptic | **No single-session mode exists in the PoC; it is net-new design, not "degenerate path."** `GraniteAgentLoop.run()` unconditionally starts BOTH pm and dev sessions (`granite_agent_loop.py:141-173`). Single-session mode (Phase C / task 4 / Open Question #2) is undesigned new architecture on the critical path of every conversational Telegram/email turn, gated only by an unresolved Open Question. The plan treats it as a small "degenerate mode" but it is a from-scratch second runner topology. | Open Question #2 + task 4 | Resolve Open Question #2 (operator+one-session vs. operator-bypass-for-single-turn) BEFORE task 4 starts; task 4 currently has `Informed By: Open Question #2 resolution` but no fallback if the answer is "operator adds latency for no benefit." Specify the completion-detection mechanism for single-session (the PoC detects "TASK COMPLETE" from PM only — `granite_agent_loop.py:300`; a lone conversational session has no such phrase). |
| CONCERN | Operator | **Operator-down behavior is unresolved (Open Question #3) yet ollama becomes a hard, no-fallback dependency for ALL execution.** Today ollama down only degrades summarization; after cutover, ollama down = zero sessions run, with no fallback path permitted by the directive. The plan defers the queue-vs-fail decision to an Open Question but ships the hard dependency regardless. | Open Question #3 + Update System gate | Decide before Phase D: on ollama unreachable, the runner must surface a user-visible PM-persona message via `OutputHandler` (per `feedback_telegram_persona_always`) and either re-enqueue the `AgentSession` (queue-and-retry) or mark it failed — never silent-hang. The `/update` green-light gate (Update System section) must hard-block a machine missing granite so the worker fails the gate rather than crash-looping on first session. |
| CONCERN | Skeptic, User | **Billing premise (Risk #1 / Open Question #1) may invalidate the whole cutover, but build tasks do not gate on it.** The 2026-06-15 change meters headless `claude -p` against a separate Agent SDK credit pool; a dual-session loop burns ~2× headless usage. The plan says "decide before Phase D" but tasks 1-5 (build + gates) proceed regardless, risking large sunk effort on a substrate whose economic premise has expired. | Open Question #1 + No-Gos [EXTERNAL] | Make the billing decision an explicit gate on task 6 (cutover), not just prose. Phase B gate (task 5) must MEASURE headless credit consumption per session (the smoke/concurrency runs are the natural place) so the go/no-go has data, not speculation. The agent cannot read Anthropic pricing policy (No-Gos [EXTERNAL]) — flag the human-decision dependency at the top of task 5. |
| CONCERN | Adversary | **Concurrency gate (≥5 concurrent) may be insufficient vs. the documented ~3-4-session burst limiter, and the runner doubles process count.** Research #2 cites throttling after ~3-4 concurrent headless sessions; a dual-session runner spawns 2 `claude` subprocesses per logical session, so 5 concurrent logical sessions = 10 subprocesses — well past the observed limit. The gate threshold (≥5) is below realistic production concurrency. | Risk #2 + task 5 | The concurrency gate must count *subprocesses*, not logical sessions: 5 dual-mode sessions = 10 `claude` processes. Test at production-representative concurrency and assert that the burst-limiter error string (`Server is temporarily limiting requests`) is caught and surfaced as a retriable PM-persona message, not a hard session failure. Single-session mode (CONCERN above) is the primary lever to halve process count — couple the two. |
| NIT | Operator | **Verification smoke-gate assertion is both too loose and too strict.** The plan asserts `output contains "Parse error rate:   0"`, but the script prints `Parse error rate:   {pct:.1f}%` (`granite_smoke_test.py:361`), e.g. `0.0%`. The substring matches `0.0%` but the kill criterion from #1486 is ≤20%, not 0% — asserting exact-zero is stricter than the agreed gate and brittle to spacing. | Verification table | Change the assertion to parse the JSON `parse_error_rate` field the script writes (`granite_smoke_test.py:376`) and assert `<= 0.20`, rather than substring-matching formatted stdout. |
| NIT | Simplifier | **Risk #3 (ollama chokepoint) mitigation pre-judges its own measurement.** Risk #3 says "operator calls are ~1s so headroom exists, but verify" — asserting the conclusion before the concurrency gate produces data. | Risk #3 | Drop the "headroom exists" assertion; let the Phase B concurrency gate (task 5) report operator p50/p99 latency under load and decide `OLLAMA_NUM_PARALLEL` from the measurement. |

---

## Open Questions

**Still open (need human input):**

1. **[BILLING — HUMAN GATE on Task 6] Does the 2026-06-15 subscription billing change
   invalidate the economic premise?** Headless `claude -p` on subscription will draw from
   a separate monthly Agent SDK credit. A dual-session runner uses ~2× headless usage. Is
   the new credit pool still cheaper/preferable to the current API-key billing, given
   production volume? Task 5 supplies measured per-session credit consumption; the human
   makes the go/no-go before the irreversible Task 6. The agent cannot read Anthropic
   pricing policy (No-Gos [EXTERNAL]).
4. **Model-per-role config granularity.** Per-project, per-task-complexity, or a single
   global triplet to start? (Affects the config schema in Phase E. Defaulting to a single
   global triplet unless directed otherwise.)

**Resolved in this revision (recorded for traceability):**

2. ~~How should a single conversational turn map onto a dual-session runner?~~ **RESOLVED:**
   single-session mode = one `claude -p` session + operator (tools reduced to
   `probe_session`/`signal_done`), completion detected from the lone session's stream-json
   `result` event. Always through the runner per the directive; latency cost measured in
   Phase B. See Phase C.
3. ~~Operator-down failure behavior?~~ **RESOLVED:** on ollama/granite unreachable, the
   runner re-enqueues the `AgentSession` with bounded retries, then on exhaustion marks it
   failed and surfaces a PM-persona message via `OutputHandler`. Never silent-hang, never a
   fallback to the old path. See Failure Path Test Strategy + Update System (the `/update`
   green-light gate hard-blocks a machine missing granite).
