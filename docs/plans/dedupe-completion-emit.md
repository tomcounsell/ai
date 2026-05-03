---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-05-03
tracking: https://github.com/tomcounsell/ai/issues/1262
last_comment_id:
---

# Dedupe Completion Emit

Give the PM completion runner visibility into messages already sent during a session so it stops re-emitting reformatted versions of content the user already received.

## Problem

A PM bridge session finishes its work in two visible steps:

1. A sub-skill (e.g. `/do-docs`, `/sdlc`) calls `valor-telegram send` from inside the session and posts an answer to the user.
2. Seconds later, the completion runner (`agent/session_completion.py::_deliver_pipeline_completion`) fires its auto-emit at session-end and posts a *reformatted version of the same answer*.

The user sees two consecutive messages saying substantively the same thing.

**Current behavior:** The completion runner's drafter prompt is built only from stage outcome metadata (`agent/session_completion.py:551`):

```python
prompt = _COMPLETION_PROMPT_PREFIX + (summary_context or "")[:3000]
```

`summary_context` is `f"Stage {current_stage} completed with outcome={outcome} (reason={reason}). Result preview: {result_preview}"` — no view of `chat_message_log`, no `recent_sent_drafts`. The drafter literally cannot see what was already sent.

The redundancy filter that *would* catch this (`bridge/redundancy_filter.py`) is wired only into `TelegramRelayOutputHandler.send()` (`agent/output_handler.py:407`). The completion runner calls `send_cb` directly (`agent/session_completion.py:697`), bypassing it. The filter's terminal-status exemption (`bridge/redundancy_filter.py:161-162`) compounds this — it explicitly forces delivery on `completed`/`failed`/`blocked`, the inverse of what we want.

