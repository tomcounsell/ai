---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1922
last_comment_id:
---

# Disambiguate `exit_reason=pm_user`: split real-answer delivery from needs-input prompt delivery

## Problem

The headless session runner tags a completed PM turn with an `exit_reason` for
observability. Today `pm_user` is overloaded: it is emitted both for a **real
`[/user]` answer** the PM chose to deliver AND for a **needs-input prompt** the
runner forwarded because a `needs_human` hook edge fired on a turn with no
routing prefix. On the dashboard, in `session_events`, and in the executor's
reaction gate these two outcomes are indistinguishable — "the PM answered the
human" reads identically to "the PM paused and is waiting on the human."

**Current behavior:**
In `agent/session_runner/runner.py`, three sites set `exit_reason="pm_user"`:
- `runner.py:887` — real `[/user]` delivery (`classification.destination == "user"`). A **real answer**.
- `runner.py:902` — `needs_human` edge fired on an unroutable turn; the PM's raw text is delivered as a genuine question. A **needs-input prompt**.
- `runner.py:928` — wrap-up guard delivered a `[/user]` payload. A **real answer**.

Site 902 shares the `pm_user` label with 887/928, so the "real answer vs
needs-input prompt" distinction the issue asks for is lost.

**Desired outcome:**
The `needs_human` prompt-delivery path (902) gets its own clean `exit_reason`
(`pm_needs_human`), while genuine `[/user]` answers (887, 928) keep `pm_user`.
Both remain **clean** exits (REACTION_COMPLETE, terminal status `completed`, no
Sentry anomaly). The observability signal becomes unambiguous with zero
behavioral change to delivery or reactions.

## Freshness Check

