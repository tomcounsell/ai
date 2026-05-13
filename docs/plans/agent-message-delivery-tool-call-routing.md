---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-05-13
revised: 2026-05-14
revision_applied: true
tracking: https://github.com/tomcounsell/ai/issues/1369
last_comment_id: none
---

# Agent Message Delivery — Tool-Call Routing Through Canonical Handler

## Problem

The agent-message-delivery doc promises that tool-call deliveries inherit the same drafter / RTR / redundancy / promise-gate pipeline the silent worker path enjoys. In reality `tools/send_message.py` writes raw payloads straight to the Redis outbox, bypassing every filter except `linkify_references` and the promise gate. The agent's "final say" path is materially less safe than the path it's supposed to mirror.

**Current behavior:**
- `tools/send_message.py::_send_via_telegram` (lines 71-145) builds a payload and `rpush`es it to `telegram:outbox:{session_id}`. The drafter, redundancy filter, and RTR never run.
- `tools/send_message.py::_send_via_email` (lines 148-199) writes raw to `email:outbox:{session_id}`. The drafter never runs.
- `TelegramRelayOutputHandler.send` (`agent/output_handler.py:249-552+`) runs drafter → redundancy filter → RTR → promise-gate-equivalents → outbox rpush on its telegram branch. Its email branch (`_send_via_email_outbox`, lines 175-247) queues without drafting and ships only a single `to` recipient.
- A third path, `tools/send_telegram.py`, runs the drafter via `_draft_text`, so three paths disagree.
- `docs/features/agent-message-delivery.md:29` and `:50` claim the canonical contract is honored. It isn't.

**Desired outcome:**
The tool-call path runs the same pipeline as the silent worker path. The agent retains "final say" over content; the system retains its safety nets over channel compliance and conversation-appropriateness. The doc's claim becomes true.

## Decision: Option A — Single Canonical Handler for Both Transports

We choose **Option A** from the issue, with one routing clarification surfaced by the war-room critique: **both telegram and email tool-call paths route through `TelegramRelayOutputHandler.send`**, not through `EmailOutputHandler.send`. `EmailOutputHandler.send` performs synchronous SMTP and is the wrong layer for the tool process (which must queue, not send). `TelegramRelayOutputHandler.send` is the single canonical queue-side entrypoint; its internal `transport == "email"` branch already writes to `email:outbox:{session_id}` and is the correct place to land both pipelines.

Justification:

1. **#589 design intent — "agent has final say over content."** That phrase scopes the agent's authority to *what is said*, not to *how it's formatted for the channel* or *whether the moment is right*. The drafter (length/format compliance), RTR (read-the-room), and redundancy filter (no double-sends) operate on properties the agent cannot self-enforce from prompt context alone — RTR uses a Haiku call against a chat snapshot the agent never sees, and the redundancy filter compares against `session.recent_sent_drafts` in Redis. Stripping these from the agent's deliberate-send path inverts the original feature's purpose: the *more* the agent participates, the *less* safety it gets.

2. **#1072 refactor intent — classification authority, not filter bypass.** #1072 removed the `delivery_action` / `delivery_text` fields and made `classify_delivery_outcome` (transcript-tail inspection) authoritative for routing the agent's choice. That refactor changed *how the choice is recognized*, not *what filtering the chosen payload receives*. The doc's still-accurate claim ("Tool-call payloads route through `TelegramRelayOutputHandler.send`") was the intended contract; the bypass appeared as a side effect when `send_message.py` was extracted as a standalone CLI without re-entering the handler. This is an oversight to correct, not a design to ratify.

3. **Precedent already exists.** `tools/send_telegram.py:71-99` runs the drafter from a CLI tool today. Calling into the handler's filtering pipeline from a CLI is a solved problem in this codebase.

4. **One handler, one drafter call.** Routing both transports through `TelegramRelayOutputHandler.send` means the drafter is hoisted to a single call site at the top of `send()` (before the transport branch). Both the telegram outbox write and the email outbox write consume the drafted text. The relay consumers (`telegram_relay.py`, `email_relay.py`) do not draft. The handler drafts exactly once; the relay sends exactly once. No double-drafting.

5. **Option B locks in an inconsistency.** Picking B requires documenting that `send_telegram.py` drafts but `send_message.py` doesn't, and asking the agent to internalize Telegram's 4096-char limit, channel-native markdown rules, and RTR-equivalent judgment from prompt context every turn. That's a worse contract than fixing the gap.

## Freshness Check

