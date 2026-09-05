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

### Key Elements

- **Route 1 positional leg**: makes a `db` at the third positional slot of a Redis construction a candidate, judged by exactly the same value rules as a written-out `db=`.
- **Route 2 keyword leg**: makes a `url=` keyword on `from_url` a candidate, judged by exactly the same URL rules as a positional URL.
- **Route 1 direct-fixture leg**: accepts a bare `ast.Name` that is a sanctioned fixture parameter, making `CLAIM_FIXTURE_NAMES` mirror `CLAIM_URL_NAMES` for real rather than in docstring only.
- **Per-kind violation message**: `format_violation` renders each of the three kinds explicitly, and the remedial text is pinned by assertion rather than left to review.
- **Residual-gap disclosure**: a module-level paragraph naming, in one place, every enumeration the guard still carries and what each one costs.
- **Signature tripwire**: a test that re-derives the `db` parameter index from `inspect.signature(redis.Redis.__init__)`, so a redis-py reshuffle turns the suite red rather than quietly reopening the positional hole.

### Flow

Author writes a Redis construction in `tests/` → `scan_tree()` walks it during the unit suite → the call reaches route 1 or route 2 → **every argument-passing shape now yields a candidate** → an unsanctioned value becomes a violation → `format_violation` names the file, the line, the shape, the db it provably takes, and the sanctioned alternative → the author fixes the line or writes a disposition.

The change is entirely in the third step. Today three shapes fall out of the flow between "walks it" and "yields a candidate" and are never heard from again.

### Technical Approach

**Fix 1: positional `db` (route 1).** After the `node.keywords` loop, add a
positional leg. It is scoped to `Redis`/`StrictRedis` by terminal name, because
unlike a `db=` keyword a bare third positional argument means nothing without
knowing the callee: `some_helper("a", "b", 7)` is not a db. That callee scoping
is the plan's **second deliberate, bounded exception** to the module's
callee-agnostic polarity (the first is the opaque-splat leg), and it must be
disclosed as such rather than slipped in.

- The index lives in a module-level named constant, `REDIS_DB_POSITIONAL_INDEX = 2`, not as a bare `2` inside the walk. The name is what makes the pin test legible.
- Guard on `len(node.args) > REDIS_DB_POSITIONAL_INDEX`.
- Emit **only when no `db=` keyword is present on the same call**. `Redis("h", 6379, 7, db=8)` is a `TypeError` at runtime and cannot be a live site; emitting twice for it would produce a duplicate violation for an unrunnable line and muddy the message.
- Judge the value through the same helpers as the keyword leg (`_is_claim_call`, `_resolve_one_hop`, `_first_pool_db`), so a positional `claim_test_db()` is accepted and a positional literal is flagged with its `pool_db` set. Sharing the judgment is the point: a separate judgment path would drift.
- New `kind`: `"db-positional"`.

**Fix 2: `url=` keyword (route 2).** Replace the `and node.args` gate with a
resolution step that yields the URL argument from either position:
`node.args[0]` when present, otherwise the `url=` keyword's value, otherwise no
candidate. Prefer the positional when both are somehow present, for the same
single-candidate reason as fix 1. Everything downstream (the `CLAIM_URL_NAMES`
call leg, the fixture-parameter leg, `_url_db`, `pool_db`) is untouched, and the
`kind` stays `"from-url"` because the shape being reported is identical.

**Fix 3: direct fixture parameter (route 1).** In the `isinstance(value, ast.Name)`
branch, accept before resolving when `value.id in CLAIM_FIXTURE_NAMES`, with
detail `"claim-API fixture parameter"`. This is the exact structural twin of
route 2's `isinstance(arg, ast.Name) and arg.id in CLAIM_URL_NAMES` leg.

The laundering constraint from comment 5277517215 governs this leg and is
satisfied by construction: the accepted identifier set is the reserved
`CLAIM_FIXTURE_NAMES`, never an arbitrary name, so `test_db = 7; db=test_db`
stays red (spike-4). Ordering matters and must be got right: the fixture check
runs **before** `_resolve_one_hop`, and a locally rebound `scratch_test_db = 7`
must still be refused. Both directions get a test.

