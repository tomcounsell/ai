---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-10-21
tracking: https://github.com/tomcounsell/ai/issues/3658
last_comment_id: none
---

# Old memory, imported

**Ask.** Tom, 2026-10-21: import the old system's memory, `source` human and
agent only, only records with empty `superseded_by`; skip knowledge; "also
dedup them"; show him before/after counts and a sample of merges before
anything is written. Prior art: cutover-data.md G8 (commit 4158b7719)
deferred this to after milestone 6; nothing was built.

**Premise, checked** (main 3bb8e730e). The export is the Pirate's old Redis
(`~/valor-retired/cutover-2026-10-19/memory.jsonl`, 5,049 lines; rollout.md
step 7). Of the 2,597 records Tom chose, 188 are session summaries
(`^In an? \d+-event session`, `agent_id` `tui-*`): dropped. `project_key` is
coarse: 614 records keyed `valor` were extracted from Cyndra chats
(`agent_id` `extraction-tg_cyndra_<chat>_N`; chats -1003900483201 and
-1003794218389 are in `projects/cyndra.toml:19-20`), so the project is the
`extraction-tg_<name>_` label when there is one, else `project_key`. After
the summaries go: cyndra 1,609, valor 788, cuttlefish 4, yudame 3,
mondayflowers 2, none 3 (dropped, listed). The ref
`backup/pre-sync-main-2026-10-15` exists neither here nor on origin; the
fields read plainly without it.

**How the new store takes records.** A record carries `ledger`, `key`
(`<ledger id>.<n>`), `client`, `project` (the spec's `repo` URL, not its
name: `core/memory.py:129,247`), `ledger_id`, `origin`, `text`, `meta`
(`memory/records.py:23-39`). Recall picks a task's project records from its
*own* ledger only, before its `task.started` (`memory/recall.py:42-46`), and
the store is rebuilt by ingesting the ledger again (`docs/memory.md:67-71`).
So a record written straight into the store would vanish on a rebuild and
would not be recalled by a Mac whose ledger is not named on it. The import
therefore writes ledger rows and lets ingest, the one mechanism that makes
records, make them (rule 4).

## What is built

1. **`scripts/memory_import_prep.py EXPORT... OUT/`** (one-off, like
   `scripts/cutover_export_memory.py`). Filters as above; maps each record to
   a spec by name (`Spec.all`, `core/workspace.py:219-228`) and takes its
   `repo` and `client`; collapses exact and near-exact duplicates within a
   project (token Jaccard >= 0.8 on content: 92 records); then the
   paraphrase pass: within each project, candidate groups (Jaccard 0.4 to
   0.8, up to about 320 more) go to `claude -p` (Claude Code itself, rule
   10), which returns one keeper id per real duplicate group and leaves the
   rest. The keeper is an existing record's text, never rewritten (raw
   entries, `docs/memory.md:36-37`); Tom's own record is preferred as
   keeper. Writes
   `OUT/import.jsonl` (keeper text, project repo, client, `by`, old
   `memory_id`, `merged_from` ids) and `OUT/report.md`: counts per stage and
   per project, and 20 sampled merges side by side. Expected 2,000 to 2,300.
2. **`python -m core memory import FILE`** beside `memory mark`
   (`core/__main__.py:752-759,993-995`): appends one `memory.imported` row per
   line on the `memory` stream (`core/memory.py:57`, as `mark` does at
   160-169), with provenance `by` (for `source` human the record's
   `agent_id`, which names the speaker: `Tom` in 308 of 1,193, written
   `tom`; `valor` for agent records) and `via` "the old system's memory".
   Its `at` is the import time (`core/ledger.py:24-28`): the export holds
   no creation time (`relevance` is the last outcome's time).
   Only lines whose project is served here (`Spec.here`,
   `core/workspace.py:231-234`); an old `memory_id` already on the stream is
   skipped, so a second run appends nothing.
3. **Ingest takes the row.** `memory.imported` joins `TYPES`
   (`core/memory.py:47-54`) and `ORIGINS` (`memory/ingest.py:24-30`, origin
   `imported`); its project and client come from the payload, since stream
   rows have none (`core/memory.py:238-239`). Its record is recalled by that
   project's later tasks like any other.

**Order.** Prep runs on the Pirate; Tom reads `report.md` in chat; only then
does `memory import` run, on that same `import.jsonl` (prep is not run
again after his review: the model pass need not repeat its groups). Rows
on the ledger are permanent; the export stays in `~/valor-retired`, so
`merged_from` ids still resolve. Each Mac imports its own projects: the
Pirate now (yudame, mondayflowers); the Cowboy (valor) by copying
`import.jsonl` there; the Bald (cyndra) and the Captain (cuttlefish) at
their installs, where step 7 exports that Mac's own old memory and prep
runs over it and the Pirate's export together, so duplicates across the
two collapse, and Tom reads that report first (rollout.md step 7 gains
the line). Once `memory_dsn` names the shared store, each Mac's next ingest
takes every row that store has not taken (`core/memory.py:183-207`,
`memory/ingest.py:33-36`), so the import is run once per ledger and reaches
the shared store with no second run.

## Not here

Governance is restrained by structure, not sentiment. Before any check,
gate, hook, validator, review round, approval step, isolation, sandbox rule,
denial, allowlist, refusal, capability removal, or verification layer is
added, the change names the mission item it serves and the incident that
already happened without it; missing either, it is not added. This plan
adds none: Tom's review of `report.md` is his ask, done by hand, not a gate
in code.

- The 2,413 knowledge records (Tom: skip).
- [ORDERED] cyndra (1,609) and cuttlefish (4) wait for the Bald's and the
  Captain's installs.
- No marks: whether an imported record is a standard of working is Valor's
  judgement later (`python -m core memory mark`).

## Tests

- `memory.imported` row ingested gives a record with the payload's project
  and client and origin `imported`, recalled for a later task of that
  project and not for another project's.
- `memory import` run twice appends rows once; a line for a project not
  served here appends nothing.
- Prep on a small fixture: summary dropped, superseded and knowledge
  skipped, cyndra-labelled `valor` record mapped to cyndra, 0.8 duplicate
  collapsed (the model pass is stubbed), a human record by `agent_id`
  `Tom` written `by` `tom`.
- Full suite under 5 minutes; `tests/test_bloat.py` passes.

## Done

Tom has read the report, the Pirate's and the Cowboy's rows are imported,
and the Brief of a later valor task on the Cowboy, whose instruction
shares words with a named imported record, shows it under Remembered;
recorded here with the date, the task id and the record's ref (rule 5).
Cyndra's 1,609 are used only once the Bald runs.

## Decided

- Placement (Tom, 2026-10-21: "Each project on its Mac"): each project's
  records are imported on the Mac that serves it. The Pirate takes yudame
  and mondayflowers now, the Cowboy takes valor, and cyndra and cuttlefish
  wait for the Bald and the Captain.

## Open questions

None.
