# Emoji Embedding Reactions

Emoji reaction selection for Telegram messages. Uses action-intent vocabulary at receipt time and embedding-based sentiment matching for agent-driven reactions.

## Overview

The system has two distinct emoji selection paths:

**Receipt-time reactions** (`find_best_emoji_for_message`): When a Telegram message arrives, the bridge reacts with an emoji that reflects the **agent's intended handling action**, not the message's content sentiment. The reaction is chosen from a small pre-defined vocabulary keyed by `work_type` (bug/feature/chore/sdlc), which the bridge derives from `classify_work_type()`. No embedding call is made; the selection is immediate and synchronous.

**Agent-driven reactions** (`find_best_emoji`): The agent can set a reaction by feeling word (e.g. `--react "excited"`). This still uses content embedding and cosine similarity against the 72 validated Telegram emoji labels.

Premium accounts gain access to thousands of custom emoji (animated sticker-based emoji) in addition to the 72 standard reactions. The system indexes available custom emoji packs and includes them in the similarity search, with automatic fallback to standard emoji when custom emoji are unavailable.

## How It Works

### Message Reaction Flow

1. Message arrives in `bridge/telegram_bridge.py` handler
2. `mark_read()` is called immediately
3. Eyes emoji set as initial acknowledgment
4. `find_best_emoji_for_message(text, work_type)` maps `work_type` to an action category via `WORKTYPE_TO_ACTION` and selects from `ACTION_EMOJI_MAP[action]` via `random.choice` -- no API call, no embedding, synchronous
5. Reaction updated to the action-intent emoji
6. The `work_type` comes from `classification_result["type"]` already computed by `classify_work_type()` before the reaction update

### Agent Reaction Flow

1. Agent calls `python tools/react_with_emoji.py "excited"`
2. `find_best_emoji("excited")` embeds the feeling word and finds the nearest emoji
3. A reaction payload `{type: "reaction", emoji: "...", ...}` is queued to Redis outbox
4. `bridge/telegram_relay.py` detects the reaction payload and calls `set_reaction()` via Telethon

## EmojiResult Type

All emoji lookup functions return an `EmojiResult` dataclass instead of a raw string. This type carries both standard and custom emoji information:

```python
from tools.emoji_embedding import EmojiResult

result = find_best_emoji("excited")
result.emoji         # Standard Unicode emoji string (e.g., "🔥"), or None
result.document_id   # Telegram custom emoji document ID (int64), or None
result.is_custom     # True when the match is a custom emoji
result.score         # Cosine similarity score of the best match
str(result)          # Backward-compatible string (emoji char or placeholder)
result.display       # Alias for str(result)
```

Backward compatibility is preserved: `str(result)` returns the Unicode emoji character for standard results, a placeholder character for custom results, or the default thinking emoji if nothing matched. Existing code that passes the result to string-consuming functions continues to work.

## Components

### Emoji Embedding Index (`tools/emoji_embedding.py`)

The core module that maps feelings to emojis.

**Key functions:**
- `find_best_emoji(feeling: str) -> EmojiResult` -- embeds a feeling word and returns the nearest emoji (standard or custom) by cosine similarity (agent-driven reactions)
- `find_best_emoji_for_message(text: str, work_type: str | None) -> EmojiResult` -- maps `work_type` to an action category and selects a random emoji from `ACTION_EMOJI_MAP`; ignores text content at selection time
- `clear_cache()` -- clears the in-memory cache (for testing)
- `rebuild_custom_emoji_index(client)` -- async function to query Telethon for available custom emoji packs and rebuild the custom embedding cache

**Action vocabulary:** `WORKTYPE_TO_ACTION` maps work-type labels to action intent categories. `ACTION_EMOJI_MAP` maps each category to a list of valid Telegram reaction emoji candidates. All candidates are confirmed in `VALIDATED_REACTIONS`.