**Fix 4: `format_violation` per kind (from #2768).** Replace the two-way branch
with an explicit mapping over the three kinds. The `else` branch stops meaning
`from_url`; an unrecognized kind renders its own name rather than impersonating
another shape, so the next kind added fails visibly. Assertions pin the remedial
sentence (that it names `claim_test_db()`, `redis_test_url()`, and both
disposition tables), which is #2768's first folded-in item.

**Fix 5: residual-gap disclosure (from #2768).** The issue asks for this in "the
module docstring's known cost paragraph". That paragraph is in
`_splat_candidate.__doc__`, not the module docstring (see Freshness Check).
Add a module-docstring section, **What this guard still cannot see**, listing
each residual gap in one place:

1. A Redis client constructed through an alias outside `REDIS_CONSTRUCTORS` and receiving an opaque `**` splat with no visible `"db"` key.
2. A `db` passed positionally to a constructor alias outside `REDIS_CONSTRUCTORS` (new with fix 1, and the honest cost of its callee scoping).
3. A `db` computed inside a helper the guard cannot see through, more than one binding hop from the call site.
4. `_matches` disposition matching is per-file-per-expression and kind-agnostic, so one `ALLOWLIST` entry can cover the same expression across kinds. Bounded by the db-0-only invariant and by `apply_dispositions`'s refusal to let `ALLOWLIST` cover any candidate with a `pool_db`.

`REDIS_CONSTRUCTORS` is named there as the residual permit list it is, which is
#2768's second folded-in item.

**Fix 6: signature tripwire.** In the test file, assert
`list(inspect.signature(redis.Redis.__init__).parameters)[REDIS_DB_POSITIONAL_INDEX + 1] == "db"`
(the `+ 1` skips `self`) and assert `redis.StrictRedis is redis.Redis` or, if
they ever diverge, that both still take `db` at that index. The scanner keeps no
`redis` import; only the test knows about the library.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] No `except Exception: pass` blocks exist in `tests/db_derivation_guard.py`. The module's one deliberate exception behavior is the opposite: `scan_source` lets `SyntaxError` propagate rather than skipping an unparseable file, pinned by `test_unparseable_source_raises_rather_than_being_skipped`. That test must still pass unchanged.
- [ ] `_url_db` returns `None` rather than raising on a non-string or unparseable node; the new keyword leg routes through it, so the "URL is not `redis_test_url()` and its db cannot be determined" path is asserted for `from_url(url=cfg.url)`.
- [ ] No new `try`/`except` is introduced by any of the six fixes. If a builder finds one necessary, that is a signal the approach drifted and belongs in review, not in a swallowed handler.

### Empty/Invalid Input Handling

- [ ] `Redis()` with zero positional args and no `db=`: the positional leg's `len(node.args) > REDIS_DB_POSITIONAL_INDEX` guard must not raise `IndexError`. Asserted directly.
- [ ] `Redis("h")` and `Redis("h", 6379)`: fewer than three positionals, no candidate, no exception.
- [ ] `from_url()` with neither a positional nor a `url=` keyword: no candidate, no exception (invalid Python at runtime, but the scanner must survive parsing it).
- [ ] `from_url(**kw)` with no visible `url`: no `from-url` candidate; the splat leg is what fires, and its existing behavior is unchanged.
- [ ] `Redis(*args)` (a starred positional, which parses as `ast.Starred`): must not be read as a `db` value. Asserted so the leg cannot mistake an unpack for a literal.
- [ ] Empty source, comment-only source, and a file with no calls: `scan_source` returns zero candidates, as today.

### Error State Rendering

- [ ] The user-visible output of this feature **is** the violation message. Each of the three kinds gets an assertion that its first line names the right shape: `db-positional` must not render as `from_url(...)` (the exact defect spike-3 measured).
- [ ] Each kind's message must name the file and a non-zero line number, matching the existing `test_planted_offender_goes_red` contract.
- [ ] The `pool_db` sentence ("This provably names db N, inside the claimable pool") must appear for a positional literal in the pool, and must be absent when `pool_db` is `None`.
- [ ] The remedial sentence must name `claim_test_db()`, `redis_test_url()`, and both disposition tables, for every kind. This is #2768's unpinned-message item, and it is asserted rather than reviewed.

## Test Impact

All impact is in one file. No test outside `tests/unit/test_db_derivation_guard.py`
imports the guard, and no live call site changes, so nothing else can break.

- [ ] `tests/unit/test_db_derivation_guard.py::PLANTED_OFFENDERS` — UPDATE: add four rows (positional `db` on `redis.Redis`, positional `db` on `redis.StrictRedis`, `from_url(url=...)` with a pool-db literal, `from_url(url=cfg.url)` unparseable). Each must be shown red against the pre-fix implementation before the fix lands.
- [ ] `tests/unit/test_db_derivation_guard.py::test_sanctioned_shapes_are_accepted` — UPDATE: add the positional claim call (`redis.Redis("localhost", 6379, claim_test_db())`), the keyword sanctioned URL (`from_url(url=redis_test_url())`), and the direct fixture parameter (`db=scratch_test_db`). The last is currently red and is fix 3's demonstrated-red evidence.
- [ ] `tests/unit/test_db_derivation_guard.py::test_guard_sees_a_non_zero_number_of_candidates` — UPDATE: extend the floor assertions to cover `db-positional` without requiring a live site. The tree has zero positional sites, so the floor for that kind is 0 and the non-vacuity evidence has to come from the planted rows, not from the tree. State that in the test's docstring so a future reader does not "fix" it by asserting a floor that can never be met.
- [ ] `tests/unit/test_db_derivation_guard.py::test_no_test_derives_its_own_redis_db` — UPDATE (assertion unchanged, must stay green): the three new legs must add zero violations to the live tree. If this goes red, a new leg has a false positive and the fix is wrong.
- [ ] `tests/unit/test_db_derivation_guard.py::test_no_stale_disposition_entries` — UPDATE (assertion unchanged, must stay green): the new legs must not orphan any of the four `ALLOWLIST` entries, and must not silently absorb one either. `_matches` is kind-agnostic, so a new-kind candidate on the same `(path, expr)` could consume an entry; asserted explicitly rather than assumed.
- [ ] `tests/unit/test_db_derivation_guard.py::TestSplatHandling` — UPDATE: add one case proving the positional leg and the splat leg compose (`redis.Redis("h", 6379, 7, **kw)` yields both violations, not one that swallows the other).
- [ ] `tests/unit/test_db_derivation_guard.py` — UPDATE: new test for the signature tripwire (fix 6), new tests for the four spike-4 laundering probes, new tests for the empty/invalid inputs listed in Failure Path Test Strategy, and new per-kind message assertions (fix 4).
- [ ] `tests/unit/test_db_derivation_guard.py::test_every_redis_construction_in_the_tree_is_attribute_qualified` — no change. It measures callee node kinds, which this work does not touch.

No expected-failure markers are affected: `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` returns nothing across the whole suite, so there is no xfail to convert.

## Rabbit Holes

- **Making the positional leg callee-agnostic.** It is the module's stated ideal and it is wrong here. A third positional argument has no meaning without the callee; the callee-agnostic version of the splat leg produced 183 violations across 100+ unrelated files, and a guard that fires on every test helper gets deleted rather than fixed. Scope it to `Redis`/`StrictRedis`, disclose the cost, and move on.
- **Widening `REDIS_CONSTRUCTORS` to catch more aliases.** Every name added is a guess about the future and buys nothing measurable: the tree has zero non-`redis.Redis` constructions. The disclosure paragraph is the deliverable here, not a longer list.
- **Widening `CLAIM_FIXTURE_NAMES` to "any function parameter with no local rebinding".** Comment 5277517215 argues for it, and it is the single largest trap in this plan. Spike-4 measured the shipped leg and it does not launder, so the widening buys no correctness and costs the reserved-identifier property that makes the leg safe. Add the direct-use leg for the reserved names only.
- **Making `_matches` kind-aware.** It would require adding a `kind` field to all four `ALLOWLIST` entries and to `Exemption`, for a collision that cannot happen while `ALLOWLIST` is db-0-only and `apply_dispositions` refuses to cover a `pool_db` candidate. Disclose the property, do not restructure the dataclass.
- **Teaching `_resolve_one_hop` a second hop.** Multi-hop resolution is a general dataflow problem, unbounded in effort, and orthogonal to the three argument-shape holes this plan closes. Listed as residual gap 3 instead.
- **Reformatting or re-organizing the guard while in there.** The module is dense, heavily commented, and every comment is load-bearing history. Its docstring already records one case where a maintainer who trusted a wrong comment would have deleted the only thing holding the invariant up. Touch the six places and nothing else.
- **Converting live call sites to "exercise" the new legs.** There are no positional or `url=` sites and there is no reason to manufacture one. Non-vacuity comes from planted offenders, which is how every other leg in this guard is proven.

## Risks

### Risk 1: A new leg has a false positive and turns the live suite red

**Impact:** `test_no_test_derives_its_own_redis_db` fails across the tree, blocking every lane on the machine, not just this one. Fix 3 is the specific worry: it is the one change that moves a site from red to green by *accepting* a shape, and an over-broad accept is worse than a false positive because it fails silently in the safe direction.

**Mitigation:** Run `apply_dispositions(scan_tree())` before and after and assert the violation and stale counts are identical (7 and 0). Pair fix 3's accept test with the four spike-4 refusal probes in the same commit, so the widening and its bound land together. Both counts are Verification rows.

### Risk 2: A test is written that cannot fail

**Impact:** The worst outcome available here. A guard test that passes without reaching the new code leaves the hole open and adds a green check that says otherwise, which is exactly how #2700 shipped these two holes in the first place.

**Mitigation:** Mutation-check each leg individually, not the file as a whole. For each of the six fixes, revert that one hunk, watch its specific test go red, restore, and re-measure. A whole-file revert proves only that some test somewhere bites. `source_fingerprint()` exists for the revert check and should be used.

### Risk 3: The positional index silently stops meaning `db`

**Impact:** A redis-py upgrade reshuffles `Redis.__init__`, `node.args[2]` starts naming `password` or something else, and the guard reports confidently about the wrong argument. Silent wrongness in a guard is worse than no guard.

**Mitigation:** Fix 6's signature tripwire, derived from `inspect.signature` at test time rather than restated as a comment. redis/redis-py#510 shows the divergence is not hypothetical.

### Risk 4: `format_violation`'s new branch mislabels a kind

**Impact:** An author reads a message describing the wrong shape and edits the wrong line. Spike-3 measured this already happening for any non-`db-kwarg` kind.

**Mitigation:** An explicit per-kind assertion for all three kinds, plus an unknown-kind case asserting the fallback renders the kind name rather than impersonating `from_url`.

### Risk 5: Scope creep from fix 3

**Impact:** Fix 3 is not in the issue body. It arrived from a plan-time probe, and an unreviewed addition to a safety guard is how guards acquire holes.

**Mitigation:** It is isolated to one `if` in one branch, it makes an existing docstring claim true, and it is called out in Open Questions so it can be dropped in critique without disturbing fixes 1, 2, 4, 5, and 6. If it is dropped, the `CLAIM_FIXTURE_NAMES` docstring's "mirrors ... exactly" sentence must be corrected in the same pass rather than left standing as a false claim.

## Race Conditions

No race conditions identified. The guard is a synchronous, single-threaded
static scan: it reads `.py` files, parses them with `ast`, and returns a list.
It opens no socket, holds no lock, touches no Redis, and shares no mutable state
across processes. Nothing in this plan changes that.

Worth stating explicitly, because the *subject matter* is cross-process db
collision and the reflex is to look for one: the guard exists to prevent a race
between pytest processes. It does not participate in one. The claim protocol it
protects (`flock` over `[1..TEST_DB_POOL_MAX]` in `tests/db_claim.py`) is
untouched by this work.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

The two additions folded in from #2768 when it was closed as a duplicate of
#2764 (pinning `format_violation()`'s remedial message content, and disclosing
`REDIS_CONSTRUCTORS` as a residual permit list) are in scope as fixes 4 and 5
rather than left to a successor issue. The design avenues that are deliberately
**not taken** are recorded in Rabbit Holes with the measurement that rules each
one out; they are rejected approaches, not deferred work, and none of them is
a promise to anyone.

