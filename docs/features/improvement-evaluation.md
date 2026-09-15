# Improvement Evaluation

How a candidate change is measured against the incumbent, and what makes the
resulting verdict worth anything.

Tracking issue: [#3177](https://github.com/tomcounsell/ai/issues/3177); the
harness landed under [#3216](https://github.com/tomcounsell/ai/issues/3216).
Companion to [Improvement Controller](improvement-controller.md).

## What exists

Two records, `ImprovementExperiment` and `ImprovementEvaluation`, on the
verifying artifact store, and the harness that turns one into the other:

| Module (`tools/improvement_eval/`) | Owns |
|---|---|
| `runner.py` | Gate ordering, the three exit handlers, the single `ImprovementEvaluation` write, `capture_baseline`, `freeze_protocol`, `repair_wedged_experiment` |
| `corpus.py` | Corpus export through `Memory.export_records`, the canonical corpus digest, restore through `Memory.import_records` |
| `arena.py` | One private `redis-server` per arm on a unix socket; the arm worker subprocess launch |
| `arm_worker.py` | The only process that talks to an arm: restore, retrieve, re-export digest, teardown re-check |
| `writer_guard.py` | The writer kill switch and the independent digest re-check |
| `retrieval.py` | The retrieval adapter and the baseline parity gate |
| `blinding.py` | Seeded arm assignment, blinded arm ids, the identity leak scan |
| `envelope.py` | The judge envelope wrapper |
| `judges/serves_charter.py` | The `serves-charter` judge |
| `calibration.py` | The frozen reference set and the two calibration numbers |
| `correction.py` | Holm step-down and the named fixed-batch stopping rule |
| `statistics.py` | Clustered bootstrap CIs and per-endpoint p-values over `tools/memory_eval/metrics.py` |
| `errors.py` | `InfraFailure` |

What the arms compare today is retrieval: two arms retrieving from one frozen
memory corpus, scored on retrieval endpoints (`recall_at_<k>`, `mrr`). Every
mechanism the harness exists to establish is mechanism-agnostic, and proving
it on retrieval costs minutes per run. A later lane points the same apparatus
at a bigger arm; comparing full candidate agent runs is out of scope here.

Nothing under `bridge/`, `worker/`, or `agent/` imports the harness, and it
adds no CLI or MCP surface. The operator surface belongs to lane 3's
`valor-improve`.

`tools/memory_eval/metrics.py` is imported for `bootstrap_ci`, `recall_at_k`,
and `mrr`. It is byte-identical to `main` and a test pins that.

## The frozen contract

An experiment's contract is preregistered and hashed before any arm runs.
`runner.compute_contract_digest` produces the `"sha256:<hex>"` digest over
the experiment's `hypothesis`, `mechanism`, `falsifier`,
`candidate_surfaces`, and `manifest` text, CRLF-normalized with sorted keys,
and `ImprovementExperiment.contract_digest` holds it.

The manifest names the candidate: the surfaces changed, the base revision,
the environment the arm ran in. It also cites the **protocol**, a second
artifact on the verifying store:

```json
{"protocol_ref": "$CF:<sha256>:ImprovementProtocol/protocol-....txt", "base_revision": "..."}
```

The protocol carries everything the contract fixes about *how* the
measurement runs and that the candidate must never see:

| Protocol field | What it fixes |
|---|---|
| `endpoints` | What is measured, named before measuring |
| `thresholds` | Per-endpoint margin and alpha (defaults `0.0` and `0.05`) |
| `batch_size` | The fixed-batch stopping rule |
| `holdout_partition` | Which split, and its rotation epoch |
| `queries` | The holdout inputs, each with a `trial_id`, `query_text`, and `gold_id` |
| `baseline` | The incumbent's recorded ranked ids and the corpus digest they were captured under |
| `incumbent`, `candidate` | Per-arm retrieval parameters passed to the arm worker |
| `infra_failure_cap` | How many harness-errored trials a run tolerates (default 0) |

The `$CF:` reference carries the protocol's own sha256, so the contract digest
covers the protocol bytes transitively, and a tampered protocol raises
`ArtifactIntegrityError` on load like any other artifact. Keeping the protocol
outside the manifest is also what keeps blinding honest: `blinding.identity_tokens`
treats every string in the manifest as candidate identity, and the holdout
queries and memory ids a judge legitimately sees must never read as a leak. The
manifest is a `ContentField`, so a queried row hydrates it as its `$CF:`
reference; `runner.identity_source` resolves it (and every other scanned
field) through its store before the scan, so the scan reads the branch name
and files the manifest carries rather than one reference string. The
candidate arm receives only `query_text` per trial; the gold answers stay in
the runner's process.

`runner.capture_baseline(project_key, queries, incumbent=...)` records the
baseline for the protocol by running the incumbent arm once per query in its
own private Redis. `runner.freeze_protocol(protocol)` writes the protocol and
returns the reference the manifest cites.

**Freezing precedes measurement.** `state="frozen"` and `frozen_at` are what
make that checkable. An experiment whose contract digest changes after
freezing is a new experiment: Gate 0 recomputes the digest and refuses a
mismatch.

## The run

`runner.evaluate(experiment_id, project_key)` composes the gates in this
order, and the order is load-bearing:

1. **Gate 0, contract re-check.** `state` must be `frozen` and the recomputed
   contract digest must match. The first write moves the experiment to
   `running`.
2. **Charter pin.** `ImprovementCharter.pinned(project_key)` supplies the
   digest written to `ImprovementEvaluation.charter_digest` and into every
   judge envelope, and the text the `serves-charter` judge quotes (charter
   §12: actions complete under the digest they carry).
3. **Corpus export.** One `Memory.export_records` per run, hashed by the
   canonical digest and frozen to the verifying store with a provenance
   header (record count, timestamp, git SHA).
4. **Arm assignment.** A seed derived from the experiment id decides the run
   order and the incumbent/candidate to `arm-a`/`arm-b` mapping;
   `arm_assignment_digest` is written before either arm runs.
5. **Arena spawn and digest comparison.** Two private Redis arms restore from
   the one export; each re-exports its own corpus and hashes it. Unequal
   digests, or a remaining manifest that differs between arms, end the run.
6. **Writer kill switch**, armed inside each arm worker after restore.
7. **Gate 1, baseline parity.** The incumbent arm runs every holdout query and
   its ordered ranked ids must reproduce the protocol's baseline under the
   same corpus digest. A miss ends the run and **the candidate arm is never
   invoked**: an operator who learns the candidate's number and then learns
   the run was invalid has already been influenced by it. A test asserts the
   candidate arm function was never called.
8. **Paired trials.** Both arms run each input in the assigned order. The
   incumbent's slot re-runs it and demands the Gate 1 ranking again, so an
   incumbent that drifts within the run is an `infra_failure`, never an
   average. A worker failure on either arm (timeout, a dead `redis-server`,
   unparseable output, an escaped write) is a harness error: the trial is
   excluded and counted against `infra_failure_cap`. Both arms run the same
   worker code, so a candidate-side failure says nothing about the candidate
   and never scores as a zero.
9. **Judges.** Each arm's per-trial output goes to the roster carrying the
   blinded arm id. `scan_for_identity` runs over what the judge sees and over
   the finished envelope; a hit sets `blinded=False` and annotates `notes`,
   and the run continues. Raw judge responses go to the verifying store and
   the envelopes land in `judge_records`.
10. **Statistics and Holm.** Paired per-trial deltas per endpoint, clustered
    by project, through `bootstrap_ci`; per-endpoint p-values through
    `holm_adjust`.
11. **Stopping rule.** A batch short of `batch_size` is `inconclusive` with the
    shortfall named in `notes`, never a partial-batch verdict.
12. **Verdict** and the one `ImprovementEvaluation` write.

`correction` is written as the named string, for example
`"holm; fixed-batch(n=40, endpoints=3)"`. A correction nobody can name was not
applied.

## Verdicts

`ImprovementEvaluation.verdict` is one of four, and the fourth is the important
one:

| Verdict | Produced only by |
|---|---|
| `accept` | Every endpoint cleared its margin, its Holm-adjusted alpha, and a CI lower bound above zero |
| `reject` | The measurement completed and at least one endpoint's whole interval sits below its margin |
| `inconclusive` | The measurement ran and could not distinguish the arms, or the batch was short |
| `infra_failure` | The harness broke. **This says nothing about the candidate** |

`infra_failure` comes from `errors.InfraFailure`, whose raise sites fall into
six categories, each with a test: a Gate 0 refusal (state or contract digest),
an arm that would not spawn or whose worker broke (on either arm), unequal
corpus digests, a baseline parity miss, a judge provider that could not be
reached, and an uncalibrated judge. Any other
exception is also written as `infra_failure` with the exception type in
`notes`. Collapsing `infra_failure` into `reject` would let flaky
infrastructure read as evidence against a change, and a test drives both
verdicts from their separate causes.

**Invalidated, no verdict.** On any `ArtifactIntegrityError` the runner writes
`state="invalidated"` and leaves `verdict` at its schema default.
`runner.has_verdict(evaluation)` returns True only for `state == "complete"`;
consumers read it before `verdict`, and a test pins that an invalidated row
never carries `accept` or `reject`. An unverifiable artifact is not weak
evidence, it is no evidence.

The three handlers are disjoint with no shared fall-through. On every exit
other than `complete` the experiment is left `aborted`, the state the model
reserves for "infra failure, never a result"; see the repair below.

## Arm isolation

Isolation is **snapshot-and-restore with a writer kill switch**. Redis has no
logical-database copy-on-write, so building it means intercepting every
command; a shared instance with a freeze keeps both arms in one keyspace,
where one escaped write corrupts the comparison invisibly. A fresh process
per arm gives byte-identical reads by construction, and the kill switch
survives as an independent second guard.

The subprocess boundary:

- `arena.py` spawns `redis-server --port 0 --unixsocket <tmp>/arm.sock --save '' --appendonly no --dir <tmp>`
  in its own process group. No TCP listener means no port for a concurrent
  agent to collide on (#2799). The `finally` terminates the child and removes
  the tmpdir, on the clean path and the raising path.
- `arm_worker.py` is the only process that talks to an arm. It is launched
  with an env dict carrying `REDIS_URL=unix://<arm.sock>`,
  `POPOTO_CONTENT_PATH=<arm tmp>/content`, `VALOR_PROJECT_KEY`,
  `POPOTO_EMBEDDING_INVALIDATION=none`, and `RETRIEVAL_MODE=current`. Popoto
  binds its canonical pool from `REDIS_URL` at import, so inside the child,
  and only inside the child, the ORM *is* the arm's private server:
  `Memory.query` and `agent.memory_retrieval.retrieve_memories` work
  unmodified. The clock-gap test's skew travels inside the job spec
  (`clock_skew_s`), never through the environment, and the runner forwards
  only `ARM_PARAM_KEYS` (`limit`) from a protocol's arm dicts. Both arm
  dicts are validated once at protocol load, before the corpus export and
  the arms spawn, so any other key ends the run as `infra_failure` whatever
  the protocol's `infra_failure_cap`; neither the environment nor a frozen
  contract can skew a real arm's clock.
- The corpus identity is `corpus.canonical_corpus_digest`, never a hash of
  the raw JSONL: record order and the manifest's `exported_at` vary per call,
  and the exporter's embedding-provider fingerprint (`embedding_provenance`
  in the manifest, `state.<field>.provenance` per record) varies per
  *process*. An arm subprocess has no provider configured, and a parent's
  provider depends on import order, so the fingerprint is popped by name;
  the carried vector bytes stay in the digest. Any other manifest key that
  turns volatile surfaces as an inter-arm mismatch rather than being absorbed.
- The corpus moves as popoto `export_records`/`import_records` JSONL. Restore
  passes `on_conflict="overwrite"` and `on_embedding_mismatch="carry"`, and
  relies on `import_records` saving with `skip_auto_now=True`. Dropping
  either makes two arms rank differently from identical bytes: a re-stamped
  relevance timestamp or a re-embedded vector moves the ranking the baseline
  rests on, and the parity gate catches it.

**The parent's canonical pool is never re-pointed.** The runner never
constructs a Redis client, never assigns `REDIS_URL` in its own environment,
never calls popoto's pool-rebinding helper, and never imports the test-suite
db-claim pool. A later reader will be tempted to simplify the child process
away; three tests and a row of `grep` anti-criteria exist so that simplification
fails loudly.

**The harness measures the four-signal RRF path.** Retrieval ranks through
`agent.memory_retrieval.retrieve_memories` with `RETRIEVAL_MODE=current`
pinned in every arm's env dict (`arena.ARM_RETRIEVAL_MODE`), so the four RRF
signals, all reads of persisted state, are what both arms run. The ambient
default `auto` routes through popoto's hybrid `ContextAssembler` first, whose
post-retrieve effects write confidence and access-tracker updates through a
raw pipeline; inside an arm that write trips the corpus digest re-check
whenever the query's candidate pool is larger than the assembler's
`max_items` (`2 * limit`), so some hit goes unselected and gets suppressed,
which is the production shape. A test runs exactly that query through the
shipped arm worker: first with the pin patched back to `auto`, asserting the
digest re-check fails, then pinned, asserting the job reports `ok` with
exactly `limit` ids. The RRF path also breaks confidence
ties by key: Redis iterates a hashtable-encoded hash in per-server random
order, and two fresh arms would otherwise rank tied records differently from
identical bytes. It never ranks through popoto's decay-clock query, whose
clock is `time.time()` inside the call with no parameter to pin it; a test
queries a second arm under a clock 30 days forward and asserts the ranked ids
do not move.

## Blinding

Blinding is measured, and a leak is recorded rather than suppressed.
`scan_for_identity` derives its token list from the experiment record (the
surfaces, manifest, hypothesis, mechanism, falsifier, and any operator-supplied
identity tokens), each resolved through its store by `runner.identity_source`
so the manifest's content is what gets tokenized, and scans the serialized
judge input and envelope. A hit sets `ImprovementEvaluation.blinded=False` and
names the tokens in `notes`. `blinded=True` is written only when a scan ran
and found nothing. It is a typed boolean on every run that reached the
judges, so `blinded=False` is a queryable fact; a run that ended before any
judge scan (an `infra_failure`, or every trial excluded within the cap)
leaves it null because there is no blinding fact to state.

## The judge envelope

Built on the record shape `agent/sdlc_review_consensus.py` already consumes
(`judge_id`, `verdict`, `blockers`, `confidence`), wrapped with the experiment
id, contract digest, charter digest, evaluator version, trial id, a reference
to the raw response on the verifying store, and the **blinded arm id**. The
wrapper refuses a true arm identity in the blinded slot.

The `serves-charter` judge (`SERVES_CHARTER_JUDGE_ID = "serves-charter"`,
proven disjoint from `code-quality`, `risk`, and `cross-vendor`) scores one
arm's output against `ImprovementCharter.text`, quoted verbatim in the prompt,
and carries the digest that text hashed to. Every response field is coerced
with a typed fallback; an unparseable response is `{"status": "skipped", ...}`
and never a fabricated verdict. A skipped judge is an `infra_failure` for the
run. The verdict is one input to consensus and gates nothing on its own.

Provider routing follows charter §7 through `tools/improvement_eligibility.py::is_open_source`:
an open-source project may route the judge to any provider within the
inference budget; client work stays on the Claude subscription. The guard is
called, never reimplemented.

The subscription transport is a headless `claude -p` confined to the prompt:
`--tools=` disables every built-in tool, `--strict-mcp-config` loads no MCP
server, and the process runs from an empty temporary directory, so the judge
cannot open the repo or the artifact store and de-blind itself. With no tools
the call is a single model turn.

## Calibration

A judge whose agreement with a reference set is unmeasured is an opinion, not
an instrument. `calibration.py` reads this project's `ImprovementEvidence` rows
classified `architectural`, freezes that set to the verifying artifact store,
and cites it by digest from every calibration record. The freeze exists because
`ImprovementEvidence` expires on a 30-day window and the architectural bucket
is small and rotating; a kappa reported in March has to remain checkable in
September, so recalibration writes a new artifact rather than mutating one.

Three numbers are reported: Cohen's kappa (chance-corrected agreement against
the reference expectation), raw agreement (the plain fraction the judge
labelled as expected), and paired position-swap consistency (the fraction of
items whose verdict survives a swapped presentation). They are recorded on
the evaluation, not only logged: the default roster writes one
`calibration: {...}` JSON line into `ImprovementEvaluation.notes` carrying
the frozen artifact reference, its digest, the set size, and all three
figures; `runner.calibration_record(evaluation)` reads it back.

**Read raw agreement, not kappa, on the current reference set.** The set is
one class (every retained architectural correction expects `CHANGES
REQUESTED`), and against a one-class reference observed agreement equals
chance agreement for any imperfect judge, so kappa is exactly `0.0` from 0
through n-1 agreements and `1.0` only at n of n. It cannot rank two imperfect
judges and a `0.0` is not a failing judge. Kappa stays recorded because a
later reference set with a negative class (retained non-architectural
corrections expected `APPROVED`) turns the same figure informative without
changing the record shape; a test pins the degeneracy on the real shape.

The reference-set floor is `MIN_REFERENCE_SET_SIZE = 20`. Below it the judge
returns `infra_failure` rather than an uncalibrated opinion; the default
roster calibrates before any arm spawns, so an uncalibrated judge fails the run
before any Redis is started. No kappa threshold gates anything yet: early
calibrations are labelled by their size.

## Statistics

- Paired per-trial deltas per endpoint (candidate minus incumbent).
- Seeded bootstrap 95% confidence intervals from `tools/memory_eval/metrics.py`,
  with clustered resampling by project so one noisy project cannot carry a result.
- One-sided bootstrap p-values per endpoint, Holm-adjusted. Holm is three
  named operations (sort ascending, multiply by `(m - j + 1)`, cumulative
  maximum clamped at 1.0) mapped back to input order; the cumulative maximum
  is the known defect site and its monotonicity is pinned as a property.
- Fixed-batch stopping only. Interim estimation is biased, so a short batch is
  refused rather than computed and flagged.

`effect` and `confidence_interval` are stored per endpoint as JSON; the CI
carries lower, upper, `n`, and both raw and adjusted p-values.

## Recovering a wedged experiment

A crash between the `frozen -> running` write and the verdict write leaves the
experiment at `state="running"`, and Gate 0 refuses every retry. The harness
does not reclaim it automatically: `ImprovementExperiment` carries no liveness
timestamp, so nothing can tell a live run from a dead one. The control plane's
reconcile pass (`improvement-intent-reconcile`, [Improvement Controller](improvement-controller.md#dispatch-lane-3))
reclaims the dispatch intent and its lane slot; the experiment row itself is
repaired by hand as below.

The repair is explicit, through the ORM:

```python
from tools.improvement_eval.runner import repair_wedged_experiment
repair_wedged_experiment(project_key=project_key, experiment_id=experiment_id)
```

which is the one-liner

```python
e = ImprovementExperiment.query.filter(project_key=project_key, id=experiment_id).first()
e.state = "frozen"
e.save()
```

(`id`, not `experiment_id`: the experiment key is an `AutoKeyField`;
`experiment_id` is the field on `ImprovementEvaluation`). The same call
returns an `aborted` experiment to `frozen` for a retry once the cause of an
`infra_failure` is fixed. `test_crashed_run_leaves_a_documented_repair` imports
and executes this function, so the tested repair and the documented repair are
the same object.

A second `evaluate()` for one experiment that reads a state other than
`frozen` writes an `infra_failure` evaluation naming the state it found and
touches nothing else. Popoto offers no compare-and-set, so that window is a
small tolerated race; the control plane's case lease (`CaseLease`, [Improvement Controller](improvement-controller.md#control-namespace-contract-lane-3))
fences dispatch, not this per-experiment write.

## Mutation proofs

Every guard in the harness ships with a recorded red-state proof: the named
mutation was applied, the named test went red, the mutation was reverted, and
the test went green. The table lives in the plan
(`docs/archive/plans-completed/improvement-controller-lane-4-frozen-evaluation-inputs.md`,
"Mutation proofs"), and the proof record from the build is in the PR that
landed #3216.

## Releases

An `accept` verdict produces an `ImprovementRelease` in `state="proposed"`, which
is a proposal for a human. It carries the surfaces, the exposure, the
`rollback_plan`, and the `observation_window_ends_at`, all written **before**
anything is exposed, so "we can undo this" is a written commitment rather than a
reassurance offered after something goes wrong.

**Automated promotion is disabled and no record here enables it.** It stays
disabled until evaluator secrets and production credentials are separated from
candidate execution, and until a human-amended charter names the reversible
surfaces. Both are events outside this work. A worktree is not a security
boundary. The harness never touches `ImprovementRelease`.

## What the dashboard will never show

Experiment count and merged-patch count. They are activity. Presenting either as
improvement is the specific dishonesty the plan names, and
`ui/data/improvement.py` has no function that returns them, pinned by a test.

## See also

- [Improvement Controller](improvement-controller.md), records, evidence collection, control namespace, break-glass
- [`docs/plans/recursive-self-improvement.md`](../plans/recursive-self-improvement.md), the full design
- [`docs/archive/plans-completed/improvement-controller-lane-4-frozen-evaluation-inputs.md`](../archive/plans-completed/improvement-controller-lane-4-frozen-evaluation-inputs.md), the lane 4 plan with the mutation table and verification rows
- [Capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md), what is implemented versus measured
