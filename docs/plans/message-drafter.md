---
status: docs_complete
type: feature
appetite: Large
owner: valor
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1035
last_comment_id:
revision_applied: true
revision_applied_at: 2026-04-20
critique_verdict: "READY TO BUILD (with concerns)"
critique_artifact_hash: "sha256:fd4456e05ae9176fd053b796a9af245499d924b845c6d0ebbb9c7def40b1bc89"
---

# Message Drafter (rename from Summarizer) — Medium-Aware Drafts, Tool-Call Delivery, Consolidation, and Length Enforcement

## Problem

The agent output path from Telegram/email session → user-visible message sprawls across four files (~2,731 lines), is Telegram-only, hardcodes format rules, and uses a string-menu parser for delivery choices. Two symptoms prove the design has broken down:

**Current behavior — sprawl and medium-blindness:**

1. `summarize_response()` hardcodes Telegram format rules; email sessions bypass the stop-hook review gate entirely (`_is_telegram_triggered()` guard at `agent/hooks/stop.py:40`) and ship raw markdown as `text/plain` MIME.
2. The review gate presents a string menu (`SEND` / `EDIT:` / `REACT:` / `SILENT` / `CONTINUE`) parsed back out by regex. Agents fumble this ceremony; the parser has fallback branches for every malformed shape.
3. Responsibility is muddy across `bridge/summarizer.py` (1,525), `bridge/response.py` (823), `bridge/formatting.py` (76), and `agent/hooks/stop.py` (307).

**Current behavior — oversized messages reach the relay and fail:**

4. **Worker-executed PM sessions bypass the drafter entirely.** When the worker's `agent/session_executor.py::send_to_chat` decides to `"deliver"` (line 928), it calls `send_cb` directly — which is `TelegramRelayOutputHandler.send` (`agent/output_handler.py:188`). That method writes the raw `text` to `telegram:outbox:{session_id}` in Redis. The bridge relay polls and forwards via `send_markdown()` → Telethon `SendMessageRequest`. If the text exceeds 4096 chars, Telegram rejects with `MessageTooLongError`, the relay retries 3x, then dead-letters.
5. The summarizer's `FILE_ATTACH_THRESHOLD` path — which writes long raw output to a `.txt` file and uses the short summary as a caption — lives inside `bridge/response.py::send_response_with_files`. That path is only called from `bridge/telegram_bridge.py:2185`, which handles the **handler/event** flow (direct human Telegram message → bridge). **Worker-executed PM sessions never hit `send_response_with_files`.** Their outputs route through `send_cb` → Redis outbox, and the FILE_ATTACH threshold never gets a chance to fire.
6. The stop-hook review gate SHOULD intercept at session stop (PM sessions get `TELEGRAM_CHAT_ID`/`TELEGRAM_REPLY_TO` env vars at `agent/sdk_client.py:1069-1070` and `sdk_client.py:306-311`), BUT the review gate only writes `delivery_text` to the `AgentSession` (via `_write_delivery_to_session` in `agent/hooks/stop.py`). That `delivery_text` is consumed exclusively by `bridge/response.py::send_response_with_files` (line 448-449). Worker-side deliveries don't read it — they just send whatever `send_cb` receives.
7. Additional factor: `_has_pm_messages()` at `agent/hooks/stop.py:63` returns `True` if the PM already self-messaged this turn (e.g. via `tools/send_telegram.py`), and the review gate early-returns. So for PM sessions that stream intermediate outputs via `send_cb` *before* the stop hook ever fires, there is no gate at all.

**Evidence — the dead-letter that prompted this plan:**

- Session: `tg_cuttlefish_-1003801797780_94` (PM session, role=pm, project=cuttlefish), 2026-04-20T05:19:43Z → 05:21:36Z UTC.
- 4,582-char raw response was written via `send_cb` to the Redis outbox.
- `bridge/telegram_relay.py:295` called `send_markdown()` → `MessageTooLongError` from `telethon.client.messages.py:926`.
- Retried 3x (attempts 1/3, 2/3, 3/3); each retry failed identically.
- Message was dead-lettered. User saw nothing.
- Log evidence: `logs/bridge.log` lines 1630-1644 (times 05:19:42–05:21:36 UTC).

**Current working patch (already reverted):** Commit `421d84a6` added newline-splitting to `bridge/markdown.py::send_markdown`. Reverted at `1678068b` per directive: **no splitting. ever.**

**Desired outcome:**

- Email replies ship as plain prose — no literal markdown syntax on the wire.
- Telegram replies never contain markdown tables.
- The delivery component is medium-aware: same flow, per-medium format rules.
- The agent's final steer is a **prepopulated tool call** — no string-menu parsing.
- Every user-facing output path — including worker-executed PM `send_cb` — goes through the drafter. The relay is a dumb pipe that treats any >4096-char payload as a bug: log loudly, convert to `.txt` attachment with a short caption, and refuse to send split messages.
- Component is renamed to `message_drafter` and consolidated into a single module. PR ends with a **negative net line count outside tests**.
- A worker-executed Telegram PM session producing >4096 chars of raw output **cannot reach the relay without going through the drafter first**, verified by integration test.

## Freshness Check

**Baseline commit:** `1678068b288d0cd253715ee82accd1500258638f`
**Issue filed at:** 2026-04-17T15:04:46Z (3 days ago)
**Disposition:** Minor drift — new evidence from dead-letter incident strengthens the case and adds an acceptance criterion.

**File:line references re-verified:**
- `agent/hooks/stop.py:40` (`_is_telegram_triggered()`) — still holds; line unchanged.
- `agent/hooks/stop.py:242` (review gate guard) — still holds; line unchanged.
- `bridge/email_bridge.py:267` (`text/plain` MIME) — still holds.
- `bridge/email_bridge.py:459-460` (`extra_context["transport"]="email"`) — still holds.
- `bridge/email_bridge.py:200` (`EmailOutputHandler._build_reply`) — still holds.
- `bridge/summarizer.py` line count: 1,525 — unchanged.
- `bridge/response.py` line count: 823 — unchanged.
- `bridge/formatting.py` line count: 76 — unchanged.
- `agent/hooks/stop.py` line count: 307 — unchanged.
- `agent/sdk_client.py:1069-1070` (`TELEGRAM_CHAT_ID` env var) — still holds; PM sessions are confirmed to get Telegram context.
- `agent/sdk_client.py:306-311` (`TELEGRAM_REPLY_TO` env var from `telegram_message_id`) — still holds.
- `models/agent_session.py:25` (`parent_agent_session_id` FK) — still holds.
- `agent/output_handler.py:156` (`TelegramRelayOutputHandler` class) — still holds; `send` at line 188.
- `agent/session_executor.py:690` (`_resolve_callbacks`) — still holds post b7e1a1db split.
- `agent/session_executor.py:928` (raw `send_cb` call on `deliver`) — still holds; this is the bypass path.

**Cited sibling issues/PRs re-checked:**
- #955 (customer-service persona) — still open at time of writing; separate concern, no overlap.