**Baseline commit:** d49c29b1
**Issue filed at:** 2026-05-09T22:22:10Z (≈3 days before plan, within the same week)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/send_message.py:71-145` — `_send_via_telegram` writes raw to Redis, no drafter — confirmed
- `tools/send_message.py:148-199` — `_send_via_email` writes raw to Redis, no drafter — confirmed
- `agent/output_handler.py:249-552+` — `TelegramRelayOutputHandler.send` runs drafter → redundancy → RTR on telegram branch — confirmed
- `agent/output_handler.py:175-247` — `_send_via_email_outbox` queues without drafter, single `to` recipient — confirmed
- `bridge/email_bridge.py:540-607` — `EmailOutputHandler.send` runs drafter and synchronous SMTP — confirmed
- `bridge/email_relay.py:144-220` — relay consumer builds MIME via `_build_reply_mime` (no drafter) — confirmed
- `tools/send_telegram.py:71-99` — runs drafter via `_draft_text` — confirmed (third-path inconsistency)
- `docs/features/agent-message-delivery.md:29` and `:50` — drafter-routing claim still present — confirmed
- `agent/hooks/stop.py` — `classify_delivery_outcome` at lines 217-245 — confirmed

**Cited sibling issues/PRs re-checked:**
- #589 — original agent-controlled delivery tracking issue, closed
- #1058 — PM final delivery via `agent.session_completion._deliver_pipeline_completion`, merged
- #1072 — tool-call classification refactor, merged — origin of current divergence

**Commits on main since issue was filed (touching referenced files):**
- None touching `tools/send_message.py`, `agent/output_handler.py`, `bridge/email_bridge.py`, `bridge/email_relay.py`, or `agent/hooks/stop.py`. Recent main commits (`d49c29b1`, `161b4c18`, `109355d9`) all touch unrelated areas.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** No drift; all line numbers in the issue still resolve correctly.

## Prior Art

- **Issue #589**: Original agent-controlled delivery — established the "agent has final say" framing. Closed.
- **Issue #1058**: Replaced `[PIPELINE_COMPLETE]` marker with `_deliver_pipeline_completion`. Established that some delivery paths legitimately bypass the review gate, but explicitly so and via dedicated code. Out of scope here but informs the principle that bypass-or-not is a per-path documented decision.
- **Issue #1072**: Refactored stop-hook delivery away from `delivery_action`/`delivery_text` fields toward tool-call classification. The bypass dates from this refactor's CLI extraction, not from an intentional design pivot.
- **`tools/send_telegram.py`**: Pre-existing CLI tool that already runs the drafter from a CLI context — proves the integration pattern works.

## Research

Purely internal — no external library, API, or ecosystem pattern is involved. No web research needed.

## Data Flow

Before (current):
1. Agent invokes `python tools/send_message.py "<text>"` during second stop
2. `send_message.py` linkifies, runs promise gate, `rpush`es to `telegram:outbox:{session_id}` (or `email:outbox:{session_id}` for email transport)
3. Relay pops from outbox, sends via Telethon / SMTP
4. Raw payload reaches user; drafter / RTR / redundancy never ran

After (Option A — single canonical handler for both transports):
1. Agent invokes `python tools/send_message.py "<text>"` during second stop
2. `send_message.py`:
   - linkifies (unchanged)
   - runs `cli_check_or_exit` promise gate (unchanged — CLI-side gate that short-circuits before any handler call)
   - resolves session via `VALOR_SESSION_ID` → `AgentSession.query.filter(...).first()`
   - calls `await TelegramRelayOutputHandler.send(chat_id, text, reply_to_msg_id, session, file_paths=...)` regardless of transport
3. `TelegramRelayOutputHandler.send`:
   - runs drafter ONCE at the top (hoisted above the transport branch)
   - runs redundancy filter on drafted text
   - runs RTR on drafted text
   - on telegram transport: rpushes to `telegram:outbox:{session_id}`
   - on email transport: rpushes to `email:outbox:{session_id}` with a `to` field built from `session.extra_context.email_to_addrs + email_cc_addrs` (reply-all preserved)
4. Relay consumers (`telegram_relay.py`, `email_relay.py`) drain queues and ship over Telethon / SMTP. Neither relay drafts.
5. Drafter-normalized payload reaches user; RTR-suppressed payloads trigger a 👀 reaction and skip the outbox; redundant repeats trigger a reaction and skip.

## Architectural Impact

- **New dependencies**: `tools/send_message.py` gains an import of `TelegramRelayOutputHandler` (one class) and the Popoto `AgentSession` model. No import of `EmailOutputHandler` — the tool talks only to the queue-side handler.
- **Handler change**: `TelegramRelayOutputHandler.send`'s drafter call is hoisted from the telegram branch to before the transport switch, so the email branch inherits it. `_send_via_email_outbox` extended to build a reply-all `to` list and accept the drafter result. `TelegramRelayOutputHandler.send` gains an optional `file_paths` kwarg (additive; existing callers unaffected).
- **Interface changes**: `send_message.py` CLI arg surface stays identical. Handler signature gains one optional kwarg.
- **Coupling**: Increases tool → handler coupling. We mitigate by reusing the public `send()` method and keeping the tool's own arg parsing / file validation / linkify / promise gate untouched.
- **Data ownership**: Unchanged. Handler still owns the Redis outbox write.
- **Reversibility**: Trivially reversible — revert `_send_via_*` function bodies and the drafter-hoist.

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

- **`agent/output_handler.py::TelegramRelayOutputHandler.send`**: hoist the drafter call from inside the telegram branch to before the transport switch. Pass the drafted text and (when present) `draft.full_output_file` into both branches. Add optional `file_paths: list[str] | None = None` kwarg so CLI callers can forward tool-validated attachments.
- **`agent/output_handler.py::_send_via_email_outbox`**: accept drafted text and `file_paths`; build the outbox payload's `to` field as a list combining `chat_id`, `session.extra_context.email_to_addrs`, and `session.extra_context.email_cc_addrs` minus the session's own SMTP user (mirrors `EmailOutputHandler.send` lines 591-598). Carry `attachments` from `file_paths`.
- **`tools/send_message.py::_send_via_telegram`**: rewrite body to reconstitute `AgentSession` from `VALOR_SESSION_ID`, then `await TelegramRelayOutputHandler.send(chat_id, text, reply_to_msg_id, session, file_paths=...)`. Preserve file-validation and linkify in the tool.
- **`tools/send_message.py::_send_via_email`**: rewrite body to reconstitute the session and call **the same** `TelegramRelayOutputHandler.send(recipient_addr, text, 0, session, file_paths=...)`. The handler's internal email branch handles the email-specific payload shape; the tool process never imports `EmailOutputHandler`.
- **`AgentSession` lookup**: tool runs in its own process; reconstitute the session from Popoto. On lookup failure, see "Legacy fallback" below.
- **Async entry point**: handler is `async def`; the CLI uses `asyncio.run(...)` once per invocation.
- **`docs/features/agent-message-delivery.md`**: fold in the doc-only fixes from the issue (VALOR_TRANSPORT enumeration, stale e2e test reference at :77, cross-links to `redundancy_filter.py` and `read_the_room.py`, canonical-term normalization).

### Flow

Agent's second stop → `classify_delivery_outcome` sees `tools/send_message.py` invocation → `send` outcome → session completes → on subsequent runs the tool routes via canonical handler → drafter normalizes (once) → redundancy filter inspects → RTR judges → transport-branch outbox write → relay sends.

### Technical Approach

- **Single drafter invocation.** Hoist the drafter from the telegram branch to immediately after the early-return `if not text` guard in `TelegramRelayOutputHandler.send`. The hoisted block produces `delivery_text`, `file_paths` (from `draft.full_output_file`), `steering_deferred`, and `draft` (for persistence). The transport branch consumes these. Worker silent paths (telegram and email) and CLI paths (telegram and email) all draft exactly once at this call site. The relay consumers continue not to draft.

- **`EmailOutputHandler.send` redundancy.** `EmailOutputHandler.send` (`bridge/email_bridge.py:540-607`) continues to exist for its current consumer: the worker's silent-output handler registration for email-routing projects (`worker/__main__.py:265-269`). This change does NOT rewire that registration; the silent email path keeps drafting inside `EmailOutputHandler.send` exactly as today. The CLI path simply does not import or call `EmailOutputHandler`. Result: no path drafts twice. (Converging the silent email path onto `TelegramRelayOutputHandler.send` is a separate, larger change — listed in No-Gos.)

- **Reply-all CC preservation.** `_send_via_email_outbox` currently writes `"to": chat_id` only — a single recipient. Extend it to read `session.extra_context.email_to_addrs` and `email_cc_addrs`, build the reply-all list using the same filter `EmailOutputHandler.send` applies (`bridge/email_bridge.py:591-598` — drop own address, drop the primary recipient from CC dedupe), and serialize as a list in the payload's `to` field. `bridge/email_relay.py::_normalize_payload` already accepts a list (`email_relay.py:82-84`). The tool relies on the bridge having stamped `email_to_addrs` / `email_cc_addrs` onto `extra_context` when it created the session (`bridge/email_bridge.py` does this for inbound mail); env-derived `EMAIL_REPLY_TO` is never used as a CC source.

- **`file_paths` precedence and suppression.**
  - **Precedence**: `TelegramRelayOutputHandler.send` accepts an optional `file_paths: list[str] | None` from the CLI caller. After the drafter runs, the effective file list is `(file_paths or []) + ([str(draft.full_output_file)] if draft.full_output_file else [])` — CLI-supplied attachments first, drafter-produced overflow file appended. Duplicates filtered via `dict.fromkeys` preserving order. Result threaded into both the telegram outbox payload's `file_paths` key (read by `telegram_relay.py`) and the email outbox payload's `attachments` key (read by `email_relay.py`).
  - **Suppression**: when RTR or redundancy suppresses the send, the entire payload is suppressed — text and `file_paths` together. There is no partial-send-attachments-only branch. The 👀 reaction is queued via the existing reaction path; no file is ever queued without its accompanying text.

- **Legacy raw-rpush fallback — fail closed.** When `AgentSession` lookup returns `None` (race / dev / misconfigured environment), the tool does NOT silently bypass the handler. Default behavior: log an error and exit non-zero so the agent's harness sees the failure. The legacy raw-rpush path is gated by env flag `ALLOW_LEGACY_RPUSH_FALLBACK=1` (off by default); when set, the tool logs a warning, writes raw to the outbox, and exits 0. This makes "bypass the pipeline" an explicit, debuggable opt-in rather than a silent default. The flag is intended for short-lived diagnostic use only; never set in production worker env.

- **Promise gate vs drafter ordering — rationale.** `cli_check_or_exit` runs in the tool **before** the handler call. The promise gate is a precondition on the agent's right to send at all (per #1058's dedicated bypass-or-not principle): if the agent owes a promise, no message goes out regardless of channel compliance. Running the gate first short-circuits before the drafter incurs Haiku latency, before the Popoto session lookup, and before any Redis write. The drafter (channel format compliance) and the filters (RTR, redundancy) operate on already-permitted text — they are about *how* and *when*, not *whether*. Order is gate → linkify → handler(drafter → redundancy → RTR → outbox).

- **Linkify.** Stays in the tool, before the handler call. The drafter consumes already-linkified text from agent transcripts in the silent path; the CLI path must match that order.

- **Doc updates.** Done in the same PR as the code: rewrite no lines at :29 / :50 (they become true); add VALOR_TRANSPORT enumeration to the Activation Rules section; fix :77 stale test reference; cross-link `bridge/redundancy_filter.py` and `bridge/read_the_room.py`; pick "review gate" as the canonical term and normalize.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/send_message.py` currently has `try/except Exception: pass` around `linkify_references` (line 116) — covered by an existing test, not in scope.
- [ ] `TelegramRelayOutputHandler.send` has multiple `try/except` blocks (drafter, redundancy filter, RTR). The hoisted drafter call inherits the existing fail-open posture; no behavioral change.
- [ ] New `try/except` around `AgentSession` lookup must distinguish "row not found" (typed: fall through to fail-closed exit or env-gated legacy path) from "Popoto/Redis raised" (re-raise after logging) — assert both via `caplog` and exit code.

