# Message Drafter

Medium-aware drafting layer that owns wire-format compliance for every user-visible message leaving the agent. Replaces the Telegram-only `summarizer`. Tracked by [issue #1035](https://github.com/tomcounsell/ai/issues/1035).

## Why it exists

Historically two parallel delivery paths existed:

1. **Bridge-handler flow** — human Telegram message → bridge → `send_response_with_files` → summarizer → Telethon. This path called the summarizer and handled long-output file attachment.
2. **Worker `send_cb` flow** — worker-executed PM sessions → `TelegramRelayOutputHandler.send` → raw text straight to Redis outbox → relay → Telethon.

Only the first path ran the drafter. The second wrote raw text to the outbox and fell over on any content >4096 characters (Telethon `MessageTooLongError`, dead-letter, user saw nothing). Email sessions shipped literal markdown as `text/plain` MIME because the summarizer was Telegram-only.

**Baseline incident**: session `tg_cuttlefish_-1003801797780_94` (2026-04-20), 4,582-char PM response dead-lettered.

The drafter consolidates both paths. The rename (`summarizer` → `message_drafter`) signals the expanded scope: medium-aware + tool-call delivery + per-medium validators.

## Architecture

```
[agent output] ──> OutputHandler.send() ──> draft_message(medium, persona)
                                                 │
                                   ┌─────────────┴─────────────┐
                                   │                           │
                                   ▼                           ▼
                         TelegramRelay outbox            Email SMTP
                         (text + file_paths?)          (plain prose body)
                                   │
                                   ▼
                         telegram_relay length guard
                         (>4096 → .txt attachment)
                                   │
                                   ▼
                              Telethon send
```

Every user-visible message passes through `bridge/message_drafter.py::draft_message` before it leaves the worker. The relay has a belt-and-suspenders length guard that converts any oversize text to a `.txt` attachment. Splitting messages is **banned** (see No-Gos).

## API

### `draft_message(raw_response, session=None, *, medium="telegram", persona=None) -> MessageDraft`

The sole public entry point. Everything else is an implementation detail.

**Arguments:**

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `raw_response` | `str` | — | The raw agent output text. |
| `session` | `AgentSession \| None` | `None` | Enriches the draft with SDLC stage progress, persona/mode context, and linkifies PR/issue numbers. |
| `medium` | `str` | `"telegram"` | Discriminator for per-medium rules. Today this is wired through for routing; per-medium prompt/validator logic is staged in follow-up work. |
| `persona` | `str \| None` | `None` | Optional tone hint. Orthogonal to medium. Not used today. |

**Returns `MessageDraft`:**

```python
@dataclass
class MessageDraft:
    text: str                                 # body to deliver
    full_output_file: Path | None = None      # .txt attachment for long raw output
    was_drafted: bool = False                 # True if LLM shaped the text
    needs_self_draft: bool = False            # True when all LLM backends failed
    artifacts: dict[str, list[str]] = {}      # commit hashes, URLs, PRs
    context_summary: str | None = None        # one-sentence session topic
    expectations: str | None = None           # what the agent needs from the human
```

### Short-output early return (D5a)

Texts under `SHORT_OUTPUT_THRESHOLD = 200` skip the LLM drafter and return verbatim when all of these hold:

- No SDLC session (SDLC always needs stage progress + link footer).
- No extracted artifacts (commit hashes, URLs, PRs deserve drafter polish).
- No `?` in the text (questions go through expectation handling).
- No fenced code block.

The goal is to bound per-message latency on brief replies. See Risk 1 in `docs/plans/message-drafter.md`.

## Drafter-at-the-handler (the critical fix)

`agent/output_handler.py::TelegramRelayOutputHandler.send` reads the `MESSAGE_DRAFTER_IN_HANDLER` env var once at `__init__` (default `true`; accepts `0`/`false`/`no`/`off` to disable). When enabled:

1. Before writing to Redis, the handler calls `await draft_message(text, session=session, medium="telegram")`.
2. If the draft has `full_output_file`, the outbox payload grows a `file_paths=[…]` entry — the relay already handles file sends.
3. If the drafter raises, the handler falls back to the raw text. The relay length guard is the final safety net.

`bridge/email_bridge.py::EmailOutputHandler.send` has the mirror integration with `medium="email"`. Email retains no-op `react()` semantics.

`FileOutputHandler` remains a pass-through debug sink — no drafter.

**Why handle the drafter here instead of in the bridge?** The bridge-handler flow and the worker `send_cb` flow both eventually arrive at `OutputHandler.send`. Wiring the drafter at that boundary is the smallest change that closes both paths. See `docs/plans/message-drafter.md` §Part C.

## Relay length guard

`bridge/telegram_relay.py::_send_queued_message` enforces one invariant: if a text payload >4096 chars reaches the relay, it is **converted to a `.txt` attachment** (never split). The guard:

1. Logs ERROR with `session_id`, `chat_id`, `len`, `preview=text[:200]`.
2. Writes the raw text to `/tmp/relay_overlong_{session_id}_{ts}.txt`.
3. Sends via `telegram_client.send_file(caption="[auto-attached: response exceeded 4096 chars]")`.
4. On conversion failure (disk full, etc.): falls through to normal text send; Telethon raises `MessageTooLongError`; existing retry + dead-letter kicks in.

This is **defense-in-depth**. The primary fix is the drafter-at-the-handler wiring above. The length guard catches residual bugs — anything that ever bypasses the drafter manifests as a loud ERROR log instead of a silent failure.

## No-Gos

- **No message splitting. Ever.** Any PR that splits messages at newlines, sentence boundaries, or character counts is rejected — even as a "safety net." See baseline commit `1678068b` which reverted an earlier splitting attempt.
- **No email HTML / multipart bodies.** Plain prose only.
- **No persona-specific drafter skips.** Medium and persona stay orthogonal.
- **No retry loops on drafter failure.** One attempt, one fallback path.
- **No Telegraph (telegra.ph) integration.** `.txt` attachment is the long-form delivery mechanism.

## Feature flag: `MESSAGE_DRAFTER_IN_HANDLER`

Default: `true`. Read once at handler `__init__` (not per-send) per Race 3 in the plan.

**Rollback**: set `MESSAGE_DRAFTER_IN_HANDLER=false` in `~/Desktop/Valor/.env`, restart bridge + worker (`./scripts/valor-service.sh restart`). Output handlers revert to the raw-text pass-through behavior. The relay length guard still applies.

The flag is a temporary safety net. It will be removed two weeks post-merge if no rollbacks occur (tracked in the file-chore follow-up issue).

## Format rules by medium

### Telegram

- `Markdown V2` rendering via Telethon (existing `bridge/markdown.py::send_markdown`).
- **No tables.** Markdown table syntax (`| --- |`) does not render in Telegram; the drafter and downstream validators treat it as a wire-format violation.
- `FILE_ATTACH_THRESHOLD` (default 3000 chars): full raw output also written to a `.txt` attachment alongside the short drafted message.
- Emoji prefix conveys SDLC status (`✅` completion, `⏳` in progress, `❌` failed, `⚠️` blocked, empty = routine).

### Email

- Plain-prose only. No markdown on the wire.
- Threading via `In-Reply-To` + `References` headers from `extra_context.email_message_id`.
- Reactions are no-ops (`EmailOutputHandler.react` returns early).

## SDLC stage progress

SDLC sessions get a structured header with stage names and a live `▶` marker. Rendering happens in Python (`_compose_structured_draft`), not the LLM. Sample:

```
⏳
ISSUE 1035 → PLAN → ▶ BUILD → TEST → REVIEW → DOCS
• Renamed summarizer → message_drafter
• Wired drafter into OutputHandler.send
• Added relay length guard
Issue #1035
```

Non-SDLC chats get a simpler emoji + bullets layout. Teammate persona bypasses all structure and returns the prose verbatim.

## Five-outcome delivery (planned)

The stop-hook review gate currently uses a `SEND/EDIT:/REACT:/SILENT/CONTINUE` string menu that regex-parses the agent's response. This will be replaced by **prepopulated `send_message` tool calls** — the agent invokes, edits-and-invokes, swaps for `react_with_emoji`, or stops silent. Clearing is implicit via transcript inspection (five outcomes: send, edit+send, react, silent, continue).

This work is staged in follow-up tasks (9 and 11 in the plan).

## Delivery Tool Surface

The agent delivers user-visible messages and reactions via two CLI tools invoked through the `Bash` tool, not through a dedicated MCP server:

- `tools/send_message.py '<text>'` — primary delivery tool. Writes the payload to the same Redis outbox consumed by `bridge/telegram_relay.py`. Handles `--reply-to <msg_id>` and `--file <path>` flags for threaded replies and attachments.
- `tools/react_with_emoji.py '<emoji>'` — posts a reaction emoji on the triggering message. Used for lightweight acknowledgements ("thumbs up, done") when a full text response would be noise.

The stop hook classifies each turn's outcome by scanning `tool_use` blocks for these exact script paths (`agent/hooks/stop.py::classify_delivery_outcome`). Matches produce one of the five outcomes (send, edit+send, react, silent, continue).

**Why CLI tools over a bespoke MCP server:** shipping a `mcp_servers/message_delivery_server.py` would require a root `.mcp.json` registration and add 300–500 lines of infrastructure for a surface that already works. The CLI tools route through the same outbox + relay as every other delivery path, the stop hook already recognizes them, and they are transparent to `gh pr comment` or any other bridge path that bypasses the drafter. Transcript readability (tool calls appearing as `Bash` invocations rather than semantic `send_message` tool_use blocks) is the only real trade-off, and the stop hook compensates by attaching semantic classification after the fact.

**Reversibility:** the CLI-tool surface can be wrapped in an MCP server in a future chore if transcript readability becomes a pain point. The stop-hook classification logic would gain a pattern match on the new tool name and keep the existing Bash-pattern match as a fallback for legacy turns.

Recorded as **Resolved Decision RD-1** in `docs/plans/message-drafter-followup.md` (2026-04-20 follow-up).

## Files

- `bridge/message_drafter.py` — the drafter module (replaces `bridge/summarizer.py`). Includes `_truncate_at_sentence_boundary` since the #1074 follow-up.
- `agent/output_handler.py::TelegramRelayOutputHandler` — canonical delivery entry point. Drafter runs here; payload is written to the Redis outbox. Used by both the worker `send_cb` and (since the #1074 follow-up) the bridge's handler-event send callback.
- `bridge/email_bridge.py::EmailOutputHandler` — drafter-in-handler wiring for email.
- `bridge/telegram_relay.py::_send_queued_message` — belt-and-suspenders length guard.
- `bridge/response.py` — slim reactions + helpers module. Contains `set_reaction`, `VALIDATED_REACTIONS`, `filter_tool_logs`, `extract_files_from_response`, `clean_message`. The pre-#1074 `send_response_with_files` delivery function was removed in the follow-up (see `docs/plans/message-drafter-followup.md` Part C).
- `agent/hooks/stop.py` — stop-hook review gate that drafts the final reply and classifies delivery outcomes by matching `tool_use` blocks for the CLI delivery tools.
- `tools/send_message.py`, `tools/react_with_emoji.py` — the agent-facing CLI delivery surface (see "Delivery Tool Surface" above).

## Tests

- `tests/unit/test_message_drafter.py` — drafter classification, artifact extraction, prompt building, per-medium assertions.
- `tests/unit/test_medium_validators.py` — `validate_telegram`, `validate_email`, `_validate_for_medium`, `format_violations` unit coverage (added in the #1074 follow-up).
- `tests/unit/test_output_handler.py::TestDrafterInHandler` — drafter-at-the-handler wiring: flag read at init, drafter invoked when enabled, bypassed when disabled, file_paths propagated, exception fallback.
- `tests/unit/test_relay_length_guard.py` — 4096-char pass-through, 4097-char `.txt` conversion, no splitting, conversion-failure fallback.
- `tests/unit/test_tool_call_delivery.py` — stop-hook classification for send / react / silent / edit+send / continue outcomes via `tool_use` pattern match on the CLI delivery tools.
- `tests/integration/test_message_drafter_integration.py` — real Haiku/OpenRouter round-trips.
- `tests/integration/test_reply_delivery.py` — end-to-end reaction paths (PM self-message bypass, completion emoji, error emoji).
