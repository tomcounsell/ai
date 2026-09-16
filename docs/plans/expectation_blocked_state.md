---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2862
last_comment_id: 5571046486
revision_applied: true
revision_applied_at: 2026-09-16T10:39:31Z
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

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` (umbrella, #2494) and `durability-m1-fence-canary.md`. No plan addresses blocked state. *(Revision pass: an earlier draft of this line cited `docs/plans/promise-gate-recorded-obligations.md`; a direct existence check — `ls docs/plans/ | grep -i "promise\|gate\|obligat"` returns nothing — shows no such file exists, so the citation is dropped. The promise-gate claim it carried is re-anchored on source below.)*

**Promise gate, anchored on source rather than a plan:** `bridge/promise_gate.py::promise_override_active` (`bridge/promise_gate.py:451`) clears the gate on `job.open_expectations(direction="inbound")` being non-empty. A blocked entry is still open (`removed_ts is None`, the orthogonality decision below), so the gate's behavior is unchanged by this plan. That is why `tests/unit/test_promise_advisory.py` needs no change; the property is instead asserted by one new row in `tests/unit/test_job_model.py` ("a blocked inbound expectation still counts as open").

**Notes:** the issue's open questions are answered in Technical Approach as proposals, each with its rationale.

**Re-verification at plan settle (baseline `23964450c`):** every symbol the plan names still exists at the shape described — `_mutable_goal_data` (`models/job.py:289`), `_write_goal_data` (`:307`), `goal_is_corrupt` (`:209`), `discharge_expectation` (`:416`), `open_expectations` (`:430`), and the reconciler loop (`reflections/expectation_reconciler.py:447-572`). Commits landed since `45d5d42d4` touch `models/job.py` (#2856 shadow-append, #3180 UTC-reattach removal) but neither touches the goal-JSON entry shape or the chokepoint. Two corrections the re-read forced, both now folded into the plan below:

1. `_escalate_once` has **three** call sites (`:481`, `:523`, `:554`), not one. The plan's "the escalation seam" was ambiguous; it is now named as the `attempts >= _max_attempts()` branch at `:480` only.
2. The fresh-snapshot re-fetch (`:499`) sits **below** the escalation branch, which `continue`s at `:494`. The plan's earlier claim that the reconciler "already re-fetches before acting" is false at that seam. Corrected in Data Flow and Race 1.

## Prior Art

- **#2708 / PR #2814**: expectations as the single obligation primitive with a reconciler. Established the append-only entry shape, the chokepoint-derived projection, and the "reconciler never discharges" rule this plan keeps.
- **#2862 Part 2 hotfix**: the pinned-visible posture (a Job the system cannot reason about stays active and in the reconciler's index, surfaced as a finding). Blocked state reuses that posture rather than inventing a second one.
- **#1208** (killed sessions resurrecting): the same class of bug, a terminal-looking state that was not terminal. Blocked is deliberately *not* terminal here; that is why it is an annotation rather than a third state.
- No closed issue has attempted a blocked/stuck marker on expectations before.

## Research

No relevant external findings — this is an internal schema and reflection change with no new libraries; proceeding with codebase context.

## Data Flow

1. **Entry point (lane self-report)**: a lane that cannot deliver runs `python -m tools.job_tool expectation-block --job-id J --expectation-id E --code needs_human --detail "..."`. `job_tool` enforces Room scope, then calls `Job.block_expectation(E, code=..., detail=..., by="lane")`.
2. **Entry point (reconciler inference)**: in `_reconcile_project`, the annotation is written when and only when the recovery budget is spent (`attempts >= _max_attempts()`), from two call sites that together make the write crash-safe — the fresh-escalation write and the crash-window repair write. Both re-fetch the Job (`Job.query.get(id=job.id, room_id=job.room_id)`) and call `block_expectation(E, code="attempts_exhausted", detail=<escalation text>, by="reconciler")` on that snapshot; **neither branch has an existing re-fetch** (the one at `:499` sits below both, past the `continue` at `:494`), so the build adds one at each rather than writing through the stale scan object. On the fresh-escalation path, escalation is written first and the annotation second, so a Job whose write is refused (corrupt goal) still pages. See **Technical Approach → Crash-window resolution** for the exact control flow and why two sites, not one.
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
- PM check-ins: 0 (the four open questions are settled in Technical Approach → Settled Decisions; nothing is waiting on a human)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies beyond the repo venv and the test Redis pool.

## Solution

### Key Elements

- **Blocked annotation on an open entry**: `entry["blocked"]` is `None` (or absent, for pre-existing entries) or `{"code": str, "detail": str, "ts": iso, "by": "lane" | "reconciler" | "pm"}`. Orthogonal to `removed_ts`.
- **Closed reason vocabulary** (`models/job.py::BLOCKED_REASONS`): **four** members, each with a day-one writer — `attempts_exhausted` (reconciler), `needs_human`, `missing_credential`, `upstream_unmergeable` (lane or PM via `job_tool expectation-block`). `block_expectation` rejects any other code loudly (`ValueError`), the same posture as `add_expectation`'s direction check. A test asserts the exact frozen set, so an addition is a deliberate edit in two places rather than a silent widening. *(Revision pass: `owner_gone` was cut — see Settled Decision 1.)*
- **Three writers, all recorded, one code reserved**: the reconciler at its escalation seam, and the PM or the owning lane through `job_tool`. `by` is one of `"reconciler" | "pm" | "lane"` and is itself validated against a closed set. `attempts_exhausted` is **reconciler-only**: `block_expectation` raises `ValueError` if that code arrives with any other `by`. No other code may be written with `by="reconciler"`.
- **Reconciler backs off**: a blocked row is skipped and surfaced as a finding; the attempts/cooldown machinery is never touched for it.
- **Rest is unchanged**: a blocked expectation is still open and still pins the Job `active`.

### Flow

Lane cannot deliver → `job_tool expectation-block` → entry carries `blocked` → reconciler tick skips it, findings show `blocked: E needs_human` → PM sees it in `job_tool show` / operator log → PM either fixes and `expectation-unblock`s (reconciler resumes) or `expectation-remove`s (discharged, annotation preserved in history).

### Technical Approach

- **Annotation, not a third state.** A row can block and later unblock without ever being discharged; a state field would need a back-transition and would tempt readers into treating blocked as done (the #1208 shape). `removed_ts` keeps its single meaning.
- **No projection field.** The reconciler's scan root is already bounded by `has_open_expectations`; blocked rows are a subset of that set, so a third `IndexedField` buys nothing and adds a projection to keep honest. `blocked_expectations()` is a reader over the goal JSON.
- **All writes through `_write_goal_data`, all reads for write through `_mutable_goal_data()`.** A corrupt goal therefore refuses a block/unblock too, and the Job stays on the corrupt-goal path (rule 8, `durability-model.md`).
- **Escalation writes the annotation, not the first steer — and only at the budget-spent seam.** The first re-steer is legitimate recovery, not a stall. `_escalate_once` is called from three places; only the `attempts >= _max_attempts()` condition writes the annotation, because it is the only one where the recovery budget is *spent*. The evidence-path escalation (`:523`) and the no-PM-no-slug escalation (`:554`) both happen with attempts remaining and are followed by further ticks that can still steer or respawn; annotating them would freeze a row the reconciler is still legitimately working. An owner-gone row that has been respawned successfully is not blocked.

- **Crash-window resolution: the annotation must be reachable without the escalation key.** *(Added by the revision pass; this is the critique's KEY CONCERN and it is real.)* The pre-revision plan put the annotation strictly after `_escalate_once`, which claims a Redis key with a multi-day TTL via SETNX. Read against the real code, that ordering reintroduces the bug this issue exists to fix:

  ```
  :472   if _escalation_exists(job.job_id, eid) is not False:   # ← gate
  :473       continue
  :474   attempts = _attempts_count(job.job_id, eid)
  :480   if attempts >= _max_attempts():
  :481       sent, sup = _escalate_once(...)                    # ← claims the TTL key
             <pre-revision plan wrote the annotation HERE>
  :494       continue
  ```

  If the reconciler dies (or the worker is restarted, or Redis write of the goal fails hard) between `_escalate_once` returning and `block_expectation` landing, the TTL key exists but the annotation does not. Every later tick for the whole escalation TTL hits the `:472` gate and `continue`s: no re-steer, no re-escalation, no annotation, and `blocked_expectations()` reports nothing. One page fired and then silence — the exact silent stall in the Problem statement.

  **Chosen resolution: make the annotate step reachable independently of the escalation key, as a self-healing repair at the `:472` gate — not by reordering the escalation.** Reordering (annotate before escalate) was considered and rejected: `block_expectation` raises `CorruptGoalError` on a corrupt goal, the per-entry `try` at `:567` swallows it, and the `continue` that unwinding implies would skip the escalation entirely — a corrupt-goal Job would stop paging, which is a strictly worse failure than the one being fixed. Escalate-first is load-bearing and stays.

  The build instead lifts the attempts read above the gate and adds an idempotent repair inside it:

  ```python
  attempts = _attempts_count(job.job_id, eid)
  if attempts is None:
      findings.append(f"gate-unknown: attempts-read {eid}")
      continue
  budget_spent = attempts >= _max_attempts()

  escalated = _escalation_exists(job.job_id, eid)
  if escalated is not False:
      # Crash-window repair (#2862): a prior tick may have claimed the
      # escalation key and died before annotating. Re-assert the annotation
      # idempotently, then skip exactly as before.
      if escalated is True and budget_spent and entry.get("blocked") is None:
          <re-fetch; block_expectation(code="attempts_exhausted", by="reconciler")>
          findings.append(f"blocked: {eid} attempts_exhausted")
      continue
  if budget_spent:
      sent, sup = _escalate_once(...)        # unchanged, still first
      ...
      <re-fetch; block_expectation(code="attempts_exhausted", by="reconciler")>
      continue
  ```

  Four properties this preserves, each one a test in Test Impact:
  1. `escalated is True`, never `is not False` — `_escalation_exists` returns `None` on a Redis read failure (`reflections/expectation_reconciler.py:166-173`), and a read failure is not evidence that an escalation happened. The `continue` still fires on `None`, matching today's behavior.
  2. `entry.get("blocked") is None` makes the repair idempotent: once annotated, the blocked-skip added at `:461` catches the row before it ever reaches the gate again, so the repair runs at most once per row.
  3. Moving `_attempts_count` above the gate costs one extra Redis read per escalated row per tick and changes no behavior: its `None` branch keeps the same `gate-unknown` finding and the same `continue`.
  4. Both writes carry `by="reconciler"` and `code="attempts_exhausted"` and sit inside the existing per-entry `try`, so the `attempts_exhausted` ↔ `by="reconciler"` biconditional and the three-escalation-site rule are untouched. `:523` and `:554` still write nothing.

  The cost of the resolution is that `grep -c "block_expectation" reflections/expectation_reconciler.py` is now `2`, not `1`. The Verification table is updated accordingly, and the anti-criterion that `:523`/`:554` stay bare moves to a test rather than a grep count.
- **Blocked rows stop re-escalating; the finding line is the recurring signal.** Because the blocked skip sits above the age/liveness checks, a row annotated `attempts_exhausted` is skipped on every later tick and `_escalation_exists`'s TTL-expiry re-escalation never fires for it again. That is the intended trade: one page plus a `blocked:` line every tick, instead of a page every escalation TTL forever. Documented in rule 9.
- **Every shipped code has a writer.** `missing_credential` and `upstream_unmergeable` are reachable from day one through `job_tool expectation-block` (lane or PM), which is a real writer. `owner_gone` has none and is therefore not shipped (Settled Decision 1); it arrives with the session-health drift-advisory writer.

### Settled Decisions

These four were open at first draft and are decided here; the rationale is recorded so critique can challenge the reasoning rather than re-derive it.

1. **Vocabulary members — ship the four that have a day-one writer; `owner_gone` is cut.** *(Reversed by the revision pass. The pre-revision decision shipped five and justified `owner_gone` as "so the enum does not need reopening when the session-health drift advisory grows a writer." The critique named that as speculative future-proofing for an unbuilt feature, and it is right: the repo's posture is no speculative abstraction, and "reopening the enum is expensive" is not true here — it is one constant, one frozen-set row, one docstring line, and one sentence of rule 9, all of which the session-health writer's own change has to touch anyway to explain who writes the new code and why it is trustworthy. Carrying an unemittable member inflates the frozen-set test, the docstrings, rule 9, and the `job_tool --code` choices with a value nothing can produce, which is worse than a four-line diff later.)* `attempts_exhausted`, `needs_human`, `missing_credential`, and `upstream_unmergeable` each have a writer the day this ships — the reconciler for the first, the lane or the PM at `job_tool expectation-block` for the other three, which is the entry point the issue exists to serve. `owner_gone` lands in the change that wires the session-health writer, alongside the trust question that writer raises. The frozen-set test asserts exactly these four and is what keeps "closed vocabulary" a real property rather than a comment.
2. **Lane self-report — allowed, with `attempts_exhausted` fenced off.** The lane is the only party that knows why it cannot deliver. Refusing it the write does not make the lane deliver; it makes the stall silent, which is precisely the bug this issue exists to fix. The gaming risk (Risk 2) is closed structurally rather than by policy: blocking does not retire the obligation. The row stays open, keeps `has_open_expectations` true, keeps the Job `active`, prints a `blocked:` line in the operator log every tick, and shows in `job_tool show` — a lane that blocks to escape work has made itself *more* visible, not less. The one code that would confer real authority, `attempts_exhausted` (the reconciler's own "budget spent" verdict), is rejected from any `by` other than `"reconciler"`, so a lane cannot forge the reconciler's judgment. `by` is recorded on every annotation.
3. **Rest — confirmed unchanged: a blocked expectation keeps the Job pinned `active`.** Blocked means unfinished work awaiting a human, which is exactly what `active` already means. Letting a blocked Job rest would make "at rest" mean two different things (nothing outstanding / something outstanding that nobody is working) and would require a second visibility channel to compensate. The finding line and `job_tool show` are that channel today and cost nothing. This also keeps `_write_goal_data`'s existing `status="active"` forcing untouched: the annotation adds no branch to the chokepoint.
4. **Migration — none registered, deliberately.** The addendum's `MIGRATIONS` requirement is scoped to the Popoto *field set*, because `run_pending_migrations()` exists to backfill Popoto-managed keys and rebuild indexes. This change adds a key inside an existing `Field(null=True)` JSON payload: no field is added, renamed, retyped, or indexed, and `entry.get("blocked")` makes absence semantically identical to not-blocked, so there is nothing to backfill. Registering a no-op would write a false record into `data/migrations_completed.json` and set a precedent that JSON-payload changes need migrations. The reading is stated in **Update System** so a reviewer does not re-litigate it.
- **Reconciler invariant amended, not broken.** "No writes outside its own bookkeeping keys" becomes "never discharges; its only Job write is the blocked annotation through the chokepoint".
- **Absence is not-blocked.** Every reader uses `entry.get("blocked")`; the goal JSON is schemaless and pre-existing entries have no key.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_reconcile_project`'s per-entry `try` swallows and logs; add a test that a `CorruptGoalError` raised by `block_expectation` at the escalation seam still produces the escalation and logs a warning (escalate first, annotate second).
- [ ] `job_tool` converts `ValueError` / `CorruptGoalError` to `JobToolError`; test both for the two new subcommands.

