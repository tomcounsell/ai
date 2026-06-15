---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1512
last_comment_id: 4693087535
revision_applied: true
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

- **PR #1651** (merged 2026-06-12) — Improved the *completion-time* reaction in `agent/session_executor.py`
  by ORing `agent_session.user_facing_routed` into the `communicated` check so `REACTION_COMPLETE` vs
  `REACTION_SUCCESS` fires correctly for the granite-container path. **Relevance:** this is a *different
  reaction path* (completion-time, not receipt-time) and is out of scope for this plan, which is scoped
  strictly to the receipt-time `find_best_emoji_for_message` path. Noted for context — no overlap, no conflict.

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
6. **Proposed** (after this change): `select_and_set_emoji_reaction()` reads the work-type that
   `classify_work_type()` already computed (`classification_result["type"]`, opportunistically), maps it
   via `WORKTYPE_TO_ACTION` to an **action category**, and `random.choice`s a reaction emoji from the
   curated `ACTION_EMOJI_MAP`. No second LLM call; `find_best_emoji_for_message` stays synchronous and is
   still dispatched via `asyncio.to_thread`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #677 | Replaced Ollama with embedding-based selection | Changed the mechanism (Ollama → embeddings) but kept selecting based on content sentiment — a fundamental design error |
| PR #1505 | Removed 🖕 from the label set + added blocklist | Fixed one offensive match but left 70+ sentiment-labeled emojis that can still mirror negative user content |

**Root cause pattern:** The architecture has always embedded the *message content* against *sentiment
labels*. Patching individual emojis in or out never addresses the fact that the labels are
content-descriptive, not action-descriptive.

## Architectural Impact

- **Interface changes**: `find_best_emoji_for_message` gains an optional `work_type: str | None = None`
  second parameter and changes its internal logic from content-embedding to work-type → action mapping.
  The function stays **synchronous** (B2) and returns `EmojiResult`. The bridge call site adds the new
  arg but keeps `asyncio.to_thread` (B2 — never a bare `await` on a coroutine). Additive, backward-safe.
- **`EMOJI_LABELS` vocab**: The labels change from sentiment/content descriptions to
  action/intent descriptions. This breaks the 1:1 bijection with `VALIDATED_REACTIONS` tests
  (currently enforced in `test_emoji_embedding.py`) — the labels must be redesigned. We may
  reduce the active set to a smaller curated action vocabulary while keeping all 72 emojis in
  `VALIDATED_REACTIONS`.
- **Work-type classifier reuse (the chosen design, C1)**: `classify_request_async` already runs
  concurrently and writes `classification_result["type"]`. The emoji task REUSES that result via a
  pure-Python `WORKTYPE_TO_ACTION` map — no second LLM call. The race (emoji task may read before the
  classifier writes) is handled by an opportunistic `.get("type")` → `general` fallback (see Race
  Conditions). This reverses the first revision's "run an independent classifier" choice.
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
- **Action-intent mapping (no new LLM call)**: `find_best_emoji_for_message(text, work_type)` maps the
  work-type that `classify_work_type()` already computes (`bug/feature/chore/sdlc`) to an action category
  via the pure-Python `WORKTYPE_TO_ACTION` table. The six action categories are `investigate_bug`,
  `problem_solving`, `acknowledge_task`, `receive_praise`, `answer_question`, `general`; the work-type
  reuse populates `investigate_bug`/`acknowledge_task`, with everything else folding to `general` (C1).
- **Emoji selection**: Each action category maps to a small candidate list. Selection uses
  `random.choice` over the list (B1 — the lists are bare strings, so `_softmax_sample` is the wrong
  tool; uniform random is the correct, simple choice).

### Flow

Message arrives → 👀 eyes set immediately → 2-second pause → read the work-type
`classify_work_type()` already produced → map to action category → `random.choice` a candidate → set
final reaction. No second LLM call; the function stays sync and runs via `asyncio.to_thread`.

### Technical Approach

**Action vocabulary (proposed `EMOJI_LABELS` replacement):**

