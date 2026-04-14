---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/949
last_comment_id:
revision_applied: true
---

# Reply-Thread Context Hydration

## Problem

When someone in Telegram replies to Valor — using the native "Reply" feature — or sends a message whose text obviously references prior conversation ("did we get that fixed?", "what about the bug?"), the agent frequently responds as if nothing was said before. It asks for clarification on a topic that is literally ten messages up in the same chat.

The machinery to fix this already exists in the codebase: `fetch_reply_chain`, `format_reply_chain`, `build_conversation_history`, `valor-telegram`. They are just not reliably wired in at the moments they are needed most.

**Concrete incident (2026-04-14 11:54, PM: PsyOPTIMAL):** Tom replied to a Valor message with "can you check and see if we got this fixed?". The live handler took the resume-completed branch (`bridge/telegram_bridge.py:1303-1343`), resumed session `_89` (a 2-second prior session whose only state was a `CLI harness not found` error), fed the new agent an empty `context_summary`, and got clarification-begging back. The information was available in three places: the Telegram reply chain, the chat history, and the prior session's transcript. None of it was given to the agent.

**Current behavior:**

1. **Resume-completed branch loses the thread.** `_build_completed_resume_text` (`bridge/telegram_bridge.py:619-636`) injects *only* `context_summary` — a single-sentence field that is often empty or useless (e.g., after an errored session). The actual reply-chain messages are not included. The re-enqueued session passes `message_text=augmented_text` but omits `telegram_message_key`, so the deferred enrichment in `agent_session_queue.py:3461-3502` cannot find the stored `TelegramMessage` and silently drops the reply-chain fetch too.
2. **Reply-chain enrichment is conditional.** `enrich_message` (`agent/agent_session_queue.py:3485-3502`) only fires when `TelegramMessage.query.filter(msg_id=session.telegram_message_key)` returns a hit. On a miss it silently skips — no fallback that derives `reply_to_msg_id` from the live Telegram message.
3. **No implicit-context signal.** Messages without `reply_to_msg_id` whose text references prior state (deictic pronouns: "this", "that", "the bug", "we", "still", "again") get no special treatment. The `build_conversation_history` docstring at `bridge/context.py:237-246` says the agent *should* use `valor-telegram` in these cases — but that is a suggestion in a docstring, not a guarantee.

**Desired outcome:**

- Any Telegram message with `reply_to_msg_id` set results in an agent prompt that includes a `REPLY THREAD CONTEXT` block — always, regardless of which branch the bridge took.
- The resume-completed branch carries the reply-thread context *in addition to* `context_summary`, not one or the other.
- Messages that reference prior context without a reply-to still get help: the agent receives a system-prompt directive telling it to fetch chat history before answering.
- Replaying Tom's 11:54 message against the new code would surface either the prior session's transcript or the recent chat thread to the agent.

## Freshness Check