## Update System

No update system changes required. Both changed files live under `tests/` and
are exercised only by the unit suite. Nothing is imported by `bridge/`,
`worker/`, `agent/`, or `tools/`; no new dependency, config file, or entry point
is added; `scripts/remote-update.sh` and the `/update` skill need no change; and
there is no state on any machine to migrate.

Fleet-wide effect is limited to this: after `/update` pulls the change, a
machine whose `tests/` grows a call site in one of the three shapes will see the
unit suite fail where it previously passed silently. That is the intended
behavior of the change and needs no propagation step of its own.

## Agent Integration

No agent integration required. This is a test-suite-internal change with no
runtime surface.

- No new CLI entry point in `pyproject.toml [project.scripts]`. The guard is invoked by pytest collection, never by the agent's Bash tool.
- `bridge/telegram_bridge.py` does not and must not import `tests.db_derivation_guard`; `tests/` is not on the bridge's import path.
- No MCP server or `.mcp.json` change.
- The agent already reaches this code the only way it needs to: by running `scripts/pytest-clean.sh tests/unit/test_db_derivation_guard.py`, which is unchanged.

## Documentation

### Feature Documentation

- [ ] No new file in `docs/features/`. The guard has no feature doc today and this work does not create the need for one: it repairs three legs of an existing internal test guard, adds no user-facing or agent-facing behavior, and the guard's own module docstring is deliberately the source of truth for how it works and what it cannot see. Creating a thin `docs/features/db-derivation-guard.md` that restates the docstring would create a second place to drift, which is the failure this module's own history warns about.
- [ ] No entry in `docs/features/README.md` index table, for the same reason.

