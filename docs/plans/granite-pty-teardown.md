---
status: Built
pr: https://github.com/tomcounsell/ai/pull/1930
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-06
tracking: https://github.com/tomcounsell/ai/issues/1924
last_comment_id: 4890693222
revision_applied: true
---

# Granite PTY Teardown: All Sessions Headless via `claude -p`

## Problem

On 2026-07-06 two watched SDLC retries (#1915, #1916) produced zero work. The granite PTY substrate — which drives two interactive Claude Code TUIs and infers all session state by parsing what the TUI paints on screen — never reached a detectable idle state: both PM and Dev prime steps burned their full 360s ceilings with `saw_idle=False`, the startup loop plateaued, one session failed `never_started` and the other was **auto-marked "completed" with no PR and no reply to the CEO**. The worker sat in `granite degraded`, a state that fails *every* eng session.

This was not a tuning problem. The first-run symptom (Bash tool-wedge > 300s) and the retry symptom (prime never idle) are two faces of one root cause: **we are screen-scraping a human-facing UI that Anthropic changes constantly and that carries no stability contract.** The full case is the committed postmortem: [`docs/postmortems/2026-07-06-granite-pty-fragility.md`](../postmortems/2026-07-06-granite-pty-fragility.md) (d451c1bd).

**Current behavior:** eng/PM/Dev session execution depends on 9,392 LOC of PTY automation (`agent/granite_container/`), a 725-LOC browser OAuth robot, ~30 timing knobs, and PTY heuristics leaked across ~30 `agent/` files. It fails opaquely and sometimes reports success on failure.

**Desired outcome:** the PTY substrate is deleted — not flagged off, deleted. Every session role runs as headless `claude -p` stream-json subprocesses on subscription OAuth, turn-end comes from the protocol (stream-json `result` event + Stop-hook envelope), resume comes from persisted per-role `claude_session_id`s, and the codebase sheds ~10k LOC of accidental complexity. **One-way cutover. No revert path, no transport abstraction, no PTY fallback.**

## Locked Tradeoff Decisions (owner, 2026-07-06)

These four decisions were made explicitly by the owner after the audit and are **not** open for re-litigation during build:

| # | Decision | Choice |
|---|---|---|
| D1 | Session topology | **AMENDED by owner same-day, post-spike #1928: single Opus PM session; Dev runs as a resumable subagent *inside* the PM session.** The PM is the only top-level `claude -p` session; Dev is spawned via the harness's agent tool from a well-defined `dev` agent definition (built from the dev prime command) and continued across turns — and across process restarts — via agent continuation. The original choice (two headless roles + runner-mediated relay) was contingent on subagent resumability being unproven; spike #1928 proved it the same day (subagent context survives `--resume` across processes, transcript-backed), meeting the owner's recorded condition. There is no relay loop at all. |
| D2 | ollama | **Out of the session-execution path entirely.** Worker probe, circuit breaker, reprobe loop, degraded-mode deferral, and the update green-light gate are deleted. Bridge routing and email triage keep their direct ollama calls (follow-up: #1923). |
| D3 | Resume fidelity | **Simple `--resume`.** Persist per-role session IDs; resume injects the reply/steer as the next turn. Supersedes the #1721 lossless-checkpoint plan (loop cursor dropped). |
| D4 | Mid-turn control | **Auto-preempt on any steer.** A steering message terminates the in-flight turn subprocess; the runner resumes with the steer injected. The PTY two-stage ctrl-c interject path is deleted. |

## Freshness Check

**Baseline commit:** d451c1bd (HEAD at plan time — the postmortem commit itself)
**Issue filed at:** 2026-07-06 (tracking issue #1924 created by this plan)
**Disposition:** Unchanged (evidence is same-day and first-hand) + **Overlap surfaced and resolved**

- The postmortem, the incident telemetry, and all six audit passes are from today against d451c1bd. No drift possible.
- File:line anchors re-verified during the audit itself: `session_executor.py:1690-1696` (pm=headless→pty coercion), `container.py:2787` (Dev relays via PM PTY), `role_driver.py:353` (--resume chaining), `session_health.py:407` (TOOL_TIMEOUT_DEFAULT_SEC=3000 stopgap), `models/agent_session.py:499` (resume_handles written, unconsumed).
- **Overlap:** `docs/plans/granite_lossless_checkpoint_resume.md` (status: Ready, tracking #1721) plans lossless PTY checkpoint resume. Resolved by decision D3: this plan supersedes it; task 9 marks it Cancelled with a pointer here.
- **Overlap:** open issue #1921 proposes per-role headless default *with PTY auto-fallback* — contradicts the one-way mandate; superseded (implementation PR closes it).
- **Overlap (same-day):** `docs/plans/idle-notification-verbatim-delivery.md` (#1919, committed 4b62c646 while this plan was being audited) parked itself as "draft pending the cutover decision." Resolved by **absorption**: its root cause — `hook_edge.py:71` unconditionally classifying every `Notification` as `needs_human`, which both leaked "Claude is waiting for your input" boilerplate to the CEO and swallowed the PM's real `[/user]` answer — lives in a module that *graduates* into the runner, so the defect would survive a naive cutover. Task 1 carries its fix into the graduated `hook_edge`; the standalone plan is Cancelled; the implementation PR closes #1919.

## Prior Art

- **#1542 (cancelled 2026-06):** first headless cutover attempt, cancelled because the then-thesis required driving the real interactive TUI. That thesis is formally reversed by the postmortem; the memory record was updated 2026-07-06.
- **#1842 (merged):** per-role transport hedge — built the `GRANITE__{PM,DEV}_TRANSPORT` seam and `HeadlessRoleDriver`. This plan finishes what it started and then deletes the seam itself (one transport needs no selector).
- **#1751 (merged):** adopted `claude setup-token` (~1-year `CLAUDE_CODE_OAUTH_TOKEN`) — the auth this cutover rides.
- **#1688 (shipped):** hook-driven turn returns (`docs/features/granite-hook-driven-turn-returns.md`) — the turn-end mechanism that replaces idle scraping; explicitly transport-agnostic.
- **#1681 (merged):** made the PM↔Dev shuttle zero-LLM — confirms routing is regex, not ollama.
- **#1633 (open):** prescribed exactly the D1-amended end-state — "dependent work runs as subagents WITHIN a session." This plan delivers it for the Dev role; the child_session_gate's eventual removal remains #1633's scope (task 9 comments).
- **#1917 (open):** crash auto-resume is structurally dead — granite PTY sessions always classify non-resumable. Task 3 resolves the non-resumable-classification half; the reflection-scheduling half is re-triaged after cutover (task 9 comments on the issue).
- **#1918 / #1843 / #1792 / #1851 / 4f9f929e:** the patch-the-heuristic lineage (see Why Previous Fixes Failed).
- **#1724 / #1879 (merged):** the mid-run wedge/nudge lineage — mid-run quiescence constants (#1724) and the wedge-nudge steering channel (#1879). Task 5 (build-health) deletes their entire implementation surface; both close via the implementation PR.

## Research

External verification via the Claude Code docs (claude-code-guide agent, 2026-07-06):

- **PermissionRequest hooks do not fire under `-p`** (hooks-guide: use PreToolUse for automated permission decisions). Moot for us — role sessions run `--permission-mode bypassPermissions` — but the graduated `generate_hook_settings` should stop registering PermissionRequest hooks headless.
- **`--resume <session_id>` works across separate `-p` subprocess invocations**; `session_id` arrives in the `system/init` stream event. **Session lookup is scoped to the working directory (and its git worktrees)** — resume must always re-invoke from the same `working_dir`; the persisted resume handle must therefore carry `working_dir`.
- **Slash commands and skills work under `-p`** (expanded before running) — the `/granite:prime-*-role` prime path survives; `role_driver.py:56-62` already verified this empirically.
- **`--bare` does not read `CLAUDE_CODE_OAUTH_TOKEN`** — never use `--bare` on the role paths.
- **Auth precedence** confirms stripping `ANTHROPIC_API_KEY` + exporting `CLAUDE_CODE_OAUTH_TOKEN` yields subscription auth headless — the exact machinery `get_response_via_harness` ships today.
- Sources: code.claude.com/docs/en/{headless,hooks-guide,permission-modes,authentication,sessions}.md

## Spike Results

Six parallel audit passes ran at plan time (2026-07-06, baseline d451c1bd) in lieu of spikes — the assumptions were verifiable by reading, not prototyping:

### audit-1: granite_container module map
- **Finding:** Clean graduate/delete split. GRADUATE (~2,600 LOC): `hook_edge.py` (514), `hook_forwarder.py` (104), `transcript_tailer.py` (454), `granite_classifier.py::classify_pm_prefix` (regex, zero ollama on the routing path), `HeadlessRoleDriver` (~300), ~800 LOC of `container.py` relay/exit-classification/wrapup-guard, and `bridge_adapter.py`'s delivery callbacks + `_persist_resume_handles` + `_publish_exit_summary` + `_transcript_path_from_spec`. DELETE: everything else, including `byob_relogin.py` in full (confirmed: zero production importers outside the package).
- **Only two coupling severs:** `role_driver.py:467 → bridge_adapter._transcript_path_from_spec` (moves with it) and `granite_classifier.py:190 → pty_driver._strip_ansi` (drop — stream-json carries no ANSI).
- **Confidence:** high

### audit-2: cross-codebase coupling (~30 files)
- **Finding:** Two disambiguation traps. (1) "granite" is two subsystems — the ollama classifier machinery (worker breaker/reprobe/deferral, update gate, `session_state.granite_available`, `session_pickup` deferral) merely *lives* in the doomed package; decision D2 deletes that machinery deliberately rather than accidentally. (2) "wedge" is overloaded — bridge/worker **loop-wedge** detectors (`monitoring/bridge_watchdog.py`, `monitoring/session_watchdog.py`, `bridge/liveness.py`, restore-wedge in `agent/session_archive.py`) are OUT OF SCOPE and must survive; only the PTY tool-wedge/quiescence family dies. Hard-import blast radius: `session_executor.py:1821`, `agent_session_queue.py:1551`, `worker/__main__.py` (×5), `tools/granite_loop/cli.py`, `reflections/stall_advisory.py:442`, 2 spike scripts.
- **Confidence:** high

### audit-3: config/docs/scripts surface
- **Finding:** ~15 PTY timing constants are bare module-level defaults (not in `.env.example`) — deletion touches only source. Survivors: `hook_turn_end_wait_s`, `hook_crash_resume_cap`, `pm_model`/`dev_model`, delivery timeout, supervisor trio. `TOOL_TIMEOUT_DEFAULT_SEC` reverts 3000→300 (`.env.example:343` already says 300; the 3000 lives in code at `session_health.py:407` and runtime vault `.env:324`). The four prime commands are transport-neutral and survive with a PTY-wording scrub. `valor-granite-loop` in pyproject is already dangling (target package absent). No plist file contains GRANITE-specific env — `install_worker.sh` injects the whole `.env` generically.
- **Confidence:** high

### audit-4: test surface
- **Finding:** ~648 tests / 38 files DELETE (all of `tests/unit/granite_container/` minus one misfiled keeper, 4 integration files, 3 unit files, the 1,559-LOC `tests/granite_faults/` harness). 15 files UPDATE. **~120+ headless replacement tests already exist** (`test_headless_role_driver.py` ×13, `test_transport_dispatch_e2e.py` ×5, harness streaming/retry/token-capture suites). Zero granite xfails. One genuine coverage gap: PTY frozen-frame wedge detection has no headless analog — replaced by turn-timeout + subprocess-liveness coverage.
- **Confidence:** high

### audit-5: headless replacement readiness
- **Finding:** ~60% ready; gaps are engineering, not research. Working today: OAuth-stripped subscription auth (`sdk_client.py:2469`), stream-json result/usage parse (`:2725`), `--resume` chaining with stale-UUID fallback (`:2585-2622`), hook-edge turn-end, slash prime, steering drain, Dev leg headless in production (`container.py:2720`), message drafter running `claude -p` daily (`session_completion.py:755,817`). Gaps: **G1** no PM headless dispatcher (L); **G2** executor coerces pm=headless→pty (`session_executor.py:1690-1696`) (S); **G3** headless Dev relays through the PM PTY (`container.py:2787`) (M/L); **G4** resume handles written, never consumed (M); **G5** OAuth token inherited from worker env, not explicitly injected (S); **G6** teammate/eng roles unrouted through the role driver (M).
- **Confidence:** high

### audit-6: headless CLI capabilities
- See ## Research above. **Confidence:** high (doc-cited; stream-json event schema flagged for empirical confirmation against our pinned CLI version — mitigated by the fact that `get_response_via_harness` already parses it in production daily).

## Data Flow

1. **Entry:** Telegram message → bridge (`bridge/telegram_bridge.py`) → enqueue `AgentSession` (Redis, Popoto) — unchanged.
2. **Worker:** `python -m worker` claims the session → `session_executor.execute_agent_session` — unchanged up to dispatch.
3. **Dispatch (changed):** executor builds a `SessionRunner` (new `agent/session_runner/`) instead of `BridgeAdapter`+`PTYPool`+`Container`. No transport resolution — there is one transport.
4. **Runner loop (changed, D1-amended):** per turn, spawn ONE `claude -p --output-format stream-json --resume <persisted uuid>` subprocess — the PM session — in the session's `working_dir`; turn 1 primes via the PM prime slash command. For eng work the PM spawns/continues its `dev` subagent *inside* its own turn (harness agent tool; the parent `-p` process blocks until the subagent finishes — spike Exp4). The PM's turn output routes by simplified regex: `[/user]` → deliver via callbacks; `[/complete]` → wrapup guard → exit; anything else → continue. The `[/dev]` routing token and external relay are gone — the PM calls Dev itself.
5. **Turn end (changed):** stream-json `result` event (usage, cost, is_error) reconciled with the Stop-hook envelope (`hook_edge` NDJSON file) — no idle scraping anywhere.
6. **Steering:** `push_steering_message()` → Redis list → runner's preempt watcher terminates the in-flight subprocess (D4) → next loop iteration drains the steer and resumes with it injected.
7. **Persistence (owner mandate — baked into the Popoto model):** `AgentSession` carries the four resume scalars from spike #1928 — `claude_session_id` (the PM session UUID, the sole `--resume` entry point; reuses the existing `claude_session_uuid` field), `dev_agent_id` (the subagent continuation handle, captured **structurally, not from PM prose**: after each turn — and on preempt — the runner scans `{project-slug}/{claude_session_id}/subagents/agent-*.jsonl` under `~/.claude/projects/` for new agent ids and persists them; the sidechain file exists from spawn, so a preempt mid-Dev-spawn still captures it), `runner_cwd` (exact absolute path — resume is cwd-scoped), `claude_version` (behavior is version-specific). `claude_session_id` is captured at `system/init` (Race 5); `dev_agent_id` upserted the turn Dev is first spawned. Additionally, a compact **turn history** is mirrored per turn (extending the existing session-event stream): `{ts, actor: pm|dev, text}` with full user-visible texts, tool-noise excluded, length env-capped — observability on the dashboard plus a recovery seed if the on-disk transcripts are ever GC'd. Transcripts remain the source of truth; the mirror is insurance. `_publish_exit_summary` unchanged.
8. **Output:** delivery callbacks → Redis outbox → bridge → Telegram — unchanged.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1918 / 0a3a8162 | Re-derived idle markers after claude CLI 2.1.201 changed the TUI paint | Fixed one drift instance; the next TUI release re-breaks it. Markers are unversioned UI output. |
| 4f9f929e | Raised TOOL_TIMEOUT_DEFAULT_SEC 300→3000 | Aimed at the wrong layer — the retry failed before any tool ran. You cannot timeout your way to a signal that isn't coming. |
| #1843 | Wired existing wedge signals to recovery | Added consumers of the same unreliable screen-derived signals. |
| #1792 | Softened priming kills (no_progress/never_started grace) | Tuned the grace window around prime flakiness instead of removing the prime-scrape. |
| #1837 | Built a failure-simulation harness | A simulator for failure modes too frequent to debug live — institutionalized the fragility rather than removing it. |

**Root cause pattern:** every fix tuned or re-derived heuristics over an unstable human-facing UI. This plan removes the UI from the loop; the protocol (stream-json + hooks) reports its own state.

## Architectural Impact

- **New dependencies:** none. Deletes `pexpect` (PTY-only) from `pyproject.toml` after verifying no other consumer.
- **Interface changes:** executor-facing construction changes from `BridgeAdapter(...)` + `.run()` to `SessionRunner(...)` + `.run()` with the same delivery-callback contract (transport-keyed, per repo convention). `AgentSession` loses all PTY fields.
- **Coupling:** strictly decreases — the ~30-file PTY leakage collapses; the worker no longer couples session execution to ollama availability (D2) or to a pool of long-lived terminals.
- **Data ownership:** unchanged (Redis/Popoto remains the session store; per-role Claude session state lives in Claude's own transcript JSONLs keyed by persisted UUIDs).
- **Reversibility:** deliberately none. This is a one-way cutover by owner mandate and repo rule (no parallel-run migrations).

## Appetite

**Size:** Large

**Team:** Solo dev (builder agents), PM check-ins at critique and pre-merge, code review round.

**Interactions:**
- PM check-ins: 2-3 (tradeoffs already locked; critique + pre-merge sign-off)
- Review rounds: 2 (code review + cruft audit — a teardown plan invites leftover-legacy findings)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` in vault env | `python -c "from dotenv import dotenv_values; assert dotenv_values('$HOME/Desktop/Valor/.env').get('CLAUDE_CODE_OAUTH_TOKEN')"` | Subscription auth for all headless role turns (vault path — worktrees have no `.env` symlink) |
| `claude` CLI on PATH | `command -v claude` | The headless substrate |
| Worker/bridge stoppable on this machine | `./scripts/valor-service.sh status` | Cutover restart at the end of build |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite-pty-teardown.md`

## Solution

### Key Elements

- **`agent/session_runner/` (new module, ~1,800-2,600 LOC total):** the graduated survivors under a name that retires "granite" from the session path — `role_driver.py` (HeadlessRoleDriver + prime loading), `hook_edge.py` + `hook_forwarder.py`, `transcript_tailer.py`, `router.py` (regex `classify_pm_prefix` + `RouteOutcome` exit classification), `runner.py` (the single-session turn loop), `adapter.py` (executor-facing construction, delivery callbacks, resume-scalar persistence, exit summary).
- **No relay loop at all (D1-amended):** the PM is the single top-level session, driven by `HeadlessRoleDriver`; Dev is a **well-defined subagent** — a `dev` agent definition (`.claude/agents/dev.md`, generated from the dev prime command + shared rails) that the PM spawns on first need and continues via agent continuation, across turns and across `--resume`d processes (spike #1928). PM↔Dev coordination is the harness's own agent mechanism; the runner orchestrates exactly one subprocess per turn. No pool, no prime phase, no startup loop, no plateau detector, no idle scraper, no relay.
- **Steer-preempt (D4):** a watcher polls the steering list during a turn; on arrival it terminates the turn's subprocess (generation-token-guarded), the loop drains the steer, and the next turn `--resume`s with it injected.
- **Simple resume (D3, four-scalar shape):** exactly four flat fields on `AgentSession` — `claude_session_id`, `dev_agent_id`, `runner_cwd`, `claude_version` — plus the bounded turn-history mirror. There is NO `resume_handles` list in the end state (task 5 deletes the field; its writer is reworked in task 1). Runner init consumes the scalars (`--resume`, skip prime, re-introduce the dev agent id); the existing stale-UUID fallback (retry once, no `--resume`, full context) is the only recovery tier.
- **Health = protocol, not paint:** liveness is subprocess-alive + hook-edge/turn-record recency; the only ceilings are the per-turn timeout (`turn_timeout_s`) and `hook_turn_end_wait_s`. All PTY quiescence/wedge/prime heuristics in `session_health.py`, `session_stall_classifier.py`, `agent_session_queue.py`, `tool_budget.py` are deleted or collapsed.
- **Worker without ollama (D2):** startup goes straight to recovery + queue; no model probe, no breaker, no deferred-ENG state. `session_pickup` deferral and `session_state.granite_available` die.

### Flow

**Telegram message** → bridge enqueues session → **worker claims** → executor builds SessionRunner → **PM turn** (`claude -p`, resume or prime; spawns/continues its `dev` subagent internally for eng work) → regex route on PM output → **[/user]**: deliver to Telegram, await reply (dormant) → **[/complete]**: wrapup guard → exit summary → drafter delivery — with **any steer** killing the in-flight turn (PM and its in-flight Dev together) and re-entering via `--resume`.

### Technical Approach

- **Graduate-then-delete, one branch, one PR.** Move survivors into `agent/session_runner/` with imports severed, rewire the executor/worker, then `git rm -r agent/granite_container/` in the same PR. No interim state where both substrates are wired (repo rule: no parallel-run migrations).
- **One dispatcher, period (G1+G3 dissolve).** `runner.py` is a single-session turn loop: `HeadlessRoleDriver.run_turn` for the PM, every session type. G1 (PM headless dispatcher) is just this; G3 (Dev's PM-PTY coupling) ceases to exist because Dev is no longer a sibling process — it's the PM's subagent. The Dev-side steering/unlock protocol is **baked into the `dev` agent definition at authoring time** (spike gotcha 1: post-hoc overrides are refused; design the continuation contract into the original task).
- **Explicit auth injection (G5):** the runner (not ambient worker env) sets `CLAUDE_CODE_OAUTH_TOKEN` and strips `ANTHROPIC_API_KEY` in the subprocess env, so headless owns its auth posture deliberately. Never pass `--bare`.
- **Roles beyond PM/Dev (G6):** every session type is now structurally identical — one top-level session with a role prime; eng sessions additionally carry the `dev` subagent definition. Teammate sessions are the same loop with a different prime and no dev agent. The `ValorAgent`/SDK bridge-chat path is untouched (already headless; #1925's territory).
- **Long Dev work runs inside the PM turn.** The parent `-p` process blocks until its subagent finishes (spike Exp4), so an eng PM turn containing a full Dev build is legitimately long. The per-turn timeout becomes role-aware (eng: generous, env-overridable, provisional; teammate: short) — an honest ceiling on a protocol that reports completion, not a guess about screen paint. **Timeout expiry is a graceful preempt, not an error** (third-pass concern): same SIGTERM→grace→SIGKILL path as D4 with `turn_end_source="timeout"`, partial work preserved in the transcript, session surfaced as needs-attention with a persona-safe message — a long Dev build is never silently discarded by its own ceiling.
- **Rename discipline:** `GraniteSettings` → `SessionRunnerSettings` (env prefix `SESSION_RUNNER__`), `.claude/commands/granite/` → `.claude/commands/roles/` with `hardlinks.py` stale-removal, "granite" survives only in (a) the postmortem/history and (b) the ollama classifier consumers outside the session path (bridge routing, email triage — #1923's territory). The rename ships with a **mandatory stale-prefix guard** (critique C3): settings load emits a loud startup warning when any legacy `GRANITE__*`/`GRANITE_*` key is present in the environment, and `/update` surfaces it during deploy — old vault overrides fail loudly instead of silently reverting to defaults.
- **Preempt mechanics:** the runner records `(turn_generation, process_handle)` at spawn; the watcher only terminates a process whose generation matches the current turn. SIGTERM, 10s grace, SIGKILL. A preempted turn records `turn_end_source="preempted"`; its partial transcript is preserved in the Claude session JSONL and `--resume` continues from it.
- **False-success closed at the transport (the #1916 class):** a turn either yields a stream-json `result` or the subprocess errored — there is no "plateau then auto-complete." The graduated `_run_wrapup_guard` continues to guarantee a user-facing message on the semantic layer.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Runner turn dispatch: subprocess exits nonzero with no `result` event → `exit_reason=error` (never `completed`), exit summary published, persona-safe apology delivered — test asserts the AgentSession terminal status AND the delivery record (regression net for the #1916 false-success class)
- [ ] Preempt watcher: exception inside the watcher task must not kill the runner loop — test asserts a logged warning and an intact turn
- [ ] `hook_forwarder` stays fail-silent (`exit 0`) — existing contract test relocates with the module
- [ ] Delivery callback failure → outbox-recovery path still fires (existing `granite_delivery_recovered_via_outbox` event, renamed)

### Empty/Invalid Input Handling
- [ ] Empty PM turn text → `classify_pm_prefix` returns `unknown` → routed to wrapup guard, not an infinite relay loop — test with `""` and whitespace-only
- [ ] Stale/garbage resume scalars on the session (malformed `claude_session_id` UUID, missing/nonexistent `runner_cwd`, unknown `dev_agent_id`) → validated, discarded, cold-start with prime — no crash
- [ ] Empty steering message drained mid-preempt → ignored, turn not re-killed

### Error State Rendering
- [ ] Every non-clean `exit_reason` maps to a persona-safe Telegram message (no raw exit strings to the CEO) — parametrized over the exit-classification table
- [ ] Dashboard renders sessions without PTY fields (no KeyError on old records post-migration)

## Test Impact

Full audit in Spike Results (audit-4). Dispositions:

- [ ] `tests/unit/granite_container/` — **DELETE** 31 files (~593 tests: pty_driver, pty_pool, container, transcript_tailer PTY paths, startup_parser, byob_relogin, bridge_adapter PTY paths, hook_edge PTY paths, persona_priming, fault_injection, tui_marker_contract, crash_resume, mid-run steering, etc.)
- [ ] `tests/unit/granite_container/test_headless_role_driver.py` — **RELOCATE** to `tests/unit/session_runner/` (13 tests; replacement coverage, survives)
- [ ] `tests/granite_faults/` — **DELETE** harness (scenarios, recorder, hook_fidelity, mocks, fixtures); **SALVAGE** `headless_hook_probe.py` into `tests/unit/session_runner/` support; `ollama_env.py` **DELETE** (its consumers die with D2)
- [ ] `tests/integration/test_granite_container_loop.py`, `test_granite_mid_run_steering.py`, `test_granite_pty_production.py`, `test_append_system_prompt_interactive.py` — **DELETE** (8 tests; also remove the `granite_integration` marker from `pyproject.toml:179`)
- [ ] `tests/integration/test_transport_dispatch_e2e.py` — **UPDATE**: repoint imports to `agent/session_runner/`; it becomes the runner dispatch E2E
- [ ] `tests/integration/test_granite_ollama_e2e.py` — **REPLACE**: keep the headless turn-end/prime-resolution probe subtests, drop recorder/hook-fidelity/ollama subtests
- [ ] `tests/unit/test_bridge_adapter_pty_normalize.py`, `test_granite_startup_diagnostic.py`, `test_granite_oauth_token_env.py` — **DELETE** (47 tests; OAuth-env coverage rewritten against the runner's explicit injection)
- [ ] `tests/unit/test_session_executor_granite.py` — **UPDATE**: dispatch tests target SessionRunner; delete PTY-default/transport-coercion/pty-uuid tests
- [ ] `tests/unit/test_session_health_wedge_nudge_producer.py` — **DELETE** (wedge-nudge channel is removed); `test_session_health_tool_timeout.py` — **UPDATE** (drop PTY branches, assert 300 default)
- [ ] `tests/unit/test_session_stall_classifier.py` — **UPDATE**: delete `granite_wedged` class + PTY-field probes; keep generic stall classes
- [ ] `tests/unit/test_transport_routing_matrix.py`, `test_transport_config_validation.py` — **DELETE** (no transport selector exists after cutover)
- [ ] `tests/unit/test_worker_granite_degradation.py` — **DELETE** (degraded mode removed, D2); `tests/unit/test_worker_contract_check.py`, `tests/integration/test_worker_concurrency.py`, `tests/integration/test_progress_deadline_cancel.py`, `tests/integration/test_update_loop_wedge_recovery.py`, `tests/integration/test_worker_wedge_pending.py` — **UPDATE** (remove PTY-pool/marker-contract branches)
- [ ] `tests/integration/test_pi_builder_e2e.py` — **DELETE** (the pi harness dies with the package; zero production consumers — supersedes the earlier UPDATE disposition)
- [ ] **NEW:** `tests/unit/session_runner/test_runner_turns.py` (single-session loop, simplified route table, wrapup), `test_runner_dev_subagent.py` (dev agent definition contract, agent-id capture + persistence, continuation-across-resume against a fake harness), `test_runner_preempt.py` (generation-token guard, kill-at-boundary race, SIGTERM→SIGKILL), `test_runner_resume.py` (four-scalar consumption, cwd-scoped resume, stale-UUID fallback, skip-prime, turn-history mirror), `test_runner_liveness.py` (role-aware turn timeout, subprocess-death detection — the wedge-coverage replacement)

## Rabbit Holes

- **Rebuilding a transport abstraction "just in case."** There is one transport. Any `RoleTransport` enum, config selector, or fallback branch is scope creep and violates the one-way mandate.
- **Porting `byob_relogin.py` "in case headless needs re-auth."** It exists only because the TUI paints login frames; headless never does. Delete, don't port.
- **Perfecting lossless resume.** D3 chose simple `--resume`. Do not implement loop cursors, mid-relay position tracking, or skip-priming edge matrices from the superseded #1721 plan. The turn-history mirror is NOT an exception to this rule (third-pass reconciliation): it is an owner-mandated, bounded observability record and disaster-recovery seed — it is never read on the normal resume path, and any urge to make resume *replay* from it is exactly this rabbit hole.
- **Replacing bridge/email ollama calls.** Deliberately out of scope (#1923). Touching `bridge/routing.py` or `tools/email_cs/triage.py` classification here widens the blast radius for zero teardown value.
- **Renaming every historical `exit_reason` value.** `pm_complete`/`pm_user`/`dev_hang` values persist in old records and telemetry; keep the vocabulary, delete only the PTY-only producers (`startup_unresolved`, `plateau`).
- **The `.worktrees/` copies.** 38+ stale worktree checkouts mirror these paths; they are branch checkouts, not mainline code. Do not "fix" them — worktree GC owns them.

## Risks

### Risk 1: Big-bang cutover breaks live session execution fleet-wide
**Impact:** eng/PM sessions fail after deploy — though note the honest baseline: they are *already* failing under `granite degraded`, which caps the downside.
**Mitigation:** the Dev leg and the message drafter already run this exact transport in production daily; `test_transport_dispatch_e2e.py` proves runner dispatch pre-merge; the E2E probe (`valor-telegram send --await-reply` against the registered bot) is the post-deploy gate; worker deploys one machine first (bridge-role machine last).

### Risk 2: Steer-preempt (D4) wastes long productive Dev turns or corrupts a turn mid-write
**Impact:** a low-priority nudge kills a 20-minute build turn; repeated steers thrash.
**Mitigation:** generation-token guard prevents cross-turn kills; SIGTERM→grace→SIGKILL lets the CLI flush its transcript; `--resume` continues from the partial turn; a short debounce batches steers that arrive within a few seconds into one preempt. If thrash emerges in practice, the owner's stated posture is to iterate on the clean base — not to re-add PTY interject.

### Risk 3: Removing the ollama gate (D2) breaks a hidden consumer
**Impact:** something besides the worker gate assumed `ensure_granite_model` ran at startup.
**Mitigation:** audit-2 enumerated all consumers (worker, session_pickup, session_state, update gate — all deleted together; bridge routing and email triage call ollama directly and are untouched). Verification row greps for dangling references.

### Risk 4: stream-json event drift on a future CLI release
**Impact:** the same class of breakage as the TUI, one layer down.
**Mitigation:** categorically better surface — stream-json is a documented machine interface, already parsed in production daily, with `is_error`/nonzero-exit as a hard failure signal (drift fails loudly; the TUI failed silently). The stale-UUID fallback and error-exit paths turn parse failures into visible `exit_reason=error`, never false success.

### Risk 5a: Agent-continuation behavior is CLI-version-specific
**Impact:** a claude CLI upgrade changes sidechain layout or continuation semantics and silently breaks Dev resumability (spike evidence is from 2.1.201).
**Mitigation:** persist `claude_version` per session; validate-cutover smoke includes a full subagent continuation round-trip across a process restart; treat CLI upgrades as deploy-gated (smoke the continuation contract before fleet rollout). Failure mode is loud (SendMessage/resume errors), not silent — categorically better than the TUI.

### Risk 5: Popoto field removal corrupts old session records
**Impact:** dashboard or archive reads crash on records carrying deleted PTY fields.
**Mitigation:** ORM-only migration in `scripts/update/migrations.py` (idempotent, registered in `MIGRATIONS`); `ADD_ONLY_LIVENESS_FIELDS` list updated in the same commit; UI readers tolerate absent fields; `valor-session-archive` restore guard re-verified with a `--dry-run`.

## Race Conditions

### Race 1: Steer arrives as the turn completes naturally
**Location:** `agent/session_runner/runner.py` (preempt watcher vs turn-await)
**Trigger:** steering push lands in the window between the `result` event and the watcher's next poll — a naive kill would hit the *next* turn's subprocess.
**Data prerequisite:** watcher holds `(generation, process_handle)` captured at spawn.
**State prerequisite:** generation increments before any new spawn.
**Mitigation:** watcher kills only if its captured generation equals the current generation AND the process is alive; otherwise the steer simply drains at the boundary that is already occurring.

### Race 2: Worker dies mid-turn; orphan `claude -p` subprocess keeps running
**Location:** runner spawn path + worker startup sweep
**Trigger:** worker crash/restart while a role turn is in flight.
**Data prerequisite:** child PID/PGID recorded on the AgentSession turn record before awaiting.
**State prerequisite:** worker startup sweep runs before queue pickup.
**Mitigation:** subprocesses spawn in their own process group; the existing worker-startup orphan sweep (PPID==1, heartbeat-gated — issue #1271 machinery, which survives) reaps them; resume then re-enters via persisted handles.

### Race 3: Resume from a different working directory
**Location:** runner init resume-consumption path
**Trigger:** session's worktree was GC'd or the slug's cwd changed between runs; `--resume` lookup is cwd-scoped (Research).
**Data prerequisite:** `working_dir` stored inside each resume handle.
**State prerequisite:** the stored `working_dir` still exists on disk.
**Mitigation:** validate `working_dir` exists before `--resume`; on mismatch/absence, discard handles and cold-start (the stale-UUID fallback path already covers the CLI-side miss).

### Race 4: Stale hook-edge envelope from a prior turn read as this turn's TURN_END
**Location:** `role_driver._snapshot_edges` / `_reconcile_turn_end`
**Trigger:** prior turn's Stop envelope unconsumed when the next turn spawns.
**Mitigation:** already solved — pre-spawn edge snapshot + freshness reconciliation graduates unchanged (`role_driver.py:269-319`); keep its tests.

### Race 5: Mid-turn preempt vs. new-turn session-id capture
**Location:** `agent/session_runner/role_driver.py` turn dispatch + handle persistence
**Trigger:** a steer-preempt (D4) kills the subprocess after Claude has created the new turn's session but before the `result` event; if handles are only upserted at turn end, the resume pointer stays on the *pre-turn* uuid and the partial transcript D4 promises to preserve is silently discarded.
**Data prerequisite:** the new turn's `claude_session_id` from the stream-json `system/init` event.
**State prerequisite:** handle persisted before the turn-await begins.
**Mitigation:** capture-at-init (task 3 bullet) — persist the handle the moment `system/init` is parsed. Test: fake CLI subprocess emits `system/init` then hangs (never emits `result`); assert the persisted `claude_session_id` equals the new turn's id after preempt, and the next `--resume` invocation is built with it. **Same discipline for `dev_agent_id` (third-pass concern):** it is captured from the on-disk sidechain directory (which exists from the moment of spawn), never parsed from PM prose — the post-preempt scan asserts a Dev spawned mid-turn is still captured.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1923] Machine-wide ollama removal — replacing `bridge/routing.py` and `tools/email_cs/triage.py` classifier calls with a small Claude call. This plan only removes ollama from the *session-execution* path (D2).
- [SEPARATE-SLUG #1802] PM file-capable send path (screenshots/images to users). Real gap, orthogonal to the transport; unchanged by this cutover.
- [ORDERED] Fleet deploy: `/do-deploy` + per-machine `/update` after merge (plist env regeneration requires `launchctl bootout`/`bootstrap`, and the bridge-role machine goes last after the E2E probe passes on the first machine). Human-gated post-merge event. **Includes the vault-config edit:** remove the `transport` keys from `~/Desktop/Valor/projects.json` only after the bridge-role machine's `/update` completes and the E2E probe passes — `projects.json` is iCloud-synced fleet-wide, and removing the keys at build time would strand pre-cutover machines whose live `validate_transport` path still expects them during the staged rollout window.
- [DESTRUCTIVE] Purging historical PTY telemetry values (old `exit_reason=startup_unresolved`, `startup_failure_kind=plateau` records) from Redis/the session archive. Old records keep their historical values; only the *producers* are deleted. Review-before-execute if ever desired — not this plan.
- [SEPARATE-SLUG #1925] Removing `claude_code_sdk` / migrating the ValorAgent chat path to the harness / PydanticAI for non-harness LLM calls. This plan touches only the granite dispatch leg of `sdk_client.py`.
- [SEPARATE-SLUG #1926] Broader guardian consolidation (watchdog fleet, stall taxonomy, crash-signature library, auto-continue machinery, `child_session_gate` removal) — deliberately post-cutover, pruned against real headless failure data.
- [SEPARATE-SLUG #1927] AgentSession schema diet and field renaming beyond the PTY fields this plan removes.

## Update System

Changes required (this feature is deployed to multiple machines via `/update`):

- **`scripts/update/run.py`:** delete Step 4.75 entirely (owner-ratified in the revision pass — was Open Question 2; re-added under #1923's scope only if bridge routing needs its own gate); no PTY-substrate gating remains. Add the stale-`GRANITE__*` warning surface (see task 7).
- **`scripts/update/verify.py`:** delete the `pty_driver.py` marker checks (lines ~112-176) — they verify files that no longer exist.
- **`scripts/update/hardlinks.py`:** `.claude/commands/granite/` → `.claude/commands/roles/`; add stale-removal entries for the old `~/.claude/commands/granite/` links (same pattern as `RENAMED_REMOVALS`).
- **`scripts/update/migrations.py`:** new idempotent migration — strip removed PTY fields from existing `AgentSession` records via ORM-safe operations (no raw Redis), registered in `MIGRATIONS`.
- **Runtime env:** revert `TOOL_TIMEOUT_DEFAULT_SEC=3000` in `~/Desktop/Valor/.env:324` to 300 (build task — the vault file is reachable from this machine and iCloud-syncs); `/update`'s plist heal regenerates launchd env from `.env` on each machine.
- **`.env.example`:** delete `GRANITE__PM_TRANSPORT`/`GRANITE__DEV_TRANSPORT` commented entries; rename surviving `GRANITE_*` knobs that move under `SESSION_RUNNER__`; keep the classifier-breaker entries only if #1923 hasn't landed (they now belong to bridge routing).
- **`scripts/install_nightly_tests.sh` / `scripts/nightly_regression_tests.py`:** drop PTY/granite-container test targets.
- **`pyproject.toml`:** remove the dangling `valor-granite-loop` entry point; remove `pexpect` from dependencies; remove the `granite_integration` marker.

## Agent Integration

No new MCP servers or `.mcp.json` changes. Integration is subtractive:

- The bridge/worker reach the new code through the same path as today: worker → `session_executor` → (new) `agent/session_runner.adapter` — a direct Python import, same as the current `BridgeAdapter` wiring. Delivery callbacks remain transport-keyed per the repo convention.
- **Deleted agent surfaces:** `valor-granite-loop` CLI entry point (already dangling), `tools/granite_loop/` package.
- Integration test: `tests/integration/test_transport_dispatch_e2e.py` (updated) proves the executor actually dispatches through the runner; the post-deploy `valor-telegram send --await-reply` probe proves the full bridge→worker→runner→Telegram loop.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/headless-session-runner.md` — the new execution model: runner loop, role drivers, hook-edge turn returns, steer-preempt, simple resume, liveness (absorbs the rewritten `granite-hook-driven-turn-returns.md` content)
- [ ] Delete `docs/features/granite-pty-production.md`, `granite-interactive-tui.md`, `granite-login-recovery.md`, `granite-failure-simulation-harness.md`, `granite-hook-driven-turn-returns.md` (content absorbed above)
- [ ] Rewrite the execution-path sections of `docs/features/bridge-worker-architecture.md` and `docs/features/eng-session-architecture.md`
- [ ] Update `docs/features/README.md` index (sort check enforces)
- [ ] Update `CLAUDE.md` architecture diagram (the "granite PTY container" line) and Quick Commands if any die
- [ ] Update `docs/features/session-steering.md` for boundary-drain + auto-preempt semantics
- [ ] Mark `docs/plans/granite_lossless_checkpoint_resume.md` frontmatter `status: Cancelled` with a superseded-by pointer to this plan
- [ ] Keep `docs/infra/granite-oauth-token.md` (the surviving auth doc); scrub its PTY framing

### Inline Documentation
- [ ] Module docstring on `agent/session_runner/` stating the protocol-not-paint contract and the one-way mandate
- [ ] Docstrings preserved on graduated modules (hook_edge's "never touches the PTY" contract becomes "the only turn-end source")

## Success Criteria

- [ ] `agent/granite_container/` does not exist; no source file imports it
- [ ] An eng session dispatched from Telegram completes end-to-end (PM prime → `dev` subagent work → user delivery) with zero PTY processes spawned
- [ ] `valor-session resume` / user reply-to resumes a session via `--resume` with prior context intact — **including continuation of the SAME Dev subagent across a worker restart** (agent id read from the AgentSession record)
- [ ] `dev_agent_id`, `runner_cwd`, `claude_version`, and the turn-history mirror are visible on `dashboard.json` for a live eng session
- [ ] A steering message during a long Dev turn preempts it within the debounce window and the resumed turn reflects the steer
- [ ] Worker starts and serves ENG sessions with ollama stopped (degraded-mode machinery gone)
- [ ] A turn whose subprocess dies produces `exit_reason=error` and a persona-safe user message — never `completed` (the #1916 class)
- [ ] `TOOL_TIMEOUT_DEFAULT_SEC` default is 300 again
- [ ] Tests pass (`/do-test`); lint/format clean
- [ ] Documentation updated (`/do-docs`); features index has no granite-PTY entries
- [ ] A boilerplate idle Notification never reaches an outbound chat message, and a `[/user]` answer coinciding with one is delivered (the #1919 class)
- [ ] Implementation PR closes #1924, #1918, #1919, #1921, #1724, #1879 (task 5 deletes the #1724/#1879 wedge-nudge implementation surface) and comments-supersedes #1721

## Team Orchestration

### Team Members

- **Builder (session-runner)** — Name: runner-builder — Role: graduate module + runner loop + preempt + resume (tasks 1-3) — Agent Type: builder — Resume: true
- **Builder (integration)** — Name: integration-builder — Role: executor/worker/health/telemetry rewiring + deletions (tasks 4-6) — Agent Type: builder — Resume: true
- **Builder (config-docs)** — Name: config-builder — Role: settings, scripts, update system, pyproject, prime commands (task 7) — Agent Type: builder — Resume: true
- **Test engineer** — Name: test-builder — Role: test deletions/updates/new coverage (task 8) — Agent Type: test-engineer — Resume: true
- **Validator (cutover)** — Name: cutover-validator — Role: verify each phase + final — Agent Type: validator — Resume: true
- **Documentarian** — Name: docs-writer — Role: Documentation section — Agent Type: documentarian — Resume: true

### Available Agent Types
Tier 1 core as declared in the template; domain framing for async/concurrency (the preempt watcher) per `DOMAIN_FRAMING.md`.

## Step by Step Tasks

### 1. Graduate the survivors into `agent/session_runner/`
- **Task ID**: build-graduate
- **Depends On**: none
- **Validates**: tests/unit/session_runner/ (relocated test_headless_role_driver.py passes against new paths)
- **Informed By**: audit-1 (graduate set + the two coupling severs)
- **Assigned To**: runner-builder — **Agent Type**: builder — **Parallel**: false
- Create `agent/session_runner/` with `role_driver.py`, `hook_edge.py`, `hook_forwarder.py`, `transcript_tailer.py`, `router.py` (classify_pm_prefix minus `_strip_ansi`, RouteOutcome/exit-classification tables), `adapter.py` (delivery callbacks, resume-scalar persistence — `_persist_resume_handles` reworked to the four-scalar shape, not graduated verbatim, `_publish_exit_summary`, `_transcript_path_from_spec`)
- Sever the two audited couplings; PermissionRequest hook registration dropped from `generate_hook_settings` (doesn't fire under -p); `hook_forwarder` path constant updated
- **Absorb the #1919 fix into graduated `hook_edge`:** remove `"Notification"` from `_NEEDS_HUMAN_EVENTS`; add content-aware classification — a Notification carrying known Claude Code boilerplate (exact idle string "Claude is waiting for your input", permission-phrasing prefix) or an empty message emits **no edge**; substantive Notifications remain `needs_human`. One central boilerplate constant, conservative matching. In the runner/driver reconciliation, prefer a `turn_end` edge over a `needs_human` edge when both arrive in one poll batch (inverts the ordering bug that swallowed the answer). Port the #1919 plan's test list against the graduated module
- Explicit auth injection (G5): subprocess env sets `CLAUDE_CODE_OAUTH_TOKEN`, strips `ANTHROPIC_API_KEY`; never `--bare`
- The `[/dev:pi]` harness-suffix routing and `PiSubprocessBuilder`/`parse_pi_final_text` do **not** graduate — zero production consumers outside the package (grep-verified at plan time); Dev runs on the claude harness only
- Old package untouched in this task (deletion is task 6)

### 2. Build the runner: single-session turn loop + `dev` subagent definition + steer-preempt
- **Task ID**: build-runner
- **Depends On**: build-graduate
- **Validates**: tests/unit/session_runner/test_runner_turns.py, test_runner_dev_subagent.py, test_runner_preempt.py (create)
- **Informed By**: audit-5 (G1; G3 dissolved by D1 amendment), D1-amended (spike #1928), D4; Race 1
- **Assigned To**: runner-builder — **Agent Type**: builder — **Parallel**: false
- `runner.py`: single-session turn loop for ALL session types (PM turn → simplified route: `[/user]` deliver / `[/complete]` wrapup / else continue), wrapup guard graduated in, per-turn progress hook, steering boundary drain; role-aware turn timeout (eng generous — Dev work runs inside the PM turn; env-overridable, provisional)
- Author `.claude/agents/dev.md` from the dev prime command + shared rails, with the steering/continuation protocol **baked into the definition** (spike gotcha 1: post-hoc overrides are refused); update the PM prime to spawn `dev` on first need, report its agent id, and continue the SAME agent on later turns
- Preempt watcher with generation-token guard, steer debounce (3s default; env-overridable constant, provisional/tunable), SIGTERM→grace→SIGKILL, `turn_end_source="preempted"`
- Subprocesses in own process group; PID/PGID recorded pre-await (Race 2)

### 3. Simple resume: consume persisted handles
- **Task ID**: build-resume
- **Depends On**: build-runner
- **Validates**: tests/unit/session_runner/test_runner_resume.py (create)
- **Informed By**: audit-5 (G4), D3; Research (cwd-scoped resume); Race 3
- **Assigned To**: runner-builder — **Agent Type**: builder — **Parallel**: false
- Persist the four spike-#1928 scalars on `AgentSession` (owner mandate): `claude_session_id` (reuse the existing `claude_session_uuid` field), `dev_agent_id`, `runner_cwd`, `claude_version` — nullable adds, no backcompat code needed (`_heal_descriptor_pollution` walks fields generically); runner init validates + consumes (seed `--resume`, skip prime, re-introduce `dev_agent_id` in the resume prompt); stale/invalid → cold start
- Mirror a compact **turn history** per turn by extending the existing session-event stream: `{ts, actor: pm|dev, text}` with full user-visible texts, tool-noise excluded, length env-capped — dashboard observability plus a recovery seed if on-disk transcripts are GC'd (transcripts stay the source of truth)
- **Capture-at-init (Race 5, critique C1):** persist the new turn's `claude_session_id` as soon as the stream-json `system/init` event is parsed — *before* awaiting `result` — so a preempted or killed turn's partial transcript remains the resume target, never the stale pre-turn uuid
- Crash-recovery/user-reply paths pass the reply/steer as the resumed first message; stall-classifier resumability no longer hardcodes granite-non-resumable (unblocks the #1917 class)

### 4. Rewire executor + worker; delete the transport seam and ollama gate
- **Task ID**: build-integrate
- **Depends On**: build-runner
- **Validates**: tests/unit/test_session_executor_granite.py (updated), tests/integration/test_transport_dispatch_e2e.py (updated)
- **Informed By**: audit-2 (dispositions a/c), D2
- **Assigned To**: integration-builder — **Agent Type**: builder — **Parallel**: false
- `session_executor.py`: granite leg → SessionRunner; delete `_resolve_role_transports`, the pm-coercion guard, PTYPool imports
- `worker/__main__.py`: delete `ensure_granite_model` probe/breaker/reprobe/deferred-resume, `verify_tui_marker_contract`, PTY pool init/orphan-kill, `_fleet_has_pty_transport_role`
- Delete `session_pickup` granite-degraded deferral, `session_state.granite_available`, `bridge/config_validation.validate_transport` (the `transport` keys in `~/Desktop/Valor/projects.json` are NOT removed at build time — that edit is sequenced into the post-merge fleet deploy so pre-cutover machines aren't stranded mid-rollout; see No-Gos [ORDERED] entry)
- **Retain `models/child_session_gate.py`** (external critique C4): re-enabling child-session fanout is a real behavior change with no named replacement bound. Rewrite its docstring to the post-PTY rationale (semantic redundancy + no independent fanout cap until #1633's subagent refactor lands or #1926 names a cap); removal is deferred, not smuggled into this cutover
- `reflections/stall_advisory.py` repointed to session_runner

### 5. Health, stall, telemetry, model migration
- **Task ID**: build-health
- **Depends On**: build-integrate
- **Validates**: tests/unit/test_session_health_tool_timeout.py (updated), test_session_stall_classifier.py (updated), tests/unit/session_runner/test_runner_liveness.py (create)
- **Informed By**: audit-2 (disposition b/d), audit-3
- **Assigned To**: integration-builder — **Agent Type**: builder — **Parallel**: false
- `session_health.py`: delete `_pty_quiescent_long_enough`, `_is_granite_pty_session`, `_prime_pty_alive`, wedge-nudge producer + `_eval_mid_run_pty_stage1`, the three wedge constants; revert `TOOL_TIMEOUT_DEFAULT_SEC` → 300
- `steering.py`: delete the wedge-nudge channel (:267-540); `session_stall_classifier.py`: delete `granite_wedged` + PTY-field probes; `agent_session_queue.py`: drop granite deconfliction; `tool_budget.py`: un-gate granite caveats; `crash_signature.py`: drop plateau producer mapping
- `models/agent_session.py`: remove PTY fields (`pm_pty_pid`, `dev_pty_pid`, `pty_slot`, `last_pty_read_loop_at`, `last_pty_activity_at`, `mid_run_quiescent_since`, `mid_run_pty_snapshot`, `role_transports`) **and `resume_handles`** (superseded by the four flat scalars — leaving it would resurrect the G4 orphaned-field defect), update `ADD_ONLY_LIVENESS_FIELDS`; ORM-safe migration in `scripts/update/migrations.py` (registered, idempotent)
- `ui/data/sdlc.py` + `ui/app.py`: drop PTY mirrors, tolerate absent fields on old records; review `monitoring/worker_watchdog.py` U-state rationale (relax, don't blindly delete)

### 6. Delete the substrate
- **Task ID**: build-delete
- **Depends On**: build-integrate, build-health
- **Validates**: Verification table inverse rows
- **Assigned To**: integration-builder — **Agent Type**: builder — **Parallel**: false
- `git rm -r agent/granite_container/ tools/granite_loop/`; delete the PTY probe/spike/smoke/monitor scripts **unconditionally**: `granite_tui_pty_spike*.py` (×3), `granite_smoke_test.py`, `granite_long_hold_monitor.py`, and `scripts/probe_slash_arguments.py` (external blocker B1: it drives the real TUI via pexpect — its purpose dies with the TUI; the slash-prime path is already empirically verified under `-p` at `role_driver.py:56-62`. Unconditional deletion supersedes the internal pass's audit-and-maybe-keep disposition)
- Repo-wide grep sweep: zero remaining `granite_container` / `pexpect` / wedge-nudge / transport-seam references (loop-wedge family explicitly preserved — audit-2 out-of-scope list)

### 7. Config, scripts, update system, prime commands
- **Task ID**: build-config
- **Depends On**: build-delete
- **Validates**: scripts/check_prerequisites.py passes; `python -m tools.doctor --quick` clean
- **Informed By**: audit-3
- **Assigned To**: config-builder — **Agent Type**: builder — **Parallel**: false
- `config/settings.py`: `GraniteSettings` → `SessionRunnerSettings` (keep `pm_model`, `dev_model`, `hook_turn_end_wait_s`, `hook_crash_resume_cap`; supervisor trio unchanged; delete pool/transport/flag fields + breaker/reprobe if the update-gate re-scope removes their consumer); `.env.example` updated
- `pyproject.toml`: remove `valor-granite-loop`, `pexpect`, **`ptyprocess`** (`pyproject.toml:37`, "Low-level PTY spawn backing pexpect" — PTY-only, no other consumer; critique C2), `granite_integration` marker
- **Stale-prefix guard (hard requirement, was Open Question 3):** settings load warns loudly on any legacy `GRANITE__*`/`GRANITE_*` env key; `scripts/update/run.py` surfaces the warning during deploy
- Update system changes per ## Update System (run.py, verify.py, hardlinks.py rename + stale removal, migrations.py already in task 5)
- `.claude/commands/granite/` → `.claude/commands/roles/`: scrub PTY framing from the four prime commands (persona content unchanged); update `role_driver` prime-path constants
- Vault: revert `~/Desktop/Valor/.env` `TOOL_TIMEOUT_DEFAULT_SEC` to 300

### 8. Test suite reshape
- **Task ID**: build-tests
- **Depends On**: build-delete
- **Validates**: full suite via `scripts/pytest-clean.sh tests/`
- **Informed By**: audit-4 (## Test Impact dispositions)
- **Assigned To**: test-builder — **Agent Type**: test-engineer — **Parallel**: true (with build-config)
- Execute every ## Test Impact checkbox; salvage `headless_hook_probe.py`; write the four new session_runner test files

### 9. Supersede bookkeeping
- **Task ID**: build-supersede
- **Depends On**: build-config
- **Assigned To**: config-builder — **Agent Type**: builder — **Parallel**: true
- `docs/plans/granite_lossless_checkpoint_resume.md` → `status: Cancelled`, superseded-by note; comment on #1721 and #1921 pointing here (closure via the PR body: Closes #1924, Closes #1918, Closes #1919, Closes #1921). `idle-notification-verbatim-delivery.md` already Cancelled at plan time (absorbed into task 1); comment on #1917 (task 3 resolves its non-resumable-classification half; the reflection-scheduling half is re-triaged post-cutover); comment on #1633 (D1-amended topology delivers its prescribed subagent direction for Dev; gate removal remains its scope)

### 10. Validate cutover
- **Task ID**: validate-cutover
- **Depends On**: build-resume, build-tests, build-config, build-supersede
- **Assigned To**: cutover-validator — **Agent Type**: validator — **Parallel**: false
- Run the full Verification table; live smoke: dispatch a real eng session locally (worker running), observe PM prime → `dev` subagent work → delivery with `ps` proving zero PTY children; steer it mid-turn and confirm preempt + resume
- **Worker-restart continuation smoke (third-pass concern; backs Risk 5a + Success Criterion 3):** mid-session, restart the worker; confirm the session resumes via the persisted scalars, the SAME `dev_agent_id` is continued (Dev recalls state established before the restart), and the turn-history mirror is intact

### 11. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-cutover
- **Assigned To**: docs-writer — **Agent Type**: documentarian — **Parallel**: false
- Execute every ## Documentation checkbox

### 12. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cutover-validator — **Agent Type**: validator — **Parallel**: false
- All Success Criteria + Verification rows; restart services (`./scripts/valor-service.sh restart`) and confirm `dashboard.json` healthy with no PTY fields

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Substrate gone | `test -d agent/granite_container` | exit code 1 |
| No dangling imports | `grep -rn "granite_container" --include='*.py' agent/ worker/ bridge/ tools/ reflections/ models/ ui/ config/ scripts/ \| wc -l` | match count == 0 |
| pexpect gone (code) | `grep -rn "pexpect" --include='*.py' agent/ worker/ bridge/ tools/ scripts/ tests/ monitoring/ \| wc -l` | match count == 0 |
| pexpect gone (deps) | `grep -c "pexpect" pyproject.toml` | match count == 0 |
| ptyprocess gone (deps) | `grep -c "ptyprocess" pyproject.toml` | match count == 0 |
| No live GRANITE__ readers | `grep -rn "GRANITE__" --include='*.py' agent/ worker/ bridge/ models/ ui/ \| wc -l` | match count == 0 |
| Transport seam gone | `grep -rn "PM_TRANSPORT\|DEV_TRANSPORT\|role_transports" --include='*.py' agent/ worker/ bridge/ config/ models/ \| wc -l` | match count == 0 |
| Wedge-nudge gone | `grep -rn "wedge_nudge" --include='*.py' agent/ \| wc -l` | match count == 0 |
| Stopgap reverted | `grep -c 'TOOL_TIMEOUT_DEFAULT_SEC", 3000' agent/session_health.py` | match count == 0 |
| Runner exists + PM dispatch | `grep -c "run_turn" agent/session_runner/runner.py` | output > 0 |
| Dev agent definition exists | `test -f .claude/agents/dev.md` | exit code 0 |
| Loop-wedge family preserved | `grep -c "loop_wedged" monitoring/bridge_watchdog.py` | output > 0 |
| Anti-criterion: no #1923 scope creep | `grep -c "OLLAMA_CLASSIFIER_MODEL" bridge/routing.py` | output > 0 |
| Anti-criterion: no PTY fallback branch | `grep -rniE "fallback.*\bpty\b|\bpty\b.*fallback" --include='*.py' agent/ worker/ \| wc -l` | match count == 0 |
| Tests pass | `scripts/pytest-clean.sh tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

Two same-day critique passes (2026-07-06), both addressed in this revision (`revision_applied: true`). Where they disagreed on `scripts/probe_slash_arguments.py` (internal: audit-and-maybe-keep; external: unconditional delete), the external blocker's **unconditional delete** won — it resolves the pexpect contradiction outright rather than managing it.

**Post-critique amendment:** D1 was amended by the owner *after* both passes (single Opus PM session + resumable `dev` subagent, evidence spike #1928 — see the Locked Decisions table). A third, targeted re-critique of the amended surfaces ran the same day (below); all its findings are addressed in this document.

### Third pass — targeted re-critique of the D1 amendment — verdict: NEEDS REVISION (1 blocker, 5 concerns; all addressed same-day)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | All three (converged) | Resume-mechanism three-way contradiction: D3 bullet/Failure Path/task 1 still carried the per-role `resume_handles` shape while tasks 3/5 specified four flat scalars — a literal build would leave `resume_handles` as a live-but-orphaned field, resurrecting the G4 defect this plan exists to kill | D3 bullet, Failure Path bullet, task 1 adapter bullet all rewritten to the four-scalar shape; `resume_handles` added to task 5's deletion list | End state has exactly four flat scalars + the bounded mirror; no handles list anywhere |
| CONCERN | Risk & Robustness | `dev_agent_id` captured from PM prose "at spawn" — the late-capture pattern Race 5 exists to eliminate; a preempt mid-Dev-spawn would leave it null | Data Flow §7 + Race 5 rewritten: structural capture via sidechain-directory scan (file exists from spawn), post-turn AND post-preempt | Never parse agent ids from prose |
| CONCERN | Risk & Robustness | Eng-turn timeout expiry behavior undefined — could hard-error and discard hours of Dev work | Technical Approach bullet: timeout expiry = graceful preempt (`turn_end_source="timeout"`), partial work preserved, needs-attention surfacing | Same SIGTERM→grace→SIGKILL path as D4 |
| CONCERN | Risk & Robustness | Task 10 omitted the worker-restart continuation smoke promised by Risk 5a and Success Criterion 3 | Task 10 gains an explicit restart-continuation smoke step | Same-`dev_agent_id` recall assertion |
| CONCERN | Scope & Value | Turn-history mirror in tension with D3 simplicity + the lossless-resume Rabbit Hole | Rabbit Hole bullet reconciled: mirror is owner-mandated bounded observability, never read on the normal resume path | Resume-replay from the mirror is explicitly the rabbit hole |
| CONCERN | History & Consistency | `runner.py` called "the new relay loop" two lines from "No relay loop at all" | Key Elements bullet: "the single-session turn loop" | Terminology sweep done |

### Internal war-room pass — verdict: READY TO BUILD (with concerns)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | History & Consistency | "Stopgap reverted" verification row grepped for quoted `"3000"`, which never matches the unquoted literal at `session_health.py:407` — false green by construction | Verification table row updated | Command is now `grep -c 'TOOL_TIMEOUT_DEFAULT_SEC", 3000' agent/session_health.py` expecting 0 (absence of the old default) |
| CONCERN | History & Consistency | Prerequisite check loaded `.env` from cwd, which fails in `.worktrees/{slug}/` (no `.env` symlink) — false prerequisite failure on every build run | Prerequisites table row 1 updated | Check now loads `$HOME/Desktop/Valor/.env` (vault path) directly |
| CONCERN | History & Consistency | Task 5 deletes the #1724 mid-run quiescence constants and #1879 wedge-nudge channel without Prior Art or closure bookkeeping | Prior Art bullet added; Success Criteria closure line updated | PR body now carries Closes #1724, Closes #1879; task 5 (build-health) deletes their implementation surface |
| CONCERN | Risk & Robustness (Adversary) | Task 10 (validate-cutover) did not depend on build-resume, so a DAG scheduler could validate cutover before resume consumption landed | Task 10 Depends On updated | `Depends On: build-resume, build-tests, build-config, build-supersede` |
| CONCERN | Risk & Robustness (Operator) | "pexpect gone (code)" grep excluded `scripts/` and `tests/`, where known pexpect importers live (`probe_slash_arguments.py`, `test_session_executor_granite.py`) — possible false green | Verification row extended | Grep now covers `agent/ worker/ bridge/ tools/ scripts/ tests/ monitoring/`; the audit-and-keep option for `probe_slash_arguments.py` was superseded by the external pass's unconditional delete |
| CONCERN | Risk & Robustness (Operator) | Removing `transport` keys from iCloud-synced `projects.json` at build time would strand pre-cutover machines during the staged rollout | Task 4 checklist amended; No-Gos [ORDERED] Fleet deploy entry expanded | Vault-config edit sequenced after the bridge-role machine's `/update` + E2E probe pass |

### External reviewer pass — verdict: NEEDS REVISION (1 blocker)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | `probe_slash_arguments.py` conditional keep contradicts pexpect removal; verification grep blind to `scripts/` | Task 6: unconditional delete; Verification row widened | Critique's claim that `agent/session_health.py` imports pexpect re-verified FALSE at revision time — no change needed there |
| CONCERN | Risk & Robustness | Missing race: mid-turn preempt leaves resume pointer on stale pre-turn uuid | Race 5 added; task 3 capture-at-init bullet | Fake-CLI test: emit `system/init` then hang; assert persisted id == new turn's id post-preempt |
| CONCERN | History & Consistency | `ptyprocess>=0.7.0` dangling post-cutover | Task 7 removal + Verification row | `pyproject.toml:37`; no consumer outside pexpect |
| CONCERN | Scope & Value | Env-prefix rename creates silent-failure mode via stale vault overrides | Rename kept (owner clarity mandate); stale-prefix guard made a hard task 7 requirement | Loud settings-load warning + `/update` surfacing; was Open Question 3 |
| CONCERN | Scope & Value | `child_session_gate.py` deletion is an unexamined behavior change | Task 4: gate retained, docstring rewritten; removal deferred to #1633/#1926 | Gate rationale verified: pool scarcity (dies) + semantic redundancy (survives) |
| CONCERN | Structural 2b | `build-resume` orphaned in task graph | Task 10 Depends On += build-resume | Same finding as internal Adversary row — converged independently |
| NIT | Scope & Value | #1917 cited but untracked | Prior Art entry + task 9 comment bullet | Only the non-resumable-classification half resolves here; no Closes claim |
| NIT | Structural 2c | Five worker test files mislabeled `tests/unit/` | Test Impact paths corrected to `tests/integration/` | `test_pi_builder_e2e.py` also flipped UPDATE→DELETE (pi harness dies) |
| NIT | Structural 2c | `session_watchdog.py` cited without directory | audit-2 text: `monitoring/session_watchdog.py` | Keeps task 6's preservation grep accurate |

---

## Open Questions

None — all three original questions were resolved in the revision pass: (1) steer-preempt debounce set to 3s default, env-overridable (task 2); (2) update-gate Step 4.75 deleted now, re-added under #1923 only if needed (Update System); (3) env-prefix rename kept with a mandatory stale-prefix guard (task 7, Critique Results).
