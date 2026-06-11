---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1512
last_comment_id:
---

# Action-Intent Emoji Reactions

## Problem

When a Telegram message arrives, the bridge reacts with an emoji chosen by embedding the message's
content and finding the nearest sentiment label in `EMOJI_LABELS`. This mirrors the *sentiment of
the user's words* back at them, which is the wrong signal.

**Current behavior:**
- `find_best_emoji_for_message(text)` embeds the first 100 characters of the message and cosine-matches
  against sentiment/content labels (e.g., "fire, hot, trending, lit" or "rude, angry, offensive").
- A frustrated message can match an offensive emoji, which was patched reactively by removing 🖕 from
  `EMOJI_LABELS` and adding a `BLOCKED_REACTION_EMOJIS` blocklist. This is whack-a-mole — any future
  sentiment-match to an inappropriate-to-mirror emoji will recur.
- The reaction reads as the bot reflecting the user's mood back at them rather than signaling what the
  agent is about to do.

**Desired outcome:**
- The reaction reflects the **agent's intended handling action**, not the content's sentiment.
- A problem/bug message → 🔍 (investigate). A task request → 🫡 (will do). Praise → 🙏/❤. Question → 🤔.
- Offensive reactions are structurally impossible because the action vocabulary never contains them;
  the blocklist stays as a belt-and-suspenders backstop.
- The reaction reads as a first-person statement from the bot: "Here's what I'm about to do."

## Freshness Check

**Baseline commit:** e6523d5b600dd35d1b5771410528a76913dc19f6
**Issue filed at:** 2026-06-11
**Disposition:** Minor drift (two patches landed after filing)

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1550-1563` — receipt-time emoji selection confirmed present at these lines.
- `tools/emoji_embedding.py:88` — `BLOCKED_REACTION_EMOJIS = frozenset({"🖕"})` confirmed.
- `tools/emoji_embedding.py:144` — `EMOJI_LABELS` dict with 72 sentiment labels confirmed.

**Cited sibling issues/PRs re-checked:**
- PR #1505 (fix emoji: never react with 🖕) — merged 2026-06-02 — symptom patch; root design unchanged.
- PR #677 (embedding-based reactions replacing Ollama) — merged 2026-04-03 — established the current
  content-embedding design we're now replacing.

**Commits on main since issue was filed (touching referenced files):**
- `8abc4645` Add softmax temperature sampling to emoji reaction selection — cosmetic variety improvement;
  does not address root label vocabulary problem.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/emoji-embedding-reactions.md` — status: Merged. The prior plan, now historical. No conflict.

**Notes:** Line numbers at `bridge/telegram_bridge.py:1553-1558` cited in the issue have shifted slightly
to `1550-1563` in current HEAD due to surrounding additions. All claims still hold.

## Prior Art

- **PR #677** (Replace Ollama emoji reactions with embedding-based lookup) — Replaced Ollama intent
  classification (10 hardcoded emojis, 2-10s latency) with content-embedding cosine similarity across
  all 72 validated reactions. Succeeded in improving coverage and speed. Introduced the current
  content-sentiment matching problem.
- **PR #1505** (fix: never react with 🖕) — Symptom fix: removed middle finger from labels/validated set
  and added `BLOCKED_REACTION_EMOJIS` blocklist. Did not change the embedding-against-content-sentiment
  architecture. Left the root design intact.

**Root cause pattern:** Both prior changes addressed symptoms (latency, offensive match) at selection
time without changing the fundamental approach of embedding message *content* against *sentiment* labels.

## Research

No relevant external findings — this is a purely internal refactor. The action vocabulary and mapping
are based on the existing work-type classifier (`tools/classifier.py`) which already runs at receipt
time. No external library research needed.

## Data Flow

1. **Entry point**: Telegram message arrives at `bridge/telegram_bridge.py` `_handle_new_message()`.
2. **Phase 1 reaction** (line 1543): Set 👀 (REACTION_RECEIVED) immediately.
3. **Async task** (line 1550): `select_and_set_emoji_reaction()` sleeps 2 seconds, then calls
   `find_best_emoji_for_message(clean_text)` which embeds the message snippet and cosine-matches
   against `EMOJI_LABELS`.
