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
recurrence and topics resurfacing after months); "Yes to both" to building
the test set and importing the old May-Oct memories.

**Premise, checked.** The import path exists (docs/plans/memory-import.md,
#3660): `scripts/memory_import_prep.py` then `python -m core memory import`,
one `memory.imported` row per old `memory_id`, made a record by ingest.
Every other Mac has an `import.jsonl` from its install
(`~/valor-retired/cutover-*`); the Cowboy has none, and `valor-record
memory` on the Cowboy finds no record of origin `imported`. Imported rows
carry no date: `at` is the import time. Tom's 2026-10-21 rules stand: human
and agent records only, not superseded, no session summaries, knowledge
skipped, not-worth-recalling dropped, deduplicated.

## What is built

1. **Dates.** `scripts/cutover_export_memory.py` gains a mode reading a
   Redis dump restored into a throwaway Redis (never a live one): each
   exported line gains `occurred_at` and `dated_by`, from, in order: the
   matching `TelegramMessage`'s date (same chat, same text); for an
   `extraction-tg_<chat>_N` record, the latest message date of that batch;
   for a doc-derived record, the `KnowledgeDocument`'s file date; else none.
   Prep carries both fields into `import.jsonl` and counts dated and
   undated per project in `report.md`.
2. **Import and ingest.** The row's payload carries `occurred_at`; ingest
   uses it as the record's time when present (`core/memory.py` `_unit`).
   `import` appends a line whose `memory_id` is already on the stream only
   when the line brings a date the earlier row lacked; ingest keys an
   imported record by its `memory_id`, so the later row replaces the
   earlier record and a rerun appends nothing.
3. **Run, per Mac, for its own projects.** On each Mac: `python -m core
   backup`, and `pg_dump -n memory` beside it in `backup_dir`; prep over
   that Mac's export and every other Mac's (so cross-Mac duplicates
   collapse); Valor reads `report.md`; `memory import`; `memory ingest`. The
   Cowboy takes valor; the others re-run to add dates, or import for the
   first time where their rows are missing. Each runs over ssh as that
   Mac's user, the commands rollout.md step 7 already names.
4. **The measurement, private.** `~/Desktop/Valor/memory-bench/`: a corpus
   (all four dumps' Memory records, superseded and knowledge included, each
   with project, client, `occurred_at`), and `questions.json`: about 150,
   30-40 each of long-term, episodic, seasonal-calendar,
   seasonal-resurfacing and confinement, each with `asked_on`, project, and
   gold record ids (confinement: the other client's records that must not
   appear). Drafted by `claude -p` from the corpus, each gold id checked by
   Valor against the corpus.
5. **popoto adapter** (popoto PR): `tests/benchmarks/datasets/dated.py`,
   dataset `dated`, reading `--fixture PATH`; an item's history is its
   project's records dated before `asked_on`; `question_type` is the
   category. Run with `--output ~/Desktop/Valor/memory-bench/results/`,
   `--retrieval-mode lexical` and `hybrid`. Committed fixture is made up.
6. **Brief score** (this repo): `python -m core memory bench QUESTIONS
   CORPUS OUT` loads the corpus into a scratch `memory` schema in the
   workspace cluster and, per question, runs recall's own `candidates` and
   ranking (`memory/recall.py`) with the question's project, client and
   `asked_on`; reports gold-in-Brief and leaks per type. Confinement is
   scored here only: popoto has no client scope.
7. **Monthly.** `routines/memory-bench/routine.toml`, runner `task`, on the
   Cowboy, the 1st of each month: re-export the current store's records,
   add new calendar questions, run 5 and 6, compare with the last result
   in the private folder.

## Not here

Governance is restrained by structure, not sentiment. Before any check,
gate, hook, validator, review round, approval step, isolation, sandbox rule,
denial, allowlist, refusal, capability removal, or verification layer is
added, the change names the mission item it serves and the incident that
already happened without it; missing either, it is not added. This plan
adds none.

- Any client name, question, answer or result in this repo or popoto.
- Yearly questions ("last December"): not enough history until autumn 2027.
- Changing recall itself; the baseline is what a memory change is judged by.

## Tests

- Export: a line dated by message, by batch, by doc, and undated.
- Ingest: an imported row with `occurred_at` gives that record time; a
  second row for the same `memory_id` replaces, never adds; rerun appends
  nothing; a project not served here appends nothing.
- Bench: a record dated after `asked_on` is never a candidate; client B's
  record never in client A's Brief, and a standard of working is; gold in
  and out of the Brief counted right.
- popoto: `dated` fixture run, history bounded by `asked_on`.
- Full suite under 5 minutes; `tests/test_bloat.py` passes.

## Done

Tom has seen the first baseline per type and used it to judge a memory
change (rule 5), recorded here with the date.