**Baseline commit:** `e422fc4e68ef03b9f93659609debc23ad8fe9e2d` (main at plan time)
**Issue filed at:** 2026-04-14T05:14:33Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1303-1340` — resume-completed branch — holds, verified in Read.
- `bridge/telegram_bridge.py:619-636` — `_build_completed_resume_text` — exact match.
- `agent/agent_session_queue.py:3485-3502` — reply-chain enrichment — exact match.
- `bridge/context.py:55-65` — `STATUS_QUESTION_PATTERNS` — exact match.
- `bridge/context.py:237-246` — `build_conversation_history` docstring — exact match.
- `bridge/context.py:294-358` — `fetch_reply_chain` — exact match.
- `bridge/context.py:361-403` — `format_reply_chain` — exact match.

**Cited sibling issues/PRs re-checked:**
- #567 — closed 2026-03-27 — established `resolve_root_session_id` which this plan preserves.
- #318 — closed — established steering inbox pattern (`queued_steering_messages`).
- #919 / PR #922 — closed 2026-04-13 — **directly adjacent prior art**. Introduced the resume-completed branch and `_build_completed_resume_text` that this plan extends. No conflict; this is a layered improvement on a fix that shipped two days ago.
- #948 — open — structural dedup cleanup of the same handler. No ordering dependency declared.
- #730 — closed — original resume branch work.

**Commits on main since issue was filed (touching referenced files):** none — `git log --since=2026-04-14T05:14:33Z -- bridge/telegram_bridge.py bridge/context.py bridge/enrichment.py agent/agent_session_queue.py` returned empty.

**Active plans in `docs/plans/` overlapping this area:** 
- `resume-hydration-context.md` — scope is PM-session git-log hydration on resume, complementary but distinct. No overlap. This plan covers Telegram reply-chain context for any session type.
- No other plans reference these files or address reply-thread hydration.

**Notes:** Issue is ~7 hours old and main has not moved in the interim. All file:line references hold. Proceed as premised.

## Prior Art

- **Issue #919 / PR #922** (merged 2026-04-13): *"Fix: deterministic reply-to root cache + completed session resume"* — introduced `_build_completed_resume_text` and the resume-completed branch that issue #949 is now extending. Success metric: stopped creating split sessions. Gap it left behind: `context_summary`-only hydration is too thin.
- **Issue #567 / PRs #573–576** (merged 2026-03-27): *"Reply-to should resume original AgentSession, not create a new one"* — introduced `resolve_root_session_id` and the reply-chain walk. Succeeded at canonicalizing session_id. This plan preserves that canonicalization; no changes to `resolve_root_session_id`.
- **Issue #318** (closed): *"Semantic Session Routing: Structured Summarizer + Context-Aware Message Routing"* — established the `queued_steering_messages` pattern the steering fast-path uses. Steering fast-path continues to handle reply-to-running sessions; this plan touches only the paths that *don't* land on a running session.
- **Issue #274** (closed): *"Semantic Session Routing: Context-Aware Message Routing"* — earlier exploration of routing intent. Informs the implicit-context heuristic approach: keep it small and high-precision, not semantic.
- **Issue #725** (closed): `valor-telegram` CLI work — the tool the agent is already expected to reach for. This plan makes the agent's prompt explicitly instruct it to use the tool.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #922 (#919) | Added the resume-completed branch with `_build_completed_resume_text` | Only wired up `context_summary`, did not propagate `telegram_message_key` through the re-enqueue call, so deferred enrichment cannot hydrate the reply chain for resumed sessions. Was a minimum fix for the "split session" symptom; did not address the broader "agent has no thread context" pain. |
| Enrichment pipeline (pre-existing) | Added `enrich_message` with reply-chain fetch as step 4 | Reads enrichment params exclusively from `TelegramMessage` by key. Any path that enqueues without a key (resume-completed branch; any future enqueue point) silently drops all enrichment — including reply chain — with only a DEBUG log. No hot-path fallback. |

**Root cause pattern:** Both fixes treat enrichment as a property of one specific session-creation path rather than a property of *any* message with a `reply_to_msg_id`. The enrichment piggy-backs on `telegram_message_key`; any enqueue path that forgets to set it loses enrichment silently. The fix is to make reply-chain hydration an explicit, named parameter of `enqueue_agent_session` that the bridge handler fills in *regardless* of which branch it took.

## Architectural Impact

- **New dependencies**: None. All building blocks (`fetch_reply_chain`, `format_reply_chain`, `STATUS_QUESTION_PATTERNS`) already exist.
- **Interface changes**:
  - `enqueue_agent_session` gains an optional `reply_to_msg_id: int | None` parameter. Passed through to `AgentSession` or used at enqueue time to pre-hydrate `message_text`.
  - `_build_completed_resume_text` gains an optional `reply_chain_context: str | None` parameter (or equivalent — see Technical Approach).
  - `bridge/context.py` gains a `references_prior_context(text: str) -> bool` helper alongside `is_status_question`.
- **Coupling**: Slightly increases coupling between the bridge handler and `bridge/context.py` (handler now calls `fetch_reply_chain` directly in the resume path). This is a deliberate trade: reliability beats the current "implicit via TelegramMessage cache" coupling.
- **Data ownership**: No changes. `AgentSession` remains the carrier. `TelegramMessage` remains the optional enrichment cache.
- **Reversibility**: High. All changes are additive or pass-through. The implicit-context directive is a prompt string that can be removed in a single commit.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder), code reviewer.

**Interactions:**
- PM check-ins: 1 (confirm the implicit-context heuristic keyword list before ship)
- Review rounds: 1

Three coordinated changes across two files, each localized. Existing test infrastructure (`tests/integration/test_steering.py`) already has fixtures for reply-to scenarios. The risk is not size, it is the implicit-context heuristic precision — one PM review pass to lock in the keyword list.

## Prerequisites

No prerequisites — this work has no external dependencies. The Telegram client is already available via `get_telegram_client()`; all other primitives exist.

## Data Flow

### Change A — Always hydrate the reply thread

**Current flow (resume-completed path, gap visible):**

1. Telegram message arrives with `reply_to_msg_id=42`
2. `resolve_root_session_id` resolves to a completed session
3. Handler takes resume-completed branch (`bridge/telegram_bridge.py:1303-1343`)
4. `_build_completed_resume_text(completed, clean_text)` produces `"[Prior session context: <summary>]\n\n<text>"` — **reply chain not fetched**
5. `enqueue_agent_session(..., message_text=augmented_text)` — **no `telegram_message_key` passed**
6. Worker's deferred enrichment (`agent_session_queue.py:3461`) finds no key, skips reply-chain fetch
7. Agent receives only `context_summary` preamble

**New flow:**

1. Telegram message arrives with `reply_to_msg_id=42`
2. `resolve_root_session_id` resolves to a completed session
3. Handler takes resume-completed branch
4. **New**: Handler fetches reply chain directly via `fetch_reply_chain(client, event.chat_id, message.reply_to_msg_id)` and calls `format_reply_chain` to get the `REPLY THREAD CONTEXT` block
5. `_build_completed_resume_text(completed, clean_text, reply_chain_context=...)` produces `"[Prior session context: <summary>]\n\n<REPLY THREAD CONTEXT>\n\n<text>"`
6. `enqueue_agent_session(..., message_text=augmented_text, telegram_message_key=stored_msg_id)` — key passed so *other* enrichments (media, links) still work
7. Worker's deferred enrichment sees `reply_to_msg_id` already hydrated in message_text; does not double-fetch (see Race 1 below)
8. Agent receives context summary + reply thread + current message

### Change B — Richer resume context

Same flow as Change A for the reply thread. Additionally: when the prior session's last-N transcript turns are available (check session log files under `logs/sessions/{session_id}/`), append them after `context_summary`. If unavailable (common — logs may have been rotated), silently fall back to `context_summary` + reply-thread. This is a best-effort addition; the reply-thread is the primary carry.

### Change C — Implicit-context heuristic + prompt directive

1. Telegram message arrives with `reply_to_msg_id=None`
2. Handler calls `references_prior_context(clean_text)` in addition to the existing `is_status_question` check
3. If either matches, handler prepends a system directive to `message_text` before enqueue:
   ```
   [CONTEXT DIRECTIVE] This message references context not in the current turn. Use these tools in order until you have the context you need:
   1. `valor-telegram read --chat "<chat_title>" --limit 20` to fetch recent chat history
   2. Memory search via `memory_search`
   3. The project knowledge base
   4. `gh issue list` / `gh pr list` if an issue or PR is implied
   ```
4. The agent decides whether to act on the directive — false positives cost one agent turn at most, not a broken response

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] Reply-chain fetch in the handler must be wrapped in try/except and log a WARNING on failure (not silent). On failure, fall back to the existing `_build_completed_resume_text` behavior (summary-only). Test: simulate a `fetch_reply_chain` raising `ConnectionError`; assert logger.warning was called and the session still enqueues with the summary-only preamble.
- [x] Implicit-context detection must not crash on empty/None text. Test: `references_prior_context("")` returns `False`, `references_prior_context(None)` returns `False` (or raises TypeError and is guarded by caller — pick one and test it).
- [x] Existing `except Exception: pass` at `agent_session_queue.py:3483` remains but must be accompanied by a debug log confirming the fallback fired. Already present — verify unchanged.

### Empty/Invalid Input Handling

- [x] `fetch_reply_chain` returns an empty list when the replied-to message was deleted. Test: mock client.get_messages to return None; assert chain is empty and `format_reply_chain([])` returns "" so the augmented text is unchanged.
- [x] Agent receives empty string from `format_reply_chain([])` — must not cause a double-blank-line or malformed preamble. Test: assert `_build_completed_resume_text(session, "hi", reply_chain_context="")` produces the same output as `_build_completed_resume_text(session, "hi")`.
- [x] `references_prior_context` must return False for whitespace-only input.

### Error State Rendering

- [x] If reply-chain fetch fails, the user still gets an agent response (just without the extra context). No user-visible error should be rendered. Integration test: force `fetch_reply_chain` to raise; assert session completes normally.
- [x] `logger.warning` message must include `session_id`, `chat_id`, and the exception message so it is greppable in `logs/bridge.log`.

## Test Impact

- [x] `tests/integration/test_steering.py::test_reply_to_completed_session_reenqueues_with_context` — UPDATE: assert the augmented text now also contains a `REPLY THREAD CONTEXT` block, not just `context_summary`.
- [x] `tests/integration/test_steering.py::test_reply_to_completed_session_fallback_without_summary` — UPDATE: same assertion for the fallback path (no summary but reply chain should still appear).
- [x] `tests/integration/test_steering.py` — ADD: new test `test_resume_completed_carries_reply_chain` that wires `fetch_reply_chain` through the handler and asserts the chain appears in the enqueued session's `message_text`.
- [x] `tests/integration/test_steering.py` — ADD: new test `test_implicit_context_directive_injected` asserting `references_prior_context("did we get this fixed?")` triggers the directive injection.
- [x] `tests/integration/test_steering.py` — ADD: new test `test_reply_chain_fetch_failure_falls_back` asserting a `fetch_reply_chain` exception does not block session enqueue.
- [x] `tests/integration/test_catchup_revival.py` — VERIFY (no changes expected): existing tests must still pass; the resume-completed branch is touched but only additively.
- [x] `tests/unit/` — ADD: unit tests for `references_prior_context` covering the keyword list (positive and negative cases).
- [x] `tests/unit/test_delivery_execution.py` — VERIFY: pattern-match tests for enrichment path must still pass; `telegram_message_key` is still passed on the normal path.

## Rabbit Holes

- **Auto-fetching chat history on the bridge side** for implicit-context messages. Tempting — feels more deterministic. Rejected: makes the bridge stateful, costs Telegram API calls for false positives, and duplicates what the agent can decide to do with the directive. Keep the bridge thin.
- **Semantic intent classification** for implicit-context detection. Tempting — a small LLM call would be more accurate than regex. Rejected for this appetite: adds latency, cost, and a new failure mode to the hot path. Regex keyword matching is high-precision enough for the 80% case.
- **Unconditional last-N transcript injection** from prior sessions. Tempting — "just dump the last 10 turns." Rejected: log rotation, PII concerns, size limits, and the fact that `context_summary` + reply-chain covers most of the signal. Treat transcript as a best-effort addition in Change B, not a primary carry.
- **Refactoring the enrichment pipeline** to not depend on `telegram_message_key`. Tempting — would make the whole pipeline uniform. Rejected: larger scope than #949 needs; `telegram_message_key` enrichment works for the normal path. Localized fix to the resume-completed branch is sufficient.
- **Revisiting `resolve_root_session_id`** semantics. Out of scope — covered by #919/#567 and stable.

## Risks

### Risk 1: Double-fetching the reply chain

**Impact:** If the handler pre-hydrates `message_text` with the reply chain AND the worker's deferred enrichment also fetches and prepends it, the agent sees the chain twice — wasted tokens and confusing format.

**Mitigation:** The handler should *either* pre-hydrate the text with `REPLY THREAD CONTEXT` (and skip the enrichment step for reply-chain), or mark the session so the worker skips the fetch. Concrete choice: the handler pre-hydrates and passes `telegram_message_key=None` *for the reply-chain enrichment specifically* — but this breaks media enrichment. Better: add an explicit `reply_chain_already_hydrated: bool` flag (or, since we prepend with the canonical `REPLY THREAD CONTEXT:` header, the worker checks for that string in `message_text` and skips the fetch). Locked in during implementation — confirm during build that exactly one path produces the block.

### Risk 2: Implicit-context heuristic false positives

**Impact:** Over-triggers the directive, agent wastes a turn calling `valor-telegram` when the message was self-contained ("let me think about this again" — "this" refers to the current message, not prior context).

**Mitigation:** Keep the keyword list small and multi-token (e.g., require "this" + a status-question marker, not "this" alone). PM check-in before ship to lock the list. Log every injection so we can audit false-positive rate post-ship.

### Risk 3: Telegram API rate limits from hot-path reply-chain fetches

**Impact:** The resume-completed branch now calls `fetch_reply_chain` synchronously. For a Telegram flood (rapid replies), this could hit rate limits or add latency.

**Mitigation:** `fetch_reply_chain` already has `max_depth=20` as a hard cap. Handler-side call runs inside the existing message handler which is already async and subject to Telethon's internal rate limiting. Defer actual cap-checking to the build; if latency becomes visible, move the fetch to a deferred path similar to the worker's current approach but gate on `reply_to_msg_id` being present in a new explicit field on `AgentSession`.

### Risk 4: Last-N transcript injection hits rotated logs

**Impact:** `Change B`'s best-effort transcript additions may reference log files that no longer exist after rotation, producing silent gaps.

**Mitigation:** Explicit existence check with fall-through to summary-only. Log at DEBUG when transcript is unavailable. No user-facing error.

## Race Conditions

### Race 1: Handler pre-hydration racing with worker deferred enrichment

**Location:** `bridge/telegram_bridge.py:1303-1343` (handler resume branch) and `agent/agent_session_queue.py:3485-3502` (worker enrichment).

**Trigger:** Handler prepends `REPLY THREAD CONTEXT:` to `message_text` and enqueues with `telegram_message_key=stored_msg_id`. Worker loads `TelegramMessage`, sees `reply_to_msg_id`, and runs `enrich_message` which *also* prepends a `REPLY THREAD CONTEXT:` block.

**Data prerequisite:** The handler's pre-hydration must be idempotent with the worker's enrichment — exactly one block should appear.

**State prerequisite:** The enqueued `message_text` must not contain the `REPLY THREAD CONTEXT:` header twice after worker processing.

**Mitigation:** Worker's `enrich_message` checks if `message_text` already starts with (or contains) the canonical header `"REPLY THREAD CONTEXT "` and skips step 4 if so. Simple string check — no new state. Alternative: add a new `AgentSession.reply_chain_hydrated: bool` field and gate on that. Choose the string check — smaller surface, reversible.

### Race 2: Concurrent reply-to messages resolving to the same completed session

**Location:** `bridge/telegram_bridge.py` resume-completed branch.

**Trigger:** Two rapid-fire replies land within the same event-loop tick. Both take the resume-completed branch. Both fetch reply chain. Both call `enqueue_agent_session` with the same `session_id`.

**Data prerequisite:** Only one session re-enqueue should land; or both should land but coalesce.

**State prerequisite:** Existing dedup via `record_message_processed` (line 1340-1342) already covers this — each message has a unique `event.chat_id, message.id` pair.

**Mitigation:** No new mitigation needed; existing dedup handles it. Verify during build that adding the reply-chain fetch before dedup does not widen the race window.

## No-Gos (Out of Scope)

- Changing `resolve_root_session_id` or any reply-chain walk logic (#919/#567 are stable).
- Auto-fetching chat history on the bridge side (rejected as a rabbit hole).
- Using LLM classification for implicit-context detection (appetite-incompatible).
- Unconditional injection of prior session transcript (deferred to best-effort in Change B).
- Modifying `enqueue_agent_session` in a way that breaks the normal happy path (e.g., making `reply_to_msg_id` required).
- Structural dedup cleanup — that is #948's scope.
- Adding a new MCP tool — the agent already has `valor-telegram` available.

## Update System

No update system changes required — this is a bridge-internal code change with no new dependencies, config files, or deployment topology shifts. The `/update` skill pulls and restarts as usual.

## Agent Integration

No new agent integration required. The agent already has access to `valor-telegram` via its existing tool surface. The implicit-context directive is a prompt-text addition that steers the agent's tool-choice behavior — it does not expose new tools or MCP capabilities.

One integration test verifies end-to-end: simulate a Telegram message matching the implicit-context heuristic, enqueue through the bridge, confirm the agent's first prompt contains the directive string. Test lives in `tests/integration/test_steering.py`.

## Documentation

### Feature Documentation

- [x] Update `docs/features/session-management.md` — the existing "Resume Completed Session" section must describe the new reply-chain hydration and the implicit-context directive.
- [x] Update `docs/features/bridge-module-architecture.md` if its handler flow diagrams show the resume-completed branch (verify during docs pass; update if so).
- [x] Create `docs/features/reply-thread-context-hydration.md` describing the three changes (A/B/C), the hydration rules, and the precedence between pre-hydration and deferred enrichment.
- [x] Add an entry to `docs/features/README.md` index table.

### Inline Documentation

- [x] Update `_build_completed_resume_text` docstring to describe the new `reply_chain_context` parameter and the layered preamble format.
- [x] Add a module-level docstring to `bridge/context.py`'s `references_prior_context` helper with examples of matching and non-matching text.
- [x] Update `build_conversation_history` docstring at `bridge/context.py:237-246` — remove the "NOT called by default" language now that the implicit-context directive exists.

### External Documentation Site

This repo does not publish external docs; skip.

## Success Criteria

- [x] A Telegram message with `reply_to_msg_id` set produces an agent prompt containing `REPLY THREAD CONTEXT` **in both the new-session branch and the resume-completed branch** (verified by integration test).
- [x] The resume-completed branch includes both `context_summary` (when present) AND reply-chain context (when present).
- [x] Messages matching `references_prior_context` and lacking `reply_to_msg_id` get a system-prompt directive instructing the agent to use `valor-telegram` first.
- [x] Replaying the 2026-04-14 11:54 incident through the new code path surfaces either the prior session's transcript or the chat thread context to the agent.
- [x] No double-hydration: the agent never sees two `REPLY THREAD CONTEXT` blocks.
- [x] No regressions: `tests/integration/test_steering.py` and `tests/integration/test_catchup_revival.py` pass.
- [x] New unit tests for `references_prior_context` pass.
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`).
- [x] Ruff clean (`python -m ruff check bridge/ agent/`).

