# Plan Critique: Reliable PM Final-Delivery Protocol

**Plan**: `docs/plans/reliable-pm-final-delivery.md`
**Issue**: #1058
**Re-run of**: prior critique (NEEDS REVISION, `2026-04-20T22:50:22Z`, findings lost)
**Baseline**: session/sdlc-1058 @ revision commit `05e9a805`
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor
**Findings**: 12 total (2 blockers, 7 concerns, 3 nits)

> Persisted to this non-plan path (not `docs/plans/critiques/`) because repo hooks treat any new `.md` under `docs/plans/` as a plan requiring `## Test Impact` / `## Documentation` / `## Success Criteria` / `## Update System` / `## Agent Integration` sections, which do not belong in a critique artifact. Also mirrored to issue #1058 as a comment for external retrievability.

---

## Blockers

### B1. Fabricated symbol `_get_claude_session_uuid` in Technical Approach

- **Severity**: BLOCKER
- **Critics**: Skeptic, Archaeologist, Consistency Auditor
- **Location**: Solution → Technical Approach → "New coroutine `_deliver_pipeline_completion`" (plan L141–143)
- **Finding**: The Technical Approach says the runner "Loads the PM's Claude Code UUID via `agent.sdk_client._get_claude_session_uuid(session_id)`." No such helper exists in `agent/sdk_client.py`. Verified via `grep` — only `_store_claude_session_uuid` (L203), `_get_prior_session_uuid` (L152), and `_has_prior_session` (L191) exist. The plan also claims "the UUID lives on the PM's `AgentSession.claude_session_uuid` field (set by `_store_claude_session_uuid` after the first harness turn)" — that part is correct (`models/agent_session.py:179`), but the fetch helper is invented.
- **Suggestion**: Either (a) rename the reference to `_get_prior_session_uuid(session_id)` — which already does this lookup — or (b) declare a new thin helper explicitly and add it to the Technical Approach as a created symbol. Do NOT leave the plan citing an imagined private function; the builder will waste a turn re-investigating.
- **Implementation Note**: `_get_prior_session_uuid(session_id)` at `agent/sdk_client.py:152` reads `AgentSession.claude_session_uuid` from Redis and returns `str | None`. It is the canonical read path (also used by `get_response_via_harness` at L1109). The runner should call `_get_prior_session_uuid(parent.session_id)` and treat `None` as "fall through to `full_context_message` no-UUID path" — which matches the existing harness behavior for stale/missing UUIDs.

### B2. Residual marker-instruction strings in `agent/session_completion.py` not covered by the Solution

