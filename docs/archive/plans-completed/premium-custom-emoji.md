---
status: Shipped
type: feature
appetite: Medium
owner: Valor
created: 2026-04-04
tracking: https://github.com/tomcounsell/ai/issues/690
last_comment_id:
---

# Premium Custom Emoji for Reactions and Emoji-Only Messages

## Problem

The agent communicates emotion through Telegram emoji reactions selected by embedding similarity. Today, only 73 standard Unicode reaction emojis are available. Telegram Premium accounts unlock thousands of custom emoji (animated sticker-based emoji identified by a `document_id`), but the system cannot use them.

**Current behavior:**
- `set_reaction()` in `bridge/response.py` only constructs `ReactionEmoji(emoticon=emoji)` -- no custom emoji path exists
- `tools/emoji_embedding.py` indexes only the 73 standard Telegram reaction emojis
- `send_telegram --react` resolves feelings to standard emoji only
- When the agent sends an emoji-only message, it can only use standard Unicode -- not the expressive custom emoji available to Premium accounts
- No code queries custom emoji packs or handles `ReactionCustomEmoji` document IDs

**Desired outcome:**
- The agent can react to messages with custom emoji from Premium emoji packs, selected by feeling keyword (same `find_best_emoji()` interface)
- The agent can send a single custom emoji as a standalone message (using `MessageEntityCustomEmoji`)
- When Premium custom emoji are unavailable (non-Premium account, API error), the system silently falls back to standard emoji
- The feeling-to-emoji interface remains transparent to callers -- they never specify standard vs custom

## Prior Art

- **Issue #658**: Embedding-based emoji reactions -- shipped the current 73-emoji embedding system, explicitly deferred Premium custom emoji as out-of-scope for v1
- **PR #677**: Replace Ollama emoji reactions with embedding-based lookup -- merged, implements the current system
- **PR #680**: Fix REACT emoji leak as literal text -- post-ship fix for emoji appearing as text in messages

No prior attempts at custom emoji integration exist. Issue #658 listed it as a deliberate future enhancement.

## Spike Results

### spike-1: Telethon custom emoji API surface
- **Assumption**: "Telethon exposes ReactionCustomEmoji and MessageEntityCustomEmoji classes for setting custom emoji reactions and sending custom emoji in messages"
- **Method**: code-read
- **Finding**: Telethon provides `ReactionCustomEmoji(document_id=int)` in `telethon.tl.types` for reactions via `SendReactionRequest`, and `MessageEntityCustomEmoji(offset, length, document_id)` for inline custom emoji in message text. Both require the document_id (int64) of a custom emoji sticker.
- **Confidence**: high
- **Impact on plan**: Confirms the reaction path is a drop-in alongside `ReactionEmoji`. Message path requires constructing message entities manually.

### spike-2: Discovering available custom emoji packs
- **Assumption**: "We can enumerate custom emoji available to the account via Telethon API"
- **Method**: web-research
- **Finding**: Telethon wraps `messages.getEmojiStickers` (returns all custom emoji sticker sets the user has) and `messages.getStickerSet` (returns individual stickers with document IDs). Each sticker in a custom emoji set has a `document` with an `id` field (the `document_id` needed for reactions/messages). Sticker sets also have associated emoji characters for each sticker that can serve as descriptive metadata.
- **Confidence**: medium (API surface confirmed, but descriptive metadata quality varies by pack)
- **Impact on plan**: Indexing strategy should extract the associated emoji character + sticker set title as labels for embedding. May need manual label curation for better quality.

## Data Flow

### Reaction flow (extended)

1. **Entry point**: `find_best_emoji(feeling)` called by bridge or `send_telegram --react`
2. **Embedding lookup**: Query embedding computed, compared against both standard and custom emoji embeddings via cosine similarity
3. **Result type detection**: Best match is either a standard emoji (string) or a custom emoji (document_id int)
4. **set_reaction()**: Constructs `ReactionEmoji(emoticon=str)` for standard or `ReactionCustomEmoji(document_id=int)` for custom
5. **Telethon**: `SendReactionRequest` sent to Telegram API
6. **Fallback**: If custom emoji reaction fails (non-Premium, API error), retry with best standard emoji match

### Emoji-only message flow (new)

