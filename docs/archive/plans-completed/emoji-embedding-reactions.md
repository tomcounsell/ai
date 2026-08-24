---
status: Merged
type: feature
appetite: Medium
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/658
last_comment_id:
---

# Embedding-Based Emoji Reactions

## Problem

When a Telegram message arrives, the bridge sets a reaction emoji to indicate processing status. Currently this uses a local Ollama LLM to classify message intent (search, code, chat, etc.) and maps that to one of 10 hardcoded emojis via `INTENT_REACTIONS`.

**Current behavior:**
- Ollama classification takes 2-10 seconds and frequently times out, falling back to the default thinking emoji
- Most messages get the same reaction regardless of content
- Only 10 of 73 validated standard Telegram reactions are used
- Premium custom emoji reactions are completely unused
- `send_telegram` tool can only send text and files -- no reactions, no emoji-only messages
- Incoming messages are never marked as read

**Desired outcome:**
- Emoji reactions selected via embedding cosine similarity in under 50ms (after initial computation)
- All 73 validated standard reactions are available; Premium accounts additionally use custom emoji packs
- The agent can react to messages and send emoji-only messages through `send_telegram` by specifying a feeling word
- Messages are marked as read on receipt
- Ollama intent classifier removed from the reaction path

## Prior Art

No prior issues or PRs found related to embedding-based emoji selection. PR #4 (Enhanced Telegram Security) touched early reaction handling but is not relevant to this work.

## Data Flow

### Current flow (being replaced)
1. **Message arrives** in `bridge/telegram_bridge.py` handler (~line 771)
2. **Dedup check** passes, message is stored
3. **Eyes reaction** set immediately (`REACTION_RECEIVED`)
4. **`classify_and_update_reaction()`** fires as `asyncio.create_task` (~line 1035):
   - Calls `get_processing_emoji_async()` which runs Ollama in executor thread
   - Ollama classifies intent (2-10s, 10s timeout)
   - Maps intent string to emoji via `INTENT_REACTIONS` dict
   - Updates reaction to classified emoji
   - Also runs `classify_request_async()` from `tools/classifier.py` for work-type classification
5. **Session enqueued** with `classification_result` dict

### New flow
1. **Message arrives** in handler
2. **Dedup check** passes, message is stored
3. **`mark_read()`** called immediately
4. **Eyes reaction** set immediately
5. **Embedding-based emoji** selected in under 50ms from pre-computed index, reaction updated
6. **Work-type classification** (`classify_request_async`) runs separately as its own task
7. **Session enqueued** with classification result

### send_telegram reaction flow (new)
1. Agent calls `python tools/send_telegram.py --react "excited"` 
2. Tool embeds "excited", finds nearest emoji via cosine similarity from index
3. Queues a reaction payload `{type: "reaction", emoji: "...", ...}` to Redis outbox
4. `bridge/telegram_relay.py` detects reaction payload, calls `set_reaction()` via Telethon

## Architectural Impact

- **New module**: `tools/emoji_embedding.py` -- the embedding index (lazy-initialized, cached to disk)
- **Interface changes**: `send_telegram.py` gains `--react` flag; `telegram_relay.py` gains reaction message type
- **Coupling reduction**: Removes dependency on Ollama for reaction selection; removes `intent/` module entirely (sole caller is `get_processing_emoji`)
- **Data ownership**: Emoji embedding index owned by `tools/emoji_embedding.py`, consumed by both bridge and send_telegram tool
- **Reversibility**: Easy -- revert to hardcoded `REACTION_PROCESSING` default if embedding approach fails

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on Premium emoji handling)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| OpenRouter API key (for embeddings) | `python -c "from utils.api_keys import get_openrouter_api_key; assert get_openrouter_api_key()"` | Embedding computation via `text-embedding-3-small` |

No new dependencies required -- reuses existing OpenRouter embedding infrastructure from `tools/knowledge_search/`.

## Solution

### Key Elements

