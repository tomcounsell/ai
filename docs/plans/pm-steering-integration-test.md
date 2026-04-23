---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1057
last_comment_id:
revision_applied: true
---

# PM Steering Popoto Integration Test (follow-up for hotfix #1055 / PR #1056)

## Problem

PR #1056 shipped a hotfix that decoupled post-session memory extraction from session finalization so that a hung extraction call could not delay the PM nudge beyond the 5-second SLO. The plan (`docs/plans/unblock-worker-event-loop-hotfix.md`, archived post-merge; origin at commit `fc60fe4b`, Test Impact lines 395-432) called for a Popoto-backed integration test asserting that after a dev session completes and extraction is deferred, **the PM's ``queued_steering_messages`` list on the Popoto record grew by exactly 1 within the 5-second SLO window**.

The shipped test (`tests/integration/test_session_finalization_decoupled.py`) uses a simpler `asyncio.Event`-based stub for the PM nudge, with documented rationale (lines 7-13) pointing to the Popoto + pyrogram + harness-subprocess stack as too heavy for the scheduler-boundary contract being verified there. Three tests pass, but none read the PM's real Popoto field back to confirm the inbox grew.

**Current behavior:**
- Scheduler-boundary contract: **covered** by `test_session_finalization_decoupled.py` (asyncio.Event proxy).
- Fire-and-forget invariants of `_schedule_post_session_extraction`: **covered** by `test_session_executor_extraction_decoupling.py`.
- `_handle_dev_session_completion` → `steer_session` → `push_steering_message` → `queued_steering_messages` field write, end-to-end on a real Popoto `AgentSession` pair: **not covered**. The closest test (`tests/integration/test_parent_child_round_trip.py::test_success_result_steers_parent`) patches `steer_session`, so the real Popoto field growth is never exercised.

**Desired outcome:**
- A single new integration test (or focused extension of `test_session_finalization_decoupled.py`) that:
  1. Creates a real Popoto `AgentSession` pair (PM role + Dev role with `parent_agent_session_id` pointing at PM).
  2. Advances the PM's PipelineStateMachine to a non-terminal stage (BUILD) so `classify_outcome` has context and `is_pipeline_complete` returns False — this is the path where the real steering write happens.
  3. Stubs the CLI harness and stubs `run_post_session_extraction` to stall (≥ 10s of cooperative `asyncio.sleep`).
  4. Exercises the post-finalization slice: `_schedule_post_session_extraction(...)` then `await _handle_dev_session_completion(...)` with no patches on `steer_session`, `push_steering_message`, or `AgentSession`.
  5. Asserts within 5 seconds that the PM's **reloaded-from-Popoto** `queued_steering_messages` list grew by exactly 1 relative to the pre-call baseline, and that the new entry contains the dev session's result preview.
  6. Confirms the scheduled extraction task is still `.done() is False` at the assertion point.
  7. Cleans up both sessions via Popoto (`session.delete()`) in teardown, using a recognizable `project_key` prefix (`test-1057-`).

## Freshness Check

