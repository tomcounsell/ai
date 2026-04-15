---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/975
last_comment_id: null
---

# Terminal Emoji Upgrade — Semantic Reactions via find_best_emoji

## Problem

The three fixed terminal reactions applied at the end of every message handling cycle are hardcoded plain Unicode strings in `agent/constants.py`. These were chosen before semantic emoji lookup existed in the codebase. Now that `find_best_emoji()` is available and the `set_reaction()` call site already handles `EmojiResult`, there is no reason to keep hand-picking emojis.

**Current behavior:**
- `REACTION_SUCCESS = "👍"` — Silent ack, no text reply sent. 👍 is the most overloaded emoji in Telegram; indistinguishable from human thumbs-up reactions.
- `REACTION_COMPLETE = "🏆"` — Work done, text reply attached. 🏆 reads as "you won a trophy" rather than "task completed."
- `REACTION_ERROR = "😱"` — Something went wrong. 😱 reads as panic, not a calm operational signal.

**Desired outcome:** Terminal reactions are chosen semantically using `find_best_emoji()`, cached at process startup, and fall back gracefully when the API is unavailable. Constants become `EmojiResult` objects so Premium custom emoji are automatically considered. No explicit `TELEGRAM_PREMIUM_ENABLED` flag needed — the existing `CUSTOM_EMOJI_DELTA` threshold handles the standard-vs-custom decision.

## Freshness Check

**Baseline commit:** `71e32c49`
**Issue filed at:** 2026-04-15T00:32:41Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/constants.py:10-12` — Still holds. Three hardcoded Unicode strings, no EmojiResult.
- `agent/agent_session_queue.py:4122-4126` — Still holds. Terminal dispatch with direct str constants.
- `bridge/response.py:759` — Still holds. `set_reaction(str | EmojiResult | None)` — EmojiResult path fully wired.
- `tools/emoji_embedding.py:240` — Still holds. `find_best_emoji(feeling: str) -> EmojiResult` implemented.
- `data/custom_emoji_embeddings.json` — Does not exist on this machine. Confirmed absence at plan time.

**Cited sibling issues/PRs re-checked:**
- PR #691 (Premium custom emoji infrastructure) — Merged 2026-04-04. EmojiResult + set_reaction are the result.
- PR #677 (Embedding-based reactions) — Merged 2026-04-03. find_best_emoji is the result.

**Commits on main since issue was filed (touching referenced files):**
- None. `agent/constants.py`, `bridge/response.py`, `tools/emoji_embedding.py` are clean.

**Active plans in `docs/plans/` overlapping this area:** None.

## Prior Art

- **PR #677** (Replace Ollama emoji reactions with embedding-based lookup) — Added `find_best_emoji()` for in-flight reactions. Did not touch the three terminal constants.
- **PR #691** (Add Premium custom emoji for reactions and messages) — Added `EmojiResult`, `custom_emoji_document_id`, and updated `set_reaction()` to handle EmojiResult. Terminal constants were left as plain strings.

No prior issues found specifically targeting terminal constant upgrade.

## Research

Work is purely internal — reuses existing codebase infrastructure. No external research needed.

No relevant external findings — proceeding with codebase context and training data.

## Data Flow

1. **Session completes** — `agent/agent_session_queue.py:4112` evaluates outcome: error, communicated, or silent.
2. **Constant selected** — `REACTION_ERROR`, `REACTION_COMPLETE`, or `REACTION_SUCCESS` chosen.
3. **react_cb called** — `await react_cb(session.chat_id, session.telegram_message_id, emoji)`.
4. **set_reaction dispatches** — `bridge/response.py:759` normalizes str→EmojiResult, tries custom then standard.
5. **Telegram API** — `SendReactionRequest` sets the final emoji reaction on the message.

After this change: Step 2 uses pre-cached `EmojiResult` objects. Step 4 receives `EmojiResult` directly (already supported). No other steps change.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all infrastructure (EmojiResult, set_reaction, find_best_emoji, OPENROUTER_API_KEY in env) is already in place.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENROUTER_API_KEY` | `python -c "import os; assert os.environ.get('OPENROUTER_API_KEY')"` | Semantic emoji lookup (graceful fallback if absent) |

## Solution

### Key Elements

- **Feeling strings**: Define one human-readable feeling phrase per terminal state (e.g., `"task completed successfully, work done"`, `"error occurred something went wrong"`, `"acknowledged, received, silently noted"`).
- **Lazy-cached EmojiResult**: Call `find_best_emoji(feeling)` once at first access, cache the result in a module-level dict. No disk persistence needed — process lifetime cache is sufficient.
- **Hardcoded fallbacks**: When `find_best_emoji()` fails (no API key, no embeddings file), fall back to safe validated emojis: `👌` for SUCCESS, `👏` for COMPLETE, `😢` for ERROR. All confirmed in `VALIDATED_REACTIONS`.
- **Type stays EmojiResult**: Constants become `EmojiResult` objects. `set_reaction()` already handles this transparently — zero call-site changes in `agent_session_queue.py` or `bridge/telegram_bridge.py`.