1. **Entry point**: Agent calls `send_telegram --emoji "excited"` (new flag)
2. **Embedding lookup**: Resolves feeling to best custom emoji (prefers custom over standard for messages)
3. **Message construction**: Builds message text with a placeholder character and `MessageEntityCustomEmoji(offset=0, length=N, document_id=id)` entity
4. **Queue**: Payload queued to Redis outbox with `type: "custom_emoji_message"` and document_id
5. **Relay**: `telegram_relay.py` sends message with entity via Telethon `send_message(entities=[...])`

### Index building flow (new)

1. **Trigger**: First bridge start after cache miss, or manual `rebuild_custom_emoji_index()`
2. **API call**: `messages.getEmojiStickers` to get all custom emoji sticker sets
3. **Sticker enumeration**: For each sticker set, extract sticker document IDs, associated emoji characters, and set title
4. **Label generation**: Compose descriptive labels from associated emoji + set title (e.g., "party celebration confetti" for a party popper custom emoji)
5. **Embedding**: Compute embeddings for each label via OpenRouter (same as standard emoji)
6. **Cache**: Save to `data/custom_emoji_embeddings.json` with document_id as key

## Architectural Impact

- **New data file**: `data/custom_emoji_embeddings.json` -- cached embeddings for custom emoji, keyed by document_id (string representation of int64)
- **Interface changes**: `find_best_emoji()` return type changes from `str` to `EmojiResult` (a simple dataclass with `emoji: str | None`, `document_id: int | None`, `is_custom: bool`). Callers that only need the string representation can use `str(result)` or `result.display`.
- **Backward compatibility**: The `EmojiResult` class provides a `__str__` method returning the standard emoji string (or a placeholder for custom), so existing string consumers continue to work
- **Coupling**: `set_reaction()` gains awareness of custom vs standard emoji, but the decision is made by the embedding module -- reaction code just dispatches
- **New dependency**: No new Python packages. Uses existing Telethon API classes already available in the authenticated client.
- **Reversibility**: Easy -- remove custom emoji cache file and `EmojiResult` dataclass, revert `set_reaction()` to standard-only. Standard emoji path is preserved as-is.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (review custom emoji label quality, confirm UX for emoji-only messages)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telegram Premium account | `python -c "print('Manual check: Telethon client is Premium')"` | Custom emoji API access |
| OpenRouter API key | `python -c "import os; assert os.environ.get('OPENROUTER_API_KEY')"` | Embedding computation for custom emoji labels |

## Solution

### Key Elements

- **EmojiResult dataclass** (`tools/emoji_embedding.py`): Return type for `find_best_emoji()` that carries both standard emoji string and custom emoji document_id, with `is_custom` flag. Provides `__str__` for backward compatibility.
- **Custom emoji indexer** (`tools/emoji_embedding.py`): Queries Telethon API for available custom emoji packs, extracts document IDs and descriptive labels, computes embeddings, caches to `data/custom_emoji_embeddings.json`.
- **Unified embedding search**: `find_best_emoji()` searches both standard and custom emoji embeddings, returning the best overall match as an `EmojiResult`.
- **Extended set_reaction()** (`bridge/response.py`): Accepts `EmojiResult` or string. For custom emoji, uses `ReactionCustomEmoji(document_id=...)`. Falls back to standard on failure.
- **Emoji-only message support** (`tools/send_telegram.py`): New `--emoji` flag that sends a custom emoji as a standalone message using `MessageEntityCustomEmoji`.
- **Graceful degradation**: All custom emoji paths catch failures and fall back to the best standard emoji match.

### Flow

**Agent reacts** -> `find_best_emoji("excited")` -> searches standard + custom embeddings -> returns `EmojiResult(is_custom=True, document_id=12345)` -> `set_reaction()` uses `ReactionCustomEmoji` -> if fails, retries with best standard emoji

**Agent sends emoji** -> `send_telegram --emoji "celebration"` -> `find_best_emoji("celebration")` -> queues custom emoji message payload -> relay sends with `MessageEntityCustomEmoji` entity

**Bridge reaction** -> message arrives -> `find_best_emoji_for_message(text)` -> returns `EmojiResult` -> `set_reaction()` dispatches appropriately

### Technical Approach