## Team Orchestration

### Team Members

- **Builder (reply-chain-hydration)**
  - Name: reply-chain-builder
  - Role: Wire `fetch_reply_chain` into the resume-completed branch and extend `_build_completed_resume_text`
  - Agent Type: builder
  - Resume: true

- **Builder (implicit-context)**
  - Name: implicit-context-builder
  - Role: Add `references_prior_context` helper and wire the directive injection at the handler
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: hydration-test-writer
  - Role: Write integration and unit tests for all three changes, including failure paths
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: hydration-validator
  - Role: Verify acceptance criteria, replay the 11:54 incident, confirm no double-hydration
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: hydration-docs
  - Role: Update `session-management.md`, `bridge-module-architecture.md`, create new feature doc, update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build Change A + B — reply-chain hydration in resume-completed branch

- **Task ID**: build-reply-chain-hydration
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py::test_reply_to_completed_session_reenqueues_with_context`, `tests/integration/test_steering.py::test_resume_completed_carries_reply_chain` (new)
- **Informed By**: Prior art — PR #922 introduced `_build_completed_resume_text`; this task extends it without breaking its existing behavior.
- **Assigned To**: reply-chain-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `reply_chain_context: str | None = None` (or equivalent) parameter to `_build_completed_resume_text` in `bridge/telegram_bridge.py`.
- In the resume-completed branch (`bridge/telegram_bridge.py:1303-1343`), call `fetch_reply_chain` + `format_reply_chain` when `message.reply_to_msg_id` is set, wrap in try/except with WARNING log on failure.
- Pass the result into `_build_completed_resume_text`.
- Pass `telegram_message_key=stored_msg_id` in the resume-completed `enqueue_agent_session` call (currently omitted).
- In `agent/agent_session_queue.py:3485-3502` (enrichment), add an idempotency guard: if `message_text` already contains the canonical `REPLY THREAD CONTEXT` header, skip step 4 of `enrich_message` (or equivalent: check before calling).
- For Change B transcript best-effort: check `logs/sessions/{session_id}/` for recent turn snapshots; if available, append truncated last-N turns; if not, silently skip.

### 2. Build Change C — implicit-context heuristic + directive

- **Task ID**: build-implicit-context
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py::test_implicit_context_directive_injected` (new), `tests/unit/test_context_helpers.py::test_references_prior_context*` (new)
- **Assigned To**: implicit-context-builder
- **Agent Type**: builder
- **Parallel**: true (can run alongside task 1; different files)
- Add `references_prior_context(text: str) -> bool` to `bridge/context.py`, alongside `STATUS_QUESTION_PATTERNS`. Keep the pattern list small and high-precision: `STATUS_QUESTION_PATTERNS + deictic patterns` (e.g., `the bug`, `that issue`, `still broken`, `we fixed`, `last time`). Guard against empty/None input.
- In `bridge/telegram_bridge.py` message handler, after the reply-to checks, if `not message.reply_to_msg_id` and `references_prior_context(clean_text)` matches, prepend the context-directive string (defined in Data Flow section) to `clean_text` before enqueue.
- Update `build_conversation_history` docstring to remove the "NOT called by default" sentence and instead describe the directive-driven invocation.

