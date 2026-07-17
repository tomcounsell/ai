---
status: Planning
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-07-17
tracking: https://github.com/tomcounsell/ai/issues/2136
last_comment_id:
---

# Session Resume Goal Re-injection

## Problem

Telegram-originated engineering sessions run through the headless session runner; a
completed/killed/failed/abandoned session can be resumed with `valor-session resume`,
which re-enters the prior transcript via `claude -p --resume <uuid>`. But the resume
path forwards only continuation *plumbing* (the new `--message` plus four resume
scalars) and never the session's *objective*. In a production incident (cuttlefish PM
session, branch `session/dev-a814b30e`) a resumed session reported: *"the session args
are pure resume-metadata and never state the actual goal, so there's nothing concrete
for me to continue from"* — and had to ask the human to restate the task.

**Current behavior:**
- `resume_session()` (`tools/valor_session.py:680-757`) pushes only the caller's new
  `--message` via `push_steering_message` (line 737). No goal-bearing field is read.
- The turn-input drainer overwrites the enriched text with that steering message
  (`agent/session_executor.py:1716-1718`), so the popped steering text IS the turn input.
- If the transcript's goal was compacted and the new message is generic ("continue"),
  the resumed session is goalless. Its only goal source is the `--resume <uuid>` transcript.

**Desired outcome:**
A resumed session's first turn input contains an explicit goal statement built from the
record's goal-bearing fields (`context_summary` → `message_text` → latest `summary`
event, in that fallback order), mirroring the existing continuation-augmentation pattern
at `agent/session_executor.py:2262-2269`. Resuming with a generic message must yield a
session that can state its own objective without asking the human. Cold-start (non-resume)
turn construction must be unchanged.

## Freshness Check

**Baseline commit:** 85d5f0432f2f6029677a986b8bbb3b8009cdd24e
**Issue filed at:** 2026-07-17T10:55:41Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/valor_session.py:680-757` — `resume_session()` pushes raw message + four scalars — still holds (read verbatim).
- `agent/session_executor.py:1716-1718` — `_turn_input = steering_msgs[0].get("text","")` overwrites enriched text — still holds.
- `agent/session_runner/harness/claude.py:1468-1473` — SCOPE header always applied, de-scopes prior threads — still holds.
- `agent/session_runner/runner.py:163-167, 713-714` — `DEV_CONTINUATION_PREFIX` names the dev agent, not the task — still holds.
- `agent/session_executor.py:2262-2269` — working `[Prior session context: {context_summary}]` augmentation pattern to model the fix on — still holds.
- `models/agent_session.py:276` (`context_summary` Field), `:1178-1193` (`message_text` property), `:1337-1346` (`summary` property) — three goal-bearing fields — all present.

**Cited sibling issues/PRs re-checked:**
- #1741 — closed. Fixed "MESSAGE: None" phantom-task at session **creation**; resume was out of scope. Landscape unchanged.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** All line references drift-free; the issue was filed ~hours ago and no commits touched the referenced files. Bug confirmed present by reading the code path.

## Prior Art

- **Issue #1741**: "granite messageless-session silent success + /goal-driven prime design" — fixed the "MESSAGE: None" phantom-task bug so `message_text` carries a real goal anchor, but only at session **creation**. Resume was explicitly out of scope. This plan closes the resume-path gap it left.
- **PR #1940**: "Remove the interrupted-will-resume announcement entirely (#1937)" — removed a resume-time user announcement; unrelated to goal injection but confirms the resume path is actively maintained.
- No prior attempt injected a goal on the resume path — this is the first fix for this gap. `## Why Previous Fixes Failed` omitted (no failed prior fixes).

## Data Flow

1. **Entry point**: operator/auto-resume calls `resume_session(session, message, source=...)` (`tools/valor_session.py:680`).
2. **Steering push**: currently `push_steering_message(session_id, message, f"resume:{source}")` (line 737) — the raw message lands on the Redis steering list.
3. **Transition**: session → `pending`; worker picks it up.
4. **Turn-input drain**: `agent/session_executor.py:1716-1718` pops the steering list; `steering_msgs[0]["text"]` becomes `_turn_input`.
5. **Harness wrap**: `build_enriched(...)` (`agent/session_runner/harness/claude.py:1443-1489`) prepends the SCOPE header and emits `...\nMESSAGE: {turn_input}`.
6. **Output**: the resumed `claude -p --resume` subprocess receives the turn input as its first message.

