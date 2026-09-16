---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2862
last_comment_id: 5571046486
revision_applied: true
revision_applied_at: 2026-09-16T10:57:05Z
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
2. **Entry point (reconciler inference)**: in `_reconcile_project`, the annotation is written when and only when the recovery budget is spent (`attempts >= _max_attempts()`), from two call sites that together make the write crash-safe — the fresh-escalation write and the crash-window repair write. Both re-fetch the Job (`Job.query.get(id=job.id, room_id=job.room_id)`) and call `block_expectation(E, code="attempts_exhausted", detail=<escalation text>, by="reconciler")` on that snapshot; **neither branch has an existing re-fetch** (the one at `:499` sits below both, past the `continue` at `:494`), so the build adds one at each rather than writing through the stale scan object. On the fresh-escalation path, escalation is written first and the annotation second, so a Job whose write is refused (corrupt goal) still pages. The crash-window repair sits **above** the owner-liveness gate (`:465-470`) so it converges on the next tick regardless of whether the owner reads as alive. See **Technical Approach → Crash-window resolution** for the exact control flow, the placement decision, and why two sites, not one.
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

  **Chosen resolution: make the annotate step reachable independently of the escalation key, as a self-healing repair that sits ABOVE the owner-liveness gate — not by reordering the escalation.** Reordering (annotate before escalate) was considered and rejected: `block_expectation` raises `CorruptGoalError` on a corrupt goal, the per-entry `try` at `:567` swallows it, and the `continue` that unwinding implies would skip the escalation entirely — a corrupt-goal Job would stop paging, which is a strictly worse failure than the one being fixed. Escalate-first is load-bearing and stays.

  **Placement decision (revision round 2; the critique's key concern, and it is correct).** An earlier draft of this section put the repair inside the `_escalation_exists` gate at `:472`. That gate sits *below* the owner-liveness gate at `:465-470`, which this plan does not touch: a `None` liveness read `continue`s at `:467` and a live owner (`gone is False`) `continue`s at `:469`. A repair placed at `:472` therefore converges only on a tick where the owner is *also* still gone — and in the live-owner case the row can stay silent for the whole escalation TTL, which is exactly the window Race 2 exists to close. **The repair is therefore placed between the age-check `continue` at `:463` and the `gone = _owner_is_gone(owner)` call at `:465`**, so "next tick" is literally true. Option (b) — keeping the low placement and weakening the claim to "the next tick on which the owner is still gone" — was rejected: the annotation records a fact fixed in the past (the recovery budget was spent and an escalation fired), not a statement about the owner's current liveness, so gating it on liveness makes the durability of the record depend on an unrelated live read. Nor does the high placement cost any recovery: once the escalation key exists, *every* path below the `:472` gate already `continue`s, so an escalated budget-spent row has no remaining reconciler action to freeze. Annotating it can only make it more visible.

  The build adds this block, and leaves the liveness gate and the `:472` gate otherwise untouched:

  ```python
  age = _entry_age_seconds(entry, now)
  if age is None or age < min_age:
      continue

  # Site A — crash-window repair (#2862). Above the liveness gate on purpose:
  # the annotation records a past fact (budget spent, escalation fired) and
  # must not wait for a tick on which the owner also reads as gone.
  if entry.get("blocked") is None and _escalation_exists(job.job_id, eid) is True:
      attempts_seen = _attempts_count(job.job_id, eid)
      if attempts_seen is not None and attempts_seen >= _max_attempts():
          <re-fetch; block_expectation(code="attempts_exhausted", by="reconciler")>
          findings.append(f"blocked: {eid} attempts_exhausted")
          continue
      # attempts unreadable or budget not spent: fall through unchanged;
      # the :472 gate below still continues on an existing escalation key.

  gone = _owner_is_gone(owner)        # unchanged
  ...
  if _escalation_exists(job.job_id, eid) is not False:   # unchanged
      continue
  ...
  if budget_spent:                    # site B, unchanged ordering
      sent, sup = _escalate_once(...)        # still first
      ...
      <re-fetch; block_expectation(code="attempts_exhausted", by="reconciler")>
      continue
  ```

  Five properties this preserves, each one a test in Test Impact:
  1. `is True`, never `is not False` — `_escalation_exists` returns `None` on a Redis read failure (`reflections/expectation_reconciler.py:166-173`), and a read failure is not evidence that an escalation happened. On `None` site A does nothing and the row falls through to today's behavior.
  2. `entry.get("blocked") is None` makes the repair idempotent, backed by the blocked-skip added at `:461`, which catches an annotated row before it reaches site A at all. The repair runs at most once per row.
  3. Site A emits no `gate-unknown` finding of its own. An unreadable `_attempts_count` falls through silently rather than adding a new finding on a path (live owner) that is silent today; the existing `gate-unknown: attempts-read` at `:476` is untouched and still fires where it always did.
  4. The cost is one extra `_escalation_exists` read per aged, unannotated outbound row per tick — including rows whose owner is alive, which skip that read today. The scan root is already bounded by `has_open_expectations` and `min_age`, so this is a small constant against an already Redis-bound loop, and no control flow changes for any row where site A does not fire.
  5. Both writes carry `by="reconciler"` and `code="attempts_exhausted"` and sit inside the existing per-entry `try`, so the `attempts_exhausted` ↔ `by="reconciler"` biconditional and the three-escalation-site rule are untouched. `:523` and `:554` still write nothing.

  Sites A and B do identical re-fetch-and-annotate work; **factoring them into a shared private helper is explicitly allowed** (and is the preferred shape). Nothing in the Verification table counts call sites, precisely so a correct refactor cannot fail the gate.
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

- [ ] `tests/unit/test_job_model.py` — UPDATE: add a `TestBlockedExpectations` class (block, unblock, discharge-preserves-annotation, absent key reads as not blocked, unknown code rejected, unknown `by` rejected, the `attempts_exhausted`/`by` cross-guard in both directions, the frozen-vocabulary assertion — **exactly the four members**, corrupt goal refuses, `has_open_expectations` and `status` unchanged by block, a blocked inbound expectation still counts as open, and the Race 3 precedence rule: a `pm`/`lane` block over an existing `by="reconciler"` annotation returns `False` and writes nothing; the reconciler over a `lane` annotation writes; `unblock_expectation` clears a reconciler annotation regardless of caller). Two rows carried in verbatim from **Failure Path Test Strategy**, which task 1 would otherwise not surface:
  - **unknown / already-discharged id returns `False` and writes nothing** — `job.block_expectation("nonexistent-eid", code="needs_human", by="lane", detail="")` is `False`, and the same for a discharged entry's id; the stored `goal` payload is byte-identical before and after (same idiom as the Race 3 no-write assertion). Mirrors `discharge_expectation` (`models/job.py:416-429`).
  - **empty `code` raises `ValueError`** — `block_expectation(eid, code="", by="lane", detail="")` raises before any `_write_goal_data` call. Distinct from the already-listed unknown-code test, which uses a non-empty string absent from `BLOCKED_REASONS`.
- [ ] `tests/unit/reflections/test_reflections_expectation_reconciler.py` — UPDATE: blocked row is skipped with a `blocked:` finding and no steer/respawn; site B (fresh escalation, budget spent) writes `attempts_exhausted`; **site A crash-window regression test, owner gone** — seed the escalation key and the spent attempts counter with no annotation and assert the next tick writes `attempts_exhausted` and emits the `blocked:` finding (RED against a build that only implements site B); **site A crash-window regression test, owner ALIVE** — identical seeding but `_owner_is_gone` returns `False`, asserting the repair still fires on that tick (RED against a build that places site A inside the `:472` gate, which is the placement rejected in Technical Approach; this is the row that discriminates the two designs); site A does **not** fire when `_escalation_exists` returns `None`; site A does not fire when attempts are below `_max_attempts()` or `_attempts_count` returns `None`, and emits no `gate-unknown` finding in either case; site A does not re-write an already-annotated row; the `:523` and `:554` escalation sites write **no** annotation (anti-test — guards the seam choice against a build that annotates all three); a refused write (corrupt goal) still escalates.
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
**Mitigation:** the crash-window repair (site A) in **Technical Approach → Crash-window resolution**, placed above the owner-liveness gate at `:465-470` so that neither a live owner nor an unreadable liveness probe can defer it. The next tick after any crash re-asserts the annotation, unconditionally on owner state. Self-healing rather than transactional: there is no cross-store transaction available between the Redis escalation key and the goal JSON, so convergence-on-next-tick is the right shape. Two regression tests named in Test Impact — one with the owner gone, one with the owner alive; the second is the one that discriminates this placement from the rejected below-the-gate placement.

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
- [ ] Update `docs/features/expectation-reconciler.md`: amend the "no writes" invariant, add the `blocked:` finding, document the back-off, and state explicitly that a blocked row stops re-escalating, that only the budget-spent condition annotates, and that the annotation is written from two sites (fresh escalation and the crash-window repair) for the crash-safety reason in Race 2 — including why the repair sits above the owner-liveness gate.
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
- [ ] **The crash window is closed on the next tick, unconditionally on owner liveness:** an entry whose escalation key exists, whose attempts are spent, and whose annotation is absent is annotated on the next tick whether `_owner_is_gone` returns `True` or `False` (two Race 2 regression tests). The owner-gone test is RED against a build that writes only at the fresh-escalation site; the owner-alive test is RED against a build that places the repair below the liveness gate.
- [ ] `block_expectation` on an unknown or already-discharged expectation id returns `False` and leaves the stored `goal` payload byte-identical (test).
- [ ] `block_expectation` with an empty `code` raises `ValueError` before any write (test), distinct from the unknown-code test.
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
- **Site A (crash-window repair)**, inserted between the age-check `continue` at `:463` and the `gone = _owner_is_gone(owner)` call at `:465` — above the liveness gate, per the placement decision in **Technical Approach → Crash-window resolution**: when `entry.get("blocked") is None` AND `_escalation_exists(...) is True` AND `_attempts_count(...)` is not `None` and `>= _max_attempts()`, re-fetch the Job, `block_expectation(..., code="attempts_exhausted", by="reconciler")`, append the `blocked:` finding, and `continue`. Use `is True`, never `is not False` — a `None` read failure must not be treated as an escalation. Any other combination falls through with no finding and no control-flow change; the liveness gate at `:465-470` and the `_escalation_exists` gate at `:472` are otherwise left exactly as they are, including the existing `gate-unknown: attempts-read` at `:476`.
- **Site B (fresh escalation)**, in the `attempts >= _max_attempts()` branch (`:480`): escalate first, then re-fetch the Job (this branch has none today — the re-fetch at `:499` is below it) and `block_expectation(..., code="attempts_exhausted", by="reconciler")` on that snapshot. Escalate-first is load-bearing; do not reorder.
- Both sites sit inside the existing per-entry `try`. Leave `:523` and `:554` untouched — they escalate with attempts remaining and must stay re-steerable.
- Sites A and B perform identical re-fetch-and-annotate work; factoring them into one private helper called from both branches is allowed and preferred. No verification check counts call sites.
- Tests: skip + finding + no action; site B writes on fresh escalation; **site A repairs a row whose escalation key exists but whose annotation is absent, with the owner gone AND again with the owner alive** (the two crash-window regression tests); site A does not fire when `_escalation_exists` returns `None`, when attempts are below the max, or when `_attempts_count` returns `None`, and adds no `gate-unknown` finding in those cases; site A does not re-write an already-annotated row; no annotation from `:523`/`:554`; refused write still escalates.
- Five of those tests carry **mandated names**, because the Verification table selects them with `-k`: `test_crash_window_repair_owner_gone`, `test_crash_window_repair_owner_alive`, `test_annotation_attributed_to_reconciler`, `test_no_annotation_from_evidence_escalation`, `test_no_annotation_from_no_pm_escalation`.

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
| Crash window closes on the next tick regardless of owner liveness (behavioral) | `scripts/pytest-clean.sh tests/unit/reflections/test_reflections_expectation_reconciler.py -k "crash_window_repair" -q` | exit 0, **2 tests collected and passed** (`..._owner_gone`, `..._owner_alive`). The `ZERO TESTS EXECUTED` guard makes a mistyped `-k` fail loudly rather than pass vacuously. |
| Annotation is reconciler-attributed and only the budget-spent seam annotates (behavioral anti-criterion) | `scripts/pytest-clean.sh tests/unit/reflections/test_reflections_expectation_reconciler.py -k "annotation_attributed or no_annotation_from" -q` | exit 0, **3 tests collected and passed** (`test_annotation_attributed_to_reconciler`, `test_no_annotation_from_evidence_escalation`, `test_no_annotation_from_no_pm_escalation`) |
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
| `scripts/pytest-clean.sh tests/unit/reflections/test_reflections_expectation_reconciler.py -k "crash_window_repair" -q` | `ZERO TESTS EXECUTED` (exit 1) — no such tests exist yet | RED before task 3, GREEN at exactly 2 passing tests after. |
| `scripts/pytest-clean.sh tests/unit/reflections/test_reflections_expectation_reconciler.py -k "annotation_attributed or no_annotation_from" -q` | `ZERO TESTS EXECUTED` (exit 1) | RED before task 3, GREEN at exactly 3 passing tests after. |

*(Revision round 2: the two call-site greps these rows replace — `grep -c "block_expectation" … == 2` and `grep -c 'by="reconciler"' … == 2` — were dropped. They asserted a factoring, not a behavior, and would have failed a correct build that pulls the identical site-A/site-B annotate work into one private helper. Every property they stood in for already has a named test, and the two rows above select those tests directly.)*

## Critique Results

**Round 2 (re-critique of the revised plan).** Round 1's 7 rows were all re-audited this
pass and are confirmed resolved: every one of the plan's six recorded baseline measurements
reproduces exactly at HEAD, every reconciler line number (`:455 :461 :472 :474 :480 :481
:495 :499 :523 :554 :567`) and every `models/job.py` symbol coordinate is exact, the
nonexistent `promise-gate-recorded-obligations.md` citation is gone and its replacement
anchor is real, `owner_gone` is cut with no orphaned reference surviving anywhere, and the
rule-9 / MIGRATIONS anti-criteria are now RED-before / GREEN-after rather than vacuous.
The rows below are new findings against text the revision introduced.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (corroborated by the driver's own structural trace) | The site-A crash-window repair is not reachable on "the next tick after any crash" as Technical Approach and Race 2 claim. The per-entry guard chain gates on owner liveness at `reflections/expectation_reconciler.py:465-470`, which sits ABOVE the `_escalation_exists` gate at `:472` where site A lives and is untouched by this plan. If a later tick's fresh `_owner_is_gone(owner)` returns `False` (a non-terminal row now claims that owner) the loop `continue`s at `:469` and the repair never runs; if it returns `None` it `continue`s at `:467` with a `gate-unknown` finding. The repair therefore converges on "the next tick on which the owner is also still gone", not on the next tick, and in the `False` case the silence can persist for the escalation TTL — the very window Race 2 was added to close. The named crash-window regression test keeps the owner gone throughout, so this branch would not go red. | **RESOLVED (revision round 2)** — option (a): site A moves above the liveness gate, between the age-check `continue` at `:463` and `gone = _owner_is_gone(owner)` at `:465`. Decision and rejection of option (b) stated in **Technical Approach → Crash-window resolution → Placement decision**; Data Flow step 2, Race 2, task 3, Test Impact and Success Criteria updated. Second regression row added: escalation key present, attempts spent, no annotation, `_owner_is_gone` returns `False` → repair still fires (`test_crash_window_repair_owner_alive`), which is RED against the rejected placement. | Either (a) move the site-A check to sit between the age-check `continue` at `:463` and the `gone = _owner_is_gone(owner)` call at `:465`, falling through to the liveness gate only when no repair fires, or (b) keep the placement and correct the plan's claim to "the next tick on which the owner is still gone", stating why annotating `attempts_exhausted` on a row whose owner has come back alive would be wrong. Whichever is chosen, add a second regression row to the Test Impact crash-window bullet: escalation key present, no annotation, `_owner_is_gone` returns `False` — assert the documented outcome explicitly, because the currently-named test cannot distinguish the two designs. |
| CONCERN | Scope & Value | The two new Verification rows `grep -c "block_expectation" reflections/expectation_reconciler.py` == 2 and `grep -c 'by="reconciler"' ...` == 2 assert on call-site count rather than behavior, and penalize a correct, cleaner build. A builder who factors the identical re-fetch-and-annotate work at sites A and B into one private helper (e.g. `_annotate_attempts_exhausted(job, eid)`) called from both branches produces a less duplicated and still-correct reconciler in which each literal string appears once, failing both greps. | **RESOLVED (revision round 2)** — both grep rows dropped from Verification and replaced with two behavioral rows that select the named tests by `-k` (`crash_window_repair`; `annotation_attributed or no_annotation_from`), with baseline rows showing both RED (`ZERO TESTS EXECUTED`) before task 3. Technical Approach and task 3 now state explicitly that a shared private helper for sites A and B is allowed and preferred, and that no check counts call sites. Five test names are mandated in task 3 so the `-k` selectors are stable. | Every property these two rows are standing in for — `escalated is True` rather than `is not False`, idempotency via `entry.get("blocked") is None`, `by="reconciler"` on both writes, and `:523`/`:554` staying bare — already has a named test in Test Impact, so the greps add no coverage. Drop both rows, or re-anchor them on something a refactor cannot break (e.g. assert the two call *conditions* exist). If forbidding the helper is genuinely intended, state that reasoning in Technical Approach rather than encoding it as a grep count the builder must reverse-engineer. |
| CONCERN | History & Consistency | Failure Path Test Strategy mandates two `block_expectation` tests that the Test Impact checklist never names: `False` on an unknown or already-discharged expectation id (the `discharge_expectation` idiom it is meant to mirror, `models/job.py:416-429`), and `ValueError` on an empty `code`. Success Criteria is silent on both. Task 1 points the builder at Test Impact ("Add `TestBlockedExpectations` per Test Impact"), so a builder working strictly from that list satisfies every named row while leaving both paths unexercised. | **RESOLVED (revision round 2)** — both tests added verbatim to the `tests/unit/test_job_model.py` Test Impact line (missing/discharged id → `False` with a byte-identical `goal` payload; empty `code` → `ValueError` before any `_write_goal_data`), and each gets its own Success Criteria row. Task 1 still points at Test Impact, which now names them. | Add the two bullets to the `tests/unit/test_job_model.py` Test Impact line verbatim from Failure Path Test Strategy. The missing-id test asserts `job.block_expectation("nonexistent-eid", code="needs_human", by="lane", detail="") is False` and that the stored `goal` bytes are byte-identical before and after (same idiom as the Race 3 no-write assertion already specified). The empty-code test asserts `block_expectation(eid, code="", by="lane", detail="")` raises `ValueError` before any `_write_goal_data` call — distinct from the already-listed "unknown code" test, which uses a non-empty string absent from `BLOCKED_REASONS`. |