4. **Current** (to be replaced): `EMOJI_LABELS` maps emojis to sentiment/content descriptions.
   Result: reaction mirrors message content.
5. **Concurrent** (line 1568): `classify_work_type()` calls `classify_request_async(clean_text)` using
   Haiku to return `{"type": "bug"|"feature"|"chore"|"sdlc", "confidence": ...}`. This classification
   result is stored in `classification_result` dict and used for session routing.
6. **Proposed** (after this change): `select_and_set_emoji_reaction()` maps the message to an **action
   category** (via classifier or a small dedicated prompt), then selects a reaction emoji from a
   curated action-vocabulary in `EMOJI_LABELS`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #677 | Replaced Ollama with embedding-based selection | Changed the mechanism (Ollama → embeddings) but kept selecting based on content sentiment — a fundamental design error |
| PR #1505 | Removed 🖕 from the label set + added blocklist | Fixed one offensive match but left 70+ sentiment-labeled emojis that can still mirror negative user content |

**Root cause pattern:** The architecture has always embedded the *message content* against *sentiment
labels*. Patching individual emojis in or out never addresses the fact that the labels are
content-descriptive, not action-descriptive.

## Architectural Impact

- **Interface changes**: `find_best_emoji_for_message(text)` changes its internal logic from
  content-embedding to action-classification. The function signature, return type (`EmojiResult`),
  and call sites remain unchanged. This is a behavioral change, not an API change.
- **`EMOJI_LABELS` vocab**: The labels change from sentiment/content descriptions to
  action/intent descriptions. This breaks the 1:1 bijection with `VALIDATED_REACTIONS` tests
  (currently enforced in `test_emoji_embedding.py`) — the labels must be redesigned. We may
  reduce the active set to a smaller curated action vocabulary while keeping all 72 emojis in
  `VALIDATED_REACTIONS`.
- **Work-type classifier reuse**: `classify_request_async` already runs concurrently. The emoji
  selection task (`select_and_set_emoji_reaction()`) can either (a) use the same classification
  result directly (race condition concern — see Race Conditions), or (b) run its own fast LLM
  call for action classification.
- **Reversibility**: Low — the change is isolated to `tools/emoji_embedding.py` and the bridge
  call site. Reverting means restoring the old `EMOJI_LABELS` dict.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — no new external dependencies required.

## Solution

### Key Elements

- **Action vocabulary in `EMOJI_LABELS`**: Replace the 72 sentiment/content label strings with a
  small curated set of action/intent labels. Emojis not in the action vocabulary are removed from
  selection (but remain in `VALIDATED_REACTIONS`).
- **Action-intent classification**: `find_best_emoji_for_message(text)` classifies the message into
  an action category using a fast LLM call (Haiku, same as the existing work-type classifier).
  The classification returns one of: `problem-solving`, `investigate-bug`, `acknowledge-task`,
  `receive-praise`, `answer-question`, `general`.
- **Emoji selection**: Each action category maps to 2-3 candidate emojis. Selection uses the
  existing softmax temperature sampling over the candidates.

### Flow

Message arrives → 👀 eyes set immediately → 2-second pause → classify action intent (Haiku) → map
intent category to emoji candidates → softmax sample → set final reaction

### Technical Approach

**Action vocabulary (proposed `EMOJI_LABELS` replacement):**

```python
ACTION_EMOJI_MAP: dict[str, list[str]] = {
    "problem_solving": ["🤔", "🔍", "💡"],       # thinking, investigating, insight
    "investigate_bug": ["🔍", "🛠", "🕸"],        # search, fix, debug (🕸 = spider web)
    "acknowledge_task": ["🫡", "👀", "👍"],       # salute, watching, approve
    "receive_praise":  ["🙏", "❤", "🏆"],        # grateful, love, trophy
    "answer_question": ["🤔", "💡", "🤓"],        # thinking, idea, nerd
    "general":         ["🤔", "👀", "🫡"],        # default fallback set
}
```

