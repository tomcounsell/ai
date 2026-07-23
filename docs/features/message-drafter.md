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
5. If `_detect_empty_promise` fires (agent made a promise without substance — "will do", "going forward" etc.) **or** `_validate_for_medium` returns any non-empty `violations` list (markdown table, local file-path reference, etc.): return `MessageDraft(text="", needs_self_draft=True, violations=[...])` — caller injects a self-draft steering nudge. This promotion happens on **both** return points that can carry a `violations` list — the short-output early return (see below) and this main-path return — so a violation never ships silently regardless of message length (issue #1955). All promoted drafts route through the self-draft steering path (`agent/output_handler.py:429-441`), the mechanism actually live for eng/session_runner sessions; `agent/hooks/stop.py`'s stop-hook "delivery review gate" is dead code on that path and is **not** a violation-surfacing mechanism today — see [Agent-Controlled Message Delivery](agent-message-delivery.md#stop-hook-review-gate-agenthooksstoppy). Steering is not always consumable, though: on a session's **final** turn there is no next turn left to receive the nudge, so a `local_file_path_reference` violation there falls to a second remedy — the terminal flush's `convert_local_paths_to_attachments` conversion (see [Agent-Controlled Message Delivery §Validator-aware terminal flush](agent-message-delivery.md#validator-aware-terminal-flush-local-path--attachment-conversion-2211)).
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

Even when all four hold, the early return is superseded if `_validate_for_medium` flags a violation on the raw text (e.g. a `local_file_path_reference` — see [Validator surface](#validator-surface)): the draft returns `text=""`, `needs_self_draft=True` instead of verbatim pass-through. This is the path a terse reply like `"Done. Saved to /tmp/x.txt."` actually exits through, and is the one the #1955 fix targeted — promoting only the main-path return would have left this exact message class unfixed.

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

### `detect_local_file_reference(text) -> list[Violation]`

Medium-agnostic check (issue #1955): flags machine-local filesystem paths and macOS-only shell command references that are meaningless once a message leaves the machine that produced it. Emits `Violation(rule=LOCAL_FILE_PATH_RULE, ...)`, where `LOCAL_FILE_PATH_RULE = "local_file_path_reference"` is a module constant so callers can match on it without a bare string literal. Patterns checked:

- `/tmp/\S+` — temp-file paths
- `/Users/\S+` and `/home/\S+` — absolute home-directory paths
- `~/\S+` — tilde-relative paths
- `` `open -a ...` `` (backtick-wrapped) or bare `open -a \S+` — macOS `open` command references

Returns `[]` on empty input or text with no path-like substrings (no false positives on ordinary prose). This closes the gap surfaced by a real incident: `/weekly-review` saved output to `/tmp/eng_review_jul1-8.txt` and told the user to run `open -a TextEdit /tmp/...` — instructions that only resolve on the machine that ran the session.

### `convert_local_paths_to_attachments(text) -> tuple[str, list[str], int, int]`

Public helper (issue #2211) consumed by the terminal-flush chokepoint, not by `draft_message` itself — it runs on **held/deferred** text at `flush_deferred_self_draft_sync`, not on the normal per-turn drafting path. For each local-path token `detect_local_file_reference` would flag: attaches the file (existing, non-secret paths) via the outbox builders' `file_paths=` param, or scrubs the token from the text (dead paths, secret-excluded paths). See [Agent-Controlled Message Delivery §Validator-aware terminal flush](agent-message-delivery.md#validator-aware-terminal-flush-local-path--attachment-conversion-2211) for the full mechanism (secret-exclusion gate, empty-text guard, telemetry counters) — documented there to avoid duplication.

### `_validate_for_medium(text, medium) -> list[Violation]`

Dispatcher. Routes to `validate_telegram` or `validate_email` based on `medium`, then unconditionally extends the result with `detect_local_file_reference(text)` — local paths are meaningless on both Telegram and email, so this check runs regardless of medium (including for an unknown medium, which otherwise contributes `[]` from the per-medium branch).

### `format_violations(violations, medium) -> str`

Renders violations as a `⚠️` note for the review-gate presentation shown to the agent.

### `_detect_empty_promise(text_lower) -> bool`

Detects if the agent acknowledged feedback without concrete evidence. Backwards-compat shim — delegates to `bridge.promise_gate._detect_empty_promise`, which covers both the original behavioral-change patterns ("got it / will do") and forward-deferral patterns ("I'll follow up / stay tuned / more soon").

## Steering-first flag handling

When a blocking flag fires (`needs_self_draft=True`), the delivery path does **not** substitute a fallback message. Instead, `_inject_self_draft_steering` (in `agent/output_handler.py`) pushes a steering nudge back to the authoring agent, asking it to rewrite and resend. This is the PRIMARY flag-handling mechanism, not a failure fallback.

### Violation-aware instruction (issue #1955)

`_inject_self_draft_steering(self, session, draft)` takes the deferred `draft` and composes the pushed message from the base `SELF_DRAFT_INSTRUCTION` plus, when `draft.violations` contains an entry with `rule == LOCAL_FILE_PATH_RULE`, a targeted addendum:

> One or more local filesystem paths were detected in your message. Those paths are meaningless to the recipient. If you meant to share a file, attach it as a real Telegram attachment with `tools/send_message.py "<caption>" --file <path>` instead of pasting the path. If no file was meant, remove the path reference.

The base `SELF_DRAFT_INSTRUCTION` constant is unchanged and stays medium-agnostic (it actively says "omit internal code details," which alone does not point the agent at attaching a file) — the addendum is composed at injection time so it fires only for the local-path rule. Other violation types (markdown table, empty promise) get the base instruction alone.

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

## Tool-call delivery (shipped)

The stop-hook review gate presents **prepopulated tool-call examples** rather than a string menu — the agent invokes `send_message.py` (verbatim or revised text, both classify identically), swaps for `react_with_emoji.py`, or stops silent. There is no server-side rewrite, so "send as-is" and "edit and send" are not distinct outcomes: both classify as `send`. Clearing is implicit via transcript inspection. Canonical vocabulary (Decision D, `docs/plans/consolidate_delivery_paths.md`): the gate is the **delivery review gate**; the classifier's four outcomes are **send / react / silent / continue** — see [`classify_delivery_outcome`](agent-message-delivery.md#delivery-execution-tool-call-path).

### Delivery Tool Surface

The agent delivers user-visible messages and reactions via two CLI tools invoked through the `Bash` tool, not through a dedicated MCP server:

- `tools/send_message.py '<text>'` — primary delivery tool. Reconstitutes the `AgentSession` from `VALOR_SESSION_ID` and delegates to `agent.output_handler.TelegramRelayOutputHandler.send` for both telegram and email transports, so the drafter / redundancy filter / read-the-room gate run identically on the tool-call path and the silent-worker path. Handles `--reply-to <msg_id>` and `--file <path>` flags for threaded replies and attachments. Prints the returned `DeliveryOutcome` (`sent` / `suppressed_redundant` / `suppressed_rtr` / `deferred_self_draft` / `dropped_empty`) instead of an unconditional "Queued", so a redundancy- or RTR-suppressed send is visible to the calling agent rather than silently misreported. Fail-closed on missing session; `ALLOW_LEGACY_RPUSH_FALLBACK=1` opts into a diagnostic-only raw-rpush fallback path.
- `tools/react_with_emoji.py '<emoji>'` — posts a reaction emoji on the triggering message. Used for lightweight acknowledgements ("thumbs up, done") when a full text response would be noise. `--standalone` sends a custom-emoji standalone message (its own bubble) instead of a reaction — this is the migrated capability from the retired proactive-send tool's `--emoji` flag (issue #1370).

The stop hook classifies each turn's outcome by scanning `tool_use` blocks for these exact script paths (`agent/hooks/stop.py::classify_delivery_outcome`). Matches produce one of the four outcomes (send, react, silent, continue). The earlier proactive-send CLI (whose drafter call silently discarded validation verdicts) was retired in issue #1370; `send_message.py` is now the sole agent-facing CLI wrapper for both transports (see [Agent-Controlled Message Delivery §Delivery paths](agent-message-delivery.md#delivery-paths)).

**Why CLI tools over a bespoke MCP server:** a dedicated MCP server would require a root `.mcp.json` registration and add 300–500 lines of infrastructure for a surface that already works. The CLI tools route through the same outbox + relay as every other delivery path, the stop hook already recognizes them, and they are transparent to `gh pr comment` or any other bridge path that bypasses the drafter. Transcript readability (tool calls appearing as `Bash` invocations rather than semantic `send_message` tool_use blocks) is the only real trade-off, and the stop hook compensates by attaching semantic classification after the fact.

**Reversibility:** the CLI-tool surface can be wrapped in an MCP server in a future chore if transcript readability becomes a pain point. The stop-hook classification logic would gain a pattern match on the new tool name and keep the existing Bash-pattern match as a fallback for older-format turns.

Recorded as **Resolved Decision RD-1** in `docs/plans/message-drafter-followup.md` (2026-04-20 follow-up).

## Adjacent suppression layers

After the drafter finalises `delivery_text`, two optional suppression layers may intercept the message before it reaches the outbox:

1. **Redundancy filter** (`bridge/redundancy_filter.py`, issue #1205) — deterministic bigram-Jaccard guard for SDLC sessions. Runs first. Suppresses near-verbatim PM status repeats within a time window. See [Drafter Redundancy Suppression](drafter-redundancy-suppression.md).
2. **Read-the-Room** (`bridge/read_the_room.py`, issue #1193) — opt-in Haiku verdict for non-SDLC sessions (`send` / `trim` / `suppress`). See [Read-the-Room Pre-Send Pass](read-the-room.md).

Both layers queue a 👀 reaction on suppress (with an anchor) and emit `session_events` entries for observability.

## Files

- `bridge/message_drafter.py` — the drafter module. Includes `_truncate_at_sentence_boundary` since the #1074 follow-up, plus `convert_local_paths_to_attachments` (issue #2211), consumed by the terminal-flush chokepoint — see [Agent-Controlled Message Delivery §Validator-aware terminal flush](agent-message-delivery.md#validator-aware-terminal-flush-local-path--attachment-conversion-2211).
- `bridge/redundancy_filter.py` — deterministic redundancy filter for SDLC sessions (issue #1205).
- `agent/output_handler.py::TelegramRelayOutputHandler` — canonical delivery entry point. Drafter runs here; payload is written to the Redis outbox. Used by both the worker `send_cb` and (since the #1074 follow-up) the bridge's handler-event send callback.
- `bridge/email_bridge.py::EmailOutputHandler` — drafter-in-handler wiring for email.
- `bridge/telegram_relay.py::_send_queued_message` — belt-and-suspenders length guard.
- `bridge/response.py` — slim reactions + helpers module. Contains `set_reaction`, `VALIDATED_REACTIONS`, `filter_tool_logs`, `extract_files_from_response`, `clean_message`. The pre-#1074 `send_response_with_files` delivery function was deleted as part of the follow-up (see `docs/plans/message-drafter-followup.md` Part C).
- `agent/hooks/stop.py` — stop-hook review gate that drafts the final reply and classifies delivery outcomes by matching `tool_use` blocks for the CLI delivery tools. **Dead code for `session_type=eng` / `session_runner` sessions** — `agent/session_runner/hook_edge.py::generate_hook_settings` wires the Stop hook only to `hook_forwarder.py`, never to this file (confirmed twice independently: issue #1955 and the `consolidate_delivery_paths` freshness re-check). It is not a live violation-surfacing mechanism for those sessions; see [Agent-Controlled Message Delivery](agent-message-delivery.md#stop-hook-review-gate-agenthooksstoppy).
- `agent/steering.py` — `SELF_DRAFT_MAX_ATTEMPTS`, `bump_self_draft_attempts`, `reset_self_draft_attempts` — Redis counter for the sequential self-draft loop bound.
- `tools/send_message.py`, `tools/react_with_emoji.py` — the agent-facing CLI delivery surface (see "Delivery Tool Surface" above).

## Tests

- `tests/unit/test_message_drafter.py::TestDraftMessage` — drafter classification, artifact extraction, prompt building, per-medium assertions, plus (issue #1955) a short-output case and a long/composed case each asserting a local-path violation promotes to `needs_self_draft=True`/`text=""` on its respective return path.
- `tests/unit/test_medium_validators.py` — `validate_telegram`, `validate_email`, `_validate_for_medium`, `format_violations` unit coverage (added in the #1074 follow-up), plus `TestDetectLocalFileReference` (issue #1955): `/tmp/...`, `~/...`, `/Users/...`, `/home/...`, `` `open -a ...` `` matches and false-positive guards on ordinary prose.
- `tests/unit/test_drafter_validators.py::TestDetectLocalFileReference` — mirrors `test_medium_validators.py`'s coverage of the same validator (the two files intentionally duplicate validator tests; see Rabbit Holes in `docs/plans/message-drafter-file-path-flagging.md`).
- `tests/unit/test_output_handler.py::TestDrafterInHandler` — drafter-at-the-handler wiring: flag read at init, drafter invoked when enabled, bypassed when disabled, file_paths propagated, exception fallback. Also covers (issue #1955) the `_inject_self_draft_steering(session, draft)` signature: the pushed instruction contains the attach-via-`--file` addendum when a `local_file_path_reference` violation is present, and omits it for other violation types (e.g. markdown table).
- `tests/unit/test_relay_length_guard.py` — 4096-char pass-through, 4097-char `.txt` conversion, no splitting, conversion-failure fallback.
- `tests/unit/test_tool_call_delivery.py` — stop-hook classification for send / react / silent / continue outcomes via `tool_use` pattern match on the CLI delivery tools, plus per-path contract tests asserting input → outbox payload with the real handler (issue #1370) — classification logic only, the gate itself is dead for session_runner/eng sessions (see Files above).
- `tests/integration/test_message_drafter_integration.py` — pass-through validation: narration strip, composition, validator surface, self-draft steering path, plus (issue #1955) a regression case reproducing the weekly-review local-path incident text end-to-end.
- `tests/integration/test_reply_delivery.py` — end-to-end reaction paths (PM self-message bypass, completion emoji, error emoji).
