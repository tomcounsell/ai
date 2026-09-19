# Local Ollama Model Policy

**Status:** Shipped (issue #1636)

This document defines which Ollama models run locally on each machine and which workloads use them. Every call site reads from a single config constant or setting — model ids are never scattered across source files.

## Model roles

| Workload | Model | Config source | Notes |
|----------|-------|--------------|-------|
| **Classification** (the sites declared `backend=OLLAMA` in the [taxonomy table](llm-task-taxonomy.md#site-table): Job bind-or-mint, intake intent, memory audit) | `granite4.1:3b` | `OLLAMA_CLASSIFIER_MODEL` in `config/models.py`, run by the Ollama leg of `run_typed` | Hard precondition: worker will not start without it. |
| **Free-text generation** (memory title generation, test AI judge, knowledge doc summarization) | `gemma4:31b-cloud` (default) or `gemma4:31b-mlx` (RAM-rich) | `settings.models.ollama_generation_model` (`config/settings.py`) | Soft / fail-soft everywhere. Override per machine via env `MODELS__OLLAMA_GENERATION_MODEL`. |
| **Embeddings** | `nomic-embed-text` | `agent/embedding_provider.py` | Out of scope for this consolidation; local only. |

## Which classification sites run on granite

The classifier/generation split is a property of each call site's `LLMTask` declaration, not of the model constant: a site declares `kind=CLASSIFICATION` with `backend=Backend.OLLAMA`, and `agent/llm/router.py::resolve` sends it to the Ollama leg on `OLLAMA_CLASSIFIER_MODEL` for eligible context, with the Anthropic leg (Haiku) as the one-shot fallback when the local leg raises. Thinking sites (`kind=THINKING`) never run on a local model. A classification site lands on granite only with a comparison record that clears the acceptance bar; a site that misses declares `backend=Backend.ANTHROPIC` with its record attached. The landed backend per site, the router rules, and the bar are in [LLM Task Taxonomy](llm-task-taxonomy.md).

The fallback rule: a client-keyed call, a `None` key, or a cold eligibility cache resolves to the subscription backend before any local call (charter §7, fail-closed); a local leg that times out, refuses, or fails schema validation falls back to Haiku once inside the caller's budget and logs `llm_fallback`. The daemon settings that keep the local leg inside its budget (`OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=4`) and the rollback levers are in [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md).

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

- [LLM Task Taxonomy](llm-task-taxonomy.md): which sites declare `backend=OLLAMA`, the router rules, and the acceptance bar.
- [`docs/infra/llm-task-routing.md`](../infra/llm-task-routing.md): Ollama service settings per machine, log evidence, rollback.
- [Headless Session Runner](headless-session-runner.md): the session-execution substrate; session dispatch has no ollama dependency (D2, issue #1924).
- [Subconscious Memory](subconscious-memory.md#title-generation): title-generator and generation model usage.
- [Intake Classifier](intake-classifier.md) and [Durability Model](durability-model.md): the two bridge sites on granite.
- [Email CS Auto-Reply](email-cs-auto-reply.md): email triage, `client_only` on the subscription backend.
