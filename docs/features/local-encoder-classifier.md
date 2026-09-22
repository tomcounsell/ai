# Local Encoder Classifier

**Status:** Shipped (issue #3420, lane B). The backend, the fit path, the weights step, and the doctor row are in place; no site declares `Backend.LOCAL_ENCODER` yet. The per-site landing run happens on the Valor host that owns the `valor` bridge, and its outcome table lands in this page and in [LLM Task Taxonomy](llm-task-taxonomy.md#lane-b-outcome).

`Backend.LOCAL_ENCODER` is the third backend leg under `agent/llm/run_typed`: a supervised local classifier made of one pinned sentence-embedding model served in-process on CPU and one committed linear head per landed site. The head is distilled from the site's reference backend (Haiku with the site's prompt verbatim) by the comparison runner's fit path, measured on a held-out split by the same acceptance bar every local landing meets, and landed by the same one-word `backend` edit. A call costs a few milliseconds, needs no daemon, no GPU, and no request. The router rules, the bar, and the site table are in [LLM Task Taxonomy](llm-task-taxonomy.md); the transport is in [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md); the weights, the log greps, rollback, and crash recovery are in [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md).

## The Leg

`agent/llm/backends/local_encoder.py` meets lane A's leg protocol (`call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack)`). `route.model` is the site id, which names the head: this backend's "model" is the per-site head.

### The embedding

`config.models.LOCAL_ENCODER_MODEL` is `Xenova/bge-small-en-v1.5` at revision `ea104dacec62c0de699686887e3f920caeb4f3e3` (the ONNX export of `BAAI/bge-small-en-v1.5`, MIT), served as `onnx/model_int8.onnx` (34 MB) with its `tokenizer.json` through `onnxruntime` and `tokenizers` (the `classification-local` extra in `pyproject.toml`). `embed` tokenizes with truncation at 512 tokens (`MAX_TOKENS`), feeds `input_ids`, `attention_mask`, and `token_type_ids`, takes the CLS vector of `last_hidden_state`, and L2-normalizes it: a 384-d float32 vector (`LOCAL_ENCODER_DIM`). `scores` is `softmax(vector @ W + b)` keyed by class.

`load_runtime` builds the `InferenceSession` (`CPUExecutionProvider`, `intra_op_num_threads = min(4, os.cpu_count() or 1)`) and the tokenizer once per process under a `threading.Lock`, so a burst of first calls after a restart pays one load (Race 1). `_build_runtime` verifies every pinned file's sha256 against `LOCAL_ENCODER_FILES` before importing the extra, so a machine with neither reports the precise file and the download script. Measured on the M4 Air (Spike 3): load 0.05 s; 1.2 ms p50 and 1.4 ms p95 per call at concurrency 1; 2.4 ms p50 and 3.1 ms p95 at concurrency 4; a 512-token input embeds in 51 ms. There is no semaphore: `InferenceSession.run` is thread-safe and the CPU work runs inside `asyncio.to_thread`, so the event loop never blocks. Each landing record carries `p95_c4` measured on the landing host, which is what confirms the no-semaphore decision per host.

### The head file

A landed site serves `agent/llm/backends/heads/<site>.json`, committed beside the leg (`served_head_path`). The file is exactly `Head.to_dict()`, written with `indent=2, sort_keys=True`:

| Field | Meaning |
|-------|---------|
| `site` | The site id. |
| `classes` | The output field's values as the runner's `label` reducer renders them: `"True"`/`"False"` for a `bool` field, the literal strings for a `Literal` field. Column order of `W` and `b`. |
| `W` | `LOCAL_ENCODER_DIM` (384) rows of `k` floats; `W[i][j]` is dimension `i`, class `j`. |
| `b` | `k` floats. |
| `embedding_model`, `embedding_revision`, `embedding_sha256` | The pin the head was fit on; `embedding_sha256` is the int8 model's digest and must equal `LOCAL_ENCODER_FILES["onnx/model_int8.onnx"]`. |
| `run_id` | The fit run that produced it; the audit compares it to the landed record's `fit.head_run_id`. |
| `n_train`, `n_train_real`, `reference_model`, `created_at` (ISO 8601 UTC), `fit_settings` | Provenance: the training split's size and real-message count, the reference arm that labeled it, and the fit's `epochs`, `lr`, `l2`, `normalized`. |