### Empty/Invalid Input Handling
- [ ] `block_expectation` with an unknown code, an empty code, or an unknown/discharged expectation id: `ValueError` for the code, `False` for a missing/discharged id (mirrors `discharge_expectation`). Tests for each.
- [ ] `detail` may be empty; `code` may not.
- [ ] `by` outside `{"reconciler", "pm", "lane"}`: `ValueError`.
- [ ] `code="attempts_exhausted"` with `by="lane"` or `by="pm"`: `ValueError` (Settled Decision 2 — the forgery guard). And `by="reconciler"` with any code other than `attempts_exhausted`: `ValueError`.
- [ ] The vocabulary is frozen: a test asserts `BLOCKED_REASONS` equals the exact four-member set, so widening it is a deliberate two-file edit.
- [ ] `block_expectation(by="pm"|"lane")` against an entry already annotated `by="reconciler"` returns `False`, writes nothing, and leaves the existing annotation byte-identical (Race 3).

### Error State Rendering
- [ ] `job_tool show` renders `blocked` on the entry; `job_tool expectation-block` on a Job in another Room fails with the Room-scope `JobToolError`.

## Test Impact

- [ ] `tests/unit/test_job_model.py` — UPDATE: add a `TestBlockedExpectations` class (block, unblock, discharge-preserves-annotation, absent key reads as not blocked, unknown code rejected, unknown `by` rejected, the `attempts_exhausted`/`by` cross-guard in both directions, the frozen-vocabulary assertion — **exactly the four members**, corrupt goal refuses, `has_open_expectations` and `status` unchanged by block, a blocked inbound expectation still counts as open, and the Race 3 precedence rule: a `pm`/`lane` block over an existing `by="reconciler"` annotation returns `False` and writes nothing; the reconciler over a `lane` annotation writes; `unblock_expectation` clears a reconciler annotation regardless of caller).
- [ ] `tests/unit/reflections/test_reflections_expectation_reconciler.py` — UPDATE: blocked row is skipped with a `blocked:` finding and no steer/respawn; site B (fresh escalation, budget spent) writes `attempts_exhausted`; **site A crash-window regression test** — seed the escalation key with no annotation and assert the next tick writes `attempts_exhausted` and emits the `blocked:` finding (this test must be RED against a build that only implements site B); site A does **not** fire when `_escalation_exists` returns `None`; site A does not re-write an already-annotated row; the `:523` and `:554` escalation sites write **no** annotation (anti-test — guards the seam choice against a build that annotates all three); a refused write (corrupt goal) still escalates.
- [ ] `tests/unit/test_job_tool.py` — UPDATE: two new subcommands, Room scope, error conversion.
- [ ] `tests/unit/test_promise_advisory.py` — no change: the gate clears on an open inbound expectation; a blocked inbound expectation is still open (asserted by one new row in `test_job_model.py`, not here).

