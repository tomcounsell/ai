---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-27
tracking: https://github.com/tomcounsell/ai/issues/3027
last_comment_id: none
---

# Promise gate: key on recorded obligations, not grammar — and run it on the path the PM actually uses

## Problem

Issue #3027 (rev 2) documents two verified failures of the outbound honesty gate:

**Symptom 1 — half an ask silently dropped.** A two-clause ask ("merge to main and test on stage server") got a reply covering one clause; the second was never mentioned — not done, not declined, not deferred. All three gate layers correctly allowed it: no component receives both the ask and the reply, so a dropped clause is structurally undetectable.

**Symptom 2 — the gate taught the PM to evade it.** "Say the word and I'll re-run that same dispatch" blocks 8/8 on the LLM layer as `forward_deferral` but shipped anyway, because the PM's real route (`output_handler.send` → `draft_message` → `_evaluate_drafter_promise`) is regex-only. The PM prompt's phrasebook (`prime-pm-role.md:51–67`) teaches phrasings measured against a gate that is not on the PM's path. A rule that grades grammar can only ever be satisfied by better grammar.

**Current behavior:** the gate's discriminator is the presence of a forward-looking clause; the drafter path never consults the LLM layer; nothing checks reply-vs-ask coverage; completion claims need no evidence.

**Desired outcome:** a forward-looking statement is honest iff the obligation behind it is durably recorded (Job inbound expectation, `schedule_id`, or PR URL); the LLM judgment runs on the main drafter path; every clause of an ask carries a user-visible disposition; bare completion claims are structurally unshippable.

The issue's R1–R4 rulings are decided (R3 conditional in execution — see Solution). This plan implements them; it does not re-litigate them.

## Freshness Check

**Baseline commit:** `00a3d93ca` (re-verified 2026-09-03; supersedes the 2026-08-27 baseline `d7b7e92cb`)
**Issue filed at:** 2026-08-26T10:45:36Z (body rewritten 2026-08-27 with re-verified claims)
**Disposition:** Amended — every load-bearing mechanism holds; one new outbound route appeared and is dispositioned as a No-Go below.

**Anchors that hold, re-read at `00a3d93ca`.** Symbol names are the durable anchor; line numbers below are current, not the ones the 2026-08-27 pass recorded.

- `bridge/message_drafter.py:667` `_evaluate_drafter_promise` — regex-only, by-design docstring — holds.
- `bridge/message_drafter.py:1160` (short path) / `:1211` (main path) — the two `_evaluate_drafter_promise` call sites Task 5 awaits — hold, both still inside `draft_message` (`:1072`).
- `bridge/promise_gate.py:402` `promise_override_active` + `:422` owner-ruling note — hold.
- `bridge/promise_gate.py:661` `evaluate_promise` signature carries no ask — holds.
- `bridge/promise_gate.py:762,830` `_PromiseTimeoutError` raised nowhere, caught at `:762` — the dead-code fix in Task 5 is still needed.
- `agent/session_runner/router.py:61` `PM_TURN_JSON_SCHEMA` (route/message/file_paths/blocked_reason only) — holds.
- `.claude/commands/roles/prime-pm-role.md:50-70` phrasebook — holds, shifted from `:51-67` (line 71 is the blank separator before the retained "Client rooms and Eng rooms" heading at 72). Task 3 anchors on text, not line numbers.
- `docs/features/promise-gate.md:124` "the presence of a forward-looking clause, not the presence of evidence" — holds.
- `models/agent_session.py:189` `initial_telegram_message` — holds.
- `agent/session_runner/role_driver.py:418` `run_turn(message)` — holds.

**What moved on main, and what it costs this plan.** Four commits since the prior baseline touched referenced files: #3080 (ask-me Telegram polls), #3084 (persona toolbelts Lane A), `c09775999` (dev subagent model pinning), `f6ba598ce` (Fable 5.1 default).

1. **A new human-facing outbound route now bypasses the drafter entirely.** #3080 added `TelegramRelayOutputHandler.send_poll` (`agent/output_handler.py:1523`), which validates its question through the new public seam `bridge/message_drafter.py::validate_poll_question` and deliberately never calls `draft_message` — composition would staple an emoji prefix and stage line into a poll question. Owner Ruling 4 ("all `draft_message` callers get the main-path LLM gate") therefore no longer means "all human-facing outbound". Dispositioned as **No-Go 5** below, with the residual risk named.
2. **`prime-pm-role.md` gained two bullets downstream of the phrasebook region** — the `/ask-me` routing bullet (line 91) and the `[missing-capability]` escalation line (line 93), both from #3080. They are not adjacent to the deletion: the whole "Jobs: goals and expectations" section (74-84) and four other persona bullets (87-90) sit between. Task 3 deletes the phrasebook only; both bullets are retained verbatim. Text-exact anchoring makes this safe — a line-number-based edit could clip all of it.
3. Line drift in `bridge/message_drafter.py` (+44 lines above the gate functions) and `prime-pm-role.md` (+1 line above the phrasebook). Recorded in the anchor list above; no behavioral consequence.
4. #3084, `c09775999`, and `f6ba598ce` touch persona/model selection, not the gate, the drafter, or the schema. No consequence.

**Cited sibling issues/PRs re-checked (2026-09-03):**
- #2494 / #2632 / #2708 — closed/merged; expectation machinery shipped (PRs #2631, #2814).
- #2423 — closed by PR #2621 (terminal flush gated).
- #3016 — still OPEN; failure is `action='block'` with `class_=None` — a label assertion, reconciled by this plan (test rewrite, `Closes #3016` in the implementation PR).
- #3035 — still OPEN, still embargoed until 2026-09-10, which is after this plan lands. Owner Ruling 2 stands: phase 4 and the required-tightening decision remain out of this lane.

**Active plans in `docs/plans/` overlapping this area:** the prior coordination note on `ask-me-telegram-polls.md` is discharged — it merged as #3080 and its `bridge/message_drafter.py` footprint (`_validate_for_medium`'s `telegram_poll` branch, `validate_telegram_poll`, `validate_poll_question`) is function-disjoint from this plan's `_evaluate_drafter_promise`/`draft_message` changes, as predicted. `overclaim-guard-greps-whole-worktree.md` is unrelated despite the name (grep/`__pycache__` hygiene).

## Prior Art

- **#2421 / PR #2621** — "Promise gate is unreachable for replies under 200 chars." Established `_evaluate_drafter_promise` as the single chokepoint both `draft_message` return paths flow through. This plan preserves that chokepoint; the LLM call lands inside it, main path only.
- **#2490** — kill switch coverage on the drafter path; the `PROMISE_GATE_ENABLED` handling in `_evaluate_drafter_promise` exists because of it. Retained.
- **#2423 / PR #2621** — terminal-flush bypass; prior instance of a delivery route skipping an honesty check. Pattern this plan closes for the main drafter route.
- **#2664 / PR #2676** — "evidence-bearing progress updates" — the PR that authored the phrasebook this plan deletes. Its intent (speak in present facts) is preserved; its gate-satisfaction framing is not.
- **#2494 / #2632 / #2708 (PRs #2631, #2814)** — Job / expectation machinery, the advisory revise-or-override flow, and `promise_override_active`. R1 promotes this shipped mechanism from override-hatch to primary discriminator.
- **#2139 / PR #2175** — schedulable check-in primitive; `schedule_id` citations already satisfy `_SCHEDULED_DELIVERY_PATTERNS` (tested in `test_checkin_primitive.py`). Unchanged.
- **#1055** — coroutine-level timeouts leak httpx connections under cancellation; gate timeouts must go to the `AsyncAnthropic` constructor. Binding constraint on Task 5.

## Research

No external research performed — the work is purely internal (existing gate, prompts, schema, and Anthropic SDK patterns already in the repo). The one external-facing bet in the issue (CLI JSON Schema `if/then` draft coverage) is deliberately avoided by the two-phase optional→required rollout.

## Spike Results

Three parallel code-read spikes ran at plan time (Explore agents; full reports in session transcript).

### spike-1: Test-surface inventory
- **Assumption**: "The gate/drafter/schema test surface is known well enough to write Test Impact."
- **Method**: code-read
- **Finding**: Complete inventory produced (see Test Impact). Three load-bearing discoveries: (a) `tests/unit/test_pm_progress_updates.py::TestPromiseGateFallbackAllowsTaughtPhrasings` is a **direct prompt-to-gate lock** — every phrasing the PM role doc teaches must clear `_evaluate_promise_heuristic`; the phrasebook deletion breaks it first. (b) ~~`tests/fixtures/persona/eng_worker_repo_baseline.txt` quotes both prompt files~~ — **spike-1 was wrong here; disproved at critique.** The fixture contains zero phrasebook text and `compose_system_prompt` never reads the role files. No regeneration is needed. See Task 3. (c) Three coverage gaps: the short-path zero-LLM guarantee is asserted by no test; `PM_TURN_JSON_SCHEMA` has exactly one structural assertion; `SCHEMA_ROUTING_FALLBACK_METRIC` is never asserted at its emission site.
- **Confidence**: high
- **Impact on plan**: Test Impact section is exhaustive; Tasks 1/3/5 each close one of the three gaps.