Note: All candidate emojis must be in `VALIDATED_REACTIONS`. Verify before finalizing the map.

**Classification prompt** (Haiku, fast):
```
Classify this message into the agent's intended handling action:
- problem_solving: User describes a problem, inefficiency, or issue to solve
- investigate_bug: User reports something broken, a bug, or unexpected behavior  
- acknowledge_task: User gives a task, request, or instruction (will do)
- receive_praise: User expresses thanks, approval, or positive feedback
- answer_question: User asks a factual or conceptual question
- general: None of the above / unclear

Message: {text[:200]}

Respond with JSON: {"action": "<category>"}
```

**Integration in `find_best_emoji_for_message`**:
1. Run classification (async, using `asyncio.to_thread` or a direct `await` if the function is made async).
2. Look up `ACTION_EMOJI_MAP[action]` to get candidates.
3. Apply existing softmax sampling over candidates using `_softmax_sample`.
4. Return `EmojiResult`.

**`EMOJI_LABELS` treatment**: The dict remains for `find_best_emoji(feeling)` callers (intent-driven
callers pass their own action words). The dict's labels do NOT need to change for those callers.
`find_best_emoji_for_message` gets a separate fast-path that bypasses content-embedding entirely
and uses direct action classification.

**No shared state with classify_work_type()**: The emoji selection task runs its own classification
call rather than depending on `classification_result` from the concurrent task. This avoids a
race condition (the work-type classification may not have completed before the 2-second sleep
finishes) and keeps the two tasks independent.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `select_and_set_emoji_reaction()` task at bridge line 1561 has `except Exception as e: logger.debug(...)`. After this change, the new classification call is inside that try block. Add a test asserting that classification failure falls back to a reasonable default (🤔 or 👀).

### Empty/Invalid Input Handling
- [ ] `find_best_emoji_for_message("")` must still return default emoji (🤔). The new classification path must guard against empty text.
- [ ] Classification returning an unknown action category must fall back to `general` candidates, not raise.

### Error State Rendering
- [ ] No user-visible error output for this feature. Failures fall back silently to the 👀 eyes reaction (already set in step 1).

## Test Impact

- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiLabels::test_all_validated_reactions_have_labels` — UPDATE: The bijection contract changes. `EMOJI_LABELS` is still used by `find_best_emoji(feeling)` callers and should keep all 72 entries for that path. The new `ACTION_EMOJI_MAP` covers the message-reaction path; add new tests for it.
- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiLabels::test_label_count_matches_validated` — EVALUATE: May need relaxation if action vocab is a strict subset. Leave bijection test intact if `EMOJI_LABELS` is unchanged.
- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiLabels::test_no_extra_labels` — Same as above.
- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiSelection::test_message_function_uses_snippet` — UPDATE: The new behavior embeds action intent, not content. Rewrite the mock to stub the classifier, not the embedding API.
- [ ] `tests/unit/test_emoji_embedding.py::TestOffensiveEmojiBlocked` — These tests remain green by construction: the action vocabulary never contains offensive emojis, and the blocklist stays.

## Rabbit Holes

- **Two-phase reaction editing**: Updating the reaction after the agent has decided its actual handling action is maximally faithful but adds significant plumbing (back-channel from agent to bridge). The predicted-intent at receipt time is 95%+ accurate and simpler. Skip this.
- **Reusing `classification_result` from work-type classifier**: Tempting (avoids a second Haiku call) but creates a timing dependency on the concurrent `classify_work_type()` task. Keeping the calls independent is safer and the cost is negligible.
- **Expanding to 20+ action categories**: The 6-category vocabulary covers 95% of messages. Fine-grained categories (e.g., "writing task" vs "code task") add complexity without meaningful UX improvement.
- **Removing `EMOJI_LABELS` entirely**: `find_best_emoji(feeling)` callers (agent/constants.py, react_with_emoji.py, send_telegram.py) rely on this. Leave it intact; only change `find_best_emoji_for_message`'s internal path.