- `EmojiResult` is a lightweight dataclass, not a breaking change. `str(result)` returns the emoji character for standard or a Unicode placeholder for custom. This preserves all existing call sites.
- Custom emoji index is built lazily on first use (same pattern as standard emoji cache). Rebuilt if cache file is missing. Manual rebuild via `rebuild_custom_emoji_index(client)` requires a Telethon client instance.
- The Telethon client is passed to the indexer at bridge startup. The indexer is async (needs `await` for API calls). Standard emoji indexing remains synchronous.
- Custom emoji embeddings are stored separately from standard emoji embeddings (`data/custom_emoji_embeddings.json` vs `data/emoji_embeddings.json`) to allow independent cache invalidation.
- The unified search loads both caches and does a single cosine similarity sweep across all entries. Standard emoji entries use emoji string as key; custom emoji entries use `"custom:{document_id}"` as key.
- For the `--emoji` flag in `send_telegram.py`, the relay needs to handle a new payload type `"custom_emoji_message"` that constructs the message with entities.
- Premium detection: attempt the custom emoji API call at startup. If it fails with an auth/Premium error, disable custom emoji for the session and log a warning. All subsequent lookups return standard emoji only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `set_reaction()` with custom emoji: If `ReactionCustomEmoji` raises (non-Premium, invalid document_id), catch and retry with best standard emoji. Test asserts fallback occurs and standard reaction is set.
- [x] Custom emoji indexer API failure: If `getEmojiStickers` fails, log warning and return empty custom index. Test asserts standard-only search works when custom index is empty.
- [x] Relay custom emoji message send failure: If `send_message` with `MessageEntityCustomEmoji` fails, fall back to sending the emoji character as plain text. Test asserts fallback message sent.

### Empty/Invalid Input Handling
- [x] `find_best_emoji("")` still returns default thinking emoji (unchanged behavior)
- [x] `find_best_emoji("excited")` with empty custom emoji cache returns best standard emoji
- [x] `--emoji ""` exits with clear error message
- [x] Custom emoji index with zero sticker sets returns empty dict (no crash)

### Error State Rendering
- [x] When custom emoji reaction fails and falls back to standard, the user sees a standard reaction (not nothing)
- [x] When emoji-only message fails, user sees a plain-text emoji message (not an error)

## Test Impact

- [x] `tests/unit/test_emoji_embedding.py::test_find_best_emoji_returns_string` -- UPDATE: assert returns `EmojiResult` with `__str__` compatibility
- [x] `tests/unit/test_emoji_embedding.py::test_find_best_emoji_fallback` -- UPDATE: assert returns `EmojiResult` with standard emoji on failure
- [x] `tests/unit/test_emoji_embedding.py::test_find_best_emoji_for_message` -- UPDATE: assert returns `EmojiResult`
- [x] `tests/unit/test_send_telegram.py::test_reaction_mode` -- UPDATE: handle `EmojiResult` in reaction payload
- [x] `tests/unit/test_delivery_execution.py` -- UPDATE: `set_reaction` mock may need to accept `EmojiResult` or string

## Rabbit Holes

- **LLM-powered label generation for custom emoji**: Tempting to use an LLM to describe each custom emoji sticker for better embedding quality. The associated emoji character + set title is good enough for v1. Manual label curation can follow.
- **Custom emoji pack management UI**: Do not build a UI for selecting/managing emoji packs. The system indexes whatever packs the account has.
- **Animated emoji rendering in logs**: Custom emoji are animated stickers. Do not try to render or preview them in logs -- just log the document_id.
- **Sticker set auto-installation**: Do not auto-install popular custom emoji packs. Use only what the account already has.
- **Fine-tuning embedding model on emoji data**: Pre-trained `text-embedding-3-small` is sufficient. Do not train custom models.

## Risks

### Risk 1: Custom emoji API rate limits
**Impact:** Indexing many emoji packs could hit Telegram API rate limits, slowing bridge startup.
**Mitigation:** Cache aggressively (rebuild only when cache file is missing or manually triggered). Index at most once per bridge restart. Use exponential backoff on API calls.

### Risk 2: Poor custom emoji label quality
**Impact:** Associated emoji characters and set titles may not produce good embeddings, leading to irrelevant custom emoji being selected.
**Mitigation:** Start with the associated data. If quality is poor, add a manual label override file (`data/custom_emoji_labels.json`) that maps document_ids to curated descriptions. This is a follow-up enhancement, not a blocker.

