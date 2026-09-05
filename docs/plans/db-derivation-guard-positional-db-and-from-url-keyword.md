---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2764
last_comment_id: 5277517215
---

# db-derivation guard: positional `db` and keyword `from_url(url=...)` produce no candidate

## Problem

`tests/db_derivation_guard.py` exists so that no test can compute its own Redis
`db=` and issue a destructive command against a database another live pytest
process owns. That defect has been fixed three times (#2117, #2606, #2624) and
re-emerged each time at a call-site shape nobody had enumerated. #2700 answered
that by inverting the polarity: enumerate nothing about *what* is called, flag
every `db=` keyword and every `from_url(...)` argument, and judge the value.

The enumeration moved from callee names to argument-passing *syntax*, and that
second enumeration has two mirror-image holes. Both produce **no candidate at
all**: not a violation, not an accepted site, simply unseen. This is precisely
the failure mode the guard was built to end, reappearing one layer down.

**Current behavior**

```python
redis.Redis("localhost", 6379, 7)                      # -> 0 candidates
redis.StrictRedis("localhost", 6379, 7)                # -> 0 candidates
redis.Redis.from_url(url="redis://localhost:6379/9")   # -> 0 candidates
```

Route 1 (`tests/db_derivation_guard.py:520`) iterates `node.keywords` only, so a
`db` passed at its third positional slot is invisible. Route 2
(`tests/db_derivation_guard.py:567`) is gated on `node.args` being non-empty, so
a `url` passed by keyword is invisible. The two holes are exact mirrors: one
route reads only keywords, the other only positionals.

A third, opposite defect sits on the same surface. Route 1 refuses the
sanctioned fixture parameter when it is used directly:

```python
def test_x(scratch_test_db):
    redis.Redis(db=scratch_test_db)          # -> ok=False, "no local binding"

def test_y(scratch_test_db):
    divergent_db = scratch_test_db
    redis.Redis(db=divergent_db)             # -> ok=True
```

The obvious shape is refused and the indirect one accepted. `CLAIM_FIXTURE_NAMES`'s
own docstring asserts it "mirrors CLAIM_URL_NAMES / the `redis_test_url` leg of
Route 2 exactly", but route 2 carries two legs (an `ast.Call` leg and a bare
`ast.Name` leg) where route 1 carries only the alias hop. The documentation
describes a symmetry the code does not have.

Measured live exposure of all three is **zero** in the tree today. These are
latent gaps that open the moment someone writes a new site in one of the shapes.

**Desired outcome**

Every one of the three shapes above resolves to a candidate with the correct
disposition: the two positional/keyword holes go red exactly as their mirror
already does, and the direct fixture parameter goes green exactly as its alias
already does. Each is pinned by a test that has been shown to fail against the
current implementation before the fix lands. The residual gaps that remain after
this work (the enumerations this guard still cannot avoid) are disclosed in
writing, next to the code that carries them.

## Freshness Check

