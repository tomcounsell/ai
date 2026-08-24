---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1961
last_comment_id:
---

# Fix Duplicate Reaction Emoji in Reply-Delivery Constants

## Problem

The Telegram bridge uses emoji reactions as lightweight signals on messages
(👀 received, 🤔 error, 👏 completed, etc.). The invariant that every reaction
constant maps to a distinct glyph is guarded by
`tests/integration/test_reply_delivery.py::TestReactionEmojiSelection::test_reaction_constants_are_distinct`,
so reaction-based routing never has to disambiguate two constants that share a glyph.

**Current behavior:**
`REACTION_PROCESSING` (`bridge/response.py:112`, hardcoded `"🤔"`) and
`REACTION_ERROR` (`agent/constants.py:75`, pinned to `"🤔"`) resolve to the same
glyph on every run. `REACTION_ERROR`'s pin equals `DEFAULT_EMOJI` (also 🤔), so
the collision is deterministic. The distinctness test fails:

```
AssertionError: assert 4 == 5   # from the issue's run
# both REACTION_PROCESSING and REACTION_ERROR are 🤔
```

**Desired outcome:**
The five reaction constants the test collects map to distinct glyphs; the
distinctness test passes. `REACTION_ERROR`'s deliberate 🤔 pin is preserved.

## Freshness Check

**Baseline commit:** `01214eac`
**Issue filed at:** 2026-07-09T04:42:02Z (references main HEAD `0d3180f2`; main has since advanced to `01214eac`)
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/response.py:112` — `REACTION_PROCESSING = "🤔"` — still holds (hardcoded 🤔).
- `agent/constants.py:75` — `REACTION_ERROR` pinned to `\U0001f914` (🤔) — still holds.
- `tests/integration/test_reply_delivery.py:159-181` — test collects
  `[REACTION_RECEIVED, REACTION_PROCESSING, str(REACTION_SUCCESS), str(REACTION_COMPLETE), str(REACTION_ERROR)]` — still holds.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=<createdAt> -- bridge/response.py agent/constants.py` is empty.
The collision code is unchanged since filing.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The *exact* failing set is NOT stable across runs because
`REACTION_SUCCESS` and `REACTION_COMPLETE` are resolved by `find_best_emoji()`
(a nondeterministic semantic draw over `VALIDATED_REACTIONS`). Three observed
runs produced: `[👀,🤔,🫡,👏,🤔]` (issue body), `[👀,🤔,👏,👏,🤔]`
(SUCCESS==COMPLETE==👏 by chance), and `SUCCESS=👍, COMPLETE=🏆` (direct call).
The only *guaranteed, always-present* collision is `REACTION_PROCESSING` vs
`REACTION_ERROR` (both 🤔) — that is what this fix targets. SUCCESS/COMPLETE
colliding with each other is a separate, latent flakiness of the semantic
resolver (see Risks); it is out of scope for this Small fix.

## Prior Art

- **PR #1893 (issue #1882)**: "Pin error reaction to 🤔 and block hostile faces" —
  pinned `REACTION_ERROR` to 🤔 so a terminal-failure reaction is never hostile
  and never a semantic lottery. This is the change that created the deterministic
  collision: `REACTION_PROCESSING` was already hardcoded 🤔, and before the pin
  `REACTION_ERROR` was semantically resolved so it usually differed by chance.
  **Do not undo this pin** — it is deliberate and documented.
- **PR #992**: "terminal reactions via find_best_emoji (EmojiResult, lazy cache)" —
  introduced the semantic resolution for SUCCESS/COMPLETE/ERROR. Explains why
  those three are `EmojiResult` objects while RECEIVED/PROCESSING are plain strings.
- **PR #1700 (issue #1512)**: action-intent emoji vocabulary refactor — established
  that reaction glyphs signal the agent's intended action; keep the replacement
  glyph semantically sensible for a "processing/working" state.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Left This Bug |
|-----------|-------------|----------------------|
| PR #1893 | Pinned `REACTION_ERROR` to 🤔 (`DEFAULT_EMOJI`) to guarantee a safe, non-hostile terminal-failure glyph. | Correct for its own goal, but it did not check that `REACTION_PROCESSING` was already hardcoded to 🤔. Pinning ERROR to the same glyph turned a previously-rare chance collision into a guaranteed one that the distinctness test now catches every run. |

**Root cause pattern:** Two independently-owned constants (`REACTION_PROCESSING`
in `bridge/response.py`, `REACTION_ERROR` in `agent/constants.py`) both claim 🤔
with no single source of truth cross-checking them. The distinctness test is the
cross-check; the fix is to give `REACTION_PROCESSING` a distinct validated glyph.

## Data Flow

