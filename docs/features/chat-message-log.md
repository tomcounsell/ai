# Chat Message Log

## Overview

`AgentSession.chat_message_log` is a bounded, session-scoped rolling log of inbound and outbound chat traffic. It gives the message drafter (`bridge/message_drafter.py`) a first-class view of what has been said in the current session, so it can avoid duplicating messages it (or the agent) already sent.

**Problem it solves:** The drafter previously had no visibility into its own prior outbound messages. This caused observable duplication: the same content posted twice in rapid succession, long-form summaries followed immediately by paraphrases of themselves. Issue #1192.

## Data Model

### Field: `AgentSession.chat_message_log`

```python
# models/agent_session.py
CHAT_LOG_MAX_ENTRIES = 50    # entries stored per session
CHAT_LOG_DISPLAY_ENTRIES = 20  # entries included in drafter prompt

chat_message_log = ListField(default=list)
```

Each entry is a dict with this shape:

| Key | Type | Description |
|-----|------|-------------|
| `direction` | `"in"` or `"out"` | Inbound (from user) or outbound (sent by Valor) |
| `sender` | str | Display name, e.g. `"Tom"`, `"valor"`, `"unknown"` |
| `content` | str | Message text (stripped) |
| `message_id` | int or None | Telegram message ID, if available |
| `ts` | float | Unix timestamp |

The log is bounded to `CHAT_LOG_MAX_ENTRIES` (50). The drafter reads only the last `CHAT_LOG_DISPLAY_ENTRIES` (20) entries to keep prompt size manageable.

### Method: `AgentSession.append_chat_log`

```python
session.append_chat_log(direction, sender, content, message_id=None, ts=None)
```

- Re-fetches the freshest session record from Redis before appending to narrow the concurrent-write race window (see Race Conditions below).
- Silently skips empty or whitespace-only content.
- Substitutes `"unknown"` for `None` or empty sender.
- Wraps the entire operation in `try/except` — a save failure never crashes the caller. The chat log is enrichment, not a critical path.

## Write Paths

### Path A: TelegramRelayOutputHandler → relay (natural session output)

**Location:** `bridge/telegram_relay.py::_append_outbound_chat_log`

After every successful Telegram send, the relay appends an outbound entry to the owning session's `chat_message_log`.

The function runs synchronously in a thread (`asyncio.to_thread`) so it cannot `await`. Session resolution uses a **three-tier** lookup:

1. **`owner_agent_session_id`** in the relay payload — set by Path B (see below).
2. **`session_id`** in the payload, if it doesn't start with `cli-` or `local-` — real bridge session ID (Path A).
3. **`chat_id` + `status="running"`** direct query — covers manual CLI sends outside any agent session.

If no session is resolved, the append is skipped silently (logged at DEBUG). The relay must never crash on chat-log bookkeeping.

**Timing invariant:** The outbound entry is appended _after_ the successful send. The drafter never sees the current turn's text in the log (it produces that text). It sees prior turns and any Path B sends from earlier in this session. This is the correct and desired ordering.

### Path B: `valor-telegram send` from inside an agent session

**Location:** `tools/valor_telegram.py::cmd_send`

When an agent invokes `valor-telegram send` via Bash inside a session, the agent environment contains `AGENT_SESSION_ID` (or `VALOR_SESSION_ID` as fallback), injected by `agent/sdk_client.py`. The CLI reads this env var and adds `owner_agent_session_id` to the relay payload:

```python
_agent_session_id = os.environ.get("AGENT_SESSION_ID") or os.environ.get("VALOR_SESSION_ID")
if _agent_session_id:
    payload["owner_agent_session_id"] = _agent_session_id
```

The relay then uses this key as Tier 1 of its three-tier resolution, ensuring the outbound entry lands on the correct `AgentSession.chat_message_log`.

When neither env var is set (manual CLI invocation outside any agent), the key is omitted and the relay falls back to Tier 3 (chat_id lookup).

### Inbound: Telegram messages routed to a session

**Location:** `bridge/dispatch.py::_append_inbound_chat_log`, called from `dispatch_telegram_session`

Every Telegram message dispatched to a session via `dispatch_telegram_session` records an inbound entry after the session is enqueued. This is the single chokepoint for all Telegram-originating session enqueues, so the inbound log stays consistent with the actual sessions created.

> **Dispatch gap:** Follow-up steering messages and interjection messages to already-running sessions (dispatched via `bridge/dispatch.py` steering path) are not captured in the chat log — only new-session dispatch is logged.

## Read Path 1: Message Drafter