`load_head` refuses fewer than two classes, duplicate classes, a `W` or `b` of the wrong shape, or an embedding digest that is not the pin (`LLMCallError(reason="validation")`). `served_head(site)` memoizes the served head per process under its own lock, so a head read on the event-loop thread never waits behind a runtime build on a worker thread; a missing file raises `LLMCallError(reason="transport")` naming the path. `tests/unit/test_classifier_heads.py` is the static half: every committed head loads through `load_head`, its `classes` equal the closed set of its site's output type, and the site declares `Backend.LOCAL_ENCODER`; a head without a declaration or a declaration without a head fails the test.

### The output-type shape rule

`_shape(output_type, head)` runs before any ONNX work. A landed site's output type has exactly one field typed `bool` or `Literal[...]`, and its rendered values equal `set(head.classes)`; a `confidence: float` field, if present, receives the winning softmax score; every other field has a default. Two closed-set fields, a required field outside the label and `confidence`, or classes that differ from the head's raise `LLMCallError(reason="validation")` with no ONNX run, and the wrapper's Anthropic fallback answers. `classify` then builds `output_type(**{label_field: value, "confidence": score})`, mapping `"True"`/`"False"` back to `bool` for a bool field.

### What the leg accepts and ignores

`system` is accepted and ignored. The head was fit on the text alone; the instruction block is carried for the Anthropic fallback, which receives it as its system prompt. `slot_timeout`, `max_retries`, and `stack` are accepted and unused: there is no semaphore, no SDK, and no third-party symbol taken from the stack.

There is no request timer, because there is no request. The CPU work is bounded by the 512-token truncation window. The one timing check is `bound_to_deadline` first in `call`: on a fallback call with under `MIN_REMAINDER_S` (0.5 s) of the caller's budget left it raises `LLMCallError(reason="timeout")` before any load. `default_sdk_timeout(Backend.LOCAL_ENCODER)` returns `settings.timeouts.local_typed_hard_s`, and on this leg that value only feeds the wrapper's fallback deadline arithmetic ([Config Timeout Catalog](config-timeout-catalog.md)). No `asyncio.wait_for` appears in the leg; `tests/unit/test_llm_task_taxonomy.py` check 6 walks every function in `agent/llm/backends/` for it.

Every failure is an `LLMCallError`, so the router's Anthropic fallback runs inside the caller's budget: `transport` for a missing extra (`classification-local extra not installed`), a missing or mismatched weights file (naming the file and `scripts/download_local_encoder_models.py`), a missing head, or an exception inside the ONNX run (the one broad handler in the leg, cause chained, one `logger.error`); `validation` for an empty prompt, a malformed head, or an output type the head cannot answer.

### Import safety

