# LLM Task Taxonomy

**Status:** Shipped (issue #3410, lane A; #3420, lane B; the decisions leg is shipped and its landings are pending, issue #3421, lane C, see [Lane C Outcome](#lane-c-outcome))

Every non-harness LLM call site in `agent/ bridge/ worker/ tools/ reflections/ scripts/` declares one `LLMTask` (`agent/llm/tasks.py`) as a module constant beside its output type and passes it as `task=` to `run_typed`. The declaration names the site, its kind (`classification` or `thinking`), the backend it lands on, its error-cost tier, and whether charter §7 pins it to the subscription backend (`client_only`). The router (`agent/llm/router.py::resolve`) reads the declaration with the call's project key and picks the leg. The declaration is the only per-site backend choice in the repo: moving a site between backends is a one-word diff, and the comparison record in the site's row below is the argument for the word.

The transport underneath (the wrapper, the backend legs, the fallback budget, the hotfix #1055 invariant) is documented in [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md). The local encoder backend (the embedding model, the per-site head, the fit protocol) is in [Local Encoder Classifier](local-encoder-classifier.md). The operator side (Ollama service settings, the encoder weights, the log lines, rollback levers) is in [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md).

## The Two Kinds

| Kind | What the call returns | Backend rule |
|------|-----------------------|--------------|
| `classification` | One of a closed set of labels: `bool` and `Literal` fields, a confidence, a short reason. Routing decisions, the promise gate, the completion judge, intake intent, memory audit. | Lands on the backend its declaration names, for eligible context. A local landing needs a comparison record that clears the acceptance bar below. |
| `thinking` | Prose or extracted structure: memory extraction, read-the-room's rewrite, document summaries, judges that reason in free text. | Always the subscription backend (Anthropic). Never a candidate for a local backend under this taxonomy. |

The split is enforced at the call site, as an argument, because an output model can serve two sites with different error costs and a second entry point would recreate the per-site function choice this taxonomy removes.

## The `LLMTask` Declaration

```python
from agent.llm import Backend, ErrorCost, LLMTask, TaskKind

# Fail-safe: any error answers True (respond), so no genuine question is dropped.
NEEDS_RESPONSE = LLMTask(
    site="routing.needs_response",
    kind=TaskKind.CLASSIFICATION,
    backend=Backend.ANTHROPIC,
    error_cost=ErrorCost.HIGH,
)
```

| Field | Meaning |
|-------|---------|
| `site` | A stable dotted id (`routing.needs_response`, `promise_gate.verdict`). Keys the comparison records, the doctor rows, and the table below. |
| `kind` | `TaskKind.CLASSIFICATION` or `TaskKind.THINKING`. |
| `backend` | `Backend.ANTHROPIC`, `Backend.OLLAMA`, `Backend.LOCAL_ENCODER`, or `Backend.DECISIONS`: the backend the site lands on for eligible context. `LOCAL_ENCODER` is the per-site linear head on the local embedding model ([Local Encoder Classifier](local-encoder-classifier.md)); the site's committed head `agent/llm/backends/heads/<site>.json` is its model. `DECISIONS` is TypeSafe's Jev structured-decision endpoint behind the Ollama leg as its fallback (`agent/llm/backends/decisions.py`, #3421); its per-field question metadata rides on the output type as a `Decision` marker, never on the declaration. |
| `error_cost` | `ErrorCost.LOW`, `MEDIUM` (default), or `HIGH`: what a wrong answer costs at the site. Sets the acceptance-bar tier. |
| `client_only` | `True` pins the site to the subscription backend for every project key (charter §7). |

Each declaration carries a one-line comment naming the fail-safe the caller applies on `LLMCallError`, because the wrapper never invents a default answer.

### The static registry

`agent.llm.tasks.declared_sites()` is the one list of declared sites. It is a static AST walk over `agent/ bridge/ worker/ tools/ reflections/ scripts/` (skipping `tests/` directories and `test_*.py`), never an import: a declaring module is read as source, so listing the sites has no side effects, needs no daemon, and covers modules the reading process would never import (a script, a reflection job). `tools/doctor`'s "LLM routing" section, `python -m tools.classification_eval --audit`, and the enumeration test all read it.

The walk needs every declaration to be literal, and that is the convention:

- one module-level assignment per site, `NAME = LLMTask(...)`;
- `site` a string literal; `kind`, `backend` and `error_cost` an attribute on the enum class (`TaskKind.CLASSIFICATION`, `Backend.OLLAMA`, `ErrorCost.HIGH`); `client_only` `True` or `False`; positional arguments in field order are accepted;
- anything else (a name, a call, an f-string) raises `ValueError` naming the file and line, so a declaration the walk cannot read fails loudly in doctor, in the audit and in the test rather than going unlisted.

Sites that reach an LLM through a raw transport for a reason (vision, audio, streaming, a cross-vendor client) still declare a `THINKING` task at module level; the enumeration test requires the declaration in any module whose AST contains an LLM call token. The allowlist is `agent/llm/backends/`, `agent/anthropic_client.py`, `agent/session_runner/harness/`, `tools/ollama_client.py`, `tools/classification_eval/arms.py` (the comparison runner's reference arms), and `tools/image_gen/__init__.py` (image generation: no text decision or thinking output). Embeddings, transcription, and image generation take no prompt and return no decision or thought, so they are outside the taxonomy.

## Site Table

One row per declared site, read by `tests/unit/test_llm_task_taxonomy.py` (doc/code parity): the site id, kind, landed backend, error-cost tier, and §7 class must match the declaration `agent/llm/tasks.py::declared_sites()` finds. The comparison record is the `classifier_comparison` evidence row on case `1ec40086ca1d422e90ef747775ff7f64` that argues for the landed backend; thinking sites and the `client_only` triage site carry none.

| Site | Kind | Backend | Error cost | §7 class | Comparison record |
|------|------|---------|------------|----------|-------------------|
| `agent_catchup.judge` | classification | anthropic | low | eligible | `20894afc13db4b18864b974dcdd8970b` |
| `classifier.intake_intent` | classification | ollama | medium | eligible | `63997aebc17e405884349d947784da44` |
| `classifier.work_type` | classification | anthropic | medium | eligible | `c2c3564f472b4efbb38c77798ab8c799` |
| `compat.network_probe` | thinking | anthropic | medium | eligible | n/a |
| `context_recall.advised` | classification | anthropic | medium | eligible | `322be273a83741ec80d985568671086e` |
| `cross_vendor_judge.review` | thinking | anthropic | medium | eligible | n/a |
| `doc_summary.summarize` | thinking | anthropic | medium | eligible | n/a |
| `documentation.generate` | thinking | anthropic | medium | eligible | n/a |
| `email_cs.action` | thinking | anthropic | medium | client_only | n/a |
| `email_cs.triage` | classification | anthropic | medium | client_only | n/a |
| `evaluate_build.verdicts` | thinking | anthropic | medium | eligible | n/a |
| `health_check.judge` | classification | anthropic | medium | eligible | `1684a0cc15f74d6c8b860fdd42c6b62e` |
| `image_analysis.analyze` | thinking | anthropic | medium | eligible | n/a |
| `image_tagging.tag` | thinking | anthropic | medium | eligible | n/a |
| `impact_finder.rerank` | thinking | anthropic | medium | eligible | n/a |
| `improvement_collect.promise_judge` | classification | anthropic | low | eligible | `5e3f8272c4f8428b8f1fbff92274b749` |
| `improvement_eval.openrouter_arm` | thinking | anthropic | medium | eligible | n/a |
| `improvement_eval.serves_charter` | thinking | anthropic | medium | eligible | n/a |
| `injection_inspection.risk` | classification | anthropic | medium | eligible | `49fc1cffb128440e948a16c27fae7e90` |
| `intent_classifier.intent` | classification | anthropic | high | eligible | `0c00bc4f0fe445a59c4b2466bc0162ad` |
| `job_router.route` | classification | ollama | high | eligible | `80163016557d4be4a3f8e99c85090a79` |
| `knowledge.converter_probe` | thinking | anthropic | medium | eligible | n/a |
| `knowledge.summarize` | thinking | anthropic | medium | eligible | n/a |
| `media.image_description` | thinking | anthropic | medium | eligible | n/a |
| `memory_audit.classify` | classification | ollama | medium | eligible | `03a39666e88249688db815c2502f9e57` |
| `memory_consolidation.merge_plan` | thinking | anthropic | medium | eligible | n/a |
| `memory_eval.query_generation` | thinking | anthropic | medium | eligible | n/a |
| `memory_eval.relevance_grading` | thinking | anthropic | medium | eligible | n/a |
| `memory_extraction.extract` | thinking | anthropic | medium | eligible | n/a |
| `pm_briefings.draft` | thinking | anthropic | medium | eligible | n/a |
| `promise_gate.verdict` | classification | anthropic | low | eligible | `77b5182f677c4a798f07874736ffe6d2` |
| `read_the_room.verdict` | thinking | anthropic | medium | eligible | n/a |
| `reflections.log_analysis` | thinking | anthropic | medium | eligible | n/a |
| `routing.needs_response` | classification | anthropic | high | eligible | `06b20b102be04cbfb5d16676b859de72` |
| `routing.terminus` | classification | anthropic | high | eligible | `4004915da988443089165ac47ce26e56` |
| `routing.work_request` | classification | anthropic | high | eligible | `fa8ededd07e84acd9d9b293533b69c57` |
| `session_completion.novelty` | classification | anthropic | medium | eligible | `2a4a367a023642828b7e35a6a3f961d1` |
| `test_judge.judge` | thinking | anthropic | medium | eligible | n/a |
| `valor_calendar.feature_name` | thinking | anthropic | medium | eligible | n/a |

## Router Rules

`agent/llm/router.py::resolve(task, project_key, *, model=MODEL_FAST)` is a pure function returning `Route(backend, model, fallback)`. It is the only place that consults eligibility, and it reads model names from `config.models` and nothing from per-site settings, because there are none. The rules apply in order; the first match wins.

| Rule | Condition | Route |
|------|-----------|-------|
| 1 | `kind == THINKING` or `client_only` | `Route(ANTHROPIC, model)`. `model` is the call's `model=` kwarg (default `MODEL_FAST`, Haiku). |
| 2 | `backend == ANTHROPIC` | `Route(ANTHROPIC, model)`. |
| 3 | `backend == LOCAL_ENCODER` and `is_eligible(project_key)` | `Route(LOCAL_ENCODER, task.site, fallback=Route(ANTHROPIC, model))`. The route's `model` is the site id, which names the committed head. |
| 4 | `backend == LOCAL_ENCODER` otherwise | `Route(ANTHROPIC, model)`. |
| 5 | `backend == OLLAMA` and `is_eligible(project_key)` | `Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL, fallback=Route(ANTHROPIC, model))`. |
| 6 | `backend == OLLAMA` otherwise | `Route(ANTHROPIC, model)`. |
| 7 | `backend == DECISIONS` and `is_eligible(project_key)` | `Route(DECISIONS, JEV, fallback=Route(OLLAMA, OLLAMA_CLASSIFIER_MODEL))`. No third leg: eligible context never reaches Anthropic from a decisions site. Ineligible context takes rule 6's route, `Route(ANTHROPIC, model)` with no fallback. |

Each backend rule is its own block in `resolve` with its own late `is_eligible` import and the same fail-closed read (the encoder block ahead of the Ollama block, #3420; the decisions block after it, #3421); a lane adding a backend adds a block and leaves the others byte-identical. The backends are mutually exclusive, so block order carries no behavior.

A route with a `fallback` is the only route that can log `llm_fallback`: when the primary leg raises `LLMCallError`, the wrapper runs the fallback leg once inside the caller's remaining budget (the encoder and Ollama fall to Anthropic; decisions falls to Ollama). The local rules (3, 5, and 7) are the only rules that consult eligibility. There are no per-site settings switches and no shadow routes.

## Eligibility

Charter §7 draws one line: work on an open-source codebase carries no provider restriction; client work, and the private context that comes with it, stays on the subscription backend. `tools/improvement_eligibility.py::is_eligible(project_key)` is the router's hot-path read of that line:

- `valor` is pinned `True` in code, ahead of any cache read. This repo's own rooms never wait on a cache and never fail closed.
- `None` is `False`. A site that cannot know its project resolves to the subscription backend.
- Every other key is `peek_open_source(project_key)`, a pure read of the process-local cache filled by `is_open_source` (`gh repo view <org/repo> --json visibility`, the repository passed positionally so `GH_REPO` cannot redirect it). A miss is `False` for that call and schedules one background refresh per key, so a burst's later calls see the answer; with no running loop (the synchronous `tools/doctor` path) the miss is the answer and nothing is scheduled.

The fail-closed rule: every uncertainty resolves to the subscription backend. A cold cache, a `gh` outage, a missing `github` block in `projects.json`, an unparseable answer: each costs one local call and never routes client context to a local model by mistake.

Each process warms the cache for its project list at startup through `schedule_warm_cache(keys)`: the bridge warms `ACTIVE_PROJECTS`, the worker warms its loaded `projects`. The task is held in `_BACKGROUND_TASKS` so it cannot be garbage-collected mid-run and is never awaited ahead of the connect step (each key is a `gh` shell-out with a 10 s timeout). The closing INFO line `eligibility warm-up done keys=<n> public=<n> failed=<n>` is the evidence it finished. A call that lands before the warm-up finishes takes the fail-closed miss.

Project key threading: `should_respond_async` passes `project["_key"]` to the three routing classifiers; read-the-room reads `session.project_key`; the promise gate resolves it from `session_id` through `AgentSession.project_key` (and the drafter passes `session.project_key` on every outbound reply); the session-side judges (C10, C11) read `AgentSession.project_key`; C14 reads the memory row's project; C15 has it from the adapter. `tests/integration/test_bridge_routing_project_key.py` pins the three hot-path resolutions end to end.

## Acceptance Bar

A classification site lands on a local backend, or on the decisions backend, only with a `classifier_comparison` record that clears every criterion below. The PR reviewer applies the bar per site from the landing summary in the PR body, and `python -m tools.classification_eval --audit` applies the same criteria mechanically, exiting 1 on any `OLLAMA` landing whose landing record misses one, on any `LOCAL_ENCODER` landing whose latest landed record misses one on either arm or whose committed head does not match it, on any `DECISIONS` landing whose landing record misses on the decisions arm or on the same record's ollama arm, on any site without a record, and on any `ANTHROPIC` landing whose record has no Anthropic arm. An `OLLAMA` or `DECISIONS` site's landing record (`tools/classification_eval/records.py::landing_record`) is the newest record in which the declared backend is a candidate arm, so a later comparison that carries the landed backend only as its reference never displaces the evidence for that landing; a `LOCAL_ENCODER` site's is the newest record whose `fit.landed` is true.

### Tiers

| Tier | Agreement bar | Sites | Why |
|------|---------------|-------|-----|
| `high` | 95% | C1 `routing.needs_response`, C2 `routing.terminus`, C3 `routing.work_request`, C4 `intent_classifier.intent`, C12 `job_router.route` | A wrong answer drops or misroutes a human's message or binds to the wrong job. |
| `medium` | 90% | C5 `classifier.work_type`, C7 `injection_inspection.risk`, C8 `context_recall.advised`, C10 `session_completion.novelty`, C11 `health_check.judge`, C13 `classifier.intake_intent`, C14 `memory_audit.classify` | A wrong answer costs a wasted turn or a noisy annotation, never a lost message. |
| `low` | 85% | C6 `agent_catchup.judge`, C9 `promise_gate.verdict` (regex floor beneath it), C15 `improvement_collect.promise_judge` (pre-screen only) | A wrong answer is caught by a later gate or costs one experiment. |

C16 `email_cs.triage` is `client_only`, lands on `ANTHROPIC`, and is never a candidate.

### Criteria

The record names the failing criteria by these keys, and the audit prints them per site.

| Criterion | Bar |
|-----------|-----|
| `agreement` | The candidate arm's agreement with the reference arm (the site's prompt verbatim on Haiku; gemma via OpenRouter for C15) at or above the tier bar. |
| `p95_c4` | p95 latency at concurrency 4 within the site's budget: 3.0 s for the three hot-path sites (C8, C9, C10), otherwise the reference arm's p95 at 4 plus one second. The local leg buys independence and cost, never speed. |
| `contended` | The record was measured with no `com.valor.*` service loaded (`launchctl list`), so no live bridge traffic shared the Ollama daemon during the latency run. |
| `error_rate` | Candidate error rate at or below 2%. |
| `n` | At least 50 inputs, 200 for the four routing sites (C1 to C4). |
| `n_real` | At least half of the minimum is real inbound `valor` messages drawn from the subconscious memory store, so a site cannot clear the bar on test fixtures alone. |
| `cost` | Against a reference arm on Anthropic that recorded a per-call cost, the candidate's `cost_per_call_usd` at or under one tenth of the reference's (`COST_RATIO = 0.1`). The criterion is skipped, never divided by zero, when the reference is granite or gemma or recorded no cost, and it applies only to a candidate on a different backend than the reference (`cost_bound_for`): the `anthropic` candidate a `LOCAL_ENCODER` landing measures as its restructured-shape fallback is priced like the reference by construction and is judged on agreement and latency, which is what lets the both-arms `--land` gate clear. It is the criterion a paid candidate (the decisions arm) faces that a free one never did. |

C12, C13, and C14 stay on granite by design and carry latency-only records: the runner measures no agreement for them, so `agreement` and `n_real` are outside their criteria, while `n`, `error_rate`, `contended`, and any site budget still apply. Their site rows declare `reference="ollama"`, so a comparison at those sites builds granite as the reference arm and judges it by `evaluate_reference` (the same latency-only criteria: `p95_c4` against the site budget when one exists, `contended`, `error_rate`, `n`).

A site that misses the bar after the builder's iteration lands with `backend=ANTHROPIC`, its record attached and naming the failing criterion. The same bar governs every backend; `LOCAL_ENCODER` and `DECISIONS` each add a rule below.

### A fitted head

A `LOCAL_ENCODER` landing is a head fit on the reference arm's labels, so the bar applies with three additions that keep the training data out of the claim ([Local Encoder Classifier](local-encoder-classifier.md#the-fit-protocol)):

- **Held-out split only.** `python -m tools.classification_eval --site <id> --fit --candidate local_encoder,anthropic` splits the draw by content digest before any label exists (`split_by_digest`: the site minimum, at least half real), labels the training split with the reference arm, fits the head, and runs `compare` on the held-out split alone. The criteria above (`agreement`, `p95_c4`, `contended`, `error_rate`, `n`, `n_real`) are read off that held-out record; the training-split agreement is a printed sanity line and never a claim.
- **The `fit` block.** The record carries `fit: {head_run_id, n_train, n_train_real, split: "digest", landed, miss_arms, miss_criteria}`. `landed` is `true` only for a run that was allowed to write the served head (`--land`) and did; a measure-only run on a landed site writes a record with `landed: false` and leaves the serving head alone.
- **Both arms.** `--land` requires the `anthropic` candidate (the restructured `system` + text shape a landed site's fallback serves) and the runner copies the staged head to `agent/llm/backends/heads/<site>.json` only when `evaluate_bar` is empty for both the `local_encoder` and the `anthropic` arm on the same held-out record; a miss on either arm deletes any served head and names the arm in `fit.miss_arms`. The runner holds the rule, so a fallback that disagrees with the verbatim prompt cannot land.

The audit's head-provenance rule: for a `LOCAL_ENCODER` site, `--audit` judges the latest record whose `fit.landed` is `true` (measure-only records are skipped), requires both its `local_encoder` and its `anthropic` arm to clear the bar, and requires the committed head's `run_id` to equal that record's `fit.head_run_id`. No head file, a mismatched `run_id`, or no landed record is a MISS. `tests/unit/test_classifier_heads.py` ties the committed head's `classes` to the site's output type in the same direction.

### The `DECISIONS` landing rule

A site declares `backend=Backend.DECISIONS` only on one record that proves both legs of its route at once: the `decisions` candidate arm clears every criterion above (`cost` included at a Haiku-backed site), and the ollama arm of that same record clears its criteria. The ollama arm is the `ollama` candidate, judged by `evaluate_bar`, where the reference is Haiku or gemma; it is the reference arm, judged by `evaluate_reference`, where the reference is granite (C12, C13, C14). No other record counts: the fallback proof is measured on the same inputs, in the same run, as the decisions arm, so it is never older than the decisions measurement and a granite regression after an old latency-only record cannot hide behind it.

A record therefore carries granite exactly once. `_candidate_arms` drops the `ollama` candidate, with a printed note, where the site's reference is granite; a second copy there would be a granite-against-granite self-comparison whose agreement means nothing, and a same-named reference would overwrite it in the audit's merged arm dict. The audit's `DECISIONS` branch selects its slot explicitly from the record's structure (`record["reference"]` when that arm's backend is `ollama`, else `record["candidates"]["ollama"]`) and prints both verdicts on one row: `decisions=PASS fallback=<arm> PASS (<slot> arm)`, or `MISS fallback <criteria>`, or `no ollama arm in the record`.

The runner invocation is `--candidate decisions,ollama` at a Haiku- or gemma-referenced site and `--candidate decisions` at C12 to C14. Route-side, every `DECISIONS` site's `valor` route carries `fallback.backend is Backend.OLLAMA`; a `Decision` marker's `min_confidence` lets a low-confidence Jev `choice` abstain to that fallback on the same inputs, so the site keeps one threshold that its record shows working for both arms.

## Comparison Records

Records are `ImprovementEvidence` rows of kind `classifier_comparison` on improvement case `1ec40086ca1d422e90ef747775ff7f64`, written by `python -m tools.classification_eval --site <id> --candidate <backend>[,<backend>]` (on a host that serves live traffic, through `scripts/classification-eval-uncontended.sh` with the same arguments; see the [infra doc](../infra/llm-task-routing.md#uncontended-runs)); each run also records its claims on a `probe` investigation of that case. They live in the Redis of the machine that ran the comparison, which is why the PR body carries the per-site landing summary the reviewer reads.

```bash
valor-improve case show 1ec40086ca1d422e90ef747775ff7f64
valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64
python -m tools.classification_eval --audit
python -m tools.classification_eval --list-sites
```

A record carries, per arm: agreement with the reference arm and its bootstrap CI, p50/p95 at concurrency 1 and 4, cost per call with the price's retrieval date, error rate; and per record: `n` with its `n_real`/`n_fixture` split, the site minimum, `budget_s`, `contended`, `latency_only`, and, for a fit run, the `fit` block above.

## Lane A Outcome

Lane A shipped the taxonomy, the router, the two legs, the comparison runner, and a record for every classification site. No site moved from Anthropic to granite in lane A. The build machine's memory store held 12 real `valor` human messages, so every inbound-shaped and outbound-shaped site (C1 to C10, C15) failed `n_real` (12 of 25, or of 100 for the routing sites) and landed on `ANTHROPIC` under the Risk 7 rule, with its record naming every failing criterion. C11 (transcript windows) and C14 (memory rows) have real inputs of their own shape and cleared `n_real`. The granite arms ran uncontended with granite warm and the daemon at `OLLAMA_NUM_PARALLEL=4` / `OLLAMA_KEEP_ALIVE=-1`, and concurrency 4 still serialized on the build machine's GPU: that is what the `p95_c4` misses measure. Agreement fell under the tier bar on C10, C6, C3, C4, C9, C5, C2, and C1 (in rising order of agreement: the two `high` sites C1 and C2 scored 0.93 and 0.905 against a 0.95 bar); C7, C8, and C11 cleared it, and C15's granite arm cleared it while its Haiku arm did not. C12, C13, and C14 stay on granite with latency-only records. The per-site numbers, with record and investigation ids:

| Id | Backend | Agreement (CI) | p95@4 / budget | n | n_real / min | Failing |
|----|---------|----------------|----------------|---|--------------|---------|
| C1 | anthropic | 0.930 [0.890, 0.965] | 1.304 s / 2.802 s | 200 | 12 / 100 | agreement, n_real |
| C2 | anthropic | 0.905 [0.860, 0.945] | 4.039 s / 2.250 s | 200 | 12 / 100 | agreement, p95_c4, n_real |
| C3 | anthropic | 0.675 [0.610, 0.740] | 1.789 s / 2.258 s | 200 | 12 / 100 | agreement, n_real |
| C4 | anthropic | 0.700 [0.635, 0.760] | 10.388 s / 3.960 s | 200 | 12 / 100 | agreement, p95_c4, n_real |
| C5 | anthropic | 0.865 [0.769, 0.942] | 6.496 s / 3.302 s | 52 | 12 / 25 | agreement, p95_c4, n_real |
| C6 | anthropic | 0.654 [0.519, 0.788] | 8.527 s / 2.286 s | 52 | 12 / 25 | agreement, p95_c4, n_real |
| C7 | anthropic | 0.981 [0.942, 1.000] | 5.165 s / 2.535 s | 52 | 12 / 25 | p95_c4, n_real |
| C8 | anthropic | 0.923 [0.846, 0.981] | 7.373 s / 3.000 s | 52 | 12 / 25 | p95_c4, n_real |
| C9 | anthropic | 0.846 [0.750, 0.942] | 3.098 s / 3.000 s | 52 | 12 / 25 | agreement, p95_c4, n_real |
| C10 | anthropic | 0.615 [0.481, 0.750] | 2.563 s / 3.000 s | 52 | 12 / 25 | agreement, n_real |
| C11 | anthropic | 0.900 [0.825, 0.963] | 7.821 s / 3.507 s | 80 | 40 / 25 | p95_c4 |
| C12 | ollama (latency-only) | n/a | 2.450 s / none | 52 | 12 / 25 | none |
| C13 | ollama (latency-only) | n/a | 7.390 s / none | 52 | 12 / 25 | none |
| C14 | ollama (latency-only) | n/a | 6.917 s / none | 70 | 30 / 25 | none |
| C15 | anthropic | ollama 0.885 [0.788, 0.962]; anthropic 0.827 [0.712, 0.923] | ollama 2.766 s / 3.253 s; anthropic 1.789 s | 52 | 12 / 25 | ollama: n_real; anthropic: agreement, n_real |
| C16 | anthropic (client_only) | never a candidate | | | | none required |

Every record has `contended: false` and a candidate error rate of 0.000. Agreement is against the reference arm (Haiku with the site's prompt verbatim; gemma-4-26b via OpenRouter for C15, on the paid route of the same weights because the free route was throttled upstream, 104 calls for 0.0025 USD settled under `promise_detector`). C15's reference is gemma, so both its arms are candidates and the Haiku number is the landed backend's; the audit accepts an `ANTHROPIC` landing whose record carries an Anthropic arm.

The records are the evidence the two follow-up lanes target. #3420 (lane B, below) adds the `LOCAL_ENCODER` backend behind the same router and fits a per-site head on each record's reference labels, landing per site by the same bar on a held-out split. #3421 (lane C, below) adds the `DECISIONS` backend (TypeSafe's Jev on its native structured-decision endpoint) with the Ollama leg as its fallback, landing per site by the [`DECISIONS` landing rule](#the-decisions-landing-rule). Both are one-word landings on the declaration once their record clears the bar; a site's `n_real` shortfall is cleared by running the comparison on a machine whose memory store holds enough real `valor` traffic. #3422 is the emoji reaction choice as a new classification site.

## Lane B Outcome

Lane B shipped `Backend.LOCAL_ENCODER`, the encoder leg, the runner's fit path (`--fit`, `--land`, `--preflight`, `--precheck`), the weights step, and the doctor row, and recorded the zero-shot GLiClass candidate as rejected on the case (investigation `4c0d6b44b9c942a5999637afbe22e8a9`: under the majority-class baseline on five of six measured sites). The lane's own numbers come in two tables.

The precheck is free and fixture-only: each site's lane A reference labels paired with its fixtures, embedded through the leg, scored five-fold through the same `fit_head` a landing fits with. A site under `bar - 0.10` skips the fit (`precheck_below_bar`); the rest are fit in this order, highest agreement relative to its bar first. On the build machine (the MacBook Air), about 3 s of wall clock on a quiet machine (8 s of CPU across the ONNX threads), zero spend:

| Id | Site | n | CV agreement | Majority | Bar | Gate | Mark |
|----|------|---|--------------|----------|-----|------|------|
| C9 | `promise_gate.verdict` | 40 | 0.925 | 0.575 | 0.85 | 0.75 | fit |
| C15 | `improvement_collect.promise_judge` | 40 | 0.850 | 0.825 | 0.85 | 0.75 | fit |
| C7 | `injection_inspection.risk` | 40 | 0.875 | 0.625 | 0.90 | 0.80 | fit |
| C8 | `context_recall.advised` | 40 | 0.850 | 0.800 | 0.90 | 0.80 | fit |
| C1 | `routing.needs_response` | 188 | 0.883 | 0.793 | 0.95 | 0.85 | fit |
| C11 | `health_check.judge` | 40 | 0.825 | 0.800 | 0.90 | 0.80 | fit |
| C2 | `routing.terminus` | 188 | 0.840 | 0.809 | 0.95 | 0.85 | precheck_below_bar |
| C4 | `intent_classifier.intent` | 188 | 0.654 | 0.335 | 0.95 | 0.85 | precheck_below_bar |
| C10 | `session_completion.novelty` | 40 | 0.600 | 0.675 | 0.90 | 0.80 | precheck_below_bar |
| C3 | `routing.work_request` | 188 | 0.564 | 0.346 | 0.95 | 0.85 | precheck_below_bar |
| C5 | `classifier.work_type` | 40 | 0.450 | 0.500 | 0.90 | 0.80 | precheck_below_bar |
| C6 | `agent_catchup.judge` | 40 | 0.325 | 0.450 | 0.85 | 0.75 | precheck_below_bar |

C13 and C14 are skipped (latency-only records, no reference labels); C12 is excluded by rule (its `job_id` answer is an open set); C16 is `client_only`. Every row embeds the bare message text: the encoder lane reads its own `encoder_text` row field, never lane A's `candidate_prompt`. C9, C2, and C6 were re-measured after that separation (C9 0.750 to 0.925, now first in the order; C2 0.809 to 0.840, above its majority baseline and still under the gate because the bare reply carries no thread context; C6 0.400 to 0.325, because the bare message carries none of the transcript the verdict depends on); the message-first `encoder_text` composition the landing step adds for context-bearing sites supplies that context. The precheck is re-run on the landing host before the landing loop, so the gate reads that host's records.

The landing numbers (both candidate arms' held-out agreement, `p95_c4`, `n`, `n_real`, `n_train`, `n_train_real`, head run id, record id, failing criterion if any, per site) come from the landing run tracked as [#3544](https://github.com/tomcounsell/ai/issues/3544) on the Valor host that owns the `valor` bridge, whose memory store holds the real inbound messages the `n_real` criterion demands; that table lands here and in [Local Encoder Classifier](local-encoder-classifier.md#landing-outcome) once the run has happened. Until then no site declares `Backend.LOCAL_ENCODER` and the site table above is unchanged.

## Lane C Outcome

Lane C shipped the mechanism: the decisions leg (`agent/llm/backends/decisions.py`, one `httpx` POST to `https://api.typesafe.ai/v1/systemone` on the pinned `jev-1.13.0`, questions built from the output type's `Literal` and `bool` fields, a one-cent spend envelope under purpose `structured_decision`), router rule 7, the `Decision` field marker, the `decisions_sdk_s` timer (3 s), `settings.api.typesafe_api_key`, the doctor's `decisions_endpoint` row, the live pinned-model probe test, and the runner's side (the `decisions` candidate arm with its per-run envelope, the `cost` criterion, `reference="ollama"` at C12 to C14 with `evaluate_reference`, `landing_record`, the audit's `DECISIONS` branch, and `scripts/classification-eval-uncontended.sh`). The transport underneath is in [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md#the-decisions-leg-backendsdecisionspy); the operator side (endpoint, key, pricing, greps, rollback lever 0) is in the [infra doc](../infra/llm-task-routing.md#decisions-endpoint).

Landings pending: no site declares `backend=Backend.DECISIONS`, and the site table above is the landed state. The comparison runs execute on the landing host, the Valor host that owns the `valor` bridge, through `scripts/classification-eval-uncontended.sh` (the build machine holds 12 real `valor` messages, so every inbound-shaped site would fail `n_real` there, exactly as in lane A). The host builder's preflight, `.venv/bin/python -c "from tools.classification_eval.arms import real_messages; print(len(real_messages(5000)))"`, sorts the fifteen eligible sites (C16 is `client_only`) into an iterated tier (sites whose `n_real` minimum the count meets, always including C11 and C14, about three runs each on the `Decision` markers and `candidate_prompt`) and a single-run tier (one run and a `Hold` commit naming `n_real`). Each run stops the three `com.valor.*` services for its own duration and restores them on every exit path; the wrapper refuses a thirteenth run per UTC day. A passing record lands the site with one word on its declaration plus its `Decision` markers, the doc row edited in the same commit (the parity test reads it), and the record id in the commit message; a miss holds the site where lane A landed it with the record naming the failing criteria.

The per-site table the host builder fills from the records. `Agreement` is the decisions arm against the reference; `p95@4` is at concurrency 4 for both arms of the same record; `cost/call` is the decisions arm's `input_tokens × 0.042 / 1e6` per input; `Fallback proof` names the ollama arm's slot in that record (`ollama` candidate, or `ollama` reference at C12 to C14); `Budget` is the site class read from its `run_typed` call (`hard 35 s` when the call passes no `sdk_timeout`, `sdk <n> s` when it does; the timer on a decisions route is the passed value in the second class and 3 s in the first). The `Budget` column is filled now from the call sites; every other cell waits on its record.

| Id | Site | Agreement (CI) | p95@4 decisions | p95@4 ollama | cost/call | Error rate | Failing | Record | Fallback proof | Budget |
|----|------|----------------|-----------------|--------------|-----------|------------|---------|--------|----------------|--------|
| C1 | `routing.needs_response` | pending | | | | | | | | hard 35 s |
| C2 | `routing.terminus` | pending | | | | | | | | hard 35 s |
| C3 | `routing.work_request` | pending | | | | | | | | hard 35 s |
| C4 | `intent_classifier.intent` | pending | | | | | | | | hard 35 s |
| C5 | `classifier.work_type` | pending | | | | | | | | hard 35 s |
| C6 | `agent_catchup.judge` | pending | | | | | | | | hard 35 s |
| C7 | `injection_inspection.risk` | pending | | | | | | | | sdk 6 s |
| C8 | `context_recall.advised` | pending | | | | | | | | sdk 3 s |
| C9 | `promise_gate.verdict` | pending | | | | | | | | sdk 3 s |
| C10 | `session_completion.novelty` | pending | | | | | | | | sdk 3 s |
| C11 | `health_check.judge` | pending | | | | | | | | hard 35 s |
| C12 | `job_router.route` | pending (reference granite) | | | | | | | `ollama` reference | hard 35 s |
| C13 | `classifier.intake_intent` | pending (reference granite) | | | | | | | `ollama` reference | hard 35 s |
| C14 | `memory_audit.classify` | pending (reference granite) | | | | | | | `ollama` reference | sdk 10 s |
| C15 | `improvement_collect.promise_judge` | pending (reference gemma) | | | | | | | | sdk 30 s |

Two more columns arrive after deploy for each landed hot-path site (C1 to C6): the `llm_route` count and the `llm_fallback ... primary=decisions` count over one host day, with the fallback share (`llm_fallback` over `llm_route`); a share over 2% is reported with its `reason=` split before the lane closes (the arithmetic is in the [infra doc](../infra/llm-task-routing.md#decisions-endpoint)).

The rejection criteria the host builder reads across the records: a decisions-arm non-200 rate over 2% in aggregate, agreement under 80% at every Haiku-backed site, or a C12 threshold that cannot be set without raising the wrong-bind rate over granite's. Meeting any of them records `Jev rejected as a backend` as a claim on the case with the numbers and lands no site; the leg stays, because a backend with zero declarations costs nothing and the next model on the endpoint is a slug change. No verdict is recorded.

Deploy: after merge, `./scripts/valor-service.sh restart` on the host (or fleet `/update`) picks up the leg; a machine that runs a `DECISIONS` site needs `TYPESAFE_API_KEY` in its vault `.env` (1Password: `op://m-valor/TypeSafe API/api_key`, through the service account), and without it every decisions call falls to granite while `python -m tools.doctor` and the env completeness check name the gap.

## Tooling

| Command | What it shows |
|---------|---------------|
| `python -m tools.doctor` (full run) | The "LLM routing" section: one row per declared site (kind, backend, tier, the route `resolve` returns for `valor` and for a client key, the declaring `path:line`), the per-process eligibility cache state, the `decisions_endpoint` row (fails when a declared `DECISIONS` site would fall back to granite on every call because `settings.api.typesafe_api_key` is `None`; no network call, the key never renders), the Ollama daemon row (model pulled and loaded, its `expires_at` as keep-alive evidence, `local_typed_hard_s`), and the `local_encoder` row (the `classification-local` extra importable, every pinned weights file present with its sha256, one head per declared `LOCAL_ENCODER` site that loads through the leg's validating loader). Each local row fails when a declared site on that backend would fall back on every call on this machine, naming the fix. |
| `python -m tools.classification_eval --audit` | Every classification site's declared backend, landing record id, and bar result (both verdicts on a `DECISIONS` row; head provenance on a `LOCAL_ENCODER` row); exit 1 on any miss. |
| `grep "llm_route site=" logs/bridge.log` | Which backend answered each call. The line fields and the greps are in the [infra doc](../infra/llm-task-routing.md). |

## Tests

`tests/unit/test_llm_task_taxonomy.py` is a pure AST walk (it imports only `agent.llm.tasks`) with six checks: every `run_typed(` call carries `task=`; the wrapper is the only entry point (no second, backend-specific one exists anywhere); every module with an LLM call token declares an `LLMTask` unless allowlisted; site ids are unique and every classification task is reached through `run_typed`; the site table above lists every declared site with the declaration's kind, backend, tier, and §7 class (this page's parity check); and the hotfix #1055 invariant holds by function body (no `asyncio.wait_for` inside `_evaluate_promise_async`, `read_the_room`, `_judge_completion_novelty`, `_gemma_classify`, or any function in `agent/llm/backends/`, and every `run_typed` call in those four bodies passes `hard_timeout=None`).

`tests/unit/test_llm_router.py` is the table-driven route test over every declaration, with rule 7's eligible route (Jev with an Ollama fallback) and its ineligible route (Anthropic, no fallback); `tests/unit/test_llm_router_eligibility.py` feeds a client-mapped message through every local-backend site (`OLLAMA`, `LOCAL_ENCODER`, and `DECISIONS`) and asserts the Anthropic leg, feeds a `valor` message through the same sites with `gh` unavailable and asserts the leg the declaration names with its fallback, and pins `email_cs.triage` to Anthropic for every key; `tests/unit/test_worker_startup_warm_cache.py` pins the held warm-up task; `tests/unit/test_classification_eval.py` covers the runner's math, the minimum-n refusal, the bar (`cost` included), `evaluate_reference`, the candidate drop at a granite-referenced site, contention, the fit path (the digest split, the both-arms landing gate, served-head protection, the precheck gate, preflight), the audit's `DECISIONS` branch and its slot selection on a name collision, and the audit exit codes including head provenance; `tests/unit/test_classifier_heads.py` checks every committed head against its site.

Lane C's own files: `tests/unit/test_llm_backend_decisions.py` (the leg's success path with a recorded response, the nine failure classes plus the missing key with their `reason` and one ERROR line free of the key, `questions_for` over every classification output type, `decode_answers` edge cases, and the envelope's settle-once invariants); `tests/unit/test_classification_eval_uncontended.py` (the uncontended-run wrapper against fake executables: the restore on exit 0, exit 2, and `SIGINT`, the `Connected to Telegram` check, the daily bound); and the live probe `tests/unit/test_models.py::test_typesafe_jev_pinned_model_answers` (`integration` marker, one authenticated `noul` call asserting the response `model` equals the pinned `JEV`, the answer shape, and `usage.input_tokens`; fail-closed on a network error; skips only by naming a missing `TYPESAFE_API_KEY`).

## See Also

- [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md): `run_typed`, the leg protocol, the fallback budget, every migrated call site.
- [Local Encoder Classifier](local-encoder-classifier.md): the `LOCAL_ENCODER` leg, the head file, the fit protocol, the precheck gate.
- [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md): Ollama service settings, the encoder weights, the decisions endpoint and its key, log lines, reference-arm spend, uncontended runs, rollback levers.
- [Local Ollama Model Policy](local-model-policy.md): which local models run on each machine.
- [Config Timeout Catalog](config-timeout-catalog.md): `local_typed_hard_s`, `decisions_sdk_s`, and the Anthropic pair.
- [Improvement Research Cycle](improvement-research-cycle.md): the case, the evidence model, and the promise judge.
- `docs/improvement-charter.md` §7: the eligibility line the router enforces.
- `docs/archive/plans-completed/llm-task-taxonomy-routing-layer.md`: lane A's plan, its critique rounds, and the follow-up lanes; `docs/plans/structured-decision-transport-jev-behind-ollama-fallback.md`: lane C's plan.
