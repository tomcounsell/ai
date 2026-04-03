# Emoji Embedding Reactions

Embedding-based emoji reaction selection for Telegram messages, replacing the previous Ollama intent classification approach.

## Overview

When a Telegram message arrives, the bridge selects a contextual reaction emoji by embedding the message text and comparing it against pre-computed embeddings for all 73 validated Telegram reaction emojis via cosine similarity. This replaces the previous Ollama-based intent classifier that mapped messages to 10 hardcoded emojis with 2-10 second latency.

The same embedding index powers the `send_telegram --react` flag, allowing the agent to set emoji reactions on messages by describing a feeling word.

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

## Components

### Emoji Embedding Index (`tools/emoji_embedding.py`)

The core module that maps feelings to emojis.

**Key functions:**
- `find_best_emoji(feeling: str) -> str` -- embeds a feeling word and returns the nearest emoji by cosine similarity
- `find_best_emoji_for_message(text: str) -> str` -- extracts a 100-char snippet from a message and delegates to `find_best_emoji`
- `clear_cache()` -- clears the in-memory cache (for testing)

**Emoji labels:** Each of the 73 validated Telegram reaction emojis has a descriptive label string (e.g., "fire, hot, trending, lit, exciting, impressive, awesome" for the fire emoji). These labels are embedded and compared against input text.

**Caching:** Embeddings are computed lazily on first use via OpenRouter (`text-embedding-3-small`) and cached to `data/emoji_embeddings.json`. On subsequent bridge starts, embeddings are loaded from disk with no API call needed.

**Fallback:** If the embedding API is unavailable, the API key is not set, or any error occurs, the default thinking emoji is returned.

### send_telegram Reaction Flag (`tools/send_telegram.py`)

```bash
# React to the current message with a feeling
python tools/send_telegram.py --react "excited"
python tools/send_telegram.py --react "great work"
python tools/send_telegram.py --react "thinking"
```

The `--react` flag:
- Requires `TELEGRAM_REPLY_TO` (must have a message to react to)
- Resolves the feeling word to an emoji via `find_best_emoji()`
- Queues a reaction payload to the Redis outbox for relay delivery
- Exits with an error if `TELEGRAM_REPLY_TO` is not set or the feeling is empty

### Relay Reaction Handler (`bridge/telegram_relay.py`)

The relay detects payloads with `"type": "reaction"` and calls `set_reaction()` via the Telethon client. Failed reactions are logged and skipped (no re-queue).

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
| `tools/emoji_embedding.py` | Embedding index and emoji selection |
| `tools/send_telegram.py` | `--react` flag for agent reactions |
| `bridge/telegram_bridge.py` | Message handler integration |
| `bridge/telegram_relay.py` | Reaction payload delivery |
| `bridge/response.py` | Reaction helpers (legacy intent code removed) |
| `data/emoji_embeddings.json` | Disk cache for computed embeddings |
| `tests/unit/test_emoji_embedding.py` | Embedding index tests |
| `tests/unit/test_send_telegram.py` | Reaction flag tests |

## See Also

- [Classification](classification.md) -- work-type classification (bug/feature/chore), which is separate from emoji reactions
- [Reaction Semantics](reaction-semantics.md) -- emoji reaction protocol for message delivery feedback
