# Test-DB Ownership

Two `tests/unit/` runs on the same commit, on the same machine, used to produce different
failure sets. Every failing node passed in isolation. A suite whose failure set changes
between identical runs cannot answer "did my change break something", so this is a defect in
the measuring instrument rather than an ordinary flake.

The cause was three independent writers, each corrupting state that a *different live pytest
process* owned. This page is about the rule that ends that class: **a test process may flush
only a database it has claimed, and the claim is a kernel-enforced fact rather than a number
each call site re-derives.**

## The claim lifecycle

Ownership is established once per process, at session start, and every state is defined.

| State | When | What the guard does |
|---|---|---|
| `unclaimed-not-yet` | a process that will own a db, before its claim exists | **Designed out.** `tests/conftest.py::pytest_configure` claims before collection, so it runs before every fixture — autouse, installed-plugin, or otherwise. No test and no plugin fixture can observe this state. |
| `claimed` | normal operation on an xdist worker, or on the master under `-n0` | permits exactly the claimed set |
| `unclaimable-permanently` | the pool is exhausted; the claim raised | denies, and the failure is **sticky**: the wait is paid once per process, never once per test. `pytest.exit` aborts the session with one line instead of thousands of setup errors. |
| `not-applicable-controller` | the xdist controller, which runs no tests | never claims, never flushes. Claiming there would hold one of only 15 machine-global slots for the whole session and never use it. |

Claiming at session start rather than lazily is the decision the whole design rests on. The
alternative was an exemption permitting each worker's first flush while the claimed set was
still empty, and that exemption is a hole exactly the width of the bug: "permit a flush when
we do not know who owns the target" is the pre-#2606 status quo restated, and it would have
been load-bearing on every worker's first test forever.

## How to get a database

| Need | Use |
|---|---|
| this process's db | `tests.db_claim.claim_test_db()` |
| a URL for a raw client or a `REDIS_URL` env | the `redis_test_url` fixture |
| a **second** db, for a test whose subject is divergence between two | the `scratch_test_db` fixture |
| a subprocess that must see the same data | `tests.db_claim.subprocess_env()` |

Never derive a db number yourself. Not from `PYTEST_XDIST_WORKER`, not from a literal, and
not by reading it back out of `POPOTO_REDIS_DB.connection_pool.connection_kwargs` — that last
one resolves to the right answer today and is still a second authority for a fact that has
one owner.

## What the guard denies, and why

`tests/conftest.py::_install_redis_flush_ownership_guard` patches `flushdb` and `flushall` on
both the sync and the async Redis class at conftest import, before collection, so it covers
clients built by installed plugins and not only this repo's own code.

- `flushdb()` against a db in `claimed_test_dbs()` — permitted.
- `flushdb()` against anything else — `RuntimeError` naming the attempted db, the claimed
  set, and `scratch_test_db`.
- `flushdb()` against db 0 — `RuntimeError` with its own message and the 2026-06-03
  production-wipe rationale. Ownership subsumes this rule (no test process can claim db 0),
  but the branch stays because the message is what a reader needs. One wrapper, not two
  idioms: two independently-maintained flush guards is how one of them drifts.
- `flushall()` — always denied. It ignores the selected db and wipes every one.
- An empty claimed set denies everything. Fail-closed is safe precisely because of the
  lifecycle table above.

The guard reads the claimed set on every call. It is installed long before any claim exists,
so a captured snapshot would be permanently empty.

## Pool exhaustion

`claim_test_db()` polls the pool at roughly one-second intervals for `TEST_DB_CLAIM_WAIT_S`
seconds (default 30) and then raises, naming the pool size and `scripts/reap-xdist.sh --apply`.
It does **not** fall back to a derived number any more. A colliding database is strictly worse
than a clear failure: it silently produces the corruption this whole mechanism exists to
remove, and a blocked run is recoverable where a corrupted baseline is not.

Thirty seconds is deliberate rather than generous. Two concurrent `-n auto` runs on a 10-core
box demand 20 of 15 slots and each takes about twenty minutes, so in the exact contention a
wait targets, no slot frees inside any tolerable window. A longer wait buys a stall before an
identical error.

Two mechanisms keep the wait from being paid repeatedly. The session-start claim enters the
poll once per process by construction, and a sticky `_CLAIM_FAILURE` memo makes any later
direct caller re-raise instantly. Without both, a function-scoped autouse fixture would pay
the wait per test — roughly ten hours per worker, and structurally invisible to
`--timeout=420` because no single test exceeds it. Note that xdist clones a replacement for a
worker that dies during startup, up to `--max-worker-restart` (default `numprocesses * 4`),
and each replacement is a fresh process with a fresh memo, so "once per process" means once
per worker process.

The registry-unreachable path keeps its legacy fallback: no lock can be taken there at all, so
a collision is unavoidable and degrading loudly beats refusing to run. Its number is still
registered as owned — skipping that would compose with the fail-closed guard into a
whole-process setup outage, turning a documented graceful degradation into an outright one.

## The three writers

### 1. Two call sites still computing the pre-#2606 answer

`tests/unit/test_redis_flush_guard.py` derived `gw{N} -> db{N+1}` in a helper whose docstring
claimed it "matches `redis_test_db()`" — true until #2606 replaced that derivation with the
flock claim. `tests/unit/test_conftest_isolation_guards.py` hardcoded db 15 and called it
"scratch space", but 15 is `_TEST_DB_POOL_MAX`, the top slot of the pool. Under
`PYTEST_XDIST_WORKER=gw3` a process claimed slot 11 while the legacy rule returned 4, so the
flush landed on a sibling run's data.