### Inline Documentation

- [ ] Add a **What this guard still cannot see** section to the module docstring of `tests/db_derivation_guard.py`, enumerating the four residual gaps listed in Technical Approach fix 5, and naming `REDIS_CONSTRUCTORS` as the residual permit list it is (#2768's second folded-in item).
- [ ] Update the `_splat_candidate` docstring's existing "known cost" paragraph to cross-reference the new module-level section rather than remaining the only place a residual gap is disclosed.
- [ ] Update the `Candidate.kind` comment (`# "db-kwarg" | "from-url"`) to include `"db-positional"`. It is a contract comment, not decoration.
- [ ] Update the `CLAIM_FIXTURE_NAMES` docstring: its "mirrors CLAIM_URL_NAMES / the `redis_test_url` leg of Route 2 exactly" claim becomes true when fix 3 lands. If fix 3 is dropped in critique, correct the sentence instead of leaving a false claim standing.
- [ ] Add a comment at `REDIS_DB_POSITIONAL_INDEX` naming the test that pins it to `inspect.signature`, so a reader who wants to change the constant finds the tripwire.
- [ ] Update the module docstring's route description to state, per route, which argument positions it reads. The current text says the guard flags "every `db=` keyword argument" and "every `from_url(...)` argument"; that phrasing is what made both holes invisible to review.

### External Documentation Site

- [ ] Not applicable. This repo has no Sphinx/MkDocs site.

## Success Criteria

- [ ] `redis.Redis("h", 6379, 7)` and `redis.StrictRedis("h", 6379, 7)` each yield exactly one violation naming db 7.
- [ ] `redis.Redis.from_url(url="redis://localhost:6379/9")` yields exactly one violation naming db 9, matching what the positional form already yields.
- [ ] `redis.Redis(db=scratch_test_db)` used directly on the fixture parameter yields zero violations, matching the aliased form.
- [ ] `redis.Redis("h", 6379, claim_test_db())` and `from_url(url=redis_test_url())` yield zero violations.
- [ ] The four laundering probes stay red: `scratch_test_db = 7`, `test_db = 7`, a `scratch_test_db=7` default argument, and any non-`CLAIM_FIXTURE_NAMES` identifier.
- [ ] The live tree is unchanged: 0 undispositioned violations and 0 stale disposition entries, before and after.
- [ ] A positional violation's message names the `Redis(...)` shape and its db, and does not render as `from_url(...)` (spike-3's measured defect).
- [ ] Every violation message, for all three kinds, names `claim_test_db()`, `redis_test_url()`, and both disposition tables (#2768's unpinned-message item).
- [ ] `REDIS_DB_POSITIONAL_INDEX` is pinned to `inspect.signature(redis.Redis.__init__)` by a test, and `tests/db_derivation_guard.py` still imports no `redis`.
- [ ] The module docstring carries a **What this guard still cannot see** section naming all four residual gaps and `REDIS_CONSTRUCTORS` (#2768's disclosure item).
- [ ] Each of the six fixes is mutation-checked individually: revert that hunk alone, its own test goes red, restore, re-measure. A whole-file revert is not accepted as evidence.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions needed: `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` returns nothing across the suite.

## Team Orchestration

The lead agent orchestrates and does not build directly. This is a small,
single-file-pair change with one dominant risk (a test that cannot fail), so the
team is deliberately shaped as one builder plus a reviewer whose only job is
mutation evidence.

### Team Members

- **Builder (guard)**
  - Name: `guard-builder`
  - Role: Implements all six fixes in `tests/db_derivation_guard.py` and their tests in `tests/unit/test_db_derivation_guard.py`. Owns both files exclusively for the duration.
  - Agent Type: `builder`
  - Domain: Redis/Popoto data, plus AST/static-analysis care
  - Resume: true

- **Validator (mutation)**
  - Name: `mutation-validator`
  - Role: Read-only. For each of the six fixes independently, reverts that single hunk, confirms the fix's own test goes red, restores, and re-measures. Reports per-fix, never in aggregate.
  - Agent Type: `validator`
  - Resume: true

- **Reviewer (guard semantics)**
  - Name: `guard-reviewer`
  - Role: Reviews the two callee-scoping exceptions and the `CLAIM_FIXTURE_NAMES` widening against the laundering constraint in comment 5277517215. Confirms the disclosure paragraph matches what the code actually does.
  - Agent Type: `code-reviewer`
  - Resume: true

- **Documentarian**
  - Name: `guard-documentarian`
  - Role: Docstring work only (fix 5 and the Documentation checklist). No behavior changes.
  - Agent Type: `documentarian`
  - Resume: true

**Worktree ownership note.** `mutation-validator` reverts hunks in the working
tree. It must run in its own worktree, never in `guard-builder`'s, or the two
corrupt each other's measurements in both directions.

## Step by Step Tasks

### 1. Capture the red state

- **Task ID**: `capture-red`
- **Depends On**: none
- **Validates**: none (measurement only)
- **Informed By**: spike-1, spike-3, spike-5
- **Assigned To**: `guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Run every command in the Verification table against unmodified `main` and record the output verbatim.
- Confirm the three defect rows are red (`POSITIONAL 0`, `URLKW 0`, `FIXTURE 1`) and the four invariant rows are green.
- Paste this block into the PR description as the red-state proof. A fix with no recorded red state is not accepted.

### 2. Route 1 positional `db` leg

- **Task ID**: `build-positional`
- **Depends On**: `capture-red`
- **Validates**: `tests/unit/test_db_derivation_guard.py` (planted rows `positional-db-redis`, `positional-db-strictredis`; sanctioned row `positional-claim-call`)
- **Informed By**: spike-2 (index 2 confirmed against redis-py 7.4.0, but derive it in a test, do not trust it forever), spike-1
- **Assigned To**: `guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `REDIS_DB_POSITIONAL_INDEX = 2` as a module-level constant with a comment naming its pin test.
- In `scan_source`, after the `node.keywords` loop, add a positional leg scoped to `Redis`/`StrictRedis` by terminal name, guarded on `len(node.args) > REDIS_DB_POSITIONAL_INDEX`, emitting only when no `db=` keyword is present on the same call.
- Judge the value through `_is_claim_call` / `_resolve_one_hop` / `_first_pool_db`, sharing the keyword leg's judgment rather than duplicating it.
- Emit `kind="db-positional"` and update the `Candidate.kind` contract comment.
- Reject `ast.Starred` at that index rather than reading it as a value.
- Add the empty/invalid input cases from Failure Path Test Strategy (`Redis()`, `Redis("h")`, `Redis("h", 6379)`, `Redis(*args)`).

### 3. Route 2 `url=` keyword leg

- **Task ID**: `build-url-keyword`
- **Depends On**: `capture-red`
- **Validates**: `tests/unit/test_db_derivation_guard.py` (planted rows `url-keyword-pool-literal`, `url-keyword-unparseable`; sanctioned row `url-keyword-claim-call`)
- **Informed By**: spike-1
- **Assigned To**: `guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Replace the `if callee == "from_url" and node.args:` gate with a resolution that yields the URL argument from `node.args[0]` when present, otherwise from a `url=` keyword, otherwise no candidate.
- Prefer the positional when both appear, so the call yields one candidate rather than two.
- Leave `kind="from-url"`, `_url_db`, `CLAIM_URL_NAMES`, and the `pool_db` logic untouched; the shape being reported is identical.
- Cover `from_url()` with neither argument and `from_url(**kw)` with no visible `url`: no candidate, no exception.

### 4. Route 1 direct fixture-parameter leg

- **Task ID**: `build-fixture-leg`
- **Depends On**: `capture-red`
- **Validates**: `tests/unit/test_db_derivation_guard.py` (sanctioned row `direct-fixture-parameter`; refusal rows for the four spike-4 laundering probes)
- **Informed By**: spike-4 (the shipped leg does not launder, and the reserved-identifier property is why), spike-5
- **Assigned To**: `guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- In the `isinstance(value, ast.Name)` branch of route 1, accept when `value.id in CLAIM_FIXTURE_NAMES`, detail `"claim-API fixture parameter"`, **before** calling `_resolve_one_hop`.
- Confirm a locally rebound `scratch_test_db = 7` is still refused: the accept must key on the identifier being a parameter with no local rebinding, not on the identifier alone.
- Add all four spike-4 probes as standing refusal tests, so the next widening attempt is caught.
- Update the `CLAIM_FIXTURE_NAMES` docstring so its "mirrors ... exactly" claim becomes true.

### 5. Per-kind violation messages

- **Task ID**: `build-format-violation`
- **Depends On**: `build-positional`, `build-url-keyword`
- **Validates**: `tests/unit/test_db_derivation_guard.py` (per-kind message assertions; unknown-kind fallback)
- **Informed By**: spike-3 (measured: a `db-positional` candidate renders as `from_url(7)`)
- **Assigned To**: `guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Replace the two-way `kind` branch with an explicit per-kind rendering; the `else` branch must stop meaning `from_url`.
- An unrecognized kind renders its own kind name, so the next kind added fails visibly rather than impersonating a shape.
- Assert, per kind, that the message names the file, a non-zero line, the shape, and the `pool_db` sentence when and only when `pool_db` is set.
- Assert the remedial sentence names `claim_test_db()`, `redis_test_url()`, and both disposition tables (#2768's unpinned-message item).

### 6. Signature tripwire

- **Task ID**: `build-signature-pin`
- **Depends On**: `build-positional`
- **Validates**: `tests/unit/test_db_derivation_guard.py` (new signature pin test)
- **Informed By**: spike-2 and the Research finding on redis/redis-py#510
- **Assigned To**: `guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- In the **test file only**, import `inspect` and `redis` and assert `list(inspect.signature(redis.Redis.__init__).parameters)[REDIS_DB_POSITIONAL_INDEX + 1] == "db"` (the `+ 1` skips `self`).
- Assert `redis.StrictRedis is redis.Redis`, or if they ever diverge, that both take `db` at that index.
- Confirm `tests/db_derivation_guard.py` still imports no `redis`; it stays a pure-AST module.

### 7. Residual-gap disclosure

- **Task ID**: `document-residual-gaps`
- **Depends On**: `build-positional`, `build-url-keyword`, `build-fixture-leg`, `build-format-violation`
- **Assigned To**: `guard-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Add the **What this guard still cannot see** section to the module docstring with all four residual gaps from Technical Approach fix 5, naming `REDIS_CONSTRUCTORS` as the residual permit list.
- Cross-reference it from `_splat_candidate`'s existing "known cost" paragraph.
- Rewrite the module docstring's route description to state which argument positions each route reads.
- Update the `Candidate.kind` contract comment and the `REDIS_DB_POSITIONAL_INDEX` comment.

### 8. Per-fix mutation validation

- **Task ID**: `validate-mutations`
- **Depends On**: `build-positional`, `build-url-keyword`, `build-fixture-leg`, `build-format-violation`, `build-signature-pin`
- **Assigned To**: `mutation-validator`
- **Agent Type**: validator
- **Parallel**: false
- **In its own worktree.** Do not share a checkout with `guard-builder`.
- For each of the six fixes independently: revert that one hunk, run the suite, confirm **that fix's own test** goes red (not merely that something failed), restore, re-run, confirm green.
- Use `source_fingerprint()` for the restore check.
- Report per-fix. An aggregate "mutation testing passed" is not an accepted result.

### 9. Guard-semantics review

- **Task ID**: `review-semantics`
- **Depends On**: `validate-mutations`, `document-residual-gaps`
- **Assigned To**: `guard-reviewer`
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify the positional leg's callee scoping is disclosed as the second bounded exception, not presented as callee-agnostic.
- Verify the `CLAIM_FIXTURE_NAMES` widening satisfies comment 5277517215's laundering constraint, with the four probes as evidence.
- Verify the disclosure paragraph describes what the code does, line by line. A disclosure that overstates coverage is worse than none.

### 10. Final validation

- **Task ID**: `validate-all`
- **Depends On**: `review-semantics`
- **Assigned To**: `mutation-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and compare against the `capture-red` baseline.
- Confirm the live tree still reports 0 undispositioned and 0 stale.
- Run `./scripts/pytest-clean.sh tests/unit/test_db_derivation_guard.py -q -n0`.
- Confirm all Success Criteria boxes.

## Verification

Every row below was executed against unmodified `main` at `67d714662` while
writing this plan. The three defect rows are **red today** (`POSITIONAL 0`,
`STRICT 0`, `URLKW 0`, `FIXTURE 1`) and the invariant rows are green. That
recorded red state is the paper trail: a fix that cannot be shown flipping these
exact rows has not been demonstrated.

| Check | Command | Expected |
|-------|---------|----------|
| Positional db on Redis goes red | `python -c "from tests.db_derivation_guard import scan_source as s; print('POSITIONAL', len(s('import redis\ndef t():\n    redis.Redis(\"h\", 6379, 7)\n','x.py').violations))"` | output contains POSITIONAL 1 |
| Positional db on StrictRedis goes red | `python -c "from tests.db_derivation_guard import scan_source as s; print('STRICT', len(s('import redis\ndef t():\n    redis.StrictRedis(\"h\", 6379, 7)\n','x.py').violations))"` | output contains STRICT 1 |
| Positional claim call is accepted | `python -c "from tests.db_derivation_guard import scan_source as s; print('POSCLAIM', len(s('import redis\nfrom tests.db_claim import claim_test_db\ndef t():\n    redis.Redis(\"h\", 6379, claim_test_db())\n','x.py').violations))"` | output contains POSCLAIM 0 |
| Keyword url on from_url goes red | `python -c "from tests.db_derivation_guard import scan_source as s; print('URLKW', len(s('import redis\ndef t():\n    redis.Redis.from_url(url=\"redis://localhost:6379/9\")\n','x.py').violations))"` | output contains URLKW 1 |
| Keyword url names the right db | `python -c "from tests.db_derivation_guard import scan_source as s; print('URLDB', [c.pool_db for c in s('import redis\ndef t():\n    redis.Redis.from_url(url=\"redis://localhost:6379/9\")\n','x.py').violations])"` | output contains URLDB [9] |
| Keyword sanctioned url is accepted | `python -c "from tests.db_derivation_guard import scan_source as s; print('URLOK', len(s('import redis\nfrom tests.db_claim import redis_test_url\ndef t():\n    redis.Redis.from_url(url=redis_test_url())\n','x.py').violations))"` | output contains URLOK 0 |
| Direct fixture parameter is accepted | `python -c "from tests.db_derivation_guard import scan_source as s; print('FIXTURE', len(s('import redis\ndef t(scratch_test_db):\n    redis.Redis(db=scratch_test_db)\n','x.py').violations))"` | output contains FIXTURE 0 |
| Rebound sanctioned name stays red | `python -c "from tests.db_derivation_guard import scan_source as s; print('LAUNDER1', len(s('import redis\ndef t():\n    scratch_test_db = 7\n    redis.Redis(db=scratch_test_db)\n','x.py').violations))"` | output contains LAUNDER1 1 |
| Ordinary local stays red | `python -c "from tests.db_derivation_guard import scan_source as s; print('LAUNDER2', len(s('import redis\ndef t():\n    test_db = 7\n    redis.Redis(db=test_db)\n','x.py').violations))"` | output contains LAUNDER2 1 |
| Positional message is not mislabeled as from_url | `python -c "from tests.db_derivation_guard import scan_source as s, format_violation as f; v=s('import redis\ndef t():\n    redis.Redis(\"h\", 6379, 7)\n','x.py').violations; print(f(v[0]) if v else 'NOVIOLATION')"` | output contains takes db=7 |
| Every kind names the remedial API | `python -c "from tests.db_derivation_guard import Candidate, format_violation as f; print('REMEDY', all(all(x in f(Candidate(path='x.py',lineno=1,kind=k,expr='7',callee='Redis',ok=False,detail='d',pool_db=7)) for x in ('claim_test_db()','redis_test_url()','ALLOWLIST','DEFERRED')) for k in ('db-kwarg','from-url','db-positional')))"` | output contains REMEDY True |
| Short and starred calls raise nothing | `python -c "from tests.db_derivation_guard import scan_source as s; print('EDGE', len(s('import redis\ndef t(args):\n    redis.Redis()\n    redis.Redis(\"h\")\n    redis.Redis(\"h\", 6379)\n    redis.Redis(*args)\n','x.py').candidates))"` | output contains EDGE 0 |
| Live tree has no undispositioned violation | `python -c "from tests.db_derivation_guard import scan_tree, apply_dispositions as a; r,_=a(scan_tree()); print('UNDISP', len(r))"` | output contains UNDISP 0 |
| Live tree has no stale disposition entry | `python -c "from tests.db_derivation_guard import scan_tree, apply_dispositions as a; _,s=a(scan_tree()); print('STALE', len(s))"` | output contains STALE 0 |
| Disposition tables satisfy their own rules | `python -c "from tests.db_derivation_guard import check_dispositions; print('DISP', check_dispositions())"` | output contains DISP [] |
| Positional index still names db in redis-py | `python -c "import inspect, redis; from tests.db_derivation_guard import REDIS_DB_POSITIONAL_INDEX as i; print('SIGPIN', list(inspect.signature(redis.Redis.__init__).parameters)[i+1])"` | output contains SIGPIN db |
| Scanner imports no redis | `grep -c "^import redis\|^from redis" tests/db_derivation_guard.py` | match count == 0 |
| Residual gaps are disclosed in the module docstring | `python -c "import tests.db_derivation_guard as g; print('DISCLOSE', 'What this guard still cannot see' in (g.__doc__ or '') and 'REDIS_CONSTRUCTORS' in (g.__doc__ or ''))"` | output contains DISCLOSE True |
| Candidate kind contract comment lists all three | `grep -c "db-kwarg.*from-url.*db-positional" tests/db_derivation_guard.py` | output contains 1 |
| No xfail markers to convert | `grep -rn "pytest.mark.xfail\|pytest.xfail(" tests/` | exit code 1 |
| Guard suite passes | `./scripts/pytest-clean.sh tests/unit/test_db_derivation_guard.py -q -n0` | exit code 0 |
| Lint clean | `python -m ruff check tests/db_derivation_guard.py tests/unit/test_db_derivation_guard.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/db_derivation_guard.py tests/unit/test_db_derivation_guard.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Is fix 3 (the direct fixture-parameter leg) in scope?** It is not in the issue body. It surfaced from a plan-time probe: `redis.Redis(db=scratch_test_db)` is refused while `divergent_db = scratch_test_db; redis.Redis(db=divergent_db)` is accepted, so the obvious spelling is the one that fails. Live exposure is zero. The case for including it is that it is one `if` on the same route as fix 1, and it makes `CLAIM_FIXTURE_NAMES`'s existing "mirrors ... exactly" docstring claim true instead of leaving a false claim in a safety guard. The case against is that widening an accept in a guard is the direction that fails silently, and comment 5277517215 warns specifically about this leg. If dropped, the docstring sentence must be corrected in the same pass. **Default if unanswered: include it, with the four laundering probes landing in the same commit as the accept.**

2. **Should the positional leg emit for a call that also passes `db=` explicitly?** `Redis("h", 6379, 7, db=8)` is a `TypeError` at runtime and cannot be a live site. The plan suppresses the positional candidate when a `db=` keyword is present, so the call yields one violation rather than two. The alternative is to emit both and let the author see every derived db on the line. **Default if unanswered: suppress, on the grounds that a duplicate violation on an unrunnable line is noise.**

3. **Is a `docs/features/` page wanted for this guard?** The plan argues no: the module docstring is deliberately the source of truth, and a second description of a 740-line self-documenting guard is a drift risk this module's own history warns about. If the answer is yes, it changes the Documentation section and adds a documentarian task. **Default if unanswered: no feature doc, docstring only.**