### Empty/Invalid Input Handling
- [ ] Empty text: tool's `argparse` rejects empty text+empty files combination today. Behavior unchanged.
- [ ] Missing `VALOR_SESSION_ID`: tool exits with error today. Same after the change.
- [ ] Missing `AgentSession` row in Popoto with `ALLOW_LEGACY_RPUSH_FALLBACK` unset: assert tool exits non-zero with an error log. Test covers this.
- [ ] Missing `AgentSession` row with `ALLOW_LEGACY_RPUSH_FALLBACK=1`: assert tool logs a warning, writes raw to outbox, exits 0. Test covers this.

### Error State Rendering
- [ ] Drafter exception inside the handler: handler already falls back to raw text. Confirm via existing handler tests; no new assertion.
- [ ] RTR suppression with no anchor: handler already falls through to send-original. No new assertion.
- [ ] Redis write failure inside the handler: handler logs and returns; the agent's CLI invocation reports nonzero exit so the harness sees the failure. Add assertion.

## Test Impact

- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting `_send_via_telegram` invokes `TelegramRelayOutputHandler.send` with telegram-transport session (mock the handler, assert call args).
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting `_send_via_email` invokes **the same** `TelegramRelayOutputHandler.send` with email-transport session — confirming there is only one canonical handler entrypoint.
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting an RTR-suppressed draft (mock `read_the_room` to return `suppress`) produces an empty `telegram:outbox:{session_id}` after invoking the tool.
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting a drafter-normalized payload (mock `draft_message` to return revised text) lands in the outbox instead of the raw input.
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting email outbox payload's `to` field contains the full reply-all list (chat_id + email_to_addrs + email_cc_addrs minus own SMTP user).
- [ ] `tests/unit/test_tool_call_delivery.py` — UPDATE: add a test asserting missing session + `ALLOW_LEGACY_RPUSH_FALLBACK` unset causes non-zero exit; and missing session + `ALLOW_LEGACY_RPUSH_FALLBACK=1` causes raw rpush with warning.
- [ ] `tests/unit/test_output_handler.py` (or equivalent handler test file) — UPDATE: add an assertion that the drafter is called exactly once for an email-transport session (proves the hoist).
- [ ] `tests/unit/test_stop_hook_review.py` — No change. Tool routing is orthogonal to stop-hook classification.
- [ ] `tests/unit/test_duplicate_delivery.py` — No change.
- [ ] `tests/unit/test_qa_handler.py` — No change.
- [ ] `tests/e2e/test_message_pipeline.py` — REPLACE the stale reference at `docs/features/agent-message-delivery.md:77` (this is a doc-only fix; the test file itself is not modified).
- [ ] No existing test asserts the legacy raw-outbox shape of `_send_via_telegram` directly, so nothing breaks at the contract boundary.