**Desired outcome:** The final stop hook / completion drafter sees what messages were already sent during the session. If the user-facing answer was already delivered mid-session, the completion runner suppresses the auto-emit (or replaces it with a 👀 reaction on the user's last message) rather than reformat-and-resend the same content.

## Freshness Check

**Baseline commit:** `1a28000d304b729b2a9110666804864854e2be03`
**Issue filed at:** `2026-05-03T06:46:49Z` (today)
**Disposition:** **Unchanged**

**File:line references re-verified:**
- `agent/session_completion.py:551` — `prompt = _COMPLETION_PROMPT_PREFIX + (summary_context or "")[:3000]` — still holds
- `agent/session_completion.py:563-566` — sentinel + `delivery_attempted = False` init — still holds
- `agent/session_completion.py:690-691` — "guaranteed non-empty" comment — still holds
- `agent/session_completion.py:694-717` — `send_cb` delivery block — still holds (send_cb call at line 697)
- `bridge/redundancy_filter.py:52` — `_TERMINAL_STATUSES = frozenset({"completed", "failed", "blocked"})` — still holds
- `bridge/redundancy_filter.py:161-162` — terminal-status exemption — still holds
- `bridge/redundancy_filter.py:74-132` — `should_suppress()` API contract — still holds (fail-open: any unhandled exception returns `send`)
- `agent/output_handler.py:407` — `_recent_drafts: list = getattr(session, "recent_sent_drafts", None) or []` — still holds (sole production caller of the filter)
- `models/agent_session.py:228` — `recent_sent_drafts = ListField(null=True)` — still holds
- `models/agent_session.py:396` — `chat_message_log = ListField(default=list)` — still holds
- `models/agent_session.py:1407, 1484-1521` — append helpers for both fields — still hold
- `bridge/message_drafter.py:1246-1276` — chat-log injection pattern in regular drafter — still holds

**Cited sibling issues/PRs re-checked:**
- PR #1239 — *Drafter-Suppress-Redundant* — merged 2026-05-01 (the redundancy filter's origin). Established the 👀-reaction-on-suppress pattern this plan reuses.
- PR #1244 — *chat_message_log* field — merged. Field exists; mid-session sends already populate it via Tier-1 resolution at `bridge/telegram_relay.py:519-528`.
- PR #1204 — *Read-the-Room* — merged. Provides the Haiku-judge pattern this plan adapts for borderline-Jaccard cases.
- #1058 — *PM Final-Delivery Protocol* — closed. Constraint: completion runner remains sole owner of the final emit and parent-session `completed` transition.
- #1203 — *RTR for Path B* — closed without shipping. Conceptually adjacent, deferred.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** `phantom-pm-twin-dedupe.md` is unrelated — it dedupes Redis `AgentSession` rows, not Telegram messages.

**Notes:** All cited file:line pointers match current HEAD exactly. No drift.

## Prior Art

- **PR #1239** — *feat(drafter): suppress redundant PM status messages with bigram-Jaccard filter (#1205)* — merged 2026-05-01. Origin of `bridge/redundancy_filter.py`. Wires suppression into `TelegramRelayOutputHandler.send` only; explicitly does not cover the completion runner. Established the *suppress → queue 👀 reaction* convention this plan extends.
- **PR #1244** — Added the `chat_message_log` `ListField` to `AgentSession` so the regular drafter could see recent in-thread history. The data infrastructure this plan needs already exists; this plan is the consumer the field was always intended to serve.
- **PR #1204** — Read-the-Room (RTR) pre-send drafter pass for personal chats. Uses Haiku with `tool_use` for nuanced suppression decisions, fail-open contract. Pattern reused for the borderline-Jaccard band in this plan.
- **#1203** — RTR-for-Path-B — deferred and closed. Conceptually adjacent (mid-session sends bypassing post-send safeguards), different cut. This plan addresses the closely-related completion-runner blind spot.
- **#1058** — Established the PM Final-Delivery Protocol: completion runner is the sole caller that transitions parent session to `completed` on the success path. *This plan must not move that ownership.*

## Spike Results

Five parallel research passes ran during issue triage (transcript: `/tmp/issue-1262-research.md`). Each resolved one of the issue's open design questions.

### spike-1: Where to intercept (Q1)
- **Assumption**: Either context-injection at Pass 1 (let drafter judge) or post-draft suppression (deterministic backstop) is sufficient on its own.
- **Method**: code-read of `agent/session_completion.py:454-786`, `bridge/message_drafter.py:1246-1276`, `bridge/redundancy_filter.py`.
- **Finding**: Tom's framing in-thread (*"the drafter just needs to see what messages were already sent and help to judge whether another final message needs to also be sent"*) maps to context-injection. Subagent independently recommended post-draft suppression for deterministic-cost properties. **Both compose cleanly**: context-injection lets the drafter naturally choose to elide / shrink / acknowledge, and post-draft suppression catches the LLM-disobeys-instructions case. Net cost: ~3-5 ms (chat-log read + format) + zero LLM cost (filter is deterministic).
- **Confidence**: high
- **Impact on plan**: Adopt **both cuts**. Context-injection at Pass 1 prompt assembly (`session_completion.py:551`, mirroring `message_drafter.py:1246-1276`); post-draft suppression after Pass 2 produces `final_text`, before `send_cb` (`session_completion.py:694`).

### spike-2: What "duplicate" means for completion summaries (Q2)
- **Assumption**: The existing 0.65 bigram-Jaccard threshold is appropriate for completion summaries.
- **Method**: code-read of `bridge/redundancy_filter.py:148-226`, `bridge/read_the_room.py:119-154, 343-518`, `tests/unit/test_redundancy_filter.py`.
- **Finding**: 0.65 is tuned for ~verbatim status repeats. Completion summaries that legitimately *include* a mid-session send while *adding* new context typically land in J ≈ 0.55-0.65 — would slip past current threshold and ship as duplicates. Lifting the threshold to 0.75 catches near-verbatim instantly; the borderline band [0.55, 0.75) needs an LLM judge using the RTR pattern (Haiku + `tool_use`, fail-open, ~1-2s latency, ~1000 tokens).
- **Confidence**: high
- **Impact on plan**: Hybrid scoring with a new env-tunable completion-specific threshold (default `DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD=0.75`) plus Haiku escalation in band [0.55, 0.75). Pass `prior_timestamp` into the Haiku prompt so the judge can weight stale-vs-fresh context.

### spike-3: Suppressed-emit fallback behavior (Q3)
- **Assumption**: Silent suppression vs text "Done." vs emoji reaction are all viable.
- **Method**: code-read of `tools/react_with_emoji.py:80`, `bridge/telegram_relay.py:84-144`, `bridge/response.py:54-75`, `models/agent_session.py:1425-1431`. Memory check: `feedback_emoji_over_acks`, `feedback_reactor_voice_emoji`.
- **Finding**: 👀 reaction is the established project convention (`feedback_emoji_over_acks`, `feedback_reactor_voice_emoji`). Infrastructure exists end-to-end: `tools/react_with_emoji.py` queues to `telegram:outbox:{session_id}`; `bridge/telegram_relay.py::_send_queued_reaction` consumes and dispatches via `set_reaction`; `bridge/response.py::VALIDATED_REACTIONS` includes "👀". Completion runner already receives `telegram_message_id` (anchor message id) as a parameter — no new plumbing. Plan #1205 already shipped this exact pattern for RTR. Reuse.
- **Confidence**: high
- **Impact on plan**: On suppress, queue 👀 reaction on `telegram_message_id`. If `telegram_message_id` is None (rare — only when the completion runner was invoked without an anchor), fall through to silent completion + log warning. Never emit text "Done." (violates persona convention).

### spike-4: Terminal-status exemption reconciliation (Q4)
- **Assumption**: Either modify `_TERMINAL_STATUSES` exemption or add a separate completion-specific filter call.
- **Method**: grep for `should_suppress` callers (only one production site: `agent/output_handler.py:407`); code-read of `bridge/redundancy_filter.py:161-162`; trace of session-status state at the moment `send_cb` is invoked.
- **Finding**: The exemption was designed by #1239 for *in-session terminal transitions* — when a PM goes from running → terminal mid-loop, the final draft must reach the user. Concern still valid for `TelegramRelayOutputHandler.send`. Critically: at the moment the completion runner calls `send_cb` (line 697), the parent session is **NOT YET terminal** — `finalize_session("completed")` runs in the `finally` block at line 771, *after* delivery. So even if the runner did call the existing filter, the exemption would be inert — but mixing the two semantic concerns is a future-confusion trap. Cleanest answer: leave `bridge/redundancy_filter.py` untouched; in the completion runner, call `should_suppress(...)` with `session_status=None` to explicitly bypass the exemption.
- **Confidence**: high
- **Impact on plan**: Do NOT modify `bridge/redundancy_filter.py`. New helper in completion runner reuses `should_suppress(...)` with `session_status=None`. Zero broken tests in `tests/unit/test_redundancy_filter.py::TestTerminalStatus`.

### spike-5: AGENT_SESSION_ID Tier-3 ambiguity blast radius (Q5)
- **Assumption**: Mid-session sends without `AGENT_SESSION_ID` may land in the wrong session's `chat_message_log`, defeating the whole fix.
- **Method**: code-read of `bridge/telegram_relay.py:519-549`, `tools/valor_telegram.py:1042-1044`, `agent/sdk_client.py:1380-1385`. Subprocess env-inheritance trace.
- **Finding**: Tier 1 succeeds in the common case — `cmd_send` reads `AGENT_SESSION_ID` from env, and `subprocess.run()` without explicit `env=` propagates the parent's `os.environ` (including the SDK client's injected `AGENT_SESSION_ID`). Tier-3 ambiguity only triggers under a narrow concurrence (multiple sessions in same chat + manual CLI send within window + Tier 2 skips due to `cli-` prefix) — rare in practice; no log evidence in `logs/`. Out of scope for this plan; warrants a separate follow-up issue.
- **Confidence**: medium-high (no production telemetry to confirm rarity, but code path analysis is conclusive)
- **Impact on plan**: Out of scope. Add to No-Gos. File a separate follow-up issue: `Tier-3 owner-session resolution silently picks newest of multiple candidates (instrumentation + tiebreaker)`.

