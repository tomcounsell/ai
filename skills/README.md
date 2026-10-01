# skills

Versioned skills.

## Scope

The structure of the versioned skill system, and of skills that refactor repo-specific skills in other repos, is deferred until Tom's requirements are gathered. Until then the directory holds plain files, unversioned:

- `sdlc/`: one file per stage of the SDLC state machine (`clarify.md`, `plan.md`, `critique.md`, `build.md`, `review.md`, `docs.md`, `patch.md`), each stating the stage's goal and exit evidence, not steps, and `channel.md`, how a turn reaches Tom through `.valor/`. `core/tasks.py` renders `channel.md` (with the effects the registered performers offer) and the current state's file into a turn's Brief. The judge is a classifier prompt (`docs/judgement-layer.md`) and the merge is the kernel's, so neither has a file. `critique.md`, `review.md`, and `docs.md` wait for the fresh sessions of milestone 1.4.

The versioned system is governed by no doc yet; one is written when the requirements are gathered. Until then [docs/harnesses.md](../docs/harnesses.md) (Skill rendering) and [docs/sdlc-state-machine.md](../docs/sdlc-state-machine.md) (stages are skills) state what skills must fit.

## Imports

Deferred with the structure.

## Effect classes

Holds none. A skill describes work; effects happen through the broker under the task's ceiling.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Anything written ahead of the requirements.
- Kernel rules expressed as skill text. Authority is in `core/`.
