---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/948
last_comment_id:
revision_applied: true
---

# Centralize Dedup Recording in Bridge Dispatch

## Problem

The dedup contract between the Telegram bridge's live handler and the reconciler is enforced per-call-site, not per-dispatch. Every live-handler branch that enqueues (or resolves-without-enqueueing) a message must remember to call `record_message_processed(chat_id, message_id)` so the reconciler skips the message on its next 3-minute scan. There are 6 such call sites today; adding a new early-return branch and forgetting this single line produces a duplicate agent session with no compile-time or test-time signal.

**Current behavior:**

- On 2026-04-14 11:54:01 a single PM: PsyOPTIMAL message was processed by the live handler's resume-completed-session branch, which enqueued the session but did not record dedup.
- ~2m 33s later the reconciler scanned the chat, found the message absent from dedup, re-classified it as missed, and enqueued a second session under a different `session_id`.
- The user received two replies — one from the live-handler session (delivered via Valor's user account) and one from the reconciler session (delivered via the relay/bot account).
- A secondary problem: `docs/features/message-reconciler.md:83-85` claims the live/reconciler race is benign because the queue coalesces duplicate `session_id`s. The claim holds only when both paths derive the same `session_id`. The live handler's resume-completed branch keys by the pre-existing session (e.g. `tg_psyoptimal_-1003743854645_89`); the reconciler always keys by incoming message ID (e.g. `tg_psyoptimal_-1003743854645_93`). Different IDs, no coalescing, duplicate dispatch.

**Desired outcome:**

- A single dispatch wrapper records dedup as part of the enqueue contract. The live handler never calls `record_message_processed` directly.
- The 5 redundant inline dedup calls in `bridge/telegram_bridge.py` disappear; the end-of-handler call is replaced by the wrapper and also disappears as a separate line.
- A regression test fails the build if a future contributor adds an `enqueue_agent_session` (or any Telegram-originating session enqueue) in the live handler without routing through the wrapper.
- `docs/features/message-reconciler.md` and `docs/features/bridge-module-architecture.md` document the ingestion flow with diagrams and correct the "benign race" claim.
- The 2026-04-14 11:54 incident cannot recur via this mechanism (verified by reading the centralized wrapper's code path, not by the hotfix alone).

## Freshness Check

**Baseline commit:** `54ea819f`
**Issue filed at:** 2026-04-14T05:10:04Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1336-1340` (issue's resume-completed branch) — still present; exact dedup lines are now 1340/1342 after the hotfix rearranged adjacent ack-message removal. Observation holds.
- `bridge/telegram_bridge.py:1423/1425` (in-memory coalescing guard branch) — verified, present.
- `bridge/telegram_bridge.py:1533/1535` (intake classifier interjection branch) — verified, present.
- `bridge/telegram_bridge.py:1565/1567` (intake classifier acknowledgment branch) — verified, present.
- `bridge/telegram_bridge.py:1723/1725` (canonical end-of-handler dedup after normal enqueue) — verified, present.
- `bridge/catchup.py:217` (catchup dedup call) — verified, present.
- `bridge/reconciler.py:183` (reconciler dedup call) — verified, present.
- `bridge/telegram_bridge.py:1035` (issue-cited: live handler derives `session_id`) — line has drifted; session_id derivation is currently around `bridge/telegram_bridge.py:1122-1139` via classifier + session lookup paths. Observation about session_id reuse still holds.
- `bridge/reconciler.py:160` (reconciler always derives `tg_{project}_{chat_id}_{message_id}`) — verified at line 160 exactly.

**Cited sibling issues/PRs re-checked:**
- #588 (Bridge misses messages during live connection) — closed 2026-03-30, motivated the reconciler. Still relevant context.
- #590 (Add periodic message reconciler) — merged, introduced today's code. Still relevant.
- #720 (Fix rapid-fire message coalescing race condition) — merged 2026-03-11, addressed the in-memory guard branch that today is one of the 5 redundant dedup call sites. Still relevant.
- #918 (Bridge delivers same message multiple times to same session) — closed 2026-04-12. Different root cause (worker re-picking `running` sessions) but same symptom class (duplicate delivery). Linked in Prior Art, not a conflict.

**Commits on main since issue was filed (touching referenced files):**
- `e422fc4e` "dedup recording in bridge dispatch" — the hotfix described in the issue. Added dedup to the resume-completed branch. This is the "hotfix lands first" precondition called out in the issue. Main is now ready for the structural refactor.

**Active plans in `docs/plans/` overlapping this area:** none. `docs/plans/redis-popoto-migration.md` references `bridge/dedup.py` but addresses storage-backend migration, not the call-site contract. No overlap on the wrapper design.

**Notes:** The issue's line numbers drifted by a handful of lines after the hotfix, but every cited call site and every cited race condition still holds. Proceeding without scope change.

## Prior Art

- **Issue #588**: Bridge misses messages during live connection — no runtime gap detection. Closed 2026-03-30. Motivated the reconciler. Successful.
- **PR #590**: Add periodic message reconciler for live bridge gaps. Merged. Introduced the reconciler and with it the implicit contract that every live-handler enqueue must also record dedup. Successful on the recovery side, but left the contract as a distributed per-call-site rule.
- **PR #720**: Fix rapid-fire message coalescing race condition. Merged 2026-03-11. Added the in-memory coalescing guard and its dedup call (one of today's 5 redundant sites). Successful for its own scope; did not centralize.
- **Issue #918**: Bridge delivers same message multiple times to same session. Closed 2026-04-12. Different root cause (worker re-picking `running` sessions after completion); adjacent symptom. Not a prior fix attempt for this specific gap.
- **Hotfix `e422fc4e`**: Added the missing `record_message_processed` call to the resume-completed branch. Addresses the acute incident; does not address the structural risk.

## Data Flow

```
Telegram Update (Telethon dispatch)
    |
    v
handler() in bridge/telegram_bridge.py
    |
    +-- reply-to-valor fast path
    |       -> resume-completed branch (enqueue + return)  [site 1]
    |
    +-- in-memory coalescing guard (merge into pending session + return)  [site 2]
    |
    +-- intake classifier
    |       -> interjection: steer existing session + return  [site 3]
    |       -> acknowledgment: finalize dormant + return  [site 4]
    |       -> new_work: fall through
    |
    +-- canonical path: enqueue new AgentSession                  [site 5]
    |
    v
record_message_processed(chat_id, message_id) -> bridge/dedup.py -> DedupRecord (Popoto model, 2h TTL, ~50 IDs/chat)

Reconciler loop (every 3 min)
    |
    v
for each monitored group: get_messages(limit=20)
    for each message within lookback window:
        is_duplicate_message? -> skip if yes
        should_respond? -> skip if no
        enqueue_agent_session(priority="low")
        record_message_processed()
```

The bug surface is the five "+-- ... return" arrows in the live handler. Each one must remember to call `record_message_processed` before returning; missing it causes the reconciler to re-dispatch ~2–5 minutes later.

## Architectural Impact

- **New dependencies:** none. The wrapper lives inside `bridge/` and reuses existing `bridge.dedup` and `agent.agent_session_queue.enqueue_agent_session`.
- **Interface changes:** a new function `dispatch_telegram_session(...)` in `bridge/dispatch.py`. `enqueue_agent_session` itself is unchanged — non-Telegram callers (catchup, reconciler) continue to call it directly alongside their own explicit dedup call.
- **Coupling:** decreases. Today the live handler is coupled to the dedup module at 6 separate call sites; after this change the coupling moves to one site.
- **Data ownership:** unchanged. `DedupRecord` ownership stays with `bridge/dedup.py`.
- **Reversibility:** easy. The wrapper is a thin pass-through; reverting it would restore the 5 inline calls.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0–1 (design is settled in the issue; the wrapper-vs-inside debate is resolved in favor of wrapper)
- Review rounds: 1 (one PR review pass after build)

This is a mechanical refactor: extract a wrapper, replace 5 call sites, add one regression test, update two docs. The cost is almost entirely in the regression test design (how strictly can we enforce "no bare `enqueue_agent_session` in the handler") and the flow diagrams.

## Prerequisites

No prerequisites — this work has no external dependencies. The hotfix `e422fc4e` is already on main, so the structural refactor can be built against current HEAD.

## Solution

### Key Elements

- **`bridge/dispatch.py` (new module)**: Contains `dispatch_telegram_session(...)`. Calls `enqueue_agent_session(...)` and `record_message_processed(chat_id, telegram_message_id)` in that order. Dedup is recorded only on successful enqueue (a raised exception propagates without recording, matching today's semantics).
- **`bridge/telegram_bridge.py` (modified)**: All 5 Telegram-handler enqueue sites are replaced. Four of them (resume-completed, in-memory guard, intake interjection, intake acknowledgment) call `dispatch_telegram_session` instead of `enqueue_agent_session` + inline dedup. The fifth (canonical end-of-handler) also calls `dispatch_telegram_session`, and the tail `record_message_processed` line disappears with it.
- **Non-enqueue branches**: the in-memory coalescing guard, the intake interjection, and the intake acknowledgment branches do NOT call `enqueue_agent_session` — they steer or finalize existing sessions. These branches should call a smaller helper `record_telegram_message_handled(chat_id, message_id)` that ONLY records dedup, to preserve the "we handled this, reconciler skip it" semantic without the enqueue. Placing this helper in the same `bridge/dispatch.py` module keeps the contract in one file.
- **Regression test**: `tests/unit/test_bridge_dispatch_contract.py`. AST-based check (or regex-on-source fallback) asserting that `bridge/telegram_bridge.py` contains zero direct calls to `enqueue_agent_session(` inside the `handler()` function and zero direct calls to `record_message_processed(`. The single allowed exception is the helper module `bridge/dispatch.py`.
- **Docs**: `docs/features/message-reconciler.md` gets a corrected Race Conditions section and a Mermaid flow diagram showing the live/reconciler paths and the centralized record point. `docs/features/bridge-module-architecture.md` gets a Mermaid flow diagram of the live handler's early-return branches and the central dispatch point.

### Flow

**Telegram update arrives** → live handler classifies route → calls `dispatch_telegram_session` (enqueue branches) or `record_telegram_message_handled` (steer/finalize branches) → dedup record persisted → reconciler's next scan sees the message in dedup → skip.

### Technical Approach

- **Wrapper over enclose**: `dispatch_telegram_session` wraps `enqueue_agent_session`. The reconciler and catchup keep their current explicit pairing — they each call `enqueue_agent_session` then `record_message_processed` directly. Rationale: the reconciler and catchup are not "live dispatch" paths; they are recovery paths that already know they are writing to dedup. Moving dedup inside `enqueue_agent_session` itself would double-record for them (harmless but ugly) and would entangle a Telegram-specific concern into a generic queue primitive that also serves non-Telegram transports (email, future channels).
- **Signature for `dispatch_telegram_session`**: identical to `enqueue_agent_session` (pass through all kwargs). Return the same `depth` integer. Record dedup only after `enqueue_agent_session` returns without raising.
- **Signature for `record_telegram_message_handled`**: `(chat_id, telegram_message_id)`. Thin wrapper over `record_message_processed` that exists only for semantic symmetry at the call site ("I handled this message without enqueueing").
- **Exception semantics**: if `enqueue_agent_session` raises, we do NOT record dedup. The reconciler will re-pick the message in 3 minutes — correct behavior, matching today. Same for `record_telegram_message_handled`: the underlying `record_message_processed` already swallows exceptions and logs at debug level, so no additional handling needed.
- **Import placement**: prefer top-of-file import in `bridge/telegram_bridge.py` over the current pattern of local `from bridge.dedup import record_message_processed` inside each branch. The inline import was likely added to avoid circular imports; `bridge/dispatch.py` as a leaf module cannot be part of a cycle with `telegram_bridge.py` because it only depends on `bridge/dedup.py` and `agent/agent_session_queue.py`.
- **Regression test strategy**: use `ast.parse` on `bridge/telegram_bridge.py`, walk the tree for the `handler` function, and assert zero `Call` nodes whose `func.id` or `func.attr` is `enqueue_agent_session` or `record_message_processed`. Follow the pattern in `tests/unit/test_duplicate_delivery.py:TestCatchupCodeStructure` (regex-based) if AST ends up fragile; AST is preferred for correctness.
- **Docs-as-code**: Mermaid diagrams are rendered inline in Markdown; do not render to PNG (the docs are read in browsers/GitHub, which support Mermaid natively). No Excalidraw dependency needed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/dispatch.py` must not have `except Exception: pass`. If it wraps `enqueue_agent_session` with any try/except, the handler must log at warning and re-raise (or return the same exception-shape that today's inline code produces — today's inline code does not catch, it just propagates).
- [ ] `bridge/dedup.py::record_message_processed` already has `except Exception: logger.debug(...)`. No changes needed; existing `tests/unit/test_dedup.py::test_record_does_not_raise_on_error` covers this.

### Empty/Invalid Input Handling
- [ ] `dispatch_telegram_session` with `telegram_message_id=None` or `chat_id=None` — today `enqueue_agent_session` takes `telegram_message_id: int` and `chat_id: str`. The wrapper should type-hint the same way. No new validation needed because the live handler always supplies real values; defensive handling would be clutter.
- [ ] Empty `message_text` — passed through to `enqueue_agent_session`, which handles it today. Not in scope.

### Error State Rendering
- [ ] Not applicable. The dispatch wrapper has no user-visible output; user-visible errors flow through the existing agent session execution path.

## Test Impact

- [ ] `tests/unit/test_duplicate_delivery.py::TestCatchupCodeStructure::test_dedup_record_after_enqueue` — KEEP. This test verifies `bridge/catchup.py`'s ordering, which is not changed by this plan. The reconciler and catchup continue to call the two functions explicitly in order.
- [ ] `tests/unit/test_dedup.py` — KEEP. `bridge/dedup.py` surface area is unchanged.
- [ ] `tests/unit/test_reconciler.py` — KEEP. Reconciler is not refactored here; it still calls `record_message_processed` directly.
- [ ] `tests/integration/test_reconciler.py` — KEEP. Same reason.
- [ ] `tests/integration/test_catchup_revival.py` — KEEP. Catchup is not refactored here.
- [ ] `tests/e2e/test_session_continuity.py`, `tests/e2e/test_session_lifecycle.py`, `tests/e2e/test_message_pipeline.py` — KEEP. They import `record_message_processed` directly for test setup; the symbol still exists.
- [ ] `tests/unit/test_bridge_dispatch_contract.py` — CREATE. New regression test asserting the live handler contains no direct `enqueue_agent_session(` or `record_message_processed(` calls.

No existing tests need UPDATE, DELETE, or REPLACE — this is purely an internal refactor that preserves all observable behavior. The only ADD is the new contract test.

## Rabbit Holes

- **Don't move dedup inside `enqueue_agent_session`.** Tempting because it is even more central, but `enqueue_agent_session` serves email, future transports, and programmatic callers that are not Telegram. Coupling dedup to the generic enqueue primitive pollutes the layer. The wrapper preserves the separation.
- **Don't change `session_id` derivation in the reconciler.** The issue calls this out explicitly as dropped scope. The "different session_id, no coalescing" behavior is the symptom, not the root cause; fixing the contract gap fixes the symptom by keeping the reconciler out of the race entirely (if dedup is correctly recorded, the reconciler never sees the message).
- **Don't refactor `bridge/catchup.py` or `bridge/reconciler.py` to use the wrapper.** They are recovery paths, not live dispatch. Their semantics ("I found a missed message, now enqueue it and record it") are intentionally two-step. Bundling them into the wrapper would mislead future maintainers into thinking these paths are the same as the live handler's dispatch.
- **Don't change the `DedupRecord` wire format** (issue constraint). `bridge/catchup.py` and `bridge/reconciler.py` depend on it.
- **Don't add structured logging or telemetry for dedup outcomes.** Out of scope for a dedup-contract refactor; add in a separate issue if needed.

## Risks

### Risk 1: Regression test is too strict and breaks legitimate future changes
**Impact:** A future contributor adds a legitimate new direct call to `enqueue_agent_session` (e.g., a non-Telegram pathway inside `bridge/telegram_bridge.py`) and the test blocks the commit even though the change is correct.
**Mitigation:** Scope the AST check to the `handler()` function only, not the entire module. Non-handler code in `bridge/telegram_bridge.py` (e.g., startup, shutdown, imports-for-other-modules) is not subject to the contract.

### Risk 2: Circular import when adding `bridge/dispatch.py`
**Impact:** `bridge/dispatch.py` imports `enqueue_agent_session` from `agent/agent_session_queue.py`. If `agent_session_queue` ever imports from `bridge/` (today it doesn't), we'd have a cycle.
**Mitigation:** Verify at build time (`python -c "import bridge.dispatch"`). If a cycle arises later, resolve by keeping `enqueue_agent_session` import inside the wrapper function body, matching today's inline-import pattern.

### Risk 3: Dedup recorded before enqueue completes (reordering bug)
**Impact:** If the wrapper records dedup before `enqueue_agent_session` returns successfully, and the enqueue later raises, we've poisoned the dedup record and the reconciler will skip a message that was never enqueued.
**Mitigation:** Strict ordering: `depth = await enqueue_agent_session(...)` first, `await record_message_processed(...)` second. Assert this order in the regression test if we want belt-and-suspenders (check that the `await enqueue` appears textually before the `await record` inside `dispatch_telegram_session`).

### Risk 4: The "benign race" doc correction introduces a misleading claim
**Impact:** If the corrected text overstates the severity or understates the mitigation, reviewers will file a follow-up to re-revise.
**Mitigation:** State the race precisely: live handler resume-completed branch uses the existing `session_id`; reconciler always uses a fresh `tg_{project}_{chat_id}_{message_id}`; different IDs bypass queue coalescing. Centralized dedup recording closes the window because the reconciler's `is_duplicate_message` check will skip the message before enqueue.

## Race Conditions

### Race 1: Live handler enqueues but crashes before recording dedup
**Location:** `bridge/dispatch.py::dispatch_telegram_session` between the `await enqueue_agent_session` return and the `await record_message_processed` call.
**Trigger:** Process killed (SIGTERM, OOM) in the narrow window.
**Data prerequisite:** The AgentSession must already be in Redis after `enqueue_agent_session` returns.
**State prerequisite:** Worker has not yet picked up the session.
**Mitigation:** On crash, the worker's recovery path re-picks the enqueued session normally. If the reconciler also picks the message on its next scan (because dedup was never written), it will enqueue a second session under a different `session_id` — same bug class this issue is trying to fix, but only in a crash-during-microsecond-window scenario. Acceptable residual risk; matches today's behavior and is orders of magnitude less likely than the full class of 5 inline sites we are removing.

### Race 2: Live handler and reconciler race on a fresh message
**Location:** `bridge/telegram_bridge.py::handler` vs `bridge/reconciler.py::reconcile_once`.
**Trigger:** Reconciler scan fires while live handler is mid-processing the same message.
**Data prerequisite:** Live handler has not yet reached the `record_message_processed` line.
**State prerequisite:** `is_duplicate_message` returns False for the reconciler's check.
**Mitigation:** Today's behavior — the queue coalesces duplicate `session_id`s for the canonical path (both derive `tg_{project}_{chat_id}_{message_id}`), which is benign. The resume-completed branch is the exception and is now addressed by centralizing dedup recording so it happens before the reconciler's next scan. For deeper safety, a future follow-up could add a Redis `SETNX` lock around dispatch; out of scope here.

## No-Gos (Out of Scope)

- Changing `session_id` derivation in the reconciler to match live-handler-resume sessions. Dropped in the issue's Recon Summary.
- Moving dedup inside `enqueue_agent_session` itself.
- Refactoring `bridge/catchup.py` or `bridge/reconciler.py` to use the new wrapper.
- Adding Redis locks around dispatch (future-work if the crash-window race ever manifests in production).
- Changing the `DedupRecord` storage format or TTL.
- Rendering Mermaid diagrams to PNG (GitHub renders them natively).
- Adding structured logging or telemetry for dedup outcomes.

## Update System

No update system changes required. This refactor is purely internal to `bridge/` and requires no new dependencies, no config file changes, no migration, and no cross-machine coordination. The `/update` skill pulls code; a restart of the bridge (`./scripts/valor-service.sh restart`) picks up the new dispatch wrapper.

## Agent Integration

No agent integration required. This is a bridge-internal change. `dispatch_telegram_session` is not an agent-visible tool and is not exposed via any MCP server. The agent never calls dedup code directly; it only interacts with sessions after they are enqueued. No changes to `.mcp.json` or `mcp_servers/`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/message-reconciler.md`:
  - Replace the "benign race" paragraph (current lines 83-85) with a precise race-condition analysis covering both the canonical path (queue coalesces) and the resume-completed path (different session_ids, no coalescing, now mitigated by centralized dedup).
  - Add a Mermaid flow diagram showing live handler ingestion, reconciler ingestion, and the shared `DedupRecord` gate. Diagram must include every live-handler early-return branch.
- [ ] Update `docs/features/bridge-module-architecture.md`:
  - Add a "Message Ingestion Flow" section with a Mermaid diagram of `handler()`'s branches (reply-to-valor, in-memory coalescing, intake classifier sub-branches, canonical path) and the single `dispatch_telegram_session` entry point.
  - Reference `bridge/dispatch.py` in the sub-module responsibility table.

### External Documentation Site
- [ ] Not applicable — this repo does not use Sphinx/MkDocs/ReadTheDocs.

### Inline Documentation
- [ ] Module docstring for `bridge/dispatch.py` stating the contract: "Every Telegram-originating session enqueue goes through `dispatch_telegram_session`, which enqueues and then records dedup atomically from the caller's perspective."
- [ ] Docstring for `dispatch_telegram_session` noting that it does NOT catch exceptions from `enqueue_agent_session`; a failed enqueue leaves dedup unrecorded so the reconciler can retry.
- [ ] Docstring for `record_telegram_message_handled` noting that this is the non-enqueue counterpart (message was steered or finalized, not enqueued).

## Success Criteria

- [ ] `bridge/dispatch.py` exists with `dispatch_telegram_session` and `record_telegram_message_handled`.
- [ ] `bridge/telegram_bridge.py::handler` contains zero direct calls to `enqueue_agent_session(` or `record_message_processed(`. All 5 Telegram-originating enqueue/steer sites go through the new helpers.
- [ ] `tests/unit/test_bridge_dispatch_contract.py` exists, uses AST (or regex fallback) to enforce the contract, and fails when the live handler violates it.
- [ ] All existing tests pass: `pytest tests/ -x -q`.
- [ ] Lint clean: `python -m ruff check .` and `python -m ruff format --check .`.
- [ ] `docs/features/message-reconciler.md` no longer contains the "benign race" paragraph; replacement text matches the solution section; Mermaid diagram renders on GitHub.
- [ ] `docs/features/bridge-module-architecture.md` includes the Message Ingestion Flow section and Mermaid diagram.
- [ ] Reasoning walkthrough (documented in the PR description): given the 2026-04-14 11:54 incident's exact code path, trace through the new centralized implementation and confirm the second reconciler-driven session cannot occur. This is a reasoning check against the refactored code, not a replay of the incident.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (dispatch-wrapper)**
  - Name: dispatch-builder
  - Role: Create `bridge/dispatch.py` with the two helpers, replace the 5 call sites in `bridge/telegram_bridge.py`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (contract-test)**
  - Name: contract-test-engineer
  - Role: Author `tests/unit/test_bridge_dispatch_contract.py` (AST-based contract enforcement).
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (docs-and-diagrams)**
  - Name: docs-writer
  - Role: Update `docs/features/message-reconciler.md` and `docs/features/bridge-module-architecture.md` with corrected race analysis and Mermaid flow diagrams.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final-check)**
  - Name: final-validator
  - Role: Verify all success criteria, run pytest + ruff, and walk through the 2026-04-14 11:54 incident against the new code path.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the dispatch wrapper
- **Task ID**: build-dispatch
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_dispatch_contract.py (create), existing tests in tests/unit/test_duplicate_delivery.py, tests/unit/test_dedup.py
- **Informed By**: Freshness Check confirms hotfix `e422fc4e` landed; all 5 call sites confirmed at lines 1340/1342, 1423/1425, 1533/1535, 1565/1567, 1723/1725.
- **Assigned To**: dispatch-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/dispatch.py` with `dispatch_telegram_session(...)` (same kwargs as `enqueue_agent_session`, returns depth) and `record_telegram_message_handled(chat_id, message_id)`.
- Module docstring explains the contract.
- Function docstrings cover exception semantics and the steer-vs-enqueue distinction.
- Replace the 5 call sites in `bridge/telegram_bridge.py`:
  - Line ~1323-1342 (resume-completed branch): replace the `enqueue_agent_session(...) ... record_message_processed(...)` pair with a single `await dispatch_telegram_session(...)`.
  - Line ~1408-1425 (in-memory coalescing guard): replace the inline `record_message_processed` with `await record_telegram_message_handled(...)`.
  - Line ~1512-1535 (intake classifier interjection): same as above.
  - Line ~1554-1567 (intake classifier acknowledgment): same as above.
  - Line ~1701-1725 (canonical end-of-handler): replace `enqueue_agent_session(...)` + tail `record_message_processed(...)` with `await dispatch_telegram_session(...)`.
- Remove the 5 `from bridge.dedup import record_message_processed` inline imports from `handler()`.
- Run `python -c "import bridge.dispatch"` to verify no circular imports.

### 2. Author the contract regression test
- **Task ID**: build-contract-test
- **Depends On**: build-dispatch
- **Validates**: tests/unit/test_bridge_dispatch_contract.py (self-validating)
- **Assigned To**: contract-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_bridge_dispatch_contract.py`.
- Parse `bridge/telegram_bridge.py` with `ast.parse`, walk to the `handler` function, assert zero `Call` nodes with `func.id` or `func.attr` in {`enqueue_agent_session`, `record_message_processed`}.
- Add a positive test that `bridge/dispatch.py::dispatch_telegram_session` calls `enqueue_agent_session` before `record_message_processed` (textual or AST ordering check).
- Add a negative-path smoke test: construct a synthetic `bridge/telegram_bridge.py`-shaped module with a bare `enqueue_agent_session` call inside a handler-named function, confirm the contract check would fail on it.

### 3. Update documentation (parallel with test authoring)
- **Task ID**: document-feature
- **Depends On**: build-dispatch
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/message-reconciler.md`:
  - Replace the "Race Conditions" section (current lines 83-85) with the precise live-vs-reconciler analysis.
  - Add a Mermaid flow diagram showing live handler branches, reconciler scan, and `DedupRecord` as the shared gate. Place it under the existing "Data Flow" section or a new "Ingestion Paths" section.
  - Add `bridge/dispatch.py` to the "Files" table.
- Update `docs/features/bridge-module-architecture.md`:
  - Add `bridge/dispatch.py` to the sub-module responsibility table.
  - Add a "Message Ingestion Flow" section with a Mermaid diagram of `handler()`'s 5 branches converging on the single `dispatch_telegram_session` / `record_telegram_message_handled` entry point.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-dispatch, build-contract-test, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all pass.
- Run `python -m ruff check .` and `python -m ruff format --check .` — clean.
- Run the new contract test directly: `pytest tests/unit/test_bridge_dispatch_contract.py -v` — passes, including `test_contract_detects_violation_in_synthetic_source` (C5 — no manual inject/revert dance).
- Verify Mermaid diagrams render in both docs files (visual check on GitHub preview or with a local Mermaid renderer).
- Walk through the 2026-04-14 11:54 incident against the new code path and document the reasoning in the PR description.
- Confirm the "benign race" paragraph is gone from `docs/features/message-reconciler.md`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Contract test passes | `pytest tests/unit/test_bridge_dispatch_contract.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No direct enqueue in handler | `python -c "import ast; src=open('bridge/telegram_bridge.py').read(); tree=ast.parse(src); h=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='handler'); bad=[c for c in ast.walk(h) if isinstance(c,ast.Call) and ((isinstance(c.func,ast.Name) and c.func.id in {'enqueue_agent_session','record_message_processed'}) or (isinstance(c.func,ast.Attribute) and c.func.attr in {'enqueue_agent_session','record_message_processed'}))]; assert not bad, bad"` | exit code 0 |
| Dispatch module imports cleanly | `python -c "import bridge.dispatch; assert callable(bridge.dispatch.dispatch_telegram_session) and callable(bridge.dispatch.record_telegram_message_handled)"` | exit code 0 |
| Benign-race paragraph removed | `grep -q "benign race" docs/features/message-reconciler.md` | exit code 1 |
| Mermaid diagrams present | `grep -q '```mermaid' docs/features/message-reconciler.md docs/features/bridge-module-architecture.md` | exit code 0 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns)
**Findings:** 0 blockers, 5 concerns, 3 nits
**Revision pass:** applied — Implementation Notes embedded below.

### Concerns (with Implementation Notes)

**C1 — AST contract check must be scope-aware.**
*Concern:* A naïve `ast.walk(handler)` will descend into nested helper functions defined inside `handler()`, treating their calls as handler calls. It will also miss calls made via attribute access on imported aliases.
*Implementation Note:* In `tests/unit/test_bridge_dispatch_contract.py`, use a scope-aware walker that enters the `handler` `AsyncFunctionDef` body but does NOT descend into inner `FunctionDef`/`AsyncFunctionDef`/`Lambda` nodes. Concrete shape: write a small `_iter_direct_calls(node)` generator that yields `Call` nodes only while the current node's enclosing function is `handler` itself. Cover both `Name` (`enqueue_agent_session(...)`) and `Attribute` (`dedup.record_message_processed(...)`) forms.

**C2 — Top-level AST lookup must pin to `handler` deterministically.**
*Concern:* `next(n for n in ast.walk(tree) if ... n.name=='handler')` matches the first function named `handler` anywhere in the tree, which risks matching a nested helper or a future same-name function.
*Implementation Note:* Resolve `handler` by walking only the top-level module body (`tree.body`) and selecting the `AsyncFunctionDef` whose name is `handler` AND whose decorator list contains a `client.on(...)` call (Telethon event registration). If not found, fail the test with a clear error so a rename is caught loudly, not silently skipped. The Verification shell command in the plan should use the same deterministic lookup (walk `tree.body`, match decorator).

**C3 — `record_telegram_message_handled` shim: keep it, but justify via observability.**
*Concern:* A thin wrapper that only calls `record_message_processed` adds a second name for the same operation and duplicates the contract surface. Drop it and call `record_message_processed` directly, OR keep it and earn its weight by adding observability.
*Implementation Note:* Keep the shim and earn its weight. Inside `record_telegram_message_handled`, emit a single `logger.debug("telegram message handled without enqueue: chat=%s msg=%s", chat_id, message_id)` before delegating to `record_message_processed`. This gives the "steered/finalized without enqueue" branches a grep-able signature distinct from the enqueue path, which is exactly the semantic the shim exists to express. No new log levels, no new metrics.

**C4 — `record_message_processed` swallows exceptions; dedup failures are invisible.**
*Concern:* `bridge/dedup.py::record_message_processed` has `except Exception: logger.debug(...)`. A Redis outage silently disables dedup, and the reconciler will re-dispatch every message for the duration of the outage. Debug-level logging guarantees no one will notice.
*Implementation Note:* Raise the log level in `bridge/dedup.py::record_message_processed` from `logger.debug(...)` to `logger.warning(...)` and include the exception in the message (`logger.warning("dedup record failed for chat=%s msg=%s: %s", chat_id, message_id, exc)`). Do NOT re-raise — the caller's semantics (never break on dedup failure) are correct. Add a unit test in `tests/unit/test_dedup.py::test_record_logs_warning_on_redis_failure` asserting the warning is emitted when the underlying save raises.

**C5 — Replace the manual "inject a bare call, confirm, revert" step with a synthetic-source smoke test.**
*Concern:* Task 4 currently asks the validator to manually inject a bare `await enqueue_agent_session(...)` into `handler()` to confirm the contract test fails, then revert. This is fragile: a revert failure leaves the repo dirty; a skipped verification defeats the contract.
*Implementation Note:* In `tests/unit/test_bridge_dispatch_contract.py`, add a test case `test_contract_detects_violation_in_synthetic_source` that constructs an in-memory string containing a minimal module with an async function named `handler` decorated with `@client.on()`, containing a bare `await enqueue_agent_session(...)` call. Run the same AST walker against this synthetic source and assert the walker reports the violation. This provides the same guarantee without touching the real source tree. Remove the manual "inject and revert" step from Task 4's validator checklist; update the Success Criteria accordingly.

### Nits (acknowledged, non-blocking)

- N1: Consider renaming `dispatch_telegram_session` → `dispatch_telegram_message` for symmetry with `record_telegram_message_handled`. Deferred — not worth churn if current name ships first.
- N2: Verification table's shell one-liner is brittle (single-line AST via `python -c`); the unit test supersedes it, but keep the shell check as a smoke signal.
- N3: `Step by Step Tasks` header numbering skips from 4 → no 5; cosmetic.

---

## Open Questions

1. **Regression test scope**: should the AST contract also forbid imports of `enqueue_agent_session` / `record_message_processed` inside `handler`'s enclosing module scope, or only direct calls within `handler`? I propose calls-only (import at module top is fine; only the handler body is constrained). Confirm.
2. **Non-enqueue steering sites — wrapper or raw call?**: the intake interjection, intake acknowledgment, and in-memory coalescing guard branches don't enqueue anything; they steer or finalize existing sessions. I'm proposing a separate `record_telegram_message_handled` helper for semantic clarity. Alternative: let these three branches just call `record_message_processed` directly (one import at top of file, three call sites), and reserve the wrapper only for actual enqueue sites. The wrapper is cleaner for the contract test (one rule: no direct `record_message_processed` in handler); the raw-call approach is less code. Which do you prefer?
3. **Mermaid vs ASCII diagrams**: I propose Mermaid because GitHub renders it natively. `docs/features/message-reconciler.md` currently uses ASCII art. Should I keep the ASCII art and add Mermaid alongside, or replace ASCII with Mermaid outright?
