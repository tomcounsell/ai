---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-08
tracking: https://github.com/tomcounsell/ai/issues/2655
last_comment_id: none
revision_applied: false
---

# Recurrence Guard: Detect Self-Derived Redis `db=` in Tests

## Problem

The test suite runs many pytest processes at once. Each must own a private Redis logical database or
they flush each other's data mid-test. Ownership comes from an `flock` claim over the pool
`[1..TEST_DB_POOL_MAX]` in `tests/db_claim.py`; `claim_test_db()` is the only sanctioned source of a
`db=` value.

A test that computes its own database number can issue a destructive command against a database a
different live process owns. That defect has been fixed three times (#2117, #2606, #2624) and
re-emerged each time at a new call site, because nothing makes a wrong derivation *detectable*.
Review is the only control and review has missed it three times.

**Desired outcome:** a test that constructs a Redis client with a `db=` value not obtained from the
claim API fails a check, by name, at the offending line, every time, without a human noticing.

## Freshness Check

**Baseline commit:** `76aae750e`
**Issue filed:** 2026-08-08
**Disposition:** **Overlap** — #2628 owns the underlying rotation fix and holds an exclusive lock on
four files. No code drift in the guard's own surface (no guard file exists).

Re-verified against the worktree at `76aae750e`:

| Issue claim | Verification | Result |
|---|---|---|
| 19 attribute-qualified `X.Redis(` in `tests/` | `grep -rnoE "[A-Za-z_]+\.Redis\(" tests/` | 19 — **but only 17 are code**. Two are prose inside comments (`tests/conftest.py:97`, `tests/integration/test_session_notify.py:167`). An AST walk sees 17/17 real call sites. |
| Zero bare `Redis(` | `grep -rnE "(^\|[^.A-Za-z_])Redis\("` minus `.Redis(` | 0. AST agrees: 17 `Redis` calls, **all `ast.Attribute`, zero `ast.Name`**. |
| `_redis_test_db_num` is a pure alias | `tests/conftest.py:656-658` | Holds: `return claim_test_db()`. |
| ~10 `from_url(` sites | grep 10, AST 9 (the tenth is the same `conftest.py:97` comment) | 9 real calls. |
| No guard file exists | `git ls-files tests/ \| grep derivation` | Confirmed. |

## Prior Art

- **#2060** — introduced the flock claim pool, replacing the per-worker `gw{N}->db{N+1}` derivation
  that collided across concurrent pytest *processes*.
- **#2117, #2606, #2624** — three separate fixes to individual call sites in this same defect family.
  Each corrected one derivation without making the next wrong one visible.
- **#2605** — moved the claim into `tests/db_claim.py` as a plain module so pytest's conftest import
  machinery could not fork the memoized state.
- **#2628** (open, in flight) — fixes the rotation itself and ships a connection-layer ownership
  check. This guard was Task 6 of its plan and is deliberately descoped to a separate issue.

## Research

### Finding 1 — every `db=` keyword in `tests/` is a Redis construction today

The decisive measurement. An AST walk over all 17 `db=` keyword arguments in `tests/`:

```
callee terminal name -> count
Redis -> 17
(anything else) -> 0
```

**Nothing else in `tests/` passes a `db=` keyword to anything.** A rule that flags *every* `db=`
keyword regardless of what is being called therefore has **zero false positives on current `main`**,
while being structurally incapable of missing a new Redis-like constructor name. This is the evidence
that decides open question 1.

### Finding 2 — the `ast.Name` branch is dead on arrival, the `ast.Attribute` branch carries everything

17 of 17 `Redis` call sites parse as `ast.Attribute`. A matcher reading `node.func.id` matches zero.
Confirms the issue's central finding and dictates that the mutation harness must specifically neuter
the `Attribute` branch and observe RED.

### Finding 3 — three live sites are the defect itself, not hypotheticals

| Site | Expression | Why it is wrong |
|---|---|---|
| `tests/unit/test_redis_flush_guard.py:48,56` | `db=_own_test_db(request)` | Re-implements the pre-#2060 `gw{N}+1` derivation, then calls `client.flushdb()` at `:58`. **A live destructive flush against a pool slot another process may own.** |
| `tests/unit/test_conftest_isolation_guards.py:365` | `db=divergent_db` where `divergent_db = 15 if base_test_db != 15 else 14` | Hand-picked pool slot, then `divergent_client.flushdb()` at `:366`. Same destructive shape. |
| `tests/integration/test_agent_catchup_recovery.py:61` | `redis.Redis(db=1).ping()` | Hardcoded pool slot. Non-destructive, but it is the exact derivation error and it reads a foreign process's db. |

The guard is not a speculative control. It finds three real instances of its own defect class on the
first run, two of which flush.

### Finding 4 — a runtime flush-ownership check cannot see two of those three

`test_agent_catchup_recovery.py:61` performs no flush, so a flush-ownership check never fires. Worse,
the line sits inside `try: ... except Exception: pytest.skip(...)`. A runtime guard that raised there
would be **swallowed into a skip** and the run would report green. This decides open question 4.

## Spike Results

### spike-1: Does an inverted rule leave legitimate code unallowlisted?

Prototyped the rule "a `db=` value must be a direct call to the claim API" against all 17 sites.
Result: `tests/conftest.py:615,631` (`db=test_db` where `test_db = claim_test_db()` seven lines
above) failed. That is the canonical fixture. Allowlisting it would teach every reader that the
allowlist is the normal path.

Added exactly one bounded second accepted shape: **a local variable bound exactly once in the
enclosing function to a direct claim call**. Re-ran:

```
PASS conftest.py:615        db=test_db          [one-hop local from claim call]
PASS conftest.py:631        db=test_db          [one-hop local from claim call]
PASS integration/test_email_bridge.py:81  db=claim_test_db()  [direct claim call]
FAIL unit/test_conftest_isolation_guards.py:365  db=divergent_db  [bound 1x, not a claim call]
FAIL unit/test_email_bridge.py:1426  db=_test_db  [bound 1x, not a claim call]
... 12 more FAIL
```

The hop admits the canonical fixture and admits nothing else. It is a *shape*, not a name, so it does
not enumerate anything and cannot decay the way a permit list does.

### spike-2: Is `from_url` genuinely out of reach?

Under a permit-list polarity it is: a URL built at runtime cannot be parsed statically. Under the
inverted polarity **the problem disappears** — an argument the guard cannot prove safe is a violation
by definition, so an unparseable URL is loudly reported rather than silently passed. This is the
strongest single argument for inverting, and it makes a full `from_url` leg cheap. Decides open
question 3.

### spike-3: What does #2628 own?

`tests/conftest.py`, `tests/db_claim.py`, `tests/integration/test_notify_isolation.py`,
`docs/features/test-db-ownership.md`. `claim_scratch_test_db()` — named in #2655's definitions table
as part of the claim API — **does not exist in `tests/db_claim.py` today**. #2628 is adding it.
Consequence: `test_conftest_isolation_guards.py:365` needs a second, independently-claimed db and
therefore cannot be fixed this round without editing an off-limits file.

## Why Previous Fixes Failed

Every prior round enumerated **accepted shapes**: this constructor name, that helper, this call
pattern. An enumeration of accepted shapes means everything unenumerated passes silently. The next
call site was always written in a shape nobody had thought to enumerate, so the check stayed green
while the defect shipped. The issue's own framing of the vacuous `node.func.id` matcher is the same
failure one level down: a check that looks correct and measures nothing.

The correction is polarity, not coverage. Enumerate the *one accepted shape* and make everything else
a violation that must be dispositioned in writing.

## Solution

### Key Elements

1. **`tests/db_derivation_guard.py`** — a plain module (not collected by pytest; same pattern as
   `tests/db_claim.py` and `tests/_worker_guard.py`) holding the AST walker, the dispositions, and
   the invariant checks.
2. **Inverted rule, name-agnostic on the callee.** Every `db=` keyword argument to any call anywhere
   under `tests/`, plus every `from_url(...)` argument, is a candidate. The value must match one of
   two accepted shapes or it is a violation.
3. **Two dispositions, deliberately distinct.**
   - `ALLOWLIST` — permanent exemptions. Machine-enforced: the exempted expression may not contain an
     integer literal in `[1..TEST_DB_POOL_MAX]`, nor a URL naming such a db. In practice this means
     db-0 only.
   - `DEFERRED` — temporary, dated, issue-linked. Reported loudly on every run and hard-failing after
     its expiry date. Kept separate from `ALLOWLIST` precisely so the settled invariant is never
     weakened to accommodate one.
4. **`tests/unit/test_db_derivation_guard.py`** — runs the guard over `tests/`, asserts zero
   violations, and proves non-vacuity by planting offending sources in `tmp_path` and asserting RED.
5. **Six real conversions** in files this PR owns, fixing two live destructive-flush defects.

### Why not a hook or a standalone CLI

A pytest test under `tests/unit/` runs in every suite invocation, is how this repo already expresses
architectural constraints (`tests/unit/test_architectural_constraints.py`), and needs no separate
registration. A PreToolUse hook would only see agent-authored edits and would miss a human commit. No
hook this round.

### Technical Approach

#### Accepted shapes for a `db=` value

```
S1  a direct Call whose TERMINAL name is in {claim_test_db, claim_scratch_test_db}
    terminal name = func.id for ast.Name, func.attr for ast.Attribute
    -> covers claim_test_db()  AND  _db_claim.claim_test_db()

S2  an ast.Name bound EXACTLY ONCE in the enclosing function scope to an S1 call
    more than one binding, a binding from anything else, or no local binding at all -> violation
```

#### Accepted shapes for a `from_url(...)` first argument

```
U1  a direct Call whose terminal name is in {redis_test_url}
U2  an ast.Name whose identifier is redis_test_url   (the pytest fixture parameter; the name is
    reserved by conftest and by tests/db_claim.py, and a fixture parameter has no local binding
    for S2 to resolve)
```

A string-literal URL is parsed: the db in its path is extracted and reported in the violation
message, and a db in `[1..TEST_DB_POOL_MAX]` is marked **never allowlistable**. Anything else is a
violation requiring a disposition.

#### Invariant enforcement — "no allowlist entry may name a db in `[1..TEST_DB_POOL_MAX]`"

Enforced by a check, not by review, in three layers:

1. Every integer literal anywhere in the allowlisted expression's AST subtree is scanned. A value in
   `[1..MAX]` rejects the entry.
2. Where the expression is a one-hop local `ast.Name`, the **binding expression's** subtree is
   scanned too. `divergent_db = 15 if base_test_db != 15 else 14` yields `{15, 14}` and is rejected.
3. String-literal URLs are parsed and their db path checked the same way.

`TEST_DB_POOL_MAX` is imported from `tests.db_claim` rather than re-declared, so there is one
definition on the machine.

#### Anti-decay checks

- An `ALLOWLIST` or `DEFERRED` entry that matches no site is a **failure** ("stale entry — the site
  was fixed or moved; delete this entry"). Exemptions cannot outlive their sites.
- A `DEFERRED` entry must carry a `blocked_on` matching `#\d+` and an ISO `expires` date.
- `date.today() > expires` is a hard failure naming the blocking issue. This is the only forcing
  function that does not require a human to remember.

#### Disposition of every site under `tests/`

| Site | Expression | Action |
|---|---|---|
| `tests/conftest.py:615,631` | `db=test_db` | **Passes** via S2. No edit, no entry. |
| `tests/integration/test_email_bridge.py:81` | `db=claim_test_db()` | **Passes** via S1. |
| `tests/{integration,unit}/test_valor_email.py`, `test_email_relay.py`, `test_send_message.py`, `test_email_bridge.py:1174` | `from_url(redis_test_url)` | **Passes** via U2. |
| `tests/unit/test_redis_flush_guard.py:48,56` | `db=_own_test_db(request)` | **CONVERT** to `claim_test_db()`; delete `_own_test_db`. Fixes a live flush on a foreign pool slot. |
| `tests/integration/test_agent_catchup_recovery.py:61` | `db=1` | **CONVERT** to `claim_test_db()`. |
| `tests/unit/test_email_bridge.py:1426` | `db=_test_db` from `connection_kwargs` | **CONVERT** to `claim_test_db()`. Behavior-preserving: the autouse fixture set that db *from* `claim_test_db()`. |
| `tests/unit/test_conftest_isolation_guards.py:296,352` | `_conftest._redis_test_db_num()` | **CONVERT** both to `_db_claim.claim_test_db()` (`_db_claim` is already imported there). |
| `tests/unit/test_email_history.py:29` | `from_url(redis_url_env)` | **CONVERT** the fixture to take `redis_test_url` directly. |
| `tests/unit/test_agent_session.py:690,782` | `from_url("redis://localhost:6379/0")` | **CONVERT** to `redis_test_url()` from `tests.db_claim`. Reachability ping; any db works. |
| `tests/_worker_guard.py:91` | `db=0` | **ALLOWLIST** — production heartbeat read; registrations only exist on db 0. Non-destructive. |
| `tests/integration/test_redis_models.py:696` | `db=0` | **ALLOWLIST** — asserts test data did *not* leak to db 0. |
| `tests/unit/test_redis_flush_guard.py:28,40,62,69` | `db=0` | **ALLOWLIST** — the db-0 flush guard's own tests must construct db 0 to prove it is refused. |
| `tests/unit/test_redis_flush_guard.py:34` | `from_url("redis://localhost:6379/0")` | **ALLOWLIST** — same, via the URL route. |
| `tests/integration/test_notify_isolation.py:62` | `db=int(kw.get("db",0) or 0)` | **DEFERRED** — file is owned by #2628, which is folding the conversion in. |
| `tests/unit/test_conftest_isolation_guards.py:365` | `db=divergent_db` | **DEFERRED** — needs `claim_scratch_test_db()`, which does not exist until #2628 lands. |

Four permanent entries, all db-0. Two deferred entries, both naming #2628.

## Open Questions — Answered

### Q1. AST walk, or an inverted rule?

**Both, and the inversion is the load-bearing half.** AST is the only instrument that can see the
*structure* of a `db=` value (a regex cannot distinguish `db=claim_test_db()` from `db=claim + 1`).
But the instrument was never the problem. The problem was polarity.

Evidence for inverting: all 17 `db=` keywords in `tests/` today are Redis constructions, so a rule
that ignores the callee name entirely has zero false positives *and* cannot be defeated by a
constructor name nobody enumerated. That is precisely how #2117, #2606 and #2624 each escaped. The
inverted rule enumerates one accepted value shape (plus one bounded local hop) and makes everything
else a written disposition.

Second-order evidence: inverting dissolves the `from_url` scoping problem entirely (spike-2). Under a
permit list, an unparseable URL passes; under the inverted rule it is a violation.

### Q2. Permit `_redis_test_db_num` by name, or delete it?

**Neither permitted nor deleted this round — the two call sites that feed it into `db=` are
converted instead.** Deleting the alias requires editing `tests/conftest.py:656-658`, which is inside
#2628's exclusive lock, so **deletion is not available to this PR** and I state that explicitly.
Permitting it by name is rejected: it is a pure alias, blessing it grows the accepted-name set for no
behavioral gain, and the accepted-name set is the exact artifact that failed three times.

After this PR the alias has one remaining consumer (`tests/conftest.py:669`) and one assertion about
its behavior (`tests/unit/test_conftest_isolation_guards.py:534`, which is not a `db=` feed).
**Recommendation carried to #2628: delete `_redis_test_db_num` and inline `claim_test_db()`.**

### Q3. Is `from_url(...)` in scope for v1?

**In scope, fully.** Under the inverted rule the "different parse problem" framing does not survive:
the guard does not need to understand every URL, it needs to refuse every argument it cannot prove
safe. Literal URLs are parsed and their db reported; the sanctioned `redis_test_url` call and fixture
name pass; everything else is a violation. Cost is roughly 30 lines. The alternative — declaring it
out of scope — would leave a hardcoded `redis://localhost:6379/7` invisible, which is the same
recurrence in a different syntax.

### Q4. Is a runtime guard cheaper and more reliable than the static one?

**They are complementary; neither subsumes the other.** #2628's connection-layer check fires when an
unclaimed *destructive* operation executes. Evidence that this is not sufficient:

- `test_agent_catchup_recovery.py:61` (`db=1`, ping only) performs no destructive operation, so a
  flush-ownership check never fires, yet the derivation is wrong and it reads a foreign db.
- That same line is inside `except Exception: pytest.skip(...)`. A runtime raise would be converted
  into a **skip**, and the suite would report green. A runtime guard can be swallowed by the code it
  is guarding; a static one cannot.
- The static guard evaluates every site in the tree, including tests that a given narrow run never
  executes. Runtime coverage equals execution coverage.

Evidence the static guard is *not* sufficient alone: #2628's investigation found a flushing writer
inside installed library code, invisible to any walk of this repo's sources. Only a runtime check
sees that.

Static catches authorship; runtime catches execution. Ship both, owned by their respective issues.

## Architectural Impact

Additive. One new non-collected module under `tests/`, one new unit test file, six small conversions.
No production code touched. No new dependency (`ast` is stdlib).

## Appetite

Medium. One session. The risk is not implementation size, it is designing a guard that measures
nothing — which the mutation harness below exists to disprove.

## Test Impact

| Area | Change |
|---|---|
| New | `tests/unit/test_db_derivation_guard.py` — guard-clean assertion, non-vacuity via planted offenders, invariant checks, stale-entry and expiry checks. |
| Modified | `test_redis_flush_guard.py`, `test_conftest_isolation_guards.py`, `test_email_bridge.py` (unit), `test_email_history.py`, `test_agent_session.py`, `test_agent_catchup_recovery.py` — derivation conversions only, no assertion changes. |
| Behavior | `test_flushall_is_blocked_even_on_test_db` and `test_flushdb_on_own_test_db_is_allowed` stop flushing a db this process may not own. Strict improvement. |

The guard's own tests never claim a pool db and never touch db 0: planted offenders are written to
`tmp_path` and parsed, never executed.

## Failure Path Test Strategy

- **Unparseable source** — a file with a syntax error must raise a named error, not be skipped. A
  guard that silently skips files it cannot parse is the vacuous failure mode.
- **Empty tests tree** — scanning a directory with no `db=` sites must report zero *candidates*, and
  the suite asserts the real tree's candidate count is non-zero. A guard that finds nothing to check
  is indistinguishable from a passing one unless the count is asserted.
- **Stale disposition** — an entry matching no site fails.
- **Expired deferral** — `expires` in the past fails, naming the blocking issue.

## Rabbit Holes

- Full dataflow analysis across modules. Explicitly not doing it; one local hop, single binding.
- Scanning production code. `db=` in `bridge/`, `agent/`, `worker/` is legitimate configuration.
- Building a `claim_scratch_test_db()` here. It belongs to #2628; duplicating it would collide.

## Risks

### Risk 1: The guard is vacuous and nobody notices

The failure the issue exists to prevent, one level up. Mitigations, all mandatory:
the guard prints the candidate count it matched and the suite asserts it is non-zero; planted
offenders in `tmp_path` prove RED; a mutation harness neuters each branch (the `ast.Attribute` branch
above all) and requires the targeted test to go RED. **The mutation harness refuses to report unless
the unmutated baseline is green first** — a syntax error in the mutant makes every mutation look
"killed" while the baseline is broken.

### Risk 2: A deferred entry becomes permanent

Mitigated by the hard expiry failure and by keeping `DEFERRED` a separate structure whose entries are
printed on every run. Accepted residual: the expiry fires on a calendar date rather than on a code
change, so it can fail a run unrelated to its cause. The message names #2628 and the remedy.

### Risk 3: `divergent_db` is laundered through the allowlist

Real, and specifically designed against. `divergent_db` resolves to pool slot 15 or 14 and is
flushed. It is a `DEFERRED` entry, not an `ALLOWLIST` entry, and layer 2 of the invariant check
(binding-subtree literal scan) would **reject** it outright as an allowlist entry. The invariant
survives verbatim.

### Risk 4: Seam collision with #2628

The PR must contain zero changes to `tests/conftest.py`, `tests/db_claim.py`,
`tests/integration/test_notify_isolation.py`, `docs/features/test-db-ownership.md`. Verified with
`git diff --name-only origin/main...HEAD` before the PR opens, and reported in the PR body.

## No-Gos (Out of Scope)

- Editing any of the four #2628-owned files. **No-go reason:** exclusive lock held by a concurrent
  workstream; a conflicting edit would force one of the two PRs to be rebuilt.
- Adding a runtime connection-layer ownership check. **No-go reason:** #2628 ships it; duplicating it
  creates two guards with divergent messages.
- A PreToolUse hook. **No-go reason:** it sees agent edits only, and the suite test already covers
  every commit path.

## Documentation

- New: `docs/features/test-db-derivation-guard.md` — what the rule is, the two accepted shapes, how
  to disposition a new site, and why the polarity is inverted.
- `docs/features/README.md` — index entry.

## Success Criteria

1. `python -m pytest tests/unit/test_db_derivation_guard.py` passes on the converted tree.
2. The guard reports a **non-zero** candidate count, asserted by a test. Expected 17 `db=` candidates
   plus 9 `from_url` candidates on the converted tree.
3. A planted offending source in `tmp_path` produces a violation naming the file and line.
4. Mutating the `ast.Attribute` terminal-name branch turns a targeted test RED.
5. Every site under `tests/` passes or carries a disposition with a stated reason.
6. No `ALLOWLIST` entry names a db in `[1..TEST_DB_POOL_MAX]`, enforced by a check.
7. `from_url` is covered, not deferred.
8. `git diff --name-only origin/main...HEAD` contains none of the four #2628-owned paths.
9. `python -m ruff check` and `python -m ruff format --check` clean.

## Step by Step Tasks

### 1. Guard module

Write `tests/db_derivation_guard.py`: terminal-name helper covering `ast.Name` and `ast.Attribute`,
candidate collection for `db=` keywords and `from_url` arguments, the one-hop local binding resolver,
the two disposition tables, the invariant checks, and `scan_tree()` returning
`(violations, candidate_count)`.

### 2. Convert the six owned sites

`test_redis_flush_guard.py` (delete `_own_test_db`), `test_agent_catchup_recovery.py`,
`test_email_bridge.py` (unit), `test_conftest_isolation_guards.py` (two sites),
`test_email_history.py`, `test_agent_session.py` (two sites).

### 3. Guard tests

Clean-tree assertion, non-zero candidate count, planted-offender RED for each shape
(`ast.Attribute` callee, hardcoded pool db, `connection_kwargs` derivation, literal pool URL),
syntax-error propagation, stale-entry detection, expiry detection, and the allowlist invariant.

### 4. Mutation check

Baseline-green gate, then neuter each branch in turn, record kill/survive, revert, assert the file is
byte-identical to its pre-mutation hash.

### 5. Docs, seam verification, PR

## Verification

```bash
export PYTHONPATH=/Users/tomcounsell/src/ai/.worktrees/db-derivation-guard
cd "$PYTHONPATH"
./scripts/pytest-clean.sh tests/unit/test_db_derivation_guard.py -p no:randomly -q
./scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -p no:randomly -q
python -m ruff check && python -m ruff format --check
git diff --name-only origin/main...HEAD   # must exclude the four #2628 paths
```