### 3. Write tests (all three changes)

- **Task ID**: test-hydration
- **Depends On**: build-reply-chain-hydration, build-implicit-context
- **Assigned To**: hydration-test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Write integration tests in `tests/integration/test_steering.py` for all new test cases listed in the Test Impact section.
- Write unit tests in `tests/unit/test_context_helpers.py` (create if missing) for `references_prior_context`: positive cases (deictic + status patterns), negative cases (self-contained statements), edge cases (empty, None, whitespace-only).
- Add a failure-path test asserting that a `fetch_reply_chain` exception does not block enqueue and produces a warning log.
- Add the 11:54 replay as a concrete integration test: fabricate a completed session with empty `context_summary`, a reply-to message, and a mock chat history; assert the final `message_text` contains the reply-chain block.

### 4. Validate acceptance criteria

- **Task ID**: validate-hydration
- **Depends On**: test-hydration
- **Assigned To**: hydration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_steering.py tests/integration/test_catchup_revival.py tests/unit/test_context_helpers.py -x -q`.
- Grep the new code for double-hydration indicators; confirm exactly one `REPLY THREAD CONTEXT` block per agent prompt by inspecting a test-generated prompt.
- Walk through each Success Criteria item; check off in the plan.
- Report pass/fail.

### 5. Documentation

- **Task ID**: document-hydration
- **Depends On**: validate-hydration
- **Assigned To**: hydration-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-management.md` with the new resume-completed hydration rules.
- Create `docs/features/reply-thread-context-hydration.md` describing the three changes, precedence rules, and the implicit-context directive.
- Add the new doc to `docs/features/README.md` index table.
- Update inline docstrings as listed in Documentation section.