**Baseline commit:** `8884150722e9b6f559a2421047529703253d9d06`
**Issue filed at:** 2026-07-06T07:20:32Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/granite_container/container.py:1332-1354 / :2553 / :2347 / :2197` — the issue's original item-1/item-2 anchors — **gone**: the file is deleted by the #1930 cutover. As the issue's own scope update states.
- `agent/session_runner/router.py` — owns `CLEAN_EXIT_REASONS` (line 148), `WRAPUP_ELIGIBLE_EXIT_REASONS` (161), `ANOMALY_EXIT_REASONS` (174); `pm_user` present in the first two. Confirmed.
- `agent/session_runner/runner.py:887, 902, 928` — the three `exit_reason="pm_user"` producers. **This is the drift:** the scope update re-anchored item 2 to `router.py` + `adapter.py`, but the assignments actually live in `runner.py`. `router.py` only holds the classification *tables*; `adapter.py:417` only *persists* `summary.exit_reason`. The claim (disambiguate `pm_user`) still holds — the edit site is `runner.py:902`, the vocabulary-registration site is `router.py`, and the executor's duplicate set must sync.
- `agent/session_executor.py:35-36` — duplicated `_CLEAN_RUNNER_EXIT_REASONS`, consumed by `_is_non_clean_runner_exit` (line 40). Confirmed still present.

**Cited sibling issues/PRs re-checked:**
- #1930 (granite PTY teardown PR) — MERGED 2026-07-07T04:54:35Z. Delivered item 1 and relocated the exit vocabulary into `agent/session_runner/`.
- #1924 (teardown issue) — CLOSED 2026-07-07T04:54:37Z.
- #1919 (idle-notification verbatim delivery) — CLOSED 2026-07-07T04:54:36Z. Its hook boilerplate filter is why the 902 path is now "a genuine question," not idle noise.

**Commits on main since issue was filed (touching referenced files):**
- `e8351e4c` Granite PTY teardown (#1930) — this is the cutover that moved the code; it is the reason for the re-anchoring. Fully accounted for.

**Active plans in `docs/plans/` overlapping this area:** none. `granite-pty-teardown.md` shipped and is archived; no open plan touches `runner.py`/`router.py` exit classification.

**Notes:** Item 1 is delivered and needs no code work — verified `_await_turn_end` no longer exists and the turn_end-over-needs_human preference lives in the graduated hook-edge reconciliation. This plan is item 2 only.

## Prior Art

- **PR #1743**: `fix(#1719): per-turn routing-prefix reminder + relaxed wrap-up floor` — introduced `pm_floor_delivered`, the precedent for adding a distinct clean exit reason for a specific delivery shape. Directly analogous: this plan adds `pm_needs_human` the same way.
- **PR #1930**: `Granite PTY teardown` — graduated the exit-classification tables into `router.py` and the `_CLEAN_RUNNER_EXIT_REASONS` mirror in `session_executor.py`. Establishes the two places a new clean reason must be registered.
- **Issue #1708**: `pm_no_user_message` churn — prior evidence that exit-reason semantics feed real observability/reaction decisions, so a new reason must be wired into both the router table and the executor mirror or it is misclassified as a failure.

No prior attempt tried to split `pm_user`; this is a first, additive change.

## Research

No relevant external findings — this is a purely internal change to this repo's headless-runner exit vocabulary, with no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

1. **Entry point**: A PM turn completes; `SessionRunner._route_turn(outcome)` classifies it (`runner.py:879`).
2. **Branch**: If no `[/user]`/`[/complete]` prefix matched but `outcome.needs_human is not None` and the text is non-empty, the runner delivers the PM's text via `self._adapter.on_user_payload(...)` and returns a `_RouteDecision(exit_reason="pm_user")` (**today**) → **`"pm_needs_human"`** (this plan). `runner.py:900-902`.
3. **Persist**: `summary.exit_reason` propagates to `SessionRunnerAdapter.publish_exit_summary`, which writes `agent_session.exit_reason` (`adapter.py:417`) and `user_facing_routed=True` (delivery already happened).
4. **Classify**: `session_executor._is_non_clean_runner_exit(agent_session)` checks the reason against `_CLEAN_RUNNER_EXIT_REASONS` (`session_executor.py:60`) to pick REACTION_COMPLETE vs REACTION_ERROR and terminal status (`_runner_final_status`).
5. **Output**: The dashboard renders `exit_reason` as a free-form string (`ui/data/sdlc.py:995`, `ui/app.py:735`) — the new value flows through with no enumeration change.

The correctness-critical handoff is step 4: if `pm_needs_human` is not added to the executor's clean set, a healthy needs-input pause is mislabeled a failure (error reaction + `failed` status).

## Architectural Impact

- **New dependencies**: none. To eliminate the duplicated clean-set, `session_executor.py` will import `CLEAN_EXIT_REASONS` from `agent.session_runner.router` instead of redefining it — a dependency `session_executor → session_runner.router` that already exists structurally (the executor dispatches the runner).
- **Interface changes**: none. `exit_reason` is a free-form `str`; adding a member to a frozenset is additive.
- **Coupling**: decreases — replacing the hand-maintained `_CLEAN_RUNNER_EXIT_REASONS` duplicate with a single import removes a drift hazard (the NO-LEGACY / fix-at-source principle).
- **Data ownership**: unchanged. `router.py` remains the single source of truth for the exit vocabulary.
- **Reversibility**: trivial — revert three small edits.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue + recon)
- Review rounds: 1 (critique + standard review)

## Prerequisites

No prerequisites — this work has no external dependencies. It edits three Python modules and their unit tests; the existing `.venv` and pytest suffice.

## Solution

### Key Elements

- **New clean exit reason `pm_needs_human`**: distinguishes a runner-forwarded needs-input prompt from a PM-authored `[/user]` answer.
- **Single-source vocabulary**: `router.py` owns `CLEAN_EXIT_REASONS`; `session_executor.py` imports it rather than duplicating it.
- **Behavior parity**: `pm_needs_human` is clean (no error reaction, terminal `completed`) and wrap-up-eligible, exactly like `pm_user` — only the label changes.

### Flow

PM turn with a `needs_human` edge but no routing prefix → runner delivers the PM's text as a question → **exit `pm_needs_human`** (was `pm_user`) → executor sees it in the clean set → REACTION_COMPLETE + status `completed` → dashboard shows `pm_needs_human`.

