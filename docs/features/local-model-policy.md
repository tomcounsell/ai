# Local Ollama Model Policy

**Status:** Shipped (issue #1636; the local encoder backend, #3420)

This document defines which local models run on each machine and which workloads use them. Every call site reads from a single config constant or setting; model ids live in `config/models.py` and `config/settings.py` only.

## Model roles

| Workload | Model | Config source | Notes |
|----------|-------|--------------|-------|
| **Classification on granite** (the sites declared `backend=OLLAMA` in the [taxonomy table](llm-task-taxonomy.md#site-table): Job bind-or-mint, intake intent, memory audit) | `granite4.1:3b` | `OLLAMA_CLASSIFIER_MODEL` in `config/models.py`, run by the Ollama leg of `run_typed` | Hard precondition: worker will not start without it. |
| **Classification on the local encoder** (the sites declared `backend=LOCAL_ENCODER`; today none, the landing run on the Valor host decides) | `Xenova/bge-small-en-v1.5` int8 ONNX plus one committed linear head per site (`agent/llm/backends/heads/<site>.json`) | `LOCAL_ENCODER_MODEL`, `LOCAL_ENCODER_REVISION`, `LOCAL_ENCODER_FILES` in `config/models.py`, run in-process by the encoder leg of `run_typed` | No daemon, no GPU; weights fetched by `/update` Step 3.15 into `~/.cache/valor-encoder/`; a machine without them falls back to Haiku per call. A head is fit and installed per site by `python -m tools.classification_eval --site <id> --fit --land --candidate local_encoder,anthropic` on a host with real messages, and `python -m tools.doctor`'s `local_encoder` row reports extra, weights, and heads. See [Local Encoder Classifier](local-encoder-classifier.md). |
| **Free-text generation** (memory title generation, test AI judge, knowledge doc summarization) | `gemma4:31b-cloud` (default) or `gemma4:31b-mlx` (RAM-rich) | `settings.models.ollama_generation_model` (`config/settings.py`) | Soft / fail-soft everywhere. Override per machine via env `MODELS__OLLAMA_GENERATION_MODEL`. |
| **Embeddings** | `nomic-embed-text` | `agent/embedding_provider.py` | Out of scope for this consolidation; local only. |

## Which classification sites run locally

Two local classification backends sit behind the same router, and the split between them and the subscription backend is a property of each call site's `LLMTask` declaration, not of a model constant. A site declares `kind=CLASSIFICATION` with `backend=Backend.OLLAMA` or `backend=Backend.LOCAL_ENCODER`, and `agent/llm/router.py::resolve` sends it to that leg for eligible context (granite on `OLLAMA_CLASSIFIER_MODEL`; the encoder on the site's committed head), with the Anthropic leg (Haiku) as the one-shot fallback when the local leg raises. Thinking sites (`kind=THINKING`) never run on a local model. A classification site lands on either local backend only with a comparison record that clears the acceptance bar (for the encoder, measured on a held-out split with both the encoder and the Anthropic arm clearing it); a site that misses declares `backend=Backend.ANTHROPIC` with its record attached. The landed backend per site, the router rules, and the bar are in [LLM Task Taxonomy](llm-task-taxonomy.md).

Today granite serves C12 `job_router.route`, C13 `classifier.intake_intent`, and C14 `memory_audit.classify`; the encoder serves no site yet. The landing run on the Valor host decides which candidate sites move to the encoder ([Lane B Outcome](llm-task-taxonomy.md#lane-b-outcome)); a site that lands there keeps granite out of its path entirely, since the encoder's fallback is Haiku.

The fallback rule: a client-keyed call, a `None` key, or a cold eligibility cache resolves to the subscription backend before any local call (charter §7, fail-closed); a local leg that times out, refuses, or fails schema validation (or, for the encoder, has no extra, weights, or head on this machine) falls back to Haiku once inside the caller's budget and logs `llm_fallback`. The daemon settings that keep the Ollama leg inside its budget (`OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=4`), the encoder weights, and the rollback levers are in [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md).

## Steady-state local Ollama per machine type

**Cloud machine (e.g. 16 GB RAM):**
- `granite4.1:3b` — classification
- `nomic-embed-text` — vector embeddings
- Generation → Ollama Cloud (`gemma4:31b-cloud`)

**RAM-rich Apple-Silicon machine (≥ 48 GB):**
- `granite4.1:3b` — classification
- `nomic-embed-text` — vector embeddings
- `gemma4:31b-mlx` — local generation (opt-in, selected by `/setup`)

## Setup and update integration

- **`/setup`** measures RAM (`sysctl -n hw.memsize`), selects `gemma4:31b-mlx` when RAM ≥ `MIN_LOCAL_GEN_RAM_GB` (48 GB), else `gemma4:31b-cloud`, and writes `MODELS__OLLAMA_GENERATION_MODEL` to `~/.zshenv` (machine-local, NOT the iCloud-synced `~/Desktop/Valor/.env`). `install_worker.sh` injects the same var into the launchd plist.
- **`/update` Step 4.76** checks that the classifier model is present on this machine. It is a presence check only; the restart-blocking smoke gate that used to run here died with the PTY substrate (plan #1924).
- **`/update` Step 4** ensures the configured generation model via `ensure_generation_model()` — cloud: signin check; mlx: RAM-guarded probe/pull. Warning only, never suppresses restart.
- **`gemma4:e2b`** (the prior single local model, standardized in issue #671) is in `OLLAMA_SUPERSEDED_MODELS` and is removed from each machine by the `/update` superseded-cleanup loop, gated on the classifier model being present AND the `data/spike1_parity_ok` marker being present.

## `ensure_generation_model()` helper

Defined in `config/models.py`. Returns `(model_available: bool, detail: str)`.

- `:cloud` tag — near-no-op: confirms cloud is signed in, always reports available.
- `-mlx` tag — RAM-guard first: returns `(False, "RAM too low for local mlx — use cloud")` when RAM < `MIN_LOCAL_GEN_RAM_GB` without pulling; otherwise probe→pull-once→re-probe.

This helper is a config-layer detection tool, NOT a startup gate. It is called by `/setup`, `/update` (warning path), and by the title-generator (skip-on-unavailable path). It never causes the worker to exit or suppresses a service restart.

## Migration from `gemma4:e2b`

Prior to issue #1636, `gemma4:e2b` was the single local model for all Ollama workloads (`OLLAMA_LOCAL_MODEL` constant, standardized in #671). The consolidation:
- Repoints classification call sites to `granite4.1:3b` (already resident, stronger at structured output).
- Repoints generation call sites to the per-machine `ollama_generation_model` setting (cloud by default).
- Removes `OLLAMA_LOCAL_MODEL` constant; adds `OLLAMA_CLASSIFIER_MODEL` and `ensure_generation_model()`.
- Adds `gemma4:e2b` to `OLLAMA_SUPERSEDED_MODELS` for retirement via `/update`.

## See also

- [LLM Task Taxonomy](llm-task-taxonomy.md): which sites declare `backend=OLLAMA` or `backend=LOCAL_ENCODER`, the router rules, and the acceptance bar.
- [Local Encoder Classifier](local-encoder-classifier.md): the encoder leg, its head file, and the fit protocol.
- [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md): Ollama service settings and encoder weights per machine, log evidence, rollback.
- [Headless Session Runner](headless-session-runner.md): the session-execution substrate; session dispatch has no ollama dependency (D2, issue #1924).
- [Subconscious Memory](subconscious-memory.md#title-generation): title-generator and generation model usage.
- [Intake Classifier](intake-classifier.md) and [Durability Model](durability-model.md): the two bridge sites on granite.
- [Email CS Auto-Reply](email-cs-auto-reply.md): email triage, `client_only` on the subscription backend.
