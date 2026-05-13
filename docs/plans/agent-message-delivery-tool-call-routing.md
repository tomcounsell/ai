---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-05-13
tracking: https://github.com/tomcounsell/ai/issues/1369
last_comment_id: none
---

# Agent Message Delivery — Tool-Call Routing Through Canonical Handler

## Problem

The agent-message-delivery doc promises that tool-call deliveries inherit the same drafter / RTR / redundancy / promise-gate pipeline the silent worker path enjoys. In reality `tools/send_message.py` writes raw payloads straight to the Redis outbox, bypassing every filter except `linkify_references` and the promise gate. The agent's "final say" path is materially less safe than the path it's supposed to mirror.

**Current behavior:**
- `tools/send_message.py::_send_via_telegram` (lines 71-145) builds a payload and `rpush`es it to `telegram:outbox:{session_id}`. The drafter, redundancy filter, and RTR never run.
- `tools/send_message.py::_send_via_email` (lines 148-199) writes raw to `email:outbox:{session_id}`. The drafter that lives in `EmailOutputHandler.send` (`bridge/email_bridge.py:571-581`) is not reached.
- `TelegramRelayOutputHandler.send` (`agent/output_handler.py:249-552+`) runs drafter → redundancy filter → RTR → promise-gate-equivalents → outbox rpush. Only worker `output_cb` paths get this.
- A third path, `tools/send_telegram.py`, does run the drafter via `_draft_text`, so three paths disagree.
- `docs/features/agent-message-delivery.md:29` and `:50` claim the canonical contract is honored. It isn't.

**Desired outcome:**
The tool-call path runs the same pipeline as the silent worker path. The agent retains "final say" over content; the system retains its safety nets over channel compliance and conversation-appropriateness. The doc's claim becomes true.

## Decision: Option A — Route Through Canonical Handler

We choose **Option A** from the issue. Justification:

1. **#589 design intent — "agent has final say over content."** That phrase scopes the agent's authority to *what is said*, not to *how it's formatted for the channel* or *whether the moment is right*. The drafter (length/format compliance), RTR (read-the-room), and redundancy filter (no double-sends) all operate on properties the agent cannot self-enforce from prompt context alone — RTR uses a Haiku call against a chat snapshot the agent never sees, and the redundancy filter compares against `session.recent_sent_drafts` in Redis. Stripping these from the agent's deliberate-send path inverts the original feature's purpose: the *more* the agent participates, the *less* safety it gets.

2. **#1072 refactor intent — classification authority, not filter bypass.** #1072 removed the `delivery_action` / `delivery_text` fields and made `classify_delivery_outcome` (transcript-tail inspection) authoritative for routing the agent's choice. That refactor changed *how the choice is recognized*, not *what filtering the chosen payload receives*. The doc's still-accurate claim ("Tool-call payloads route through `TelegramRelayOutputHandler.send`") was the intended contract; the bypass appeared as a side effect when `send_message.py` was extracted as a standalone CLI without re-entering the handler. This is an oversight to correct, not a design to ratify.

3. **Precedent already exists.** `tools/send_telegram.py:71-99` already runs the drafter from a CLI tool. Calling into the handler's filtering pipeline from a CLI is a solved problem in this codebase.

4. **Option B locks in an inconsistency.** Picking B requires documenting that `send_telegram.py` drafts but `send_message.py` doesn't, and asking the agent to internalize Telegram's 4096-char limit, channel-native markdown rules, and RTR-equivalent judgment from prompt context every turn. That's a worse contract than fixing the three-line gap.

## Freshness Check

