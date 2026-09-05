---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2862
last_comment_id:
---

# Expectation blocked state and reason code

## Problem

An expectation on a Job (`models/job.py`) is binary today: open (`removed_ts is None`) or discharged (`removed_ts` set). When an expectation genuinely cannot be discharged (the owning lane died, a credential is missing, an upstream PR is unmergeable, the request was ambiguous and nobody answered) it stays open and looks identical to one that is merely in progress. The reconciler (`reflections/expectation_reconciler.py`) has one action for an open outbound expectation: steer or respawn, then escalate once after `EXPECTATION_MAX_ATTEMPTS`. Nothing records *why* a row is not progressing, so nothing downstream can route, back off, or hand it to the right person.

This is Part 1 of #2862. Part 2 (a corrupt `goal` fails open and is then overwritten) landed as a direct-to-main hotfix ahead of this plan: `Job.goal_is_corrupt()`, `CorruptGoalError`, refusing mutators, a pinned-visible Job, and a `corrupt-goal` reconciler finding. This plan builds on that code: the same chokepoint, the same "pinned visible until a human acts" posture, the same finding channel.

**Current behavior:**
A permanently stuck expectation is re-steered until the attempts counter runs out, then escalated once. After escalation the row is silent again: still open, still pinning the Job active, indistinguishable from live work in every read (`open_expectations()`, `job_tool show`, the dashboard count).

**Desired outcome:**
An expectation can carry a machine-readable blocked annotation (a closed reason code plus free-text detail, plus who wrote it and when), written only through `_write_goal_data`. The reconciler enumerates blocked rows without parsing text, stops re-steering them, and surfaces them as findings. The PM can block, unblock, and see blocked state through `tools/job_tool`. Existing entries without the key read as not blocked.

## Freshness Check

