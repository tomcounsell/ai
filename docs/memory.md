# Memory

Serves Mission item 5 and the Evidence item "Tom's feedback, both
directions": a preference Tom stated in one task reaches the Brief of a
later task in the same project that never stated it.

Memory is popoto [20] 1.10.0's Postgres backend, owned by `valor_memory`,
which holds no privilege on `events` or `documents`
(docs/plans/b1-memory.md). `VALOR_MEMORY=off` means no import, no ingest,
and no section.

**Where it lives.** The store moves to the shared Postgres that all four
Macs reach, one Mac per client ([machine.md](machine.md), The shared memory
store), so working standards travel between clients while client facts stay
on their Mac (docs/business.md, constraint "Generic across businesses").
Every record carries its client (its project spec's `client`), its project,
and its ledger (`<machine>/<kernel database>`, since ledger ids are unique
only within one ledger). The store's connection string is the setting
`memory_dsn` (`VALOR_MEMORY_DSN`), apart from the kernel's; its host is
chosen with a second Mac's install, and the Cowboy's cutover leaves it
unset. Until it is set, the store is the schema `memory` in the
Mac's own kernel database, as memory's role.

- **Memory grants nothing.** Retrieved content can act as instructions to
  a model [7], so nothing read from memory changes a ceiling or a
  grant: the port, `core/memory.py`, returns a string.
- **Memory never writes the ledger.** The port reads the ledger as
  `valor_kernel` and hands `memory/` plain data. Once the turn slot is
  released, `run_turn` ingests the rows memory has not taken: the
  `task.started`, answers, and feedback of tasks that have run a turn, the
  `corrections` stream (which `core/` owns and renders whole into every
  Brief), and each `turn.ended`'s transcript text entries without the
  turn's prompt, tool uses, or tool results, and the `memory` stream of
  standard-of-working marks. One unit per row, keyed by ledger and ledger
  id, under an advisory lock; `python -m core memory ingest` runs the same
  by hand.
- **Raw entries**, no model call: raw turn ingestion beat LLM extraction
  on judged accuracy in popoto's measurement [20].
- **Recall.** A working session's Brief gains a Remembered section after
  the corrections: BM25 over the task's instruction, among the records of
  the task's own project (its spec's `repo`), plus the records Valor has
  judged to be standards of working, from any client, all from before the
  task began, less corrections, ten records within 4000 estimated tokens,
  in ledger order (the task's own ledger's records first, then other
  ledgers' by time). No fixed rule decides which records are standards of
  working; Valor judges each one and records it with `python -m core memory
  mark <record>`, a `memory.marked` row on the ledger's `memory` stream, so
  a rebuild keeps it. Each record is quoted and escaped as data, labelled
  by its name (`<ledger>/<key>`, which a mark or a correction names) and
  its row's provenance; a transcript entry is always the turn's. Fresh
  sessions and tasks with no project get none; a failure renders `Memory:
  unavailable: <reason>`. A Mac that cannot reach the store (its server
  does not accept a connection within two seconds) runs the turn without
  recall, does not wait, and its Brief records `Memory: recall skipped:
  the store could not be reached (<reason>)`; the ingest after the turn is
  skipped too, and its rows are taken by the next ingest that reaches it.
- **A client fact in the wrong Brief.** A fact about one client that turns
  up in another client's Brief is recorded as a correction naming the
  record it came from (`python -m core correct TEXT --record <record>`),
  and once ingested that record stops being recalled outside its client,
  marked or not.

Turn-written text reaches a later task's Brief only as a record labelled
as that turn's, escaped, scoped to its project or judged a standard of
working, and kept out of fresh sessions; the effect ceiling bounds it, as
with any input a turn reads.

The backup's digests cover `events` and `documents`, not the schema
`memory`: its records are rebuilt by ingesting the ledger again
([machine.md](machine.md), Backups). On the shared store, a rebuild ingests
all four Macs' ledgers, each Mac's kernel ingesting its own: what is taken
is kept per ledger, so one Mac's ingest never passes over another's rows.
A store written before records named their ledger and client is dropped
by `python -m core migrate` and rebuilt by the next ingest.
