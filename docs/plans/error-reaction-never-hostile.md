---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1882
last_comment_id:
---

# Error/terminal reactions must never be hostile toward the user

## Problem

When a session ends in an error or non-clean terminal state, the bridge sets an emoji reaction on the **user's own triggering message**. That reaction is currently selected non-deterministically from a pool that includes outright hostile faces (`👎 🤬 😡 🤮`). In the reported incident, Thabiso's session (which actually *succeeded* — see #1881) set a 👎 on his message; re-running the same code path on the bridge machine now draws 😱. Either way, a thumbs-down / scream lands *at the person who messaged us*.

**Current behavior:**
- On `task.error` or a non-clean granite exit, the executor sets `emoji = REACTION_ERROR` (`agent/session_executor.py:2250-2256`).
- `REACTION_ERROR` is not a fixed emoji. It resolves lazily via `find_best_emoji("error occurred something went wrong")` (`agent/constants.py:31`), which **softmax-samples** the nearest match over the full `VALIDATED_REACTIONS` index — a pool that contains `👎 🤬 😡 🤮 😱 😭 😨 😢`.
- Result: the terminal-error reaction is both **hostile-reachable** and **non-deterministic** — it can't be reasoned about or tested, and it reads as blame directed at the user.

**Desired outcome:**
- A reaction placed on a user's message can never be mean/hostile. `👎 🤬 😡 🤮` are unreachable for any user-facing reaction, by construction.
- The terminal-error reaction is **deterministic** and apologetic/neutral (e.g. 🫡 "understood, on it"), not a semantic lottery.
- The fix reuses the existing precedent (`BLOCKED_REACTION_EMOJIS`, the 🖕 filter from PR #1505) rather than inventing a parallel mechanism.

## Freshness Check

**Baseline commit:** `8adc39fa`
**Issue filed at:** 2026-07-03T10:52:56Z
**Disposition:** Minor drift (line numbers only; all claims hold)

**File:line references re-verified:**
- `agent/session_executor.py:2178-2211` (reported) — reaction-selection block **drifted to `agent/session_executor.py:2250-2256`** (`task.error → REACTION_ERROR`; `_is_non_clean_granite_exit → REACTION_ERROR`); reaction set on user's message at line 2271. Claim holds.
- `agent/constants.py:28-101` — semantic resolver for `REACTION_ERROR` via `find_best_emoji("error occurred something went wrong")`, fallback 😢. Confirmed unchanged.
- `bridge/response.py:57-79` — `VALIDATED_REACTIONS` still contains the hostile block `😱 🤯 🤬 😢 😭 🤮 😨 😡` and `👎`. Confirmed.
- `tools/emoji_embedding.py:89` — `BLOCKED_REACTION_EMOJIS = frozenset({"🖕"})`, already applied as a candidate filter inside `find_best_emoji` at line 359. **New discovery — this is the extension point.**

**Cited sibling issues/PRs re-checked:**
- #1881 — still OPEN. It is the *cause* of the specific mis-fire (a succeeded session mislabeled `startup_unresolved`), but this issue is scoped to the reaction *policy* and is independent of #1881's classification fix.

**Commits on main since issue was filed (touching referenced files):**
- `d9cb76b1` "Fix session lifecycle notification gaps (#1877/#1884)" — added a failure-notification helper to `agent/session_executor.py`; **did not touch the reaction-selection block** (verified by reading the block at HEAD). Irrelevant to this fix.

**Active plans in `docs/plans/` overlapping this area:** none. (Nearby plans `emoji-embedding-reactions.md` and `reply-drop-terminus-granite-resume.md` touch reactions/granite but not the hostile-reaction policy.)

**Notes:** Only line numbers drifted. Corrected references used throughout the plan.

## Prior Art

- **PR #1505** — "fix(emoji): never react with 🖕 to user messages" (merged 2026-06-01). Introduced `BLOCKED_REACTION_EMOJIS` and the `if emoji in BLOCKED_REACTION_EMOJIS: continue` filter inside `find_best_emoji`. **This is the exact precedent to extend** — the deny-list mechanism already exists and is enforced; this plan adds the hostile faces to it.
- **PR #992** — "feat: terminal reactions via find_best_emoji (EmojiResult, lazy cache)" (merged 2026-04-15). Established the lazy `__getattr__` resolution of `REACTION_SUCCESS/COMPLETE/ERROR` in `agent/constants.py`. This plan pins `REACTION_ERROR` out of that semantic path.
- **PR #1700** — "action-intent emoji reactions" (merged 2026-06-15). Introduced `ACTION_EMOJI_MAP` (work-type → curated emoji lists). Verified: no `ACTION_EMOJI_MAP` entry contains a hostile emoji, so extending `BLOCKED_REACTION_EMOJIS` does not conflict with the existing consistency test.
- **PR #1314** — "Add user-visible stall reaction" (merged 2026-05-07). Precedent for deterministic pinned reaction constants (`REACTION_PROCESSING`, `REACTION_ABORT = 🫡`).

No prior attempt addressed the hostile-terminal-reaction policy specifically. No `## Why Previous Fixes Failed` section — this is the first fix for this defect.

## Research

No relevant external findings — purely internal (no external libraries, APIs, or ecosystem patterns). The fix is contained to two source modules and the existing in-repo reaction machinery. Phase 0.7 skipped.

## Data Flow

1. **Entry point:** A session finishes in `session_executor.py` (`await react_cb(...)` region, ~L2240).
2. **Branch selection (`agent/session_executor.py:2244-2269`):** `task.error` or `_is_non_clean_granite_exit(agent_session)` → `emoji = REACTION_ERROR`; else `REACTION_COMPLETE` / `REACTION_SUCCESS`; Teammate success → `None` (clear).
3. **Constant resolution (`agent/constants.py` `__getattr__` → `_resolve_terminal_emoji`):** For `REACTION_ERROR`, calls `find_best_emoji("error occurred something went wrong")`.
4. **Emoji selection (`tools/emoji_embedding.py:find_best_emoji`):** scores `VALIDATED_REACTIONS` embeddings, skipping `BLOCKED_REACTION_EMOJIS` (L359), then `_softmax_sample`s the top-K (non-deterministic) → an `EmojiResult`.
5. **Reaction set (`agent/session_executor.py:2271`):** `await react_cb(chat_id, telegram_message_id, emoji)` → `bridge.response.set_reaction` sets the emoji **on the user's own message**.

The bug lives at steps 3-4: an error feeling maps to a sampled member of the negative-faces cluster, which can be hostile.

## Appetite

**Size:** Small

**Team:** Solo dev, plus one validator pass.

**Interactions:**
- PM check-ins: 0-1 (only the emoji-choice Open Question)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. `find_best_emoji`'s embedding path requires `OPENROUTER_API_KEY`, but the fix's deterministic path (pinned `REACTION_ERROR` + block-list) does **not**, and the tests must run without it.

## Solution

### Key Elements

- **Pinned error reaction:** `REACTION_ERROR` becomes a fixed, non-hostile emoji (recommend 🫡) resolved without calling `find_best_emoji`. Deterministic and apologetic.
- **Extended hostile deny-list:** `BLOCKED_REACTION_EMOJIS` grows from `{🖕}` to also cover the hostile faces (`👎 🤬 😡 🤮`, plus 😱 — see Open Questions). Because `find_best_emoji` already filters this set out of every candidate, no semantically-resolved reaction (success/complete/processing) can ever draw a hostile face at a user. This is the issue's "USER_SAFE / HOSTILE deny-list" requirement, implemented via the established precedent.
- **Deterministic test:** asserts the pinned error emoji is fixed and safe, and that no terminal reaction constant can resolve to a hostile emoji.

### Flow

Session errors → executor sets `REACTION_ERROR` → constant resolves to the **fixed** 🫡 (no semantic draw) → `set_reaction` places 🫡 on the user's message. Success/complete reactions still resolve semantically, but `find_best_emoji` can no longer return any hostile face because the deny-list filters them out of candidate scoring.

### Technical Approach

1. **Pin `REACTION_ERROR` in `agent/constants.py`.** Route `REACTION_ERROR` to a fixed `EmojiResult(emoji="🫡")` and stop calling `find_best_emoji` for it. Preserve the existing contract: it must remain an `EmojiResult`, resolved lazily via the same `__getattr__` / `_TERMINAL_EMOJI_CACHE` machinery (so `from agent.constants import REACTION_ERROR` in `bridge/response.py` and the `patch("agent.session_executor.REACTION_ERROR", ...)` test seams keep working, and no import cycle is introduced). Implementation option: mark `REACTION_ERROR` as "pinned" in `_TERMINAL_EMOJI_CONFIG` (e.g. a third tuple element `pinned=True`, or a small `_PINNED_TERMINAL` set) so `_resolve_terminal_emoji` returns `EmojiResult(emoji=pinned)` directly, skipping the `find_best_emoji` branch. `REACTION_SUCCESS` / `REACTION_COMPLETE` keep semantic resolution (positive variety is desirable and now provably safe via the deny-list).
2. **Extend `BLOCKED_REACTION_EMOJIS` in `tools/emoji_embedding.py:89`.** Add the hostile faces to the frozenset. Update the inline comment to state the broadened intent ("never aim hostility at a user"). Keep `VALIDATED_REACTIONS` unchanged — those emojis remain valid Telegram reactions; they are simply unselectable by the resolver. Single source of truth for "hostile" lives here.
3. **Verify consistency.** The pinned 🫡 must be in `VALIDATED_REACTIONS` and NOT in `BLOCKED_REACTION_EMOJIS`. `ACTION_EMOJI_MAP` must contain no member of the extended block-list (already true; keep the existing `test_emoji_embedding.py:347` guard green).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_resolve_terminal_emoji` (`agent/constants.py:79`) has a broad `except Exception` that falls back to a hardcoded `EmojiResult`. For the pinned `REACTION_ERROR`, no `find_best_emoji` call occurs so this handler is not on its path — add a test asserting the pinned value is returned **even with `OPENROUTER_API_KEY` unset / embeddings unavailable** (the degraded environment must still yield 🫡, not 😢).
- [ ] The `react_cb` call site wraps `set_reaction` in `try/except Exception` logging a warning (`agent/session_executor.py:2272-2273`) — unchanged by this work; no new swallowing introduced.

### Empty/Invalid Input Handling
- [ ] `find_best_emoji` already returns `DEFAULT_EMOJI` (🤔) for empty/whitespace feeling — unchanged. The pinned error path takes no feeling string, so empty-input is not reachable for `REACTION_ERROR`.
- [ ] Add a test that `BLOCKED_REACTION_EMOJIS` filtering holds even if a hostile emoji were the top-scoring candidate (mock/inject scoring so a hostile face would win, assert it is skipped).

### Error State Rendering
- [ ] The user-visible output here IS the error reaction. Test asserts the error path renders 🫡 (deterministic), not a hostile face — this is the core acceptance test.

## Test Impact

- [ ] `tests/unit/test_worker_entry.py:236-262` — UPDATE: currently asserts `REACTION_ERROR` is an `EmojiResult` whose `.emoji in VALIDATED_REACTIONS`. Still passes with pinned 🫡 (🫡 ∈ VALIDATED_REACTIONS). Extend it to also assert `REACTION_ERROR.emoji not in BLOCKED_REACTION_EMOJIS` and equals the fixed pinned value.
- [ ] `tests/unit/test_session_executor_granite.py:722-724` — no change needed: it patches `REACTION_ERROR` with a sentinel object, independent of the constant's real value. Verify it still passes.
- [ ] `tests/integration/test_reply_delivery.py:130-133` — no change needed: asserts `REACTION_COMPLETE.emoji in VALIDATED_REACTIONS`; success/complete path unchanged. Verify still green.
- [ ] `tests/unit/test_emoji_embedding.py:68-70, 347-349` — UPDATE: extend the `BLOCKED_REACTION_EMOJIS` membership assertions to cover the new hostile faces; keep the `ACTION_EMOJI_MAP ∩ BLOCKED == ∅` guard (already satisfied).
- [ ] NEW: `tests/unit/test_reaction_never_hostile.py` (REPLACE/create) — deterministic assertions for the pinned error emoji and the hostile-face unreachability (see Success Criteria).

## Rabbit Holes

- **Rewriting `find_best_emoji` to accept a candidate allow-list parameter.** Tempting for "cleanliness," but the existing `BLOCKED_REACTION_EMOJIS` filter already does the job with a one-line set edit. A signature change ripples to every caller. Avoid.
- **Removing hostile emojis from `VALIDATED_REACTIONS` and rebuilding the embedding index.** Not needed — those emojis are genuinely valid Telegram reactions; blocking *selection* is sufficient and avoids an index rebuild. Out of scope.
- **Fixing the `startup_unresolved` misclassification** that made a succeeded session look like an error. That is #1881, a separate root cause. This plan only changes the reaction *policy*.
- **Redesigning the whole reaction taxonomy** (success vs complete vs error semantics). Leave the existing three-constant model intact; only pin ERROR and add the deny-list.

## Risks

### Risk 1: Pinning `REACTION_ERROR` breaks a test that expects semantic resolution
**Impact:** A test asserting `REACTION_ERROR` varies or calls `find_best_emoji` could fail.
**Mitigation:** Grep confirmed the only assertions are "is an `EmojiResult` in `VALIDATED_REACTIONS`" (`test_worker_entry.py`) and sentinel-patching (`test_session_executor_granite.py`). Both remain valid with a pinned 🫡. No test currently asserts error-reaction variability.

### Risk 2: The chosen pinned emoji reads wrong in context
**Impact:** 🫡 already means "steering abort acknowledged" (`REACTION_ABORT`), so error+abort could look similar.
**Mitigation:** Surfaced as an Open Question. Alternatives: 😢 (current fallback — apologetic, visually distinct from abort, but in the negative-faces block) or `None` (clear the processing reaction, leaving no negative signal). Recommend 🫡 as issue's first suggestion; defer final choice to the human.

### Risk 3: Extending the deny-list silently starves a legitimate reaction
**Impact:** If some code path *wanted* a negative face for a non-user target, blocking it would change behavior.
**Mitigation:** This system only ever sets reactions on user messages (verified: processing + terminal reactions all target `telegram_message_id`). No non-user reaction target exists, so a global block is correct. The `ACTION_EMOJI_MAP` consistency test guards against starving a curated candidate list.

## Race Conditions

No race conditions identified — emoji constant resolution is synchronous (`__getattr__` + dict cache) and the reaction is set with a single awaited `react_cb` call. The `_TERMINAL_EMOJI_CACHE` is populated on first access; concurrent first-access at worst recomputes an idempotent pinned value. No shared mutable state is written under contention.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1881] The granite `startup_unresolved` misclassification that made a *succeeded* session set an error reaction. That is a distinct root cause tracked in #1881; this plan only changes what emoji an error path uses, not whether the path is entered.

## Update System

No update system changes required — this is a purely internal code change (two source modules, no new dependencies, no config files, no Popoto model changes, no migration). Nothing to propagate via `/update`.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to the reaction-selection path. No new CLI entry point, no MCP surface, no `.mcp.json` change, no new bridge import. The reaction machinery is already wired; this plan only changes the emoji it picks.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reaction-semantics.md` to document: (a) `REACTION_ERROR` is a pinned, deterministic, non-hostile emoji; (b) `BLOCKED_REACTION_EMOJIS` is the single source of truth for reactions that must never target a user, now covering hostile faces.
- [ ] Cross-check `docs/features/emoji-embedding-reactions.md` for any statement that terminal-error reactions are semantically resolved and correct it.

### Inline Documentation
- [ ] Update the `BLOCKED_REACTION_EMOJIS` comment (`tools/emoji_embedding.py:89`) to state the broadened "never aim hostility at a user" intent.
- [ ] Update the `_TERMINAL_EMOJI_CONFIG` / module docstring in `agent/constants.py` to note that `REACTION_ERROR` is pinned (not semantically resolved).

## Success Criteria

- [ ] `REACTION_ERROR.emoji` is a fixed, non-hostile value (recommend 🫡), stable across repeated attribute access and across `find_best_emoji` availability (asserted with `OPENROUTER_API_KEY` absent).
- [ ] `BLOCKED_REACTION_EMOJIS ⊇ {👎, 🤬, 😡, 🤮}` (final membership per Open Question 2).
- [ ] `find_best_emoji` can never return a member of `BLOCKED_REACTION_EMOJIS` (asserted even when a hostile face would be the top candidate).
- [ ] No terminal reaction constant (`REACTION_ERROR/SUCCESS/COMPLETE`) resolves to a hostile emoji (parametrized deterministic test).
- [ ] `REACTION_ERROR.emoji ∈ VALIDATED_REACTIONS` and `∉ BLOCKED_REACTION_EMOJIS`.
- [ ] Existing tests remain green (`test_worker_entry.py`, `test_session_executor_granite.py`, `test_reply_delivery.py`, `test_emoji_embedding.py`).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (reaction-policy)**
  - Name: `reaction-builder`
  - Role: Pin `REACTION_ERROR`, extend `BLOCKED_REACTION_EMOJIS`, write deterministic tests, update inline docs.
  - Agent Type: builder
  - Resume: true

- **Validator (reaction-policy)**
  - Name: `reaction-validator`
  - Role: Verify pinned/deterministic error emoji, hostile-face unreachability, and that existing tests stay green.
  - Agent Type: validator
  - Resume: true

- **Documentarian (reaction-semantics)**
  - Name: `reaction-doc`
  - Role: Update `docs/features/reaction-semantics.md` and cross-check `emoji-embedding-reactions.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Pin the error reaction + extend the deny-list
- **Task ID**: build-reaction-policy
- **Depends On**: none
- **Validates**: tests/unit/test_reaction_never_hostile.py (create), tests/unit/test_emoji_embedding.py, tests/unit/test_worker_entry.py
- **Assigned To**: reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/constants.py`: mark `REACTION_ERROR` as pinned so it resolves to a fixed `EmojiResult(emoji="🫡")` (final emoji per Open Question 1) without calling `find_best_emoji`; preserve the lazy `__getattr__`/cache contract and EmojiResult type.
- In `tools/emoji_embedding.py`: extend `BLOCKED_REACTION_EMOJIS` to include the hostile faces; update the comment.
- Update inline docstrings/comments in both files.

### 2. Write deterministic tests
- **Task ID**: build-tests
- **Depends On**: build-reaction-policy
- **Assigned To**: reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_reaction_never_hostile.py` covering all Success Criteria assertions (pinned value, degraded-env stability, hostile unreachability, parametrized no-hostile-terminal-constant).
- Extend `tests/unit/test_emoji_embedding.py` block-list membership assertions; extend `tests/unit/test_worker_entry.py` error-constant assertions.

### 3. Validate
- **Task ID**: validate-reaction-policy
- **Depends On**: build-tests
- **Assigned To**: reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the new + affected tests; confirm all Success Criteria; confirm no hostile emoji reachable and error emoji deterministic.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-reaction-policy
- **Assigned To**: reaction-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reaction-semantics.md`; cross-check `docs/features/emoji-embedding-reactions.md`.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full affected suite + lint/format; verify docs updated; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New reaction tests pass | `pytest tests/unit/test_reaction_never_hostile.py -q` | exit code 0 |
| Affected emoji/worker tests pass | `pytest tests/unit/test_emoji_embedding.py tests/unit/test_worker_entry.py -q` | exit code 0 |
| Error reaction is pinned (no semantic draw) | `grep -n "find_best_emoji" agent/constants.py \| grep -i error` | exit code 1 |
| Hostile faces are in the deny-list | `python -c "from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS as b; assert {'\U0001f44e','\U0001f92c','\U0001f621','\U0001f92e'} <= b"` | exit code 0 |
| Error reaction is not hostile | `python -c "from agent.constants import REACTION_ERROR; from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS as b; assert REACTION_ERROR.emoji not in b"` | exit code 0 |
| Lint clean | `python -m ruff check agent/constants.py tools/emoji_embedding.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/constants.py tools/emoji_embedding.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Pinned error emoji:** Recommend 🫡 ("understood, on it" — apologetic, in `VALIDATED_REACTIONS`, not hostile). It already doubles as `REACTION_ABORT`, so error and abort would look similar. Acceptable, or prefer 😢 (visually distinct, apologetic, but a negative-block face) or `None` (clear the processing reaction, leaving no negative signal)?
2. **Deny-list membership:** Mandatory to block `👎 🤬 😡 🤮`. The issue says "arguably no 😱 😭 😨" as well. Recommend also blocking 😱 (scream reads as alarm/blame). Block the milder 😭 😨 😢 too, or keep those selectable for non-error semantic reactions?
