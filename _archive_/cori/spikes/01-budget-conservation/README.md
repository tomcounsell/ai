# Spike 01: Budget conservation under concurrency

Closes tech-stack §3 ("budget conservation in a transaction") and spike 4 in §14.

## Question

If the kernel role can only INSERT and SELECT on event tables, and `delegate()`
locks the parent, checks remaining, and inserts an allocation event, does the
sum of child allocations ever exceed the parent's budget when N workers race on
one parent? What does it cost?

## Method

- Private Postgres 18 cluster on port 5499 (`spikes/.pgdata-01`, gitignored),
  database `cori_spike_budget`. Nothing else is touched.
- `schema.sql`: `tree.nodes` (budget fixed at creation) and `tree.budget_events`
  (append-only ledger of `allocate` and `consume` rows). Role `cori_kernel` gets
  SELECT and INSERT and nothing else. BEFORE UPDATE / DELETE / TRUNCATE triggers
  raise on both tables as the second lock.
- `kernel.py`: `delegate()` and `consume()` in one transaction each. Remaining
  budget is always derived from the ledger, never stored. Three lock strategies:
  `advisory` (`pg_advisory_xact_lock(hashtext(parent))`), `row`
  (`SELECT ... FOR UPDATE` on the parent node), and `none` as the control.
- `check_grants.py`: every mutation the kernel role must fail at, tried for real.
- `test_conservation.py`: Hypothesis stateful machine (60 examples, 30 steps)
  running random `delegate` / `consume` sequences against the real database,
  checking the DB refuses exactly when a pure Python model says it should, and
  that no node's outflow ever exceeds its budget.
- `bench.py`: 16 async workers, 60 attempts each, random amounts 1..100 against a
  parent with budget 20,000, per strategy. Plus a single-worker baseline.

Run: `./run.sh` (starts the cluster, runs everything, stops it).

## Numbers

Apple silicon laptop, local Postgres 18, `fsync=on`.

| Strategy | Role | Workers | Attempts | Over-allocation | Throughput | p50 | p95 |
|---|---|---|---|---|---|---|---|
| advisory lock | cori_kernel (INSERT+SELECT) | 1 | 60 | 0 | 1,027 delegate/s | 0.74 ms | 1.44 ms |
| advisory lock | cori_kernel (INSERT+SELECT) | 16 | 960 | 0 | 1,642 delegate/s | 7.9 ms | 15.4 ms |
| row lock (FOR UPDATE) | cori_kernel_fu (+UPDATE grant) | 16 | 960 | 0 | 1,615 delegate/s | 8.0 ms | 13.8 ms |
| none (control) | cori_kernel | 16 | 960 | **402 over budget** (20,402 / 20,000) | 4,012 delegate/s | 3.6 ms | 5.2 ms |

Hypothesis stateful test: passes. The audit query (any node with outflow >
budget) returns empty after every step.

Grant check, role `cori_kernel`: INSERT allowed on both tables; UPDATE, DELETE,
TRUNCATE, and DROP TRIGGER all refused by the grant before the trigger is even
reached. Under `cori_kernel_fu` (which has the UPDATE grant so FOR UPDATE works)
a real UPDATE is refused by the trigger instead.

## Surprises

1. **`SELECT ... FOR UPDATE` needs the UPDATE privilege.** Postgres refuses a row
   lock under a role that has only SELECT and INSERT
   (`permission denied for table nodes`). The tech-stack recipe, "kernel role has
   INSERT and SELECT and nothing else" plus "SELECT ... FOR UPDATE on the parent
   node", cannot both be true on the same table. Two fixes work:
   - `pg_advisory_xact_lock(hashtext(parent_id))`: no table privilege needed,
     same throughput and latency, and the grant stays minimal. This is the one
     used by default here.
   - Grant UPDATE on `nodes` and rely on the trigger to reject actual updates.
     Works, but the grant is no longer the first lock; the trigger is.
2. **The lock is worth about 2.4x in throughput and nothing else.** Unlocked
   delegation is faster (4,012 vs 1,642 per second) and over-allocated by 2% in
   under a second. That is the whole argument for doing arithmetic in the
   database rather than in the LLM or the harness.
3. **Contention cost is modest.** p95 goes from 1.4 ms uncontended to 15 ms
   with 16 workers serialised on one parent. At Cori's expected rate (tens of
   delegations per minute, not thousands per second) this is invisible.
4. Remaining budget derived by `SUM` over the ledger is fine at this scale. A
   node with tens of thousands of events would want a materialized running
   total in a kernel-owned (non-append-only) cache table. Not needed for M0.

## Not covered

- Cancel / re-plan returning a child's unspent budget to the parent. The
  ledger shape supports it (a `release` event) but it was not tested.
- Cross-node deadlock when two delegations lock different parents in different
  orders. Advisory locks keyed per parent are taken one at a time here.

## Recommendation

**Assumption holds, with one edit to the docs.** Budget conservation in a
single transaction with an append-only ledger is cheap and correct under
concurrency. Change tech-stack §3 from "SELECT ... FOR UPDATE on the parent
node" to "take a transaction-scoped advisory lock keyed on the parent node
id", because FOR UPDATE and an insert-only grant are incompatible. Keep the
trigger as the second lock; the grant already refuses everything the trigger
would.