- **Emoji embedding index** (`tools/emoji_embedding.py`): Pre-computes embeddings for descriptive labels of all 73 validated reactions. Caches to `data/emoji_embeddings.json`. Provides `find_best_emoji(feeling: str) -> str` with sub-50ms lookup.
- **Bridge integration**: Replace `classify_and_update_reaction()` with a lightweight async call to the embedding index. Separate work-type classification into its own task.
- **send_telegram reaction support**: Add `--react <feeling>` flag that queues a reaction payload. Add emoji-message support via feeling-to-emoji resolution.
- **Relay extension**: `telegram_relay.py` handles new `type: "reaction"` payloads by calling `set_reaction()`.
- **Mark read**: Single `await event.message.mark_read()` call after dedup check.
- **Cleanup**: Remove `INTENT_REACTIONS`, `get_processing_emoji`, `get_processing_emoji_async` from `bridge/response.py`. Remove `intent/__init__.py` module entirely.

### Flow

**Message arrives** -> mark_read() -> eyes reaction -> embed message snippet -> cosine lookup -> update reaction -> enqueue session

**Agent reacts** -> `send_telegram --react "excited"` -> embed "excited" -> cosine lookup -> queue reaction payload -> relay sends reaction

### Technical Approach

- Emoji labels are short descriptive phrases per emoji (e.g., "fire, excitement, impressive, hot" for the fire emoji). These are hardcoded in the module since the set of 73 validated reactions is static and well-known.
- Embeddings computed lazily on first use, then cached to `data/emoji_embeddings.json`. On subsequent bridge starts, load from cache (no API call needed).
- For message-to-emoji matching: extract a short sentiment/topic snippet from the message (first 100 chars), embed it, find nearest emoji by cosine similarity.
- For `--react` flag: embed the feeling word directly, find nearest emoji.
- Premium custom emoji support is deferred to a follow-up (see No-Gos). The index starts with the 73 standard reactions only.
- Reuse `_compute_embedding` and `_cosine_similarity` from `tools/knowledge_search/__init__.py` by extracting them to a shared utility or importing directly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `tools/emoji_embedding.py`: If embedding API fails, fall back to `REACTION_PROCESSING` ("thinking" emoji). Test asserts fallback emoji is returned on API error.
- [x] `bridge/telegram_relay.py`: If `set_reaction()` fails for a queued reaction, log warning and skip (do not re-queue reaction failures). Test asserts no crash on reaction failure.
- [x] `tools/send_telegram.py`: If `--react` is used without `TELEGRAM_REPLY_TO`, exit with clear error. Test asserts error message.

### Empty/Invalid Input Handling
- [x] `find_best_emoji("")` returns default thinking emoji
- [x] `find_best_emoji(None)` returns default thinking emoji
- [x] `--react` with empty string exits with error

### Error State Rendering
- [x] If emoji embedding cache file is corrupted/missing, rebuild transparently on next call
- [x] If relay receives malformed reaction payload, log and skip (no crash)

## Test Impact

- [x] `tests/unit/test_send_telegram.py` -- UPDATE: Add test cases for `--react` flag validation, reaction payload queuing, and emoji-message mode
- [x] `tests/unit/test_intake_classifier.py` -- No changes needed (tests `tools/classifier.py` message intent, not Ollama intent)
- [x] `tests/unit/test_intent_classifier.py` -- No changes needed (tests `agent/intent_classifier.py` teammate/work routing, not the `intent/` module)

No existing tests directly test the `intent/__init__.py` module or `get_processing_emoji` -- those are untested code paths being removed.

## Rabbit Holes

- **Custom emoji ID discovery**: Querying `messages.getAvailableReactions` for Premium custom emoji packs involves sticker pack enumeration, numeric ID extraction, and account-type detection. This is a separate feature.
- **Sophisticated message summarization**: Tempting to build an LLM-powered message summarizer for better emoji matching. The simple "first 100 chars" approach is good enough for reaction selection.
- **Emoji embedding fine-tuning**: Pre-trained embeddings from `text-embedding-3-small` are sufficient. Do not fine-tune or train custom emoji embeddings.
- **Shared embedding utility refactor**: Extracting `_compute_embedding` and `_cosine_similarity` into a shared `utils/embeddings.py` is clean but not required. Import directly from `tools/knowledge_search/` for now; refactor later if a third consumer appears.

## Risks

### Risk 1: Embedding API latency on cold start
**Impact:** First message after bridge restart could be slow (API call to compute 73 embeddings).
**Mitigation:** Cache embeddings to disk (`data/emoji_embeddings.json`). Only recompute if cache is missing. The 73 label embeddings are static -- cache never goes stale.

