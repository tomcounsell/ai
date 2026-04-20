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

## Files

- `bridge/message_drafter.py` — the drafter module (replaces `bridge/summarizer.py`).
- `agent/output_handler.py::TelegramRelayOutputHandler` — drafter-in-handler wiring for Telegram.
- `bridge/email_bridge.py::EmailOutputHandler` — drafter-in-handler wiring for email.
- `bridge/telegram_relay.py::_send_queued_message` — belt-and-suspenders length guard.
- `bridge/response.py` — consumes `MessageDraft` for handler-flow sends.
- `agent/hooks/stop.py` — stop-hook review gate that drafts the final reply.

## Tests

- `tests/unit/test_message_drafter.py` — drafter classification, artifact extraction, prompt building, per-medium assertions.
- `tests/unit/test_output_handler.py::TestDrafterInHandler` — drafter-at-the-handler wiring: flag read at init, drafter invoked when enabled, bypassed when disabled, file_paths propagated, exception fallback.
- `tests/unit/test_relay_length_guard.py` — 4096-char pass-through, 4097-char `.txt` conversion, no splitting, conversion-failure fallback.
- `tests/integration/test_message_drafter_integration.py` — real Haiku/OpenRouter round-trips.