## Rabbit Holes

- **Refactoring the handler to make it CLI-friendlier.** Don't. Handler.send is already a public coroutine; calling it from `asyncio.run` is two lines.
- **Removing `tools/send_telegram.py`.** Tracked separately — see No-Gos.
- **Converging the silent email path onto `TelegramRelayOutputHandler.send`.** Out of scope — see No-Gos.
- **Adding the drafter to `react_with_emoji.py`.** Reactions are not text. Excluded by the issue. Skip.
- **Rewriting the doc from scratch.** The doc's claim at :29 / :50 becomes true after this change; the only edits are the enumerated doc-only fixes from the issue.

## Risks

### Risk 1: Handler call from CLI changes timing semantics
**Impact:** Handler.send is `async`; the CLI must `asyncio.run` it. If the handler holds an event-loop-bound resource (e.g., a singleton Redis client cached on an unrelated loop), the per-invocation loop teardown could surface latent bugs.
**Mitigation:** Handlers already use synchronous `redis.Redis` (no asyncio Redis), and `draft_message` is async-safe. Add a smoke test that runs the tool end-to-end with a real handler and a real (test) Redis to confirm no event-loop pollution.

### Risk 2: AgentSession Popoto lookup fails in non-bridge environments
**Impact:** Tests, scripts, or dev invocations of `send_message.py` without a real Popoto-managed session would exit non-zero by default (fail-closed).
**Mitigation:** Document the `ALLOW_LEGACY_RPUSH_FALLBACK` env flag in the tool's `--help` output and in `docs/features/agent-message-delivery.md`. The legacy path is opt-in for diagnostics; production workers never set the flag.