Isolated constant definition — no multi-component data flow to trace. The change
is a single literal in `bridge/response.py`. `REACTION_PROCESSING` is defined
there and referenced only by the distinctness test (see Test Impact); it has no
live call sites in bridge or agent code.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is a single-glyph literal change)
- Review rounds: 1 (confirm glyph choice + test pass)

## Prerequisites

No prerequisites — this work has no external dependencies. The distinctness test
runs without network or API access (it exercises the pinned/hardcoded paths;
`find_best_emoji` results are irrelevant to the collision being fixed).

## Solution

### Key Elements

- **`REACTION_PROCESSING` literal** (`bridge/response.py:112`): change from `"🤔"`
  to a distinct glyph that is present in `VALIDATED_REACTIONS`, is not
  `DEFAULT_EMOJI` (🤔), and is semantically appropriate for a "processing/working"
  state. Recommended: `"🤓"` (studious/working — clean single codepoint, clearly
  distinct). Acceptable alternatives, all confirmed in `VALIDATED_REACTIONS`:
  `"👨‍💻"` (computing/working) or `"🤨"`.
- **`REACTION_ERROR`** (`agent/constants.py:75`): left untouched — its 🤔 pin is
  deliberate (never hostile, never a semantic lottery) and documented in the
  module header.

### Flow

Message arrives → bridge assigns 👀 (RECEIVED) → work proceeds → terminal
reaction assigned (SUCCESS / COMPLETE / ERROR). `REACTION_PROCESSING` is a
defined-but-unused "default thinking" constant retained for the distinctness
contract; changing its literal has no runtime routing effect.

### Technical Approach

- Single-line literal change in `bridge/response.py`. No logic changes, no new
  imports, no signature changes.
- Confirm the chosen glyph is a member of `VALIDATED_REACTIONS` (Telegram only
  accepts a fixed reaction set) and is not in `INVALID_REACTIONS`.
- Verify the chosen glyph is a single codepoint or an explicitly-validated ZWJ
  sequence (avoid U+FE0F variation-selector forms, which `INVALID_REACTIONS`
  documents as rejected).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope — the change is a constant literal; there
  are no `try/except` blocks in the edited region of `bridge/response.py`.

### Empty/Invalid Input Handling
- [ ] Not applicable — no function receives input here. The one guard that
  matters is glyph membership: the replacement must be in `VALIDATED_REACTIONS`.
  Covered by the existing `test_reaction_received_is_valid`-style checks and by
  adding an assertion (see Test Impact) that `REACTION_PROCESSING in VALIDATED_REACTIONS`.

### Error State Rendering
- [ ] Not applicable — `REACTION_PROCESSING` has no user-visible rendering path
  (no live call sites). The distinctness test is the observable behavior.

## Test Impact

- [ ] `tests/integration/test_reply_delivery.py::TestReactionEmojiSelection::test_reaction_constants_are_distinct` — UPDATE (no code change needed to the test): it will pass once `REACTION_PROCESSING` is distinct. No edits required unless the assertion message needs refresh; leave as-is.
- [ ] `tests/integration/test_reply_delivery.py::TestReactionEmojiSelection` — ADD: a small assertion that `REACTION_PROCESSING in VALIDATED_REACTIONS` (mirrors `test_reaction_received_is_valid` at line 148) so a future edit to a non-validated glyph is caught directly rather than only via Telegram rejection at runtime.

No other existing tests reference `REACTION_PROCESSING` (grep confirms only the
definition and this test file).

## Rabbit Holes

- **Do not** rework `find_best_emoji` or the semantic resolution of
  SUCCESS/COMPLETE to make the whole distinctness test deterministic. That is a
  larger, separate concern (the resolver can draw duplicate glyphs by chance) and
  expands a one-line fix into resolver surgery.
- **Do not** unpin `REACTION_ERROR` or change its 🤔 — PR #1893 pinned it
  deliberately.
- **Do not** consolidate the two constants' ownership into a shared module in
  this pass; the collision is fixed by a distinct literal without a refactor.

## Risks

### Risk 1: Chosen glyph is not a valid Telegram reaction
**Impact:** Runtime `ReactionInvalidError` if the constant were ever sent.
**Mitigation:** Pick from `VALIDATED_REACTIONS` (all validated 2026-02-13); add
the `REACTION_PROCESSING in VALIDATED_REACTIONS` assertion so CI catches a bad
glyph. `🤓`, `👨‍💻`, and `🤨` are all confirmed members.

### Risk 2: Latent SUCCESS/COMPLETE semantic collision (pre-existing, out of scope)
**Impact:** The distinctness test could still flake in an environment where
`find_best_emoji` happens to return the same glyph for both the "success" and
"complete" feeling phrases (observed once: both drew 👏). This is independent of
the 🤔 fix.
**Mitigation:** Out of scope for this Small fix — noted here for the record. If
the test proves flaky in CI after this fix, file a separate issue to either pin
SUCCESS/COMPLETE fallbacks distinctly or make the test resilient to semantic
draws. This fix removes the *deterministic* failure; it does not claim to remove
the resolver's chance-collision.