### Technical Approach

1. In `agent/constants.py`, replace the three hardcoded Unicode strings with a `_resolve_terminal_emoji(feeling, fallback)` helper that calls `find_best_emoji()` and catches all exceptions, returning a fallback `EmojiResult` on any failure.
2. Call the helper three times with distinct feeling strings, caching the results as module-level `EmojiResult` constants.
3. The helper is called at import time (module load), so results are available immediately. No async needed — `find_best_emoji()` is synchronous.
4. Update `bridge/response.py` comment (line 166) to reflect that re-exported constants are now `EmojiResult` objects.
5. Update tests to assert: (a) constants are `EmojiResult` instances, (b) their `.emoji` fallback strings are in `VALIDATED_REACTIONS`, (c) all three are distinct.

### Feeling Strings

| Constant | Feeling String | Fallback Emoji |
|----------|---------------|----------------|
| `REACTION_SUCCESS` | `"acknowledged received silently noted"` | `👌` |
| `REACTION_COMPLETE` | `"task completed successfully work done"` | `👏` |
| `REACTION_ERROR` | `"error occurred something went wrong"` | `😢` |

## Failure Path Test Strategy

### Exception Handling Coverage
- `_resolve_terminal_emoji()` must catch all exceptions and return the fallback EmojiResult. Test: assert the function returns a valid EmojiResult even when find_best_emoji raises RuntimeError.
- `find_best_emoji()` already has internal exception handling and returns DEFAULT_EMOJI result on failure.

### Empty/Invalid Input Handling
- The feeling strings are constants, not user input. No empty/None risk.
- If OPENROUTER_API_KEY is absent, `find_best_emoji()` returns DEFAULT_EMOJI result. The fallback in `_resolve_terminal_emoji()` will override this with a validated fallback.

### Error State Rendering
- All fallback emoji (`👌`, `👏`, `😢`) are confirmed in `VALIDATED_REACTIONS`. Telegram API calls will not raise ReactionInvalidError.
- Custom emoji path: if `is_custom=True` and the document_id is stale (pack removed), `set_reaction()` already falls back to `emoji_result.emoji`. No additional handling needed.

## Test Impact

- [ ] `tests/unit/test_worker_entry.py::test_reaction_constants_importable_from_agent` — UPDATE: assert constants are EmojiResult instances; assert `.emoji` attr is in VALIDATED_REACTIONS rather than hardcoded specific emoji values.
- [ ] `tests/unit/test_worker_entry.py::test_reaction_re_exports_from_bridge` — UPDATE: same update — EmojiResult instances, not hardcoded strings.
- [ ] `tests/integration/test_reply_delivery.py::test_reaction_complete_in_validated_list` — UPDATE: extract `.emoji` from EmojiResult before checking VALIDATED_REACTIONS membership.
- [ ] `tests/integration/test_reply_delivery.py::test_reaction_error_in_validated_list` — UPDATE: same — extract `.emoji`.
- [ ] `tests/integration/test_reply_delivery.py::test_reaction_success_in_validated_list` — UPDATE: same — extract `.emoji`.
- [ ] `tests/integration/test_reply_delivery.py::test_all_reaction_constants_unique` — UPDATE: compare `.emoji` attributes (or str(result)) rather than the EmojiResult objects directly.

## Rabbit Holes

- **Bridge startup pre-computation** — The issue suggests pre-computing at startup alongside `build_custom_emoji_index`. Startup is async; `find_best_emoji()` is sync. Wiring this requires async-to-sync bridging. Module-level import-time call is simpler and equally correct — avoid the startup hook complexity.
- **Persisting to `data/terminal_reaction_cache.json`** — Unnecessary. In-process caching at module load achieves the same result. Disk persistence adds file I/O, migration concerns, and staleness risk.
- **Adding `TELEGRAM_PREMIUM_ENABLED` flag** — Not needed. `CUSTOM_EMOJI_DELTA` in `find_best_emoji()` already handles the decision. Adding a flag duplicates this logic.
- **Upgrading in-flight reactions** (REACTION_RECEIVED, REACTION_PROCESSING) — Out of scope. Those are already handled by the emoji embedding system.

## Risks

### Risk 1: Import-time HTTP call
**Impact:** Bridge startup time increases if `find_best_emoji()` makes a live API call at module import.
**Mitigation:** `find_best_emoji()` is called lazily via `_resolve_terminal_emoji()` at module import, but only if `OPENROUTER_API_KEY` is set. If not set, it returns immediately with the fallback. In practice the bridge already calls `find_best_emoji()` for in-flight reactions, so the embedding cache is already warm.

### Risk 2: Stale custom emoji document ID
**Impact:** If the Premium custom emoji pack is updated, a cached document_id may become invalid, causing `set_reaction()` to fail the custom path and fall back to standard.
**Mitigation:** `set_reaction()` already implements this fallback. No additional risk introduced.

## Race Conditions

No race conditions identified. The module-level cache is set once at import time in a single-threaded import context. All subsequent accesses are read-only.

## No-Gos (Out of Scope)

