---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3091
last_comment_id: 5696048394
revision_applied: true
revision_applied_at: 2026-09-16T10:58:52Z
---

# One ordering owns the eng preference for every session_id read

## Problem

`AgentSession.session_id` is a plain `Field()`; the primary key is the `AutoKeyField` `id`
(`models/agent_session.py:163-165`). Two `ensure` calls for one logical session therefore leave two
rows sharing a `session_id`, and SDLC lanes make that deterministic because the id is
`f"sdlc-local-{issue_number}"` (`tools/sdlc_session_ensure.py:900`).

Stage one of #3091 already shipped (`3c77e1eab`, 2026-09-05): `rows_for_session_id()` and
`newest_for_session_id()` fixed the *ordering* of those duplicate rows, and ~74 single-row reads were
routed through them. That killed the coin flip.

It did not kill the second half. Six call sites do not want "the newest row" — they want "the
eng-typed row, which owns `stage_states` and `active_run_id`, and the newest only as a fallback".
The resolver cannot express that, so each site re-implements it by hand:

| Site | Context | Shape |
|------|---------|-------|
| `tools/sdlc_stage_query.py:93` | `_find_session_by_id`, inside the class-set retry loop | select one |
| `tools/stage_states_helpers.py:108` | `_reload_session`, re-read before a `stage_states` write | select one |
| `tools/_sdlc_utils.py:368` | `find_session_by_issue`, deterministic-id pass (pre-narrowed list) | select one |
| `tools/_sdlc_utils.py:470` | `find_session` Step 1 — explicit `session_id` argument | select one |
| `tools/_sdlc_utils.py:495` | `find_session` Step 3 — env-var `VALOR_SESSION_ID` fallback | select one |
| `agent/session_executor.py:330` | `_fetch_live_active_run_id`, issue-lock renewal on the 60s tick | **scan many** |

The first five are the same block verbatim, modulo variable names:

```python
for s in rows:
    if getattr(s, "session_type", None) == "eng":
        return s
return rows[0]
```

The sixth is the one that matters most, and it is why the fix is an *ordering* rather than a
selection. `agent/session_executor.py:328-338` fetches rows, then runs **two** full passes — an
eng-preferred pass and an any-row pass — looking for the first row with a non-empty `active_run_id`:

```python
rows = AgentSession.rows_for_session_id(sid)
# Prefer the eng-typed record (mirrors the resolution the SDLC tools use).
for row in rows:
    if getattr(row, "session_type", None) == "eng":
        rid = getattr(row, "active_run_id", None)
        if rid:
            return rid
for row in rows:
    rid = getattr(row, "active_run_id", None)
    if rid:
        return rid
return None
```

Its own comment — "mirrors the resolution the SDLC tools use" — states outright that it is a
replication. It feeds `active_run_id` resolution that the session queue depends on, so consolidating
the `tools/` sites while leaving this one hand-rolled would be a half-fix.

**Current behavior:** the rule for "which row for this `session_id` owns the lane's state" is a
replicated value living in six places. A new call site is correct only if its author remembers to
copy the block. Worse, the model *sanctions* the copying: the `rows_for_session_id` docstring
(`models/agent_session.py:1295-1298`) tells callers with a domain preference to "iterate this list
and fall back to `[0]`". The replication spread with the model's blessing.

**Desired outcome:** the eng preference becomes part of the ordering `rows_for_session_id` already
owns. All six sites iterate one correctly-ordered list. A grep sweep proves nothing re-implements
it, and the docstring stops recommending that they do.

This is exactly #3091's own acceptance test: **no caller has to remember a tie-break to be correct.**

## Freshness Check

**Baseline commit:** `23964450c983789ba0c471a849983fc98e474baf`
**Issue filed at:** 2026-09-03T06:44:22Z
**Disposition:** Major drift — scope revised by the lane owner, not closed. See Notes.

**How these were verified (tightened after critique).** The first pass of this section carried
`models/agent_session.py:156-157` for the identity fields and asserted it had been re-verified. It had
not: `156-157` is inside the class docstring's lifecycle prose, and a reader skimming that range sees
plausible-looking text about sessions, so the error reads as confirmation. A freshness check that
passes on a stale coordinate is reporting on itself, not on the file. **Every coordinate below was
re-verified by printing the exact line range with `awk 'NR>=A && NR<=B'` and reading the code at it,
not by grepping for the symbol and trusting a remembered number.** A coordinate is only "still holds"
if the printed range contains the cited construct.

**File:line references re-verified:**
- `models/agent_session.py:163-165` — `# === Identity ===` block: `id = AutoKeyField()` at 163,
  `session_id = Field()` at 164, `session_type = KeyField(null=True)` at 165 — **still holds**, at
  corrected coordinates.
- `models/agent_session.py:132` — `superseded` documented as "Replaced by a newer session for the
  same session_id" — **still holds**, still has no writer.
- `models/agent_session.py:1991-1992` — cited by the issue as the docstring asserting
  `query.filter(session_id=...)` is the correct idiom. **Drifted**: that text is gone; the line now
  sits inside `create_local()`. The claim moved to the `rows_for_session_id` docstring at
  `models/agent_session.py:1282-1302` — which this plan corrects, because it now sanctions the
  defect.
- `tools/sdlc_session_ensure.py:900` — `local_session_id = f"sdlc-local-{issue_number}"` — **still
  holds**.
- `tools/sdlc_stage_query.py:89-95` — the eng tie-break — **still holds**, now at `:87-95`, raw
  filter replaced by `rows_for_session_id` but the tie-break intact.

