---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-04-21
tracking: https://github.com/tomcounsell/ai/issues/1058
last_comment_id:
---

# Reliable PM Final-Delivery Protocol

## Problem

PM sessions running the SDLC pipeline use a **string-marker protocol** (`[PIPELINE_COMPLETE]`) to signal that the pipeline has finished and the next output should be delivered to Telegram rather than nudged. The router in `agent/output_router.py` (L115-118) inspects every PM output for the marker; if missing, the output is re-enqueued as a nudge.

This protocol has three observed production failure modes:

**1. Marker omission → 50-nudge loop → garbage forced delivery.** The PM forgets (or cannot emit) the marker — due to context overflow, a stale Claude Code UUID triggering a first-turn fallback, or persona drift after many steering cycles. The session loops until `auto_continue_count >= MAX_NUDGE_COUNT (50)` forces an empty-or-mid-pipeline output to Telegram. Evidence: session `tg_valor_-1003449100931_672` was nudged 49 times and the forced delivery was 49 characters.

**2. Empty harness output → ghost session (partially fixed).** Commit `3a0346b3` patched `agent/messenger.py::_run_work` to route an empty harness result into `_send_callback("")`, so the router can apply `nudge_empty` or `deliver_fallback`. That moves the problem from "silent" to "eventually delivers a fallback line after 50 nudges." The underlying design — that delivery depends on the router reasoning about empty strings — remains fragile.

**3. Worker shutdown (CancelledError) → 5-minute silence.** `_run_work` in `agent/messenger.py` catches `except Exception` (L238). `asyncio.CancelledError` inherits from `BaseException`, so it propagates uncaught. The session stays `"running"` until startup-recovery reschedules it (~5 min). During that window the user sees nothing.

**Current behavior:**
Completed SDLC pipelines sometimes produce no Telegram message, a garbled mid-pipeline status line, or a multi-minute silent gap.

**Desired outcome:**
Every PM session that transitions to a terminal state delivers exactly one final message to Telegram — a clean summary generated from a dedicated terminal turn, not a mid-work fragment — within 60 seconds. No dependence on a string marker appearing in the agent output.

## Freshness Check

**Baseline commit:** `32821125` (session/sdlc-1058 HEAD, tracks main at time of planning)
**Issue filed at:** `2026-04-20T02:32:27Z`
**Disposition:** Unchanged (minor drift in one referenced file)

**File:line references re-verified:**
- `agent/output_router.py:38` — `PIPELINE_COMPLETE_MARKER = "[PIPELINE_COMPLETE]"` — still holds.
- `agent/output_router.py:115-118` — marker check in `determine_delivery_action` — still holds.
- `agent/session_executor.py:590` — `send_cb` resolved from `_resolve_callbacks()` — moved to L690 by PR #1023 (file split). Semantics unchanged.
- `agent/session_executor.py:738-753` (nudge loop L refs) — actual nudge handling is now L798-848. `_enqueue_nudge` lives at L249+. Semantics unchanged.
- `agent/messenger.py::_run_work` — `if self._result:` guard patched by `3a0346b3` at L221-236; `except Exception` at L238 unchanged.
- `agent/session_health.py` startup-recovery — present at L168+, still the 5-min fallback path.
- `models/agent_session.py` `delivery_action` / `delivery_text` fields — present at L203-205 plus `result_text` property at L784-798.

**Cited sibling issues/PRs re-checked:**
- Commit `3a0346b3` — merged into main Apr 20 2026 — referenced as "partial fix #2" in the issue. Verified by reading the current `messenger.py` — empty-result callback is live.
- PR #1010 (closes #1005 "PR left open") — merged Apr 16 2026. Reinforces PM Rule 5 ("MERGE before PIPELINE_COMPLETE"). This work preserves the semantic intent.
- PR #990 (fix #987 "pipeline continuation race") — merged Apr 15 2026. Adds re-check guard in `_handle_dev_session_completion`. This plan hooks Option B into the same function — must preserve the re-check ordering.
- PR #1008 (fix #1004 "PM deadlock") — merged Apr 16 2026. Added `waiting_for_children` guard in router at L109-110. Our refactor must not regress this path.

**Commits on main since issue was filed (touching referenced files):**
- `34d368f4` (Apr 20) — `session_mode` → `session_type` refactor. Affects our naming (we read `session_type`), no behavioral drift.
- `c5c24ee3` (Apr 20) — re-enqueue dropped steering; preserves `session_type`. Touches `session_executor.py` but at the steering-completion path, not the router. No conflict.
- `c66b7b1c` (Apr 20) — phantom-record destruction fix. Independent.
- `26c0ed5e` (Apr 20) — message drafter rename. Independent of the PM final-delivery path.
- `0fd28c87` (Apr 20) — memory-extraction unblocking. Docstring in `_run_work` warns not to re-introduce extraction there. Our refactor will not touch extraction ordering.

**Active plans in `docs/plans/` overlapping this area:** None found. `grep -rln "PIPELINE_COMPLETE\|output_router\|PM final" docs/plans/ docs/plans/archive/` returns nothing.

**Notes:** Minor drift — `session_executor.py` line numbers have shifted since issue was filed due to PR #1023 (file split) and `3a0346b3`. All logical behaviors the issue references still hold; updated line numbers are incorporated inline above.

## Prior Art