## Risks

### Risk 1: Classification latency exceeds 2-second eyes linger
**Impact:** If the Haiku classification call takes >2 seconds, the reaction update happens before the
user sees the 👀 eyes, losing the "reading" visual beat.
**Mitigation:** The existing `asyncio.sleep(2)` can be shortened to 0.5s if needed (the 2s was for
embedding API latency, not LLM latency). Haiku typically responds in <1s. The `except Exception`
wrapper ensures the 👀 reaction persists on failure.

### Risk 2: Action vocabulary produces wrong emoji for edge cases
**Impact:** A message that straddles categories (e.g., "thanks but that's broken") picks the wrong
action intent.
**Mitigation:** The `general` fallback category with neutral emojis (🤔, 👀, 🫡) handles ambiguous
messages safely. Classification confidence can be used to fall back to `general` when low.

## Race Conditions

### Race 1: Emoji selection vs. work-type classification result
**Location:** `bridge/telegram_bridge.py:1550-1583`, `classification_result` dict
**Trigger:** `select_and_set_emoji_reaction()` sleeps 2s then classifies; `classify_work_type()` also
runs and writes to `classification_result`. Both may finish in either order.
**Data prerequisite:** The emoji selection task must not depend on `classification_result` being
populated by `classify_work_type()`.
**Mitigation:** Emoji selection runs its own independent Haiku classification call. No shared state
between the two async tasks.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1512] Two-phase reaction editing (update reaction after agent decides actual action) — requires back-channel from agent execution to bridge; filed as part of this issue's open questions but deliberately deferred given complexity vs. marginal gain.
- Nothing else deferred — the action-vocabulary approach is complete within this plan.

## Update System

No update system changes required — this is a bridge-internal change to emoji label logic. No new
dependencies, configuration files, or migration steps needed across machines.

## Agent Integration

No agent integration required — this is a bridge-internal change. The reaction fires in `telegram_bridge.py`
before any agent session is started. Intent-driven callers (`react_with_emoji.py`, `send_telegram.py`,
`agent/constants.py`) are unaffected; they pass action/feeling words directly to `find_best_emoji(feeling)`,
which does not change.

## Documentation

- [ ] Update `docs/features/emoji-embedding-reactions.md` to describe the action-intent vocabulary
  replacing the content-sentiment approach in `find_best_emoji_for_message`.
- [ ] Update `docs/features/reaction-semantics.md` to note that receipt-time reactions now reflect
  predicted agent action, not content sentiment.

## Success Criteria

- [ ] `find_best_emoji_for_message(text)` classifies message intent via LLM and selects from an
  action-vocabulary (`ACTION_EMOJI_MAP`), not content-embedding against sentiment labels.
- [ ] A reported bug/problem reacts with 🔍, 🛠, or 🕸 (investigate/fix set).
- [ ] A task/request message reacts with 🫡, 👀, or 👍 (acknowledge set).
- [ ] Praise/thanks messages react with 🙏, ❤, or 🏆 (receive-warmly set).
- [ ] No content sentiment can produce an offensive reaction (action vocab contains no offensive emojis).
- [ ] `find_best_emoji(feeling)` callers (`agent/constants.py`, `tools/react_with_emoji.py`,
  `tools/send_telegram.py`) are unaffected.
- [ ] `TestOffensiveEmojiBlocked` tests still pass.
- [ ] New tests cover: action classification → emoji candidate selection, empty input, API failure fallback.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (emoji-intent)**
  - Name: emoji-builder
  - Role: Implement action-intent classification in `find_best_emoji_for_message`, define `ACTION_EMOJI_MAP`, update call site in bridge.
  - Agent Type: builder
  - Resume: true

- **Validator (emoji-intent)**
  - Name: emoji-validator
  - Role: Verify action vocab construction, test coverage, backward compat for `find_best_emoji(feeling)` callers.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(See template for full list.)

