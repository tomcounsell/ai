---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1636
last_comment_id: 4687842710
revision_applied: true
---

# Consolidate gemma4:e2b Ollama Usage onto Granite (classification) and Ollama Cloud (generation)

## Problem

Local Ollama on each bridge machine currently runs **two** instruct models that
can be resident at the same time:

- `granite4.1:3b` (2.1 GB) — the granite PTY classifier, on the hot path of
  every Dev session turn (`extract_dev_prompt` / `summarize_for_pm`).
- `gemma4:e2b` (7.2 GB on disk, ~1.7 GB resident) — used for message routing
  classification, memory-audit classification, email triage, memory-title
  generation, and the test AI judge.

**Current behavior:**
When a granite PTY session is active and a Telegram message arrives, Ollama is
asked to load/serve granite AND gemma concurrently on a single GPU. `ollama ps`
confirms gemma4:e2b sits resident (~1.7 GB) alongside `nomic-embed-text`. Two
instruct models loaded for what is really one workload (short local inference)
wastes GPU memory and risks eviction thrashing under load. Gemma was
standardized in issue #671 as the single local model, but the granite PTY work
(#1542/#1572) introduced a second, better-suited local model that is *already
resident* whenever sessions run.

**Desired outcome:**
The small `gemma4:e2b` is removed from local Ollama entirely. Every current
gemma4:e2b call site is repointed following a simple rule:

- **Classification / structured-output tasks → `granite4.1:3b`** (the model
  already resident for PTY work — reuse it, zero extra memory, zero network
  latency, and granite is strong at tool-structured output).
- **Free-text generation tasks → Ollama Cloud `gemma4:31b-cloud`** (verified
  real: 32B, gemma4 arch, 262k ctx, BF16; offloads compute off the local GPU).
  The cloud tag is a lightweight pointer — no large local weights — so it fits
  every machine in the fleet, including this 16 GB host. The model id lives in a
  single per-machine setting `ollama_generation_model` (default
  `gemma4:31b-cloud`), so a future RAM-rich machine can opt into a local variant
  by overriding the env var *without a code change* — but this plan ships
  **cloud-only**; no RAM detection, no local-MLX code path (see No-Gos).

End state on every machine: local Ollama runs `granite4.1:3b` (classifier +
message classification) and `nomic-embed-text` (embeddings) only; generation
goes to Ollama Cloud.

This mirrors the just-landed granite config requirement (commits `98ca1b57`
/update gate + `52740fbb` worker precondition + `ensure_granite_model()`
helper) — the generation model gets the same setup/update treatment, but as a
**soft warning** rather than a hard gate, because the generation call sites are
all fail-soft (a missing generation model degrades titles/judging, it does not
mis-route or crash anything).

## Freshness Check

**Baseline commit:** 7291053022c626e46c5738b06dadecf8e0d780b1
**Issue filed at:** N/A — plan authored directly from conversation 2026-06-12
**Disposition:** Unchanged

**File:line references re-verified (read at plan time):**
- `config/models.py:135` — `OLLAMA_LOCAL_MODEL = "gemma4:e2b"` — confirmed.
- `config/models.py:138-144` — `OLLAMA_SUPERSEDED_MODELS` list — confirmed; gemma4:e2b not yet in it.
- `bridge/routing.py:519-546` — `classify_needs_response` (binary, NO Haiku fallback) — confirmed.
- `bridge/routing.py:708-746` — `classify_terminus` (3-way, Haiku fallback) — confirmed.
- `bridge/routing.py:904-950` — `_classify_work_request_llm` (4-way, Haiku fallback) — confirmed.
- `reflections/memory_management.py:537-557` — `_gemma_classify` (structured JSON) — confirmed.
- `tools/email_cs/triage.py:89-103` — tier-1 triage (structured JSON) — confirmed.
- `tools/memory_search/title_generator.py:43-148` — title generation (free text, HTTP `/api/generate`) — confirmed.
- `tests/ai_judge/judge.py:34,70-120` — AI judge (CLI `ollama run`, OpenRouter fallback already present) — confirmed.
- `config/settings.py:172-196` — `ollama_vision_model` (default gemma4:e2b) has **no code consumers** (grep clean) — confirmed dead config.
- `scripts/update/run.py:807` — module-level `from config.models import OLLAMA_LOCAL_MODEL, OLLAMA_SUPERSEDED_MODELS` inside Step 4 (`if config.do_ollama:`); `:810` uses `os.getenv("OLLAMA_SUMMARIZER_MODEL", OLLAMA_LOCAL_MODEL)` — confirmed. Crashes at import if the constant is removed without migrating this line.
- `scripts/update/mcp_memory.py:281,283,287,289,291` — try/except import of `OLLAMA_LOCAL_MODEL` with a hardcoded `"gemma4:e2b"` fallback string at :283 — confirmed. A pure rename leaves the literal stale.
- `scripts/update/verify.py:1046` — `os.getenv("OLLAMA_SUMMARIZER_MODEL", "gemma4:e2b")` hardcoded gemma fallback (critique missed this; found during revision) — confirmed. Must be repointed too.

**Empirical environment check (`ollama list` / `ollama ps` / `ollama show`):**
- `granite4.1:3b` present (2.1 GB). `gemma4:e2b` present (7.2 GB), currently resident.
- `glm-5.1:cloud` present (cloud signed in 8 days ago) — cloud path is live, de-risks the generation bucket.
- `gemma4:31b-cloud` confirmed real via `ollama show` (32B, BF16, 262k ctx) — the cloud generation target for every machine.
- This host is **16 GB RAM** → cloud-only for generation (a 32B local model won't fit). No fleet machine is currently confirmed RAM-rich, so the local-MLX path is deferred (see No-Gos); this plan ships cloud-only.
- `nomic-embed-text` present (embeddings, out of scope).

**Granite config precedent (build atop this — pulled at plan-revision time):**
- `agent/granite_container/granite_classifier.py::ensure_granite_model()` — probe→pull-once→re-probe helper returning `(ok, detail)`.
- `scripts/update/run.py` Step 4.75 — auto-pull + 30s smoke test, **suppresses service restart** on failure (hard gate).
- `worker/__main__.py` Step 4b.5 — `ensure_granite_model()` hard precondition; worker `sys.exit(1)` on failure, launchd self-heals.
- `scripts/update/run.py` Step 4 — existing Ollama-model step (pull/smoke/superseded-cleanup) keyed on `OLLAMA_LOCAL_MODEL`; `verify.check_ollama()` / `verify.pull_ollama_model()` helpers work for cloud and local tags alike.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **Issue #671**: *Standardize all Ollama usage on gemma4:e2b, add cleanup and smoke test to /update* — established the current single-local-model state. This plan **partially reverses** #671: the "one local model" principle is kept, but the model becomes granite (already needed for PTY), and gemma is retired. The `/update` cleanup + smoke-test machinery #671 built is reused (add gemma4 to the superseded list, point the smoke test at granite).
- **Issue #1231**: *Memory health audit: 3-layer reflection (… gemma classification)* — created `_gemma_classify` (Layer 3). This plan repoints that call to granite; its fail-soft contract is unchanged.
- **Issue #1573**: *Email customer-service auto-reply (two-tier triage)* — created `tools/email_cs/triage.py` tier-1 gemma call. Repointed to granite here.
- **Issue #1542 / #1572**: granite PTY operator — introduced `granite4.1:3b` as a second local model and the `granite_classifier.py` tool-calling pattern this plan reuses for the structured sites.
- **Commit `98ca1b57`** (*gate service restart on granite4.1:3b availability*) and **`52740fbb`** (*make granite a hard startup precondition*): the directly-preceding work that added `ensure_granite_model()`, the `/update` Step 4.75 gate, and the worker Step 4b.5 precondition. **This plan is explicitly built atop that pattern** — the generation model gets the same setup/update ensure-treatment, deliberately downgraded to warning-only because generation is fail-soft.

## Research

No external WebSearch needed — the cloud mechanism was verified empirically
(`glm-5.1:cloud` already pulled and signed in). Reference for builders:
Ollama Cloud uses the `<model>:cloud` / `<model>:<size>-cloud` tag convention
and routes the request to Ollama's hosted GPUs via the same local `ollama` /
`ollama.chat` client when the machine is signed in (`ollama signin`). Docs:
https://docs.ollama.com/cloud

## Spike Results

<!-- Filled by Phase 1.5 spikes during build kickoff. Four spikes enumerated below. -->

### spike-1: Classification parity (granite vs gemma) — **BLOCKING PRE-BUILD GATE**
- **Assumption**: "`granite4.1:3b` produces equivalent or better labels than `gemma4:e2b` on the three routing classifiers (binary work/ignore, 3-way terminus, 4-way work-request)."
- **Method**: prototype — replay a sample of real classification inputs (mine `logs/` terminus DEBUG lines from issue #1318, plus hand-built cases) through both models; compute **per-classifier** label agreement. `classify_needs_response` (no Haiku fallback) MUST be sampled separately and held to a higher bar — a confident-but-wrong granite label there silently drops a real work message.
- **Agent Type**: builder in worktree
- **Time cap**: 5 minutes
- **Gate**: This spike is a **hard pre-build gate** (task `build-classification` does not start until it records a per-classifier agreement verdict). Parity is the real miscalibration guard; the Haiku fallback only covers granite *unavailability*, never a confident wrong label (see Risk 1). If `classify_needs_response` parity is poor, do NOT repoint it blind — either keep it on gemma in shadow-mode (log granite, route gemma) until parity is confirmed, or drop the local call entirely and lean on the conservative `True` default.
- **Result**: [filled after spike]
- **Confidence**: [filled after spike]
- **Impact if false**: If granite diverges materially on a site WITH a Haiku fallback (terminus, work-request), keep that site on Haiku (drop the local call). If it diverges on `classify_needs_response` (no fallback), use shadow-mode or the conservative `True` default rather than repointing.

### spike-2: Cloud generation model reachability + latency
- **Assumption**: "`gemma4:31b-cloud` returns a usable title in < 5 s via the title-generator's HTTP `/api/generate` path (and via `ollama.chat`) on this signed-in 16 GB host."
- **Method**: prototype — on this 16 GB host, call `gemma4:31b-cloud` through both `ollama.chat` and the HTTP `/api/generate` path; measure latency. Tag is pre-confirmed real via `ollama show`; this spike is latency + path correctness only. (RAM-threshold / MLX-variant investigation is **cut** — this plan is cloud-only; see No-Gos.)
- **Agent Type**: builder in worktree
- **Time cap**: 5 minutes
- **Result**: [filled after spike] — cloud tag pre-confirmed real via `ollama show`.
- **Confidence**: [filled after spike]
- **Impact if false**: Cloud is fail-soft (timeout → title unchanged), so generation is never blocked. If latency is unacceptable, raise the title-gen timeout or accept category-only titles; the OpenRouter fallback covers the ai-judge path.

### spike-3: granite structured-output reliability
- **Assumption**: "`granite4.1:3b` reliably emits parseable JSON for the memory-audit prompt (`GEMMA_AUDIT_PROMPT`) and the email-triage prompt, OR cleanly calls a classification tool."
- **Method**: prototype — run both prompts through granite ~20× each; measure parse-success rate for (a) emit-JSON-and-parse and (b) native tool-calling.
- **Agent Type**: builder in worktree
- **Time cap**: 5 minutes
- **Result**: [filled after spike]
- **Confidence**: [filled after spike]
- **Impact if false**: If JSON-emit is flaky, switch the two structured sites to granite native tool-calling (the `granite_classifier.py` pattern). If both are flaky, keep the structured sites' fail-soft default (they already no-op gracefully).

### spike-4: granite hot-path cold-start latency
- **Assumption**: "When granite is NOT resident (no active PTY session), the first routing classification call completes within the existing timeout / is covered by the Haiku fallback."
- **Method**: prototype — `ollama stop granite4.1:3b`, then time a single `classify_terminus` call.
- **Agent Type**: builder in worktree
- **Time cap**: 5 minutes
- **Result**: [filled after spike]
- **Confidence**: [filled after spike]
- **Impact if false**: Add `keep_alive` to the classification calls and/or rely on the existing Haiku fallback for the cold first call.

## Data Flow

Two distinct flows touch the changed code:

1. **Hot-path message classification (bridge process):**
   Telegram message → `bridge/telegram_bridge.py` → `routing.classify_needs_response` /
   `classify_terminus` / `_classify_work_request_llm` → **was** `ollama.chat(model=gemma4:e2b)`,
   **now** `ollama.chat(model=granite4.1:3b)` (localhost:11434) → label → routing decision.
   Haiku fallback remains on terminus + work-request; binary classifier keeps its
   conservative `True` default.

2. **Off-hot-path generation/audit (worker / reflection / tool):**
   - Memory save → `title_generator._do_generate` → **was** local gemma `/api/generate`,
     **now** Ollama Cloud `/api/generate` (model `*:cloud`) → title → `Memory.save()`.
   - Hourly memory-audit reflection → `_gemma_classify` → granite local → JSON verdict.
   - Email tier-1 triage → `triage` → granite local → JSON verdict.
   - Test AI judge → `_call_ollama` (subprocess `ollama run`) → cloud model → verdict.

## Architectural Impact

- **New dependencies**: none new at the package level — `ollama` client and
  HTTP API already in use. Adds a *runtime* dependency on Ollama Cloud
  reachability for the title-generator + ai-judge paths (both already
  fail-soft). New internal helper `ensure_generation_model()` lives in
  **`config/models.py`** (NOT `agent/granite_container/`) — it is a config-layer
  availability probe shared by `/setup`, `/update`, and the title-generator,
  reachable from all three without importing the agent/PTY stack. It is a
  detection helper, **not** a startup gate like `ensure_granite_model()` (the
  "mirrors ensure_granite_model" framing is dropped — see Blocker-2 resolution);
  for cloud tags it is a near-no-op because a signed-in cloud tag is always
  "available".
- **Interface changes**: `config/models.py` gains `OLLAMA_CLASSIFIER_MODEL`
  (granite) and `ensure_generation_model()`; `config/settings.py::ModelSettings`
  gains `ollama_generation_model` (per-machine, default `gemma4:31b-cloud`);
  `OLLAMA_LOCAL_MODEL` is **removed** (NO LEGACY: no aliasing the old name). All
  importers updated, **including the three `scripts/update/` consumers**
  (`run.py:807,810`, `mcp_memory.py:281,283`, `verify.py:1046`) — these must
  change in the same commit that removes the constant or `/update` crashes at
  import. `granite_classifier.DEFAULT_MODEL` repointed to
  `OLLAMA_CLASSIFIER_MODEL` so the granite model id lives in one place.
- **Coupling**: reduces local model sprawl on every machine (one local
  instruct model instead of two). Classification model centralized in
  `config/models.py`; generation model centralized in the per-machine setting.
- **Data ownership**: title-generator content moves from local-only inference
  to Ollama Cloud egress (private-stripped, with a defensive runtime strip — see
  Risk 3 / Concern resolution).
- **Reversibility**: code revert restores the constants and gemma4:e2b is still
  re-pullable, but `ollama rm gemma4:e2b` (superseded-cleanup) is irreversible
  per-machine without a manual re-pull. To keep the in-flight transition window
  recoverable, the `rm` is **gated on the granite smoke-test having passed
  earlier in the same `/update` run** (see Update System) — a machine never
  deletes its old generation model in the same run that proves the new
  classification model is broken.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm cloud-vs-local split for title generation; confirm cloud model)
- Review rounds: 1 (classification-behavior drift is the main review concern)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Ollama installed | `ollama --version` | Local inference runtime |
| Granite model present | `ollama list \| grep -q granite4.1:3b` | Classification target |
| Ollama Cloud signed in (cloud machines) | `ollama list \| grep -q ':cloud'` | Generation target (cloud variant) |
| gemma4:31b-cloud reachable | `ollama show gemma4:31b-cloud >/dev/null 2>&1` | Cloud generation model exists |

Run all checks: `python scripts/check_prerequisites.py docs/plans/gemma4_ollama_consolidation.md`

## Solution

### Key Elements

- **Centralized model constants** (`config/models.py`): introduce
  `OLLAMA_CLASSIFIER_MODEL = "granite4.1:3b"`. Remove `OLLAMA_LOCAL_MODEL`.
  Add `gemma4:e2b` to `OLLAMA_SUPERSEDED_MODELS`.
- **Per-machine generation-model setting** (`config/settings.py`): add
  `ModelSettings.ollama_generation_model` (env `MODELS__OLLAMA_GENERATION_MODEL`),
  **default `"gemma4:31b-cloud"`**. The generation sites read this setting. The
  field is kept so a future RAM-rich machine can override to a local tag via env
  — but this plan ships **cloud-only**: no RAM detection, no `/setup`
  variant-selection logic, no `MIN_LOCAL_GEN_RAM_GB` constant, no local-MLX code
  path.
- **`ensure_generation_model()` helper** (new, in **`config/models.py`**): probe
  the configured generation tag, return `(model_available: bool, detail: str)`.
  It is a config-layer detection helper, **not** a startup gate — it is reused by
  `/setup`, `/update`, and the title-generator, all of which treat a `False`
  result per their own failure-cost profile (warning for `/update`; `None`/skip
  for title-gen; hard-fail for the ai-judge — see "typed signal" below). For a
  `:cloud` tag it is a near-no-op: a signed-in cloud tag is always reported
  available, so the only real check is the cloud-signin warning. (The "mirrors
  `ensure_granite_model()`" framing is dropped — granite's is a hard worker
  precondition; this one is not.)
- **`/setup` + `/update` cloud availability** (cloud-only): `/setup` writes
  `MODELS__OLLAMA_GENERATION_MODEL=gemma4:31b-cloud` **unconditionally** (no RAM
  branch). Both `/setup` and `/update` ensure the configured tag is reachable and
  surface a **warning** if Ollama Cloud isn't signed in (`:cloud` tag with no
  `:cloud` entry in `ollama list`). Warning-only — never suppresses the service
  restart or blocks the worker.
- **Classification bucket → granite** (5 sites): the three `bridge/routing.py`
  classifiers, `reflections/memory_management.py::_gemma_classify`, and
  `tools/email_cs/triage.py` tier-1.
- **Generation bucket → configurable gemma4:31b** (2 sites): `title_generator`
  and the test `ai_judge`, both reading `ollama_generation_model`.
- **Dead-config cleanup**: remove the unused `ModelSettings.ollama_vision_model`
  field (no code consumers) — or repoint if a consumer is found in build.
- **`/update` retirement of gemma**: superseded-models cleanup pulls gemma off
  every machine over time; the local-model smoke test targets granite.

### Decision table (each current gemma4 call site)

| # | Call site | Task shape | Hot path? | Existing fallback | → Target |
|---|-----------|-----------|-----------|-------------------|----------|
| 1 | `routing.classify_needs_response` | binary classify | yes | none (defaults True) | **granite** |
| 2 | `routing.classify_terminus` | 3-way classify | yes | Haiku | **granite** |
| 3 | `routing._classify_work_request_llm` | 4-way classify | yes | Haiku | **granite** |
| 4 | `reflections._gemma_classify` | structured JSON | no | fail-soft None | **granite** |
| 5 | `email_cs.triage` (tier-1) | structured JSON | no | escalate | **granite** |
| 6 | `title_generator` | free-text gen | no | fail-soft no-op (skip on unavailable) | **gemma4:31b-cloud** (via `ollama_generation_model`) |
| 7 | `tests/ai_judge.judge` | free-text/eval | no (test) | OpenRouter free-tier | **gemma4:31b-cloud** (same setting; hard-fails first call on misconfig) |

### Flow

Incoming message → routing classifier → granite (resident, local) → decision.
Memory save → title generator → `ollama_generation_model` (`gemma4:31b-cloud`)
→ title persisted (or skipped if the model reports unavailable).
Local Ollama steady state on every machine: **granite4.1:3b + nomic-embed-text
only**; generation runs in the cloud.

### Technical Approach

- Repoint by constant, not by scattering model strings. Each site's parsing
  logic (single-word vs JSON vs tool-call vs HTTP) stays as-is; only the model
  argument changes. This keeps the diff small and reviewable.
- For the two structured sites (#4, #5), default to the existing
  emit-JSON-and-parse path on granite (lowest risk). If spike-3 shows JSON is
  flaky, switch them to granite native tool-calling using the
  `granite_classifier.py` pattern.
- Preserve every existing fallback and fail-soft default verbatim — this
  migration must not change failure semantics, only the primary model.
- **`classify_needs_response` parse guard (Blocker 3)**: the current
  `"work" in result` substring test (`bridge/routing.py:542`) is brittle against
  granite's more verbose output — it false-positives on `"...work-related..."`
  and false-negatives when the literal token is absent. Before the substring
  test, bound the parse:
  `normalized = result.strip().lower(); if len(normalized) > 30: raise ValueError("oversized classifier output")`
  so oversized/verbose granite output routes to the conservative bare-`except`
  `True` default (never silently mis-parses). Apply the same length-bound spirit
  to the terminus / work-request parsers if spike-1 shows verbose granite output.
- **Generation helper typed signal (Concern: Skeptic/Simplifier)**:
  `ensure_generation_model()` returns `(model_available: bool, detail: str)`.
  The two generation call sites have **opposite failure-cost profiles** and must
  consume it differently:
  - `title_generator`: on `model_available is False`, **return `None` and skip
    persistence** — never persist an empty/garbage title.
  - `tests/ai_judge/judge.py`: **HARD-fail at first call** if the model is
    unavailable, so CI catches a misconfiguration instead of silently emitting
    unreliable verdicts that could pass a bad build.
- **Title-gen defensive private strip (Concern: Adversary)**: cloud is now the
  default generation target, so an un-audited future caller that forgets
  `strip_private` would exfiltrate raw `<private>` content off-machine,
  asynchronously and invisibly. Add a runtime guard inside
  `title_generator._do_generate` *before* the HTTP call:
  `if "<private>" in text: logger.warning("title_generator: unstripped private tag — stripping defensively"); text = strip_private(text)`
  (import `strip_private` from `agent.private_tag`). Known callers already strip;
  this turns silent exfiltration into an observable defensive strip for any
  future caller, with zero breakage.
- Title generator (#6) and ai-judge (#7) call through the same client/CLI they
  already use; only the model id changes — and it now comes from
  `settings.models.ollama_generation_model` (default `gemma4:31b-cloud`) rather
  than a hardcoded constant. Both paths stay fail-soft except the ai-judge's
  deliberate hard-fail-on-misconfig above.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `routing.py` classifiers catch `Exception` and fall back (Haiku or conservative default) — keep existing tests asserting fallback fires when the local model is unreachable; update them to monkeypatch the new constant name.
- [ ] `_gemma_classify` returns `None` on any failure (fail-soft) — assert unchanged with granite as the model.
- [ ] `title_generator._post_ollama_generate` returns `None` on URL/timeout error — add a test that a cloud timeout leaves `Memory.title` unchanged.
- [ ] `email_cs.triage` escalates on model failure — assert escalation still fires with granite.

### Empty/Invalid Input Handling
- [ ] Empty/whitespace message → `classify_needs_response` fast-paths to False before any model call (unchanged); cover with existing test.
- [ ] Granite returns garbage (non-label / unparseable JSON) → classifiers fall through to fallback/default; add a granite-returns-garbage case per structured site.
- [ ] Empty title from cloud → `_normalize_title` yields `""` → no save (unchanged).

### Error State Rendering
- [ ] Hot-path classification failure must never drop a genuine work message — assert the conservative `RESPOND`/`True` default on total model failure (no API key, ollama + cloud both down).
- [ ] **`classify_needs_response` oversized-output guard**: feed a >30-char granite response (e.g. `"this looks work-related to me, so respond"`) and assert it routes to the conservative `True` default via the `ValueError`→bare-except path, NOT a substring false-positive.
- [ ] **`title_generator` defensive private strip**: pass text containing `<private>...</private>` directly to `_do_generate`; assert it logs the warning and the HTTP payload contains no `<private>` content.
- [ ] **`ensure_generation_model()` typed signal**: assert `title_generator` returns `None` (skips persistence) when the helper reports `model_available=False`; assert `tests/ai_judge/judge.py` raises (hard-fails) at first call under the same condition.
- [ ] **`/update` superseded-rm gate**: assert `ollama rm gemma4:e2b` is NOT invoked when the granite smoke-test failed earlier in the same run (mock the smoke-test boolean).

## Test Impact

- [ ] `tests/unit/test_routing.py` — UPDATE: tests monkeypatch `routing.OLLAMA_LOCAL_MODEL` (lines ~138, ~213) and inject fake `ollama` modules. Repoint to the new `OLLAMA_CLASSIFIER_MODEL` constant name; assertions on fallback behavior stay.
- [ ] `tests/unit/test_reflections_memory.py` — UPDATE: `_gemma_classify` tests reference the model constant; repoint to granite constant, keep fail-soft assertions.
- [ ] `tests/unit/test_memory_title_generator.py` — UPDATE: `_resolve_ollama_config` now returns `settings.models.ollama_generation_model` (default `gemma4:31b-cloud`); update expected model id and assert the setting is honored. Cloud still routes via the local client, so base_url is unchanged.
- [ ] `tests/unit/test_memory_title_writer_paths.py` — UPDATE: same model-id expectation change (read from setting, not hardcoded).
- [ ] `tests/unit/test_email_cs_triage.py` — UPDATE: repoint model constant to granite; keep escalation-on-failure assertions.
- [ ] `tests/ai_judge/judge.py` + `tests/ai_judge/test_ai_judge.py` — UPDATE: default `JudgeConfig.model` from `gemma4:e2b` to `settings.models.ollama_generation_model`; OpenRouter fallback stays.
- [ ] Add a `ModelSettings` test asserting `ollama_generation_model` defaults to `gemma4:31b-cloud` and is overridable via `MODELS__OLLAMA_GENERATION_MODEL`.
- [ ] `tests/unit/granite_container/test_cli.py` — VERIFY: references granite model; ensure no breakage from centralizing `DEFAULT_MODEL` into `config/models.py`.
- [ ] `tests/unit/test_pm_session_factory.py`, `tests/e2e/test_message_pipeline.py` — VERIFY: reference classification; confirm new constant name resolves.
- [ ] **`scripts/update/` import smoke (Blocker 1)** — ADD: `python -c "import scripts.update.run; import scripts.update.mcp_memory; import scripts.update.verify"` must exit 0 after `OLLAMA_LOCAL_MODEL` removal. Add as a CI/verification check (it has no module-level side effects gating on `do_ollama`).
- [ ] **`scripts/update/run.py` superseded-rm gate** — ADD: test that `ollama rm gemma4:e2b` is skipped when the granite smoke-test boolean is False in the same run.
- [ ] `tests/ai_judge/test_ai_judge.py` — additionally assert the ai-judge HARD-fails at first call when `ensure_generation_model()` reports unavailable (opposite of title-gen's skip).
- [ ] **No MLX/RAM test added** — the `MIN_LOCAL_GEN_RAM_GB` / `/setup` variant-selection path is cut from this plan (cloud-only), so no test covers it.

## Rabbit Holes

- **Rewriting every classifier to use native tool-calling.** Tempting ("granite is good at tools"), but the simple single-word classifiers work fine with plain prompting. Only the two structured-JSON sites are tool-calling candidates, and only if spike-3 shows JSON-emit is flaky.
- **Building a generic "local LLM router" abstraction** across all 7 sites. The sites have genuinely different parsing (word / JSON / tool-call / HTTP-generate / CLI). A shared wrapper would leak all four shapes. Centralize the *model id* in config; leave call sites' parsing local.
- **Migrating the embedding provider.** `nomic-embed-text` is a different model for a different job (vector embeddings); Ollama Cloud's instruct catalog is not a drop-in. Out of scope.
- **Re-tuning the gemma-mined few-shot prompts for granite.** Don't preemptively rewrite the issue-#1318 terminus examples. Run spike-1 first; only touch prompts if parity is poor.

## Risks

### Risk 1: Classification behavior drift
**Impact:** The routing prompts (especially terminus RESPOND/REACT/SILENT) were few-shot-tuned against gemma's behavior via real mined misclassifications (#1318). Granite may label differently, causing dropped messages or emoji spam.
**Mitigation:** The **spike-1 parity gate (blocking pre-build)** is the real protection against miscalibration — it must record per-classifier label agreement before `build-classification` starts. The Haiku fallback on the terminus + work-request sites covers granite *unavailability* (exception/timeout) only; it does **NOT** fire on a confident-but-wrong granite label, which is the dominant model-swap failure mode — so it is not a drift guard. `classify_needs_response` has no Haiku fallback at all; its parse guard (length-bound → conservative `True`) plus the spike-1 separate-sampling are its only protection. If a site's parity is poor, use shadow-mode (log granite, route gemma) until confirmed, or keep that site on its existing fallback/default rather than repointing.

### Risk 2: Hot-path latency / contention with active PTY sessions
**Impact:** When a granite PTY session is mid-translation, a concurrent bridge classification call queues behind it on the single GPU, adding latency to message routing.
**Mitigation:** granite calls are short (single label); Haiku fallback covers timeouts on 2/3 sites. spike-4 measures cold-start; add `keep_alive` if needed. Net memory pressure *drops* (one fewer resident model), which reduces eviction thrashing overall.

### Risk 3: Title-generator content egress to Ollama Cloud
**Impact:** Memory-title generation currently runs **local-only**. Cloud sends memory content off-machine — a privacy-posture change. `<private>` regions are stripped by known callers, but that stripping was a caller-side docstring convention, **not enforced** in `_do_generate`; an un-audited future caller could exfiltrate raw `<private>` content asynchronously (daemon thread), invisible to request-path logs.
**Mitigation:** A **defensive runtime strip is now enforced inside `_do_generate`** (see Technical Approach): if `<private>` survives to the call site, it is logged and stripped before the HTTP request — silent exfiltration becomes an observable defensive strip. This holds regardless of caller discipline. The `ollama_generation_model` env override remains the lever for a future RAM-rich machine to keep generation fully local; until then, cloud egress is private-stripped at two layers (caller + defensive).

### Risk 4: Cloud reachability / quota for frequent title generation
**Impact:** Title-gen fires on every memory save; cloud outage or rate limit could stall titles.
**Mitigation:** the path is fire-and-forget and fail-soft (timeout → title unchanged, stub falls back to category-only rendering). No user-visible failure. Ollama subscription covers expected volume.

### Risk 5: gemma4:e2b removed but granite broken (irreversible per-machine)
**Impact:** `ollama rm gemma4:e2b` during `/update` superseded-cleanup is irreversible without a manual re-pull. A rollback after granite mislabels would leave the machine pointing (post-code-revert) at a gemma binary that no longer exists locally; `mcp_memory.py`'s health check silently falls through to category-only titles.
**Mitigation:** Gate the `ollama rm gemma4:e2b` step on the **granite smoke-test having passed earlier in the same `/update` run** (a boolean set by the Step 4.75 smoke-test, checked immediately before the `rm`). `ollama rm` exits 0 even when the model is already absent, so the gate cannot rely on `rm`'s exit code — it must read the smoke-test boolean. This keeps the in-flight transition window recoverable and mirrors the `ensure_granite_model()` gate pattern.

(The earlier "wrong local-32B variant" risk is removed: this plan is cloud-only, with no local-MLX path to misconfigure. A future plan that reintroduces the local variant must restore that risk.)

## Race Conditions

No new race conditions identified. The migration changes only which model id is
passed to existing call sites; concurrency structure is unchanged. The
title-generator already runs in a daemon thread with fail-soft save semantics;
the memory-audit Layer 3 already bounds itself with a wallclock budget and a
single-thread executor. Neither timing contract changes.

## No-Gos (Out of Scope)

- **Local-MLX generation variant (`gemma4:31b-mlx`) — DEFERRED to a follow-up.** No fleet machine is currently confirmed RAM-rich enough to run a ~18-20 GB local 32B model alongside granite + nomic-embed + OS, and this 16 GB host cannot. Building RAM detection, a `MIN_LOCAL_GEN_RAM_GB` threshold, `/setup` variant selection, the mlx pull path, and the setup SKILL.md subsection would be future-proofing for a target that does not exist. The `ollama_generation_model` env key is retained (zero-cost) so the follow-up is a config flip + a small `ensure_generation_model()` local-pull branch, **gated on ≥1 fleet machine being confirmed capable**. This plan ships **cloud-only**.
- `[SEPARATE-SLUG]` Migrating `agent/embedding_provider.py` (`nomic-embed-text`) — different model class (embeddings), no cloud drop-in, no memory-contention problem. Not filed; genuinely separate concern, left local.
- Removing the old `granite3.2-vision` model from disk — unrelated stale artifact; the `/update` superseded-cleanup can absorb it separately if desired.
- Re-tuning classification prompts for granite beyond what spike-1 requires — prompt engineering is a follow-up only if parity testing demands it.

(Everything that *could* be finished in this plan — the 7 call-site repoints, the
config centralization, the dead `ollama_vision_model` cleanup, the `/update`
superseded-list edit, and all test updates — is **in scope**, not deferred.)

## Update System

This work **builds directly atop the just-landed granite config requirement**
(commits `98ca1b57` /update Step 4.75 gate, `52740fbb` worker Step 4b.5
precondition, `ensure_granite_model()` helper). The generation model gets the
same shape of treatment, with one deliberate difference: it is **soft**
(warning-only) everywhere, never a restart-suppressing gate or a worker hard
exit, because every generation call site is fail-soft.

- **Migrate the three `scripts/update/` importers in the SAME commit that
  removes `OLLAMA_LOCAL_MODEL` (Blocker 1).** `from config.models import
  OLLAMA_LOCAL_MODEL` is **module-level** at `run.py:807` (inside `Step 4`'s
  `if config.do_ollama:` block) — it raises `ImportError` before any try/except,
  so no in-step graceful degradation can save it. The migration of all three
  files must land atomically with the constant removal:
  - `run.py:807` import → drop `OLLAMA_LOCAL_MODEL`, keep
    `OLLAMA_SUPERSEDED_MODELS`. `run.py:810` → read
    `settings.models.ollama_generation_model` (drop the `OLLAMA_SUMMARIZER_MODEL`
    env fallback) and ensure it via `ensure_generation_model()`.
  - `mcp_memory.py:281` import → repoint to read the generation setting; **and
    fix the hardcoded `"gemma4:e2b"` fallback literal at `:283`** to the
    generation default (a pure import rename leaves the literal stale).
  - `verify.py:1046` → replace `os.getenv("OLLAMA_SUMMARIZER_MODEL",
    "gemma4:e2b")` with the generation setting (this consumer was missed by
    critique; found during revision).
  - **CI guard**: `python -c "import scripts.update.run; import
    scripts.update.mcp_memory; import scripts.update.verify"` must exit 0 after
    the constant removal (added to the Verification table).
- **`config/models.py::OLLAMA_SUPERSEDED_MODELS`**: add `"gemma4:e2b"`. The
  `/update` Step 4 "Cleaning up superseded Ollama models" then `ollama rm`s it on
  every machine over time. Granite is already pulled by its own Step 4.75; no new
  pull needed for classification.
- **Gate the gemma `rm` on the granite smoke-test (Concern: Operator)**: the
  superseded-cleanup loop must only run `ollama rm gemma4:e2b` if the **Step 4.75
  granite smoke-test passed earlier in the same `/update` run** (a boolean set by
  that step, checked immediately before the `rm`). `ollama rm` exits 0 even when
  the model is already absent, so the gate reads the smoke-test boolean, not the
  `rm` exit code. This keeps the transition window recoverable if granite is
  broken on this run.
- **`/update` Step 4 repoint (cloud-only)**: Step 4 currently pulls/smoke-tests
  `OLLAMA_LOCAL_MODEL` (gemma4:e2b). Repoint it to ensure the configured
  **generation** model instead — read `settings.models.ollama_generation_model`
  (always `gemma4:31b-cloud` in this plan), call `ensure_generation_model()`. For
  a `:cloud` tag this is a near-no-op reachability/signin check, NOT a heavy local
  pull. The classifier (granite) is already covered by Step 4.75, so Step 4 no
  longer needs to touch gemma4:e2b except to retire it.
- **`/setup` (cloud-only, NO RAM logic)**: `/setup` writes
  `MODELS__OLLAMA_GENERATION_MODEL=gemma4:31b-cloud` **unconditionally** — no
  `sysctl` RAM detection, no `MIN_LOCAL_GEN_RAM_GB`, no variant branch, no setup
  SKILL.md "generation model" subsection (all cut per Blocker 4). It then ensures
  the cloud tag is reachable via the helper.
- **Cloud signin precondition**: because the generation model is a `:cloud` tag,
  both `/setup` and `/update` surface a **warning** (not block) if Ollama Cloud
  isn't signed in (`ollama list` shows no `:cloud` entry). Detection-only,
  consistent with the gws-auth and granite patterns.
- No new config files. `OLLAMA_VISION_MODEL` env override is removed alongside
  the dead setting (verify no machine sets it before deleting).

## Agent Integration

No agent integration required — this is a bridge/worker-internal model-routing
change. The agent's tool surface, MCP servers, and bridge message handling are
unchanged. The bridge already imports `routing.py` directly; only the model id
inside those functions changes. Integration coverage is provided by the existing
`tests/e2e/test_message_pipeline.py` classification path (updated for the new
constant name).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — note title generation now runs on the configured `ollama_generation_model` (Ollama Cloud `gemma4:31b-cloud`) instead of local gemma4:e2b, with a defensive `<private>`-strip inside `_do_generate`.
- [ ] Update `docs/features/granite-pty-production.md` (or the granite classifier doc) — note granite4.1:3b now also serves bridge message classification; document the "local Ollama = granite + nomic-embed-text only" steady state on every machine; note `ensure_generation_model()` lives in `config/models.py` as a detection helper (NOT a startup gate like `ensure_granite_model()`).
- [ ] Add/refresh a short "Local model policy" note: classification → granite (hard precondition), generation → Ollama Cloud `gemma4:31b-cloud` (soft, env-overridable), embeddings → nomic-embed-text. Index it in `docs/features/README.md`.
- [ ] Document the `MODELS__OLLAMA_GENERATION_MODEL` env override (cloud-only default; local-MLX variant is a deferred follow-up) in the setup guide — no RAM-threshold logic to document.

### Inline Documentation
- [ ] Update the `config/settings.py` ModelSettings docstrings (remove the dead vision-model field; clarify `ollama_host` now serves granite + cloud).
- [ ] Update `config/models.py` section comments: "LOCAL OLLAMA MODELS" → document the classifier/cloud split and the superseded gemma entry.
- [ ] Update docstrings at each repointed call site to name granite/cloud, not gemma.

## Success Criteria

- [ ] `grep -rn "gemma4:e2b\|OLLAMA_LOCAL_MODEL" --include=*.py .` returns no hits in `bridge/`, `reflections/`, `tools/`, `config/`, `agent/`, `tests/`, **`scripts/`** (excluding `OLLAMA_SUPERSEDED_MODELS`'s historical `"gemma4:e2b"` entry and `.claude/worktrees/`).
- [ ] `python -c "import scripts.update.run; import scripts.update.mcp_memory; import scripts.update.verify"` exits 0 after the constant removal (Blocker 1 guard).
- [ ] `config/models.py` defines `OLLAMA_CLASSIFIER_MODEL` and `ensure_generation_model()`; `OLLAMA_LOCAL_MODEL` is gone. `config/settings.py` defines `ollama_generation_model` (default `gemma4:31b-cloud`, env-overridable).
- [ ] `gemma4:e2b` is in `OLLAMA_SUPERSEDED_MODELS`; `/update` Step 4 ensures the configured generation tag (cloud reachability check, no heavy local pull); the gemma `rm` is gated on the granite smoke-test passing in the same run.
- [ ] `ensure_generation_model()` lives in `config/models.py`, returns `(model_available, detail)`, and is consumed per call-site profile (warning for `/update`, `None`/skip for title-gen, hard-fail for ai-judge) — never exits the worker or suppresses restart.
- [ ] `/setup` writes `MODELS__OLLAMA_GENERATION_MODEL=gemma4:31b-cloud` unconditionally (cloud-only; no RAM detection, no `MIN_LOCAL_GEN_RAM_GB`).
- [ ] `classify_needs_response` has a length-bound parse guard routing oversized granite output to the conservative `True` default.
- [ ] `title_generator._do_generate` defensively strips `<private>` before the cloud call.
- [ ] All 7 call sites pass their (updated) unit tests; fallback/fail-soft semantics unchanged.
- [ ] spike-1 parity is recorded and gated `build-classification` (blocking); spike-2 cloud reachability, spike-3 granite JSON, spike-4 cold-start results recorded in Spike Results.
- [ ] Dead `ollama_vision_model` setting removed (or repointed if a consumer surfaces).
- [ ] After a manual run on this machine, `ollama ps` under load shows granite (not gemma) serving classification.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (config-and-classification)**
  - Name: `classifier-builder`
  - Role: Centralize model constants + `ensure_generation_model()` in `config/models.py`; remove `OLLAMA_LOCAL_MODEL` AND migrate the three `scripts/update/` importers in the same commit (Blocker 1); add the `classify_needs_response` parse guard; repoint the 5 classification sites (routing ×3, memory-audit, email-triage) to granite (gated on spike-1 parity); update their unit tests.
  - Agent Type: builder
  - Resume: true

- **Builder (generation-and-cloud)**
  - Name: `cloud-builder`
  - Role: Repoint title-generator + ai-judge to the cloud model (reading `ollama_generation_model`); wire the `ensure_generation_model()` typed signal (title-gen skips, ai-judge hard-fails); add the defensive `<private>`-strip in `_do_generate`; update their unit tests. (The dead `ollama_vision_model` removal is in build-config.)
  - Agent Type: builder
  - Resume: true

- **Builder (setup/update integration)**
  - Name: `update-builder`
  - Role: Add `ensure_generation_model()` helper in `config/models.py` (detection helper returning `(model_available, detail)`, NOT a startup gate); add gemma4:e2b to `OLLAMA_SUPERSEDED_MODELS`; migrate the three `scripts/update/` importers (`run.py`, `mcp_memory.py`, `verify.py`) in the same commit that removes `OLLAMA_LOCAL_MODEL`; repoint `/update` Step 4 to ensure the cloud generation model and gate the gemma `rm` on the granite smoke-test; write `MODELS__OLLAMA_GENERATION_MODEL=gemma4:31b-cloud` unconditionally in `/setup` (no RAM logic); add cloud-signin detection warning. All warning-level (no restart suppression, no worker gate).
  - Agent Type: builder
  - Resume: true

- **Validator (migration)**
  - Name: `migration-validator`
  - Role: Verify no gemma references remain, fallbacks intact, all tests green, `ollama ps` shows the new steady state.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `migration-documentarian`
  - Role: Update feature docs + inline docs per the Documentation section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Run spikes (parity, cloud, JSON, cold-start)
- **Task ID**: spike-all
- **Depends On**: none
- **Validates**: Spike Results section populated
- **Assigned To**: classifier-builder (dispatches the four spikes in parallel worktrees)
- **Agent Type**: builder
- **Parallel**: true
- Replay classification inputs through granite vs gemma (spike-1)
- Probe cloud model reachability + latency (spike-2)
- Measure granite JSON/tool-call reliability (spike-3)
- Measure granite cold-start (spike-4)
- Record findings; decide JSON-emit vs tool-call for structured sites, and cloud-vs-local for title-gen pending Open Question 1

### 2. Centralize model constants
- **Task ID**: build-config
- **Depends On**: spike-all
- **Validates**: tests/unit/test_routing.py imports resolve
- **Informed By**: spike-2 (cloud model id)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `OLLAMA_CLASSIFIER_MODEL`; add `ensure_generation_model()` (detection helper, `(model_available, detail)`); add gemma4:e2b to superseded list
- Add `ModelSettings.ollama_generation_model` (default `gemma4:31b-cloud`, env `MODELS__OLLAMA_GENERATION_MODEL`); remove dead `ollama_vision_model`
- **Remove `OLLAMA_LOCAL_MODEL` AND migrate the three `scripts/update/` importers (`run.py:807,810`, `mcp_memory.py:281,283`, `verify.py:1046`) in the SAME commit** (Blocker 1 — module-level import crashes `/update` otherwise). Verify `python -c "import scripts.update.run; import scripts.update.mcp_memory; import scripts.update.verify"` exits 0.
- Repoint `granite_classifier.DEFAULT_MODEL` to the shared constant

### 3. Repoint classification sites (granite)
- **Task ID**: build-classification
- **Depends On**: build-config
- **Validates**: tests/unit/test_routing.py, tests/unit/test_reflections_memory.py, tests/unit/test_email_cs_triage.py
- **Informed By**: spike-1 (BLOCKING — must record per-classifier parity before this task starts), spike-3
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- **Gated on spike-1**: do not repoint a classifier whose parity is poor — use shadow-mode or keep its existing fallback/default (especially `classify_needs_response`, which has no Haiku fallback)
- Add the length-bound parse guard to `classify_needs_response` (oversized granite output → conservative `True`)
- Repoint routing ×3, `_gemma_classify`, email triage; preserve every fallback/default
- Update tests to monkeypatch the new constant name

### 4. Repoint generation sites (cloud) + dead-config cleanup
- **Task ID**: build-generation
- **Depends On**: build-config
- **Validates**: tests/unit/test_memory_title_generator.py, tests/unit/test_memory_title_writer_paths.py, tests/ai_judge/test_ai_judge.py
- **Informed By**: spike-2
- **Assigned To**: cloud-builder
- **Agent Type**: builder
- **Parallel**: true
- Repoint title-generator + ai-judge to read `settings.models.ollama_generation_model`
- Wire the `ensure_generation_model()` typed signal: title-gen returns `None`/skips persistence on unavailable; ai-judge HARD-fails at first call on unavailable
- Add the defensive `<private>`-strip inside `title_generator._do_generate` (import `strip_private` from `agent.private_tag`)
- (the dead `ollama_vision_model` setting is removed in build-config)

### 5. Setup/update generation-model integration (atop granite pattern)
- **Task ID**: build-update
- **Depends On**: build-config
- **Validates**: tests covering the `/update` Step 4 generation path + the granite-gated gemma `rm`
- **Informed By**: spike-2 (cloud latency)
- **Assigned To**: update-builder
- **Agent Type**: builder
- **Parallel**: true
- Repoint `/update` Step 4: ensure the cloud `ollama_generation_model` via `ensure_generation_model()` (cloud reachability check, NOT a heavy local pull); the constant removal + `scripts/update/` importer migration already landed in build-config
- **Gate `ollama rm gemma4:e2b`** on the Step 4.75 granite smoke-test boolean (read the boolean, not `rm`'s exit code — `rm` exits 0 when absent)
- Write `MODELS__OLLAMA_GENERATION_MODEL=gemma4:31b-cloud` unconditionally in `/setup` (cloud-only — NO `sysctl` RAM detection, NO `MIN_LOCAL_GEN_RAM_GB`, NO setup SKILL.md variant subsection)
- Add cloud-signin detection warning when the configured tag ends in `:cloud`

### 6. Migration validation
- **Task ID**: validate-migration
- **Depends On**: build-classification, build-generation, build-update
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep-confirm no gemma references; run all affected tests; verify fallbacks; check `ollama ps` steady state

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-migration
- **Assigned To**: migration-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update subconscious-memory + granite docs; add local-model policy note; refresh inline docs

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm all success criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No gemma in source | `grep -rn "gemma4:e2b" bridge/ reflections/ tools/ config/ agent/ scripts/ \| grep -v SUPERSEDED` | exit code 1 |
| Old constant gone | `grep -rn "OLLAMA_LOCAL_MODEL" --include=*.py bridge/ reflections/ tools/ config/ agent/ scripts/` | exit code 1 |
| scripts/update importable after removal | `python -c "import scripts.update.run; import scripts.update.mcp_memory; import scripts.update.verify"` | exit code 0 |
| New classifier constant present | `python -c "from config.models import OLLAMA_CLASSIFIER_MODEL"` | exit code 0 |
| Generation setting present | `python -c "from config.settings import settings; assert settings.models.ollama_generation_model"` | exit code 0 |
| gemma superseded | `python -c "from config.models import OLLAMA_SUPERSEDED_MODELS as s; assert 'gemma4:e2b' in s"` | exit code 0 |
| Generation ensure-helper exists | `python -c "from config.models import ensure_generation_model"` | exit code 0 |
| Routing tests pass | `pytest tests/unit/test_routing.py -q` | exit code 0 |
| Memory + triage tests pass | `pytest tests/unit/test_reflections_memory.py tests/unit/test_memory_title_generator.py tests/unit/test_email_cs_triage.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-06-12. Verdict: NEEDS REVISION (4 blockers). -->
<!-- REVISION APPLIED 2026-06-12: all 4 blockers + all 6 concerns resolved (see Addressed By column). -->

**Findings: 11 total (4 blockers, 6 concerns, 1 nit-equivalent rolled up). Critics: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor. All resolved in the revision pass.**

| Severity | Critic(s) | Finding | Addressed By | Implementation Note |
|----------|-----------|---------|--------------|---------------------|
| BLOCKER | Operator, Archaeologist, Consistency | `scripts/` is excluded from both the Success-Criteria grep (L422) and Verification table (L558-559), yet `scripts/update/run.py:790,793` imports `OLLAMA_LOCAL_MODEL` and `scripts/update/mcp_memory.py:281` imports it (with a hardcoded `"gemma4:e2b"` fallback at :283). Removing the constant crashes `/update` Step 4 at import time on every machine before granite is smoke-tested. The plan's "All importers updated" claim (L181) is unverifiable by its own greps. | RESOLVED — build-config migrates all three `scripts/update/` importers (run.py, mcp_memory.py, **and verify.py:1046**, which critique missed) in the same commit as the removal; `scripts/` added to both greps + Verification table; import-smoke check added. | Module-level `from config.models import OLLAMA_LOCAL_MODEL` in run.py:790 crashes before any try/except, so no in-step graceful degradation helps — the migration of both files MUST land in the same commit that removes the constant. Repoint run.py to `OLLAMA_GENERATION_MODEL` and drop the `OLLAMA_SUMMARIZER_MODEL` env fallback; edit mcp_memory.py:281 import **and** the hardcoded `"gemma4:e2b"` string at :283 (a pure rename leaves it stale). Add `scripts/` to both greps. CI guard: `python -c "import scripts.update.run; import scripts.update.mcp_memory"` must exit 0 after removal. |
| BLOCKER | Consistency, Archaeologist, Simplifier | `ensure_generation_model` has two contradictory home modules: Architecture (L177) / Solution (L227) say it "mirrors `ensure_granite_model()`" (which lives in `agent/granite_container/granite_classifier.py:54`), but the Verification table (L563) imports it from `tools.memory_search.title_generator`. The two modules sit in different execution paths (agent-layer startup bootstrap vs. lazy memory-save tool). | RESOLVED — pinned to `config/models.py` as a detection helper (NOT a startup gate); "mirrors ensure_granite_model" framing dropped; Architecture/Solution/Verification all import from `config.models`. | Pin ONE canonical home and make L177/L227/L563 agree. If it lives in `tools.memory_search.title_generator`, drop the "parallels ensure_granite_model" framing — it is a tool-local call-site guard, NOT a worker-startup gate, and the Verification import path is correct. If it is meant as a startup gate, place it in `config/models.py` or `agent/` and fix the L563 import path. A mismatched path makes the L563 smoke command silently raise ImportError, invalidating the only automated check for the new helper. |
| BLOCKER | Skeptic, Adversary, User | Classification parity is unverified (spike-1 "[filled after spike]") yet the plan is Ready, and `classify_needs_response` (bridge/routing.py:519-546) has NO Haiku fallback. The Haiku fallback on the other 2/3 sites fires only on granite *failure*, never on granite returning a *confident wrong label* — the dominant model-swap failure mode. The `"work" in result` substring parser is brittle against granite's verbose output (false positive on "...work-related...", false negative when the literal token is absent → silent dropped message). | RESOLVED — spike-1 made a blocking pre-build gate (per-classifier parity, `classify_needs_response` sampled separately); length-bound parse guard (>30 chars → conservative `True`) added to Technical Approach + build-classification; shadow-mode named as the fallback for poor parity. | Make spike-1 a blocking pre-build gate requiring per-classifier label agreement on the #1318 mined-misclassification corpus, sampling `classify_needs_response` separately (no fallback there). Add a length-bounded parse guard before the substring test: `normalized = result.strip().lower(); if len(normalized) > 30: raise ValueError(...)` so oversized granite output routes to the conservative bare-except `True` path instead of a false substring match. Consider shadow-mode (log granite, route gemma) until parity confirmed. |
| BLOCKER | Simplifier | The local-MLX variant machinery (RAM measurement, `MIN_LOCAL_GEN_RAM_GB` threshold, `/setup` variant selection, setup SKILL.md subsection) builds a per-machine abstraction for a deployment target that does not exist: the 16 GB host is cloud-only, `gemma4:31b-mlx` is "not found" locally, and no fleet machine is confirmed RAM-rich. Future-proofing dressed as configuration; the core problem is solved by the classification repoint + superseded-cleanup alone. | RESOLVED — collapsed to cloud-only: deleted RAM detection, `MIN_LOCAL_GEN_RAM_GB`, `/setup` variant selection, mlx code path, and the setup SKILL.md subsection. Env key + `ModelSettings` field kept (zero-cost); mlx branch deferred to a follow-up (No-Gos) gated on ≥1 confirmed-capable machine. `ensure_generation_model()` kept but reduced to a near-no-op cloud reachability check. | Collapse `/setup` to write `MODELS__OLLAMA_GENERATION_MODEL=gemma4:31b-cloud` unconditionally; delete RAM detection, the threshold constant, the mlx code path, and the setup SKILL.md subsection. Keep the env key + `ModelSettings.ollama_generation_model` field (zero-cost). Defer the mlx branch to a follow-up gated on "≥1 fleet machine confirmed capable." If generation is cloud-only after this collapse, `ensure_generation_model()` reduces to a near-no-op (cloud tags are always "available") and may be deletable — resolving the location-ambiguity blocker too. |
| CONCERN | Adversary | Privacy stripping for title-gen is a caller-side docstring convention (title_generator.py:14,161), not enforced in `_do_generate`. With cloud now the DEFAULT generation target, any un-audited caller that forgets `strip_private` exfiltrates raw `<private>` content off-machine, asynchronously (daemon thread), invisible to request-path logs. Risk 3 asserts the mitigation "is already mandatory at the call site" but it is unenforced. | RESOLVED — defensive runtime `<private>`-strip added inside `_do_generate` (Technical Approach + build-generation task + failure-path test); Risk 3 reworded to claim two-layer (caller + defensive) stripping. | Add a defensive runtime guard inside `_do_generate` before the HTTP call: `if "<private>" in text: logger.warning("title_generator: unstripped private tag — stripping defensively"); text = strip_private(text)` (import from `agent.private_tag`). Converts silent exfiltration into an observable defensive strip with zero caller breakage. Known callers (memory_search/__init__.py:266, knowledge/indexer.py:390, memory_extraction.py:456) already strip; this guards future ones. |
| CONCERN | User | The Risk section frames "Haiku fallback on 2/3 sites" as drift protection, but it only triggers on granite exception/timeout, not on a confident-but-wrong label — overstating the actual protection for the dominant model-swap failure mode. | RESOLVED — Risk 1 reworded: Haiku fallback covers *unavailability* only, NOT miscalibration; spike-1 parity gate named as the real miscalibration guard; shadow-mode named as the honest interim protection. | Reword Risk 1 to state the fallback covers granite *unavailability*, not *miscalibration*; pair it with the spike-1 parity gate (above) which is the real miscalibration guard. Optionally add shadow-mode as the honest protection: route gemma labels while logging granite until parity is confirmed. |
| CONCERN | Skeptic, Simplifier | `ensure_generation_model()` is warning-only (fail-soft), so a configured-but-absent generation model silently degrades: title_generator persists empty/garbage titles, and `tests/ai_judge/judge.py` produces unreliable verdicts that could pass bad builds. The "mirrors ensure_granite_model" framing implies startup-precondition parity it does not have. | RESOLVED — helper returns `(model_available: bool, detail)`; title-gen returns `None`/skips persistence on unavailable, ai-judge HARD-fails at first call (opposite profiles wired in build-generation + tests); "mirrors ensure_granite_model" framing dropped. | Keep warning-only for the bridge/worker path, but give the helper a typed signal (`model_available: bool` or raise `GenerationModelUnavailableError`). title_generator returns `None` → caller skips persistence (no garbage title). judge.py should HARD-fail at first call so CI catches misconfiguration rather than silently passing — the two call sites have opposite failure-cost profiles. If the plan collapses to cloud-only (Simplifier blocker), the cloud tag is always available and this helper may be removable. |
| CONCERN | Operator | Reversibility is overstated. Code revert restores the constants, but `ollama rm gemma4:e2b` (superseded-cleanup) is irreversible per-machine without a manual re-pull. A 3am rollback after granite mislabels leaves machines pointing (post-revert) at a model binary that no longer exists locally; mcp_memory.py's health check silently falls through to "category-only" titles. | RESOLVED — `/update` gemma `rm` gated on the Step 4.75 granite smoke-test boolean (reads the boolean, not `rm` exit code); wired in build-update + Update System + Risk 5 + failure-path test. | Gate the `ollama rm gemma4:e2b` step on the granite smoke-test having passed earlier in the same `/update` run (boolean set by smoke-test, checked immediately before `rm`). `ollama rm` exits 0 even when the model is already absent, so do not rely on its exit code. Keeps at least the in-flight transition window recoverable. Mirror the `ensure_granite_model()` gate pattern. |

---

## Resolved Decisions

All three plan-time questions were settled by the supervisor on 2026-06-12:

1. **Generation destination — Ollama Cloud (cloud-only this plan).** Generation
   tasks (title-gen, ai-judge) use `gemma4:31b-cloud` via the per-machine setting
   `ollama_generation_model` (default `gemma4:31b-cloud`). The env key is kept so
   a future RAM-rich machine can override to a local tag, but the local-MLX path
   is **deferred** (see No-Gos) — no machine in the fleet is confirmed capable.
2. **Which model — `gemma4:31b-cloud`.** Confirmed real via `ollama show` (32B,
   BF16, 262k ctx). The cloud tag is a lightweight pointer that fits every
   machine including this 16 GB host.
3. **ai-judge uses the same setting** as title-gen. Its existing OpenRouter
   free-tier fallback stays as a safety net, BUT the ai-judge HARD-fails at first
   call if `ensure_generation_model()` reports the configured model unavailable —
   so CI catches a misconfiguration instead of silently passing on unreliable
   verdicts (Concern resolution).
4. **Configured properly during /setup and /update**, built atop the granite
   config pattern that just landed (`98ca1b57`, `52740fbb`). `/setup` writes the
   cloud tag unconditionally (no RAM branch); `/update` ensures cloud
   reachability and gates the gemma `rm` on the granite smoke-test. All
   warning-level — generation is fail-soft, not a hard precondition like granite.

**Revision note (2026-06-12):** This plan was revised to resolve 4 critique
blockers + 6 concerns. Key changes: (a) `scripts/update/` importers migrate in
the same commit as the constant removal; (b) `ensure_generation_model()` is
pinned to `config/models.py` as a detection helper (not a startup gate); (c)
spike-1 parity is a blocking pre-build gate, plus a length-bound parse guard on
`classify_needs_response`; (d) the local-MLX / RAM-threshold machinery is cut —
this plan ships cloud-only, MLX deferred to a follow-up.