### Risk 3: Custom emoji not supported in all chats
**Impact:** Custom emoji reactions may fail in group chats where the feature is restricted, or with non-Premium users.
**Mitigation:** `set_reaction()` already catches failures. The fallback to standard emoji handles this transparently. Log the failure reason for debugging.

## Race Conditions

### Race 1: Custom emoji index not ready when first message arrives
**Location:** `tools/emoji_embedding.py` custom index loading
**Trigger:** Bridge starts, message arrives before async custom emoji indexing completes
**Data prerequisite:** Custom emoji embeddings must be loaded before `find_best_emoji()` can return custom results
**State prerequisite:** Telethon client must be connected and authenticated
**Mitigation:** Standard emoji index loads synchronously (existing behavior, unchanged). Custom emoji index loads asynchronously in background. Until custom index is ready, `find_best_emoji()` returns standard emoji only. No blocking, no waiting. Custom emoji become available once the background task completes.

## No-Gos (Out of Scope)

- Custom emoji in formatted text messages (only standalone emoji-only messages, not inline in paragraphs)
- Custom emoji in message reactions from other users (only the agent's own reactions)
- Auto-installing or recommending custom emoji packs
- Custom emoji search by visual appearance (only by feeling/text similarity)
- Changing the existing 73 standard emoji labels or embeddings
- Building a custom emoji management interface

## Update System

The update script needs to handle the new `data/custom_emoji_embeddings.json` cache file. However, this file is generated lazily at runtime (same as the existing `data/emoji_embeddings.json`), so no propagation is needed. The file will be created automatically on first bridge start after update.

No update system changes required -- the new cache file is auto-generated at runtime, and no new dependencies or config files are introduced.

## Agent Integration

- **`send_telegram` tool extension**: The new `--emoji` flag is automatically available to the agent since `send_telegram` is already registered as a Bash tool. No MCP server changes needed.
- **Relay changes**: `bridge/telegram_relay.py` needs to handle two new payload types: custom emoji reactions (handled transparently via existing `set_reaction()` path since the emoji field carries the `EmojiResult`) and `type: "custom_emoji_message"` payloads for standalone custom emoji messages. This is bridge-internal.
- **Bridge changes**: `bridge/telegram_bridge.py` already calls `find_best_emoji_for_message()` -- the return type change to `EmojiResult` propagates automatically to `set_reaction()`.
- **Integration test**: Verify that `python tools/send_telegram.py --emoji "happy"` queues the correct payload structure in Redis, and that `--react "happy"` can produce custom emoji payloads when custom index is available.
- No MCP server changes needed.

## Documentation

- [x] Update `docs/features/emoji-embedding-reactions.md` to cover custom emoji support, the `EmojiResult` type, `--emoji` flag, and graceful degradation behavior
- [x] Add custom emoji section to `docs/features/README.md` index table (or update existing emoji entry)
- [x] Update inline docstrings in `tools/emoji_embedding.py` (new `EmojiResult` class, updated `find_best_emoji()` return type)
- [x] Update inline docstrings in `bridge/response.py` (`set_reaction()` custom emoji support)
- [x] Update inline docstrings in `tools/send_telegram.py` (new `--emoji` flag)
- [x] Update `docs/tools-reference.md` to document `--emoji` flag for `send_telegram`

## Success Criteria

- [x] `find_best_emoji("excited")` returns an `EmojiResult` that may be standard or custom
- [x] `str(find_best_emoji("excited"))` returns a valid emoji string (backward compatible)
- [x] `set_reaction()` can set both standard and custom emoji reactions
- [x] Custom emoji reactions fall back to standard emoji on failure without errors
- [x] `send_telegram --emoji "celebration"` queues a custom emoji message payload
- [x] Custom emoji index is cached to `data/custom_emoji_embeddings.json`
- [x] Standard emoji behavior is completely unchanged when custom index is empty
- [x] `send_telegram --react "feeling"` works with both standard and custom emoji transparently
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (emoji-index)**
  - Name: emoji-index-builder
  - Role: Implement EmojiResult dataclass, custom emoji indexer, unified embedding search
  - Agent Type: builder
  - Resume: true

- **Builder (reaction-dispatch)**
  - Name: reaction-builder
  - Role: Extend set_reaction() for custom emoji, update relay, add --emoji flag to send_telegram
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Verify all success criteria, run tests, check integration
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build EmojiResult and custom emoji indexer
- **Task ID**: build-emoji-index
- **Depends On**: none
- **Validates**: tests/unit/test_emoji_embedding.py (update), tests/unit/test_custom_emoji_index.py (create)
- **Informed By**: spike-1 (confirmed: ReactionCustomEmoji API), spike-2 (confirmed: getEmojiStickers API)
- **Assigned To**: emoji-index-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `EmojiResult` dataclass in `tools/emoji_embedding.py` with `emoji`, `document_id`, `is_custom`, `__str__` method
- Add `async def build_custom_emoji_index(client) -> dict[str, list[float]]` that queries Telethon for custom emoji packs, extracts labels, computes embeddings
- Add custom emoji cache load/save to `data/custom_emoji_embeddings.json`
- Update `find_best_emoji()` to return `EmojiResult` and search both standard and custom embeddings
- Update `find_best_emoji_for_message()` to return `EmojiResult`
- Create `tests/unit/test_custom_emoji_index.py` testing index building (mocked Telethon), cache round-trip, unified search
- Update `tests/unit/test_emoji_embedding.py` for `EmojiResult` return type

### 2. Extend set_reaction and relay for custom emoji
- **Task ID**: build-reaction-dispatch
- **Depends On**: build-emoji-index
- **Validates**: tests/unit/test_send_telegram.py (update), tests/unit/test_delivery_execution.py (update)
- **Assigned To**: reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `set_reaction()` in `bridge/response.py` to accept `EmojiResult | str`, use `ReactionCustomEmoji(document_id=...)` for custom, fall back to standard on failure
- Add `--emoji` flag to `tools/send_telegram.py` for standalone custom emoji messages
- Add `type: "custom_emoji_message"` handler in `bridge/telegram_relay.py` that sends with `MessageEntityCustomEmoji` entity
- Update bridge handler (`select_and_set_emoji_reaction`) to pass `EmojiResult` to `set_reaction()`
- Update `tests/unit/test_send_telegram.py` with `--emoji` flag tests
- Update `tests/unit/test_delivery_execution.py` if set_reaction mocks need adjustment

### 3. Add graceful degradation and Premium detection
- **Task ID**: build-degradation
- **Depends On**: build-emoji-index
- **Validates**: tests/unit/test_custom_emoji_index.py (update)
- **Assigned To**: reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Add Premium detection at bridge startup: attempt custom emoji API call, disable custom index on auth failure
- Add fallback in `set_reaction()`: if `ReactionCustomEmoji` raises, retry with best standard emoji from the same query
- Add fallback in relay: if custom emoji message send fails, send plain text emoji character
- Test all degradation paths

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-reaction-dispatch, build-degradation
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify `EmojiResult` backward compatibility: `str(find_best_emoji("excited"))` returns a string
- Verify standard emoji path unchanged when custom index is empty
- Run lint and format checks

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: final-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/emoji-embedding-reactions.md` with custom emoji section
- Update `docs/features/README.md` index
- Update `docs/tools-reference.md` for `--emoji` flag
- Update inline docstrings

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| EmojiResult exists | `python -c "from tools.emoji_embedding import EmojiResult"` | exit code 0 |
| Backward compat | `python -c "from tools.emoji_embedding import find_best_emoji; print(str(find_best_emoji('test')))"` | exit code 0 |
| Custom cache path | `python -c "from tools.emoji_embedding import CUSTOM_CACHE_PATH; print(CUSTOM_CACHE_PATH)"` | output contains custom_emoji |
| --emoji flag exists | `python tools/send_telegram.py --help` | output contains --emoji |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Custom emoji label quality**: ✅ Resolved — use auto-generated labels (associated emoji + set title) for v1. Manual curation deferred to follow-up if quality is poor.
2. **Custom emoji index refresh**: ✅ Resolved — rebuild only when cache file is missing (manual deletion or first run). Periodic refresh deferred.
3. **Preference weighting**: ✅ Resolved — standard emoji is the default. Custom emoji wins only when similarity score exceeds the standard match by a clear margin (e.g., 0.05+ delta). This keeps behavior conservative.