### Risk 2: Poor emoji selection quality
**Impact:** Embedding cosine similarity may select irrelevant emojis for some messages.
**Mitigation:** The bar is low -- current system maps most messages to the same default emoji. Any variety is an improvement. Labels can be tuned iteratively without code changes.

### Risk 3: Relay backward compatibility
**Impact:** Old relay code receiving new reaction payloads could crash.
**Mitigation:** Relay already skips unknown/malformed payloads with a warning log. New payload type (`type: "reaction"`) will be ignored by old relay code until the update propagates.

## Race Conditions

### Race 1: Work-type classification result not ready at enqueue time
**Location:** `bridge/telegram_bridge.py` ~line 1055-1070
**Trigger:** `classify_request_async` task hasn't completed by the time session is enqueued.
**Data prerequisite:** `classification_result` dict must be populated before `enqueue_agent_session` reads it.
**State prerequisite:** Async task must have written to the mutable dict.
**Mitigation:** This race already exists today and is handled by the fast-path regex for PR/issue references (lines 1057-1068). The new code preserves this exact pattern: emoji selection runs synchronously (fast), work-type classification runs as a separate async task with the same fast-path override.

## No-Gos (Out of Scope)

- Premium custom emoji reactions (requires sticker pack enumeration, account-type detection -- separate issue)
- Changing the work-type classifier (`tools/classifier.py`) -- preserved as-is
- Building a message summarizer for better emoji matching -- use raw message text
- Refactoring embedding utilities into a shared module -- import from knowledge_search directly
- Changing any other Ollama usage outside the reaction path (e.g., `config/models.py` references)

## Update System

No update system changes required. The new `tools/emoji_embedding.py` module and `data/emoji_embeddings.json` cache are auto-created on first use. The bridge restart after update will pick up the new code. No new config files, environment variables, or dependencies need propagation.

## Agent Integration

- **`send_telegram` tool extension**: The `--react` flag is automatically available to the agent since `send_telegram` is already registered as a Bash tool. No MCP server changes needed.
- **Relay changes**: `bridge/telegram_relay.py` needs to handle the new `type: "reaction"` payload format. This is bridge-internal and does not require MCP registration.
- **Bridge changes**: `bridge/telegram_bridge.py` imports the new `tools/emoji_embedding.py` module directly for reaction selection. No MCP involvement.
- **Integration test**: Verify that `python tools/send_telegram.py --react "happy"` queues the correct payload structure in Redis.

## Documentation

- [x] Create `docs/features/emoji-embedding-reactions.md` describing the embedding-based reaction system, emoji label format, cache behavior, and send_telegram --react usage
- [x] Add entry to `docs/features/README.md` index table
- [x] Update `docs/features/classification.md` to remove references to Ollama intent classification for reactions
- [x] Update inline docstrings in `bridge/response.py` (remove intent classification references), `tools/send_telegram.py` (document --react flag)

## Success Criteria

- [x] Incoming messages are marked as read immediately after dedup check
- [x] Emoji embedding index maps all 73 validated reactions to feeling-word embeddings
- [x] `find_best_emoji("excited")` returns a sensible emoji in under 50ms (after cache warm)
- [x] `classify_and_update_reaction()` no longer calls Ollama -- uses embedding lookup
- [x] `classify_request_async` (work-type) continues to function independently
- [x] `send_telegram --react "thinking"` queues a reaction payload and relay delivers it
- [x] `INTENT_REACTIONS`, `get_processing_emoji`, `get_processing_emoji_async` removed from `bridge/response.py`
- [x] `intent/__init__.py` module removed (no other callers exist)
- [x] All existing tests pass
- [x] New tests cover embedding selection, fallback behavior, and send_telegram --react flag
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (emoji-index)**
  - Name: emoji-builder
  - Role: Build emoji embedding index module and integrate into bridge
  - Agent Type: builder
  - Resume: true