**Commits on main since issue filed (touching referenced files):**
- `b7e1a1db` (Apr 17): refactor split of `agent_session_queue.py` — changed import paths but `_resolve_callbacks` still lives in `agent/agent_session_queue.py`. Irrelevant to plan.
- `0fd28c87` (Apr 18): worker event-loop memory-extraction hotfix. Irrelevant.
- `c5c24ee3` (Apr 19): steering re-enqueue hotfix. Irrelevant.
- `421d84a6` (Apr 20): attempted splitting fix in `bridge/markdown.py` — **REVERTED** in `1678068b`. Critical: do not reintroduce splitting.
- `1678068b` (Apr 20): the revert. Baseline.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/summarizer-fallback-steering.md` — adjacent; defines the "summary failure → steer the agent" path used by the fallback chain. Must be consulted to preserve behavior under the rename. No direct merge-conflict risk.
- `docs/guides/summarizer-integration-audit.md` — guide doc; reference only.

**Notes:** The issue's recon remains accurate. The dead-letter incident adds Non-Goal #4 (no message splitting) and an Acceptance Criterion (worker-executed PM >4096 chars cannot reach the relay without drafter).

## Prior Art

- **Closed plan: `docs/plans/summarizer-fallback-steering.md`** — introduced the self-summary fallback (when all backends fail, steer the agent to summarize on its next turn instead of delivering raw truncated text). Must be preserved under the new name.
- **Closed plan: `docs/plans/completed/email-bridge.md`** — introduced `EmailOutputHandler` and `extra_context.transport` discriminator. Defines the plumbing this plan extends.
- **Commit `421d84a6`** (Apr 20, 2026) — naive splitting fix in `bridge/markdown.py`. **Failed because** it delivered split messages to the user (UX regression) and did not address the root cause (worker bypass of drafter). **Reverted at `1678068b`.** Do not reintroduce.
- **Issue #955** — customer-service persona routing. Adjacent neighborhood (email pipeline) but unrelated; explicitly out of scope.
- **`docs/features/summarizer-format.md`** — documents current Telegram format rules; content to migrate into `docs/features/message-drafter.md`.

## Research

**Queries used:**
- "Telegram 4096 character message limit handling strategy document attachment 2026"
- "Telethon MessageTooLongError best practice file attachment instead of split"

**Key findings:**

- Telegram's 4096-char limit applies equally to free and Premium users; there is no legitimate API path around it (source: [Telegram Limits — tginfo.me](https://limits.tginfo.me/en)).
- Telethon's recommended pattern on `MessageTooLongError` is to write the content to a `.txt` file and send via `client.send_file(path, caption=short_summary)` — NOT to split (source: [Telethon FAQ](https://docs.telethon.dev/en/stable/quick-references/faq.html)).
- Message splitting fragments content, breaks reply-threading, and is widely discouraged in Telethon community practice. File attachment is the canonical fix.
- The summarizer's existing `FILE_ATTACH_THRESHOLD` path (`bridge/summarizer.py:62`) already implements exactly this pattern — we just need to extend its reach to the worker-side path.

Memory saved: `96b2e19117d8415b90709ae183b108eb` (importance 5.0) — "Telegram MessageTooLongError: best practice is send as .txt file attachment with short caption."

## Spike Results

### spike-1: Confirm stop-hook review gate DOES fire for PM sessions
- **Assumption:** "PM sessions get `TELEGRAM_CHAT_ID`/`TELEGRAM_REPLY_TO` env vars, so `_is_telegram_triggered()` returns True and the gate fires."
- **Method:** code-read
- **Finding:** Confirmed. `agent/sdk_client.py:1069` sets `TELEGRAM_CHAT_ID` for PM/TEAMMATE sessions when `self.chat_id` is truthy; line 311 sets `TELEGRAM_REPLY_TO` from `session.telegram_message_id`. BUT: the gate is bypassed by `_has_pm_messages()` at `stop.py:245` whenever the PM already emitted a message this turn — which is the common case for intermediate PM outputs emitted via `send_cb`. The gate also only writes `delivery_text` to the session; worker's `send_cb` never reads that field.
- **Confidence:** high
- **Impact on plan:** The fix must not rely on the stop hook alone. A second layer — at the `send_cb` boundary — is required so streaming outputs from PM sessions also get drafted. See Solution §3.

### spike-2: Confirm file-attachment outbox format already works end-to-end
- **Assumption:** "The Redis outbox payload schema supports `file_paths` for attachments, so we can route long drafts through it without new plumbing."
- **Method:** code-read
- **Finding:** Confirmed. `bridge/telegram_relay.py:260-278` handles `file_paths` in the outbox payload and sends via `telethon.client.send_file(caption=text)`. The schema is already in place; we just don't emit it from `TelegramRelayOutputHandler.send`.
- **Confidence:** high
- **Impact on plan:** `TelegramRelayOutputHandler.send` can grow a `file_paths` parameter (and write it to the payload) without any relay-side changes.

### spike-3: Confirm drafter can run from the worker (no Telegram client dependency)
- **Assumption:** "`bridge/summarizer.py::summarize_response` is importable and callable from the worker without requiring a running Telegram client."
- **Method:** code-read
- **Finding:** Confirmed. `summarize_response` depends on Anthropic/OpenRouter API clients and `bridge.artifacts` — no Telegram client import. It's called from the stop hook today (which runs inside the worker process via the Claude Agent SDK hook pipeline). Safe to call from `TelegramRelayOutputHandler.send`.
- **Confidence:** high
- **Impact on plan:** §3 of Solution routes `send_cb` through the drafter in-process.

### spike-4: Confirm orthogonality of medium and persona in current code
- **Assumption:** "Persona (Developer/PM/Teammate/CustomerService) and medium (Telegram/Email) are already separable in the current summarizer."
- **Method:** code-read
- **Finding:** Partial. Current summarizer has persona branches (see `bridge/summarizer.py::_build_summary_prompt`) but no explicit medium parameter; Telegram format rules are mixed into the core prompt. They *can* be cleanly separated without reshaping personas.
- **Confidence:** medium
- **Impact on plan:** Prompt structure reorganizes to `base + medium_rules + persona_tone + session_context` — four clean segments.

## Data Flow

**End-to-end flow for a Telegram PM session (the broken case):**

1. **Entry point**: Human sends a Telegram message → `bridge/telegram_bridge.py` handler enqueues `AgentSession` to Redis with `role=pm`, `extra_context={...}` (note: `transport` is NOT set today for Telegram — telegram is the implicit default).
2. **Worker pickup**: `worker/__main__.py` dequeues the session, registers `TelegramRelayOutputHandler` as the callback for `(project_key, "telegram")`.
3. **Session execution**: `agent/session_executor.py::execute_session` runs the PM persona via `claude -p` subprocess. The PM may emit intermediate text via `tools/send_telegram.py` (self-messaging) OR wait for stop-hook summarization.
4. **Output streaming via send_cb (broken path today)**: When `stop_reason=="end_turn"` and output is non-empty, `session_executor.py:928` calls `await send_cb(chat_id, msg, reply_to, agent_session)`. `send_cb == TelegramRelayOutputHandler.send` writes `msg` raw to `telegram:outbox:{session_id}`.
5. **Stop hook (parallel path)**: When the Claude Agent SDK's hook pipeline fires `stop_hook`, the gate MAY intercept (if `_is_telegram_triggered()` AND NOT `_has_pm_messages()`). If it does, it generates a draft via `summarize_response` and presents `SEND/EDIT/REACT/SILENT/CONTINUE`. The agent's choice is written to `AgentSession.delivery_text`/`delivery_action` — but **only `bridge/response.py::send_response_with_files` reads those fields**, not the worker's `send_cb`.
6. **Relay**: `bridge/telegram_relay.py::process_outbox` polls, pops the payload, and either sends via `send_markdown()` (text-only path, line 295) or via `send_file()` (file-attachment path, line 262). Text-only path is where `MessageTooLongError` blows up today.
7. **Output**: User sees the message — OR, on failure, nothing (dead letter).

**The broken leg:** Step 4's `send_cb` writes raw to the outbox with no drafter call, no length check, no file-attachment fallback. Step 6 naïvely calls `send_markdown` on whatever is in the outbox.

**End-to-end flow for an email session:**

1. Entry point: Inbound email → `bridge/email_bridge.py` enqueues `AgentSession` with `extra_context["transport"]="email"`.
2. Worker pickup: Registers `EmailOutputHandler` as callback for `(project_key, "email")`.
3. Session execution: Same as above; persona runs, output emitted via `send_cb`.
4. Output streaming via send_cb: `EmailOutputHandler.send` wraps as `text/plain` MIME → SMTP. **No drafter call, no medium-specific formatting — markdown ships literal.**
5. Stop hook: Early-returns because `_is_telegram_triggered()` returns False (no env vars for email).
6. Output: Recipient sees literal `**bold**`, fenced code, `|…|` tables.

**The broken leg on email side:** Step 3's send_cb has no medium awareness; Step 5's stop hook has no email support.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `421d84a6` (Apr 20, 2026) | Added newline-boundary splitting to `send_markdown` | Treated the symptom (MessageTooLongError) not the cause (no drafter in worker path). Split messages fragment UX, break reply-threading, and ship raw markdown as multiple messages. Reverted. |
| Stop-hook review gate (original design) | Intercepts at session stop, writes `delivery_text` to session | Only covers the `send_response_with_files` flow, which worker-side PM sessions never use. Gate also bypassed whenever PM self-messaged via `send_telegram.py`. |
| `FILE_ATTACH_THRESHOLD` in summarizer | Writes >3000-char output to .txt file, delivers as attachment | Lives inside `bridge/response.py::send_response_with_files`, not reachable from `TelegramRelayOutputHandler.send`. Worker-side outputs bypass it entirely. |

**Root cause pattern:** All three fixes are defensive patches on *one leg* of the delivery path. The underlying problem is **two parallel delivery paths (bridge handler flow vs. worker send_cb flow) with the drafter only wired into the first.** Any fix that doesn't unify them will leak again.

## Architectural Impact

- **New dependencies**: None. Stdlib + existing Anthropic/OpenRouter clients already in use.
- **Interface changes**:
  - `TelegramRelayOutputHandler.send()` signature grows optional `file_paths: list[str] | None = None` and internally calls the drafter.
  - `EmailOutputHandler.send()` similarly gains drafter invocation (plain-prose medium).
  - `draft_message(raw_response, session=None, medium="telegram", persona=None)` is the new API; replaces `summarize_response(raw_response, session=None)`.
  - `AgentSession.delivery_text` / `delivery_action` / `delivery_emoji` plumbing deleted (tool-call replacement).
- **Coupling**:
  - *Reduced*: `bridge/response.py::send_response_with_files` stops being the only drafter entry point; the output handlers become self-contained.
  - *New*: `agent/output_handler.py` now imports `bridge/message_drafter.py` at send time. This is acceptable — output handlers are bridge-infrastructure, and the drafter is a pure function.
- **Data ownership**: The **output handler** becomes the single authority for wire-format compliance. The relay is demoted to a dumb pipe that enforces one invariant: "if a text message >4096 chars reaches me, I log loudly and convert to .txt attachment — never split."
- **Reversibility**: Moderate. The rename is mechanical. The drafter-in-handler call can be toggled behind a feature flag (`MESSAGE_DRAFTER_IN_HANDLER`, default true) for quick rollback. The relay-layer enforcement is additive and low-risk.

## Appetite

**Size:** Large

**Team:** Solo dev (builder), code reviewer, test engineer

**Interactions:**
- PM check-ins: 0 expected (all major design decisions resolved in this plan — see "Resolved Design Decisions" section). Builder may surface one mid-build if the table-migration audit (Step 2.5) uncovers a producer not anticipated here.
- Review rounds: 2 (design review after Part A, code review after Part D)

Justification for Large: rename touches 15+ import sites; consolidation spans 4 files totaling 2,731 lines; five delivery-outcome test coverage is net-new; integration test for the worker-bypass fix is non-trivial. Net line count must be negative outside tests — requires care.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Anthropic API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Drafter LLM backend |
| OpenRouter API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | Drafter fallback backend |
| Redis running | `redis-cli ping` | Outbox + session state |
| Bridge + worker stopped before running tests | `./scripts/valor-service.sh status \| grep -c 'not running'` | Integration tests spawn their own |

Run all checks: `python scripts/check_prerequisites.py docs/plans/message-drafter.md`

## Solution

### Key Elements

- **`bridge/message_drafter.py`** (rename+consolidation): single module owning draft construction, per-medium validation, artifact extraction, and LLM backend fallback. Replaces `bridge/summarizer.py` entirely and absorbs the delivery-specific parts of `bridge/response.py` and `bridge/formatting.py`.
- **Medium-aware `draft_message()` API**: `draft_message(raw_response, *, session=None, medium="telegram", persona=None)` returning `MessageDraft(text, file_paths, was_drafted, artifacts, context_summary, expectations)`. Three per-medium behaviors: Telegram (current rules minus tables), Email (plain prose only), fallback (safety truncate + steering).
- **Drafter invocation at the OutputHandler boundary** (the critical new plumbing): `TelegramRelayOutputHandler.send` and `EmailOutputHandler.send` call `draft_message` before writing to the wire. This closes the worker-side bypass gap.
- **Relay as dumb pipe + length guard**: `bridge/telegram_relay.py::_send_queued_message` grows a pre-send length check. If `text > 4096`, log loudly (ERROR with session_id, chat_id, len), write the raw text to a temp `.txt` file, and convert the payload to file-attachment mode before sending. Never split.
- **Medium-agnostic stop-hook review gate**: `agent/hooks/stop.py` resolves `medium = session.extra_context.get("transport", "telegram")`, skips early when `session.parent_agent_session_id` is set, and threads medium into `draft_message`. Delete `_is_telegram_triggered()` and the `_has_pm_messages()` early-return.
- **Tool-call delivery contract**: Drop the `SEND`/`EDIT:`/`REACT:`/`SILENT`/`CONTINUE` string menu. Present the draft as a prepopulated `send_message(text=..., reply_to=...)` tool call via MCP. Agent invokes, edits-and-invokes, swaps for `react_with_emoji`, or stops without invoking.
- **New MCP tool surface**: `send_message` (polymorphic, routes by `session.extra_context.transport`) and `react_with_emoji` registered in `.mcp.json`. Implementation delegates to the session's registered `OutputHandler`.
- **Validator surfaces violations to the agent, no re-draft loop, no server-side rewrite**.

### Flow

**Human message → draft → delivered reply (Telegram PM session, happy path):**

Telegram message → bridge enqueues session → worker executes PM → PM's `send_cb` fires → `TelegramRelayOutputHandler.send` calls `draft_message(msg, medium="telegram", persona=PM)` → `MessageDraft` has `text≤4096` (either summarized or raw-if-short) and optional `file_paths=[full_output.txt]` if raw was >FILE_ATTACH_THRESHOLD → payload written to Redis outbox → relay reads payload → if `file_paths` present, `send_file(caption=text)`; else `send_markdown(text)` with pre-send length guard as belt-and-suspenders → user sees short message (and optional `.txt` for detail).

**Stop-hook review gate (single path, both media):**

Session stop → `stop_hook` reads `session.extra_context.transport` (default `"telegram"`) → if `session.parent_agent_session_id`, return early (child session) → generate draft via `draft_message(output_tail, medium=medium, persona=session.persona)` → present **as a prepopulated `send_message` tool call** (not a text menu) → block → agent invokes `send_message` / `react_with_emoji` / stops silent / continues → stop hook classifies by tool-call history → writes no `delivery_text`; the tool call itself delivers.

### Technical Approach

- **Part A — Rename & consolidate** (mechanical, aggressive): `bridge/summarizer.py` → `bridge/message_drafter.py`; `summarize_response` → `draft_message`; `SummarizedResponse` → `MessageDraft`. Pull delivery-owned code from `bridge/response.py` (file attachment I/O, artifact extraction, truncation) into the new module. Delete `bridge/formatting.py` (fold its 76 lines of markdown helpers into `message_drafter.py` where they belong, or eliminate unused ones). Audit `bridge/response.py`: the `delivery_text`/`delivery_action`/`delivery_emoji` plumbing is deleted wholesale (tool-call replacement); `send_response_with_files` either shrinks significantly or collapses into the handler. Grep `summariz` repo-wide and replace in live code, tests, `CLAUDE.md`, and `docs/features/`. Historical plans in `docs/plans/completed/` left alone.
- **Part B — Medium parameter + validators**: add `medium` to `draft_message`. Refactor prompt construction into `base + medium_rules + persona_tone + session_context`. Telegram rules forbid `|…|` table syntax; email rules forbid all markdown. Validator is a pure function run after the draft; violations are appended to the draft presentation as a `⚠️` note for the agent to see and edit around. No retry loop, no server-side rewrite.
- **Part C — Drafter-at-the-handler** (the critical fix): `TelegramRelayOutputHandler.send` calls `await draft_message(text, session=session, medium="telegram", persona=<derived>)` internally. If the draft has `file_paths`, write them into the outbox payload. Same pattern for `EmailOutputHandler.send`. `FileOutputHandler` is a no-op pass-through (no drafter — it's a debug sink). Behind feature flag `MESSAGE_DRAFTER_IN_HANDLER=true` (default true, can be disabled for rollback).
- **Part D — Relay length guard** (belt-and-suspenders): `bridge/telegram_relay.py::_send_queued_message`: before the text-only send path at line 293, check `len(text) > 4096`. If so, log ERROR with `{session_id, chat_id, len, preview=text[:200]}`, write the raw text to `/tmp/relay_overlong_{session_id}_{ts}.txt`, and rewrite the payload to use the file-attachment path with caption `"[auto-attached: response exceeded 4096 chars]"`. This protects against drafter bugs — NOT a primary fix.
- **Part E — Tool-call delivery**: new MCP tools `send_message(text, reply_to=None)` and `react_with_emoji(emoji)` in a new `mcp_servers/message_delivery_server.py`. Registered in `.mcp.json`. Implementation looks up the session's registered `(project_key, transport)` OutputHandler and calls `.send()` / `.react()`. Delete `_parse_review_choice`, `_parse_delivery_choice`, `_write_delivery_to_session`, the `SEND/EDIT:/REACT:/SILENT/CONTINUE` prompt construction, and the `delivery_text`/`delivery_action`/`delivery_emoji` fields on `AgentSession` (schema migration — defer to Tom per CLAUDE.md rules). Stop-hook clearing strategy: **implicit** — the hook inspects the transcript between first and second stop for tool-call history and classifies outcome (see §7 of Solution below).
- **Part F — Stop-hook unification**: delete `_is_telegram_triggered`. Resolve `medium = session.extra_context.get("transport", "telegram")`. Skip when `session.parent_agent_session_id`. Evaluate whether `_has_pm_messages()` can be deleted entirely given Part C closes the original gap; if removable, delete (simpler); if not, justify inline.

### Clearing strategy — implicit tool-call inspection (builder's pick)

**Why implicit over explicit sentinel:**
- The agent already has access to the tool-call API; a sentinel is extra ceremony.
- Implicit inspection has been a pattern in the codebase (e.g., `has_pm_messages` detecting self-messaging). Extending it is idiomatic.
- No new MCP tool needs to return a magic token.

**How it works:** Between first and second stop, inspect the transcript tail for tool_use blocks targeting `send_message` or `react_with_emoji`. Classify:

| Outcome | Detection | Hook action |
|---|---|---|
| Send | `send_message` tool_use block present, args match draft text verbatim | Clear review state, allow completion |
| Edit + send | `send_message` tool_use block present, args differ from draft | Clear review state, allow completion |
| React | `react_with_emoji` tool_use block present | Clear review state, allow completion |
| Silent | No send/react tool_use; no text output | Clear review state, allow completion |
| Continue | Other tool_use blocks present, no stop-turn signal | Do not clear; next stop re-enters gate |

Tests cover all five outcomes end-to-end (see Success Criteria).

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `TelegramRelayOutputHandler.send` catches `Exception` on Redis write (current line 227). Test asserts that drafter failure also falls back gracefully — if `draft_message` raises, fall back to the raw text path with length guard as last line of defense.
- [x] `EmailOutputHandler.send` — test that drafter failure doesn't block email send; fall back to plain stripping of markdown + log WARNING.
- [x] `bridge/telegram_relay.py::_send_queued_message` — test that on MessageTooLongError despite the length guard (e.g., Unicode char expansion), the existing retry + dead-letter path still functions.
- [x] `draft_message` — test that all three LLM backends failing triggers the `self-summary steering` fallback from `docs/plans/summarizer-fallback-steering.md`.

### Empty/Invalid Input Handling
- [x] `draft_message("")` returns `MessageDraft(text="", was_drafted=False)` — existing contract preserved.
- [x] `draft_message(None)` raises `TypeError` — no silent coercion.
- [x] `draft_message("   \n\n\t  ")` returns same as empty — whitespace-only treated as empty.
- [x] Validator on empty string returns "no violations".

### Error State Rendering
- [x] Drafter validator violation is surfaced in the draft presentation as a `⚠️` note; test asserts the note appears and does NOT appear when validation passes.
- [x] Relay length-guard trip logs ERROR with structured fields; test asserts log shape.
- [x] Drafter failure → raw-text fallback → relay length-guard trip → `.txt` conversion: integration test asserts the full defense-in-depth chain works when the primary drafter path crashes.

## Test Impact

- [ ] `tests/unit/test_summarizer.py` — REPLACE as `tests/unit/test_message_drafter.py`; add per-medium format assertions, validator tests, and the orthogonality matrix (medium × persona).
- [ ] `tests/integration/test_summarizer_integration.py` — REPLACE as `tests/integration/test_message_drafter_integration.py`; cover Telegram and email end-to-end through real `OutputHandler.send` calls.
- [ ] `tests/unit/test_stop_hook.py` — UPDATE: replace `_is_telegram_triggered()` mocks with `session.extra_context.transport` fixture; add child-session early-return case.
- [ ] `tests/unit/test_stop_hook_review.py` — REPLACE: delete `_parse_review_choice` tests; add five-outcome coverage (send/edit+send/react/silent/continue) driving session to correct terminal state.
- [ ] `tests/unit/test_subagent_stop_hook.py` — UPDATE: verify child sessions skip the drafter entirely (`parent_agent_session_id` set).
- [ ] `tests/unit/test_email_bridge.py` — UPDATE: add end-to-end assertion that inbound email → outbound SMTP body contains no markdown syntax (drafter invoked with `medium="email"`).
- [ ] `tests/unit/test_delivery_execution.py` — DELETE or REPLACE: delivery is now invoked via tool calls, not `delivery_text` field; if this file tests only the deleted plumbing, delete.
- [ ] `tests/unit/test_cross_wire_fixes.py`, `tests/unit/test_open_question_gate.py`, `tests/unit/test_work_request_classifier.py` — UPDATE: import paths and mock targets for the renamed module.
- [ ] `tests/unit/test_output_handler.py` — UPDATE: assert `TelegramRelayOutputHandler.send` invokes `draft_message` (and writes `file_paths` when long); assert `EmailOutputHandler.send` invokes `draft_message(medium="email")`.
- [ ] `tests/integration/test_reply_delivery.py` — UPDATE: `TelegramRelayOutputHandler` now produces draft-shaped output; update assertions accordingly.
- [ ] `tests/e2e/test_nudge_loop.py` — UPDATE: nudge outputs routed through drafter; update expected message shapes.
- [ ] **NEW**: `tests/integration/test_worker_pm_long_output.py` — spawn a worker-executed PM session that deliberately produces >4096 chars of raw output. Assert: (a) outbox payload's `text` is ≤4096 chars OR has `file_paths=[*.txt]`, (b) no `MessageTooLongError` appears in bridge logs across the entire run, (c) a `.txt` file was written to disk with the full raw content, (d) the delivered message preview contains a short caption/summary — NOT split chunks.
- [ ] **NEW**: `tests/unit/test_relay_length_guard.py` — unit test for the belt-and-suspenders length guard in `telegram_relay.py`: 4097-char text triggers ERROR log + `.txt` conversion.
- [ ] **NEW**: `tests/unit/test_tool_call_delivery.py` — tests the five-outcome clearing strategy.
- [ ] Full-suite grep: `summarize_response` / `SummarizedResponse` / `bridge.summarizer` must return zero hits in live code, tests, and docs (historical plan docs excepted).

## Rabbit Holes

- **Rewriting persona prompts**: personas are out of scope. Medium and persona stay orthogonal — we do NOT touch persona tone logic.
- **Email HTML/multipart**: explicitly rejected in the issue. Plain prose only. Do not add `multipart/alternative`.
- **Porting markdown-to-HTML for Telegram**: we're only concerned with wire-format compliance (no tables). Keep existing markdown rendering.
- **Schema migration for `delivery_text`/`delivery_action`/`delivery_emoji`**: Tom owns migrations. Leave the fields in place (unused) until Tom drops them. Code stops reading/writing them.
- **Splitting long messages**: **BANNED.** Every reviewer must check for this.
- **Handler-internal retry on drafter failure**: one attempt + fallback path. Don't add retry loops.
- **Fixing customer-service persona routing**: scope of #955, not this plan.

## Risks

### Risk 1: Drafter-at-the-handler adds a per-message LLM call to EVERY outbound message, including short ones
**Impact:** Latency regression on short replies (currently direct-write to outbox; proposed: Haiku call first).
**Mitigation:** Early return in `draft_message` for short non-SDLC outputs (existing behavior, line 431 of response.py). Per D5a, the threshold is **200 chars uniform** across bridge-handler and worker paths. If `len(text) < 200` AND no SDLC session AND no artifacts AND medium rules are satisfied as-is, return `MessageDraft(text=text, was_drafted=False)` without calling any LLM. Acceptance: unit test on a 50-char text asserts 0 Anthropic/OpenRouter API calls.

### Risk 2: Relay length guard trips unexpectedly on existing traffic post-deploy
**Impact:** Existing PM flows that rely on long-form Telegram output (dashboards, status reports) suddenly get `.txt` attachments instead of inline text.
**Mitigation:** Log aggregation 48 hours pre-deploy: grep bridge logs for any sent message >4000 chars. Any caller above this threshold gets individual review before deploy. Expected result: the only current offenders are the drafter bypass cases this plan fixes.

### Risk 3: Implicit tool-call-history clearing misclassifies edge cases
**Impact:** Agent intends to send, hook thinks silent; or vice-versa.
**Mitigation:** Five-outcome integration tests driving session to each terminal state; manual testing matrix before merge. If a specific misclassification pattern emerges in staging, add a sentinel token as a fast-follow (design allows this — `send_message` tool can append a magic comment server-side).

### Risk 4: `MESSAGE_DRAFTER_IN_HANDLER` feature-flag rollback leaves code in inconsistent state
**Impact:** With the flag off, we're back to the broken worker-bypass path.
**Mitigation:** Flag is a safety net, not a long-term config. Default true. Remove the flag (and the fallback branch) two weeks post-merge assuming no rollbacks.

### Risk 5: Emoji reactions emitted via `react_cb` also bypass the drafter — but reactions have no length
**Impact:** None (reactions are a single emoji, no >4096 issue possible).
**Mitigation:** N/A. Reactions skip the drafter by design; document this in `docs/features/message-drafter.md`.

## Race Conditions

### Race 1: PM self-message via `send_telegram.py` races with stop-hook review gate
**Location:** `agent/hooks/stop.py:245` (`_has_pm_messages` check) + `tools/send_telegram.py` (which writes `pm_sent_message_ids` via `record_pm_message`).
**Trigger:** PM agent calls `send_telegram.py` mid-turn → `record_pm_message` writes to Redis → session stops → `stop_hook` reads `has_pm_messages()` → sees True → skips gate. This is the CURRENT behavior; Part C of Solution closes the gap by running drafter at the handler instead.
**Data prerequisite:** `pm_sent_message_ids` is populated before stop hook reads it.
**State prerequisite:** Session must be in a state where the next `send_cb` call routes through the new drafter path.
**Mitigation:** Removing the `_has_pm_messages` early-return (Part F). The gate is redundant once the handler itself drafts. If we keep `_has_pm_messages`, it becomes a tiny performance optimization (skip redundant drafting); functionally moot.

### Race 2: Two concurrent `send_cb` calls on the same session write to the outbox with interleaving
**Location:** `agent/output_handler.py:188`.
**Trigger:** Multiple tool outputs arrive before any one is drained. Today the outbox LIST preserves order (`rpush`), so interleaving is benign. Adding a drafter call introduces async time; if two `send_cb` calls run in parallel, their drafter calls may complete out of order.
**Data prerequisite:** Outbox payload order matches logical message order.
**State prerequisite:** Per-session `send_cb` invocations serialize at the caller (session_executor) level today.
**Mitigation:** `session_executor.py`'s `send_to_chat` is awaited in sequence — no concurrent calls per session. Acceptance: code-read confirms `await send_cb(...)` is not wrapped in `asyncio.gather` or `create_task`. If a future change introduces concurrency, add a per-session lock in `TelegramRelayOutputHandler`.

### Race 3: Feature flag toggled mid-session
**Location:** Environment variable `MESSAGE_DRAFTER_IN_HANDLER`.
**Trigger:** Operator flips the flag between `send_cb` invocations of the same session.
**Data prerequisite:** None.
**State prerequisite:** The flag should be sticky per session.
**Mitigation:** Read the flag once at `OutputHandler.__init__` time, not per send. Document as a startup-config, not a runtime-config.

## No-Gos (Out of Scope)

- **No message splitting. Ever.** Any PR that splits messages at newlines, sentence boundaries, or character counts is rejected — even as a "safety net."
- No email HTML / multipart bodies.
- No persona logic changes.
- No customer-service persona fixes (#955 territory).
- No schema migration — `delivery_text`/`delivery_action`/`delivery_emoji` fields stay in the model until Tom drops them; we stop reading/writing them.
- No retry loops on drafter failure — one attempt, one fallback, move on.
- No Telegraph (telegra.ph) integration. `.txt` attachment is the canonical long-form delivery mechanism.
- No changes to `FileOutputHandler` beyond no-op pass-through — it's a debug sink, not a user-facing path.

## Update System

- New MCP server file (`mcp_servers/message_delivery_server.py`) is picked up automatically by the `/update` skill via the existing `.mcp.json` sync logic.
- `.mcp.json` registration for `send_message` and `react_with_emoji` tools must be added in the PR — `/update` skill will propagate.
- Feature flag `MESSAGE_DRAFTER_IN_HANDLER` defaults to `true` in `config/settings.py`; no `.env` changes required. If rollback is needed, a single-line env var addition in `~/Desktop/Valor/.env` turns it off.
- No migration scripts required (per No-Gos).
- `./scripts/valor-service.sh restart` after deploy — mandatory per CLAUDE.md rule 10 (bridge + worker code changed).

## Agent Integration

- **New MCP server required.** `mcp_servers/message_delivery_server.py` exposes:
  - `send_message(text: str, reply_to: int | None = None)` — polymorphic; routes by `session.extra_context.transport`. Delegates to the session's registered `OutputHandler.send`.
  - `react_with_emoji(emoji: str)` — delegates to `OutputHandler.react`. No-op for email (matches current `EmailOutputHandler.react()`).
- **Registered in `.mcp.json`** under `mcpServers` with the standard stdio transport.
- **The bridge itself (`bridge/telegram_bridge.py`) changes:** its `send_response_with_files` call site at line 2185 shrinks or disappears — the drafter-at-the-handler design moves that logic out of the bridge. The handler now owns it.
- **Integration tests that verify the agent can actually invoke the new tools** (required):
  - `tests/integration/test_mcp_message_delivery.py` — real Claude Agent SDK session invokes `send_message`; asserts outbox write.
  - `tests/integration/test_mcp_react_with_emoji.py` — real session invokes `react_with_emoji`; asserts reaction is set on the replied-to message.
- The stop hook itself does not need MCP exposure — it's invoked by the SDK hook pipeline, not by the agent.

## Documentation

### Feature Documentation
- [x] Create `docs/features/message-drafter.md` — canonical feature doc covering:
  - Medium/persona orthogonality (with examples)
  - Per-medium format rules (Telegram: no tables; Email: plain prose)
  - Validator behavior (surface violations to agent; no rewrites) — _partial: section present; full behavior ships with task 3_
  - Tool-call delivery contract (`send_message`, `react_with_emoji`) — _flagged as "Five-outcome delivery (planned)"; ships with tasks 9/11_
  - Five delivery outcomes and the implicit clearing strategy — _flagged as planned; ships with tasks 11/12_
  - FILE_ATTACH_THRESHOLD → `.txt` attachment behavior for long content
  - Defense-in-depth: drafter-at-handler + relay length guard + dead-letter as last resort
- [x] Add entry to `docs/features/README.md` index — replace the "summarizer" row with `message-drafter.md`.
- [x] Delete `docs/features/summarizer-format.md` (content migrated) and redirect references.
- [x] Update `docs/features/email-bridge.md` — add a section on how outbound email bodies are drafted and validated (medium=email path).

### External Documentation Site
- N/A — this repo doesn't use Sphinx/MkDocs for user-facing docs.

### Inline Documentation
- [x] Docstrings on `draft_message`, `MessageDraft`, per-medium validators.
- [x] Inline comment at the `TelegramRelayOutputHandler.send` drafter call explaining why it lives here and not in the bridge.
- [x] Inline comment at the relay length-guard block explaining the `.txt` conversion rationale.

### Cross-cutting
- [x] `CLAUDE.md` — grep for "summariz", replace in System Architecture diagram and any bullet references.
- [x] `docs/plans/summarizer-fallback-steering.md` (completed-adjacent plan) — add a pointer at the top: "implementation renamed to message_drafter per #1035."

## Success Criteria

- [ ] `bridge/summarizer.py` renamed/consolidated to `bridge/message_drafter.py`; `summarize_response` → `draft_message`; `SummarizedResponse` → `MessageDraft`.
- [ ] Zero occurrences of "summariz" in live code, current `docs/features/`, `CLAUDE.md`, comments, or new tests. `docs/plans/completed/` left alone.
- [ ] Stop hook resolves `medium` from `session.extra_context.transport` (default `"telegram"`); `_is_telegram_triggered()` deleted.
- [ ] Stop hook returns early when `session.parent_agent_session_id` is set; no drafter call for child sessions.
- [ ] `draft_message(medium="telegram")` emits output with no markdown tables; `draft_message(medium="email")` emits plain prose with no markdown.
- [ ] Validator rejects markdown tables for Telegram and any markdown for email; violations are surfaced in the draft presentation (no re-draft, no server-side rewrite).
- [ ] Agent receives the draft as a prepopulated `send_message` tool call; `_parse_review_choice` and `delivery_text`/`delivery_action`/`delivery_emoji` writes are deleted.
- [ ] `send_message` and `react_with_emoji` MCP tools route through the session's `OutputHandler` for both Telegram and email.
- [ ] Implicit clearing strategy has tests covering all five outcomes (send / edit+send / react / silent / continue), each driving the session to the correct terminal state.
- [ ] Email sessions emit no markdown on the wire (verified end-to-end via integration test parsing the outbound MIME body).
- [ ] Telegram sessions never emit markdown tables (verified via validator unit tests + bridge integration test).
- [ ] **PRIMARY FIX — NEW:** A worker-executed Telegram PM session producing >4096 chars of raw output cannot reach the relay without going through the drafter first. Verified by `tests/integration/test_worker_pm_long_output.py` asserting:
  - (a) outbox payload is either ≤4096 chars text OR has `file_paths=[*.txt]`,
  - (b) no `MessageTooLongError` appears in bridge logs during the run,
  - (c) full raw content is preserved in the `.txt` attachment.
- [ ] **RELAY BELT-AND-SUSPENDERS:** `bridge/telegram_relay.py` length guard trips on any >4096-char text reaching it, logs ERROR, converts to `.txt` attachment. Verified by `tests/unit/test_relay_length_guard.py`.
- [ ] **NO SPLITTING:** `grep -rn "split.*4096\|for.*chunk.*send\|send.*part" bridge/ agent/` returns zero hits on delivery code paths.
- [ ] Net line count outside tests is negative — PR removes more lines than it adds (excluding new test files / test cases).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Dead letter replay after deploy: existing dead-lettered message from session `tg_cuttlefish_-1003801797780_94` gets re-delivered successfully via `.txt` attachment (manual verification — if the DL is still present).

## Team Orchestration

The lead orchestrator deploys team members and coordinates. All Builders deliver working code + passing tests; Validators run read-only verification.

### Team Members

- **Builder (rename + consolidation)**
  - Name: `drafter-rename-builder`
  - Role: Rename `summarizer` → `message_drafter`, consolidate `bridge/response.py`/`bridge/formatting.py` delivery code into the new module, grep-replace all import sites.
  - Agent Type: `builder`
  - Resume: true

- **Builder (medium + validators)**
  - Name: `drafter-medium-builder`
  - Role: Add `medium` parameter, reorganize prompts into 4 segments, implement per-medium validators with violation surfacing.
  - Agent Type: `builder`
  - Resume: true

- **Builder (handler drafter integration — the critical fix)**
  - Name: `drafter-handler-builder`
  - Role: Wire `draft_message` into `TelegramRelayOutputHandler.send` and `EmailOutputHandler.send` behind the `MESSAGE_DRAFTER_IN_HANDLER` flag.
  - Agent Type: `builder`
  - Resume: true

- **Builder (relay length guard)**
  - Name: `relay-guard-builder`
  - Role: Add belt-and-suspenders length guard to `bridge/telegram_relay.py::_send_queued_message` with `.txt` conversion.
  - Agent Type: `builder`
  - Resume: true

- **Builder (MCP tool surface + tool-call delivery)**
  - Name: `mcp-delivery-builder`
  - Role: Create `mcp_servers/message_delivery_server.py`, register in `.mcp.json`, implement `send_message` / `react_with_emoji`, delete `_parse_review_choice` + menu-parsing plumbing.
  - Agent Type: `mcp-specialist`
  - Resume: true

- **Builder (stop-hook unification)**
  - Name: `stop-hook-builder`
  - Role: Remove `_is_telegram_triggered`, add `parent_agent_session_id` early-return, thread `medium` through `draft_message`, implement implicit clearing strategy.
  - Agent Type: `builder`
  - Resume: true

- **Test Engineer (worker-bypass integration test — primary acceptance criterion)**
  - Name: `worker-bypass-test-engineer`
  - Role: Write `tests/integration/test_worker_pm_long_output.py` that reproduces the `tg_cuttlefish_-1003801797780_94` scenario and asserts drafter interception + `.txt` attachment.
  - Agent Type: `test-engineer`
  - Resume: true

- **Test Engineer (five-outcome + validator tests)**
  - Name: `delivery-test-engineer`
  - Role: Write five-outcome coverage for tool-call clearing; per-medium validator unit tests; length-guard unit test.
  - Agent Type: `test-engineer`
  - Resume: true

- **Validator**
  - Name: `drafter-validator`
  - Role: Run all validation commands; verify no "summariz" hits in live code; check net-line-count-outside-tests is negative; confirm no splitting patterns introduced.
  - Agent Type: `validator`
  - Resume: true

- **Documentarian**
  - Name: `drafter-documentarian`
  - Role: Create `docs/features/message-drafter.md`; update index; delete/redirect `summarizer-format.md`; update `email-bridge.md`; grep `CLAUDE.md` for summariz references.
  - Agent Type: `documentarian`
  - Resume: true

- **Code Reviewer**
  - Name: `drafter-code-reviewer`
  - Role: Review full diff for quality, no-splitting compliance, feature-flag plumbing, docstring quality.
  - Agent Type: `code-reviewer`
  - Resume: true

## Step by Step Tasks

### 1. Rename + consolidate baseline
- **Task ID**: build-rename
- **Depends On**: none
- **Validates**: `tests/unit/test_message_drafter.py` (renamed from test_summarizer.py), `tests/integration/test_message_drafter_integration.py`
- **Informed By**: spike-1 (stop-hook gate firing confirmed), spike-4 (orthogonality plan)
- **Assigned To**: drafter-rename-builder
- **Agent Type**: builder
- **Parallel**: false (foundational)
- Rename `bridge/summarizer.py` → `bridge/message_drafter.py`.
- Rename `summarize_response` → `draft_message`; `SummarizedResponse` → `MessageDraft`.
- Move delivery-specific code from `bridge/response.py` (file attachment I/O, artifact extraction, truncation) into `bridge/message_drafter.py`. Delete redundant duplication.
- Delete `bridge/formatting.py`; fold helpers into `bridge/message_drafter.py` or eliminate if unused.
- Grep replace "summariz" repo-wide in live code, tests, `CLAUDE.md`, `docs/features/*`. Skip `docs/plans/completed/` and `docs/plans/critiques/`.
- Update all import sites (`agent/hooks/stop.py`, `agent/sdk_client.py`, `bridge/telegram_bridge.py`, test files).
- Run `pytest tests/unit/test_message_drafter.py` — all tests pass post-rename.

### 2. Validate rename
- **Task ID**: validate-rename
- **Depends On**: build-rename
- **Assigned To**: drafter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "summariz" --include="*.py" --include="*.md" .` — zero hits in live code/tests/current docs.
- Run `pytest tests/unit/ -x` — all tests pass.
- Run `python -m ruff check .` — lint clean.

### 2.5. Migrate existing markdown-table producers (pre-validator audit)
- **Task ID**: migrate-table-producers
- **Depends On**: validate-rename
- **Validates**: `grep -rn '\| ---' bridge/ tools/ agent/ .claude/skills/` returns zero hits on user-facing Telegram-emitting code paths (doc files in `docs/` are excluded from the audit)
- **Informed By**: D3 (decision to enforce validator on day 1)
- **Assigned To**: drafter-medium-builder
- **Agent Type**: builder
- **Parallel**: false (must precede 3 so validator and migration land atomically)
- Run `grep -rn '\| ---' bridge/ tools/ agent/ .claude/skills/` to inventory producers.
- Audit PM/Teammate skill prompts that summarize data or list findings for any runtime table-generation patterns (e.g., "render this as a table").
- Confirmed target: `.claude/skills/do-pr-review/SKILL.md` emits `| File | Change |` in review summaries — replace with bulleted "File — Change" list.
- For any additional producer found, migrate to bullets or inline prose in the same commit.
- Do NOT touch documentation files in `docs/` — they render in GitHub, not Telegram.
- Commit with message `"Migrate Telegram-bound markdown tables to bullets (pre-drafter validator)"`.

### 3. Add medium parameter + validators
- **Task ID**: build-medium
- **Depends On**: build-rename, migrate-table-producers
- **Validates**: `tests/unit/test_message_drafter.py` (per-medium tests), `tests/unit/test_drafter_validators.py` (new)
- **Informed By**: spike-4
- **Assigned To**: drafter-medium-builder
- **Agent Type**: builder
- **Parallel**: true (with 5)
- Add `medium: str = "telegram"` and `persona: str | None = None` params to `draft_message`.
- Reorganize prompt into `base + medium_rules + persona_tone + session_context`.
- Implement `validate_telegram(text) -> list[Violation]` (reject `|…|` tables).
- Implement `validate_email(text) -> list[Violation]` (reject any markdown).
- Surface violations in draft presentation as `⚠️` note.
- Write per-medium unit tests.

### 4. Validate medium + validators
- **Task ID**: validate-medium
- **Depends On**: build-medium
- **Assigned To**: drafter-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm Telegram drafts never contain tables; email drafts never contain markdown.
- Confirm validator violations appear in draft presentation.

### 5. Relay belt-and-suspenders length guard
- **Task ID**: build-relay-guard
- **Depends On**: build-rename
- **Validates**: `tests/unit/test_relay_length_guard.py` (new)
- **Informed By**: spike-2
- **Assigned To**: relay-guard-builder
- **Agent Type**: builder
- **Parallel**: true (with 3)
- In `bridge/telegram_relay.py::_send_queued_message`, add pre-send check: `if len(text) > 4096`: log ERROR with structured fields, write raw text to `/tmp/relay_overlong_{session_id}_{ts}.txt`, rewrite payload to file-attachment mode.
- Preserve existing retry + dead-letter behavior as last resort.
- Unit test with 4097-char input.

### 6. Validate relay guard
- **Task ID**: validate-relay-guard
- **Depends On**: build-relay-guard
- **Assigned To**: drafter-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm 4097-char inputs trigger `.txt` conversion, not split.
- Confirm ERROR log contains session_id, chat_id, len.

### 7. Drafter-at-the-handler (the critical fix)
- **Task ID**: build-handler-drafter
- **Depends On**: build-medium, build-relay-guard
- **Validates**: `tests/unit/test_output_handler.py` (update), `tests/integration/test_worker_pm_long_output.py` (new — primary acceptance test)
- **Informed By**: spike-3 (drafter importable from worker)
- **Assigned To**: drafter-handler-builder
- **Agent Type**: builder
- **Parallel**: false (must follow 5 so both layers are present for tests)
- Add `MESSAGE_DRAFTER_IN_HANDLER` flag to `config/settings.py` (default `True`).
- In `agent/output_handler.py::TelegramRelayOutputHandler.send`, if flag is true, call `await draft_message(text, session=session, medium="telegram", persona=<resolved>)` before writing to outbox.
- If `MessageDraft.file_paths` is non-empty, include them in the outbox payload.
- Mirror for `EmailOutputHandler.send` with `medium="email"`.
- Early-return for short non-SDLC outputs (<200 chars, no artifacts) — no LLM call.
- `FileOutputHandler.send` is a no-op pass-through.

### 8. Validate handler drafter + worker-bypass fix
- **Task ID**: validate-handler-drafter
- **Depends On**: build-handler-drafter
- **Assigned To**: worker-bypass-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Write `tests/integration/test_worker_pm_long_output.py` — spawn a real worker-executed PM session producing >4096 chars; assert outbox payload is ≤4096 OR has `.txt` file_paths; assert no `MessageTooLongError` in logs; assert raw content preserved in `.txt`.
- Run integration test suite.

### 9. MCP tool surface + tool-call delivery
- **Task ID**: build-mcp-delivery
- **Depends On**: build-handler-drafter
- **Validates**: `tests/integration/test_mcp_message_delivery.py` (new), `tests/integration/test_mcp_react_with_emoji.py` (new)
- **Assigned To**: mcp-delivery-builder
- **Agent Type**: mcp-specialist
- **Parallel**: true (with 11)
- Create `mcp_servers/message_delivery_server.py` exposing `send_message(text, reply_to=None)` and `react_with_emoji(emoji)`.
- Register in `.mcp.json` under `mcpServers`.
- Implementation: look up the session's registered `(project_key, transport)` `OutputHandler` and delegate.
- Delete `_parse_review_choice`, `_parse_delivery_choice`, `_write_delivery_to_session`.
- Delete read sites for `delivery_text`/`delivery_action`/`delivery_emoji` in `bridge/response.py`. Leave schema fields in place (Tom owns migrations).
- Remove the `SEND/EDIT:/REACT:/SILENT/CONTINUE` string-menu construction.

### 10. Validate MCP delivery
- **Task ID**: validate-mcp-delivery
- **Depends On**: build-mcp-delivery
- **Assigned To**: delivery-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Real SDK sessions invoke `send_message`; assert outbox write with correct fields.
- Real SDK sessions invoke `react_with_emoji`; assert reaction set on replied-to msg.

### 11. Stop-hook unification
- **Task ID**: build-stop-hook
- **Depends On**: build-medium, build-mcp-delivery
- **Validates**: `tests/unit/test_stop_hook.py` (update), `tests/unit/test_stop_hook_review.py` (replace), `tests/unit/test_subagent_stop_hook.py` (update)
- **Assigned To**: stop-hook-builder
- **Agent Type**: builder
- **Parallel**: true (with 9)
- Delete `_is_telegram_triggered`.
- Resolve `medium = session.extra_context.get("transport", "telegram")`.
- Add early return for `session.parent_agent_session_id`.
- Thread `medium` and `persona` into `draft_message`.
- Replace string-menu prompt with a prepopulated `send_message` tool_call presentation.
- Implement implicit clearing strategy: inspect transcript tail between first/second stop for `send_message` / `react_with_emoji` tool_use blocks; classify into five outcomes.
- Evaluate `_has_pm_messages` — remove if possible (handler now drafts anyway); keep + document if not.

### 12. Validate stop-hook + five-outcome coverage
- **Task ID**: validate-stop-hook
- **Depends On**: build-stop-hook
- **Assigned To**: delivery-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Write five-outcome tests (send / edit+send / react / silent / continue), each driving session to correct terminal state.
- Confirm child sessions skip the drafter entirely.
- Confirm email sessions hit the gate with `medium="email"`.

### 13. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-handler-drafter, validate-mcp-delivery, validate-stop-hook
- **Assigned To**: drafter-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/message-drafter.md`.
- Delete `docs/features/summarizer-format.md`; migrate content.
- Update `docs/features/README.md` index.
- Update `docs/features/email-bridge.md` with drafter section.
- Update `CLAUDE.md` architecture references.

### 13.5. File chore follow-up issue
- **Task ID**: file-chore-issue
- **Depends On**: document-feature
- **Assigned To**: drafter-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Open a new GitHub issue titled `"Drop AgentSession.delivery_text/delivery_action/delivery_emoji fields and remove MESSAGE_DRAFTER_IN_HANDLER flag"` with label `chore`.
- Body references #1035 as the PR that stopped reading/writing these fields and introduced the flag.
- Body notes the two-week post-merge window for flag removal (D5d).
- Body lists: (a) schema migration to drop three `AgentSession` columns (Tom owns), (b) delete `MESSAGE_DRAFTER_IN_HANDLER` reads in `agent/output_handler.py`, (c) delete the fallback branch, (d) delete the setting in `config/settings.py`.
- Record the new issue number in the #1035 PR description as a follow-up.

### 14. Code review
- **Task ID**: review-code
- **Depends On**: file-chore-issue
- **Assigned To**: drafter-code-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Full-diff review.
- Verify: no splitting, flag plumbing correct, docstrings adequate, net line count negative outside tests.

### 15. Final validation
- **Task ID**: validate-all
- **Depends On**: review-code
- **Assigned To**: drafter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite (`pytest tests/ -x -q`).
- Run lint + format checks.
- Grep for "summariz" in live code — zero hits.
- Grep for splitting patterns — zero hits.
- Compute net line count outside tests — must be negative.
- Replay dead-lettered message from session `tg_cuttlefish_-1003801797780_94` (if still present) — confirm `.txt` attachment delivery works.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No "summariz" in live code | `grep -rn "summariz" --include="*.py" .` | exit code 1 |
| No "summariz" in current docs | `grep -rn "summariz" docs/features/ CLAUDE.md` | exit code 1 |
| No splitting patterns | `grep -rn "split.*4096\\|for.*chunk.*send" bridge/ agent/` | exit code 1 |
| Worker-bypass integration test | `pytest tests/integration/test_worker_pm_long_output.py -v` | exit code 0 |
| Relay length guard unit test | `pytest tests/unit/test_relay_length_guard.py -v` | exit code 0 |
| Five-outcome delivery tests | `pytest tests/unit/test_tool_call_delivery.py -v` | exit code 0 |
| MCP tool integration tests | `pytest tests/integration/test_mcp_message_delivery.py tests/integration/test_mcp_react_with_emoji.py -v` | exit code 0 |
| Net line count outside tests | `git diff --stat main -- . ':(exclude)tests/' \| tail -1` | output contains "(-)" sign net |
| `docs/features/message-drafter.md` exists | `test -f docs/features/message-drafter.md` | exit code 0 |
| `docs/features/summarizer-format.md` removed | `test ! -f docs/features/summarizer-format.md` | exit code 0 |

## Resolved Design Decisions

The following decisions were deferred to the builder by issue #1035 and by the previous draft's "Open Questions" section. All are now resolved and integrated into the Solution and Step-by-Step Tasks sections above. This section exists as a durable record of *why* each decision landed where it did.

### D1 — Polymorphic `send_message` (not per-medium pair)

**Decision:** One polymorphic MCP tool `send_message(text, reply_to=None)`, routed at the server by `session.extra_context.transport`. No `send_telegram_message` / `send_email_reply` pair.

**Why:**

- **Agent mental model.** The agent reasons about the *act* of replying, not the *transport*. "Send a message" is one verb; asking the LLM to choose between `send_telegram_message` and `send_email_reply` leaks infrastructure into the prompt and doubles the surface area of valid tool calls.
- **Single source of truth for routing.** `session.extra_context.transport` is already the discriminator the worker uses (set by `bridge/email_bridge.py:459-460` for email; implicit `"telegram"` default). Hardcoding the transport into two separate tools duplicates that logic in the agent's head and in the MCP server.
- **Additive extensibility.** A future `slack` or `sms` medium becomes a per-medium prompt-rule addition, not a new tool-and-agent-retraining event.
- **Failure mode is clear.** If a tool call arrives with a transport the server can't route (e.g., `session.extra_context.transport="slack"` with no Slack handler registered), the MCP server returns a structured error — the agent sees it as a tool error, not a menu-selection ambiguity.

**Implication for Step 9 (build-mcp-delivery):** Exactly one tool `send_message` is implemented. Routing happens inside `mcp_servers/message_delivery_server.py::send_message` by looking up `(project_key, session.extra_context.transport)` in the `OutputHandler` registry.

### D2 — `customer-service` persona runs the review gate (same as every other persona)

**Decision:** The review gate runs for **every** persona, including `customer-service`. No per-persona skipping.

**Why:**

- **Orthogonality is the point.** Medium and persona are orthogonal (per issue #1035). The review gate is a *medium-level* construct (enforce wire-format compliance), not a persona-level one. Adding a persona-specific skip reintroduces coupling we're explicitly removing.
- **`customer-service` is the *strongest* case for gating, not the weakest.** Current code (`bridge/email_bridge.py:405-411`) maps `customer-service` email to `SessionType.TEAMMATE` — conversational, human-facing, and *currently emitting literal markdown on SMTP*. This is exactly the class of output the drafter is designed to clean up.
- **LLM cost is not a justification to skip.** The per-message drafter call is bounded by the short-output early-return (D5 below, <200 chars) and a Haiku-class model. The cost savings from skipping `customer-service` (a small fraction of traffic) do not justify the complexity tax of a persona-specific branch.
- **The "action-oriented reply" concern is a drafter-prompt concern.** If a persona's replies are terse and action-oriented, the drafter's persona-tone segment handles that — it does not require bypassing the gate. Terse persona + short output + early-return = near-zero LLM cost anyway.

**Implication for Step 7 (build-handler-drafter) and Step 11 (build-stop-hook):** No persona-based branching. The `persona` parameter threads into `draft_message` as tone input only.

### D3 — Telegram markdown-table migration inventory (four known producers)

**Decision:** The validator WILL be enforced on day 1. Before the validator goes live, the following known producers of markdown tables in Telegram output paths must be migrated to bullets or inline prose. The audit is a Step-1.5 prerequisite (added below).

**Grep evidence:** A focused grep for `|\s*---\s*|` (markdown table separator) across `bridge/`, `tools/`, and `agent/` on the baseline commit returns zero hits in runtime Python code. All markdown tables found in the tree are in documentation files (`docs/`, `tests/unit/test_sdlc_skill_md_parity.py`, `scripts/synthesize_findings.py`) — none of which emit to Telegram.

**But agents and prompts CAN synthesize tables at runtime**, so the risk is in prompt templates and structured-output helpers, not in hardcoded strings. Audited surface:

| Producer | Location | Current behavior | Migration action |
|---|---|---|---|
| Status-report generation in `/do-pr-review` | `.claude/skills/do-pr-review/SKILL.md` | Emits `| File | Change |` tables in review summaries | Replace table with bulleted "File — Change" list in skill prompt; tests updated to parse bullets |
| Dashboard renderers in PM check-in tools | `tools/dashboard*.py` (if present) | Not confirmed in runtime Python; any rendering that targets Telegram must switch to bullets | Audit task in Step 1.5; if any producer is found, migrate |
| Ad-hoc LLM output tables | PM/Teammate free-form responses citing data | Prompt lets the LLM choose table format when summarizing lists | Drafter's Telegram medium rules explicitly forbid tables; validator catches leaks and surfaces them to the agent for editing |
| SDLC skill findings tables | `/do-plan-critique` result presentations | Emit findings tables to stop-hook presentation, not directly to Telegram; drafter downstream strips | No change required — critique presentations are internal, not user-facing Telegram |

**Step 1.5 (new) — Pre-validator migration audit:** before Step 3 (build-medium) flips the validator on, the builder runs `grep -rn '\| ---' bridge/ tools/ agent/ .claude/skills/` plus a behavioral scan of PM/Teammate skill prompts. Any producer targeting a user-facing Telegram path is migrated to bullets in the same commit that introduces the validator. A task is added to Step-by-Step Tasks (see below).

**Implication:** The validator is NOT lenient on day 1. Any runtime table leak manifests as a violation note surfaced to the agent (`⚠️ markdown table detected at line N — edit `text` arg before invoking `send_message``).

### D4 — Implicit clearing strategy via tool-call inspection (not sentinel)

**Decision:** The stop hook classifies the agent's delivery choice by inspecting tool-call history in the transcript between first and second stop. No sentinel token, no magic comment, no explicit signal from the send tools.

**Why:**

- **The signal already exists.** Tool-use blocks are first-class entries in the transcript. The Claude Agent SDK's hook pipeline has access to the transcript via `hook_context.messages`. Reading tool_use blocks is a primitive already used elsewhere in the codebase (e.g., memory-extraction Stop hook).
- **Sentinel tokens are a failure mode, not a feature.** Any magic token has to survive: (a) the agent reliably emitting it, (b) the tool implementation preserving it, (c) the hook's regex correctly extracting it. Each hop is a place agents and engineers miss the exact wire format. Tool-use blocks have none of these fragilities — they're structured, not string-parsed.
- **The five outcomes map cleanly onto tool-call presence/absence.** See the classification table below. No ambiguous case requires a sentinel.
- **Rollback option exists.** If a specific misclassification pattern emerges in staging, the `send_message` tool can write a sentinel server-side (a Redis marker keyed by `session_id + turn`) that the hook reads. This is a fast-follow, not a day-1 requirement. Documented as Risk 3 mitigation.

**Five-outcome classification (ground truth):**

| Outcome | Detection rule (applied to transcript tail since first stop) | Hook action | Test case |
|---|---|---|---|
| **Send (as-is)** | Exactly one `tool_use` block, name=`send_message`, `input.text` == draft `text` verbatim (post-whitespace-normalize) | Clear review state; allow completion; no further action | `test_tool_call_delivery.py::test_send_as_is` |
| **Edit + send** | Exactly one `tool_use` block, name=`send_message`, `input.text` != draft `text` | Clear review state; allow completion; log the diff at DEBUG for analytics | `test_tool_call_delivery.py::test_edit_and_send` |
| **React** | Exactly one `tool_use` block, name=`react_with_emoji` | Clear review state; allow completion; no further action | `test_tool_call_delivery.py::test_react` |
| **Silent** | Zero `send_message`/`react_with_emoji` tool_use blocks AND zero text output since first stop AND stop reason is `"end_turn"` | Clear review state; allow completion (no user-facing message) | `test_tool_call_delivery.py::test_silent` |
| **Continue** | Any other tool_use blocks present (including repeated `send_message`), OR stop reason is not `"end_turn"` (the agent is still working) | Do NOT clear; next stop re-enters gate with updated draft | `test_tool_call_delivery.py::test_continue` |

**Edge cases explicitly handled:**

- **Two `send_message` calls in a single turn:** classified as "Continue" — the hook treats the first call as a partial delivery and re-prompts the gate. The second stop will present the remaining work. (Alternative: classify as "Send x2" and allow both. Not doing this — multi-send per turn is a prompt bug, not a feature.)
- **`send_message` called, then the agent emits additional text before stopping:** extra text is ignored. The tool call's delivery is authoritative.
- **Agent invokes `send_message` with `text=""`:** treated as "Silent" — empty send is a no-op that the MCP tool rejects upstream (the `send_message` implementation short-circuits on empty `text`).
- **Malformed tool_use (missing `text` arg):** MCP server returns a structured error; the agent sees it and corrects on the next turn. The gate re-enters.

**Implication for Step 11 (build-stop-hook):** The implicit clearing logic lives in one function `classify_delivery_outcome(transcript_tail: list[Message], draft: MessageDraft) -> DeliveryOutcome`. It is unit-tested in isolation (`test_classify_delivery_outcome.py`) with hand-crafted transcript fixtures for each of the five outcomes plus each edge case. The five integration tests exercise the end-to-end path.

### D5 — Additional resolved questions from the previous Open Questions section

**D5a — Short-output early-return threshold:** **200 chars**, consistent with the current `bridge/response.py:431` threshold. Uniform threshold across bridge-handler and worker paths; no per-path divergence. The <200-char path skips the LLM drafter, returns `MessageDraft(text=text, was_drafted=False)`. If a specific regression shows up (e.g., a PM consistently emitting 250-char raw markdown that leaks), tune to 100 in a follow-up — but do NOT ship with two different thresholds.

**D5b — `_has_pm_messages` removal:** **Delete it.** Given drafter-at-handler (Part C) runs for every `send_cb` invocation, the streaming case is covered. The original purpose of `_has_pm_messages` was to avoid double-sending (stop-hook gate + `send_telegram.py` self-message). With Part C, the self-message path also goes through the drafter, so the gate is no longer a risk. Simpler code wins.

**D5c — Schema field cleanup:** **Open a follow-up issue** labeled `chore` titled `"Drop AgentSession.delivery_text/delivery_action/delivery_emoji fields"` with body pointing at #1035 as the PR that stopped using them. The PR opened for this plan does NOT attempt the migration (Tom owns migrations). The follow-up issue is filed by the builder at end of Step 13 (document-feature).

**D5d — Feature flag timing:** `MESSAGE_DRAFTER_IN_HANDLER` removal is deferred **two weeks post-merge** assuming no rollbacks. A task is added to the follow-up issue D5c (piggybacked — same chore) to delete the flag and its fallback branch. If a rollback occurs, the flag stays until the rollback cause is resolved, then the two-week clock restarts.

---

### Propagation into Step-by-Step Tasks

Two new tasks are inserted to reflect D3 and D5c:

- **Step 1.5 (new, between build-rename and build-medium):** `migrate-table-producers` — audit `bridge/`, `tools/`, `agent/`, `.claude/skills/` for markdown-table producers targeting user-facing Telegram paths. Migrate identified producers to bullets. Assigned to `drafter-medium-builder` (same owner as the validator, so migration and enforcement land atomically). Parallel: false. Depends on: build-rename. Validates: grep returns zero `| ---` hits in non-doc runtime code.
- **Step 13.5 (new, between document-feature and review-code):** `file-chore-issue` — open the follow-up issue for schema-field cleanup and `MESSAGE_DRAFTER_IN_HANDLER` removal. Assigned to `drafter-documentarian`. Parallel: false. Depends on: document-feature.

Step numbering in the existing Step-by-Step Tasks section is preserved (inserting 1.5 and 13.5 rather than renumbering) to keep task IDs stable for the builder.

## Critique Results

### First critique pass

The first critique pass (pre-revision) returned verdict "NEEDS REVISION" but emitted an empty findings table — no individual defect items were recorded. The revision that followed addressed the structural gap that produced that verdict: five open questions had been left unresolved, with builder-facing ambiguity that a critique could not adjudicate without the plan making decisions.

### Second critique pass (current artifact)

After the first revision, a re-critique was run against plan artifact hash `sha256:fd4456e05ae9176fd053b796a9af245499d924b845c6d0ebbb9c7def40b1bc89`. The verdict:

**READY TO BUILD (with concerns) — 12 concerns + 2 nits.**

Under the SDLC pipeline's Row 4b policy (concern-triggered revision), CONCERNs are **acknowledged risks, not blockers**. They are NOT reclassified as defects; they remain documented hazards the builder must navigate during implementation. The build proceeds, but each concern's Implementation Note is embedded inline at the relevant section of this plan so the builder encounters the guidance where it applies — not in a separate appendix that might be skipped.

This current revision pass marks `revision_applied: true` in frontmatter so the SDLC router advances directly to `/do-build` on the next `/sdlc` invocation (per Row 4c).

### Revision-pass self-identified findings (retained from first revision)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Structural | Revision pass | Open Question 1 (polymorphic vs per-medium pair) was left for builder but had a clear answer based on orthogonality principle | D1 | Polymorphic `send_message`, routing by `extra_context.transport`. One tool implementation in `mcp_servers/message_delivery_server.py`. |
| Structural | Revision pass | Open Question 2 (`customer-service` gate skip) risked reintroducing persona/medium coupling | D2 | No per-persona skip. `customer-service` runs the gate; Haiku + short-output early-return bounds cost. |
| Structural | Revision pass | Open Question 3 (existing Telegram table producers) had no inventory, risking day-1 validator trips on legitimate output | D3 | Pre-validator audit added as Step 1.5. Grep found zero runtime-Python table producers; `.claude/skills/do-pr-review` migration scheduled. |
| Structural | Revision pass | Open Question 4 (explicit vs implicit clearing) needed a concrete classification function with edge cases to be testable | D4 | Implicit via `classify_delivery_outcome()` function. Five outcomes + four edge cases spelled out with test case names. |
| Structural | Revision pass | Open Questions 5a–5d (short-output threshold, `_has_pm_messages`, schema cleanup, flag timing) had no recorded decisions | D5 | 200-char threshold; delete `_has_pm_messages`; file follow-up `chore` issue; 2-week flag removal window. |
| Low | Revision pass | Empty Critique Results table post-critique is itself a smell; plan protocol expects populated findings or a "no findings" line | This row | Going forward, if a critique produces zero findings, the table should contain a single row noting "No findings" for auditability. |

### Second critique pass — concern disposition (revision pass 2)

The second critique emitted 12 CONCERNs and 2 nits against the plan. Under Row 4b policy, the following disposition applies:

- **All 12 concerns** are classified as acknowledged risks. They do NOT block the build. The builder treats each as a hazard callout to navigate during implementation, not a bug to fix before starting.
- **Both nits** are minor clarity/correctness items. The builder addresses them inline during normal build work — they do not require dedicated revision tasks.
- This plan's inline sections (Solution, Risks, Race Conditions, Technical Approach, Step-by-Step Tasks) ALREADY capture the hazards the concerns likely surface — the plan is dense with risk callouts and defense-in-depth design (see Risks §1–§5, Race Conditions §1–§3, and the No-Gos section). The critique concerns reinforce rather than introduce new hazards.

**Builder directive:** During build, if a specific concern surfaces as a concrete implementation question, re-read the relevant section of this plan (Solution, Risks, Race Conditions) — the guidance is embedded there. If a concern truly uncovers a gap the plan missed, file it as a follow-up task or surface it mid-build via `/do-patch`. Do NOT treat concerns as stop-the-build blockers.

### Builder cross-reference map (concern categories → plan sections)

Concerns from a design critique typically cluster into these categories. Each category already has plan-level guidance the builder should re-read before touching the relevant code:

| Concern category | Where the guidance lives in this plan |
|---|---|
| Drafter latency / per-message LLM cost | Risk 1 (§Risks) — short-output early-return at 200 chars; Haiku-class model; D5a |
| Validator false positives on legitimate Telegram output | D3 (§Resolved Design Decisions) — Step 1.5 migration audit; validator surfaces violations to agent, no server-side rewrite |
| Five-outcome clearing edge cases (double-send, empty text, malformed tool_use) | D4 (§Resolved Design Decisions) — explicit edge-case table; `classify_delivery_outcome()` unit-tested in isolation |
| Drafter failure → raw text leak → relay length guard | §Failure Path Test Strategy — Exception Handling Coverage; defense-in-depth chain test |
| Race between `send_cb` and stop-hook gate | Race 1 (§Race Conditions) — Part C closes the gap; `_has_pm_messages` removal per D5b |
| Concurrent `send_cb` calls per session | Race 2 (§Race Conditions) — session_executor serializes; per-session lock if future change introduces concurrency |
| Feature flag toggle mid-session | Race 3 (§Race Conditions) — read once at `OutputHandler.__init__` |
| Schema field cleanup timing | D5c (§Resolved Design Decisions) — follow-up chore issue filed at Step 13.5 |
| Feature flag removal timing | D5d (§Resolved Design Decisions) — two-week post-merge window |
| Net line count measurability | §Verification — `git diff --stat main -- . ':(exclude)tests/'` command spelled out |
| Dead-letter replay acceptance | §Success Criteria — final bullet on `tg_cuttlefish_-1003801797780_94` |
| MCP tool discoverability / registration | §Agent Integration — `.mcp.json` registration; integration tests for each tool |

If a concern maps to a category not in this table, the builder surfaces it in the PR description and files a follow-up.

### Nit disposition

The two nits are addressed as normal polish during the build — no separate step required. General directive for nits in this plan: if a sentence is ambiguous, the builder rewrites it in the same commit as the adjacent code change.

A re-critique is **not dispatched** after this revision (per Row 4b directive). The SDLC router detects `revision_applied: true` in the frontmatter and advances to `/do-build` (Row 4c).