**Baseline commit:** d49c29b1
**Issue filed at:** 2026-05-09T22:22:10Z (≈3 days before plan, within the same week)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/send_message.py:71-145` — `_send_via_telegram` writes raw to Redis, no drafter — confirmed
- `tools/send_message.py:148-199` — `_send_via_email` writes raw to Redis, no drafter — confirmed
- `agent/output_handler.py:249-552+` — `TelegramRelayOutputHandler.send` runs drafter → redundancy → RTR — confirmed
- `bridge/email_bridge.py:571-581` — `EmailOutputHandler.send` runs drafter — confirmed
- `tools/send_telegram.py:71-99` — runs drafter via `_draft_text` — confirmed (third-path inconsistency)
- `docs/features/agent-message-delivery.md:29` and `:50` — drafter-routing claim still present — confirmed
- `agent/hooks/stop.py` — `classify_delivery_outcome` at lines 217-245 — confirmed

**Cited sibling issues/PRs re-checked:**
- #589 — original agent-controlled delivery tracking issue, closed
- #1058 — PM final delivery via `agent.session_completion._deliver_pipeline_completion`, merged
- #1072 — tool-call classification refactor, merged — origin of current divergence

**Commits on main since issue was filed (touching referenced files):**
- None touching `tools/send_message.py`, `agent/output_handler.py`, `bridge/email_bridge.py`, or `agent/hooks/stop.py`. Recent main commits (`d49c29b1`, `161b4c18`, `109355d9`) all touch unrelated areas.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** No drift; all line numbers in the issue still resolve correctly.

## Prior Art

- **Issue #589**: Original agent-controlled delivery — established the "agent has final say" framing. Closed.
- **Issue #1058**: Replaced `[PIPELINE_COMPLETE]` marker with `_deliver_pipeline_completion`. Established that some delivery paths legitimately bypass the review gate, but explicitly so and via dedicated code. Out of scope here but informs the principle that bypass-or-not is a per-path documented decision.
- **Issue #1072**: Refactored stop-hook delivery away from `delivery_action`/`delivery_text` fields toward tool-call classification. The bypass dates from this refactor's CLI extraction, not from an intentional design pivot.
- **`tools/send_telegram.py`**: Pre-existing CLI tool that already runs the drafter from a CLI context — proves the integration pattern works.

## Research

This is purely internal — no external library, API, or ecosystem pattern is involved. The change re-points one in-process call. No web research needed.

## Data Flow

Before (current):
1. **Entry**: Agent invokes `python tools/send_message.py "<text>"` during second stop
2. **`send_message.py`**: linkifies, runs promise gate, `rpush` to `telegram:outbox:{session_id}`
3. **Telegram relay**: pops from outbox, sends via Telethon (with a >4096-char belt-and-suspenders fallback in `bridge/telegram_relay.py:374`)
4. **Output**: Raw payload reaches user, drafter/RTR/redundancy never ran

After (Option A):
1. **Entry**: Agent invokes `python tools/send_message.py "<text>"` during second stop
2. **`send_message.py`**: Resolves session, loads `AgentSession` from Popoto, calls `TelegramRelayOutputHandler.send(chat_id, text, reply_to_msg_id, session)` (or `EmailOutputHandler.send` for email transport)
3. **Handler**: drafter → redundancy filter → RTR → outbox rpush (existing pipeline, no changes)
4. **Telegram relay / email relay**: unchanged
5. **Output**: Drafter-normalized payload reaches user; RTR-suppressed payloads are reacted-to with 👀 and skipped; redundant repeats are reacted-to and skipped

## Architectural Impact

- **New dependencies**: `tools/send_message.py` gains an import of `TelegramRelayOutputHandler` / `EmailOutputHandler` and the Popoto `AgentSession` model. Coupling the tool to bridge handler internals is the explicit trade-off accepted in choosing Option A.
- **Interface changes**: No public signature changes. `send_message.py` arg surface stays identical.
- **Coupling**: Increases tool → handler coupling. We mitigate this by reusing the existing public `send()` method (no internal refactor needed) and by keeping the tool's own arg parsing / file validation / linkify steps untouched.
- **Data ownership**: Unchanged. Handler still owns the Redis outbox write.
- **Reversibility**: Trivially reversible — revert the two `_send_via_*` function bodies.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (clear-cut bug fix with a documented decision)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Required by handler's outbox write and by tests |

## Solution

### Key Elements

- **`tools/send_message.py::_send_via_telegram`**: rewrite body to instantiate (or reuse) `TelegramRelayOutputHandler` and `await` its `send()` instead of writing directly to Redis. Preserve file-validation and linkify steps that already live in the tool (they're orthogonal to the handler pipeline).
- **`tools/send_message.py::_send_via_email`**: rewrite body to call `EmailOutputHandler.send` (`bridge/email_bridge.py:462+`). Same shape; subject/in-reply-to are read from env by the tool and passed via session `extra_context`, or loaded from the persisted `AgentSession` record.
- **`AgentSession` lookup**: the tool runs in its own process, so it must reconstitute the session from Popoto using `VALOR_SESSION_ID`. Both handlers take `session` as the fourth positional arg.
- **Async entry point**: handlers are `async def`; the CLI uses `asyncio.run(...)` once per invocation.
- **`docs/features/agent-message-delivery.md`**: Fold in the doc-only fixes from the issue (VALOR_TRANSPORT enumeration, stale e2e test reference at :77, cross-links to `redundancy_filter.py` and `read_the_room.py`, canonical-term normalization).

### Flow

Agent's second stop → `classify_delivery_outcome` sees `tools/send_message.py` invocation → `send` outcome → session completes → on subsequent runs the tool routes via canonical handler → drafter normalizes → redundancy filter inspects → RTR judges → outbox write → relay sends.

### Technical Approach

- **Handler reuse, not duplication.** Both `TelegramRelayOutputHandler` and `EmailOutputHandler` already expose a public `async def send(chat_id, text, reply_to_msg_id, session)`. The tool calls these directly. No handler refactor.
- **Session reconstitution.** The tool reads `VALOR_SESSION_ID` from env, calls `AgentSession.query.filter(session_id=...).first()`. If the session is missing (test/dev scenarios), fall through to the existing raw outbox write with a logged warning — never block delivery (mirrors the handler's own fail-open posture).
- **File attachments.** Telegram file paths today are validated in the tool and stuffed into the outbox payload's `file_paths` key. `TelegramRelayOutputHandler.send` already handles `draft.full_output_file` and the relay reads `file_paths` from the rpushed dict, so the cleanest route is: keep the tool's file validation; pass validated paths to the handler via a thin extension OR rpush the file_paths separately after the handler's drafter-only call. Spike during build to pick the shorter diff; default plan is "extend `TelegramRelayOutputHandler.send` to accept an optional `file_paths` arg" (additive, no behavior change for current callers).
- **Email subject / in-reply-to.** Currently `_send_via_email` reads `EMAIL_REPLY_TO`, `EMAIL_SUBJECT`, `EMAIL_IN_REPLY_TO` from env and writes them into the payload. `EmailOutputHandler.send` reads the same fields from `session.extra_context`. The tool already has access to both env (its own) and the session (after reconstitution); pass-through via `extra_context` is the existing contract.
- **Promise gate.** `cli_check_or_exit` stays in the tool — it's a CLI-side gate that exits the process before any handler call, and removing it would silently change agent behavior. Promise-gate-equivalents inside the handler (narration fallback) still run.
- **Linkify.** Keep `linkify_references` in the tool — it runs on raw text *before* the drafter, which is the existing order in the silent path (the drafter consumes already-linkified text from agent transcripts via the same mechanism).
- **Doc updates.** Done in the same PR as the code: rewrite no lines at :29 / :50 (they become true); add VALOR_TRANSPORT enumeration to the Activation Rules section; fix :77 stale test reference; cross-link `bridge/redundancy_filter.py` and `bridge/read_the_room.py`; pick "review gate" as the canonical term and normalize.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/send_message.py` currently has `try/except Exception: pass` around `linkify_references` (line 116) — covered by an existing test, not in scope. New: any new `try/except` around handler invocation must log a warning and fall through to the raw outbox write; assert via `caplog` that the warning fires.
- [ ] `TelegramRelayOutputHandler.send` has multiple `try/except` blocks (drafter, redundancy filter, RTR) — these are pre-existing and not modified by this work. No new assertions needed beyond the existing tests.

