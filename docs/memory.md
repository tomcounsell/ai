# Memory

Serves Mission item 5 and the Evidence item "Tom's feedback, both
directions": a preference Tom stated in one task reaches the Brief of a
later task in the same project that never stated it.

Memory is popoto [20] 1.10.0's Postgres backend, owned by `valor_memory`,
which holds no privilege on `events` or `documents`. `VALOR_MEMORY=off` means no import, no ingest,
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
  `valor_kernel` and hands `memory/` plain data. Once the turn's slot is
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
- **Search.** Tom, 2026-10-27: "memory search is critical. it should be
  a readily avialable skill just like chat history search". Any turn, a
  chat session or a task in any stage, runs `valor-record memory "<words>"
  [--limit N] [--offset N] [--json]`. The kernel answers it on the turn's
  gateway token, as it answers the rest of `valor-record` ([harnesses.md](harnesses.md),
  Reading the project's record): `core/record.py`'s `memory` verb takes the
  project and client from the asking task's spec and calls
  `core/memory.py` `search`, which runs recall's own candidates and BM25
  ranking (`memory/recall.py`). The scope is recall's: the project's
  records on this Mac's ledger, plus standards of working from any client
  and ledger, a confined record only for its own client, never a
  correction. Unlike recall it has no start-of-task bound, so a long chat
  session finds what was ingested after it opened. A page is ten records
  (at most fifty) and stops before the one that would pass recall's 4000
  estimated tokens, saying the offset of the next; each record prints as
  its Remembered entry, quoted as data. It reads only: no ingest, no
  write. Memory off, or a store that cannot be reached, answers with the
  reason. A chat message's own words are not a record (a turn's prompt is
  left out of its transcript entries); `valor-record messages --search`
  finds them.
- **The old system's memory.** `scripts/cutover_export_memory.py` reads an
  old Redis (or a dump restored into a throwaway one) and dates each record
  from the best source it holds: its Telegram message, an extraction's
  message or session, a document's file date, a date in its fields, and
  with `--telegram` the chat's own history; failing those, an upper bound
  (the record that superseded it, its first outcome), labelled `(no later
  than)`. `scripts/memory_import_prep.py EXPORT... OUT/ [--imported
  ROWS]` chooses the old records Tom kept (human and agent, not
  superseded, no session summaries), maps each to its project, moves one
  whose own Telegram group or extraction label names another project,
  drops by `claude -p` those not worth recalling on a future task, and
  deduplicates the rest (near-exact by word overlap, paraphrases by
  `claude -p`), keeping each record already imported on some Mac as its
  group's keeper, past every filter; it writes `import.jsonl` and a
  `report.md`, listing every move and drop and the dated, bounded and
  undated counts per project, that Valor reviews first. `python -m core
  memory import FILE` then appends one `memory.imported` row per line on
  the `memory` stream for the projects this Mac serves, once per old
  `memory_id`; ingest makes each a record of origin `imported` in the
  line's own project and client, recalled like any other, its time its
  `occurred_at` when it has one, which its label shows. A line for a
  `memory_id` already imported here whose date, project or client differ
  appends a second row, served here or not; ingest replaces the first
  row's record under the same key and ledger id (marks and confinements
  still name it, recall's bound does not move), or deletes it when the
  line moves it to a project this Mac does not serve. A rerun appends
  nothing.
- **Measuring recall.** Tom, 2026-11-08: "I'd like to better measure its
  effectiveness at long term, episodic, and seasonal memory". A private set
  of dated questions (long-term: a superseded decision, the current one
  gold; episodic: before or after a dated event; seasonal: a monthly or
  weekly cycle, and a topic quiet for two to five months that comes back;
  client confinement), each with the day it is asked, is replayed against
  the four Macs' stores as they stood that day: `python -m core memory
  bench QUESTIONS DUMP... --out DIR --scratch DSN` loads `pg_dump -Fc -n
  memory` files into a scratch database (never the live store), ranks the
  records by time so recall's own candidates and ranking
  (`memory/bench.py`) see only the records from before each question,
  and scores whether a gold record is in the top ten and reaches the
  Brief, with and without undated imports, and whether any other client's
  record reaches it. It writes the same questions as a fixture for
  popoto's `dated` benchmark, which compares BM25 alone with hybrid
  retrieval. The questions, gold answers and results stay in the private
  Valor folder; the routine `memory-bench` re-runs it on the 1st of each
  month against a fixed question set, new calendar questions scored as a
  set of their own.
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