## Rabbit Holes

- Designing a generic state machine for expectations. The obligation primitive is deliberately one entry shape with one discharge; a blocked annotation is the whole scope.
- Inferring blocked from age alone. Age is already what the reconciler's `min_age` and attempts do; a second age heuristic would disagree with the first.
- A dashboard surface. `ui/data/sdlc.py` reads `len(job.open_expectations())`; leave it, a blocked count is a follow-on once real data exists.
- Wiring `owner_gone` from session health in this plan — and, after the revision pass, not even reserving the code. The vocabulary member and its writer land together in that separate change, with its own trust question.

## Risks

### Risk 1: The reconciler stops acting on rows that were only transiently blocked
**Impact:** A lane blocks on `missing_credential`, the credential is added, nobody unblocks; the row sits forever.
**Mitigation:** The finding line repeats every tick (30 min) in the operator log, exactly like `corrupt-goal`; `job_tool show` exposes it to the PM on every read. Rest is unchanged, so the Job never disappears. Unblock is one command.

### Risk 2: Trust asymmetry between writers
**Impact:** A lane self-reports `needs_human` to get out of work; the reconciler backs off.
**Mitigation:** structural, not policy (see Settled Decision 2). Blocking retires nothing: the row stays open, the Job stays `active`, a `blocked:` finding prints every tick, and `job_tool show` exposes it — a lane that blocks to escape work is more visible, not less. `attempts_exhausted`, the one code that carries the reconciler's authority, is rejected with any `by` other than `"reconciler"`, so it cannot be forged. `by` is recorded on every annotation.

