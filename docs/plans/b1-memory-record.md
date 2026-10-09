---
tracking: none
slug: b1-memory-record
type: record
---

# B1 memory: the evidence runs

The pair `docs/plans/b1-memory.md` names in "The evidence": one seed task
with a recorded preference, then one item run with memory on and off.

## Setup

- Branch `b1-memory` at 4ccc89074 plus this record. The candidate
  6a0a07fea is that commit and this record rebased onto
  b832d10ec, with the same code diff.
- Ledger: `VALOR_DB=valor_rebuild_test_b1emu`, a fresh database on the
  machine cluster (trust auth), migrated once by `db.migrate(...,
  fresh=True)`. `VALOR_WORK=~/valor-tasks-b1emu`. `VALOR_PG_PORTS` and
  `VALOR_REDIS_PORTS` 6460 to 6469; the toy items run no services.
- Runner: `.venv/bin/python -m tests.emulator.replay ITEM --arm bare
  --run NAME`, in order, one at a time.
- Items: `~/src/valor-demo/items/toy-pref-seed.json` and `toy-pref.json`,
  as the plan gives them. Both specs name the bare cache
  `~/src/valor-demo/cache/greeter.git` as `repo`, so both tasks share one
  project.

## Runs

Spend is in US dollars, kernel spend gateway-metered.

| Run | `VALOR_MEMORY` | Task | Final rev | Kernel | Emulator | Where it stopped |
|---|---|---|---|---|---|---|
| `b1-toy-pref-seed` | on | 5dc38d1eedf4 | 4e8c494f | 0.7931 | 0 | `checks`, review: the VM exited 1 with no result |
| `b1-toy-pref-on` | on | 3532d776aafe | a3bbc051 | 0.9460 | 0 | `checks`, docs: no runner for docs on this branch |
| `b1-toy-pref-off` | off | 3489aff706cb | e7c008ef | 0.6779 | 0.0166 | `checks`, review: the VM exited 1 with no result |

Total: kernel 2.4170, emulator 0.0166.

The review VM's output (`checks/vm-head-4e8c494fcdc1.run.out` in the seed
task) reads `XPC connection error: Connection invalid. Ensure container
system service has been started`: Apple's container service was stopped
under the run by another suite on the same machine. No run reached a held
merge, so the stand-in judged nothing; the verify commands below were run
by hand on each final rev, checked out from the task's `kernel.git`.

## Ingest

After the seed run, `memory.record` in `valor_rebuild_test_b1emu` held:

| Ledger id | Origin | Text, first words |
|---|---|---|
| 1 | correction | the governance paragraph, correction 1 |
| 6 | instruction | "Add a birthday greeting for users. From now on, every greeting function..." |
| 29 | transcript | two text entries of the plan turn |
| 45 | transcript | one text entry of the critique turn |
| 58 | transcript | one text entry of the build turn |

`memory.ingested` held 5 ids. Every row was taken by `after_turn` once its
turn slot was released; no ingest was run by hand.

## Recall

`turn.started` rows of each task, with the offset of `## Remembered` in
the Brief (0 is absent):

| Task | Row | Remembered at | Turn |
|---|---|---|---|
| 3532d776aafe (on) | 102 | 11920 | first turn |
| 3532d776aafe (on) | 118 | 0 | fresh session |
| 3532d776aafe (on) | 131 | 11998 | later turn |
| 3532d776aafe (on) | 175 | 0 | fresh session |
| 3489aff706cb (off) | 194, 211, 223, 238 | 0 | every turn |

No Brief of either run holds `Memory: unavailable`.

The on run's first Brief, under `## Remembered`, held three records in
ledger order: the seed's instruction, labelled `task 5dc38d1eedf4, Tom's
instruction (by tom, via the command line, 2026-10-09T09:51:52...)` with
the request quoted whole, then two transcript entries labelled `task
5dc38d1eedf4, turn ..., from that turn's transcript, written by the
turn`. Every record line was quoted `  > `. The seed's label reads "Tom's
instruction" because of how the emulator records provenance: the replay
driver starts each task with `core start`, which writes `by` tom, `via`
the command line, and `role_played` false on its `task.started`. The
kernel labels a record from the provenance its row carries, so a replayed
request is labelled as Tom's.

## Verify

The item's verify commands, run on each final rev:

| Run | `unittest -q` | `doctest greeter.py` | keyword-only and doctest on `greet` |
|---|---|---|---|
| `b1-toy-pref-seed` (its one command) | OK | | |
| `b1-toy-pref-on` | OK | exit 0 | exit 0 |
| `b1-toy-pref-off` | OK | exit 0 | exit 1, `IndexError`: `greet` has no doctest example |

## Result

The on run's first Brief holds the seed's instruction record and the off
run's Briefs hold no section. The on run's final commit passes all three
verify commands and the off run's fails the preference check. The off run
did not meet the preference unprompted, so the fallback pair the plan
names is not run.

The lead's decision on the review's residual: turn-written transcripts
reach a later Brief in the same project, labelled, quoted and escaped,
with no kernel decision reading them; that is accepted as the milestone's
Done requires.
