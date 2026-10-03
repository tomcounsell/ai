# docs

Documentation that describes the system as it is.

## Scope

- `docs/features/`: one doc per mechanism, naming the mission item it serves or the constraint it enforces.
- `docs/plans/`: plans, each with `tracking:` frontmatter, and records such as the demonstration.
- `docs/conventions/`: conventions that hold across the repository.
- `docs/sdlc/`: repo-specific addenda for SDLC stages.
- Top-level docs that each directory README points at: `architecture.md`, `mission.md`, `tech-stack.md`, `judgement-layer.md`, `sdlc-state-machine.md`, `data.md`, `persona.md`, `harnesses.md`, `browser.md`, `routines.md`, `emulator.md`, `machine.md`, and `bridges/`.

Governed by [mission.md](mission.md) (the constraint "Docs describe reality").

Docs describe reality: the new status quo only, no history. Every design assumption cites `REFERENCES.md` or is marked as a gap.

## Imports

Docs import nothing. Every directory README points at the doc that governs it.

## Effect classes

Holds none.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Code, generated reports, or logs.
- History, migration notes, or descriptions of what the system used to do.
- Docs that grade the system's own narration. Docs are checked by a blind verifier reading the doc as the contract.