```python
ACTION_EMOJI_MAP: dict[str, list[str]] = {
    "investigate_bug":  ["👨‍💻", "👀"],        # on it / debugging — leads with the working dev, no flippant nerd face
    "problem_solving":  ["👨‍💻", "🤝"],        # working it / here to help
    "acknowledge_task": ["🫡", "👍"],          # salute / will do — leads with the salute
    "receive_praise":   ["🙏", "❤", "🏆"],     # grateful / love / trophy
    "answer_question":  ["🤔", "🤝"],          # thinking (reserved for questions) / here to help
    "general":          ["👀"],                # distinct neutral fallback — plain "I see you", NOT 🤔
}
```

**Emoji-distinctiveness retune (resolves C5 + C6).** The first revision spread `🤔` across four of
six categories, so almost every message landed on a thinking face — visually identical to the plain
👀 already set in phase 1, and indistinguishable between categories. The retune above:

- **C6 — reserve `🤔` for `answer_question` only.** A thinking face now genuinely signals "the bot is
  considering your question." Every other category leads with a distinct, action-specific emoji.
- **C6 — give `general` its own distinct emoji.** The fallback is now `👀` ("I see you"), which is
  honest about the lack of a strong intent signal and is visually distinct from the thinking-face
  category. Tasks lead with `🫡`/`👍` (acknowledge); bug work leads with `👨‍💻` (on it).
- **C5 — drop the nerd face `🤓` from bug handling entirely.** A frustrated user reporting a bug should
  not see a flippant `🤓`. `investigate_bug` now leads with `👨‍💻` (a developer at work — reads as
  "I'm on it") with `👀` as the only alternate. `🤓` is removed from every category.
- All emojis above remain inside `VALIDATED_REACTIONS` (verified — see the constraint note below). The
  builder MUST keep the `all(e in VALIDATED_REACTIONS ...)` assertion test (Task 3) so this can never
  regress; `🔧`/`🛠` are NOT valid Telegram reactions and must never be added.

**CRITICAL Telegram-reaction constraint (verified against `bridge/response.py` HEAD `0d000e59`):**
Telegram only accepts a fixed whitelist of reaction emojis (`VALIDATED_REACTIONS`, 72 entries).
The intuitive "investigate/fix" emojis from the issue's examples — **🔍, 🛠, 🔨, 🕸, 💡** — are
**NOT valid Telegram reactions**. `🔍` and `💡` are explicitly listed in `INVALID_REACTIONS`
(`bridge/response.py`) and return `ReactionInvalidError`; `🛠`, `🔨`, `🕸` are absent from the
whitelist entirely. Setting any of them as a reaction throws at runtime.

The map above substitutes whitelist-valid emojis that still read as "I'm working on this":
`👨‍💻` (coding/on it), `🤝` (here to help), `👀` (watching/on it), with `🤔` (thinking) reserved for
`answer_question`. (`🤓` was dropped entirely per C5 — see the retune note above.) Every candidate
in the map above has been verified present in `VALIDATED_REACTIONS`. The builder MUST add a unit
test asserting `all(e in VALIDATED_REACTIONS for cat in ACTION_EMOJI_MAP for e in ACTION_EMOJI_MAP[cat])`
so this constraint can never silently regress.

**Implication for the issue's acceptance criteria:** the issue asks for bug reactions in the set
🔍/🛠/🔨/🕸. Since none are valid Telegram reactions, the plan satisfies the *intent* of that
criterion (a distinct "investigating" signal) with the closest valid emojis (👨‍💻/👀) rather than
the literal emojis named. This is a forced substitution, not a scope reduction — flagged here so
the reviewer and critique stage see it explicitly.

**Why returning to intent classification is not the pre-#677 mistake (resolves C4).** PR #677
*replaced* an Ollama intent-classifier (10 hardcoded emojis, 2-10s latency) with embedding-over-content.
A reviewer could reasonably ask whether re-introducing intent classification swings the pendulum back to
the rejected design. It does not, for two concrete reasons:

1. **Latency.** #677 rejected the old classifier because Ollama added 2-10s of receipt-time latency.
   This plan adds *zero* new latency on the recommended path: it reuses the `bug/feature/chore/sdlc`
   result that `classify_work_type()` already computes at receipt (see "Classification source" below).
   Even on the fallback path, the classifier is Haiku (median <1s), comfortably below the 2-10s threshold
   that doomed the Ollama design. Latency was the disqualifier for the old approach, and it is no longer
   present.
