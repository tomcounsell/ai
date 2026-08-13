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

The guard is callee-agnostic almost everywhere, but not unconditionally, and the exception is worth
naming precisely. Route 1's `db=` keyword matching, and a dict-literal splat with a visible `"db"` key
(`redis.Redis(**{"db": 15})`), are both callee-agnostic: the value is visible in the AST, so it is
judged on its own terms regardless of what it is being passed to, even when a later entry in the same
literal could overwrite it (`**{"db": 15, **overrides}`). Callee matters wherever the value is not
visible at all: the opaque `**name` splat (`redis.Redis(**kw)`) and a dict literal with no visible
`"db"` key but an opaque entry (`redis.Redis(**{**base, "host": "x"})`) are both judged by callee,
because there is nothing else to judge them by.

Enumerating accepted *callee* shapes is what failed three times: everything unenumerated passes
silently, and the next call site is always written in a shape nobody enumerated. That is still the
guard's thesis for every route where the value is visible, and it holds without exception there.

**Why the opaque leg is scoped to `REDIS_CONSTRUCTORS`.** An unrestricted, callee-agnostic version of
the opaque-splat leg was tried first, not assumed safe: flagging every opaque `**` splat regardless of
callee produced 183 violations across 100+ unrelated files, because `**kwargs` forwarding is the
ordinary way test helpers are written in this tree. `tests/` has 191 `**` splat call sites total and
zero of them are Redis-ish. A guard that fires on every helper in the repo does not get fixed, it gets
deleted, and then the real hole is open again with no guard at all — so the opaque leg alone is scoped
to `REDIS_CONSTRUCTORS` (`Redis`, `StrictRedis`, `from_url`), and the cost is recorded rather than
hidden. This matters beyond the code comment: a maintainer reading only this doc, without the module's
own docstring, could see `REDIS_CONSTRUCTORS` as a violation of the guard's stated thesis and "restore"
name-agnostic matching on that leg — which would re-trigger the 183-violation flood and get the guard
deleted rather than fixed. Two live sites already sit on this callee-gated dict-literal path —
`tests/unit/session_runner/test_runner_preempt.py:95` (`**{**FAST, **kwargs}`) and
`tests/unit/test_settings.py:95` (`**{field: bad_value}`) — and widening the leg to callee-agnostic
would turn both into violations, which is exactly the flood this scoping exists to avoid.

The same inversion dissolves the `from_url` problem. The guard does not need to understand every URL
expression, only to refuse every one it cannot prove safe.

## Two dispositions

When a site genuinely cannot use the claim API, add an entry to one of two tables in
`tests/db_derivation_guard.py`. They are separate on purpose.

**`ALLOWLIST`** — permanent. Subject to a machine-checked invariant: **no allowlist entry may name a
database in `[1..TEST_DB_POOL_MAX]`**. In practice this means db 0 only, and in practice the only
legitimate cases are tests that must construct a db-0 client to prove the db-0 flush guard refuses it,
and reads of production-only keys such as worker heartbeat registrations.

The invariant is held by **two layers doing different halves**, and which is which matters if you ever
touch either.

`check_dispositions()` inspects the exemption expression *as text*. It parses `entry.expr` on its own,
with no syntax tree and no bindings, and rejects an entry containing a pool-slot integer literal —
`14`, `'redis://localhost:6379/14'`, `15 if base != 15 else 14`. What it cannot do is resolve a name:
`divergent_db` has no integer in it, so this layer **accepts** it.

`apply_dispositions()` is what actually stops laundering. It refuses to let an `ALLOWLIST` entry cover
a candidate the *scan* proved names a pool slot, and the scan does have the bindings, so
`divergent_db` reaches it carrying `pool_db=14`. Hiding a pool database behind a variable name is
closed here, not above.

That distinction is worth stating because the failure mode is quiet. One test covers the
`apply_dispositions` condition. A maintainer who believes the text-level check enforces the invariant
on its own, and simplifies that condition away, sees a single failure, reads it as noise, and removes
the only thing holding the invariant up.

**`DEFERRED`** — temporary. Must carry `blocked_on` (an issue like `#2628`) and an ISO `expires` date.
Reported on every run and a hard failure once the date passes. This exists so a site that cannot be
fixed yet is never laundered through `ALLOWLIST` by hiding its pool database behind a variable.

Entries are keyed by `(path relative to tests/, exact unparsed expression)`. That survives line moves
and is narrower than exempting a whole file, but it is **per-file-per-expression, not per-site**: one
entry covers every site in that file sharing the expression, and would silently cover a new one. The
`test_redis_flush_guard.py` / `"0"` entry currently covers four sites. The db-0 invariant bounds the
blast radius — a newcomer can only be swept up if it names db 0 too. An entry that matches no site is
a failure: exemptions cannot outlive the sites they were written for.

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
a `node.func.id` matcher would be vacuous), and plants one offending source per shape it claims to
catch (`PLANTED_OFFENDERS` in `tests/unit/test_db_derivation_guard.py`, currently eleven) in
`tmp_path`, asserting each produces a violation naming the file and line. Planted sources are parsed,
never executed, and no test here claims a pool database or touches db 0.