### 6. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-hydration
- **Assigned To**: hydration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`.
- Run `python -m ruff check bridge/ agent/`.
- Verify all Success Criteria checked.
- Confirm new feature doc is indexed and the existing `session-management.md` reflects the new behavior.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_steering.py tests/integration/test_catchup_revival.py tests/unit/test_context_helpers.py -x -q` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/ agent/` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/ agent/` | exit code 0 |
| Feature doc exists | `test -f docs/features/reply-thread-context-hydration.md` | exit code 0 |
| Feature doc indexed | `grep -q "reply-thread-context-hydration" docs/features/README.md` | exit code 0 |
| No double-hydration header | `grep -c 'REPLY THREAD CONTEXT' <sample-generated-prompt>` | output = 1 (per prompt) |

## Critique Results

### Verdict: READY TO BUILD (with concerns)

The plan passed critique with 0 blockers and multiple concerns. The Implementation Notes below translate each concern into concrete direction the builder must apply.

## Implementation Notes (from critique — MUST apply during build)

These notes are authoritative. The builder MUST follow them; deviation requires explicit rationale in the PR description.

### IN-1: Idempotency uses canonical header constant + explicit parameter (replaces string-magic)

- In `agent/agent_session_queue.py` (enrichment module, around line 3513-3565 depending on current main — re-verify at build time), introduce a module-level constant `REPLY_THREAD_CONTEXT_HEADER = "REPLY THREAD CONTEXT"`.
- Also export it from `bridge/context.py` (single source of truth — import it in the queue module).
- `enrich_message` MUST check `if REPLY_THREAD_CONTEXT_HEADER in message_text: return` BEFORE fetching the chain. Exact substring match, not regex. This replaces the "string check" hand-wave from Risk 1.
- Additionally, add an explicit `reply_chain_hydrated: bool = False` parameter to the enqueue path (or set a flag on the `AgentSession` record) so we are NOT relying on substring match alone. The belt-and-suspenders: flag is primary, header check is defensive.
- **Rationale:** Critique flagged the substring-only approach as fragile. A canonical constant plus explicit flag is both reviewable and resilient.

