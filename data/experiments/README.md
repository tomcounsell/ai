# Legacy experiment corpora

These JSONL files are the only surviving evidence from **autoexperiment**, the
2025 overnight prompt-optimization script (#410, PR #411). The script itself,
its installer, its launchd plist, its tests, and its feature doc were removed in
#3177. The corpora stay because they are data, not code: a record of what was
evaluated and against what.

| Path | Origin |
|------|--------|
| `summarizer/eval_samples.jsonl` | Evaluation samples for the summarizer optimization target |
| `observer/eval_corpus.jsonl` | Evaluation corpus for the observer target, whose module (`bridge/observer.py`) was itself deleted in #466 |

**Read these as legacy evidence, never as a baseline.** Autoexperiment was never
installed or run on any machine in this fleet: there is no log, no dated result
set, and no run history behind these files. Any number derived from them
describes the 2025 script's design intent, not observed production behavior.

The replacement is the improvement controller (#3177), which keeps its evidence
in Redis records and the Popoto content store rather than in loose files under
`data/`. See [`docs/features/improvement-controller.md`](../../docs/features/improvement-controller.md)
and [`docs/features/improvement-evaluation.md`](../../docs/features/improvement-evaluation.md).