- **PR #1010 (closes #1005)**: "fix(#1005): prevent PM session from completing before merge gate" — merged Apr 16 2026. Added PM persona Rule 5 ("MERGE is Mandatory Before Pipeline Complete"). The rule is expressed in marker terms. Relevance: our plan must preserve the rule's semantic intent (no completion while an open PR exists) in a marker-free form.
- **Commit `0e4d41e1`**: "fix(sdlc): deliver PM final summary instead of nudging indefinitely" — Apr 15 2026. This is the commit that **introduced** `PIPELINE_COMPLETE_MARKER`. It identified two failure modes (fan-out, single SDLC) where the PM's final summary was nudged instead of delivered. Our plan replaces this marker-based fix with a protocol that doesn't require the PM to emit any specific string.
- **Commit `3a0346b3`**: "hotfix: invoke router on empty harness result to prevent ghost sessions" — Apr 20 2026. Patches empty-output silence (failure mode #2). Relevance: this fix is orthogonal to our Option B implementation — it patches the empty-output branch in `_run_work`. We keep it; our plan adds a separate completion-turn path.
- **PR #990 (fix #987)**: "resolve SDLC pipeline continuation race" — merged Apr 15 2026. Adds a re-check guard after `_steer_session` returns to detect `_finalize_parent_sync` racing the steer. Relevance: Option B hooks into `_handle_dev_session_completion`, which is where that guard lives — our implementation must preserve the ordering invariants documented there.
- **PR #1008 (fix #1004)**: "prevent PM session deadlock" — merged Apr 16 2026. Added `waiting_for_children` guard at `output_router.py` L109-110. Relevance: our refactor will simplify the router by removing the marker branch but must **not** remove the `waiting_for_children` → `deliver` path.
- **PR #898/#905**: Nudge-stomp CAS regression. Relevance: any change to nudge behavior must pass the existing CAS-regression tests (`tests/unit/test_nudge_stomp*.py`).
- **Commit #749**: "externalize session steering via queued_steering_messages". The worker already has a reliable non-string-based way to instruct the PM via `queued_steering_messages`. Our plan uses the same mechanism — the worker sends a dedicated "compose final summary" steer instead of relying on the PM emitting a marker.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `0e4d41e1` (Apr 15) | Added `PIPELINE_COMPLETE_MARKER` so PM can break out of nudge loop | Depends on a content-aware router and on the PM's willingness/ability to emit a literal string. Persona drift, context overflow, and stale-UUID fallbacks all cause omission. The router has no other signal to distinguish "pipeline done" from "mid-pipeline status." |
| `3a0346b3` (Apr 20) | Empty harness result triggers router with empty string | Moves the problem one layer down — empty output still relies on the router applying `nudge_empty` and eventually `deliver_fallback` after the cap fires. The user still sees a delayed fallback, not a summary. |

**Root cause pattern:** Both fixes treat symptoms (marker absent, output empty) rather than the structural cause — **there is no dedicated "compose final message" step in the pipeline**. The marker protocol is the PM trying to self-declare pipeline completion from inside a nudge-driven loop that has no concept of "this is the last turn." Any fix that puts the burden of the signal on the PM's output content will keep regressing.

## Architectural Impact

- **New dependencies:** None. All changes are internal to `agent/`.
- **Interface changes:**
  - `agent/output_router.py`: removes `PIPELINE_COMPLETE_MARKER` constant and the `deliver_pipeline_complete` action. Router no longer inspects message content for any marker.
  - `agent/session_executor.py`: removes the `deliver_pipeline_complete` branch in `send_to_chat`.
  - `agent/session_completion.py::_handle_dev_session_completion`: new "terminal steer" behavior when the pipeline judges itself done (Option B).
  - `agent/session_health.py::_agent_session_hierarchy_health_check`: replaces the fan-out marker-instructing steering message with a plain "compose final summary — this is your last turn" steer.
  - `config/personas/project-manager.md`: four marker references removed; Rule 5 semantic (no completion while PR open) preserved in marker-free form.
- **Coupling:** Reduces coupling between the router (infrastructure) and the PM persona (content). The router no longer needs to understand pipeline semantics — that stays in `_handle_dev_session_completion`.
- **Data ownership:** Unchanged. `AgentSession.result_text` (a property backed by `session_events`) remains the canonical place to find what the PM last said.
- **Reversibility:** High. All changes are additive/subtractive at specific call sites; the `delivery_action` / `delivery_text` fields are unchanged.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (approach confirmation: Option B chosen)
- Review rounds: 1 (standard PR review cycle)

The implementation is localized to ~4 files in `agent/`, with most of the delta being deletions. The complexity is in getting the terminal-turn ordering right without regressing the existing race-condition guards in `_handle_dev_session_completion`.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal to the `agent/` module.

## Solution

### Decision: Option B (dedicated completion turn)

**Chosen** over Option C because:

1. **No ORM dependency.** Option B never touches Redis/Popoto during the completion transaction — the final message flows through the normal `send_cb` path like any other delivery.
2. **Higher-quality message.** The PM gets an explicit "this is your final turn — write a summary" prompt, which produces a purpose-fit summary rather than whatever happened to be in `result_text` at the moment the session transitioned.
3. **Simpler failure model.** If the completion turn fails (harness error, CancelledError), the session is still in a clean state — `startup_recovery` will re-queue it and the completion prompt can be re-delivered idempotently. Option C would leave `result_text` possibly stale from a mid-pipeline turn and require additional state to distinguish "freshly delivered" from "stale."
4. **Recon confirmed CLI feasibility.** The Claude Code CLI does not expose `--max-turns`, but `claude -p` runs exactly one turn per invocation anyway. Option B's "single synthetic prompt" is just one more `get_response_via_harness()` call with `--resume <pm_uuid>`.

Option C is kept in the Rabbit Holes section as a considered alternative.

### Key Elements

- **Pipeline-complete predicate** (new): a pure function that decides whether the pipeline has reached a terminal state based on PM session stage_states + PR state. Returns `(is_complete, reason)`. Lives in `agent/pipeline_complete.py` (new module).
- **Completion-turn runner** (new): a coroutine that invokes the harness with a dedicated "compose final summary" prompt and pushes the result directly through `send_cb`, bypassing the nudge loop. Lives in `agent/session_completion.py` (extended).
- **Hierarchy health check update**: when fan-out children all complete, trigger the completion-turn runner instead of pushing a steering message with marker instructions.
- **Router simplification**: remove `PIPELINE_COMPLETE_MARKER`, `deliver_pipeline_complete`, and the marker inspection branch from `determine_delivery_action`. The router's only PM-SDLC branch becomes `nudge_continue` (continue pipeline) — plus the existing `waiting_for_children` → `deliver` path.
- **CancelledError guard** (new): catch `BaseException` in `agent/messenger.py::_run_work`; store the error, emit an "I was interrupted; please resend or wait for retry" message via `send_cb` if possible, then re-raise `CancelledError` to preserve asyncio shutdown semantics.
- **Persona cleanup**: remove all `[PIPELINE_COMPLETE]` references from `config/personas/project-manager.md`. Rule 5 ("MERGE is Mandatory Before Pipeline Complete") is rewritten as "do not indicate pipeline completion while an open PR exists for this issue."

### Flow

**Normal completion path:**
1. Dev session completes → `_handle_dev_session_completion` fires.
2. After the existing stage-comment + steer logic, check `is_pipeline_complete(parent)`:
   - If **false**, steer PM as today (pipeline continues).
   - If **true**, skip the "continue" steer. Instead, spawn a completion-turn runner coroutine.
3. Completion-turn runner:
   - Loads the PM's Claude Code UUID from `models/session_lifecycle`.
   - Calls `get_response_via_harness(message=COMPLETION_PROMPT, prior_uuid=pm_uuid, full_context_message=COMPLETION_PROMPT)` — the fallback `full_context_message` is identical because on UUID failure we can generate the summary from the steer's context.
   - On success: calls `send_cb(chat_id, summary, telegram_message_id, agent_session)` directly, sets `response_delivered_at`, finalizes the PM session to `"completed"`.
   - On empty/failed harness result: uses the existing outcome summary (what we had in the steer) as the fallback text; still delivers via `send_cb`.
4. The PM session never re-enters the nudge loop for the final turn — the runner owns the final delivery.

**Fan-out completion path:**
1. `_agent_session_hierarchy_health_check` detects all children terminal.
2. Instead of pushing a steering message with `PIPELINE_COMPLETE_MARKER` instructions, call the same completion-turn runner with a fan-out-specific prompt (listing per-child outcomes).
3. Same delivery path as the normal flow.

**CancelledError path:**
1. Worker shuts down mid-session → `asyncio.CancelledError` raised inside `_run_work`.
2. New `except BaseException` handler catches it:
   - If `isinstance(e, CancelledError)`: emit `"I was interrupted and will resume automatically. No action needed."` via best-effort `send_cb`, then `raise` to preserve shutdown semantics.
   - Other `BaseException` (rare): emit error message, log, re-raise.
3. Startup-recovery handles the re-queue as today (preserved).

### Technical Approach

- **New module `agent/pipeline_complete.py`**:
  - Pure predicate `is_pipeline_complete(parent_session, current_stage, outcome) -> tuple[bool, str]`.
  - Logic: pipeline is complete iff `current_stage == "MERGE"` AND `outcome == "success"`, OR `current_stage == "DOCS"` AND `outcome == "success"` AND no open PR exists (for issues that legitimately skip MERGE — e.g., docs-only changes, plan PRs that close on merge). MERGE-stage success is the primary path; the DOCS+no-PR branch handles the corner cases already excluded by Rule 5.
  - Pure function, no I/O beyond reading PR state (via `gh pr list` subprocess, reusing existing utility in `utils/issue_comments.py` or a thin wrapper).
  - Unit testable without Redis/GitHub mocks (pass in `current_stage`, `outcome`, `pr_open` as args).
- **New coroutine `agent/session_completion.py::_deliver_pipeline_completion`**:
  - Takes `(parent_session, pipeline_summary_context) -> None`.
  - Constructs the completion prompt:
    ```
    The SDLC pipeline has finished. Context: {pipeline_summary_context}

    This is your final turn. Write a 2-3 sentence summary for the user covering what was accomplished and any notable outcomes. Do NOT use any special markers or format instructions — just write the summary directly.
    ```
  - Runs harness with `--resume <pm_uuid>`, `full_context_message=<same prompt>` (for stale-UUID fallback).
  - On non-empty result: calls `send_cb` directly, sets `response_delivered_at`, transitions PM to `completed`.
  - On empty result (harness returned nothing): delivers `pipeline_summary_context` as a fallback — same content the steering-based path would have started from.
  - Wraps all operations in `try/except Exception` (not `BaseException`) — if something explodes, the existing finalization paths still run.
- **Router simplification (`agent/output_router.py`)**:
  - Delete `PIPELINE_COMPLETE_MARKER` constant.
  - Delete the `deliver_pipeline_complete` action string.
  - Remove the `if PIPELINE_COMPLETE_MARKER in msg:` branch from `determine_delivery_action`.
  - The PM+SDLC path becomes unconditionally `nudge_continue` (except for the `waiting_for_children` → `deliver` and `completion_sent`/`terminal_statuses` guards — all preserved).
- **Executor simplification (`agent/session_executor.py`)**:
  - Delete the `elif action == "deliver_pipeline_complete":` branch (L850-879).
  - Keep everything else.
- **Messenger CancelledError guard (`agent/messenger.py`)**:
  - Replace `except Exception as e:` (L238) with separate `except asyncio.CancelledError:` and `except Exception as e:` clauses.
  - The `CancelledError` handler: best-effort `send_cb` of the "interrupted" message, then `raise` to preserve shutdown. Any exception from `send_cb` is swallowed (can't block shutdown).
  - Existing separator-overflow and generic-error paths remain in `except Exception`.
- **Fan-out path (`agent/session_health.py`)**:
  - In `_agent_session_hierarchy_health_check` at L1065-1081, replace the `push_steering_message` + `transition_status` pair with a direct call to `_deliver_pipeline_completion(parent, fan_out_summary)`.
  - Keep the failed-parent branch (immediate `_transition_parent(parent, "failed")`) unchanged.
- **Persona cleanup (`config/personas/project-manager.md`)**:
  - Rewrite Rule 5 (L42-52): "Do not claim pipeline completion while an open PR exists for the current issue. Before exiting, run `gh pr list --search "#{issue_number}" --state open`; if any open PR exists, invoke `/sdlc` to dispatch `/do-merge`. Your final message is delivered automatically when the pipeline reaches a terminal state."
  - Remove `[PIPELINE_COMPLETE]` instructions at L44, L49, L384, L487.
  - Add a note: "You do not need to emit any special marker to trigger final delivery. When the pipeline reaches MERGE success (or a legitimate non-MERGE terminal state), the worker composes the final summary by asking you directly."
- **Docs cleanup (`docs/features/pipeline-state-machine.md` L161)**:
  - Update to describe the new protocol: "Final delivery is driven by `_handle_dev_session_completion` detecting pipeline completion and invoking `_deliver_pipeline_completion`, not by a persona-emitted marker."

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `agent/messenger.py::_run_work` `except Exception` → test asserts that `BaseException` subclasses (specifically `CancelledError`) reach the new handler and call `send_cb` before re-raise. Covered by `test_messenger_cancelled_error_delivers_interrupted_message`.
- [x] `agent/session_completion.py::_deliver_pipeline_completion` `except Exception` → test asserts that if the harness raises, the exception is logged, `send_cb` is called with the fallback text, and the PM session is finalized. Covered by `test_deliver_pipeline_completion_harness_failure_fallback`.
- [x] `agent/pipeline_complete.py::is_pipeline_complete` `except Exception` in PR-state fetch → test asserts that subprocess failures return `(False, "pr_state_unavailable")` conservatively, never treating unknown state as "complete."

### Empty/Invalid Input Handling
- [x] Empty harness result in `_deliver_pipeline_completion` — test asserts fallback text is delivered via `send_cb`.
- [x] `None`/missing `pm_uuid` when calling completion-turn runner — test asserts the runner falls back to `full_context_message` path (no-UUID, first-turn harness call).
- [x] Whitespace-only harness result — test asserts same fallback path as empty.

### Error State Rendering
- [x] CancelledError interrupt message is user-visible (actual Telegram delivery assertion — this is what the issue's failure mode #3 demands).
- [x] Harness-failure fallback summary is user-visible (not swallowed into logs).
- [x] No silent loops: if `_deliver_pipeline_completion` raises, the PM session finalizes to `"failed"` (not `"running"` indefinitely).

## Test Impact

- [ ] `tests/unit/test_output_router.py` (all tests referencing `deliver_pipeline_complete` or `PIPELINE_COMPLETE_MARKER`, approximately L85, L151, L158) — UPDATE: remove marker-specific assertions; add assertions that PM/SDLC paths without markers resolve to `nudge_continue` (or `deliver` on `waiting_for_children`). Reference lines from current file: L10, L85, L87, L151, L158 import the marker — these lines become obsolete.
- [ ] `tests/unit/test_steering_mechanism.py` L161-223 ("Tests for PIPELINE_COMPLETE marker behavior in output router") — DELETE: entire class `TestPipelineCompleteMarker` becomes obsolete. Replaced by new test file `tests/unit/test_pipeline_complete_predicate.py` (create).
- [ ] `tests/unit/test_session_completion*.py` (if any reference the marker-based steering path in `_handle_dev_session_completion`) — UPDATE: patch assertions to check for `_deliver_pipeline_completion` invocation instead of `push_steering_message` with marker instructions.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::test_cancelling_handle_task_does_not_cancel_worker_loop` — UPDATE: extend to assert the new "interrupted" message is delivered when CancelledError fires mid-session.
- [ ] `tests/unit/test_worker_cancel_requeue.py` — UPDATE: new test `test_cancelled_error_delivers_interrupted_message` added; existing tests preserved unchanged.
- [ ] `tests/unit/test_messenger*.py` (if exists) — UPDATE: add CancelledError → `send_cb("interrupted...")` → re-raise assertion. If no file exists, create `tests/unit/test_messenger_cancelled_error.py`.
- [ ] `tests/integration/test_session_finalization_decoupled.py::test_cancellation_does_not_crash_via_wrapper` — UPDATE: extend to assert the interrupted-message delivery, preserving existing "does not crash" assertion.
- [ ] `tests/unit/test_qa_nudge_cap.py` (any marker references) — UPDATE: remove marker-based assertions; the Teammate path was never affected by the marker (only PM+SDLC was), so most tests here unchanged.
- [ ] New test file `tests/unit/test_pipeline_complete_predicate.py` — CREATE: pure-function tests for `is_pipeline_complete` (MERGE success → True, DOCS+no-PR → True, DOCS+open-PR → False, unknown stage → False, PR-state fetch failure → False).
- [ ] New test file `tests/unit/test_deliver_pipeline_completion.py` — CREATE: tests for the completion-turn runner (success, empty harness, harness raises, no pm_uuid fallback, CancelledError propagation).
- [ ] New test file `tests/integration/test_pm_final_delivery.py` — CREATE: end-to-end test covering failure mode #1 (marker-free success), failure mode #2 (empty harness → fallback delivered), failure mode #3 (CancelledError → interrupted message delivered within 60s proxy). Uses the existing bridge/worker test harness (see `tests/integration/test_session_finalization_decoupled.py` for patterns).

## Rabbit Holes

- **Option C (ORM-based status-transition reactor)** — considered and rejected. Would require a reliable "this is the final output" state flag to avoid delivering a stale `result_text` from a mid-pipeline turn. Solvable but adds state and coupling. Revisit only if Option B's harness-call latency proves unacceptable.
- **Rewriting the entire nudge loop** — tempting but massively expands scope. The nudge loop is fine for non-PM sessions and for mid-pipeline PM turns. The problem is specifically the PM→final-turn handoff; fix that narrowly.
- **Tracking "intent to complete" with a dedicated ORM field** — the PM would set `completing = True` before its final output. This reintroduces the same signal-fragility as the marker, just with a typed field. Option B removes the signal entirely.
- **Streaming partial summaries during the completion turn** — not worth it. The final summary is short (2-3 sentences). Delivering once at the end is simpler and more reliable.
- **Unifying `BossMessenger.send` with the router's `send_cb`** — plausible cleanup, but out of scope. Current dual-path design is intentional (Messenger is for mid-session acknowledgments; `send_cb` is the finalization path). Separate refactor.
- **Bypassing the harness entirely for the completion turn (programmatic summary generation)** — tempting for reliability, but produces lower-quality summaries. The PM's full Claude Code context via `--resume` is exactly what makes the summary good.

## Risks

### Risk 1: Option B completion turn latency degrades UX
**Impact:** A dedicated harness call adds 3-15 seconds of latency before the user sees the final message.
**Mitigation:** (a) Budget: typical harness turn is 5-10s; acceptable for a final summary that previously could take 50+ nudges to force-deliver. (b) The runner can optimistically set `response_delivered_at` at start so the bridge's outbox-drain logic doesn't time out. (c) If latency becomes a problem in prod, fall back to Option C (read current `result_text`) as a fast path and only invoke the harness when `result_text` is stale or empty.

### Risk 2: `_deliver_pipeline_completion` races with `_finalize_parent_sync`
**Impact:** If the completion runner is invoked but `_finalize_parent_sync` fires first (another child completing in parallel), the parent transitions to `completed` before the runner delivers the summary — the router's `deliver_already_completed` branch then handles it, but we've now produced two potential messages (the in-flight completion turn and whatever the original session last said).
**Mitigation:** The completion runner must be the **only** caller that transitions the parent to `completed`. Other paths (health-check, `_finalize_parent_sync`) defer to the runner when `is_pipeline_complete()` returns true. Concretely: add a `pipeline_complete_pending` flag on AgentSession (transient in-memory or Redis-only, not persisted) set by `_handle_dev_session_completion` before spawning the runner. `_finalize_parent_sync` checks this flag and skips finalization if set.

### Risk 3: Persona drift re-introduces the marker or expects it
**Impact:** Existing persona segments, documentation, and future contributors might still expect `[PIPELINE_COMPLETE]` to be meaningful. A PM that emits it would have it delivered literally in a message (which would be jarring).
**Mitigation:** (a) Add a test that asserts router.`determine_delivery_action` does NOT special-case the literal string `[PIPELINE_COMPLETE]` (negative test — it's delivered as-is in message content, no special routing). (b) Add a post-merge memory (`python -m tools.memory_search save`) noting the marker is deprecated. (c) Update `docs/features/pipeline-state-machine.md` (line 161 and any other refs) with a deprecation note.

### Risk 4: CancelledError best-effort `send_cb` itself hangs
**Impact:** Worker shutdown wedged waiting for `send_cb("interrupted...")` to complete, preventing clean shutdown.
**Mitigation:** Wrap the send in `asyncio.wait_for(send_cb(...), timeout=2.0)`. If it times out or raises, swallow and proceed to `raise` — shutdown semantics are preserved at the cost of the interrupted message on rare shutdown-path failures (startup-recovery will still re-queue).

### Risk 5: `is_pipeline_complete` subprocess (`gh pr list`) adds latency or fails in offline tests
**Impact:** Every dev-session completion now runs a subprocess before deciding whether to invoke the completion turn.
**Mitigation:** (a) Cache PR state on AgentSession.extra_context under TTL (5s) — same session's subsequent completion checks reuse the result. (b) On subprocess failure, conservatively return `False` (pipeline not complete) so the old nudge-based path still works as a safety net. (c) In tests, inject a mock via dependency-injection — the predicate function takes `pr_open: bool | None` as an optional override argument.

## Race Conditions

### Race 1: Completion runner vs. _finalize_parent_sync
**Location:** `agent/session_completion.py::_handle_dev_session_completion` ↔ `models/session_lifecycle.py::_finalize_parent_sync`
**Trigger:** Two child sessions complete near-simultaneously; both invoke `_handle_dev_session_completion`. The first detects pipeline completion and spawns the runner. The second sees the parent still `"running"` and invokes `_finalize_parent_sync`, which transitions parent to `"completed"` before the runner's `send_cb` fires. The runner's subsequent `send_cb` delivers a valid message, but the parent is already terminal — the router's `deliver_already_completed` branch handles it (which is actually correct).
**Data prerequisite:** Parent must NOT be finalized to `"completed"` by any path OTHER than the completion runner.
**State prerequisite:** A "completion pending" flag set atomically before the runner starts.
**Mitigation:** Set `pipeline_complete_pending = True` on parent AgentSession at the very start of `_deliver_pipeline_completion`. `_finalize_parent_sync` checks this flag; if set, transitions to `"completing"` (a new intermediate non-terminal status already documented in `docs/features/session-lifecycle.md`) or just returns early. The runner is the sole transition to `"completed"`. If the flag is unset and the runner never fires (e.g., pipeline not actually complete), existing behavior applies.

### Race 2: Concurrent completion runner invocations
**Location:** `_handle_dev_session_completion` called multiple times for the same parent.
**Trigger:** Fan-out PM with 3 children all completing within the same health-check tick AND `_handle_dev_session_completion` invocations from each child overlapping.
**Data prerequisite:** Exactly one completion-turn runner per parent.
**State prerequisite:** Idempotency on runner entry.
**Mitigation:** Runner entry is guarded by an atomic compare-and-set on `pipeline_complete_pending`: if already True, skip (another invocation owns the runner). Uses the same CAS discipline PR #885 established for nudge-stomp protection.

### Race 3: Harness call in runner while worker is shutting down
**Location:** `_deliver_pipeline_completion` invokes `get_response_via_harness` near shutdown.
**Trigger:** User kills worker after runner spawns but before harness completes.
**Data prerequisite:** Worker shutdown must drain or cancel the runner; the runner must deliver its best-effort message before fully exiting.
**State prerequisite:** Runner coroutine is tracked in worker's shutdown-drain set.
**Mitigation:** Schedule the runner as a tracked task (similar to `_pending_extraction_tasks` in `session_executor.py`) and drain it in worker shutdown sequence with a reasonable timeout (10-15s). If the timeout fires, cancel the task; the CancelledError handler in the runner (inherited from the messenger fix) delivers the "interrupted" message.

### Race 4: `response_delivered_at` stamped twice
**Location:** Runner sets `response_delivered_at` AND the existing deliver-path in `send_to_chat` also sets it.
**Trigger:** Runner delivers, then a stray PM output produces another turn that also goes through `send_to_chat`.
**Data prerequisite:** `response_delivered_at` reflects the actual final delivery.
**State prerequisite:** Runner's delivery transitions parent to `completed` before any subsequent turn can reach `send_to_chat`.
**Mitigation:** Runner transitions parent to `completed` synchronously after its `send_cb`. Subsequent PM output hits `deliver_already_completed` (which already clears the state correctly). Standard behavior, no new guard needed — but assert it in tests.

## No-Gos (Out of Scope)

- Removing `startup-recovery` — it remains the backstop for unhandled crashes.
- Adding new ORM fields (beyond the transient in-memory `pipeline_complete_pending` flag if needed, which can live in `extra_context` dict without schema migration).
- Changing the nudge loop for non-PM sessions (Teammate and Dev sessions untouched).
- Reworking the `BossMessenger` class.
- Rewriting the PM persona beyond removing marker references and updating Rule 5.
- Adding a new MCP server or agent tool. This is an agent-internal delivery refactor, not a user-facing feature.
- Changing how PR state is detected in `/sdlc` or `/do-merge` — the new `is_pipeline_complete` predicate is a local helper for the completion runner only.
- Adding telemetry beyond standard logging (dashboard already tracks `response_delivered_at`).

## Update System

No update system changes required — this refactor is purely internal to `agent/` and `config/personas/`. No new dependencies, services, or config files. The deployed systems will pick up the new behavior on the next `/update` that pulls the merged code. The update skill (`scripts/remote-update.sh`) does not need changes.

## Agent Integration

No agent integration required — this is a worker-internal change. No new MCP server, no new tool registration, no changes to `.mcp.json`. The PM agent's persona prompt changes (removing marker instructions), but that is a persona content update, not a tool integration.

The bridge (`bridge/telegram_bridge.py`) does not need changes — `send_cb` is already plumbed via `_resolve_callbacks` in `agent/agent_session_queue.py`, and the runner uses that same callback.

Integration verification: existing `tests/integration/test_session_finalization_decoupled.py` covers the bridge ↔ worker boundary. The new `tests/integration/test_pm_final_delivery.py` adds coverage for the specific "PM completes → Telegram message" path.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pm-final-delivery.md` describing the new protocol — what triggers final delivery, the completion-turn mechanism, fallbacks, and the deprecation of `[PIPELINE_COMPLETE]`.
- [ ] Update `docs/features/README.md` index table to include the new feature doc.
- [ ] Update `docs/features/pipeline-state-machine.md` L161 — replace the marker-based description with the new mechanism.
- [ ] Update `docs/features/agent-message-delivery.md` — add a note that the PM's final message uses a dedicated completion turn, not the review-gate path used by Teammate.
- [ ] Update `docs/features/session-steering.md` — clarify that the marker is deprecated and fan-out completion uses a direct runner invocation, not a steering message with marker instructions.

### External Documentation Site
- [ ] No external doc site in this repo; skip.

### Inline Documentation
- [ ] Docstring on `agent/pipeline_complete.py::is_pipeline_complete` explaining the predicate.
- [ ] Docstring on `agent/session_completion.py::_deliver_pipeline_completion` explaining the runner's contract (idempotent, sole path to `"completed"` for its parent, CancelledError-safe).
- [ ] Docstring on `agent/messenger.py::_run_work` CancelledError handler explaining the shutdown semantics.
- [ ] Comment block at the top of `agent/output_router.py` noting the marker-protocol removal and linking to this plan.
- [ ] Update CLAUDE.md if any architecture diagram references the marker — verified no diagram currently references it.

## Success Criteria

- [ ] Final Telegram message delivered within 60 seconds of pipeline completion in all three scenarios: (a) happy-path MERGE success, (b) empty harness result on the completion turn, (c) `CancelledError` during worker shutdown.
- [ ] `PIPELINE_COMPLETE_MARKER` is removed from `agent/output_router.py`. `grep -rn PIPELINE_COMPLETE agent/ config/personas/` returns zero matches.
- [ ] No `in msg` / string-content inspection in the router for delivery decisions. Verified by inspecting `determine_delivery_action` — the only content check is `msg.strip()` for emptiness.
- [ ] Integration test `tests/integration/test_pm_final_delivery.py` passes for all three failure modes.
- [ ] `CancelledError` during worker shutdown produces a user-visible "I was interrupted" message. Existing startup-recovery still re-queues the session afterward.
- [ ] Tests pass (`pytest tests/ -x -q`).
- [ ] Ruff format clean (`python -m ruff format .`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `grep -rn deliver_pipeline_complete agent/ tests/` returns zero matches.
- [ ] `grep -c PIPELINE_COMPLETE config/personas/project-manager.md` returns 0.

## Team Orchestration

### Team Members

- **Builder (pipeline-predicate)**
  - Name: `predicate-builder`
  - Role: Implement `agent/pipeline_complete.py` — pure predicate `is_pipeline_complete`.
  - Agent Type: builder
  - Resume: true

- **Builder (completion-runner)**
  - Name: `runner-builder`
  - Role: Implement `_deliver_pipeline_completion` in `agent/session_completion.py`; wire it into `_handle_dev_session_completion`; update fan-out path in `agent/session_health.py`.
  - Agent Type: async-specialist
  - Resume: true

- **Builder (router-simplification)**
  - Name: `router-builder`
  - Role: Remove `PIPELINE_COMPLETE_MARKER`, `deliver_pipeline_complete` action, and associated branches from `agent/output_router.py` and `agent/session_executor.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (cancellation-guard)**
  - Name: `cancel-builder`
  - Role: Add `BaseException`/`CancelledError` handler to `agent/messenger.py::_run_work` with best-effort user-visible interrupted message.
  - Agent Type: async-specialist
  - Resume: true

- **Builder (persona-cleanup)**
  - Name: `persona-builder`
  - Role: Remove `[PIPELINE_COMPLETE]` references from `config/personas/project-manager.md`; rewrite Rule 5 in marker-free form.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (unit-tests)**
  - Name: `unit-tester`
  - Role: Implement new unit tests; update or delete obsolete marker-based tests per Test Impact section.
  - Agent Type: test-engineer
  - Resume: true

- **Test Engineer (integration-tests)**
  - Name: `integration-tester`
  - Role: Implement `tests/integration/test_pm_final_delivery.py` for all three failure modes.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (feature-docs)**
  - Name: `docs-builder`
  - Role: Create `docs/features/pm-final-delivery.md` and update all docs listed in the Documentation section.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Run full test suite, verify success criteria, grep-check marker removal, confirm docs updated.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement pipeline-complete predicate
- **Task ID**: build-predicate
- **Depends On**: none
- **Validates**: `tests/unit/test_pipeline_complete_predicate.py` (create)
- **Informed By**: Freshness Check (line refs), Recon Summary (Option B plumbing)
- **Assigned To**: predicate-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/pipeline_complete.py` with `is_pipeline_complete(current_stage: str, outcome: str, pr_open: bool | None = None) -> tuple[bool, str]`.
- Return `(True, "merge_success")` when `current_stage == "MERGE"` AND `outcome == "success"`.
- Return `(True, "docs_success_no_pr")` when `current_stage == "DOCS"` AND `outcome == "success"` AND `pr_open == False`.
- Return `(False, <reason>)` otherwise; on `pr_open is None`, conservatively return `(False, "pr_state_unavailable")`.
- Add a helper `_check_pr_open(issue_number: int) -> bool | None` that runs `gh pr list --search "#{issue_number}" --state open` via subprocess with a 5-second timeout; returns `True`/`False`/`None` (on error).

### 2. Validate predicate
- **Task ID**: validate-predicate
- **Depends On**: build-predicate
- **Assigned To**: unit-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_pipeline_complete_predicate.py` with tests for: MERGE success → True, DOCS+no-PR → True, DOCS+open-PR → False, unknown stage → False, `pr_open=None` → False, subprocess failure in `_check_pr_open` → returns None.

### 3. Implement completion runner
- **Task ID**: build-runner
- **Depends On**: build-predicate
- **Validates**: `tests/unit/test_deliver_pipeline_completion.py` (create)
- **Informed By**: Risks 1-5, Race Conditions 1-4
- **Assigned To**: runner-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Implement `_deliver_pipeline_completion(parent: AgentSession, summary_context: str, send_cb: Callable, chat_id: str, telegram_message_id: str | None) -> None` in `agent/session_completion.py`.
- Set `pipeline_complete_pending = True` on parent via `parent.extra_context` CAS before spawning the harness call (handles Race 2).
- Load PM's Claude Code UUID from session UUID store.
- Call `get_response_via_harness(message=COMPLETION_PROMPT, prior_uuid=pm_uuid, full_context_message=COMPLETION_PROMPT, session_id=parent.session_id)`.
- Wrap in `try/except Exception`; on failure, deliver `summary_context` as fallback.
- On any result (harness or fallback), call `send_cb(chat_id, text, telegram_message_id, parent)`.
- Set `parent.response_delivered_at = datetime.now(UTC)` and transition parent to `"completed"` via `finalize_session()`.
- Wrap the entire thing in `try/except asyncio.CancelledError` to catch shutdown-time cancellation: best-effort `asyncio.wait_for(send_cb("I was interrupted and will resume automatically. No action needed."), timeout=2.0)`; re-raise `CancelledError`.
- In `_handle_dev_session_completion`, after current-stage and outcome are known, call `is_pipeline_complete(current_stage, outcome, _check_pr_open(issue_number))`. If True, invoke `_deliver_pipeline_completion` instead of the existing `_steer_session` call. Preserve the existing re-check guard logic for the non-complete path.
- Schedule the runner as a tracked asyncio.Task (similar to `_pending_extraction_tasks`) and add to worker shutdown-drain set.

### 4. Validate runner
- **Task ID**: validate-runner
- **Depends On**: build-runner
- **Assigned To**: unit-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_deliver_pipeline_completion.py` with tests: success with pm_uuid, success without pm_uuid (falls back), empty harness → fallback delivered, harness raises → fallback delivered, CancelledError → interrupted message delivered + re-raise.
- Update any existing `tests/unit/test_session_completion*.py` per Test Impact section.

### 5. Update fan-out path
- **Task ID**: build-fanout
- **Depends On**: build-runner
- **Validates**: existing fan-out tests in `tests/unit/test_health_check_recovery_finalization.py`
- **Assigned To**: runner-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- In `agent/session_health.py::_agent_session_hierarchy_health_check` (L1065-1081), replace the `push_steering_message` with marker instructions + `transition_status(parent, "pending", ...)` with a direct `_deliver_pipeline_completion(parent, fan_out_summary, ...)` call.
- Build `fan_out_summary` from the child outcomes list (existing `child_lines` variable).
- Keep failed-parent branch unchanged.

### 6. Simplify router + executor
- **Task ID**: build-router
- **Depends On**: build-runner, build-fanout (new path must be live before old path is removed)
- **Validates**: `tests/unit/test_output_router.py` (updated)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `PIPELINE_COMPLETE_MARKER` from `agent/output_router.py`.
- Remove the `deliver_pipeline_complete` action from the return-value union and the marker-inspection branch in `determine_delivery_action`.
- Remove the `elif action == "deliver_pipeline_complete":` branch from `agent/session_executor.py::send_to_chat` (L850-879).
- Update all docstrings mentioning the marker.

### 7. Update router tests
- **Task ID**: validate-router
- **Depends On**: build-router
- **Assigned To**: unit-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `tests/unit/test_output_router.py` per Test Impact — remove marker-based assertions.
- Delete `TestPipelineCompleteMarker` class in `tests/unit/test_steering_mechanism.py`.
- Add negative test: `determine_delivery_action` with `msg="Pipeline done. [PIPELINE_COMPLETE]"` treats the string as ordinary content (no routing effect).

### 8. Add CancelledError guard in messenger
- **Task ID**: build-cancel
- **Depends On**: none
- **Validates**: `tests/unit/test_messenger_cancelled_error.py` (create), `tests/unit/test_worker_cancel_requeue.py` (update)
- **Assigned To**: cancel-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- In `agent/messenger.py::_run_work` (L238), add `except asyncio.CancelledError:` branch before `except Exception`.
- The handler: best-effort `asyncio.wait_for(self.messenger._send_callback("I was interrupted and will resume automatically. No action needed."), timeout=2.0)` wrapped in its own `try/except (Exception, asyncio.TimeoutError)`; then `raise`.
- Keep existing `except Exception` unchanged for other errors.

### 9. Validate cancel guard
- **Task ID**: validate-cancel
- **Depends On**: build-cancel
- **Assigned To**: unit-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_messenger_cancelled_error.py` with tests: CancelledError during coro → interrupted message sent, send_callback raises → swallowed, CancelledError re-raised from handler, TimeoutError on send → swallowed.
- Update `tests/unit/test_worker_cancel_requeue.py` to add `test_cancelled_error_delivers_interrupted_message`.

### 10. Clean up PM persona
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: manual grep + persona consistency check
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- In `config/personas/project-manager.md`: remove `[PIPELINE_COMPLETE]` references at L44, L49, L384, L487.
- Rewrite Rule 5 (L42-52): preserve the semantic intent (no completion while PR open) without mentioning the marker.
- Remove the marker instruction sentence from the fan-out section (L384) and from the Pre-Completion Checklist (L487).
- Add a new short section or inline note: "Final delivery is automatic. When the pipeline reaches a terminal state, the worker will compose your final summary by asking you directly. Do not emit any special markers."

### 11. Integration tests
- **Task ID**: build-integration
- **Depends On**: build-router, build-cancel, build-persona, build-fanout
- **Validates**: `tests/integration/test_pm_final_delivery.py` (create)
- **Assigned To**: integration-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_pm_final_delivery.py` with three test scenarios:
  - `test_happy_path_merge_success_delivers_summary_within_60s` — mock MERGE completion, assert `send_cb` called with non-empty summary within 60s.
  - `test_empty_harness_result_delivers_fallback` — mock `get_response_via_harness` returning `""`, assert fallback summary delivered.
  - `test_cancelled_error_delivers_interrupted_message` — mock worker shutdown during completion turn, assert "interrupted" message delivered.
- Follow patterns from `tests/integration/test_session_finalization_decoupled.py`.

### 12. Documentation
- **Task ID**: document-feature
- **Depends On**: build-integration (so docs describe the final landed behavior)
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-final-delivery.md` covering: problem statement, protocol (pipeline-complete predicate, completion runner, fan-out path, CancelledError path), race-condition mitigations, deprecation note for the marker.
- Update `docs/features/README.md` index table.
- Update `docs/features/pipeline-state-machine.md` L161 with the new mechanism.
- Update `docs/features/agent-message-delivery.md` with a "PM final delivery" subsection.
- Update `docs/features/session-steering.md` to clarify the marker deprecation and new fan-out path.
- Save a learning memory: `python -m tools.memory_search save "PM final delivery uses a dedicated completion turn invoked by _handle_dev_session_completion when is_pipeline_complete() returns True. The [PIPELINE_COMPLETE] marker was removed in issue #1058 because content-marker-based routing fails under context overflow, stale UUIDs, and persona drift. See docs/features/pm-final-delivery.md." --importance 7.0 --source agent`

### 13. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-predicate, validate-runner, validate-router, validate-cancel, build-integration, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`.
- Run `python -m ruff format --check .` and `python -m ruff check .`.
- Run `grep -rn PIPELINE_COMPLETE agent/ tests/ config/personas/ docs/features/` — assert ONLY expected hits (deprecation notes or historical references in docs).
- Run `grep -rn deliver_pipeline_complete agent/ tests/` — assert zero matches.
- Verify all Success Criteria checkboxes.
- Generate final report to PM session.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_pm_final_delivery.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Marker removed from code | `grep -rn PIPELINE_COMPLETE_MARKER agent/` | exit code 1 |
| Marker removed from persona | `grep -c "\[PIPELINE_COMPLETE\]" config/personas/project-manager.md` | output == "0" |
| deliver_pipeline_complete action removed | `grep -rn deliver_pipeline_complete agent/ tests/` | exit code 1 |
| New predicate module exists | `test -f agent/pipeline_complete.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/pm-final-delivery.md` | exit code 0 |
| CancelledError handler present | `grep -n "except asyncio.CancelledError" agent/messenger.py` | output contains "except asyncio.CancelledError" |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Completion prompt wording.** The current draft is generic ("Write a 2-3 sentence summary"). Should it be more prescriptive (e.g., "Summary should cover: (1) what was accomplished, (2) any tradeoffs/decisions, (3) next steps or follow-ups")? More structure yields more consistent summaries but reduces flexibility.

2. **Caching `_check_pr_open` result.** The predicate is called in `_handle_dev_session_completion`, which can fire multiple times per PM (once per child). Each call currently runs a subprocess. Is a 5-second TTL cache on `extra_context` sufficient, or do we need a more robust cache (Redis-backed)?

3. **Non-MERGE terminal paths.** The predicate's "DOCS success + no open PR" branch handles issues where merge happens elsewhere (e.g., plan PRs that close automatically). Should we enumerate the legitimate non-MERGE terminal paths explicitly, or keep the generic "stage=DOCS + no PR" heuristic?

4. **Shutdown drain for completion runners.** How long should the drain timeout be? Memory extraction uses 5 seconds; completion runners need 10-15 seconds to let the harness finish. Does that conflict with our shutdown SLA?

5. **Deprecation period for the marker.** Should we keep `PIPELINE_COMPLETE_MARKER` as a no-op stripped string for one release cycle (to handle any PM session that still emits it from persona memory), or remove it immediately? The issue's Acceptance Criteria allow for a compatibility shim.