### Risk 3: Drafter latency on the tool-call path
**Impact:** The drafter is a Haiku call (sub-second p50, but adds perceptible latency vs. raw rpush). Agent invoking the tool sees the call block longer than today.
**Mitigation:** This is the *correct* latency budget — the worker path already pays it. Accept; document in the feature doc.

### Risk 4: Hoist regresses telegram silent path
**Impact:** Moving the drafter from inside the telegram branch to before the transport switch changes the order of operations on the silent worker path (drafter now runs before `_resolve_transport`).
**Mitigation:** `_resolve_transport` is a pure read of `session.extra_context`; no side effects. The hoist is purely structural. Existing telegram handler tests cover the post-drafter behavior; add one assertion that drafter runs for both transport values to lock in the invariant.

## Race Conditions

### Race 1: Concurrent agent tool invocations
**Location:** `tools/send_message.py::_send_via_telegram` after refactor — same process invokes handler.send.
**Trigger:** Agent invokes `send_message.py` twice in quick succession.
**Data prerequisite:** None — the handler write is `rpush`, naturally ordered.
**State prerequisite:** None — Redis `rpush` is atomic; the relay processes in order.
**Mitigation:** No new race. The handler's redundancy filter catches near-duplicate sends already.

### Race 2: Session lookup mid-deletion
**Location:** Popoto `AgentSession.query.filter(session_id=...).first()` inside the tool.
**Trigger:** Maintenance / cleanup reflection deletes a session while the tool is running.
**Data prerequisite:** The session record exists at tool start.
**State prerequisite:** Worker is not actively cleaning the session.
**Mitigation:** Lookup happens in a single Popoto call; on `None` we exit non-zero (fail-closed default). The cleanup reflection is heartbeat-gated and shouldn't touch a session whose tool is mid-invocation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1058] PM final delivery via `_deliver_pipeline_completion` deliberately bypasses the review gate and is documented in `docs/features/pm-final-delivery.md`. Out of scope here.
- **`tools/send_telegram.py` consolidation** — deferred. `send_telegram.py` is the PM self-messaging path; it already runs the drafter via `_draft_text`, so it's not part of the bug being fixed. Folding it into a unified canonical CLI is a separate, larger consolidation tracked outside this slug. If/when picked up, route it through `TelegramRelayOutputHandler.send` for symmetry.
- **Silent worker email path consolidation** — deferred. `worker/__main__.py:265-269` registers `EmailOutputHandler` for the silent path; that handler does drafter + synchronous SMTP. Converging this onto `TelegramRelayOutputHandler.send` (queue-only) is a worthwhile refactor but is a separate change and not required to make the agent doc's claim true.

