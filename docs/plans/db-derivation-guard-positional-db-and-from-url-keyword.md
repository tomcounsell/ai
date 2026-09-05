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

All five spikes were run as `code-read` / local-prototype probes against
`67d714662` in a scratch interpreter. No repo edits, nothing committed. Every
one is reproducible by pasting its snippet into `.venv/bin/python`.

### spike-1: Do both holes still reproduce, and is the scanner alive on the input?
- **Assumption**: "The two gaps described in the issue are still present in shipped source."
- **Method**: prototype (drive `scan_source` on five snippets, three defective and two controls)
- **Finding**: Confirmed. The three defective shapes yield 0 candidates each; both controls yield 1 candidate with correct `pool_db`. The controls are load-bearing: without them, three zeros are equally consistent with "the scanner is broken on synthetic input".
- **Confidence**: high
- **Impact on plan**: The premise holds; no rescope needed. The control-pairing discipline carries into the Verification table, where each new red row is paired with the already-passing mirror it is modeled on.

### spike-2: Is `db` really positional index 2, and is that index safe to hardcode?
- **Assumption**: "`db` is the third positional parameter of `redis.Redis.__init__`."
- **Method**: code-read (`inspect.signature`) plus web-research
- **Finding**: Confirmed for `redis-py 7.4.0`: `(self, host, port, db, password, ...)`, and `redis.StrictRedis is redis.Redis`. But redis-py has reshuffled argument conventions before (redis/redis-py#510), so the index is a fact about the *installed library*, not a permanent truth.
- **Confidence**: high
- **Impact on plan**: Decisive. The scanner keeps `node.args[2]` as a module-level named constant and stays a pure-AST module with no `redis` import; a **test** imports `redis`, reads `inspect.signature(redis.Redis.__init__)`, and asserts the constant still names the `db` parameter. A future signature reshuffle then goes red instead of silently reopening the hole. Hardcoding the 2 without that test would reproduce this guard's own founding failure mode one level up.

### spike-3: Does adding a third `kind` break `format_violation`?
- **Assumption**: "A new candidate kind flows through the reporting path unchanged."
- **Method**: prototype (construct a `Candidate(kind="db-positional", expr="7", callee="Redis")` and format it)
- **Finding**: **Refuted.** `format_violation` is a two-way branch: `db-kwarg` or *else* `from_url(...)`. The probe printed `tests/x.py:3: from_url(7)` for a positional `Redis` site. Any new kind is silently mislabeled as a `from_url` violation.
- **Confidence**: high
- **Impact on plan**: `format_violation` must gain an explicit branch per kind, and the `else` fallback must stop being an implicit `from_url`. This dovetails with #2768's folded-in item that no criterion pins the remedial message content: the message assertions land in the same task.

### spike-4: Does the shipped `CLAIM_FIXTURE_NAMES` leg launder a pool slot?
- **Assumption**: "Comment 5277517215's laundering warning describes a live defect."
- **Method**: prototype (four probes through `scan_source`)
- **Finding**: **Refuted as a current defect, retained as a constraint.** `scratch_test_db = 7`, `test_db = 7`, and a `scratch_test_db=7` default argument each go red. The leg is sound because the sanctioned identifier is the reserved `scratch_test_db`, not an ordinary local. The warning correctly forbids widening the leg to arbitrary identifiers.
- **Confidence**: high
- **Impact on plan**: No laundering fix is needed, which removes the largest speculative chunk of scope. The four probes are added to the test file as standing anti-regression rows so the next widening attempt is caught.

### spike-5: Is the direct fixture-parameter refusal real, and is the tree clean?
- **Assumption**: "Route 1 accepts `db=scratch_test_db` used directly, as its docstring implies."
- **Method**: prototype (`scan_source` on the direct form and the alias form; then `apply_dispositions(scan_tree())`)
- **Finding**: **Refuted.** Direct use returns `ok=False, "'scratch_test_db' has no local binding in the enclosing function"`; the alias hop returns `ok=True`. The tree itself is clean (23 candidates, 7 violations, 0 undispositioned, 0 stale), so this is a false positive with zero live exposure, not a red suite.
- **Confidence**: high
- **Impact on plan**: Adds a third fix to scope, deliberately. It is one leg on the same route as fix 1, it makes an existing docstring claim true instead of leaving it false, and leaving it would ship a guard whose next author hits a false positive on the most obvious correct spelling. Flagged in Open Questions since it is not in the issue body.

## Data Flow

The guard is a single-process static scan with no I/O beyond reading `.py`
files. The flow below is the path a single call site takes from source text to
a failed assertion, annotated with where each of the three defects sits.

1. **Entry point**: `tests/unit/test_db_derivation_guard.py::test_no_test_derives_its_own_redis_db` calls `scan_tree()`.
2. **`scan_tree`**: walks `tests/**/*.py`, reads each file, calls `scan_source(source, rel_path)`. A `SyntaxError` propagates deliberately.
3. **`scan_source`**: `ast.parse` → `_parent_map` → `_LocalBindings().visit` → `ast.walk`, filtering to `ast.Call`, and resolving `callee = _terminal_name(node.func)`.
4. **Route 1** (`for kw in node.keywords`): splat leg (`kw.arg is None`) → `_splat_candidate`; `db=` leg → value judged by `_is_claim_call` / `_resolve_one_hop` / `_first_pool_db`. **Defect 1 lives here**: `node.args` is never consulted, so a positional `db` never enters this stage. **Defect 3 lives here**: an `ast.Name` that is a sanctioned fixture parameter is sent straight to `_resolve_one_hop`, which finds no local binding and refuses it.
5. **Route 2** (`if callee == "from_url" and node.args`): `node.args[0]` judged by `CLAIM_URL_NAMES` (two legs, `ast.Call` and bare `ast.Name`) or `_url_db`. **Defect 2 lives here**: the `and node.args` gate drops the whole call when `url` arrived by keyword.
6. **`ScanResult.candidates`** accumulates; `.violations` is the `ok=False` subset.
7. **`apply_dispositions`**: matches each violation against `ALLOWLIST + DEFERRED` by `_matches` (path plus `ast.unparse` of the expression, **not** kind), refusing any `ALLOWLIST` cover for a candidate whose `pool_db` is set. Returns `(undispositioned, stale)`.
8. **`format_violation`**: renders each undispositioned violation. **Spike-3's defect lives here**: the two-way `kind` branch mislabels anything that is not `db-kwarg` as a `from_url` site.
9. **Output**: the assertion message in the failing test, read by the author who wrote the offending line.

The three fixes all land between steps 4 and 5, and one message repair lands at
step 8. Nothing upstream of step 3 or downstream of step 9 changes.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2117 | Fixed one cross-process db collision at the site that flaked | Point fix at a single call site. The next site was written in a shape the fix did not cover. |
| #2606 | Repaired shared-state leaks, added a db-claim guard | Enumerated the *accepted* constructor names. Anything unenumerated passed silently, so #2628 found the suite still rotating. |
| #2624 / #2628 / PR #2683 | Enforced test-DB ownership; added `CLAIM_FIXTURE_NAMES` and the `_resolve_one_hop` alias leg | Correct as far as it went, but added only the alias-hop leg for the sanctioned fixture and not the direct-use leg, while writing a docstring claiming it mirrored route 2 "exactly". That gap is defect 3 in this plan. |
| #2655 / PR #2700 | Inverted the polarity: judge every `db=` value, callee-agnostic | Replaced an enumeration of callee *names* with an enumeration of argument-passing *syntax*, and did not enumerate the mirror of each route it wrote. Route 1 reads keywords and not positionals; route 2 reads positionals and not keywords. Defects 1 and 2. |
| #2768 | Filed the positional half independently, from the late plan critique | Never implemented. Closed as a duplicate of #2764 four minutes after PR #2700 merged, folding two additions in by comment. Being closed-as-duplicate is exactly how those two additions came within one prior-art search of vanishing. |

**Root cause pattern.** Every round has closed the hole it could see and left the
symmetric hole it could not. The recurring mechanism is not carelessness about
Redis; it is that each fix enumerates one axis and treats the enumeration as
exhaustive. #2700's own docstring is candid that enumerating accepted shapes was
what let the defect recur, then enumerates argument syntax without pairing each
route with its mirror. The countermeasure this plan adopts is narrow and
checkable: **for every route, state in the code which argument positions it
reads, and pin the mirror shape with a demonstrated-red test.** Where an
enumeration genuinely cannot be avoided (the callee scoping on the positional
leg and the splat leg), disclose it in prose next to the code, so the next
author inherits a known boundary rather than an assumed guarantee.

## Architectural Impact

- **New dependencies**: none in the scanner. `tests/db_derivation_guard.py` stays a pure-AST module importing only stdlib plus `tests.db_claim._TEST_DB_POOL_MAX`. The test file gains an `import redis` and an `import inspect` for the signature-pin assertion (spike-2); `redis` is already a hard dependency of the suite.
- **Interface changes**: `Candidate.kind` gains a third value, `"db-positional"`. Its docstring comment (`# "db-kwarg" | "from-url"`) is part of the contract and must be updated with it. `format_violation` gains an explicit per-kind branch. `scan_source` returns candidates for three previously-silent shapes; no signature changes anywhere.
- **Coupling**: unchanged for the scanner. It rises slightly in the test file, which now knows about `redis.Redis.__init__`'s parameter order. That coupling is the point: it is the tripwire that converts a future redis-py reshuffle from a silent hole into a red test.
- **Data ownership**: unchanged. No Popoto model, no Redis write, no schema. The Popoto migration requirement in `docs/sdlc/do-plan.md` does not apply.
- **Reversibility**: high. Every change is additive within two files and revertable by a single `git revert`. The one irreversible-feeling risk is the opposite of a rollback problem: if the new legs produce false positives on the live tree the suite goes red immediately and loudly, which is the desired failure direction.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue plus the two items folded in from #2768; the one judgment call is in Open Questions)
- Review rounds: 1

Two files, no runtime code, no deploy surface, no live call sites to convert.
The work is small in edits and demanding in evidence: this guard's entire value
is that it fires, so every new leg needs a demonstrated-red test and every
existing green needs to stay green. Budget the time in proving the tests bite,
not in writing the AST branches.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `redis` importable with a readable `Redis.__init__` signature | `python -c "import inspect, redis; assert list(inspect.signature(redis.Redis.__init__).parameters)[3] == 'db'"` | The positional index the new leg reads, and the pin test that guards it |
| Guard module imports and the tree is clean | `python -c "from tests.db_derivation_guard import scan_tree, apply_dispositions; r,s=apply_dispositions(scan_tree()); assert not r and not s"` | Establishes the green baseline the fix must preserve |
| Claim-pool ceiling importable | `python -c "from tests.db_claim import _TEST_DB_POOL_MAX; assert _TEST_DB_POOL_MAX >= 1"` | `_first_pool_db` bounds depend on it |

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