### spike-2: Ask-threading call chain
- **Assumption**: "The turn's full ask can reach the drafter without a data-model change."
- **Method**: code-read
- **Finding**: The **same in-memory `AgentSession` instance** travels runner → adapter closure → `send_cb` → `output_handler.send` → `draft_message` (object identity preserved; delivery runs as a separate `loop.create_task` on the same loop, after `_route_turn` returns). Minimal plumbing is one write: the runner stamps the turn's ask on the session instance before `_run_one_turn` (`runner.py:796`); the drafter reads it off `session`. A durable Popoto field is needed only if the value must survive a crash or reach CLI senders. Caveat: "the ask" is ambiguous — turn 1 is human text (+ pre-run steering already merged durably into `initial_telegram_message` by `session_pickup.py:212-233`); later turns are usually `PM_COMPLIANCE_NUDGE` or merged steers, and mid-run steer text is never persisted anywhere.
- **Confidence**: high
- **Impact on plan**: Phases 1–3 need **no ask plumbing at all** (the PM already sees the ask in its own context; the schema forces per-clause confrontation there). Session-stamping is specified for phase 4 only, with the anchor defined as human-authored inputs (initial text + human steers), never nudge/wrap-up prompts. Race 1 documents the overwrite hazard.

### spike-3: Telemetry, latency, and LLM-call scaffolding
- **Assumption**: "The p50<500ms/p99<3s budget is instrumented somewhere we can extend."
- **Method**: code-read
- **Finding**: **Zero latency instrumentation exists** — the budget is prose in `bridge/promise_gate.py:74-76`; nothing measures p50, and a 200ms→2.9s regression would hide inside the 3s SDK timeout. `draft_message` is already `async def` with all three callers awaiting it (`output_handler.py:703`, `hooks/stop.py:147`, `email_bridge.py:925`), but its body has zero awaits and no LLM imports — adding the call is greenfield-in-module with the async plumbing free. The SDK timeout is `RTR_SDK_TIMEOUT = 3.0` (`bridge/read_the_room.py:87`), imported by the gate with a do-not-redefine comment; it is NOT in `TimeoutSettings`. Two hard invariants: timeout at the `AsyncAnthropic` constructor (never `asyncio.wait_for` — #1055) and `semaphore_slot()` around the call. Bonus finding: `_PromiseTimeoutError` is dead code — `APITimeoutError` returns `None` like every other failure, so `source="promise_gate_timeout"` has zero live occurrences and real timeouts are misrecorded as `promise_gate_heuristic`; the or-assertion in `test_promise_gate.py:533` cannot catch it. Audit file has 40 legacy rows with no `kind` key (sources `llm`/`empty`/`heuristic`) that any reader must tolerate.
- **Confidence**: high
- **Impact on plan**: Task 5 adds `elapsed_ms` to audit rows (the measurement is greenfield); the drafter awaits a new async public gate API directly (the sync `evaluate_promise` does `asyncio.run` internally and would deadlock inside the drafter's running loop); the timeout dead-code fix rides along in Task 5; the audit-reader in Task 7's measurement tooling tolerates legacy rows.

## Data Flow

1. **Entry point**: human message → bridge → `AgentSession` (full text durably in `initial_telegram_message.message_text`; pre-run steers merged in by `session_pickup`).
2. **Runner**: `session_executor` composes turn input → `SessionRunner.run` → `RoleDriver.run_turn(message)` → `claude -p` subprocess. The PM's terminal `StructuredOutput` call is validated against `PM_TURN_JSON_SCHEMA` — **phase 1 adds `ask_coverage` here** (per-clause `{item, disposition, evidence}`).
3. **Routing**: `_route_turn` → `adapter.on_user_payload(payload, file_paths)` → `_deliver_sync` → `loop.create_task(send_cb(...))`. **Phase 1 adds the coverage bounce here** (runner layer, where the validated schema object lives): a turn whose `ask_coverage` carries non-`delivered` clauses is not dispatched; instead a coverage advisory (enumerating each such clause and its disposition) is pushed as self-draft steering, and the PM's revised message — which must state those dispositions in prose — is what ships. One bounce per turn, bounded like the existing self-draft flow.
4. **Delivery**: `output_handler.send` → `draft_message(text, session, medium)` → `_evaluate_drafter_promise` (single chokepoint, both return paths). **Phase 3 makes the main path await the LLM layer**; short path (<200 chars, non-SDLC, no artifacts/`?`/fence) keeps the regex heuristic. BLOCK → `promise_override_active(session)` (open inbound expectation on the bound Job) → override or self-draft steering.
5. **Output**: Telegram/email outbox; audit row to `logs/classification_audit.jsonl` (now with `elapsed_ms`); phase-4 measurement samples these rows plus the schema objects.

## Architectural Impact

- **New dependencies**: none. The drafter gains its first LLM call, but via the existing gate module's client pattern (`AsyncAnthropic`, `MODEL_FAST`, `semaphore_slot`, `RTR_SDK_TIMEOUT`).
- **Interface changes**: `PM_TURN_JSON_SCHEMA` gains optional `ask_coverage`; `bridge/promise_gate.py` gains an async public API (`evaluate_promise_async`) sharing the audit/fallback internals with the sync one; `_evaluate_drafter_promise` becomes async with a `use_llm` discriminator, both call sites awaiting it. The sync `evaluate_promise` contract is untouched — CLI consumers (`tools/send_message.py`, `tools/valor_telegram.py`, `tools/valor_email.py`, `agent/session_health.py`) see no change.
- **Coupling**: drafter→gate coupling deepens (already imports the heuristic); runner→schema coupling gains one field. No new cross-module edges.
- **Data ownership**: unchanged. Obligations stay on Job; `ask_coverage` lives in the turn's transient schema payload (not persisted to a model in phases 1–3).
- **Reversibility**: high. `PROMISE_GATE_ENABLED` kill switch covers the new LLM path (it lives inside `_evaluate_drafter_promise`, which already honors it); `ask_coverage` is optional in phase A of the rollout; prompt changes revert with a file.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (owner rulings already recorded in the issue), plan critique round

**Interactions:**
- PM check-ins: 1–2 (open-questions round now; phase-4 gate decision after soak)
- Review rounds: 1 (critique) + PR review

The conditional phase 4 is deliberately **not** in this lane's build scope (see No-Gos) — this plan ships phases 1–3 plus the measurement that decides phase 4.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Real-API gate integration tests (`tests/integration/test_promise_gate_real_api.py`). **Resolve through `utils.api_keys`, not `dotenv_values('.env')`** — `.env` is a gitignored symlink that exists only in the main checkout, so a path-relative read returns `{}` in this lane's worktree and reports a present credential as missing. `utils.api_keys` is what the gate itself uses. |
| Redis reachable | `python -c "import redis,os; redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0')).ping()"` | Popoto-backed unit tests (Job/expectation override leg) |

Run via `python scripts/check_prerequisites.py docs/plans/promise-gate-recorded-obligations.md`.

## Solution

### Key Elements

- **`ask_coverage` schema field (R4)** — per-clause self-attested dispositions with mandatory evidence on `delivered`; a turn with non-delivered clauses takes one advisory bounce (self-draft steering enumerating them), so the PM's own revised prose carries every clause's status to the user. Owner ruling 2026-08-27: bounce chosen over a deterministic runner-appended footer.
- **PM prompt rewrite (R1/R4)** — phrasebook deleted; present-fact reporting norm retained; new rules: "a dispatch you can execute, you execute" and "a commitment you keep is a commitment you record (`expectation-add`)".
- **LLM on the drafter main path (R2)** — `_evaluate_drafter_promise` awaits the gate's LLM judgment on the composed path; short path stays regex-only. Combined with the shipped `promise_recorded_override`, this **is** R1's over-claim behavior: forward-looking + recorded expectation passes; forward-looking + nothing recorded blocks, however phrased.
- **Latency instrumentation** — `elapsed_ms` on every audit row (greenfield; nothing measures the budget today) plus a unit test pinning the short path to zero LLM calls.
- **Phase-4 measurement (R3 gate)** — an audit-sampling report comparing `ask_coverage` objects against delivered text; the entry criterion for ever building `evaluate_delivery`.

### Flow

Human ask → PM turn → StructuredOutput `{route, message, ask_coverage[]}` → runner validates (delivered ⇒ evidence) → non-delivered clauses ⇒ one coverage bounce (advisory steering → PM revises) → `output_handler.send` → `draft_message` → `_evaluate_drafter_promise` (main path: LLM verdict; block ⇒ open-inbound-expectation check ⇒ override or self-draft steering with revise-or-record advisory) → delivery + audit row with `elapsed_ms`.

### Technical Approach

- **Schema (Task 1).** `ask_coverage`: optional array of `{item: string, disposition: enum(delivered|blocked|declined|not_started), evidence: string}`; validation in `validate_structured_route` rejects `delivered` with empty/whitespace `evidence` (treat as invalid structured output → existing fallback path, observable via `SCHEMA_ROUTING_FALLBACK_EVENT`). `route:"continue"` accepts `[]`/absent. **Two-phase rollout:** phase A optional-but-prompted (this plan); phase B (tighten to required) only after `SCHEMA_ROUTING_FALLBACK_METRIC` confirms flat against its existing 5%/1-hour alert (`monitoring/schema_routing_alert.py:34-35`) over the #3035 window (not before 2026-09-10). No JSON Schema `if/then` — the enforcement lives in Python validation, avoiding the unverified CLI draft-coverage bet.
- **Coverage bounce (Task 1).** Owner ruling 2026-08-27: advisory bounce, not a runner-appended footer. When the validated payload's `ask_coverage` contains non-`delivered` clauses, the runner withholds dispatch and pushes self-draft steering carrying a coverage advisory — the same channel and posture as the promise advisory (revise, don't mechanically rewrite). The advisory enumerates each non-delivered clause and its disposition and instructs the PM to state them in prose; the revised turn ships. **Exactly one coverage bounce per turn** (the revision is delivered even if still imperfect — no semantic verification of the revision, which would be the arms race R1 kills), and the existing `self_draft_attempts` bound (`test_output_handler_drafter.py::test_self_draft_attempts_bound_terminates_loop`) is the global backstop. Advisory text must itself clear the heuristic (precedent: `test_deferred_self_draft_completed.py::test_substitute_message_passes_the_heuristic`).
- **Prompt (Task 3).** Delete `prime-pm-role.md:51-67` (measured-verdicts table + "two ways to stay on the allowed side"). Retain, reworded without gate framing: present-fact norm, turn-boundary/bounded-dispatch guidance, client-room threshold. Add: dispatch-you-can-execute rule; record-don't-phrase rule; `ask_coverage` authoring guidance (decompose the ask, dispositions honest, evidence = artifact reference). `_prime-rails.md:57` unchanged (its rule is now enforced by schema, but the prose stays as the cross-repo rail).
- **Drafter LLM (Task 5).** New public `async def evaluate_promise_async(text, *, transport, session_id, classifier_verdict)` in `bridge/promise_gate.py` extracted from the existing internals (the sync `evaluate_promise` becomes a `_run_async_safely` wrapper over it — behavior-identical for CLI callers). `_evaluate_drafter_promise` becomes async, gains `use_llm: bool`; `draft_message:1116` (short path) passes `use_llm=False`, `:1167` (main path) `use_llm=True`. LLM failure/timeout falls to the heuristic exactly as the sync path does (fail-closed on judgment, fail-open on infrastructure — posture unchanged). Override ordering unchanged: any BLOCK (LLM or heuristic) → `promise_override_active` → allow as `promise_recorded_override`. Constraints: timeout via `AsyncAnthropic(timeout=RTR_SDK_TIMEOUT)` constructor (#1055), `semaphore_slot()` wrap, `_evaluate_drafter_promise` remains the single chokepoint (#2421). Audit: main-path LLM verdicts write `source="promise_gate_drafter_llm"` (additive value; existing vocabulary untouched — confirmed by owner 2026-08-27). Ride-along fix: remove dead `_PromiseTimeoutError`, map `APITimeoutError` → `source="promise_gate_timeout"` properly, split the or-assertion at `test_promise_gate.py:533`.
- **Instrumentation (Task 5).** `perf_counter` elapsed around both gate entry points, written as `elapsed_ms` in the audit row (single-writer file, additive field; readers must already tolerate the 40 legacy no-`kind` rows). p50/p99 reported offline from the JSONL — no new metric pipeline.
- **Docs (Task 7).** `docs/features/promise-gate.md` rewritten to the obligation-keyed contract as current state (no "formerly"); `docs/features/message-drafter.md` drafter-path section updated; `#3016` test rewritten to assert `action` + non-empty `reason` only (never `class_` labels), PR carries `Closes #3016`.
- **Phase-4 measurement (Task 7).** A small `tools/`-side report (invoked manually, not a service): sample audit rows + session transcripts over the soak window, tabulate `ask_coverage` dispositions vs delivered text, flag contradictions (a `delivered` clause whose evidence artifact doesn't exist; a footer clause the PM also claimed in prose as done). Output is the recorded entry-criterion artifact for the phase-4 decision.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_write_promise_audit` swallow (`promise_gate.py:534`) — existing `test_promise_gate_audit.py::TestWritePromiseAuditFailureSwallowing` covers it; extend for the new `elapsed_ms` field (failure still silent).
- [ ] New async LLM call in `_evaluate_drafter_promise`: test that SDK exception AND timeout each fall through to the heuristic verdict with the correct audit `source` (closing the misrecorded-timeout bug).
- [ ] Coverage-bounce failure (steering push raises, or malformed `ask_coverage` survives validation): must degrade to plain delivery of the original message, never a dropped send — test asserts message still delivered and a `logger.warning` fires.

### Empty/Invalid Input Handling
- [ ] `ask_coverage: []`, absent, `None`, and clause with whitespace `item` — validation behavior pinned for each (empty list valid; whitespace item dropped with warning).
- [ ] `delivered` + empty/whitespace `evidence` → structured output invalid → regex fallback path (asserted via `SCHEMA_ROUTING_FALLBACK_EVENT`, and — closing spike-1 gap 3 — via the metric emission site).
- [ ] Empty `text` into `_evaluate_drafter_promise` unchanged (existing empty-input short-circuit tests keep passing).

### Error State Rendering
- [ ] Self-draft steering advisory on an LLM-layer block renders the revise-or-record instruction (extend `test_promise_advisory.py::TestDrafterCarriesAdvisory` to the LLM-verdict case).
- [ ] Coverage advisory renders each non-delivered clause and its disposition verbatim and clears the heuristic itself.

## Test Impact

From spike-1's inventory. Dispositions: UPDATE / DELETE / REPLACE.

- [ ] `tests/unit/test_pm_progress_updates.py::TestPromiseGateFallbackAllowsTaughtPhrasings` — **REPLACE**: the prompt-to-gate phrasing lock dies with the phrasebook. Replacement asserts the new prompt teaches no phrasing workaround (no "stay on the allowed side" content), retains the present-fact norm, and names the recorded-obligation discriminator.
- [ ] `tests/unit/test_pm_progress_updates.py::TestPMRoleGuidance::test_names_the_actual_discriminator` — **UPDATE**: discriminator anchor changes from forward-looking-clause to recorded-obligation.
- [ ] `tests/unit/test_pm_progress_updates.py::TestPMRoleGuidance` (remaining anchors) + `TestWorkPatternsScopeClarification` — **UPDATE**: re-anchor to the rewritten section; the ethos/present-fact assertions must keep passing by design.
- [ ] `tests/fixtures/persona/eng_worker_repo_baseline.txt` — **UPDATE**: regenerate; it quotes `prime-pm-role.md` verbatim at lines 141/673/1043.
- [ ] `tests/integration/test_promise_gate_real_api.py::test_forward_deferral_blocks_real_api` — **REPLACE**: assert `action == "block"` + non-empty `reason`; drop the `class_ == "forward_deferral"` label assertion (#3016). Add a third case: same deferral text via the drafter path with an open inbound expectation on a bound Job → allow.
- [ ] `tests/unit/test_promise_gate.py::TestSDKTimeout::test_timeout_falls_through_to_heuristic_with_timeout_source` — **UPDATE**: split the `heuristic or timeout` or-assertion; timeout now audits `promise_gate_timeout` deterministically.
- [ ] `tests/unit/test_message_drafter.py::TestShortOutputPromiseGate` (4 tests) — **UPDATE**: short-path tests now also assert zero LLM calls (closing spike-1 gap 1); full-path test updates for the async chokepoint signature.
- [ ] `tests/unit/test_message_drafter.py::TestDraftMessage` (14 tests) — **UPDATE**: pin `use_llm=False` on every case. This class exercises the main path unmocked, so once `draft_message` defaults to `use_llm=True` each case makes a live Haiku call and flakes on any input the regex cannot match. It asserts composition and attachment, never gate judgment, so the pin loses no coverage; real-API gate coverage stays in `tests/integration/test_promise_gate_real_api.py`.
- [ ] `tests/unit/test_message_drafter.py::TestQuestionFabricationPrevention` (3 tests) and `::TestExpectationsRecallParity` (5 tests) — **UPDATE**: pin `use_llm=False`, same reason as `TestDraftMessage`. Both classes call `draft_message` unmocked on inputs at or above 200 chars, so the new default routes them onto the live LLM path; they assert open-question extraction, never gate judgment.
- [ ] `tests/unit/test_open_question_gate.py::TestWorkflowAnnouncementExtraction` (2 tests) and `::TestSummarizeResponseOpenQuestions` (4 tests) — **UPDATE**: pin `use_llm=False`. Same class of exposure across a file the original inventory scoped as unaffected, which is the gap that let the live calls through: the inventory was built per-file, but the exposure is per-call-site — any unmocked `draft_message` call above the threshold, in any file.
- [ ] `tests/unit/test_promise_advisory.py::TestPromiseOverride` (3 tests) — **UPDATE**: exercise the override against LLM-layer BLOCK verdicts too (currently heuristic-only, the sole override coverage). Its live-API call is deliberate and stays: it is the R1 discriminator criterion's direct evidence, so it is annotated at the call site rather than pinned or mocked.
- [ ] `tests/unit/test_promise_gate_audit.py::TestDrafterPathAudit` — **UPDATE**: main-path rows carry `source="promise_gate_drafter_llm"` and `elapsed_ms`; short-path rows unchanged.
- [ ] `tests/unit/session_runner/test_router_classification.py::TestBlockedReasonSchemaField` — **UPDATE**: extend the schema structural assertions to `ask_coverage` (optional in phase A, shape pinned).
- [ ] `tests/unit/session_runner/test_schema_routing.py` — **UPDATE**: new cases for `ask_coverage` validation (valid, delivered-without-evidence → fallback, `[]` on continue) and the coverage bounce: non-delivered clauses ⇒ no `send_cb` dispatch + advisory steering pushed; revised turn ⇒ dispatch proceeds; second bounce for the same turn never fires.
- [ ] `tests/unit/output_handler/test_output_handler_drafter.py` — **UPDATE**: async `_evaluate_drafter_promise` in the mocked paths; no behavioral change to steering/bounce tests.
- [ ] `tests/unit/valor_telegram/conftest.py::_bypass_promise_gate` — **UPDATE** only if the patched symbol moves; the sync `cli_check_or_exit` surface is unchanged, so likely no-op — verify.
- [ ] Unaffected (verified by inventory, listed to bound the sweep): `test_drafter_validators.py` (every `draft_message` call asserted under the short-output threshold in-test), `test_message_drafter_linkify.py`, `test_checkin_primitive.py`, `test_deferred_self_draft_completed.py`, `test_promise_gate_session_events.py`, `test_resume_reverification*.py` (rails file untouched), `test_schema_routing_alert.py`.

**This inventory bounds the sweep; it does not close it.** The rows above are per-file, but the exposure is per-call-site, so the closing criterion is a grep, not this list: `grep -rn "draft_message(" tests/unit/ | grep -v "use_llm=False"` must leave only mocked calls, docstring mentions, calls asserted under the short-output threshold, and the one deliberate live-API test. Confirm with a `--durations` run showing no test above ~0.2s except that one.

## Rabbit Holes

- **Semantically verifying the bounce revision.** Checking whether the PM's revised prose "really" states each disposition is keyword matching against LLM output — the exact losing arms race R1 ends. One bounce, deliver the revision, let the phase-4 measurement judge quality in aggregate; move on.
- **Promoting `RTR_SDK_TIMEOUT` into `TimeoutSettings`.** Tempting config hygiene; the do-not-redefine comment and the promote-vs-name-locally criterion make it a standalone decision. Import it as the gate already does.
- **Broadening `_FORWARD_DEFERRAL_PATTERNS`.** Explicitly dropped in the issue. The heuristic stays a narrow fail-closed backstop; the LLM's presence on the main path removes the temptation's justification.
- **Building `evaluate_delivery` "while we're in there."** Phase 4 has an entry criterion for a reason; the measurement may show phases 1–3 suffice. Resist.
- **Rewriting the drafter's composition logic.** `draft_message` is 1200 lines of deterministic composition; this plan touches only its gate chokepoint and nothing else.
- **A metrics pipeline for latency.** `elapsed_ms` in the existing audit JSONL answers the AC. A Prometheus-shaped detour is not in the appetite.

## Risks

### Risk 1: Main-path LLM call adds latency to every composed send, for all session types
**Impact:** ~500ms p50 (bounded at 3s by the SDK timeout) added to `draft_message`'s main path. There are exactly three non-test callers: `agent/output_handler.py:754`, `bridge/email_bridge.py:925`, and `agent/hooks/stop.py:147`. The third is a hard blocker in its own right and is carved out separately below.
**Mitigation:** Short path (<200 chars) keeps zero-LLM guarantee, now test-enforced. The call is bounded by `RTR_SDK_TIMEOUT=3.0` at the client constructor and falls open to the heuristic on failure, so worst case equals today's behavior plus ≤3s. `elapsed_ms` instrumentation makes the real cost observable from day one; the AC names the budget (p50<500ms/p99<3s) and the measurement now exists to check it.

### Risk 1a (BLOCKER-derived): the Stop hook cannot absorb an inline LLM call — carve it out
**Impact:** `agent/hooks/stop.py:147` (`_generate_draft`) awaits `draft_message(output_tail, medium=medium)` **inline on the harness-invoked Stop hook's critical path**, with no `use_llm` control. The Stop hook has a hard **10-second harness wall**, and this repo has already had the incident: [`docs/features/memory-hook-performance.md`](../features/memory-hook-performance.md) records **126 of 131 runs** timing out when an inline Haiku round-trip was added to this exact path, with the harness SIGKILLing the process mid-call — no graceful degradation, no log. The documented fix was "**detach, don't bound**," precisely because an SDK-level timeout "would only guarantee loss on every slow turn." Routing this caller onto the new main-path LLM gate would reproduce that incident on every stop-hook invocation whose output is long enough, SDLC-shaped, or artifact-bearing enough to take the main path.
**Mitigation (mandatory, not optional):** `draft_message` gains an explicit caller-controlled `use_llm` parameter, and `agent/hooks/stop.py:147` passes `use_llm=False`. The Stop hook keeps today's zero-LLM heuristic behavior verbatim — it is a *review-gate draft presented back to the agent*, not a delivered message, so the honesty gate has no delivery to guard there anyway. A regression test asserts the stop-hook path issues no LLM call. **Do not** attempt to bound this with a timeout instead; the cited incident is exactly that mistake.

### Risk 1b: Semaphore contention makes the "≤3s worst case" claim false
**Impact:** `RTR_SDK_TIMEOUT=3.0` bounds only the `AsyncAnthropic` call. The gate wraps it in `semaphore_slot()`, a process-wide `asyncio.Semaphore` (default 5) shared with RTR, router classification, and memory extraction, acquired via a bare `await _semaphore.acquire()` with **no timeout** (`agent/anthropic_client.py:106-110`). Once every `draft_message` main-path call also draws on that pool, queue wait is unbounded and additive on top of the 3s budget — so Risk 1's "worst case equals today plus ≤3s" does not hold under load.
**Mitigation:** Bound the acquisition — `asyncio.wait_for(_semaphore.acquire(), timeout=RTR_SDK_TIMEOUT)` (or a bounded-wait guard beside `semaphore_slot()`), catching `asyncio.TimeoutError` and falling through to the heuristic exactly as `APITimeoutError` does. Fail-open-on-infrastructure posture unchanged. Record `queue_wait_ms` beside `elapsed_ms` so a p99 breach can be attributed to contention rather than API latency; without that split the measurement is undiagnosable.

### Risk 7: The terminal-flush route keeps the regex-only gate
**Impact:** `_gate_terminal_promise` (`agent/session_health.py:2536-2575`) is docstring-labeled `(#2423)` and calls `_evaluate_promise_heuristic`. #2423 is the very issue about a delivery route skipping an honesty check, and this plan claims credit for closing that pattern — yet after it ships, a Symptom-2-shaped over-claim phrased to dodge the regex still ships through session-end deferred-draft delivery.
**Mitigation:** Out of scope here, and named rather than left silent (the omission was the finding). Wiring the async gate in is not a drop-in: `flush_deferred_self_draft_sync` (`agent/session_health.py:2595`) is synchronous today, so this is a real design question. There is also a genuine argument that it is acceptable — at terminal flush the session is already ending, so there is no PM turn left to revise and the advisory bounce has nothing to bounce to. Tracked in the same followup issue as the No-Go 5 residual, not silently dropped.

### Risk 6: Job-scoped override launders unrelated promises (accepted, pre-existing)
**Impact:** `promise_override_active` (`bridge/promise_gate.py:402-422`) is Job-scoped by deliberate owner ruling: **any** open inbound expectation on the bound Job clears the gate for **every** outbound on that Job until discharge. So a PM holding one innocuous recorded obligation can ship an unrelated forward-looking claim, and it passes as `promise_recorded_override`.
**Mitigation:** None in this plan, and that is a decision rather than an oversight. Open Question 2 defaults to per-Job, the code carries an explicit owner-ruling warning against tightening to per-message matching without a fresh ruling, and per-message matching is the nag machine that warning exists to prevent. Named here so the boundary is visible: this plan's R1 success criterion proves the override *passes when an obligation is recorded*, **not** that it refuses to launder an unrelated one. Revisit only with soak evidence, per the issue's own instruction. Task 6 adds a test that *documents* the laundering case as accepted current behavior rather than leaving it uncharacterized.

### Risk 2: `ask_coverage` self-attestation becomes a rubber stamp
**Impact:** The PM fills in its own coverage — including the clause decomposition, performed by the same model that dropped the clause. A rubber stamp would make both symptoms *look* solved.
**Mitigation:** This is exactly Open Question 3 of the issue, resolved as: ship, then measure. The phase-4 measurement (Task 7) is a committed deliverable that samples dispositions against delivered text and ground truth; its report is the recorded entry criterion for the deeper gate-side axis.

### Risk 3: Required-field tightening spikes schema-routing fallback
**Impact:** A PM omitting a required `ask_coverage` would fall back to regex classification, degrading routing.
**Mitigation:** Two-phase rollout — optional in this plan; tightening is a one-line follow-up gated on the existing 5%/1h fallback alert staying quiet over the soak window. Validation failures in phase A are observable (`SCHEMA_ROUTING_FALLBACK_EVENT` + metric, now tested at the emission site).

### Risk 4: Prompt rewrite regresses honest progress reporting
**Impact:** Deleting the phrasebook without preserving its honest core would teach silence — Symptom 1's failure mode, made worse.
**Mitigation:** The present-fact norm is an explicit retained rule with its own prompt-content test in the replacement suite (Task 3), and the critique round is directed at the replacement text specifically.

### Risk 5: Async refactor of the sync gate API breaks CLI consumers
**Impact:** `tools/send_message.py`, `tools/valor_telegram.py`, `tools/valor_email.py`, `agent/session_health.py` call the sync `evaluate_promise`/`cli_check_or_exit` from non-async contexts.
**Mitigation:** The sync API's signature and behavior are frozen; it becomes a thin wrapper over the extracted async internals via the existing `_run_async_safely`. The full existing `test_promise_gate.py` suite (which exercises the sync surface) is the regression net; `TestCliCheckOrExit` runs unchanged.

## Race Conditions

### Race 1: Delivery task reads session state after the runner has moved on
**Location:** `agent/session_runner/adapter.py:785-797` (`loop.create_task(send_cb(...))`) vs `agent/session_runner/runner.py:796` (next turn begins).
**Trigger:** Fire-and-forget delivery means `output_handler.send` (and the drafter's gate call) can run while the runner is already composing turn N+1 on the same in-memory `AgentSession`.
**Data prerequisite:** Everything the gate reads off `session` must be stable at task-creation time or immutable-for-the-turn.
**State prerequisite:** In phases 1–3 the gate reads only `session_id`, Job binding, and `extra_context` — none mutated per-turn; the footer is composed *before* task creation from the validated payload, so no shared-state read at delivery time. Phase 4's session-stamped ask WOULD be exposed to overwrite by turn N+1.
**Mitigation:** Phases 1–3: none needed (documented invariant — new gate inputs must be bound into the coroutine's arguments at creation, not read from `session` at execution). Phase 4 (when/if built): pass the ask as a bound argument through the payload path or copy-on-create, never a live `session` attribute read; this constraint is recorded here so the phase-4 design inherits it.

### Race 2: Coverage bounce vs steering preempt and the promise-gate bounce
**Location:** `agent/session_runner/runner.py:1223-1240` (`_preempt_watcher`), `_route_turn`, and the self-draft steering channel shared with the drafter's promise bounce.
**Trigger:** (a) A steer arriving mid-turn preempts routing — a preempted turn's `ask_coverage` must not leave a pending coverage advisory behind. (b) A single turn could qualify for BOTH a coverage bounce (runner layer) and a promise-gate self-draft (drafter layer), risking ping-pong revisions.
**Data prerequisite:** The advisory must derive only from the payload actually being routed; the once-per-turn bounce flag must be turn-scoped, not session-global.
**State prerequisite:** Coverage bounce fires before dispatch (runner layer); promise bounce fires at delivery (drafter layer) — the coverage-revised message still passes through the promise gate, so ordering is coverage-then-promise, never interleaved.
**Mitigation:** Advisory composition co-located with the routing handoff (a preempted payload composes nothing); one coverage bounce per turn enforced by a turn-scoped flag; the shared `self_draft_attempts` bound terminates any residual loop. **A concurrent human steer and the coverage advisory cannot clobber each other**: `push_steering_message` (`agent/steering.py:176`) is a Redis list push, so the steering inbox is FIFO and both entries coexist and are drained in order at the turn boundary — this, not just turn-scoping, is why case (a) is safe. Test: a turn that triggers both bounces converges within the existing attempts bound.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3035] **Phase 4 (two-axis `evaluate_delivery`) and phase-B schema tightening (`ask_coverage` → required).** Both decisions are gated on the post-soak measurement and filed as investigation issue #3035, which its title defers to no earlier than 2026-09-10 (owner ruling 2026-08-27: recent update churn means no reliable stability for a sooner soak). The measurement tooling, the recorded entry criterion, and the phase-4 design constraints (spike-2 ask anchor, Race 1 bind-at-creation rule) all ship in THIS plan so #3035 starts unblocked. **The phase-B followup also owns deleting the phase-A/phase-B narration** in the `PM_TURN_JSON_SCHEMA` comment (`agent/session_runner/router.py:61`) once `ask_coverage` becomes required — leaving "formerly optional" text behind would be exactly the historical artifact CLAUDE.md principle 1 forbids.
- [SEPARATE-SLUG #3016] The nightly-regression issue itself is *resolved* by this plan's test rewrite (implementation PR carries `Closes #3016`); listed here only to record that no further work on it exists outside this plan.
- **No-Go 5 — no *LLM* gate on the poll question path, but it does not stay ungated.** #3080 landed `TelegramRelayOutputHandler.send_poll` (`agent/output_handler.py:1523`), a human-facing outbound route that reaches Telegram without passing through `draft_message`. Owner Ruling 4 is stated in transport-level terms ("honesty is transport-level"), so narrowing it to a literal `draft_message`-callers match because a new transport appeared afterwards would be scope-narrowing dressed as scope fidelity — especially in a plan that claims credit for closing the #2423 bypass class.
  **What is out of scope:** adding an LLM round-trip to `send_poll`. A poll question is an interactive prompt; a ~500ms round-trip there is real cost against a low-volume affordance.
  **What is therefore IN scope (Task 5):** run the existing zero-cost `_evaluate_promise_heuristic` over poll question text inside `validate_poll_question`. `validate_telegram_poll` currently checks exactly two things — non-empty and `len <= POLL_QUESTION_MAX_CHARS` — and does no honesty checking at all. The heuristic is already the sanctioned backstop on the short drafter path for exactly this cost reason, so reusing it here contradicts nothing and costs no latency. This closes the concrete gap: a poll question *can* carry a completion claim ("Merge is done — proceed to stage?"), authored by the same PM model that Symptom 2 already showed learning to route around a gate by rephrasing — and `/ask-me` is a route it now knows is unguarded. Violations are surfaced the way `send_poll` already surfaces validation failures (logged, non-blocking), preserving the route's fail-open posture.
  **Residual risk:** the heuristic is a narrow backstop, not the LLM's judgment, so a cleverly-phrased claim inside a poll question still ships. That residual is tracked in its own issue (filed at build time, referenced from `docs/features/promise-gate.md`), **not** folded into #3035 — #3035 is the phase-4/schema-tightening investigation and this plan will be archived out of `docs/plans/` once it lands, which would leave the risk with no durable home.

Anti-criteria for the code-level No-Gos are in `## Verification` (no `evaluate_delivery` symbol ships; `ask_coverage` not in `required` in phase A; `send_poll` gains no *LLM* call).

## Update System

No update-script changes required. Prompt files (`.claude/commands/roles/prime-pm-role.md`) and all code propagate via normal `git pull` in `/update`; no new dependencies, env keys, or config files; no Popoto model changes, so no `scripts/update/migrations.py` entry. Operational note for deploy: the worker must be restarted (`./scripts/valor-service.sh restart`) so running PM sessions pick up the new prompt and schema — standard post-merge step, already covered by the restart rule in CLAUDE.md.

## Agent Integration

No new CLI entry points or MCP surfaces. The change is internal to the existing delivery pipeline (runner → output handler → drafter → gate), which the agent already traverses on every outbound message. CLI senders (`tools/send_message.py`, `tools/valor_telegram.py`, `tools/valor_email.py`) keep the unchanged sync `cli_check_or_exit` surface. Integration coverage: the real-API gate test (updated) plus the end-to-end drafter-path tests in `tests/integration/test_message_drafter_integration.py` (unchanged) verify the agent-reachable path.

## Documentation

### Feature Documentation
- [ ] Rewrite `docs/features/promise-gate.md` — obligation-keyed two-layer contract as current state: discriminator (recorded obligation, not grammar), path coverage table (short/main/terminal-flush/CLI), audit vocabulary incl. `promise_gate_drafter_llm` + `elapsed_ms`, retained rules (heuristic-as-backstop, fail postures, kill switch, advisory stance). No "formerly" narration.
- [ ] Update `docs/features/message-drafter.md` — main-path LLM judgment, short-path zero-LLM guarantee (now test-enforced), single-chokepoint invariant.
- [ ] Update `docs/features/eng-session-architecture.md` (or the PM-turn schema's home doc) — `ask_coverage` field, disposition footer, two-phase rollout state.
- [ ] Verify `docs/features/README.md` index rows for the touched docs still describe them accurately.

### Inline Documentation
- [ ] Module docstring of `bridge/promise_gate.py` updated (async API, discriminator, timeout-source fix); `_evaluate_drafter_promise` docstring rewritten for `use_llm`.
- [ ] `PM_TURN_JSON_SCHEMA` comment block documents `ask_coverage` semantics and the phase-A/phase-B state.

## Success Criteria

- [ ] `PM_TURN_JSON_SCHEMA` carries `ask_coverage`; `delivered` with empty `evidence` is rejected into the fallback path; `route:"continue"` accepts `[]`.
- [ ] A two-clause ask answered on one clause triggers exactly one coverage bounce whose advisory names the dropped clause and its disposition; the revised turn is what ships (asserted end-to-end with a scripted driver whose revision states the disposition, proving the pipeline carries it to delivered text).
- [ ] Incident A text ("Say the word and I'll…") blocks on the drafter main path; identical text with an open inbound expectation on the bound Job passes as `promise_recorded_override` — the R1 discriminator asserted directly.
- [ ] Short path makes zero LLM calls (test-enforced); `elapsed_ms` lands on every audit row; measured p50/p99 reported in the PR against the budget — LLM path p50 ≤ 2500ms / p99 ≤ 5000ms (owner ruling 2026-09-03: the measured p50 is API latency, not contention, and is accepted), short path unchanged.
- [ ] Phrasebook deleted; replacement teaches no phrasing workaround and retains the present-fact norm (both prompt-content-tested).
- [ ] `promise_gate_timeout` audit source is reachable and deterministic (dead-code fix verified by the split test).
- [ ] #3016 test asserts `action`, never `class_` labels; PR carries `Closes #3016`.
- [ ] Phase-4 measurement tool exists, documented, with the entry criterion recorded in the feature doc.
- [ ] Tests pass (`/do-test`); docs updated (`/do-docs`). **Build-gated only.**
- [ ] *(Not build-gated — recorded onto #3035.)* `SCHEMA_ROUTING_FALLBACK_METRIC` flat after merge, watched by the existing 5%/1h alert. This needs days of post-merge traffic, so Task 9's final validator must NOT be asked to verify it: blocking on it stalls the lane and waving it through corrupts the gate. #3035 already owns the post-soak measurement over the same window.

## Team Orchestration

### Team Members

- **Builder (schema-coverage)** — Name: `schema-builder` — Role: Task 1 (schema field, validation, footer) — Agent Type: builder — Resume: true
- **Builder (prompt-rewrite)** — Name: `prompt-builder` — Role: Task 3 (PM prompt + prompt-content tests) — Agent Type: builder — Resume: true
- **Builder (drafter-gate)** — Name: `gate-builder` — Role: Task 5 (async gate API, drafter LLM path, instrumentation, timeout fix) — Domain: async/concurrency — Agent Type: builder — Resume: true
- **Builder (docs-measurement)** — Name: `docs-builder` — Role: Task 7 (feature docs, #3016 rewrite, measurement tool) — Agent Type: builder — Resume: true
- **Validator (per-component)** — Name: `component-validator` — Role: verify each build task against its Validates list — Agent Type: validator — Resume: true
- **Documentarian** — Name: `feature-documentarian` — Role: Task 8 docs cascade — Agent Type: documentarian — Resume: true
- **Final validator** — Name: `final-validator` — Role: run the Verification table end to end — Agent Type: validator — Resume: true

## Step by Step Tasks

### 1. Schema field + validation + disposition footer
- **Task ID**: build-schema-coverage
- **Depends On**: none
- **Validates**: tests/unit/session_runner/test_router_classification.py, tests/unit/session_runner/test_schema_routing.py
- **Informed By**: spike-1 (gaps 2 and 3), spike-2 (footer composed at runner layer, before task creation)
- **Assigned To**: schema-builder
- **Agent Type**: builder
- **Parallel**: true
- Add optional `ask_coverage` to `PM_TURN_JSON_SCHEMA` (`agent/session_runner/router.py:61`) with the `{item, disposition, evidence}` shape; extend the comment block.
- Extend `validate_structured_route`: `delivered` requires non-empty `evidence` (violation ⇒ invalid structured output ⇒ existing regex fallback); whitespace `item` dropped with warning; `[]`/absent valid.
- Implement the coverage bounce at the `_route_turn`/`on_user_payload` handoff: non-`delivered` clauses ⇒ withhold dispatch, push the coverage advisory as self-draft steering (turn-scoped once-only flag; shared `self_draft_attempts` backstop) — Race 1/2 constraints.
- **The coverage bounce must increment the SHARED budget.** Call `bump_self_draft_attempts(session_id)` (`agent/steering.py`) before pushing the advisory, and honor `SELF_DRAFT_MAX_ATTEMPTS`. Today that counter is bumped from exactly one site (`agent/output_handler.py:1283`), and `runner.py` imports only `push_steering_message`/`pop_all_steering_messages` — so a bare `push_steering_message` here would make Race 2's "shared backstop" claim false, leaving the turn-scoped flag (which resets every turn) as the only guard against a multi-turn ping-pong.
- New tests per Test Impact rows for these two files, including a metric-emission-site assertion (`agent/session_runner/runner.py` (the `record_metric(SCHEMA_ROUTING_FALLBACK_METRIC, ...)` call site — anchor on the symbol, not the line)), advisory-clears-heuristic, the dual-bounce convergence case, and an assertion that a coverage bounce increments `self_draft_attempts` — not only that a promise-gate bounce does.
- **Delivered-text assertion (satisfies the issue AC that forbids schema-object-only proof).** Add a test that drives a two-clause-ask turn through the coverage bounce with a scripted driver and asserts the string actually handed to `send_cb`/`draft_message` contains the dropped clause's disposition. Asserting only that dispatch was withheld and later resumed does **not** satisfy this AC.

### 2. Validate schema coverage
- **Task ID**: validate-schema-coverage
- **Depends On**: build-schema-coverage
- **Assigned To**: component-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the two test files; verify the coverage bounce withholds dispatch and pushes the advisory, the revised turn dispatches, and no second bounce fires; verify fallback behavior on evidence-less `delivered`.

### 3. PM prompt rewrite
- **Task ID**: build-prompt-rewrite
- **Depends On**: none
- **Validates**: tests/unit/test_pm_progress_updates.py, tests/fixtures/persona/eng_worker_repo_baseline.txt
- **Informed By**: spike-1 (prompt-to-gate lock test; persona golden quotes the file)
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- **The literal sentence `say only what is already true` must survive into the replacement text**, not merely the norm "in spirit". The Verification table pins that exact string, so task and check now agree; do not leave a grep pinning a phrase the task only promises loosely.
- Delete the `prime-pm-role.md` phrasebook — **anchor on text, not line numbers** (currently `:50-70`, and it has already drifted once): the block running from `**Say it in facts that are already true.**` through the `expectation-add` sentence, inclusive of the measured verdict table and the `Two ways to stay on the allowed side` list. Write the replacement: present-fact norm (kept verbatim in spirit), recorded-obligation discriminator, dispatch-you-can-execute rule, `ask_coverage` authoring guidance, `expectation-add` as the one way to commit.
- **Retain the two bullets #3080 added below the phrasebook** — the `/ask-me` routing bullet and the `[missing-capability]` escalation line. They are adjacent to the deletion, not part of it, and the persona golden regeneration will silently absorb their loss if they are clipped.
- REPLACE `TestPromiseGateFallbackAllowsTaughtPhrasings` and UPDATE the `TestPMRoleGuidance` anchors per Test Impact. **The replacement must keep the mechanical lock, not degrade to prose checks.** The existing test feeds the literal phrasings the role doc teaches into `_evaluate_promise_heuristic` and asserts the verdict — that is what catches a future `_FORWARD_DEFERRAL_PATTERNS` tightening silently starting to block a taught sentence. Text-absence greps on the prompt file do not. Enumerate whatever illustrative phrasings survive the rewrite (mirroring the old `TAUGHT_ALLOWED`) and assert each against `_evaluate_promise_heuristic`.
- **Do NOT regenerate `tests/fixtures/persona/eng_worker_repo_baseline.txt` — the plan's earlier claim that it quotes `prime-pm-role.md` was false.** Verified: the fixture contains zero matches for the phrasebook text, and it is produced by `scripts/capture_persona_baseline.py` via `agent.sdk_client.compose_system_prompt`, which never reads either role file. The fixture itself says so: "persona lives in `.claude/commands/roles/prime-pm-role.md`... This file is NOT injected into session-runner sessions." The load-bearing check is the grep sweep below, not a regeneration.
- Grep-confirm no fixture anywhere quotes the deleted lines (currently clean repo-wide).
- **Regenerate the OpenCode mirror.** `.opencode/commands/prime-pm-role.md` is a generated copy of the exact file being edited, and the sync is **manual** — no pre-commit hook invokes it. Run `python scripts/sync_claude_to_opencode.py` **after the last prompt edit lands**, then assert `git diff --exit-code .opencode/` is clean. Skipping this leaves OpenCode sessions running the deleted phrasebook, reintroducing the Symptom-2 content on a second surface.

### 4. Validate prompt rewrite
- **Task ID**: validate-prompt
- **Depends On**: build-prompt-rewrite
- **Assigned To**: component-validator
- **Agent Type**: validator
- **Parallel**: false
- Run prompt-content tests; grep-verify no phrasing-workaround language survives; verify present-fact norm text present.

### 5. Async gate API + drafter main-path LLM + instrumentation
- **Task ID**: build-drafter-gate
- **Depends On**: none
- **Validates**: tests/unit/test_promise_gate.py, tests/unit/test_promise_advisory.py, tests/unit/test_promise_gate_audit.py, tests/unit/test_message_drafter.py::TestShortOutputPromiseGate, tests/unit/output_handler/test_output_handler_drafter.py
- **Informed By**: spike-3 (async plumbing free; AsyncAnthropic-constructor timeout #1055; semaphore_slot; dead timeout code; legacy audit rows)
- **Assigned To**: gate-builder
- **Agent Type**: builder — Domain: async/concurrency (paste DOMAIN_FRAMING.md async rules into assignment)
- **Parallel**: true
- Extract `evaluate_promise_async` (public) from `_evaluate_promise_async` internals; sync `evaluate_promise` wraps it via `_run_async_safely` — signature and behavior frozen.
- Make `_evaluate_drafter_promise` async with `use_llm`; await at both call sites (`message_drafter.py:1160` short/`use_llm=False`, `:1211` main/`use_llm=True` — line numbers current at baseline `00a3d93ca`; both sit inside `draft_message`); preserve single-chokepoint, kill-switch, and override ordering.
- **Add the `use_llm` parameter to `draft_message` itself, and pass `use_llm=False` at `agent/hooks/stop.py:147`.** Mandatory, per Risk 1a — the Stop hook's 10s harness wall plus the documented 126/131 SIGKILL incident make an inline LLM call there a repeat of a known production failure. Add a regression test asserting the stop-hook path issues no LLM call. The other two callers (`agent/output_handler.py:754`, `bridge/email_bridge.py:925`) take the main path normally.
- **Add the heuristic (not the LLM) to the poll question path**, per the revised No-Go 5: `validate_poll_question` runs `_evaluate_promise_heuristic` over the question text and surfaces a violation the same non-blocking way `send_poll` already surfaces validation failures. No LLM call, no latency cost, fail-open preserved. Test: a poll question carrying a completion claim produces a violation; an ordinary interrogative does not.
- Audit: main-path LLM verdicts → `source="promise_gate_drafter_llm"`; add `elapsed_ms` to all rows from both entry points.
- Fix the timeout dead code: delete `_PromiseTimeoutError`, catch `APITimeoutError` → heuristic fallback audited as `promise_gate_timeout`; split `test_promise_gate.py:533`. **Land this as its own commit BEFORE the async/LLM commits.** It is an orthogonal bugfix sharing the same `except` block; committing it separately keeps a later regression bisectable between "the new LLM call" and "the timeout-source fix". Task 6 runs its regression test standalone before the five-file suite for the same reason.
- New tests: short-path zero-LLM (spike-1 gap 1); Incident A text blocks on main path; same text + open inbound expectation → `promise_recorded_override`; LLM exception and timeout fall-throughs.

### 6. Validate drafter gate
- **Task ID**: validate-drafter-gate
- **Depends On**: build-drafter-gate
- **Assigned To**: component-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the five Validates files; confirm sync CLI surface untouched (`TestCliCheckOrExit` green, `tools/send_message.py` help unchanged); confirm audit rows carry `elapsed_ms`.
- **Run `tests/unit/test_promise_gate.py::TestSDKTimeout::test_timeout_falls_through_to_heuristic_with_timeout_source` standalone FIRST**, before the five-file suite, so the timeout-source fix and the new LLM call stay independently diagnosable.
- Confirm the stop-hook carve-out: `agent/hooks/stop.py` passes `use_llm=False` and its path issues no LLM call (Risk 1a).
- Add the **accepted-laundering characterization test** (Risk 6): an open inbound expectation on the bound Job clears an *unrelated* forward-looking claim as `promise_recorded_override`. This test documents current owner-ruled behavior as accepted; it is not a bug report. Name it so in the docstring, so a future reader does not "fix" it without a fresh owner ruling.

### 7. Docs contract rewrite, #3016 reconciliation, phase-4 measurement tool
- **Task ID**: build-docs-measurement
- **Depends On**: build-schema-coverage, build-drafter-gate
- **Validates**: tests/integration/test_promise_gate_real_api.py (create/replace rows per Test Impact)
- **Informed By**: spike-3 (audit readers must tolerate 40 legacy no-`kind` rows)
- **Assigned To**: docs-builder
- **Agent Type**: builder
- **Parallel**: false
- REPLACE the real-API deferral test per Test Impact (#3016; action-only assertions); add the override-path real-API case.
- Build the measurement report tool (audit-JSONL + schema-object sampler, contradiction flags, tolerant of legacy rows); document its invocation and the phase-4 entry criterion.
- Rewrite `docs/features/promise-gate.md` and update `docs/features/message-drafter.md` per Documentation section.

### 8. Documentation cascade
- **Task ID**: document-feature
- **Depends On**: build-docs-measurement, validate-schema-coverage, validate-prompt, validate-drafter-gate
- **Assigned To**: feature-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Remaining Documentation checkboxes (schema home doc, README index rows, inline docstrings sweep); no-"formerly" audit across every touched doc.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Execute the full Verification table; verify all Success Criteria.
- **Latency report must characterize general outbound traffic, not one caller class.** Group `elapsed_ms` percentiles by audit `source` **and by calling surface**, and report each separately against the budget (LLM path p50 ≤ 2500ms / p99 ≤ 5000ms per the 2026-09-03 owner ruling; the zero-LLM short path stays at p50 ≈ 0ms). A single blended number drawn from test-run rows is dominated by PM-turn-shaped drafter scenarios and does not satisfy the issue AC ("measured on general outbound traffic, not only PM turns"). Where a caller class (notably `bridge/email_bridge.py`) has no meaningful test-run sample, say so explicitly and schedule the sample from the post-merge audit JSONL rather than reporting a number that does not cover it.

## Verification

Run every row from the lane worktree (`.worktrees/promise-gate-recorded-obligations`), which has its own `.venv`. `scripts/pytest-clean.sh` correctly refuses to run against an off-pin or missing venv, so a review worktree without one produces a FAIL that is about the venv, not the code.

| Check | Command | Expected |
|-------|---------|----------|
| Unit suites green | `scripts/pytest-clean.sh tests/unit/test_promise_gate.py tests/unit/test_promise_advisory.py tests/unit/test_promise_gate_audit.py tests/unit/test_message_drafter.py tests/unit/test_pm_progress_updates.py tests/unit/session_runner/ tests/unit/output_handler/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Schema carries ask_coverage | `grep -c "ask_coverage" agent/session_runner/router.py` | output > 0 |
| Phase A: present but not required | `python -c "from agent.session_runner.router import PM_TURN_JSON_SCHEMA as s; print(int('ask_coverage' in s['properties']), int('ask_coverage' in s['required']))"` | `1 0` |
| Phrasebook gone | `grep -ci "two ways to stay on the allowed side" .claude/commands/roles/prime-pm-role.md \|\| true` | `0` |
| Present-fact norm retained | `grep -c "say only what is already true" .claude/commands/roles/prime-pm-role.md` | output > 0 |
| Short path zero-LLM test exists | `grep -cE "zero_llm\|no_llm_call" tests/unit/test_message_drafter.py` | output > 0 |
| No formerly-narration in gate doc | `grep -ci "formerly" docs/features/promise-gate.md \|\| true` | `0` |
| No evaluate_delivery ships (anti-criterion, No-Go 1) | `grep -rl "def evaluate_delivery" --include='*.py' . \| wc -l \| tr -d ' '` | `0` |
| No LLM gate on the poll route (anti-criterion, No-Go 5) | `grep -c "evaluate_promise_async" agent/output_handler.py \|\| true` | `0` |
| Timeout source reachable | `scripts/pytest-clean.sh tests/unit/test_promise_gate.py -k timeout_falls_through -q` | exit code 0 |
| Stop hook takes no LLM call (Risk 1a) | `grep -c "use_llm=False" agent/hooks/stop.py` | output > 0 |
| Poll question runs the heuristic (No-Go 5) | `sed -n '/^def validate_poll_question/,/^def /p' bridge/message_drafter.py \| grep -c _evaluate_promise_heuristic` | output > 0 (a bare file-wide grep returns 3 today from the unrelated short path, so it must be anchored inside the function) |
| OpenCode mirror regenerated | `python scripts/sync_claude_to_opencode.py && git diff --exit-code .opencode/` | exit code 0 |
| Dead timeout class removed | `grep -c "_PromiseTimeoutError" bridge/promise_gate.py` | match count == 0 |
| #3016 label assertion gone | `grep -c 'class_ == "forward_deferral"' tests/integration/test_promise_gate_real_api.py` | match count == 0 |
| Blocked phrasing no longer taught (retained rule) | `grep -c "Still working on this" .claude/commands/roles/prime-pm-role.md` | match count == 0 |
| Taught phrasings still mechanically locked to the gate | `scripts/pytest-clean.sh tests/unit/test_pm_progress_updates.py -q` | exit code 0 |

## Critique Results

Two independent critique rosters ran on 2026-09-03 and are merged here; every finding from both is addressed in the revision that follows.

**Roster A** (forked `/do-plan-critique`, full roster) returned READY TO BUILD (with concerns): 0 blockers, 7 concerns, 5 nits.
**Roster B** (three foreground critics on disjoint lenses: mechanics, scope/contract, tests) returned NEEDS REVISION: 1 blocker, 8 major, 2 minor.

The verdict of record is **NEEDS REVISION** — Roster B found a blocker Roster A missed (the Stop-hook inline LLM call), and Roster A found a route Roster B missed (terminal flush). Neither roster alone was sufficient.


| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | History & Consistency | The plan cites #2423 ("a delivery route skipping an honesty check") as the pattern it closes, but leaves the terminal-flush route — the very route #2423 was filed against — on the regex-only heuristic, with no Risk entry and no No-Go. `_gate_terminal_promise` is docstring-labeled `(#2423)` and calls `_evaluate_promise_heuristic`. After this plan ships, a Symptom-2-shaped over-claim phrased to dodge the regex still ships through session-end deferred-draft delivery. The poll route got an explicit residual-risk paragraph (No-Go 5) for a structurally identical gap; terminal-flush gets only a row in a doc table. | Risk 7 | `_gate_terminal_promise` (`agent/session_health.py:2536-2575`) would need the same `use_llm`-style branch Task 5 adds to `_evaluate_drafter_promise`, awaiting `evaluate_promise_async`. But `flush_deferred_self_draft_sync` (`agent/session_health.py:2595`) is synchronous today, so wiring the async gate in is a real design question, not a drop-in — which is precisely why it belongs in Risks/No-Gos rather than being silently absent. Add a No-Go parallel to No-Go 5 naming the residual risk, and either file the LLM upgrade as a followup or argue why session-already-ending (no PM turn left to revise) makes it acceptable. |
| CONCERN | Risk & Robustness | Risk 1's mitigation claims "worst case equals today's behavior plus ≤3s", but `RTR_SDK_TIMEOUT=3.0` bounds only the `AsyncAnthropic` call itself. The gate wraps the call in `semaphore_slot()`, which acquires a process-wide `asyncio.Semaphore(anthropic_concurrency)` (default 5) shared by every Anthropic caller in the process (RTR, router classification, memory extraction). The acquire is a bare `await _semaphore.acquire()` with no timeout (`agent/anthropic_client.py:106-110`). Once every `draft_message` call also draws on this pool, queue wait is unbounded and additive on top of the 3s budget. | Risk 1b | Bound the acquisition: `asyncio.wait_for(_semaphore.acquire(), timeout=RTR_SDK_TIMEOUT)` (or a dedicated bounded-wait guard beside `semaphore_slot()` in `agent/anthropic_client.py`), catching `asyncio.TimeoutError` to fall through to the heuristic exactly as `APITimeoutError` does — fail-open on infrastructure, posture unchanged. Record `queue_wait_ms` alongside the planned `elapsed_ms` so the measurement can separate contention from API latency; otherwise a p99 breach is undiagnosable. |
| CONCERN | Structural | Verification row "Phrasebook gone" greps lowercase `"two ways to stay on the allowed side"`, but the file text is capital-T `Two ways to stay on the allowed side:` (`prime-pm-role.md:65`). The row returns `0` against the **untouched** file, so it is green before any work is done — a check that can never fail and therefore verifies nothing. | Verification table | `grep -c` is case-sensitive. Either add `-i`, or match the exact string `Two ways to stay on the allowed side`. Verified against the pinned worktree: current command exits 1 with output `0`; the `-i` form finds 1 match at line 65. Re-run the corrected row against the pre-change file and confirm it reports non-zero, otherwise the row is still vacuous. |
| CONCERN | Structural | Verification row "Present-fact norm retained" greps `"already true"` and expects `> 0`, but **both** occurrences (`prime-pm-role.md:50` and `:67`) sit inside the `:50-71` block Task 3 deletes, while Task 3 only commits to keeping the norm "verbatim in spirit". As written the row forces the replacement text to contain the literal phrase, which the task text never promises — a spec conflict that will surface as a late verification failure. | Task 3 + Verification table | Decide one way and make both sides agree: either amend Task 3 to require the literal sentence `say only what is already true` survive into the replacement, or re-anchor the verification row onto a phrase the replacement definitely contains (e.g. the recorded-obligation discriminator wording). Do not leave a grep pinning a string the task only promises in spirit. |
| CONCERN | Structural | Prerequisite 1 (`ANTHROPIC_API_KEY` via `dotenv_values('.env')`) **fails in the lane worktree**: `.env` is a gitignored symlink present only in the main checkout (`/Users/valorengels/src/ai/.env -> ~/Desktop/Valor/.env`), and the build runs in `.worktrees/promise-gate-recorded-obligations/`. Task 7 runs the real-API gate test there and will read a missing key as a missing credential. | Prerequisites | `dotenv_values('.env')` resolves relative to cwd and returns `{}` in the worktree — verified. Either resolve the key through `utils.api_keys.get_anthropic_api_key` (which the gate itself uses) rather than reading `.env` by path, or symlink `.env` into the worktree as a documented build prerequisite step. Note the ambient shell env may still carry the key, so the check can pass by accident on some machines and fail on others. |
| CONCERN | Scope & Value | The final Success Criteria bullet bundles a build-time check with `SCHEMA_ROUTING_FALLBACK_METRIC` flat after merge — a post-merge soak observation with no Verification-table row (the table's only fallback row checks the static fact "Phase A: not required"). Nothing in this plan's execution can tick that box, and Risk 3 already assigns required-tightening (the change that would move the metric) to #3035. | Success Criteria | Split the bullet: keep "tests pass / docs updated" as build-gated, and move the metric-flat observation onto #3035's tracked entry criteria, which already own the post-soak measurement over the same window. Task 9's final-validator must not be asked to verify a live alert state that needs days of post-merge traffic — it will either block or be waved through, and both outcomes corrupt the gate. |
| CONCERN | Scope & Value | Task 5 bundles four distinct concerns for one builder under a Medium appetite: extracting the async public gate API, adding the drafter's first delivery-path LLM call, greenfield `elapsed_ms` instrumentation, and an orthogonal dead-code/timeout-source bugfix (`_PromiseTimeoutError`, `bridge/promise_gate.py:762,830`). A regression becomes hard to bisect between "the new LLM call" and "the timeout-source fix". | Task 5 + Task 6 | Keep the fix in Task 5 (it touches the same `except` block spike-3 identifies) but add a sub-bullet instructing the builder to land the `_PromiseTimeoutError` removal + `APITimeoutError` mapping as its **own commit before** the async/LLM commits. Task 6 should run `test_promise_gate.py::TestSDKTimeout::test_timeout_falls_through_to_heuristic_with_timeout_source` standalone before the full five-file suite so the two failure modes are independently diagnosable. |
| NIT | Risk & Robustness | Risk 1 says the change "serves nine non-test callers"; the pinned worktree has exactly three (`bridge/email_bridge.py:925`, `agent/output_handler.py:754`, `agent/hooks/stop.py:147`). Overstated 3x. Harmless in direction (smaller blast radius), but it weakens confidence in the section's other unverified figures, notably the ~500ms p50 estimate. | Risk 1 (corrected to three) | Correct the count to three. |
| NIT | History & Consistency | The plan has the `PM_TURN_JSON_SCHEMA` comment document the phase-A/phase-B state of `ask_coverage`, but nothing assigns ownership of deleting that narration once #3035 tightens the field to required — it becomes a stale "formerly optional" artifact, against CLAUDE.md's no-historical-artifacts rule. | No-Gos #3035 entry | One sentence in the No-Gos #3035 entry stating the phase-B followup must also delete the phase-A/phase-B comment language at `agent/session_runner/router.py:61`. |
| NIT | Structural | Two Verification rows use `git grep -c` (the `evaluate_delivery` anti-criterion and the No-Go 5 poll row). On zero matches `git grep -c` prints **nothing** and exits 1, so the stated expectation "match count == 0" never appears in the output. | Verification table | Use plain `grep -rc` (which prints `0`), or state the expectation as "exit 1, no output". Verified: both rows currently produce empty output with exit 1. |
| NIT | Structural | Test Impact's "Unaffected" list names `tests/unit/test_harness_argv_golden.py`, which does not exist in the worktree. | Test Impact | Drop the row or correct the filename; the `tests/unit/test_harness_*.py` files present are context-usage-log, model-coverage, oom-backoff, retry, stale-uuid, streaming, thinking-block-sentinel, token-capture, tool-cost-wiring. |
| NIT | Structural | Task 1 cites `agent/session_runner/runner.py:1373` as the metric emission site; the actual `record_metric(SCHEMA_ROUTING_FALLBACK_METRIC, 1.0)` call is at `:1378`. | Task 1 | Re-anchor on the symbol rather than the line, consistent with the freshness pass's own stated policy that "symbol names are the durable anchor". |
| BLOCKER | mechanics | `agent/hooks/stop.py:147` awaits `draft_message` inline on the Stop hook's 10s-walled critical path. Adding the main-path LLM call reproduces the documented 126/131 SIGKILL timeout incident (`docs/features/memory-hook-performance.md`). | Risk 1a; Task 5; Task 6 | `draft_message` gains `use_llm`; stop hook passes `use_llm=False`. Do NOT bound with a timeout — that is the mistake the incident record names. |
| MAJOR | scope | No-Go 5 (leave the poll route ungated) rationalizes a gap: `validate_telegram_poll` does zero honesty checking, a poll question can carry a completion claim, and Owner Ruling 4 is transport-level. The cheap middle option was never considered. | No-Go 5 rewritten; Task 5 | Run the existing zero-cost `_evaluate_promise_heuristic` on poll question text. No LLM, no latency. Residual gets its own issue, not #3035. |
| MAJOR | tests | The claim that `tests/fixtures/persona/eng_worker_repo_baseline.txt` quotes `prime-pm-role.md` is **false** — 0 matches; `compose_system_prompt` never reads the role files. The regeneration step was a no-op against the wrong file. | Task 3 | Regeneration step deleted. The real cascade risk is `.opencode/commands/prime-pm-role.md`, whose sync is manual. |
| MAJOR | tests | Replacing `TestPromiseGateFallbackAllowsTaughtPhrasings` with prose-content greps drops a genuine mechanical prompt-to-gate lock. | Task 3 | Surviving illustrative phrasings must still be asserted against `_evaluate_promise_heuristic`. |
| MAJOR | tests | Success Criteria promise a delivered-text assertion that no task actually builds; issue AC forbids schema-object-only proof. | Task 1 | Scripted-driver test asserting the string handed to `send_cb` carries the disposition. |
| MAJOR | tests | Four Verification rows already pass on unmodified code; "Phrasebook gone" could never fail (case-sensitive grep vs capital "Two"). | Verification table | Rows rewritten: `grep -ci`, properties+required check, real regression tests instead of source-string greps. |
| MAJOR | mechanics | Task 1's "shared `self_draft_attempts` backstop" is never wired — `runner.py` never imports `bump_self_draft_attempts`. | Task 1 | Explicit bump + a test asserting the coverage bounce increments it. |
| MAJOR | scope | R1 success criterion tests only the positive override case, never the Job-scoped laundering the code's own owner-ruling comment warns about. | Risk 6; Task 6 | Named as accepted pre-existing behavior, with a characterization test so it is not silently "fixed". |
| MAJOR | scope/tests | Latency measured only from test-run audit rows, dominated by PM-turn scenarios; AC demands general outbound traffic. | Task 9 | Percentiles grouped by source and calling surface; uncovered callers named, not blended away. |
| MINOR | scope/tests | Freshness Check said the two retained #3080 bullets are "adjacent" to the deletion (~20 lines separate them); phrasebook range stated as 50-71, actually 50-70. | Freshness Check | Both corrected. |
| MINOR | mechanics | Race 2 argues bounce safety without naming the FIFO property that actually makes it safe. | Race 2 | `push_steering_message` is an RPUSH, so a concurrent human steer and the advisory coexist rather than clobber. |

---

## Owner Rulings (2026-08-27)

Open questions were resolved by the owner at plan time; recorded here as the authority for the choices above.

1. **Advisory bounce over deterministic footer** for user-visible clause dispositions — the PM's revised prose carries them, one bounce per turn, no semantic verification of the revision.
2. **No timed soak window.** Recent update churn precludes a reliable soak; both post-merge decisions (phase-B required-tightening, phase-4 build) live in investigation issue #3035, deferred to no earlier than 2026-09-10.
3. **`promise_gate_drafter_llm`** as the additive audit source for main-path LLM verdicts.
4. **All `draft_message` callers** get the main-path LLM gate — honesty is transport-level; the short path shields brief replies.

## Owner Ruling (2026-09-03) — latency budget for the LLM path

The p50 < 500ms figure predates the decision to put a real Haiku call inline on the
composed drafter path. Task 9's measurement showed the LLM path at p50 ≈ 1619ms with
`queue_wait_ms` ≈ 0, i.e. Anthropic API round-trip time, not semaphore contention — a
floor no tuning reaches. **Ruled by the owner: the measured p50 on the obligation-carrying
LLM path is accepted, and the budget is relaxed to match reality.**

The budget for the LLM path is therefore **p50 ≤ 2500ms, p99 ≤ 5000ms**. The zero-LLM
short path keeps its existing guarantee (p50 ≈ 0ms) and is unchanged by this ruling.

<!-- Grain of salt: 2500/5000 are provisional and tunable. They are set roughly 1.5x
     above the measured p50/p99 (1619ms / 2463ms) to leave headroom for API variance
     without going so wide that a genuine regression hides inside them. Re-derive from
     post-merge audit JSONL rather than treating them as settled. -->

This closes the escalation raised in the PR body's "Open owner decision" section: option
(a) is taken (restate the figure), option (b) (a cheaper heuristic short-circuit ahead of
the LLM call) is explicitly **not** taken, because it would partly undo the purpose of
moving LLM judgment onto the main path.
