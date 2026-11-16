# A dossier per person, looked up by name or description (P-005, #3622)

Approved 2026-11-16 after Tom's call. Case (observed, 2026-11-10): a client's
DM reply reached a session that knew nothing of the report Valor had sent
that person, and it denied sending it. Built on the dated facts (#3684,
`memory/facts.py`). No schema change, no check or gate added.

Answers (product owner, 2026-11-16): facts about people are written from now
on, as Valor works, with no backfill over past chats (cut, named in the
delivery); a person asking what Valor knows about them hears name, role and
organisation only; Tom and anyone marked as on his team are internal,
everyone else (unmarked included) external, and internal ranks slightly
higher on a loose match.

## What is built

1. **Conventions on the existing model.** A person is an `Entity` of kind
   `person` with aliases, kept per project and client as every fact is. Facts
   `--about` them use free-text kinds the dossier orders first: `team`
   (claim `internal` or `external`), `role`, `organisation`, `ask`,
   `decision`, `owed`, `delivered`; any other kind follows. Something owed
   that is then sent is the `owed` fact superseded by a `delivered` one, so
   the live dossier shows what was sent and when.
2. **`memory/facts.py` `people(...)`**, a read: `about` is a name, alias or
   loose description. An exact alias of a person returns that one. Otherwise
   each person scores by query words (filler dropped, case folded), each
   word once where it best appears (as built: a name or alias 3, a live
   `team`, `role` or `organisation` claim 2, any other live claim 0.5),
   plus 0.5 if internal; best 5, ties by name. Internal means a live `team`
   fact `internal`, or a name or alias matching the operator in
   `persona/identity.toml`. Each match is a dossier: name, aliases, internal
   or external, and the live facts about them, grouped by kind in the order
   above, each as the existing `entry` (dated, sourced, inferred or
   confirmed). No match: an empty list.
3. **The port and the turn.** `core/memory.py` `people(conn, spec, about)`;
   `core/record.py` verb `people`; `valor-record person WORDS [--json]`
   prints each dossier. Only the asking project's and client's people.
4. **Calls.** The Live session gets a `person` tool (`{"about": ...}`),
   answered in-process from the call's spec through the port, as `backlog`
   is from the Sheet, plus one instruction line: look a person up before
   saying anything about them.
5. **Persona, `persona/conduct.md` Run the project**, a short "Know the
   people" item: look a person up (`valor-record person`) before assuming
   anything about them; when someone asks, decides, is owed, or is sent
   something, record it about them (`entity add NAME --kind person`, then
   `fact add --about NAME --kind ask|decision|owed|...`, superseding `owed`
   with `delivered`), marking team members `team internal`; a person asking
   what Valor knows about them hears their name, role and organisation only.
   The "In a call" section gets the same lookup line in a sentence.
6. **Docs.** `docs/memory.md` Facts (People), `memory/README.md`,
   `tools/README.md`, the `valor-record` docstring.

## Not built (no-gos and cuts)

- No dossier injected when a name is mentioned: nothing renders facts into a
  Brief, a chat prompt or a call's instructions; only the lookup reads them.
- No view for a person of their own dossier. The self-view rule is persona
  text, not a code filter (a filter would be a refusal, i.e. governance).
- No backfill from past chats or the old system's memories (cut).
- No new store, model field or migration.

## Tests (`tests/test_facts.py`, `tests/test_call.py`, real Postgres, made-up names)

1. By name and by alias: the dossier holds the person's live facts grouped
   in order, each with its date and source; an `owed` superseded by
   `delivered` shows only `delivered`, dated (the case's shape).
2. By loose description ("the finance lead at the client"), matched through
   a `role` fact; with two equal matches the internal one ranks first; an
   unmarked person is external; the operator's name is internal unmarked.
3. Another project's or client's person never returns.
4. A turn's `valor-record person` on a live gateway prints the dossier.
5. Absent person: a chat session's next prompt and Brief, for a message
   naming a person who has a dossier, carry none of its facts; the lookup
   in that session returns them.
6. A call's `person` tool returns the dossier; its instruction carries the
   lookup line.

The full suite stays under five minutes. Done means Tom (or a teammate)
asks in a chat about someone Valor has worked with and gets dated facts
from the dossier, recorded with the date.
