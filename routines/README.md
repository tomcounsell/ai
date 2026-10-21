# routines

Every scheduled task and runner.

## Scope

- Each routine is a directory holding `routine.toml`, and a runner module when its work is not a plain task. `python -m core routine NAME --plist` prints the launchd plist; none is committed.
- Routines with runner modules: `expiry` (lists what is past ninety days, reports to Tom, deletes nothing) and `verify` (the suite at the branch head, whose red groups start fix tasks through `core/findings.py`). `invoices` has none: it names the kernel's `task` runner (`core/routines.py`), which starts one SDLC task with its instruction, only on the Mac its project is placed on.
- Every routine is an objective in `core/` with an effect ceiling. A routine is never a bare script.
- Extraction follows the mission rule: a routine exists on a demonstrated second need, and one unused for ninety days is deleted by default.

Governed by [docs/routines.md](../docs/routines.md); launchd on the machine is [docs/machine.md](../docs/machine.md).

## Imports

- May import: `core/`.
- Imported by: `core/__main__.py`, the composition root, which runs a routine's runner module. launchd starts routines; `tests/` exercises them.

## Effect classes

- A routine's ceiling is set when its objective is committed and never widens at run time.
- Most routines are `read` (sweeps, reports) or `propose` (drafts, branches, proposed memory). `conditions` is `propose`: it reads businesses' data and drafts agenda items for the next strategy meeting, and sends Tom nothing.
- An `act` inside a routine is performed like any other `act`: when requested inside the routine task's ceiling, with a report to Tom. The expiry sweep's deletion merge lands as any merge does, when its delivery passes and the merge predicate holds.
- Spend is metered on each run's task and the routine's spending over its period is reported; money never stops a routine.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Long-running services (bridges, the kernel process). Those are started by launchd but live in their own directories.
- Watchdogs that kill or restart other processes on heuristics. Stop and recovery are `core/`.
- Scripts that run outside an objective in `core/`.
- Scheduling logic inside `tools/` or `bridges/`.