| Category | Trigger | Emoji candidates |
|----------|---------|-----------------|
| `investigate_bug` | `work_type=bug` | 👨‍💻, 👀 |
| `problem_solving` | (not currently auto-triggered) | 👨‍💻, 🤝 |
| `acknowledge_task` | `work_type` in feature/chore/sdlc | 🫡, 👍 |
| `receive_praise` | (not currently auto-triggered) | 🙏, ❤, 🏆 |
| `answer_question` | (not currently auto-triggered) | 🤔, 🤝 |
| `general` | `work_type` is None or unrecognized | 👀 |

**Standard emoji labels:** Each of the 72 validated Telegram reaction emojis has a descriptive label string used by `find_best_emoji()` (the agent-driven path). These labels are embedded and compared against input text.

**Blocked reactions:** `BLOCKED_REACTION_EMOJIS` is the single source of truth for reactions that must never target a user. It is a frozenset covering the middle finger 🖕 (also removed entirely from `VALIDATED_REACTIONS` and `EMOJI_LABELS`) plus five hostile faces that stay valid Telegram reactions but are excluded from selection: thumbs down 👎, face with symbols on mouth 🤬, pouting face 😡, face vomiting 🤮, and face screaming in fear 😱. `find_best_emoji()` skips any emoji in this set at selection time, so no semantically-resolved reaction can ever draw a hostile face, even if one scores as the nearest match. Self-directed sadness (😢 😭 😨) is not blocked. It expresses empathy rather than hostility, so it stays selectable. Offensive reactions are structurally impossible from `find_best_emoji_for_message` because the `ACTION_EMOJI_MAP` action vocabulary never contains them.

**Custom emoji labels:** Custom emoji from Premium sticker packs are labeled using the associated emoji character plus the sticker set title (e.g., "party celebration confetti" for a party popper custom emoji). These are embedded with the same model and stored separately.

**Caching:** Standard embeddings are cached to `data/emoji_embeddings.json`. Custom emoji embeddings are cached separately to `data/custom_emoji_embeddings.json`. Both are computed lazily on first use via OpenRouter (`text-embedding-3-small`) and loaded from disk on subsequent starts.

**Unified search:** `find_best_emoji()` searches both standard and custom emoji embeddings in a single pass. Custom emoji wins only when its similarity score exceeds the best standard match by a delta of 0.05, keeping behavior conservative.

**Fallback:** If the embedding API is unavailable, the API key is not set, or any error occurs, the default thinking emoji is returned. If the account is not Premium or custom emoji API calls fail at startup, custom emoji indexing is disabled for the session and all lookups return standard emoji only.

### Reaction Mode (`tools/react_with_emoji.py`)

```bash
# React to the current message with a feeling
python tools/react_with_emoji.py "excited"
python tools/react_with_emoji.py "great work"
python tools/react_with_emoji.py "thinking"
```

The default (reaction) mode:
- Requires `TELEGRAM_REPLY_TO` (must have a message to react to)
- Resolves the feeling word to an `EmojiResult` via `find_best_emoji()`
- Queues a reaction payload to the Redis outbox for relay delivery
- When the result is a custom emoji, includes `custom_emoji_document_id` in the payload
- Exits with an error if `TELEGRAM_REPLY_TO` is not set or the feeling is empty

### Standalone Emoji Message (`tools/react_with_emoji.py --standalone`)

```bash
# Send a standalone custom emoji message
python tools/react_with_emoji.py --standalone "celebration"
python tools/react_with_emoji.py --standalone "excited"
```

The `--standalone` flag sends a custom emoji as a standalone message (not a reaction). It:
- Requires `TELEGRAM_CHAT_ID` and `VALOR_SESSION_ID`
- Resolves the feeling word to the best emoji via `find_best_emoji()`
- Queues a `custom_emoji_message` payload to the Redis outbox
- When a custom emoji is matched, includes `custom_emoji_document_id` for the relay to render via `MessageEntityCustomEmoji`
- Falls back to sending the emoji character as plain text if the custom emoji send fails
- Exits with an error if required env vars are missing or the feeling is empty

### Relay Reaction Handler (`bridge/telegram_relay.py`)

