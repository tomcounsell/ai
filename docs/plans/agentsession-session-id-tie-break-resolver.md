---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3091
last_comment_id: 5695855298
---

# One resolver owns the eng-preference tie-break for every session_id read

## Problem

`AgentSession.session_id` is a plain `Field()`; the primary key is the `AutoKeyField` `id`
(`models/agent_session.py:156-157`). Two `ensure` calls for one logical session therefore leave two
rows sharing a `session_id`, and SDLC lanes make that deterministic because the id is
`f"sdlc-local-{issue_number}"` (`tools/sdlc_session_ensure.py:900`).

Stage one of #3091 already shipped (`3c77e1eab`, 2026-09-05): `rows_for_session_id()` and
`newest_for_session_id()` fixed the *ordering* of those duplicate rows, and ~74 single-row reads
were routed through them. That killed the coin flip.

It did not kill the second half of the problem. Four call sites do not want "the newest row" — they
want "the eng-typed row, which owns `stage_states`, and the newest only as a fallback". None of them
can express that through the resolver, so each one re-implements it by hand:

```python
sessions = AgentSession.rows_for_session_id(session_id)
if sessions:
    for s in sessions:
        if getattr(s, "session_type", None) == "eng":
            return s
    return sessions[0]
```

That block appears verbatim, modulo variable names, at:

| Site | Context |
|------|---------|
| `tools/sdlc_stage_query.py:89-95` | `_find_session_by_id`, inside the class-set retry loop |
| `tools/_sdlc_utils.py:357-368` | `find_session_by_issue`, deterministic-id pass (pre-narrowed list) |
| `tools/_sdlc_utils.py:467-471` | `find_session`, Step 1 — explicit `session_id` argument |
| `tools/_sdlc_utils.py:492-496` | `find_session`, Step 3 — env-var `VALOR_SESSION_ID` fallback |
| `tools/stage_states_helpers.py:103-110` | `_reload_session`, re-read before a `stage_states` write |

**Current behavior:** the selection rule for "which row owns this lane's stage state" is a
replicated value living in five places. A new call site is correct only if its author remembers to
copy the block; a change to the rule (say, preferring a non-terminal eng row) has to be made five
times, and the failure mode when it is not is silent — a stage write lands on the wrong row of a
duplicate pair and the pipeline reads back a stale stage.

This is precisely the acceptance test #3091 wrote for itself: **no caller has to remember a
tie-break to be correct.** It is the last surviving instance of that shape.

**Desired outcome:** one shared resolver on `AgentSession` expresses the eng preference. The four
sites call it. A grep sweep proves nothing re-implements it.

## Freshness Check

**Baseline commit:** `23964450c983789ba0c471a849983fc98e474baf`
**Issue filed at:** 2026-09-03T06:44:22Z
**Disposition:** Major drift — scope revised by the lane owner, not closed. See Notes.

**File:line references re-verified:**
- `models/agent_session.py:156-157` — `session_id = Field()`, `id = AutoKeyField()` — **still holds**,
  exact lines unchanged.
- `models/agent_session.py:132` — `superseded` status documented as "Replaced by a newer session for
  the same session_id" — **still holds**, still has no writer.
- `models/agent_session.py:1991-1992` — the issue cited this as the docstring asserting
  `query.filter(session_id=...)` is the correct idiom. **Drifted**: that text is gone; the line now
  sits inside `create_local()`. The claim it supported is now carried by the
  `rows_for_session_id` docstring at `models/agent_session.py:1282-1302`, which supersedes it.
- `tools/sdlc_session_ensure.py:900` — `local_session_id = f"sdlc-local-{issue_number}"` — **still
  holds**, exact line unchanged.
- `tools/sdlc_stage_query.py:89-95` — the eng tie-break — **still holds**, now at `:87-95` with the
  raw filter replaced by `rows_for_session_id` but the tie-break block intact.

**Cited sibling issues/PRs re-checked:**
- **#3065** ("SDLC control plane routes on asserted facts") — **still open**. Its plan
  `docs/plans/sdlc-control-plane-asserted-facts.md` is at `status: Planning`,
  `revision_applied: true`. Named as a pre-requisite by the issue, but only for *time pressure*
  reasons — it makes the ensure path correct under duplicates. This plan touches neither the ensure
  path nor the lock, so it is not a hard blocker. Treated as non-blocking.
- **#3169** ("Move AgentSession identity into the key") — **opened 2026-09-05, still open**. Carries
  stage two (the KeyField migration). Explicitly out of scope here; see No-Gos.

**Commits on main since issue was filed (touching referenced files):**
- `3c77e1eab` "AgentSession: one newest-wins resolver for every session_id read" (2026-09-05,
  `Refs #3091`) — **partially addresses**. Shipped the ordering resolver and routed ~74 reads
  through it. Left the eng-preference tie-break replicated at the four sites this plan closes.
