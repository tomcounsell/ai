# Local Ollama Model Policy

**Status:** Shipped (issue #1636)

This document defines which Ollama models run locally on each machine and which workloads use them. Every call site reads from a single config constant or setting — model ids are never scattered across source files.

## Model roles

| Workload | Model | Config source | Notes |
|----------|-------|--------------|-------|
| **Classification** (bridge routing, memory audit, email triage) | `granite4.1:3b` | `OLLAMA_CLASSIFIER_MODEL` in `config/models.py` | Hard precondition: worker will not start without it. Same model already resident for PTY operator work — zero extra memory. |
| **Free-text generation** (memory title generation, test AI judge, knowledge doc summarization) | `gemma4:31b-cloud` (default) or `gemma4:31b-mlx` (RAM-rich) | `settings.models.ollama_generation_model` (`config/settings.py`) | Soft / fail-soft everywhere. Override per machine via env `MODELS__OLLAMA_GENERATION_MODEL`. |
| **Embeddings** | `nomic-embed-text` | `agent/embedding_provider.py` | Out of scope for this consolidation; local only. |
| **PTY operator routing** (PM↔Dev turn classification) | `granite4.1:3b` | `GRANITE__DEV_MODEL` (default) | Same binary as the classifier above; one resident model serves both roles. |

## Steady-state local Ollama per machine type

**Cloud machine (e.g. 16 GB RAM):**
- `granite4.1:3b` — classification + PTY routing
- `nomic-embed-text` — vector embeddings
- Generation → Ollama Cloud (`gemma4:31b-cloud`)

**RAM-rich Apple-Silicon machine (≥ 48 GB):**
- `granite4.1:3b` — classification + PTY routing
- `nomic-embed-text` — vector embeddings
- `gemma4:31b-mlx` — local generation (opt-in, selected by `/setup`)

## Setup and update integration

- **`/setup`** measures RAM (`sysctl -n hw.memsize`), selects `gemma4:31b-mlx` when RAM ≥ `MIN_LOCAL_GEN_RAM_GB` (48 GB), else `gemma4:31b-cloud`, and writes `MODELS__OLLAMA_GENERATION_MODEL` to `~/.zshenv` (machine-local, NOT the iCloud-synced `~/Desktop/Valor/.env`). `install_worker.sh` injects the same var into the launchd plist.
- **`/update` Step 4.75** gate verifies granite is present (hard gate, suppresses restart on failure).
- **`/update` Step 4** ensures the configured generation model via `ensure_generation_model()` — cloud: signin check; mlx: RAM-guarded probe/pull. Warning only, never suppresses restart.
- **`gemma4:e2b`** (the prior single local model, standardized in issue #671) is in `OLLAMA_SUPERSEDED_MODELS` and is removed from each machine by the `/update` superseded-cleanup loop, gated on the granite smoke-test passing AND the `data/spike1_parity_ok` marker being present.

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

- [Headless Session Runner](headless-session-runner.md) — the session-execution substrate; session dispatch has no ollama dependency (D2, issue #1924). The classifier model above serves bridge routing and email triage only.
- [Subconscious Memory](subconscious-memory.md#title-generation) — title-generator and generation model usage.
- [SDLC-First Routing](sdlc-first-routing.md) — bridge classifier using `OLLAMA_CLASSIFIER_MODEL`.
- [Email CS Auto-Reply](email-cs-auto-reply.md) — email triage using `OLLAMA_CLASSIFIER_MODEL`.