**Cited sibling issues/PRs re-checked:**
- **#3065** ("SDLC control plane routes on asserted facts") — **still open**; plan at
  `status: Planning`, `revision_applied: true`. Named a pre-requisite by the issue for *time
  pressure* only, and is ruled **NON-BLOCKING** — do not wait on it. Be precise about the boundary:
  this plan does not modify `tools/sdlc_session_ensure.py`, so there is no file collision with
  `docs/plans/sdlc-control-plane-asserted-facts.md`. On the lock itself: **this plan does not touch
  lock acquisition (#3065's territory); it does change the input to lock renewal.** Data Flow entry 6
  is the path — `agent/session_executor.py:330` feeds `_tick_issue_lock_renewal`. Acquisition and
  renewal are different halves of the same mechanism; still non-blocking, but a reviewer should not be
  told this plan sits nowhere near the lock.
- **#3169** ("Move AgentSession identity into the key") — **opened 2026-09-05, still open**. Carries
  stage two. Out of scope; see No-Gos. Note that this plan **ticks #3169's AC item 4** ("`sdlc_stage_query.py`,
  `_sdlc_utils.py`, and `stage_states_helpers.py` no longer need an eng-type tie-break") ahead of
  that migration, which shrinks #3169 to the migration proper.

**Commits on main since issue was filed (touching referenced files):**
- `3c77e1eab` "AgentSession: one newest-wins resolver for every session_id read" (2026-09-05,
  `Refs #3091`) — **partially addresses**. Shipped the ordering resolver, routed ~74 reads through
  it, and deliberately left the eng preference replicated at the six sites this plan closes.
- `083c961ab` "Tests: wire the remaining mocked AgentSession classes through the resolver"
  (2026-09-05, `Refs #3091`) — **partially addresses**. Extended
  `tests/unit/session_lookup_mock.py::wire_session_lookup`. That is the seam this plan must extend
  again.
- `5af3ced91` (#3212), `f65e05dfc`, `b7cf2558f` (#3284), `5b994db3f` (#3224), `09bd4f48f` (#3219) —
  irrelevant to `session_id` selection.

**Active plans in `docs/plans/` overlapping this area:**
- `sdlc-control-plane-asserted-facts.md` (#3065) — touches `tools/sdlc_session_ensure.py`, which this
  plan does **not** modify. No file collision.
- No plan exists for #3169. If one is written while this lane is open it will touch
  `models/agent_session.py`; coordinate then.

**Notes:** The Phase 0.5 gate initially returned Major drift recommending #3091 be closed as
superseded by #3169 (issue comment 5695855298). The lane owner overrode that, independently verified
the same ground truth, and split the work: the KeyField migration stays in #3169; the eng-preference
consolidation — #3091's own acceptance test — is this lane's. A first scoping of that consolidation
named four sites and a selection-shaped resolver; both were corrected before this plan was finalized,
to **six** sites and an **ordering**-shaped resolver, after `agent/session_executor.py:330` was found
to scan rather than select. This plan reflects the corrected scope. Corrected line reference for the
issue's `:1991-1992` citation: `models/agent_session.py:1282-1302`.

## Prior Art

- **Commit `3c77e1eab`** (`Refs #3091`): introduced `rows_for_session_id()` /
  `newest_for_session_id()` and routed ~74 single-row reads through them. **Succeeded** at its scope
  (ordering). Its commit message explicitly carves out sites "with a domain preference (an eng-typed
  row owns stage_states)", which "iterate `rows_for_session_id` and fall back to `[0]`". That
  deliberate carve-out is this plan's scope. Directly relevant: this plan is its second half and must
  not disturb its ordering contract.
- **Commit `083c961ab`** (`Refs #3091`): wired six more mocked test classes through
  `wire_session_lookup`. **Succeeded**. Relevant as a warning: a new resolver argument must be added
  to `wire_session_lookup` in the same commit, or every mocked test silently receives rows in the
  **wrong order** — the kwarg is forwarded and ignored, not rejected. See Risk 1.
- **Issue #3065** (open): makes the `session-ensure` *write* path correct in the presence of
  duplicates. Complementary — it hardens a writer, this plan consolidates readers.
- **Issue #3169** (open): stage two, prevention via `KeyField`. Deferred; this plan ticks its AC
  item 4.
- **Issues #1720 / #2550**: the bounded class-set retry (`tools/class_set_retry.py`) wrapping
  `_find_session_by_id`. Relevant because the ordering must sit *inside* that retry loop, not replace
  it — the retry answers a different question (transiently empty class set during
  `rebuild_indexes()`).

**No prior attempt has failed.** Both prior commits succeeded at their declared scope; this is
continuation, not repair. The `## Why Previous Fixes Failed` section is therefore omitted.

## Research

No relevant external findings — proceeding with codebase context. This is a purely internal refactor:
no new libraries, no external APIs, no ecosystem patterns. Phase 0.7 skipped per the skill's stated
skip condition.

## Data Flow

The selection this plan consolidates sits on the read side of every SDLC stage operation **and** on
the heartbeat path that renews issue locks.

1. **Entry point A — SDLC tooling**: an `sdlc-tool` subcommand (`stage-query`, `stage-marker`,
   `verdict record`, `dispatch record`, `next-skill`) is invoked with `--issue-number N` or an
   ambient `VALOR_SESSION_ID`.
2. **`tools/_sdlc_utils.py::find_session`**: Step 1 takes an explicit `session_id`; Step 2 delegates
   to `find_session_by_issue`; Step 3 falls back to the env var. Steps 1 and 3 each apply the eng
   preference inline.
3. **`tools/_sdlc_utils.py::find_session_by_issue`**: first scans all eng sessions for an `issue_url`
   match (a *different* lookup, not a `session_id` tie-break), then falls back to the deterministic
   `sdlc-local-{N}` id — applying the eng preference to a list it has already narrowed by identity
   re-check and terminal-status exclusion.
4. **`tools/sdlc_stage_query.py::_find_session_by_id`**: independent read-only path, wrapped in the
   class-set retry budget, same preference.
5. **`tools/stage_states_helpers.py::_reload_session`**: re-reads the freshest row for a held
   session's `session_id` before a `stage_states` mutation, same preference, so the write lands on the
   row that owns stage state.
6. **Entry point B — heartbeat**: `agent/session_executor.py` tier-1 (60s) tick calls the
   `active_run_id` re-fetch, which fetches rows and scans eng-first for the first non-empty
   `active_run_id`, then feeds `_tick_issue_lock_renewal`.
7. **Output**: one `AgentSession` row (paths 2-5), or one `active_run_id` string (path 6).

Every one of these answers the same underlying question — *among rows sharing this `session_id`,
which does the eng-typed one take precedence over?* — and each answers it with its own copy. Paths
2-5 want one row; path 6 wants an ordered list it can scan. Making the answer an **ordering** rather
than a **selection** serves both: the flow does not change, only the number of places the rule lives,
from six to one.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `rows_for_session_id()` and `newest_for_session_id()` each gain a
  keyword-only `prefer_type=None`. Defaulting to `None` preserves every one of the ~74 existing
  callers byte-for-byte. No signature anywhere else changes; no new method is added.
- **Coupling**: strictly decreases. Six copies of a rule collapse to one; five modules stop knowing
  that `"eng"` is a magic `session_type` value for state ownership.
- **Data ownership**: unchanged. No schema change, no new `Field`, no stored data touched — so **no
  Popoto migration is required**. The repo's migration rule (`docs/sdlc/do-plan.md`) triggers on model
  *schema* changes; adding a keyword argument to a classmethod is not one.
- **Reversibility**: high. Additive on the model, mechanical at the call sites; a clean `git revert`
  with no data implications.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope settled by the lane owner before planning; see Freshness Check notes)
- Review rounds: 1

A six-site consolidation, one keyword argument on two existing model methods, and a mechanical
test-seam update. The bounded risk is entirely in the mocked-test seam, which has a known,
already-exercised pattern.

## Prerequisites

No prerequisites — this work has no external dependencies. It touches no secrets, no external APIs,
and no services. Redis must be reachable for the real-Redis tests, which is already true of every
test run in this repo.

## Solution

### Key Elements

- **`rows_for_session_id(session_id, *, prefer_type=None, **filters)`**: when `prefer_type` is given,
  returns rows with that `session_type` first, then the rest — **each group internally newest-first**
  by the existing `_newest_first_key`. This is the one place the eng preference lives.
- **`newest_for_session_id(session_id, *, prefer_type=None, **filters)`**: unchanged semantics —
  `rows[0]` of the above. Gains the argument so the five selection sites become one call.
- **Six sites collapse to iterating one correctly-ordered list**: the five selection sites take the
  head; `session_executor` keeps a single "first row with a run id" loop and deletes its duplicated
  eng pass.
- **Docstring correction**: `rows_for_session_id`'s current advice to hand-roll the preference is
  removed and replaced with a pointer to `prefer_type=`.
- **A grep sweep as the acceptance mechanism**, not a checklist of sites.

### Flow

Caller wants one row → `newest_for_session_id(sid, prefer_type="eng")` → the eng row if any exists,
the newest otherwise.

Caller wants to scan → `rows_for_session_id(sid, prefer_type="eng")` → eng rows first, newest-first
within each group → caller applies its own predicate over one ordered pass.

Caller pre-narrows (identity re-check, terminal-status exclusion) →
`rows_for_session_id(sid, prefer_type="eng")` → filter the list (order-preserving) → take the head.

### Technical Approach

**Decision: accept-and-encode, not prevent.** #3091 asked for an explicit choice among prevent (1),
reconcile (2), and accept-and-encode (3). This lane chooses **(3), completing it**:

- Option 1 (`session_id` as a `KeyField`) changes the Redis hash key of every `AgentSession` row and
  needs a fleet-wide idempotent migration plus SQLite-archive round-trip. Different blast radius; it
  already has #3169 with a fuller spec than #3091 carries.
- Option 2 (reconcile at read time or by sweep) would give `superseded` its first writer, but
  read-time reconciliation performs *writes on a read path* — a worse trade than ordering — and a
  sweep is a background job whose absence of a running instance is itself a failure mode. #3169 folds
  reconciliation into the migration, where it is a one-time step before uniqueness can hold.
- Option 3 is already 74/80ths done. Finishing it is small, reversible, and independently valuable:
  the invariant moves into one place, which is what #3169 later replaces with a stronger guarantee.
  When #3169 lands, `prefer_type` becomes a no-op over a one-element list and is deleted in that
  lane — it is not dead weight that must be migrated.

**Ordering, not selection — and why that matters.** An earlier scoping proposed a
`prefer_session_type(rows) -> row` primitive. That does not fit `agent/session_executor.py:330`,
which is not choosing a row: it is scanning for the first row carrying a non-empty `active_run_id`,
eng-first. A selection helper would have forced that site to keep hand-rolling, leaving the most
load-bearing copy in place. Expressing the preference as an **ordering** on `rows_for_session_id`
serves the scan and the five selections from a single implementation.

**The ordering must be a stable partition — implement it as a partition, not a composite sort key.**
`prefer_type` splits rows into two groups (matching, then non-matching) and sorts *within each group*
by the existing `_newest_first_key` (`created_at` desc as UTC epoch, then `id` desc; missing
`created_at` sorts oldest). It never invents a new comparison. `rows_for_session_id`'s newest-first
guarantee for the `prefer_type=None` path remains untouched, and
`tests/unit/test_agent_session_newest_wins.py` remains its pin.

The shape to **avoid**:

```python
sorted(rows, key=lambda r: (is_eng(r), _newest_first_key(r)), reverse=True)   # WRONG
```

A composite key can interleave the groups or let a group's internal order stop being newest-first.
Either silently changes behavior at the site that depends on a clean partition —
`_fetch_live_active_run_id`. See Risk 2, which this plan treats as a named acceptance item rather
than a footnote.

**Pre-narrowed sites stay pre-narrowed.** `find_session_by_issue`'s deterministic-id pass narrows by
identity re-check and by `include_terminal` (a *negative* status filter over
`_TERMINAL_ISSUE_LOOKUP_STATUSES`, which Popoto's equality filter cannot express). It keeps doing
that — on the list the ordering returns. Filtering a sorted list preserves order, so taking the head
afterwards is correct, and no primitive needs exposing.

**The class-set retry stays where it is.** In `sdlc_stage_query.py::_find_session_by_id` the call
goes *inside* the `class_set_retry_attempts()` loop, preserving #1720/#2550 behavior and the
`log_class_set_exhaustion` call on budget exhaustion. The retry asks "is the class set transiently
empty?"; the ordering asks "in what order?". Collapsing them would regress the retry.

**The empty-row-set fall-through contract is preserved — state it as a rule, not a site list.**
This is the critique's blocker and it is the single easiest way to break this refactor silently.

> **`newest_for_session_id(...) is None` is NOT equivalent to the old falsy-list branch. Every
> migrated site keeps its current empty-row-set behavior: an empty row set FALLS THROUGH, it does not
> return.**

Today each of the four non-`sdlc_stage_query` sites branches on a falsy list and *continues* to a
further resolution tier. A bare `return AgentSession.newest_for_session_id(sid, prefer_type="eng")`
returns `None` on an empty set and converts that fall-through into an early return. At
`tools/_sdlc_utils.py:468` that is not one lost tier but **three**: an explicit `session_id` that
resolves no rows would stop resolving by issue number *and* stop auto-ensuring — silent capability
loss on exactly the path #1671/#1672 exist to hold.

A reviewer reading a for-loop-to-helper diff will not see the branch that disappeared, which is why
the contract is written here in words rather than left to the diff.

Verified at source; the required shape at each site:

| Site | Today's fall-through | Required shape |
|------|----------------------|----------------|
| `tools/stage_states_helpers.py:103-110` | `if not matches: return session` — returns the **original** session object | `matches = AgentSession.rows_for_session_id(session_id, prefer_type="eng")` then `return matches[0] if matches else session`. Never a bare `return newest_for_session_id(...)` — its `None` would reach the `stage_states` write loop, whose sibling `_reload_ledger` proves the intended contract at `tools/stage_states_helpers.py:203` with `return fresh if fresh is not None else ledger`. |
| `tools/_sdlc_utils.py:464-472` (Step 1, explicit `session_id`) | `if sessions:` — empty falls through to Step 2 (issue-based) and Step 3 (env var), then auto-ensure | `found = AgentSession.newest_for_session_id(session_id, prefer_type="eng")` then `if found is not None: return found` and **fall through** otherwise. Never `return` the call directly. |
| `tools/_sdlc_utils.py:489-497` (Step 3, `VALOR_SESSION_ID`) | `if sessions:` — empty falls through to auto-ensure | Same shape as Step 1. |
| `tools/_sdlc_utils.py:364-371` (deterministic-id pass) | `if local: return local[0]` — empty falls through to the `message_text` regex fallback | Keep `rows_for_session_id(local_id, prefer_type="eng")`, narrow, then `if local: return local[0]` and fall through. |
| `tools/sdlc_stage_query.py:87-95` | already guarded — the call sits inside `class_set_retry_attempts()`, which owns the empty case | Unchanged in this respect. |

The Failure Path Test Strategy's existing coverage is of the resolver-**raises** branch, which is a
different path: a zero-row return raises nothing. Each site gets its own zero-rows-no-exception case
in Test Impact.

**The two raw bridge filters are left alone, deliberately.** `bridge/telegram_bridge.py:2249` and
`:2255` remain raw `AgentSession.query.filter(session_id=guard_session_id)`. Both feed only
`if guard_sessions:` and neither is ever indexed, so they are not coin flips and not this defect.
Ruled by the lane owner; recorded here so it does not read as an oversight. Changing them would be
consistency, not a fix, and would risk a later reader concluding that ordering mattered there.

**`prefer_type` is a concrete `session_type` string. No predicate argument.** A `prefer=lambda s: ...`
was considered and rejected, and the deciding reason is not "abstraction for one use case" — that is
the weak form of the argument. The deciding reason is that **a predicate makes the sweep
unfalsifiable.** This plan's entire close-out mechanism is a grep returning a known count: the code
sweep must go from 8 to 2, and the Verification row greps for the literal `prefer_type="eng"`. A
lambda is invisible to both. An equivalent-but-lambda-shaped copy of the preference would pass every
check while re-opening the defect class, which is precisely the failure this plan exists to prevent.
Secondarily: all six sites test the identical literal `getattr(row, "session_type", None) == "eng"`,
and the whole argument is deleted when #3169 lands.

### Two independent sweeps, two different anchors

These are **not** one sweep over two file sets. They match different strings in different files, and
conflating them is how the model docstring — the one that actually blesses the next hand-rolled copy
— survives a "clean" sweep.

| Sweep | Anchor | Scope | Today | After |
|-------|--------|-------|-------|-------|
| **CODE** — the replicated tie-break | `session_type", None) == "eng"` | `*.py`, production dirs (worktrees and `tests/` excluded) | exactly **8** | exactly **2** |
| **DOCS/DOCSTRING** — the replicated *sanction* | `fall back to .*\[0\]` (backtick-agnostic, by regex) | `docs/features/ models/` | exactly **2** | **0** |

**Why the docs anchor is regex and not literal.** The two sanctions are written in different backtick
forms: `docs/features/agent-session-model.md:146` uses markdown single backticks (``fall back to
`[0]` ``) while `models/agent_session.py:1297` uses RST double backticks (``fall back to ``[0]`` ``).
A pattern anchored on the single-backtick form matches the markdown and **misses the model
docstring**, reporting the sanction gone while the code docstring survives untouched. A bare
`fall back to` is also unusable — `models/agent_session.py` has 6 hits for it, 5 of them unrelated.
`fall back to .*\[0\]` is the anchor that catches both and nothing else.

**Why the docs sweep is scoped to `docs/features/ models/` and not `docs/`.** Two reasons, both
verified rather than assumed. First, **this plan document itself quotes the sanction it is deleting**
— at four places including inside the Critique Results row that proposed the check. A `docs/`-wide
check would be RED on a perfectly correct build, forever, and would then get "fixed" by someone
loosening it. Second, `docs/archive/plans-completed/` holds historical plan records
(`sdlc_issue_ownership_lock.md`, `sdlc-2140.md`, `sdlc-stall-auto-resume.md`, and others). **Standing
rule: `docs/archive/plans-completed/` is never touched.** A sweep that proposes editing an archived
plan is a sweep with the wrong anchor — treat that as the signal, not as work to do.

**The code sweep's survivors are named, with reasons — not counted.** "2 documented gates" as a bare
number invites a future sweeper to delete the thing the design depends on. Each survivor and why it
stays:

- `agent/session_executor.py:1373` — unrelated slug check; a gate on a single session, not a choice
  among rows. **Tests one already-resolved row's type; does not choose among rows.**
- `tools/sdlc_session_ensure.py:776` — gates PM `stage_states` so they never land on a Dev/Teammate
  session. **Tests one already-resolved row's type; does not choose among rows.**

**`agent/session_executor.py:330` is not a survivor — it is the sixth in-scope site and it is
deleted.** A draft of this revision listed it among the survivors on the reasoning that it is the site
that motivated expressing the preference as an *ordering* rather than a selection. That reasoning is
about *why the resolver has the shape it has*, not about the comparison surviving: task 5 collapses
the two-pass block and that `== "eng"` line goes with it. Recorded here explicitly because 8 − 6 = 2
only if `:330` is on the deleted side of the ledger, and a survivor list of three would contradict the
expected post-change count of two. If a post-change sweep still reports an eng comparison inside
`_fetch_live_active_run_id`, the collapse did not happen.

Folding any of the gates in would change behavior, not consolidate it.

Test assertions matching the pattern (`tests/integration/test_sdlc_session_ensure_integration.py:141,212,305`)
have their own explicit disposition in Test Impact — they are not covered by the count above, which
excludes `tests/`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The five selection sites sit inside `try/except Exception` blocks that log at `debug` and return
      `None` or the original session. These are **preserved as-is**; the change is inside the `try`.
      Add a test asserting `_reload_session` returns the *original* session object (not `None`) when
      the resolver raises — that fallback is load-bearing for the `stage_states` write path.
- [ ] `agent/session_executor.py`'s `rows_for_session_id` call is already wrapped in a `try/except`
      that logs a `debug` line and returns `None` to skip the tick. Assert that survives: a raising
      resolver must skip renewal, not crash the heartbeat.
- [ ] Assert `sdlc_stage_query._find_session_by_id` still calls `log_class_set_exhaustion` on budget
      exhaustion — the observable signal distinguishing "genuinely absent" from "transiently empty
      class set".
- [ ] No `except Exception: pass` blocks are introduced.

### Empty/Invalid Input Handling
- [ ] `rows_for_session_id(sid, prefer_type="eng")` on an unknown id returns `[]`;
      `newest_for_session_id` returns `None`.
- [ ] `prefer_type=None` (and `prefer_type=""`) must degrade to plain newest-first, never to "no
      match, return nothing". Test explicitly — this is the most likely silent-wrong-answer bug.
- [ ] Rows missing the `session_type` attribute entirely must not match the preferred group and must
      not raise. The existing sites use `getattr(..., None)` for exactly this reason; the ordering
      must preserve it. Test with a row lacking the attribute.
- [ ] A row set with **no** eng rows must produce the same order as `prefer_type=None`.
- [ ] **Zero rows, no exception raised — the fall-through contract.** Distinct from the raising branch
      above: a resolver that returns `[]` raises nothing, so the `try/except` never fires and only the
      falsy-list branch protects the caller. One case per migrated site, asserting the *pre-existing*
      continuation still happens:
      - `_reload_session` → `assert result is session` (the original object, not `None`).
      - `find_session` Step 1 with an explicit `session_id` resolving zero rows → resolution
        **continues** to the issue-based tier and to auto-ensure; assert it does not return `None`.
      - `find_session` Step 3 with `VALOR_SESSION_ID` resolving zero rows → continues to auto-ensure.
      - `find_session_by_issue` deterministic pass with zero rows → continues to the `message_text`
        regex fallback.

### Error State Rendering
- [ ] No user-visible output changes. The failure surface is an SDLC stage read returning `None`,
      which callers already render as "session not found", and a skipped lock-renewal tick which
      already logs at `debug` and retries next tick. Both preserved and asserted above.

## Test Impact

- [ ] `tests/unit/session_lookup_mock.py::wire_session_lookup` — UPDATE: derive the `prefer_type`
      keyword for both resolver methods, with the real grouping-and-ordering rule. **Highest-risk edit
      in the plan**: if missed, the mock accepts and forwards `prefer_type` and then **ignores** it,
      so mocked tests get a plausible list with no preference applied and the five `[0]` sites take
      the wrong row while appearing green. Tests asserting on call args go RED; tests asserting on
      returned rows stay green and wrong. Must land in the same commit as the model change. See
      Risk 1 for the reproduction and the required RED proof.
- [ ] `tests/unit/test_agent_session_newest_wins.py` — UPDATE: add real-Redis cases for
      `prefer_type`. Seed an eng row *older* than a non-eng row so preference and recency disagree;
      two eng rows to prove newest-eng leads; zero eng rows to prove the order equals the
      `prefer_type=None` order; assert group-internal newest-first for both groups.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: eng-preference assertions now satisfied through
      the resolver; verify the class-set retry and exhaustion logging survive.
- [ ] `tests/unit/test_sdlc_utils.py` — UPDATE: covers `find_session` Steps 1/3 and
      `find_session_by_issue`'s deterministic-id pass. Assert the `include_terminal` narrowing still
      happens *after* ordering and *before* taking the head — a terminal eng row must still lose to a
      live non-eng row when `include_terminal=False`.
- [ ] `tests/unit/test_agent_session_newest_wins.py` — UPDATE with the **named Risk 2 test**. Its home
      and its name are **pinned, not a choice** (an "or" here is what made the old verification row
      false-red): the test function is
      **`test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id`** and it lives in
      `tests/unit/test_agent_session_newest_wins.py`. Seed an eng row with `active_run_id=None` plus an
      **older** non-eng row with a real `active_run_id` → `_fetch_live_active_run_id` returns the older
      non-eng row's id. **Must be proven RED against the known-bad ordering**; a test green both before
      and after has pinned the happy path, not the ordering. Also assert the both-rows-carry-a-run-id
      case returns the eng row's id, which the two-pass loop provided.
- [ ] `tests/unit/test_stall_detection.py`, `tests/unit/test_agent_session_queue.py`,
      `tests/unit/test_health_check.py`, `tests/unit/test_poll_gating.py`,
      `tests/unit/test_sdk_client.py`, `tests/unit/test_valor_session_kill.py`,
      `tests/unit/test_valor_session_resume_release.py`, `tests/unit/test_bridge_relay.py`,
      `tests/unit/test_telegram_relay_chat_log.py`, `tests/unit/test_agent_session_hierarchy.py`,
      `tests/unit/test_sdlc_env_vars.py`, `tests/unit/sdlc_session_ensure/*` — UPDATE only if the
      `wire_session_lookup` change alters their behavior. Expectation is that none need editing — that
      is the point of the shared seam — but each must be **run** as evidence, not assumed.
- [ ] `tests/unit/test_steering_writer_census.py` — **NO CHANGE. Answered, not open.** `_is_resolver_call`
      (`tests/unit/test_steering_writer_census.py:205-210`) matches solely on
      `value.func.attr in RESOLVER_METHODS` and never inspects `node.value.keywords`, so adding
      `prefer_type="eng"` to a resolver call cannot change what the census counts. Recorded here with
      the citation so BUILD does not re-investigate a closed question.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py:141,212,305` — **DELIBERATELY LEFT,
      with a stated reason.** These three carry the same hand-rolled `getattr(s, "session_type", None)
      == "eng"` text and were outside the code sweep's baseline only because the sweep excludes
      `tests/`. They are **filter predicates the tests build themselves** to pick their own seeded row
      out of a list — they are not readers of production resolution, so routing them through
      `prefer_type` would couple the assertion to the thing under test and weaken it. They keep
      passing either way, which is the hazard: left unnamed they become an encoded record of a pattern
      that no longer exists in production. Named here and in the No-Gos so the disposition is explicit
      rather than an artifact of the count. If a future sweep widens to `tests/`, these three are the
      expected residue there.
- [ ] No test is DELETEd or REPLACEd. No `xfail` markers exist for this bug — `tests/` was searched
      for `pytest.mark.xfail` and runtime `pytest.xfail(` related to session_id selection; none found,
      so there are no expected-failure markers to convert.

## Rabbit Holes

- **Making `session_id` a `KeyField` "while we're in here."** That is #3169 in full: fleet-wide Redis
  rewrite, archive round-trip, duplicate reconciliation. It will look like a two-line model change and
  is not. Do not start it.
- **Giving `superseded` a writer.** Tempting — the model documents the status and nothing writes it,
  and "reconcile" was option 2 of the issue's own menu. It belongs to #3169's reconciliation step,
  where the winner rule is applied once rather than on every read.
- **Folding in the two gate sites** (`sdlc_session_ensure.py:776`, `session_executor.py:1373`). They
  match the sweep and look like the same smell. They are gates on a single resolved row, and folding
  them in changes behavior rather than consolidating it. See Technical Approach.
- **Rewriting `find_session_by_issue`'s eng-session `issue_url` scan** (`tools/_sdlc_utils.py:339`).
  A linear scan over all eng sessions with its own documented scale caveat, answering a different
  question (which session owns this *issue URL*). Not a `session_id` tie-break.
- **Collapsing the class-set retry into the resolver.** Different questions; merging regresses
  #1720/#2550.
- **Re-auditing the ~74 sites `3c77e1eab` already routed.** They are done. The sweep proves it in one
  command; re-reading them individually is the enumerated-list failure mode this plan exists to avoid.

## Risks

### Risk 1 (MUST SURVIVE CRITIQUE INTACT): the mocked-test seam returns plausible wrong data
**This risk is not waivable and its mitigation is not negotiable.** It was reproduced directly, not
reasoned about. Any proposal to drop or defer the seam update, or to weaken the RED proof below, is
rejected by this paragraph.

**Mechanism — verified by reproduction** against `tests/unit/session_lookup_mock.py` with the helper
**unmodified**, calling `rows_for_session_id("s", prefer_type="eng")`:

- **Case 1 — the test configures `query.filter.return_value` (the common shape).** Order returned:
  `['pm', 'eng']`. Call args: `call(session_id='s', prefer_type='eng')`. The kwarg is accepted,
  forwarded, and **silently ignored** by the mock. What comes back is newest-first with **no
  preference applied** — the newer `pm` row first, the older `eng` row second. No error, no warning,
  a plausible-looking ordered list that is simply wrong.
- **Case 2 — bare mock, nothing configured.** `rows: []`, `newest: None`. Loud, not silent. **Not the
  risk.**

**Why this is worse than a truthy sentinel:** under this refactor five of the six sites collapse to
`[0]` on the ordered list. A mocked test of any of those five then takes `[0]` of a list whose
preference was never applied, so it gets **the wrong row presented as the right one**, with no signal
anywhere. A truthy `MagicMock` usually breaks something downstream and gets noticed; plausible wrong
data does not.

**The safety-net asymmetry — this is the finding, state it in these terms:** tests asserting on
**call args** go RED, because the call becomes `filter(session_id='s', prefer_type='eng')` and an
`assert_called_with(session_id='s')` fails. Tests asserting on **what came back** stay green and
wrong. *Tests that check how the query was called break loudly; tests that check what it returned
break silently.* A reviewer needs to know which existing tests protect them and which do not.

This is the exact failure mode `083c961ab` was written to prevent, which is evidence it is live
rather than theoretical.

**Mitigation:** the seam update lands in the **same commit** as the model change.

**The RED proof must be on the ordering, not the wiring.** A test proving `wire_session_lookup`
forwards a kwarg proves nothing — it already forwards it, as the call args above show. The RED proof
is: configure a mock with a **newer non-eng row and an older eng row**, call through a production
site that takes `[0]`, and assert the **eng row** comes back. That fails against the unextended
helper and passes once the helper applies the preference. Anything weaker certifies the helper the
way the #3259 self-check certified a deny-all guard as healthy.

### Risk 2 (NAMED ACCEPTANCE ITEM): the two-pass → one-pass collapse in `_fetch_live_active_run_id`
This is the highest-risk part of the refactor and is tracked as an explicit acceptance criterion, not
a footnote.

**Symbol coordinates for grep:** `_fetch_live_active_run_id` is **defined** at
`agent/session_executor.py:298` and **called** at `agent/session_executor.py:384`, inside
`_tick_issue_lock_renewal`. Both ends are given so the build agent can grep either.

**Today** `_fetch_live_active_run_id` (`agent/session_executor.py:298-338`) makes **two** passes over
`rows`: pass 1 returns the first *eng* row with a non-empty `active_run_id`; pass 2 returns the first
*any* row with one. The refactor collapses that to **one** pass over a pre-ordered list. That is
behavior-preserving **only if** `prefer_type="eng"` produces a **stable partition** — all eng rows
first, then all non-eng rows, each group independently newest-first by `_newest_first_key`. Hence the
partition-not-composite-key constraint in Technical Approach.

**The case that breaks:** an eng row exists but carries **no** `active_run_id`, while an **older**
non-eng row does. The two-pass code returns the older non-eng row's run id. Any ordering bug returns
a different id, or `None`.

**Impact:** not cosmetic. The function's own docstring (`agent/session_executor.py:307-309`) records
the consequence: a `None` means renewal skips forever and the lock lapses mid-stage, reopening the
#1915 takeover window; a *wrong* id means a lapsed lock is SET-NX re-acquired **under a dead
identity** and renewed every tick, wedging the live run's own calls behind `ISSUE_LOCKED` until a
worker restart.

**Mitigation:** the collapse stays a **single loop with the same predicate** (`rid` non-empty) over
the ordered list — never a preference-then-fallback. And a **required, named test**:

> Seed an eng row with `active_run_id=None` and an **older** non-eng row with a real
> `active_run_id`; assert `_fetch_live_active_run_id` returns the **older non-eng row's** id.

That test must be **proven RED against the known-bad ordering** — stash the refactor and run it
against main's two-pass implementation, or write it first and run it against a deliberately
mis-ordered resolver. A test that passes both before and after has pinned the happy path, not the
ordering; that is precisely the failure mode to avoid here. A guard certifying absence is worthless
until proven red against known-bad.

**Test hygiene for this test:** real Redis via the autouse `redis_test_db` fixture, a `test-`
`project_key` prefix, rows deleted in fixture teardown — the same pattern as
`tests/unit/test_agent_session_newest_wins.py`. Run **only** the test files this change touches, via
`scripts/pytest-clean.sh`; never bare `pytest`, never a full `tests/unit/` run. Multiple lanes are
live on this machine.

**Not waivable:** the stable-partition ordering test is the entire thing standing between this
refactor and a wedged issue lock. If CRITIQUE or BUILD ever proposes dropping it as over-engineering,
that sentence is the answer. It is not a nice-to-have test.

### Risk 3: Narrow-then-head inverts at the deterministic-id pass
**Impact:** `find_session_by_issue` narrows by identity re-check and terminal-status exclusion. If the
refactor takes the head *before* narrowing, a terminal eng row that the current code correctly skips
would be returned — resurrecting a dead lane's session as the live one.
**Mitigation:** Order first, narrow second (order-preserving), take the head last. A test asserts a
terminal eng row loses to a live non-eng row when `include_terminal=False`.

### Risk 4: The sweep pattern is too loose or too tight
**Impact:** Too loose and it permanently flags the two gates and three test assertions, so the check
gets disabled as noisy. Too tight and a reformatted copy of the preference slips past, reopening the
defect class silently.
**Mitigation:** The code sweep uses the defining pattern (`session_type", None) == "eng"`), scoped to
production directories, baseline **8**, expected residue **exactly 2** — `sdlc_session_ensure.py:776`
and `session_executor.py:1373`, **named** rather than counted, in this plan and in the check row, and
marked by a site-local comment at each so the next reader does not re-open the question. Prove the
pattern RED by reintroducing one deleted block on a scratch copy before landing, and paste that RED
output into the PR. A guard certifying absence is worthless until proven red against the known-bad
input.

**The same failure mode killed the first draft of the docs check**, which is why Two Independent
Sweeps is spelled out in Technical Approach. That draft proposed `fall back to [0]` over `docs/`: it
was **false-red by construction** (this plan document quotes the sanction it deletes, so the check
could never reach 0 on a correct build), it used a single-backtick anchor that **misses the model
docstring** entirely, and widening it to a bare `fall back to` adds 5 unrelated hits in
`models/agent_session.py`. A check that is false-red on a correct build is worse than no check: it
gets "fixed" by loosening it, and then passes vacuously forever. Every closing check in this plan was
**run against the tree** and its baseline recorded before being written down. Do not harden a check
that has not been run.

### Risk 5: Behavior change where no eng row exists
**Impact:** Today five sites fall back to `rows[0]`, which since `3c77e1eab` is the newest row. The
ordering must reproduce that exactly; any divergence silently re-points stage reads.
**Mitigation:** With no eng rows the partition is empty and the ordering is identical to
`prefer_type=None`, by construction. A test asserts the two orders are equal for a no-eng row set.

## Race Conditions

### Race 1: Duplicate row created between read and stage-state write
**Location:** `tools/stage_states_helpers.py:103-110` (`_reload_session`), immediately before a
`stage_states` mutation.
**Trigger:** A concurrent `session-ensure` mints a second row for the same `session_id` between the
reload and the write.
**Data prerequisite:** The row selected by the reload must still be the row the write targets.
**State prerequisite:** Preference must be stable across calls — two reloads in the same window must
not disagree.
**Mitigation:** Not newly introduced and not newly fixed here; the hazard is inherent to duplicates
and is what #3169 removes. This plan makes selection deterministic *given a row set*, which improves
on today only in that the rule is singular. A test asserts repeated
`newest_for_session_id(..., prefer_type="eng")` calls over an unchanged row set return the identical
row — the same stability property `test_agent_session_newest_wins.py` already pins for ordering.

### Race 2: Transiently empty class set during `rebuild_indexes()`
**Location:** `tools/sdlc_stage_query.py:87-95`, inside `class_set_retry_attempts()`.
**Trigger:** Popoto's `rebuild_indexes()` empties `$Class:AgentSession` while a filter runs.
**Data prerequisite:** The class set must be populated for the filter to return the live row.
**State prerequisite:** None beyond the retry budget.
**Mitigation:** Existing and preserved — the call stays inside the retry loop and
`log_class_set_exhaustion` still fires on exhaustion. Explicitly asserted in Test Impact.

### Race 3: Issue-lock renewal reads a row mid-write on the 60s tick
**Location:** `agent/session_executor.py:328-338`.
**Trigger:** `active_run_id` is being written on one row while the renewal tick scans rows.
**Data prerequisite:** At least one row must carry a non-empty `active_run_id` for renewal to
proceed.
**State prerequisite:** None — the tick is explicitly best-effort.
**Mitigation:** Existing and unchanged: the function returns `None` (skip this tick) on any failure
and the next tick retries. The ordering change does not alter the retry cadence. Risk 2's test pins
that a partially-populated row set still yields the correct run_id.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3169] Making `session_id` part of the Popoto key so one `session_id` maps to one
  row. Carries a fleet-wide Redis data migration, SQLite-archive round-trip, and duplicate
  reconciliation. Out of proportion for this lane. This plan ticks that issue's AC item 4 ahead of the
  migration.
- [SEPARATE-SLUG #3169] Giving the `superseded` status a writer, and reconciling existing duplicate
  pairs. Belongs with the migration that makes uniqueness hold.
- [SEPARATE-SLUG #3065] Hardening the `session-ensure` write path against duplicates (readback by
  primary key, candidate provenance). Already planned there; this lane makes **no behavior change** to
  `tools/sdlc_session_ensure.py`. The one edit to that file is the gate-marking **comment** at `:776`
  — comment-only, and not a #3065 collision.
- [NOT-DOING] Migrating the three test-local filter predicates at
  `tests/integration/test_sdlc_session_ensure_integration.py:141,212,305`. They build their own
  filters to pick a seeded row out of a list; routing them through `prefer_type` would couple the
  assertion to the thing under test and weaken it. Deliberately left, with the reason stated here and
  in Test Impact rather than left to the sweep's `tests/` exclusion.
- [NEVER] Editing anything under `docs/archive/plans-completed/`. Archived plans are historical
  records. A sweep that proposes editing one is a sweep with the wrong anchor — treat that as the
  signal, not as work to do.
- [SEPARATE-SLUG #3169] Replacing the `issue_url` linear scan at `tools/_sdlc_utils.py:339` with an
  indexed lookup. A scale concern, not a correctness one, and not a `session_id` tie-break.

## Update System

No update system changes required — this feature is purely internal. No new dependencies, no config
files, no `pyproject.toml` changes, and **no Popoto migration**: the change adds a keyword argument to
two existing `AgentSession` classmethods and alters no `Field`, `KeyField`, or stored key shape, so
`scripts/update/migrations.py` is untouched. Existing installations pick the change up through an
ordinary `/update` (git pull + service restart). Running old and new code against the same Redis is
safe: both select from the same row set, and the new ordering is a refinement of the old one rather
than a different row universe.

## Agent Integration

No agent integration required — this is an internal refactor of existing SDLC tooling and the session
executor's heartbeat. No new CLI entry point is needed in `pyproject.toml [project.scripts]`; the
affected code is reached through `sdlc-tool` subcommands that already exist and through the worker's
own tick loop. The bridge imports nothing new. The agent-observable behavior of `sdlc-tool stage-query`
and the stage markers is unchanged **by design**, and that invariance is itself asserted by the
existing tests in `tests/unit/test_sdlc_stage_query.py`.

## Documentation

### Feature Documentation
- [ ] **DELETE the fall-back-to-`[0]` sanction at `docs/features/agent-session-model.md:145-148`** —
      not merely append `prefer_type` prose beside it. The paragraph currently reads "Callers with a
      domain preference (an eng-typed row owns `stage_states`) iterate `rows_for_session_id` and fall
      back to `[0]`…". **The documentation is part of the defect, not a description of it**: it is the
      same sanction as the `models/agent_session.py:1297` docstring, replicated into the repo's own
      feature doc, and neither is discoverable by editing code. Both must be in the diff. Replace the
      paragraph with `prefer_type`: state that it is the single owner of the eng preference, that the
      ordering is a stable partition, and *why* eng wins (it owns `stage_states` and `active_run_id`).
      Closing check: the docs sweep (`fall back to .*\[0\]` over `docs/features/ models/`) goes 2 → 0.
- [ ] Update `docs/features/sdlc-lane-identity.md` if it describes how a lane's session row is
      located; point it at the shared ordering rather than at any call site.
- [ ] No new `docs/features/README.md` index entry — this extends an existing documented feature
      rather than adding one.

### External Documentation Site
- [ ] Not applicable — this repo has no external documentation site.

### Inline Documentation
- [ ] **Rewrite the `rows_for_session_id` docstring** (`models/agent_session.py:1295-1298`). It
      currently instructs callers with a domain preference to "iterate this list and fall back to
      `[0]`". That sanction is part of the defect — it is why the replication spread. Replace it with
      a pointer to `prefer_type=`. Leaving it would actively re-teach the bug, with the model's
      blessing, to the seventh caller.
- [ ] Docstring for `prefer_type` on both methods: the grouping rule, that group-internal order is
      the existing newest-first key, that it never invents a comparison, and that it becomes a no-op
      once #3169 lands.
- [ ] A comment at `agent/session_executor.py`'s renewal scan recording that the single pass relies
      on the resolver's ordering — so nobody "restores" the second pass.
- [ ] **Gate-marking comments at the two surviving sweep sites** — `tools/sdlc_session_ensure.py:776`
      and `agent/session_executor.py:1373`. Risk 4's named failure mode is the sweep being disabled as
      noisy; a site-local comment is the cheapest defense and makes the expected residue of exactly 2
      self-documenting at the call site rather than only in a plan document nobody greps. The comment
      states **why the shape differs**, never merely that the site is excluded — "excluded from the
      sweep" tells the next reader nothing and invites the question again. Mandated wording: *tests one
      already-resolved row's type; does not choose among rows.*
      **The `sdlc_session_ensure.py` edit is comment-only** and does not violate the No-Go against
      modifying that file's behavior, nor does it collide with #3065 — stated here so a reviewer does
      not read a touched file as a lane collision.

## Success Criteria

- [ ] The eng preference is expressed once, as an ordering on `rows_for_session_id`; the rule appears
      in exactly one place in the codebase.
- [ ] All six sites use it: `tools/sdlc_stage_query.py`, `tools/_sdlc_utils.py` (×3),
      `tools/stage_states_helpers.py`, `agent/session_executor.py`.
- [ ] **Both sweeps close the issue.** Code sweep: `session_type", None) == "eng"` over production
      code goes 8 → exactly 2, and those 2 are `tools/sdlc_session_ensure.py:776` and
      `agent/session_executor.py:1373` **by name**. Docs sweep: `fall back to .*\[0\]` over
      `docs/features/ models/` goes 2 → 0. The acceptance mechanism is the sweeps, not the site list
      above, and the code pattern is proven RED against a reintroduced block before it is trusted.
- [ ] **Every migrated site preserves its empty-row-set fall-through.** An empty row set falls
      through; it does not return `None`. Asserted per site with a zero-rows-no-exception test.
- [ ] Both gate-marking comments are present, and each states *why* the shape differs — "tests one
      already-resolved row's type; does not choose among rows" — not merely that the site is excluded.
- [ ] The `docs/features/agent-session-model.md` sanction paragraph is **deleted**, not appended to.
      The documentation was part of the defect; both copies are in the diff.
- [ ] **`prefer_type` is implemented as a stable partition, not a composite sort key** — all matching
      rows first, then the rest, each group independently newest-first. No interleaving.
- [ ] **The named Risk 2 test exists and was proven RED against the known-bad ordering:** an eng row
      with `active_run_id=None` plus an **older** non-eng row with a real `active_run_id` →
      `_fetch_live_active_run_id` returns the **older non-eng row's** id. The RED output is pasted
      into the PR description. A test green both before and after the refactor does not satisfy this
      criterion.
- [ ] `agent/session_executor.py`'s renewal scan is a single pass with one predicate (`rid`
      non-empty), never a preference-then-fallback.
- [ ] The `rows_for_session_id` docstring no longer instructs callers to hand-roll the preference.
- [ ] `tests/unit/session_lookup_mock.py::wire_session_lookup` covers `prefer_type`, proven by an
      **ordering** test that is RED against the unextended seam: a newer non-eng row and an older eng
      row, called through a production site that takes `[0]`, asserting the eng row comes back. A
      test that only proves the kwarg is forwarded does not count — it already is.
- [ ] Behavior at every migrated site is unchanged when an eng row exists, when none exists, and when
      the row list is empty.
- [ ] The class-set retry and its exhaustion logging survive in `sdlc_stage_query.py`.
- [ ] `bridge/telegram_bridge.py:2249,2255` are unchanged, and the plan's reasoning for leaving them
      is reflected in the PR description.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`), including the corrected docstring
- [ ] No related xfail/xpass tests exist to convert (verified: none found)

## Team Orchestration

### Team Members

- **Builder (ordering + call sites)**
  - Name: `resolver-builder`
  - Role: Add `prefer_type` to both resolver methods, extend `wire_session_lookup`, migrate all six
    call sites, correct the docstring.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Test engineer (resolver coverage)**
  - Name: `resolver-tests`
  - Role: Extend `test_agent_session_newest_wins.py` with grouping cases against real Redis; prove the
    mocked-seam test RED before the seam is extended; pin the `session_executor` single-pass behavior;
    add the failure-path cases.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (sweep + behavior parity)**
  - Name: `resolver-validator`
  - Role: Run the grep sweep, verify its RED proof, confirm behavior parity at each migrated site, run
    the full affected-test set.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `resolver-docs`
  - Role: Update `docs/features/agent-session-model.md` and the docstrings, especially the now-wrong
    `rows_for_session_id` guidance.
  - Agent Type: documentarian
  - Resume: true

### Domain framing for `resolver-builder`

Paste the Redis/Popoto rules from `.claude/skills-global/do-plan/DOMAIN_FRAMING.md` into the
assignment. Load-bearing points: never write raw Redis ops; this change adds **no** schema field and therefore needs **no** entry in
`scripts/update/migrations.py`.

## Step by Step Tasks

### 1. Prove the sweep RED
- **Task ID**: build-sweep-red
- **Depends On**: none
- **Validates**: no test file; produces the RED output pasted into the PR description
- **Assigned To**: `resolver-tests`
- **Agent Type**: test-engineer
- **Parallel**: true
- Run the **code** sweep over production directories against current `main`: it must report **8** hits
  — the six in-scope sites plus the two documented gates. Baseline verified at plan time; BUILD
  inherits the number rather than re-deriving it.
- Run the **docs** sweep (`grep -rnE 'fall back to .*\[0\]' docs/features/ models/`): it must report
  **2** — `docs/features/agent-session-model.md:146` and `models/agent_session.py:1297`. Also verified
  at plan time. Confirm the regex catches **both** backtick forms; a single-backtick literal misses the
  model docstring.
- Confirm it does **not** flag `tools/_sdlc_utils.py:339`'s `issue_url` eng scan.
- Record which two of the eight are the gates that legitimately survive the fix.
- Save the RED output verbatim for the PR description.

### 2. Add `prefer_type` to the resolver
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_newest_wins.py
- **Informed By**: Technical Approach (ordering not selection; group-internal newest-first)
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add keyword-only `prefer_type=None` to `rows_for_session_id`. Implement as an explicit **partition**
  on `getattr(row, "session_type", None)` — matching group first, then the rest, each sorted
  independently by the existing `_newest_first_key`. **Do not** use a composite sort key
  (`sorted(rows, key=lambda r: (is_eng(r), _newest_first_key(r)), reverse=True)` is the shape to
  avoid); it can interleave groups or break group-internal order. `prefer_type=None` path
  byte-for-byte unchanged.
- Add the same argument to `newest_for_session_id`, which stays `rows[0] if rows else None`.
- Rewrite the `rows_for_session_id` docstring per the Documentation section — remove the
  "iterate and fall back to `[0]`" sanction.

### 2b. Produce both RED proofs
- **Task ID**: build-red-proofs
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_agent_session_newest_wins.py, tests/unit/session_lookup_mock.py
- **Assigned To**: `resolver-tests`
- **Agent Type**: test-engineer
- **Parallel**: false
- **Why this is its own task, and not prose inside tasks 3/5:** the two RED proofs are the plan's two
  non-waivable items, and a non-waivable item enforced by a sentence with no `Depends On` edge is not
  non-waivable — it is a wish. A builder who honors only the dependency graph could otherwise land
  both collapses before either proof exists. This task exists so the graph says what the prose says.
- **Proof A — the Risk 2 ordering proof.** Write
  `test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id` in
  `tests/unit/test_agent_session_newest_wins.py`: an eng row with `active_run_id=None` plus an
  **older** non-eng row with a real `active_run_id`; assert `_fetch_live_active_run_id` returns the
  older non-eng row's id. Run it against the **pre-collapse** two-pass code and capture the RED
  output. A test green both before and after has pinned the happy path, not the ordering.
- **Proof B — the Risk 1 seam proof.** Configure a mock with a **newer non-eng row and an older eng
  row**, read through a production site that takes `[0]`, and assert the **eng row** comes back. Run
  it against the **unextended** `wire_session_lookup` and capture the RED output. A test that only
  proves the kwarg is forwarded does not count — the unextended helper already forwards it and then
  ignores it.
- Both RED outputs go verbatim into the PR description. They are the real gate; the Verification greps
  are only smoke checks.

### 3. Extend the mocked-test seam
- **Task ID**: build-mock-seam
- **Depends On**: build-resolver, build-red-proofs
- **Validates**: tests/unit/session_lookup_mock.py
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- Extend `wire_session_lookup` so both methods honor `prefer_type` with the real grouping rule,
  derived from the mock's own `query.filter`.
- Land in the same commit as task 2 — a seam that lags the model forwards `prefer_type` and ignores
  it, handing the `[0]` sites the wrong row with no signal.
- Task 2b's Proof B must already be RED before this lands — enforced by the `Depends On` edge above,
  not by this sentence. Turning it GREEN is this task's completion signal.

### 4. Migrate the five selection sites
- **Task ID**: build-selection-sites
- **Depends On**: build-mock-seam
- **Validates**: tests/unit/test_sdlc_stage_query.py, tests/unit/test_sdlc_utils.py
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- **Read the fall-through contract in Technical Approach before touching any of these.** A bare
  `return AgentSession.newest_for_session_id(sid, prefer_type="eng")` is wrong at four of the five
  sites: its `None` on an empty row set converts a fall-through into an early return, and at
  `_sdlc_utils.py:468` that silently drops three live resolution tiers. The contract: **an empty row
  set falls through, it does not return.**
- `tools/sdlc_stage_query.py:87-95` → `newest_for_session_id(session_id, prefer_type="eng")`, kept
  **inside** `class_set_retry_attempts()`, `log_class_set_exhaustion` preserved. This is the one site
  where the direct form is correct — the retry loop owns the empty case.
- `tools/_sdlc_utils.py:464-472` (Step 1) and `:489-497` (Step 3) →
  `found = AgentSession.newest_for_session_id(..., prefer_type="eng")`, then
  `if found is not None: return found`, and **fall through** otherwise. Never `return` the call
  directly.
- `tools/_sdlc_utils.py:364-371` → `rows_for_session_id(local_id, prefer_type="eng")`, then the
  existing identity re-check and `include_terminal` narrowing (order-preserving), then
  `if local: return local[0]` and fall through to the `message_text` regex fallback.
- `tools/stage_states_helpers.py:103-110` →
  `matches = AgentSession.rows_for_session_id(session_id, prefer_type="eng")` then
  `return matches[0] if matches else session` — the **original** session object on empty, matching the
  contract `_reload_ledger` states at `tools/stage_states_helpers.py:203`.
- Delete every hand-rolled block. No commented-out remnants.
- Add the gate-marking comment at `tools/sdlc_session_ensure.py:776` with the mandated wording: *tests
  one already-resolved row's type; does not choose among rows.* **Comment-only** — no behavior change,
  and not a #3065 collision.

### 5. Collapse the session_executor scan
- **Task ID**: build-executor-scan
- **Depends On**: build-mock-seam, build-red-proofs
- **Validates**: the session_executor issue-lock-renewal test module
- **Informed By**: Risk 2 (the collapse must keep one predicate, not become preference-then-fallback)
- **Assigned To**: `resolver-builder`
- **Agent Type**: builder
- **Parallel**: false
- `_fetch_live_active_run_id` (`agent/session_executor.py:328-338`) →
  `rows_for_session_id(sid, prefer_type="eng")`, then **one** loop returning the first non-empty
  `active_run_id`. Delete the duplicated eng pass. One predicate, never preference-then-fallback.
- Task 2b's Proof A must already be RED before this lands — enforced by the `Depends On` edge above,
  not by prose. Turning it GREEN is this task's completion signal.
- Preserve the surrounding `try/except` that skips the tick on failure.
- Add the comment recording that the single pass relies on the resolver's stable partition, and that
  restoring a second pass or switching to a composite sort key reintroduces the #1915 lock-wedge
  failure described in the function's docstring.
- Add the gate-marking comment at `agent/session_executor.py:1373` with the mandated wording: *tests
  one already-resolved row's type; does not choose among rows.*

### 6. Resolver and failure-path tests
- **Task ID**: build-tests
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_agent_session_newest_wins.py
- **Assigned To**: `resolver-tests`
- **Agent Type**: test-engineer
- **Parallel**: false
- Real-Redis grouping cases: older eng row leads a newer non-eng row; newest eng leads among two eng
  rows; no eng row yields the `prefer_type=None` order exactly; group-internal newest-first holds for
  both groups; empty set → `[]` / `None`; `prefer_type=None` degrades to newest; a row missing
  `session_type` neither matches nor raises; repeated calls are stable.
- The **named Risk 2 test** (`test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id`, in
  `tests/unit/test_agent_session_newest_wins.py`) and the mocked-seam ordering test are both written
  and proven RED in **task 2b**, not here. This task confirms they are GREEN after the collapse and
  adds the remaining cases below.
- Partition-shape test: with eng and non-eng rows interleaved by `created_at`, assert the returned
  list is all-eng-then-all-non-eng and newest-first *within* each group — the property the single-pass
  scan depends on.
- Risk 3 case: terminal eng row loses to live non-eng row when `include_terminal=False`.
- `_reload_session` returns the original session when the resolver raises; the renewal tick returns
  `None` rather than crashing.
- **Fall-through cases — one per migrated site, zero rows and no exception raised:** `_reload_session`
  returns the original session (`assert result is session`); `find_session` Steps 1 and 3 continue to
  the next tier and to auto-ensure rather than returning `None`; the deterministic-id pass continues
  to the `message_text` fallback. These are a different branch from the raising cases above — a
  zero-row return never enters the `except`.
- **Hygiene:** real Redis via the autouse `redis_test_db` fixture, `test-` `project_key` prefix, rows
  deleted in fixture teardown — the pattern `tests/unit/test_agent_session_newest_wins.py` already
  uses. Run **only** the touched test files via `scripts/pytest-clean.sh`; never bare `pytest`, never
  a full `tests/unit/` run. Multiple lanes are live on this machine.

### 7. Validate
- **Task ID**: validate-all
- **Depends On**: build-sweep-red, build-red-proofs, build-tests, build-selection-sites, build-executor-scan
- **Assigned To**: `resolver-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run **both** sweeps. Code sweep: 8 → exactly 2, and those 2 are `sdlc_session_ensure.py:776` and
  `session_executor.py:1373` by name, not by count. Docs sweep (`fall back to .*\[0\]` over
  `docs/features/ models/`): 2 → 0.
- Run every test file named in Test Impact via `scripts/pytest-clean.sh`, recording counts as
  evidence rather than assumption.
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
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_agent_session_newest_wins.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_utils.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| **Code sweep: only the two documented gates remain** | `grep -rn --include='*.py' 'session_type", None) == "eng"' agent/ bridge/ tools/ models/ reflections/ ui/ .claude/hooks/` | exactly 2 lines, and they are `tools/sdlc_session_ensure.py:776` and `agent/session_executor.py:1373` **by name** (**baseline today: exactly 8**). Read the lines, do not just count them — a bare count invites a future sweeper to delete a survivor the design depends on |
| Sweep: no tie-break left in the five tools sites | `grep -rn 'session_type", None) == "eng"' tools/sdlc_stage_query.py tools/_sdlc_utils.py tools/stage_states_helpers.py \| wc -l` | output contains 0 |
| All six sites use prefer_type | `grep -rn 'prefer_type="eng"' tools/sdlc_stage_query.py tools/_sdlc_utils.py tools/stage_states_helpers.py agent/session_executor.py \| wc -l` | output contains 6 |
| Resolver accepts prefer_type | `grep -c 'prefer_type' models/agent_session.py` | output > 0 |
| Mocked seam covers prefer_type | `grep -c 'prefer_type' tests/unit/session_lookup_mock.py` | output > 0 |
| session_executor scan is single-pass | `python -c "import ast,sys;f=[n for n in ast.walk(ast.parse(open('agent/session_executor.py').read())) if isinstance(n,ast.FunctionDef) and n.name=='_fetch_live_active_run_id'][0];print(sum(1 for n in ast.walk(f) if isinstance(n,ast.For)))"` | output contains 1 (baseline today: 2). Coordinate-free — resolves the function by name, so it survives any edit above it |
| Named Risk 2 test exists, by exact name | `grep -c 'def test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id' tests/unit/test_agent_session_newest_wins.py` | output contains 1 (baseline today: 0). Anchored on a string that cannot pre-exist; the module is pinned, not "or". The pasted RED output in the PR description is the real gate — this row is only a smoke check |
| prefer_type is a partition, not a composite sort key | `grep -n 'prefer_type' -A12 models/agent_session.py \| grep -c 'is_eng(r), _newest_first_key'` | match count == 0 |
| Class-set retry preserved | `grep -c 'log_class_set_exhaustion' tools/sdlc_stage_query.py` | output > 0 |
| Bridge presence checks untouched | `grep -c 'query.filter(session_id=guard_session_id)' bridge/telegram_bridge.py` | output contains 2 |
| No Popoto migration added | `git diff --name-only main -- scripts/update/migrations.py \| wc -l` | output contains 0 |
| **Docs sweep: the `[0]` sanction is gone from both places** | `grep -rnE 'fall back to .*\[0\]' docs/features/ models/ \| wc -l` | output contains 0 (**baseline today: exactly 2** — `docs/features/agent-session-model.md:146`, markdown single-backtick, and `models/agent_session.py:1297`, RST double-backtick). Backtick-agnostic by design: a single-backtick literal matches the markdown and misses the model docstring. Scoped to `docs/features/ models/` because this plan document quotes the sanction it deletes (a `docs/`-wide check is RED forever on a correct build) and because `docs/archive/plans-completed/` is never touched |
| Mocked-seam ordering proof exists | `grep -rc 'prefer_type' tests/unit/session_lookup_mock.py` | output > 0, **and** task 2b's Proof B RED output is pasted in the PR description |
| Gate-marking comments present at both survivors | `grep -c 'does not choose among rows' tools/sdlc_session_ensure.py agent/session_executor.py` | 1 in each file (baseline today: 0 in both) |

## Critique Results

FULL roster (3 critics), independent roster. Verdict: **NEEDS REVISION** (1 blocker).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + aggregator (independent convergence) | The empty-row-set fall-through contract is dropped at four of the five selection sites. Today each site branches on a falsy list and CONTINUES: `tools/stage_states_helpers.py:104` `if not matches: return session`; `tools/_sdlc_utils.py:468` Step 1 `if sessions:` falls through to Step 2 (issue-based) and Step 3; `tools/_sdlc_utils.py:493` Step 3 `if sessions:` falls through to auto-ensure; `tools/_sdlc_utils.py:369-370` deterministic pass `if local: return local[0]` falls through to the message_text fallback. Task 4 instructs a bare `newest_for_session_id(..., prefer_type="eng")` at each, which returns `None` on an empty row set — converting a fall-through into a returned `None`. Only `tools/sdlc_stage_query.py` is guarded ("kept inside `class_set_retry_attempts()`"). Failure Path Test Strategy covers only the resolver-RAISES path, which is a different branch. | **applied** | State it as a contract rule, not a site list: **`newest_for_session_id` returning `None` is NOT equivalent to the old falsy-list branch; every migrated site keeps its current empty-set behavior.** Concretely: `stage_states_helpers` → `matches = AgentSession.rows_for_session_id(session_id, prefer_type="eng"); return matches[0] if matches else session` (never a bare `return newest_for_session_id(...)`, whose `None` would be handed to the `stage_states` write loop at `tools/stage_states_helpers.py:203` where `_reload_ledger` proves the intended contract with `return fresh if fresh is not None else ledger`). `_sdlc_utils` Steps 1/3 → `found = AgentSession.newest_for_session_id(sid, prefer_type="eng")` then `if found is not None: return found` and FALL THROUGH otherwise — never `return` the call directly. Deterministic pass → keep `rows_for_session_id(...)`, narrow, then `if local: return local[0]` and fall through. Add a Test Impact case per site seeding **zero rows with no exception raised** and asserting the pre-existing fall-through still happens (for `_reload_session`, `assert result is session`). |
| CONCERN | aggregator (verified against source) | `docs/features/agent-session-model.md:146` replicates the exact sanction this plan exists to delete — "iterate `rows_for_session_id` and fall back to `[0]`" — so the defect's instruction survives the docstring rewrite in the repo's own feature doc. The Documentation section only says "Extend that section to describe `prefer_type`". The sanction is a replicated value in two places, and the plan treats only one. | **applied** | The Documentation task must DELETE the fall-back-to-`[0]` guidance at `docs/features/agent-session-model.md:146` (not merely append `prefer_type` prose), and the closing check must be a grep, matching this plan's own sweep-not-checklist doctrine: `grep -rn 'fall back to `\[0\]`\|fall back to \[0\]' docs/ models/` returns 0 hits. Add that as a Verification row alongside the existing docstring row. |
| CONCERN | Risk & Robustness + aggregator | The Verification row "Named Risk 2 test exists" (`grep -rn 'active_run_id' tests/unit/test_agent_session_newest_wins.py \| wc -l` → `> 0`) proves nothing about the Risk 2 scenario — any incidental mention passes. It also breaks in the other direction: Test Impact permits the test to land in "`tests/unit/test_agent_session_newest_wins.py` (or the session_executor test module covering issue-lock renewal)", in which case this hard-coded path yields 0 and fails spuriously. A non-waivable acceptance item is gated by a check that can be both false-green and false-red. | **applied** | Pin the test's home and its name. Name the function `test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id`, decide its module in the plan (not "or"), and change the row to `grep -rc 'def test_fetch_live_active_run_id_prefers_older_non_eng_with_run_id' <that module>` expecting `1`. The pasted RED output in the PR description stays the real gate; the grep is only the smoke check. |
| CONCERN | aggregator (structural) | The two non-waivable RED proofs are enforced by prose only; the machine-readable `Depends On` graph contradicts it. Task 5 says "Do not land this task until task 6's named Risk 2 test has been proven RED" but `build-executor-scan` depends on `build-mock-seam` alone. Task 3 says "Prove it RED against the unextended helper first" while the mocked-seam test is owned by task 6, which has no edge to task 3. A builder honoring only the dependency graph can land both collapses before either RED proof exists. | **applied** | Encode the ordering: split the RED proofs out of task 6 into their own task (`build-red-proofs`, depending on `build-resolver` only, producing both RED outputs), then add `build-red-proofs` to `Depends On` for `build-mock-seam` (task 3) and `build-executor-scan` (task 5). Do not rely on the "Do not land this task until…" sentence surviving a builder's task-by-task read. |
| CONCERN | History & Consistency | The Problem section and the Freshness Check's "still holds" bullet both cite `models/agent_session.py:156-157` for `id = AutoKeyField()` / `session_id = Field()`. The real coordinates are `163-164` (`id = AutoKeyField()` at 163, `session_id = Field()` at 164). The Freshness Check explicitly asserts this citation was re-verified, so the drift falsifies a stated verification claim rather than being a stale reference. | **applied** | Replace both occurrences of `models/agent_session.py:156-157` with `models/agent_session.py:163-164`. The other cited coordinates were checked against source and are correct: `1282-1302`, `1295-1298`, `sdlc_stage_query.py:93`, `_sdlc_utils.py:368,470,495`, `stage_states_helpers.py:108`, `session_executor.py:298,330,384,1373`, `sdlc_session_ensure.py:776`. |
| NIT | aggregator | Team Orchestration references `DOMAIN_FRAMING.md` bare; the file lives at `.claude/skills-global/do-plan/DOMAIN_FRAMING.md`. | **applied** | n/a (NIT) |
| NIT | aggregator | The Verification row `sed -n '300,345p' agent/session_executor.py \| grep -c 'for row in rows'` is coordinate-brittle: deleting the eng pass shifts the function, and any unrelated edit above line 298 invalidates the window. | **applied** | n/a (NIT) |
| RULING | aggregator (Open Question 1) | **`prefer_type` stays a concrete `session_type` string; no predicate.** All six sites test the identical literal `getattr(row, "session_type", None) == "eng"`; a `prefer=lambda` would be an abstraction for one use case, and it would defeat the plan's own acceptance mechanism — the sweep greps for the literal comparison and the Verification row greps for `prefer_type="eng"`, neither of which can see an equivalent lambda. The argument is also deleted when #3169 lands. No plan change. | n/a — ruling | Independently reached by the Scope & Value critic and the aggregator. |
| RULING | aggregator (Open Question 2) | **No. `tests/unit/test_steering_writer_census.py` does NOT need the new keyword registered.** Verified against source: `_is_resolver_call` (`tests/unit/test_steering_writer_census.py:205-210`) matches solely on `value.func.attr in RESOLVER_METHODS` and never inspects `node.value.keywords`, so adding `prefer_type="eng"` to a resolver call cannot change what the census counts. | **applied** | Replace the Test Impact "VERIFY" checkbox with this answered fact plus the `:205-210` citation, so BUILD does not re-investigate a closed question. |
| RULING | aggregator (Open Question 3) | **Yes — add the marking comments at `tools/sdlc_session_ensure.py:776` and `agent/session_executor.py:1373`, with the plan's mandated wording.** Risk 4's named failure mode is the sweep being disabled as noisy; a site-local comment is the cheapest defense and makes the expected residue of exactly 2 self-documenting at the call site rather than only in a plan document nobody greps. | **applied** | The comment states WHY the shape differs, never merely that the site is excluded: *tests one already-resolved row's type; does not choose among rows.* Add it as an explicit bullet in a task (the plan notes it is "Not currently in the task list") and add a Success Criterion; task 5 is the natural home for the `session_executor` one, and the `sdlc_session_ensure.py:776` comment is a comment-only edit that does not violate the No-Go against modifying that file's behavior — say so in the plan so a reviewer does not read it as a #3065 collision. |

---

## Open Questions

**None open.** All three were ruled during critique and the rulings are folded into the plan body.
Recorded here so a later reader sees they were decided, not dropped:

1. **Predicate vs. concrete `session_type` string** → **concrete string, no predicate.** The deciding
   argument is that a predicate makes the **sweep unfalsifiable**: the close-out is a grep returning a
   known count, and a lambda is invisible to it — an equivalent-but-lambda-shaped copy would pass every
   check while re-opening the defect class. See Technical Approach.
2. **Does `tests/unit/test_steering_writer_census.py` need the new keyword registered?** → **No.**
   `_is_resolver_call` (`tests/unit/test_steering_writer_census.py:205-210`) matches solely on
   `value.func.attr in RESOLVER_METHODS` and never inspects `node.value.keywords`. Recorded as an
   answered fact in Test Impact, with the citation, so BUILD does not re-investigate.
3. **Gate-marking comments at the two surviving sweep sites?** → **Yes**, at
   `tools/sdlc_session_ensure.py:776` and `agent/session_executor.py:1373`, with the mandated wording
   *tests one already-resolved row's type; does not choose among rows*. Now a task bullet (tasks 4 and
   5), a Documentation item, a Success Criterion, and a Verification row. The `sdlc_session_ensure.py`
   edit is comment-only and not a #3065 collision.

### Positions this lane will defend on the merits

Recorded so a later round argues the substance rather than re-deriving it. These are defended by the
reasoning below, not by who asked for them:

- **No predicate argument.** It makes the sweep unfalsifiable — see ruling 1. "Abstraction for one use
  case" is the weaker form of the argument and is not the reason.
- **Risk 2's stable-partition test is non-waivable and carries a real `Depends On` edge** (task 2b),
  not a sentence. It is the only thing standing between this refactor and a wedged issue lock: per
  `agent/session_executor.py:305-313`, a wrong return there gets a lapsed lock SET-NX re-acquired
  under a **dead identity** and renewed every tick, wedging the live run's own calls behind
  `ISSUE_LOCKED` until a worker restart. A non-waivable item enforced only by prose is a wish.
- **The fall-through contract is stated in words** because a for-loop-to-helper diff hides the branch
  that disappears. A reviewer cannot see a deleted `if sessions:` in a diff that reads as a tidy
  one-line replacement.
