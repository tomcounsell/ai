# Test Redis `db=` Derivation Guard

A static check that fails the suite when a test computes its own Redis database number instead of
asking the claim API. Implemented in `tests/db_derivation_guard.py`, enforced by
`tests/unit/test_db_derivation_guard.py`. Issue #2655.

## Why it exists

Many pytest processes run at once on one machine and they all hit the same Redis server. Each process
owns a private logical database, claimed via an `flock` over the pool `[1..TEST_DB_POOL_MAX]` in
`tests/db_claim.py`. A test that computes its own database number can flush a database another live
process is using, which shows up as data vanishing mid-test in a run that has no visible connection
to the offending test.

That defect was fixed at one call site in #2117, at another in #2606, and at a third in #2624. Each
fix was correct and none of them made the next wrong site visible.

## The rule

**A `db=` value must be a call to the claim API.** Two shapes are accepted:

```python
redis.Redis(db=claim_test_db())          # direct call
redis.Redis(db=_db_claim.claim_test_db())  # attribute-qualified, same thing

test_db = claim_test_db()                # a local bound exactly once to a claim call,
redis.Redis(db=test_db)                  # used within the same function
```

For URLs, `tests.db_claim.redis_test_url()` and the `redis_test_url` pytest fixture are the sanctioned
sources:

```python
redis.Redis.from_url(redis_test_url())            # direct call
def test_x(redis_test_url): ...from_url(redis_test_url)  # fixture parameter
```

Everything else is a violation.

## The polarity is inverted, deliberately

The guard does **not** enumerate which functions it inspects. It flags every `db=` keyword argument
passed to *any* call anywhere under `tests/`, plus every `from_url(...)` argument, and then requires
the value to match one of the shapes above.

Enumerating accepted *callee* shapes is what failed three times: everything unenumerated passes
silently, and the next call site is always written in a shape nobody enumerated. Ignoring the callee
name costs nothing today (every `db=` keyword in `tests/` is a Redis construction) and means a
constructor name nobody has thought of yet is still checked.

The same inversion dissolves the `from_url` problem. The guard does not need to understand every URL
expression, only to refuse every one it cannot prove safe.

## Two dispositions

When a site genuinely cannot use the claim API, add an entry to one of two tables in
`tests/db_derivation_guard.py`. They are separate on purpose.

**`ALLOWLIST`** — permanent. Subject to a machine-checked invariant: **no allowlist entry may name a
database in `[1..TEST_DB_POOL_MAX]`**. In practice this means db 0 only, and in practice the only
legitimate cases are tests that must construct a db-0 client to prove the db-0 flush guard refuses it,
and reads of production-only keys such as worker heartbeat registrations.

The invariant is enforced three ways: integer literals anywhere in the exempted expression's subtree,
literals in the expression's local binding, and the db path of a literal Redis URL. A site the guard
can prove names a pool slot is not coverable by an allowlist entry at all, even a matching one.

**`DEFERRED`** — temporary. Must carry `blocked_on` (an issue like `#2628`) and an ISO `expires` date.
Reported on every run and a hard failure once the date passes. This exists so a site that cannot be
fixed yet is never laundered through `ALLOWLIST` by hiding its pool database behind a variable.

Entries are keyed by `(path relative to tests/, exact unparsed expression)`. That survives line moves
and is narrower than exempting a whole file. An entry that matches no site is a failure: exemptions
cannot outlive the sites they were written for.

## Relationship to the runtime ownership check

#2628 ships a connection-layer check that fails closed when an unclaimed destructive operation
executes. The two are complementary rather than redundant:

- The static guard sees sites that never execute in a given run, and sites that perform no destructive
  operation at all. `redis.Redis(db=1).ping()` is a wrong derivation that no flush-ownership check
  will ever observe.
- A runtime raise can be swallowed by the code around it. One such site sat inside
  `except Exception: pytest.skip(...)`, which would have converted the runtime guard's exception into
  a green skip.
- The runtime check sees derivations computed inside installed library code, which no walk of this
  repo's sources can reach.

Static catches authorship; runtime catches execution.

## Adding a new site

Call `claim_test_db()`. If you need a *second*, different database (to prove a split-brain, say), you
need an independently claimed one — hand-picking a pool slot is the defect this guard exists to catch.

## Proving the guard still works

A guard that measures nothing looks exactly like a guard that passes. The test file asserts the
candidate count is non-zero, asserts that Redis constructions in this tree are attribute-qualified (so
a `node.func.id` matcher would be vacuous), and plants nine offending sources in `tmp_path` — one per
shape — asserting each produces a violation naming the file and line. Planted sources are parsed,
never executed, and no test here claims a pool database or touches db 0.
