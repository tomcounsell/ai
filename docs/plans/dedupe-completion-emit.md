---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-05-03
tracking: https://github.com/tomcounsell/ai/issues/1262
last_comment_id:
revision_applied: true
revision_applied_date: 2026-05-04
prior_critique_artifact_hash: sha256:75f2ad0276272d3d14f38204ad6c62fe3a03a3ccba438e5ec2e3c63b70b2827b
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

**Notes:** All cited file:line pointers match current HEAD within ±2 lines. No semantic drift.

**Revision (2026-05-04) re-check:** Re-verified the load-bearing assumption that `recent_sent_drafts` is *not* populated by Path B. Confirmed via `grep -rn "recent_sent_drafts" agent/ bridge/ tools/`: only writers are `models/agent_session.py:1519` (the `record_recent_sent_draft` helper definition) and `agent/output_handler.py:586` (the sole caller, gated on `session.is_sdlc`). No Path B writer exists. This refutes the prior revision's Data Flow §5 claim and triggered the BLOCKER fix recorded in §Critique Results.

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
- **Finding (revised)**: 👀 reaction is the established project convention (`feedback_emoji_over_acks`, `feedback_reactor_voice_emoji`). Outbox-side infrastructure exists end-to-end: `bridge/telegram_relay.py::_send_queued_reaction` consumes outbox reaction events and dispatches via `set_reaction`; `bridge/response.py::VALIDATED_REACTIONS` includes "👀". Completion runner already receives `telegram_message_id` (anchor message id) as a parameter — no new plumbing on the consumer side. Plan #1205 already shipped this exact pattern for RTR. **Producer-side correction (revision 2026-05-04)**: spike-3 originally claimed `tools/react_with_emoji.py` could be reused as the producer. False — `react(feeling: str)` is CLI-only, reads `TELEGRAM_CHAT_ID` / `TELEGRAM_REPLY_TO` / `VALOR_SESSION_ID` from env vars, and `sys.exit(1)`s on missing values. The actual usable in-process producer is `TelegramRelayOutputHandler._rtr_queue_reaction` at `agent/output_handler.py:763-787` (which builds a payload via `_build_reaction_payload` and rpushes it).
- **Confidence**: high
- **Impact on plan**: On suppress, queue 👀 reaction on `telegram_message_id` via the canonical outbox path — `TelegramRelayOutputHandler._build_reaction_payload` (`agent/output_handler.py:789-820`) + `r.rpush(f"telegram:outbox:{parent.session_id}", json.dumps(payload))` + `r.expire(..., 3600)`. Implementation choice in revised Technical Approach: extract to `bridge/reaction_outbox.py` (preferred) or inline-replicate the snippet. If `telegram_message_id` is None (rare — only when the completion runner was invoked without an anchor), fall through to silent completion + log warning. Never emit text "Done." (violates persona convention).

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
5. **`bridge/telegram_relay.py::_resolve_owner_session`** Tier-1 resolves to the parent session; relay appends `{direction: "out", sender, content, message_id, ts}` entry to `parent.chat_message_log` (PR #1244 plumbing — appended via `await asyncio.to_thread(_append_outbound_chat_log, ...)` after the underlying send succeeds, see `bridge/telegram_relay.py:697`). **The send does NOT land in `parent.recent_sent_drafts`** — that field is populated only by `TelegramRelayOutputHandler.send` (Path A, see `agent/output_handler.py:586`), which Path B `valor-telegram send` bypasses entirely. **This is the load-bearing reason the suppression baseline below must read `chat_message_log`, not `recent_sent_drafts`.**
6. **Sub-skill returns**; PM continues; eventually all stages complete.
7. **Completion runner fires** (`agent/session_completion.py::_deliver_pipeline_completion`) — runs Pass 1 + Pass 2 drafter, produces `final_text`.
8. **NEW**: Pass 1 prompt is built with a *"messages already sent this session"* block extracted from `parent.chat_message_log` outbound entries (mirroring `bridge/message_drafter.py:1246-1276` shape). Drafter is instructed to acknowledge what was sent and produce only materially-new content.
9. **NEW**: After Pass 2 produces `final_text`, a completion-specific suppression check runs against **`chat_message_log` outbound entries** (NOT `recent_sent_drafts`, which Path B does not populate):
   - **Adapter step** (required because shapes differ): build a `chat_log_baseline: list[dict]` by filtering `parent.chat_message_log` to entries with `direction == "out"` from the last `REDUNDANCY_WINDOW_SECONDS` (re-using the existing constant in `bridge/redundancy_filter.py:47`), then mapping each `{direction, sender, content, message_id, ts}` entry to `{ts, text: content, artifacts: extract_artifacts(content)}`. Cap to last 5 entries.
   - Call `should_suppress(final_text, extract_artifacts(final_text), chat_log_baseline, expectations=None, session_status=None, threshold=0.55)` — note the **low** threshold of 0.55, NOT 0.75. Rationale below in §"Two-tier verdict via low-threshold call".
   - If verdict is `suppress` AND `verdict.jaccard >= 0.75` (high-confidence duplicate): queue 👀 reaction on `telegram_message_id`, log the decision, skip `send_cb`, set `delivery_attempted=False`.
   - If verdict is `suppress` AND `0.55 <= verdict.jaccard < 0.75` (borderline): escalate to a Haiku judge (RTR pattern), passing `prior_text=baseline[verdict.matched_index]["text"]` and `prior_ts=baseline[verdict.matched_index]["ts"]`. Judge returns `restate` (suppress as above) or `new` (proceed to send_cb).
   - If verdict is `send` (J < 0.55, OR new artifact present, OR empty baseline): proceed with `send_cb` as today.
10. **`finally`** block at line 771 runs `finalize_session(parent, "completed", ...)` regardless. Session terminates cleanly.

### Two-tier verdict via low-threshold call (load-bearing implementation note)

`SuppressionVerdict` populates `jaccard` and `matched_index` **only when `action == "suppress"`** (`bridge/redundancy_filter.py:218-224`). For `action == "send"` returns — including the `below_threshold` path at line 226 — both fields are `None` (the dataclass defaults at lines 70-71). This means the borderline-band escalation cannot inspect the score on a `send` verdict.

**Solution: call `should_suppress` with `threshold=0.55` (the bottom of the borderline band).** This forces the function into the suppress branch for *any* match J ≥ 0.55, which populates both `jaccard` and `matched_index`. The completion runner then inspects `verdict.jaccard` to decide:

- `verdict.jaccard >= 0.75` → final suppress (high confidence)
- `0.55 <= verdict.jaccard < 0.75` → escalate to Haiku judge (uses `verdict.matched_index` to fetch prior text/ts from the baseline)
- (N/A — verdict.action would have been `send` if J < 0.55 with no other suppression triggers)

For `action == "send"` returns from `should_suppress` (J < 0.55, new artifact present, empty baseline, etc.), the completion runner skips Haiku entirely and proceeds to `send_cb`. The Termination Conditions 1-5 in `should_suppress` (`bridge/redundancy_filter.py:148-162, 197-201`) are unaffected — they all force `send` and are appropriate exits for the completion runner too (empty draft → existing sentinel guard handles; no baseline → nothing to compare; new artifact → real progress, ship it).

This removes the need for any change to `SuppressionVerdict` or `should_suppress` beyond adding the `threshold` parameter (step 1). The completion-specific threshold semantics live entirely in the caller.

## Architectural Impact

- **New dependencies**: None external. Reuses `bridge/redundancy_filter.should_suppress`, `bridge/message_drafter.extract_artifacts`, `bridge/read_the_room` Haiku-judge pattern, and the outbox `rpush` mechanism that `TelegramRelayOutputHandler._rtr_queue_reaction` uses.
- **Interface changes**: One additive parameter — `should_suppress` gains optional `threshold: float | None = None` (defaults preserve all existing behavior). `_deliver_pipeline_completion` signature unchanged. `chat_message_log` already exists on `AgentSession`. **Removed dependency**: `recent_sent_drafts` is NOT read by this plan (corrected in revision); see Solution > Technical Approach for rationale.
- **Optional new module**: `bridge/reaction_outbox.py` exposing `build_reaction_payload(...)` and `queue_reaction(...)` — extracts the shared payload schema currently duplicated between `output_handler.py:789-820` (`_build_reaction_payload`) and the new completion-runner suppression branch. Net code change: zero (extracted, not added) plus one import. Optional — if extraction is rejected during build, the completion runner inline-replicates the snippet with a sync-with-canonical comment.
- **Coupling**: Modest increase — `agent/session_completion.py` gains imports from `bridge/redundancy_filter`, `bridge/message_drafter`, `bridge/read_the_room`, and either `bridge/reaction_outbox` (if extracted) or the redis client. Acceptable: these are shared bridge-layer utilities, and this is the same layering as `agent/output_handler.py` already does.
- **Data ownership**: Unchanged. Completion runner remains sole owner of the final-emit decision and parent-session `completed` transition (per #1058).
- **Reversibility**: High. The suppression check is a single conditional branch around the existing `send_cb` block; the helpers (`_build_completion_baseline`, `_await_outbox_drained`, `_judge_completion_novelty`) are dead code if the branch is removed. Removing the feature is a clean revert. The optional `bridge/reaction_outbox.py` extraction is independently reversible by inlining the helper back into `output_handler.py`.

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

- **Chat-log prompt injection** (Pass 1): Append a "messages already sent this session" block (drawn from `parent.chat_message_log` outbound entries) to the existing `_COMPLETION_PROMPT_PREFIX + summary_context` prompt. Mirrors `bridge/message_drafter.py:1262-1276` shape. Lets the drafter elide / shrink / acknowledge naturally.
- **Post-draft hybrid suppression** (after Pass 2): Bigram-Jaccard pre-check via `should_suppress(..., threshold=0.55)` (low-threshold call — see §Data Flow §"Two-tier verdict via low-threshold call" for the load-bearing rationale around `SuppressionVerdict.jaccard` being unpopulated on `send` verdicts) against an adapter-mapped view of `parent.chat_message_log` outbound entries. **Critical: baseline source is `chat_message_log`, NOT `recent_sent_drafts`** — `recent_sent_drafts` is Path-A-only (`agent/output_handler.py:586`); the duplicate scenario in this issue is Path-B-source, which only populates `chat_message_log`. The completion-specific `0.75` cutoff (env-tunable via `DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD`) is enforced in the caller by inspecting the populated `verdict.jaccard`: `verdict.jaccard >= 0.75` → suppress, `0.55 <= verdict.jaccard < 0.75` → escalate to Haiku, `action == "send"` → ship.
- **Shape adapter**: `_build_completion_baseline(parent, window_seconds=REDUNDANCY_WINDOW_SECONDS, max_entries=5) -> list[dict]` translates `chat_message_log` `{direction, sender, content, message_id, ts}` entries into the `{ts, text, artifacts}` shape expected by `should_suppress`'s `recent_sent_drafts` parameter. Filters to `direction == "out"` and entries inside the time window. Computes `artifacts` via `bridge.message_drafter.extract_artifacts(content)` per entry.
- **Haiku judge**: Single Haiku call with `tool_use` returning `{action: "restate" | "new"}`. Fail-open contract per RTR pattern. Prompt includes `prior_timestamp` so the judge can weight stale-vs-fresh context.
- **Suppress-fallback**: Queue 👀 reaction on `telegram_message_id` via the same outbox path used by `TelegramRelayOutputHandler._rtr_queue_reaction` (`agent/output_handler.py:763-787`) — NOT via `tools/react_with_emoji` (CLI-only, reads chat/reply/session from env vars). Either (a) extract `_build_reaction_payload` from `output_handler.py:789-820` to a new `bridge/reaction_outbox.py` module and call it from both sites, or (b) inline-replicate the 6-line payload + rpush snippet in the completion runner with a comment cross-referencing the canonical site. Silent fallback (log + skip send) when `telegram_message_id` is None.
- **Documentation update**: Document that `response_delivered_at = None` is intentional when the completion runner suppresses (so dashboards/analytics treat it as an intentional signal, not failure).

### Flow

End-to-end user-visible flow:

**Mid-session sub-skill send** → User sees status update with content X → **PM session continues** → All stages complete → **Completion runner fires** → Pass 1 sees "you already sent X" in prompt (sourced from `chat_message_log` outbound entries) → Drafts a focused "all done" message OR returns minimal acknowledgement → Pass 2 reviews → Post-draft filter compares against the same `chat_message_log` outbound baseline (shape-adapted for `should_suppress`) → If duplicate of X: queue 👀 on user's last message → User sees only one message + reaction.

**Counterexample (legitimate non-duplicate)**: Mid-session send covers partial content → completion summary adds new outcomes → Post-draft Jaccard < 0.55 (or Haiku judges "new") → emit fires → user sees both messages.

### Technical Approach

- **Module placement**: New private helpers live inside `agent/session_completion.py` initially. If the suppression block grows past ~80 LoC, extract to `agent/completion_suppression.py`. Keep coupling local until the file requires it.
- **Hybrid filter call**: Reuse `bridge/redundancy_filter.should_suppress` with three adjustments:
  1. Pass `session_status=None` to bypass `_TERMINAL_STATUSES` exemption (per spike-4).
  2. Pass `expectations=None` — Pass 2 returns plain text, not a `MessageDraft`-shaped object, so there is no expectations concept here. (Termination Condition 3 inside `should_suppress` would force `send` if expectations were non-empty; passing `None` keeps the suppression check active.)
  3. Add a `threshold: float | None = None` parameter to `should_suppress` (additive, defaults to `REDUNDANCY_THRESHOLD`) and **call with `threshold=0.55`** — NOT 0.75. The 0.75 completion-specific cutoff is enforced by the caller inspecting `verdict.jaccard` after a `suppress` verdict. This is forced by `SuppressionVerdict.jaccard` being `None` on `send` verdicts (`bridge/redundancy_filter.py:226`) — see §Data Flow §"Two-tier verdict via low-threshold call". Rejected alternatives: (a) monkey-patch `REDUNDANCY_THRESHOLD` — global module state, race-prone; (b) populate `jaccard` on `send` verdicts too — touches `SuppressionVerdict` semantics in a way that affects the existing `output_handler.py` consumer and complicates the existing logger-formatted `reason` string; (c) compute jaccard separately in the completion runner — duplicates `_extract_bigrams` and the windowing logic. The low-threshold-call approach is the smallest delta and keeps `should_suppress` itself unchanged in semantics.
- **Baseline source — `chat_message_log` (NOT `recent_sent_drafts`)**: The `recent_sent_drafts` field is updated only inside `TelegramRelayOutputHandler.send` at `agent/output_handler.py:586` (and only when `session.is_sdlc` is True at `agent/output_handler.py:584`). Path B `valor-telegram send` writes directly to `telegram:outbox:{session_id}` and the relay drains it — neither path touches `recent_sent_drafts`. The relay does, however, append to `chat_message_log` via `_append_outbound_chat_log` at `bridge/telegram_relay.py:697` for both Path A AND Path B sends (Tier-1 resolution covers Path B when `owner_agent_session_id` is in the payload, which `cmd_send` injects from `AGENT_SESSION_ID` at `tools/valor_telegram.py:1042-1044`). Therefore: `chat_message_log` is the only field that captures Path B mid-session sends, and is the only valid suppression baseline for this plan.
- **Shape adapter helper** (new in `agent/session_completion.py`):
  ```python
  def _build_completion_baseline(parent, *, window_seconds=REDUNDANCY_WINDOW_SECONDS, max_entries=5) -> list[dict]:
      """Adapt chat_message_log outbound entries to the should_suppress recent_sent_drafts shape.
      Returns [{ts, text, artifacts}, ...]; empty list if no qualifying entries.
      Fail-open: any exception → []."""
      from bridge.message_drafter import extract_artifacts
      try:
          import time as _t
          now = _t.time()
          entries = parent.chat_message_log or []
          out = []
          for e in entries:
              if not isinstance(e, dict): continue
              if e.get("direction") != "out": continue
              ts = e.get("ts")
              if not isinstance(ts, (int, float)) or now - ts > window_seconds: continue
              content = (e.get("content") or "").strip()
              if not content: continue
              out.append({"ts": ts, "text": content, "artifacts": extract_artifacts(content) or {}})
          return out[-max_entries:]
      except Exception:
          return []
  ```
  Note `REDUNDANCY_WINDOW_SECONDS` is exported by `bridge/redundancy_filter.py:47`.
- **Haiku escalation**: Copy the call shape from `bridge/read_the_room.py:343-518` (`run_read_the_room` Haiku invocation with `tool_use`). New helper `_judge_completion_novelty(prior_text, prior_ts, draft_text) -> bool` returns `True` to suppress, `False` to send. Fail-open: any exception → `False` (deliver).
- **Chat-log block format**: Mirror `bridge/message_drafter.py:1262-1276` line shape: `"\n\nYou already sent these messages in this thread (do not repeat them — only add materially-new context):\n" + "\n".join(f"[out] {sender}: {content}" for entry in outbound_entries[-N:])`. Cap N at 5 to bound prompt growth.
- **Reaction queueing**: `tools/react_with_emoji` is CLI-only — its `react()` function reads `TELEGRAM_CHAT_ID` / `TELEGRAM_REPLY_TO` / `VALOR_SESSION_ID` from the process environment and `sys.exit(1)`s on missing values, so it cannot be called in-process from `agent/session_completion.py`. Use the same outbox path as the existing RTR-suppress branch: `TelegramRelayOutputHandler._build_reaction_payload` (`agent/output_handler.py:789-820`) + `r.rpush(f"telegram:outbox:{parent.session_id}", json.dumps(payload))` + `r.expire(..., 3600)`. Implementation choice: extract `_build_reaction_payload` to a new `bridge/reaction_outbox.py::build_reaction_payload(...)` module function (preferred — single source of truth, removes the existing duplication risk) and call from both `output_handler.py:763-787` (`_rtr_queue_reaction`) and the new completion-runner suppression branch. Backstop: if extraction is rejected during build for scope reasons, inline-replicate the 6-line payload dict + rpush in the completion runner with a comment `# Mirror of TelegramRelayOutputHandler._build_reaction_payload — keep in sync.`
- **Logging**: Every suppression decision logs `[completion-runner] Suppressed final emit for {parent_id} (jaccard=X.XX, judge={judge_verdict_or_n/a})` so operators can audit. Send decisions log similarly (`reason=below_threshold` or `reason=judge_says_new`).
- **`is_sdlc` scope note**: This plan targets SDLC PM sessions where the completion runner fires and a duplicate emit can occur. Non-SDLC PM/teammate sessions don't trigger `_deliver_pipeline_completion` (it's only invoked from the pipeline-completion path), so the suppression logic naturally only runs in scope. No new gating needed; document explicitly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `should_suppress` already has fail-open contract — any unhandled exception returns `SuppressionVerdict(action="send", reason="filter_error")`. Test asserts the completion runner inherits this: a malformed `chat_message_log` entry passed through the adapter MUST NOT crash delivery.
- [ ] Haiku judge MUST fail-open (deliver) on any exception (timeout, API error, malformed response). Test mocks Haiku to raise `TimeoutError` — verify `final_text` is delivered.
- [ ] `_build_completion_baseline` adapter MUST fail-open on any exception (returns `[]`). Test passes a `chat_message_log` containing a non-dict entry; adapter returns `[]`; suppression filter sees no baseline and delivers normally.
- [ ] If the reaction-outbox `rpush` raises (e.g. Redis unavailable), the suppression branch logs a warning and the session still finalizes cleanly — test asserts no crash. (Tests target the new shared helper if extracted to `bridge/reaction_outbox.py`, otherwise the inline-replicated snippet.)
- [ ] If `_await_outbox_drained` raises or times out, the runner proceeds with whatever `chat_message_log` contains; test mocks Redis to raise → wait helper returns `True` (fail-open) → suppression check runs against current state.

### Empty/Invalid Input Handling
- [ ] Empty `chat_message_log`: prompt-injection helper produces empty string (no "you already sent" block); adapter returns `[]`; `should_suppress` returns `send` with reason `no_baseline`; delivery proceeds normally.
- [ ] `chat_message_log` with no outbound entries (only inbound): adapter filters them out → returns `[]` → same path as empty.
- [ ] `chat_message_log` with outbound entries OUTSIDE the `REDUNDANCY_WINDOW_SECONDS` window: adapter filters them out → returns `[]` → same path as empty (stale entries never suppress).
- [ ] `final_text` is empty/sentinel: existing sentinel/fallback path takes over at `agent/session_completion.py:567`; suppression check MUST NOT run on the sentinel string `[completion-runner internal error — no final_text assigned]`. Add an early-return guard checking for the sentinel literal AND `not final_text.strip()`.
- [ ] `telegram_message_id` is None on suppression: fall through to silent + log warning; verify no exception, no reaction queued, `delivery_attempted=False`.

### Error State Rendering
- [ ] When suppressed: verify the user sees exactly the 👀 reaction on their anchor message and no text. Integration test inspects the outbox for both message and reaction payloads.
- [ ] When delivered (judge says "new"): verify the auto-emit fires as today and `response_delivered_at` is stamped.

## Test Impact

Existing tests that must be updated, plus new tests to add. Each item carries a disposition.

- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE: add `chat_message_log` setup to existing fixtures (NOT `recent_sent_drafts` — see plan rationale; that field is Path-A-only). Most existing tests should still pass (no-suppression path unchanged when `chat_message_log` is empty).
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_suppressed_when_final_text_matches_chat_log_outbound` — ADD: parent has a recent outbound entry in `chat_message_log` (simulating a Path B `valor-telegram send`); final_text matches at J ≥ 0.75 after adapter shape-mapping; verify `send_cb` not called, 👀 reaction queued via the canonical reaction-outbox path, `response_delivered_at` stays None, `finalize_session("completed")` still runs.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_delivered_when_final_text_unique` — ADD: parent has recent outbound chat_log entries; final_text unrelated; verify normal delivery.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_baseline_excludes_inbound_entries` — ADD: parent has an inbound (`direction: "in"`) chat_log entry whose content matches `final_text`; baseline adapter filters it out; suppression returns `send` (we never suppress against the user's own message).
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_baseline_excludes_stale_entries` — ADD: parent has outbound chat_log entry with `ts` older than `REDUNDANCY_WINDOW_SECONDS`; baseline adapter filters it out; suppression returns `send`.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_judge_called_in_borderline_band` — ADD: J ∈ [0.55, 0.75); mock Haiku judge to return "restate" → suppressed; flip to "new" → delivered. Test asserts the call to `should_suppress` uses `threshold=0.55` (low cutoff), NOT 0.75, and that the verdict's `jaccard` and `matched_index` are populated and used to fetch prior context for the Haiku call.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_high_confidence_suppress_skips_haiku` — ADD: J ≥ 0.75 (e.g. 0.85). Verify verdict.action == "suppress", verdict.jaccard >= 0.75, suppress branch fires WITHOUT calling Haiku. Mocks Haiku to assert it is NOT called (failing the test if Haiku is invoked).
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_borderline_with_invalid_matched_index_falls_through_to_send` — ADD: defensive case. Construct a `SuppressionVerdict` with `action="suppress", jaccard=0.65, matched_index=99` (out of range vs a 3-entry baseline). Verify warning logged and `send_cb` invoked normally (no Haiku call, no crash).
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_send_verdict_with_new_artifact_proceeds_normally` — ADD: baseline contains an outbound entry with high text similarity but the new draft includes a new commit URL not in the baseline; `should_suppress` returns `action="send", reason="new_artifact"` (per `bridge/redundancy_filter.py:201`); no Haiku call; `send_cb` proceeds.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_adapter_failopen_on_malformed_entry` — ADD: malformed `chat_message_log` entry (non-dict, missing keys); adapter returns `[]`; verify delivery proceeds + warning logged.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_silent_fallback_when_telegram_message_id_none` — ADD: suppression decision + None anchor → no reaction, no send, log warning, finalize cleanly.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_skips_suppression_check_on_sentinel_text` — ADD: `final_text` is the sentinel string from `agent/session_completion.py:567`; suppression check is bypassed; existing sentinel/fallback path takes over.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_outbox_drain_wait_times_out_gracefully` — ADD: mock Redis `llen` to always return 1 (queue never drains); `_await_outbox_drained` returns False after timeout; runner proceeds with whatever's in chat_message_log; verify no crash and warning logged.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_completion_refetches_parent_before_suppression_check` — ADD: in-memory `parent` has stale chat_message_log; `AgentSession.get_by_id` returns a fresh copy with new outbound entry; runner uses fresh copy for suppression baseline.
- [ ] `tests/unit/test_message_drafter_chat_log.py` — UPDATE: confirm regular drafter's chat-log injection format remains the canonical reference. Completion runner injection mirrors but does NOT share code (per spike-1 / spike-4 rationale: keep the two surfaces decoupled).
- [ ] `tests/integration/test_chat_message_log_e2e.py` — REPLACE: extend the existing end-to-end fixture. (a) PM session with `is_sdlc=True`, (b) mid-session `valor-telegram send` writes to `chat_message_log` via Tier-1 owner resolution (NOT `recent_sent_drafts`), (c) completion runner fires, (d) only one user-visible message is emitted (or message + reaction). Asserts on the recorded outbox payloads, not on `recent_sent_drafts`.
- [ ] `tests/unit/test_redundancy_filter.py::TestTerminalStatus` — NO CHANGE. Plan does NOT modify `_TERMINAL_STATUSES`. Existing exemption stays valid for the regular `TelegramRelayOutputHandler.send` path.
- [ ] `tests/unit/test_redundancy_filter.py::test_threshold_parameter_overrides_default` — ADD: add one test case asserting the new `threshold: float | None = None` parameter on `should_suppress` overrides `REDUNDANCY_THRESHOLD` at the call site (passes `threshold=0.75`, asserts borderline 0.70 case is no longer suppressed).
- [ ] `tests/unit/test_reaction_outbox.py` — ADD (only if `_build_reaction_payload` is extracted to `bridge/reaction_outbox.py`): unit test for the extracted function asserting the payload schema is byte-identical to `output_handler.py:789-820`'s output. Skip this test row if the inline-replication option is chosen during build.

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
**Location:** Both new reads (Pass 1 prompt injection AND post-draft suppression) read `parent.chat_message_log`, which is written by `_append_outbound_chat_log` at `bridge/telegram_relay.py:697` from inside the relay's outbox-drain loop.
**Trigger:** Mid-session `valor-telegram send` rpushes to `telegram:outbox:{session_id}` and **returns immediately** (`tools/valor_telegram.py:1049-1052`). The PM session's sub-skill returns; PM proceeds to mark stages complete; completion runner fires — all of this is **independent of and concurrent with** the relay drain loop. There is NO cross-process synchronization that guarantees the relay has called `_append_outbound_chat_log` before the completion runner reads the field.
**Data prerequisite:** Outbound entries from this session that should logically be visible (i.e. were rpushed to the outbox before the runner started) MUST be visible in `parent.chat_message_log` when the runner reads it — OR the runner must handle their absence gracefully without false negatives that re-emit duplicates.
**State prerequisite (CORRECTED — prior revision asserted a non-existent ordering guarantee):** None at the architecture level. The publisher (`cmd_send`) returns after `r.rpush` succeeds; the consumer (relay drain loop) processes outbox events asynchronously in `bridge/telegram_relay.py::run_outbox_consumer`. The only ordering guarantee is FIFO inside a single outbox queue.
**Mitigation (NEW — three-layer defense, no architectural ordering claim):**
  1. **Bounded synchronous wait** before reading `chat_message_log` in the completion runner. Pseudocode (uses the sync `redis.Redis` client wrapped via `asyncio.to_thread` because the codebase has no async-redis client — verified via `grep -rn "redis.asyncio" agent/ bridge/`, returns no matches):
     ```python
     async def _await_outbox_drained(parent, *, timeout_seconds=2.0, poll_interval=0.1):
         """Wait for the parent session's outbox queue to be empty (best effort).
         Returns True if drained, False on timeout. Fail-open: returns True on any exception.
         """
         try:
             import asyncio as _asyncio
             import os
             import time as _time
             import redis  # sync client — codebase has no async-redis usage
             redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
             r = redis.Redis.from_url(redis_url, decode_responses=True)
             deadline = _time.time() + timeout_seconds
             queue_key = f"telegram:outbox:{parent.session_id}"
             while _time.time() < deadline:
                 # llen is sub-millisecond; to_thread keeps the event loop free
                 length = await _asyncio.to_thread(r.llen, queue_key)
                 if length == 0:
                     return True
                 await _asyncio.sleep(poll_interval)
             return False
         except Exception:
             return True  # fail-open — never block delivery on monitoring
     ```
     This bounds the worst-case race to a 2-second wait. The drain loop's per-event latency is sub-100ms in practice, so the typical wait is ≤100ms. If timeout fires: log a warning and proceed with whatever's in `chat_message_log`; the suppression check may miss the most-recent send and emit a duplicate (degraded behavior == today's behavior, not worse). The `redis.Redis.from_url(decode_responses=True)` constructor is the same shape used by `agent/output_handler.py:159` and `bridge/telegram_relay.py:81` — single source-of-truth for the connection string.
  2. **Re-fetch the parent session from Popoto immediately before the suppression check** so a stale in-memory copy from earlier in the runner doesn't shadow a fresh chat_log append: `parent = AgentSession.get_by_id(parent.agent_session_id) or parent`. Pattern matches `models/agent_session.py:1407-1410`'s `append_to_chat_message_log` re-fetch defense against the same hazard.
  3. **Idempotent fallback**: if the post-draft suppression baseline is empty AND the Pass-1 chat-log block was also empty, the runner has no signal and proceeds with `send_cb` as today. This is no worse than current behavior — the duplicate ships, but it's a logged, observable degradation rather than a silent crash.
**Failure mode if mitigation insufficient:** A duplicate emit slips through. Same as today's behavior. The mitigation reduces the race window from "always" to "the rare case where the drain takes >2s AND the wait times out AND a re-fetch still misses the entry." Acceptable degradation gradient.

### Race 2 (REMOVED — was based on the false `recent_sent_drafts`-is-populated assumption)
The prior revision included a Race 2 stating that `recent_sent_drafts` is also written by the relay during outbox drain. That is incorrect — `recent_sent_drafts` is written ONLY by `TelegramRelayOutputHandler.send` (Path A) at `agent/output_handler.py:586`, never by the relay drain loop and never by Path B. Since this plan no longer reads `recent_sent_drafts`, no Race 2 exists.

### Race 3: Two concurrent completion runners for sibling PM sessions
**Trigger:** Two PM sessions in the same chat, both completing within seconds of each other. Each runner reads its OWN `parent.chat_message_log` (session-scoped via `get_by_id(parent.agent_session_id)`). Not a race in the suppression-baseline sense. There IS a UX consideration: the user may see two completion summaries for two distinct sessions in rapid succession, but each is correctly attributed and not a duplicate of the other.
**Mitigation:** None needed for the suppression mechanism. (Cross-session deduplication is out of scope — see No-Gos.)

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
- [x] Updated `docs/features/pm-final-delivery.md` (canonical home for the completion-runner narrative; +134 lines in PR #1278) with the *"Mid-session-send-aware completion suppression"* section covering (a)–(d). `docs/features/pm-dev-session-architecture.md` got a one-paragraph cross-reference.
- [x] Updated `docs/features/README.md` index table — both "PM Final Delivery" and "Drafter Redundancy Suppression" entries now mention the new behavior; no new top-level doc was added.
- [x] Module docstring on `bridge/redundancy_filter.py` extended to document the second consumer's distinct semantics (`session_status=None` bypasses `_TERMINAL_STATUSES`, additive `threshold` parameter). Cross-referenced from `docs/features/drafter-redundancy-suppression.md` (new "Second call site" section), `docs/features/chat-message-log.md` (new "Read Path 2" section), and `docs/features/read-the-room.md` ("Adjacent layers" cross-ref to the second Haiku judge site).

### External Documentation Site
N/A — this repo has no external docs site.

### Inline Documentation
- [x] Inline comment at the new suppression-check call site in `agent/session_completion.py` explaining the `session_status=None` choice (per Risk 4 mitigation).
- [x] Docstring on the new `_judge_completion_novelty` helper documenting fail-open contract and timeout.

## Success Criteria

- [ ] When a sub-skill posts content X via `valor-telegram send` mid-session and the completion runner's drafter would otherwise post a reformatted version of X at session-end, the user receives only one message (or one message + a 👀 reaction).
- [ ] The completion runner's prompt assembly reads from `parent.chat_message_log` recent outbound entries and exposes them to the drafter — verifiable by inspecting `agent/session_completion.py` and the prompt sent to the harness.
- [ ] The post-draft suppression check runs against `parent.chat_message_log` outbound entries (shape-adapted via `_build_completion_baseline`) with `session_status=None` — verifiable by code inspection. **Specifically: the implementation does NOT read from `parent.recent_sent_drafts`** (Path B does not populate that field).
- [ ] The reaction-queueing path uses the canonical outbox mechanism (`_build_reaction_payload` shape) — NOT `tools/react_with_emoji.react()`. Verifiable by code inspection.
- [ ] A bounded outbox-drain wait runs before the suppression baseline read (`_await_outbox_drained`, ≤2s, fail-open) — verifiable by code inspection.
- [ ] The parent session is re-fetched from Popoto immediately before the suppression baseline read — verifiable by code inspection.
- [ ] The terminal-status exemption in `bridge/redundancy_filter.py` is unchanged. New behavior is documented in this plan and inline at the call site.
- [ ] Regression test in `tests/integration/test_chat_message_log_e2e.py` exercises the full flow and asserts single user-visible message (or message + reaction) for the duplicate-content case. The test asserts on `chat_message_log` populating, NOT `recent_sent_drafts`.
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
- **Informed By**: spike-1 (hybrid intercept), spike-3 (👀 reaction fallback), spike-4 (`session_status=None` bypass), revision (Path B does NOT update `recent_sent_drafts`; only `chat_message_log` is the valid baseline)
- **Assigned To**: suppression-builder
- **Agent Type**: builder
- **Parallel**: false

**2a. Add `_build_completion_baseline` helper in `agent/session_completion.py`:**
- Adapter function that translates `parent.chat_message_log` outbound entries to the `should_suppress` `recent_sent_drafts` shape (`{ts, text, artifacts}`).
- Filter to `direction == "out"` AND `ts` within `REDUNDANCY_WINDOW_SECONDS` (imported from `bridge/redundancy_filter.py:47`).
- Cap at last 5 entries.
- Compute `artifacts` per entry via `bridge.message_drafter.extract_artifacts(content)`.
- Fail-open: any exception → return `[]`.
- See exact pseudocode in plan §Solution > Technical Approach.

**2b. Add `_await_outbox_drained` helper in `agent/session_completion.py`:**
- Async function that polls `LLEN telegram:outbox:{parent.session_id}` every 100ms until empty or 2-second timeout.
- Returns True on drain, False on timeout. Fail-open: any exception → return True (don't block delivery on monitoring bugs).
- See exact pseudocode in plan §Race Conditions > Race 1 Mitigation.

**2c. Add `_judge_completion_novelty` helper in `agent/session_completion.py`:**
- Single Haiku call with `tool_use` returning `{action: "restate" | "new"}`. Pattern copied from `bridge/read_the_room.py:343-518`.
- Prompt includes `prior_text`, `prior_ts` (formatted as relative time delta, e.g. "23s ago", "2m ago"), and `draft_text`.
- 3-second timeout; fail-open (return False on any exception → deliver).

**2d. Wire context-injection into Pass 1 prompt assembly:**
- In `_deliver_pipeline_completion`, before the existing prompt assembly (current `agent/session_completion.py:551`):
  - Re-fetch parent: `parent = AgentSession.get_by_id(parent.agent_session_id) or parent` (defends against stale in-memory copy).
  - Build `chat_log_block` from `parent.chat_message_log` outbound entries (last 5, in-window) using the format from `bridge/message_drafter.py:1262-1276`. Mirror shape but DO NOT share code (decoupling rationale per spike-1 / spike-4).
  - Append the block to `summary_context` before the `[:3000]` truncation (or to a separate variable that's concatenated with the prefix — choose whichever preserves the existing literal-`{}`-safety property at line 549-550).

**2e. Wire post-draft suppression check between `final_text` finalization and `send_cb`:**
- After `final_text` is finalized (current `agent/session_completion.py:691`) and before the `send_cb` call (current line 694):
  - Early-return guard: if `final_text` is empty, whitespace-only, OR equals the sentinel `[completion-runner internal error — no final_text assigned]` → skip suppression check, proceed to existing send path.
  - Await `_await_outbox_drained(parent)` (best-effort, bounded to 2s).
  - Re-fetch parent again to capture any chat_log writes that landed during the wait: `parent = AgentSession.get_by_id(parent.agent_session_id) or parent`.
  - Build baseline: `baseline = _build_completion_baseline(parent)`.
  - If `baseline == []`: skip suppression (no signal); proceed to existing send path.
  - Read the high-confidence cutoff from env once: `HIGH_CUTOFF = float(os.environ.get("DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD", "0.75"))` and the low cutoff from a module constant: `LOW_CUTOFF = 0.55` (sourced from §Solution > Two-tier verdict; document inline).
  - **Call `should_suppress` with the LOW threshold** (not the high one — see §Data Flow §"Two-tier verdict"):
    ```python
    verdict = should_suppress(
        final_text,
        extract_artifacts(final_text),
        baseline,
        expectations=None,       # Pass 2 returns plain text — no expectations concept here
        session_status=None,     # Bypass _TERMINAL_STATUSES exemption (Risk 4 / spike-4)
        threshold=LOW_CUTOFF,    # Low threshold so verdict.jaccard is populated for the borderline-band check below
    )
    ```
  - **Branch on the verdict shape**:
    - If `verdict.action == "suppress"` AND `verdict.jaccard is not None` AND `verdict.jaccard >= HIGH_CUTOFF`: high-confidence duplicate. Queue 👀 reaction on `telegram_message_id` via the canonical reaction-outbox path (see step 2f); log `[completion-runner] Suppressed final emit for {parent_id} (jaccard={verdict.jaccard:.2f}, judge=n/a, decision=high_confidence)`; set `delivery_attempted = False`; skip `send_cb`.
    - If `verdict.action == "suppress"` AND `verdict.jaccard is not None` AND `LOW_CUTOFF <= verdict.jaccard < HIGH_CUTOFF`: borderline. Resolve prior context defensively: `idx = verdict.matched_index; prior = baseline[idx] if (idx is not None and 0 <= idx < len(baseline)) else None`. If `prior is None`: log warning `[completion-runner] borderline verdict but matched_index out of range; defaulting to send`, proceed to `send_cb`. Otherwise: call `_judge_completion_novelty(prior_text=prior["text"], prior_ts=prior["ts"], draft_text=final_text)`. If True (restate): suppress as above (log `judge=restate, decision=borderline_haiku_restate`). If False (new): proceed with `send_cb` (log `judge=new, decision=borderline_haiku_new`).
    - If `verdict.action == "suppress"` AND `verdict.jaccard is None`: should not happen given the dataclass populates it on the suppress path (`bridge/redundancy_filter.py:218-224`), but handle defensively — log warning `[completion-runner] suppress verdict missing jaccard; defaulting to send` and proceed to `send_cb`.
    - If `verdict.action == "send"`: proceed with `send_cb` as today (log `reason={verdict.reason}, decision=below_low_cutoff_or_other_send_reason`). This branch covers J < LOW_CUTOFF, new artifact present, empty draft, etc. — all the legitimate send paths from `should_suppress`.
- Inline comment at the `should_suppress` call site explaining: (a) why `session_status=None` (per Risk 4 / spike-4), (b) why `expectations=None` (Pass 2 returns plain text), (c) why `threshold=LOW_CUTOFF` not `HIGH_CUTOFF` (verdict.jaccard population — cite this plan and `bridge/redundancy_filter.py:218-226`).
- If `telegram_message_id is None` on the suppression branch: log warning `[completion-runner] suppress decision but no anchor message_id; falling silent`, no reaction queued, no send, silent fall-through. `delivery_attempted = False`.
- Guard the entire suppression block (steps 2d wait/refetch + 2e suppression check) with `try/except Exception` that logs `[completion-runner] suppression-block crashed (non-fatal): {exc}` and falls through to the existing delivery path on any unhandled exception. Fail-open: a buggy suppression check must never block a legitimate completion delivery.

**2f. Decide and implement reaction-queueing path:**
- Preferred: extract `_build_reaction_payload` from `agent/output_handler.py:789-820` to a new module `bridge/reaction_outbox.py` exposing `build_reaction_payload(...)` and `queue_reaction(parent_session_id, chat_id, reply_to_msg_id, emoji)`. Update `_rtr_queue_reaction` at `agent/output_handler.py:763-787` to call into the new module so there's one source of truth. Call from completion runner.
- Backstop (if extraction is rejected for scope reasons): inline-replicate the 6-line payload + `r.rpush` + `r.expire(..., 3600)` snippet directly in the completion runner with a comment `# Mirror of TelegramRelayOutputHandler._build_reaction_payload — keep in sync.`
- DO NOT call `tools/react_with_emoji.react()` — it's CLI-only and `sys.exit(1)`s on missing env vars.

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
  - `test_completion_suppressed_when_final_text_matches_chat_log_outbound`
  - `test_completion_delivered_when_final_text_unique`
  - `test_completion_baseline_excludes_inbound_entries`
  - `test_completion_baseline_excludes_stale_entries`
  - `test_completion_judge_called_in_borderline_band` (with mocked Haiku)
  - `test_completion_adapter_failopen_on_malformed_entry`
  - `test_completion_silent_fallback_when_telegram_message_id_none`
  - `test_completion_skips_suppression_check_on_sentinel_text`
  - `test_completion_outbox_drain_wait_times_out_gracefully`
  - `test_completion_refetches_parent_before_suppression_check`
  - `test_threshold_parameter_overrides_default` (in `tests/unit/test_redundancy_filter.py`)
  - `test_reaction_outbox.py` shape-equivalence test (only if extraction option chosen in step 2f)
- REPLACE the integration test in `tests/integration/test_chat_message_log_e2e.py` per Test Impact (assert against `chat_message_log`, NOT `recent_sent_drafts`).
- UPDATE existing fixtures in `test_deliver_pipeline_completion.py` to include `chat_message_log` (defaults to empty list for unaffected tests).
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
| Low-threshold call enforced | `grep -nE 'should_suppress\(' agent/session_completion.py` | output > 0 AND the call uses `threshold=LOW_CUTOFF` (or literal `0.55`), NOT `HIGH_CUTOFF` directly |
| HIGH_CUTOFF read from env | `grep -n 'DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD' agent/session_completion.py` | output > 0 (env var read with default `0.75`) |
| Two-tier verdict logic present | `grep -nE 'verdict\.jaccard\s*>=\s*HIGH_CUTOFF\|verdict\.jaccard\s*<\s*HIGH_CUTOFF' agent/session_completion.py` | output >= 1 (high/borderline branching present) |
| Defensive matched_index guard | `grep -nE 'matched_index|borderline.*range' agent/session_completion.py` | output >= 1 (defensive idx-bounds check) |
| Sync-redis pattern in wait helper | `grep -nE 'redis\.Redis\.from_url|asyncio\.to_thread\(.*llen' agent/session_completion.py` | output >= 2 (constructor + to_thread wrapper for sync llen) |
| Baseline source is chat_message_log NOT recent_sent_drafts | `grep -nE '\.chat_message_log\|_build_completion_baseline\|recent_sent_drafts' agent/session_completion.py` | output contains `chat_message_log` and `_build_completion_baseline`; NO occurrences of `recent_sent_drafts` |
| Outbox-drain wait helper exists | `grep -n '_await_outbox_drained\|_build_completion_baseline\|_judge_completion_novelty' agent/session_completion.py` | output contains all three names |
| Reaction queueing is canonical (not react_with_emoji) | `grep -n 'react_with_emoji\|tools.react_with_emoji' agent/session_completion.py` | exit code 1 (no match — completion runner does NOT call this) |
| Threshold parameter present in should_suppress | `grep -n 'threshold' bridge/redundancy_filter.py` | output > 0 (parameter exists) |
| Doc updated | `grep -rl 'Mid-session-send-aware completion suppression' docs/features/` | exit code 0 |

## Critique Results

### Cycle 2 (rev2 → rev3)

Cycle 2 critique returned `NEEDS REVISION` (recorded 2026-05-04T11:51:35Z, artifact_hash `sha256:75f2ad0276272d3d14f38204ad6c62fe3a03a3ccba438e5ec2e3c63b70b2827b`). The critic-by-critic transcript was not persisted; the rev3 author re-derived the load-bearing issues by re-reading `bridge/redundancy_filter.py:55-71,138-226` (`SuppressionVerdict` shape and `_should_suppress_inner` exit paths), `agent/output_handler.py:159` and `bridge/telegram_relay.py:81` (sync-redis client patterns), and grep'd for `redis.asyncio` (no matches). Each finding has a concrete addressed-by reference below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic + Adversary | Plan rev2's step 2e references `verdict.jaccard` and `verdict.matched_index` on a `verdict.action == "send"` branch (`if verdict.action == "send" AND 0.55 <= verdict.jaccard < 0.75`). False — `SuppressionVerdict.jaccard` and `SuppressionVerdict.matched_index` are populated **only on the `suppress` path** (`bridge/redundancy_filter.py:218-224`). All `send` exit paths (lines 150, 154, 158, 162, 201, 226) construct the verdict with default `None` for those fields (defaults at lines 70-71). Step 2e's borderline-band escalation would dereference `None` and raise `TypeError: '<=' not supported between instances of 'float' and 'NoneType'` — completion runner crashes inside the new suppression block. | Restructured §Solution > Technical Approach and step 2e to call `should_suppress` with `threshold=0.55` (the LOW cutoff) and inspect `verdict.jaccard` only when `verdict.action == "suppress"`. Added new §Data Flow §"Two-tier verdict via low-threshold call" that documents the rationale and the dataclass invariant. Added defensive guard for `verdict.jaccard is None` AND defensive bounds check on `verdict.matched_index`. Added 4 new test cases to Test Impact: high-confidence-suppress-skips-haiku, borderline-with-invalid-matched-index, send-verdict-with-new-artifact, and revised borderline-band test to assert `threshold=0.55` is used. Added Verification rows asserting LOW_CUTOFF and HIGH_CUTOFF are both wired correctly. | Source check: `SuppressionVerdict` dataclass (`bridge/redundancy_filter.py:55-71`) has `jaccard: float \| None = field(default=None)` and `matched_index: int \| None = field(default=None)`. Only `_should_suppress_inner` line 219-223 (the suppress-with-match branch) ever passes non-None values. |
| BLOCKER | Operator + Archaeologist | Plan rev2's `_await_outbox_drained` pseudocode imports `from agent.redis_client import get_redis_async`, which does not exist in this codebase. Verified via `find . -name "*.py" -path "*/agent/*" \| xargs grep -l "def get_redis"` (no matches) and `grep -rn "redis.asyncio" agent/ bridge/` (no matches). The codebase uses sync `redis.Redis.from_url(decode_responses=True)` exclusively (see `agent/output_handler.py:159`, `bridge/telegram_relay.py:81`). Build will fail at import time when the helper is first invoked. | Rewrote `_await_outbox_drained` pseudocode in §Race Conditions > Race 1 Mitigation to use sync `redis.Redis.from_url(...)` matching the existing codebase pattern, with `await asyncio.to_thread(r.llen, queue_key)` to keep the event loop free. Added explicit comment in the pseudocode explaining the choice. Added Verification row asserting both `redis.Redis.from_url` and `asyncio.to_thread(...llen...)` appear in `agent/session_completion.py`. | The same connection-string handling (`os.environ.get("REDIS_URL", "redis://localhost:6379/0")`) is used by `agent/output_handler.py:155-159` and `bridge/telegram_relay.py:75-81`. The plan now matches that single source-of-truth pattern. |
| CONCERN | Operator | Step 2e didn't explicitly justify `expectations=None`. A reader unfamiliar with the regular drafter pipeline might assume the completion runner has access to a `MessageDraft.expectations` value and that `None` is wrong. (Termination Condition 3 in `should_suppress` forces `send` if expectations is non-empty — passing the wrong value would silently break the suppression check.) | Added explicit rationale in §Solution > Technical Approach > "Hybrid filter call" item 2: Pass 2 returns plain text, not a `MessageDraft`-shaped object, so there is no expectations concept here. Inline comment at the call site (per step 2e's revised inline-comment requirement) now also covers this. | Pass 2 in `_deliver_pipeline_completion` returns the harness's plain string output (see `agent/session_completion.py:597`), not a structured drafter object. The `expectations` mechanism only exists on `MessageDraft` returned by the regular drafter (`bridge/message_drafter.py`). |
| NIT | Simplifier | Plan rev2 had a `prior_critique_artifact_hash` frontmatter field still pointing to the cycle-1 hash (`5230ef8c...`) even though cycle 2 had run and produced a new verdict (`75f2ad...`). Stale tracking field. | Updated frontmatter `prior_critique_artifact_hash` to the cycle-2 hash. The G5 (Unchanged critique artifact) guard in `/sdlc` will now see the next critique as a re-evaluation of a *new* artifact (rev3 differs from rev2). | n/a — frontmatter housekeeping. |

### Cycle 1 (rev1 → rev2)

Cycle 1 critique returned `NEEDS REVISION` (recorded 2026-05-04T11:23:39Z, artifact_hash `sha256:5230ef8c0a11c04101f913f726689af0a87cf924af2e4782e166afe7c9f26069`). Findings are reconstructed below from a self-critique pass against the recon-validated source files (the original critic-by-critic transcript was not persisted; this plan's revision cycle re-derives the load-bearing issues by re-reading `agent/output_handler.py:584-590`, `tools/valor_telegram.py:1042-1052`, `tools/react_with_emoji.py:43-92`, `bridge/telegram_relay.py:519-549,690-700`, and `agent/session_completion.py:551,694-697,771`). Each finding has a concrete addressed-by reference in the revised plan.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic + Archaeologist | Plan's Data Flow §5 and Technical Approach state `parent.recent_sent_drafts` is "already populated by the relay" and use it as the suppression baseline. False — `recent_sent_drafts` is written ONLY by `TelegramRelayOutputHandler.send` at `agent/output_handler.py:586` (Path A), gated on `session.is_sdlc`. Path B (`valor-telegram send`) writes directly to the outbox and the relay's drain loop never touches `recent_sent_drafts`. The post-draft suppression check would compare against an empty/incomplete baseline for the Path-B-source duplicates that this issue is filed to fix. | Revised Solution > Technical Approach to use `chat_message_log` as the baseline source; added `_build_completion_baseline` adapter helper; updated Data Flow §5, all task steps, all test cases, Success Criteria. | Source-of-truth check: `grep -rn "recent_sent_drafts =\|.recent_sent_drafts.append\|record_recent_sent_draft" agent/ bridge/ tools/` returns ONLY `models/agent_session.py:1519` (the helper itself) and `agent/output_handler.py:586` (the sole caller). No Path-B writer exists. |
| BLOCKER | Adversary + Operator | Race 1 mitigation reasoning is wrong. Plan claimed "by construction the relay's append precedes the PM session's completion signal" because "the relay's outbox-drain loop processes the publish and writes `chat_message_log` synchronously." This is false: `tools/valor_telegram.py:1049-1052` returns immediately after `r.rpush`; the relay drains in a separate process via `bridge/telegram_relay.py::run_outbox_consumer`. There is NO cross-process synchronization between publisher return and consumer append. The race is real and present. | Replaced Race 1 mitigation with three-layer defense: (a) `_await_outbox_drained` bounded 2s wait helper, (b) re-fetch parent from Popoto immediately before suppression-baseline read, (c) idempotent fallback (empty baseline → existing send path == today's behavior, not worse). Removed the false architectural-ordering claim. | The drain consumer runs in `bridge/telegram_relay.py::run_outbox_consumer` as a separate asyncio task on the bridge process; cmd_send runs in a different (subprocess of the) session worker process. Cross-process ordering is not a thing here. |
| CONCERN | Skeptic | Reaction-queueing direction names a non-existent function: `tools/react_with_emoji::queue_reaction`. The `tools/react_with_emoji.py` module exposes `react(feeling: str)` only — a CLI entry point that reads `TELEGRAM_CHAT_ID` / `TELEGRAM_REPLY_TO` / `VALOR_SESSION_ID` from env vars and `sys.exit(1)`s on missing values. It is unusable from in-process code in `agent/session_completion.py`. | Revised Technical Approach to use `TelegramRelayOutputHandler._build_reaction_payload` (`agent/output_handler.py:789-820`) + outbox `rpush` directly. Added step 2f offering two options: extract to `bridge/reaction_outbox.py` (preferred — single source of truth) OR inline-replicate with sync-with-canonical comment. Added Success Criterion verifying the canonical mechanism is used. | The existing pattern is at `agent/output_handler.py:763-787` (`_rtr_queue_reaction`) plus the static `_build_reaction_payload`. Either lift those to a module and call from both sites, or copy the 6-line snippet inline. |
| CONCERN | Operator | Data shape mismatch: `should_suppress`'s `recent_sent_drafts` parameter expects `[{ts, text, artifacts}, ...]`. `chat_message_log` entries are `[{direction, sender, content, message_id, ts}, ...]`. Plan didn't account for the adapter step. | Added explicit `_build_completion_baseline(parent, *, window_seconds, max_entries)` adapter helper with full pseudocode in Technical Approach. Filters by direction and time window; computes per-entry `artifacts` via `extract_artifacts`. Fail-open. Added unit tests for inbound-filter, stale-filter, and malformed-entry-failopen behavior. | Adapter pseudocode in plan lives in §Solution > Technical Approach. Caller site references it by name in step 2e. |
| CONCERN | Adversary | Sentinel string `"[completion-runner internal error — no final_text assigned]"` could be passed through the suppression check if the assignment-tracking logic doesn't catch a code path. Bigram-Jaccard against the sentinel against any baseline is undefined and not a useful signal. | Added explicit early-return guard in step 2e: skip suppression check if `final_text` is empty, whitespace-only, or equals the sentinel literal. Added unit test `test_completion_skips_suppression_check_on_sentinel_text`. | Guard placement: at the start of the suppression block, immediately after Pass 2 returns. The sentinel literal is a constant in `agent/session_completion.py:567` and should be referenced by name (export as `_DEGRADED_FINAL_TEXT_SENTINEL` if not already). |
| CONCERN | Operator | The `is_sdlc` gate at `agent/output_handler.py:584` means non-SDLC sessions don't update `recent_sent_drafts` at all. Even if the plan had used `recent_sent_drafts`, non-SDLC PM sessions would have an empty baseline. This was a hidden scope assumption. | Now moot for the suppression baseline (we use `chat_message_log` which has no `is_sdlc` gate). Added explicit scope note in Technical Approach: this plan targets SDLC PM sessions (where `_deliver_pipeline_completion` actually fires); non-SDLC sessions don't trigger the runner. | `_deliver_pipeline_completion` is called from `agent/session_completion.py::_attempt_pipeline_completion`, which only runs at end of pipeline-ish completions. Non-SDLC PM/teammate sessions take a different path. |
| NIT | Archaeologist | Tracking URL in frontmatter was malformed (`https://github.com/tomcounsell/issues/1262` — missing repo slug). | Corrected to `https://github.com/tomcounsell/ai/issues/1262` in frontmatter. | n/a — typo fix. |
| NIT | Simplifier | Race 2 (recent_sent_drafts read-after-write) was redundant scaffolding once the baseline source changed. | Race 2 removed entirely with explanatory note pointing to the false-assumption history. Race 3 retained (sibling-PM concurrent runners). | n/a — section-level cleanup. |

---

## Open Questions

The four open questions from cycle 1 are resolved by this revision:

1. **Hybrid intercept (Q1) vs single cut.** RESOLVED: hybrid (both context-injection at Pass 1 AND post-draft suppression as backstop). Forced by the corrected understanding of Path B's data flow — context-injection alone can't catch the LLM-disobeys-instructions case, and post-draft suppression alone doesn't help the drafter avoid synthesizing the duplicate in the first place. Both cuts are cheap (chat-log read is ~3-5ms, per-call adapter is O(5 entries)).

2. **Suppression-fallback when `telegram_message_id is None`.** RESOLVED: silent fall-through with warning log. Rationale: this case occurs only when the completion runner was invoked without an anchor message (rare — most invocations carry one through from the originating Telegram message). Sending text "Done." or similar would violate the persona convention (per `feedback_emoji_over_acks`); silent fall-through is the conservative choice. If operators see this warning frequently, file a follow-up to plumb anchor through more reliably.

3. **Per-completion threshold default.** RESOLVED: `0.75` (env-tunable via `DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD`). Higher than the in-session `0.65` because completion summaries legitimately include some prior content and we don't want false-positive suppressions; the borderline band [0.55, 0.75) gets the Haiku judge.

4. **Helper module placement.** RESOLVED: new private helpers (`_build_completion_baseline`, `_await_outbox_drained`, `_judge_completion_novelty`) live in `agent/session_completion.py`. The reaction-payload helper EXTRACTION is a separate decision in step 2f (preferred: extract to `bridge/reaction_outbox.py`; backstop: inline-replicate in completion runner). Other helpers stay local.

**No new open questions** introduced by rev2 (cycle-1 fix). 

Rev3 (cycle-2 fix) likewise introduces no new open questions — the BLOCKER findings (verdict shape mismatch, nonexistent `get_redis_async`) were both self-resolvable design errors with clear codebase-grounded fixes:

- The `should_suppress` `threshold` parameter (already in step 1) is now used at the LOW value (0.55) by the completion runner; the HIGH cutoff (0.75) lives in the caller. No external interface changes beyond what step 1 already proposed.
- `_await_outbox_drained` now uses the same sync `redis.Redis.from_url(...)` pattern as the rest of the codebase, wrapped in `asyncio.to_thread` for non-blocking poll. Zero new dependencies.