## Race Conditions

No race conditions identified — the change is a synchronous, single-threaded
module-level constant assignment with no shared mutable state.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Making the SUCCESS/COMPLETE semantic resolver deterministic /
  collision-proof — *if* the test flakes after this fix, this must be filed as
  its own issue before work begins; do not fold it into this plan. (No issue
  filed yet; this entry is advisory context, not a tracked deferral — the fix
  here targets only the deterministic 🤔 collision.)

## Update System

No update system changes required — this feature is purely internal (a single
constant literal). No new dependencies, config, or migration steps to propagate
via `/update`.

## Agent Integration

No agent integration required — this is a bridge-internal constant change. No new
MCP surface, no `.mcp.json` change, no new bridge import. `REACTION_PROCESSING`
is a module-level constant already resident in `bridge/response.py`.

## Documentation

No documentation changes needed — this is a bug fix to an existing constant, not
a new capability. There is no `docs/features/` page dedicated to reaction
constants, and the behavior contract (distinct glyphs) is documented by the
distinctness test itself. Only the inline comment on the changed constant is
touched.

### Inline Documentation
- [ ] Update the inline comment on `REACTION_PROCESSING` if the glyph's meaning
  shifts (e.g. `# Working/processing indicator (distinct from ERROR's pinned 🤔)`).

## Success Criteria

- [ ] `REACTION_PROCESSING` maps to a glyph distinct from all other reaction constants.
- [ ] The chosen glyph is a member of `VALIDATED_REACTIONS`.
- [ ] `REACTION_ERROR` still pins to 🤔 (unchanged).
- [ ] `pytest tests/integration/test_reply_delivery.py::TestReactionEmojiSelection::test_reaction_constants_are_distinct -q -n0` passes.
- [ ] Tests pass (`/do-test`).
- [ ] No documentation changes needed beyond the inline comment (`/do-docs` no-op).

## Team Orchestration

Single-task fix. The lead assigns one builder; no parallel fan-out needed.

### Team Members

- **Builder (reaction-constant)**
  - Name: reaction-fixer
  - Role: Change the `REACTION_PROCESSING` literal to a distinct validated glyph and add the membership assertion.
  - Agent Type: builder
  - Resume: true

- **Validator (reaction-constant)**
  - Name: reaction-validator
  - Role: Confirm the distinctness test passes and no other test regressed.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Change REACTION_PROCESSING to a distinct glyph
- **Task ID**: build-reaction-constant
- **Depends On**: none
- **Validates**: tests/integration/test_reply_delivery.py::TestReactionEmojiSelection
- **Assigned To**: reaction-fixer
- **Agent Type**: builder
- **Parallel**: false
- Edit `bridge/response.py:112`: change `REACTION_PROCESSING = "🤔"` to a distinct glyph from `VALIDATED_REACTIONS` (recommend `"🤓"`; `"👨‍💻"` or `"🤨"` acceptable).
- Update the inline comment to note it is the working/processing indicator, distinct from ERROR's pinned 🤔.
- Add an assertion in `TestReactionEmojiSelection` (near line 148) that `REACTION_PROCESSING in VALIDATED_REACTIONS`.
- Do NOT touch `REACTION_ERROR` in `agent/constants.py`.

### 2. Validate
- **Task ID**: validate-reaction-constant
- **Depends On**: build-reaction-constant
- **Assigned To**: reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_reply_delivery.py::TestReactionEmojiSelection -q -n0` and confirm all pass.
- Confirm `git diff` touches only `bridge/response.py` and (optionally) the test file — not `agent/constants.py`.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Distinctness test passes | `pytest tests/integration/test_reply_delivery.py::TestReactionEmojiSelection::test_reaction_constants_are_distinct -q -n0` | exit code 0 |
| Reaction selection suite passes | `pytest tests/integration/test_reply_delivery.py::TestReactionEmojiSelection -q -n0` | exit code 0 |
| PROCESSING no longer 🤔 | `grep -n 'REACTION_PROCESSING = "🤔"' bridge/response.py` | exit code 1 |
| ERROR pin untouched | `grep -c '_TerminalEmojiConfig(None, "\\U0001f914", True)' agent/constants.py` | output contains 1 |
| Format clean | `python -m ruff format --check bridge/response.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Glyph choice for `REACTION_PROCESSING`: recommendation is `🤓` (clean single
   codepoint, semantically "working/thinking", clearly distinct). `👨‍💻`
   (computing) and `🤨` are equally valid alternatives. Confirm the preferred
   glyph, or accept the `🤓` recommendation.
