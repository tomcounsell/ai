---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-21
tracking: https://github.com/tomcounsell/ai/issues/3420
last_comment_id: 5755701599
revision_applied: true
revision_applied_at: 2026-09-21T05:33:57Z
---

# Local Encoder Classification Backend Behind Lane A's Router (Lane B of #3410)

## Problem

Lane A (#3410, PR #3524, merged at `4703bce23`) shipped the taxonomy, the router, the Anthropic and Ollama legs, the comparison runner, and a `classifier_comparison` record for every classification site. No candidate site moved to granite. Every inbound-shaped and outbound-shaped site (C1 to C10, C15) landed on `ANTHROPIC` with a record naming its failing criteria, and the records are precise about why: the build machine's memory store held 12 real `valor` messages against minimums of 25 and 100 (`n_real`); concurrency 4 serialized on that machine's GPU (`p95_c4`); and granite's agreement with Haiku fell under the tier bar on C1 to C6, C9, and C10. Tom's principles for this work (issue #3410 comment 5737918683) stand: local-first, no single external provider on a hot path, no transition phase, no default-off switches, the builder flips and revises until the PR is approved, and the comparison record against the site's current backend is the only argument for a landing (charter §6; low price is not evidence, §7).

This issue proposed a second local CPU backend, zero-shot GLiClass through int8 ONNX, on the premise that "a zero-shot encoder classifier can beat a 3B generative model on closed-set labels while staying on CPU". That premise was measurable at plan time and it is false for these sites. The pinned model (`knowledgator/gliclass-base-v1.0`, the only GLiClass with a published ONNX export) runs on CPU exactly as the issue describes: 0.45 s to load, 10 ms per call, 31 ms p95 at concurrency 4 on this MacBook Air, `onnxruntime` and `tokenizers` only. Scored against Haiku's own labels on lane A's committed fixtures (188 inputs for each routing site, 40 for the 50-minimum sites, reference labels read from the lane A records in this machine's Redis), its agreement is 0.70 on C1, 0.47 on C2, 0.21 on C3, 0.19 on C4, 0.20 on C5, and 0.60 on C9, under the majority-class baseline on every site but C9 and 15 to 75 points under the tier bars, across three label phrasings each (Spike Results). Label wording moves the numbers by a few points; nothing in a build day closes a gap of that size. The issue's rejection exit fires before the build.

The same machinery, one step over, does carry the local-first outcome. The reference arm's per-input labels are a labeled dataset for every site, and a linear head on a small local embedding model learns them: five-fold cross-validation on the same fixtures gives 0.894 on C1 (bar 0.95) and 0.825 on C9 (bar 0.85) from 150 and 32 training examples, with 1.4 ms per call and 3.1 ms p95 at concurrency 4 (`Xenova/bge-small-en-v1.5` int8, 34 MB, `onnxruntime` and `tokenizers` only). That is a supervised local classifier distilled from the site's reference backend, measured on a held-out split by the same bar, landed per site by the same one-word edit. It needs no `torch`, no daemon, no GPU, and its latency does not depend on the machine's graphics hardware, which is what the issue was reaching for with GLiClass. This plan builds that backend and keeps the GLiClass rejection as a recorded result on the case. The change of model was put to Tom in Open Questions and accepted on 2026-09-21 (issue comment 5755701599, quoted verbatim there).

**Current behavior:**

Verified on `main` at `149f0d0da`:

- `agent/llm/tasks.py::Backend` has two members, `ANTHROPIC` and `OLLAMA` (`:71-75`); `agent/llm/router.py::resolve` (`:52-70`) has four rules and raises `ValueError` for any other backend; `agent/llm/wrapper.py::_LEGS` (`:95-98`) maps the two legs; `agent/llm/backends/__init__.py::default_sdk_timeout` (`:51-64`) knows the two timers.
- `tools/classification_eval/__main__.py::CANDIDATE_BACKENDS` (`:47`) is derived from the enum, and `_candidate_arms` (`:115-122`) has builders for `ollama` and `anthropic` only; `tools/classification_eval/records.py::_audit_row` (`:152-156`) applies the landed-arm rule to `OLLAMA` only.
- Every classification site except C12, C13, and C14 declares `backend=ANTHROPIC`; the records in `docs/features/llm-task-taxonomy.md` ("Lane A Outcome") carry `agreement`, `p95_c4`, and `n_real` misses per site.
- The runner has no way to fit anything: it measures arms that exist. The reference arm's per-input labels are already kept in every record (`ArmResult.labels`, `tools/classification_eval/core.py:159-161`), in input order, which is what makes the plan-time measurement above possible with no spend.
- `logs/classification_audit.jsonl` (573 promise-gate rows) is not a real-input corpus: 66 unique texts, every one a 200-character `text_preview`, most of them test traffic (`cli-*` sessions). It cannot satisfy `n_real` for any site.
- `/update` runs `uv sync --all-extras` (`scripts/update/deps.py:136`), so a new optional extra installs on every fleet machine at the next update; the lane A plan's note that extras are skipped by default was wrong for this repo.

**Desired outcome:**

1. `Backend.LOCAL_ENCODER` joins the enum with a leg at `agent/llm/backends/local_encoder.py` that meets lane A's protocol exactly (`call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack) -> BaseModel`): it embeds `prompt` with a pinned local ONNX model, applies the per-site linear head named by `route.model` (the site id), and returns a validated instance of `output_type`, or raises `LLMCallError` so the router's Anthropic fallback runs. `resolve` gains one rule ahead of the Ollama rules. Weights are fetched by `/update` with checksums, never by the leg. `python -m tools.doctor` reports the extra, the weights, and the heads.
2. The comparison runner gains the arm (`--candidate local_encoder`), a fit mode (`--site <id> --fit`) that draws inputs, splits them by digest, labels the training split with the reference arm, fits the head, and measures the candidate on the held-out split only, writing the record with its `fit` provenance; a `--preflight` that says whether this machine's memory store can satisfy `n_real`; and an audit rule that a `LOCAL_ENCODER` landing's committed head is the head its record measured.
3. Per-site landing by the plan's bar (`high` 95%, `medium` 90%, `low` 85% agreement on the held-out split; p95 at concurrency 4 within budget; error rate at or under 2%; `n` and `n_real` at or above the minimums; `contended: false`): the builder sets `backend=LOCAL_ENCODER`, commits the head beside the leg, and commits per site with the record id; a site that misses stays where lane A landed it. Sites are tried in the order the plan-time numbers rank them, C1, C9, then C15, C8, C10, C7, C14, C11, C13, C6, after a free precheck skips any site more than 0.10 under its bar (on today's numbers C2, C3, C4, and C5); C12 is excluded (its `job_id` answer is not a closed set) and C16 is `client_only`.
4. The GLiClass zero-shot candidate is recorded as rejected on case `1ec40086ca1d422e90ef747775ff7f64` with the plan-time numbers, and this document is the fixture result the issue's acceptance criterion 2 asks for.

## Freshness Check

**Baseline commit:** `149f0d0da` (HEAD of `main` at plan time; lane A merged at `4703bce23`)
**Issue filed at:** 2026-09-18T13:07:59Z; body rewritten twice during lane A's critique; the upstream-change notice (comment 5745557381, posted after PR #3524 reached REVIEW) is the authoritative description of the landed interfaces and this plan is written against the merged code, not the issue text. Tom's decision on the model change is comment 5755701599 (2026-09-21T05:11:51Z), the latest comment on the issue and the one `last_comment_id` names.
**Disposition:** Major drift on the premise, minor drift on the interfaces, overlap with #3421's plan in progress. The interface drift is folded in below; the premise drift (zero-shot GLiClass measured under the bar) is the reason this plan changes the model. It was put to Tom in Open Questions and accepted (comment 5755701599) before this revision.

**File:line references re-verified** (every claim in the issue body and the notice, read on the baseline):

- `agent/llm/backends/__init__.py:5-8`: leg protocol `call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack)`. Holds; the issue's outcome 1 omits `deadline` and `stack`, corrected in Solution.
- `agent/llm/backends/__init__.py:93-109`: `bound_to_deadline(sdk_timeout, deadline, now, *, leg)`. Holds; the encoder leg calls it first.
- `agent/llm/backends/ollama.py:52-96`: the local leg shape (deadline re-check, no `asyncio.wait_for`, third-party symbols from `stack`). Holds; the encoder leg mirrors the structure and takes no symbol from `stack`, since none of its dependencies live there.
- `agent/llm/router.py:31-32` and `:64-70`: the reserved rule slot ("#3420 adds `LOCAL_ZERO_SHOT` the same way") and the `ValueError` for an unrouted backend. Holds; the member is named `LOCAL_ENCODER` here, and the docstring line is rewritten in Task 1.
- `agent/llm/wrapper.py:95-98` (`_LEGS`), `:223` (`signature_axis=(route.backend is Backend.ANTHROPIC)`), `:237` (`default_sdk_timeout(route.backend)`), `:242-305` (fallback budget and the two log lines). Hold; the encoder route takes the non-Anthropic axis and the Ollama-shaped budget with no wrapper change beyond the `_LEGS` entry.
- `agent/llm/tasks.py:51-52`: "Lane B (#3420) and lane C (#3421) append their own `Backend` members and their own keyword fields with defaults". Holds; this plan adds a member and no field.
- `tools/classification_eval/__main__.py:47` (`CANDIDATE_BACKENDS = tuple(b.value for b in Backend)`) and `:115-122` (`_candidate_arms` builders). Holds; the notice's "`--candidate local_zero_shot` is rejected as unknown candidate backend" is true only until the enum member exists, after which the vocabulary is automatic and the builder dict is the one addition.
- `tools/classification_eval/arms.py:108-137` (`ollama_arm`, the direct-leg pattern). Holds; `local_encoder_arm` follows it.
- `tools/classification_eval/core.py:97-135` (`Site` with `candidate_prompt`, `candidate_system`, `candidate_output_type`, `real_inputs`), `:289-315` (`_run_arm`: agreement pass at 1, latency pass at 4), `:411-437` (`evaluate_bar`), `:504-520` (`is_contended`). Hold; `Site` gains no field, the fit mode reuses `candidate_prompt` as the text shape and `compare` unchanged on the held-out split.
- `tools/classification_eval/records.py:126-159` (`_audit_row`: `if backend == Backend.OLLAMA.value`). Holds; generalized to every non-Anthropic backend plus the head-provenance rule.
- `tools/classification_eval/sites.py:109-117` (minimums 50 / 200, `FIXTURES_PER_SITE = 40`, `ROUTING_FIXTURES = 188`) and `:283-336` in `arms.py` (`real_messages` from the memory store, digest-ordered). Hold; the digest order is what makes the fit split deterministic.
- `tools/improvement_eligibility.py::is_eligible`: `valor` pinned `True`, cache-only for other keys. Holds (`docs/features/llm-task-taxonomy.md:113-121`).
- `tests/unit/test_llm_task_taxonomy.py` check 5 (doc table parity) and check 6 (no `asyncio.wait_for` in any function under `agent/llm/backends/`). Hold; Test Impact lists what each existing test needs.
- `pyproject.toml:62-75`: `onnxruntime>=1.25.0` already floors the `knowledge` and `tts` extras; `onnxruntime 1.25.0` is installed in this venv, `tokenizers` is not. Holds.
- `scripts/update/kokoro.py` and `scripts/download_kokoro_models.py`: the weights-at-update pattern (`ensure_models` subprocess, `~/.cache/<name>/`, env override, idempotent). Holds; the encoder weights follow it.
- `docs/features/llm-task-taxonomy.md:171-196` ("Lane A Outcome", the per-site table). Holds and is the target list.

**Cited sibling issues/PRs re-checked:**
- #3410: closed 2026-09-20 by PR #3524 (merged at `4703bce23`); its plan is archived at `docs/archive/plans-completed/llm-task-taxonomy-routing-layer.md` (five critique rounds; the caller-census discipline and the hotfix #1055 rules from its Critique Results apply here).
- #3421 (lane C, the decisions transport): open; its plan `docs/plans/structured-decision-transport-jev-behind-ollama-fallback.md` is being written in this same checkout right now (committed at `56674a2d7`, `88cd285bc`). Both lanes add a `Backend` member, a router rule, a `_LEGS` entry, a `default_sdk_timeout` branch, a runner arm builder, doc table rows, and an audit generalization. The audit generalization (landed-arm rule for every non-Anthropic backend) is written once here in a form lane C can reuse; the rest are adjacent one-line additions that rebase cleanly in either order. See Risk 6.
- #3422 (emoji reaction as a decision site): open; untouched here (anti-criterion in Verification).
- #3525 (`agent/__init__.py` eager import chain): open; the encoder leg keeps module scope stdlib-only and the download script reads its constants from `config/models.py`, so this lane adds nothing to that chain.
- #3177 (RSI controller; the case substrate): open; records and claims attach to case `1ec40086ca1d422e90ef747775ff7f64` exactly as lane A's did.

**Commits on main since issue was filed (touching referenced files):** `4703bce23` (lane A itself, the prerequisite), `7d642f3df` (pydantic-ai-slim 2.46.0), `e61b4f0b0` (claude-agent-sdk), `cb03e33ad` (anthropic 1.7.0): the three bumps are dependency pins the encoder leg does not touch.

**Active plans in `docs/plans/` overlapping this area:** `structured-decision-transport-jev-behind-ollama-fallback.md` (#3421, above: coordinate, not conflict); `recursive-self-improvement.md` (#3177) owns the case substrate this plan writes records to; `durability-room-job-agentrun.md` (#2494) owns C12, which this plan excludes.

**Notes:** The issue's own rejection condition ("if the ONNX path cannot reproduce the reference pipeline's per-label scores on a 20-item fixture") is not the one that fired: the ONNX path works (Spike 1). The bar is what GLiClass misses, and it misses it at plan time on the same fixtures lane A's records were measured on, which is stronger evidence than a build-day iteration would have produced. The RSI lineage rows named in the brief (case `1ec40086`, rows `996af74c` local-first, `c4e1199d` lane A comparison outcome, `9984d338` merge and claim level) are the rows this lane's rejection claim and landing claims sit beside, together with the two Tom named when accepting the pivot: model revision 8 (`1f161425`) and the intake row on case `1ec40086`.

## Prior Art

- **#3410 / PR #3524** (merged 2026-09-20): lane A. The taxonomy, router, two legs, runner, records. Its Research finding 3 and spike-2 already flagged GLiClass zero-shot as "feasible in principle, unmeasured" and gave this lane its rejection exit; its Risk 7 (inputs too few or too clean) and the `n_real` criterion are the constraints this plan's fit protocol is built around. Its Critique Results (five rounds) established the rules this plan inherits: every caller census by grep with the count pasted, no `asyncio.wait_for` around an LLM call (#1055), one shared fake per leg, a mutation check per fail-safe.
- **#2494 Task 13 / PR #2632** (merged 2026-08-07): put C12 and C13 on granite with no measurement. Lane A's latency-only records now cover them; C13 is a candidate here, C12 is not.
- **#1923 / #1925 (PR #2045)**: the wrapper and the first Haiku migration. The wrapper contract this leg plugs into.
- **#1636**: the classifier/generation model split at the constant level; the seed of the taxonomy.
- **#3338 / PR #3381**: the live-listing probe pattern for a pinned external model; the encoder weights are pinned by revision and checksum instead, since they are fetched once and verified locally.
- **`tools/tts/` + `scripts/download_kokoro_models.py` + `scripts/update/kokoro.py`**: the one existing pattern for ONNX weights on every machine (cache dir, env override, idempotent fetch at `/update`, doctor row). Reused as is.
- **`tools/emoji_embedding.py`**: an OpenRouter embedding call over `data/emoji_embeddings.json`; the only prior "embed and pick a label" path in the repo, external and outside the taxonomy (#3422). Not touched.
- No closed issue or merged PR has attempted a supervised local classifier; `gh issue list --state closed --search "GLiClass OR zero-shot OR classifier local"` returns only #3410 and the granite history above.

## Research

**Queries used:**
- GLiClass ONNX export, pre-exported weights, input packing and post-processing (Knowledgator GLiClass, GLiClass.c, `convert_to_onnx.py`, `test_onnx.py`)
- Hugging Face Hub API for `knowledgator/gliclass-*` (files, sizes, LFS sha256, license, revision)
- `Xenova/bge-small-en-v1.5` ONNX exports and `BAAI/bge-small-en-v1.5` license
- PyPI wheel availability of `tokenizers` and `onnxruntime` for Python 3.14 on arm64 macOS

**Key findings** (all retrieved 2026-09-21):

1. **GLiClass models on the Hub.** `knowledgator/gliclass-base-v1.0` (revision `aa3ac24a77860a033c9e9a3aa90d5872722cb7bf`, Apache-2.0, encoder `microsoft/deberta-v3-base`, `max_num_classes: 25`, `prompt_first: false`) is the only GLiClass with a published ONNX export: `onnx/model.onnx` 745,604,075 B (sha256 `b6e550c912dbb06a42227366ca9d5bce0a960c36f5f8575eb8cda9f95d507bfa`) and `onnx/model-int8-quantized.onnx` 245,961,591 B (sha256 `8eb7db6e3c82746b5bc917305b2cf6dc2b020c1223205ed8638f26dd8ee428e1`), `tokenizer.json` 8,649,234 B (sha256 `519648948c4c59da1af88f2cf2c8b4f84417b5c673981bc9809abf84cda1b7cc`). `gliclass-small-v1.0` has the same layout (int8 174 MB). `gliclass-edge-v3.0` and `gliclass-modern-base-v2.0` ship safetensors only. Source: `https://huggingface.co/api/models/knowledgator/gliclass-base-v1.0?blobs=true`. *Informs:* base-v1.0 int8 was the candidate measured in Spike 1 and 2.
2. **GLiClass packing and scoring** (https://github.com/Knowledgator/GLiClass/blob/main/gliclass/pipeline.py, `UniEncoderZeroShotClassificationPipeline.prepare_input`; https://github.com/Knowledgator/GLiClass.c/blob/main/src/preprocessor.c and `postprocessor.c`): input is `text + "<<LABEL>>label1<<LABEL>>label2..." + "<<SEP>>"` (labels first when `prompt_first`), tokenized with the model's `tokenizer.json` (`<<LABEL>>` = 128001, `<<SEP>>` = 128002 as added tokens), fed as `input_ids` and `attention_mask` (int64), output `logits` of shape `[batch, num_labels]`; single-label is a softmax over the label logits, multi-label a sigmoid per label. The ONNX export's `onnx/config.json` carries `original_logits` for a batch that the published test script does not reproduce (4×6 logits against a one-text, four-label example), so the reproduction check in Spike 1 used the C and Python pipelines' packing directly. *Informs:* the pipeline is small enough to write in twenty lines; it was, and it runs.
3. **Embedding model for the supervised head.** `Xenova/bge-small-en-v1.5` (revision `ea104dacec62c0de699686887e3f920caeb4f3e3`; the ONNX export of `BAAI/bge-small-en-v1.5`, MIT, 64M downloads): `onnx/model_int8.onnx` 33,760,831 B (sha256 `bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197`), `tokenizer.json` 711,396 B (sha256 `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66`); inputs `input_ids`, `attention_mask`, `token_type_ids`; output `last_hidden_state` `[batch, seq, 384]`, CLS pooling then L2-normalize (the BGE convention). Sources: `https://huggingface.co/api/models/Xenova/bge-small-en-v1.5?blobs=true`, `https://huggingface.co/BAAI/bge-small-en-v1.5`. *Informs:* the pinned model, revision, and checksums in `config/models.py`; `bge-base-en-v1.5` int8 (109 MB, same repo family) is the one permitted swap if the small model plateaus on a site (Solution).
4. **Wheels for the pinned interpreter** (Python 3.14, arm64 macOS): `tokenizers 0.23.2` ships `cp310-abi3` wheels (stable ABI, so 3.14 is covered; `requires_python >=3.10`); `onnxruntime 1.30.0` ships `cp314-cp314-macosx_14_0_arm64`. Both installed cleanly into a scratch venv for the spikes. Source: `https://pypi.org/pypi/tokenizers/json`, `https://pypi.org/pypi/onnxruntime/json`. *Informs:* the `classification-local` extra is `onnxruntime>=1.25.0`, `tokenizers>=0.21`, `numpy` (already a base dependency).
5. **The issue's replay-corpus option is empty.** `logs/classification_audit.jsonl`: 573 rows, all `kind=promise_gate`, 66 unique `text_preview` values, every preview truncated at 200 characters, sources `promise_gate_drafter` 278 / `terminal_flush` 197 / `promise_gate_llm` 78 / `promise_gate_heuristic` 20, most sessions `cli-*` test traffic. *Informs:* `n_real` is satisfied only from a memory store that holds real inbound `valor` traffic; the landing run has to happen on the host that owns the `valor` bridge (Solution, Prerequisites).

`tools.memory_search save` was not attempted for these findings; the plan is the capture point, and finding 1's checksums are carried into `config/models.py` by Task 1.

## Spike Results

All three spikes ran on this MacBook Air (Apple M4, 10 cores, 16 GB) in a scratch venv (`uv venv -p 3.14`; `onnxruntime 1.30.0`, `tokenizers 0.23.2`, `numpy 2.5.3`), never in the repo venv. The fixture set for spikes 2 and 3 is lane A's own: for each site, `site_for(id).fixtures()` (deterministic) paired with the reference arm's per-input labels read from the site's latest `classifier_comparison` record in this machine's Redis (`latest_record(site)["reference"]["labels"][:n_fixture]`; records `06b20b10…` C1, `4004915d…` C2, `fa8ededd…` C3, `0c00bc4f…` C4, `c2c3564f…` C5, `77b5182f…` C9). Only the fixture prefix of each record was used because the 12 real messages are ordered by content digest and the store may have changed since the run; the fixture count matched each record's `n_fixture` exactly (188, 188, 188, 188, 40, 40).

### spike-1: Does GLiClass int8 ONNX run from Python with `onnxruntime` and `tokenizers` only?
- **Assumption**: "The pre-exported int8 model, the model's `tokenizer.json`, the C pipeline's packing, and a softmax are enough; no `torch`, no `gliclass`."
- **Method**: prototype (scratch venv; 246 MB download verified against the LFS sha256 above)
- **Finding**: Yes. `InferenceSession` load 0.45 s; inputs `input_ids`/`attention_mask` int64, output `logits`; the readme example (`"ONNX is an open-source format..."`, labels `format, model, tool, cat`) ranks `format` first (logits 9.54, 8.19, 8.67, 7.94). Per call 10 ms at concurrency 1 (p50 = p95), 25 ms p50 / 31 ms p95 at concurrency 4 with `intra_op_num_threads=4`; a 572-token input runs in 223 ms (DeBERTa's relative positions accept inputs past the 512 nominal window). The tokenizer emits `[CLS] … <<LABEL>> ▁model … <<SEP>> [SEP]` as the pipeline expects.
- **Confidence**: high
- **Impact on plan**: the issue's stated rejection condition does not fire; latency is a non-issue for any CPU encoder on this class of machine, which removes `p95_c4` as a constraint for lane B on every site.

### spike-2: Does zero-shot GLiClass agree with Haiku on the sites granite missed?
- **Assumption**: "A zero-shot encoder classifier can beat a 3B generative model on closed-set labels" (the issue's premise).
- **Method**: prototype; single-label softmax; three label phrasings per site (descriptive sentences, the site's instruction wording, one-word labels); for C2 the thread context was prepended to the message.
- **Finding**: No, and not closely. Agreement with the Haiku reference labels on lane A's fixtures, best phrasing of the three, with the majority-class baseline and the tier bar beside it:

  | Site | n | Agreement (best of 3) | Range across phrasings | Majority baseline | Bar |
  |------|---|-----------------------|------------------------|-------------------|-----|
  | C1 `routing.needs_response` | 188 | 0.702 | 0.649 to 0.702 | 0.793 | 0.95 |
  | C2 `routing.terminus` | 188 | 0.473 | 0.468 to 0.473 | 0.809 | 0.95 |
  | C3 `routing.work_request` | 188 | 0.213 | 0.170 to 0.213 | 0.346 | 0.95 |
  | C4 `intent_classifier.intent` | 188 | 0.186 | one phrasing | 0.335 | 0.95 |
  | C5 `classifier.work_type` | 40 | 0.200 | one phrasing | 0.500 | 0.90 |
  | C9 `promise_gate.verdict` | 40 | 0.600 | 0.575 to 0.600 | 0.575 | 0.85 |

  On a ten-message probe the softmax distributions sit near uniform (0.25 to 0.45 on the winning label of four), so this is not a calibration or wording problem: the model does not separate speech acts (request, acknowledgment, question, status line), which is what every candidate site asks for. GLiClass v1.0 was trained on topic-shaped labels.
- **Confidence**: high (188-input fixtures on four sites, three phrasings each, the same reference labels lane A's records were measured against)
- **Impact on plan**: the zero-shot candidate is rejected at plan time; Task 0 records the rejection on the case with these numbers. No GLiClass code ships. The `Backend` member, leg, extra, weights, and doctor row in this plan belong to the supervised encoder below.

### spike-3: Can a linear head on a local embedding model learn the reference arm's labels?
- **Assumption**: "Frozen sentence embeddings from a small local ONNX model plus a per-site multinomial logistic regression, fit on Haiku's labels, reach the bar on at least the lower tiers with a few hundred examples."
- **Method**: prototype; `Xenova/bge-small-en-v1.5` int8, CLS pooling, L2-normalized 384-d vectors; five-fold cross-validation (seed 0) of a numpy multinomial logistic regression (300 full-batch epochs, lr 0.5, L2 1e-3) and of 5-nearest-neighbor voting, on the same fixtures and reference labels as spike-2.
- **Finding**:

  | Site | n (train per fold) | LR 5-fold agreement | kNN-5 | Majority baseline | Bar |
  |------|--------------------|---------------------|-------|-------------------|-----|
  | C1 | 188 (150) | 0.894 | 0.872 | 0.793 | 0.95 |
  | C2 | 188 (150) | 0.824 | 0.867 | 0.809 | 0.95 |
  | C3 | 188 (150) | 0.574 | 0.463 | 0.346 | 0.95 |
  | C4 | 188 (150) | 0.638 | 0.617 | 0.335 | 0.95 |
  | C5 | 40 (32) | 0.450 | 0.425 | 0.500 | 0.90 |
  | C9 | 40 (32) | 0.825 | 0.825 | 0.575 | 0.85 |

  Embedding 832 texts took 3.2 s. Load 0.05 s; 1.2 ms p50 / 1.4 ms p95 per call at concurrency 1, 2.4 ms p50 / 3.1 ms p95 at concurrency 4; a 512-token input embeds in 51 ms.
- **Confidence**: medium for the ranking (C1 and C9 are within 6 and 3 points of their bars from 150 and 32 training examples on fixtures alone; C3 to C5 are far); low for the absolute numbers on real traffic, which is what the held-out record measures.
- **Impact on plan**: this is the backend the plan builds. The learning curve is the open variable: a Valor host's memory store supplies the training and held-out real messages, and the record on the held-out split (with the bar's `n_real` rule) is the only claim. This table measured six sites (C1 to C5, C9), so it fixes only their relative order; the positions of the other eight sites in the landing order are provisional, and Task 5's precheck table (the same procedure over every site with a lane A record) fixes the per-site order before Task 8 starts. If a site plateaus under its bar with the small model, the builder may swap to `bge-base-en-v1.5` int8 (Research finding 3) once, on the record; anything past that (fine-tuning the encoder, more labels) is out of scope (Rabbit Holes).

The scoring routine the spikes share, so a critic can reproduce them from the scratch-venv recipe above (paths are the downloaded files; `fixtures_ref.json` is the site → `[{text, context, ref}]` export described at the top of this section):

```python
enc = tok.encode(text)                       # tokenizers.Tokenizer.from_file("tokenizer.json"); truncation 512
feed = {"input_ids": np.array([enc.ids], np.int64), "attention_mask": np.array([enc.attention_mask], np.int64)}
feed["token_type_ids"] = np.zeros_like(feed["input_ids"])          # bge only
hidden = session.run(None, feed)[0][0]      # onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
vector = hidden[0] / np.linalg.norm(hidden[0])                     # CLS pooling, L2 norm
# GLiClass instead: encode(text + "".join(f"<<LABEL>>{l}" for l in labels) + "<<SEP>>"); logits = run(...)[0][0][:len(labels)]; softmax
```

## Data Flow

**Serving path** (C1 shown after landing; every landed site has the same frame):

1. **Entry point**: `bridge/telegram_bridge.py` resolves the project and calls `should_respond_async(..., project, ...)`, which passes `project["_key"]` into `classify_needs_response(text, project_key=...)` (lane A, unchanged).
2. **Call site**: `await run_typed(text, NeedsResponseDecision, task=NEEDS_RESPONSE, project_key=project_key, system=NEEDS_RESPONSE_INSTRUCTIONS)`. Landing restructures the call: `prompt` is the text under classification and the instruction block moves to `system`. The encoder leg embeds `prompt` and ignores `system`; the Anthropic fallback receives both, which is the same information the verbatim prompt carried, in the `system` + `user` shape Haiku is measured on as the record's second candidate arm. For a context-bearing site the text is composed message-first (`f"{message}\n\n{context}"`) by one function beside the declaration that the runner's `candidate_prompt` also calls, so the 512-token truncation drops old context before it drops the message and the served text is byte-identical to the measured text.
3. **Router**: `resolve(task, project_key)`; the new rule sits ahead of the Ollama rules: `task.backend is LOCAL_ENCODER and is_eligible(project_key)` → `Route(LOCAL_ENCODER, task.site, fallback=Route(ANTHROPIC, model))`; ineligible → `Route(ANTHROPIC, model)`. `route.model` is the site id, which is the head's name: this backend's "model" is the per-site head.
4. **Wrapper**: `_guard_stack(signature_axis=False)` (a non-Anthropic route, as for Ollama), `_load_stack()` (memoized; the leg takes nothing from it), `default_sdk_timeout(LOCAL_ENCODER)` = `settings.timeouts.local_typed_hard_s`, then `_LEGS[LOCAL_ENCODER](prompt, output_type, route, system=..., sdk_timeout=..., slot_timeout=..., max_retries=..., deadline=None, stack=stack)`.
5. **Encoder leg** (`agent/llm/backends/local_encoder.py`): `bound_to_deadline` first (fallback calls only); load the head `agent/llm/backends/heads/<route.model>.json` (memoized per site; missing → `LLMCallError(reason="transport")` naming the file); check the output type's shape against the head's classes (one closed-set field, `confidence` optional, every other field defaulted; mismatch → `LLMCallError(reason="validation")`); `await asyncio.to_thread(_classify, text, head)`, where `_classify` runs the memoized `onnxruntime.InferenceSession` and `tokenizers.Tokenizer` (loaded once per process under a lock from `LOCAL_ENCODER_MODELS_DIR`, checksums verified at load; a missing extra raises `LLMCallError("classification-local extra not installed", reason="transport")`, missing or mismatched weights raise `LLMCallError(reason="transport")` naming `scripts/download_local_encoder_models.py`); softmax over the head's logits; return `output_type(**{field: value, "confidence": score})` (bool fields map `"True"`/`"False"` back to `bool`). No `asyncio.wait_for`, no request timer (there is no request), no download, no third-party import at module scope.
6. **Fallback**: on `LLMCallError` the wrapper runs the Anthropic leg once inside the caller's budget and logs `llm_fallback site=<site> primary=local_encoder fallback=anthropic reason=<reason>`; after whichever leg answers, `llm_route site=<site> backend=<backend> elapsed_ms=<int>` (lane A), now with ` confidence=<score>` appended whenever the returned instance carries a `confidence` attribute (`getattr(result, "confidence", None)` is not `None`; formatted `%.3f`). The token is backend-neutral: an Anthropic answer with a `confidence` field logs it too, so the same grep compares the two legs' score distributions per site after deploy. An instance without the attribute logs the lane A line byte-for-byte.
7. **Output**: the caller receives its output type and applies its own fail-safe on `LLMCallError`, exactly as before.

**Fit path** (`python -m tools.classification_eval --site <id> --fit --candidate local_encoder,anthropic`, run on the `valor`-owning host with services stopped):

1. `site_inputs(site, real_limit)` draws the row's fixtures plus real inputs (the default memory-store loader or the row's own), each with `source`; `--save-inputs` keeps the draw for replay.
2. **Split by digest**: inputs sort by `sha256(text)`; the held-out split takes, in digest order, inputs until it holds the site minimum with at least half of them real (real inputs are drawn into it first, by digest, up to half the minimum, then fixtures and remaining real by digest until the minimum); everything else is the training split. The split is a pure function of the input set, so a `--save-inputs` file replays the same split anywhere. Under the held-out minimum or under half-minimum real, `ShortfallError` refuses before any arm runs (exit 2, no spend).
3. **Label the training split** with the reference arm (Haiku with the site's prompt verbatim; gemma for C15), at the runner's concurrency-4 gate, keeping the per-input label; errors drop the input from training.
4. **Fit the head**: embed each training text through the leg's own `_embed` (the same code path that serves), fit the multinomial logistic regression (numpy, deterministic: fixed epochs, learning rate, L2, zero init), write the head to the staging path `data/classification_eval/heads/<site>.<run_id>.json` (gitignored) with provenance (`site`, `classes`, `W`, `b`, `embedding_model`, `embedding_revision`, `embedding_sha256`, `run_id`, `n_train`, `n_train_real`, `reference_model`, `created_at`), and print the training-split agreement as a sanity line (never the claim). The served path `agent/llm/backends/heads/<site>.json` is untouched at this step. The candidate arm for step 5 loads the staged head by path (`local_encoder_arm(site, head_path=...)`), so the measurement never reads the served head.
5. **Measure on the held-out split**: `compare(site, held_out, reference=<reference arm>, candidates=[local_encoder_arm(site, head_path=staged), anthropic_arm(site)], contended=...)` unchanged from lane A (agreement pass at 1, latency pass at 4, both arms on the candidate prompt shape); the record gains `fit: {head_run_id, n_train, n_train_real, split: "digest", landed: <bool>}`; `write_record` and `attach_claims` as before. The record is written on every run, because it is the measurement; `fit.landed` says whether this run was allowed to touch the served path.
6. **Land** (`--land` only): `run_fit(..., land=True)` is the only path that writes or deletes `agent/llm/backends/heads/<site>.json`, and the runner holds the landing rule, not the reviewer. `--land` requires both candidate arms: `run_fit` refuses before any arm runs (exit 2, no spend) when `anthropic` is absent from `--candidate`, because the restructured-shape Anthropic arm is the fallback a landed site serves on every leg error and its agreement is part of the landing. After `compare` returns the record, `run_fit` evaluates `evaluate_bar(record, "local_encoder")` and `evaluate_bar(record, "anthropic")` on the same held-out split; the copy runs only when both lists are empty. On that double PASS the staged head is copied to the served path (atomic rename), the record carries `fit.landed: true`, and the builder sets `backend=Backend.LOCAL_ENCODER` on the declaration, restructures the call to `(text, system=instructions)`, and commits the head with the declaration and the record id in the message. When either arm misses, the run is a MISS for landing: the served head for that site is deleted if one exists (no orphan weights), the staged head stays under `data/` for inspection, the record carries `fit.landed: false` and `fit.miss_arms: [<arm>, ...]` naming which arm failed which criterion, `render_report` prints the same, and the site stays on `ANTHROPIC`. Without `--land` the run measures, prints `render_report`, leaves the staged head in place, and stops: a drift check on a landed site can never remove or replace its serving head.
7. **Audit** (`--audit`): for a `LOCAL_ENCODER` site, the latest record whose `fit.landed` is `true` is the one judged (measure-only records are skipped by the audit, so a diagnostic re-run on a landed site leaves the audit green); both its `local_encoder` and `anthropic` arms must PASS `evaluate_bar` and the committed head's `run_id` must equal that record's `fit.head_run_id`; otherwise exit 1. A `LOCAL_ENCODER` site with no landed record at all is a MISS.

## Architectural Impact

- **New dependencies**: optional extra `classification-local = ["onnxruntime>=1.25.0", "tokenizers>=0.21"]`; `numpy` is already a base dependency. `tokenizers` pulls two transitive packages, `huggingface-hub` and `tqdm` (`uv pip install --dry-run onnxruntime tokenizers` on the pinned interpreter lists both), so the full fleet-wide install is four packages; the leg imports neither transitive package anywhere, and the module-scope anti-criterion grep in Verification covers the leg. Installed fleet-wide by `/update`'s `uv sync --all-extras`. Outside the coupled `anthropic` + `pydantic-ai-slim` pin set (#3089/#3140) and outside `_load_stack`.
- **New weights**: 34 MB (`model_int8.onnx`) + 0.7 MB (`tokenizer.json`) in `~/.cache/valor-encoder/` (`LOCAL_ENCODER_MODELS_DIR` override), fetched and checksum-verified by the `/update` step numbered after the last existing 3.x step in `scripts/update/run.py` (3.15 on today's main: 3.13 is Redis durability and 3.14 is Redis replication) and placed in the code after the kokoro (3.11) and ffmpeg (3.12) pair; per-site heads (about 30 KB of JSON each) committed under `agent/llm/backends/heads/`.
- **Interface changes**: none to `run_typed`, the leg protocol, `Route`, `LLMTask`, or `Site`. One enum member, one router rule, one `_LEGS` entry, one `default_sdk_timeout` branch, one runner arm builder, five runner flags (`--fit`, `--land`, `--preflight`, `--precheck`, and `--candidate local_encoder` by derivation), one `fit` block in the record, one audit rule, one `confidence=<score>` token on the wrapper's `llm_route` log line.
- **Coupling**: a landed site now depends on a committed head that was fit against that site's output type and prompt shape; the audit ties the head to the record and the parity test ties the head's classes to the output type, so a prompt or label change on a landed site is caught by the test (class mismatch) or by review (a record older than the change).
- **Data ownership**: the reference arm's labels are used twice (record and training set) and both uses are recorded; the head is derived data owned by the site's declaration, versioned in git.
- **Reversibility**: a site's `backend` word back to `ANTHROPIC` plus deleting its head; the leg, extra, and weights go with the last landed site.

## Appetite

**Size:** Large

**Team:** Solo dev (builder + validator pairs), PM, code reviewer

**Interactions:**
- PM check-ins: 2 (the preflight result on the landing host; the per-site landing summary). Open Question 1 was answered before build (comment 5755701599).
- Review rounds: 2+ (the leg and runner; then each landing commit against the bar)

The code is a day; the landing loop is where the time goes, and it is bounded per site (Task 8 gives each site half a build day).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Lane A merged | `git merge-base --is-ancestor 4703bce23 HEAD` | `LLMTask`, `resolve`, the leg protocol, the runner, the records |
| `onnxruntime` and `tokenizers` installable on the pinned interpreter | `.venv/bin/python -c "import sys; assert sys.version_info[:2] == (3, 14)" && uv pip install --dry-run -p .venv/bin/python onnxruntime tokenizers` | Research finding 4 |
| Redis reachable for records and heads | `.venv/bin/python -c "from tools.classification_eval.records import latest_record; latest_record('routing.needs_response')"` | The runner reads and writes `ImprovementEvidence` |
| Anthropic key for the reference arm | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Haiku labels the training split and is the reference arm |
| Real `valor` messages in this memory store (informational here; the hard gate is Task 7's `--preflight` on the landing host) | `.venv/bin/python -c "from tools.classification_eval.arms import real_messages; print(len(real_messages(2000)))"` | `n_real` for the held-out split needs 100 real for the routing sites and 25 elsewhere, plus training real messages; this MacBook Air prints 12 |
| Services state (must print `free` on the landing host before a fit run) | `.venv/bin/python -c "from tools.classification_eval import is_contended; print('contended' if is_contended() else 'free')"` | `contended: false` (Race 3 of lane A); `./scripts/valor-service.sh stop` clears it |

The real-message row is the machine question: this MacBook Air prints 12, which satisfies no site, so Tasks 7 to 9 run on the Valor host that owns the `valor` bridge (Solution, "Where the landing runs"), where Task 7's `--preflight` is the hard gate.

## Solution

### Key Elements

- **`Backend.LOCAL_ENCODER`** (`agent/llm/tasks.py`): the third member, value `"local_encoder"` (the log token). Declared sites keep the literal convention (`Backend.LOCAL_ENCODER`), so the AST registry, doctor, the audit, and the parity test read them with no change.
- **The encoder leg** (`agent/llm/backends/local_encoder.py`): lane A's leg protocol, met as Data Flow step 5 describes. Module scope is stdlib and our own code only (#3001, #3525); `onnxruntime`, `tokenizers`, and `numpy` are imported inside `_load_runtime()`, the memoized loader that is also the test seam (`monkeypatch.setattr(local_encoder, "_load_runtime", fake)`). The leg never downloads. Its `system` argument is accepted and ignored: instructions are for the Anthropic fallback, and the doc says so.
- **The head file** (`agent/llm/backends/heads/<site>.json`): `classes` (the output field's values as the runner's `label` reducer renders them, so `"True"`/`"False"` for a bool field), `W` (384 × k), `b`, and provenance. Loaded once per site per process. The output-type shape rule a landed site meets: exactly one field typed `bool` or `Literal[...]` whose values equal `classes`; a `confidence: float` field, if present, receives the winning softmax score; every other field has a default. Enforced at call time by the leg (`LLMCallError(reason="validation")` → fallback) and statically by `tests/unit/test_classifier_heads.py` over every committed head against its site's output type as `tools/classification_eval/sites.py` imports it.
- **Router rule** (`agent/llm/router.py`): ahead of the Ollama rules, `task.backend is Backend.LOCAL_ENCODER` → eligible: `Route(LOCAL_ENCODER, task.site, fallback=anthropic)`, else `anthropic`. Same eligibility read, same fail-closed rule (charter §7). The docstring's reserved line is rewritten for the member that landed.
- **Wrapper and timers**: `_LEGS[Backend.LOCAL_ENCODER] = local_encoder.call`; `default_sdk_timeout(LOCAL_ENCODER)` returns `settings.timeouts.local_typed_hard_s` (the existing operator lever; it only bounds the fallback's deadline arithmetic, since the leg has no request). No new settings key. The `llm_route` line at `agent/llm/wrapper.py:300` gains ` confidence=<score>` when the result carries a `confidence` attribute (Data Flow step 6), so a post-deploy grep shows the per-site score distribution; this is the live signal for a head that clears the held-out bar and drifts on traffic (Risk 2). A scheduled re-comparison against fresh real messages (the measure-only `--fit` run on a timer, with its reference spend) is a follow-up issue named in the feature doc, not built here.
- **Weights** (`config/models.py`): `LOCAL_ENCODER_MODEL = "Xenova/bge-small-en-v1.5"`, `LOCAL_ENCODER_REVISION = "ea104dacec62c0de699686887e3f920caeb4f3e3"`, `LOCAL_ENCODER_FILES = {"onnx/model_int8.onnx": "bf64d054…", "tokenizer.json": "d241a60d…"}` (full sha256 from Research finding 3), `LOCAL_ENCODER_DIM = 384`. `scripts/download_local_encoder_models.py` (the kokoro script's shape: `~/.cache/valor-encoder/`, `LOCAL_ENCODER_MODELS_DIR` override, `--dry-run`, `--force`, streams to `.part`, verifies sha256 before renaming, re-downloads on mismatch) and `scripts/update/local_encoder.py::ensure_models` run as the `/update` step numbered after the last existing 3.x step (3.15 on today's main, after the kokoro/ffmpeg pair), non-fatal (a machine without the weights falls back to Anthropic on every call and doctor says so).
- **Runner** (`tools/classification_eval/`): `local_encoder_arm(site_id)` in `arms.py` (direct leg call with `Route(LOCAL_ENCODER, site_id)`, like `ollama_arm`, so a leg failure is the arm's own error and never a fallback answer); the builders dict in `__main__.py` gains the entry and a test asserts every `Backend` member has one; `--fit` (Data Flow, fit path) in a new `tools/classification_eval/fit.py` (split, label, embed, fit, write the staged head, measure) and `--land` (the only flag under which `run_fit` writes or deletes the served head `agent/llm/backends/heads/<site>.json`; it requires `anthropic` among the candidates and refuses before any spend otherwise; the copy to the served path runs only when `evaluate_bar` is empty for both the `local_encoder` and the `anthropic` arm on the same held-out record, and a miss on either arm deletes any served head and stamps `fit.landed: false` with `fit.miss_arms`; without `--land` a run on an already-landed site is a drift check that leaves the serving head alone); `--preflight` prints `real messages available: N` and one line per site (`needs ≥ M real in the held-out split; n_train = n_fixtures + N - minimum_n; n_train_real = <count>` for the site's fixture count and minimum, where `n_train_real` is computed by running `split_by_digest` on the actual draw, since the held-out split takes M real first and then fills to `minimum_n` from fixtures and real by digest, so it lies between `max(0, N - minimum_n)` and `N - M`; a routing site at exactly 100 real prints `n_train = 88, n_train_real = 0`) and exits 1 when N is under the routing sites' need; `ComparisonRecord` gains `fit: dict | None` (with `landed: bool`); `_audit_row` applies the landed-arm rule to every non-Anthropic backend and, for `LOCAL_ENCODER`, judges the latest record whose `fit.landed` is `true` (measure-only records are skipped) and requires the committed head's `run_id` to equal that record's `fit.head_run_id`. `latest_record` grows an optional predicate for this (`latest_record(site, where=lambda r: r.get("fit", {}).get("landed"))`), so the audit's read stays one call.
- **Doctor**: a `local_encoder` row in the "LLM routing" section: extra importable, weights present with matching checksums, one head per declared `LOCAL_ENCODER` site; fails when a declared site would fall back on every call on this machine (no extra, no weights, or no head), mirroring the `ollama_daemon` row.
- **Per-site landing**: the one-word `backend` edit, the `(text, system=instructions)` call shape, the committed head, the record id in the commit message and in the site's row of the taxonomy table, and the PR body's landing summary (site, backend, held-out agreement for both candidate arms, p95 at 4, `contended`, error rate, `n`, `n_real` with the minimum, `n_train`, `n_train_real`, record id, head run id, failing criterion if any).
- **The GLiClass rejection**: one claim on a `probe` investigation of case `1ec40086` (`tools/improvement_investigations.py::open_investigation` + `record_claims`, the runner's own path) carrying the spike-2 table and the model's pin, URL, and checksums, pointing at this plan on GitHub. Recorded in Task 0 on this machine; no GLiClass code is written.

### Flow

Inbound message → `run_typed(text, Model, task=SITE, project_key, system=instructions)` → `resolve` picks `LOCAL_ENCODER` for eligible context → leg embeds the text, applies the site's head, returns the typed answer in a few milliseconds → `llm_route site=… backend=local_encoder` → caller proceeds. On any leg error → Anthropic fallback inside the budget → `llm_fallback` + `llm_route backend=anthropic`.

Builder on the landing host → `--preflight` (enough real messages?) → `--precheck` table orders the sites and skips any more than 0.10 under its bar → per remaining site: `--site <id> --fit --land --candidate local_encoder,anthropic --save-inputs data/classification_eval/<id>.jsonl` → the runner applies `evaluate_bar` to both arms → PASS on both: the staged head is copied to the served path, flip `backend`, restructure the call, commit head + record id → MISS on either: the served head (if any) is deleted, `fit.miss_arms` names the arm, site stays → `--audit` exit 0 at the PR head → PR body carries the summary → reviewer applies the bar per site. After landing, `--site <id> --fit` without `--land` is the drift check: a new record, the served head untouched, the audit still green.

### Technical Approach

- **Where the landing runs.** Tasks 0 to 6 (rejection claim, leg, runner, tests, doctor, docs skeleton) are machine-independent and run wherever the lane starts; this MacBook Air can run them and the free offline pre-check (Task 5). Tasks 7 to 9 (fit and land) run on the Valor host that owns the `valor` bridge, because its subconscious memory store holds the real inbound `valor` messages the bar's `n_real` criterion demands (`real_messages` reads `fetch_all_records("valor")`, `source="human"`, TUI rows excluded), and `--preflight` is the gate that proves it before any spend. The Air's store holds 12 real messages, so nothing lands from here; `logs/classification_audit.jsonl` is not a corpus (Research finding 5). Latency is measured on that host too, which is representative of the serving fleet and, for a CPU encoder, does not depend on the GPU. If the lane's build session is not on that host, the builder finishes Tasks 0 to 6, pushes, and hands Tasks 7 to 9 to a session on the host through the pipeline's normal steering, with the plan's commands verbatim; the PR is not opened until the landing tasks have run somewhere that passes preflight.
- **What "real" means for the outbound sites** stays as lane A defined it (`tools/classification_eval/sites.py:21-29`): C8, C9, C10, C15 use inbound messages as adversarial drafts, recorded honestly under `n_real`; C11 reads transcript windows; C14 reads memory rows of every source. The fit path draws through the same loaders, so each site trains on the shape it is measured on.
- **Text-first composition for context-bearing sites** (C2 thread, C6 transcript, C10 pair, C11 window): one `def <site>_text(message, context) -> str` beside the declaration, message first, used by the call site and by the row's `candidate_prompt`. Truncation at 512 tokens then drops the oldest context. The reference prompt is untouched (it is the thing being agreed with).
- **The fit is deterministic and cheap**: numpy multinomial logistic regression, zero init, 300 full-batch epochs, learning rate 0.5, L2 1e-3 (the spike's settings), inputs L2-normalized; the head file records them. No scikit-learn; no early stopping on the held-out split (that would leak it into the fit). A second fit on the same inputs and labels writes the same `W` and `b` bit-for-bit, and a test pins that.
- **Reference spend**: per routing site about 700 training labels plus 200 × 2 held-out reference calls; per 50-minimum site about 300 + 50 × 2. Haiku at the list price in `arms.py` (`HAIKU_PRICE`, retrieved 2026-06-24) is well under one dollar per site; C15's gemma reference is metered under `promise_detector` as lane A left it. The Anthropic candidate arm on the restructured shape doubles the held-out Haiku calls; it is what proves the fallback still agrees with the verbatim prompt. Derived from the precheck gate's fit list on today's numbers: the measured sites that reach a fit are C1 (one routing site: about 700 + 400 = 1,100 Haiku calls) and C9 (one 50-minimum site: about 300 + 100 = 400), about 1,500 calls and under two dollars for both; C2 to C5 spend nothing. Each of the eight unmeasured sites the live precheck admits adds its own line at the per-site figures above, so the lane's ceiling is about 1,100 for each admitted routing-shaped site and 400 for each 50-minimum site, all inside the $10/day inference line Tom's answer to Open Question 2 covers.
- **Landing order and the per-site box**: C1, C9, then C15, C8, C10, C7, C14, C11, C13, C6 as the provisional order for the sites that reach a fit (Spike 3 measured C1 to C5 and C9; on today's numbers the precheck gate admits C1 and C9 and skips C2, C3, C4, and C5; the other eight are placed by tier and input shape, unmeasured); Task 5's precheck table replaces it with the measured order, highest precheck agreement relative to its bar first, before Task 8 starts. Each site that reaches the fit is boxed at half a build day, iteration limited to the text composition, `real_limit`, and the one permitted model swap (`bge-base-en-v1.5` int8, pinned the same way, recorded on the head).
- **Precheck gate (`PRECHECK_MARGIN = 0.10`)**: a site whose Task 5 precheck agreement is under `bar - 0.10` (for example under 0.85 on a `high` site, under 0.80 on `medium`, under 0.75 on `low`) skips the fit: zero reference-arm calls, no comparison record. The precheck number is recorded as a claim on the case through the same `record_claims` path Task 0 uses, and the site is listed in the PR body and the "Lane B Outcome" table with reason `precheck_below_bar`. This applies per site the fixture-only signal Task 0 trusts to reject GLiClass for the whole backend. The number the gate reads is the LR head's five-fold agreement (the Spike 3 "LR 5-fold agreement" column, which is what `fit_head` computes), never the kNN column. On today's numbers, against a gate of `bar - 0.10`: C1 fits (0.894 vs 0.85), C9 fits (0.825 vs 0.75), C2 skips (0.824 vs 0.85, one point under the gate), C3 skips (0.574 vs 0.85), C4 skips (0.638 vs 0.85), C5 skips (0.450 vs 0.80). So the measured fit list is C1 and C9, and the eight sites Spike 3 did not measure (C15, C8, C10, C7, C14, C11, C13, C6) are decided by Task 5's live table. A live `precheck_below_bar` on C2 is the gate working as written, not a tool bug: the precheck is fixture-only, so the landing host's table will read close to the same number, and the site skips. Widening the margin to admit C2 is a change to this plan's constant, not a builder call. The margin is stated here and in `tools/classification_eval/fit.py` as one constant; a site inside the margin fits, because real training messages can close a gap of ten points and cannot close one of thirty. C12 is excluded: `JobRouteDecision.job_id` is an open answer over a per-call candidate list, not a closed set, and a head cannot produce it. C16 is `client_only`.
- **Thresholds**: if C13 lands, `INTENT_CONFIDENCE_THRESHOLD` (`tools/classifier.py`) is re-tuned on the record's held-out softmax scores and the value is committed with the record id in the message; `JOB_ROUTER_CONFIDENCE_THRESHOLD` is untouched because C12 does not land.
- **Rejection exit for the supervised backend** (the issue's shape, kept): if no site clears its bar after the loop, the lane records each MISS on the case, deletes the leg, the extra, the weights script, the update step, the doctor row, and the runner arm in one commit (no dead backend under Tom's no-default-off rule), and the PR ships the runner's `--fit` and `--preflight` plus the rejection claims and the doc section. `Backend` stays two members in that branch.
- **Import cost (#3525)**: nothing new at `agent.llm` module scope beyond the leg module, whose own module scope is stdlib. The download script and the update step import `config.models` only.
- **Concurrency**: `_classify` runs in `asyncio.to_thread`; one `InferenceSession` per process with `intra_op_num_threads=min(4, os.cpu_count() or 1)` (the spike used 4 on a 10-core machine; a smaller host gets fewer threads rather than oversubscription) is shared across calls (`InferenceSession.run` is thread-safe); the loader lock prevents a double load on the first burst. No semaphore. The 3 ms p95 at concurrency 4 was measured on the 10-core M4 Air only; the no-semaphore decision is confirmed per host by the landing host's own latency pass, since every landing record carries `p95_c4` measured there, and a host whose `p95_c4` misses the budget does not land.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The leg's `except Exception` around the ONNX run (the only broad handler this plan adds) re-raises as `LLMCallError(reason="transport")` with the cause chained and one `logger.error` line; a test injects a raising fake session and asserts the `LLMCallError`, its `reason`, the `__cause__`, and the log line on `caplog`. No swallowed exception anywhere in the leg.
- [ ] Missing extra: `_load_runtime` catches `ImportError` on `onnxruntime` or `tokenizers` and raises `LLMCallError("classification-local extra not installed", reason="transport")`; tested with the raising shim from `tests/unit/test_llm_import_safety.py`, and the wrapper-level test asserts the Anthropic fallback then answers with `llm_fallback ... primary=local_encoder reason=transport`.
- [ ] Missing or mismatched weights: `_load_runtime` verifies each file's sha256 against `LOCAL_ENCODER_FILES` and raises `LLMCallError(reason="transport")` naming the file and the download script; tested with a temp models dir holding a wrong-content file (mutation: flip one byte, the leg must refuse).
- [ ] Missing head: `LLMCallError(reason="transport")` naming `heads/<site>.json`; tested with a fake site id.
- [ ] Output-type shape violations (two closed-set fields; a required string field with no default; classes that differ from the head's): `LLMCallError(reason="validation")`, one test per violation, each asserting no ONNX run happened (the fake session's call count is 0).
- [ ] Runner: `--fit` refuses under the held-out minimum or half-minimum real with `ShortfallError` before any arm call (test counts reference-arm calls: 0); a reference-arm error on a training input drops that input and is counted in the report; a reference-arm error rate above `MAX_ERROR_RATE` on the training split aborts the fit (no head written, staged or served).
- [ ] Runner, served head protection: a `--fit` run without `--land` on a site whose served head exists leaves `agent/llm/backends/heads/<site>.json` byte-identical on both PASS and MISS, writes its record with `fit.landed: false`, and the audit over that site still judges the earlier landed record (test: land once, measure-only twice with a scripted MISS, assert the served file's sha256 is unchanged and `--audit` exits 0). Mutation: remove the `land` guard around the delete and the test must go red.
- [ ] Runner, both-arms landing gate: `--fit --land` with a scripted record where the `local_encoder` arm passes and the `anthropic` arm misses the tier bar leaves `agent/llm/backends/heads/<site>.json` absent (or deletes a pre-existing one), writes the record with `fit.landed: false` and `fit.miss_arms: ["anthropic"]`, and `render_report` names the arm and criterion; the mirror case (`anthropic` passes, `local_encoder` misses) behaves the same with `["local_encoder"]`; only the double PASS copies the head. `--land` with `--candidate local_encoder` alone (no `anthropic` arm) refuses with exit 2 before any arm call (the reference-arm fake's call count is 0). Mutation: gate the copy on `local_encoder` alone and the Anthropic-miss test must go red.
- [ ] Runner, precheck gate: a site whose precheck agreement is under `bar - PRECHECK_MARGIN` skips the fit with zero reference-arm calls and no comparison record, and records one `precheck_below_bar` claim on the case (test with a scripted precheck number; the reference-arm fake's call count is 0).
- [ ] `scripts/download_local_encoder_models.py`: a checksum mismatch after download deletes the `.part` file and exits 1 with the expected and actual digests; the update step surfaces that as a warning (non-fatal), tested through `ensure_models` with a fake script exit.

### Empty/Invalid Input Handling
- [ ] `run_typed` already rejects an empty or whitespace prompt before routing (lane A); the leg never sees one. A test on the leg directly with `prompt=""` asserts `LLMCallError(reason="validation")` so the leg is safe when called by the runner's arm, which bypasses the wrapper.
- [ ] A text longer than 512 tokens is truncated by the tokenizer (`enable_truncation(512)`); a test embeds a 2,000-word input and asserts a 384-d result and a call that finishes.
- [ ] A head with `classes` of length 1 or with a `W` of the wrong shape is refused at load (`LLMCallError(reason="validation")`), and `tests/unit/test_classifier_heads.py` refuses to let one be committed.
- [ ] `--fit` with a training split whose labels collapse to one class writes no head and reports it (a one-class head would answer that class for everything and the held-out record would say so, but refusing earlier saves the reference spend).

### Error State Rendering
- [ ] The runner's report renders MISS with the failing criteria named (lane A) and the new `fit` line (`fit: n_train=… n_train_real=… head=<run_id>`); a rendering test covers a MISS record with a `fit` block.
- [ ] `--preflight` prints the shortfall per site in words a human acts on (`routing sites need ≥ 100 real in the held-out split; this store has 12`) plus the training arithmetic (`n_train`, `n_train_real`) per site, and exits 1; tested with a fake store, including the boundary case of exactly 100 real messages (`n_train = 88, n_train_real = 0`).
- [ ] Doctor's `local_encoder` row fails with a `fix` naming the script or the extra when a declared site would fall back on every call; tested with the routing-section fixtures in `tests/unit/test_doctor.py`.

## Test Impact

Verified on `main` at `149f0d0da` by reading each file; the router table test, the eligibility test, the wrapper's `_LEGS` tests, the doctor test, and the runner's audit tests all key on the two-member `Backend` enum and on `Backend.OLLAMA` as the only local backend.

- [ ] `tests/unit/test_llm_tasks.py:24` (`{b.value for b in Backend} == {"anthropic", "ollama"}`): UPDATE: the set gains `"local_encoder"`.
- [ ] `tests/unit/test_llm_router.py::_expected` (`:120-123`) and `TestEveryDeclaration::test_route_matches_the_declaration`: UPDATE: `_expected` returns `Route(LOCAL_ENCODER, task.site, fallback=ANTHROPIC_ROUTE)` for a `LOCAL_ENCODER` declaration with key `valor`, `ANTHROPIC_ROUTE` otherwise; add `test_rule_3_eligible_local_encoder_carries_an_anthropic_fallback`, `test_rule_4_ineligible_local_encoder_fails_closed`, and extend `test_only_the_ollama_rule_consults_eligibility` (`:103`) to the two local rules (rename to `test_only_the_local_rules_consult_eligibility`).
- [ ] `tests/unit/test_llm_router_eligibility.py` (`:76-96`, the `OLLAMA`-site parametrization): UPDATE: parametrize over every declaration whose backend is not `ANTHROPIC`; the "valor message reaches the local leg with `gh` unavailable" case asserts the leg named by the declaration (`legs[task.backend]`), so a `LOCAL_ENCODER` site asserts the encoder leg.
- [ ] `tests/unit/test_llm_wrapper.py::TestPerBackendSdkTimer` (`:633-656`): UPDATE: add the `local_encoder` rows (`local_typed_hard_s` default, explicit `sdk_timeout` wins) and a third fake in the `_LEGS` table fixture at `:607-608`; the fallback tests at `:553-708` gain one case: encoder leg raises `LLMCallError` → Anthropic fallback inside the budget, `llm_fallback ... primary=local_encoder` on `caplog`. Any existing test asserting the exact `llm_route` line: no change (an output type without `confidence` logs the lane A line byte-for-byte); add one test that a result with `confidence=0.91` logs `llm_route site=... backend=... elapsed_ms=... confidence=0.910`.
- [ ] `tests/unit/test_llm_backend_ollama.py`: no change; kept as the template for `tests/unit/test_llm_backend_local_encoder.py` (create).
- [ ] `tests/unit/test_llm_import_safety.py`: UPDATE: the raising shim fixture also writes `onnxruntime.py` and `tokenizers.py`, and the assertion that `import agent.llm` (and `bridge.telegram_bridge`) succeeds with every third-party module broken now covers the encoder leg's module scope.
- [ ] `tests/unit/test_llm_task_taxonomy.py` (doc/code parity, check 5; hotfix #1055 check 6 over every function in `agent/llm/backends/`): no test change; the new leg module and the new site-table rows must satisfy both as written. Check 6 is the guard that keeps `asyncio.wait_for` out of the encoder leg.
- [ ] `tests/unit/test_doctor.py:881-909` (routing section: `Backend.OLLAMA` sites and the `ollama_daemon` row): UPDATE: add the `local_encoder` row assertions (extra importable, weights present with matching checksum, one head per declared `LOCAL_ENCODER` site) with a fake models dir and a fake heads dir; the `ollama_daemon` assertions are unchanged.
- [ ] `tests/unit/test_classification_eval.py::test_audit_exit_codes` (`:395`) and `test_audit_over_two_sites_fails_when_either_misses` (`:412`): UPDATE: parametrize the local-landing cases over `ollama` and `local_encoder` (the audit's landed-arm rule now applies to every backend other than `ANTHROPIC`), and add the head-provenance cases: a `LOCAL_ENCODER` landing whose committed head `run_id` differs from the landed record's `fit.head_run_id` exits 1; a newer measure-only record (`fit.landed: false`, MISS) after a landed PASS record leaves the audit at exit 0; a `LOCAL_ENCODER` site with only measure-only records exits 1.
- [ ] `tests/unit/test_classification_eval.py::test_parse_candidates_rejects_an_unknown_backend` (`:444`): no change (`CANDIDATE_BACKENDS` is derived from the enum, so `local_encoder` is accepted automatically); add `test_candidate_arm_builders_cover_every_backend` so a member without an arm builder fails by name.
- [ ] `tests/unit/test_classification_eval.py`: ADD (create alongside the existing runner tests): the `--fit` split is deterministic by digest and refuses under the held-out minimum before any arm runs; the fitted head round-trips through the leg's loader from the staging path; the record carries the `fit` provenance block with `landed`; `--fit` without `--land` never writes or deletes the served head; `--land` copies only when both the `local_encoder` and the `anthropic` arm clear `evaluate_bar` and deletes when either misses; `--land` without an `anthropic` candidate refuses before any spend; `--precheck` and `run_fit` share the one `fit_head` call (a test monkeypatches `fit_head` and asserts both paths hit it); the precheck gate skips a site under `bar - 0.10` with zero reference calls; `--preflight` prints the real-message count, `n_train`, and `n_train_real` against each site's need and exits 1 when the routing sites cannot be met.
- [ ] The landed sites' own test files (C1 `classify_needs_response`, `bridge/routing.py:811`, is tested in `tests/unit/test_routing.py`, verified by `grep -ln "classify_needs_response" tests/unit/*.py` on the baseline; C9 in `tests/unit/test_promise_gate.py`): UPDATE only where a landing restructures the call (`prompt` becomes the text under classification and the instructions move to `system`): any test asserting the prompt string the fake receives is updated to the new `(prompt, system)` split. The fail-safe tests are byte-identical, since the wrapper contract does not change. Before Task 8 the sites-builder runs `grep -ln "<site function>" tests/unit/*.py` for every candidate site and pastes the file list into the PR (lane A's caller-census rule), so no site's test file is named from memory.

## Rabbit Holes

- **Tuning GLiClass label wording.** Spike 2 tried three phrasings per site and moved the numbers by a few points against gaps of 15 to 75. The rejection stands on that evidence; the build spends no time on it.
- **Fine-tuning the encoder** (GLiClass or BGE with `torch` in a throwaway env, ONNX re-export, hosting per-site 100 MB+ weights). It would likely beat a frozen-embedding head, and it is a different lane: a training environment, weight hosting, and a held-out protocol of its own. If the linear head plateaus under the bar on the high-tier sites, that is the follow-up to file, with this lane's records as its baseline.
- **Active learning or more labels than the reference arm gives for free.** The training set is whatever the draw holds; buying more Haiku labels to chase a bar is a budget question for Tom, not a builder loop.
- **Re-scoring the reference.** The reference is Haiku with the verbatim prompt, by lane A's definition. Agreement with it is the claim; whether Haiku is right is a different experiment.
- **Making the leg download weights on a miss.** A 34 MB fetch inside a 3 s hot-path call is a timeout with extra steps; `/update` fetches, the leg refuses, the router falls back.
- **A generic "trainable backend" abstraction.** One head format, one fit function, one embedding model. Lane C's decisions transport shares the leg protocol and nothing else.
- **C12.** Its answer is a job id from a per-call list; that is retrieval, not classification. Out.
- **Serving the embedding through Ollama or an embedding endpoint.** The whole point is a process-local CPU call with no daemon.

## Risks

### Risk 1: The learning curve flattens under the bar on real traffic
**Impact:** The high-tier sites (C1 to C4, 95%) stay on Anthropic; the lane lands only medium and low tiers, or nothing.
**Mitigation:** The plan-time numbers (C1 0.894 and C9 0.825 from 150 and 32 fixtures) rank the sites so the likeliest landings run first, and Task 5's precheck re-ranks every site for free before any spend; a site more than `PRECHECK_MARGIN` (0.10) under its bar on the precheck skips the fit entirely (Technical Approach, precheck gate); each site that fits is boxed at half a day with three levers (text composition, `real_limit`, the one model swap); a MISS is a recorded result with the failing criterion, and the rejection exit for the whole backend is written into the Technical Approach so a lane that lands nothing still ships the runner's fit mode and the claims. The Success Criteria count "either landed by the bar or recorded as rejected" as done, exactly as the issue does. The learning curve is bounded by the landing host's real-message count, not by the spike's 150-example folds: with `ROUTING_FIXTURES = 188` and a held-out split of `minimum_n = 200` holding at least 100 real inputs, a routing site's training split is `n_fixtures + n_real - minimum_n = n_real - 12` examples, real-heavy; at the preflight minimum of 100 real messages that is 88 fixtures and no real training example, smaller than the spike's folds. "About 700 training labels" (Reference spend) assumes roughly 700 real messages on the host. `--preflight` prints `n_train` and `n_train_real` per site beside the held-out need so the number is known before the fit runs.

### Risk 2: The head overfits the fixtures and the record flatters it
**Impact:** A site clears the bar on the held-out split and misbehaves on live traffic.
**Mitigation:** The held-out split is chosen by content digest before any label exists, carries at least half real inputs (the bar's `n_real` rule applies to it), and the fit never sees it (no early stopping, no model selection on it). The training-split agreement is printed as a sanity line and is never a claim. The record's `fit` block makes the split visible to the reviewer, and the audit ties the committed head to that record. Live evidence after deploy is the `llm_route` grep in Verification.

### Risk 3: The restructured call changes what the Anthropic fallback answers
**Impact:** A landed site's fallback (Haiku on `system` + text) disagrees with the verbatim prompt Haiku was measured on, so the degraded state is silently worse than lane A's.
**Mitigation:** `--land` requires `anthropic` among the candidates and refuses before any spend without it, so every landing record measures Haiku on the restructured shape against the verbatim reference on the same held-out inputs. The runner holds the rule: `run_fit` copies the staged head to the served path only when `evaluate_bar` is empty for both arms, and an Anthropic-arm miss is a landing MISS (served head deleted, `fit.landed: false`, `fit.miss_arms: ["anthropic"]`) that no builder step can override. The PR body shows both arms' agreement per site, and the taxonomy doc states the rule. The fixture-only shape in the runner and the served shape share one function (Text-first composition).

### Risk 4: The landing host cannot satisfy `n_real`
**Impact:** The same `n_real` miss lane A recorded, again, on every site.
**Mitigation:** `--preflight` prints the real-message count against each site's need before any spend and the plan names the host (the one owning the `valor` bridge). If no host holds 100 real inbound messages for the routing sites, those sites are out of reach for this lane by the bar's own rule and the plan says so in the PR; the 50-minimum sites need 25 real in the held-out split plus training real messages, which is the first number the preflight reports.

### Risk 5: The first call in a process pays the model load inside a 3 s budget
**Impact:** A hot-path site's first call after a restart falls back to Anthropic once.
**Mitigation:** Load is 0.05 s on the Air for the 34 MB model (Spike 3), well inside every budget, and the loader lock means a burst pays it once. No warm-up code; the number is the argument, and the doc records it.

### Risk 6: Merge conflicts with lane C (#3421) on the same seams
**Impact:** Two PRs each add an enum member, a router rule, a `_LEGS` entry, a timer branch, an arm builder, doc rows, and touch `_audit_row`.
**Mitigation:** Each addition is one adjacent line; whichever lane merges second rebases. The audit generalization is written here as "every backend other than `ANTHROPIC`" so lane C needs no change to it; the doc table rows are per site and disjoint. Both plans name the other in Freshness Check.

### Risk 7: The extra or the weights are missing on a fleet machine after deploy
**Impact:** A landed site falls back to Anthropic on every call on that machine.
**Mitigation:** `/update` installs all extras and runs the weights step; doctor's `local_encoder` row fails and names the fix; the `llm_fallback ... primary=local_encoder reason=transport` line is the log signature (one per call, each fast); the fail direction is the safe one (Haiku answers).

## Race Conditions

### Race 1: Two first calls load the ONNX session at once
**Location:** `agent/llm/backends/local_encoder.py::_load_runtime`
**Trigger:** A burst of messages on a freshly restarted bridge.
**Data prerequisite:** none
**State prerequisite:** Exactly one `InferenceSession` per process.
**Mitigation:** A module-level `threading.Lock` around the memoized load (the loader runs inside `asyncio.to_thread`, so an `asyncio.Lock` would be the wrong primitive); a test fires 20 concurrent calls on a fresh module and asserts one load.

### Race 2: Fit and serve on the same head file
**Location:** `tools/classification_eval/fit.py` writing `heads/<site>.json` while a bridge process on the same machine has the head memoized
**Trigger:** A builder re-fits a landed site on the landing host with services running.
**Data prerequisite:** none
**State prerequisite:** A served head is the committed head.
**Mitigation:** The fit path writes the fitted head to the staging path under `data/classification_eval/heads/` and touches the served path only under `--land` (atomic rename on PASS, delete on MISS), so a measure-only re-fit of a landed site never changes what serves; the runner refuses to fit while `is_contended()` is true (the same services check lane A uses for latency), so the bridge is stopped when a head changes under `--land`; the memoized head is reloaded at process start only, which `/update`'s restart provides.

### Race 3: Comparison arms interleave with live traffic
**Location:** `tools/classification_eval/`
**Trigger:** A latency run while the bridge serves messages.
**Mitigation:** Lane A's `contended` stamp and refusal to clear the bar, unchanged; the encoder arm has no daemon to share, but the reference arm and the Anthropic candidate share the semaphore, so the rule stays.

## No-Gos (Out of Scope)

- [EXTERNAL] Running Tasks 7 to 9 on the Valor host that owns the `valor` bridge. This MacBook Air cannot reach that host and its own memory store holds 12 real messages; the preflight is the gate and the hand-off is described in Technical Approach.
- [SEPARATE-SLUG #3421] The decisions transport (lane C); its Ollama fallback and its own rule. Coordination in Risk 6.
- [SEPARATE-SLUG #3422] The emoji reaction as a decision site; `tools/emoji_embedding.py` and its callers are untouched (anti-criterion in Verification).
- [SEPARATE-SLUG #3525] Deferring `agent/__init__.py`'s eager import chain; this lane adds nothing to it (anti-criteria on the leg's module scope in Verification).
- [SEPARATE-SLUG #2494] C12 `job_router.route`: excluded from this backend (its `job_id` is not a closed set); stays on granite with its latency-only record. Anti-criterion in Verification.
- [EXTERNAL] Any change to the charter's §7 line or the unit-2 budget. Tom-owned.
- [EXTERNAL] Fine-tuning the encoder (Rabbit Holes). If the linear head plateaus, the builder files the follow-up issue from the PR with this lane's records as the baseline; nothing in this lane depends on it.

## Update System

- `pyproject.toml`: optional extra `classification-local = ["onnxruntime>=1.25.0", "tokenizers>=0.21"]`; `uv lock` regenerated in the same commit. `/update` runs `uv sync --all-extras --frozen` (`scripts/update/deps.py:136`), so every fleet machine installs it at its next update; no `/update` skill change for the dependency.
- Weights: `scripts/download_local_encoder_models.py` and `scripts/update/local_encoder.py::ensure_models`, wired in `scripts/update/run.py` as the step numbered after the last existing 3.x step at build time (3.15 on today's main, since 3.13 and 3.14 are the Redis durability and replication steps) and placed in the code after the kokoro (3.11) and ffmpeg (3.12) pair, idempotent (skips present files whose sha256 matches, re-fetches on mismatch), non-fatal (warning in the update result). Cache dir `~/.cache/valor-encoder/` shared across worktrees; `LOCAL_ENCODER_MODELS_DIR` overrides it, read in code like `KOKORO_MODELS_DIR` (no `.env.example` entry, no settings field).
- Heads ship in the repo under `agent/llm/backends/heads/`, so a `git pull` is the propagation; no migration, no Popoto change, no new env key.
- Services: `./scripts/valor-service.sh restart` after merge (the bridge and worker import the wrapper); `/update` already does this. Doctor's `local_encoder` row is the post-update check.
- If the lane ends in the rejection exit, none of the above ships and the update system is unchanged.

## Agent Integration

- No new `[project.scripts]` entry. The runner stays `python -m tools.classification_eval` (developer tooling); records reach the agent through `valor-improve case show 1ec40086ca1d422e90ef747775ff7f64` and `valor-improve investigation list --case …`, which exist.
- The bridge, the worker, and the reflection jobs reach the leg through `agent.llm.run_typed` and the router; no direct import of the leg anywhere outside `agent/llm/wrapper.py` and the runner's arm.
- Integration tests: `tests/unit/test_llm_router_eligibility.py` (a client-keyed message through every `LOCAL_ENCODER` site reaches the Anthropic leg; a `valor` message with `gh` unavailable reaches the encoder leg); `tests/unit/test_llm_wrapper.py` (encoder leg error → Anthropic fallback inside the budget with both log lines); `tests/integration/test_bridge_routing_project_key.py` is unchanged and still proves the key reaches the routing classifiers.
- Live evidence: after `/update` on the landing host and one inbound `valor` message, `logs/bridge.log` carries `llm_route site=<landed site> backend=local_encoder` (Verification, manual row).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/local-encoder-classifier.md`: the leg (embedding model, per-site head, the output-type shape a landed site must have, `system` ignored by the leg and carried for the fallback), the fit protocol (digest split, held-out record, `fit` provenance with `landed`, the staged versus served head paths, `--land` as the only write to the served path, the head-run-id audit rule over the latest landed record), the precheck gate and its margin, the text-first composition rule for context-bearing sites, the `confidence=` token on `llm_route` and the grep that reads a site's score distribution after deploy, the rejection of zero-shot GLiClass with the plan-time numbers, the per-site outcome table for this lane (including `precheck_below_bar` rows), and a named follow-up: a scheduled measure-only re-comparison of each landed site against fresh real messages (its own issue; it needs reference spend and a timer, neither built here).
- [ ] Update `docs/features/llm-task-taxonomy.md`: the `backend` field row (`Backend.LOCAL_ENCODER`), the Router Rules table (the local-encoder rule ahead of the Ollama rules, renumbered), the Site Table rows for every landed site (backend `local_encoder`, the record id), the Acceptance Bar section (a fitted head is measured on the held-out split only; the `fit` block; the audit's head-provenance rule), and a "Lane B Outcome" section beside "Lane A Outcome" with the per-site numbers.
- [ ] Update `docs/features/nonharness-llm-wrapper.md`: the third leg under `agent/llm/backends/`, `default_sdk_timeout` for `LOCAL_ENCODER`, and the note that the encoder leg has no request timer (CPU work bounded by the 512-token window) and honors the deadline re-check only.
- [ ] Update `docs/infra/llm-task-routing.md`: a "Local encoder weights" section (model id, revision, file checksums, `LOCAL_ENCODER_MODELS_DIR`, `scripts/download_local_encoder_models.py`, the `/update` step, what doctor reports), the `llm_route ... backend=local_encoder` grep, and the rollback lever for an encoder landing (the one-word `backend` edit; no daemon to stop).
- [ ] Update `docs/features/config-timeout-catalog.md`: `TIMEOUTS__LOCAL_TYPED_HARD_S` is also the encoder leg's default budget for the fallback deadline check.
- [ ] Update `docs/features/local-model-policy.md`: the local encoder joins granite as a local classification backend; which sites each serves.
- [ ] Add rows to `docs/features/README.md` for `local-encoder-classifier.md` and update the taxonomy and wrapper rows' one-line summaries (three legs).

### Inline Documentation
- [ ] Module docstring for `agent/llm/backends/local_encoder.py` stating the leg protocol as this leg meets it, the import-safety contract, the head file format, and the output-type shape rule.
- [ ] Each landed `LLMTask` declaration keeps its fail-safe comment and gains one line naming the head file and the record id.
- [ ] Head files carry their provenance inline (`site`, `classes`, `embedding_model`, `embedding_sha256`, `run_id`, `n_train`, `n_train_real`, `reference_model`, `created_at`).

## Success Criteria

- [ ] The zero-shot GLiClass candidate is recorded as rejected on case `1ec40086ca1d422e90ef747775ff7f64`: one claim on a `probe` investigation carrying the spike-2 table, the model pin (`knowledgator/gliclass-base-v1.0` at `aa3ac24a…`, int8 sha256 `8eb7db6e…`), and this plan's URL (`valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64` shows it).
- [ ] `Backend.LOCAL_ENCODER` exists with a leg meeting lane A's protocol; `tests/unit/test_llm_backend_local_encoder.py` covers the success path (bool and `Literal` fields, `confidence` filled), the missing extra, the checksum failure, the missing head, each shape violation, `system` ignored, the deadline re-check, and the single load under concurrency; `tests/unit/test_llm_task_taxonomy.py` check 6 passes over the new leg; `tests/unit/test_llm_import_safety.py` passes with `onnxruntime` and `tokenizers` in the raising shim.
- [ ] `resolve` returns `Route(LOCAL_ENCODER, task.site, fallback=Route(ANTHROPIC, model))` for every `LOCAL_ENCODER` declaration with key `valor` and the Anthropic route for a client key or `None` (table-driven test over all declarations, `gh` monkeypatched unavailable); `email_cs.triage` still resolves to Anthropic for every key.
- [ ] `python -m tools.classification_eval --site <id> --fit --candidate local_encoder,anthropic` writes the staged head and a record with a `fit` block (`landed: false`) measured on the held-out split only and leaves the served head untouched; `--fit --land` is the only path that writes or deletes `agent/llm/backends/heads/<site>.json` (mutation-checked in review: remove the `land` guard, the served-head protection test must go red), it refuses before any spend without an `anthropic` candidate, and it copies the head only when `evaluate_bar` is empty for both the `local_encoder` and the `anthropic` arm on the same record (mutation-checked in review: gate the copy on `local_encoder` alone, the Anthropic-miss test must go red); the split is deterministic by digest and refuses under the minimums before any spend; `--precheck` skips a site under `bar - 0.10` with zero reference calls and one claim; `--preflight` reports the real-message count, `n_train`, and `n_train_real` against each site's need; `--audit` judges the latest record with `fit.landed: true` and exits 1 on a `LOCAL_ENCODER` landing whose head `run_id` is not that record's `fit.head_run_id` (mutation-checked in review: edit one head's `run_id`, the audit must go red).
- [ ] `llm_route site=<site> backend=<backend> elapsed_ms=<int> confidence=<score>` is logged for every result that carries a `confidence` attribute, on either leg; a result without one logs the lane A line unchanged (`tests/unit/test_llm_wrapper.py`).
- [ ] Every landed site: `backend=Backend.LOCAL_ENCODER`, the call restructured to `(text, system=instructions)` with the composition function shared by the runner row, a committed head whose classes equal the output type's closed set (`tests/unit/test_classifier_heads.py`), a record on the case with `fit.landed: true`, whose `local_encoder` arm clears every criterion with `contended: false` and `n_real` at or above half the minimum, whose `anthropic` arm (restructured shape) clears `evaluate_bar` on the same held-out split (the runner enforced both before copying the head), and whose id is in the site's row of `docs/features/llm-task-taxonomy.md` and in the landing commit message. Every site that misses stays where lane A landed it with the new record naming the failing criterion, and has no head file.
- [ ] Either at least one site is landed by the bar, or the rejection exit ran: no `LOCAL_ENCODER` member, leg, extra, weights script, update step, or doctor row remains, the runner's `--fit` and `--preflight` ship, and each site's MISS is on the case.
- [ ] `python -m tools.classification_eval --audit` exits 0 at the PR head on the landing host; the PR body carries the per-site landing summary (Key Elements) and the audit output.
- [ ] `python -m tools.doctor` shows the `local_encoder` row (extra, weights with checksums, heads) and fails it on a machine where a declared site would fall back on every call.
- [ ] `/update` fetches and verifies the weights (`scripts/update/run.py`, the step numbered after the last existing 3.x step, 3.15 on today's main, placed after the kokoro/ffmpeg pair) and installs the extra through the existing `--all-extras` sync; a fresh machine after `/update` passes the doctor row.
- [ ] If C13 lands, `INTENT_CONFIDENCE_THRESHOLD` is re-tuned on the record and the commit names the record id; `JOB_ROUTER_CONFIDENCE_THRESHOLD` is untouched.
- [ ] `tools/emoji_embedding.py` and its callers are untouched; `bridge/job_router.py` declares no `LOCAL_ENCODER`; no `torch`, `transformers`, or `gliclass` appears in `pyproject.toml`; no per-site switch appears in `config/settings.py`; the leg's module scope imports no third-party module and never downloads.
- [ ] Tests pass (`/do-test`); documentation updated (`/do-docs`); `python -m ruff check` and `python -m ruff format --check` clean.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (leg and runtime)**
  - Name: encoder-builder
  - Role: `Backend.LOCAL_ENCODER`, the leg, the head loader and shape rule, `config/models.py` pins, the download script and update step, the doctor row, the extra
  - Agent Type: builder
  - Domain: async/concurrency (the `to_thread` boundary, the loader lock, no `wait_for`)
  - Resume: true

- **Builder (runner)**
  - Name: runner-builder
  - Role: `local_encoder_arm`, `--fit`, `--preflight`, the `fit` record block, the audit generalization and head-provenance rule, the offline pre-check
  - Agent Type: builder
  - Resume: true

- **Builder (landings)**
  - Name: sites-builder
  - Role: the per-site loop on the landing host: preflight, fit, land or record the miss, the text composition functions, the call-site restructuring, thresholds
  - Agent Type: builder
  - Resume: true

- **Validator (leg, router, runner)**
  - Name: encoder-validator
  - Role: the mutation checks (checksum byte flip, head `run_id` edit, shape violations, `task=` removal on a restructured site), the "no change expected" list, the Verification table minus the doc rows
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: encoder-docs
  - Role: the Documentation section; the taxonomy table rows the parity test reads
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

**Tier 1: Core (default choices):** `builder`, `validator`, `code-reviewer`, `test-engineer`, `documentarian`, `plan-maker`, `frontend-tester`. Domain expertise is a `Domain:` line plus the matching rules from `DOMAIN_FRAMING.md`; `Explore` / `general-purpose` for recon.

## Step by Step Tasks

### 0. Record the GLiClass rejection
- **Task ID**: record-rejection
- **Depends On**: none
- **Validates**: `valor-improve investigation list --case 1ec40086ca1d422e90ef747775ff7f64` shows the claim
- **Informed By**: spike-1 (the ONNX path runs), spike-2 (agreement under the majority baseline on every site but C9)
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: false
- Open a `probe` investigation on the case through `tools.improvement_investigations.open_investigation` (uncertainty: "does zero-shot GLiClass clear any site's tier bar", decision affected: "the model behind Backend.LOCAL_ENCODER") and record one claim per spike-2 row plus the pin (model, revision, int8 sha256, tokenizer sha256, license Apache-2.0, retrieval date 2026-09-21), `url` this plan's GitHub path. The claim text names Tom's acceptance of the pivot (issue comment 5755701599) and the lineage rows he named (model revision 8 `1f161425`, the intake row on case `1ec40086`). No GLiClass code.

### 1. Backend member, leg, pins, weights script, update step, extra
- **Task ID**: build-encoder-leg
- **Depends On**: record-rejection
- **Validates**: `tests/unit/test_llm_backend_local_encoder.py` (create), `tests/unit/test_llm_tasks.py`, `tests/unit/test_llm_import_safety.py`, `tests/unit/test_llm_task_taxonomy.py -k hotfix_1055`
- **Informed By**: spike-3 (I/O names, CLS pooling, L2 norm, 0.05 s load, 512 truncation), Research finding 3 (pins and checksums), finding 4 (wheels)
- **Assigned To**: encoder-builder
- **Agent Type**: builder
- **Parallel**: true (with Task 2)
- `Backend.LOCAL_ENCODER = "local_encoder"`; rewrite the router docstring's reserved line and `tasks.py`'s "Lane B" line for the member that landed.
- `config/models.py`: `LOCAL_ENCODER_MODEL`, `LOCAL_ENCODER_REVISION`, `LOCAL_ENCODER_FILES` (name → sha256), `LOCAL_ENCODER_DIM`, `LOCAL_ENCODER_MODELS_DIR` resolution (env override, default `~/.cache/valor-encoder/`).
- `agent/llm/backends/local_encoder.py` per Data Flow step 5 and Key Elements: `_load_runtime()` (memoized, `threading.Lock`, imports inside, checksum verification, `intra_op_num_threads=4`, `enable_truncation(512)`), `_load_head(site)` (memoized, shape and dimension checks), `_shape(output_type, head)` (the one-closed-set-field rule; bool ↔ `"True"`/`"False"`), `_embed(text)`, `_classify(text, head)`, and `call(...)` with `bound_to_deadline` first and `asyncio.to_thread` around the CPU work. Module scope stdlib only.
- `agent/llm/backends/__init__.py::default_sdk_timeout`: the `LOCAL_ENCODER` branch (`local_typed_hard_s`); docstring updated. `agent/llm/wrapper.py::_LEGS` entry, and the ` confidence=%.3f` suffix on the `llm_route` line (`wrapper.py:300`) when `getattr(result, "confidence", None)` is not `None`.
- `scripts/download_local_encoder_models.py` (the kokoro script's shape, plus sha256 verification and `.part` cleanup on mismatch) and `scripts/update/local_encoder.py::ensure_models`, wired in `scripts/update/run.py` as the step numbered after the last existing 3.x step (3.15 on today's main; comment `# Step 3.15: Local encoder weights ...`), placed in the code after the ffmpeg step; `pyproject.toml` extra `classification-local`; `uv lock`.
- `intra_op_num_threads=min(4, os.cpu_count() or 1)` in `_load_runtime`.
- Tests: the shared fake runtime (`tests/helpers/llm_fakes.py` gains `FakeEncoderRuntime` with a call counter and a scripted vector), every Failure Path row for the leg, the concurrent-load test, a test that the wrapper's fallback runs on the leg's `LLMCallError` with both log lines, the `confidence=` token test and its absence for a result without the attribute.

### 2. Runner: arm, fit, preflight, record block, audit rule
- **Task ID**: build-runner-fit
- **Depends On**: record-rejection
- **Validates**: `tests/unit/test_classification_eval.py` (the rows in Test Impact and Failure Path)
- **Informed By**: spike-3 (the fit settings and the CV numbers), Research finding 5 (the audit log is not a corpus)
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: true (with Task 1; the arm imports the leg by name, so the two builders agree the module path up front)
- `arms.py::local_encoder_arm(site_id)`; the builders dict in `__main__.py` and the every-member test.
- `tools/classification_eval/fit.py`: `PRECHECK_MARGIN = 0.10`, `split_by_digest(inputs, minimum_n) -> (held_out, train)` (pure; the rule in Data Flow fit step 2), `label_training_split(reference, train, site) -> list[tuple[Input, str]]`, `fit_head(vectors, labels, classes) -> Head` (numpy, the spike's settings, deterministic), `write_head(path, head)` (atomic), `staged_head_path(site, run_id)` under `data/classification_eval/heads/` (gitignored), `served_head_path(site)` under `agent/llm/backends/heads/`, and `run_fit(..., land: bool = False)` that refuses under the minimums, refuses when `is_contended()`, applies the precheck gate when a precheck number is supplied, labels, embeds through the leg's `_embed`, fits, writes the staged head, prints the training-split sanity line, calls `compare` on the held-out split with the candidate arm reading the staged head, stamps `fit` (including `landed` and `miss_arms`) on the record, and only when `land` is true applies the landing rule: `evaluate_bar(record, "local_encoder")` and `evaluate_bar(record, "anthropic")` both empty copies the staged head to the served path; anything else deletes the served head. With `land=True` and no `anthropic` candidate, `run_fit` raises before labeling (exit 2).
- `local_encoder_arm(site_id, *, head_path=None)`: `None` reads the served head (the audit's and the live path's view); a path reads that file (the fit path's view).
- `--fit`, `--land`, `--precheck`, `--preflight` (the real-message count from `real_messages(2000)` against each site's need: half-minimum real for the held-out split, plus `n_train` and `n_train_real` per site from `split_by_digest` on the draw; exit 1 when the routing sites cannot be met), `ComparisonRecord.fit`, `render_report`'s `fit` line (`fit: n_train=… n_train_real=… head=<run_id> landed=<bool>`), `latest_record(site, where=...)`, `_audit_row` generalized to every non-Anthropic backend plus the head-provenance rule over the latest landed record.
- Tests per Test Impact; the split test seeds two different draws and asserts the held-out set is a function of the inputs alone; the fit test asserts bit-identical `W` on a re-run; the served-head protection test, the both-arms landing gate tests (Anthropic miss, encoder miss, missing `anthropic` candidate), the shared-`fit_head` test for `--precheck`, and the precheck-gate test from Failure Path.

### 3. Doctor row
- **Task ID**: build-doctor-row
- **Depends On**: build-encoder-leg
- **Validates**: `tests/unit/test_doctor.py -k local_encoder`
- **Assigned To**: encoder-builder
- **Agent Type**: builder
- **Parallel**: false
- `tools/doctor.py::_check_llm_routing`: the `local_encoder` row (extra importable; each pinned file present with a matching sha256; one head per declared `LOCAL_ENCODER` site) with the `fix` naming `uv sync --all-extras` or the download script; passes when no site declares the backend.

### 4. Validate the leg and the runner
- **Task ID**: validate-leg-runner
- **Depends On**: build-encoder-leg, build-runner-fit, build-doctor-row
- **Validates**: the Verification rows for tests, lint, format, module scope, `wait_for`, import safety, runner, doctor
- **Assigned To**: encoder-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the "no change expected" list (every lane A test file in Test Impact that says "no change") and `scripts/pytest-clean.sh tests/unit/`; mutation-check: flip one byte of a weights file under a temp dir (the leg must refuse), edit a head's `run_id` (the audit must go red), remove `deadline` handling (the fallback deadline test must go red), add an `asyncio.wait_for` in the leg (check 6 must go red). Paste the four red outputs into the PR.

### 5. Offline pre-check on lane A's records (free)
- **Task ID**: precheck-sites
- **Depends On**: build-runner-fit
- **Validates**: a table in the PR body (site, CV agreement, majority baseline, bar) for every site with a lane A record on the build machine
- **Informed By**: spike-3 (the same procedure, now through the repo's own `fit_head` and `_embed`)
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: true (with Task 4)
- `python -m tools.classification_eval --precheck` (a fit-module subcommand that reads each site's latest record, pairs `reference.labels[:n_fixture]` with `site.fixtures()`, and prints five-fold CV agreement through the real `_embed` and `fit_head`, beside the majority baseline, the bar, and `bar - PRECHECK_MARGIN`): zero spend, runs on any machine with the lane A records. The precheck measures through the same `fit_head` (the multinomial LR) that `run_fit` fits with, never a separate kNN or other routine, so the gate's number is the head that would serve; a test monkeypatches `fit.fit_head` and asserts both `--precheck` and `run_fit` call it. Spike 3's kNN column was a plan-time comparison only and has no counterpart in the runner. Its output fixes the per-site order for Task 8 (highest agreement relative to bar first), marks each site `fit` or `precheck_below_bar`, and is the first evidence in the PR body. It is re-run on the landing host before Task 8 so the gate reads that host's records.

### 6. Documentation skeleton
- **Task ID**: document-skeleton
- **Depends On**: validate-leg-runner
- **Validates**: `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q -k parity`
- **Assigned To**: encoder-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Every Documentation item except the per-site rows and the outcome table, which Task 9 fills.

### 7. Preflight on the landing host
- **Task ID**: preflight-host
- **Depends On**: validate-leg-runner
- **Validates**: `python -m tools.classification_eval --preflight` exit 0 for at least the 50-minimum sites
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- On the Valor host owning the `valor` bridge, after `/update` (extra and weights present; doctor row green), stop the services (`./scripts/valor-service.sh stop`) and run the preflight. Paste the output into the PR. If the routing sites cannot be met, say so in the PR and run Task 8 on the sites the store supports.

### 8. Fit and land, per site
- **Task ID**: build-land-sites
- **Depends On**: preflight-host, precheck-sites
- **Validates**: per site, the runner's report (PASS for `local_encoder` and the tier agreement for `anthropic`), `tests/unit/test_classifier_heads.py`, the site's own test file, `python -m tools.classification_eval --audit`
- **Informed By**: Task 5's precheck table (the measured order and the per-site `fit` / `precheck_below_bar` mark); spike-3 supplied only the provisional order (C1, C9, then C15, C8, C10, C7, C14, C11, C13, C6, with C2 to C5 under the gate on today's numbers)
- **Assigned To**: sites-builder
- **Agent Type**: builder
- **Parallel**: false
- First, the caller census: `grep -ln "<site function>" tests/unit/*.py` for every candidate site, pasted into the PR (C1 is `tests/unit/test_routing.py`).
- Sites marked `precheck_below_bar` by Task 5's table on the landing host (agreement under `bar - 0.10`) skip the fit: no reference-arm calls, no record; one claim on the case with the precheck number through `record_claims`, and a row in the PR body and the outcome table with that reason.
- For each remaining site in the precheck order, boxed at half a build day: add the text composition function beside the declaration if the site carries context and point the row's `candidate_prompt` at it; `python -m tools.classification_eval --site <id> --fit --land --candidate local_encoder,anthropic --save-inputs data/classification_eval/<id>.jsonl` (`--reference-model paid` for C15 when the free route is throttled, as lane A did); the runner decides the landing (both arms must clear `evaluate_bar`; the report prints `landed=true` or the arm and criterion that missed). On `landed=true`: the runner has copied the staged head to the served path; set `backend=Backend.LOCAL_ENCODER`, restructure the call to `(text, system=instructions)`, keep the fail-safe byte-identical, update the site's tests per Test Impact, commit the head with the record id and head run id in the message. On `landed=false`: the runner has removed any served head for the site; leave the declaration and note `fit.miss_arms` and the failing criterion for the PR. Iterate only on the three levers (composition, `real_limit`, the one model swap); a swap re-pins the embedding in `config/models.py` and refits every landed head (`--land`) in the same commit.
- If C13 lands, re-tune `INTENT_CONFIDENCE_THRESHOLD` on the held-out softmax scores and commit with the record id.
- If no site lands, run the rejection exit (Technical Approach): delete the backend end to end in one commit, keep `--fit`, `--preflight`, `--precheck`, and record each MISS on the case.

### 9. Documentation: per-site rows and the outcome table
- **Task ID**: document-outcome
- **Depends On**: build-land-sites, document-skeleton
- **Validates**: `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q -k parity`, the three doc rows in Verification
- **Assigned To**: encoder-docs
- **Agent Type**: documentarian
- **Parallel**: false
- The site table rows, the "Lane B Outcome" section with the per-site numbers (both candidate arms, `n_train`, `n_train_real`, head run id, record id), and the rejection paragraph for GLiClass.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-outcome
- **Validates**: the full Verification table and every Success Criteria checkbox
- **Assigned To**: encoder-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table on the landing host (the audit reads that host's Redis); confirm Success Criteria; the `task=` mutation on one restructured site (check 1 of the taxonomy test must go red); generate the report and the PR body's landing summary.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/ -x -q` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Encoder leg tests pass | `scripts/pytest-clean.sh tests/unit/test_llm_backend_local_encoder.py tests/unit/test_classifier_heads.py -q` | exit code 0 |
| Router and eligibility tests cover the new member | `scripts/pytest-clean.sh tests/unit/test_llm_router.py tests/unit/test_llm_router_eligibility.py tests/unit/test_llm_tasks.py -q` | exit code 0 |
| Doc/code parity and the hotfix #1055 walk over the new leg | `scripts/pytest-clean.sh tests/unit/test_llm_task_taxonomy.py -q` | exit code 0 |
| Import safety covers onnxruntime and tokenizers | `scripts/pytest-clean.sh tests/unit/test_llm_import_safety.py -q` | exit code 0 |
| No third-party import at the leg's module scope | `/usr/bin/grep -c "^import onnxruntime\|^import tokenizers\|^import numpy\|^from onnxruntime\|^from tokenizers\|^from numpy" agent/llm/backends/local_encoder.py` | match count == 0 |
| No `asyncio.wait_for` in any backend leg | `/usr/bin/grep -c "wait_for" agent/llm/backends/local_encoder.py` | match count == 0 |
| `agent.llm` imports without the extra | `.venv/bin/python -c "import sys; sys.modules['onnxruntime']=None; sys.modules['tokenizers']=None; import agent.llm, agent.llm.backends.local_encoder"` | exit code 0 |
| Runner tests pass (fit, preflight, audit provenance) | `scripts/pytest-clean.sh tests/unit/test_classification_eval.py -q` | exit code 0 |
| Every backend member has a runner arm builder | `scripts/pytest-clean.sh tests/unit/test_classification_eval.py -q -k "arm_builders_cover_every_backend"` | exit code 0 |
| Doctor reports the encoder row | `scripts/pytest-clean.sh tests/unit/test_doctor.py -q -k "local_encoder"` | exit code 0 |
| Every landed site's declared backend matches its record and its head | `.venv/bin/python -m tools.classification_eval --audit` | exit code 0 |
| Every committed head matches a declared `LOCAL_ENCODER` site and the pinned embedding checksum | `scripts/pytest-clean.sh tests/unit/test_classifier_heads.py -q` | exit code 0 |
| No head without a landed site (no orphan weights) | `.venv/bin/python -c "import json,pathlib,sys; from agent.llm.tasks import Backend, declared_sites; landed={d.task.site for d in declared_sites() if d.task.backend is Backend.LOCAL_ENCODER}; heads={p.stem for p in pathlib.Path('agent/llm/backends/heads').glob('*.json')}; sys.exit(0 if heads==landed else 1)"` | exit code 0 |
| Anti-criterion: no `torch`, `transformers`, or `gliclass` dependency | `/usr/bin/grep -c "torch\|transformers\|gliclass" pyproject.toml` | match count == 0 |
| Anti-criterion: no per-site backend switch in settings | `/usr/bin/grep -c "_sites\|local_encoder\|classification_local" config/settings.py` | match count == 0 |
| Anti-criterion: the leg never downloads weights | `/usr/bin/grep -c "urlopen\|httpx\|requests\.\|hf_hub_download" agent/llm/backends/local_encoder.py` | match count == 0 |
| Anti-criterion: C12 is not a `LOCAL_ENCODER` site | `/usr/bin/grep -c "Backend.LOCAL_ENCODER" bridge/job_router.py` | match count == 0 |
| Anti-criterion: the emoji embedding path is untouched (#3422 owns it) | `git diff main --stat -- tools/emoji_embedding.py agent/constants.py tools/react_with_emoji.py \| wc -l` | match count == 0 |
| Weights script verifies checksums | `/usr/bin/grep -c "sha256" scripts/download_local_encoder_models.py` | output > 0 |
| `/update` fetches the weights, as a 3.x step after ffmpeg (3.12) | `/usr/bin/grep -c "local_encoder.ensure_models" scripts/update/run.py` | output > 0 |
| The weights step is numbered after every existing 3.x step | `/usr/bin/grep -n "# Step 3\." scripts/update/run.py \| tail -1 \| /usr/bin/grep -c "local encoder"` | output > 0 |
| Extra declared | `/usr/bin/grep -c "^classification-local = \[" pyproject.toml` | output > 0 |
| Feature doc exists | `test -f docs/features/local-encoder-classifier.md` | exit code 0 |
| Feature index updated | `grep -c "local-encoder-classifier.md" docs/features/README.md` | output > 0 |
| Infra doc names the weights | `grep -c "download_local_encoder_models" docs/infra/llm-task-routing.md` | output > 0 |
| Taxonomy doc carries the new rule | `grep -c "LOCAL_ENCODER" docs/features/llm-task-taxonomy.md` | output > 0 |

**Live evidence (manual, deployed bridge):** after `/update` has run on the `valor`-owning host (the extra installed, the weights fetched, services restarted) and one inbound message in a `valor` room: `grep -c "llm_route site=<landed site> backend=local_encoder" logs/bridge.log` is above 0 for the site the PR names, and `grep -c llm_fallback logs/bridge.log` is unchanged across that message. After a day of traffic, `grep -o "llm_route site=<landed site> backend=local_encoder .*confidence=[0-9.]*" logs/bridge.log` gives the site's score distribution; a cluster near `1/k` (k classes) is the drift signal that calls for the measure-only re-fit. Needs a deployed bridge and a human reading the counts, so it stays outside the table.

## Critique Results

War room round 2 (2026-09-21, re-critique of the revised plan; FULL depth, independent roster of 3 critics, plus the structural pass). Every round-1 concern and nit was checked against the section its Addressed By column names: four of the five concerns are resolved; the Simplifier's precheck-gate concern and the structural /update-step nit are each resolved in mechanism and carry one leftover inconsistency, filed below as fresh findings. Verdict: READY TO BUILD (with concerns).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | Two accounts of what gates the `--land` write. Data Flow fit step 6 copies the staged head to the served path "on PASS for the `local_encoder` arm" alone; Risk 3 says a site whose Anthropic arm misses the tier bar "does not land" but places that check in "the reviewer's rule"; Task 8 reads "on PASS for `local_encoder` with the `anthropic` arm at or above the tier bar: the runner has copied the staged head", so a run where `local_encoder` passes and the restructured-shape `anthropic` arm misses has already copied the head and stamped `fit.landed: true` with nothing in the CLI to stop it, and no Failure Path or Success Criteria test covers that case. | pending | In `tools/classification_eval/fit.py::run_fit`, after `compare(...)` returns the record, gate the `land` branch on both candidate arms passing `evaluate_bar` on the same held-out split (`local_encoder` PASS and `anthropic` PASS), not on `local_encoder` alone; when the `anthropic` arm misses, treat the run as a MISS for landing (delete the served head if one exists, `fit.landed: false`) and name the reason in `render_report`. Add the test to Failure Path and Success Criteria with its mutation (force the `anthropic` arm to MISS while `local_encoder` passes; `--land` must leave the served path untouched). Rewrite Risk 3 and Data Flow step 6 so the runner, not the reviewer, holds the rule. |
| CONCERN | Scope & Value (Simplifier); Risk & Robustness | The precheck gate's worked example contradicts its own rule. The rule skips a site under `bar - 0.10` (0.85 for a `high` site); Spike 3's LR agreement for C2 is 0.824 against a 0.95 bar, so C2 is `precheck_below_bar` by the formula, yet the same sentence lists "C1, C2, and C9 would fit". The example holds only if the kNN-5 column (C2 0.867) is used instead of the LR head that `fit_head` and `--precheck` run. Two critics reached this independently. | pending | Prose fix in Technical Approach (precheck gate bullet): on today's numbers C1 (0.894 vs 0.85) and C9 (0.825 vs 0.75) fit; C2 (0.824 vs 0.85), C3, C4, and C5 skip. State in Task 5 that `--precheck` measures through the same `fit_head` (LR) routine `run_fit` uses, never a separate kNN routine, and add a test asserting the two share that call. No code depends on the site list here (Task 5's live run recomputes it), so the fix is the sentence plus the test; the note exists so a builder reading the example does not treat a live `precheck_below_bar` on C2 as a tool bug. |
| CONCERN | History & Consistency (Consistency); structural pass | Architectural Impact, Update System, and Task 1 place the weights step "in the code after the kokoro (3.11) and ffmpeg (3.12) pair" / "after the ffmpeg step", which is ahead of the existing 3.13 (Redis durability, `scripts/update/run.py:2012`) and 3.14 (Redis replication, `:2044`) blocks, while the Verification row `grep -n "# Step 3\." scripts/update/run.py \| tail -1 \| grep -c "local encoder"` passes only when the new comment is the last `# Step 3.` match by file position. Built as written, the file reads 3.12, 3.15, 3.13, 3.14 and the row fails on a correctly numbered step. | pending | Change the placement wording in Architectural Impact, Update System, Success Criteria, and Task 1 to "immediately after the last existing 3.x block (today: after Step 3.14, Redis replication)" so file order and numbering agree and the `tail -1` row is a real check; keep the Verification row as is. (The alternative, a numeric-max parse, is weaker: `# Step 3.95` at `run.py:1948` already sits out of numeric order, so file position is the property worth checking.) |
| NIT | Risk & Robustness (Adversary) | Race 2 and fit step 6 describe the staged-versus-served split but name no recovery for a `--land` run that crashes between the served-head rename and `write_record`. The next `--audit` goes red (the on-disk head's `run_id` matches no landed record), which is the right direction, but the operator step is unwritten. | pending | One sentence in Race 2 and the infra doc: the fit is deterministic, so re-running the same `--site <id> --fit --land` command reproduces the identical head and record and clears the audit; `git checkout -- agent/llm/backends/heads/<site>.json` restores the committed head when the site should not have been touched. |
| NIT | Structural pass (cross-references) | No-Gos lists "[EXTERNAL] Running Tasks 7 to 9 on the Valor host that owns the `valor` bridge" as out of scope, while Tasks 7 to 9 are planned work in Step by Step Tasks and Tom's answer to Open Question 3 assigns them to that host. The intent is that this Air session cannot execute them, which is a hand-off, not an exclusion. | pending | Reword the No-Go to what is out of scope for this session: "[EXTERNAL] Executing Tasks 7 to 9 from this MacBook Air (12 real messages); they run on the Valor host per Open Question 3 and the hand-off in Technical Approach." |
| NIT | Structural pass | Architectural Impact counts the transitive install as "two transitive packages, `huggingface-hub` and `tqdm` ... four packages" fleet-wide. `uv pip install --dry-run -p .venv/bin/python onnxruntime tokenizers` on the pinned interpreter today reports "Would install 7 packages": `tokenizers`, `huggingface-hub`, `tqdm`, `filelock`, `fsspec`, `hf-xet`, and a `click` bump from the locked 8.3.2 to 8.5.0. | pending | Name all five transitive packages in Architectural Impact and have Task 1 check the `uv lock` diff for a `click` version change (repo CLIs use `click`); if the lock bumps it, say so in the PR body so the reviewer sees the fleet-wide effect. The leg still imports none of them. |

### Round 1 (2026-09-21T05:24Z, resolved by the revision applied 2026-09-21T05:33:57Z)

Verdict: READY TO BUILD (with concerns). Every concern and nit below is folded into the section its Addressed By column names; round 2 verified each one against that section.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | `--fit` has one code path for a first landing attempt and for a re-check of an already-landed site: fit step 4 writes `agent/llm/backends/heads/<site>.json` (the served path) before the held-out measurement runs, and a MISS deletes the head. A diagnostic re-fit of a landed site therefore overwrites, and on MISS deletes, the committed head that serves production, and writes a record that turns `--audit` red for that site until the file is restored from git. | Data Flow fit steps 4 to 7 (staged head under `data/classification_eval/heads/`, `--land` gates the served path, `fit.landed`, audit over the latest landed record); Key Elements (Runner); Race 2; Failure Path (served head protection); Test Impact (audit cases); Tasks 2 and 8; Success Criteria; Flow | In `tools/classification_eval/fit.py::run_fit`, write the fitted head to a staging path (`data/classification_eval/heads/<site>.<run_id>.json`) and gate the copy into `agent/llm/backends/heads/<site>.json` and the head-delete-on-MISS branch behind an explicit `land: bool` parameter (default `False`, wired from a `--land` CLI flag). `--fit --site <id>` alone measures and prints `render_report`; `--fit --site <id> --land` is required before the served path is written or deleted, so a drift check on a landed site can never remove its serving head. The comparison record is still written on every run (it is the measurement); the audit's head-provenance rule compares the committed head's `run_id` to the latest record's `fit.head_run_id`, so a measure-only re-run on a landed site must also be reflected: either the audit reads the latest record whose `fit.landed` is true, or the record carries `fit.landed: bool` and the audit skips measure-only records. |
| CONCERN | Risk & Robustness (Operator) | The only post-landing signal is a one-time manual grep for `llm_route ... backend=local_encoder` plus an unchanged `llm_fallback` count. Risk 2 concedes a site can clear the held-out bar and misbehave on live traffic, but the `confidence` score the leg computes on every call is never logged, so a live regression under the tier bar produces no signal short of a human noticing wrong routing. | Data Flow serving step 6 (`confidence=<score>` on `llm_route`, backend-neutral); Key Elements (Wrapper and timers); Task 1; Test Impact (`test_llm_wrapper.py`); Success Criteria; Verification live evidence (the score-distribution grep); Documentation (follow-up issue named in the feature doc) | Append `confidence=<score>` to the wrapper's existing `llm_route site=<site> backend=<backend> elapsed_ms=<int>` line (`agent/llm/wrapper.py`) whenever the returned instance carries a `confidence` attribute (`getattr(result, "confidence", None)`), so a post-deploy grep shows the score distribution per site. A scheduled re-comparison against fresh real messages is a follow-up issue (it needs reference spend and the measure-only mode above), named in the feature doc, not built in this lane. |
| CONCERN | Scope & Value (Simplifier) | Every one of the 14 ordered sites is boxed at half a build day of fit-and-land iteration plus reference-arm spend, although Task 5's offline precheck reproduces each site's CV agreement for free before Task 8 starts and Spike 3 already shows C3 (0.574 vs 0.95), C4 (0.638 vs 0.95), and C5 (0.450 vs 0.90, under its own majority baseline) 30 to 45 points under bar on fixtures alone, the gap size that rejected GLiClass without a build day. Nothing gates Task 8 on Task 5's number, so a builder pays Haiku labels and a half-day chasing sites the plan's own data predicts will miss. | Technical Approach (precheck gate, `PRECHECK_MARGIN = 0.10`, applied to today's numbers); Risk 1; Failure Path (precheck gate); Tasks 2, 5, and 8 (`precheck_below_bar` rows and claims); Success Criteria; Flow | Gate Task 8's per-site loop on Task 5's precheck: when `precheck_agreement < bar - MARGIN` (margin 0.10, stated in the plan), skip the fit for that site (zero reference-arm calls, no comparison record), record the precheck number as a claim on the case through the same `record_claims` path Task 0 uses, and list the site in the PR body and the "Lane B Outcome" table with reason `precheck_below_bar`. The gate reuses the fixture-only signal Task 0 already trusted to reject GLiClass for the whole backend, applied per site. |
| CONCERN | History & Consistency (Archaeologist) | The frontmatter `last_comment_id: 5745557381` points at the 2026-09-19 upstream-change notice; Tom's 2026-09-21T05:11:51Z comment (id 5755701599, `IC_kwDOEYGa088AAAABVxEJXw`) is the latest and answers all three Open Questions, yet the plan still poses them as open and Task 0 still carries a "confirm Open Question 1, stop if close" branch. | Frontmatter `last_comment_id: 5755701599`; Open Questions (the three answers quoted verbatim); Problem and Freshness Check (acceptance noted, lineage rows `1f161425` and the intake row added); Task 0 (stop branch removed, lineage named in the claim); Appetite; the OQ1 success criterion removed | Set `last_comment_id` to `5755701599`, fold the answers into the Open Questions section verbatim (OQ1: accept the pivot; OQ2: reference-arm spend for training labels under one dollar per site is inside charter §8, proceed and meter it; OQ3: Tasks 7 to 9 run on the Valor host that owns the `valor` bridge, this Air runs Tasks 0 to 6 only), delete Task 0's "if close, stop" branch and the "Open Question 1 is answered before Task 1 starts" success criterion, and note the RSI lineage rows Tom named (model revision 8 `1f161425`, intake row on case `1ec40086`). |
| CONCERN | History & Consistency (Consistency) | Test Impact names `tests/unit/test_routing_classifiers.py` as C1's test file; that path does not exist on main. C1's tests (`classify_needs_response`, `bridge/routing.py:811`) live in `tests/unit/test_routing.py`. | Test Impact (C1 is `tests/unit/test_routing.py`, verified by grep on the baseline; caller census before Task 8); Task 8 (census as the first step) | In Test Impact and Task 8, name `tests/unit/test_routing.py` for C1; when C1's call is restructured to `(text, system=instructions)`, update the prompt-string assertions there. Before Task 8 the sites-builder runs `grep -ln "<site function>" tests/unit/*.py` for each candidate site and pastes the file list into the PR, per lane A's caller-census rule. |
| NIT | Risk & Robustness (Skeptic) | `intra_op_num_threads=4` and the 3 ms p95 at concurrency 4 that justify "no semaphore" were measured only on the 10-core M4 Air; the plan carries that number as the reason no concurrency guard is needed on every fleet machine. | Technical Approach (Concurrency: `min(4, os.cpu_count() or 1)`, no-semaphore decision confirmed per host by the landing record's `p95_c4`); Task 1 | Say that the no-semaphore decision is confirmed per host by the landing host's own latency pass (`p95_c4` in the record), and size `intra_op_num_threads` as `min(4, os.cpu_count() or 1)`. |
| NIT | Scope & Value (User) | The landing order is credited to Spike 3, but Spike 3 measured six sites (C1 to C5, C9); the other eight sites' positions in the order have no measured backing in the plan. | Spike 3 impact (six sites measured, eight provisional); Technical Approach (Landing order); Task 5 (fixes the order); Task 8 (Informed By) | State that the order for the eight unmeasured sites is provisional and that Task 5's precheck table, not Spike 3, fixes the per-site order before Task 8 starts. |
| NIT | Structural pass | The plan wires the weights fetch as `/update` "step 3.13 after kokoro", but `scripts/update/run.py` already numbers Redis durability as Step 3.13 and Redis replication as Step 3.14 (kokoro is 3.11, ffmpeg 3.12). | Architectural Impact, Update System, Success Criteria, Task 1, and Verification (the step numbered after the last existing 3.x step, 3.15 on today's main, placed after ffmpeg; a Verification row checks it is the last 3.x step) | Number the new step after the last existing 3.x step in `scripts/update/run.py` at build time (3.15 on today's main) and place it after the kokoro/ffmpeg pair in the code; fix the number in Update System, Success Criteria, and the Verification row. |
| NIT | Structural pass | `uv pip install --dry-run onnxruntime tokenizers` on the pinned interpreter also pulls `huggingface-hub` and `tqdm` (transitive dependencies of `tokenizers`); Architectural Impact lists only `onnxruntime`, `tokenizers`, and `numpy`. | Architectural Impact (`huggingface-hub` and `tqdm` named; neither imported by the leg) | Name the two transitive packages in Architectural Impact so the reviewer sees the full fleet-wide install, and confirm neither is imported anywhere in the leg (the anti-criterion grep covers the leg's module scope already). |
| NIT | Structural pass | The training-set arithmetic is implicit: with `ROUTING_FIXTURES = 188` and a held-out split of `minimum_n` (200) holding at least 100 real inputs, a routing site's training split is `188 + n_real - 200 = n_real - 12` real-heavy examples, so "about 700 training labels" assumes roughly 700 real messages on the landing host; at the preflight minimum (100 real) training is 88 fixtures and 0 real, smaller than the 150-example folds Spike 3 measured. | Risk 1 (`n_train = n_fixtures + n_real - minimum_n`, the 100-real boundary, the curve bounded by the host's real count); Key Elements (`--preflight` prints `n_train` and `n_train_real`); Failure Path (preflight boundary test); Task 2 | Have `--preflight` print `n_train = n_fixtures + n_real - minimum_n` (and `n_train_real`) per site beside the held-out need, and state in Risk 1 that the learning curve is bounded by the host's real-message count, not by the spike's 150. |

---

## Open Questions

All three were put to Tom on the issue (comment 5754647523) and answered by Tom on 2026-09-21T05:11:51Z (comment 5755701599). The questions stay here with the answers quoted verbatim so the decision that changed this lane's model is visible in the plan itself.

1. **The model behind lane B changes on evidence: confirm or close.** The issue's zero-shot GLiClass candidate measures under the majority-class baseline on every site but C9 (Spike 2), so the issue's own rejection exit fires at plan time. This plan builds the local backend the evidence supports instead: a pinned local embedding model plus a per-site linear head fit on the reference arm's labels, measured on a held-out split by the same bar (Spike 3: C1 0.894 and C9 0.825 from fixtures alone, 1.4 ms per call). Same enum member shape, same leg protocol, same one-word landing, same runner, no `torch`, no daemon. The alternatives were to close #3420 with the rejection claim recorded (Task 0 alone), or to file the encoder fine-tuning lane instead (Rabbit Holes).
   **Answer (Tom, comment 5755701599):** "the pivot in plan 359cbfd7b is accepted. Lane B's local backend is bge-small-en-v1.5 int8 embeddings plus a per-site linear head fit on the reference arm's labels; GLiClass zero-shot is rejected on the plan-time measurement (under the majority-class baseline on 5 of 6 sites). Open Question 1: accept."
2. **Reference spend for training labels.** Each fit labels the training split with Haiku (about 700 calls per routing site, 300 per 50-minimum site, under one dollar per site at list price) in addition to lane A's held-out reference calls. This is inside the $10/day inference line and metered where lane A metered; is labeling a training split an acceptable use of the reference arm under charter §6 (it is a comparison against the current workflow, on more inputs)?
   **Answer (Tom, comment 5755701599):** "Open Question 2: reference-arm spend for training labels under one dollar per site is inside charter §8; proceed and meter it."
3. **The landing host.** The plan names "the Valor host that owns the `valor` bridge" as the machine whose memory store can satisfy `n_real`, and puts a preflight in front of every spend. Is there a host whose store holds at least 100 real inbound `valor` messages (the routing sites' held-out need)? If none does, the routing sites are out of reach for this lane by the bar's own rule, and the lane lands the 50-minimum sites only.
   **Answer (Tom, comment 5755701599):** "Open Question 3: the landing run (Tasks 7 to 9) goes on the Valor host that owns the `valor` bridge; the Air holds 12 real messages and runs only Tasks 0 to 6. RSI lineage: model revision 8 (1f161425) and intake row on case 1ec40086."
