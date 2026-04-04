# Emoji Embedding Reactions

Embedding-based emoji reaction selection for Telegram messages, replacing the previous Ollama intent classification approach. Supports both standard Unicode reactions and Telegram Premium custom emoji.

## Overview

When a Telegram message arrives, the bridge selects a contextual reaction emoji by embedding the message text and comparing it against pre-computed embeddings for all 73 validated Telegram reaction emojis via cosine similarity. This replaces the previous Ollama-based intent classifier that mapped messages to 10 hardcoded emojis with 2-10 second latency.

The same embedding index powers the `send_telegram --react` flag, allowing the agent to set emoji reactions on messages by describing a feeling word.

Premium accounts gain access to thousands of custom emoji (animated sticker-based emoji) in addition to the 73 standard reactions. The system indexes available custom emoji packs and includes them in the similarity search, with automatic fallback to standard emoji when custom emoji are unavailable.

## How It Works

### Message Reaction Flow

1. Message arrives in `bridge/telegram_bridge.py` handler
2. `mark_read()` is called immediately (new: messages are now marked as read on receipt)
3. Eyes emoji set as initial acknowledgment
4. `find_best_emoji_for_message(text)` embeds the first 100 characters and finds the nearest emoji by cosine similarity (under 50ms after cache warm-up)
5. Reaction updated to the contextual emoji
6. Work-type classification (`classify_request_async`) runs as a separate async task

### Agent Reaction Flow

1. Agent calls `python tools/send_telegram.py --react "excited"`
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
- `find_best_emoji(feeling: str) -> EmojiResult` -- embeds a feeling word and returns the nearest emoji (standard or custom) by cosine similarity
- `find_best_emoji_for_message(text: str) -> EmojiResult` -- extracts a 100-char snippet from a message and delegates to `find_best_emoji`
- `clear_cache()` -- clears the in-memory cache (for testing)
- `rebuild_custom_emoji_index(client)` -- async function to query Telethon for available custom emoji packs and rebuild the custom embedding cache

**Standard emoji labels:** Each of the 73 validated Telegram reaction emojis has a descriptive label string (e.g., "fire, hot, trending, lit, exciting, impressive, awesome" for the fire emoji). These labels are embedded and compared against input text.

**Custom emoji labels:** Custom emoji from Premium sticker packs are labeled using the associated emoji character plus the sticker set title (e.g., "party celebration confetti" for a party popper custom emoji). These are embedded with the same model and stored separately.

**Caching:** Standard embeddings are cached to `data/emoji_embeddings.json`. Custom emoji embeddings are cached separately to `data/custom_emoji_embeddings.json`. Both are computed lazily on first use via OpenRouter (`text-embedding-3-small`) and loaded from disk on subsequent starts.

**Unified search:** `find_best_emoji()` searches both standard and custom emoji embeddings in a single pass. Custom emoji wins only when its similarity score exceeds the best standard match by a delta of 0.05, keeping behavior conservative.

**Fallback:** If the embedding API is unavailable, the API key is not set, or any error occurs, the default thinking emoji is returned. If the account is not Premium or custom emoji API calls fail at startup, custom emoji indexing is disabled for the session and all lookups return standard emoji only.

### send_telegram Reaction Flag (`tools/send_telegram.py`)

```bash
# React to the current message with a feeling
python tools/send_telegram.py --react "excited"
python tools/send_telegram.py --react "great work"
python tools/send_telegram.py --react "thinking"
```

The `--react` flag:
- Requires `TELEGRAM_REPLY_TO` (must have a message to react to)
- Resolves the feeling word to an `EmojiResult` via `find_best_emoji()`
- Queues a reaction payload to the Redis outbox for relay delivery
- When the result is a custom emoji, includes `custom_emoji_document_id` in the payload
- Exits with an error if `TELEGRAM_REPLY_TO` is not set or the feeling is empty

### send_telegram Emoji Flag (`tools/send_telegram.py`)

```bash
# Send a standalone custom emoji message
python tools/send_telegram.py --emoji "celebration"
python tools/send_telegram.py --emoji "excited"
```

The `--emoji` flag sends a custom emoji as a standalone message (not a reaction). It:
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

## What Was Removed

This feature removed the following legacy code:

- **`intent/__init__.py`** -- the entire module was deleted. It contained Ollama-based intent classification with heuristic fallback, used solely by `get_processing_emoji` for reaction selection.
- **`INTENT_REACTIONS` dict** from `bridge/response.py` -- hardcoded mapping of 10 intent strings to emojis
- **`get_processing_emoji()` and `get_processing_emoji_async()`** from `bridge/response.py` -- Ollama classification wrappers

The work-type classifier (`tools/classifier.py` / `classify_request_async`) was preserved and now runs as its own independent async task.

## Performance

| Metric | Old (Ollama) | New (Embedding) |
|--------|-------------|-----------------|
| Cold start | 2-10 seconds | ~1 second (compute 73 embeddings via API) |
| Warm lookup | 2-10 seconds | Under 50ms (cosine similarity only) |
| Emoji coverage | 10 hardcoded | All 73 validated reactions |
| Timeout fallback | Frequent | Rare (only on API key missing) |

## Related Files

| File | Purpose |
|------|---------|
| `tools/emoji_embedding.py` | Embedding index, `EmojiResult` dataclass, standard + custom emoji selection |
| `tools/send_telegram.py` | `--react` flag for agent reactions, `--emoji` flag for standalone emoji messages |
| `bridge/telegram_bridge.py` | Message handler integration |
| `bridge/telegram_relay.py` | Reaction and custom emoji message payload delivery |
| `bridge/response.py` | `set_reaction()` with standard and custom emoji dispatch |
| `data/emoji_embeddings.json` | Disk cache for standard emoji embeddings |
| `data/custom_emoji_embeddings.json` | Disk cache for custom (Premium) emoji embeddings |
| `tests/unit/test_emoji_embedding.py` | Embedding index and EmojiResult tests |
| `tests/unit/test_custom_emoji_index.py` | Custom emoji index building and cache tests |
| `tests/unit/test_send_telegram.py` | Reaction and emoji flag tests |

## See Also

- [Classification](classification.md) -- work-type classification (bug/feature/chore), which is separate from emoji reactions
- [Reaction Semantics](reaction-semantics.md) -- emoji reaction protocol for message delivery feedback