### IN-2: Drop Change B transcript injection for this PR

- Remove transcript-injection work (`logs/sessions/{session_id}/*_turn_*.json` glob) from the build scope. The actual log layout uses `transcript.jsonl` and the glob pattern proposed in the plan is wrong.
- Change B in this PR is **reply-chain + context_summary** only (still a richer-than-current carry). Transcript injection is deferred to a follow-up issue.
- Update `Step by Step Tasks` task 1 to drop the transcript bullet. Keep `context_summary` + reply-chain hydration only.
- **Rationale:** Critique flagged wrong glob pattern and layering-boundary concerns. Defer rather than ship half-correct.

### IN-3: Lock down `references_prior_context` heuristic list

- Initial patterns (narrow, high-precision):
  - Deictic + status: `r"\b(the|that)\s+(bug|issue|ticket|PR|pull request)\b"`, `r"\bstill\s+(broken|failing|crashing)\b"`, `r"\bwe\s+(fixed|shipped|merged|resolved)\b"`, `r"\blast\s+time\b"`, `r"\bas\s+I\s+(mentioned|said)\b"`, `r"\bdid\s+we\s+"`, `r"\bwhat\s+about\s+(that|the)\b"`.
  - Combined with existing `STATUS_QUESTION_PATTERNS`.
