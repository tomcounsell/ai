---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1370
last_comment_id: 4786392583
---

# Consolidate Agent-Message-Delivery Send Paths

## Problem

The 2026-05-10 daily integration audit (issue #1370) found multiple doors into
the outbound-message outbox, each with a different post-processing pipeline,
and no document declaring which path is canonical for which caller. Since
filing, the divergence has both *narrowed* (PR #1382 routed the stop-hook
tool-call path through the canonical handler; PR #1685 turned the drafter into
a verbatim pass-through + validation filter) and *widened* (PR #1738 added
health-checker recovery notices; issues #1730/#1794/#1797 added a synchronous
terminal-status flush that writes raw payloads to the outbox).

**Current behavior** (verified against `main` @ `e7a7f987`, see Freshness
Check): agent-authored text can reach the user through paths with three
different filter postures. `tools/send_telegram.py` — still the tool the
eng/PM system prompt teaches for proactive sends (`agent/sdk_client.py:3527`,
`3596`, `3634`; `config/personas/engineer.md:476`) — runs the drafter but
**ignores its `needs_self_draft` verdict**, skips the redundancy filter and
RTR, and raw-rpushes to `telegram:outbox`. A wire-format violation or empty
promise that the canonical handler would bounce back to the agent for
self-draft sails straight to Telegram on this path. Meanwhile the same module
is labeled "legacy" in `agent/hooks/stop.py:58-60` yet actively taught to
every collaboration-mode session. Vocabulary for the stop-hook gate drifts
across four spellings, and the gate's failure modes (drafter exception, worker
restart between stops, malformed transcript tail) are undocumented.

**Desired outcome:** One declared canonical pipeline for agent-authored text;
one grep-able, documented seam for the only sanctioned bypass class
(system-authored canned notices); `tools/send_telegram.py` deleted with its
unique capabilities migrated; a delivery-path registry in
`docs/features/agent-message-delivery.md` naming every remaining path, its
caller, and its filters; canonical vocabulary; failure-mode documentation; and
a contract test per delivery path asserting input → outbox payload with the
real handler.

## Freshness Check

