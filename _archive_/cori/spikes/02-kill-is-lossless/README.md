# Spike 02: Kill is lossless

Closes architecture §1 ("Cori can be killed and restarted mid-task with zero
loss") and decision 1 of the five ("stateless compute over a stateful store").

## Question

If a control loop renders its context each turn purely from the event store
and keeps nothing in process memory, does SIGKILL at a random point lose
anything? And what happens to an external effect when the kill lands between
the intent event and the outcome event?

## Method

- `loop.py`: a stateless loop over Postgres (`es.events`, append-only by
  trigger). Each turn takes an advisory lock on the objective (single-flight),
  folds every event from seq 0 into a state, asks a deterministic scripted
  agent for the next action, appends events, commits. The script has 11 steps:
  plan, think, five external effects, one delegate with a report that lands as
  an event, and done. Each effect is `effect.intent` -> commit -> write to
  `es.world` on a separate autocommit connection -> `effect.outcome` -> commit,
  with a 20 ms sleep in each window so kills land in them.
- Three policies for a dangling intent found on restart:
  - `naive`: re-run the effect (at-least-once).
  - `at_most_once`: never re-run; record `outcome=unknown`. This is what the
    brief and the architecture text describe.
  - `reconcile`: look the idempotency key up in the world; re-run only if
    absent, else record `outcome=recovered`.
- `chaos.py`: per policy, one unkilled reference run, then 50 trials. Each
  trial spawns the loop, sleeps a uniform random time within the reference
  wall time, SIGKILLs it, restarts it, lets it finish, then compares the folded
  state and the world (count of rows per effect key) with the reference.
  `--kills 3` kills three times before the final run.

Run: `./run.sh [--trials 50] [--kills 1]`.

## Numbers

50 trials per policy, one SIGKILL each (seed 7). "Killed in flight" excludes
trials where the loop finished before the kill landed.

| Policy | Killed in flight | Event-log state converged | World converged | Kills between intent and outcome | Duplicated effect | Lost effect |
|---|---|---|---|---|---|---|
| reconcile | 49 | 49/49 | 49/49 | 19 (11 recovered, 8 re-run) | 0 | 0 |
| naive | 50 | 50/50 | 39/50 | 22 | 11 | 0 |
| at_most_once | 49 | 29/49 | 39/49 | 20 | 0 | 10 (and 20 outcomes recorded as unknown) |

Three SIGKILLs per trial (seed 11): reconcile 47/47 state and 47/47 world;
naive 24 of 50 runs with a duplicated effect; at_most_once 11 lost effects
and 37 runs ending with an `unknown` outcome in the state.

Time from restart to the first new event (Python start, connect, lock, fold
of up to 27 events): median 35 ms, max 65 ms. The fold itself is under 1 ms.

The resumed-from-step histogram is spread across all 11 steps, so kills
covered the whole script, and no restarted run ever repeated a completed step.

## Surprises

1. **The event log is lossless in every run, for every policy.** 146 of 146
   killed runs reached the same terminal event-log state up to the outcome
   label. Postgres rolled back whatever transaction was open at the kill, the
   advisory lock released with the connection, and the restart resumed from
   the last committed event. Nothing about "stateless compute over a stateful
   store" needed defending.
2. **"Lossless" and "no duplicated or lost effects" are different properties,
   and the second is a policy choice, not a consequence of the first.** With
   about 40% of kills landing between intent and outcome (two 20 ms windows in
   a 540 ms run), at-least-once duplicated an effect in 22% of killed runs and
   at-most-once lost one in 20%. Only reconcile-by-idempotency-key got both to
   zero, and it needs the external system to be queryable by that key.
3. **The at-most-once policy the architecture describes leaves the state
   lying about the world.** In 10 of 20 dangling cases the world had the
   effect but the log said `unknown`; in the other 10 the log said `unknown`
   and the world had nothing. The kernel cannot tell those apart without
   asking the world, which is exactly what the action broker is positioned
   to do with idempotency keys.
4. **My first version of the loop re-ran effects after every recovery**
   because the scripted agent advanced by step counter instead of by looking
   at the projected effects. A stateless loop is only as lossless as its
   projection: anything the agent decides from must be in the fold. This is
   the kind of bug the chaos harness exists to catch, and it caught it on the
   first run.

## Not covered

- An LLM in the loop. The scripted agent is deterministic, so "same final
  state" is exact. With a model, convergence means "same effects and same
  ledger", not the same prose.
- A kill inside Postgres itself, or a kill of the world.
- Single-flight across two live loop processes (the advisory lock was only
  exercised sequentially here).

## Recommendation

**Assumption holds, with a caveat that belongs in the docs.** Kill is lossless
for the event log at any point, and resume costs tens of milliseconds. The
caveat: state the effect semantics as "intent, outcome, and reconcile by
idempotency key through the action broker", because at-most-once as written
loses effects, and at-least-once duplicates them. Every class 2 and 3 action
in the broker needs an idempotency key the broker can query the target system
with; that is what makes a kill between intent and outcome recoverable rather
than merely visible.