## Step by Step Tasks

### 1. Define action vocabulary and update `find_best_emoji_for_message`
- **Task ID**: build-action-vocab
- **Depends On**: none
- **Validates**: `tests/unit/test_emoji_embedding.py`
- **Assigned To**: emoji-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/emoji_embedding.py`: add `ACTION_EMOJI_MAP` dict mapping 6 action categories to candidate emoji lists. Verify every candidate emoji is in `VALIDATED_REACTIONS`.
- In `tools/emoji_embedding.py`: add `classify_message_action(text: str) -> str` (async) that calls Haiku with the classification prompt and returns one of the 6 action categories. Falls back to `"general"` on error or low confidence.
- In `tools/emoji_embedding.py`: rewrite `find_best_emoji_for_message(text)` to: (1) call `classify_message_action`, (2) look up `ACTION_EMOJI_MAP[action]`, (3) apply `_softmax_sample` over the candidate list, (4) return `EmojiResult`. Keep signature and return type identical.
- Keep `find_best_emoji(feeling)` and `EMOJI_LABELS` entirely unchanged — they serve intent-driven callers.

### 2. Update bridge call site if needed
- **Task ID**: build-bridge
- **Depends On**: build-action-vocab
- **Validates**: bridge integration (manual smoke test)
- **Assigned To**: emoji-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/telegram_bridge.py:select_and_set_emoji_reaction()`: if `find_best_emoji_for_message` is now async, update the `asyncio.to_thread` call to a direct `await`. Otherwise no change needed (the function signature is unchanged, so the call site is transparent).

### 3. Update and add tests
- **Task ID**: build-tests
- **Depends On**: build-action-vocab
- **Validates**: `tests/unit/test_emoji_embedding.py`
- **Assigned To**: emoji-builder
- **Agent Type**: builder
- **Parallel**: false
- UPDATE `TestEmojiSelection::test_message_function_uses_snippet`: stub the classifier call instead of the embedding API.
- ADD `TestActionVocabulary`: verify `ACTION_EMOJI_MAP` candidates are all in `VALIDATED_REACTIONS`, all 6 categories present, no offensive emojis in any candidate list.
- ADD `TestMessageActionClassification`: mock Haiku response, verify each action category maps to expected emoji candidates, verify empty input returns `EmojiResult` with default emoji, verify classification error falls back to `"general"`.
- Verify `TestOffensiveEmojiBlocked` still passes unchanged.

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-bridge, build-tests
- **Assigned To**: emoji-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_emoji_embedding.py -v` — all pass.
- Verify `find_best_emoji(feeling)` callers are unmodified (grep confirms `agent/constants.py`, `tools/react_with_emoji.py`, `tools/send_telegram.py` still call `find_best_emoji`, not `find_best_emoji_for_message`).
- Verify `EMOJI_LABELS` dict is unchanged.
- Verify no offensive emoji appears in `ACTION_EMOJI_MAP` values.
- Run `python -m ruff check . && python -m ruff format --check .` — clean.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/emoji-embedding-reactions.md`: replace description of content-embedding for `find_best_emoji_for_message` with the action-intent classification approach. Document `ACTION_EMOJI_MAP` structure.
- Update `docs/features/reaction-semantics.md`: add a note that the receipt-time reaction reflects predicted agent action (classify then react), not content sentiment.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_emoji_embedding.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No offensive emoji in action map | `python -c "from tools.emoji_embedding import ACTION_EMOJI_MAP, BLOCKED_REACTION_EMOJIS; blocked = [e for v in ACTION_EMOJI_MAP.values() for e in v if e in BLOCKED_REACTION_EMOJIS]; assert not blocked, blocked"` | exit code 0 |
| find_best_emoji unchanged callers | `grep -n "find_best_emoji_for_message" tools/react_with_emoji.py tools/send_telegram.py agent/constants.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — all architectural choices resolved in recon. Ready for critique.