- Composition rule: match if ANY pattern hits (OR). Keep max ~10 patterns; resist expansion without empirical false-negative data from logs.
- Input contract: `references_prior_context(text: str) -> bool`.
  - `None` input: return `False` (explicit guard at the top of the function).
  - Empty string / whitespace-only: return `False`.
  - Non-string: return `False` (do not raise).
- Covered by explicit unit tests in `tests/unit/test_context_helpers.py`.

### IN-4: Early `is_message_processed` short-circuit

- In the bridge handler, move the `is_message_processed(chat_id, msg_id)` check to run **before** `fetch_reply_chain` on the resume-completed branch.
- Rationale: reply-chain fetch is an API call. Running it for a duplicate message wastes calls and widens Race 2's window.
- Verify during build that no dedup guarantees are violated (prior dedup happens at line 1340-1342; earlier short-circuit is purely additive tightening).

### IN-5: Env-var kill-switch + structured audit log for implicit-context directive

- Honor `REPLY_CONTEXT_DIRECTIVE_DISABLED` env var. When truthy, skip directive injection entirely (regardless of heuristic match). This gives us a 1-line rollback.
- When the directive IS injected, emit a structured log entry:
  ```python
  logger.info(
      "implicit_context_directive_injected",
      extra={
          "session_id": session_id,
          "chat_id": chat_id,
          "matched_patterns": matched_patterns_list,
          "text_preview": clean_text[:80],
      },
  )
  ```
- Use the structured `extra=` form so log-analysis tooling can aggregate false-positive rates.

### IN-6: Prefer deferred enrichment with explicit `reply_to_msg_id` parameter

- Instead of calling `fetch_reply_chain` synchronously from the handler hot path, the preferred pattern is:
  1. Handler extracts `message.reply_to_msg_id` from the live Telegram event.
  2. Handler passes it as an explicit `reply_to_msg_id: int | None` parameter to `enqueue_agent_session` (new param).
  3. Worker's enrichment sees the explicit param (bypassing the `TelegramMessage` cache lookup) and runs `fetch_reply_chain` asynchronously before the agent turn starts.
