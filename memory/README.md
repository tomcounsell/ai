# memory

What Valor remembers across sessions.

## Scope

- The operator record: what Valor knows about Tom and his preferences.
- Reading and curating the corrections and exemplar streams (same store, distinct source class, the exemplars recording work Tom loved and why). `core/` keeps both in the ledger and owns them; memory never writes them.
- Episodic memory.

Built last, on popoto over Postgres. Until popoto's Postgres support ships, this directory holds this README and the port `core/` reads memory through.

Governed by [docs/data.md](../docs/data.md) (Memory, last).

## Imports

- May import: `core/` ports only.
- Imported by: nothing directly. `core/` reads memory through its own port; nothing else reaches in.

## Effect classes

Holds none. Writes to memory are kernel-mediated records, not effects on the world.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- The effect ledger. That is `core/`, and memory never writes to it.
- Conversation threads and transcripts. Harness transcript capture is `harnesses/`.
- Retrieval tooling for outside sources (search, ingestion). That is `tools/`.
- A second store. Memory is Postgres, like everything else.