### Technical Approach

- `agent/session_runner/runner.py:902` — change `exit_reason="pm_user"` → `exit_reason="pm_needs_human"` on the `needs_human`-edge delivery branch only. Leave `887` and `928` (real `[/user]` answers) as `pm_user`. Update the module docstring bullet (`runner.py:13`) and the branch comment (897-899) to name the new reason.
- `agent/session_runner/router.py` — add `"pm_needs_human"` to `CLEAN_EXIT_REASONS` (148) and to `WRAPUP_ELIGIBLE_EXIT_REASONS` (161) for parity with `pm_user` (defensive: if delivery ever reports `user_facing_routed=False`, the wrap-up guard must still drive a user-facing message). Update the historical-vocabulary docstring (19-23) to mention `pm_needs_human`.
- `agent/session_executor.py:35-36` — replace the duplicated literal `_CLEAN_RUNNER_EXIT_REASONS` frozenset with `from agent.session_runner.router import CLEAN_EXIT_REASONS as _CLEAN_RUNNER_EXIT_REASONS` (or a thin re-export), so the new reason is recognized without a second edit. Update the `_is_non_clean_runner_exit` docstring (44-51) to name `pm_needs_human` as clean.
- No migration: `exit_reason` is a free-form string field on `AgentSession`; existing records with `pm_user` remain valid, and no historical rewrite is attempted.

## Failure Path Test Strategy

### Exception Handling Coverage
- The wrap-up guard (`runner.py:912`) and adapter persistence (`adapter.py:415`) wrap their bodies in `except Exception` fail-silent blocks. This plan does not add or remove any such block; it only changes a string literal and a frozenset membership. State: no new exception handlers in scope; existing ones are untouched and already covered by `test_runner_turns.py` / `test_session_executor_runner_dispatch.py`.

### Empty/Invalid Input Handling
- The 902 branch is already guarded by `outcome.needs_human is not None and text.strip()` — empty/whitespace text falls through to the compliance nudge, unchanged. No new input paths are introduced.

### Error State Rendering
- The user-visible output (the delivered prompt) is unchanged — only the telemetry label changes. The executor reaction gate is exercised by an added `pm_needs_human` case in `test_session_executor_runner_dispatch.py` asserting REACTION_COMPLETE (not REACTION_ERROR) and terminal `completed`.

## Test Impact

- [ ] `tests/unit/session_runner/test_runner_turns.py::test_needs_human_edge_with_unroutable_text_delivers` (line 179-193) — UPDATE: assert `summary.exit_reason == "pm_needs_human"` (was `"pm_user"`); keep `user_facing_routed is True`.
- [ ] `tests/unit/session_runner/test_runner_turns.py::test_user_route_delivers_and_exits` (line 94-98) — UPDATE (assertion-preserving): add a comment / no-op confirming the real `[/user]` path still yields `pm_user`, guarding against a wrong-branch edit. No behavioral change to the test.
- [ ] `tests/unit/session_runner/test_runner_liveness.py` (line 137) — UPDATE: extend the `not in ("pm_complete", "pm_user")` guard to also exclude `"pm_needs_human"` so a healthy needs-human exit is never asserted as a liveness failure.
- [ ] `tests/unit/test_session_executor_runner_dispatch.py` (parametrized cases at 360, 403) — UPDATE: add a `("pm_needs_human", ...)` row asserting it is treated as clean (non-error reaction, `completed` status), mirroring the existing `pm_user` row.

## Rabbit Holes

- **Do NOT rename or remove `pm_user`.** The issue's own Rabbit Holes (inherited from #1924) explicitly KEEP the historical vocabulary. This is an additive split, not a rename.
- **Do NOT touch item 1** (ts-order `turn_end` vs `needs_human`). It shipped in #1930; the graduated hook-edge reconciliation already prefers `turn_end`. Verifying/closing it is a separate, no-code action.
- **Do NOT attempt to backfill historical `pm_user` records** into `pm_needs_human`. The old records predate the distinction; there is no reliable signal to reclassify them, and `exit_reason` is telemetry, not state.
- **Do NOT re-architect the exit-classification tables** (e.g., collapse the three frozensets or add a status enum). Out of scope; a one-member addition is the whole job.
- **Do NOT split site 928** (wrap-up-guard `[/user]`) — it is a real answer produced during wrap-up and correctly stays `pm_user`.