## Data Flow

End-to-end trace of a session that fires both a mid-session send and the completion runner:

1. **Entry point**: User sends a Telegram message → bridge enqueues `AgentSession` (PM, `session_type="pm"`).
2. **Worker spawns CLI harness**: `agent/sdk_client.py:1380-1385` injects `VALOR_SESSION_ID` and `AGENT_SESSION_ID` into the subprocess env.
3. **PM dispatches sub-skill** (e.g. `/sdlc` or `/do-docs`) which calls `valor-telegram send "status update"`.
4. **`tools/valor_telegram.py::cmd_send`** reads `AGENT_SESSION_ID` from env (line 1042-1044), sets `payload["owner_agent_session_id"]`, publishes to `telegram:outbox:{session_id}`.
5. **`bridge/telegram_relay.py::_resolve_owner_session`** Tier-1 resolves to the parent session; relay appends `{direction: "out", sender, content, message_id, ts}` entry to `parent.chat_message_log` (PR #1244 plumbing). The send also lands in `parent.recent_sent_drafts` via the post-send hook in `TelegramRelayOutputHandler.send` (`agent/output_handler.py:407`+).
6. **Sub-skill returns**; PM continues; eventually all stages complete.
7. **Completion runner fires** (`agent/session_completion.py::_deliver_pipeline_completion`) — runs Pass 1 + Pass 2 drafter, produces `final_text`.
8. **NEW**: Pass 1 prompt is built with a *"messages already sent this session"* block extracted from `parent.chat_message_log` outbound entries (mirroring `bridge/message_drafter.py:1246-1276` shape). Drafter is instructed to acknowledge what was sent and produce only materially-new content.
9. **NEW**: After Pass 2 produces `final_text`, a completion-specific suppression check runs:
   - Call `should_suppress(final_text, extract_artifacts(final_text), parent.recent_sent_drafts, expectations=None, session_status=None)` with a per-call threshold of `0.75`.
   - If verdict is `suppress`: queue 👀 reaction on `telegram_message_id`, log the decision, skip `send_cb`, set `delivery_attempted=False`.
   - If verdict is `send` AND best Jaccard ∈ [0.55, 0.75): escalate to a Haiku judge (RTR pattern). Judge returns `restate` (suppress) or `new` (send).
   - Otherwise proceed with `send_cb` as today.
10. **`finally`** block at line 771 runs `finalize_session(parent, "completed", ...)` regardless. Session terminates cleanly.

## Architectural Impact

- **New dependencies**: None. Reuses `bridge/redundancy_filter.should_suppress`, `bridge/message_drafter.extract_artifacts`, `bridge/read_the_room` Haiku-judge pattern, `tools/react_with_emoji` (or its underlying outbox publish).
- **Interface changes**: None. `_deliver_pipeline_completion` signature unchanged; `should_suppress` signature unchanged. `recent_sent_drafts` and `chat_message_log` already exist on `AgentSession`.
- **Coupling**: Modest increase — `agent/session_completion.py` gains imports from `bridge/redundancy_filter`, `bridge/message_drafter`, and (conditionally) `bridge/read_the_room`. Acceptable: these are shared bridge-layer utilities, and this is the same layering as `agent/output_handler.py` already does.
- **Data ownership**: Unchanged. Completion runner remains sole owner of the final-emit decision and parent-session `completed` transition (per #1058).
- **Reversibility**: High. The suppression check is a single conditional branch around the existing `send_cb` block. Removing the feature is a clean revert.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in for one open question

**Interactions:**
- PM check-ins: 1 (confirm hybrid Q1+Q2 approach is what's wanted; the user's instruction said "post-draft suppression," but Tom's earlier framing implied context-injection — plan proposes both)
- Review rounds: 1 (code review)

Why Medium and not Small: the core suppression logic is ~30 LoC, but the Haiku-judge integration in the borderline band, the chat-log prompt injection, and the integration test all add real surface area. Estimated 80-150 LoC + 4-5 tests.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` set | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Haiku judge requires Anthropic SDK credentials |

Run all checks: `python scripts/check_prerequisites.py docs/plans/dedupe-completion-emit.md`

## Solution

### Key Elements

- **Chat-log prompt injection** (Pass 1): Append a "messages already sent this session" block (drawn from `parent.chat_message_log` outbound entries) to the existing `_COMPLETION_PROMPT_PREFIX + summary_context` prompt. Mirrors `bridge/message_drafter.py:1246-1276` shape. Lets the drafter elide / shrink / acknowledge naturally.
- **Post-draft hybrid suppression** (after Pass 2): Bigram-Jaccard pre-check at threshold `0.75` (env-tunable). Suppress on J ≥ 0.75. Send on J < 0.55. Escalate to Haiku judge in band [0.55, 0.75).
- **Haiku judge**: Single Haiku call with `tool_use` returning `{action: "restate" | "new"}`. Fail-open contract per RTR pattern. Prompt includes `prior_timestamp` so the judge can weight stale-vs-fresh context.
- **Suppress-fallback**: Queue 👀 reaction on `telegram_message_id` via existing `tools/react_with_emoji` infrastructure. Silent fallback (log + skip send) when `telegram_message_id` is None.
- **Documentation update**: Document that `response_delivered_at = None` is intentional when the completion runner suppresses (so dashboards/analytics treat it as an intentional signal, not failure).

### Flow

End-to-end user-visible flow:

**Mid-session sub-skill send** → User sees status update with content X → **PM session continues** → All stages complete → **Completion runner fires** → Pass 1 sees "you already sent X" in prompt → Drafts a focused "all done" message OR returns minimal acknowledgement → Pass 2 reviews → Post-draft filter compares to `recent_sent_drafts` → If duplicate of X: queue 👀 on user's last message → User sees only one message + reaction.

**Counterexample (legitimate non-duplicate)**: Mid-session send covers partial content → completion summary adds new outcomes → Post-draft Jaccard < 0.55 (or Haiku judges "new") → emit fires → user sees both messages.

### Technical Approach

- **Module placement**: New private helpers live inside `agent/session_completion.py` initially. If the suppression block grows past ~80 LoC, extract to `agent/completion_suppression.py`. Keep coupling local until the file requires it.
- **Hybrid filter call**: Reuse `bridge/redundancy_filter.should_suppress` with two adjustments:
  1. Pass `session_status=None` to bypass `_TERMINAL_STATUSES` exemption.
  2. Wrap the call to use a per-completion threshold (default `0.75`, env `DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD`). Implementation option: temporarily monkey-patch `REDUNDANCY_THRESHOLD` (rejected — global module state, race-prone), or add a `threshold: float | None = None` parameter to `should_suppress` (preferred — additive, defaults to existing constant).
- **Haiku escalation**: Copy the call shape from `bridge/read_the_room.py:343-518` (`run_read_the_room` Haiku invocation with `tool_use`). New helper `_judge_completion_novelty(prior_text, prior_ts, draft_text) -> bool` returns `True` to suppress, `False` to send. Fail-open: any exception → `False` (deliver).
- **Chat-log block format**: Mirror `bridge/message_drafter.py:1262-1276` line shape: `"\n\nYou already sent these messages in this thread (do not repeat them — only add materially-new context):\n" + "\n".join(f"[out] {sender}: {content}" for entry in outbound_entries[-N:])`. Cap N at 5 to bound prompt growth.
- **Reaction queueing**: Use existing path. Either import `tools/react_with_emoji::queue_reaction` directly (if a clean function exists) or replicate its outbox-publish snippet. Reaction queue key: `telegram:outbox:{parent.session_id}`.
- **Logging**: Every suppression decision logs `[completion-runner] Suppressed final emit for {parent_id} (jaccard=X.XX, judge={judge_verdict_or_n/a})` so operators can audit. Send decisions log similarly (`reason=below_threshold` or `reason=judge_says_new`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `should_suppress` already has fail-open contract — any unhandled exception returns `SuppressionVerdict(action="send", reason="filter_error")`. Test asserts the completion runner inherits this: a malformed `chat_message_log` entry MUST NOT crash delivery.
- [ ] Haiku judge MUST fail-open (deliver) on any exception (timeout, API error, malformed response). Test mocks Haiku to raise `TimeoutError` — verify `final_text` is delivered.
- [ ] If `tools/react_with_emoji::queue_reaction` raises (e.g. Redis unavailable), the suppression branch logs a warning and the session still finalizes cleanly — test asserts no crash.

### Empty/Invalid Input Handling
- [ ] Empty `chat_message_log`: prompt-injection helper produces empty string (no "you already sent" block); suppression filter returns `no_baseline` and delivers normally.
- [ ] `chat_message_log` with no outbound entries (only inbound): same as empty — no-op.
- [ ] `final_text` is empty: existing sentinel/fallback path takes over; suppression check should NOT run on the sentinel string. Add an early-return guard.
- [ ] `telegram_message_id` is None on suppression: fall through to silent + log warning; verify no exception.

### Error State Rendering
- [ ] When suppressed: verify the user sees exactly the 👀 reaction on their anchor message and no text. Integration test inspects the outbox for both message and reaction payloads.
- [ ] When delivered (judge says "new"): verify the auto-emit fires as today and `response_delivered_at` is stamped.

## Test Impact

Existing tests that must be updated, plus new tests to add. Each item carries a disposition.

- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE: add `recent_sent_drafts` and `chat_message_log` setup to existing fixtures. Most existing tests should still pass (no-suppression path unchanged when those fields are empty).
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_suppressed_when_final_text_matches_recent_sent_draft` — ADD: parent has a recent outbound draft; final_text matches at J ≥ 0.75; verify `send_cb` not called, 👀 reaction queued, `response_delivered_at` stays None, `finalize_session("completed")` still runs.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_delivered_when_final_text_unique` — ADD: parent has recent outbound drafts; final_text unrelated; verify normal delivery.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_judge_called_in_borderline_band` — ADD: J ∈ [0.55, 0.75); mock Haiku judge to return "restate" → suppressed; flip to "new" → delivered.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_filter_failopen_on_exception` — ADD: malformed `recent_sent_drafts` entry; verify delivery proceeds + warning logged.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_silent_fallback_when_telegram_message_id_none` — ADD: suppression decision + None anchor → no reaction, no send, log warning, finalize cleanly.
- [ ] `tests/unit/test_message_drafter_chat_log.py` — UPDATE: confirm regular drafter's chat-log injection format remains the canonical reference. Completion runner injection mirrors but does NOT share code (per spike-1 / spike-4 rationale: keep the two surfaces decoupled).
- [ ] `tests/integration/test_chat_message_log_e2e.py` — REPLACE: extend the existing end-to-end fixture. (a) PM session, (b) mid-session `valor-telegram send` writes to `chat_message_log`, (c) completion runner fires, (d) only one user-visible message is emitted (or message + reaction). Asserts on the recorded outbox payloads.
- [ ] `tests/unit/test_redundancy_filter.py::TestTerminalStatus` — NO CHANGE. Plan does NOT modify `_TERMINAL_STATUSES`. Existing exemption stays valid for the regular `TelegramRelayOutputHandler.send` path.
- [ ] `tests/unit/test_redundancy_filter.py` — UPDATE if the `threshold: float | None = None` parameter is added to `should_suppress`: add one test case asserting the per-call threshold overrides the env default.

## Rabbit Holes

- **Modifying `_TERMINAL_STATUSES` exemption.** Tempting because it looks like the "obvious" fix in the issue. Don't — the exemption is correct for in-session terminal transitions on the regular drafter path; mixing the two semantic concerns will leak bugs across both surfaces. Spike-4 rationale.
- **Refactoring the redundancy filter to push exemption logic to callers.** The "right" architectural move if there were multiple call sites needing different policies. There aren't — only `output_handler.py` uses it today, and this plan adds one more (with `session_status=None` as the explicit override). Defer until a third caller appears.
- **Tier-3 owner-session resolution fix.** Real bug, but rare and orthogonal. Spike-5 rationale. File separately.
- **Skill-level "stop calling `valor-telegram send` mid-session"** approach. Tom explicitly rejected in the issue thread in favor of the hook/drafter-level cut. Don't reopen.
- **Replacing the bigram filter with pure-LLM judging.** Throws away the deterministic-cost win; adds latency to every completion. Hybrid is the design.
- **Wiring this into the regular drafter's chat-log injection at `bridge/message_drafter.py:1246-1276`.** The completion runner does NOT use `_build_draft_prompt()` — it calls `get_response_via_harness` directly with a hardcoded prompt prefix (this is by design per #1058 / S-1 / ADV-2 notes at `agent/session_completion.py:553-558`). Re-routing it to share code is a much bigger refactor than this issue warrants.

## Risks

### Risk 1: Haiku judge has a false-negative bias and suppresses legitimate completions
**Impact:** User loses the "deployment happened" signal in the example from spike-2 (J≈0.68 borderline; judge says "restate"; deployment news is silently dropped). Operator confusion + loss of trust.
**Mitigation:** Pass `prior_timestamp` and the time delta into the Haiku prompt so it can bias toward "new" when the prior message is older than ~2 minutes (user has likely scrolled away). Log every judge decision with full inputs so post-incident inspection is straightforward. Conservative initial threshold of 0.75 (down from a hypothetical 0.80) keeps the borderline band wide and the bigram-only suppress path narrow.

### Risk 2: Latency added to user-visible completion path
**Impact:** Borderline cases (~5-15% of completions, estimated) pay an extra ~1-2 seconds for the Haiku call. Most completion summaries are not borderline and pay zero extra.
**Mitigation:** Run the Haiku judge with a 3-second timeout (matching RTR's `_RTR_TIMEOUT_SECONDS`). Fail-open on timeout — deliver the message rather than block. Document the latency profile in the feature doc.

### Risk 3: `chat_message_log` is stale at completion time (race with bridge ack)
**Impact:** If the bridge hasn't yet flushed the mid-session send to `parent.chat_message_log` when the completion runner reads it, suppression misses.
**Mitigation:** `chat_message_log` is appended synchronously by the relay during outbox drain (`bridge/telegram_relay.py:519-528` Tier-1 path). The relay completes the append before publishing the next outbox event. Completion-runner read happens after PM session signals completion, which is well after the mid-session send has been ack'd. Race is theoretically possible but practically negligible. If it bites: add a 100ms re-read after first-empty result. Defer until evidence of the bug.

### Risk 4: The terminal-status exemption disagreement leaks to a future maintainer
**Impact:** A future engineer reads `bridge/redundancy_filter.py:161-162` ("final message must always deliver") and wonders why the completion runner is suppressing. Confusion ladders to wrong fixes.
**Mitigation:** Inline comment at the call site in `agent/session_completion.py` explicitly stating: *"We pass `session_status=None` to bypass `_TERMINAL_STATUSES` exemption — that exemption applies to in-session drafts via `TelegramRelayOutputHandler.send`. The completion runner's auto-emit is a different surface (out-of-band post-session emit) where suppression IS desired. See plan `docs/plans/dedupe-completion-emit.md` and #1262."* Plus a single line in the feature doc.

### Risk 5: Suppression silently breaks a downstream consumer of `response_delivered_at`
**Impact:** Dashboard or analytics expects every completed session to have `response_delivered_at` set; treats `None` as a failure.
**Mitigation:** Grep all consumers of `response_delivered_at` before shipping. Update each to treat `None` post-completion as "intentional silent suppression" rather than failure. Documentation task explicitly covers this.

## Race Conditions

### Race 1: chat_message_log read-after-write between mid-session send and completion runner
**Location:** `agent/session_completion.py:551` (new context-injection read) reads `parent.chat_message_log`, which is written by `bridge/telegram_relay.py:519-528` in the relay drain loop.
**Trigger:** Mid-session `valor-telegram send` published to outbox; relay processes the send; completion runner fires before the relay's chat-log append commits to Redis.
**Data prerequisite:** All outbound entries from this session that have completed sending (i.e. been ack'd by the relay) MUST be visible in `parent.chat_message_log`.
**State prerequisite:** PM session signals "completion ready" only after its last sub-skill subprocess returns. The sub-skill subprocess returns only after `valor-telegram send` writes to outbox AND the publish completes. Relay's outbox-drain loop processes the publish and writes `chat_message_log` synchronously. By construction, the relay's append precedes the PM session's completion signal.
**Mitigation:** The architecture already enforces this ordering via the synchronous outbox-drain → chat-log-append sequence in `_resolve_owner_session`. No new mitigation needed. If the assumption breaks (e.g., relay decoupled to be async): add a re-read with 100ms backoff.

### Race 2: recent_sent_drafts read-after-write
**Location:** Same as Race 1 — `parent.recent_sent_drafts` is appended by `agent/output_handler.py:407+` after each successful send.
**Trigger:** Same as Race 1.
**Mitigation:** Same. Both `chat_message_log` and `recent_sent_drafts` are written by the relay/handler synchronously during outbox drain.

### Race 3: Two concurrent completion runners for sibling PM sessions
**Trigger:** Two PM sessions in the same chat, both completing within seconds of each other. Each suppression check sees the other's `recent_sent_drafts`? No — they read their OWN `parent.recent_sent_drafts`, which is session-scoped. Not a race.
**Mitigation:** None needed. Confirmed not a race.

## No-Gos (Out of Scope)

- **Tier-3 owner-session resolution fix.** Spike-5 finding: rare in practice, orthogonal to this fix. File a separate follow-up issue: *"Tier-3 owner-session resolution silently picks newest of multiple candidates — add WARNING log when len(candidates) > 1 and consider oldest-first tiebreaker"*.
- **Modifying `bridge/redundancy_filter.py::_TERMINAL_STATUSES` or its exemption logic.** The exemption is correct for the in-session-drafter path it was designed for. This plan adds a new caller that explicitly opts out via `session_status=None`.
- **Refactoring `_deliver_pipeline_completion` to share `_build_draft_prompt()` with the regular drafter.** The completion runner intentionally does NOT use the regular drafter pipeline (per #1058 / S-1 / ADV-2 architecture). Don't reroute.
- **Skill-level prevention** (modifying `/do-docs`, `/sdlc`, etc. to not call `valor-telegram send` when a bridge session is active). Tom explicitly rejected in favor of hook/drafter-level cut. Drafter-level fix is durable across all current and future sub-skills.
- **Synthetic CLI-ID rename in `cmd_send`.** Adjacent bug worth its own issue; the data needed for *this* issue's fix already lands correctly when `AGENT_SESSION_ID` is set, which it is for sub-skill Bash calls during a session.
- **Completion-runner Ollama fallback (#1137 territory).** Pass-1 / Pass-2 degraded-fallback is unchanged by this plan.
- **Feature flag / parallel-run / coexistence shim.** Per memory `feedback_no_parallel_migrations` — full cutover. The new behavior replaces the old; no env gate, no opt-in toggle.

## Update System

**No update system changes required** — fix is internal to the worker process. No new dependencies, no new config files, no migration steps. The `DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD` env var has a sensible default (`0.75`); operators can override in `.env` if needed but the default is the desired production value.

## Agent Integration

**No agent integration required** — fix is bridge/worker-internal:
- No new CLI entry point in `pyproject.toml [project.scripts]`.
- No new MCP server registration.
- The bridge does not need to import or call any new code directly.
- The completion runner is invoked by the worker as part of the existing session-completion lifecycle; this plan modifies its internals only.

The integration test at `tests/integration/test_chat_message_log_e2e.py` already exercises the agent → bridge → relay → chat_message_log path; the REPLACE entry in Test Impact extends it to assert single-user-visible-message at session end.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-dev-session-architecture.md` (or whichever doc owns the completion-runner narrative) with a new section: *"Mid-session-send-aware completion suppression"*. Document: (a) why the runner now reads `chat_message_log`, (b) when it suppresses, (c) what the user sees on suppression (👀 reaction, no text), (d) that `response_delivered_at = None` is intentional after suppression.
- [ ] Update `docs/features/README.md` index table only if a new top-level feature doc is added (otherwise the existing PM completion-runner doc gets the section).
- [ ] Add a one-paragraph callout to `bridge/redundancy_filter.py` module docstring (the *## Suppression contract* section): note that the completion runner is now a second consumer of `should_suppress`, calling it with `session_status=None` to bypass `_TERMINAL_STATUSES`. Cross-reference this plan.

### External Documentation Site
N/A — this repo has no external docs site.

### Inline Documentation
- [ ] Inline comment at the new suppression-check call site in `agent/session_completion.py` explaining the `session_status=None` choice (per Risk 4 mitigation).
- [ ] Docstring on the new `_judge_completion_novelty` helper documenting fail-open contract and timeout.

## Success Criteria

- [ ] When a sub-skill posts content X via `valor-telegram send` mid-session and the completion runner's drafter would otherwise post a reformatted version of X at session-end, the user receives only one message (or one message + a 👀 reaction).
- [ ] The completion runner's prompt assembly reads from `parent.chat_message_log` recent outbound entries and exposes them to the drafter — verifiable by inspecting `agent/session_completion.py` and the prompt sent to the harness.
- [ ] The post-draft suppression check runs against `parent.recent_sent_drafts` with `session_status=None` — verifiable by code inspection.
- [ ] The terminal-status exemption in `bridge/redundancy_filter.py` is unchanged. New behavior is documented in this plan and inline at the call site.
- [ ] Regression test in `tests/integration/test_chat_message_log_e2e.py` exercises the full flow and asserts single user-visible message (or message + reaction) for the duplicate-content case.
- [ ] No regression to legitimate non-duplicate completion summaries — when mid-session sends covered partial content and the completion adds new info, the auto-emit still fires.
- [ ] `response_delivered_at = None` after suppression is documented in the affected feature doc.
- [ ] All new unit tests in `tests/unit/test_deliver_pipeline_completion.py` pass.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (suppression-core)**
  - Name: `suppression-builder`
  - Role: Implement context-injection at Pass 1 prompt, post-draft `should_suppress` call, Haiku judge in borderline band, and 👀 reaction fallback in `agent/session_completion.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (filter-parameter)**
  - Name: `filter-param-builder`
  - Role: Add optional `threshold: float | None = None` parameter to `bridge/redundancy_filter.should_suppress` (additive, defaults to existing constant). Single small change to support per-call threshold without monkey-patching module state.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `completion-test-engineer`
  - Role: Implement the new and updated tests in `tests/unit/test_deliver_pipeline_completion.py` and `tests/integration/test_chat_message_log_e2e.py` per the Test Impact section.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `completion-validator`
  - Role: Verify all success criteria, run the full unit + integration test slice, confirm no regressions to `tests/unit/test_redundancy_filter.py` or `tests/unit/test_message_drafter_chat_log.py`. Inspect logs from a manual smoke test for the suppression-decision log line.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `completion-documentarian`
  - Role: Update `docs/features/pm-dev-session-architecture.md` (or owning doc) with the suppression behavior section. Update `bridge/redundancy_filter.py` module docstring to note the second consumer.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard list — see PLAN_TEMPLATE.md for the full inventory. This plan uses Tier 1 only.)

## Step by Step Tasks

### 1. Add optional threshold parameter to should_suppress
- **Task ID**: build-filter-param
- **Depends On**: none
- **Validates**: `tests/unit/test_redundancy_filter.py` (extend with one new test case asserting per-call threshold overrides env default)
- **Informed By**: spike-2 (hybrid scoring needs per-call threshold without global state)
- **Assigned To**: filter-param-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `threshold: float | None = None` parameter to `should_suppress(...)` in `bridge/redundancy_filter.py`.
- Default to `REDUNDANCY_THRESHOLD` when None (preserves all existing call-site behavior).
- Plumb through `_should_suppress_inner`.
- Add one unit test in `tests/unit/test_redundancy_filter.py` asserting per-call threshold overrides default.

### 2. Implement context-injection + post-draft suppression in completion runner
- **Task ID**: build-suppression-core
- **Depends On**: build-filter-param
- **Validates**: `tests/unit/test_deliver_pipeline_completion.py` (existing tests still pass; new tests added in step 4 will exercise the new behavior)
- **Informed By**: spike-1 (hybrid intercept), spike-3 (👀 reaction fallback), spike-4 (`session_status=None` bypass)
- **Assigned To**: suppression-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_completion.py::_deliver_pipeline_completion`, before the existing prompt assembly at line 551:
  - Read `parent.chat_message_log`, filter to outbound entries from this session, take last 5.
  - Build a `chat_log_block` string mirroring `bridge/message_drafter.py:1262-1276` shape.
  - Append the block to `summary_context` before truncation.
- After `final_text` is finalized (line 691) and before the `send_cb` call (line 694):
  - Import `bridge.redundancy_filter.should_suppress` and `bridge.message_drafter.extract_artifacts`.
  - Read `parent.recent_sent_drafts` (already populated by the relay).
  - Call `should_suppress(final_text, extract_artifacts(final_text), recent_sent_drafts, expectations=None, session_status=None, threshold=float(os.environ.get("DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD", "0.75")))`.
  - If verdict.action == "suppress": queue 👀 reaction on `telegram_message_id` (use existing `tools/react_with_emoji` infrastructure or replicate its outbox-publish snippet); log decision; set `delivery_attempted = False`; skip `send_cb`.
  - If verdict.action == "send" AND `verdict.jaccard ∈ [0.55, 0.75)`: call new `_judge_completion_novelty(prior_text, prior_ts, final_text)` helper. If returns True (restate): suppress as above. If False (new): proceed with `send_cb`.
  - Else: proceed with `send_cb` as today.
- Add `_judge_completion_novelty` helper to `agent/session_completion.py`:
  - Single Haiku call with `tool_use` returning `{action: "restate" | "new"}`. Pattern copied from `bridge/read_the_room.py:343-518`.
  - Prompt includes `prior_text`, `prior_ts` (as relative time delta), and `draft_text`.
  - 3-second timeout; fail-open (return False on any exception).
- Inline comment at the suppression-check call site explaining `session_status=None` (per Risk 4).
- If `telegram_message_id is None` on the suppression branch: log warning, no reaction, silent fall-through.
- Guard the entire suppression block with `try/except` that logs and falls through to delivery on any unhandled exception.

### 3. Validate suppression-core implementation
- **Task ID**: validate-suppression-core
- **Depends On**: build-suppression-core
- **Assigned To**: completion-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all code paths described in step 2 are present.
- Run `pytest tests/unit/test_deliver_pipeline_completion.py -x -q` — existing tests must still pass.
- Run `pytest tests/unit/test_redundancy_filter.py -x -q` — must pass with the new threshold parameter test.
- Confirm no modification to `bridge/redundancy_filter.py::_TERMINAL_STATUSES`.
- Confirm inline comment at suppression call site.

### 4. Implement new and updated tests
- **Task ID**: build-tests
- **Depends On**: build-suppression-core
- **Validates**: `tests/unit/test_deliver_pipeline_completion.py`, `tests/integration/test_chat_message_log_e2e.py`
- **Informed By**: spike-1, spike-2, spike-3
- **Assigned To**: completion-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true (with step 5)
- Implement all ADD entries in Test Impact:
  - `test_completion_suppressed_when_final_text_matches_recent_sent_draft`
  - `test_completion_delivered_when_final_text_unique`
  - `test_completion_judge_called_in_borderline_band` (with mocked Haiku)
  - `test_completion_filter_failopen_on_exception`
  - `test_completion_silent_fallback_when_telegram_message_id_none`
- REPLACE the integration test in `tests/integration/test_chat_message_log_e2e.py` per Test Impact.
- UPDATE existing fixtures in `test_deliver_pipeline_completion.py` to include `recent_sent_drafts` and `chat_message_log` (defaults to empty for unaffected tests).
- All tests use real Popoto Redis fixtures; only Haiku call is mocked.

### 5. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-suppression-core
- **Assigned To**: completion-documentarian
- **Agent Type**: documentarian
- **Parallel**: true (with step 4)
- Update `docs/features/pm-dev-session-architecture.md` with new section *"Mid-session-send-aware completion suppression"* covering: why runner reads chat_message_log, suppression decision, 👀 reaction behavior, intentional `response_delivered_at = None`.
- Add callout to `bridge/redundancy_filter.py` module docstring noting the second consumer.
- Confirm `docs/features/README.md` index entry remains accurate.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-suppression-core, build-tests, document-feature
- **Assigned To**: completion-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all checks in the Verification table.
- Verify all Success Criteria checkboxes.
- Inspect logs from a single manual smoke run (mid-session send + completion runner fire) — confirm the new log line appears with expected fields.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_deliver_pipeline_completion.py tests/unit/test_redundancy_filter.py tests/unit/test_message_drafter_chat_log.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_chat_message_log_e2e.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_completion.py bridge/redundancy_filter.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_completion.py bridge/redundancy_filter.py` | exit code 0 |
| Terminal-status exemption untouched | `grep -n '_TERMINAL_STATUSES = frozenset' bridge/redundancy_filter.py` | output contains `frozenset({"completed", "failed", "blocked"})` |
| Suppression call site exists | `grep -n 'session_status=None' agent/session_completion.py` | output > 0 |
| Inline rationale present | `grep -n '_TERMINAL_STATUSES' agent/session_completion.py` | output contains comment line referencing this plan or #1262 |
| Doc updated | `grep -l 'Mid-session-send-aware completion suppression' docs/features/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Hybrid intercept (Q1) vs single cut.** The user's invocation explicitly named "post-draft suppression," but Tom's earlier message in the Telegram thread asked for the *drafter to see* what was sent (= context-injection). This plan proposes both — context-injection at Pass 1 plus post-draft suppression as backstop. Confirm both are wanted. If only one: post-draft suppression is the deterministic-cost choice; context-injection is the lighter-touch choice that may not catch LLM-disobeys-instructions cases.

2. **Suppression-fallback when `telegram_message_id is None`.** Plan proposes silent fall-through (skip send, log warning). Alternative: deliver the auto-emit anyway (acknowledge that no anchor → no reaction → user gets nothing → fall back to text). Which is preferred?

3. **Per-completion threshold default.** Plan proposes `0.75` (env-tunable via `DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD`). The existing in-session threshold is `0.65`. Confirm 0.75 is the right starting value, or specify a different default. Higher = fewer suppressions (false-negative risk: user sees duplicates); lower = more suppressions (false-positive risk: legit completion summaries get dropped).

4. **Helper module placement.** New helpers live in `agent/session_completion.py` initially; extract to `agent/completion_suppression.py` if the block exceeds ~80 LoC. Confirm OK or pre-decide module layout now.
