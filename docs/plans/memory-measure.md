---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-11-09
---

# Old memory dated and imported everywhere; a private memory measurement

**Ask.** Tom, valor team chat, 2026-11-08 to 11-09: measure popoto's
long-term, episodic and seasonal memory (seasonal: "Both a and b", calendar
recurrence and topics resurfacing after months); "Yes to both" to the test
set and to importing the old May-Oct memories.

**Premise, checked.** The import path exists (docs/plans/memory-import.md):
prep, then `python -m core memory import` (one `memory.imported` row per old
`memory_id`), made a record by ingest, keyed `<ledger id>.<n>`. The other
three Macs have an `import.jsonl` (`~/valor-retired/cutover-*`); the Cowboy
has none and holds no record of origin `imported`. Imported rows carry no
date. Tom's 2026-10-21 rules stand for what is imported.

## What is built

1. **Dates.** `scripts/cutover_export_memory.py` reads a dump restored into
   a throwaway Redis (never a live one) and adds `occurred_at` and
   `dated_by` to each line, first source that answers: the matching
   `TelegramMessage` (same chat, same text); an `extraction-tg_<chat>_N`
   batch's latest message; the `KnowledgeDocument`'s file date; a date in
   `metadata` or `reference`; then upper bounds, `dated_by` ending
   `(no later than)`: the superseding record's date, the earliest outcome
   `ts`. `report.md` counts exact, bounded and undated per project.
2. **Confinement evidence wins.** Prep derives each record's project
   independently of its mapping too: the Telegram chat id against the
   private specs' chats, or the `extraction-tg_<name>_` label. Where they
   disagree the evidence wins: the line's project and client are set from
   it, and `report.md` lists each change with the count, which Valor reads
   before any import.
3. **A second row corrects the first.** `import_old` copies the line's
   `occurred_at`, `dated_by`, project and client into the payload, and
   appends a second row for a `memory_id` already on the stream only when
   its date, project or client differ from the latest row's, on any Mac
   whose stream holds that `memory_id`, served there or not (a first row
   still needs a project served here). `_unit`
   (`core/memory.py`, which reads the ledger; `memory/ingest.py` does not)
   finds the first `memory.imported` row of that `memory_id` and passes it
   in the unit; ingest deletes the record saved from it and saves one under
   the same key `<first id>.0` and the same `ledger_id`, with the new
   project, client and `meta.occurred_at`; when the new project is not
   served on that Mac the record is deleted and none saved. So a misplaced
   record leaves the wrong project's candidates, and the Mac serving the
   right project imports it once, as a first row; marks name
   the same ref; the recall cutoff does not move; a rebuild replays the
   rows in order to the same result. In live recall the date changes only
   the label (`occurred <date> (<dated_by>)`) and the cross-ledger order.
4. **Run, per Mac, its own projects**, over ssh as that Mac's user (the
   commands rollout.md step 7 names): `python -m core backup` and
   `pg_dump -n memory` into `backup_dir`; prep over all four exports
   together, so cross-Mac duplicates collapse, reading the existing
   `import.jsonl` files first: an already-imported `memory_id` is its
   group's keeper and gets only dating and project correction; the
   duplicates of it and of each other are dropped; only records never
   imported or grouped go through the model passes; Valor reads `report.md`;
   `memory import`; `memory ingest`; a second `pg_dump -n memory`, the
   bench's input (one dump in all, once `memory_dsn` names the shared
   store).
5. **Questions, private** (`~/Desktop/Valor/memory-bench/`):
   `questions-v1.json`, about 150, 30-40 each of long-term, episodic,
   seasonal-calendar, seasonal-resurfacing, confinement; each with
   `asked_on`, ledger, project, client and gold record refs, every gold a
   record that survived the import (long-term: the current decision).
   Confinement gold comes from item 2's evidence, not prep's mapping;
   episodic and resurfacing gold are exact-dated records only.
   Drafted by `claude -p`, each gold checked by Valor against the dump.
6. **Brief score** (this repo): `python -m core memory bench QUESTIONS
   DUMPS... --out OUT --scratch SCRATCH`. It restores the post-import dumps (four
   while each Mac has its own schema, one once the store is shared) into
   `SCRATCH`, its own database in the workspace cluster (never
   `memory_dsn`; `memory.records.use(SCRATCH)`, `reset()` before and
   after), and renumbers each record's `ledger_id` by time (`occurred_at`,
   else the row time; keys unchanged). Undated imported records rank
   before every question, as in live recall, where they precede every
   later task; a bounded one ranks at its bound. Per question: cutoff the
   first rank after `asked_on`, then recall's own `candidates` and ranking.
   Reports gold@10, gold-in-Brief, and any other client's record in the
   Brief, per type, each scored with and without undated records, with the
   undated count per type: an undated record written after an event would
   otherwise answer a "before" question.
7. **popoto adapter** (popoto PR): dataset `dated`
   (`tests/benchmarks/datasets/dated.py`) reading `--fixture`, history the
   project's records dated before `asked_on`, `question_type` the type;
   lexical and hybrid, `--output` the private folder. A second run adds the
   superseded records as distractors, labelled so. Committed fixture made up.
8. **Monthly.** `routines/memory-bench/routine.toml`, runner `task`, the
   Cowboy, the 1st: fresh dumps (one, from the shared store, once
   `memory_dsn` is set), items 6 and 7 on `questions-v1` (fixed,
   compared with last month) and new calendar questions as the next set,
   scored apart. Each result file names its question set and its sha256.

## Not here

Governance is restrained by structure, not sentiment. Before any check,
gate, hook, validator, review round, approval step, isolation, sandbox rule,
denial, allowlist, refusal, capability removal, or verification layer is
added, the change names the mission item it serves and the incident that
already happened without it; missing either, it is not added. This plan
adds none: Valor's reading of `report.md` is by hand.

- Any client name, question, answer or result in this repo or popoto.
- Yearly questions: not enough history until autumn 2027.
- Changing recall itself: the baseline judges a later change.

## Tests

- Export: dated by message, batch, doc, metadata, bound by superseder and
  by outcome, undated; a record mapped to project A whose chat is B's is
  written for B and listed.
- Prep: an imported `memory_id` with a near-duplicate in another export
  stays keeper, and no line is written for the duplicate.
- Import: on a Mac serving only A, a line moving an imported A record to B
  leaves no record in A's candidates; on B's Mac it is imported once.
- Ingest: a second row on a store already holding the first record leaves
  one record, first key and `ledger_id`, dated; one moving A to B leaves it
  only in B's candidates; render, search and a mark work on it; a rerun
  appends nothing; `import_old` carries the new payload fields.
- Bench: a record dated after `asked_on` is never a candidate, an undated
  one is only in the with-undated score; client B's record never in client A's Brief, a standard
  is; gold in and out of the Brief counted; after a run the backend is
  reset and the installed `memory_dsn` store is unchanged.
- popoto: `dated` fixture, history bounded by `asked_on`.
- Full suite under 5 minutes; `tests/test_bloat.py` passes.

## Done

Tom has seen the first baseline per type and used it to judge a memory
change (rule 5), recorded here with the date.
