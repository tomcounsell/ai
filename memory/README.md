# memory

What Valor remembers across sessions.

## Scope

- Episodic memory: raw records of Tom's words (task instructions, answers, feedback), the corrections stream, and the text entries of each turn's transcript, taken from the ledger after each turn.
- Recall: the Remembered section of a working session's Brief, found by BM25 among one project's records from before the task began, quoted and escaped as data.
- `records.py` holds the popoto models and their Postgres backend (schema `memory`, role `valor_memory`); `ingest.py` turns a ledger row, handed over as plain data, into records; `recall.py` searches and renders.

Built on popoto 1.10.0's Postgres backend. `VALOR_MEMORY=off` means nothing here is imported.

Governed by [docs/data.md](../docs/data.md) (Memory, last) and [docs/plans/b1-memory.md](../docs/plans/b1-memory.md).

## Imports

- May import: popoto. Nothing from `core/`: the port hands over plain data and memory's own DSN, never a kernel connection.
- Imported by: `core/memory.py`, the port, only. Nothing else reaches in.

## Effect classes

Holds none. Writes to memory are kernel-mediated records, not effects on the world, and nothing read from memory grants anything.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- The effect ledger. That is `core/`, and memory never writes to it.
- Conversation threads and transcripts. Harness transcript capture is `harnesses/`.
- Retrieval tooling for outside sources (search, ingestion). That is `tools/`.
- A second store. Memory is Postgres, like everything else.
