# routines

Every scheduled task and runner.

## Scope

- launchd plists and the routines they run.
- Every routine is an objective in `core/` with an effect ceiling. A routine is never a bare script.
- Extraction follows the mission rule: a routine exists on a demonstrated second need, and one unused for ninety days is deleted by default.

Governed by [docs/routines.md](../docs/routines.md); launchd on the machine is [docs/machine.md](../docs/machine.md).

## Imports

- May import: `core/`.
- Imported by: nothing. launchd starts routines; `tests/` exercises them.

## Effect classes

- A routine's ceiling is set when its objective is committed and never widens at run time.
- Most routines are `read` (sweeps, reports) or `propose` (drafts, branches, proposed memory).
- An `act` inside a routine needs Tom's tap per action like any other. A schedule is not a standing approval.
- Spend is metered on each run's task and the routine's spending over its period is reported; money never stops a routine.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Long-running services (bridges, the kernel process). Those are started by launchd but live in their own directories.
- Watchdogs that kill or restart other processes on heuristics. Stop and recovery are `core/`.
- Scripts that run outside an objective in `core/`.
- Scheduling logic inside `tools/` or `bridges/`.