- `083c961ab` "Tests: wire the remaining mocked AgentSession classes through the resolver"
  (2026-09-05, `Refs #3091`) — **partially addresses**. Extended
  `tests/unit/session_lookup_mock.py::wire_session_lookup` to six more mocked test files. This is
  the seam this plan must extend again.
- `5af3ced91` "AgentSession quarantine counter: count rows, not shim invocations" (#3212) —
  irrelevant to selection.
- `f65e05dfc` "AgentSession Meta.ttl keepalive is the retention policy" — irrelevant.
- `b7cf2558f` (#3284), `5b994db3f` (#3224), `09bd4f48f` (#3219) — irrelevant to `session_id`
  selection; touched the files for unrelated reasons.

**Active plans in `docs/plans/` overlapping this area:**
- `sdlc-control-plane-asserted-facts.md` (#3065) — touches `tools/sdlc_session_ensure.py`, which
  this plan does **not** modify. No file collision.
- No plan exists for #3169. If one is written while this lane is open it will touch
  `models/agent_session.py`; coordinate then, not now.

**Notes:** The Phase 0.5 gate initially returned Major drift with a recommendation to close #3091 as
superseded by #3169 (see issue comment 5695855298). The lane owner overrode that disposition,
independently verified the same ground truth, and split the remaining work: the KeyField migration
stays in #3169, and the eng-preference tie-break — #3091's own stated acceptance test — is this
lane's. This plan is written against that revised premise. The corrected line reference for the
issue's `:1991-1992` citation is `models/agent_session.py:1282-1302`.

## Prior Art

- **Commit `3c77e1eab`** (`Refs #3091`): introduced `rows_for_session_id()` /
  `newest_for_session_id()` and routed ~74 single-row reads through them, deleting per-site
  `created_at` sort copies. **Succeeded** at its stated scope (ordering). Deliberately did not
  collapse the eng preference — the commit message says sites "with a domain preference (an
  eng-typed row owns stage_states) iterate `rows_for_session_id` and fall back to `[0]`". That
  deliberate carve-out is exactly this plan's scope. Directly relevant: this plan is its second half,
  and must not disturb its ordering contract.
- **Commit `083c961ab`** (`Refs #3091`): wired six more mocked test classes through
  `wire_session_lookup`. **Succeeded**. Relevant as a warning: any new resolver method must be added
  to `wire_session_lookup` in the same commit, or every mocked test silently gets a bare `MagicMock`
  back instead of a row.
- **Issue #3065** (open): makes the `session-ensure` path correct *in the presence of* duplicates via
  readback-by-primary-key and candidate provenance. Complementary, not overlapping — it hardens a
  writer, this plan consolidates readers.
- **Issue #3169** (open): stage two, prevention via `KeyField`. Explicitly deferred.
- **Issue #1720 / #2550**: the bounded class-set retry (`tools/class_set_retry.py`) wrapping
  `_find_session_by_id`. Relevant because the new resolver must sit *inside* that retry loop, not
  replace it — the retry answers a different question (transient empty class set during
  `rebuild_indexes()`).

**No prior attempt has failed.** Both prior commits succeeded at their declared scope; this is
continuation, not repair. The `## Why Previous Fixes Failed` section is therefore omitted.

## Research

No relevant external findings — proceeding with codebase context. This is a purely internal
refactor: no new libraries, no external APIs, no ecosystem patterns. Phase 0.7 skipped per the
skill's stated skip condition.

## Data Flow

The selection this plan consolidates sits on the read side of every SDLC stage operation.

1. **Entry point**: an `sdlc-tool` subcommand (`stage-query`, `stage-marker`, `verdict record`,
   `dispatch record`, `next-skill`) is invoked with `--issue-number N` or an ambient
   `VALOR_SESSION_ID`.
2. **`tools/_sdlc_utils.py::find_session`**: resolves the owning session. Step 1 takes an explicit
   `session_id`; Step 2 delegates to `find_session_by_issue`; Step 3 falls back to the env var.
   Steps 1 and 3 each apply the eng tie-break inline.
3. **`tools/_sdlc_utils.py::find_session_by_issue`**: first scans all eng sessions for an
   `issue_url` match (a different lookup, not a `session_id` tie-break), then falls back to the
   deterministic `sdlc-local-{N}` id — applying the eng tie-break to a list it has already narrowed
   by `session_id` identity and by terminal-status exclusion.
4. **`tools/sdlc_stage_query.py::_find_session_by_id`**: independent path used by the read-only
   stage query, wrapped in the class-set retry budget, applying the same tie-break.
5. **`tools/stage_states_helpers.py::_reload_session`**: given an already-held session object,
   re-reads the freshest row for its `session_id` before a `stage_states` mutation — applying the
   same tie-break so the write lands on the row that owns stage state.
6. **Output**: one `AgentSession` row, whose `stage_states` the caller reads or mutates.

Every one of steps 2-5 answers the identical question — *which row for this `session_id` owns the
lane's stage state?* — and each answers it with its own copy of the rule. Consolidating the answer
is the whole of this plan. The flow itself does not change; only the number of places the rule lives
changes, from five to one.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `AgentSession.newest_for_session_id()` gains a keyword-only `prefer_type`
  parameter (defaulting to `None`, preserving every existing caller byte-for-byte). One new
  classmethod is added for the pre-narrowed case. `tests/unit/session_lookup_mock.py::wire_session_lookup`
  gains matching support. No signature anywhere else changes.
- **Coupling**: strictly decreases. Five copies of a selection rule collapse to one; four modules
  stop knowing that `"eng"` is a magic `session_type` value for stage-state ownership.
- **Data ownership**: unchanged. No schema change, no new `Field`, no stored data touched — so **no
  Popoto migration is required** (the repo's migration rule in `docs/sdlc/do-plan.md` is triggered by
  model *schema* changes; adding classmethods is not one).
- **Reversibility**: high. The change is additive on the model and mechanical at the call sites; a
  revert is a clean `git revert` with no data implications.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope was settled by the lane owner before planning; see Freshness Check notes)
- Review rounds: 1

This is a five-site consolidation with one new model method and a mechanical test-seam update. The
bounded risk is entirely in the mocked-test seam, which has a known, already-exercised pattern.

## Prerequisites

No prerequisites — this work has no external dependencies. It touches no secrets, no external APIs,
and no services. Redis must be reachable for the real-Redis tests, which is already true of every
test run in this repo.

## Solution

### Key Elements

- **`AgentSession.prefer_session_type(rows, session_type)`**: the primitive. Given rows already
  ordered newest-first, return the first row whose `session_type` matches, else the first row, else
  `None`. This is the one place the eng-preference rule lives.
- **`AgentSession.newest_for_session_id(session_id, *, prefer_type=None, **filters)`**: the
  convenience form for the three sites that do no pre-narrowing. Delegates to
  `prefer_session_type` when `prefer_type` is given; otherwise behaves exactly as today.
- **`wire_session_lookup` extension**: the mocked-test seam derives both methods — including the new
  `prefer_type` path — from the mock's own `query.filter`, so mocked tests keep their meaning.
- **A grep sweep as an acceptance criterion**: a `Verification` row proving zero remaining
  hand-rolled tie-breaks, so this defect class closes on a sweep rather than on an enumerated list
  of sites.

### Flow

Caller needs a session → `AgentSession.newest_for_session_id(sid, prefer_type="eng")` → one row
(eng-typed if any exists, newest otherwise) → caller proceeds.

Caller has already narrowed a row list (identity re-check, terminal-status exclusion) →
`AgentSession.prefer_session_type(rows, "eng")` → one row → caller proceeds.

### Technical Approach

**Decision: accept-and-encode, not prevent.** #3091 asked for an explicit choice among prevent (1),
reconcile (2), and accept-and-encode (3). This lane chooses **(3), completing it**, for these
reasons:

- Option 1 (`session_id` as a `KeyField`) changes the Redis hash key of every `AgentSession` row and
  requires a fleet-wide, idempotent data migration plus SQLite-archive round-trip. That is a
  different blast radius and already has its own issue (#3169) with a fuller specification than
  #3091 carries.
- Option 2 (reconcile at read time or by sweep) would give `superseded` its first writer, but a
  read-time reconciliation performs writes on a read path — a worse trade than ordering — and a
  sweep is a background job whose absence of a running instance is itself a failure mode. #3169
  folds reconciliation into the migration where it belongs, as a one-time step before uniqueness can
  hold.
- Option 3 is already 74/78ths done. Finishing it is small, reversible, and independently valuable:
  it holds the invariant in one place, which is what #3169 will later replace with a stronger
  guarantee. When #3169 lands, `prefer_session_type` becomes a one-element no-op and can be deleted
  in that lane — it does not become dead weight that must be migrated.

**Two methods, one rule.** `prefer_session_type` is the rule; `newest_for_session_id(prefer_type=)`
is sugar over it. This is not a duplicated invariant — the second is implemented by calling the
first — and both are needed because `find_session_by_issue`'s deterministic-id pass narrows its list
in ways `query.filter` cannot express (`include_terminal` is a *negative* status filter, excluding
membership in `_TERMINAL_ISSUE_LOOKUP_STATUSES`, which Popoto's equality filter cannot represent).
Forcing that site through the keyword form would mean either duplicating the negative filter into
the model or dropping the narrowing — both worse than exposing the primitive.

**Ordering contract is untouched.** `prefer_session_type` operates on an already-sorted list and
never re-sorts. `rows_for_session_id`'s newest-first guarantee (`created_at` desc as UTC epoch, then
`id` desc; missing `created_at` sorts oldest) remains the sole ordering authority, and its tests in
`tests/unit/test_agent_session_newest_wins.py` remain the pin.

**The class-set retry stays where it is.** In `sdlc_stage_query.py::_find_session_by_id` the new call
goes *inside* the `class_set_retry_attempts()` loop, preserving the #1720/#2550 behavior and the
`log_class_set_exhaustion` call on budget exhaustion. The retry answers "is the class set transiently
empty?"; the resolver answers "which row?". Collapsing them would regress the retry.

**Decision on the two raw bridge filters.** `bridge/telegram_bridge.py:2249` and `:2255` (line
numbers current as of the baseline commit; they sit in the pending-merge coalescing guard) are still
raw `AgentSession.query.filter(session_id=guard_session_id)`. **They will be routed through
`rows_for_session_id()`.** Reasoning, stated because the owner asked for it explicitly:

- They are genuine per-`session_id` reads, not aggregates. They are used only for truthiness today,
  but "presence check" is a property of the current caller, not of the query — the next edit that
  wants the row itself would reintroduce a raw `[0]`.
- The cost is one sort of a ≤2-element list on a cold path that already contains a deliberate
  `asyncio.sleep(0.2)` retry. It is not measurable.
- The benefit is that `bridge/` reaches zero raw `session_id` filters, shrinking the "deliberately
  left raw" exclusion set to three sites in `agent/` that are self-evidently aggregate (a `.count()`
  at `agent/agent_session_queue.py:385`, an all-terminal-rows comprehension at `:1645`, an
  all-pending comprehension at `:1716`) plus one diagnostic count at
  `agent/session_completion.py:206` and one any-row scan at `.claude/hooks/sdlc/sdlc_context.py:318`.
  An exclusion set that is describable by a property ("consumes every row") rather than by an
  enumerated list is one a future sweep can re-derive.

The counter-argument — that routing a pure presence check through a sorting resolver is ceremony —
is real, and this is the cheapest possible point at which to overturn the call. It is flagged for
critique rather than buried.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Each of the four tie-break sites sits inside a `try/except Exception` that logs at `debug` and
      returns `None` or the original session. These handlers are **preserved as-is**; the change is
      inside the `try`. Add a test asserting `_reload_session` returns the *original* session object
      (not `None`) when the resolver raises, since that fallback is load-bearing for the
      `stage_states` write path.
- [ ] Assert `sdlc_stage_query._find_session_by_id` still calls `log_class_set_exhaustion` when the
      retry budget is exhausted — the observable behavior that distinguishes "genuinely absent" from
      "transiently empty class set".
- [ ] No `except Exception: pass` blocks are introduced.

### Empty/Invalid Input Handling
- [ ] `prefer_session_type([])` returns `None`. Test explicitly.
- [ ] `prefer_session_type(rows, None)` — a `None`/empty `prefer_type` must degrade to "newest",
      never to "no match, return None". Test explicitly; this is the most likely silent-wrong-answer
      bug in the change.
- [ ] `newest_for_session_id(unknown_id, prefer_type="eng")` returns `None`, not an exception.
- [ ] Rows whose `session_type` attribute is absent entirely (`getattr(..., None)`) must not match
      and must not raise. The existing sites use `getattr` with a default for exactly this reason;
      the primitive must preserve it. Test with a row missing the attribute.

### Error State Rendering
- [ ] No user-visible output changes. The failure surface is an SDLC stage read returning `None`,
      which existing callers already render as "session not found". Verified by the preserved
      `log_class_set_exhaustion` WARNING path above.

## Test Impact

- [ ] `tests/unit/session_lookup_mock.py::wire_session_lookup` — UPDATE: derive the `prefer_type`
      keyword and the new `prefer_session_type` classmethod from the mock's `query.filter`, matching
      the real implementation. **This is the highest-risk edit in the plan**: if it is missed, every
      mocked test gets a bare `MagicMock` from the new code path and asserts nothing while appearing
      green. Must land in the same commit as the model change.
- [ ] `tests/unit/test_agent_session_newest_wins.py` — UPDATE: add real-Redis cases for
      `prefer_session_type` and `newest_for_session_id(prefer_type=...)`. Seed an eng row that is
      *older* than a non-eng row so preference and recency disagree, proving preference wins; seed
      two eng rows to prove newest-eng wins; seed zero eng rows to prove the newest fallback.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: the eng-preference assertions must now be
      satisfied through the resolver; verify the class-set retry and exhaustion logging survive.
- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: covers `find_session` Steps 1/3 and
      `find_session_by_issue`'s deterministic-id pass. Assert the `include_terminal` narrowing still
      happens *before* preference is applied — the ordering of narrow-then-prefer is a behavior this
      refactor could silently invert.
- [ ] `tests/unit/test_stall_detection.py`, `tests/unit/test_agent_session_queue.py`,
      `tests/unit/test_health_check.py`, `tests/unit/test_poll_gating.py`,
      `tests/unit/test_sdk_client.py`, `tests/unit/test_valor_session_kill.py`,
      `tests/unit/test_valor_session_resume_release.py`, `tests/unit/test_bridge_relay.py`,
      `tests/unit/test_telegram_relay_chat_log.py`, `tests/unit/test_agent_session_hierarchy.py`,
      `tests/unit/test_sdlc_env_vars.py`, `tests/unit/sdlc_session_ensure/*` — UPDATE only if the
      `wire_session_lookup` change alters their behavior. Expectation is no edit needed (that is the
      point of the shared seam), but each must be **run** as evidence, not assumed.
- [ ] `tests/unit/test_steering_writer_census.py` — UPDATE if the census counts resolver-bound names:
      `3c77e1eab` already taught it to recognize a name bound to the resolver as a newest-first
      selection. A new resolver method may need registering there. Verify.
- [ ] No test is DELETEd or REPLACEd. No `xfail` markers exist for this bug — searched
      `tests/` for `pytest.mark.xfail` and runtime `pytest.xfail(` related to session_id selection;
      none found, so there are no expected-failure markers to convert.

## Rabbit Holes

- **Making `session_id` a `KeyField` "while we're in here."** That is #3169 in full: a fleet-wide
  Redis rewrite, archive round-trip, and duplicate reconciliation. It will look like a two-line
  model change and is not. Do not start it.
- **Giving `superseded` a writer.** Tempting because the model documents the status and nothing
  writes it, and because "reconcile" was option 2 of the issue's own menu. It belongs to #3169's
  reconciliation step, where the winner rule is applied once rather than on every read.
- **Rewriting `find_session_by_issue`'s eng-session `issue_url` scan** (`tools/_sdlc_utils.py:339`).
  It is a linear scan over all eng sessions and it looks like the same smell, but it answers a
  different question (which session owns this *issue URL*) and is not a `session_id` tie-break. It
  carries its own scale caveat in a comment. Leave it; its presence in grep output is why the sweep
  pattern must be precise.
- **Collapsing the class-set retry into the resolver.** They answer different questions. Merging them
  regresses #1720/#2550.
- **Auditing all ~74 sites that `3c77e1eab` already routed.** They are done. The sweep proves it in
  one command; re-reading them one by one is the enumerated-list failure mode this plan exists to
  avoid.

## Risks

### Risk 1: The mocked-test seam silently disarms assertions
**Impact:** If `wire_session_lookup` is not extended for `prefer_type`, mocked tests that exercise
the new path receive a bare `MagicMock` — which is truthy and has every attribute — so assertions
pass vacuously. The suite goes green while the four call sites are untested. This is the exact
failure mode `083c961ab` was written to prevent, which is evidence it is a live risk and not
theoretical.
**Mitigation:** The `wire_session_lookup` update lands in the same commit as the model change, and a
dedicated test asserts that a wired mock returns a *seeded row object*, not a `MagicMock`, from
`newest_for_session_id(..., prefer_type="eng")`. Prove that test RED against the unextended seam
before landing the fix.

### Risk 2: Narrow-then-prefer order inverts at the deterministic-id pass
**Impact:** `find_session_by_issue` narrows by `session_id` identity re-check and by terminal-status
exclusion *before* applying preference. If the refactor routes it through the keyword form instead,
preference would be applied to the unnarrowed list and could return a terminal eng row that the
current code correctly skips — resurrecting a dead lane's session as the live one.
**Mitigation:** That site uses the `prefer_session_type` primitive on its already-narrowed list, by
design (see Technical Approach). A test asserts a terminal eng row loses to a live non-eng row when
`include_terminal=False`.

### Risk 3: The sweep pattern is too loose or too tight
**Impact:** Too loose and it flags `_sdlc_utils.py:339`'s legitimate `issue_url` scan forever, so the
check gets disabled. Too tight and a reformatted copy of the tie-break slips past it, and the defect
class reopens silently.
**Mitigation:** The sweep targets the selection idiom, not the string `"eng"`: rows-then-compare
inside a loop over a resolver result. Prove the pattern RED by reintroducing one deleted tie-break
block on a scratch copy before landing, and paste that RED output into the PR. A guard certifying
absence is worthless until proven red against the known-bad input.

### Risk 4: Behavior change at sites where no eng row exists
**Impact:** Today three sites fall back to `sessions[0]`, which since `3c77e1eab` is the newest row.
The new resolver must reproduce that exactly. Any divergence silently re-points stage reads.
**Mitigation:** `prefer_session_type` returns `rows[0]` on no match, by construction, and a test
asserts the no-eng-row case returns the same row `newest_for_session_id` would.

## Race Conditions

### Race 1: Duplicate row created between read and stage-state write
**Location:** `tools/stage_states_helpers.py:103-110` (`_reload_session`), called before a
`stage_states` mutation.
**Trigger:** A concurrent `session-ensure` mints a second row for the same `session_id` between the
reload and the write.
**Data prerequisite:** The row selected by the reload must still be the row the subsequent write
targets.
**State prerequisite:** Preference must be stable across calls — two reloads in the same window must
not disagree.
**Mitigation:** Not newly introduced by this change, and not newly fixed by it; the hazard is
inherent to duplicates and is what #3169 removes. This plan makes selection *deterministic given a
row set*, which is strictly better than today only in that the rule is now singular. A test asserts
repeated `newest_for_session_id(..., prefer_type="eng")` calls over an unchanged row set return the
identical row — the same stability property `test_agent_session_newest_wins.py` already pins for
ordering.

### Race 2: Transiently empty class set during `rebuild_indexes()`
**Location:** `tools/sdlc_stage_query.py:87-95`, inside `class_set_retry_attempts()`.
**Trigger:** Popoto's `rebuild_indexes()` empties `$Class:AgentSession` while a filter runs.
**Data prerequisite:** The class set must be populated for the filter to return the live row.
**State prerequisite:** None beyond the retry budget.
**Mitigation:** Existing and preserved — the resolver call stays inside the retry loop and
`log_class_set_exhaustion` still fires on budget exhaustion. Explicitly asserted in Test Impact.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3169] Making `session_id` part of the Popoto key so one `session_id` maps to one
  row. Carries a fleet-wide Redis data migration, SQLite-archive round-trip, and duplicate
  reconciliation. Out of proportion for this lane.
