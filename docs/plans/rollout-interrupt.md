---
status: planned
type: feature
tracking: none
---
# Rollout interrupt: a restart cancels running jobs instead of waiting

Split out of `docs/plans/update.md` (the user, 2026-10-20); not built in that run.

**Idea.** A restarting rollout cancels every job, as `close` does, and names them on
`rollout.restarting`; each kind comes back in the new kernel (`recover`, `broker.reconcile`, an
empty `seen`, `_ready`). `_holding` and `restart_due` go. Tom, 2026-10-20: "a stuck process that
needs the update gets fixed" and "we gain telemetry on how good our durability is".

**Measured.** The hold on the Cowboy, merge outcome to `rollout.restarting`, 21 rollouts: median
0.1 min, longest 8.7.

**Round-2 critique findings about the window** (between fast-forward and the cancel):
- Jobs run on through backup, `uv sync` and migrate before the cancel (`core/serve.py:639-662`):
  old modules meet the migrated schema and a rewritten `.venv`.
- Function-level imports load moved files mid-job (`core/notices.py:145,181`).
- A failed migrate's `_step_back` re-syncs `.venv` under running jobs (`core/serve.py:680-692`).
- A raise in the window writes `step.failed`, counting toward the four-in-a-row task end
  (`core/README.md:68`).
- Do not move the cancel earlier: a cancelled turn gets no `turn.ended` until `recover`.

**Suggested tests.** A job raising `ImportError` mid-window; a migrate failure with
`plan.sync=True` while a job runs.