2. **Vocabulary size is a deliberate feature, not a regression.** The old classifier's 10 hardcoded
   emojis were criticized as *too few*; #677 swung to all 72 sentiment labels for "coverage." But broad
   sentiment coverage was the *root failure* — it is precisely what let a frustrated message mirror an
   offensive emoji. The ~12 distinct intent emojis across 6 action categories here are intentionally
   curated: enough to read distinctly, deliberately too few to ever mirror a hostile sentiment. Small
   vocabulary is the safety property, not the bug.

In short: #677's two objections (slow, narrow) do not apply — this path is fast (reuse, or Haiku <1s)
and its narrowness is the entire point.

**Classification source — REUSE the work-type classifier; do NOT add a second Haiku call (resolves C1).**
The first revision proposed a *second* per-message Haiku call (`classify_message_action`) running
concurrently with the routing-critical `classify_work_type()`. Both would contend on the same
`anthropic_slot()` semaphore (#1111), doubling receipt-time Haiku pressure for a cosmetic reaction.
The Simplifier flagged this call as likely unnecessary, and it is. The resolution is the **reuse path**:

- `classify_work_type()` already runs at receipt (`bridge/telegram_bridge.py:1568`) and writes
  `classification_result["type"]` ∈ `{bug, feature, chore, sdlc}`. The emoji task maps that result
  to an action category with a pure-Python lookup — **no second LLM call**:

  ```python
  WORKTYPE_TO_ACTION = {
      "bug":     "investigate_bug",
      "feature": "acknowledge_task",
      "chore":   "acknowledge_task",
      "sdlc":    "acknowledge_task",
  }
  # action = WORKTYPE_TO_ACTION.get(classification_result.get("type"), "general")
  ```

- **Coverage gap acknowledged, by design.** The work-type classifier has no `receive_praise` or
  `answer_question` category, so pure reuse folds praise/questions into `general` (→ 👀). That is an
  acceptable Small-appetite trade: the dominant receipt signals are "I'm on it" (bug) vs. "will do"
  (task), and `general` → 👀 is a safe, honest neutral. Distinguishing praise/questions would require
  the second classifier this concern exists to eliminate, so it is explicitly deferred (see No-Gos).

- **Race handling (the one real subtlety).** `classify_work_type()` may not have finished when the
  emoji task wakes from its sleep. The emoji task therefore **does not block on it**: it reads
  `classification_result.get("type")` opportunistically, and if the type is not yet populated it
  selects from `general` (→ 👀). Because phase 1 already set 👀, an unresolved-classification path is a
  visual no-op, not a regression. The existing `asyncio.sleep(2)` linger gives the concurrent
  classifier ample time to populate the result in the common case. To make the common case reliable
  without a hard dependency, the emoji task may optionally `await asyncio.wait_for(classify_done_event.wait(),
  timeout=4)` on a small `asyncio.Event` the classifier sets — bounded so a slow/failed classifier still
  yields the 👀 fallback. The builder picks whichever of (a) opportunistic read or (b) event-with-timeout
  is cleaner; both keep the 👀 fallback and add no second Haiku call.

**B1 — emoji selection over the candidate list uses `random.choice`, NOT `_softmax_sample`.**
`ACTION_EMOJI_MAP` values are bare emoji-string lists (`list[str]`). `_softmax_sample` expects
`list[tuple[str, float]]` and computes `score / temperature`; passing bare strings raises `TypeError`,
and even if wrapped as `[(e, 1.0) for e in ...]` the uniform scores collapse softmax to plain uniform
sampling anyway (see Nit N1). The plan therefore selects with `random.choice(ACTION_EMOJI_MAP[action])`
— simpler, no fake scores, identical statistical result. `_softmax_sample` stays untouched and is still
used by `find_best_emoji(feeling)`'s scored embedding path.

**B2 — `find_best_emoji_for_message` stays SYNCHRONOUS; the bridge keeps `asyncio.to_thread`.**
The function does no I/O on the reuse path (pure dict lookup + `random.choice`), so there is no reason
to make it a coroutine. It accepts the already-computed work-type as an argument and returns an
`EmojiResult` synchronously:

```python
def find_best_emoji_for_message(text: str, work_type: str | None = None) -> EmojiResult:
    if not text or not isinstance(text, str) or not text.strip():
        return EmojiResult(emoji=DEFAULT_EMOJI)
    action = WORKTYPE_TO_ACTION.get(work_type, "general")
    candidates = ACTION_EMOJI_MAP.get(action) or ACTION_EMOJI_MAP["general"]
    return EmojiResult(emoji=random.choice(candidates))
```

The bridge call site keeps `await asyncio.to_thread(find_best_emoji_for_message, clean_text, work_type)`
**unchanged in shape** — it stays a sync function dispatched to a thread. This explicitly avoids the
silent-no-op failure mode where making the function a coroutine would leave `to_thread` returning an
*unawaited coroutine* (the emoji would never be set, the `RuntimeWarning` swallowed by the surrounding
`except`). The new `work_type` parameter is optional and defaults to `None` → `general`, so the call
site change is purely additive.

**C2 — log failures at `logger.warning`, not `logger.debug`.** The `except Exception` in
`select_and_set_emoji_reaction()` (`bridge/telegram_bridge.py:1561`) currently logs at `logger.debug`,
which is invisible in production. Change it to `logger.warning(f"Emoji reaction selection failed
(non-fatal): {e}")` so silent failures surface. One-line change.

**C3 — no dead "low confidence" logic; validate the action category instead.** The first revision
referenced "fall back on low confidence," but the reuse path has no per-message confidence to threshold
on (and the eliminated prompt had no confidence field), so that wording is dead logic and is removed.
The real guard is category validation: after mapping, `if action not in ACTION_EMOJI_MAP: action =
"general"`. (The `WORKTYPE_TO_ACTION.get(..., "general")` default already enforces this for the reuse
path; the explicit check covers any future caller passing a raw action string.) If the *fallback*
classifier path below is ever used, it reuses `tools/classifier.py::_parse_json_response` to strip
markdown ```json fences before `json.loads` — never hand-rolled fence stripping.

**Fallback path (only if the reuse signal is unavailable).** If `classification_result["type"]` is
absent AND a builder chooses to add a direct action classifier rather than degrade to `general`, that
classifier MUST: (1) be the existing async Haiku helper bounded by `asyncio.wait_for(..., timeout=4)`
inside the existing try/except so a slow call still yields the 👀 fallback; (2) reuse
`_parse_json_response` for fence-stripping; (3) validate the returned action against `ACTION_EMOJI_MAP`,
defaulting to `general`. This path is **not the recommended implementation** — it exists only to bound
the design space. The default and strongly-preferred implementation is pure reuse with no second call.

**`EMOJI_LABELS` treatment**: The dict remains for `find_best_emoji(feeling)` callers (intent-driven
callers pass their own action words). The dict's labels do NOT need to change for those callers.
`find_best_emoji_for_message` gets a separate fast-path that bypasses content-embedding entirely
and uses the reused work-type → action mapping.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `select_and_set_emoji_reaction()` task at bridge line 1561 has `except Exception as e: logger.debug(...)`. C2: change this to `logger.warning(...)` so failures are visible in prod. The mapping is now pure-Python (no LLM call on the reuse path), so the most likely failure is `set_reaction` itself; the 👀 from phase 1 persists regardless.

### Empty/Invalid Input Handling
- [ ] `find_best_emoji_for_message("")` must still return the default emoji (🤔 via `EmojiResult(emoji=DEFAULT_EMOJI)`). The empty-text guard stays first in the function.
- [ ] `find_best_emoji_for_message(text, work_type=None)` (type not yet classified) must select from `general` (→ 👀), not raise.
- [ ] An unknown/unexpected `work_type` value must map to `general` via `WORKTYPE_TO_ACTION.get(..., "general")` and the `if action not in ACTION_EMOJI_MAP: action = "general"` guard (C3) — never raise, never index-error.

### Error State Rendering
- [ ] No user-visible error output for this feature. Failures fall back silently to the 👀 eyes reaction (already set in step 1).

## Test Impact

- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiLabels::test_all_validated_reactions_have_labels` — UPDATE: The bijection contract changes. `EMOJI_LABELS` is still used by `find_best_emoji(feeling)` callers and should keep all 72 entries for that path. The new `ACTION_EMOJI_MAP` covers the message-reaction path; add new tests for it.
- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiLabels::test_label_count_matches_validated` — EVALUATE: May need relaxation if action vocab is a strict subset. Leave bijection test intact if `EMOJI_LABELS` is unchanged.
- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiLabels::test_no_extra_labels` — Same as above.
- [ ] `tests/unit/test_emoji_embedding.py::TestEmojiSelection::test_message_function_uses_snippet` — REPLACE: the function no longer embeds a snippet at all; it maps the reused work-type to an action and `random.choice`s a candidate. Rewrite to assert the returned emoji is in the mapped category — no embedding/LLM stub needed (the reuse path makes no API call).
- [ ] `tests/unit/test_emoji_embedding.py::TestOffensiveEmojiBlocked` — These tests remain green by construction: the action vocabulary never contains offensive emojis, and the blocklist stays.

## Rabbit Holes

- **Two-phase reaction editing**: Updating the reaction after the agent has decided its actual handling action is maximally faithful but adds significant plumbing (back-channel from agent to bridge). The predicted-intent at receipt time is 95%+ accurate and simpler. Skip this.
- **A second per-message Haiku call (`classify_message_action`)**: REJECTED (this is the reversal of the first revision's stance — see C1). The first revision proposed running a dedicated action classifier concurrently with `classify_work_type()`. Both contend on the same `anthropic_slot()` semaphore (#1111), doubling receipt-time Haiku pressure against routing-critical classification for a purely cosmetic reaction. The correct design REUSES `classify_work_type()`'s already-computed `bug/feature/chore/sdlc` result via a pure-Python `WORKTYPE_TO_ACTION` map (see Technical Approach → "Classification source"). The timing dependency this rabbit hole once feared is handled by reading `classification_result` opportunistically and falling back to `general` → 👀 when not yet populated — no blocking, no second call.
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
action intent. With the reuse path, praise and questions also fold into `general` (the work-type
classifier has no praise/question category).
**Mitigation:** The `general` fallback category (→ 👀) handles ambiguous and uncovered messages safely
and honestly. There is no per-message confidence to threshold on in the reuse path; the safety property
is the small curated vocabulary, not a confidence cutoff (see C3 in Technical Approach — the "low
confidence" wording was removed as dead logic).

## Race Conditions

### Race 1: Emoji selection reads `classification_result` before `classify_work_type()` populates it
**Location:** `bridge/telegram_bridge.py:1550-1583`, `classification_result` dict
**Trigger:** `select_and_set_emoji_reaction()` sleeps 2s then maps the work-type to an action;
`classify_work_type()` runs concurrently and writes `classification_result["type"]`. The emoji task may
wake before the classifier has written.
**Data prerequisite:** The emoji task reads `classification_result["type"]`; it must tolerate that key
being absent.
**Mitigation:** The emoji task reads the type **opportunistically** — `classification_result.get("type")`
→ `WORKTYPE_TO_ACTION.get(..., "general")`. If the classifier has not yet written, the task selects from
`general` (→ 👀), which is a visual no-op because phase 1 already set 👀. No blocking dependency, no
second Haiku call. The `asyncio.sleep(2)` linger makes the populated case the common case; the builder
may optionally bound a short `asyncio.wait_for(event.wait(), timeout=4)` on an `asyncio.Event` the
classifier sets, but the timeout must always fall back to `general` so a slow/failed classifier never
hangs the reaction. This is a deliberate reversal of the first revision's "run an independent classifier"
mitigation (see C1) — reuse with a safe fallback replaces the second LLM call.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1512] Two-phase reaction editing (update reaction after agent decides actual action) — requires back-channel from agent execution to bridge; filed as part of this issue's open questions but deliberately deferred given complexity vs. marginal gain.
- [DEFERRED] Distinguishing `receive_praise` and `answer_question` at receipt time — the reused work-type classifier (`bug/feature/chore/sdlc`) has no praise/question category, so these fold into `general` → 👀. Adding the distinction would require the second per-message Haiku call that C1 exists to eliminate; not worth the receipt-time semaphore contention for a cosmetic gain. The categories remain in `ACTION_EMOJI_MAP` for the (out-of-scope) fallback-classifier path and future use.
- Nothing else deferred — the reuse-based action-vocabulary approach is complete within this plan.

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

- [ ] `find_best_emoji_for_message(text, work_type)` maps the reused work-type to an action category and selects from `ACTION_EMOJI_MAP` via `random.choice` (B1), not content-embedding and not `_softmax_sample`. It stays a synchronous function (B2); no new Haiku call is added (C1).
- [ ] A reported bug/problem reacts with 👨‍💻 or 👀 (working set; `🤓` removed per C5 — these are the closest *valid* Telegram reactions; the issue's literal 🔍/🛠/🕸 are not on Telegram's whitelist, see Technical Approach).
- [ ] A task/request message reacts with 🫡 or 👍 (acknowledge set).
- [ ] Praise/thanks messages react with 🙏, ❤, or 🏆 when covered; uncovered praise/questions fold to `general` → 👀 (reuse-path coverage gap, accepted by design — C1).
- [ ] `🤔` appears only for `answer_question`, and `general` → 👀 is visually distinct from the thinking-face category (C6).
- [ ] Every emoji in `ACTION_EMOJI_MAP` is present in `VALIDATED_REACTIONS` (asserted by a unit test; guards against `ReactionInvalidError` at runtime).
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
- In `tools/emoji_embedding.py`: add `ACTION_EMOJI_MAP` (6 categories, retuned per C5/C6 — `🤔` only in `answer_question`, `general` → `["👀"]`, no `🤓` anywhere) and `WORKTYPE_TO_ACTION` (`bug→investigate_bug`, `feature/chore/sdlc→acknowledge_task`). Verify every candidate emoji is in `VALIDATED_REACTIONS`.
- In `tools/emoji_embedding.py`: rewrite `find_best_emoji_for_message(text: str, work_type: str | None = None) -> EmojiResult` as a **synchronous** function (B2 — do NOT make it async; the bridge keeps `asyncio.to_thread`). Logic: (1) empty-text guard returns `EmojiResult(emoji=DEFAULT_EMOJI)`; (2) `action = WORKTYPE_TO_ACTION.get(work_type, "general")`; (3) `if action not in ACTION_EMOJI_MAP: action = "general"` (C3); (4) `candidates = ACTION_EMOJI_MAP[action]`; (5) `return EmojiResult(emoji=random.choice(candidates))` — B1: use `random.choice`, NOT `_softmax_sample` (which would `TypeError` on bare-string lists). Do NOT add a second Haiku call (C1 — reuse the work-type result the bridge already computes).
- Do NOT add `classify_message_action`. The reuse path needs no new LLM helper. (If a fallback classifier is ever genuinely required, it must reuse `tools/classifier.py::_parse_json_response` for fence-stripping and be bounded by `asyncio.wait_for(..., timeout=4)` — see Technical Approach "Fallback path"; this is explicitly out of the default build.)
- Keep `find_best_emoji(feeling)`, `_softmax_sample`, and `EMOJI_LABELS` entirely unchanged — they serve intent-driven callers and the scored embedding path.

### 2. Update bridge call site
- **Task ID**: build-bridge
- **Depends On**: build-action-vocab
- **Validates**: bridge integration (manual smoke test + N2 human-eval)
- **Assigned To**: emoji-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/telegram_bridge.py:select_and_set_emoji_reaction()`: KEEP `await asyncio.to_thread(find_best_emoji_for_message, clean_text, classification_result.get("type"))` — the function stays SYNC (B2), so `to_thread` is correct and must NOT become a bare `await` on a coroutine (that would silently no-op). The only change is passing the work-type as the new second arg, read opportunistically from `classification_result` (Race 1 — `.get("type")` tolerates the classifier not having finished; `None` → `general` → 👀).
- C2: change the `except` log line in `select_and_set_emoji_reaction()` from `logger.debug(...)` to `logger.warning(f"Emoji reaction selection failed (non-fatal): {e}")`.
- Optional (Race 1 hardening): if the builder finds the opportunistic read too often races to `general`, set a small `asyncio.Event` in `classify_work_type()` and `await asyncio.wait_for(event.wait(), timeout=4)` in the emoji task before reading — always falling back to `general` on timeout. Not required for correctness.

### 3. Update and add tests
- **Task ID**: build-tests
- **Depends On**: build-action-vocab
- **Validates**: `tests/unit/test_emoji_embedding.py`
- **Assigned To**: emoji-builder
- **Agent Type**: builder
- **Parallel**: false
- UPDATE `TestEmojiSelection::test_message_function_uses_snippet`: the function no longer embeds a snippet — REPLACE it with a test that `find_best_emoji_for_message(text, work_type)` returns an emoji from the mapped category's candidate list (no embedding API, no LLM stub needed since the reuse path makes no call).
- ADD `TestActionVocabulary`: verify `ACTION_EMOJI_MAP` candidates are all in `VALIDATED_REACTIONS` (HARD assertion — invalid Telegram reactions like 🔍/🛠/💡 throw `ReactionInvalidError` at runtime), all 6 categories present, `🤔` appears ONLY in `answer_question` (C6), `🤓` appears in NO category (C5), `general == ["👀"]` (C6), no offensive/blocked emoji in any candidate list, none in `INVALID_REACTIONS`.
- ADD `TestWorktypeToAction`: verify `WORKTYPE_TO_ACTION` maps `bug→investigate_bug` and `feature/chore/sdlc→acknowledge_task`; verify `find_best_emoji_for_message(text, "bug")` returns from `investigate_bug` candidates; verify `work_type=None` and unknown work-types return from `general` (→ 👀); verify empty input returns `EmojiResult(emoji=DEFAULT_EMOJI)`; verify `random.choice` is used (no `TypeError` from `_softmax_sample`).
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
- Verify no offensive emoji appears in `ACTION_EMOJI_MAP` values; verify `🤔` is only in `answer_question` and `🤓` is absent everywhere (C5/C6 retune).
- Run `python -m ruff check . && python -m ruff format --check .` — clean.
- **N2 — manual human-eval acceptance (do after restart).** Restart the bridge (`./scripts/valor-service.sh restart`), then from a real Telegram chat send one message of each kind and confirm the reaction reads correctly: a bug report (expect 👨‍💻/👀), a task/request (expect 🫡/👍), a praise/thanks message (expect 🙏/❤/🏆 if covered, else 👀), and a question (expect 🤔). Record the observed reactions in the validation notes. This catches "reads flippant / indistinguishable" UX regressions that unit tests cannot.

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
| All action emojis are valid Telegram reactions | `python -c "from tools.emoji_embedding import ACTION_EMOJI_MAP; from bridge.response import VALIDATED_REACTIONS; bad = [e for v in ACTION_EMOJI_MAP.values() for e in v if e not in VALIDATED_REACTIONS]; assert not bad, bad"` | exit code 0 |
| 🤔 reserved for questions; 🤓 absent (C5/C6) | `python -c "from tools.emoji_embedding import ACTION_EMOJI_MAP as M; assert all(('🤔' in v)==(k=='answer_question') for k,v in M.items()); assert all('🤓' not in v for v in M.values()); assert M['general']==['👀']"` | exit code 0 |
| Message path is sync + uses random.choice (B1/B2) | `python -c "import inspect, asyncio; from tools import emoji_embedding as m; src=inspect.getsource(m.find_best_emoji_for_message); assert not asyncio.iscoroutinefunction(m.find_best_emoji_for_message); assert 'random.choice' in src and '_softmax_sample' not in src"` | exit code 0 |
| Bridge keeps to_thread (no coroutine no-op, B2) | `grep -n "asyncio.to_thread(find_best_emoji_for_message" bridge/telegram_bridge.py` | exit code 0 |
| Failures logged at warning (C2) | `grep -n "Emoji reaction selection failed" bridge/telegram_bridge.py` shows logger.warning | exit code 0 |
| find_best_emoji unchanged callers | `grep -n "find_best_emoji_for_message" tools/react_with_emoji.py tools/send_telegram.py agent/constants.py` | exit code 1 |

## Critique Results

<!-- Critique verdict: NEEDS REVISION (2026-06-15). Revision applied 2026-06-16. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator | B1: `_softmax_sample` would `TypeError` on bare-string `ACTION_EMOJI_MAP` lists | Technical Approach → B1; Task 1 | Use `random.choice(candidates)` — scoreless lists make softmax ≈ uniform anyway (N1). `_softmax_sample` left untouched for the embedding path. |
| BLOCKER | Operator | B2: making the fn a coroutine breaks `asyncio.to_thread` → silent no-op | Technical Approach → B2; Task 2; Verification | Keep `find_best_emoji_for_message` SYNC; bridge keeps `to_thread`, adds optional `work_type` arg. |
| CONCERN | Simplifier | C1: doubled receipt-time Haiku calls contend on `anthropic_slot()` | Technical Approach → "Classification source"; Rabbit Holes; Race 1 | REUSE `classify_work_type()`'s result via `WORKTYPE_TO_ACTION`; no 2nd call. Opportunistic read + `general` fallback handles the race. |
| CONCERN | Operator | C2: failures only at `logger.debug`, invisible in prod | Failure Path; Task 2; Verification | Change to `logger.warning`. |
| CONCERN | Skeptic | C3: dead "low confidence" logic; no confidence field | Technical Approach → C3 | Removed the wording; guard is `if action not in ACTION_EMOJI_MAP: action = "general"`. Fallback path reuses `_parse_json_response` fence-stripping. |
| CONCERN | Archaeologist | C4: revives pre-#677 Ollama intent design without justification | Technical Approach → "Why returning to intent classification is not the pre-#677 mistake" | #677's objections were latency (2-10s) and narrow vocab; reuse adds 0 latency / Haiku <1s, and small vocab is the safety property. |
| CONCERN | User | C5: 🤓/👨‍💻 may read flippant to a frustrated user | ACTION_EMOJI_MAP retune; Task 1/3 | `investigate_bug` leads with 👨‍💻; `🤓` removed from every category. |
| CONCERN | User | C6: 🤔 in 4/6 categories → indistinguishable from 👀 | ACTION_EMOJI_MAP retune; Task 1/3; Verification | `🤔` reserved for `answer_question`; `general` → 👀; tasks lead with 🫡/👍. |
| NIT | Operator | N1: softmax over scoreless candidates ≈ random.choice | Resolved via B1 | Favored the `random.choice` resolution. |
| NIT | User | N2: add manual human-eval acceptance step | Task 4 (validate) | Send bug/task/praise/question in a real chat; confirm reactions read correctly. |

---

## Resolution of Issue's Open Questions

The issue body posed four numbered open questions and required `/do-plan` to resolve them. Each is resolved here:

1. **Where does "intended action" get decided?** → **Option (a): reuse the receipt-time work-type classifier.** The reaction fires fire-and-forget at receipt, mapping the `bug/feature/chore/sdlc` result that `classify_work_type()` already computes to an action category — no new classification call (C1). Option (b) two-phase reaction-editing and (c) hybrid are deferred to **No-Gos**. This preserves the existing snappy 👀-then-contextual UX with zero added receipt-time LLM cost.
2. **What is the action vocabulary?** → A closed set of 6 categories (`investigate_bug`, `problem_solving`, `acknowledge_task`, `receive_praise`, `answer_question`, `general`) in `ACTION_EMOJI_MAP`, each mapped to a small list of *Telegram-valid* candidate emojis (retuned per C5/C6 so `🤔` is question-only and `general` → 👀). The category comes from the reused work-type, not embedding-over-content and not a new LLM call. See Technical Approach.
3. **Does the embedding approach stay?** → **No** for the message-reaction path. `find_best_emoji_for_message` is rewritten to map the reused work-type to an action and `random.choice` a candidate; it no longer embeds content against sentiment labels. `EMOJI_LABELS` and the embedding mechanism remain **only** for the intent-driven `find_best_emoji(feeling)` callers, whose contract is unchanged.
4. **Backward compatibility?** → The refactor is scoped strictly to `find_best_emoji_for_message` and its bridge call site. `find_best_emoji(feeling)` and its callers (`agent/constants.py`, `tools/send_telegram.py`, `tools/react_with_emoji.py`) are unaffected; a Verification check greps to confirm they still call `find_best_emoji`.

## Open Questions

None — all four issue open questions resolved above; the Telegram-reaction-whitelist constraint (🔍/🛠/🕸/💡 are invalid) is surfaced and worked around; and the NEEDS REVISION critique (B1, B2, C1-C6, N1-N2) has been fully addressed (see Critique Results). Ready to build.