### Empty/Invalid Input Handling
- [ ] Empty text: tool's `argparse` rejects empty text+empty files combination today (line 244). Behavior unchanged.
- [ ] Missing `VALOR_SESSION_ID`: tool exits with error today. Same after the change.
- [ ] Missing `AgentSession` row in Popoto (race / dev scenario): assert tool logs a warning and falls back to the legacy raw-outbox path (i.e., behavior matches today's path). Test covers this.

### Error State Rendering
- [ ] Drafter exception inside the handler: handler already falls back to raw text. Confirm via existing handler tests; no new assertion.
- [ ] RTR suppression with no anchor: handler already falls through to send-original. No new assertion.
- [ ] Redis write failure inside the handler: handler logs and returns; the agent's CLI invocation should report a nonzero exit so the harness sees the failure. Add assertion.

## Test Impact

- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting `_send_via_telegram` invokes `TelegramRelayOutputHandler.send` (mock the handler, assert call args). The existing `classify_delivery_outcome` tests remain unchanged.
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting an RTR-suppressed draft (mock `read_the_room` to return `suppress`) produces an empty `telegram:outbox:{session_id}` after invoking the tool.
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting a drafter-normalized payload (mock `draft_message` to return revised text) lands in the outbox instead of the raw input.
- [ ] `tests/unit/test_stop_hook_review.py` — No change. This file tests the stop hook itself; the tool's routing is orthogonal.
- [ ] `tests/unit/test_duplicate_delivery.py` — No change. Redundancy at this layer is the relay's catchup dedup, not the handler's redundancy filter.
- [ ] `tests/unit/test_qa_handler.py` — No change.
- [ ] `tests/e2e/test_message_pipeline.py` — REPLACE the stale reference at `docs/features/agent-message-delivery.md:77` (this is a doc-only fix; the test file itself is not modified).
- [ ] No existing test asserts the legacy raw-outbox shape of `_send_via_telegram` directly, so nothing breaks at the contract boundary.

## Rabbit Holes

- **Refactoring the handler to make it CLI-friendlier.** Don't. Handler.send is already a public coroutine; calling it from `asyncio.run` is two lines. A "CLI-friendly façade" is over-engineering for two callers.
- **Removing `tools/send_telegram.py`.** It's the third path and the inconsistency is real, but consolidating it is a separate change — PM self-messaging routes through it and has its own contract. Tracked elsewhere if needed.
- **Adding the drafter to `react_with_emoji.py`.** Reactions are not text. Excluded by the issue. Skip.
- **Rewriting the doc from scratch.** The doc's claim at :29 / :50 becomes true after this change; the only edits are the enumerated doc-only fixes from the issue. Don't rewrite what becomes correct on its own.

## Risks

### Risk 1: Handler call from CLI changes timing semantics
**Impact:** Handler.send is `async`; the CLI must `asyncio.run` it. If the handler holds an event-loop-bound resource (e.g., a singleton Redis client cached on an unrelated loop), the per-invocation loop teardown could surface latent bugs.
**Mitigation:** Handlers already use synchronous `redis.Redis` (no asyncio Redis), and `draft_message` is async-safe. Add a smoke test that runs the tool end-to-end with a real handler and a real (test) Redis to confirm no event-loop pollution.

### Risk 2: AgentSession Popoto lookup fails in non-bridge environments
**Impact:** Tests, scripts, or dev invocations of `send_message.py` without a real Popoto-managed session would crash instead of falling through to the legacy path.
**Mitigation:** Wrap the lookup in a try/except; on failure, log a warning and fall through to the existing raw-outbox write. The legacy path becomes the explicit fallback, not the default. Asserted by a test.

### Risk 3: Drafter latency on the tool-call path
**Impact:** The drafter is a Haiku call (sub-second p50, but adds perceptible latency vs. raw rpush). Agent invoking the tool sees the call block longer than today.
**Mitigation:** This is the *correct* latency budget — the worker path already pays it. Accept; document in the feature doc.

## Race Conditions

### Race 1: Concurrent agent tool invocations
**Location:** `tools/send_message.py::_send_via_telegram` after refactor — same process invokes handler.send.
**Trigger:** Agent invokes `send_message.py` twice in quick succession (rare; classifier shouldn't allow this, but the second-stop loop in principle could).
**Data prerequisite:** None — the handler write is `rpush`, naturally ordered.
**State prerequisite:** None — Redis `rpush` is atomic; the relay processes in order.
**Mitigation:** No new race. The handler's redundancy filter catches near-duplicate sends already.

### Race 2: Session lookup mid-deletion
**Location:** Popoto `AgentSession.query.filter(session_id=...).first()` inside the tool.
**Trigger:** Maintenance / cleanup reflection deletes a session while the tool is running.
**Data prerequisite:** The session record exists at tool start.
**State prerequisite:** Worker is not actively cleaning the session.
**Mitigation:** Lookup happens in a single Popoto call; on `None` we fall through to legacy raw-outbox write. The cleanup reflection is heartbeat-gated and shouldn't touch a session whose tool is mid-invocation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1058] PM final delivery via `_deliver_pipeline_completion` deliberately bypasses the review gate and is documented in `docs/features/pm-final-delivery.md`. Out of scope here.
- Nothing else deferred — the doc-only fixes, the test additions, and the code change all ship in this plan.

## Update System

No update system changes required. This is a purely internal code+doc change; no new dependencies, config files, or migration steps. Existing machines pick up the new behavior on the next `/update` pull and worker restart.

## Agent Integration

The agent invokes `tools/send_message.py` via Bash, as it does today. The CLI entrypoint declared in `pyproject.toml [project.scripts]` is unchanged. Specifically:

- No new MCP server needed — the tool is already a Bash-invoked CLI.
- No `.mcp.json` changes.
- No bridge import changes — `bridge/telegram_bridge.py` continues to use `TelegramRelayOutputHandler` for the silent-worker path; the only addition is that the same handler is now reachable from the tool process.
- Integration test: `tests/unit/test_tool_call_delivery.py` gains an assertion that invoking the tool with a mocked session reaches the mocked handler. The agent's existing prompt (which already says "invoke `python tools/send_message.py`") needs no change.

## Documentation

- [ ] Update `docs/features/agent-message-delivery.md`:
  - Verify lines 29 and 50 are accurate after the code change (no rewrite expected — the claims become true).
  - Add VALOR_TRANSPORT accepted-values enumeration (`telegram` / `email`, case-insensitive) to the Activation Rules section (currently around lines 31-38).
  - Fix stale test reference at line 77 (`tests/e2e/test_message_pipeline.py — Bool classifier assertions`).
  - Cross-link `bridge/redundancy_filter.py` and `bridge/read_the_room.py` in the Delivery Execution section.
  - Pick "review gate" as the canonical term; normalize `agent/hooks/stop.py` docstrings and `docs/features/agent-message-delivery.md` to match.
- [ ] Add a brief subsection "Filters layered on every send" enumerating drafter → redundancy → RTR → narration-fallback, with handler-line references, so future readers see what the pipeline does without spelunking.
- [ ] Add entry to `docs/features/README.md` index table if not already present (verify during build).
- [ ] No external docs site changes (no Sphinx/MkDocs in this repo for this surface).
- [ ] Inline: docstring on `tools/send_message.py::_send_via_telegram` and `_send_via_email` updated to reflect handler-routing.

## Success Criteria

- [ ] `python tools/send_message.py "<text>"` invoked in a test session produces drafter-processed bytes in `telegram:outbox:{session_id}` (assert via Redis introspection that the queued payload's `text` field differs from raw input when the input contains drafter-normalized content).
- [ ] An RTR-suppressed payload from `tools/send_message.py` does NOT reach the outbox (assert the outbox is empty after the call; assert a 👀 reaction was queued instead).
- [ ] A redundant payload (matches `session.recent_sent_drafts`) from `tools/send_message.py` does NOT reach the outbox (assert empty outbox + reaction queued).
- [ ] `tests/unit/test_tool_call_delivery.py` gains test cases asserting handler invocation (not just transcript classification) for both telegram and email transports.
- [ ] `docs/features/agent-message-delivery.md` lines 29 and 50 are verified accurate against post-change code, with no rewrite required.
- [ ] `docs/features/agent-message-delivery.md` enumerates VALOR_TRANSPORT accepted values, corrects the stale test reference at :77, cross-links `redundancy_filter.py` and `read_the_room.py`, and uses one canonical term ("review gate") consistently.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n "TelegramRelayOutputHandler" tools/send_message.py` returns a match (confirms wiring).
- [ ] `grep -n "EmailOutputHandler" tools/send_message.py` returns a match (confirms email wiring).

## Team Orchestration

Single-builder plan with one reviewer. The build is one focused diff (two function bodies + doc edits + tests); a single builder owns end-to-end.

### Team Members

- **Builder (send-message-handler-routing)**
  - Name: `send-message-router-builder`
  - Role: Rewrite `_send_via_telegram` and `_send_via_email` to route through canonical handlers; update doc; add tests.
  - Agent Type: builder
  - Resume: true

- **Validator (delivery-routing)**
  - Name: `delivery-routing-validator`
  - Role: Verify outbox payloads, suppression behavior, doc accuracy, and absence of legacy raw-rpush in the tool.
  - Agent Type: validator
  - Resume: true

- **Code Reviewer**
  - Name: `delivery-routing-reviewer`
  - Role: Review coupling, fallback semantics, async-loop boundaries, and doc/code alignment.
  - Agent Type: code-reviewer
  - Resume: true

### Available Agent Types

Standard set (builder, validator, code-reviewer).

## Step by Step Tasks

### 1. Refactor telegram path
- **Task ID**: build-telegram-routing
- **Depends On**: none
- **Validates**: `tests/unit/test_tool_call_delivery.py` (new cases)
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `_send_via_telegram` body to reconstitute `AgentSession` from `VALOR_SESSION_ID`, call `TelegramRelayOutputHandler.send(chat_id, text, reply_to_msg_id, session)`, with fallback to legacy rpush on session lookup failure.
- Extend `TelegramRelayOutputHandler.send` signature to accept an optional `file_paths` kwarg (additive; existing callers unaffected); thread file paths through to the outbox payload after the drafter/RTR/redundancy pipeline.
- Keep `linkify_references` and `cli_check_or_exit` in the tool; remove the direct rpush.

### 2. Refactor email path
- **Task ID**: build-email-routing
- **Depends On**: build-telegram-routing
- **Validates**: `tests/unit/test_tool_call_delivery.py` (new email case)
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `_send_via_email` body to reconstitute the session, call `EmailOutputHandler.send`, with fallback to legacy rpush on session lookup failure.
- Pass `EMAIL_SUBJECT` / `EMAIL_IN_REPLY_TO` through `session.extra_context` (already the canonical contract).

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-telegram-routing, build-email-routing
- **Validates**: `pytest tests/unit/test_tool_call_delivery.py -x -q`
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add three telegram test cases (handler invocation, RTR suppression empties outbox, redundancy suppression empties outbox).
- Add one email test case (handler invocation; drafter-revised body lands in outbox).
- Add a fallback test (missing session → legacy rpush + warning logged).

### 4. Doc edits
- **Task ID**: build-docs
- **Depends On**: build-tests
- **Validates**: `grep -c "VALOR_TRANSPORT" docs/features/agent-message-delivery.md`
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add VALOR_TRANSPORT accepted values to Activation Rules.
- Fix `:77` stale e2e test reference.
- Cross-link `bridge/redundancy_filter.py` and `bridge/read_the_room.py`.
- Normalize terminology to "review gate" across `agent/hooks/stop.py` and the doc.
- Add the "Filters layered on every send" subsection.
- Update `docs/features/README.md` index entry if missing.

### 5. Validate
- **Task ID**: validate-routing
- **Depends On**: build-docs
- **Assigned To**: delivery-routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `grep -n "rpush" tools/send_message.py` only appears in fallback branches.
- Confirm `grep -n "TelegramRelayOutputHandler\|EmailOutputHandler" tools/send_message.py` returns matches.
- Run the new tests; assert pass.
- Verify doc edits resolve every doc-only bullet from the issue.

### 6. Review
- **Task ID**: review-routing
- **Depends On**: validate-routing
- **Assigned To**: delivery-routing-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Review coupling and fallback semantics.
- Check async event-loop boundaries (no shared-loop leakage from `asyncio.run`).
- Spot-check the doc for accuracy against final code.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: review-routing
- **Assigned To**: delivery-routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_tool_call_delivery.py tests/unit/test_stop_hook_review.py -x -q`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Confirm all Success Criteria boxes are demonstrably met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `pytest tests/unit/test_tool_call_delivery.py tests/unit/test_stop_hook_review.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Handler wired (telegram) | `grep -c "TelegramRelayOutputHandler" tools/send_message.py` | output > 0 |
| Handler wired (email) | `grep -c "EmailOutputHandler" tools/send_message.py` | output > 0 |
| Doc enumerates transport | `grep -c "VALOR_TRANSPORT" docs/features/agent-message-delivery.md` | output > 0 |
| Doc cross-links RTR | `grep -c "read_the_room" docs/features/agent-message-delivery.md` | output > 0 |
| Doc cross-links redundancy | `grep -c "redundancy_filter" docs/features/agent-message-delivery.md` | output > 0 |
| Stale e2e ref removed | `grep -c "test_message_pipeline.py — Bool classifier" docs/features/agent-message-delivery.md` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The Option-A vs Option-B decision is made and justified; all implementation choices are local and reversible.