**Baseline commit:** `a834d9b3127580fda118a6be49a3b8235458eadb`
**Issue filed at:** 2026-04-19T16:19:46Z
**Disposition:** Minor drift (one material change: #1089 added an is_pipeline_complete predicate that short-circuits the steering path when the pipeline is terminal)

**File:line references re-verified:**
- `tests/integration/test_session_finalization_decoupled.py:7-13` — issue-cited rationale for the lighter-weight approach. **Still holds** verbatim.
- `agent/session_executor.py:1478` (schedule extraction) and `agent/session_executor.py:1502-1507` (`_handle_dev_session_completion` call). Line numbers verified against current HEAD. **Still holds.**
- `agent/session_completion.py:653` `_handle_dev_session_completion` definition. **Still holds.** New predicate check at lines 777-833 (added by #1089) short-circuits via `schedule_pipeline_completion` BEFORE reaching the `steer_session` call at line 846-848. This means the test must drive the PM to a non-terminal stage so the steering branch (not the completion branch) is taken.
- `agent/session_executor.py:499-552` `steer_session` — still calls `session.push_steering_message(message)` at line 539. **Still holds.**
- `models/agent_session.py:1402-1440` `push_steering_message` / `pop_steering_messages` / `queued_steering_messages = ListField(null=True)`. **Still holds** — docstring was tweaked (ChatSession → PM session; no semantic change).

**Cited sibling issues/PRs re-checked:**
- #1055 (root bug, worker event-loop stall) — closed 2026-04-20.
- #1056 (hotfix PR) — merged 2026-04-20T02:47:50Z.
- #987 (ordering invariant between `complete_transcript` and `_handle_dev_session_completion`) — still enforced at `session_executor.py:1481-1507` (comments intact).
- #1058 (PM final-delivery protocol) — closed 2026-04-21 via #1089; added `is_pipeline_complete` predicate. **This is the one change the test must accommodate** (see Disposition).

**Commits on main since issue was filed (touching referenced files):**
- `a13b7470` (compaction hardening #1135) — added compaction guards in `send_to_chat`; does NOT touch the post-finalization extraction / steering path. **Irrelevant.**
- `f147a85d` (#1123 PM auto-slug) — raised `_DEV_RESULT_PREVIEW_MAX_CHARS`; bumps the truncation length used in the steering message but doesn't change the call graph. **Irrelevant** to the exactly-one assertion.
- `9ebdfe78` (#1089, feat #1058 PM final-delivery protocol) — added `is_pipeline_complete` check that returns BEFORE `steer_session` when the pipeline is in terminal MERGE-success. **Partially addresses** in the sense that a different code path exists, but not the path this test targets. Test must drive a non-terminal stage.
- `0fd28c87` (original hotfix #1056) — the commit that introduced `_schedule_post_session_extraction`. **Still present** and unchanged.

**Active plans in `docs/plans/` overlapping this area:** None found for the session_completion / steering / extraction decoupling area. (Greps for `1055`, `1056`, `1057`, `steering`, `extraction` in `docs/plans/*.md` and `docs/plans/completed/*.md` returned no active overlap.)

**Notes:** The one material drift is the `is_pipeline_complete` guard added by #1089. The test must advance the PM to the BUILD stage (non-terminal) so the steering branch (not the pipeline-complete branch) is taken. This matches the existing pattern in `tests/integration/test_parent_child_round_trip.py::test_success_result_steers_parent` lines 158-167.

## Prior Art

- **PR #1056** (`session/unblock-worker-event-loop-hotfix`, merged 2026-04-20) — shipped the hotfix and the three companion tests (`test_session_finalization_decoupled.py`, `test_session_executor_extraction_decoupling.py`, `test_memory_extraction.py::TestEventLoopSafety`). This issue is the explicit follow-up.
- **#987** (PM-final-delivery ordering) — closed prior to this timeline, established the invariant that `_handle_dev_session_completion` runs AFTER `complete_transcript`. Our test must call them in this order.
- **#1018** (PM→Dev mid-execution steering silently fails on CLI-harness children) — closed 2026-04-17. Addressed at a different layer (steering injection at turn boundary in `_execute_agent_session:1218-1241`). Unrelated to the post-finalization steering path this test targets.
- **Test pattern — `tests/integration/test_parent_child_round_trip.py::TestHandleDevSessionCompletion`** (not closed; still active) — already creates real Popoto PM+Dev AgentSession fixtures and calls `_handle_dev_session_completion` directly. The only gap versus the issue: it patches `steer_session`, so the real `queued_steering_messages` field is never written. This plan eliminates that patch and adds the field-growth assertion.
- **Test pattern — `tests/integration/test_pm_final_delivery.py`** (#1058, merged 2026-04-21) — pattern for stubbing `agent.sdk_client.get_response_via_harness` and driving session-completion logic against Popoto. Relevant for the harness stub, less so for the assertion (that test targets `send_cb`, not `queued_steering_messages`).

## Research

No external research required — this is a pure test-depth enhancement of internal code paths. All context comes from codebase.

## Data Flow

Trace of the production path the test exercises (post-harness, post-finalization):

1. **Entry**: `_execute_agent_session` sees `task.error` is falsy and `_session_type == "dev"` (session_executor.py:1502).
2. **Extraction scheduled** (fire-and-forget): `_schedule_post_session_extraction(session.session_id, task._result or "")` at L1478 wraps a coroutine around `run_post_session_extraction` and stores the task in `_pending_extraction_tasks[session_id]`. Returns synchronously.
3. **Dev completion handler invoked**: `await _handle_dev_session_completion(session, agent_session, result)` at L1503-1507.
4. Inside `_handle_dev_session_completion` (session_completion.py:653):
   - Reads `parent_agent_session_id` (either from `agent_session` or the outer `session` Path-B fallback at L693-695).
   - Looks up parent PM via `AgentSession.get_by_id(parent_id)` at L705.
   - Runs `PipelineStateMachine` to compute `current_stage` and `outcome` (L711-722). For BUILD with a success-like result, outcome=success and stage=BUILD.
   - **Branch decision at L777-833**: `is_pipeline_complete(psm_states, outcome, pr_open=...)`. For BUILD (not MERGE-success), `is_complete=False` and the function falls through to the steering block.
   - At L840-848: constructs `steering_msg` (format: `"Dev session completed. Stage: BUILD. Outcome: success. Result preview: ...\n\nIMPORTANT: If an open PR exists..."`) and calls `steer_session(parent.session_id, steering_msg)`.
5. `steer_session` (session_executor.py:499-552): looks up PM via `AgentSession.query.filter(session_id=...)`, verifies status not terminal (L532-537), calls `session.push_steering_message(message)` at L539.
6. `push_steering_message` (agent_session.py:1402-1423): appends to `self.queued_steering_messages` list, partial-saves with `update_fields=["queued_steering_messages", "updated_at"]`.
7. **Output visible to test**: re-query PM via `AgentSession.query.filter(session_id=pm.session_id)`; `queued_steering_messages[-1]` contains the steering message string.

**Extraction task status at output time**: its coroutine is suspended inside `asyncio.sleep(10)` — `task.done() is False`. `_pending_extraction_tasks[dev_session.session_id]` still holds the task reference.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue)
- Review rounds: 1 (one `/do-pr-review` pass on the new test)

Solo dev work is fast — this is one new test function (plus fixture scaffolding) in one file. The bottleneck is getting the assertion shape right — specifically, reading the PM's field back from Popoto after the partial-save commits.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running on localhost:6379 | `redis-cli ping` | popoto test fixtures need Redis |
| pytest + pytest-asyncio installed | `python -c "import pytest_asyncio"` | async test harness |
| Codebase AgentSession importable | `python -c "from models.agent_session import AgentSession"` | popoto model ready |

No new dependencies. All prerequisites are baseline repo state.

## Solution

### Key Elements

- **New test case** in the existing `tests/integration/test_session_finalization_decoupled.py` (or a new sibling file — see "Technical Approach" for the decision rationale). Single test function, documents the precise scope the shipped scheduler-boundary test deferred.
- **Real Popoto fixture pair**: one PM `AgentSession` (`session_type="pm"`, `status="active"`, PipelineStateMachine advanced to BUILD) and one Dev `AgentSession` (`session_type="dev"`, `parent_agent_session_id=<pm.agent_session_id>`, `status="active"`). Both created in a function-scoped fixture, deleted in teardown.
- **Harness stub**: patch `agent.sdk_client.get_response_via_harness` to return a canned success string. (Not strictly required since we don't call `_execute_agent_session` directly — see Technical Approach — but retained to match the exact stub pattern used by sibling tests.)
- **Extraction stall stub**: patch `agent.memory_extraction.run_post_session_extraction` to `await asyncio.sleep(10)` — this is longer than the 5s SLO window, so the task stays pending the whole assertion window.
- **Production call shape**: exercise `_schedule_post_session_extraction(dev.session_id, "<success result preview>")` then `await _handle_dev_session_completion(session=dev, agent_session=dev, result="<same>")` — the same sequence executed in `_execute_agent_session:1478-1507` post-hotfix.

**Implementation Note (C1 — deliberate omission of `complete_transcript`):** The test deliberately does **not** call or exercise `complete_transcript(...)` before the `_handle_dev_session_completion` invocation. The ordering invariant from #987 (transcript completes first, then dev-completion handler runs) is enforced at `agent/session_executor.py:1481-1507` via explicit comments, and is already covered by sibling tests (`tests/integration/test_parent_child_round_trip.py` and the session-executor unit tests). This plan's test is scoped to the **post-finalization slice only** — specifically the `queued_steering_messages` field-write invariant under a stalled extraction. Folding transcript completion into this test would (a) require a `ChatSession`/`BossMessenger` fixture pair just to satisfy the transcript side effects, (b) broaden the stub surface to cover transcript persistence, and (c) re-cover an invariant that already has direct tests elsewhere. See also No-Go bullet on `complete_transcript` coverage below. If the builder finds that skipping `complete_transcript` causes `_handle_dev_session_completion` to fail on a missing transcript dependency, STOP and raise this as a blocker — do **not** silently add a transcript-complete call to satisfy the test; that would expand scope past what this plan authorized.
- **Field-growth assertion**: re-query the PM from Popoto (`AgentSession.query.filter(session_id=pm.session_id)`) and compare `queued_steering_messages` length to the pre-call baseline. Must be exactly 1 longer.
- **5-second SLO assertion**: wrap the whole post-finalization sequence in `time.monotonic()` start/end; elapsed < 5.0s.
- **Extraction-pending assertion**: `_pending_extraction_tasks[dev.session_id].done() is False` at assertion time.
- **Cleanup**: explicit `session.delete()` on both PM and Dev records in a `finally` block (defensively, since `redis_test_db` already flushes the test db, but the project_key hygiene rule in `CLAUDE.md` applies regardless).

### Flow

**Test start** → Create PM `AgentSession` (project_key=`test-1057-popoto`, advance to BUILD) → Create Dev `AgentSession` linked via `parent_agent_session_id` → Patch extraction to `sleep(10)` → Record `baseline_len = len(pm.queued_steering_messages or [])` (expected 0) → `t0 = time.monotonic()` → Call `_schedule_post_session_extraction(dev.session_id, result)` → `await _handle_dev_session_completion(session=dev, agent_session=dev, result=result)` → `elapsed = time.monotonic() - t0` → Re-query PM → Assert `len(pm_reloaded.queued_steering_messages) == baseline_len + 1` → Assert `elapsed < 5.0` → Assert `"BUILD" in pm_reloaded.queued_steering_messages[-1]` (or `"Dev session completed"`) → Assert `_pending_extraction_tasks[dev.session_id].done() is False` → Cleanup (cancel task, delete both sessions).

### Technical Approach

**Placement decision: extend the existing `tests/integration/test_session_finalization_decoupled.py`** rather than create a new file. Rationale:
- The shipped test file's docstring (lines 1-42) explicitly references #1057 as the follow-up and commits to the scheduler-boundary contract being verified there being a narrower version of the Popoto-backed contract. Colocating the follow-up keeps both invariants side-by-side and lets the docstring cross-reference the new class. One file per "hotfix #1055 decoupling guarantee," narrow + deep.
- A new file would duplicate the fixture imports and the `_clear_pending_tasks` autouse fixture.

**What we explicitly do NOT patch** (contrast with `test_parent_child_round_trip.py::test_success_result_steers_parent`):
- `agent.session_executor.steer_session` — the real function runs.
- `models.agent_session.AgentSession.push_steering_message` — the real method runs; it's the method under test.
- `agent.session_completion._extract_issue_number` — in the prior-art test this is patched to return None to skip GitHub comment posting. We MUST also patch it here (issue number lookup would attempt a `gh` subprocess call), but this is a pragmatic short-circuit of a side effect, NOT a mock of the code under test.
- `utils.issue_comments.post_stage_comment` — likewise patched with a lambda returning `True`, since the test has no GitHub access.

**What we DO patch**:
- `agent.memory_extraction.run_post_session_extraction` — stub with `await asyncio.sleep(10)` so extraction stalls past the 5s SLO window.
- `agent.session_completion._extract_issue_number` — return `None` (short-circuits the GitHub comment-posting branch).
- `agent.sdk_client.get_response_via_harness` — set via `AsyncMock(return_value="...")`, defensively. Not strictly needed since we don't call `_execute_agent_session`, but included for parity with sibling tests and to catch any accidental call.

**Why narrowing to `_schedule_post_session_extraction + _handle_dev_session_completion` is faithful to the issue's "real `_execute_agent_session` path"**:
- The issue's "drives the real `_execute_agent_session` path with a stubbed-out CLI harness" phrasing describes the **code path under test**, not the literal outermost call. That literal interpretation would require stubbing BossMessenger, BackgroundTask, worktree_manager, enrichment, routing callbacks, calendar heartbeats, and 20+ other collaborators — that's precisely what the shipped test's rationale called "too heavy."
- The post-finalization slice (`_schedule_post_session_extraction` → `_handle_dev_session_completion`) is the only part of `_execute_agent_session` that reaches the PM's inbox. Everything before it is plumbing for harness I/O, which has separate coverage (`test_harness_*`).
- Running that slice against real Popoto and without patching the steering chain gives us faithful end-to-end coverage of the exact invariant the issue is asking about: "PM's `queued_steering_messages` grew by exactly 1 within the 5s SLO."

**pyrogram / transport stubbing**: not needed. The post-finalization slice never calls into pyrogram; `steer_session` writes to Popoto only (optionally kicks a worker ping at L541 which is wrapped in `try/except RuntimeError`).

**Implementation Note (C2 — canonical import path):** The test MUST import `_handle_dev_session_completion` as:

```python
from agent.agent_session_queue import _handle_dev_session_completion
```

Do **not** import it from `agent.session_completion` even though that module is where the function is *defined*. `agent.agent_session_queue` re-exports it (see `agent/agent_session_queue.py:52`), and the rest of the codebase (including `agent/session_executor.py:16`) imports it via `agent.agent_session_queue`. Using the re-export keeps the test aligned with production call sites — so if the re-export is ever removed or renamed, the test fails loudly at import time instead of silently binding to a stale module path. This applies to both the top-of-file import in the test module AND any `unittest.mock.patch(...)` targets that reference the function by its dotted path.

Consequence for patching: when the test patches `_extract_issue_number` or related helpers, it MUST patch them at `agent.session_completion.<name>` (the definition module) because that is where the call site inside `_handle_dev_session_completion` resolves them. Do NOT patch them at `agent.agent_session_queue.<name>`. This is the standard "patch where it's looked up, not where it's defined" rule — the re-export of `_handle_dev_session_completion` does NOT re-export its internal callees.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `_handle_dev_session_completion` wraps the whole body in `try/except Exception` (session_completion.py:686 / 928-929). The test does not need to assert this silence — it's already covered by sibling unit tests. We DO assert the primary success invariant (field grew by 1).
- [x] `steer_session` returns a dict on terminal-status rejection (L532-537) — not raised, no exception to swallow. The PM is driven to `"active"` in the fixture so this branch is not taken.

### Empty/Invalid Input Handling
- [x] No new empty-input paths created by the test. The dev session's `result` is a fixed non-empty string.
- [x] Empty `queued_steering_messages` at baseline (field initialized as `None`, normalizes to `[]` inside `push_steering_message` at L1408-1410). The test asserts baseline length is 0, which exercises the `isinstance(current, list)` guard.

### Error State Rendering
- [x] No user-visible rendering involved — this is an internal Popoto field write. The "rendering" here is the test's final assertion, which reads the field back and compares to baseline.

## Test Impact

- [ ] `tests/integration/test_session_finalization_decoupled.py` — UPDATE: append a new test class `TestPMSteeringPopotoIntegration` with one test case (`test_pm_queued_steering_messages_grew_by_exactly_one_within_5s`). Reuse the existing `_clear_pending_tasks` autouse fixture for extraction-task teardown. Update the module docstring's note on lines 7-13: replace the "A follow-up issue (#1057) tracks adding..." sentence with "The follow-up test `TestPMSteeringPopotoIntegration` below now provides that Popoto-backed coverage." so readers see both layers of coverage in one place.

No other existing tests are affected — this is a pure addition alongside the existing scheduler-boundary class. The existing `TestSessionFinalizationDecoupled` class and its three tests are untouched.

## Rabbit Holes

- **Driving the full `_execute_agent_session` call**: tempting to "go further" than the shipped test; do not. Stubbing BossMessenger/BackgroundTask/worktree/enrichment for a post-finalization assertion is exactly the yak-shave the shipped test's docstring warned against. The post-finalization slice gives full coverage of the invariant without the stubbing blast radius.
- **Asserting the specific wording of the steering message** beyond the "contains BUILD" / "contains 'Dev session completed'" substring checks: the message text is shaped by `_handle_dev_session_completion:840-848` and its format is owned by that function's tests. We check enough to prove the right message landed — not enough to couple to wording changes.
- **Testing the `is_pipeline_complete` MERGE-success branch**: out of scope; already covered by `tests/integration/test_pm_final_delivery.py`. This test's job is the steering branch, not the completion branch.
- **Running a real harness subprocess**: pointless — `get_response_via_harness` is only called in the pre-finalization code path we are NOT exercising. Patch it defensively with `AsyncMock` so a regression that accidentally invokes it fails loudly.
- **Asserting TIME-to-push_steering_message precisely within a sub-second budget**: the 5s SLO is the promised budget; the actual write is a single Redis hash partial-save plus a dict append, which is sub-millisecond. A sub-second budget would be brittle and adds no information. Stick with `elapsed < 5.0`.
- **Covering `_create_continuation_pm` fallback** (when steer returns success:False for terminal parent): separate test path. This test drives the happy-path branch.

## Risks

### Risk 1: `push_steering_message` partial-save is eventually consistent when re-queried
**Impact:** The test re-queries the PM via `AgentSession.query.filter(session_id=pm.session_id)` and reads `queued_steering_messages`. If Popoto's partial-save to Redis is not observable from the re-query before the assertion, the test would see the stale value (length 0) and fail spuriously.
**Mitigation:** `push_steering_message` uses Popoto's `save(update_fields=...)` which is a synchronous `HSET` against Redis. The re-query is a `HGETALL` on the same key. Both go through the same (sync) connection popoto was pointed at by the `redis_test_db` fixture. There is no async intermediary here, so the write is fully visible by the time `push_steering_message` returns. Verified by grep: `push_steering_message` is declared `def`, not `async def`. `steer_session` is also `def`, not `async def` — so by the time `await _handle_dev_session_completion(...)` returns in the test, the Popoto write has already completed synchronously. No poll needed.
**Backstop:** If the assertion is flaky in practice, add a bounded poll with `asyncio.sleep(0.05)` up to 5s total — but this is belt-and-suspenders over a synchronous write.

### Risk 2: `is_pipeline_complete` short-circuits the steering branch if MERGE is marked completed
**Impact:** If the test advances the PM too far through the pipeline and accidentally completes MERGE (or any stage after DOCS), `_handle_dev_session_completion` returns early via `schedule_pipeline_completion` at L830-833 and never calls `steer_session`. The PM's field grows by 0.
**Mitigation:** The fixture advances the PM exactly to BUILD via `PipelineStateMachine.start_stage("BUILD")` (NO `complete_stage`). No other stages are transitioned. This exactly matches the pattern in `tests/integration/test_parent_child_round_trip.py:158-167` which is known to hit the steering branch. Explicit assertion in the test: after `psm.start_stage("BUILD")`, read back `PipelineStateMachine(pm).current_stage() == "BUILD"`.

### Risk 3: Worker ping inside `steer_session` spawns a thread or fails the test environment
**Impact:** `steer_session` at L541 calls `_call_ensure_worker(...)`. If this attempts to spawn a worker process or hit a real system resource, the test environment may not support it.
**Mitigation:** Already wrapped in `try/except RuntimeError` at L542-543 with comment "No event loop (CLI context) — worker will pick it up on next loop". Under pytest's `asyncio_mode = auto` we ARE in a running event loop, so the function will try its normal path (`_ensure_worker_alive` — verified to be a no-op if a worker is already considered alive for the project_key, which it will be since we never started one but it polls a process table). Worst case: the `_ensure_worker_alive` call raises something unexpected, which propagates up and fails the test with a clear error. We defensively wrap `steer_session`'s worker-ping side effect by patching `agent.session_executor._call_ensure_worker` with a MagicMock in the test. This is a surgical side-effect suppression, not a mock of code under test.

### Risk 4: `_extract_issue_number` attempts a `gh` subprocess or Redis lookup
**Impact:** Test environment may not have `gh` authenticated, or the lookup may depend on state the test doesn't set.
**Mitigation:** Patch `agent.session_completion._extract_issue_number` to return `None`. This matches the pattern used by `test_parent_child_round_trip.py:176`. Returning None skips the stage-comment branch cleanly (L751 `if issue_number and current_stage`).

### Risk 5: `STEERING_QUEUE_MAX` interferes with the length assertion
**Impact:** If baseline `queued_steering_messages` is already at `STEERING_QUEUE_MAX`, push will drop oldest and length stays the same.
**Mitigation:** Fresh fixture PM starts with `queued_steering_messages=None` → normalizes to `[]` → baseline length is 0. Pushing 1 message yields length 1, which is well under `STEERING_QUEUE_MAX` (**10** per `models/agent_session.py:52`). No risk in practice.

**Implementation Note (C3 — source of truth):** `STEERING_QUEUE_MAX` is defined at `models/agent_session.py:52` as `STEERING_QUEUE_MAX = 10` (NOT 50 — an earlier draft of this plan cited 50 in error). The builder MUST verify the constant's value at build time by reading `models/agent_session.py` directly — do not trust this number in the plan. If the constant has moved or changed value by the time the build runs, update the citation here and in the assertion reasoning. The cap is small enough (10) that it is plausibly relevant to a test that exercises `push_steering_message` in a loop; this test only pushes once, so the cap is not load-bearing for the length assertion, but the citation must remain accurate so future readers / future tests don't copy a wrong number.

## Race Conditions

### Race 1: Extraction task completes before assertion fires
**Location:** `agent/session_executor.py:_schedule_post_session_extraction` and the test's `await _handle_dev_session_completion` boundary.
**Trigger:** If the stub is accidentally made short (`asyncio.sleep(0.001)` instead of `asyncio.sleep(10)`), the extraction task could complete before the test asserts `task.done() is False`.
**Data prerequisite:** Stub must suspend for longer than the entire `_handle_dev_session_completion` + assertion window.
**State prerequisite:** The `_pending_extraction_tasks[dev.session_id]` entry must still be populated at assertion time.
**Mitigation:** Stub suspends for 10 seconds — an order of magnitude longer than the expected `_handle_dev_session_completion` runtime (which is dominated by Redis partial-save latency, ~sub-millisecond per operation). The autouse `_clear_pending_tasks` fixture at teardown cancels any lingering tasks, so the sleep never actually fires to completion.

### Race 2: Heartbeat or auto-save races push_steering_message writes
**Location:** `models/agent_session.py:push_steering_message` partial-save.
**Trigger:** `push_steering_message` uses `save(update_fields=["queued_steering_messages", "updated_at"])`. If a concurrent caller (e.g., a heartbeat tick) does a full `save()` on the same PM record between the push and the re-query, the write could be clobbered.
**Data prerequisite:** No concurrent writer on `pm` during the test.
**State prerequisite:** Only the `_handle_dev_session_completion` call may write to `pm.queued_steering_messages` during the test window.
**Mitigation:** No worker loop, no heartbeat task, no health check is running in the test — this is a pure sync-async test. The only path that writes to `pm.queued_steering_messages` is the `push_steering_message` call inside `steer_session`. No race possible in the test environment.

## No-Gos (Out of Scope)

- **Redo the scheduler-boundary assertions** already covered by `TestSessionFinalizationDecoupled` (the shipped test class). The new test class targets a different layer.
- **Any production code changes** — this is test-depth only. If the new test surfaces a production bug, STOP and file a separate issue before patching.
- **Exercise the MERGE-success / `is_pipeline_complete` branch** — already covered by `test_pm_final_delivery.py`.
- **Exercise the terminal-parent continuation-PM fallback** (`_create_continuation_pm`) — separate code path, separate test.
- **Drive the full outermost `_execute_agent_session`** — see Rabbit Holes; not aligned with the issue's practical intent.
- **Add coverage for `post_stage_comment` / GitHub integration** — out of scope; patched to no-op.
- **Change `test_parent_child_round_trip.py`** — it already provides Popoto coverage of `_handle_dev_session_completion` with a `steer_session` patch. We do not need to modify it; this plan's test provides the stricter no-patch coverage.
- **Cover `complete_transcript` ordering or side-effects** — **deliberate omission.** The #987 invariant ("_handle_dev_session_completion runs AFTER complete_transcript") is already tested at the session-executor call-site level and by `test_parent_child_round_trip.py`. This plan's test exercises the **post-finalization slice only** (`_schedule_post_session_extraction` → `_handle_dev_session_completion`), starting at a point where a successful transcript completion is presumed. Adding transcript coverage here would require ChatSession/BossMessenger fixtures, widen the patch surface, and duplicate sibling coverage. If future work needs a single test that spans transcript → dev-completion → steering end-to-end, file a separate issue — do not fold it into this one.

## Update System

No update system changes required — this is a test file addition. The `/update` skill pulls from main and re-runs test dependencies; nothing about the test suite's install surface changes. No new pip dependencies, no new config files, no new launchd items.

## Agent Integration

No agent integration required — this is a pytest test file invoked by `pytest tests/integration/test_session_finalization_decoupled.py` or by the agent's existing `/do-test` skill. No MCP server changes, no `.mcp.json` edits, no bridge imports. The test runs as part of the integration suite.

## Documentation

### Feature Documentation
- [ ] No new feature docs needed — this test extends existing coverage for a shipped hotfix documented at `docs/features/subconscious-memory.md` ("Event-Loop Safety (hotfix #1055)" subsection). The section is complete and does not require a test-depth update.

### External Documentation Site
- [ ] Not applicable — repo does not use an external doc site.

### Inline Documentation
- [ ] Update the module docstring at `tests/integration/test_session_finalization_decoupled.py:1-42` to point to the new test class (one-line revision to replace the "A follow-up issue (#1057) tracks adding..." sentence).
- [ ] Docstring on the new test class and test method — describe the precise invariant being asserted (field grew by exactly 1 on real Popoto within 5s SLO).

**No documentation changes beyond the test file itself.** All justification is captured in the plan and the inline docstrings. This is an intentionally narrow scope — the plan itself serves as the durable artifact for why this test exists.

## Success Criteria

- [ ] New test class `TestPMSteeringPopotoIntegration` with one test method `test_pm_queued_steering_messages_grew_by_exactly_one_within_5s` is added to `tests/integration/test_session_finalization_decoupled.py` and passes (`pytest tests/integration/test_session_finalization_decoupled.py -v -k TestPMSteeringPopoto`).
- [ ] The test asserts `len(pm_reloaded.queued_steering_messages) == baseline_len + 1` with `baseline_len == 0`.
- [ ] The test asserts `elapsed < 5.0` seconds for the full `_schedule_post_session_extraction + _handle_dev_session_completion` sequence.
- [ ] The test asserts the extraction task remains `.done() is False` at assertion time, proving the extraction stall did not delay the PM inbox write.
- [ ] The test asserts the pushed message contains the dev session's result preview and the stage label (substring check, not exact match).
- [ ] The test uses `project_key="test-1057-popoto"` (or equivalent recognizable prefix).
- [ ] The test cleans up both PM and Dev AgentSession records via `session.delete()` in a `finally` block, in addition to the autouse `redis_test_db` flushdb.
- [ ] Existing three tests in `TestSessionFinalizationDecoupled` still pass unchanged.
- [ ] Module docstring at lines 7-13 updated to point to the new test class.
- [ ] `python -m ruff format tests/integration/test_session_finalization_decoupled.py` clean.
- [ ] Tests pass (`/do-test` or `pytest tests/integration/test_session_finalization_decoupled.py -v`).

## Team Orchestration

### Team Members

- **Builder (integration-test)**
  - Name: `pm-steering-integration-test-builder`
  - Role: Implement the new test class and update the module docstring.
  - Agent Type: builder
  - Resume: true

- **Validator (integration-test)**
  - Name: `pm-steering-integration-test-validator`
  - Role: Verify the test passes, verify cleanup happens via Popoto ORM, verify project_key hygiene, verify no production code changes were made.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Built on `builder` and `validator` — no specialists needed for this narrow scope.

## Step by Step Tasks

### 1. Implement integration test
- **Task ID**: build-pm-steering-integration-test
- **Depends On**: none
- **Validates**: `tests/integration/test_session_finalization_decoupled.py::TestPMSteeringPopotoIntegration::test_pm_queued_steering_messages_grew_by_exactly_one_within_5s`
- **Informed By**: none (no spikes; Technical Approach is fully specified)
- **Assigned To**: `pm-steering-integration-test-builder`
- **Agent Type**: builder
- **Parallel**: false
- Open `tests/integration/test_session_finalization_decoupled.py`.
- Update the module docstring lines 7-13: replace "A follow-up issue (#1057) tracks adding the full Popoto-backed test that exercises the PM inbox end-to-end." with a reference to the new class.
- Below the existing `TestSessionFinalizationDecoupled` class, add a new class `TestPMSteeringPopotoIntegration` with one `@pytest.mark.asyncio` method `test_pm_queued_steering_messages_grew_by_exactly_one_within_5s`.
- Inside the test:
  - Imports (use the canonical paths pinned in the Technical Approach C2 note):
    - `from models.agent_session import AgentSession`
    - `from models.pipeline_state_machine import PipelineStateMachine`
    - `from agent.agent_session_queue import _handle_dev_session_completion` (re-export path — NOT `agent.session_completion`)
    - `from agent.session_executor import _schedule_post_session_extraction, _pending_extraction_tasks`
  - Create `pm = AgentSession.create(session_type="pm", project_key="test-1057-popoto", status="active", session_id=f"pm-1057-{time.time_ns()}", chat_id="1057", sender_name="TestUser", message_text="BUILD issue #1057", created_at=datetime.now(tz=UTC), started_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC), turn_count=0, tool_call_count=0)`.
  - Advance PM to BUILD: `sm = PipelineStateMachine(pm); sm.start_stage("ISSUE"); sm.complete_stage("ISSUE"); sm.start_stage("PLAN"); sm.complete_stage("PLAN"); sm.start_stage("CRITIQUE"); sm.complete_stage("CRITIQUE"); sm.start_stage("BUILD")`.
  - Reload PM: `pm = list(AgentSession.query.filter(session_id=pm.session_id))[0]`.
  - Create Dev: `dev = AgentSession.create(session_type="dev", project_key="test-1057-popoto", status="active", session_id=f"dev-1057-{time.time_ns()}", chat_id="1057", sender_name="TestUser", message_text="Stage: BUILD", parent_agent_session_id=pm.agent_session_id, created_at=datetime.now(tz=UTC), started_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC), turn_count=0, tool_call_count=0)`.
  - Patches (use `unittest.mock.patch` context managers, stacked):
    - `agent.memory_extraction.run_post_session_extraction` -> `async def _slow(session_id, response_text, project_key=None): await asyncio.sleep(10)`.
    - `agent.session_completion._extract_issue_number` -> `MagicMock(return_value=None)`.
    - `agent.session_executor._call_ensure_worker` -> `MagicMock()` (suppress worker-ping side effect).
    - `agent.sdk_client.get_response_via_harness` -> `AsyncMock(return_value="PR created at https://github.com/test/repo/pull/42. BUILD complete.")` (defensive — not called in this path, but patched for parity).
  - Inside the stacked `with` block:
    - Assert `baseline_len = len(pm.queued_steering_messages or []) == 0`.
    - `result = "PR created at https://github.com/test/repo/pull/42. BUILD complete."`
    - `t0 = time.monotonic()`
    - `_schedule_post_session_extraction(dev.session_id, result)`
    - `await _handle_dev_session_completion(session=dev, agent_session=dev, result=result)`
    - `elapsed = time.monotonic() - t0`
    - Reload PM: `pm_reloaded = list(AgentSession.query.filter(session_id=pm.session_id))[0]`.
    - Assert `len(pm_reloaded.queued_steering_messages or []) == baseline_len + 1 == 1`, failure message: the exact delta and elapsed time for diagnosis.
    - Assert `elapsed < 5.0`, failure message: the measured elapsed time.
    - Assert `"BUILD" in pm_reloaded.queued_steering_messages[-1]` AND `"Dev session completed" in pm_reloaded.queued_steering_messages[-1]`.
    - Assert `dev.session_id in _pending_extraction_tasks` and `_pending_extraction_tasks[dev.session_id].done() is False`.
  - In `finally`:
    - Cancel any lingering extraction task (belt-and-suspenders; autouse fixture does this too).
    - Delete `pm` and `dev` via Popoto (not strictly needed with `redis_test_db` flushdb, but respects the CLAUDE.md Manual Testing Hygiene rule).
- Run: `python -m ruff format tests/integration/test_session_finalization_decoupled.py`.

### 2. Validate integration test
- **Task ID**: validate-pm-steering-integration-test
- **Depends On**: build-pm-steering-integration-test
- **Assigned To**: `pm-steering-integration-test-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_session_finalization_decoupled.py -v` — all 4 tests pass (3 existing + 1 new).
- Run `pytest tests/integration/test_session_finalization_decoupled.py::TestPMSteeringPopotoIntegration -v` — 1 new test passes standalone.
- Grep for any production code changes: `git diff --name-only main...HEAD -- agent/ models/ bridge/ worker/` must return only test files (nothing in production dirs). Enforced by the Success Criteria.
- Verify project_key in the test matches the recognizable prefix rule (`test-1057-*`).
- Verify `session.delete()` calls exist in the `finally` block (grep the test file).
- Verify the module docstring reference to #1057 has been updated.
- Run `python -m ruff check tests/integration/test_session_finalization_decoupled.py` — clean.
- Report pass/fail status.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pm-steering-integration-test
- **Assigned To**: (no separate documentarian — docs are inline in the plan and the test module)
- **Agent Type**: documentarian
- **Parallel**: false
- No external doc updates required (see Documentation section above). This step is a no-op except for verifying the module docstring edit was actually made and is accurate.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `pm-steering-integration-test-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_session_finalization_decoupled.py -v` — all 4 tests pass.
- Verify all Success Criteria boxes above are ticked or justified.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New test passes | `pytest tests/integration/test_session_finalization_decoupled.py::TestPMSteeringPopotoIntegration -v` | exit code 0 |
| Existing tests still pass | `pytest tests/integration/test_session_finalization_decoupled.py::TestSessionFinalizationDecoupled -v` | exit code 0 |
| No production code changes | `git diff --name-only main...HEAD -- agent/ models/ bridge/ worker/ tools/ mcp_servers/` | exit code 0 with empty output |
| Format clean | `python -m ruff format --check tests/integration/test_session_finalization_decoupled.py` | exit code 0 |
| Recognizable project_key | `grep -c 'test-1057-' tests/integration/test_session_finalization_decoupled.py` | output > 0 |
| Explicit session cleanup | `grep -cE '\.delete\(\)' tests/integration/test_session_finalization_decoupled.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Verdict: READY TO BUILD (with concerns). Revision pass applied 2026-04-23. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | C1 | Deliberate omission of `complete_transcript` coverage was implicit; future readers could mistake it for a gap. | Revision pass (2026-04-23): added explicit No-Go bullet under `## No-Gos` and Implementation Note in Technical Approach. | See Technical Approach → "Implementation Note (C1 — deliberate omission of `complete_transcript`)" and `## No-Gos` bullet on `complete_transcript` ordering. |
| CONCERN | C2 | Import path for `_handle_dev_session_completion` was unpinned; builder could import from definition module (`agent.session_completion`) instead of canonical re-export (`agent.agent_session_queue`). | Revision pass (2026-04-23): added Implementation Note in Technical Approach pinning `from agent.agent_session_queue import _handle_dev_session_completion`, plus updated task-step 1 imports to match. | See Technical Approach → "Implementation Note (C2 — canonical import path)" and Step 1 imports section. |
| CONCERN | C3 | `STEERING_QUEUE_MAX` cited as 50; actual value is 10 (`models/agent_session.py:52`). | Revision pass (2026-04-23): corrected citation in Risk 5 from 50 → 10 with source-of-truth Implementation Note directing builder to verify value at build time. | See Risks → "Risk 5" and "Implementation Note (C3 — source of truth)". |

---

## Open Questions

None. Scope is fully locked by the issue; freshness check identified one material drift (`is_pipeline_complete` added by #1089) and the plan accommodates it by driving PM to BUILD (non-terminal) stage. No supervisor input required before critique.