- [SEPARATE-SLUG #3169] Giving the `superseded` status a writer, and reconciling existing duplicate
  pairs. Belongs with the migration that makes uniqueness hold.
- [SEPARATE-SLUG #3065] Hardening the `session-ensure` write path against duplicates (readback by
  primary key, candidate provenance). Already planned there; this lane does not touch
  `tools/sdlc_session_ensure.py`.
- [SEPARATE-SLUG #3169] Replacing the `issue_url` linear scan at `tools/_sdlc_utils.py:339` with an
  indexed lookup. A scale concern, not a correctness one, and it is not a `session_id` tie-break.

## Update System

No update system changes required — this feature is purely internal. No new dependencies, no config
files, no `pyproject.toml` changes, and **no Popoto migration**: the change adds classmethods to
`AgentSession` and does not alter any `Field`, `KeyField`, or stored key shape, so
`scripts/update/migrations.py` is untouched. Existing installations pick the change up through an
ordinary `/update` (git pull + service restart), and running old and new code against the same Redis
is safe because both select from the same row set by the same rule.

## Agent Integration

No agent integration required — this is an internal refactor of existing SDLC tooling. No new CLI
entry point is needed in `pyproject.toml [project.scripts]`; the affected code is reached through
`sdlc-tool` subcommands that already exist. The bridge imports nothing new. The agent-observable
behavior of `sdlc-tool stage-query` and the stage markers is unchanged by design — that invariance is
itself asserted by the existing tests in `tests/unit/test_sdlc_stage_query.py`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-model.md` — it already documents the newest-wins resolver
      (added by `3c77e1eab`). Extend that section to describe the eng-preference resolver, state that
      it is the single owner of the rule, and record that stage-state ownership is why `"eng"` is
      preferred.
- [ ] Update `docs/features/sdlc-lane-identity.md` if it describes how a lane's session row is
      located; point it at the shared resolver rather than at any call site.
- [ ] No new `docs/features/README.md` index entry — this extends an existing documented feature
      rather than adding one.

### External Documentation Site
- [ ] Not applicable — this repo has no external documentation site.

### Inline Documentation
- [ ] Docstring on `prefer_session_type` stating the rule, why `"eng"` wins (it owns `stage_states`),
      that it assumes a newest-first input list and never re-sorts, and that it becomes a one-element
      no-op once #3169 lands.
- [ ] Docstring update on `newest_for_session_id` covering `prefer_type`.
- [ ] Update the `rows_for_session_id` docstring: it currently instructs callers with domain
      preferences to "iterate this list and fall back to `[0]`". That instruction becomes wrong the
      moment this lands and must be replaced with a pointer to the resolver — leaving it would
      actively re-teach the defect.

### No INFRA doc
No `docs/infra/` doc — this plan introduces no dependencies, services, external API calls, or
deployment changes.

## Success Criteria

- [ ] `AgentSession` exposes one resolver that expresses the eng preference; the rule appears in
      exactly one place in the codebase.
- [ ] All four sites (`tools/sdlc_stage_query.py`, `tools/_sdlc_utils.py` ×3,
      `tools/stage_states_helpers.py`) call it; no hand-rolled tie-break remains.
- [ ] **A grep sweep returns zero hand-rolled tie-breaks** — the acceptance mechanism is the sweep,
      not a checklist of the sites above. The sweep pattern is proven RED against a reintroduced
      tie-break before it is trusted.
- [ ] `bridge/telegram_bridge.py:2249,2255` route through `rows_for_session_id()`; `bridge/` has zero
      raw `query.filter(session_id=` reads.
- [ ] `tests/unit/session_lookup_mock.py::wire_session_lookup` covers the new path, proven by a test
      that is RED against the unextended seam.
- [ ] Behavior at every migrated site is unchanged when an eng row exists, when none exists, and when
      the row list is empty.
- [ ] The class-set retry and its exhaustion logging survive in `sdlc_stage_query.py`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`), including the corrected `rows_for_session_id` docstring
- [ ] No related xfail/xpass tests exist to convert (verified: none found)

## Team Orchestration

### Team Members

- **Builder (resolver + call sites)**
  - Name: `resolver-builder`
  - Role: Add the resolver to `AgentSession`, extend `wire_session_lookup`, migrate all four call
    sites plus the two bridge presence checks.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Test engineer (resolver coverage)**
  - Name: `resolver-tests`
  - Role: Extend `test_agent_session_newest_wins.py` with preference cases against real Redis; prove
    the mocked-seam test RED before the seam is extended; add the failure-path cases.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (sweep + behavior parity)**
  - Name: `resolver-validator`
  - Role: Run the grep sweep, verify its RED proof, confirm behavior parity at each migrated site,
    run the full affected-test set.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `resolver-docs`
  - Role: Update `docs/features/agent-session-model.md` and the three docstrings, especially the
    now-wrong `rows_for_session_id` guidance.
  - Agent Type: documentarian
  - Resume: true

### Domain framing for `resolver-builder`

Paste the Redis/Popoto rules from `DOMAIN_FRAMING.md` into the assignment. Load-bearing points for
this task: never write raw Redis ops; `Field(default=False)` stores `"True"`/`"False"` strings (not
relevant here but a standing trap); this change adds **no** schema field and therefore needs **no**
entry in `scripts/update/migrations.py`.

## Step by Step Tasks

### 1. Prove the sweep RED
- **Task ID**: build-sweep-red
- **Depends On**: none
- **Validates**: no test file; produces the RED output pasted into the PR description
- **Assigned To**: `resolver-tests`
- **Agent Type**: test-engineer
- **Parallel**: true
- Write the grep sweep command that detects a hand-rolled eng tie-break over a resolver result.
- Run it against current `main` — it must report **5** hits (the four sites, one of which is the
  `_sdlc_utils` triple, counted as its three distinct blocks plus `stage_states_helpers`).
- Confirm it does **not** flag `tools/_sdlc_utils.py:339`'s `issue_url` eng scan.
- Save the RED output verbatim for the PR description.

### 2. Add the resolver to `AgentSession`
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_newest_wins.py
- **Informed By**: Technical Approach (two methods, one rule; ordering contract untouched)
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `AgentSession.prefer_session_type(rows, session_type)`: first matching row, else `rows[0]`,
  else `None`. Uses `getattr(row, "session_type", None)`. Never re-sorts.
- Add keyword-only `prefer_type=None` to `newest_for_session_id`, delegating to
  `prefer_session_type` when set. Default path byte-for-byte unchanged.
- Write both docstrings per the Documentation section.
- Correct the `rows_for_session_id` docstring, which currently tells callers to hand-roll the
  tie-break.

### 3. Extend the mocked-test seam
- **Task ID**: build-mock-seam
- **Depends On**: build-resolver
- **Validates**: tests/unit/session_lookup_mock.py
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- Extend `wire_session_lookup` so `newest_for_session_id(..., prefer_type=...)` and
  `prefer_session_type` derive from the mock's own `query.filter` with the real preference rule.
- Land this in the same commit as task 2 — a mocked seam that lags the model silently disarms
  assertions.

### 4. Migrate the four tie-break sites
- **Task ID**: build-call-sites
- **Depends On**: build-mock-seam
- **Validates**: tests/unit/test_sdlc_stage_query.py, tests/unit/test_sdlc_utils.py
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- `tools/sdlc_stage_query.py:87-95` → `newest_for_session_id(session_id, prefer_type="eng")`, kept
  **inside** the `class_set_retry_attempts()` loop, with `log_class_set_exhaustion` preserved.
- `tools/_sdlc_utils.py:467-471` and `:492-496` → the keyword form.
- `tools/_sdlc_utils.py:357-368` → `prefer_session_type(local)` applied to the already-narrowed list,
  preserving the identity re-check and the `include_terminal` exclusion **before** preference.
- `tools/stage_states_helpers.py:103-110` → the keyword form.
- Delete every hand-rolled block. No commented-out remnants.

### 5. Route the two bridge presence checks
- **Task ID**: build-bridge-filters
- **Depends On**: build-call-sites
- **Validates**: tests/unit/test_bridge_relay.py
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- `bridge/telegram_bridge.py:2249,2255` → `AgentSession.rows_for_session_id(guard_session_id)`,
  preserving the Race-2 sleep-and-retry shape exactly.

### 6. Resolver and failure-path tests
- **Task ID**: build-tests
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_agent_session_newest_wins.py
- **Assigned To**: `resolver-tests`
- **Agent Type**: test-engineer
- **Parallel**: false
- Real-Redis cases: older eng row beats newer non-eng row; newest eng wins among two eng rows; no eng
  row falls back to the newest; empty list → `None`; `prefer_type=None` degrades to newest; a row
  missing `session_type` neither matches nor raises; repeated calls are stable.
- Terminal-eng-vs-live-non-eng case for the `include_terminal` narrowing (Risk 2).
- `_reload_session` returns the original session when the resolver raises.
- The mocked-seam test that is RED before task 3.

### 7. Validate
- **Task ID**: validate-all
- **Depends On**: build-sweep-red, build-tests, build-bridge-filters
- **Assigned To**: `resolver-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run the task-1 sweep: must now report zero.
- Run every test file named in Test Impact via `scripts/pytest-clean.sh` and record counts as
  evidence, not assumption.
- Confirm behavior parity at each migrated site.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `resolver-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/agent-session-model.md` and `docs/features/sdlc-lane-identity.md` per the
  Documentation section.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_agent_session_newest_wins.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_utils.py tests/unit/test_bridge_relay.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No hand-rolled eng tie-break (sweep) | `grep -rn 'session_type", None) == "eng"' agent/ bridge/ tools/ models/ reflections/ ui/ .claude/hooks/ \| grep -v 'def prefer_session_type' \| wc -l` | output contains 0 |
| Resolver exists | `grep -c 'def prefer_session_type' models/agent_session.py` | output contains 1 |
| All four sites use the resolver | `grep -rn 'prefer_type="eng"\|prefer_session_type(' tools/sdlc_stage_query.py tools/_sdlc_utils.py tools/stage_states_helpers.py \| wc -l` | output contains 4 |
| Mocked seam covers prefer_type | `grep -c 'prefer_type' tests/unit/session_lookup_mock.py` | output > 0 |
| No raw session_id filter in bridge | `grep -rn 'query.filter(session_id=' bridge/ --include=*.py \| grep -v '^bridge/telegram_relay.py:89[89]:' \| wc -l` | output contains 0 |
| Class-set retry preserved | `grep -c 'log_class_set_exhaustion' tools/sdlc_stage_query.py` | output > 0 |
| No Popoto migration added | `git diff --name-only main -- scripts/update/migrations.py \| wc -l` | output contains 0 |
| rows_for_session_id docstring no longer teaches the tie-break | `sed -n '1282,1305p' models/agent_session.py \| grep -c 'fall back to' ` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

1. **Should the two `bridge/telegram_bridge.py` presence checks be routed through the resolver?**
   The plan decides **yes** (Technical Approach, "Decision on the two raw bridge filters") for sweep
   cleanliness, at the cost of a needless sort on a cold path. The counter-argument — that a pure
   presence check gains nothing from an ordering resolver — is genuine. This is the cheapest moment
   to overturn the call; critique should rule.
2. **Is `prefer_session_type` the right shape, or should the preference be a predicate?** A
   `prefer=lambda s: ...` would generalize beyond `session_type`, but nothing else needs it today and
   a predicate makes the sweep pattern harder to write. Plan chooses the concrete form.
3. **Does `tests/unit/test_steering_writer_census.py` need the new method registered?** `3c77e1eab`
   taught it to recognize resolver-bound names. Flagged as "verify" in Test Impact rather than
   assumed either way; if it does, that is a one-line addition, not a scope change.