- This keeps the handler fast (no blocking API call) and still delivers the context to the agent BEFORE its first turn.
- **Exception:** the resume-completed branch currently builds `augmented_text` synchronously at enqueue time. If that augmentation MUST include the chain text inline (rather than deferring), wrap the fetch in `asyncio.wait_for(..., timeout=3.0)` and fall back to summary-only on timeout. Log with tag `RESUME_REPLY_CHAIN_FAIL` on failure/timeout.
- **Rationale:** Critique flagged the handler-side synchronous fetch as a latency risk. Deferred-with-explicit-param is both faster and cleaner. Sync-with-timeout is the fallback if deferred doesn't fit the resume path.

### IN-7: No-double-hydration regression test is required

- Add `tests/integration/test_steering.py::test_no_double_hydration_when_handler_prehydrates` as a first-class test (not optional).
- The test MUST: simulate handler pre-hydrating `message_text` with the canonical header, then run the worker enrichment path, then assert `message_text.count(REPLY_THREAD_CONTEXT_HEADER) == 1`.
- This is the regression guard for Risk 1 / Race 1.

### IN-8: Correct file:line citations (rolling)

- At build time, re-confirm exact line numbers for:
  - Resume-completed branch: was cited as `bridge/telegram_bridge.py:1303-1340`, verify current range.
  - `enrich_message` reply-chain call site: cite as `agent/agent_session_queue.py:3513-3565` (critique's corrected range) — re-verify against current main before build.
  - `bridge/enrichment.py:156-179` — if reply-chain wiring lives here in current code, use this range (critique noted it here, issue body cited the queue module — both possible depending on refactors).
- Any drift from these ranges in current main must be captured in a one-line note at the top of the build session's commit message.

### IN-9: Test name corrections

- `test_reply_to_completed_session_fallback_without_summary` does not exist as a standalone test in current main. Remove that UPDATE bullet from Test Impact and replace with an ADD bullet for the same name (create the test, don't update a non-existent one).
- Re-verify all cited test names with `grep -rn '<test_name>' tests/` before writing the task list for the test engineer.

### IN-10: Directive ordering vs subconscious memory auto-recall

- The subconscious memory system (see `docs/features/subconscious-memory.md` and `.claude/hooks/hook_utils/memory_bridge.py`) already auto-recalls on `UserPromptSubmit`. Its `<thought>` injection runs before the agent's first tool call.
- The implicit-context directive MUST be worded so it does NOT prescribe an unconditional `valor-telegram` call. Revised directive text:
  ```
  [CONTEXT DIRECTIVE] This message references context not in the current turn. If the auto-recalled memory below does not cover it, fetch additional context in this order: (1) valor-telegram read ..., (2) memory_search, (3) project knowledge base, (4) gh issue/gh pr. Skip this directive entirely if the prior context is obvious from the auto-recalled memory.
  ```
- This lets the agent early-exit when memory already covered the reference.

### IN-11: Rollback procedure (previously claimed "High" without description)

- **Rollback path:**
  1. Set env `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` to kill Change C without a deploy.
  2. Revert the PR's changes to `_build_completed_resume_text` and handler resume branch to restore summary-only hydration.
  3. `references_prior_context` and the header constant can stay — they are inert without the handler call sites.
- All three changes are additive to existing behavior. A full revert restores pre-PR behavior bit-for-bit (no schema changes, no migrations).

### IN-12: `references_prior_context(None)` locked to False

- Explicit unit test: `assert references_prior_context(None) is False`.
- Explicit unit test: `assert references_prior_context("") is False`.
- Explicit unit test: `assert references_prior_context("   ") is False`.
- Implementation: `if not text or not isinstance(text, str): return False` at the top of the function.

---

## Open Questions

1. **Implicit-context keyword list** — the heuristic's precision depends on its keywords. Proposed seed list: existing `STATUS_QUESTION_PATTERNS` + deictic patterns (`the bug`, `that issue`, `still broken`, `we fixed`, `last time`, `as I mentioned`). Should we start narrower (just "the bug" / "that issue" / "we fixed") to minimize false positives, or wider to maximize capture? Recommendation: start narrower and expand based on observed false-negative rate in logs.
2. **Transcript injection (Change B)** — should best-effort last-N-turns injection be included in the initial scope, or deferred to a follow-up? Recommendation: include if the log-file existence check is trivial (it is: `logs/sessions/{session_id}/*_turn_*.json`), otherwise defer.
3. **Idempotency mechanism for Race 1** — string-check on `REPLY THREAD CONTEXT` header vs. new `AgentSession` field. Recommendation: string check, as it requires no migration and is reversible. Confirm during build.
