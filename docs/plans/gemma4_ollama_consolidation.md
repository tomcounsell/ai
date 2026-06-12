---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1636
last_comment_id:
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
`gemma4:e2b` is removed from local Ollama entirely. Every current gemma4 call
site is repointed to one of two destinations following a simple rule:

- **Classification / structured-output tasks → `granite4.1:3b`** (the model
  already resident for PTY work — reuse it, zero extra memory, zero network
  latency, and granite is strong at tool-structured output).
- **Free-text generation tasks → an Ollama Cloud model** (append the `:cloud`
  tag — offloads compute off the local GPU; the machine already has cloud
  signed in, `glm-5.1:cloud` is pulled).

End state: local Ollama runs `granite4.1:3b` (classifier + message
classification) and `nomic-embed-text` (embeddings) only.

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

**Empirical environment check (`ollama list` / `ollama ps`):**
- `granite4.1:3b` present (2.1 GB). `gemma4:e2b` present (7.2 GB), currently resident.
- `glm-5.1:cloud` present (cloud signed in 8 days ago) — cloud path is live, de-risks the generation bucket.
- `nomic-embed-text` present (embeddings, out of scope).

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **Issue #671**: *Standardize all Ollama usage on gemma4:e2b, add cleanup and smoke test to /update* — established the current single-local-model state. This plan **partially reverses** #671: the "one local model" principle is kept, but the model becomes granite (already needed for PTY), and gemma is retired. The `/update` cleanup + smoke-test machinery #671 built is reused (add gemma4 to the superseded list, point the smoke test at granite).
- **Issue #1231**: *Memory health audit: 3-layer reflection (… gemma classification)* — created `_gemma_classify` (Layer 3). This plan repoints that call to granite; its fail-soft contract is unchanged.
- **Issue #1573**: *Email customer-service auto-reply (two-tier triage)* — created `tools/email_cs/triage.py` tier-1 gemma call. Repointed to granite here.
- **Issue #1542 / #1572**: granite PTY operator — introduced `granite4.1:3b` as a second local model and the `granite_classifier.py` tool-calling pattern this plan reuses for the structured sites.

## Research

No external WebSearch needed — the cloud mechanism was verified empirically
(`glm-5.1:cloud` already pulled and signed in). Reference for builders:
Ollama Cloud uses the `<model>:cloud` / `<model>:<size>-cloud` tag convention
and routes the request to Ollama's hosted GPUs via the same local `ollama` /
`ollama.chat` client when the machine is signed in (`ollama signin`). Docs:
https://docs.ollama.com/cloud

## Spike Results

<!-- Filled by Phase 1.5 spikes during build kickoff. Four spikes enumerated below. -->