## Update System

No update system changes required. Purely internal code+doc change; no new dependencies, config files, or migration steps. Existing machines pick up the new behavior on the next `/update` pull and worker restart. The new `ALLOW_LEGACY_RPUSH_FALLBACK` env flag defaults to unset; no env file changes required.

## Agent Integration

The agent invokes `tools/send_message.py` via Bash, as it does today. The CLI entrypoint declared in `pyproject.toml [project.scripts]` is unchanged. Specifically:

- No new MCP server needed — the tool is already a Bash-invoked CLI.
- No `.mcp.json` changes.
- No bridge import changes — `bridge/telegram_bridge.py` continues to use `TelegramRelayOutputHandler` for the silent-worker path; the only addition is that the same handler is now reachable from the tool process.
- Integration test: `tests/unit/test_tool_call_delivery.py` gains an assertion that invoking the tool with a mocked session reaches the mocked handler.

## Documentation

- [ ] Update `docs/features/agent-message-delivery.md`:
  - Verify lines 29 and 50 are accurate after the code change (no rewrite expected — the claims become true). Falsifiable check: `grep -n "tool-call payloads route through .TelegramRelayOutputHandler.send" docs/features/agent-message-delivery.md` returns at least one match AND no nearby contradicting prose. The verification step in §Verification grep's for the canonical phrasing.
  - Add VALOR_TRANSPORT accepted-values enumeration (`telegram` / `email`, case-insensitive) to the Activation Rules section.
  - Fix stale test reference at line 77 (`tests/e2e/test_message_pipeline.py — Bool classifier assertions`).
  - Cross-link `bridge/redundancy_filter.py` and `bridge/read_the_room.py` in the Delivery Execution section.
  - Pick "review gate" as the canonical term; normalize `agent/hooks/stop.py` docstrings and `docs/features/agent-message-delivery.md` to match.
  - Document the `ALLOW_LEGACY_RPUSH_FALLBACK` env flag (diagnostic-only, never production).
  - Document the promise-gate-then-drafter ordering rationale.
- [ ] Add a brief subsection "Filters layered on every send" enumerating drafter → redundancy → RTR → narration-fallback, with handler-line references.
- [ ] Add entry to `docs/features/README.md` index table if not already present (verify during build).
- [ ] Inline: docstring on `tools/send_message.py::_send_via_telegram` and `_send_via_email` updated to reflect handler-routing; docstring on `TelegramRelayOutputHandler.send` updated to reflect the hoisted single-drafter call.

## Success Criteria