## Risks

### Risk 1: The executor's duplicated clean-set is missed, mislabeling a healthy pause as a failure
**Impact:** A `pm_needs_human` exit would fall outside `_CLEAN_RUNNER_EXIT_REASONS`, so `_is_non_clean_runner_exit` returns True → REACTION_ERROR and terminal status `failed` for a perfectly healthy needs-input pause. This is the #1708-class regression.
**Mitigation:** Register the reason at its single source (`router.CLEAN_EXIT_REASONS`) and make `session_executor.py` import that set instead of duplicating it, so the two can never drift. The added `test_session_executor_runner_dispatch.py` case asserts clean handling end-to-end.

### Risk 2: The edit lands on the wrong branch (887/928 instead of 902)
**Impact:** Real `[/user]` answers would be mislabeled `pm_needs_human`, inverting the very signal being fixed.
**Mitigation:** The three sites are textually distinct (887 is `destination == "user"`, 902 is the `needs_human`-edge fallback, 928 is inside `_run_wrapup_guard`). The assertion-preserving update to `test_user_route_delivers_and_exits` pins 887 to `pm_user`, and `test_needs_human_edge_with_unroutable_text_delivers` pins 902 to `pm_needs_human` — a wrong-branch edit fails one of the two.

## Race Conditions

No race conditions identified. The change is a synchronous string-literal swap plus a frozenset-membership addition on the single-threaded turn-routing path (`_route_turn`). No shared mutable state, no new async operations, no cross-process handoff is introduced. `exit_reason` is written once at turn end and read once at finalization, exactly as today.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1922] Item 1 (ts-order `turn_end` vs `needs_human` in the retired `_await_turn_end`) — delivered by #1930; this plan verifies it is satisfied and the issue closes on merge, but performs no code work on it. Tracked by this same issue #1922.
- Backfilling or migrating historical `pm_user` AgentSession records to `pm_needs_human` — `[DESTRUCTIVE]` reclassification of telemetry with no reliable historical signal; deliberately not attempted (see Rabbit Holes; anti-criterion below).

## Update System