- **Builder (send-telegram)**
  - Name: telegram-builder
  - Role: Extend send_telegram tool and relay with reaction support
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove legacy Ollama intent code, add mark_read, separate work-type classification
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Verify all success criteria, run tests, check integration
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build emoji embedding index
- **Task ID**: build-emoji-index
- **Depends On**: none
- **Validates**: tests/unit/test_emoji_embedding.py (create)
- **Assigned To**: emoji-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/emoji_embedding.py` with:
  - `EMOJI_LABELS` dict mapping each of 73 validated emojis to descriptive phrase(s)
  - `_load_or_compute_embeddings()` that checks `data/emoji_embeddings.json` cache, computes via OpenRouter if missing
  - `find_best_emoji(feeling: str) -> str` that embeds the feeling and returns the nearest emoji by cosine similarity
  - `find_best_emoji_for_message(text: str) -> str` that extracts a short snippet and delegates to `find_best_emoji`
  - Fallback to `REACTION_PROCESSING` on any failure
- Import `_compute_embedding` and `_cosine_similarity` from `tools/knowledge_search`
- Create `tests/unit/test_emoji_embedding.py` testing:
  - Label completeness (all 73 validated emojis have labels)
  - Fallback on empty/None input
  - Fallback on embedding API failure (mocked)
  - Cache load/save round-trip

### 2. Extend send_telegram and relay with reaction support
- **Task ID**: build-send-telegram
- **Depends On**: build-emoji-index
- **Validates**: tests/unit/test_send_telegram.py (update)
- **Assigned To**: telegram-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--react <feeling>` argument to `tools/send_telegram.py`:
  - Resolves feeling to emoji via `find_best_emoji()`
  - Queues payload with `"type": "reaction"` and resolved emoji
  - Requires `TELEGRAM_REPLY_TO` -- exit with error if not set
- Add reaction handling to `bridge/telegram_relay.py`:
  - Detect `type: "reaction"` in payload
  - Call `set_reaction()` (imported from `bridge/response.py`) via Telethon client
  - Log success/failure, do not re-queue failed reactions
- Update `tests/unit/test_send_telegram.py` with reaction flag tests

### 3. Integrate into bridge and clean up legacy code
- **Task ID**: build-bridge-integration
- **Depends On**: build-emoji-index
- **Validates**: tests/unit/test_bridge_emoji.py (create)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `await event.message.mark_read()` after dedup check in handler (~line 771)
- Replace `classify_and_update_reaction()` body:
  - Use `find_best_emoji_for_message(clean_text)` for emoji selection (fast, sync-safe)
  - Move `classify_request_async` call into a separate `asyncio.create_task` 
  - Preserve fast-path PR/issue regex classification
- Remove from `bridge/response.py`:
  - `INTENT_REACTIONS` dict
  - `get_processing_emoji()` function
  - `get_processing_emoji_async()` function
- Remove `intent/__init__.py` module entirely (confirmed: sole caller is `get_processing_emoji`)
- Create `tests/unit/test_bridge_emoji.py` testing the new reaction selection path

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-send-telegram, build-bridge-integration
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify `INTENT_REACTIONS` and `get_processing_emoji` are gone: `grep -r "INTENT_REACTIONS\|get_processing_emoji" bridge/ intent/` returns nothing
- Verify `intent/__init__.py` is deleted
- Verify `classify_request_async` still exists in `tools/classifier.py`
- Verify `mark_read` call exists in bridge handler
- Run lint and format checks

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: final-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/emoji-embedding-reactions.md`
- Update `docs/features/README.md` index
- Update `docs/features/classification.md` references

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Legacy code removed | `grep -r "INTENT_REACTIONS\|get_processing_emoji" bridge/ intent/ 2>/dev/null` | exit code 1 |
| Intent module removed | `test ! -f intent/__init__.py` | exit code 0 |
| Work-type classifier preserved | `grep -l "classify_request_async" tools/classifier.py` | exit code 0 |
| Emoji index module exists | `python -c "from tools.emoji_embedding import find_best_emoji"` | exit code 0 |
| mark_read in bridge | `grep -c "mark_read" bridge/telegram_bridge.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Should the emoji labels be community-sourced or curated manually? The plan assumes manual curation of 73 label strings -- this is a one-time effort but quality matters for selection accuracy.
2. For the `--react` flag: should the relay record reaction message IDs on the session (like it does for text messages), or are reactions fire-and-forget?