### Risk 3: A block write races a concurrent expectation mutation
**Impact:** Two read-modify-write cycles on the same goal JSON; the later full write wins and drops the other's change.
**Mitigation:** Same whole-payload exposure as `add_expectation` vs `discharge_expectation` today, unchanged by this plan; the reconciler re-fetches a fresh snapshot immediately before writing at both annotate sites and writes only when the recovery budget is spent, at most once per row per escalation TTL. The *same-field* clobber this plan does introduce — a lane and the reconciler both writing `entry["blocked"]` — is a distinct hazard with its own precedence rule; see **Race Conditions → Race 3**.

## Race Conditions

### Race 1: reconciler annotation vs PM discharge
**Location:** `reflections/expectation_reconciler.py::_reconcile_project`, escalation branch
**Trigger:** PM discharges E between the reconciler's scan and its `block_expectation` call.
**Data prerequisite:** the snapshot written through must show E still open.
**State prerequisite:** none. The escalation branch runs *before* the cooldown claim at `:495` and before the fresh re-fetch at `:499`, so it holds neither — it is reached only once per escalation TTL, gated by `_escalation_exists` at `:472`.
**Mitigation:** two layers. The build adds a re-fetch inside both annotate sites (see Data Flow step 2), and independently `block_expectation` on a discharged or unknown id returns `False` and writes nothing — so even a fully stale object cannot resurrect a discharged row. A PM discharge always wins. The `False` return is the load-bearing guard; the re-fetch is what keeps the annotation from being written onto an otherwise stale goal payload and clobbering a concurrent `add_expectation`.

### Race 2: crash between the escalation claim and the annotation write
**Location:** `reflections/expectation_reconciler.py::_reconcile_project`, between `_escalate_once` (`:481`) and the new `block_expectation` call, gated downstream by `_escalation_exists` at `:472`.
**Trigger:** the reflection process dies, the worker restarts, or the goal write fails hard after `_escalate_once` has already claimed the multi-day-TTL Redis key via SETNX.
**Data prerequisite:** the escalation key exists; the entry carries no `blocked` annotation.
**State prerequisite:** `attempts >= _max_attempts()` — the row's recovery budget is spent.
**Consequence if unmitigated:** every later tick short-circuits at the `:472` gate for the whole escalation TTL. No re-steer, no re-escalation, no annotation, `blocked_expectations()` empty. One page, then silence — the Problem statement's silent stall, reintroduced by the fix.
**Mitigation:** the crash-window repair (site A) in **Technical Approach → Crash-window resolution**. The annotation becomes reachable through the `:472` gate itself, so the next tick after any crash re-asserts it. Self-healing rather than transactional: there is no cross-store transaction available between the Redis escalation key and the goal JSON, so convergence-on-next-tick is the right shape. Regression test named in Test Impact.