### 2. An unrestored module reload

`tests/unit/test_index_drift.py` called `importlib.reload(agent.index_drift)` to observe a
module constant, which rebinds `DRIFT_COVERED_MODELS` to a brand-new dict and re-runs the
registrations into it. `tests/unit/test_index_drift_coverage.py` held the orphaned old dict
from its collection-time import, so its restore fixture cleaned the orphan while
`register_drift_model` wrote into the live one: the cleanup silently no-opped and a fake
in-memory model stayed registered for the rest of that worker. `--dist=loadfile` decides
whether the reloader and the victim share a worker, which is the same rotate-run-to-run
signature on a path db ownership cannot touch. The reload is gone, the victim binds through
the module object, and the conftest identity guard now covers module-level registries as well
as `BaseException` subclasses.

### 3. Popoto's bundled pytest plugin — the highest-frequency one

`popoto` registers `pytest11 = popoto.pytest_plugin`, which this repo loads on every run:
`pyproject.toml` addopts disable `postgresql` only. With neither `POPOTO_TEST_DB` nor the
`popoto_test_db` ini option set, the plugin ran on its default of **db 15** and its
function-scoped autouse `_popoto_flush_db` flushed that db before *every* test in *every*
pytest process on the machine.

This is the case that proves the pool's owner is not only this repo's own code, so the rule
generalises: **any installed pytest plugin that touches Redis must be pointed at the claimed
db.** The fix is sited at the connection/claim layer, never at the installed plugin.
`pytest_configure` exports `POPOTO_TEST_DB`, which is the plugin's own documented resolution
input (env > ini > default, read at fixture setup time, long after the hook). Nothing patches,
vendors, or pins popoto. Disabling it with `-p no:popoto` was rejected: it drops the plugin's
per-test `_popoto_reset_async` event-loop reset, which this repo's own fixture does not
replicate.

Two checks keep it honest. A session-scoped `_popoto_client_ownership_check` walks whatever
clients `popoto.redis_db` holds — `POPOTO_REDIS_DB` and `_POPOTO_ASYNC_REDIS_DB` — and asserts
each points at a claimed db, so a future plugin that swaps those globals is caught whatever
version put them there. And `os.environ["POPOTO_TEST_DB"] == str(claim_test_db())` is asserted
as a drift detector: if a future popoto changes its resolution order, that fails loudly
instead of the suite quietly resuming its rotation.

`_POPOTO_ASYNC_REDIS_DB` is `None` at every moment a session-scoped fixture can observe it, and
`None` is its correct state — the plugin nulls it at both setup and teardown of every test so
no client binds to a stale event loop. Skip it; never assert it is non-`None`. An
`AttributeError` raised inside a session-scoped autouse fixture errors every test in the
process during setup.

## Why enforcement rather than another sweep

This is the fifth pass over the same substrate. #2117 fixed one victim of a cross-process
collision. #2606 replaced the derivation with the flock claim — the right fix — but changed
the *meaning* of the test db without sweeping every consumer, and nothing existed to catch
the ones it missed. #2624 swept one more consumer, found by a failing test rather than by a
guard.

The pattern underneath all of them: db ownership was a convention every call site was trusted
to re-derive correctly, and re-deriving it wrong was silent. Each fix corrected one
derivation; none made a wrong derivation *detectable*. Enforcing at the point of damage means
a stale derivation fails at its own line instead of corrupting a stranger three files away —
the same promotion from discipline to mechanism that #2163 applied to pub/sub channels.

A construction-time backstop (an AST walk rejecting any `db=` value that does not come from
the claim API) is tracked separately as #2655. It prevents the next recurrence; it stops none
of the three writers above, which is why it ships on its own cadence.

## Relationship to #2645

#2645's Layer 1 is a guarded connection helper that refuses dangerous operations on db 0. This
guard is the same mechanism generalised: db 0 is simply the db no test process can ever claim,
so "deny `flushdb` on any db not in `claimed_test_dbs()`" subsumes "deny `flushdb` on db 0".

#2645's open question — rename-command versus ACLs for legitimate db 1-15 flushes — is looking
for a **by-db discriminator**. The per-process flock-claimed set is exactly that: ownership is
decided by a kernel primitive, is queryable at call time, and already distinguishes a
legitimate test flush from a cross-process wipe. A server-side rule should key off the same
claim registry rather than a static db allowlist.

## Run provenance

Each process reports the db it owned. Under `-n0` the master emits it in the pytest header;
under xdist each worker writes it to `config.workeroutput` and the controller renders the
collected values in the terminal summary, because xdist never surfaces a worker's header. When
a result looks wrong, the first question is "did another process own my db", and this answers
it from the log instead of a fresh investigation.

## Related

- [`tests/README.md`](../../tests/README.md) — the authoring rules, in the place an author reads
- [`docs/features/test-isolation-hardening.md`](test-isolation-hardening.md) — the single-run xdist isolation work this builds on
- `tests/db_claim.py` — the claim, the pool, and the exhaustion policy
- `tests/unit/test_conftest_isolation_guards.py` — the deterministic acceptance suite