- [ ] `python tools/send_message.py "<text>"` invoked in a test session produces drafter-processed bytes in `telegram:outbox:{session_id}` (assert via Redis introspection that the queued payload's `text` field differs from raw input when the input would be drafter-normalized).
- [ ] `python tools/send_message.py "<text>"` with `VALOR_TRANSPORT=email` produces drafter-processed bytes in `email:outbox:{session_id}` with `to` field carrying the full reply-all list.
- [ ] An RTR-suppressed payload from `tools/send_message.py` does NOT reach the outbox (assert empty outbox after the call; assert a 👀 reaction was queued).
- [ ] A redundant payload (matches `session.recent_sent_drafts`) from `tools/send_message.py` does NOT reach the outbox (assert empty outbox + reaction queued).
- [ ] A unit-test mock confirms `draft_message` is called exactly once per `send_message.py` invocation, for both telegram and email transports.
- [ ] `tests/unit/test_tool_call_delivery.py` gains test cases asserting `TelegramRelayOutputHandler.send` invocation for BOTH telegram and email transports (single canonical handler).
- [ ] `tests/unit/test_tool_call_delivery.py` gains a fallback test: missing session + flag unset → non-zero exit; flag set → raw rpush + warning.
- [ ] `docs/features/agent-message-delivery.md` enumerates VALOR_TRANSPORT accepted values, corrects the stale test reference at :77, cross-links `redundancy_filter.py` and `read_the_room.py`, uses "review gate" consistently, and documents `ALLOW_LEGACY_RPUSH_FALLBACK`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n "TelegramRelayOutputHandler" tools/send_message.py` returns matches in both `_send_via_telegram` and `_send_via_email` (single-handler convergence).
- [ ] `grep -n "EmailOutputHandler" tools/send_message.py` returns NO match (the tool does not import the SMTP handler).

## Team Orchestration

Single-builder plan with one reviewer. The build is one focused diff (two function bodies + handler hoist + doc edits + tests); a single builder owns end-to-end.

### Team Members

- **Builder (send-message-handler-routing)**
  - Name: `send-message-router-builder`
  - Role: Hoist drafter in `TelegramRelayOutputHandler.send`; extend `_send_via_email_outbox` for reply-all + attachments; rewrite `_send_via_telegram` and `_send_via_email` to route through the single canonical handler; update doc; add tests.
  - Agent Type: builder
  - Resume: true

- **Validator (delivery-routing)**
  - Name: `delivery-routing-validator`
  - Role: Verify outbox payloads, suppression behavior, doc accuracy, single-drafter invariant, fail-closed fallback, absence of legacy raw-rpush in the default tool path.
  - Agent Type: validator
  - Resume: true

- **Code Reviewer**
  - Name: `delivery-routing-reviewer`
  - Role: Review coupling, fallback semantics, async-loop boundaries, reply-all CC handling, doc/code alignment.
  - Agent Type: code-reviewer
  - Resume: true

### Available Agent Types

Standard set (builder, validator, code-reviewer).

## Step by Step Tasks

### 1. Hoist drafter in handler
- **Task ID**: build-handler-hoist
- **Depends On**: none
- **Validates**: `tests/unit/test_output_handler.py` (new assertion: drafter called for both transports)
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Hoist the drafter block in `TelegramRelayOutputHandler.send` to immediately after the `if not text` guard, before `_resolve_transport`.
- Pass `delivery_text`, `file_paths`, `steering_deferred`, and `draft` into both the telegram and email branches.
- Add `file_paths: list[str] | None = None` kwarg to `send()`.
- Extend `_send_via_email_outbox` to: (a) accept `delivery_text` and `file_paths`; (b) build reply-all `to` list from `extra_context.email_to_addrs` + `email_cc_addrs` minus own SMTP user; (c) populate payload `attachments` from `file_paths`.

### 2. Refactor telegram path in tool
- **Task ID**: build-telegram-routing
- **Depends On**: build-handler-hoist
- **Validates**: `tests/unit/test_tool_call_delivery.py` (new cases)
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `_send_via_telegram` body to reconstitute `AgentSession` from `VALOR_SESSION_ID` and call `TelegramRelayOutputHandler.send(chat_id, text, reply_to_msg_id, session, file_paths=...)`.
- On session lookup failure: fail closed by default (log error, exit non-zero); raw rpush path only when `ALLOW_LEGACY_RPUSH_FALLBACK=1`.
- Keep `linkify_references` and `cli_check_or_exit` in the tool; remove the direct rpush.

### 3. Refactor email path in tool
- **Task ID**: build-email-routing
- **Depends On**: build-telegram-routing
- **Validates**: `tests/unit/test_tool_call_delivery.py` (new email case + reply-all case)
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `_send_via_email` body to reconstitute the session and call the **same** `TelegramRelayOutputHandler.send` (not `EmailOutputHandler.send`). Pass the recipient address as `chat_id`; the handler's email branch handles the email payload shape.
- Pass `EMAIL_SUBJECT` / `EMAIL_IN_REPLY_TO` through `session.extra_context` (already the canonical contract for the bridge-spawned session; the tool only writes them if `extra_context` is missing them — defensive).
- On session lookup failure: same fail-closed default as task 2.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-handler-hoist, build-telegram-routing, build-email-routing
- **Validates**: `pytest tests/unit/test_tool_call_delivery.py tests/unit/test_output_handler.py -x -q`
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add telegram test cases: handler invocation; RTR suppression empties outbox; redundancy suppression empties outbox; drafter-revised payload lands in outbox.
- Add email test cases: handler invocation (same `TelegramRelayOutputHandler.send`); reply-all `to` carries `chat_id + email_to_addrs + email_cc_addrs - own`; attachments propagate to payload.
- Add fallback tests: missing session + flag unset → non-zero exit; missing session + flag set → raw rpush + warning.
- Add handler test: `draft_message` called exactly once for both transport values.

### 5. Doc edits
- **Task ID**: build-docs
- **Depends On**: build-tests
- **Validates**: see §Verification grep table
- **Assigned To**: send-message-router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add VALOR_TRANSPORT accepted values to Activation Rules.
- Fix `:77` stale e2e test reference.
- Cross-link `bridge/redundancy_filter.py` and `bridge/read_the_room.py`.
- Normalize terminology to "review gate" across `agent/hooks/stop.py` and the doc.
- Add the "Filters layered on every send" subsection.
- Document `ALLOW_LEGACY_RPUSH_FALLBACK` (diagnostic only).
- Document promise-gate-then-drafter ordering rationale.
- Update `docs/features/README.md` index entry if missing.

### 6. Validate
- **Task ID**: validate-routing
- **Depends On**: build-docs
- **Assigned To**: delivery-routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `grep -n "rpush" tools/send_message.py` only appears in the env-gated fallback branch.
- Confirm `grep -n "TelegramRelayOutputHandler" tools/send_message.py` returns matches in both telegram and email helpers.
- Confirm `grep -n "EmailOutputHandler" tools/send_message.py` returns no matches.
- Run the new tests; assert pass.
- Verify doc edits resolve every doc-only bullet from the issue (per §Verification table).

### 7. Review
- **Task ID**: review-routing
- **Depends On**: validate-routing
- **Assigned To**: delivery-routing-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Review coupling and fallback semantics (fail-closed default).
- Check async event-loop boundaries (no shared-loop leakage from `asyncio.run`).
- Check reply-all CC handling matches `EmailOutputHandler.send`'s filter exactly.
- Spot-check the doc for accuracy against final code.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: review-routing
- **Assigned To**: delivery-routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_tool_call_delivery.py tests/unit/test_stop_hook_review.py tests/unit/test_output_handler.py -x -q`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Confirm all Success Criteria boxes are demonstrably met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `pytest tests/unit/test_tool_call_delivery.py tests/unit/test_stop_hook_review.py tests/unit/test_output_handler.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Single canonical handler (telegram) | `grep -c "TelegramRelayOutputHandler" tools/send_message.py` | output ≥ 2 |
| EmailOutputHandler NOT imported by tool | `grep -c "EmailOutputHandler" tools/send_message.py` | output = 0 |
| Doc enumerates transport | `grep -c "VALOR_TRANSPORT" docs/features/agent-message-delivery.md` | output > 0 |
| Doc cross-links RTR | `grep -c "read_the_room" docs/features/agent-message-delivery.md` | output > 0 |
| Doc cross-links redundancy | `grep -c "redundancy_filter" docs/features/agent-message-delivery.md` | output > 0 |
| Stale e2e ref removed | `grep -c "test_message_pipeline.py — Bool classifier" docs/features/agent-message-delivery.md` | exit code 1 |
| Doc documents fallback flag | `grep -c "ALLOW_LEGACY_RPUSH_FALLBACK" docs/features/agent-message-delivery.md` | output > 0 |
| Canonical handler claim present | `grep -c "TelegramRelayOutputHandler.send" docs/features/agent-message-delivery.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Adversary | Plan called `EmailOutputHandler.send` (synchronous SMTP) from CLI; wrong layer | Decision section + Solution/Key Elements + Step 3 | Both transports now route through `TelegramRelayOutputHandler.send`; the tool never imports `EmailOutputHandler` |
| Blocker | Adversary | Following the plan literally would double-draft and drop reply-all CCs | Solution/Technical Approach (single-drafter hoist) + `_send_via_email_outbox` reply-all extension (Step 1) | Drafter hoisted to single call site before transport branch; reply-all built from `extra_context.email_to_addrs + email_cc_addrs` |
| Concern | Operator | `file_paths` precedence & suppression underspecified | Solution/Technical Approach (`file_paths` precedence and suppression) | CLI-supplied paths first, drafter overflow appended, dedup preserving order; full-payload suppression on RTR/redundancy |
| Concern | Skeptic | Legacy raw-rpush fallback should fail closed | Solution/Technical Approach (legacy fallback fail closed) + Step 2/3 | Env flag `ALLOW_LEGACY_RPUSH_FALLBACK` gates the legacy path; default is non-zero exit |
| Concern | Archaeologist | Promise-gate vs drafter ordering rationale missing | Solution/Technical Approach (promise gate vs drafter ordering) | Promise-gate is precondition on right-to-send; drafter shapes already-permitted text; gate runs first to short-circuit before Haiku/Redis cost |
| Nit | Simplifier | `send_telegram.py` deferral untracked | No-Gos | Explicit "deferred" entry with rationale (PM self-messaging, already drafts) |
| Nit | User | Doc-verification success criterion unfalsifiable | Documentation + Verification table | Grep-based assertions for canonical phrasings and flag documentation |

---

## Open Questions

None. The Option-A vs Option-B decision is made and justified; routing for both transports is locked to the single canonical handler; all implementation choices are local and reversible.