### Race 3: lane/PM annotation vs reconciler annotation on the same entry
**Location:** `models/job.py::block_expectation`, `entry["blocked"]` on one entry.
**Trigger:** a lane runs `job_tool expectation-block --code needs_human` while the reconciler is writing `attempts_exhausted` on the same entry. Both are read-modify-write cycles over the whole goal JSON; the later write wins silently.
**Consequence if unmitigated:** one annotation is lost with no error and no surfaced conflict — and the two carry materially different trust semantics (Settled Decision 2), so losing the reconciler's verdict to a lane's self-report is exactly the wrong direction.
**Precedence rule, decided here rather than in the builder's head:** **a `by="reconciler"` annotation is authoritative and is never overwritten by a `pm` or `lane` write.** `block_expectation` reads the existing `entry.get("blocked")` after `_mutable_goal_data()` and:
- existing `blocked.by == "reconciler"` and the incoming `by` is `"pm"` or `"lane"` → **no write, return `False`**. `job_tool` renders this as an explicit refusal ("expectation E is blocked by the reconciler as attempts_exhausted; unblock it first"), never as a silent success.
- every other combination (no existing annotation; existing `pm`/`lane` overwritten by anyone; existing `reconciler` overwritten by the reconciler) → writes normally. A later human annotation legitimately supersedes an earlier one, and the reconciler's own repeat write is the idempotent repair from Race 2.
- `unblock_expectation` is **not** restricted by precedence: a PM can always clear any annotation, including the reconciler's, and then re-block with their own code. That keeps the human the final authority without letting a lane forge past the reconciler.
The residual exposure is the ordinary whole-payload last-write-wins on `goal` shared with `add_expectation` / `discharge_expectation` (Risk 3), which this plan does not change.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2862] Part 2 (corrupt goal fails closed) is already landed on main under this issue's `Refs`; this plan does not touch `_parse_goal`, `goal_is_corrupt`, or the corrupt-goal tests.
- [SEPARATE-SLUG #2494] Any change to the Room / Job / AgentSession field set or indexes; this plan adds no Popoto field and no index, by design.

## Update System

No update system changes required, and **no `MIGRATIONS` entry is registered — this is deliberate, not an oversight** (Settled Decision 4). `docs/sdlc/do-plan.md` requires a migration on "changes to any Popoto model"; that requirement is scoped to the field set, because `run_pending_migrations()` exists to backfill Popoto-managed keys and rebuild indexes. This plan adds a key *inside* the existing `goal = Field(null=True)` JSON payload: no field is added, renamed, retyped, or indexed, no index needs rebuilding, and `entry.get("blocked")` makes an absent key semantically identical to not-blocked, so there is nothing to backfill. A no-op migration would write a false record into `data/migrations_completed.json` and establish the wrong precedent for JSON-payload changes. Restarting the worker/reflection scheduler after deploy picks up the reconciler change through the normal `/update` path.

## Agent Integration

The PM and lanes reach this through `tools/job_tool.py` (a CLI invoked with `VALOR_SESSION_ID` set), which is how they already add and discharge expectations; two new subcommands (`expectation-block`, `expectation-unblock`) and a `blocked` field in `show` output are the whole surface. No MCP server or `.mcp.json` change: `job_tool` is not MCP-exposed today and this plan keeps that. `.claude/commands/roles/prime-pm-role` gets one sentence pointing at `expectation-block` as the answer to "I cannot deliver this and here is why", next to the existing discharge instruction. Integration test: `tests/unit/test_job_tool.py` drives the CLI functions end to end against real Redis.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/durability-model.md`: add rule 9 after rule 8 (line 190), written in the existing numbered-list format `9. **…**` so the Verification anchor matches. It must name `BLOCKED_REASONS` and its four members literally, and cover the annotation shape, the three writers, the `attempts_exhausted` fence, the Race 3 precedence rule, rest unchanged, and the deliberate no-migration reading.
- [ ] Update `docs/features/expectation-reconciler.md`: amend the "no writes" invariant, add the `blocked:` finding, document the back-off, and state explicitly that a blocked row stops re-escalating, that only the budget-spent condition annotates, and that the annotation is written from two sites (fresh escalation and the crash-window repair) for the crash-safety reason in Race 2.
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
- [ ] **PM-facing outcome (the Desired Outcome, stated as a criterion):** a PM with only `tools/job_tool` can block an expectation with a reason code, see that annotation and its `code`/`detail`/`ts`/`by` in `job_tool show`, and unblock it so the reconciler resumes — all three verified end to end against real Redis in `tests/unit/test_job_tool.py`, with no direct model access and no log-reading.
- [ ] `attempts_exhausted` is unforgeable: only `by="reconciler"` may write it, and the reconciler writes nothing else (test, both directions).
- [ ] Only the `attempts >= _max_attempts()` condition annotates; the `:523` and `:554` `_escalate_once` sites leave the row un-annotated and re-steerable (test).
- [ ] **The crash window is closed:** an entry whose escalation key exists but whose annotation is absent is annotated on the next tick (Race 2 regression test), and that test is RED against a build that writes only at the fresh-escalation site.
- [ ] A `pm`/`lane` block cannot overwrite a `by="reconciler"` annotation — it returns `False`, writes nothing, and `job_tool` reports the refusal (Race 3 precedence test).
- [ ] `BLOCKED_REASONS` is exactly the four codes that have a day-one writer; `owner_gone` is absent (frozen-set test).
- [ ] No entry is added to `scripts/update/migrations.py::MIGRATIONS`, and the reason is stated in the Update System section and in rule 9.
- [ ] Tests pass (`scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/reflections/test_reflections_expectation_reconciler.py tests/unit/test_job_tool.py -n 2`).
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
- Add `BLOCKED_REASONS` (the frozen **four**: `attempts_exhausted`, `needs_human`, `missing_credential`, `upstream_unmergeable`), `BLOCKED_BY` (`reconciler`, `pm`, `lane`), `block_expectation`, `unblock_expectation`, `blocked_expectations` to `models/job.py`; both writers go through `_mutable_goal_data()` and `_write_goal_data`.
- Enforce the `attempts_exhausted` ↔ `by="reconciler"` biconditional in `block_expectation` (`ValueError` either way).
- Enforce the Race 3 precedence rule in `block_expectation`: an incoming `by` of `"pm"` or `"lane"` over an existing `entry["blocked"]["by"] == "reconciler"` returns `False` and writes nothing. `unblock_expectation` carries no such restriction.
- Update the `goal` schema comment.
- Add `TestBlockedExpectations` per Test Impact, including the corrupt-goal refusal, the legacy-entry row, the frozen-vocabulary assertion, and the cross-guard in both directions.

### 2. Tool: subcommands and error conversion
- **Task ID**: build-tool
- **Depends On**: build-model
- **Validates**: tests/unit/test_job_tool.py
- **Assigned To**: job-blocked-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `expectation-block` / `expectation-unblock`; `show` includes `blocked`; convert `ValueError` and `CorruptGoalError` to `JobToolError`.
- Render the Race 3 precedence refusal explicitly: a `False` return from `block_expectation` against an existing reconciler annotation prints "expectation E is blocked by the reconciler as attempts_exhausted; unblock it first" and exits non-zero — never a silent success.
- One sentence in the PM prime pointing at `expectation-block`.

### 3. Reconciler: skip, finding, escalation-seam write
- **Task ID**: build-reconciler
- **Depends On**: build-model
- **Validates**: tests/unit/reflections/test_reflections_expectation_reconciler.py
- **Assigned To**: reconciler-backoff-builder
- **Agent Type**: builder
- **Parallel**: true
- Skip blocked rows with a `blocked: <eid> <code>` finding before the age/liveness checks (`:461`), inside the existing per-entry `try`.
- Restructure the gate region exactly as **Technical Approach → Crash-window resolution** specifies: lift `_attempts_count` / `budget_spent` above the `_escalation_exists` gate at `:472`, keeping its `None` → `gate-unknown` finding and `continue` unchanged.
- **Site A (crash-window repair)**, inside the `_escalation_exists` gate: when `escalated is True` AND `budget_spent` AND `entry.get("blocked") is None`, re-fetch the Job and `block_expectation(..., code="attempts_exhausted", by="reconciler")`, append the `blocked:` finding, then `continue` as before. Use `is True`, never `is not False` — a `None` read failure must not be treated as an escalation.
- **Site B (fresh escalation)**, in the `budget_spent` branch (`:480`): escalate first, then re-fetch the Job (this branch has none today — the re-fetch at `:499` is below it) and `block_expectation(..., code="attempts_exhausted", by="reconciler")` on that snapshot. Escalate-first is load-bearing; do not reorder.
- Both sites sit inside the existing per-entry `try`. Leave `:523` and `:554` untouched — they escalate with attempts remaining and must stay re-steerable.
- Tests: skip + finding + no action; site B writes on fresh escalation; **site A repairs a row whose escalation key exists but whose annotation is absent** (the crash-window regression test); site A does not fire when `_escalation_exists` returns `None`; site A does not re-write an already-annotated row; no annotation from `:523`/`:554`; refused write still escalates.

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
| Model, reconciler, tool tests pass | `scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/reflections/test_reflections_expectation_reconciler.py tests/unit/test_job_tool.py -n 2 -q` | exit code 0 |
| Lint clean | `python -m ruff check models/job.py tools/job_tool.py reflections/expectation_reconciler.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/job.py tools/job_tool.py reflections/expectation_reconciler.py` | exit code 0 |
| No new index (anti-criterion, #2494 No-Go) | `grep -c "IndexedField(" models/job.py` | output contains 2 |
| Reconciler never discharges (anti-criterion) | `grep -c "discharge_expectation" reflections/expectation_reconciler.py` | match count == 0 |
| Vocabulary is closed | `grep -c "BLOCKED_REASONS" models/job.py` | output > 1 |
| Exactly the two sanctioned annotate sites (anti-criterion) | `grep -c "block_expectation" reflections/expectation_reconciler.py` | output == 2 (the fresh-escalation write and the crash-window repair write, both inside the `attempts >= _max_attempts()` logic; `:523` / `:554` stay bare) |
| Annotation is reconciler-attributed only (anti-criterion) | `grep -c 'by="reconciler"' reflections/expectation_reconciler.py` | output == 2 |
| No migration registered — MIGRATIONS dict clean (anti-criterion, Settled Decision 4) | `awk '/^MIGRATIONS/,0' scripts/update/migrations.py \| grep -ci blocked` | output == 0 |
| No migration registered — whole file at baseline (anti-criterion) | `grep -ci "blocked" scripts/update/migrations.py` | output == 1 |
| Rule 9 documented — vocabulary named | `grep -c "BLOCKED_REASONS" docs/features/durability-model.md` | output > 0 |
| Rule 9 documented — rule exists | `grep -c "^9\. \*\*" docs/features/durability-model.md` | output == 1 |

**Baseline measurements (taken on `761cf7b58`, the revision-pass baseline — every anti-criterion above is stated relative to a measured number, never an assumed zero):**

| Command | Baseline output | Reads |
|---|---|---|
| `grep -ci "blocked" scripts/update/migrations.py` | `1` (line 1559, an unrelated `blocked_by` docstring on `ImprovementModelRevision`) | GREEN at baseline, GREEN on a correct build, RED the moment a `blocked` migration is registered anywhere in the file. The previously written `== 0` was RED at baseline and would have failed the validator on a correct build. |
| `awk '/^MIGRATIONS/,0' scripts/update/migrations.py \| grep -ci blocked` | `0` | Scopes the assertion to the registration block, so the unrelated docstring cannot mask a real registration. |
| `grep -c "BLOCKED_REASONS" docs/features/durability-model.md` | `0` | RED before task 5, GREEN only after rule 9 names the vocabulary. The previously written `grep -c "blocked" … > 0` was already satisfied at baseline by unrelated "blocked draft" prose at line 151 and would have passed with task 5 skipped entirely. |
| `grep -c "^9\. \*\*" docs/features/durability-model.md` | `0` (the numbered rule list currently ends at rule 8, line 190) | RED before task 5, GREEN only once rule 9 is written in the list's own format. |
| `grep -c "block_expectation" reflections/expectation_reconciler.py` | `0` | RED before task 3, GREEN at exactly 2 after. Note this replaces the pre-revision `== 1`, which the crash-window resolution below invalidates by adding a second sanctioned call site. |
| `grep -c 'by="reconciler"' reflections/expectation_reconciler.py` | `0` | Same shape; pins both writes to the reconciler attribution. |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, History & Consistency | The Verification row "No migration registered (anti-criterion, Settled Decision 4)" runs `grep -ci "blocked" scripts/update/migrations.py` and expects `output == 0`, but the file already matches once at baseline (line 1559, an unrelated `blocked_by` docstring for `ImprovementModelRevision`). The check fails identically on a correctly-built plan and an incorrectly-built one, so it gates nothing and will read RED for the validator in task 4. | **ADDRESSED** — Verification table rows "No migration registered — MIGRATIONS dict clean" (`awk '/^MIGRATIONS/,0' … \| grep -ci blocked` == 0, measured `0` at baseline) and "— whole file at baseline" (`== 1`, measured), plus the new **Baseline measurements** table. Every command there was executed on `761cf7b58` and its real output recorded before being written into the plan. Task 4 runs the corrected rows. | Baseline count is 1, not 0. Replace the absolute assertion with a scoped or baseline-relative one — e.g. assert no `blocked` occurrence inside the `MIGRATIONS` registration block (`grep -A40 "^MIGRATIONS" scripts/update/migrations.py \| grep -ci blocked` == 0), or assert the count is unchanged from the pre-plan baseline of 1. Both critics converged on this independently. |
| CONCERN | Risk & Robustness | Crash window between escalation and annotation. `_escalate_once` claims the Redis escalation key via SETNX with a multi-day TTL *before* the plan's new `block_expectation` write. If the reconciler dies in that gap, the next tick hits `if _escalation_exists(job.job_id, eid) is not False: continue` at `reflections/expectation_reconciler.py:472` — which runs *above* the `attempts >= _max_attempts()` branch at `:480` where the annotation lives — so the row is skipped with no re-steer and no annotation for the whole escalation TTL. The page fired, the reconciler never backed off, and `blocked_expectations()` shows nothing: the exact silent stall this plan exists to fix. | **ADDRESSED** — new **Technical Approach → Crash-window resolution** (control-flow sketch, four preserved properties), new **Race Conditions → Race 2**, task 3 sites A/B, Test Impact crash-window regression test, and a Success Criteria line. Confirmed against the real code: the `:472` gate does sit above the `:480` branch. Reordering to annotate-before-escalate was considered and rejected with reason (a `CorruptGoalError` would then suppress the page); the resolution is an idempotent self-healing repair at the gate, keyed on `escalated is True` (not `is not False`, so a `None` Redis read failure never counts as an escalation). | The window is inside the `:480` branch, between `_escalate_once(...)` and the new re-fetch + `block_expectation(...)`. The guard that then hides it is the `continue` at `:472`, which task 3 does not touch. Fix by making the annotate step reachable independently of the escalation key: when `_escalation_exists(...)` is truthy AND `attempts >= _max_attempts()` AND `entry.get("blocked") is None`, still write the `attempts_exhausted` annotation before `continue`-ing. Keep escalate-first ordering for the fresh-escalation path so a corrupt-goal refusal still pages. |
| CONCERN | Risk & Robustness | A second concurrent-writer race is undocumented. This plan adds a lane/PM writer (`job_tool expectation-block`) alongside the reconciler writer, and both target the same `entry["blocked"]` key with a full-object overwrite. Risk 3 calls the goal-JSON exposure "unchanged by this plan", but a lane `needs_human` annotation and a reconciler `attempts_exhausted` annotation racing on one entry silently lose one write, with no error and no surfaced conflict — and the two `by` values carry materially different trust semantics per Settled Decision 2. | **ADDRESSED** — new **Race Conditions → Race 3** with the precedence rule decided in the plan: a `by="reconciler"` annotation is authoritative and a `pm`/`lane` write over it returns `False` and writes nothing; every other combination overwrites; `unblock_expectation` is deliberately unrestricted so a human stays the final authority. Wired into task 1 (model guard), task 2 (`job_tool` renders the refusal explicitly and exits non-zero, never a silent success), Test Impact, Failure Path Test Strategy, and Success Criteria. Risk 3 now points at Race 3 for the same-field hazard and keeps only the whole-payload exposure it actually owns. | Data Flow step 3 sets `entry["blocked"] = {...}` unconditionally; no plan text shows a refusal or merge when `entry.get("blocked")` is already non-null. This is a same-field last-write-wins clobber, distinct from the Race 1 discharge race. Add it as an explicit Race Conditions entry and decide the precedence rule in the plan rather than in the builder's head: simplest defensible rule is reconciler-wins-over-lane for `attempts_exhausted` (never overwrite an existing `by="reconciler"` annotation with a lane write), with the loser's write recorded as a no-op return rather than a silent success. |
| CONCERN | History & Consistency | The Verification row "Rule 9 documented" runs `grep -c "blocked" docs/features/durability-model.md` expecting `output > 0`, but that file already matches once at baseline (line 151, unrelated "blocked draft" prose). The check is vacuously satisfied even if task 5 (rule 9) is skipped entirely. | **ADDRESSED** — replaced by two rows anchored on strings measured at `0` on `761cf7b58`: `grep -c "BLOCKED_REASONS" docs/features/durability-model.md` > 0 and `grep -c "^9\. \*\*" docs/features/durability-model.md` == 1 (the numbered rule list currently ends at rule 8, line 190). Both are RED before task 5 and GREEN only after it. The Documentation section now requires rule 9 to use the list's own `9. **…**` format and to name `BLOCKED_REASONS` literally, so the anchors cannot drift from the doc task. | Same failure class as the BLOCKER row — an anti-criterion written against an assumed-zero baseline the probe disproves. Anchor on a string that cannot pre-exist: `grep -c "BLOCKED_REASONS" docs/features/durability-model.md` > 0, or a literal rule-9 heading match. |
| CONCERN | Scope & Value | `owner_gone` is a frozen vocabulary member with no writer anywhere in this plan; the plan admits it ships "so the enum does not need reopening when the session-health drift advisory grows a writer". That is future-proofing for an unbuilt feature, and it inflates the frozen-set test, the docstrings, rule 9, and the `job_tool --code` choices for a code path nothing can emit. | **ADDRESSED (critique accepted, prior decision reversed)** — `owner_gone` is cut. `BLOCKED_REASONS` is now the four codes with a day-one writer. Settled Decision 1 is rewritten to record the reversal and why the "reopening the enum is expensive" rationale does not hold (the session-health writer's own change must touch the constant, the frozen-set test, the docstring, and rule 9 anyway). Propagated to Key Elements, the Technical Approach bullet, Rabbit Holes, Failure Path Test Strategy, Test Impact, task 1, the Documentation section, and a Success Criteria line asserting `owner_gone` is absent. | Ship the four codes that have a day-one writer (`attempts_exhausted`, `needs_human`, `missing_credential`, `upstream_unmergeable`); add `owner_gone` in the change that wires the session-health writer. A four-member `BLOCKED_REASONS` frozenset is a strictly smaller diff; the frozen-set test asserts four instead of five and nothing else in Data Flow, the entry points, or `job_tool` changes. If the decision is to keep five, record the counter-rationale against the repo's no-speculative-abstraction posture rather than leaving Settled Decision 1 unchallenged. |
| CONCERN | History & Consistency | The Freshness Check cites `docs/plans/promise-gate-recorded-obligations.md` as the active overlapping plan whose gate behavior must not change, but that file does not exist in `docs/plans/`. The "blocked state must not change what clears the gate" claim, which Test Impact leans on to justify no change to `tests/unit/test_promise_advisory.py`, has no re-readable anchor. | **ADDRESSED** — citation dropped (re-confirmed non-existent this pass: `ls docs/plans/ \| grep -i "promise\|gate\|obligat"` returns nothing) and replaced with the two plans that do exist, `durability-room-job-agentrun.md` and `durability-m1-fence-canary.md`. The promise-gate claim is re-anchored on source: `bridge/promise_gate.py::promise_override_active` clears the gate on `job.open_expectations(direction="inbound")` at `bridge/promise_gate.py:451`, and a blocked entry is still open, so the gate is unaffected. Freshness Check now carries that anchor explicitly, and the no-change disposition for `tests/unit/test_promise_advisory.py` points at the new `test_job_model.py` row that asserts the property. | Verified by direct existence check: no file in `docs/plans/` matches `promise\|gate\|obligat`. Either correct the path if the plan was renamed, or drop the citation and anchor the claim directly on the promise-gate source file and `tests/unit/test_promise_advisory.py` (which does exist, 405 lines), so the no-change disposition is checkable. |
| NIT | Scope & Value | Every Success Criteria bullet is a grep count, a test pass, or a doc-exists check; none states the PM-facing outcome the Desired Outcome promises ("The PM can block, unblock, and see blocked state through `tools/job_tool`"). | **ADDRESSED** — a PM-facing Success Criteria line now leads the list: a PM with only `tools/job_tool` can block with a reason code, see the annotation and its `code`/`detail`/`ts`/`by` in `show`, and unblock so the reconciler resumes, verified end to end in `tests/unit/test_job_tool.py` against real Redis with no direct model access and no log-reading. No new test was added, as the critique noted. | No new test needed — the Error State Rendering checklist already covers `job_tool show` rendering `blocked`. This only elevates it to a named Success Criteria line. |
