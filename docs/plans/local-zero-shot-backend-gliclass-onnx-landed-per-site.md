---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-21
tracking: https://github.com/tomcounsell/ai/issues/3420
last_comment_id: 5745559073
---

# Local Encoder Classification Backend Behind Lane A's Router (Lane B of #3410)

## Problem

Lane A (#3410, PR #3524, merged at `4703bce23`) shipped the taxonomy, the router, the Anthropic and Ollama legs, the comparison runner, and a `classifier_comparison` record for every classification site. No candidate site moved to granite. Every inbound-shaped and outbound-shaped site (C1 to C10, C15) landed on `ANTHROPIC` with a record naming its failing criteria, and the records are precise about why: the build machine's memory store held 12 real `valor` messages against minimums of 25 and 100 (`n_real`); concurrency 4 serialized on that machine's GPU (`p95_c4`); and granite's agreement with Haiku fell under the tier bar on C1 to C6, C9, and C10. Tom's principles for this work (issue #3410 comment 5737918683) stand: local-first, no single external provider on a hot path, no transition phase, no default-off switches, the builder flips and revises until the PR is approved, and the comparison record against the site's current backend is the only argument for a landing (charter §6; low price is not evidence, §7).

This issue proposed a second local CPU backend, zero-shot GLiClass through int8 ONNX, on the premise that "a zero-shot encoder classifier can beat a 3B generative model on closed-set labels while staying on CPU". That premise was measurable at plan time and it is false for these sites. The pinned model (`knowledgator/gliclass-base-v1.0`, the only GLiClass with a published ONNX export) runs on CPU exactly as the issue describes: 0.45 s to load, 10 ms per call, 31 ms p95 at concurrency 4 on this MacBook Air, `onnxruntime` and `tokenizers` only. Scored against Haiku's own labels on lane A's committed fixtures (188 inputs for each routing site, 40 for the 50-minimum sites, reference labels read from the lane A records in this machine's Redis), its agreement is 0.70 on C1, 0.47 on C2, 0.21 on C3, 0.19 on C4, 0.20 on C5, and 0.60 on C9, under the majority-class baseline on every site but C9 and 15 to 75 points under the tier bars, across three label phrasings each (Spike Results). Label wording moves the numbers by a few points; nothing in a build day closes a gap of that size. The issue's rejection exit fires before the build.

The same machinery, one step over, does carry the local-first outcome. The reference arm's per-input labels are a labeled dataset for every site, and a linear head on a small local embedding model learns them: five-fold cross-validation on the same fixtures gives 0.894 on C1 (bar 0.95) and 0.825 on C9 (bar 0.85) from 150 and 32 training examples, with 1.4 ms per call and 3.1 ms p95 at concurrency 4 (`Xenova/bge-small-en-v1.5` int8, 34 MB, `onnxruntime` and `tokenizers` only). That is a supervised local classifier distilled from the site's reference backend, measured on a held-out split by the same bar, landed per site by the same one-word edit. It needs no `torch`, no daemon, no GPU, and its latency does not depend on the machine's graphics hardware, which is what the issue was reaching for with GLiClass. This plan builds that backend, keeps the GLiClass rejection as a recorded result on the case, and asks Tom to confirm the change of model in Open Questions before the build.

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
3. Per-site landing by the plan's bar (`high` 95%, `medium` 90%, `low` 85% agreement on the held-out split; p95 at concurrency 4 within budget; error rate at or under 2%; `n` and `n_real` at or above the minimums; `contended: false`): the builder sets `backend=LOCAL_ENCODER`, commits the head beside the leg, and commits per site with the record id; a site that misses stays where lane A landed it. Sites are tried in the order the plan-time numbers rank them, C1, C9, C2, C15, C8, C10, C7, C14, C11, C13, then C3, C4, C5, C6; C12 is excluded (its `job_id` answer is not a closed set) and C16 is `client_only`.
4. The GLiClass zero-shot candidate is recorded as rejected on case `1ec40086ca1d422e90ef747775ff7f64` with the plan-time numbers, and this document is the fixture result the issue's acceptance criterion 2 asks for.

## Freshness Check

**Baseline commit:** `149f0d0da` (HEAD of `main` at plan time; lane A merged at `4703bce23`)
**Issue filed at:** 2026-09-18T13:07:59Z; body rewritten twice during lane A's critique; the upstream-change notice (comment 5745559073, posted after PR #3524 reached REVIEW) is the authoritative description of the landed interfaces and this plan is written against the merged code, not the issue text.
**Disposition:** Major drift on the premise, minor drift on the interfaces, overlap with #3421's plan in progress. The interface drift is folded in below; the premise drift (zero-shot GLiClass measured under the bar) is the reason this plan changes the model and is put to Tom in Open Questions rather than built silently.

**File:line references re-verified** (every claim in the issue body and the notice, read on the baseline):

- `agent/llm/backends/__init__.py:5-8` — leg protocol `call(prompt, output_type, route, *, system, sdk_timeout, slot_timeout, max_retries, deadline=None, stack)` — holds; the issue's outcome 1 omits `deadline` and `stack`, corrected in Solution.
- `agent/llm/backends/__init__.py:93-109` — `bound_to_deadline(sdk_timeout, deadline, now, *, leg)` — holds; the encoder leg calls it first.
- `agent/llm/backends/ollama.py:52-96` — the local leg shape (deadline re-check, no `asyncio.wait_for`, third-party symbols from `stack`) — holds; the encoder leg mirrors the structure and takes no symbol from `stack`, since none of its dependencies live there.
- `agent/llm/router.py:31-32` and `:64-70` — the reserved rule slot ("#3420 adds `LOCAL_ZERO_SHOT` the same way") and the `ValueError` for an unrouted backend — holds; the member is named `LOCAL_ENCODER` here, and the docstring line is rewritten in Task 1.
- `agent/llm/wrapper.py:95-98` (`_LEGS`), `:223` (`signature_axis=(route.backend is Backend.ANTHROPIC)`), `:237` (`default_sdk_timeout(route.backend)`), `:242-305` (fallback budget and the two log lines) — hold; the encoder route takes the non-Anthropic axis and the Ollama-shaped budget with no wrapper change beyond the `_LEGS` entry.
- `agent/llm/tasks.py:51-52` — "Lane B (#3420) and lane C (#3421) append their own `Backend` members and their own keyword fields with defaults" — holds; this plan adds a member and no field.
- `tools/classification_eval/__main__.py:47` (`CANDIDATE_BACKENDS = tuple(b.value for b in Backend)`) and `:115-122` (`_candidate_arms` builders) — holds; the notice's "`--candidate local_zero_shot` is rejected as unknown candidate backend" is true only until the enum member exists, after which the vocabulary is automatic and the builder dict is the one addition.
- `tools/classification_eval/arms.py:108-137` (`ollama_arm`, the direct-leg pattern) — holds; `local_encoder_arm` follows it.
- `tools/classification_eval/core.py:97-135` (`Site` with `candidate_prompt`, `candidate_system`, `candidate_output_type`, `real_inputs`), `:289-315` (`_run_arm`: agreement pass at 1, latency pass at 4), `:411-437` (`evaluate_bar`), `:504-520` (`is_contended`) — hold; `Site` gains no field, the fit mode reuses `candidate_prompt` as the text shape and `compare` unchanged on the held-out split.
- `tools/classification_eval/records.py:126-159` (`_audit_row`: `if backend == Backend.OLLAMA.value`) — holds; generalized to every non-Anthropic backend plus the head-provenance rule.
- `tools/classification_eval/sites.py:109-117` (minimums 50 / 200, `FIXTURES_PER_SITE = 40`, `ROUTING_FIXTURES = 188`) and `:283-336` in `arms.py` (`real_messages` from the memory store, digest-ordered) — hold; the digest order is what makes the fit split deterministic.
- `tools/improvement_eligibility.py::is_eligible` — `valor` pinned `True`, cache-only for other keys — holds (`docs/features/llm-task-taxonomy.md:113-121`).
- `tests/unit/test_llm_task_taxonomy.py` check 5 (doc table parity) and check 6 (no `asyncio.wait_for` in any function under `agent/llm/backends/`) — hold; Test Impact lists what each existing test needs.
- `pyproject.toml:62-75` — `onnxruntime>=1.25.0` already floors the `knowledge` and `tts` extras; `onnxruntime 1.25.0` is installed in this venv, `tokenizers` is not — holds.
- `scripts/update/kokoro.py` and `scripts/download_kokoro_models.py` — the weights-at-update pattern (`ensure_models` subprocess, `~/.cache/<name>/`, env override, idempotent) — holds; the encoder weights follow it.
- `docs/features/llm-task-taxonomy.md:171-196` ("Lane A Outcome", the per-site table) — holds and is the target list.

**Cited sibling issues/PRs re-checked:**
- #3410 — closed 2026-09-20 by PR #3524 (merged at `4703bce23`); its plan is archived at `docs/archive/plans-completed/llm-task-taxonomy-routing-layer.md` (five critique rounds; the caller-census discipline and the hotfix #1055 rules from its Critique Results apply here).
- #3421 (lane C, the decisions transport) — open; its plan `docs/plans/structured-decision-transport-jev-behind-ollama-fallback.md` is being written in this same checkout right now (committed at `56674a2d7`, `88cd285bc`). Both lanes add a `Backend` member, a router rule, a `_LEGS` entry, a `default_sdk_timeout` branch, a runner arm builder, doc table rows, and an audit generalization. The audit generalization (landed-arm rule for every non-Anthropic backend) is written once here in a form lane C can reuse; the rest are adjacent one-line additions that rebase cleanly in either order. See Risk 6.
- #3422 (emoji reaction as a decision site) — open; untouched here (anti-criterion in Verification).
- #3525 (`agent/__init__.py` eager import chain) — open; the encoder leg keeps module scope stdlib-only and the download script reads its constants from `config/models.py`, so this lane adds nothing to that chain.
- #3177 (RSI controller; the case substrate) — open; records and claims attach to case `1ec40086ca1d422e90ef747775ff7f64` exactly as lane A's did.

**Commits on main since issue was filed (touching referenced files):** `4703bce23` (lane A itself, the prerequisite), `7d642f3df` (pydantic-ai-slim 2.46.0), `e61b4f0b0` (claude-agent-sdk), `cb03e33ad` (anthropic 1.7.0): the three bumps are dependency pins the encoder leg does not touch.

**Active plans in `docs/plans/` overlapping this area:** `structured-decision-transport-jev-behind-ollama-fallback.md` (#3421, above: coordinate, not conflict); `recursive-self-improvement.md` (#3177) owns the case substrate this plan writes records to; `durability-room-job-agentrun.md` (#2494) owns C12, which this plan excludes.

**Notes:** The issue's own rejection condition ("if the ONNX path cannot reproduce the reference pipeline's per-label scores on a 20-item fixture") is not the one that fired: the ONNX path works (Spike 1). The bar is what GLiClass misses, and it misses it at plan time on the same fixtures lane A's records were measured on, which is stronger evidence than a build-day iteration would have produced. The RSI lineage rows named in the brief (case `1ec40086`, rows `996af74c` local-first, `c4e1199d` lane A comparison outcome, `9984d338` merge and claim level) are the rows this lane's rejection claim and landing claims sit beside.

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
- **Impact on plan**: this is the backend the plan builds. The learning curve is the open variable: a Valor host's memory store supplies the training and held-out real messages, and the record on the held-out split (with the bar's `n_real` rule) is the only claim. Site order follows this table. If a site plateaus under its bar with the small model, the builder may swap to `bge-base-en-v1.5` int8 (Research finding 3) once, on the record; anything past that (fine-tuning the encoder, more labels) is out of scope (Rabbit Holes).

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
6. **Fallback**: on `LLMCallError` the wrapper runs the Anthropic leg once inside the caller's budget and logs `llm_fallback site=<site> primary=local_encoder fallback=anthropic reason=<reason>`; after whichever leg answers, `llm_route site=<site> backend=<backend> elapsed_ms=<int>` (lane A, unchanged).
7. **Output**: the caller receives its output type and applies its own fail-safe on `LLMCallError`, exactly as before.

**Fit path** (`python -m tools.classification_eval --site <id> --fit --candidate local_encoder,anthropic`, run on the `valor`-owning host with services stopped):

1. `site_inputs(site, real_limit)` draws the row's fixtures plus real inputs (the default memory-store loader or the row's own), each with `source`; `--save-inputs` keeps the draw for replay.
2. **Split by digest**: inputs sort by `sha256(text)`; the held-out split takes, in digest order, inputs until it holds the site minimum with at least half of them real (real inputs are drawn into it first, by digest, up to half the minimum, then fixtures and remaining real by digest until the minimum); everything else is the training split. The split is a pure function of the input set, so a `--save-inputs` file replays the same split anywhere. Under the held-out minimum or under half-minimum real, `ShortfallError` refuses before any arm runs (exit 2, no spend).
3. **Label the training split** with the reference arm (Haiku with the site's prompt verbatim; gemma for C15), at the runner's concurrency-4 gate, keeping the per-input label; errors drop the input from training.
4. **Fit the head**: embed each training text through the leg's own `_embed` (the same code path that serves), fit the multinomial logistic regression (numpy, deterministic: fixed epochs, learning rate, L2, zero init), write `agent/llm/backends/heads/<site>.json` with provenance (`site`, `classes`, `W`, `b`, `embedding_model`, `embedding_revision`, `embedding_sha256`, `run_id`, `n_train`, `n_train_real`, `reference_model`, `created_at`), and print the training-split agreement as a sanity line (never the claim).
5. **Measure on the held-out split**: `compare(site, held_out, reference=<reference arm>, candidates=[local_encoder_arm(site), anthropic_arm(site)], contended=...)` unchanged from lane A (agreement pass at 1, latency pass at 4, both arms on the candidate prompt shape); the record gains `fit: {head_run_id, n_train, n_train_real, split: "digest"}`; `write_record` and `attach_claims` as before.
6. **Land**: the builder reads the report; on PASS for the `local_encoder` arm it sets `backend=Backend.LOCAL_ENCODER` on the declaration, restructures the call to `(text, system=instructions)`, commits the head with the declaration and the record id in the message. On MISS the head file is deleted (no orphan weights) and the site stays on `ANTHROPIC` with the record naming the failing criterion.
7. **Audit** (`--audit`): for a `LOCAL_ENCODER` site, the latest record's `local_encoder` arm must PASS and the committed head's `run_id` must equal the record's `fit.head_run_id`; otherwise exit 1.

## Architectural Impact

- **New dependencies**: optional extra `classification-local = ["onnxruntime>=1.25.0", "tokenizers>=0.21"]`; `numpy` is already a base dependency. Installed fleet-wide by `/update`'s `uv sync --all-extras`. Outside the coupled `anthropic` + `pydantic-ai-slim` pin set (#3089/#3140) and outside `_load_stack`.
- **New weights**: 34 MB (`model_int8.onnx`) + 0.7 MB (`tokenizer.json`) in `~/.cache/valor-encoder/` (`LOCAL_ENCODER_MODELS_DIR` override), fetched and checksum-verified by `/update` step 3.13; per-site heads (about 30 KB of JSON each) committed under `agent/llm/backends/heads/`.
- **Interface changes**: none to `run_typed`, the leg protocol, `Route`, `LLMTask`, or `Site`. One enum member, one router rule, one `_LEGS` entry, one `default_sdk_timeout` branch, one runner arm builder, three runner flags (`--fit`, `--preflight`, and `--candidate local_encoder` by derivation), one `fit` block in the record, one audit rule.
- **Coupling**: a landed site now depends on a committed head that was fit against that site's output type and prompt shape; the audit ties the head to the record and the parity test ties the head's classes to the output type, so a prompt or label change on a landed site is caught by the test (class mismatch) or by review (a record older than the change).
- **Data ownership**: the reference arm's labels are used twice (record and training set) and both uses are recorded; the head is derived data owned by the site's declaration, versioned in git.
- **Reversibility**: a site's `backend` word back to `ANTHROPIC` plus deleting its head; the leg, extra, and weights go with the last landed site.

## Appetite

**Size:** Large

**Team:** Solo dev (builder + validator pairs), PM, code reviewer

**Interactions:**
- PM check-ins: 2 to 3 (Open Question 1 before build; the preflight result on the landing host; the per-site landing summary)
- Review rounds: 2+ (the leg and runner; then each landing commit against the bar)

The code is a day; the landing loop is where the time goes, and it is bounded per site (Task 8 gives each site half a build day).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Lane A merged | `git merge-base --is-ancestor 4703bce23 HEAD` | `LLMTask`, `resolve`, the leg protocol, the runner, the records |
| `onnxruntime` and `tokenizers` installable on the pinned interpreter | `.venv/bin/python -c "import sys; assert sys.version_info[:2] == (3, 14)"` then `uv pip install --dry-run onnxruntime tokenizers` | Research finding 4 |
| Redis reachable for records and heads | `.venv/bin/python -c "from tools.classification_eval.records import latest_record; latest_record('routing.needs_response')"` | The runner reads and writes `ImprovementEvidence` |
| Anthropic key for the reference arm | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Haiku labels the training split and is the reference arm |
| Landing host: real `valor` messages in the memory store | `.venv/bin/python -m tools.classification_eval --preflight` (Task 3 adds it; until then `python -c "from tools.classification_eval.arms import real_messages; print(len(real_messages(2000)))"`) | `n_real` for the held-out split (100 real for the routing sites, 25 elsewhere) plus training real messages |
| Services stopped for a latency run | `launchctl list \| grep -c com.valor` is 0 | `contended: false` (Race 3 of lane A) |

The preflight row is the machine gate: on this MacBook Air it prints 12, which fails every site, so Tasks 7 to 9 run on the Valor host that owns the `valor` bridge (Solution, "Where the landing runs").

## Solution

### Key Elements

- **`Backend.LOCAL_ENCODER`** (`agent/llm/tasks.py`): the third member, value `"local_encoder"` (the log token). Declared sites keep the literal convention (`Backend.LOCAL_ENCODER`), so the AST registry, doctor, the audit, and the parity test read them with no change.
- **The encoder leg** (`agent/llm/backends/local_encoder.py`): lane A's leg protocol, met as Data Flow step 5 describes. Module scope is stdlib and our own code only (#3001, #3525); `onnxruntime`, `tokenizers`, and `numpy` are imported inside `_load_runtime()`, the memoized loader that is also the test seam (`monkeypatch.setattr(local_encoder, "_load_runtime", fake)`). The leg never downloads. Its `system` argument is accepted and ignored: instructions are for the Anthropic fallback, and the doc says so.
- **The head file** (`agent/llm/backends/heads/<site>.json`): `classes` (the output field's values as the runner's `label` reducer renders them, so `"True"`/`"False"` for a bool field), `W` (384 × k), `b`, and provenance. Loaded once per site per process. The output-type shape rule a landed site meets: exactly one field typed `bool` or `Literal[...]` whose values equal `classes`; a `confidence: float` field, if present, receives the winning softmax score; every other field has a default. Enforced at call time by the leg (`LLMCallError(reason="validation")` → fallback) and statically by `tests/unit/test_classifier_heads.py` over every committed head against its site's output type as `tools/classification_eval/sites.py` imports it.
- **Router rule** (`agent/llm/router.py`): ahead of the Ollama rules, `task.backend is Backend.LOCAL_ENCODER` → eligible: `Route(LOCAL_ENCODER, task.site, fallback=anthropic)`, else `anthropic`. Same eligibility read, same fail-closed rule (charter §7). The docstring's reserved line is rewritten for the member that landed.
- **Wrapper and timers**: `_LEGS[Backend.LOCAL_ENCODER] = local_encoder.call`; `default_sdk_timeout(LOCAL_ENCODER)` returns `settings.timeouts.local_typed_hard_s` (the existing operator lever; it only bounds the fallback's deadline arithmetic, since the leg has no request). No new settings key.
- **Weights** (`config/models.py`): `LOCAL_ENCODER_MODEL = "Xenova/bge-small-en-v1.5"`, `LOCAL_ENCODER_REVISION = "ea104dacec62c0de699686887e3f920caeb4f3e3"`, `LOCAL_ENCODER_FILES = {"onnx/model_int8.onnx": "bf64d054…", "tokenizer.json": "d241a60d…"}` (full sha256 from Research finding 3), `LOCAL_ENCODER_DIM = 384`. `scripts/download_local_encoder_models.py` (the kokoro script's shape: `~/.cache/valor-encoder/`, `LOCAL_ENCODER_MODELS_DIR` override, `--dry-run`, `--force`, streams to `.part`, verifies sha256 before renaming, re-downloads on mismatch) and `scripts/update/local_encoder.py::ensure_models` run as `/update` step 3.13 after kokoro, non-fatal (a machine without the weights falls back to Anthropic on every call and doctor says so).
- **Runner** (`tools/classification_eval/`): `local_encoder_arm(site_id)` in `arms.py` (direct leg call with `Route(LOCAL_ENCODER, site_id)`, like `ollama_arm`, so a leg failure is the arm's own error and never a fallback answer); the builders dict in `__main__.py` gains the entry and a test asserts every `Backend` member has one; `--fit` (Data Flow, fit path) in a new `tools/classification_eval/fit.py` (split, label, embed, fit, write head); `--preflight` prints `real messages available: N` and one line per site (`needs ≥ M real in the held-out split; training gets the rest`) and exits 1 when N is under the routing sites' need; `ComparisonRecord` gains `fit: dict | None`; `_audit_row` applies the landed-arm rule to every non-Anthropic backend and, for `LOCAL_ENCODER`, requires the committed head's `run_id` to equal `record["fit"]["head_run_id"]`.
- **Doctor**: a `local_encoder` row in the "LLM routing" section: extra importable, weights present with matching checksums, one head per declared `LOCAL_ENCODER` site; fails when a declared site would fall back on every call on this machine (no extra, no weights, or no head), mirroring the `ollama_daemon` row.
- **Per-site landing**: the one-word `backend` edit, the `(text, system=instructions)` call shape, the committed head, the record id in the commit message and in the site's row of the taxonomy table, and the PR body's landing summary (site, backend, held-out agreement for both candidate arms, p95 at 4, `contended`, error rate, `n`, `n_real` with the minimum, `n_train`, `n_train_real`, record id, head run id, failing criterion if any).
- **The GLiClass rejection**: one claim on a `probe` investigation of case `1ec40086` (`tools/improvement_investigations.py::open_investigation` + `record_claims`, the runner's own path) carrying the spike-2 table and the model's pin, URL, and checksums, pointing at this plan on GitHub. Recorded in Task 0 on this machine; no GLiClass code is written.

### Flow

Inbound message → `run_typed(text, Model, task=SITE, project_key, system=instructions)` → `resolve` picks `LOCAL_ENCODER` for eligible context → leg embeds the text, applies the site's head, returns the typed answer in a few milliseconds → `llm_route site=… backend=local_encoder` → caller proceeds. On any leg error → Anthropic fallback inside the budget → `llm_fallback` + `llm_route backend=anthropic`.

Builder on the landing host → `--preflight` (enough real messages?) → per site: `--site <id> --fit --candidate local_encoder,anthropic --save-inputs data/classification_eval/<id>.jsonl` → report PASS or MISS → PASS: flip `backend`, restructure the call, commit head + record id → MISS: delete the head, site stays → `--audit` exit 0 at the PR head → PR body carries the summary → reviewer applies the bar per site.

### Technical Approach

- **Where the landing runs.** Tasks 0 to 6 (rejection claim, leg, runner, tests, doctor, docs skeleton) are machine-independent and run wherever the lane starts; this MacBook Air can run them and the free offline pre-check (Task 5). Tasks 7 to 9 (fit and land) run on the Valor host that owns the `valor` bridge, because its subconscious memory store holds the real inbound `valor` messages the bar's `n_real` criterion demands (`real_messages` reads `fetch_all_records("valor")`, `source="human"`, TUI rows excluded), and `--preflight` is the gate that proves it before any spend. The Air's store holds 12 real messages, so nothing lands from here; `logs/classification_audit.jsonl` is not a corpus (Research finding 5). Latency is measured on that host too, which is representative of the serving fleet and, for a CPU encoder, does not depend on the GPU. If the lane's build session is not on that host, the builder finishes Tasks 0 to 6, pushes, and hands Tasks 7 to 9 to a session on the host through the pipeline's normal steering, with the plan's commands verbatim; the PR is not opened until the landing tasks have run somewhere that passes preflight.
- **What "real" means for the outbound sites** stays as lane A defined it (`tools/classification_eval/sites.py:21-29`): C8, C9, C10, C15 use inbound messages as adversarial drafts, recorded honestly under `n_real`; C11 reads transcript windows; C14 reads memory rows of every source. The fit path draws through the same loaders, so each site trains on the shape it is measured on.
- **Text-first composition for context-bearing sites** (C2 thread, C6 transcript, C10 pair, C11 window): one `def <site>_text(message, context) -> str` beside the declaration, message first, used by the call site and by the row's `candidate_prompt`. Truncation at 512 tokens then drops the oldest context. The reference prompt is untouched (it is the thing being agreed with).
- **The fit is deterministic and cheap**: numpy multinomial logistic regression, zero init, 300 full-batch epochs, learning rate 0.5, L2 1e-3 (the spike's settings), inputs L2-normalized; the head file records them. No scikit-learn; no early stopping on the held-out split (that would leak it into the fit). A second fit on the same inputs and labels writes the same `W` and `b` bit-for-bit, and a test pins that.
- **Reference spend**: per routing site about 700 training labels plus 200 × 2 held-out reference calls; per 50-minimum site about 300 + 50 × 2. Haiku at the list price in `arms.py` (`HAIKU_PRICE`, retrieved 2026-06-24) is well under one dollar per site; C15's gemma reference is metered under `promise_detector` as lane A left it. The Anthropic candidate arm on the restructured shape doubles the held-out Haiku calls; it is what proves the fallback still agrees with the verbatim prompt.
- **Landing order and the per-site box**: C1, C9, C2, C15, C8, C10, C7, C14, C11, C13, then C3, C4, C5, C6, half a build day each, iteration limited to the text composition, `real_limit`, and the one permitted model swap (`bge-base-en-v1.5` int8, pinned the same way, recorded on the head). C12 is excluded: `JobRouteDecision.job_id` is an open answer over a per-call candidate list, not a closed set, and a head cannot produce it. C16 is `client_only`.
- **Thresholds**: if C13 lands, `INTENT_CONFIDENCE_THRESHOLD` (`tools/classifier.py`) is re-tuned on the record's held-out softmax scores and the value is committed with the record id in the message; `JOB_ROUTER_CONFIDENCE_THRESHOLD` is untouched because C12 does not land.
- **Rejection exit for the supervised backend** (the issue's shape, kept): if no site clears its bar after the loop, the lane records each MISS on the case, deletes the leg, the extra, the weights script, the update step, the doctor row, and the runner arm in one commit (no dead backend under Tom's no-default-off rule), and the PR ships the runner's `--fit` and `--preflight` plus the rejection claims and the doc section. `Backend` stays two members in that branch.
- **Import cost (#3525)**: nothing new at `agent.llm` module scope beyond the leg module, whose own module scope is stdlib. The download script and the update step import `config.models` only.
- **Concurrency**: `_classify` runs in `asyncio.to_thread`; one `InferenceSession` per process with `intra_op_num_threads=4` (the spike's setting) is shared across calls (`InferenceSession.run` is thread-safe); the loader lock prevents a double load on the first burst. No semaphore: 3 ms p95 at concurrency 4 on the Air.

## Failure Path Test Strategy

Placeholder.

## Test Impact

Verified on `main` at `149f0d0da` by reading each file; the router table test, the eligibility test, the wrapper's `_LEGS` tests, the doctor test, and the runner's audit tests all key on the two-member `Backend` enum and on `Backend.OLLAMA` as the only local backend.

- [ ] `tests/unit/test_llm_tasks.py:24` (`{b.value for b in Backend} == {"anthropic", "ollama"}`) — UPDATE: the set gains `"local_encoder"`.
- [ ] `tests/unit/test_llm_router.py::_expected` (`:120-123`) and `TestEveryDeclaration::test_route_matches_the_declaration` — UPDATE: `_expected` returns `Route(LOCAL_ENCODER, task.site, fallback=ANTHROPIC_ROUTE)` for a `LOCAL_ENCODER` declaration with key `valor`, `ANTHROPIC_ROUTE` otherwise; add `test_rule_3_eligible_local_encoder_carries_an_anthropic_fallback`, `test_rule_4_ineligible_local_encoder_fails_closed`, and extend `test_only_the_ollama_rule_consults_eligibility` (`:103`) to the two local rules (rename to `test_only_the_local_rules_consult_eligibility`).
- [ ] `tests/unit/test_llm_router_eligibility.py` (`:76-96`, the `OLLAMA`-site parametrization) — UPDATE: parametrize over every declaration whose backend is not `ANTHROPIC`; the "valor message reaches the local leg with `gh` unavailable" case asserts the leg named by the declaration (`legs[task.backend]`), so a `LOCAL_ENCODER` site asserts the encoder leg.
- [ ] `tests/unit/test_llm_wrapper.py::TestPerBackendSdkTimer` (`:633-656`) — UPDATE: add the `local_encoder` rows (`local_typed_hard_s` default, explicit `sdk_timeout` wins) and a third fake in the `_LEGS` table fixture at `:607-608`; the fallback tests at `:553-708` gain one case: encoder leg raises `LLMCallError` → Anthropic fallback inside the budget, `llm_fallback ... primary=local_encoder` on `caplog`.
- [ ] `tests/unit/test_llm_backend_ollama.py` — no change; kept as the template for `tests/unit/test_llm_backend_local_encoder.py` (create).
- [ ] `tests/unit/test_llm_import_safety.py` — UPDATE: the raising shim fixture also writes `onnxruntime.py` and `tokenizers.py`, and the assertion that `import agent.llm` (and `bridge.telegram_bridge`) succeeds with every third-party module broken now covers the encoder leg's module scope.
- [ ] `tests/unit/test_llm_task_taxonomy.py` (doc/code parity, check 5; hotfix #1055 check 6 over every function in `agent/llm/backends/`) — no test change; the new leg module and the new site-table rows must satisfy both as written. Check 6 is the guard that keeps `asyncio.wait_for` out of the encoder leg.
- [ ] `tests/unit/test_doctor.py:881-909` (routing section: `Backend.OLLAMA` sites and the `ollama_daemon` row) — UPDATE: add the `local_encoder` row assertions (extra importable, weights present with matching checksum, one head per declared `LOCAL_ENCODER` site) with a fake models dir and a fake heads dir; the `ollama_daemon` assertions are unchanged.
- [ ] `tests/unit/test_classification_eval.py::test_audit_exit_codes` (`:395`) and `test_audit_over_two_sites_fails_when_either_misses` (`:412`) — UPDATE: parametrize the local-landing cases over `ollama` and `local_encoder` (the audit's landed-arm rule now applies to every backend other than `ANTHROPIC`), and add the head-provenance case: a `LOCAL_ENCODER` landing whose committed head `run_id` differs from the record's `fit.head_run_id` exits 1.
- [ ] `tests/unit/test_classification_eval.py::test_parse_candidates_rejects_an_unknown_backend` (`:444`) — no change (`CANDIDATE_BACKENDS` is derived from the enum, so `local_encoder` is accepted automatically); add `test_candidate_arm_builders_cover_every_backend` so a member without an arm builder fails by name.
- [ ] `tests/unit/test_classification_eval.py` — ADD (create alongside the existing runner tests): the `--fit` split is deterministic by digest and refuses under the held-out minimum before any arm runs; the fitted head round-trips through the leg's loader; the record carries the `fit` provenance block; `--preflight` prints the real-message count against each site's need and exits 1 when the routing sites cannot be met.
- [ ] The landed sites' own test files (for example `tests/unit/test_routing_classifiers.py` for C1, `tests/unit/test_promise_gate.py` for C9) — UPDATE only where a landing restructures the call (`prompt` becomes the text under classification and the instructions move to `system`): any test asserting the prompt string the fake receives is updated to the new `(prompt, system)` split. The fail-safe tests are byte-identical, since the wrapper contract does not change.

## Rabbit Holes

Placeholder.

## Risks

Placeholder.

## Race Conditions

Placeholder.

## No-Gos (Out of Scope)

Placeholder.

## Update System

Placeholder.

## Agent Integration

Placeholder.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/local-encoder-classifier.md`: the leg (embedding model, per-site head, the output-type shape a landed site must have, `system` ignored by the leg and carried for the fallback), the fit protocol (digest split, held-out record, `fit` provenance, the head-run-id audit rule), the text-first composition rule for context-bearing sites, the rejection of zero-shot GLiClass with the plan-time numbers, and the per-site outcome table for this lane.
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

Placeholder.

## Team Orchestration

Placeholder.

## Step by Step Tasks

Placeholder.

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
| `/update` fetches the weights | `/usr/bin/grep -c "local_encoder.ensure_models" scripts/update/run.py` | output > 0 |
| Extra declared | `/usr/bin/grep -c "^classification-local = \[" pyproject.toml` | output > 0 |
| Feature doc exists | `test -f docs/features/local-encoder-classifier.md` | exit code 0 |
| Feature index updated | `grep -c "local-encoder-classifier.md" docs/features/README.md` | output > 0 |
| Infra doc names the weights | `grep -c "download_local_encoder_models" docs/infra/llm-task-routing.md` | output > 0 |
| Taxonomy doc carries the new rule | `grep -c "LOCAL_ENCODER" docs/features/llm-task-taxonomy.md` | output > 0 |

**Live evidence (manual, deployed bridge):** after `/update` has run on the `valor`-owning host (the extra installed, the weights fetched, services restarted) and one inbound message in a `valor` room: `grep -c "llm_route site=<landed site> backend=local_encoder" logs/bridge.log` is above 0 for the site the PR names, and `grep -c llm_fallback logs/bridge.log` is unchanged across that message. Needs a deployed bridge and a human reading two counts, so it stays outside the table.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Placeholder.