- Upgrading in-flight reactions (REACTION_RECEIVED, REACTION_PROCESSING) — already handled.
- Adding `TELEGRAM_PREMIUM_ENABLED` config flag — not needed.
- Disk-persisted terminal reaction cache — not needed.
- Bridge startup async pre-computation — not needed.
- Changing the three terminal reaction semantics (which state maps to which constant) — out of scope.

## Update System

No update system changes required — this feature is purely internal. No new env vars, no new files, no new dependencies. Existing `OPENROUTER_API_KEY` is already in the `.env` vault.

## Agent Integration

No agent integration required — terminal reactions are set by the session execution engine, not by agent tools. No MCP server changes needed.

## Documentation

- [ ] Update `docs/features/emoji-embedding-reactions.md` — add a "Terminal Reactions" subsection explaining that `REACTION_SUCCESS`, `REACTION_COMPLETE`, and `REACTION_ERROR` are now `EmojiResult` objects resolved via `find_best_emoji()` at import time, with the feeling strings used (`"acknowledged received silently noted"`, `"task completed successfully work done"`, `"error occurred something went wrong"`) and the hardcoded fallback emojis (`👌`, `👏`, `😢`).
- [ ] Add an entry to `docs/features/README.md` index table for the terminal reactions section if one does not already reference `emoji-embedding-reactions.md`.

## Success Criteria

- [ ] `REACTION_SUCCESS`, `REACTION_COMPLETE`, `REACTION_ERROR` are `EmojiResult` instances, not plain strings.
- [ ] Each constant's `.emoji` fallback is in `VALIDATED_REACTIONS`.
- [ ] All three constants have distinct `.emoji` values.
- [ ] `_resolve_terminal_emoji()` returns a valid fallback EmojiResult when `find_best_emoji()` raises any exception.
- [ ] All updated unit and integration tests pass (`pytest tests/unit/test_worker_entry.py tests/integration/test_reply_delivery.py`).
- [ ] Ruff lint and format pass.

## Team Orchestration

### Team Members

- **Builder (constants)**
  - Name: constants-builder
  - Role: Update `agent/constants.py` and `bridge/response.py` comment; update tests.
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Run tests and lint; verify success criteria.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update agent/constants.py
- **Task ID**: build-constants
- **Depends On**: none
- **Validates**: tests/unit/test_worker_entry.py, tests/integration/test_reply_delivery.py
- **Assigned To**: constants-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the three hardcoded Unicode reaction constants with `EmojiResult` objects resolved via a `_resolve_terminal_emoji(feeling, fallback_emoji)` helper.
- The helper calls `find_best_emoji(feeling)` inside a broad try/except; on any failure returns `EmojiResult(emoji=fallback_emoji)`.
- Fallback emojis: `👌` (SUCCESS), `👏` (COMPLETE), `😢` (ERROR) — all in VALIDATED_REACTIONS.
- Update the module docstring to describe the new pattern.
- Update `bridge/response.py` line 166 comment to note constants are now EmojiResult.
- Update all six affected tests:
  - `test_worker_entry.py::test_reaction_constants_importable_from_agent` — assert isinstance(REACTION_SUCCESS, EmojiResult), assert REACTION_SUCCESS.emoji in VALIDATED_REACTIONS.
  - `test_worker_entry.py::test_reaction_re_exports_from_bridge` — same.
  - `test_reply_delivery.py::test_reaction_complete_in_validated_list` — use REACTION_COMPLETE.emoji.
  - `test_reply_delivery.py::test_reaction_error_in_validated_list` — use REACTION_ERROR.emoji.
  - `test_reply_delivery.py::test_reaction_success_in_validated_list` — use REACTION_SUCCESS.emoji.
  - `test_reply_delivery.py::test_all_reaction_constants_unique` — compare str(r) or .emoji values.

### 2. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-constants
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_entry.py tests/integration/test_reply_delivery.py -v`
- Run `python -m ruff check agent/constants.py bridge/response.py tests/unit/test_worker_entry.py tests/integration/test_reply_delivery.py`
- Run `python -m ruff format --check .`
- Verify all success criteria met.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: constants-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/emoji-reactions.md` with terminal reaction upgrade details.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_worker_entry.py tests/integration/test_reply_delivery.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/constants.py bridge/response.py` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Constants are EmojiResult | `python -c "from agent.constants import REACTION_SUCCESS, REACTION_COMPLETE, REACTION_ERROR; from tools.emoji_embedding import EmojiResult; assert all(isinstance(r, EmojiResult) for r in [REACTION_SUCCESS, REACTION_COMPLETE, REACTION_ERROR])"` | exit code 0 |
| Fallbacks in VALIDATED_REACTIONS | `python -c "from agent.constants import REACTION_SUCCESS, REACTION_COMPLETE, REACTION_ERROR; from bridge.response import VALIDATED_REACTIONS; assert all(str(r) in VALIDATED_REACTIONS for r in [REACTION_SUCCESS, REACTION_COMPLETE, REACTION_ERROR])"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — scope is clear, infrastructure is in place, recon confirmed all assumptions.