> **Updated (drafter_passthrough_validation):** The `_build_draft_prompt` function was removed when the drafter was repositioned from an LLM rewriter to a pass-through validation filter. The drafter no longer builds an LLM prompt and no longer reads `chat_message_log` directly. This section is retained for historical context; Read Path 2 (PM Completion Runner) is the active consumer of `chat_message_log`.
>
> Context about prior outbound messages is now passed to the agent via the harness system prompt (Pass 1 prompt injection in `agent/session_completion.py`) for PM final-delivery turns, not via a drafter prompt block.

## Read Path 2: PM Completion Runner (issue #1262)

**Location:** `agent/session_completion.py::_build_completion_baseline` (Pass 1 prompt block + post-draft suppression baseline)

The PM final-delivery runner reads outbound `chat_message_log` entries to detect when its drafted summary would restate a message the agent already sent in-session via `valor-telegram send` (Path B). The adapter (`_build_completion_baseline`) maps `chat_message_log` outbound entries (`{direction, sender, content, message_id, ts}`) to the `should_suppress` baseline shape (`{ts, text, artifacts}`), filters `direction == "out"`, and drops entries older than `REDUNDANCY_WINDOW_SECONDS`.

The runner uses the chat log in two distinct ways per pipeline-complete:

1. **Pass 1 prompt injection** — appends an `[out]` block of recent outbound entries to the harness prompt so the drafter "only adds materially-new context".
2. **Post-draft suppression** — calls `bridge/redundancy_filter.should_suppress(threshold=0.55, ...)` with the same adapter baseline, then enforces a HIGH cutoff (`DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD`, default `0.75`) in caller; the borderline band escalates to a Haiku judge.

To bound the read-after-write race against Path B publishes, the runner first calls `_await_outbox_drained(parent, timeout_seconds=2.0)` and re-fetches the parent from Popoto before reading. See [PM Final Delivery: mid-session-send-aware completion suppression](pm-final-delivery.md#mid-session-send-aware-completion-suppression) for the full contract, and [Drafter Redundancy Suppression](drafter-redundancy-suppression.md#second-call-site-pm-completion-runner-issue-1262) for the Path A vs. completion-runner contract differences.

## Bound and Trimming

- **Storage bound:** 50 entries (CHAT_LOG_MAX_ENTRIES). Older entries are trimmed on every `append_chat_log` call.
- **Display cap:** 20 entries in the drafter prompt (CHAT_LOG_DISPLAY_ENTRIES). The full stored log is available for future read patterns.
- **Entry size:** ~200 bytes/entry at realistic content lengths. 50 × 200 ≈ 10 KB upper bound per session — well within Redis hash comfort.

## Race Conditions

### Concurrent inbound + outbound writes

Inbound writes happen in the bridge process (sync within the bridge handler). Outbound writes happen in the relay coroutine (async). Both write to the same Redis hash via Popoto's `save()`.

**Mitigation:** `append_chat_log` re-fetches the freshest version from Redis before appending:

```python
rows = list(AgentSession.query.filter(session_id=self.session_id))
fresh = rows[0] if rows else self
log = list(fresh.chat_message_log or [])
log.append(entry)
log = log[-CHAT_LOG_MAX_ENTRIES:]
fresh.chat_message_log = log
fresh.save()
```

This narrows the race window to the read-modify-write critical section but does not eliminate it under perfect contention. Given inbound frequency (~1 per user message) and relay outbound tempo (~1 msg/sec ceiling), contention is rare in practice. Accepted as residual risk for v1.

### Drafter reads before current turn's outbound is flushed

By design: the drafter produces the current turn's outbound text. The relay appends _after_ send. The drafter never sees the current turn's text in the log — it sees prior turns and prior Path B sends. No race; correct by construction.

## Configuration

| Constant | Value | File |
|----------|-------|------|
| `CHAT_LOG_MAX_ENTRIES` | 50 | `models/agent_session.py` |
| `CHAT_LOG_DISPLAY_ENTRIES` | 20 | `models/agent_session.py` |

Both constants are importable from `models.agent_session` for use in tests.

## No-Gos (Out of Scope for This Feature)

- Cross-session chat awareness — each session sees only its own scope.
- Hydrating the log from Telegram history on session resume — fresh start is the default.
- Email-medium chat_message_log support — Telegram-only for now; email follows the same pattern in a follow-up.
- Compressing or summarizing log entries — raw text only.
- Merging with `session_events` (lifecycle) — they stay separate.

## Related Issues and PRs

- **#1192** — This feature.
- **#1191** — `--reply-to` default for `valor-telegram send` (same Path B code path).
- **#1035** — Message drafter consolidation (substrate this work plugs into).
- **#318** — Unthreaded message routing into active sessions (confirmed `chat_id → AgentSession` mapping path).
