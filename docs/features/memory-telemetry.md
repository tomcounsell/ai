# Memory Telemetry

> **Phase 1 (Layer A: Measure)** of a multi-phase memory-optimization effort (issue #2200). This phase is read-only: it measures corpus-level memory quality and establishes a pre-intervention baseline. It does not gate writes, prune records, or mutate any Memory record. **Phase 2 (write-gate unification, #2201) has since shipped** — see [Write-Path Quality Gates](subconscious-memory.md#write-path-quality-gates) in the subconscious-memory doc for how it consumes `classify_content()` from this module to gate all five writer paths at `Memory.save()`. Ingest distillation (#2202) and outcome-loop + pruning (#2203) remain separate, not-yet-built issues.

Before this feature, there was no machine-readable, corpus-level view of memory quality. `python -m tools.memory_search status` reports per-record counts and a superseded ratio, and the `/memories` dashboard inspects individual records, but nothing aggregated act rates, junk rates, or ingest volume across the whole corpus into a single exportable snapshot. That made it impossible to answer "is the memory system getting noisier or cleaner over time?" without an ad hoc script, and impossible to know whether a future write-gate change actually improved anything, because there was no "before" number to compare against.

This feature adds a shared junk-classification heuristic, a pure corpus-aggregation function, a read-only data loader, a JSON HTTP endpoint, and a non-interactive CLI that snapshots the corpus into two committed baseline artifacts.

## Architecture

```
agent/memory_quality.py          <- shared junk-definition heuristics (dependency-light leaf)
        |
        v
tools/memory_eval/ingest_quality.py   <- pure corpus aggregation (compute_corpus_metrics)
        |
        v
ui/data/memories.py::get_corpus_metrics   <- the ONE function that touches Redis (.no_track())
        |
        +---> GET /memories/metrics.json      (ui/app.py, thin JSON wrapper)
        |
        +---> python -m tools.memory_eval.snapshot   (CLI, writes docs/baselines/*)
```

`agent/memory_quality.py` is deliberately dependency-light — no imports of `redis`, `popoto`, or `models` — so it can be imported both from the pure-aggregation CLI/tool path documented here and from `models/memory.py`'s write gate (Phase 2, #2201, shipped) without pulling Redis clients or creating circular imports. The write gate imports `classify_content()` via a deferred (in-`save()`) import specifically to avoid a real circular-import cycle through `agent/__init__.py` — see [Write-Path Quality Gates](subconscious-memory.md#write-path-quality-gates).

## Junk / Fragment Heuristics

`agent/memory_quality.py` defines the Phase-1/2 shared junk definition. `classify_content(content: str | None) -> str` returns one of three labels:

- **`"durable"`** — everything that isn't ack-only or a fragment. The default, positive classification.
- **`"ack_only"`** (`is_ack_only`) — a bare acknowledgement or filler utterance: the content tokenizes to at most 3 words, and every token (after collapsing repeated-letter runs, so "Ahhh" matches "ah") is in a fixed acknowledgement lexicon ("yup", "ok", "thanks", "gotcha", "lol", etc.).
- **`"fragment"`** (`is_fragment`) — dangling or incomplete syntax: unbalanced brackets (`()[]{}`), a trailing colon with no body ("includes:"), or a bare list marker with no content ("-", "1.").

Classification order: ack-only is checked first (a short acknowledgement takes priority over any coincidental dangling-syntax match), then fragment, then everything else falls through to durable. `None` and whitespace-only input are deterministic and never raise — they classify as `"fragment"` (absent content carries no acknowledgement signal, so fragment is the more accurate bucket).

## Corpus Metrics Schema

`tools/memory_eval/ingest_quality.py::compute_corpus_metrics(records: list[dict], min_evidence: int = 2) -> dict` is a pure function over a list of *decorated* Memory record dicts (the shape produced by `ui/data/memories.py::_decorate_record`) — no Redis, no popoto, no network. Malformed entries are handled defensively (missing fields, non-dict metadata) and contribute zero evidence rather than raising. The returned dict always has every key present, even for an empty `records` list; rate fields are `None` (not a `ZeroDivisionError`) when their denominator is zero.

| Field | Type | Description |
|-------|------|-------------|
| `total_records` | `int` | Total records passed in |
| `superseded_count` | `int` | Records with `superseded_by` set (excluded from junk/durable classification) |
| `durable_denominator` | `int` | Non-superseded record count — the denominator for `junk_rate` |
| `min_evidence` | `int` | The `min_evidence` floor used for this computation, echoed back for provenance |
| `act_rate_definition` | `str` | The pinned aggregate-act-rate formula, as prose (see below) |
| `aggregate_act_rate` | `float \| None` | `acted_total / evidence_total`; `None` if `evidence_total == 0` |
| `aggregate_dismissal_rate` | `float \| None` | `dismissed_total / evidence_total`; `None` if `evidence_total == 0` |
| `acted_total` | `int` | Sum of `acted_i` across qualifying records |
| `dismissed_total` | `int` | Sum of `dismissed_i` across qualifying records |
| `evidence_total` | `int` | Sum of `evidence_i` (`acted_i + dismissed_i`) across qualifying records |
| `qualifying_record_count` | `int` | Records with `evidence_i >= min_evidence` |
| `excluded_thin_evidence_count` | `int` | Records with `evidence_i < min_evidence`, excluded from the aggregate |
| `act_rate_distribution` | `dict[str, int]` | Histogram of per-record act rate (`acted_i / evidence_i`) across qualifying records, fixed 0.2-wide buckets plus `<0.0`/`>1.0` overflow labels |
| `junk_count` | `int` | `ack_only_count + fragment_suspect_count` |
| `junk_rate` | `float \| None` | `junk_count / durable_denominator`; `None` if `durable_denominator == 0` |
| `ack_only_count` | `int` | Non-superseded records classified `"ack_only"` |
| `fragment_suspect_count` | `int` | Non-superseded records classified `"fragment"` |
| `source_counts` | `dict[str, int]` | Ingest volume grouped by `record["source"]` (`"agent"`, `"human"`, etc.; `"unknown"` for missing/non-string values) |
| `importance_histogram` | `dict[str, int]` | Histogram of `importance` values, same fixed-bucket shape |
| `confidence_histogram` | `dict[str, int]` | Histogram of `confidence` values, same fixed-bucket shape |
| `decay_imminent_count` | `int` | Records where `decay_imminent` is true (see [Subconscious Memory](subconscious-memory.md#dismissal-tracking)) |
| `never_injected_count` | `int` | Records where `access_count == 0` |

The histogram buckets (`importance_histogram`, `confidence_histogram`, `act_rate_distribution`) are fixed 0.2-wide ranges (`0.0-0.2`, `0.2-0.4`, `0.4-0.6`, `0.6-0.8`, `0.8-1.0`) plus `<0.0` and `>1.0` overflow labels, so the emitted JSON is stable and diffable across snapshots. Confidence and per-record act rate are naturally bounded to `[0, 1]`; importance is not (human-authored memories can score above 1.0), so out-of-range values land in the overflow buckets instead of being silently dropped.

`get_corpus_metrics` (see below) adds one more field to this dict: `project_key` (the resolved, comma-joined project key string).

### Pinned Aggregate-Act-Rate Formula

The aggregate act rate is computed directly from each record's `outcome_history`, not from the pre-computed `act_rate` field that `_decorate_record` already attaches. For each record:

```
acted_i     = count(outcome == "acted")
dismissed_i = count(outcome == "dismissed")
evidence_i  = acted_i + dismissed_i
```

`"used"` outcomes are excluded entirely — neither numerator nor denominator. `"used"` means the memory was consumed and reasoned about but did not drive the response: a neutral signal, not a positive or negative one (see the three-tier outcome model in [Subconscious Memory](subconscious-memory.md#dismissal-tracking)). Folding it into either side of the ratio would blur a real positive/negative signal with a "no signal" one.

A record only contributes to the aggregate once `evidence_i >= min_evidence` (default `2`). Records below the floor are excluded and counted separately in `excluded_thin_evidence_count`. This exists because a record with exactly one outcome recorded would otherwise contribute a spurious `1.0` (one lucky "acted") or `0.0` (one "dismissed") — noise dressed up as signal.

The corpus-wide aggregate is a **micro-average**: `sum(acted_i) / sum(evidence_i)` across qualifying records — pooled counts, not an average of per-record ratios (a macro-average would let a thousand-evidence record and a two-evidence record count equally, which is the wrong weighting for a corpus-level number).

This deliberately diverges from `agent.memory_extraction.compute_act_rate` (the function backing `_decorate_record["act_rate"]` on the per-record `/memories` dashboard view), which divides the acted count by the *total* number of `outcome_history` entries — a denominator that includes `"used"`. The two numbers answer different questions: `compute_act_rate` answers "of everything that happened to this one memory, how often did it get acted on," while the corpus aggregate here answers "of the decisive (acted-or-dismissed) outcomes across the whole corpus, what fraction were positive." `compute_corpus_metrics` never consumes the pre-computed `act_rate` field for this reason — it always recomputes counts directly from `outcome_history`.

The exact prose is available at runtime via `ACT_RATE_DEFINITION` in `tools/memory_eval/ingest_quality.py`, and is echoed into every metrics dict as `act_rate_definition` so a JSON consumer doesn't need to read source to know what the number means.

## Read-Only / `.no_track()` Invariant

The entire Phase-1 surface is read-only with respect to Memory records — no code path here calls `.save(`, `.delete(`, or `transition_status(` on any Memory record. This was verified by grep during code review and is a load-bearing invariant, not an incidental property: a corpus-metrics tool that itself perturbed the corpus would corrupt the very numbers it's trying to report.

`get_corpus_metrics` loads records via `Memory.query.filter(project_key=pk).no_track().all()`. `.no_track()` is mandatory, not an optimization. Popoto's `AccessTrackerMixin` normally stages a read timestamp on `on_read()` for every record touched by a query, and that staged timestamp later gets promoted into the record's `access_count`. Without `.no_track()`, the act of running a full-corpus metrics scan would itself increment `access_count` on every record it read — silently contaminating the `never_injected_count` metric (`access_count == 0`) this function exists to report, even though no `.save()` call ever ran. A metric measuring "how many memories have never been surfaced to the agent" must not be moved by the measurement itself.

## `GET /memories/metrics.json`

A thin JSON wrapper over `get_corpus_metrics`, registered in `ui/app.py`.

**Query parameters** (both optional):

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_key` | `str` | `None` | Restrict to one project. `None` resolves to every project this machine owns (same resolution as the `/memories` dashboard). |
| `min_evidence` | `int` | `2` | Passed straight through to `compute_corpus_metrics`. |

**Response:** the full metrics dict described above (JSON), plus `project_key`. Always returns HTTP 200, even for an empty or unavailable corpus — `get_corpus_metrics` never raises; on any query failure it logs a warning and returns the same zero-filled shape `compute_corpus_metrics` produces for an empty record list.

```bash
curl -s localhost:8500/memories/metrics.json | python3 -m json.tool
curl -s "localhost:8500/memories/metrics.json?project_key=valor&min_evidence=3"
```

## Snapshot CLI

`python -m tools.memory_eval.snapshot [--force] [--project-key KEY] [--min-evidence N]` is a non-interactive CLI that computes the current corpus metrics and writes two artifacts under `docs/baselines/`:

- **`memory-telemetry-baseline.json`** — the raw `get_corpus_metrics()` output plus provenance fields: `record_count` (mirrors `total_records`), `snapshot_timestamp` (ISO 8601, UTC), and `git_sha` (current `HEAD`, `"unknown"` on failure). `act_rate_definition` is already present in the metrics dict, so it isn't duplicated.
- **`memory-telemetry-baseline.md`** — a short human-readable summary: record count, superseded count, durable denominator, aggregate act rate, junk rate (with ack-only/fragment breakdown), decay-imminent count, never-injected count, ingest volume by source, and the act-rate definition prose.

**Flags:**

| Flag | Description |
|------|-------------|
| `--project-key KEY` | Project partition key. Defaults to every project this machine owns. |
| `--min-evidence N` | Minimum `acted + dismissed` outcome count for a record to count toward the aggregate act rate. Default `2`. |
| `--force` | Overwrite existing baseline artifacts. Without this flag, the CLI refuses to run if either artifact already exists. |

**Clobber guard.** Re-running the snapshot CLI without `--force` when either `memory-telemetry-baseline.json` or `.md` already exists is a no-op: it logs an error naming the existing file(s) and exits non-zero. This protects the committed pre-intervention baseline from being silently overwritten by a routine or accidental later run — the whole point of a baseline is that it stays frozen at the moment it was taken, so future snapshots must be written under a different name (or the artifact intentionally re-baselined with `--force` and a note in the commit message about why).

**Atomicity.** Both artifacts' content is fully built in memory (JSON serialized, Markdown rendered) before either file touches disk, so a computation failure midway through never leaves a truncated or partial artifact on disk.

## The Committed Baseline

`docs/baselines/memory-telemetry-baseline.json` and `.md` are the committed pre-intervention snapshot, generated via the CLI above and checked into git. Numbers as of the snapshot: 1991 total records, aggregate act rate 0.990, junk rate 0.030, 138 never-injected records.

This baseline is the "before" side of the comparison that later phases need to demonstrate they actually improved corpus quality rather than just believing they did. Write-gate unification (#2201) has shipped and reports its own immediate `gate_rejected_*`/`gate_fallback_dropped` counters as evidence of junk *prevented* (see [Write-Path Quality Gates](subconscious-memory.md#write-path-quality-gates)); the `junk_rate` trend against this baseline is a slower, post-deploy signal since existing junk records are not pruned until outcome-loop pruning (#2203) ships. Ingest distillation (#2202) also remains pending.

**`.gitignore` note.** The repo has a broad `*.json` ignore rule. Committing the baseline JSON required a scoped negation:

```gitignore
# Memory-telemetry baseline snapshot is a committed artifact (issue #2200
# plan decision): un-ignore it from the broad *.json rule above.
!docs/baselines/memory-telemetry-baseline.json
```

If a future re-baseline needs a differently-named artifact, it will need its own negation entry alongside this one.

## See Also

- [Subconscious Memory](subconscious-memory.md) — the memory system this measures: ingestion, extraction, dismissal tracking, and the three-tier outcome model (`acted`/`used`/`dismissed`) that the act-rate formula above depends on.
- [Memory Search Tool](memory-search-tool.md) — per-record CLI (`python -m tools.memory_search`), the closest existing tool prior to this corpus-level view.
- [Hybrid Retrieval Eval + Cutover](hybrid-retrieval-eval.md) — the `tools/memory_eval/` package's other resident: a two-arm retrieval-quality eval harness. This feature's `tools/memory_eval/ingest_quality.py` follows the same pure-aggregation, synthetic-fixture-tested style as `tools/memory_eval/metrics.py`.
