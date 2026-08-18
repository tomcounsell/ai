---
status: Ready
type: bug
appetite: Small
owner: dev
created: 2026-08-18
tracking: https://github.com/tomcounsell/ai/issues/2846
last_comment_id: 5324621012
revision_applied: true
revision_applied_at: 2026-08-18T07:07:43Z
---

# Fix stale tests on main: steering-call assertions (#2846) and MessageDraft expectations kwarg (#2822)

## Problem

`main` is red at ~30 unit-test nodes. This lane clears 25 of them across two
issues of the same root class: a merged rename/deletion that missed its test
seams. Production code is correct in every case; the tests are stale.

**Current behavior:**

- **#2846 — 15 unit tests fail** on `main` (15 failed, 54 passed), in two
  independent clusters:
  - **Cluster A (14 tests)** — stale `assert_called_*_with` assertions on
    `push_steering_message` after #2642 added a `room_id: str | None = None`
    kwarg. `assert_called_once_with(...)` is order- and kwarg-exact, so the new
    kwarg breaks them.
  - **Cluster B (1 test)** — `tests/unit/test_reaction_never_hostile.py:146`
    patches `tools.emoji_embedding._custom_embedding_cache`, a module global
    deleted by #2835. `mock.patch` fails closed on the missing attribute.
- **#2822 — 11 unit tests fail** in `tests/unit/test_context_recall_wiring.py`
  (11 failed, 16 passed): `TypeError: MessageDraft.__init__() got an unexpected
  keyword argument 'expectations'`. #2814 renamed the Job promise APIs to
  expectations; the tests still construct `MessageDraft(expectations=...)`.

**Desired outcome:**

All 26 named node ids green on `main`, with the assertions updated to encode the
*current* intended behavior of each call site (including which steering leg it
targets), not merely loosened until they stop failing. No production-source
change.

## Freshness Check