The fix inserts the goal statement at **step 2** so it travels with the steering message and is path-agnostic — whichever drainer (executor at 1716 or the runner's steering-boundary drain) consumes the list receives the augmented text.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (critique + PR review gates)

## Prerequisites

No prerequisites — this work has no external dependencies. It edits one function and adds tests.

## Solution

### Key Elements

- **Goal resolver** (`_resolve_resume_goal(session) -> str | None`, new, in `tools/valor_session.py`): returns the first goal-bearing field that is a non-empty **string** — `context_summary`, then `message_text`, then latest `summary` event. The `isinstance(str)` guard is load-bearing: it makes augmentation opt-in on the presence of a real string goal, so callers/tests that don't set string goal fields (e.g. MagicMock sessions) push the raw message unchanged.
- **Augmentation in `resume_session()`**: before `push_steering_message`, wrap the outbound text as `[Prior session context: {goal}]\n\n{message}` when a goal string exists; otherwise push the raw message unchanged. Mirrors `session_executor.py:2262-2269`.
- **SCOPE header resolution**: by folding the goal into the MESSAGE body, the goal becomes part of "the message below from this sender" that the SCOPE header scopes the session to — so the header's "ignore prior threads" instruction no longer contradicts resume semantics. No change to `claude.py` is required; this is documented as the intentional resolution.

### Flow

`valor-session resume --id X --message "continue"` → `resume_session()` resolves goal from record → pushes `[Prior session context: <goal>]\n\ncontinue` onto steering list → worker drains it as first turn input → resumed session states its own objective without asking the human.

### Technical Approach

- **Layer**: the fix lives entirely in `resume_session()` (`tools/valor_session.py`), the single source shared by the CLI (`cmd_resume`) and the auto-resume reflection. Both continuation entry points benefit with one edit. Nothing in the cold-start path (fresh session enqueue, `build_enriched` for non-resume turns) is touched.
- **Goal resolution order**: `context_summary` (curated "what this session is about") → `message_text` (original task anchor) → latest `summary` event (most recent progress marker). First non-empty string wins.
- **Bound**: cap the injected goal at a generous length (`_RESUME_GOAL_MAX_CHARS = 4000`, truncated with an ellipsis) so a very long `message_text` can't balloon the turn input. This is stricter than the uncapped `session_executor.py:2269` pattern but keeps the resumed first turn bounded.
- **Defensive typing**: `_resolve_resume_goal` only accepts `isinstance(x, str) and x.strip()`. Non-string / None / whitespace-only fields are skipped, falling through to the next candidate, then to no augmentation.
- **Idempotency / double-wrap guard**: if the incoming `message` already starts with `[Prior session context:` (e.g. re-resume of an already-augmented continuation), do not wrap again.

## Failure Path Test Strategy

### Exception Handling Coverage
- `_resolve_resume_goal` performs only attribute reads and string checks; it wraps the `summary` event access defensively and returns `None` on any unexpected shape rather than raising. Test asserts a session whose fields are all absent/None yields `None` (no augmentation, raw message pushed).
- No `except Exception: pass` blocks are introduced. The existing `resume_session` try/except around `transition_status` is unchanged.

### Empty/Invalid Input Handling
- Test: `context_summary=""` (empty) falls through to `message_text`.
- Test: `context_summary` and `message_text` both empty/None fall through to latest `summary` event.
- Test: all three empty/None → raw message pushed unchanged (no `[Prior session context:` prefix).
- Test: whitespace-only `context_summary` is treated as empty and falls through.

### Error State Rendering
- Not a user-visible-rendering change. The observable output is the steering-message text pushed to Redis; tests assert its content directly via `push_steering_message` mock.

## Test Impact

No existing tests affected. Justification: the augmentation is opt-in on the presence of a real **string** goal field, guarded by `isinstance(x, str)`. Every existing test in `tests/unit/test_valor_session_resume_release.py` builds sessions with `MagicMock` (helpers `_make_session` / `_make_mock_session`) and never sets `context_summary`, `message_text`, or `summary` to a string — so `_resolve_resume_goal` returns `None` and `resume_session` pushes the raw message unchanged, exactly as the existing `mock_push.assert_called_once_with(..., <raw message>, ...)` assertions require. New behavior is covered by new test cases only.

## Rabbit Holes

- **Rewording or suppressing the SCOPE header in `claude.py`**: tempting (the issue flags it as the riskiest interplay) but unnecessary — folding the goal into the MESSAGE body puts it inside the header's scope. Touching the shared `build_enriched` header risks altering cold-start turns, which is explicitly forbidden. Avoid.
- **Adding a goal field to the four resume scalars / model schema**: the goal already lives in the record; re-deriving it at resume time is simpler and needs no migration. Avoid a schema change.
- **Injecting the goal in `runner.run()` alongside `DEV_CONTINUATION_PREFIX`**: viable, but the runner would have to re-load the AgentSession to read the goal fields, and it only covers the runner path, not the executor drain. Source-side augmentation in `resume_session()` is path-agnostic and needs no extra load.

## Risks

### Risk 1: MagicMock goal fields leak into the injected text in some caller/test
**Impact:** A `MagicMock` attribute (truthy, non-string) could be stringified into the goal prefix, producing garbage turn input.
**Mitigation:** `_resolve_resume_goal` accepts only `isinstance(x, str) and x.strip()`. MagicMock children are not `str`, so they are skipped. Explicit unit test asserts a MagicMock session with no string goal fields pushes the raw message.

### Risk 2: Very long `message_text` balloons the resumed first turn
**Impact:** A multi-KB original task text inflates the turn input and token cost.
**Mitigation:** `_RESUME_GOAL_MAX_CHARS = 4000` cap with ellipsis truncation.

## Race Conditions

No race conditions identified. `resume_session` already pushes the steering message *before* `transition_status` (the established no-race ordering, preserved). The only change is the *content* of the pushed message, computed synchronously from the in-hand `session` object before the push — no new shared state, no new async boundary.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1741] Goal-injection at session **creation** (the "MESSAGE: None" phantom-task path) — already fixed by #1741; this plan only covers the resume path.

