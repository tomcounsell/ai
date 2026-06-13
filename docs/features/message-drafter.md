# Message Drafter

Medium-aware validation and pass-through layer that owns wire-format compliance for every user-visible message leaving the agent. Tracked by [issue #1035](https://github.com/tomcounsell/ai/issues/1035).

## Why it exists

Historically two parallel delivery paths existed:

1. **Bridge-handler flow** — human Telegram message → bridge → `send_response_with_files` → summarizer → Telethon. This path called the summarizer and handled long-output file attachment.
2. **Worker `send_cb` flow** — worker-executed PM sessions → `TelegramRelayOutputHandler.send` → raw text straight to Redis outbox → relay → Telethon.

Only the first path ran the drafter. The second wrote raw text to the outbox and fell over on any content >4096 characters (Telethon `MessageTooLongError`, dead-letter, user saw nothing). Email sessions shipped literal markdown as `text/plain` MIME because the summarizer was Telegram-only.

**Baseline incident**: session `tg_cuttlefish_-1003801797780_94` (2026-04-20), 4,582-char PM response dead-lettered.

The drafter consolidates both paths. The rename (`summarizer` → `message_drafter`) signals the expanded scope: medium-aware + tool-call delivery + per-medium validators. The subsequent refactor (drafter_passthrough_validation) eliminated all server-side LLM rewriting — the agent's own text now reaches the human verbatim, after narration stripping and structural composition.

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

Every user-visible message passes through `bridge/message_drafter.py::draft_message` before it leaves the worker. The drafter validates and passes through the agent's own text — no LLM rewriting. The relay has a belt-and-suspenders length guard that converts any oversize text to a `.txt` attachment. Splitting messages is **banned** (see No-Gos).

## API

### `draft_message(raw_response, session=None, *, medium="telegram", persona=None) -> MessageDraft`

The sole public entry point. Everything else is an implementation detail.

**Arguments:**

| Name | Type | Default | Purpose |
|------|------|---------|---------|
| `raw_response` | `str` | — | The raw agent output text. |
| `session` | `AgentSession \| None` | `None` | Enriches the draft with SDLC stage progress, persona/mode context, and linkifies PR/issue numbers. |
| `medium` | `str` | `"telegram"` | Discriminator for per-medium validator rules. `"telegram"` or `"email"`. |
| `persona` | `str \| None` | `None` | Optional tone hint. Orthogonal to medium. Not used today. |

**Returns `MessageDraft`:**

```python
@dataclass
class MessageDraft:
    text: str                                 # body to deliver (verbatim agent text, composed)
    full_output_file: Path | None = None      # .txt attachment for long raw output
    needs_self_draft: bool = False            # True when a blocking flag fired (violation or empty promise)
    artifacts: dict[str, list[str]] = {}      # commit hashes, URLs, PRs
    context_summary: str | None = None        # deterministic one-sentence routing hint
    expectations: str | None = None           # open questions (None when absent, never "")
    violations: list[Violation] = []          # wire-format violations for agent review
```

Note: `was_drafted` has been removed. The drafter no longer calls any LLM — the agent's own text is used after narration stripping and structural composition. There is no Haiku/OpenRouter rewrite path.

### Pass-through flow

`draft_message` runs these steps in order:

1. Strip process narration from raw text (`_strip_process_narration`).
2. Apply deterministic structural composition (`_compose_structured_draft`) — emoji prefix, SDLC stage line, bullet/question parsing, link footer.
3. Run `_validate_for_medium` on the composed text.
4. If over `FILE_ATTACH_THRESHOLD`, write a full-output `.txt` file (delivery still proceeds).
5. If any **blocking** flag fires (wire-format violation, empty promise detected via `_detect_empty_promise`): return `MessageDraft(text="", needs_self_draft=True, violations=[...])`.
6. Populate `context_summary` from `_derive_context_summary(stripped_raw_text)`.
7. Populate `expectations` from `_extract_open_questions(stripped_raw_text)` — `None` when no questions, never `""`.
8. Return `MessageDraft(text=<composed>, context_summary=..., expectations=..., violations=[...])`.

### Short-output early return (D5a)

Texts under `SHORT_OUTPUT_THRESHOLD = 200` skip structural composition and return verbatim when all of these hold:

- No SDLC session (SDLC always needs stage progress + link footer).
- No extracted artifacts (commit hashes, URLs, PRs deserve drafter polish).
- No `?` in the text (questions go through expectation handling).
- No fenced code block.

The goal is to bound per-message latency on brief replies. See Risk 1 in `docs/plans/message-drafter.md`.

## Validator surface

The per-medium validator runs on every message. No server-side rewrites — violations are surfaced to the agent so it can fix them via the self-draft steering path.

### `validate_telegram(text) -> list[Violation]`

Checks for Telegram wire-format violations. Current rules:

- **no_markdown_tables**: the `| --- | --- |` separator row does not render in Telegram.

### `validate_email(text) -> list[Violation]`

Checks for email wire-format violations (plain prose only). Rejects:

- `no_fenced_code`, `no_inline_code`
- `no_markdown_headings`
- `no_bold_markdown`, `no_italic_markdown`
- `no_markdown_links`, `no_markdown_bullets`
- Markdown tables (delegates to `validate_telegram`)

### `_validate_for_medium(text, medium) -> list[Violation]`

Dispatcher. Routes to `validate_telegram` or `validate_email`. Returns `[]` for unknown mediums.

### `format_violations(violations, medium) -> str`

Renders violations as a `⚠️` note for the review-gate presentation shown to the agent.

### `_detect_empty_promise(text_lower) -> bool`

Detects if the agent acknowledged feedback without concrete evidence. Backwards-compat shim — delegates to `bridge.promise_gate._detect_empty_promise`, which covers both legacy behavioral-change patterns ("got it / will do") and forward-deferral patterns ("I'll follow up / stay tuned / more soon").

## Steering-first flag handling

When a blocking flag fires (`needs_self_draft=True`), the delivery path does **not** substitute a fallback message. Instead, `_inject_self_draft_steering` (in `agent/output_handler.py`) pushes a steering nudge back to the authoring agent, asking it to rewrite and resend. This is the PRIMARY flag-handling mechanism, not a failure fallback.

### Sequential self-draft loop bound

To prevent infinite steering loops (the agent's self-draft also fails validation), the attempt count is tracked in Redis:

- **`SELF_DRAFT_MAX_ATTEMPTS = 2`** (in `agent/steering.py`) — maximum consecutive self-draft attempts.
- **`steering:attempts:{session_id}`** — Redis key (type: string/integer counter). Atomic `INCR` via `bump_self_draft_attempts`; `DELETE` via `reset_self_draft_attempts`.
- On cap hit (`attempts >= SELF_DRAFT_MAX_ATTEMPTS`), the handler falls through to the narration fallback instead of injecting another steering message.
- The counter resets on any clean (non-self-draft) delivery: `reset_self_draft_attempts` is called before the `STEERING_DEFERRED` early-return.

## Deterministic routing fields

### `_derive_context_summary(raw_text) -> str | None`

Derives a coarse one-sentence routing hint from the narration-stripped text. This is deliberately simple — first non-blank, non-heading line, capped at 140 characters at a word boundary. No NLP, no LLM. Purpose: give `session_router.py` and other routing readers a coarse topic hint. Not a quality deliverable, not user-facing prose. Returns `None` for empty or whitespace-only input.

### `_extract_open_questions(text) -> list[str]`

The sole source of the `expectations` field. Scans the text for a `## Open Questions` heading and extracts substantive list items below it. Returns empty list if no section is found, the section is empty, or it contains only placeholders.

**None-vs-empty contract**: `expectations` on `MessageDraft` is `None` when no questions are found, never `""`. `_persist_routing_fields` in `output_handler.py` only writes `expectations` when it is not `None`, preserving any prior persisted value when no new questions are present.

## Drafter-at-the-handler (the critical fix)

`agent/output_handler.py::TelegramRelayOutputHandler.send` always routes text through the drafter. On every call:

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
- **No server-side LLM rewriting.** The agent's own text is used verbatim. Haiku/OpenRouter are not called by the drafter.

## Format rules by medium

### Telegram

- `Markdown V2` rendering via Telethon (existing `bridge/markdown.py::send_markdown`).
- **No tables.** Markdown table syntax (`| --- |`) does not render in Telegram; the drafter and downstream validators treat it as a wire-format violation.
- `FILE_ATTACH_THRESHOLD` (default 3000 chars): full raw output also written to a `.txt` attachment alongside the short composed message.
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

### Delivery Tool Surface

The agent delivers user-visible messages and reactions via two CLI tools invoked through the `Bash` tool, not through a dedicated MCP server:

- `tools/send_message.py '<text>'` — primary delivery tool. Reconstitutes the `AgentSession` from `VALOR_SESSION_ID` and delegates to `agent.output_handler.TelegramRelayOutputHandler.send` for both telegram and email transports, so the drafter / redundancy filter / read-the-room gate run identically on the tool-call path and the silent-worker path. Handles `--reply-to <msg_id>` and `--file <path>` flags for threaded replies and attachments. Fail-closed on missing session; `ALLOW_LEGACY_RPUSH_FALLBACK=1` opts into a diagnostic-only legacy raw rpush.
- `tools/react_with_emoji.py '<emoji>'` — posts a reaction emoji on the triggering message. Used for lightweight acknowledgements ("thumbs up, done") when a full text response would be noise.

The stop hook classifies each turn's outcome by scanning `tool_use` blocks for these exact script paths (`agent/hooks/stop.py::classify_delivery_outcome`). Matches produce one of the five outcomes (send, edit+send, react, silent, continue).

**Why CLI tools over a bespoke MCP server:** a dedicated MCP server would require a root `.mcp.json` registration and add 300–500 lines of infrastructure for a surface that already works. The CLI tools route through the same outbox + relay as every other delivery path, the stop hook already recognizes them, and they are transparent to `gh pr comment` or any other bridge path that bypasses the drafter. Transcript readability (tool calls appearing as `Bash` invocations rather than semantic `send_message` tool_use blocks) is the only real trade-off, and the stop hook compensates by attaching semantic classification after the fact.

**Reversibility:** the CLI-tool surface can be wrapped in an MCP server in a future chore if transcript readability becomes a pain point. The stop-hook classification logic would gain a pattern match on the new tool name and keep the existing Bash-pattern match as a fallback for legacy turns.

Recorded as **Resolved Decision RD-1** in `docs/plans/message-drafter-followup.md` (2026-04-20 follow-up).

## Adjacent suppression layers

After the drafter finalises `delivery_text`, two optional suppression layers may intercept the message before it reaches the outbox:

1. **Redundancy filter** (`bridge/redundancy_filter.py`, issue #1205) — deterministic bigram-Jaccard guard for SDLC sessions. Runs first. Suppresses near-verbatim PM status repeats within a time window. See [Drafter Redundancy Suppression](drafter-redundancy-suppression.md).
2. **Read-the-Room** (`bridge/read_the_room.py`, issue #1193) — opt-in Haiku verdict for non-SDLC sessions (`send` / `trim` / `suppress`). See [Read-the-Room Pre-Send Pass](read-the-room.md).

Both layers queue a 👀 reaction on suppress (with an anchor) and emit `session_events` entries for observability.

## Files

- `bridge/message_drafter.py` — the drafter module. Includes `_truncate_at_sentence_boundary` since the #1074 follow-up.
- `bridge/redundancy_filter.py` — deterministic redundancy filter for SDLC sessions (issue #1205).
- `agent/output_handler.py::TelegramRelayOutputHandler` — canonical delivery entry point. Drafter runs here; payload is written to the Redis outbox. Used by both the worker `send_cb` and (since the #1074 follow-up) the bridge's handler-event send callback.
- `bridge/email_bridge.py::EmailOutputHandler` — drafter-in-handler wiring for email.
- `bridge/telegram_relay.py::_send_queued_message` — belt-and-suspenders length guard.
- `bridge/response.py` — slim reactions + helpers module. Contains `set_reaction`, `VALIDATED_REACTIONS`, `filter_tool_logs`, `extract_files_from_response`, `clean_message`. The pre-#1074 `send_response_with_files` delivery function was removed in the follow-up (see `docs/plans/message-drafter-followup.md` Part C).
- `agent/hooks/stop.py` — stop-hook review gate that drafts the final reply and classifies delivery outcomes by matching `tool_use` blocks for the CLI delivery tools.
- `agent/steering.py` — `SELF_DRAFT_MAX_ATTEMPTS`, `bump_self_draft_attempts`, `reset_self_draft_attempts` — Redis counter for the sequential self-draft loop bound.
- `tools/send_message.py`, `tools/react_with_emoji.py` — the agent-facing CLI delivery surface (see "Delivery Tool Surface" above).

## Tests

- `tests/unit/test_message_drafter.py` — drafter classification, artifact extraction, prompt building, per-medium assertions.
- `tests/unit/test_medium_validators.py` — `validate_telegram`, `validate_email`, `_validate_for_medium`, `format_violations` unit coverage (added in the #1074 follow-up).
- `tests/unit/test_output_handler.py::TestDrafterInHandler` — drafter-at-the-handler wiring: flag read at init, drafter invoked when enabled, bypassed when disabled, file_paths propagated, exception fallback.
- `tests/unit/test_relay_length_guard.py` — 4096-char pass-through, 4097-char `.txt` conversion, no splitting, conversion-failure fallback.
- `tests/unit/test_tool_call_delivery.py` — stop-hook classification for send / react / silent / edit+send / continue outcomes via `tool_use` pattern match on the CLI delivery tools.
- `tests/unit/test_message_drafter_integration.py` — pass-through validation: narration strip, composition, validator surface, self-draft steering path.
- `tests/integration/test_reply_delivery.py` — end-to-end reaction paths (PM self-message bypass, completion emoji, error emoji).
