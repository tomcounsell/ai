# Memory

Serves Mission item 5 and the Evidence item "Tom's feedback, both
directions": a preference Tom stated in one task reaches the Brief of a
later task in the same project that never stated it.

Memory is popoto [20] 1.10.0's Postgres backend in the kernel database's
schema `memory`, owned by `valor_memory`, which holds no privilege on
`events` or `documents` (docs/plans/b1-memory.md). `VALOR_MEMORY=off`
means no import, no ingest, and no section.

- **Memory grants nothing.** Retrieved content can act as instructions to
  a model [7], so nothing read from memory changes a ceiling or a
  grant: the port, `core/memory.py`, returns a string.
- **Memory never writes the ledger.** The port reads the ledger as
  `valor_kernel` and hands `memory/` plain data. Once the turn slot is
  released, `run_turn` ingests the rows memory has not taken: the
  `task.started`, answers, and feedback of tasks that have run a turn, the
  `corrections` stream (which `core/` owns and renders whole into every
  Brief), and each `turn.ended`'s transcript text entries without the
  turn's prompt, tool uses, or tool results. One unit per row, keyed by
  ledger id, under an advisory lock; `python -m core memory ingest` runs
  the same by hand.
- **Raw entries**, no model call: raw turn ingestion beat LLM extraction
  on judged accuracy in popoto's measurement [20].
- **Recall.** A working session's Brief gains a Remembered section after
  the corrections: BM25 over the task's instruction, among the records of
  the task's project (its spec's `repo`) from before the task began, less
  corrections, ten records within 4000 estimated tokens, in ledger order.
  Each record is quoted and escaped as data, labelled by its row's
  provenance; a transcript entry is always the turn's. Fresh sessions and
  tasks with no project get none; a failure renders `Memory: unavailable:
  <reason>`.

Turn-written text reaches a later task's Brief only as a record labelled
as that turn's, escaped, scoped to its project, and kept out of fresh
sessions; the effect ceiling bounds it, as with any input a turn reads.

The backup's digests cover `events` and `documents`, not the schema
`memory`: its records are rebuilt by ingesting the ledger again
([machine.md](machine.md), Backups).