- **Severity**: BLOCKER
- **Critics**: Adversary, Consistency Auditor, Archaeologist
- **Location**: Solution → Persona cleanup / Technical Approach (plan L193–196); Success Criteria L337
- **Finding**: The plan removes `[PIPELINE_COMPLETE]` references from `config/personas/project-manager.md` (L44, L49, L384, L487) and from `agent/output_router.py` / `agent/session_executor.py`, but does NOT touch `agent/session_completion.py` L271 (`"Do NOT emit [PIPELINE_COMPLETE] until the PR is merged or closed."` inside the continuation-PM steering message) or L450 (`"You MUST invoke /sdlc to dispatch /do-merge before emitting [PIPELINE_COMPLETE]."` inside `_handle_dev_session_completion`'s steering message). These are **worker-constructed prompts sent to PM sessions** — after this refactor, the PM is told to emit a marker that is no longer routed and has no effect. The Success Criterion `grep -rn PIPELINE_COMPLETE agent/ config/personas/` returns zero matches will FAIL against the plan as written (two live references remain in `agent/session_completion.py`).
- **Suggestion**: Extend Task #6 (router simplification) or add an explicit subtask under Task #10 to rewrite those two steering message strings in `agent/session_completion.py`. The semantic intent (no completion while PR open) must survive without naming the marker — e.g., `"Do NOT signal pipeline completion until the PR is merged or closed."`
- **Implementation Note**: Lines are `agent/session_completion.py:269-273` (continuation PM message) and `agent/session_completion.py:446-451` (steer message). Both are f-strings inside try/except blocks; edit the literals only — do not refactor the surrounding logic. Also update the unit test `tests/unit/test_steering_mechanism.py:205,209,223` that asserts these marker substrings are present (currently listed in Test Impact as "UPDATE" for `test_session_completion*.py` but the assertion in `test_steering_mechanism.py` is the actual landing site — callout is ambiguous).

---

## Concerns

### C1. `current_stage == "MERGE"` is a brittle proxy for "pipeline done"

- **Severity**: CONCERN
- **Critics**: Skeptic, Adversary
- **Location**: Technical Approach → `is_pipeline_complete` (plan L163); Step by Step Task 1 (plan L415–419)
- **Finding**: The predicate keys on `current_stage == "MERGE" AND outcome == "success"`. `current_stage` is captured in `_handle_dev_session_completion` at L386 via `psm.current_stage()`, which returns the stage currently `in_progress` (`agent/pipeline_state.py:600-610`). If the MERGE dev session runs and `psm.current_stage()` ever returns `None` (e.g., MERGE was already marked completed by a prior attempt, or stage_states was cleared), the predicate returns `False` and the session will fall back to the old nudge path — but the marker is gone, so it will nudge 50× then deliver_fallback. This is silently worse than today: today the marker *can* break the loop; after this refactor, the loop has no escape other than the cap.
- **Suggestion**: Either broaden the predicate to `completed_stages >= {"MERGE"}` (read from `psm.states`) rather than `current_stage == "MERGE"`, or add a defense-in-depth fallback: if `auto_continue_count >= 5` on a PM/SDLC session AND `psm.states.get("MERGE") == "completed"`, invoke `_deliver_pipeline_completion` from the router.
- **Implementation Note**: `PipelineStateMachine.states` is a `dict[str, str]` where values are `"pending" | "ready" | "in_progress" | "completed" | "failed"`. Read via `psm.states.get("MERGE")` — do NOT re-call `current_stage()` after `complete_stage(MERGE)` because complete_stage sets the state to `"completed"` and `current_stage()` then returns `None` (L604: iterates `ALL_STAGES` for any `"in_progress"` entry).

### C2. `pipeline_complete_pending` flag is described as transient but has no clear lifecycle

- **Severity**: CONCERN
- **Critics**: Adversary, Operator
- **Location**: Risks → Risk 2 (plan L246–248); Race Conditions → Race 1 (plan L264–269); Task 3 (plan L438)
- **Finding**: The plan says the flag is stored in `AgentSession.extra_context` (DictField, persisted to Redis). "Transient in-memory or Redis-only, not persisted" is contradictory — `extra_context` IS persisted. If a runner crashes mid-flight after setting the flag, the next worker run sees `pipeline_complete_pending = True` on a parent that is still `"running"` with no active runner. `_finalize_parent_sync` would then skip finalization forever, and no new runner would be spawned (idempotency guard skips entry — Race 2 mitigation).
- **Suggestion**: Clarify the flag's lifecycle. Options: (a) set `pipeline_complete_pending = {"runner_id": <task_id>, "started_at": <iso>}` so a stale flag (>N seconds) is ignored; (b) clear the flag on terminal transitions in `finalize_session` so startup-recovery naturally recovers; (c) use a Redis-native TTL key (not extra_context) so it auto-expires.
- **Implementation Note**: `extra_context` setter writes to Redis via `session.save()` — see `models/agent_session.py:152`. A 60s TTL via `POPOTO_REDIS_DB.set(f"pipeline_complete_pending:{parent_id}", "1", nx=True, ex=60)` is cheaper and self-healing. Use this instead of `extra_context` — the flag is a lock, not state.

### C3. Fan-out path races with per-child completion path

- **Severity**: CONCERN
- **Critics**: Adversary, Archaeologist
- **Location**: Flow → Fan-out completion path (plan L147–150); Race Conditions → Race 2 (plan L272–276)
- **Finding**: The fan-out path invokes `_deliver_pipeline_completion` from `_agent_session_hierarchy_health_check` when all children terminal. But each child's completion already fires `_handle_dev_session_completion` — so N children completing near-simultaneously can trigger: (a) N invocations of `_handle_dev_session_completion`, any of which might see all siblings terminal and check `is_pipeline_complete`, PLUS (b) the hierarchy-health-check tick firing concurrently. Race 2 mitigation (CAS on `pipeline_complete_pending`) handles N overlapping `_handle_dev_session_completion` calls, but the plan does NOT state that the hierarchy-health-check path uses the same CAS. If both the health-check and a `_handle_dev_session_completion` invocation race, two runners may spawn.
- **Suggestion**: Explicitly state that BOTH entry points (`_handle_dev_session_completion` AND `_agent_session_hierarchy_health_check`) use the same CAS on `pipeline_complete_pending`. Add an idempotency assertion to `_deliver_pipeline_completion` (sole owner transitions to `completed`) and reference it from both Tasks #3 and #5.
- **Implementation Note**: The CAS pattern is `POPOTO_REDIS_DB.set(key, "1", nx=True, ex=60)` returns `True` on acquisition, `False` if already held. Place this as the first line of `_deliver_pipeline_completion` (inside try/except); on `False`, log at INFO and return immediately. This matches the `continuation-pm:{parent_id}` dedup pattern at `agent/session_completion.py:247-256` — reuse it.

### C4. CancelledError handler may re-deliver on every recovery

- **Severity**: CONCERN
- **Critics**: Adversary, User
- **Location**: Flow → CancelledError path (plan L152–157); Task 8 (plan L495–499)
- **Finding**: The handler emits `"I was interrupted and will resume automatically. No action needed."` via `send_cb`, then re-raises. startup-recovery then re-queues the session. When the newly-spawned attempt also gets CancelledError (worker flapping: deploy loop, OOM kill, health-check cycling), the user receives the "interrupted" message repeatedly. No dedupe/throttle is mentioned. The existing PR #898 nudge-stomp CAS pattern is cited (plan L70) but not reused for the interrupted-message path.
- **Suggestion**: Add idempotency — only emit the interrupted message if the most recent `session_events` delivery is not already the interrupted message (or stamp a Redis key with short TTL). At minimum, document the behavior in Risk section so on-call operators know a flapping worker produces N identical messages.
- **Implementation Note**: Check `session.session_events` for the latest `event_type="delivery"` entry and compare `text == INTERRUPTED_MESSAGE`. If same, skip the send. Or: set `POPOTO_REDIS_DB.set(f"interrupted-sent:{session_id}", "1", nx=True, ex=120)` and only send on acquisition. The 120s TTL lets genuinely distinct interruptions surface while suppressing flapping.

### C5. Test Impact list under-specifies the marker-instruction assertion in `test_steering_mechanism.py`

- **Severity**: CONCERN
- **Critics**: Simplifier, Consistency Auditor
- **Location**: Test Impact (plan L220); tests/unit/test_steering_mechanism.py L205–223
- **Finding**: Test Impact says "DELETE: entire class `TestPipelineCompleteMarker`" — but the file has a SECOND block (L200–223 approx) that asserts the steering message emitted by `_handle_dev_session_completion` contains `"[PIPELINE_COMPLETE]"` (L205, L209, L223). If B2 is fixed (marker removed from that steering message), this test will fail. Test Impact needs to say "UPDATE (assert the new marker-free instruction string) or DELETE" — current wording suggests only the `TestPipelineCompleteMarker` class goes away.
- **Suggestion**: Add explicit Test Impact entry for `tests/unit/test_steering_mechanism.py:195-223` (the fan-out/continuation assertions block) — UPDATE to assert the new marker-free instruction string (e.g., `"signal pipeline completion"`), not just delete `TestPipelineCompleteMarker`.
- **Implementation Note**: The affected test reads the `_handle_dev_session_completion` steering msg literal. Once B2's rewrite lands (new string e.g. "Do NOT signal pipeline completion until the PR is merged or closed."), the assertion should check for that substring. Keep the test's intent (PM is warned about pre-merge completion) — only update the asserted literal.

### C6. `_check_pr_open` subprocess runs per dev-completion without issue-number caching semantics

- **Severity**: CONCERN
- **Critics**: Operator, Simplifier
- **Location**: Technical Approach (plan L164); Task 1 (plan L419); Risks → Risk 5 (plan L258–260); Open Question #2 (plan L597)
- **Finding**: The predicate calls `gh pr list --search "#{issue_number}" --state open` with a 5s timeout on every dev-session completion. For a pipeline with 7 stages, that is 7 subprocess invocations during a typical issue. The "5-second TTL cache on extra_context" mitigation (Risk 5) is listed as an open question (Open Q #2), so it is not part of the accepted Solution. Without the cache, builders will land code without it and revisit after the performance regression. The question should be decided before build.
- **Suggestion**: Close Open Question #2 in the plan (commit to one answer) before transitioning to build. Recommend: no cache on extra_context (adds Redis chatter and consistency risk); instead, skip the PR check unless `current_stage == "MERGE"`. For MERGE completions, the subprocess cost is one-shot per pipeline — acceptable.
- **Implementation Note**: In `is_pipeline_complete(current_stage, outcome, pr_open=None, issue_number=None)`, gate the `_check_pr_open` call on `current_stage == "MERGE"`. For DOCS-success-no-PR path, require an explicit `pr_open` kwarg (caller decides). This keeps the predicate pure and makes the subprocess cost scoped to MERGE only.

### C7. Rule 5 rewrite in persona requires a new signal word, not specified

- **Severity**: CONCERN
- **Critics**: User, Archaeologist
- **Location**: Technical Approach → Persona cleanup (plan L193–196); Task 10 (plan L511–520)
- **Finding**: The rewrite says "Do not indicate pipeline completion while an open PR exists for the current issue." But "indicate pipeline completion" is ambiguous — the whole point of the plan is to remove the marker as the indicator. Under the new protocol, the PM doesn't indicate completion at all; the worker decides. Rule 5 becomes "don't do X" where X is a thing the PM is no longer supposed to do anyway. Left as-is, the PM may misread the rule as asking for some new signal.
- **Suggestion**: Reword to describe the actual constraint: "If an open PR exists for the current issue, you must dispatch `/do-merge` before declaring the issue done. Your final message to the user is composed automatically by the worker after MERGE succeeds — do not attempt to self-signal pipeline completion." Remove the word "indicate" entirely.
- **Implementation Note**: The persona file is Markdown consumed by the agent as system prompt context. Phrase changes are high-leverage — test with at least one PM session after the rewrite (smoke test, not unit test). A clean literal to search for as a validation gate: `grep -c "composed automatically by the worker" config/personas/project-manager.md` should return 1 after Task 10.

---

## Nits

### N1. "Completion prompt wording" listed as Open Question despite plan committing to a specific prompt

- **Severity**: NIT
- **Location**: Open Question #1 (plan L595); Technical Approach (plan L168–173)
- **Finding**: The Technical Approach fully specifies the completion prompt text (L169–173) but Open Question #1 asks whether it should be "more prescriptive." Either the prompt is committed (ship as written) or it is open (block build). Having both is internally inconsistent.
- **Suggestion**: Move Open Question #1 to Rabbit Holes ("prompt iteration can happen post-ship based on observed summary quality") OR commit to a rewrite and remove from Open Questions.

### N2. Deprecation period for marker left undecided, but the plan's own Success Criteria require zero matches

- **Severity**: NIT
- **Location**: Open Question #5 (plan L603); Success Criteria (plan L337, L345)
- **Finding**: Open Q #5 asks whether to keep the marker as a no-op stripped string for one release cycle. Success Criterion at L345 says `grep -c PIPELINE_COMPLETE config/personas/project-manager.md` must return 0 — which rules out keeping a compatibility shim in the persona. The two sections are consistent on persona but not on the router (L337: `grep PIPELINE_COMPLETE agent/` returns zero). A no-op shim would violate this.
- **Suggestion**: Commit to "remove immediately, no compatibility shim" and delete Open Question #5. Or soften the success criterion.

### N3. No-Gos forbids "adding new ORM fields" but plan adds `pipeline_complete_pending` to `extra_context`

- **Severity**: NIT
- **Location**: No-Gos (plan L295); Race Conditions → Race 1 (plan L269)
- **Finding**: No-Gos says "Adding new ORM fields (beyond the transient in-memory `pipeline_complete_pending` flag if needed, which can live in `extra_context` dict without schema migration)." This is fine semantically but contradicts itself — if it's a new field, it's not transient; if it's transient, stop calling it a field. Combined with C2 (flag lifecycle undefined), this adds reader confusion.
- **Suggestion**: Pick one framing. Recommend treating it as a Redis-only advisory lock (per C2 Implementation Note) and explicitly stating in No-Gos: "No new AgentSession fields. Completion-pending coordination uses a Redis-native key with TTL, not ORM storage."

---

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections (of the plan) | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1–13 present, no gaps |
| Dependencies valid | PASS | All `Depends On` references resolve (build-predicate, build-runner, build-fanout, build-router, build-cancel, build-persona, build-integration, document-feature, validate-*) |
| Circular dependencies | PASS | DAG: predicate → runner → {fanout, router}, cancel, persona; integration depends on all builders; docs on integration; final validator on all. |
| File paths (existing) | PASS | 18 of 18 referenced existing files verified |
| File paths (new) | PASS | 6 new files (pipeline_complete.py, 5 test files + pm-final-delivery.md) correctly marked as new |
| Prerequisites | PASS | Zero prerequisites declared — no checks to run |
| Cross-references | CONCERN | Success Criterion at L337 (`grep PIPELINE_COMPLETE agent/` zero matches) will fail due to B2 (L271, L450 residual references). Criterion at L345 (persona grep=0) will pass after Task 10. |
| Critique-findings persistence | PASS | This file addresses the gap identified in Critique Results section of the plan. |

---

## Verdict

**NEEDS REVISION**

Two blockers must be resolved before build:

- **B1**: The fabricated `_get_claude_session_uuid` helper in Technical Approach needs to be corrected to the real `_get_prior_session_uuid` (or a new helper declared explicitly).
- **B2**: The two live `[PIPELINE_COMPLETE]` references in `agent/session_completion.py` (L271, L450) are not listed for rewrite anywhere in the Solution or Tasks, and the Success Criterion's `grep -rn PIPELINE_COMPLETE agent/` = 0 is not satisfiable against the plan as written.

The seven concerns (C1–C7) should be addressed in the revision pass — their Implementation Notes are included inline for direct embedding into the plan. Open Questions #1, #2, and #5 should be closed (commit to one answer each) in the same pass.

The structural checks all pass; this is a revision issue, not a rework issue. The overall design (Option B, dedicated completion turn, marker-free router, CancelledError guard) is sound and consistent with the Prior Art.

After B1 and B2 are fixed and the concerns are embedded, re-run `/do-plan-critique` against the revised plan. Expected outcome: **READY TO BUILD (with concerns)** or **READY TO BUILD**.