**Baseline commit:** `e7a7f987c3a05992acbe5d3e246b1879d505eace`
**Issue filed at:** 2026-05-10 (re-scoped to consolidation plan by comment on 2026-06-24)
**Disposition:** Minor drift (issue's inventory table is stale in three ways; the design problem stands)

**File:line references re-verified:**

- `tools/send_message.py:71-145` — issue claimed "no drafter, no RTR, no redundancy filter" — **DRIFTED / already fixed**: PR #1382 (commit `97a6cd8f`) rewrote the tool to route through `TelegramRelayOutputHandler.send`. Today it runs linkify + promise gate CLI-side (`tools/send_message.py:212-225`), then delegates to the handler at `tools/send_message.py:257-270` (telegram) and `:348-360` (email). Full pipeline applies.
- `tools/send_telegram.py:71-99` — claim "drafter only" — **still holds** (`_draft_text` at `tools/send_telegram.py:71-99`; raw rpush at `:211-217`). Additional finding: `_draft_text` returns the *original* text when `draft.text` is empty (the drafter's blocking `needs_self_draft` signal), so validation verdicts are silently discarded on this path.
- `agent/output_handler.py:301-552` — **drifted to** `TelegramRelayOutputHandler.send` at `agent/output_handler.py:346-790`: drafter (hoisted, once, `:403-462`) → self-draft steering (`:434-441`) → redundancy filter (`:513-616`, SDLC sessions) → RTR (`:618-724`, env-gated) → transport branch + outbox rpush (`:739-790`). Claims hold.
- `bridge/email_bridge.py:575-577` — **drifted to** `EmailOutputHandler.send` at `bridge/email_bridge.py:810+`; drafter call at `:923-927`. Runs the drafter only — no self-draft steering, no redundancy filter, no RTR — and sends via direct SMTP (not the outbox).
- `bridge/telegram_bridge.py:2673` — **drifted to** `:2873-2940`: the bridge's registered send callback wraps `handler.send` (adds `filter_tool_logs`, PM self-messaging bypass, `<<FILE:>>` extraction).
- `config/personas/project-manager.md:221` — **gone** (persona renamed); the live references are `config/personas/engineer.md:476` and three prompt blocks in `agent/sdk_client.py` (~`:3520-3645`).
- `agent/session_health.py::_deliver_tool_timeout_degraded_notice` (PR #1738, now merged; `agent/session_health.py:1795-1873`) — the issue comment claimed it "bypasses everything." **Partially stale**: it resolves the send callback via `_resolve_callbacks` (`agent/agent_session_queue.py:1266`), which in the worker process returns `TelegramRelayOutputHandler.send` (registered at `worker/__main__.py:892-901`) — so the canned notice *does* traverse the full filter stack; only the no-callback fallback (`FileOutputHandler`) bypasses it. Two sibling call sites share the pattern: `_deliver_deferred_self_draft_fallback` (`agent/session_health.py:2109-2121`) and the fan-out completion path (`:3598-3617`).
- **New since filing:** `flush_deferred_self_draft_sync` (`agent/session_health.py:1876-1990`, issues #1794/#1797) is a genuinely raw outbox writer — synchronous `rpush` with no drafter/redundancy/RTR, justified by running in a no-event-loop context. It builds the telegram payload inline (the email branch reuses `build_email_outbox_payload`).
- Pattern 4's claim "no doc for `bridge/redundancy_filter.py`" — **stale**: `docs/features/drafter-redundancy-suppression.md` exists and is indexed in `docs/features/README.md:58`.

**Cited sibling issues/PRs re-checked:**
- #1369 — CLOSED, fixed by PR #1382 (path 1 consolidation). Its fix is the template this plan extends.
- PR #1685 / #1680 — MERGED (`513d8eac`): drafter is verbatim pass-through + validation; `needs_self_draft` routes a steering nudge instead of a rewrite.
- PR #1738 — MERGED: degraded-notice path exists as described above.
- PR #1415 — MERGED: `build_teammate_instructions()` now uses TOOL POSTURE / OPERATIONAL WORK ENCOURAGED / WHEN BLOCKED blocks; `tests/unit/test_qa_handler.py` already asserts them, and its DELIVERY REVIEW section assertions are unaffected.

**Active plans in `docs/plans/` overlapping this area:** none (checked the ten most recently modified plans; nearest neighbors are session-lifecycle/granite plans that do not touch the send paths).

**Notes:** Because path 1 is already consolidated, this plan's code scope shrinks to: retiring `send_telegram.py`, naming/centralizing the system-notice bypass seam, vocabulary + docs, and the contract-test suite.

## Prior Art

- **Issue #641 / `docs/plans/unify-telegram-send.md` (Done)** — unified the earlier `send_telegram.py` vs `valor-telegram send` confusion by giving the PM tool `--file` support instead of teaching two tools. Lessons carried forward: (1) prompt-surface audits are load-bearing — the agent invents hybrid syntax when prompts conflict; (2) the Redis queue path is load-bearing for `has_pm_messages()` tracking; (3) `valor-telegram send` is the *operator* CLI, deliberately not an agent delivery path.
- **Issue #1369 / PR #1382** — routed `tools/send_message.py` through the canonical handler. Proves the wrapper pattern this plan finishes: CLI keeps env validation, linkify, and promise gate; handler owns everything else.
- **Issue #1680 / PR #1685 (`513d8eac`)** — drafter rewrite deleted `_draft_with_haiku`/`_draft_with_openrouter`; `draft_message()` is verbatim pass-through + validation. This removes the historical reason for `send_telegram.py --no-draft` (there is no LLM rewrite to skip anymore).
- **Issue #1205** — redundancy filter; **#1193** — RTR; **#1730/#1794/#1797** — deferred self-draft persistence and terminal flushes. These define the filter stack the registry must document.
- **PR #1738** — tool-timeout degraded notice; its issue comment explicitly asked this plan to answer "when is it correct to bypass the filter stack?"

## Research

No relevant external findings — this is purely internal consolidation of repo-owned delivery plumbing; no external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

Current outbound flows for agent-authored text (verified):

1. **Stop-hook tool-call path (canonical CLI):** agent invokes `python tools/send_message.py "text"` during the delivery review gate → linkify → promise gate → Popoto session lookup (fail-closed) → `TelegramRelayOutputHandler.send` → drafter validation → self-draft steering → redundancy filter → RTR → `telegram:outbox:{sid}` / `email:outbox:{sid}` → relay (Telethon / SMTP relay) → user. Relay records `pm_sent_message_ids` and `recent_sent_drafts` post-send (`bridge/telegram_relay.py:846-866`).
2. **Proactive PM path (to be retired):** agent invokes `python tools/send_telegram.py "text"` mid-session → `_draft_text` (validation verdicts discarded) → linkify → 4096-char truncate → promise gate → raw `rpush telegram:outbox:{sid}` → relay → user.
3. **Worker silent path:** session return text → worker `send_cb` = `TelegramRelayOutputHandler.send` (telegram/default) or `EmailOutputHandler.send` (email transport; drafter only, direct SMTP with retry + DLQ).
4. **Health-checker/recovery notices:** `agent/session_health.py` composes a canned string → `_resolve_callbacks` → `handler.send` in the worker (full stack) or `FileOutputHandler` fallback; plus `flush_deferred_self_draft_sync` writing raw payloads synchronously at the `finalize_session` chokepoint.

Target flow after this plan: paths 1 and 3 unchanged (declared canonical); path 2 deleted (callers taught path 1's tool); path 4's async call sites route through one named helper (`deliver_system_notice`) and the sync flush reuses one shared payload builder, with both declared in the registry.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #527 / #641 (send_telegram evolution) | Made `send_telegram.py` the PM tool, later added `--file` | Solved tool *confusion* but entrenched a second pipeline; filters added later (#1205, #1193, #1685 steering) landed only in the handler, so the PM path silently fell behind |
| PR #1382 (#1369) | Routed `tools/send_message.py` through the canonical handler | Fixed one of the divergent paths but left `send_telegram.py` untouched and still taught by the system prompt; no policy stopped new paths from appearing (PR #1738 added one weeks later) |
| stop.py "legacy" labeling | Marked `send_telegram.py` legacy in the classifier | A label without a migration: the prompt surfaces kept teaching the tool, so "legacy" accrued new callers |

**Root cause pattern:** filters are added at the handler, but nothing forces callers *through* the handler, and no written policy says who may bypass it. Consolidation without a declared policy re-diverges — this plan ships the policy (registry + one bypass seam) alongside the code change.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `TelegramRelayOutputHandler.send` gains a return value (`DeliveryOutcome` enum: `sent | suppressed_redundant | suppressed_rtr | deferred_self_draft | dropped_empty`) — currently returns `None`, so all existing callers remain valid (additive). `tools/send_telegram.py` is deleted; its `--emoji` capability moves to `tools/react_with_emoji.py --standalone`. New module-level helper `deliver_system_notice(entry, message)` plus shared `build_telegram_outbox_payload(...)` in `agent/output_handler.py`.
- **Coupling:** decreases — `agent/session_health.py` loses three hand-rolled callback-resolution blocks; `agent/sdk_client.py` prompt blocks reference one tool instead of two.
- **Data ownership:** unchanged — relay still owns sends and post-send recording; outbox payload shape unchanged (contract-tested).
- **Reversibility:** moderate — deleting `send_telegram.py` is a hard cutover (per NO LEGACY CODE TOLERANCE); reverting means restoring the file and prompt blocks from git history.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm the two Open Questions)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies; everything runs against repo-local code, Redis, and the existing test harness.

## Solution

### Key Elements

Four decisions, then the mechanical work that follows from them:

- **Decision A — canonical path.** `TelegramRelayOutputHandler.send` is the single queue-side pipeline for agent-authored outbound text (both transports); `tools/send_message.py` is the single agent-facing CLI wrapper. This ratifies what PR #1382 built and extends it to every remaining caller.
- **Decision B — bypass rule.** *Agent-authored content always traverses the canonical handler. Only system-authored canned notices — fixed strings composed by infrastructure code, containing no agent-generated text — may be delivered outside the CLI wrapper, and only via the named helper `deliver_system_notice()`.* The helper wraps today's `_resolve_callbacks` + `FileOutputHandler` fallback + telemetry pattern, making every bypass enumerable by a single grep. (In the worker the resolved callback is still `handler.send`, so notices retain outbox delivery; the *policy* point is that no new code hand-rolls callback resolution.) `flush_deferred_self_draft_sync` is declared the one sanctioned synchronous outbox writer (no event loop at the `finalize_session` chokepoint) and switches to the shared payload builder so the wire shape is defined once.
- **Decision C — retire `tools/send_telegram.py`.** Full cutover, no transition shims: delete the file; teach `tools/send_message.py` in the three `agent/sdk_client.py` prompt blocks, `config/personas/engineer.md:476`, and `.claude/skills/telegram/SKILL.md`; migrate `--emoji` (standalone custom-emoji message) to `tools/react_with_emoji.py --standalone`; drop `--react` (already owned by `react_with_emoji.py`) and `--no-draft` (obsolete — the drafter no longer rewrites; verbatim system notices use `deliver_system_notice`); remove `_LEGACY_SEND_TELEGRAM_PATTERN` from `agent/hooks/stop.py`. `has_pm_messages()` / `recent_sent_drafts` tracking is unaffected: the relay records both from the outbox payload regardless of which tool queued it (`bridge/telegram_relay.py:846-866`).
- **Decision D — canonical vocabulary.** Gate concept: **"delivery review gate"** (already the module docstring; the UI label "DELIVERY REVIEW" and doc heading update to match). Outcome verbs: the classifier's four — **send / react / silent / continue** — plus the new `DeliveryOutcome` values for handler results; retire "send as-is" / "edit and send" as distinct terms (both are `send`).

- **Delivery-path registry**: a "Delivery paths" section in `docs/features/agent-message-delivery.md` — one table naming each remaining path, its caller, which filters apply, and *why* (including the two declared intentional divergences: `EmailOutputHandler.send`'s drafter-only direct-SMTP posture for worker email sessions, and `valor-telegram send` as the human-operator CLI that is not an agent delivery path).
- **`DeliveryOutcome` surfacing**: `handler.send` returns the outcome; `tools/send_message.py` prints it instead of an unconditional "Queued" (today a redundancy- or RTR-suppressed CLI send prints "Queued (N chars)" — misleading to the agent that called it).
- **Failure-modes documentation + tests**: a "Failure modes" section in the feature doc covering drafter exception in first stop, worker restart between stops (`_review_state` is process-local — gate re-presents; accepted behavior), malformed transcript tail, and simultaneous tool-call + continued work.
- **Contract tests**: per-path tests asserting input → outbox payload with the real handler (see Failure Path Test Strategy).

### Flow

Agent needs to message the user (any moment, any transport) → `python tools/send_message.py "text" [--file ...]` → canonical handler pipeline → outbox → relay → user. Infrastructure needs to deliver a canned notice → `deliver_system_notice(entry, message)` → resolved callback (handler in worker) or file fallback. There is no third door.

### Technical Approach

1. **`agent/output_handler.py`**: add `DeliveryOutcome` (`enum.StrEnum`); return it from `send()` at each exit (suppression exits, defer exit, outbox write). Extract `build_telegram_outbox_payload(chat_id, text, reply_to, session_id, file_paths)` from the inline payload dict (`:750-758`) and reuse it in `flush_deferred_self_draft_sync`. Add `async def deliver_system_notice(entry, message, *, telemetry_key: str | None = None)` encapsulating the `_resolve_callbacks` + `FileOutputHandler` fallback + WARNING-and-swallow contract currently duplicated at `agent/session_health.py:1839-1858`, `2109-2121`, and `3598-3617` (the fan-out site keeps its own completion-runner logic and uses the helper only for callback resolution if extraction is clean; otherwise leave it and document it in the registry — do not force-fit).
2. **`tools/send_message.py`**: print the returned `DeliveryOutcome` (exit 0 for suppress/defer — they are pipeline verdicts, not errors; the message tells the agent what happened).
3. **`tools/react_with_emoji.py`**: add `--standalone` (port of `send_emoji` from `send_telegram.py:314-383`, payload `type: custom_emoji_message` unchanged so the relay needs no changes).
4. **Delete `tools/send_telegram.py`**; sweep references: `agent/sdk_client.py` prompt blocks (~`:3520-3645`), `config/personas/engineer.md:476`, `.claude/skills/telegram/SKILL.md:18-36`, `agent/hooks/stop.py:56-60` + `classify_delivery_outcome`, `docs/tools-reference.md`, `docs/features/emoji-embedding-reactions.md`, `docs/features/README.md:62`, comment-level references in `agent/output_handler.py` / `agent/session_executor.py:1814` / `bridge/promise_gate.py:71` / `bridge/telegram_relay.py:5,564` / `bridge/telegram_bridge.py:2890`.
5. **Vocabulary sweep** (Decision D): `agent/hooks/stop.py` docstrings and `_build_review_prompt` header, `agent/teammate_handler.py` DELIVERY REVIEW section wording, `docs/features/agent-message-delivery.md` headings, test names/docstrings touched anyway by the retirement.
6. **Docs**: registry + failure-modes sections in `docs/features/agent-message-delivery.md`; cross-links to `bridge-worker-architecture.md`, `read-the-room.md`, `drafter-redundancy-suppression.md`, `promise-gate.md`, `session-steering.md` (closing Pattern 4).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `deliver_system_notice`: callback raises → logged WARNING, swallowed, file-fallback attempted — test asserts the log record and that no exception propagates (mirrors the existing never-raises contract at `agent/session_health.py:1810`)
- [ ] `handler.send` drafter exception → falls through to raw text and returns `DeliveryOutcome.sent` — existing behavior (`agent/output_handler.py:463-470`), new assertion on the return value
- [ ] Stop hook `_generate_draft` drafter exception → truncated raw tail used as draft (`agent/hooks/stop.py:158-160`) — test asserts gate still presents
- [ ] `tools/send_message.py` Popoto lookup failure → fail-closed exit 1 (existing tests keep covering this)

### Empty/Invalid Input Handling
- [ ] `classify_delivery_outcome` on malformed/garbage transcript tail → `silent`, never raises (extend `TestClassifyDeliveryOutcome` with binary-ish garbage input)
- [ ] `deliver_system_notice` with empty message → no send, debug log
- [ ] `react_with_emoji --standalone` with empty feeling → exit 1 (port of `send_telegram.py:346-348` behavior)

### Error State Rendering
- [ ] CLI suppression/defer verdicts print the outcome name to stdout (not a false "Queued") — test each `DeliveryOutcome` branch
- [ ] Worker-restart-between-stops: simulate by clearing `_review_state` between two stop invocations → gate re-presents (documented accepted behavior; the test pins it so a future change is deliberate)

### Contract tests (Pattern 2 — one per delivery path, real handler, fake Redis)
- [ ] CLI telegram: `send_message.py` main → `telegram:outbox:{sid}` payload shape (chat_id, reply_to, text, session_id, timestamp, file_paths)
- [ ] CLI email: → `email:outbox:{sid}` payload (reply-all `to`, subject, threading headers)
- [ ] Worker silent path: registered callback → same payload shape (extends `TestToolCallHandlerRouting`)
- [ ] System notice: `deliver_system_notice` with registered handler → outbox payload; with no registration → `FileOutputHandler` write
- [ ] Sync flush: `flush_deferred_self_draft_sync` telegram branch → payload built by `build_telegram_outbox_payload` (identical shape to handler writes)

## Test Impact

- [ ] `tests/unit/test_send_telegram.py` (entire file, ~30 tests) — DELETE with the tool; REPLACE coverage: queueing/validation/file/album behavior is already covered for the canonical tool in `tests/unit/test_tool_call_delivery.py::TestToolCallHandlerRouting` and the new contract suite; reaction tests (`TestSendTelegramReaction`) move to `tests/unit/test_react_with_emoji.py` alongside new `--standalone` tests
- [ ] `tests/unit/test_stop_hook_review.py::TestClassifyDeliveryOutcome::test_legacy_send_telegram` — DELETE: legacy pattern removed
- [ ] `tests/unit/test_stop_hook_review.py::TestBuildReviewPrompt` — UPDATE: prompt header/vocabulary assertions if wording changes in the sweep
- [ ] `tests/unit/test_tool_call_delivery.py::TestClassifyDeliveryOutcome::test_legacy_send_telegram_still_classifies_as_send` — DELETE: legacy pattern removed
- [ ] `tests/unit/test_tool_call_delivery.py::TestToolCallHandlerRouting` — UPDATE: extend into the per-path contract suite; assert `DeliveryOutcome` return values
- [ ] `tests/unit/test_output_handler.py` — UPDATE: `send()` return-value assertions on existing cases; payload-shape docstrings that cite `tools/send_telegram.py` (`:191`, `:212`) re-point to `build_telegram_outbox_payload`; add `deliver_system_notice` tests
- [ ] `tests/unit/test_qa_handler.py::test_no_send_telegram_instruction` — UPDATE: keeps passing (asserts absence), but re-point its docstring; the TOOL POSTURE / OPERATIONAL WORK ENCOURAGED / WHEN BLOCKED and DELIVERY REVIEW marker assertions from PR #1415 are unaffected unless the vocabulary sweep renames the DELIVERY REVIEW header — if so, UPDATE the marker string in the same commit
- [ ] `tests/unit/test_duplicate_delivery.py` — no change: it covers bridge catchup dedup and auto-continue guards, none of which touch the send tools or handler signature
- [ ] `tests/e2e/test_message_pipeline.py` — no change: it exercises the bridge router classifier (routing/mention/response decisions), not the delivery review gate or send tools
- [ ] `tests/unit/test_nightly_regression_tests.py` — no change: its `send_telegram` is `scripts/nightly_regression_tests.py`'s own function, unrelated to the tool
- [ ] `tests/integration/test_session_spawning.py:151` / `tests/unit/test_promise_gate_session_events.py:14` — UPDATE: comment/docstring references to `tools/send_telegram.py` re-point to `tools/send_message.py`

## Rabbit Holes

- **Unifying `EmailOutputHandler.send` (direct SMTP) with the email-outbox relay path.** Two email mechanisms genuinely exist (worker-registered SMTP handler vs. `email:outbox` + relay). Reconciling them touches retry/DLQ semantics and the email bridge lifecycle — a different blast radius entirely. This plan *documents* the divergence in the registry as intentional; it does not touch email transport code.
- **Making RTR/redundancy apply to email or to system notices.** The redundancy filter is SDLC-session-scoped and RTR is chat-snapshot-based by design; "filter parity everywhere" is not the goal — *declared* filter posture per path is.
- **Rewriting the review gate's `_review_state` to survive worker restarts** (Redis-backed state). The restart behavior (gate re-presents) is acceptable; document and pin it with a test instead of building persistence.
- **Touching `tools/valor_telegram.py`.** It is the operator/teammate CLI (Path B, `owner_agent_session_id`), deliberately outside the agent delivery pipeline since #641. Registry entry only.
- **Refactoring the bridge's `_make_send_cb` wrapper** (`bridge/telegram_bridge.py:2873`). Its extra layers (filter_tool_logs, PM bypass, file extraction) are bridge-process concerns; consolidating them into the handler is a separate design question. Registry entry only.

## Risks

### Risk 1: Proactive PM sends inherit new suppression behavior
**Impact:** After retirement, mid-session sends that used `send_telegram.py` route through the full pipeline — the redundancy filter or self-draft steering could suppress/defer a message the PM expected to land, and (pre-`DeliveryOutcome`) the agent would not know.
**Mitigation:** `DeliveryOutcome` surfacing in the CLI output is part of this plan precisely so the agent sees `suppressed_redundant` / `deferred_self_draft` and can react. The drafter is verbatim pass-through, so no text is altered. Contract tests assert each verdict's CLI output.

### Risk 2: Missed reference to the deleted tool breaks a prompt or hook at runtime
**Impact:** A stale `send_telegram.py` mention in a prompt teaches the agent a nonexistent tool (exactly the #641 failure class); a stale pattern in stop.py misclassifies delivery.
**Mitigation:** Verification table includes a repo-wide grep gate (`match count == 0` outside `docs/plans/`); the sweep list in Technical Approach step 4 was built from a live grep at plan time.

### Risk 3: `DeliveryOutcome` return value breaks a caller that treated `send()` as fire-and-forget
**Impact:** None expected — adding a return value to a previously-`None` coroutine is compatible with every `await ... send(...)` call site.
**Mitigation:** Grep-verify no call site does `assert result is None`; contract tests cover the worker callback, CLI, and bridge wrapper call shapes.

## Race Conditions

### Race 1: Terminal flush vs. async fallback double-send
**Location:** `agent/session_health.py:1876-1990` (sync flush) and `:2040-2136` (async fallback)
**Trigger:** Session with a pending deferred self-draft reaches a terminal status while the health checker also fires
**Data prerequisite:** `deferred_self_draft_pending` persisted in `extra_context`
**State prerequisite:** Existing transport/status gates (`flush` owns telegram + email-completed; async owns email failed/abandoned) plus distinct SETNX dedup keys
**Mitigation:** This plan does not change the gating or dedup keys — the refactor swaps only the payload construction (shared builder) and callback resolution (named helper). Contract tests assert the dedup keys are still consulted before any write.

### Race 2: Prompt-surface cutover vs. in-flight sessions
**Location:** `agent/sdk_client.py` prompt blocks; running sessions spawned pre-deploy
**Trigger:** A session primed with the old prompt invokes `tools/send_telegram.py` after the file is deleted
**Data prerequisite:** none
**State prerequisite:** Long-running session spanning the deploy
**Mitigation:** Bash returns a clear "No such file" error; the review gate still offers `send_message.py` on the next stop, so delivery degrades to the canonical path rather than silently failing. Acceptable for a hard cutover; noted in the PR description.

## No-Gos (Out of Scope)

- Email transport unification (direct-SMTP `EmailOutputHandler` vs. `email:outbox` relay) — declared an intentional divergence in the delivery-path registry with its rationale recorded there; changing email delivery mechanics is not part of this consolidation.
- `tools/valor_telegram.py` — remains the human-operator/teammate CLI by design (boundary set in #641 and reaffirmed here); documented in the registry, no code change.
- Bridge `_make_send_cb` wrapper layers (filter_tool_logs, PM bypass) — bridge-process concerns documented in the registry; restructuring them is not required to close #1370's design questions.
- Review-gate state persistence across worker restarts — the re-present behavior is documented and test-pinned as accepted; no Redis-backed `_review_state`.

## Update System

No update system changes required — this work modifies repo Python, prompts, and docs that propagate via normal `git pull` in `/update`. No new dependencies, no config files, no Popoto schema changes (no model fields added; `DeliveryOutcome` is an in-process enum), therefore no `scripts/update/migrations.py` entry. The deleted `tools/send_telegram.py` has no `pyproject.toml [project.scripts]` entry to remove (it was invoked as `python tools/send_telegram.py`).

## Agent Integration

This plan *is* agent-integration work: it changes which CLI the agent is taught for outbound messages.

- No new `pyproject.toml [project.scripts]` entry and no MCP server changes — `tools/send_message.py` and `tools/react_with_emoji.py` remain `python tools/...` Bash invocations, matching how the review gate prompt already presents them (`agent/hooks/stop.py:176-189`).
- The prompt surfaces in `agent/sdk_client.py` (three blocks, ~`:3520-3645`) and `config/personas/engineer.md:476` switch from `send_telegram.py` to `send_message.py` (with `--file` examples preserved) — this is the load-bearing wiring, since a tool the prompt does not teach is invisible to the agent.
- `.claude/skills/telegram/SKILL.md` PM-tool guidance updates to name `send_message.py` and keep the `valor-telegram`-is-for-operators warning.
- Integration test: extend `tests/unit/test_tool_call_delivery.py` contract suite to run the real CLI `main()` against fake Redis, proving the agent-invokable entry point produces the canonical outbox payload for both transports.

## Documentation

- [ ] Update `docs/features/agent-message-delivery.md`: add **"Delivery paths"** registry table (every path, caller, filters, rationale — including declared intentional divergences), add **"Failure modes"** section (drafter exception in first stop; worker restart between stops; malformed transcript tail; simultaneous tool-call + continued work), apply canonical vocabulary ("delivery review gate"; outcomes send/react/silent/continue + `DeliveryOutcome` values), and add cross-links to `bridge-worker-architecture.md`, `read-the-room.md`, `drafter-redundancy-suppression.md`, `promise-gate.md`, `session-steering.md`
- [ ] Update `docs/features/message-drafter.md`: note the `DeliveryOutcome` return surface and that `send_telegram.py`'s drafter call is gone
- [ ] Update `docs/features/emoji-embedding-reactions.md` and `docs/tools-reference.md`: `--emoji` examples move to `react_with_emoji.py --standalone`
- [ ] Update `docs/features/README.md` index rows that mention `send_telegram` (`:62`)
- [ ] Update `.claude/skills/telegram/SKILL.md` PM-tool table and examples

## Success Criteria

- [ ] `tools/send_telegram.py` deleted; repo-wide grep for `send_telegram.py` finds zero live references outside `docs/plans/`
- [ ] `agent/hooks/stop.py` has no `_LEGACY_SEND_TELEGRAM_PATTERN`; `classify_delivery_outcome` classifies on `send_message.py` / `react_with_emoji.py` only
- [ ] `deliver_system_notice` exists and is the only call path for health-checker notice delivery (`_deliver_tool_timeout_degraded_notice`, `_deliver_deferred_self_draft_fallback` refactored onto it); `grep -n "_resolve_callbacks" agent/session_health.py` shows at most the fan-out completion site
- [ ] `TelegramRelayOutputHandler.send` returns `DeliveryOutcome`; `tools/send_message.py` prints the outcome (no unconditional "Queued")
- [ ] `flush_deferred_self_draft_sync` telegram branch uses `build_telegram_outbox_payload`
- [ ] `tools/react_with_emoji.py --standalone` sends a `custom_emoji_message` payload identical in shape to the old `send_emoji`
- [ ] Delivery-path registry and failure-modes sections exist in `docs/features/agent-message-delivery.md`
- [ ] Contract tests pass for all five paths listed in Failure Path Test Strategy
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (handler+notice)**
  - Name: handler-builder
  - Role: `DeliveryOutcome`, `build_telegram_outbox_payload`, `deliver_system_notice`, session_health refactor
  - Agent Type: builder
  - Resume: true
- **Builder (retirement)**
  - Name: retirement-builder
  - Role: delete send_telegram.py, migrate `--standalone`, sweep prompts/personas/skills/stop.py
  - Agent Type: builder
  - Resume: true
- **Test Engineer (contracts)**
  - Name: contract-tester
  - Role: per-path contract tests, failure-mode tests, Test Impact dispositions
  - Agent Type: test-engineer
  - Resume: true
- **Documentarian**
  - Name: delivery-docs
  - Role: registry, failure modes, vocabulary sweep in docs
  - Agent Type: documentarian
  - Resume: true
- **Validator (final)**
  - Name: final-validator
  - Role: run Verification table, grep gates, success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Handler outcome + system-notice seam
- **Task ID**: build-handler
- **Depends On**: none
- **Validates**: tests/unit/test_output_handler.py
- **Assigned To**: handler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `DeliveryOutcome` enum; return it from every exit of `TelegramRelayOutputHandler.send`
- Extract `build_telegram_outbox_payload`; reuse in `flush_deferred_self_draft_sync` (telegram branch)
- Add `deliver_system_notice`; refactor `_deliver_tool_timeout_degraded_notice` and `_deliver_deferred_self_draft_fallback` onto it (preserve SETNX dedup, transport gates, telemetry counters, never-raises contract exactly)
- `tools/send_message.py` prints the returned outcome

### 2. Retire send_telegram.py
- **Task ID**: build-retirement
- **Depends On**: build-handler
- **Validates**: grep gates in Verification; tests/unit/test_react_with_emoji.py
- **Assigned To**: retirement-builder
- **Agent Type**: builder
- **Parallel**: false
- Port `send_emoji` to `tools/react_with_emoji.py --standalone`; delete `tools/send_telegram.py`
- Sweep: `agent/sdk_client.py` prompt blocks, `config/personas/engineer.md`, `.claude/skills/telegram/SKILL.md`, `agent/hooks/stop.py` (pattern + docstrings), comment-level references (Technical Approach step 4 list)
- Vocabulary sweep (Decision D) across stop.py, teammate_handler.py

### 3. Contract + failure-mode tests
- **Task ID**: build-tests
- **Depends On**: build-retirement
- **Validates**: tests/unit/test_tool_call_delivery.py, tests/unit/test_output_handler.py, tests/unit/test_stop_hook_review.py
- **Assigned To**: contract-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Implement the five contract tests and the failure-mode tests from Failure Path Test Strategy
- Apply every Test Impact disposition (deletes, updates, docstring re-points)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: delivery-docs
- **Agent Type**: documentarian
- **Parallel**: false
- All items in the Documentation section

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; verify all Success Criteria including anti-criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_tool_call_delivery.py tests/unit/test_output_handler.py tests/unit/test_stop_hook_review.py tests/unit/test_react_with_emoji.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/ tools/ bridge/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ tools/ bridge/` | exit code 0 |
| Tool deleted | `ls tools/send_telegram.py` | exit code != 0 |
| No live references | `grep -rn "send_telegram\.py" agent/ tools/ bridge/ config/ .claude/skills/ tests/ docs/features/ \| grep -cv "docs/plans"` | match count == 0 |
| Legacy pattern gone | `grep -c "_LEGACY_SEND_TELEGRAM_PATTERN" agent/hooks/stop.py` | match count == 0 |
| Notice seam exists | `grep -c "def deliver_system_notice" agent/output_handler.py` | output > 0 |
| session_health uses seam | `grep -c "deliver_system_notice" agent/session_health.py` | output > 0 |
| Outcome surfaced | `grep -c "DeliveryOutcome" tools/send_message.py` | output > 0 |
| Registry documented | `grep -c "Delivery paths" docs/features/agent-message-delivery.md` | output > 0 |
| Failure modes documented | `grep -c "Failure modes" docs/features/agent-message-delivery.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Should mid-session proactive sends be exempt from the redundancy filter?** The filter is SDLC-session-scoped, and a PM deliberately re-sending a status after an edit could be suppressed. Default answer in this plan: no exemption — `DeliveryOutcome` visibility lets the agent rephrase and resend. Confirm.
2. **Vocabulary final call:** "delivery review gate" is proposed as the canonical gate term (matching the module docstring and the `── DELIVERY REVIEW ──` UI label). Confirm, or pick "review gate" and the sweep inverts.