Module scope is stdlib and our own code only (#3001, #3525). `onnxruntime`, `tokenizers`, and `numpy` are imported inside `_build_runtime`, `embed`, and `scores`, so `import agent.llm` (and therefore `import bridge.telegram_bridge`) succeeds on a machine without the extra and the failure surfaces at the call. The leg never downloads: `/update` fetches the weights. `load_runtime` is the test seam (`monkeypatch.setattr(local_encoder, "load_runtime", lambda: fake)` with `tests/helpers/llm_fakes.py::FakeEncoderRuntime`).

## Routing and the Wrapper

`agent/llm/router.py::resolve` places the encoder rule ahead of the Ollama rules: `backend == LOCAL_ENCODER` and `is_eligible(project_key)` returns `Route(LOCAL_ENCODER, task.site, fallback=Route(ANTHROPIC, model))`; ineligible context returns the Anthropic route (fail-closed, charter §7). The wrapper maps `_LEGS[Backend.LOCAL_ENCODER] = local_encoder.call`, runs the degraded-stack guard on the import axis only (`signature_axis=False`, as for Ollama), and runs the Anthropic fallback once on `LLMCallError`.

The `llm_route` line carries the score: `llm_route site=<site> backend=<backend> elapsed_ms=<int> confidence=<score>` whenever the returned instance has a `confidence` attribute that is not `None` (`%.3f`), on either leg. An Anthropic answer with a `confidence` field logs it too, so one grep compares the two legs' score distributions per site; a result without the attribute logs the lane A line byte for byte. After a day of traffic on a landed site:

```bash
grep -o "llm_route site=<site> backend=local_encoder .*confidence=[0-9.]*" logs/bridge.log
```

gives the site's score distribution. A cluster near `1/k` for `k` classes is the drift signal that calls for a measure-only re-fit (below).

### The call shape a landed site uses

Landing restructures the call: `prompt` is the text under classification and the instruction block moves to `system`. The leg embeds `prompt`; the Anthropic fallback receives both. For a context-bearing site (C2 thread, C6 transcript, C10 pair, C11 window) one composition function beside the declaration builds the text message-first (`f"{message}\n\n{context}"`), and the runner row's `candidate_prompt` calls the same function, so the 512-token truncation drops old context before the message and the served text is byte-identical to the measured text.

## Weights

`config/models.py` holds the pin: `LOCAL_ENCODER_MODEL`, `LOCAL_ENCODER_REVISION`, `LOCAL_ENCODER_FILES` (file name to sha256), `LOCAL_ENCODER_DIM`, and `local_encoder_models_dir()` (`LOCAL_ENCODER_MODELS_DIR`, default `~/.cache/valor-encoder/`, shared across worktrees). `scripts/download_local_encoder_models.py` fetches the files at the pinned revision, streaming to a `.part` sibling and renaming only on a sha256 match; `scripts/update/local_encoder.py::ensure_models` runs it as `/update` Step 3.15 (non-fatal); `python -m tools.doctor` reports the `local_encoder` row (extra, weights, heads). The operator detail, the rollback lever, and the crash recovery are in [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md#local-encoder-weights).

## The Fit Protocol

`tools/classification_eval/fit.py` and the flags in `python -m tools.classification_eval`:

```bash
python -m tools.classification_eval --preflight
python -m tools.classification_eval --precheck
python -m tools.classification_eval --site <id> --fit --candidate local_encoder,anthropic \
    --save-inputs data/classification_eval/<id>.jsonl [--precheck-agreement <n>]
python -m tools.classification_eval --site <id> --fit --land --candidate local_encoder,anthropic \
    --save-inputs data/classification_eval/<id>.jsonl
python -m tools.classification_eval --audit
```

A fit runs on a host whose memory store holds real inbound `valor` messages, with the services stopped (`./scripts/valor-service.sh stop`): `run_fit` refuses while `is_contended()` is true, because a fit changes what serves (Race 2) and a contended record cannot clear the bar (Race 3).

### Split by digest

`split_by_digest(inputs, minimum_n)` is a pure function of the input set. Inputs sort by `sha256(text)`. The held-out split takes real inputs first, by digest, up to `ceil(minimum_n / 2)`, then fixtures and the remaining real inputs by digest until it holds `minimum_n`; everything else is the training split, in digest order. A `--save-inputs` file replays the same split anywhere. Under `minimum_n` held-out inputs, or under half of them real, `ShortfallError` refuses before any arm runs (exit 2, no spend). The split is chosen before any label exists and the fit never sees the held-out inputs: no early stopping, no model selection on them.

### Label, fit, stage

`label_training_split` labels the training split with the reference arm (the site's prompt verbatim, its `system`, its output type) at the runner's concurrency-4 gate; an arm error drops that input, and an error rate over `MAX_ERROR_RATE` raises `FitError` (exit 1, no head written). Labels that collapse to one class also abort. `fit_head` is a numpy multinomial logistic regression: zero init, 300 full-batch epochs (`FIT_EPOCHS`), learning rate 0.5 (`FIT_LR`), L2 `1e-3` (`FIT_L2`), inputs L2-normalized, no randomness, so a second fit on identical inputs and labels is bit-identical. The training-split agreement prints as a sanity line and is never the claim.

`write_head` writes the head atomically to the staging path `data/classification_eval/heads/<site>.<run_id>.json` (gitignored). The served path `agent/llm/backends/heads/<site>.json` is untouched at this step, and the `local_encoder` candidate arm for the measurement reads the staged head by path (`local_encoder_arm(site, head_path=staged)`), so the measurement never reads the served head.

### What the head embeds

`encoder_text(site, inp)` is the text the head is fit on and measured on: the row's `encoder_text(inp)` when the row sets one, else the message text `inp.text`. It is never the reference prompt, and never the row's `candidate_prompt`: that field is lane A's prompt for the granite arm, and on C2, C6, and C9 it carries the instruction block, which would dominate a CLS embedding and push the message past the 512-token window. The encoder lane has its own two row fields, `encoder_text` (the message-first composition function a context-bearing site shares with its call site; unset means the bare message) and `encoder_system` (the instruction block the landed call passes as `system`; unset means the row's `system`). `measured_site` builds the row `compare` measures from them: the candidate prompt is `encoder_text`, the candidate system is `encoder_system`, and the candidate output type is the site's own, so the `local_encoder` arm receives byte for byte what the fit embedded and the `anthropic` arm receives that text with the instruction block as its system prompt, which is the restructured `(text, system=instructions)` shape a landed site's fallback serves. Lane A's `candidate_prompt`, `candidate_system`, and `candidate_output_type` never reach the encoder lane's fit or measurement. `tests/unit/test_classification_eval.py` walks every row of the site table and fails if the encoder text of any row contains its `candidate_prompt`'s instructions.

Before the first reference call, `ensure_encoder_runtime` loads and verifies the encoder runtime (extra importable, weights present with their pinned sha256, session built), so a landing host that cannot embed refuses the fit (`FitRefusalError`, exit 2) at zero spend instead of after the training labels.

### Measure on the held-out split

`compare` runs unchanged from lane A on the held-out split only: agreement pass at concurrency 1, latency pass at 4, both candidate arms on the same text. The record gains a `fit` block:

```
fit: {head_run_id, n_train, n_train_real, split: "digest", landed: <bool>, miss_arms: [...], miss_criteria: {...}}
```

`write_record` and `attach_claims` run on every fit, because the record is the measurement; `render_report` prints `fit: n_train=… n_train_real=… head=<run_id> landed=<bool>` and one `landing MISS: <arm> on <criteria>` line per missed arm. The held-out record is the only claim about a head.

### `--land`

`run_fit(..., land=True)` is the only path that writes or deletes the served head, and the runner holds the rule:

- `--land` requires the `anthropic` candidate. `run_fit` raises `FitRefusalError` before any arm runs (exit 2, no spend) when `anthropic` is absent from `--candidate`, because the restructured-shape Anthropic arm is the fallback a landed site serves on every leg error and its agreement is part of the landing (Risk 3).
- After `compare` returns, `landing_verdicts` evaluates `evaluate_bar(record, "local_encoder")` and `evaluate_bar(record, "anthropic")` on the same held-out record; an arm absent from the record misses on `missing`. Only when both lists are empty is the staged head copied to the served path (`_install_served_head`, atomic rename), `fit.landed` set `true`, and `landed: <path> (head <run_id>)` printed.
- When either arm misses, the run is a MISS for landing: any served head for the site is deleted (no orphan weights), `fit.landed` stays `false`, `fit.miss_arms` names the arms and `fit.miss_criteria` their failing criteria, and the staged head stays under `data/` for inspection.

On a landing the builder sets `backend=Backend.LOCAL_ENCODER` on the declaration, restructures the call to `(text, system=instructions)`, and commits the head with the declaration and the record id in the message.

Without `--land` a run measures, prints the report, leaves the staged head in place, and stops: a measure-only run on a landed site is a drift check that can never remove or replace its serving head.

### The audit

`--audit` judges a `LOCAL_ENCODER` site on its latest record whose `fit.landed` is `true` (`latest_record(site, where=is_landed)`); measure-only records are skipped, so a diagnostic re-run on a landed site leaves the audit green. `_audit_row` requires the record's `local_encoder` arm and its `anthropic` arm both to clear `evaluate_bar`, and the committed head's `run_id` (read uncached through `load_head`) to equal the record's `fit.head_run_id`. No head file, a mismatched `run_id`, or no landed record at all is a MISS (exit 1). Every backend other than `ANTHROPIC` is judged as a local landing this way; `OLLAMA` sites are judged on their latest record.

### Preflight

`--preflight` prints `real messages available: N` and, per site, `needs >= M real in the held-out split` with `n_train` (`n_fixtures + n_real - minimum_n`) and `n_train_real` from `split_by_digest` on the actual draw, or the shortfall in words. It exits 1 when `N` is under `ROUTING_REAL_NEED` (100, the routing sites' need). It is the gate that proves a host can satisfy `n_real` before any spend.

### The precheck gate

`--precheck` scores every site with a lane A record on this machine for free: the record's `reference.labels[:n_fixture]` paired with `site.fixtures()`, embedded through the leg's `embed`, scored five-fold by `cv_agreement`, which fits every fold through the same `fit_head` the landing run uses (never a separate routine, so the gate's number is the head that would serve). Each row prints beside the majority baseline, the tier bar, and the gate `bar - PRECHECK_MARGIN`; rows sort by agreement relative to the bar, highest first, which fixes the landing order. C12 (`job_router.route`, an open-set answer) and `client_only` sites are excluded; sites without a lane A record carrying a reference arm are listed as skipped.

`PRECHECK_MARGIN = 0.10`. A site whose precheck agreement is under `bar - 0.10` (under 0.85 on a `high` site, 0.80 on `medium`, 0.75 on `low`) is marked `precheck_below_bar` and skips the fit: `--fit --precheck-agreement <n>` on such a site makes zero reference calls, writes no record, and records one `precheck_below_bar` claim on the case through `attach_precheck_claim` (the same `record_claims` path every runner claim takes). A site inside the margin fits, because real training messages can close a gap of ten points and cannot close one of thirty. Widening the margin is a change to the constant, never a builder call.

## Precheck (build machine, Air)

`python -m tools.classification_eval --precheck` on the MacBook Air, against lane A's records in its Redis, embedding the bare message text on every row: about 3 s of wall clock (8 s of CPU across the ONNX threads) on a quiet machine, longer under load, zero spend.

| site | n | cv_agreement | majority | bar | gate | mark |
|------|---|--------------|----------|-----|------|------|
| promise_gate.verdict | 40 | 0.925 | 0.575 | 0.85 | 0.75 | fit |
| improvement_collect.promise_judge | 40 | 0.850 | 0.825 | 0.85 | 0.75 | fit |
| injection_inspection.risk | 40 | 0.875 | 0.625 | 0.90 | 0.80 | fit |
| context_recall.advised | 40 | 0.850 | 0.800 | 0.90 | 0.80 | fit |
| routing.needs_response | 188 | 0.883 | 0.793 | 0.95 | 0.85 | fit |
| health_check.judge | 40 | 0.825 | 0.800 | 0.90 | 0.80 | fit |
| routing.terminus | 188 | 0.840 | 0.809 | 0.95 | 0.85 | precheck_below_bar |
| intent_classifier.intent | 188 | 0.654 | 0.335 | 0.95 | 0.85 | precheck_below_bar |
| session_completion.novelty | 40 | 0.600 | 0.675 | 0.90 | 0.80 | precheck_below_bar |
| routing.work_request | 188 | 0.564 | 0.346 | 0.95 | 0.85 | precheck_below_bar |
| classifier.work_type | 40 | 0.450 | 0.500 | 0.90 | 0.80 | precheck_below_bar |
| agent_catchup.judge | 40 | 0.325 | 0.450 | 0.85 | 0.75 | precheck_below_bar |

Skipped: `classifier.intake_intent` (C13) and `memory_audit.classify` (C14), latency-only records with no reference labels; C12 excluded by rule; C16 `client_only`. Six sites reach a fit on this table (C9, C15, C7, C8, C1, C11, in that order); six skip. Three rows were re-measured after the fit path stopped reading lane A's `candidate_prompt`: `promise_gate.verdict` (C9) moved from 0.750 on the wrapped draft to 0.925 on the bare draft and now leads the order; `routing.terminus` (C2) moved from 0.809 to 0.840, above its majority baseline and still under the gate, because the bare reply carries no thread context (the `encoder_text` composition the landing step adds for context-bearing sites supplies it); `agent_catchup.judge` (C6) moved from 0.400 to 0.325, because the bare message carries none of the transcript the verdict depends on. The precheck is fixture-only, so the landing host's table reads close to this one; it is re-run there before the landing loop so the gate reads that host's records.

## Landing Outcome

The landing run (preflight, fit, land per site) is tracked as [#3544](https://github.com/tomcounsell/ai/issues/3544) and happens on the Valor host that owns the `valor` bridge, whose memory store holds the real inbound messages the bar's `n_real` criterion demands; this section carries its per-site table (both candidate arms' held-out agreement, `p95_c4`, `n`, `n_real`, `n_train`, `n_train_real`, head run id, record id, failing criterion if any) once that run has happened.

### The landing run, in order

Run from a checkout of the lane branch on that host. In a worktree, export `PYTHONPATH=$PWD` first: the venv's flush-guard `.pth` imports `tools` during `site` processing, before `-m` puts the cwd on `sys.path`, so a bare `python -m tools.classification_eval` in a worktree resolves to the primary checkout (`scripts/pytest-clean.sh` pins the same variable for the same reason).

1. `uv sync --all-extras --frozen`, then `python scripts/download_local_encoder_models.py` and `python -m tools.doctor | grep local_encoder` (must read `PASS`).
2. `./scripts/valor-service.sh stop` (and `worker-disable` if launchd would relaunch the worker), then `python -m tools.classification_eval --preflight`. Exit 1 means the routing sites are out of reach on this host by the bar's own rule; the 50-minimum sites can still run.
3. `python -m tools.classification_eval --precheck` on this host's records. Its `fit` rows, highest agreement relative to bar first, are the order; each `precheck_below_bar` row gets one claim on the case and no fit.
4. Per fit site, boxed at half a build day: the caller census (`grep -ln "<site function>" tests/unit/*.py`); for a context-bearing site, the message-first composition function beside the declaration, pointed at by the row's `encoder_text`, and the instruction block the restructured call will pass as `system` in the row's `encoder_system`; then `python -m tools.classification_eval --site <id> --fit --land --candidate local_encoder,anthropic --precheck-agreement <the site's cv_agreement from step 3> --save-inputs data/classification_eval/<id>.jsonl` (`--reference-model paid` for C15 when the free route is throttled). The `--precheck-agreement` value is what arms the spend gate: a site under `bar - 0.10` then refuses before any reference call. On `landed=true` the runner has installed the head: set `backend=Backend.LOCAL_ENCODER`, restructure the call to `(text, system=instructions)`, keep the fail-safe byte-identical, update the site's tests, and commit the head with the record id and head run id in the message. On `landed=false` the runner has removed any served head; the site stays on `ANTHROPIC` and the report's `miss_arms` line goes in the outcome table.
5. `python -m tools.classification_eval --audit` (exit 0), `scripts/pytest-clean.sh tests/unit/test_classifier_heads.py -q`, the per-site table into this section and [LLM Task Taxonomy](llm-task-taxonomy.md#lane-b-outcome), then `./scripts/valor-service.sh restart`.

If no site lands, the rejection exit applies: the backend member, leg, extra, weights script, update step, and doctor row go in one commit; `--fit`, `--precheck`, and `--preflight` stay; each MISS is on the case.

## Zero-Shot GLiClass: Rejected

Issue #3420 proposed a zero-shot GLiClass classifier through int8 ONNX on the premise that a zero-shot encoder can beat a 3B generative model on closed-set labels while staying on CPU. The pinned model, `knowledgator/gliclass-base-v1.0` at revision `aa3ac24a77860a033c9e9a3aa90d5872722cb7bf` (Apache-2.0; `onnx/model-int8-quantized.onnx` sha256 `8eb7db6e3c82746b5bc917305b2cf6dc2b020c1223205ed8638f26dd8ee428e1`, `tokenizer.json` sha256 `519648948c4c59da1af88f2cf2c8b4f84417b5c673981bc9809abf84cda1b7cc`), runs on CPU as the issue describes: 0.45 s to load, 10 ms per call, 31 ms p95 at concurrency 4 on the M4 Air, `onnxruntime` and `tokenizers` only. Scored against Haiku's own labels on lane A's committed fixtures, best of three label phrasings per site, it sits under the majority-class baseline on every site but C9 and 15 to 75 points under the tier bars:

| Site | n | Agreement (best of 3) | Range across phrasings | Majority baseline | Bar |
|------|---|-----------------------|------------------------|-------------------|-----|
| C1 `routing.needs_response` | 188 | 0.702 | 0.649 to 0.702 | 0.793 | 0.95 |
| C2 `routing.terminus` | 188 | 0.473 | 0.468 to 0.473 | 0.809 | 0.95 |
| C3 `routing.work_request` | 188 | 0.213 | 0.170 to 0.213 | 0.346 | 0.95 |
| C4 `intent_classifier.intent` | 188 | 0.186 | one phrasing | 0.335 | 0.95 |
| C5 `classifier.work_type` | 40 | 0.200 | one phrasing | 0.500 | 0.90 |
| C9 `promise_gate.verdict` | 40 | 0.600 | 0.575 to 0.600 | 0.575 | 0.85 |

The softmax distributions sit near uniform (0.25 to 0.45 on the winning label of four): the model does not separate speech acts (request, acknowledgment, question, status line), which is what every candidate site asks for. The rejection is recorded on case `1ec40086ca1d422e90ef747775ff7f64` as investigation `4c0d6b44b9c942a5999637afbe22e8a9` (`valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64`), carrying this table and the pin. No GLiClass code ships. The same machinery one step over, the reference arm's per-input labels as a training set for a linear head on a local embedding, is the backend this page describes; Tom accepted the change of model on the issue (comment 5755701599).

## Follow-up

A scheduled measure-only re-comparison of each landed site against fresh real messages (`--site <id> --fit` without `--land`, on a timer, with its reference spend) is tracked under #3542. It needs reference spend and a timer, neither built here; the `confidence=` grep above is the live signal until it exists.

## Tests

`tests/unit/test_llm_backend_local_encoder.py`: the success path (bool and `Literal` fields, `confidence` filled), each shape violation with no ONNX run, the empty and 2,000-word inputs, the missing extra, the checksum mismatch (one flipped byte refuses), the missing and malformed heads, `system` ignored, the deadline re-check, twenty concurrent first calls building one runtime, and the module-scope, `wait_for`, and no-download pins. `tests/unit/test_classifier_heads.py`: every committed head against its site. `tests/unit/test_classification_eval.py`: the digest split as a function of the inputs alone, the bit-identical re-fit, the head round-trip through the leg's loader, the served-head protection without `--land`, the both-arms landing gate (Anthropic miss, encoder miss, missing `anthropic` candidate), `--precheck` and `run_fit` sharing `fit_head`, the precheck gate with zero reference calls, `--preflight` including the exactly-100-real boundary, the error-rate and one-class aborts, and the audit's head-provenance cases. `tests/unit/test_llm_wrapper.py`: the encoder timer rows, the fallback on the leg's `LLMCallError` with both log lines, and the `confidence=` token. `tests/unit/test_doctor.py -k local_encoder`: the doctor row. `tests/unit/test_llm_import_safety.py`: `onnxruntime` and `tokenizers` in the raising shim.

## See Also

- [LLM Task Taxonomy](llm-task-taxonomy.md): the declarations, router rules, acceptance bar, Lane B Outcome.
- [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md): the leg protocol and the fallback budget.
- [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md): weights, the greps, rollback, crash recovery for an interrupted `--land`.
- [Local Ollama Model Policy](local-model-policy.md): the two local classification backends.
- [Config Timeout Catalog](config-timeout-catalog.md): `local_typed_hard_s`.
- [Local Doctor](local-doctor.md): the "LLM routing" section.
- `docs/plans/local-zero-shot-backend-gliclass-onnx-landed-per-site.md`: the plan, its spikes, and the critique rounds.