**Baseline commit:** `836acc8b20ed3a3b0bff3be224a4786fc5df0d8c`
**Issue #2846 filed at:** 2026-08-17T20:19:43Z
**Issue #2822 filed at:** 2026-08-16T05:07:25Z
**Disposition:** Unchanged (with two corrections from the #2846 triage comment incorporated below)

**File:line references re-verified:**
- `agent/steering.py:176` — `push_steering_message` signature still has
  `room_id: str | None = None` — holds.
- `tools/emoji_embedding.py` — `_custom_embedding_cache` is gone; only
  `_embedding_cache` survives at line 244 — holds. `find_best_emoji`'s docstring
  (lines 331-337) documents the custom-emoji branch removal as deliberate.
- `bridge/message_drafter.py:193` — `MessageDraft` has no `expectations` field;
  the field is `open_questions`, deliberately named apart from the Job
  obligation primitive (#2708) — holds.
- `bridge/telegram_bridge.py:949` — `_ack_steering_routed` declares
  `room_id: str | None = None`; line 1032 passes `room_id=room_id` through to
  `push_steering_message` — holds.
- `scripts/steer_child.py:119-129` — abort path passes a literal `room_id=None`
  with an inline comment (abort must die with its session) — holds.
- `tools/valor_session.py:1150` — resume path passes
  `room_id=room_id_for_session(session)` — holds.

**Cited sibling issues/PRs re-checked:**
- #2642 — CLOSED 2026-08-17. Added the `room_id` kwarg. Per the triage comment,
  it updated **4** test files (not 7 as the issue body states): `test_steering.py`,
  `test_context_recall_wiring.py`, `test_public_api_contract.py`,
  `test_steering_writer_census.py`.
- #2835 — MERGED 2026-08-17. Deleted the custom-emoji embedding path and
  `_custom_embedding_cache`.
- #2814 / #2708 — MERGED/CLOSED 2026-08-14. Renamed Job promise APIs to
  expectations.
- #2847 — CLOSED as a duplicate of #2846 (same 15 node IDs, same clusters).

**Commits on main since the issues were filed (touching referenced files):**
- `ac190fb26` (#2642) — the root-cause commit for Cluster A; already on main.
- No *new* commits since the issues were filed have touched the failing test
  files or the referenced production modules.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Two corrections from the #2846 triage comment (id 5319860483) are
incorporated into the Technical Approach:
1. The issue body's third Cluster-A snippet is mislabeled — the
   `'core-sess', 'continue', 'resume:cli'` output is from
   `test_valor_session_resume_release.py:622`, not `test_steer_child.py`. The
   real `test_steer_child.py:89` fails with a keyword-style diff adding
   `room_id=None`.
2. `room_id=None` is **load-bearing** in `steer_child` (a literal with an intent
   comment) but a **fixture artifact** in the bridge tests (the parameter
   default falling through). The two call sites need different fixes — see
   Technical Approach.

## Prior Art

- **#2642** — "Flip steering writers to the Room key" — added the `room_id`
  kwarg and updated 4 test files but missed the 3 files in this lane. The
  production behavior is intended; only the missed test seams are stale.
- **#2835** — "Replace the dead stall reaction with a session liveness tick
  counter" — deleted the custom-emoji embedding path and `_custom_embedding_cache`
  but missed the patch line in `test_reaction_never_hostile.py`.
- **#2814 / #2708** — "Expectations as the single obligation primitive on Job" —
  renamed promise APIs to expectations but missed the `MessageDraft(expectations=...)`
  constructions in `test_context_recall_wiring.py`.
- **#2847** — duplicate of #2846, closed. Its accurate caller list and the
  demonstrated-red caveat for Cluster B are carried forward here.

## Research

No relevant external findings — this is a purely internal test-side fix with no
external libraries, APIs, or ecosystem patterns involved. Proceeding with
codebase context.

## Spike Results

No spikes needed — every assumption was resolved by direct code-read during the
freshness check. The fix shape for each cluster is fully determined by the
production call sites.

## Data Flow

Not applicable — this is a test-only change. No production data flows are
modified. The steering-inbox write path (`push_steering_message` → Redis list)
and the emoji-selection path are unchanged; only the assertions that observe
them are corrected.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2642 | Added `room_id` kwarg, updated 4 test files | Missed 3 test files that assert on `push_steering_message` with kwarg-exact `assert_called_once_with` |
| #2835 | Deleted `_custom_embedding_cache` | Missed the `patch(...)` line in `test_reaction_never_hostile.py` |
| #2814 | Renamed promise APIs to expectations | Missed the `MessageDraft(expectations=...)` constructions in `test_context_recall_wiring.py` |

**Root cause pattern:** A merged signature/API change that updated *some* test
seams but not all. The merge gate did not catch the stragglers. This lane fixes
the stragglers; the open question of a caller-sweep guard in the build stage is
deferred (see No-Gos).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none — production code is untouched.
- **Coupling:** none.
- **Data ownership:** none.
- **Reversibility:** trivial — test-only edits, revert by reverting the test
  files.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It is a test-only
edit against existing production code.

## Solution

### Key Elements

- **Cluster A1 — bridge-ack tests (8 tests, `test_bridge_ack_steering_routed.py`):**
  pass an explicit `room_id` into `_ack_steering_routed` and assert it arrives
  unchanged at `push_steering_message`. This pins the pass-through (the live
  Room-leg path) rather than the `None` fixture default. At most one case retains
  the `None` default to cover the legacy fallback. Also assert the
  `context_advisory` push rides the same leg as the human message.
- **Cluster A2 — steer_child abort test (1 test, `test_steer_child.py::test_abort_flag`):**
  add `room_id=None` to the assertion. This `None` is load-bearing (a literal
  with an intent comment: an abort must die with its session).
- **Cluster A3 — resume tests (5 tests, `test_valor_session_resume_release.py`):**
  give the mocked session a real `project_key` string so the derived room id is
  assertable, then assert the concrete room-id string.
- **Cluster B — reaction test (1 test, `test_reaction_never_hostile.py`):**
  delete the `patch("tools.emoji_embedding._custom_embedding_cache", {})` line.
  The surviving `_embedding_cache` patch still drives the hostile-candidate path.
- **Cluster C — context-recall tests (11 tests, `test_context_recall_wiring.py`):**
  remove the `expectations=None` kwarg from the 5 `MessageDraft(...)`
  constructions.

### Flow

Red `main` (26 stale nodes) → update stale assertions to encode current intended
behavior → all 26 green via the reproduction commands → `main` unblocked for the
fleet.

### Technical Approach

**Cluster A1 — bridge-ack tests (`test_bridge_ack_steering_routed.py`, 8 tests).**
The helper `_ack_steering_routed(session_id, text, sender_name, ..., room_id=None)`
passes `room_id=room_id` through to `push_steering_message` (line 1032). The
tests currently call the helper without `room_id`, so `None` is the parameter
default — a fixture artifact, not intent. Per the triage correction, do **not**
assert `room_id=None` here (that would pin the artifact and leave the live
Room-leg pass-through uncovered). Instead:

- Pass an explicit `room_id` (e.g. `"test|system"`) into `_ack_steering_routed`
  in the 8 failing tests and assert it arrives unchanged at
  `push_steering_message` (i.e. `assert_called_once_with(..., room_id="test|system")`).
- Keep **exactly one** case calling the helper without `room_id` and asserting
  `room_id=None` to cover the legacy fallback (a floor, not a ceiling — the
  fallback must stay covered).
- The `context_advisory` push (line 1066) is documented as riding the *same leg*
  as the human message. No current test passes `context_advisory`, so the
  advisory push never fires. Add `context_advisory="advisory text"` to **one**
  helper call (e.g. the same case that asserts the explicit `room_id`
  pass-through) and assert `push.call_args_list` has **two** calls, both with
  `room_id="test|system"` (human message + advisory), pinning the same-leg
  invariant.

**Cluster A2 — steer_child abort test (`test_steer_child.py::test_abort_flag`, 1 test).**
`scripts/steer_child.py:119-129` passes a literal `room_id=None` with an inline
comment (abort is destructive and non-idempotent, must die with its session).
Add `room_id=None` to the keyword-style assertion:
`assert_called_once_with(session_id="child-001", text="stop everything",
sender="pm", is_abort=True, room_id=None)`.

**Cluster A3 — resume tests (`test_valor_session_resume_release.py`, 5 tests).**
`tools/valor_session.py:1150` passes `room_id=room_id_for_session(session)`.
`room_id_for_session` derives `room_id(str(project_key), addressee)` where
`addressee` is `"system"` for a chatless session. The mocked sessions have no
`project_key`, so the derived id is a `MagicMock` repr
(`"<MagicMock name='mock.project_key'>|system"`). Fix — **both** session
helpers must set a real `project_key`, because the 5 tests use two different
helpers:

- **`_make_session` (line 45)** — used by 4 of the 5 resume tests
  (`test_transitions_to_pending_and_appends_steering` line ~182,
  `test_killed_with_uuid_resumes` line ~292,
  `test_failed_with_uuid_resumes` line ~303,
  `test_abandoned_with_uuid_resumes` line ~458). Set `s.project_key = "test"`
  (a real string). This makes the derived room id `"test|system"` and is safe —
  no test in the file asserts on `project_key` being absent.
- **`TestResumeSessionCore._make_mock_session` (line 500)** — used by the 5th
  test, `test_steering_push_before_transition` (line ~622, `resume:cli` source).
  This helper sets no `project_key`, so without a fix here the derived room id
  stays a `MagicMock` repr and the test stays red. Set `s.project_key = "test"`
  in this helper too (lines 506-511), and update line 622 to
  `mock_push.assert_called_once_with("core-sess", "continue", "resume:cli", room_id="test|system")`.

- Update all 5 resume assertions to include `room_id="test|system"`.

**Cluster B — reaction test (`test_reaction_never_hostile.py`, 1 test).**
Delete line 146:
`patch("tools.emoji_embedding._custom_embedding_cache", {})`.
The preceding line 145 `patch("tools.emoji_embedding._embedding_cache", fake_cache)`
still exists and drives the hostile-candidate skip. Per the triage comment,
confirm the test still fails when the block filter is removed (i.e. it is not
passing vacuously) — the guard (#1882) has been erroring in setup since
2026-08-17.

**Cluster C — context-recall tests (`test_context_recall_wiring.py`, 11 tests).**
Remove the `expectations=None` kwarg from the 5 `MessageDraft(...)` constructions
at lines 56, 191, 231, 294, 322. `MessageDraft` has no `expectations` field; the
field is `open_questions`. This is a pure deletion of an invalid kwarg — no
behavior change.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this is a test-only edit; no production
exception-handling code is touched.

### Empty/Invalid Input Handling
Not applicable — no new or modified functions; the tests exercise existing
production behavior unchanged.

### Error State Rendering
Not applicable — no user-visible output is involved.

## Test Impact

- [ ] `tests/unit/test_bridge_ack_steering_routed.py` — UPDATE: 8 tests pass an
      explicit `room_id` into `_ack_steering_routed` and assert it arrives
      unchanged at `push_steering_message`; at most one case retains the `None`
      default for the legacy fallback; assert the `context_advisory` same-leg
      invariant.
- [ ] `tests/unit/test_steer_child.py::TestSteerChild::test_abort_flag` — UPDATE:
      add `room_id=None` to the assertion (load-bearing literal).
- [ ] `tests/unit/test_valor_session_resume_release.py` — UPDATE: set
      `s.project_key = "test"` in **both** `_make_session` (line 45) and
      `TestResumeSessionCore._make_mock_session` (line 500); add
      `room_id="test|system"` to the 5 resume assertions (incl. line 622
      `test_steering_push_before_transition`).
- [ ] `tests/unit/test_reaction_never_hostile.py` — UPDATE: delete the
      `_custom_embedding_cache` patch line (line 146).
- [ ] `tests/unit/test_context_recall_wiring.py` — UPDATE: remove the
      `expectations=None` kwarg from the 5 `MessageDraft(...)` constructions.

## Rabbit Holes

- **Adding a caller-sweep guard to the build stage.** The open question in #2846
  ("is there a cheap guard that would have caught this at merge time?") is
  tempting but is a separate process-improvement concern, not part of fixing the
  red tests. Defer it.
- **Refactoring `_ack_steering_routed` or `push_steering_message`.** Production
  code is correct; do not touch it. Any change here is scope creep.
- **Re-verifying the full 30-node red set.** This lane owns 26 nodes (#2846's 15
  + #2822's 11). The remaining ~3 residuals belong to #2852 and are out of scope.

## Risks

### Risk 1: Bridge-ack tests assert the wrong leg
**Impact:** The 8 bridge tests pin a fixture artifact (`room_id=None`) instead of
the live Room-leg pass-through, leaving the actual behavior uncovered and
repeating the "loosened until they stop failing" failure mode.
**Mitigation:** Follow the triage correction: pass an explicit `room_id` into the
helper and assert it arrives unchanged. This pins the pass-through, not the
default.

### Risk 2: `project_key` change breaks other tests
**Impact:** Adding `s.project_key = "test"` to `_make_session` (and
`_make_mock_session`) could affect tests that rely on `project_key` being absent.
**Mitigation:** Verified no test in `test_valor_session_resume_release.py`
references `project_key`. Run the full file after the change to confirm.

### Risk 3: Cluster B test passes vacuously after the patch-line deletion
**Impact:** Deleting the `_custom_embedding_cache` patch could hollow the
hostile-candidate test if the surviving `_embedding_cache` patch no longer drives
it.
**Mitigation:** Per the triage comment, confirm the test fails when the block
filter is removed (demonstrated-red), not just that it passes with the line
deleted.

## Race Conditions

No race conditions identified — all operations are synchronous, single-threaded
test edits against existing production code. No shared mutable state is
introduced.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2852] The remaining ~3 red nodes on `main` not owned by this
  lane are tracked in #2852 and are out of scope here.
- [SEPARATE-SLUG #2846] Adding a caller-sweep guard to the build stage (the
  open question in #2846) is a process-improvement concern deferred to a
  follow-up; this lane only fixes the stale tests.

## Update System

No update system changes required — this is a purely internal test-side fix.
No new dependencies, config files, or migration steps. The `/update` skill and
`scripts/update/` are unaffected.

## Agent Integration

No agent integration required — this is a test-only change. No new CLI entry
point, no bridge import, no MCP surface. The agent's existing surfaces
(Telegram bridge, `tools/`) are untouched.

## Documentation

### Feature Documentation
No documentation changes needed — this is a test-only fix that restores `main`
to green. No new feature, no behavior change, no user-facing surface. The
steering-leg and emoji-embedding behavior is already documented in the
production docstrings and `docs/features/session-steering.md`.

- [ ] No `docs/features/` change required — steering-leg behavior already
      documented in `docs/features/session-steering.md` (test-only fix).

### Inline Documentation
- [ ] No inline doc changes needed — production code is untouched; the test
      assertions are self-documenting once they encode the intended leg.

## Success Criteria

- [ ] All 15 #2846 node ids pass via the reproduction command (a superset of
      the 15 named nodes — the named nodes are the gate):
      `./scripts/pytest-clean.sh tests/unit/test_bridge_ack_steering_routed.py
      tests/unit/test_reaction_never_hostile.py::TestFindBestEmojiNeverHostile::test_hostile_top_candidate_is_skipped
      tests/unit/test_steer_child.py::TestSteerChild::test_abort_flag
      tests/unit/test_valor_session_resume_release.py -p no:randomly`
- [ ] All 11 #2822 node ids pass:
      `./scripts/pytest-clean.sh tests/unit/test_context_recall_wiring.py -q`
- [ ] Cluster A assertions pin the intended steering leg: bridge tests assert an
      explicit `room_id` pass-through (incl. the `context_advisory` same-leg
      invariant); steer_child asserts `room_id=None` (load-bearing); resume tests
      assert a concrete room-id string (`"test|system"`), not a `MagicMock` —
      including `test_steering_push_before_transition` (line 622).
- [ ] Caller-sweep grep clean: no `push_steering_message` assertion lacks the
      intended `room_id`; no `expectations=` remains in
      `test_context_recall_wiring.py`.
- [ ] No production-source change.
- [ ] No `Co-Authored-By` trailers on the commits.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — no changes needed, confirmed above.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.
The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (test-fixer)**
  - Name: test-fixer
  - Role: Apply the test edits across all five clusters
  - Agent Type: builder
  - Resume: true

- **Validator (test-validator)**
  - Name: test-validator
  - Role: Verify all 26 node ids pass and no production code changed
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `test-engineer` - Test implementation and strategy
- `documentarian` - Documentation updates
- `plan-maker` - Planning subagent
- `frontend-tester` - Browser testing

## Step by Step Tasks

### 1. Fix Cluster A1 — bridge-ack tests
- **Task ID**: build-bridge-ack
- **Depends On**: none
- **Description**: In `tests/unit/test_bridge_ack_steering_routed.py`, update the
  8 failing tests to pass an explicit `room_id` (e.g. `"test|system"`) into
  `_ack_steering_routed` and assert it arrives unchanged at
  `push_steering_message`. Keep at most one case with the `None` default for the
  legacy fallback. Assert the `context_advisory` push rides the same leg as the
  human message.

### 2. Fix Cluster A2 — steer_child abort test
- **Task ID**: build-steer-child
- **Depends On**: none
- **Description**: In `tests/unit/test_steer_child.py::TestSteerChild::test_abort_flag`,
  add `room_id=None` to the `assert_called_once_with(...)` call.

### 3. Fix Cluster A3 — resume tests
- **Task ID**: build-resume
- **Depends On**: none
- **Description**: In `tests/unit/test_valor_session_resume_release.py`, set
  `s.project_key = "test"` in **both** `_make_session` (line 45) and
  `TestResumeSessionCore._make_mock_session` (line 500), then add
  `room_id="test|system"` to the 5 resume assertions — including line 622
  `test_steering_push_before_transition`, which uses `_make_mock_session`.

### 4. Fix Cluster B — reaction test
- **Task ID**: build-reaction
- **Depends On**: none
- **Description**: In `tests/unit/test_reaction_never_hostile.py`, delete line
  146 (`patch("tools.emoji_embedding._custom_embedding_cache", {})`). Confirm the
  test still fails when the block filter is removed (demonstrated-red).

### 5. Fix Cluster C — context-recall tests
- **Task ID**: build-context-recall
- **Depends On**: none
- **Description**: In `tests/unit/test_context_recall_wiring.py`, remove the
  `expectations=None` kwarg from the 5 `MessageDraft(...)` constructions (lines
  56, 191, 231, 294, 322).

### 6. Verify all 26 node ids green
- **Task ID**: verify-green
- **Depends On**: build-bridge-ack, build-steer-child, build-resume,
  build-reaction, build-context-recall
- **Description**: Run both reproduction commands (the #2846 15-node command and
  the #2822 11-node command). Confirm all 26 pass, no production source changed,
  and no `Co-Authored-By` trailers on the commits. Then run a caller-sweep grep
  to prove no stale seam remains (the manual 5-file fix must not repeat the
  seam-miss failure mode):
  - `grep -rn "assert_called_once_with" tests/unit/test_bridge_ack_steering_routed.py tests/unit/test_valor_session_resume_release.py tests/unit/test_steer_child.py` — every `push_steering_message` assertion must carry the intended `room_id`.
  - `grep -rn "expectations=" tests/unit/test_context_recall_wiring.py` — must return no matches.

## Critique Results

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Cluster A1 passes an explicit `room_id="test\|system"` into all 8 bridge-ack tests, but 2 of those are abort tests (`test_abort_detected_and_salute_reaction`, `test_abort_keyword_case_insensitive`). Aborts are demoted to the legacy leg (room_id=None) by design — the demotion is internal to `push_steering_message` (see `bridge/telegram_bridge.py:1055-1060`) — so asserting `room_id="test\|system"` on an abort test pins a Room-leg value for a call that in production always lands on the legacy leg, contradicting the plan's desired outcome of encoding the intended steering leg. The "keep exactly one case with None" ceiling does not guarantee the abort tests stay on the legacy leg | pending | Keep BOTH abort tests calling `_ack_steering_routed(...)` without a `room_id` kwarg and assert `push.assert_called_once_with("sess-1", "stop", "Alice", is_abort=True, room_id=None)` (and the `"  STOP  "` variant); pass `room_id="test\|system"` only in the 6 steer/media tests |
| NIT | Risk & Robustness | Cluster B demonstrated-red verification is underspecified — the plan says "confirm the test still fails when the block filter is removed" but does not name which block filter or how to remove it | pending | Name the HOSTILE block filter in `find_best_emoji` and the exact temporary edit that demonstrates the test is not passing vacuously |
| NIT | Risk & Robustness, Scope & Value | Setting `s.project_key = "test"` in the shared module-level `_make_session` helper (line 45) affects ~40 call sites, not just the 5 failing resume tests; safe (no test asserts on `project_key` being absent) but broad | pending | Optionally scope `project_key` to the failing tests, or add a comment in `_make_session` noting the field supports the room-id assertion |
| NIT | Scope & Value | The `context_advisory` same-leg assertion is a small scope addition beyond fixing stale assertions, but it is justified by the desired outcome of pinning intended behavior | pending | None required — proportionate and documented |
| NIT | History & Consistency | Freshness Check note cites `test_steer_child.py:89` for the abort test, but the actual `test_abort_flag` is at line 72; the Technical Approach correctly omits the line number | pending | Correct the note to line 72 (or drop the line number) |
