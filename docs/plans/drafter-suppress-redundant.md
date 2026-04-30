---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-29
tracking: https://github.com/tomcounsell/ai/issues/1205
last_comment_id:
revision_applied: true
revision_round: 1
revision_at: 2026-04-30
---

# Drafter — Suppress Redundant Status Updates

## Problem

On 2026-04-29, the PM session in the "PM: Valor" chat was waiting on five child SDLC dev sessions (REVIEW/MERGE on PRs #1200/#1201/#1204 and adjacent work). Between 11:44 and 12:24 UTC the drafter emitted **9 system-authored status messages**, each a near-identical paragraph that promised "I'll confirm merge-readiness next turn" but never produced new information. The CEO had to absorb nine pings for a single in-flight pipeline.

**Current behavior:**
- Every child completion calls `transition_status(parent, "pending", reason="child completed, steering injected")` (`agent/session_completion.py:1283`), re-enqueuing the PM. With N children completing serially, the PM resumes N times.
- Each PM resume produces a draft via `bridge/message_drafter.py::draft_message()`.
- `agent/output_router.py:154` forces `deliver` (not `nudge`) when `session_status == "waiting_for_children"` so the PM can release its semaphore slot. Result: every drafted message ships to Telegram, regardless of whether it carries new information.
- There is no comparison between the new draft and the last few sent messages. A bigram-near-duplicate ships unchanged.
- Read-the-Room (PR #1204, opt-in via `READ_THE_ROOM_ENABLED`) would not have caught this even if enabled — it explicitly bypasses SDLC sessions (`bridge/read_the_room.py:400`).

**Desired outcome:**
- Within a session, when a freshly drafted message is substantially the same as a recent prior send and carries no new artifact (PR/commit/error/question), the text send is suppressed and a 👀 reaction is queued on the original triggering message instead. The PM session keeps working; the CEO sees the eye-emoji "still working" signal.
- Suppression is automatically defeated when (a) the drafter detected a question for the human (`MessageDraft.expectations` is non-empty), (b) the session enters a terminal lifecycle status, or (c) the new draft contains an artifact (PR URL, commit hash, error string) not present in any recently-sent draft.
- The fix runs deterministically (no extra LLM call) and applies inside the Path A funnel so every PM-authored Telegram message is gated.

## Freshness Check

**Baseline commit:** 4f0619ae (main, 2026-04-29)
**Issue filed at:** 2026-04-29T05:29:25Z
**Disposition:** Unchanged — minor confirmation drift only.

**File:line references re-verified:**
- `agent/output_router.py:154-155` — `waiting_for_children → deliver` guard still present and unchanged. Holds.
- `bridge/message_drafter.py:1623` — `draft_message()` entry point still in place, signature `(raw_response, session, *, medium, persona)`. Holds.
- `bridge/message_drafter.py:1198` — `DRAFTER_SYSTEM_PROMPT` still defined; voice/format rules unchanged.
- `agent/output_handler.py:162-374` — `TelegramRelayOutputHandler.send` is the single Path A funnel into `telegram:outbox:{session_id}`. Holds.
- `bridge/read_the_room.py:400-410` — RTR's SDLC-session bypass still present. Confirms RTR will not cover this case.

**Cited sibling issues/PRs re-checked:**
- #1193 / PR #1204 — RTR pre-send pass — merged 2026-04-29 10:22 UTC. Symptom in this issue (11:44–12:24 UTC) occurred *after* RTR merged, confirming RTR's SDLC bypass shielded the spam from RTR's check.
- Memory `feedback_emoji_over_acks.md` — still in `MEMORY.md` index; "for 'I heard you' signals on Telegram, react with an emoji on the user's message instead of sending a text ack". Directly informs the suppression fallback.

**Commits on main since issue was filed (touching referenced files):**
- `531e8f4e` Read-the-Room pre-send pass for the Telegram drafter (#1193) (#1204) — partially adjacent but does NOT fix this issue (SDLC bypass). Adds the reaction-queue helper (`_rtr_queue_reaction`) and `_build_reaction_payload` we will reuse.

**Active plans in `docs/plans/` overlapping this area:** none — `docs/plans/sdlc-1192.md` and `docs/plans/sdlc-1148.md` touch the drafter for unrelated concerns (medium-aware prompts, file size handling). No conflict.

**Notes:** Recon Summary could not be added directly to the issue body — the GitHub PAT in this environment is read-only on issues. The recon evidence lives below in this plan.

## Recon Summary (mirrored from issue)

**Confirmed:**
- `bridge/message_drafter.py:1623` — `draft_message()` is the entry point for all drafted Telegram outputs.
- `agent/output_handler.py:162-374` — `TelegramRelayOutputHandler.send` is the single Path A funnel; calls `draft_message()` then optionally invokes RTR before queueing.
- `agent/output_router.py:154-155` — Routing forces `deliver` (not `nudge`) for `waiting_for_children`. Every PM resume in this state ends in a deliver action.
- `agent/session_completion.py:1275-1289` — Each child completion re-enqueues the parent PM; with N serial completions, PM resumes N times → drafts N times → sends N times.
- `bridge/read_the_room.py:400-410` — RTR's intended SDLC bypass uses `getattr(session, "sdlc_slug", None)`. **This guard never actually fires** — `sdlc_slug` is not a real `AgentSession` field, so `getattr` always returns `None` and the bypass is a no-op. RTR therefore *can* run on SDLC sessions today; it simply hasn't been triggered in production because `READ_THE_ROOM_ENABLED` is opt-in and not set on the affected machine. **Implication for this plan:** we cannot rely on the cited bypass to scope our new layer; we must read SDLC-ness directly via the real `AgentSession.is_sdlc` property (defined at `models/agent_session.py:1612`). Fixing RTR's broken bypass is out of scope here — tracked as a follow-up after this plan ships.
- `bridge/read_the_room.py:64` — `RTR_SUPPRESS_EMOJI = "👀"` already defined; `_rtr_queue_reaction` (`agent/output_handler.py:532-555`) and `_build_reaction_payload` (`agent/output_handler.py:557-589`) are the established template for emoji-instead-of-text fallback.
- `agent/memory_extraction.py:589-597` — `_extract_bigrams(text) -> set[tuple[str, ...]]` already exists. Unigram + bigram extractor with a 4-char minimum word length. Re-usable.
- `models/agent_session.py:207, 1428-1446` — `pm_sent_message_ids` `ListField` and `record_pm_message(msg_id)` exist for PM-authored Telegram message IDs but DO NOT track drafted text. We need a new field to track recent drafted text.
- `bridge/message_drafter.py::extract_artifacts` (lines 390-435) — extracts artifacts as `dict[str, list[str]]` with the *actual* keys: `commits`, `urls`, `files_changed`, `test_results`, `errors`. **There are no separate `pull_requests` or `issue_refs` keys** — PR and issue links live inside `urls` (e.g., `https://github.com/.../pull/N`, `https://github.com/.../issues/N`). Reusable for "new artifact" detection by diffing the union of all values across all keys.

**Revised (from the issue's proposal):**
- The fix MUST live in `agent/output_handler.py` (the Path A funnel), not `agent/output_router.py`. The router does not see the drafted text — it only chooses an action (`deliver` vs. `nudge`). The suppression decision needs the text body and session-scoped history.
- We cannot piggyback on RTR — it bypasses SDLC sessions and is opt-in. A new deterministic path must run for SDLC sessions specifically.

**Pre-requisites:**
- A surface for tracking the last N sent drafts per session. Adding `recent_sent_drafts` `ListField` on `AgentSession` (capped at N entries, each holding text + ts + artifacts) is cleaner than fetching by `chat_id` from `TelegramMessage`: session-scoped (no cross-conversation noise), no extra DB hop, ride the AgentSession save cycle that already happens in the funnel.

**Dropped:**
- LLM-based similarity (Haiku) — RTR already does this and is bypassed for SDLC. Adding a second LLM call doubles cost for no new signal.
- Embedding similarity — adds Ollama/OpenAI dependency on every send and the failure mode is single-session redundancy detection. Bigram Jaccard resolves this adequately.

## Prior Art

- **Issue #1193 / PR #1204 — Read-the-Room pre-send pass** — Shipped 2026-04-29 10:22 UTC. Adds an opt-in Haiku verdict between drafter and outbox returning `send | trim | suppress`. Establishes the call-site pattern (after drafter, before `rpush`), the `👀` suppress emoji constant, the reaction-queue helper, and the `session_events` observability schema. **Does NOT cover SDLC sessions** — bypasses them by design (line 400). Establishes the architectural template our new layer will mirror.
- **Memory `feedback_emoji_over_acks.md`** — Reaction-instead-of-text-ack rule. The 👀 fallback chosen here directly applies that rule to the drafter spam case.
- **Issue #1190 / PR #1194 — Bridge: replace steering text-acks with emoji reactions** — Same rule applied earlier to the steering-ack path. Confirms the codebase preference: emoji on the user's anchor message beats a text ack.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1204 (RTR) | LLM-based pre-send guard with `send/trim/suppress` verdicts | SDLC sessions bypassed by design; opt-in via `READ_THE_ROOM_ENABLED` (default off). PM in `waiting_for_children` over a slug-bound work item never reaches the Haiku call. |

**Root cause pattern:** The drafter pipeline assumed every drafted message is worth sending. The SDLC pipeline produces draft cycles that are dominated by "no new material" turns (PM waiting on children, drafter regenerating an unchanged status). The existing suppression layer (RTR) was scoped to non-SDLC chats explicitly, so the spam mode is structural, not accidental.

## Architectural Impact

- **New dependencies:** none (pure Python; reuse `_extract_bigrams` from `agent/memory_extraction.py` or duplicate locally).
- **Interface changes:** `TelegramRelayOutputHandler.send` adds an internal pre-write step. Public method signature unchanged. Adds a new `recent_sent_drafts` `ListField` on `AgentSession`.
- **Coupling:** The new module (`bridge/redundancy_filter.py`) imports `_extract_bigrams` (or its own copy) and `extract_artifacts` from `bridge/message_drafter.py`. Path A `output_handler.py` imports the new module. No new cross-package dependencies.
- **Data ownership:** `recent_sent_drafts` is owned by `AgentSession`. Reads happen only inside the funnel; writes happen only on successful outbox `rpush`.
- **Reversibility:** Single env-var gate (`DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED`, default true so the fix takes effect) and a kill-switch path that returns `send_original` on any error. Disabling restores pre-fix behavior.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (review thresholds + termination conditions before merge)
- Review rounds: 1 (code review)

This is a small, additive change in a single funnel with a clear test surface. Most of the cost is in calibrating thresholds, which we can ship with conservative defaults and tune later.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | AgentSession persistence (Popoto) |
| `_extract_bigrams` available | `python -c "from agent.memory_extraction import _extract_bigrams; assert _extract_bigrams('hello world')"` | Similarity primitive |
| `extract_artifacts` available | `python -c "from bridge.message_drafter import extract_artifacts; assert callable(extract_artifacts)"` | Artifact detection primitive |

Run all checks: `python scripts/check_prerequisites.py docs/plans/drafter-suppress-redundant.md`

## Solution

### Key Elements

- **`bridge/redundancy_filter.py`** (new module): Pure functions for the suppression decision. `should_suppress(draft_text, draft_artifacts, session) -> SuppressionVerdict` returns one of `send | suppress`, plus a reason and the matched-prior-draft index. No I/O, no LLM, deterministic.
- **`AgentSession.recent_sent_drafts`** (new `ListField`): Last N (default 3) successfully-sent drafts as dicts `{ts, text, artifacts}`. Appended in the funnel after the outbox `rpush` succeeds; capped at N by the helper that writes it.
- **`TelegramRelayOutputHandler.send` integration**: After the drafter call, before (or alongside) the existing RTR call, invoke the redundancy filter for SDLC sessions. On `suppress`, queue a 👀 reaction (reusing `_build_reaction_payload`) and return without writing the text to the outbox; emit a `session_events` entry. On `send`, fall through to the existing RTR + outbox path. After a successful outbox write, persist the draft into `recent_sent_drafts`.
- **Termination conditions** (force `send` in the redundancy filter):
  1. `MessageDraft.expectations` is non-empty (drafter found a question for the human).
  2. Session status is terminal (`completed`, `failed`, `blocked`) at send time.
  3. Draft contains an artifact (any value in `extract_artifacts`) not present in any prior `recent_sent_drafts` entry.
  4. `recent_sent_drafts` is empty (no baseline → cannot be redundant).
  5. The most recent prior draft's `ts` is older than `REDUNDANCY_WINDOW_SECONDS` (default 600s) — outside the comparison window, treat as fresh.
- **Voice unchanged**: `DRAFTER_SYSTEM_PROMPT` is not modified. The agent keeps drafting as it does today; we only filter what reaches the user.

### Flow

PM session re-enqueued by child completion → PM resumes → drafter produces draft → `TelegramRelayOutputHandler.send`:

1. Run the drafter (existing).
2. **NEW:** If session is SDLC and `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED` is true, run `should_suppress(draft, recent_sent_drafts)`.
3. Outcome:
   - **`send`** → fall through to existing RTR check → outbox `rpush` → append the draft to `recent_sent_drafts` (cap at N) → save AgentSession.
   - **`suppress`** → queue a 👀 reaction on `reply_to_msg_id` (or fall through to send if no anchor, mirroring RTR's contract) → emit `drafter.suppressed_redundant` `session_event` → return without outbox write.

### Technical Approach

- **Similarity metric:** Bigram Jaccard. `J = |bigrams(A) ∩ bigrams(B)| / |bigrams(A) ∪ bigrams(B)|`. Default threshold `J ≥ 0.65`. Tunable via `DRAFTER_REDUNDANCY_THRESHOLD` env var.
- **Comparison set:** All entries in `recent_sent_drafts`. If *any* prior draft has `J ≥ threshold` and there is no new artifact relative to it, suppress.
- **Artifact set:** `extract_artifacts(draft_text)` produces a dict of `{commits: [...], urls: [...], files_changed: [...], test_results: [...], errors: [...]}` (all keys optional — only present when at least one match is found). PR and issue links land in `urls` (e.g., `https://github.com/.../pull/N`). We define "new artifact" as: the *flattened set of all values across all keys* in the new draft is not a subset of the flattened set across the prior draft. Concretely: `new_artifacts = set().union(*new_dict.values())` and we suppress only when `new_artifacts.issubset(prior_artifacts)` for the matched prior draft.
- **Recent drafts cap:** Default `DRAFTER_RECENT_DRAFTS_N = 3`. Old entries dropped FIFO when over cap.
- **Window:** Default `DRAFTER_REDUNDANCY_WINDOW_SECONDS = 600`. Entries older than the window are still kept in the list (they don't churn) but the decision uses the time stamp to skip stale baselines.
- **SDLC scoping:** Filter applies only when `session.is_sdlc` is True (the real `AgentSession` property at `models/agent_session.py:1612`). Non-SDLC sessions skip the filter and defer to RTR + the existing path — this preserves existing behavior for Teammate/PM-conversational chats. **Do not** read `session.sdlc_slug` — that field does not exist on the model and `getattr` will silently always return `None`.
- **Failure mode:** All exceptions in the filter return `SuppressionVerdict("send", reason="filter_error")`. Filter never blocks delivery.
- **Observability:** `drafter.suppressed_redundant` and `drafter.suppress_fallthrough` `session_events` mirror RTR's schema (`{type, ts, chat_id, reason, draft_preview, matched_prior_preview, jaccard}`).
- **Voice / DRAFTER_SYSTEM_PROMPT:** Untouched. Per the issue's instruction, the policy change is suppression — not voice.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/redundancy_filter.py::should_suppress` — assert that any exception inside the function path returns `SuppressionVerdict(action="send", reason="filter_error")`. Test by patching `_extract_bigrams` to raise.
- [ ] `TelegramRelayOutputHandler.send` — assert that an exception raised inside the redundancy-filter branch is caught and falls through to the existing RTR + outbox path. Test by patching the filter module to raise.
- [ ] `AgentSession.record_recent_sent_draft` — assert that the helper calls `save(update_fields=["recent_sent_drafts", "updated_at"])` (not unscoped `save()`), so concurrent writes to `context_summary` / `expectations` / `session_events` from the same `send()` flow are not clobbered. Use a mock that captures `update_fields=` kwargs.
- [ ] `AgentSession.recent_sent_drafts` append after outbox write — assert that a failed `session.save(update_fields=[...])` does not block the outbox `rpush` (logger.warning, no raise).

### Empty/Invalid Input Handling
- [ ] `should_suppress("", artifacts, session)` returns `send, reason="empty_draft"` (defensive: never suppress empty text).
- [ ] `should_suppress(text, artifacts, session)` with `recent_sent_drafts=None` returns `send, reason="no_baseline"`.
- [ ] `should_suppress(text, artifacts, session)` with `recent_sent_drafts=[]` returns `send, reason="no_baseline"`.
- [ ] Whitespace-only draft → returns `send, reason="empty_draft"`.

### Error State Rendering
- [ ] When suppression queues a 👀 reaction but the Redis `rpush` fails, log the error and emit a `drafter.suppress_fallthrough` event with `reason="reaction_redis_error"`. The user must still get *some* signal — fall back to writing the text on the next round (the next failing send detects the same redundancy and re-tries the reaction queue).
- [ ] When suppression has no `reply_to_msg_id` anchor → fall through and send the original text, emit `drafter.suppress_fallthrough` with `reason="no_reply_anchor"` (mirrors RTR's I-heard-you contract).

## Test Impact

- [ ] `tests/unit/test_output_handler.py::TestReadTheRoomWiring` — UPDATE: existing tests assume RTR is the sole pre-send guard. Add a new test class `TestRedundancyFilterWiring` that exercises the SDLC path; do not delete the RTR tests. The two layers are sequenced: redundancy filter first for SDLC, RTR for non-SDLC. UPDATE one test to assert redundancy-filter bypass occurs for non-SDLC sessions.
- [ ] `tests/unit/test_message_drafter.py` — UPDATE: existing tests stand. Add new tests for the redundancy filter: `tests/unit/test_redundancy_filter.py` (new file) covering bigram Jaccard, artifact diff, termination conditions, error fallback.
- [ ] `tests/integration/test_message_drafter_integration.py` — UPDATE: add an SDLC-session integration test that drafts twice with the same content and asserts the second send is a 👀 reaction, not a text. The first test class for RTR stays as-is.
- [ ] `tests/unit/test_agent_session.py` — UPDATE: add a test for the `recent_sent_drafts` field roundtrip and FIFO cap. (Field is additive; existing tests unaffected.)

No existing tests are deleted. All changes are additive or surgical updates to assertions.

## Rabbit Holes

- **Embedding-based similarity.** Tempting because it's "smarter," but adds an Ollama/OpenAI dependency on every Path A send, with no win over bigram Jaccard for the failure mode this issue actually demonstrates (near-verbatim repeats). Skip.
- **Per-chat (vs. per-session) recent_sent_drafts.** Tempting because it would also catch cross-session repeats from the same project. But sessions usually own one conversational thread and we have RTR for cross-session redundancy in non-SDLC chats. Stay session-scoped.
- **Touching `DRAFTER_SYSTEM_PROMPT` to teach the model "don't repeat yourself."** The drafter's voice and behavior are working correctly — it produces a coherent message; the problem is upstream forcing it to draft when nothing has changed. Suppress at the funnel, not at the prompt.
- **Reusing RTR by removing the SDLC bypass.** Tempting because it would unify the two paths, but RTR is opt-in and SDLC chats see far higher message volume; an LLM call per send is cost we don't need when bigrams catch this. Keep RTR's SDLC bypass; ship a deterministic filter alongside.
- **A "trim" verdict in the new filter** (mirroring RTR's three-way decision). The redundancy filter has no business rewriting drafts — it has no drafter context. Two-state `send | suppress` only.

## Risks

### Risk 1: False suppression of a genuinely new status update
**Impact:** The CEO loses a signal the PM intended to send. With the 👀 reaction fallback, the user still sees "still working" — but if a real material update is mistakenly suppressed, the human only learns the difference at the next non-suppressed send (next material delta, terminal status, or question).
**Mitigation:**
- Conservative threshold (`J ≥ 0.65`) — pure paraphrases drop below this; only near-verbatim repeats trip suppression.
- Termination on any new artifact (PR URL, commit hash, error string) — most "real" updates carry a new artifact.
- Termination on any drafter-detected question → suppress never fires when the human's input is needed.
- `session_events` log lets us audit false suppressions after the fact and tune thresholds.
- Env-var kill switch (`DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED=false`) restores pre-fix behavior immediately.

### Risk 2: The 👀 reaction lands without a `reply_to_msg_id` anchor
**Impact:** The bridge cannot place a reaction on no message — silent suppression breaks the I-heard-you contract.
**Mitigation:** Mirror RTR's documented fallthrough — when no anchor, send the original text and emit `drafter.suppress_fallthrough`. The audit log captures the missed-suppression case.

### Risk 3: `recent_sent_drafts` grows unbounded if the cap helper has a bug
**Impact:** AgentSession Redis hash grows beyond the safe write size; saves slow down or fail.
**Mitigation:**
- Cap is enforced inside the helper (`_record_recent_sent_draft`) by slicing the list to last N before save.
- Field stores text previews capped at 500 chars per entry, not the full draft (drafts can be ~4096 chars; 3 × 500 = 1.5 KB upper bound).
- Add a unit test asserting the list never exceeds N after multiple writes.

### Risk 4: PM continues to spin and the suppression masks an underlying loop bug
**Impact:** The drafter spam was a *symptom* of the PM session repeatedly resuming with no new work. Silencing the symptom may hide the upstream cycle (which itself wastes Claude API tokens and CPU).
**Mitigation:**
- The `drafter.suppressed_redundant` event count is a metric — when it crosses an alert threshold (e.g., > 5 suppressions in 10 minutes for a single session), the dashboard surfaces it and we investigate the upstream cycle.
- This plan does NOT modify the PM resume cadence — that's an out-of-scope concern (see No-Gos). We are addressing the *user-visible* spam, with observability that exposes the underlying loop for follow-up work.

## Race Conditions

### Race 1: Concurrent sends to the same session_id
**Location:** `agent/output_handler.py::TelegramRelayOutputHandler.send` reading `session.recent_sent_drafts` and appending after the outbox write.
**Trigger:** Two PM-resume turns produce drafts concurrently (rare but possible during executor handoff).
**Data prerequisite:** `session.recent_sent_drafts` reflects the last successful send.
**State prerequisite:** `session.save(update_fields=["recent_sent_drafts", "updated_at"])` after appending must not clobber concurrent writes to *other* session fields (`context_summary`, `expectations`, `session_events`) issued by the same `send()` flow.
**Mitigation:** Use field-scoped `save(update_fields=[...])` as documented in Step 2. This guarantees that a write to `recent_sent_drafts` does not overwrite a concurrent write to `context_summary` (line 479) or to `session_events` from `_rtr_emit_event` (line 527-ish). For *append* races on the `recent_sent_drafts` list itself (read-modify-write with no lock), match the existing `session_events` posture: best-effort. The worst case is a single missed deduplication attempt — the *next* send dedupes against whichever entry won the race. Document the read-modify-write append posture in the module docstring; the cost of locking outweighs the cost of one missed dedup.

### Race 2: `recent_sent_drafts` populated before outbox `rpush` actually succeeds
**Location:** `agent/output_handler.py::TelegramRelayOutputHandler.send` between `r.rpush(queue_key, ...)` and the AgentSession save.
**Trigger:** Redis succeeds the `rpush` but the AgentSession save fails — next draft cannot dedup against the just-sent message.
**Data prerequisite:** The append happens *after* the outbox `rpush` returns successfully.
**State prerequisite:** A failed save must not cause a duplicate `rpush` on retry.
**Mitigation:** Order is: drafter → filter → outbox `rpush` → AgentSession append+save. If the save fails, log and continue — the next send will see one fewer baseline entry but cannot double-send the same draft. Idempotency is preserved.

## No-Gos (Out of Scope)

- **Modifying the PM resume cadence in `agent/session_completion.py`.** That's the upstream cycle — separate root-cause fix tracked under a future issue (see Risk 4 mitigation). This plan addresses user-visible spam only.
- **Touching `DRAFTER_SYSTEM_PROMPT`.** Issue text explicitly: "don't change voice, change suppression policy."
- **Removing RTR's SDLC bypass.** Two filters in two layers, by design.
- **Cross-session redundancy detection.** Sessions are independent; we don't need to compare draft N from session A against draft M from session B.
- **Embedding-based similarity.** Bigram Jaccard is sufficient for the demonstrated failure mode.
- **Tuning thresholds in production via dashboard.** Initial defaults ship hardcoded with env-var overrides. A tuning UI is a separate feature.
- **Path B coverage** (`tools/valor_telegram.py` / `valor-email send`). Same scope decision RTR made — Path B writes directly to the outbox. Out of scope; if needed, follow-up issue.

## Update System

No update system changes required — this feature is bridge-internal:
- No new dependencies (uses `_extract_bigrams` from existing `agent/memory_extraction.py` and `extract_artifacts` from existing `bridge/message_drafter.py`).
- No new config files (env vars only, defaults baked in).
- The `recent_sent_drafts` field is additive and nullable on `AgentSession` — existing sessions in Redis pick up the new field on first save with no migration needed (Popoto handles missing fields generically; see `feedback_field_backcompat_heal.md`).
- `.env.example` gets four new commented lines documenting the env vars (`DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED`, `DRAFTER_REDUNDANCY_THRESHOLD`, `DRAFTER_RECENT_DRAFTS_N`, `DRAFTER_REDUNDANCY_WINDOW_SECONDS`). Operators can flip the kill switch in `~/Desktop/Valor/.env` without a code change.
- No update script changes; the new code ships with the next regular `/update` cycle.

## Agent Integration

No agent integration required — this is a bridge-internal change. Specifically:
- No new CLI entry point in `pyproject.toml [project.scripts]`.
- No new MCP tool exposed to the agent.
- The bridge calls into `bridge/redundancy_filter.py` directly from `agent/output_handler.py`. The agent is unaware of suppression — to the agent, a draft is either sent or not, indistinguishable from existing RTR behavior.
- Agent-visible side effect: when its drafted output is suppressed, the user sees a 👀 reaction instead of a text message. This matches the existing RTR-suppress contract — the agent's own session log retains the draft text via `session_events.drafter.suppressed_redundant`.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/drafter-redundancy-suppression.md` describing the feature: where it lives, when it fires, suppression criteria, termination conditions, observability schema, env-var configuration, and the relationship to RTR.
- [ ] Add an entry to `docs/features/README.md` index table next to the Read-the-Room row.
- [ ] Update `docs/features/bridge-worker-architecture.md` mermaid diagram (or text-based flow at line ~42) to show the redundancy filter sitting before RTR for SDLC sessions.
- [ ] Update `docs/features/message-drafter.md` to reference the new suppression layer.
- [ ] Update `docs/features/read-the-room.md` § "Path B follow-up" or add a § "Adjacent layers" noting that the redundancy filter covers SDLC sessions deterministically.

### Inline Documentation
- [ ] Module-level docstring on `bridge/redundancy_filter.py` explaining the suppression contract, the deterministic-vs-LLM tradeoff, and the relationship to RTR.
- [ ] `should_suppress` docstring documenting all termination conditions, the Jaccard threshold, the artifact-diff rule, and the failure-mode contract (return `send` on any error).
- [ ] Comment block in `agent/output_handler.py::send` explaining the sequencing of redundancy filter → RTR → outbox.
- [ ] Inline comment near the `recent_sent_drafts` field declaration in `models/agent_session.py` documenting the cap, the FIFO policy, and the per-entry preview length.

## Success Criteria

- [ ] PM session in `waiting_for_children` that drafts the same status three times within `DRAFTER_REDUNDANCY_WINDOW_SECONDS` produces exactly one Telegram text message and two 👀 reactions on the human's anchor message (regression test reproduces the issue scenario).
- [ ] PM session that drafts a near-duplicate but adds a new PR URL artifact does NOT suppress (artifact-diff termination fires).
- [ ] PM session that drafts a near-duplicate but raises a question for the human does NOT suppress (`expectations` termination fires).
- [ ] PM session whose status transitions to terminal between draft and send delivers the final text (terminal-status termination fires).
- [ ] Non-SDLC sessions are unaffected (filter bypassed; RTR runs as before).
- [ ] `drafter.suppressed_redundant` event appears in `session.session_events` for every suppression with a usable preview and the matched-prior preview.
- [ ] All RTR tests still pass (no regression in the existing pre-send guard).
- [ ] Tests pass (`pytest tests/unit/test_redundancy_filter.py tests/unit/test_output_handler.py tests/integration/test_message_drafter_integration.py`).
- [ ] Lint clean (`python -m ruff check bridge/redundancy_filter.py agent/output_handler.py models/agent_session.py`).
- [ ] Documentation files created/updated and linked in `docs/features/README.md`.
- [ ] Setting `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED=false` in `.env` and restarting the bridge restores pre-fix behavior (manual smoke test).

## Team Orchestration

### Team Members

- **Builder (redundancy-filter)**
  - Name: redundancy-filter-builder
  - Role: Implement `bridge/redundancy_filter.py`, add `recent_sent_drafts` field on `AgentSession`, wire into `agent/output_handler.py`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (filter-tests)**
  - Name: redundancy-filter-tester
  - Role: Author `tests/unit/test_redundancy_filter.py` and the integration test in `tests/integration/test_message_drafter_integration.py`. Cover the regression scenario plus all five termination conditions.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (filter-validator)**
  - Name: redundancy-filter-validator
  - Role: Verify `should_suppress` contract, threshold defaults, observability events, and that RTR tests still pass.
  - Agent Type: validator
  - Resume: true

- **Documentarian (filter-docs)**
  - Name: redundancy-filter-documentarian
  - Role: Create `docs/features/drafter-redundancy-suppression.md`, update `docs/features/README.md`, surgical updates to `bridge-worker-architecture.md` and `message-drafter.md` and `read-the-room.md`.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard set per skill template — no specialists required for this small, additive change.)

## Step by Step Tasks

### 1. Build the redundancy filter module
- **Task ID**: build-filter
- **Depends On**: none
- **Validates**: `tests/unit/test_redundancy_filter.py` (create)
- **Informed By**: recon (reuse `_extract_bigrams`, `extract_artifacts`)
- **Assigned To**: redundancy-filter-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/redundancy_filter.py` with `SuppressionVerdict` dataclass and `should_suppress(draft_text, draft_artifacts, recent_sent_drafts, expectations, session_status)` returning `SuppressionVerdict(action: "send"|"suppress", reason: str, jaccard: float|None, matched_index: int|None)`.
- Use bigram Jaccard via `_extract_bigrams` (import from `agent.memory_extraction`).
- Implement all five termination conditions in the documented order.
- Wrap the entire body in a top-level `try/except` returning `SuppressionVerdict("send", reason="filter_error")` on any exception.
- Module-level constants: `REDUNDANCY_THRESHOLD = float(os.environ.get("DRAFTER_REDUNDANCY_THRESHOLD", "0.65"))`; `RECENT_DRAFTS_N = int(os.environ.get("DRAFTER_RECENT_DRAFTS_N", "3"))`; `REDUNDANCY_WINDOW_SECONDS = int(os.environ.get("DRAFTER_REDUNDANCY_WINDOW_SECONDS", "600"))`; `SUPPRESSION_ENABLED = os.environ.get("DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED", "true") in ("1","true","yes","on")`; `RTR_SUPPRESS_EMOJI = "👀"` re-used from `bridge.read_the_room`.
- Module docstring documenting the contract, the deterministic-vs-LLM tradeoff, and the SDLC-scoping decision.

### 2. Add `recent_sent_drafts` field on `AgentSession`
- **Task ID**: build-field
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py` (extend with field roundtrip + cap)
- **Assigned To**: redundancy-filter-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `recent_sent_drafts = ListField(null=True)` to `models/agent_session.py` near `pm_sent_message_ids`.
- Add helper `record_recent_sent_draft(self, text: str, artifacts: dict, *, max_n: int = 3, preview_chars: int = 500) -> None` that appends `{ts: time.time(), text: text[:preview_chars], artifacts: artifacts}`, slices the list to the last `max_n` entries, then persists via **`self.save(update_fields=["recent_sent_drafts", "updated_at"])`** — never an unscoped `self.save()`. The `send()` flow (`agent/output_handler.py:479, 528`) already calls `session.save()` for `context_summary`, `expectations`, and `session_events` writes; an unscoped save here would clobber concurrent writes from those paths. The `update_fields=` pattern is the project precedent (see `agent/agent_session_queue.py:457` and `:608`). Wrap the save in a `try/except Exception` that logs `logger.warning("record_recent_sent_draft save failed for session %s: %s", ...)` and **does not raise** — matches the posture of `record_pm_message` (`models/agent_session.py:1435-1441`).
- Inline comment block documenting the cap, the per-entry preview length, and the FIFO policy.
- Verify the `_AGENT_SESSION_FIELDS` list in `agent/agent_session_queue.py` (line 181 area) is updated if the existing pattern preserves explicit field lists across the session-job boundary.

### 3. Wire the filter into `TelegramRelayOutputHandler.send`
- **Task ID**: build-wiring
- **Depends On**: build-filter, build-field
- **Validates**: `tests/unit/test_output_handler.py::TestRedundancyFilterWiring` (create)
- **Assigned To**: redundancy-filter-builder
- **Agent Type**: builder
- **Parallel**: false
- After `draft_message()` returns and *before* the existing RTR call:
  - If `not SUPPRESSION_ENABLED` or `not session` or `not getattr(session, "is_sdlc", False)`, skip the filter and proceed to the RTR call (unchanged path). Use `getattr` with default `False` so `is_sdlc`'s `@property` access is safe even on minimal/test session objects that don't expose it.
  - Else call `should_suppress(draft.text or text, draft.artifacts or {}, session.recent_sent_drafts or [], draft.expectations, session.status)`.
  - On `suppress`: queue a `👀` reaction via `_build_reaction_payload` + `r.rpush` to `telegram:outbox:{session_id}` (mirroring `_rtr_queue_reaction`); emit `drafter.suppressed_redundant` event with `{type, ts, chat_id, reason, draft_preview, matched_prior_preview, jaccard}`; return without writing the text. Fall through to send the original text if `reply_to_msg_id is None` (mirroring RTR's no-anchor contract); emit `drafter.suppress_fallthrough` with `reason="no_reply_anchor"`.
  - On `send`: fall through to the existing RTR + outbox path unchanged.
- After a successful outbox `r.rpush` (text path only), call `session.record_recent_sent_draft(delivery_text, artifacts)`.
- Wrap the new branch in a `try/except Exception` that logs a warning and falls through to the existing RTR + outbox path on any error.

### 4. Author tests
- **Task ID**: build-tests
- **Depends On**: build-filter, build-wiring
- **Assigned To**: redundancy-filter-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- `tests/unit/test_redundancy_filter.py`: cover `should_suppress` for all five termination conditions, threshold edge cases (0.64 → send, 0.66 → suppress at default 0.65), empty/None inputs, error fallback.
- `tests/unit/test_output_handler.py::TestRedundancyFilterWiring`: assert SDLC + redundant draft → 👀 reaction queued, no text in outbox; non-SDLC → filter bypassed, RTR runs; `recent_sent_drafts` appended after successful send; failed save does not block `rpush`.
- `tests/integration/test_message_drafter_integration.py`: SDLC end-to-end — three identical drafts produce one text message and two reactions.
- `tests/unit/test_agent_session.py`: `recent_sent_drafts` roundtrip, FIFO cap, preview-length cap.

### 5. Validate
- **Task ID**: validate-filter
- **Depends On**: build-tests
- **Assigned To**: redundancy-filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_redundancy_filter.py tests/unit/test_output_handler.py tests/unit/test_agent_session.py tests/integration/test_message_drafter_integration.py -x -q`.
- Verify all RTR tests still pass (`pytest tests/unit/test_read_the_room.py tests/unit/test_output_handler.py::TestReadTheRoomWiring`).
- Confirm the kill switch works: set `DRAFTER_REDUNDANCY_SUPPRESSION_ENABLED=false` in a temp env, run the SDLC integration test, assert text is sent (no suppression).
- Lint check: `python -m ruff check bridge/redundancy_filter.py agent/output_handler.py models/agent_session.py`.
- Format check: `python -m ruff format --check .`.

### 6. Document
- **Task ID**: document-feature
- **Depends On**: validate-filter
- **Assigned To**: redundancy-filter-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/drafter-redundancy-suppression.md` (sections: What it does, Where it lives, Verdicts, Termination conditions, Observability, Bypass conditions, Failure modes, Configuration, Relationship to RTR).
- Add a row to `docs/features/README.md` index table.
- Update `docs/features/bridge-worker-architecture.md` § Path A flow (around line 42) to show the redundancy filter sitting before RTR for SDLC sessions.
- Update `docs/features/message-drafter.md` to reference the new suppression layer.
- Update `docs/features/read-the-room.md` § "Adjacent layers" noting that the redundancy filter covers SDLC sessions deterministically.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: redundancy-filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit -x -q -n auto`.
- Verify all Success Criteria checkboxes.
- Confirm `docs/features/README.md` has the new entry and links resolve.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_redundancy_filter.py tests/unit/test_output_handler.py tests/unit/test_agent_session.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_message_drafter_integration.py -x -q` | exit code 0 |
| RTR tests still pass | `pytest tests/unit/test_read_the_room.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/redundancy_filter.py agent/output_handler.py models/agent_session.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/redundancy_filter.py agent/output_handler.py models/agent_session.py` | exit code 0 |
| Module imports | `python -c "from bridge.redundancy_filter import should_suppress, SuppressionVerdict"` | exit code 0 |
| Field present | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'recent_sent_drafts')"` | exit code 0 |
| Docs index updated | `grep -F 'drafter-redundancy-suppression' docs/features/README.md` | output > 0 |

## Critique Results

**Round 1 verdict:** NEEDS REVISION (3 blockers, 6 concerns, 2 nits)
**Recorded:** 2026-04-30T07:50:28Z via `sdlc-tool verdict set`
**Critique session:** `0_1777531971456` (transcript: `logs/worker/0_1777531971456.log`)

> Note: the war-room session captured the high-level summary in its worker output but did not write the full per-critic findings into this plan file. The three blockers below are reconstructed from the worker output and verified directly against the codebase as part of this revision pass. The 6 concerns and 2 nits could not be recovered from the session logs and are therefore deferred to the next critique cycle (Round 2 will re-run war-room critics on the revised plan and capture findings inline).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Archaeologist | `session.sdlc_slug` does not exist on `AgentSession`. The only related fields are `slug`, `work_item_slug`, and the `is_sdlc` `@property` (`models/agent_session.py:1612`). As written, the filter's SDLC scoping guard would never fire — every SDLC session would be skipped because `getattr(session, "sdlc_slug", None)` always returns `None`. This silently disables the entire feature. | Recon (line 60), Technical Approach SDLC scoping bullet, Step 3 wiring guard | Replace every `sdlc_slug` reference with `is_sdlc`. Use `getattr(session, "is_sdlc", False)` in the wiring guard so test sessions that don't expose the property still default to "skip filter". Note that RTR (`bridge/read_the_room.py:400`) has the same latent bug — its bypass never fires either. Fixing RTR is **out of scope** for this plan (tracked as a separate follow-up); we just must not inherit the broken pattern. |
| BLOCKER | Archaeologist | `extract_artifacts` returns `{commits, urls, files_changed, test_results, errors}` (lines 390-435 of `bridge/message_drafter.py`), not `{commit_hashes, pull_requests, urls, issue_refs}` as the plan claimed. The "new artifact" detection logic in Technical Approach is keyed off non-existent dict keys and would never find a PR or issue ref. This breaks Success Criterion 2 (new PR URL artifact suppresses correctly). | Recon (line 64), Technical Approach Artifact-set bullet | Use the real keys. PR and issue links land in `urls` already (e.g., `https://github.com/.../pull/N`). Define "new artifact" as the union of *all* values across *all* keys: `set().union(*new_dict.values())`. Suppress only when the new union is a subset of the prior union. |
| BLOCKER | Operator | `record_recent_sent_draft` plan calls `self.save()` unscoped. The `send()` flow already issues two `session.save()` calls (lines 479 and 528 of `agent/output_handler.py`) for `context_summary` / `expectations` and for `session_events`. An unscoped save in the new helper races those writers and can clobber concurrent field writes. The project precedent is `update_fields=[...]` (`agent/agent_session_queue.py:457` and `:608`). | Step 2 helper signature, Failure Path Test Strategy | Call `self.save(update_fields=["recent_sent_drafts", "updated_at"])` and wrap in `try/except` matching `record_pm_message`'s posture (warn-and-continue, no raise). Add a unit test asserting the helper passes `update_fields=` to `save()`. |
| CONCERN | (deferred — see note above) | 6 concerns from war-room critics (Skeptic / Adversary / Simplifier / User / Operator) were not recovered from the session log. | Round 2 critique | Re-run `/do-plan-critique` on the revised plan; capture all findings inline this time. |
| NIT | (deferred — see note above) | 2 nits not recovered from the session log. | Round 2 critique | Re-run `/do-plan-critique`; capture inline. |

## Revision Notes

**Round 1 (2026-04-30):** Three structural blockers identified by the Archaeologist + Operator critics. All three were factual code-vs-plan mismatches that would have caused silent failures or write races at build time. Each was verified directly against the cited source files before applying the fix:

- **B1 — `sdlc_slug` field does not exist**: Verified by `grep -n "sdlc_slug\|is_sdlc" models/agent_session.py` (no `sdlc_slug` matches; `is_sdlc` exists at line 1612 as a `@property`). Updated 4 plan locations: Recon "Confirmed" entry (line 60), Recon "Confirmed" entry for `extract_artifacts` (line 64), Technical Approach SDLC-scoping bullet (line 153), Step 3 wiring guard (line 361).
- **B2 — Wrong artifact dict keys**: Verified by reading `extract_artifacts` (`bridge/message_drafter.py:390-435`). Real keys: `commits`, `urls`, `files_changed`, `test_results`, `errors`. Updated 2 plan locations: Recon entry (line 64) and Technical Approach Artifact-set bullet (line 150).
- **B3 — Unscoped `self.save()` race**: Verified by `grep -n "session.save\|self.save" agent/output_handler.py` (two unscoped saves at lines 479 and 528 within the same `send()` flow that the new helper will be called from). Verified the project's `update_fields=` precedent at `agent/agent_session_queue.py:457`. Updated Step 2 helper signature (line 349) and Failure Path Test Strategy (line 163) to require `update_fields=["recent_sent_drafts", "updated_at"]` and a test assertion that the helper passes `update_fields=` to `save()`.

The 6 concerns and 2 nits could not be recovered from the critique session's transcript (only the high-level summary survived in `logs/worker/0_1777531971456.log`). Round 2 will re-run `/do-plan-critique` on this revised plan and capture every finding inline in the Critique Results table.

**`revision_applied: true`** is set in the frontmatter so the SDLC router's Row 4c rule will route the next dispatch directly to `/do-build` once Round 2 returns READY TO BUILD.

---

## Open Questions

1. **Suppression scope.** Should the filter apply only to SDLC sessions (`session.is_sdlc` True) as proposed, or to ALL PM sessions including conversational/Teammate ones? The risk of expanding beyond SDLC is intersection with RTR (which already covers non-SDLC) — RTR is opt-in, so there is currently a gap for non-SDLC chats with `READ_THE_ROOM_ENABLED=false`. Default proposal: SDLC only; expand later if observability shows the gap matters.
2. **Threshold default.** Bigram Jaccard `J ≥ 0.65` is a reasonable starting point but untuned. Should we ship with a stricter default (`0.70`) to bias toward false negatives, or looser (`0.60`) to bias toward false positives? My recommendation: ship at `0.65`, watch the `drafter.suppressed_redundant` event log on the dev machine for a week, tune from there.
3. **Window size.** Default `DRAFTER_REDUNDANCY_WINDOW_SECONDS = 600` (10 minutes). Does that match the observed cadence (the issue showed ~5-minute resume cycles, so 600s catches ~2 prior turns)? Going to 1800s (30 min) catches longer waits but increases the chance of suppressing a genuinely material follow-up an hour later.
4. **Reaction emoji.** I chose `👀` (matches RTR's `RTR_SUPPRESS_EMOJI` and the `feedback_emoji_over_acks.md` precedent). Should we use a *different* emoji to distinguish "redundancy-filter suppressed" from "RTR suppressed" in the UI? My take: same emoji is fine — both mean "still working, nothing new" from the user's perspective; they only differ in the implementation layer, which the user doesn't care about.
5. **Recent drafts persistence.** Field on `AgentSession` (proposed) keeps everything session-scoped. Alternative: a Redis sorted set keyed by `chat_id`. Field is simpler and matches how `pm_sent_message_ids` is stored. Confirm: stick with the field?