No update system changes required — this is a purely internal code change. No new dependencies, config files, or `scripts/update/` changes. `exit_reason` is a free-form string on the existing `AgentSession` Popoto model; no field is added or removed, so **no migration in `scripts/update/migrations.py` is needed** (per the repo's Popoto migration rule, which triggers only on model schema changes).

## Agent Integration

No agent integration required — this is a runner/executor-internal change. No new CLI entry point, no `.mcp.json` / MCP server surface, and no bridge import is involved. The agent surface is unaffected; only the telemetry label emitted at session end changes, which the dashboard already renders generically.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` — extend the exit-classification description (line 27 area and the `exit_reason` discussion near 143) to document `pm_needs_human` as the clean exit for a runner-forwarded needs-input prompt, distinct from `pm_user` (a real `[/user]` answer).

### External Documentation Site
- No external docs site (Sphinx/MkDocs) in this repo — n/a.

### Inline Documentation
- [ ] Update the `runner.py` module docstring (line 13) and the 897-899 branch comment to name `pm_needs_human`.
- [ ] Update the `router.py` vocabulary docstring (19-23) and the `session_executor._is_non_clean_runner_exit` docstring (44-51).

## Success Criteria

- [ ] `runner.py:902` emits `exit_reason="pm_needs_human"`; `887` and `928` still emit `pm_user`.
- [ ] `pm_needs_human` is a member of both `CLEAN_EXIT_REASONS` and `WRAPUP_ELIGIBLE_EXIT_REASONS` in `router.py`.
- [ ] `session_executor.py` recognizes `pm_needs_human` as clean via a single imported set (no second literal to maintain).
- [ ] A needs-human-edge exit yields REACTION_COMPLETE + terminal `completed` (asserted in `test_session_executor_runner_dispatch.py`).
- [ ] `docs/features/headless-session-runner.md` documents the new reason.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `session_executor.py` imports `CLEAN_EXIT_REASONS` from `agent.session_runner.router` (no duplicated literal set).

## Team Orchestration

### Team Members

- **Builder (exit-reason-split)**
  - Name: `runner-builder`
  - Role: Implement the `pm_needs_human` split across `runner.py`, `router.py`, `session_executor.py`, and update the four affected tests + feature doc.
  - Agent Type: builder
  - Domain: async (headless-runner turn routing)
  - Resume: true

- **Validator (exit-reason-split)**
  - Name: `runner-validator`
  - Role: Verify all success criteria, run the runner + executor unit suites, confirm the single-source import and the doc update.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement the `pm_needs_human` split
- **Task ID**: build-exit-reason-split
- **Depends On**: none
- **Validates**: tests/unit/session_runner/test_runner_turns.py, tests/unit/session_runner/test_runner_liveness.py, tests/unit/test_session_executor_runner_dispatch.py
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `agent/session_runner/runner.py:902` `exit_reason="pm_user"` → `"pm_needs_human"`; leave 887 and 928 as `pm_user`. Update the module docstring (line 13) and the 897-899 comment.
- Add `"pm_needs_human"` to `CLEAN_EXIT_REASONS` and `WRAPUP_ELIGIBLE_EXIT_REASONS` in `agent/session_runner/router.py`; update the vocabulary docstring (19-23).
- In `agent/session_executor.py`, replace the duplicated `_CLEAN_RUNNER_EXIT_REASONS` literal with an import of `CLEAN_EXIT_REASONS` from `agent.session_runner.router`; update the `_is_non_clean_runner_exit` docstring.
- Update the four tests per the Test Impact section.
- Update `docs/features/headless-session-runner.md` to document `pm_needs_human`.
- Run `python -m ruff format . && python -m ruff check .`

### 2. Validate
- **Task ID**: validate-exit-reason-split
- **Depends On**: build-exit-reason-split
- **Assigned To**: runner-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/session_runner/ tests/unit/test_session_executor_runner_dispatch.py -q`.
- Confirm each Success Criterion, including the `grep` that `session_executor.py` imports the router set (no duplicate literal).
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Runner + executor unit tests pass | `pytest tests/unit/session_runner/ tests/unit/test_session_executor_runner_dispatch.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New reason registered as clean | `grep -c 'pm_needs_human' agent/session_runner/router.py` | output > 0 |
| 902 emits new reason | `grep -c 'pm_needs_human' agent/session_runner/runner.py` | output > 0 |
| Executor imports the router set (no duplicate literal) | `grep -c 'from agent.session_runner.router import' agent/session_executor.py` | output > 0 |
| No duplicated clean-set literal remains in executor | `grep -c '"pm_complete", "pm_user", "pm_floor_delivered", "steer_abort"' agent/session_executor.py` | match count == 0 |
| Real [/user] answer still pm_user | `grep -c 'exit_reason="pm_user"' agent/session_runner/runner.py` | output > 0 |
| No historical backfill migration added | `grep -rc 'pm_needs_human' scripts/update/migrations.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Naming:** `pm_needs_human` is proposed for the 902 path. Alternatives considered: `pm_user_prompt`, `needs_human_prompt`. `pm_needs_human` mirrors the existing `needs_human` hook-edge vocabulary and the `pm_`-prefixed exit family. Confirm this name before build, or nominate another — it becomes durable telemetry vocabulary.
2. **Wrap-up eligibility:** The plan adds `pm_needs_human` to `WRAPUP_ELIGIBLE_EXIT_REASONS` for parity with `pm_user` (defensive: guarantees a user-facing message even if delivery reports `user_facing_routed=False`). Since site 902 always delivers before setting the reason, this is belt-and-suspenders. Keep it in the wrap-up set, or restrict `pm_needs_human` to `CLEAN_EXIT_REASONS` only?