Nothing else deferred — every relevant item (goal resolver, augmentation, cap, idempotency guard, SCOPE resolution, tests, docs) is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal: it edits one function in `tools/valor_session.py` and adds unit tests. No new dependencies, config files, Popoto schema changes, or migrations. `scripts/update/run.py` and `migrations.py` are untouched.

## Agent Integration

No agent integration required beyond the existing surface. `valor-session resume` is already a wired CLI entry point (`pyproject.toml [project.scripts]`); this plan changes the *content* of the steering message that command produces, not any tool/MCP surface. The auto-resume reflection (`resume_session` caller) picks up the same improvement automatically. No `.mcp.json` or `bridge/telegram_bridge.py` changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` — add a "Resume goal re-injection" subsection documenting the `context_summary → message_text → summary` resolution order, the `[Prior session context: ...]` wrap, the 4000-char cap, and the SCOPE-header resolution (goal folds into MESSAGE so it is in-scope).

### Inline Documentation
- [ ] Docstring on `_resolve_resume_goal` describing the fallback order and the `isinstance(str)` opt-in guard.
- [ ] Comment in `resume_session` at the augmentation site referencing the mirrored `session_executor.py:2262-2269` pattern and issue #2136.

## Success Criteria

- [ ] Resuming a session with a generic message (e.g. "continue") produces a pushed steering message (→ first turn input) containing the session's original goal text
- [ ] The goal statement falls back sensibly: `context_summary` empty → `message_text`; both empty → latest `summary` event; all empty → raw message unchanged
- [ ] The SCOPE-header interaction is explicitly resolved (goal folded into MESSAGE, documented as intentional) — no change to `claude.py`
- [ ] Unit test asserts the resumed steering-message content contains the goal (RED first, then GREEN — TDD)
- [ ] `docs/features/headless-session-runner.md` documents the resume goal re-injection
- [ ] No existing test in `test_valor_session_resume_release.py` regresses
- [ ] Cold-start (non-resume) turn construction is unchanged (only `resume_session` edited)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Step by Step Tasks

### 1. RED — write failing tests first (TDD)
- **Task ID**: build-tests-red
- **Depends On**: none
- **Validates**: tests/unit/test_valor_session_resume_release.py (new `TestResumeGoalReinjection` class)
- **Assigned To**: dev (self)
- **Agent Type**: builder
- **Parallel**: false
- Add a `TestResumeGoalReinjection` class to `tests/unit/test_valor_session_resume_release.py`.
- Cases: (a) `context_summary` set → pushed steering text starts with `[Prior session context: <ctx>]` and contains the generic message; (b) `context_summary` empty, `message_text` set → uses `message_text`; (c) both empty, `summary` event set → uses summary; (d) all empty/None → raw message pushed, no prefix; (e) whitespace-only `context_summary` falls through; (f) long `message_text` truncated at 4000 chars; (g) already-augmented incoming message not double-wrapped.
- Run the new tests, confirm they FAIL (red) for the right reason (goal absent from pushed text / helper missing).

### 2. GREEN — implement the goal resolver + augmentation
- **Task ID**: build-impl
- **Depends On**: build-tests-red
- **Validates**: tests/unit/test_valor_session_resume_release.py
- **Assigned To**: dev (self)
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Add `_RESUME_GOAL_MAX_CHARS = 4000` and `_resolve_resume_goal(session) -> str | None` to `tools/valor_session.py`.
- In `resume_session`, before `push_steering_message`, build the augmented outbound text when a goal string exists (with idempotency guard + cap); push that instead of the raw message.
- Run the new tests + the full existing `test_valor_session_resume_release.py`; confirm all GREEN.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-impl
- **Assigned To**: dev (self)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/headless-session-runner.md` with the resume goal re-injection subsection (resolution order, wrap format, cap, SCOPE resolution).

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: dev (self)
- **Agent Type**: validator
- **Parallel**: false
- `pytest tests/unit/test_valor_session_resume_release.py -q` → all pass.
- `python -m ruff check tools/valor_session.py tests/unit/test_valor_session_resume_release.py` → clean.
- Confirm only `resume_session`/new helper changed (cold-start untouched).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Resume tests pass | `pytest tests/unit/test_valor_session_resume_release.py -q` | exit code 0 |
| Goal resolver present | `grep -c "_resolve_resume_goal" tools/valor_session.py` | output > 1 |
| Augmentation prefix present | `grep -c "Prior session context" tools/valor_session.py` | output > 0 |
| Cold-start untouched (no claude.py edit) | `git diff --name-only main -- agent/session_runner/harness/claude.py` | output does not contain claude.py |
| Lint clean | `python -m ruff check tools/valor_session.py tests/unit/test_valor_session_resume_release.py` | exit code 0 |
| Docs updated | `grep -c "resume" docs/features/headless-session-runner.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