**Baseline commit:** `67d714662`
**Issue filed at:** 2026-08-13T07:00:12Z (23 days before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**

- `tests/db_derivation_guard.py:520` — route 1 iterates `node.keywords` only — still holds, verbatim (`for kw in node.keywords:`).
- `tests/db_derivation_guard.py:567` — route 2 gated on `node.args` — still holds, verbatim (`if callee == "from_url" and node.args:`).
- The issue body cites "lines ~560-561 at merge" via #2768; the equivalent code is now at 520 and 567. Line numbers drifted, the claims did not.

**Behavioral re-verification (not just line reading).** Driven through
`scan_source` at `67d714662`:

| shape | candidates |
|---|---|
| `redis.Redis("localhost", 6379, 7)` | 0 |
| `redis.StrictRedis("localhost", 6379, 7)` | 0 |
| `redis.Redis.from_url(url="redis://localhost:6379/9")` | 0 |
| control: `redis.Redis(db=7)` | 1 (`db-kwarg`, ok=False, pool_db=7) |
| control: `redis.Redis.from_url("redis://localhost:6379/9")` | 1 (`from-url`, ok=False, pool_db=9) |

The two controls are what make the three zeros meaningful: the scanner is alive
on this input, it simply does not see these shapes.

**Live exposure re-measured** over `tests/**/*.py` at `67d714662`:

- `Redis`/`StrictRedis` with >= 3 positional args: **0** (unchanged from the issue's `25e53671` measurement)
- `from_url(...)` sites: **9**, of which `url=` keyword: **0** (unchanged)

**Tree state:** 23 candidates, 7 violations, 0 undispositioned, 0 stale.

**Cited sibling issues/PRs re-checked:**

- #2655 — CLOSED 2026-08-13T07:26:21Z, resolved by PR #2700. The guard it asked for exists.
- PR #2700 — MERGED 2026-08-13T07:26:20Z. This issue is its round-6 review residue.
- #2768 — CLOSED 2026-08-13T07:30:42Z as **a duplicate of this issue**, folding two additions into this scope. Surfaced by prior-art search, not cited in the issue body. See Prior Art.

**Commits on main since the issue was filed (touching referenced files):**

- `00a3d93ca` *Consolidate the two owners of popoto's test db (#2771) (#3083)* — touched `tests/unit/test_db_derivation_guard.py`, left `scan_source`'s two routes unchanged. Irrelevant to the root cause; confirmed by the behavioral re-verification above, which reproduces all three defects at the current head rather than inferring them from an unchanged diff.

**Active plans in `docs/plans/` overlapping this area:** none. No open plan slug references `db_derivation_guard`; the three matches are all in `docs/archive/plans-completed/`.

**Notes:** One correction worth carrying into Technical Approach. The issue's
suggested fix says to disclose residual gaps in "the module docstring's 'known
cost' paragraph". No such paragraph is in the module docstring. It lives in
`_splat_candidate.__doc__` ("So the enumeration here buys usability at a known
cost: ..."). The plan names the real location and adds a module-level
disclosure rather than pretending one already exists.

## Prior Art

- **#2655 / PR #2700** *Recurrence guard: detect tests that derive their own Redis `db=`* — built the guard this plan repairs, and established its governing idea: enumerating accepted shapes is what let the defect recur, so enumerate nothing about the callee and judge the value instead. Succeeded. This work is its round-6 review residue, deferred rather than blocked because measured exposure was zero.
- **#2768** *db-derivation guard: positional db argument evades the scanner* — **CLOSED as a duplicate of #2764** on 2026-08-13, four minutes after PR #2700 merged. It was filed by the late CRITIQUE of `docs/plans/db-derivation-guard.md` and found the positional half independently. Its closing comment explicitly folds two additions into this issue's record, and they are in scope here:
  1. no criterion pins `format_violation()`'s remedial message content;
  2. `REDIS_CONSTRUCTORS` is an undisclosed residual permit list at the splat layer.
  The issue body of #2764 does not mention either. Without the prior-art search they would have been silently dropped when #2768 was closed.
- **#2628 / PR #2683** *Enforce test-DB ownership so the unit suite stops rotating* — converted the `test_notify_isolation.py` deferred site and introduced `CLAIM_FIXTURE_NAMES` plus the `_resolve_one_hop` structural leg for `divergent_db = scratch_test_db`. Succeeded, and emptied `DEFERRED`. It is the direct ancestor of the third defect in this plan: it added the alias-hop leg and not the direct-use leg.
- **#2606, #2624, #2117** — the three prior point-fixes for cross-process db collision, each at a new call site. They are the reason the guard's polarity is inverted, and the reason a shape that yields *no candidate* is treated here as a defect of the same class rather than a cosmetic gap.
- **#2707** *Steering tests collide across concurrent runs* — adjacent symptom (hardcoded ids on freeform Redis keys under db-claim exhaustion), different mechanism. No overlap in the files this plan touches.
- **Comment 5277517215 on #2764** — a C2 design analysis from the #2655 post-merge verification agent, arguing route 1 should accept a *function parameter with no local rebinding* rather than an identifier match on a bare `ast.Name`, because a literal identifier match would launder `test_db = 7; db=test_db`. That analysis predates #2628's `CLAIM_FIXTURE_NAMES` landing. Re-probed at `67d714662`: the shipped leg does **not** launder (see Spike Results, spike-4), because the sanctioned identifier is the reserved `scratch_test_db`, not an ordinary local. The warning stays live as a design constraint on the fix, not as a description of a current defect.

## Research

**Queries used:**

- `redis-py Redis.__init__ positional arguments host port db signature deprecation keyword-only`

**Key findings:**

- redis-py has never deprecated positional `host`/`port`/`db` on `Redis.__init__`; positional construction remains valid and in use in the wild. Source: [redis-py CHANGES](https://github.com/redis/redis-py/blob/master/CHANGES), corroborated by [cunla/fakeredis-py#36](https://github.com/cunla/fakeredis-py/issues/36), which treats positional host/port as "a perfectly valid and functional way of instantiating Redis". This is what makes the positional hole worth closing rather than dismissing as a shape nobody writes.
- redis-py **has** reshuffled argument conventions before, and `StrictRedis.__init__` and `StrictRedis.from_url` have diverged in the keyword arguments they accept between 2.7 and 2.10. Source: [redis/redis-py#510](https://github.com/redis/redis-py/issues/510). This is the finding that shapes the approach: a guard that hardcodes `node.args[2]` is correct today and silently wrong after any future signature reshuffle, and silent wrongness is the exact failure mode this guard exists to prevent. The fix therefore derives the index from `inspect.signature(redis.Redis.__init__)` in a **test**, so a reshuffle turns the suite red instead of quietly reopening the hole. The scanner itself stays a pure-AST module with no `redis` import.
- Locally confirmed against the installed `redis-py 7.4.0`: `Redis.__init__` parameters are `(self, host, port, db, password, socket_timeout, ...)`, so `db` is positional index 2 after `self`; `from_url` is `(url, **kwargs)`, so `url=` by keyword is legal; and `redis.StrictRedis is redis.Redis`, so the two names cannot diverge in this version and both must be matched by terminal name regardless.

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Why Previous Fixes Failed

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

_placeholder_