The relay handles two payload types:
- `"type": "reaction"` -- calls `set_reaction()` via Telethon. When `custom_emoji_document_id` is present in the payload, wraps it in an `EmojiResult` so `set_reaction()` can dispatch to `ReactionCustomEmoji`.
- `"type": "custom_emoji_message"` -- sends a standalone custom emoji message using `MessageEntityCustomEmoji` for rendering. Falls back to plain text on failure.

Failed reactions and messages are logged and skipped (no re-queue).

### Reaction Dispatch (`bridge/response.py`)

`set_reaction()` accepts both plain emoji strings and `EmojiResult` objects:
- For standard emoji: constructs `ReactionEmoji(emoticon=emoji)` as before
- For custom emoji (`EmojiResult.is_custom=True`): constructs `ReactionCustomEmoji(document_id=...)` first, falls back to standard emoji from the same result on failure
- Pass `None` to remove all reactions from a message

## Graceful Degradation

Every custom emoji code path has automatic fallback:

| Scenario | Behavior |
|----------|----------|
| Non-Premium account | Custom emoji indexing disabled at startup, all lookups return standard emoji |
| Custom emoji API error | Warning logged, custom index skipped, standard-only search |
| Custom emoji reaction rejected | `set_reaction()` retries with best standard emoji from same query |
| Custom emoji message send fails | Relay sends the emoji character as plain text instead |
| Empty custom emoji cache | Standard emoji path unchanged, transparent to callers |

The caller never needs to handle standard vs custom emoji explicitly. The `EmojiResult` type and dispatch logic handle all branching internally.

## Migration from Ollama Intent Classification

The embedding-based system replaced the Ollama-based intent classifier. The following code is no longer in the codebase:

- **`intent/__init__.py`** -- deleted module. Contained Ollama-based intent classification with heuristic fallback, used solely by `get_processing_emoji` for reaction selection.
- **`INTENT_REACTIONS` dict** -- hardcoded mapping of 10 intent strings to emojis, previously in `bridge/response.py`
- **`get_processing_emoji()` and `get_processing_emoji_async()`** -- Ollama classification wrappers, previously in `bridge/response.py`

The work-type classifier (`tools/classifier.py` / `classify_request_async`) was preserved and now runs as its own independent async task.

## Performance

The two emoji paths have different performance profiles:

| Metric | Old (Ollama) | Receipt-time (action-intent) | Agent-driven (embedding) |
|--------|-------------|------------------------------|--------------------------|
| Latency | 2-10 seconds | Synchronous (dict lookup, no API) | Under 50ms warm (cosine similarity) |
| Cold start | 2-10 seconds | None | ~1 second (compute 72 embeddings via API) |
| Emoji coverage | 10 hardcoded | 6 action categories, curated | All 72 validated reactions |
| Timeout risk | Frequent | None | Rare (only on API key missing) |

## Related Files

| File | Purpose |
|------|---------|
| `tools/emoji_embedding.py` | Embedding index, `EmojiResult` dataclass, standard + custom emoji selection, `BLOCKED_REACTION_EMOJIS` blocklist |
| `tools/react_with_emoji.py` | Default mode for agent reactions, `--standalone` flag for standalone emoji messages |
| `bridge/telegram_bridge.py` | Message handler integration |
| `bridge/telegram_relay.py` | Reaction and custom emoji message payload delivery |
| `bridge/response.py` | `set_reaction()` with standard and custom emoji dispatch |
| `data/emoji_embeddings.json` | Disk cache for standard emoji embeddings |
| `data/custom_emoji_embeddings.json` | Disk cache for custom (Premium) emoji embeddings |
| `tests/unit/test_emoji_embedding.py` | Embedding index and EmojiResult tests |
| `tests/unit/test_custom_emoji_index.py` | Custom emoji index building and cache tests |
| `tests/unit/test_react_with_emoji.py` | Reaction and standalone emoji message tests |

## Terminal Reactions

Session lifecycle events are reported back to Telegram via three terminal reaction constants defined in `agent/constants.py`. Two resolve semantically; one is pinned to a fixed emoji:

| Constant | Semantic | Resolution | Feeling String / Pinned Emoji | Fallback Emoji |
|----------|----------|------------|-------------------------------|-----------------|
| `REACTION_SUCCESS` | Silent ack — no text reply sent | Semantic (`find_best_emoji`) | `"acknowledged received silently noted"` | 👌 |
| `REACTION_COMPLETE` | Work done — text reply attached | Semantic (`find_best_emoji`) | `"task completed successfully work done"` | 👏 |
| `REACTION_ERROR` | Something went wrong | Pinned (fixed) | 🤔 | 🤔 (no fallback path; the pin IS the value) |

These constants are `EmojiResult` objects, **not** plain strings, resolved lazily on first access inside a live request handler and cached in a module-level dict (`_TERMINAL_EMOJI_CACHE`) — no HTTP call is made at import time and no retry occurs after the first resolution. Each constant's resolution mode is declared in `_TERMINAL_EMOJI_CONFIG`, a dict of `_TerminalEmojiConfig` NamedTuples keyed by constant name, with a `pinned` flag selecting the path.

`REACTION_SUCCESS` and `REACTION_COMPLETE` follow the semantic path: `find_best_emoji()` embeds the feeling string above and finds the nearest emoji by cosine similarity. When `find_best_emoji()` is unavailable (missing `OPENROUTER_API_KEY`, absent embeddings file, or the function returns the default thinking emoji), `_resolve_terminal_emoji()` substitutes the hardcoded fallback `EmojiResult` shown in the table. Both fallbacks are confirmed in `VALIDATED_REACTIONS` and stay distinct from each other in degraded environments.

**Reserved-glyph exclusion (issue #1961 / #2004).** A semantic draw can also land on a glyph that's already pinned or cached for a *different* reaction constant — this actually happened live: `REACTION_SUCCESS`'s semantic draw resolved to 🫡, the pinned glyph `bridge/response.py` uses for `REACTION_ABORT`. `_resolve_terminal_emoji()` now treats that as a failed resolution (same branch as an unavailable API), not a valid answer: it checks the drawn `result.emoji` against `agent.constants.RESERVED_REACTION_GLYPHS` — the union of `bridge/response.py`'s pinned glyphs (👀 `REACTION_RECEIVED`, ✍ `REACTION_PROCESSING`, 🫡 `REACTION_ABORT`, hardcoded in `agent/constants.py` to avoid an import cycle) and every pinned/fallback emoji in `_TERMINAL_EMOJI_CONFIG` (🤔 👌 👏) — and separately against every glyph already cached in `_TERMINAL_EMOJI_CACHE` for a *different* constant name. Either match raises internally and falls through to the hardcoded fallback `EmojiResult`, so the six-constant set stays pairwise distinct even when the embedding index changes.

`REACTION_ERROR` follows the pinned path instead. It resolves directly to 🤔 and never calls `find_best_emoji()` at all: no feeling string, no embedding lookup, no dependence on the semantic resolver's degraded-path fallback. An error reaction lands on the user's own message, so a semantic draw over an "error / something went wrong" feeling could surface a face that reads as hostile toward that person. Pinning the constant removes the draw entirely: every error, in every environment (API available or not), produces the same deterministic, non-hostile 🤔. See [Reaction Semantics](reaction-semantics.md#reaction_error-is-pinned-not-semantic) for the full rationale, and [Reaction Semantics § Import-Time Distinctness Assert](reaction-semantics.md#import-time-distinctness-assert-issue-1961--2004) for the complementary `bridge/response.py`-side guard.

`bridge/response.py` re-exports these constants for backward compatibility. The `set_reaction()` call site already accepts `EmojiResult` objects transparently via the existing standard/custom emoji dispatch logic.

## See Also

- [Classification](classification.md) -- work-type classification (bug/feature/chore), which is separate from emoji reactions
- [Reaction Semantics](reaction-semantics.md) -- emoji reaction protocol for message delivery feedback