### spike-1: Classification parity (granite vs gemma)
- **Assumption**: "`granite4.1:3b` produces equivalent or better labels than `gemma4:e2b` on the three routing classifiers (binary work/ignore, 3-way terminus, 4-way work-request)."
- **Method**: prototype — replay a sample of real classification inputs (mine `logs/` terminus DEBUG lines from issue #1318, plus hand-built cases) through both models; compute label agreement.
- **Agent Type**: builder in worktree
- **Time cap**: 5 minutes
- **Result**: [filled after spike]
- **Confidence**: [filled after spike]
- **Impact if false**: If granite diverges materially, keep that specific hot-path site on the Haiku fallback path (drop the local call) rather than granite.

### spike-2: Cloud model selection + reachability for generation
- **Assumption**: "An Ollama Cloud model (candidate `glm-5.1:cloud`) is reachable via the local `ollama` client and returns a usable title in < 5 s."
- **Method**: prototype — call the cloud model through `ollama.chat` and the HTTP `/api/generate` path the title generator uses; measure latency.
- **Agent Type**: builder in worktree
- **Time cap**: 5 minutes
- **Result**: [filled after spike]
- **Confidence**: [filled after spike]
- **Impact if false**: Fall back to granite-local for title generation too (keeps everything local; cloud bucket shrinks to test-only).

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
  reachability for the title-generator + ai-judge paths (both already fail-soft).
- **Interface changes**: `config/models.py` gains `OLLAMA_CLASSIFIER_MODEL`
  (granite) and `OLLAMA_CLOUD_MODEL` constants; `OLLAMA_LOCAL_MODEL` is
  **removed** (NO LEGACY: no aliasing the old name). All importers updated.
  `granite_classifier.DEFAULT_MODEL` repointed to `OLLAMA_CLASSIFIER_MODEL` so
  the granite model id lives in one place.
- **Coupling**: slightly reduces model sprawl (one local instruct model instead
  of two). Centralizes model selection in `config/models.py`.
- **Data ownership**: title-generator content moves from local-only inference to
  Ollama Cloud egress — a privacy-posture change (see Risk 3 + Open Question 1).
- **Reversibility**: high — revert the constant repoints; gemma4:e2b is still
  pullable. Until the `/update` superseded-cleanup runs on a machine, gemma
  remains on disk.

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
| Ollama Cloud signed in | `ollama list \| grep -q ':cloud'` | Generation target (cloud) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/gemma4_ollama_consolidation.md`

## Solution

### Key Elements

- **Centralized model constants** (`config/models.py`): introduce
  `OLLAMA_CLASSIFIER_MODEL = "granite4.1:3b"` and `OLLAMA_CLOUD_MODEL`
  (cloud generation model, default per spike-2). Remove `OLLAMA_LOCAL_MODEL`.
  Add `gemma4:e2b` to `OLLAMA_SUPERSEDED_MODELS`.
- **Classification bucket → granite** (5 sites): the three `bridge/routing.py`
  classifiers, `reflections/memory_management.py::_gemma_classify`, and
  `tools/email_cs/triage.py` tier-1.
- **Generation bucket → Ollama Cloud** (2 sites): `title_generator` and the
  test `ai_judge`.
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
| 6 | `title_generator` | free-text gen | no | fail-soft no-op | **cloud** |
| 7 | `tests/ai_judge.judge` | free-text/eval | no (test) | OpenRouter free-tier | **cloud** |

### Flow

Incoming message → routing classifier → granite (resident, local) → decision.
Memory save → title generator → Ollama Cloud → title persisted.
Local Ollama steady state: **granite4.1:3b + nomic-embed-text only.**

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
- Title generator (#6) and ai-judge (#7) call the cloud model through the same
  client/CLI they already use; only the model id changes (`*:cloud`).

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

## Test Impact

- [ ] `tests/unit/test_routing.py` — UPDATE: tests monkeypatch `routing.OLLAMA_LOCAL_MODEL` (lines ~138, ~213) and inject fake `ollama` modules. Repoint to the new `OLLAMA_CLASSIFIER_MODEL` constant name; assertions on fallback behavior stay.
- [ ] `tests/unit/test_reflections_memory.py` — UPDATE: `_gemma_classify` tests reference the model constant; repoint to granite constant, keep fail-soft assertions.
- [ ] `tests/unit/test_memory_title_generator.py` — UPDATE: `_resolve_ollama_config` now returns the cloud model; update expected model id and any localhost assumptions (cloud still routes via the local client, so base_url may be unchanged — verify in spike-2).
- [ ] `tests/unit/test_memory_title_writer_paths.py` — UPDATE: same model-id expectation change.
- [ ] `tests/unit/test_email_cs_triage.py` — UPDATE: repoint model constant; keep escalation-on-failure assertions.
- [ ] `tests/ai_judge/judge.py` + `tests/ai_judge/test_ai_judge.py` — UPDATE: default `JudgeConfig.model` from `gemma4:e2b` to the cloud model; OpenRouter fallback stays.
- [ ] `tests/unit/granite_container/test_cli.py` — VERIFY: references granite model; ensure no breakage from centralizing `DEFAULT_MODEL` into `config/models.py`.
- [ ] `tests/unit/test_pm_session_factory.py`, `tests/e2e/test_message_pipeline.py` — VERIFY: reference classification; confirm new constant name resolves.

## Rabbit Holes

- **Rewriting every classifier to use native tool-calling.** Tempting ("granite is good at tools"), but the simple single-word classifiers work fine with plain prompting. Only the two structured-JSON sites are tool-calling candidates, and only if spike-3 shows JSON-emit is flaky.
- **Building a generic "local LLM router" abstraction** across all 7 sites. The sites have genuinely different parsing (word / JSON / tool-call / HTTP-generate / CLI). A shared wrapper would leak all four shapes. Centralize the *model id* in config; leave call sites' parsing local.
- **Migrating the embedding provider.** `nomic-embed-text` is a different model for a different job (vector embeddings); Ollama Cloud's instruct catalog is not a drop-in. Out of scope.
- **Re-tuning the gemma-mined few-shot prompts for granite.** Don't preemptively rewrite the issue-#1318 terminus examples. Run spike-1 first; only touch prompts if parity is poor.

## Risks

### Risk 1: Classification behavior drift
**Impact:** The routing prompts (especially terminus RESPOND/REACT/SILENT) were few-shot-tuned against gemma's behavior via real mined misclassifications (#1318). Granite may label differently, causing dropped messages or emoji spam.
**Mitigation:** spike-1 parity check before committing; 2 of 3 hot-path sites already have a Haiku fallback; the binary classifier keeps its conservative `True` default. If a site regresses, drop its local call and lean on the Haiku path.

### Risk 2: Hot-path latency / contention with active PTY sessions
**Impact:** When a granite PTY session is mid-translation, a concurrent bridge classification call queues behind it on the single GPU, adding latency to message routing.
**Mitigation:** granite calls are short (single label); Haiku fallback covers timeouts on 2/3 sites. spike-4 measures cold-start; add `keep_alive` if needed. Net memory pressure *drops* (one fewer resident model), which reduces eviction thrashing overall.

### Risk 3: Title-generator content egress to Ollama Cloud
**Impact:** Memory-title generation currently runs **local-only**. Moving it to cloud sends memory content off-machine — a privacy-posture change, even though `<private>` regions are stripped before the call.
**Mitigation:** private-tag stripping is already mandatory at the call site. **Open Question 1** asks whether title-gen should stay local on granite instead. If privacy wins, the cloud bucket shrinks to test-only ai-judge and everything sensitive stays local.

### Risk 4: Cloud reachability / quota for frequent title generation
**Impact:** Title-gen fires on every memory save; cloud outage or rate limit could stall titles.
**Mitigation:** the path is fire-and-forget and fail-soft (timeout → title unchanged, stub falls back to category-only rendering). No user-visible failure. Ollama subscription covers expected volume.

## Race Conditions

No new race conditions identified. The migration changes only which model id is
passed to existing call sites; concurrency structure is unchanged. The
title-generator already runs in a daemon thread with fail-soft save semantics;
the memory-audit Layer 3 already bounds itself with a wallclock budget and a
single-thread executor. Neither timing contract changes.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG]` Migrating `agent/embedding_provider.py` (`nomic-embed-text`) — different model class (embeddings), no cloud drop-in, no memory-contention problem. Not filed; genuinely separate concern, left local.
- Removing the old `granite3.2-vision` model from disk — unrelated stale artifact; the `/update` superseded-cleanup can absorb it separately if desired.
- Re-tuning classification prompts for granite beyond what spike-1 requires — prompt engineering is a follow-up only if parity testing demands it.

(Everything that *could* be finished in this plan — the 7 call-site repoints, the
config centralization, the dead `ollama_vision_model` cleanup, the `/update`
superseded-list edit, and all test updates — is **in scope**, not deferred.)

## Update System

- **`config/models.py::OLLAMA_SUPERSEDED_MODELS`**: add `"gemma4:e2b"`. The
  `/update` Step "Cleaning up superseded Ollama models" then `ollama rm`s it on
  every machine over time. Granite is already pulled by the granite-PTY update
  path; no new pull needed.
- **Local-model smoke test**: the `/update` "Smoke testing <model>" step
  currently targets gemma4:e2b. Repoint it to `OLLAMA_CLASSIFIER_MODEL`
  (granite4.1:3b) so the gate verifies the model the system now depends on.
- **Cloud signin precondition**: `/update` should surface a warning (not block)
  if no `:cloud` model is available, since the title-generator + ai-judge paths
  now depend on it. Detection-only, consistent with the gws-auth pattern.
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
- [ ] Update `docs/features/subconscious-memory.md` — note title generation now runs on Ollama Cloud (or granite-local per Open Question 1) instead of local gemma.
- [ ] Update `docs/features/granite-pty-production.md` (or the granite classifier doc) — note granite4.1:3b now also serves bridge message classification, and document the "local Ollama = granite + nomic-embed-text only" steady state.
- [ ] Add/refresh a short "Local model policy" note: classification → granite, generation → cloud, embeddings → nomic-embed-text. Index it in `docs/features/README.md`.

### Inline Documentation
- [ ] Update the `config/settings.py` ModelSettings docstrings (remove the dead vision-model field; clarify `ollama_host` now serves granite + cloud).
- [ ] Update `config/models.py` section comments: "LOCAL OLLAMA MODELS" → document the classifier/cloud split and the superseded gemma entry.
- [ ] Update docstrings at each repointed call site to name granite/cloud, not gemma.

## Success Criteria

- [ ] `grep -rn "gemma4:e2b\|OLLAMA_LOCAL_MODEL" --include=*.py .` returns no hits in `bridge/`, `reflections/`, `tools/`, `config/`, `agent/`, `tests/` (excluding `OLLAMA_SUPERSEDED_MODELS`'s historical entry and `.claude/worktrees/`).
- [ ] `config/models.py` defines `OLLAMA_CLASSIFIER_MODEL` and `OLLAMA_CLOUD_MODEL`; `OLLAMA_LOCAL_MODEL` is gone.
- [ ] `gemma4:e2b` is in `OLLAMA_SUPERSEDED_MODELS`; `/update` smoke test targets granite.
- [ ] All 7 call sites pass their (updated) unit tests; fallback/fail-soft semantics unchanged.
- [ ] spike-1 parity, spike-2 cloud reachability, spike-3 granite JSON, spike-4 cold-start results recorded in Spike Results.
- [ ] Dead `ollama_vision_model` setting removed (or repointed if a consumer surfaces).
- [ ] After a manual run on this machine, `ollama ps` under load shows granite (not gemma) serving classification.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (config-and-classification)**
  - Name: `classifier-builder`
  - Role: Centralize model constants in `config/models.py`; repoint the 5 classification sites (routing ×3, memory-audit, email-triage) to granite; update their unit tests.
  - Agent Type: builder
  - Resume: true

- **Builder (generation-and-cloud)**
  - Name: `cloud-builder`
  - Role: Repoint title-generator + ai-judge to the cloud model; remove dead `ollama_vision_model`; update their unit tests.
  - Agent Type: builder
  - Resume: true

- **Builder (update-system)**
  - Name: `update-builder`
  - Role: Add gemma to `OLLAMA_SUPERSEDED_MODELS`; repoint the `/update` smoke test to granite; add cloud-signin detection warning.
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
- Add `OLLAMA_CLASSIFIER_MODEL`, `OLLAMA_CLOUD_MODEL`; remove `OLLAMA_LOCAL_MODEL`; add gemma to superseded list
- Repoint `granite_classifier.DEFAULT_MODEL` to the shared constant

### 3. Repoint classification sites (granite)
- **Task ID**: build-classification
- **Depends On**: build-config
- **Validates**: tests/unit/test_routing.py, tests/unit/test_reflections_memory.py, tests/unit/test_email_cs_triage.py
- **Informed By**: spike-1, spike-3
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Repoint routing ×3, `_gemma_classify`, email triage; preserve every fallback/default
- Update tests to monkeypatch the new constant name

### 4. Repoint generation sites (cloud) + dead-config cleanup
- **Task ID**: build-generation
- **Depends On**: build-config
- **Validates**: tests/unit/test_memory_title_generator.py, tests/unit/test_memory_title_writer_paths.py, tests/ai_judge/test_ai_judge.py
- **Informed By**: spike-2, Open Question 1
- **Assigned To**: cloud-builder
- **Agent Type**: builder
- **Parallel**: true
- Repoint title-generator + ai-judge to cloud (or granite-local if OQ1 says local)
- Remove the unused `ollama_vision_model` setting

### 5. Update system retirement of gemma
- **Task ID**: build-update
- **Depends On**: build-config
- **Validates**: scripts/update smoke-test path
- **Assigned To**: update-builder
- **Agent Type**: builder
- **Parallel**: true
- Repoint `/update` local-model smoke test to granite; add cloud-signin detection warning

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
| No gemma in source | `grep -rn "gemma4:e2b" bridge/ reflections/ tools/ config/ agent/ \| grep -v SUPERSEDED` | exit code 1 |
| Old constant gone | `grep -rn "OLLAMA_LOCAL_MODEL" --include=*.py bridge/ reflections/ tools/ config/ agent/` | exit code 1 |
| New constants present | `python -c "from config.models import OLLAMA_CLASSIFIER_MODEL, OLLAMA_CLOUD_MODEL"` | exit code 0 |
| gemma superseded | `python -c "from config.models import OLLAMA_SUPERSEDED_MODELS as s; assert 'gemma4:e2b' in s"` | exit code 0 |
| Routing tests pass | `pytest tests/unit/test_routing.py -q` | exit code 0 |
| Memory + triage tests pass | `pytest tests/unit/test_reflections_memory.py tests/unit/test_memory_title_generator.py tests/unit/test_email_cs_triage.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Title generation: cloud or local-granite?** Memory-title generation
   currently runs **local-only** (privacy boundary). The stated rule sends
   generation → cloud, but that means memory content (private-stripped) leaves
   the machine. Keep it local on granite for privacy, or send to cloud per the
   default rule? *Recommendation: granite-local for title-gen to preserve the
   local-only privacy posture; reserve cloud for the test-only ai-judge.*
2. **Which `:cloud` model** for the generation bucket — use the already-pulled
   `glm-5.1:cloud`, or a different/smaller cloud model better suited to short
   title/eval tasks? (spike-2 input)
3. **Test ai-judge**: migrate it to a cloud model at all, or simply drop its
   local-gemma path and rely on its existing OpenRouter `gemma-4-e2b:free`
   fallback? It's test infrastructure, so either is low-stakes.