**Baseline commit:** `45d5d42d4`
**Issue filed at:** 2026-08-18 (filed from the #2860 / #2861 lane)
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/job.py::_goal_data()` — the issue quotes the fail-open reader. Drifted: the hotfix for Part 2 split it into `_parse_goal()` / `goal_is_corrupt()` / `_report_corrupt_goal()` / `_goal_data()` and added `_mutable_goal_data()` for the read half of every read-modify-write. The Part 1 write path must use `_mutable_goal_data()`.
- `models/job.py::_write_goal_data()` — still the single write chokepoint; now also refuses on a corrupt goal. Still derives `has_open_expectations` and forces `status="active"`.
- `reflections/expectation_reconciler.py::_reconcile_project` — the loop the issue describes ("keep re-steering") is at the `for entry in job.open_expectations(direction="outbound")` block; attempts, cooldown, and escalate-once are already there and are the natural seams for a blocked write.

**Cited sibling issues/PRs re-checked:**
- #2708 — CLOSED; the reconciler is on main (PR #2814, merged 2026-08-14). Part 1 is fully exercisable now.
- #2806, #2810 — CLOSED; the registry/callable mismatch the issue mentions is resolved.
- #2860 / #2861 — CLOSED; field-scoped saves landed, which is why `Job.touch/revive/mark_at_rest` can never clobber a blocked annotation written concurrently.
- #2494 — OPEN; the durability umbrella. Rule 8 in `docs/features/durability-model.md` (corrupt goal) is the closest precedent for the at-rest decision below.

**Commits on main since issue was filed (touching referenced files):**
- The Part 2 hotfix (this lane, `Refs #2862`) — partially addresses the issue (Part 2 entirely; Part 1 untouched).
- #2860/#2861 field-scoped saves — irrelevant to the blocked schema, load-bearing for its concurrency story.

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` (umbrella, #2494) and `promise-gate-recorded-obligations.md` (the gate reads open inbound expectations; blocked state must not change what clears the gate). No plan addresses blocked state.

**Notes:** the issue's open questions are answered in Technical Approach as proposals, each with its rationale, and the ones that remain a judgment call are in Open Questions.

## Prior Art

- **#2708 / PR #2814**: expectations as the single obligation primitive with a reconciler. Established the append-only entry shape, the chokepoint-derived projection, and the "reconciler never discharges" rule this plan keeps.
- **#2862 Part 2 hotfix**: the pinned-visible posture (a Job the system cannot reason about stays active and in the reconciler's index, surfaced as a finding). Blocked state reuses that posture rather than inventing a second one.
- **#1208** (killed sessions resurrecting): the same class of bug, a terminal-looking state that was not terminal. Blocked is deliberately *not* terminal here; that is why it is an annotation rather than a third state.
- No closed issue has attempted a blocked/stuck marker on expectations before.

## Research

No relevant external findings — this is an internal schema and reflection change with no new libraries; proceeding with codebase context.

## Data Flow

1. **Entry point (lane self-report)**: a lane that cannot deliver runs `python -m tools.job_tool expectation-block --job-id J --expectation-id E --code needs_human --detail "..."`. `job_tool` enforces Room scope, then calls `Job.block_expectation(E, code=..., detail=..., by="lane")`.
2. **Entry point (reconciler inference)**: in `_reconcile_project`, at the point where `attempts >= _max_attempts()` triggers `_escalate_once`, the reconciler also calls `Job.block_expectation(E, code="attempts_exhausted", detail=<escalation text>, by="reconciler")` on the fresh snapshot. Escalation and the annotation are written in that order so a Job whose write is refused (corrupt goal) still pages.
3. **Model**: `block_expectation` reads through `_mutable_goal_data()` (refuses on corruption), finds the open entry, sets `entry["blocked"] = {"code", "detail", "ts", "by"}`, and writes through `_write_goal_data`. `has_open_expectations` stays `True`; `status` stays `active`.
4. **Readers**: `open_expectations()` still returns the entry (it is open). New `blocked_expectations()` filters `entry.get("blocked")`. `job_tool show` includes the annotation. The reconciler's loop skips a blocked row and appends `blocked: <eid> <code>` to findings.
5. **Unblock**: `job_tool expectation-unblock` → `Job.unblock_expectation(E)` sets `entry["blocked"] = None` through the same chokepoint; the reconciler resumes on the next tick. Discharge (`expectation-remove`) works on a blocked row exactly as on an unblocked one and clears nothing else (history is append-only; the last `blocked` value stays on the discharged entry as the record of why it stalled).

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: three new `Job` methods (`block_expectation`, `unblock_expectation`, `blocked_expectations`), two new `job_tool` subcommands, one new key inside the existing `goal` JSON entries. No Popoto field changes, no new index.
- **Coupling**: the reconciler gains its first write to a Job (today it writes only its own bookkeeping keys). The write is a chokepoint call, never a discharge, and the invariant in `docs/features/expectation-reconciler.md` is amended to say so precisely.
- **Data ownership**: unchanged. The goal JSON stays the single source of truth; nothing is projected to an index.
- **Reversibility**: high. Deleting the three methods and ignoring the key restores today's behavior; entries carrying `blocked` remain valid JSON.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (settle the Open Questions before build)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies beyond the repo venv and the test Redis pool.

## Solution

### Key Elements

- **Blocked annotation on an open entry**: `entry["blocked"]` is `None` (or absent, for pre-existing entries) or `{"code": str, "detail": str, "ts": iso, "by": "lane" | "reconciler" | "pm"}`. Orthogonal to `removed_ts`.
- **Closed reason vocabulary** (`models/job.py::BLOCKED_REASONS`): `owner_gone`, `attempts_exhausted`, `needs_human`, `missing_credential`, `upstream_unmergeable`. `block_expectation` rejects any other code loudly (`ValueError`), the same posture as `add_expectation`'s direction check. Extending the vocabulary is a one-line change plus a test row.
- **Two writers, both recorded**: the reconciler at its existing escalation seam, and the PM or lane through `job_tool`. `by` records which.
- **Reconciler backs off**: a blocked row is skipped and surfaced as a finding; the attempts/cooldown machinery is never touched for it.
- **Rest is unchanged**: a blocked expectation is still open and still pins the Job `active`.

### Flow

Lane cannot deliver → `job_tool expectation-block` → entry carries `blocked` → reconciler tick skips it, findings show `blocked: E needs_human` → PM sees it in `job_tool show` / operator log → PM either fixes and `expectation-unblock`s (reconciler resumes) or `expectation-remove`s (discharged, annotation preserved in history).

### Technical Approach

- **Annotation, not a third state.** A row can block and later unblock without ever being discharged; a state field would need a back-transition and would tempt readers into treating blocked as done (the #1208 shape). `removed_ts` keeps its single meaning.
- **No projection field.** The reconciler's scan root is already bounded by `has_open_expectations`; blocked rows are a subset of that set, so a third `IndexedField` buys nothing and adds a projection to keep honest. `blocked_expectations()` is a reader over the goal JSON.
- **All writes through `_write_goal_data`, all reads for write through `_mutable_goal_data()`.** A corrupt goal therefore refuses a block/unblock too, and the Job stays on the corrupt-goal path (rule 8, `durability-model.md`).
- **Escalation writes the annotation, not the first steer.** The first re-steer is legitimate recovery, not a stall. The seam where the reconciler already decides "a human is needed" (`attempts >= _max_attempts()`) is where it also records `attempts_exhausted`; an owner-gone row that has been respawned successfully is not blocked. `owner_gone` is reserved for a future writer (the session-health drift advisory) and is in the vocabulary so the enum does not need reopening; the plan does not wire it.
- **Reconciler invariant amended, not broken.** "No writes outside its own bookkeeping keys" becomes "never discharges; its only Job write is the blocked annotation through the chokepoint".
- **Absence is not-blocked.** Every reader uses `entry.get("blocked")`; the goal JSON is schemaless and pre-existing entries have no key.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_reconcile_project`'s per-entry `try` swallows and logs; add a test that a `CorruptGoalError` raised by `block_expectation` at the escalation seam still produces the escalation and logs a warning (escalate first, annotate second).
- [ ] `job_tool` converts `ValueError` / `CorruptGoalError` to `JobToolError`; test both for the two new subcommands.

### Empty/Invalid Input Handling
- [ ] `block_expectation` with an unknown code, an empty code, or an unknown/discharged expectation id: `ValueError` for the code, `False` for a missing/discharged id (mirrors `discharge_expectation`). Tests for each.
- [ ] `detail` may be empty; `code` may not.

### Error State Rendering
- [ ] `job_tool show` renders `blocked` on the entry; `job_tool expectation-block` on a Job in another Room fails with the Room-scope `JobToolError`.

## Test Impact

- [ ] `tests/unit/test_job_model.py` — UPDATE: add a `TestBlockedExpectations` class (block, unblock, discharge-preserves-annotation, absent key reads as not blocked, unknown code rejected, corrupt goal refuses, `has_open_expectations` and `status` unchanged by block).
- [ ] `tests/unit/reflections/test_expectation_reconciler.py` — UPDATE: blocked row is skipped with a `blocked:` finding and no steer/respawn; escalation seam writes `attempts_exhausted`; a refused write (corrupt goal) still escalates.
- [ ] `tests/unit/test_job_tool.py` — UPDATE: two new subcommands, Room scope, error conversion.
- [ ] `tests/unit/test_promise_advisory.py` — no change: the gate clears on an open inbound expectation; a blocked inbound expectation is still open (asserted by one new row in `test_job_model.py`, not here).

## Rabbit Holes

- Designing a generic state machine for expectations. The obligation primitive is deliberately one entry shape with one discharge; a blocked annotation is the whole scope.
- Inferring blocked from age alone. Age is already what the reconciler's `min_age` and attempts do; a second age heuristic would disagree with the first.
- A dashboard surface. `ui/data/sdlc.py` reads `len(job.open_expectations())`; leave it, a blocked count is a follow-on once real data exists.
- Wiring `owner_gone` from session health in this plan. It is in the vocabulary so the enum is stable; the writer is a separate change with its own trust question.

## Risks

### Risk 1: The reconciler stops acting on rows that were only transiently blocked
**Impact:** A lane blocks on `missing_credential`, the credential is added, nobody unblocks; the row sits forever.
**Mitigation:** The finding line repeats every tick (30 min) in the operator log, exactly like `corrupt-goal`; `job_tool show` exposes it to the PM on every read. Rest is unchanged, so the Job never disappears. Unblock is one command.

### Risk 2: Trust asymmetry between writers
**Impact:** A lane self-reports `needs_human` to get out of work; the reconciler backs off.
**Mitigation:** `by` is recorded, and the PM prime already frames discharge as PM-authored; the same framing applies to block. The reconciler's own code is the only writer of `attempts_exhausted`. This is a policy question (Open Question 2).

### Risk 3: A block write races a concurrent expectation mutation
**Impact:** Two read-modify-write cycles on the same goal JSON; the later full write wins and drops the other's change.
**Mitigation:** Same exposure as `add_expectation` vs `discharge_expectation` today, unchanged by this plan; the reconciler re-fetches a fresh snapshot immediately before writing (Race 3 in the reconciler) and writes only at the escalation seam, once per row per escalation TTL.

## Race Conditions

### Race 1: reconciler annotation vs PM discharge
**Location:** `reflections/expectation_reconciler.py::_reconcile_project`, escalation branch
**Trigger:** PM discharges E between the reconciler's scan and its `block_expectation` call.
**Data prerequisite:** the fresh snapshot (`Job.query.get(...)`) must show E still open.
**State prerequisite:** none beyond the existing cooldown claim.
**Mitigation:** `block_expectation` on a discharged id returns `False` and writes nothing; the reconciler already re-fetches before acting. A PM discharge always wins.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2862] Part 2 (corrupt goal fails closed) is already landed on main under this issue's `Refs`; this plan does not touch `_parse_goal`, `goal_is_corrupt`, or the corrupt-goal tests.
- [SEPARATE-SLUG #2494] Any change to the Room / Job / AgentSession field set or indexes; this plan adds no Popoto field and no index, by design.

## Update System

No update system changes required. The annotation is an additive key inside the existing `goal` JSON field: no Popoto field is added, renamed, or retyped, so no entry in `scripts/update/migrations.py::MIGRATIONS` is registered. Pre-existing entries read as not blocked by construction (`entry.get("blocked")`). Restarting the worker/reflection scheduler after deploy picks up the reconciler change through the normal `/update` path.

## Agent Integration

The PM and lanes reach this through `tools/job_tool.py` (a CLI invoked with `VALOR_SESSION_ID` set), which is how they already add and discharge expectations; two new subcommands (`expectation-block`, `expectation-unblock`) and a `blocked` field in `show` output are the whole surface. No MCP server or `.mcp.json` change: `job_tool` is not MCP-exposed today and this plan keeps that. `.claude/commands/roles/prime-pm-role` gets one sentence pointing at `expectation-block` as the answer to "I cannot deliver this and here is why", next to the existing discharge instruction. Integration test: `tests/unit/test_job_tool.py` drives the CLI functions end to end against real Redis.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/durability-model.md`: add rule 9 (blocked annotation: shape, vocabulary, writers, rest unchanged) after rule 8.
- [ ] Update `docs/features/expectation-reconciler.md`: amend the "no writes" invariant, add the `blocked:` finding, document the back-off.
- [ ] Update `docs/tools-reference.md` for the two `job_tool` subcommands.

### Inline Documentation
- [ ] Docstrings on `block_expectation` / `unblock_expectation` / `blocked_expectations` stating the annotation-not-state decision and the vocabulary.
- [ ] Update the schema comment above `goal = Field(null=True)` in `models/job.py` to include `blocked`.

## Success Criteria

- [ ] An open expectation can carry `blocked` with a code from the closed vocabulary, written only through `_write_goal_data`.
- [ ] `Job.blocked_expectations()` returns blocked rows without parsing free text; the reconciler emits `blocked: <eid> <code>` and performs no steer/respawn for them.
- [ ] `has_open_expectations`, `status="active"` forcing, and rest-by-age are unchanged by a block; documented in rule 9.
- [ ] Entries without the key read as not blocked (test on a hand-written legacy entry).
- [ ] A corrupt goal refuses block/unblock (test), and the reconciler still escalates when the annotation write is refused.
- [ ] Tests pass (`scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/reflections/test_expectation_reconciler.py tests/unit/test_job_tool.py -n 2`).
- [ ] Documentation updated per the Documentation section.

## Team Orchestration

### Team Members

- **Builder (model + tool)**
  - Name: job-blocked-builder
  - Role: `models/job.py` methods, vocabulary, `job_tool` subcommands, their tests
  - Agent Type: builder
  - Resume: true

- **Builder (reconciler)**
  - Name: reconciler-backoff-builder
  - Role: reconciler skip + finding + escalation-seam write, its tests
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: blocked-validator
  - Role: run the three test files, mutation-check each new guard, verify docs
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: blocked-documentarian
  - Role: durability-model rule 9, reconciler invariant, tools-reference
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Model: annotation, vocabulary, readers
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_job_model.py
- **Assigned To**: job-blocked-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `BLOCKED_REASONS`, `block_expectation`, `unblock_expectation`, `blocked_expectations` to `models/job.py`; both writers go through `_mutable_goal_data()` and `_write_goal_data`.
- Update the `goal` schema comment.
- Add `TestBlockedExpectations` per Test Impact, including the corrupt-goal refusal and the legacy-entry row.

### 2. Tool: subcommands and error conversion
- **Task ID**: build-tool
- **Depends On**: build-model
- **Validates**: tests/unit/test_job_tool.py
- **Assigned To**: job-blocked-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `expectation-block` / `expectation-unblock`; `show` includes `blocked`; convert `ValueError` and `CorruptGoalError` to `JobToolError`.
- One sentence in the PM prime pointing at `expectation-block`.

### 3. Reconciler: skip, finding, escalation-seam write
- **Task ID**: build-reconciler
- **Depends On**: build-model
- **Validates**: tests/unit/reflections/test_expectation_reconciler.py
- **Assigned To**: reconciler-backoff-builder
- **Agent Type**: builder
- **Parallel**: true
- Skip blocked rows with a `blocked: <eid> <code>` finding before the age/liveness checks.
- At the `attempts >= _max_attempts()` branch: escalate first, then `block_expectation(..., code="attempts_exhausted", by="reconciler")` on the fresh snapshot inside the existing per-entry `try`.
- Tests: skip + finding + no action; seam write; refused write still escalates.

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-tool, build-reconciler
- **Assigned To**: blocked-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; mutation-check each new guard (remove the skip, remove the vocabulary check, remove the corrupt refusal) and confirm red each time.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: blocked-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Documentation section items.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Model, reconciler, tool tests pass | `scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/reflections/test_expectation_reconciler.py tests/unit/test_job_tool.py -n 2 -q` | exit code 0 |
| Lint clean | `python -m ruff check models/job.py tools/job_tool.py reflections/expectation_reconciler.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/job.py tools/job_tool.py reflections/expectation_reconciler.py` | exit code 0 |
| No new index (anti-criterion, #2494 No-Go) | `grep -c "IndexedField(" models/job.py` | output contains 2 |
| Reconciler never discharges (anti-criterion) | `grep -c "discharge_expectation" reflections/expectation_reconciler.py` | match count == 0 |
| Vocabulary is closed | `grep -c "BLOCKED_REASONS" models/job.py` | output > 1 |
| Rule 9 documented | `grep -c "blocked" docs/features/durability-model.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Vocabulary members.** The proposed five (`owner_gone`, `attempts_exhausted`, `needs_human`, `missing_credential`, `upstream_unmergeable`) come from the reconciler's decision points and the issue's examples, not from a corpus of observed stalls. Ship these five and extend on evidence, or ship only `attempts_exhausted` + `needs_human` (the two with a writer in this plan) and add the rest when a writer exists?
2. **Lane self-report trust.** Should a lane be allowed to block its own outbound expectation, or only the PM (holder) and the reconciler? The plan proposes all three with `by` recorded; the conservative alternative is holder-or-reconciler only, with the lane steering its PM instead.
3. **Rest.** The plan keeps a blocked expectation pinning the Job `active` (blocked is not finished). The alternative in the issue, letting a blocked Job rest while staying visible, needs a new "visible" channel that is not `status`; the finding line and `job_tool show` are that channel today. Confirm the plan's choice.
4. **Migration registration.** The repo addendum asks for a `MIGRATIONS` entry on any Popoto model change. This plan changes the JSON payload, not the field set, and registers nothing. Confirm that reading, or name the no-op migration you want recorded.
