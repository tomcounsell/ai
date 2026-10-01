# tools

Non-core components and vendor-dependent tooling.

## Scope

- Anything the core can run without: vendor API clients, sandbox and container helpers, ingestion, search.
- Each tool declares its effect class and reaches the world through the broker.
- A tool is extracted on a demonstrated second need, never on a first. A tool unused for ninety days is deleted by default.

Governed by [docs/architecture.md](../docs/architecture.md) (effect classes and the broker) and [docs/tech-stack.md](../docs/tech-stack.md) (the broker's performers).

## Performers

- `workspace.py`: `workspace_write` (`propose`) and `outbox_send` (`act`), local performers for the kernel's own tests and smoke.
- `push_branch.py`: `push_branch` (`act`) pushes one commit of a task's workspace to one branch of its `origin`, never with force, after Tom's tap.

## Imports

- May import: `core/`.
- Imported by: `core/` through the broker, `harnesses/`, `routines/`, and `tests/`. Tools do not import each other's internals.

## Effect classes

- Every tool declares one class per operation: `read`, `propose`, or `act`.
- `read`: queries and fetches with no effect.
- `propose`: sandbox writes, branches, drafts, anything that can be withdrawn.
- `act`: merge, send, pay, deploy. Released by the broker only with Tom's approval on the ledger.
- A tool that cannot say which class an operation is does not ship.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Anything the kernel depends on to enforce authority. That is `core/`.
- Comms channels. Those are `bridges/`.
- Harness wrappers. Those are `harnesses/`.
- Validators, linters, or checks that police agent behavior.
- Speculative tools written for a first need.
