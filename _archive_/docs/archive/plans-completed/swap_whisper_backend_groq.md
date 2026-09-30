---
status: docs_complete
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-13
tracking: https://github.com/tomcounsell/ai/issues/1951
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-13T07:03:11Z
---

# Swap Whisper Transcription Backend to Groq (whisper-large-v3)

## Problem

Video/audio transcription in `tools/link_analysis` (and, transitively, the
`tools/video_watch` watch path) uses OpenAI `whisper-1` as its Whisper backend.
Groq serves `whisper-large-v3` over an OpenAI-compatible endpoint that is
materially cheaper and faster, which would cut transcription latency and cost on
both the YouTube-transcribe path and the frames-capable watch path.

**Current behavior:**
`tools/link_analysis/__init__.py:257` `transcribe_audio_file()` unconditionally
POSTs to `https://api.openai.com/v1/audio/transcriptions` with `model=whisper-1`,
keyed off `OPENAI_API_KEY`. Every transcription pays OpenAI whisper-1 pricing and
latency.

**Desired outcome:**
`transcribe_audio_file()` prefers Groq `whisper-large-v3` when `GROQ_API_KEY` is
present, and transparently falls back to OpenAI `whisper-1` when the Groq key is
absent OR the Groq request fails. No caller changes; `tools/video_watch` inherits
the new backend for free. Absent `GROQ_API_KEY`, behavior is identical to today
(zero regression).

## Freshness Check

**Baseline commit:** `fc272d4e`
**Issue filed at:** 2026-07-08T07:02:08Z
**Disposition:** Unchanged

All recon was performed live against current `main` on 2026-07-13, so file:line
references are current by construction.

**File:line references re-verified:**
- `tools/link_analysis/__init__.py:257` — `transcribe_audio_file()` is the sole in-scope Whisper caller — still holds.
- `tools/link_analysis/__init__.py:288` — hardcoded `data={"model": "whisper-1"}` and OpenAI endpoint at 292 — still holds.
- `tools/video_watch/pipeline.py:28,412` — imports and calls `transcribe_audio_file` — still holds.
- `config/settings.py:28-46` — `APISettings` with `openai_api_key` + shared `validate_api_keys` validator — still holds.
- `config/settings.py:~855` — dict-export block wiring keys into config — still holds.

**Cited sibling issues/PRs re-checked:**
- #1920 — CLOSED 2026-07-10, merged via PR #1953 ("Add valor-video-watch"). This is the parent feature this issue was split out of; `tools/video_watch` now exists on main, which recon already accounts for.

**Commits on main since issue was filed (touching referenced files):**
- `045b3d70` Add valor-video-watch (#1920) — created `tools/video_watch`; expected, is the parent. Irrelevant to the transcribe function body.
- `e1ec8695` Centralize magic literals into config/settings.py — added `TimeoutSettings`; did NOT touch `APISettings`. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none (video-watch-visual-grounding.md is shipped; this was its deferred `[SEPARATE-SLUG]` item).

**Notes:** No drift affecting the plan's premise.

## Prior Art

- **Issue #1920 / PR #1953**: "Add a frames-capable 'watch' path for YouTube/video links" — shipped `tools/video_watch`, which reuses `transcribe_audio_file`. This issue is the deferred backend-swap it explicitly split out. No prior attempt swapped the Whisper backend.
- No closed issues or merged PRs match `groq whisper` — this is greenfield for the Groq path.

## Research

**Queries used:**
- Groq API whisper-large-v3 audio transcriptions endpoint OpenAI compatible file size limit

**Key findings:**
- Groq exposes an OpenAI-compatible transcription endpoint at `https://api.groq.com/openai/v1/audio/transcriptions`, model `whisper-large-v3`, auth `Authorization: Bearer $GROQ_API_KEY`. Same multipart form contract as OpenAI → the swap is near drop-in (change URL, key, model id). Source: https://console.groq.com/docs/speech-to-text
- Free-tier direct-upload file limit is 25MB — the same ceiling as OpenAI whisper-1, so no new size regression is introduced. Larger files would require URL upload on a paid tier (out of scope; existing duration caps already gate this). Source: https://groq.com/blog/largest-most-capable-asr-model-now-faster-on-groqcloud

## Data Flow

1. **Entry point**: `process_youtube_url()` (link_analysis) or `watch_video()` (video_watch) downloads/extracts an audio file to a temp path.
2. **transcribe_audio_file(filepath)** (`tools/link_analysis/__init__.py:257`): resolves MIME type from extension, builds a multipart POST.
3. **Backend selection (new)**: if `GROQ_API_KEY` is set, POST to Groq (`whisper-large-v3`); on Groq HTTP error or exception, fall back to OpenAI (`whisper-1`) when `OPENAI_API_KEY` is set. If only `OPENAI_API_KEY` is set, go straight to OpenAI (today's behavior).
4. **Output**: transcript string (or `None` on total failure) returned to the caller unchanged.

## Architectural Impact

- **New dependencies**: none — reuses the existing `httpx.AsyncClient` multipart flow. No new pip package (Groq is called over raw HTTP, matching the existing OpenAI call style).
- **Interface changes**: none. `transcribe_audio_file(filepath) -> str | None` signature is unchanged. Backend selection is fully internal.
- **Coupling**: unchanged. `tools/video_watch` still depends only on `transcribe_audio_file`.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — deleting the Groq branch restores prior behavior; the new `GROQ_API_KEY` field is nullable and inert when unset.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

A single internal function plus a config field. The bottleneck is verifying the
Groq/OpenAI fallback ordering, not coding time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `GROQ_API_KEY` (runtime activation only) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('GROQ_API_KEY')"` | Activates the Groq path at runtime. NOT required to build/test — absent key exercises the OpenAI fallback, which is a first-class tested path. |

The key's absence is not a build blocker: the fallback to `whisper-1` is
intentional and covered by tests. The Prerequisite is advisory for the runtime
benefit, not a gate on the work.

## Solution

### Key Elements

- **`GROQ_API_KEY` config field**: a nullable `groq_api_key` on `APISettings` (`config/settings.py`), added to the shared `validate_api_keys` validator and the dict-export block, plus a commented placeholder in `.env.example`.
- **Groq-preferred transcription helper**: refactor `transcribe_audio_file()` so the HTTP POST is parameterized by (endpoint URL, bearer key, model id). Try Groq first when its key exists; fall back to OpenAI on failure/absence.
- **Backend labeling in logs**: log which backend served (or failed) each transcription so cost/latency wins and fallbacks are observable.

### Flow

Caller downloads audio → `transcribe_audio_file(path)` → `GROQ_API_KEY` set? → **yes**: POST Groq `whisper-large-v3` → success? → return text; **failure** → `OPENAI_API_KEY` set? → POST OpenAI `whisper-1` → return text/None. `GROQ_API_KEY` unset → straight to OpenAI (unchanged path).

### Technical Approach

- Extract the multipart-POST body into a small internal helper, e.g. `_post_transcription(client, filepath, mime_type, *, url, api_key, model)` returning `str | None`, so Groq and OpenAI share one code path differing only by the three parameters.
- Selection logic in `transcribe_audio_file`:
  - `groq_key = os.getenv("GROQ_API_KEY", "")`; `openai_key = os.getenv("OPENAI_API_KEY", "")` (keep reading env directly — consistent with the module's existing style, `__init__.py:41,267`).
  - If `groq_key`: attempt Groq; on `None`/exception, log a warning and fall through to OpenAI if `openai_key` is present.
  - If no `groq_key`: attempt OpenAI (today's behavior verbatim).
  - If neither key: warn and return `None` (today's behavior).
- Constants for endpoints/models: name them at module top (`GROQ_TRANSCRIBE_URL`, `GROQ_WHISPER_MODEL`, `OPENAI_TRANSCRIBE_URL`, `OPENAI_WHISPER_MODEL`) — provisional/tunable, grain-of-salt comment, following the repo's magic-number convention.
- Keep the existing `httpx.AsyncClient(timeout=...)` shape; the Groq call reuses the same MIME map and timeout.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] (a1) Groq transport error + **no** `OPENAI_API_KEY` set → the function logs a warning naming the Groq failure and returns `None`. Assert observable behavior (return value + logged backend), never silent pass. (Split from critique NIT — maps 1:1 to the "no fallback key" branch of the selection logic.)
- [ ] (a2) Groq transport error + `OPENAI_API_KEY` **present** → the function logs a warning naming the Groq failure and then falls back to OpenAI `whisper-1`, returning the OpenAI transcript. Assert both the OpenAI return value AND the Groq-failure warning. (Split from critique NIT — maps 1:1 to the "fallback key present" branch.)
- [ ] The new Groq→OpenAI fallback branch must have a test where Groq returns a non-200 and OpenAI then serves the transcript — assert the OpenAI result is returned AND a warning naming the Groq failure was logged.

### Empty/Invalid Input Handling
- [ ] Test: neither key set → returns `None` and warns (unchanged behavior).
- [ ] Test: Groq returns 200 with empty `text` → returns empty/stripped string exactly as the OpenAI path does today (no crash, no infinite loop).

### Error State Rendering
- [ ] `transcribe_audio_file` returns `None` on total failure; callers already surface a `"[audio too long to transcribe ...]"` / no-transcript note. Verify the `None` path still propagates (no swallow) via the existing video_watch test that patches a silent transcript.

## Test Impact

- [ ] `tools/video_watch/tests/test_watch.py` (patches `transcribe_audio_file`) — no change: patches the symbol, backend-agnostic. Verify still green.
- [ ] `tools/video_watch/tests/test_e2e_visual_grounding.py` (patches `transcribe_audio_file`) — no change: same reason. Verify still green.
- [ ] `tools/link_analysis/tests/test_link_analysis.py` — ADD: no existing case exercises `transcribe_audio_file`; add new backend-selection + fallback tests here (mock `httpx` responses for Groq/OpenAI URLs).

No existing tests are broken by this change — the swap is internal to
`transcribe_audio_file`, whose only test-facing callers patch the symbol wholesale.
New coverage is additive.

## Rabbit Holes

- **Chunking/URL-upload for >25MB files.** Groq's larger-file tiers use URL upload. The existing duration caps (`MAX_VIDEO_DURATION`, video_watch's "audio too long" gate) already bound file size to the 25MB-parity regime. Do NOT build a chunking pipeline.
- **Migrating `tools/transcribe/` (SuperWhisper voice notes).** Separate module, out of scope. Leave it alone.
- **Adding the official `groq` SDK.** Unnecessary — the endpoint is OpenAI-compatible and the module already speaks raw `httpx`. Adding a dependency is scope creep.
- **Routing `groq_api_key` through `config.settings` into the tool.** The module reads env directly today; keep that pattern for a minimal diff rather than threading a settings object into `tools/link_analysis`.

## Risks

### Risk 1: Groq transcript quality/format differs subtly from whisper-1
**Impact:** Downstream summarization or note-generation could see slightly different text.
**Mitigation:** `whisper-large-v3` is a superset-quality model; the response schema (`{"text": ...}`) is identical. The `.get("text","").strip()` extraction is unchanged. Fallback to whisper-1 remains available.

### Risk 2: Silent double-charge / wrong-backend confusion during rollout
**Impact:** Hard to tell whether Groq or OpenAI actually served a given transcription.
**Mitigation:** Log the backend used (and any fallback) at INFO/WARNING so the win is observable and fallbacks are visible in logs.

### Risk 3: Groq outage causes user-visible transcription failures
**Impact:** If Groq is down and no `OPENAI_API_KEY` is set, transcription returns `None`.
**Mitigation:** The fallback chain tries OpenAI whenever its key is present. Deployments keeping `OPENAI_API_KEY` set retain full resilience. Documented in the feature doc.

## Race Conditions

No race conditions identified — `transcribe_audio_file` is a self-contained async
function operating on a single local file path with no shared mutable state; the
backend-selection branch reads two env vars and performs sequential awaited HTTP
calls.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1951] This plan IS #1951; nothing is deferred to a further slug.
- Nothing deferred — every relevant item (config field, `.env.example`, backend swap, fallback, tests, docs) is in scope for this plan. `tools/transcribe/` and >25MB chunking are permanently out of scope (see Rabbit Holes), not deferred work.

## Update System

- `.env.example` gains a `GROQ_API_KEY` placeholder (with the required comment line above it) — propagated by the standard env-sync flow; no code change to `scripts/update/run.py` needed.
- `config/settings.py` gains a nullable `groq_api_key` field — picked up automatically; no migration required (no Popoto model change).
- No `scripts/update/migrations.py` entry required — this touches no Popoto model.
- Operators who want the Groq path add `GROQ_API_KEY` to `~/Desktop/Valor/.env`; absence is safe (OpenAI fallback).

## Agent Integration

No agent integration required — this is an internal backend swap inside an
existing tool. The agent already reaches transcription via the `valor-youtube-transcribe`
and `valor-video-watch` CLI entry points (`pyproject.toml [project.scripts]`),
which call `transcribe_audio_file` transitively. No new CLI, MCP surface, or
`.mcp.json` change is needed; the function signature is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/markitdown-ingestion.md` / `docs/features/video-watch-visual-grounding.md` (whichever documents the transcribe path) to note the Groq-preferred backend with whisper-1 fallback, and add `GROQ_API_KEY` to the relevant config/secret notes.
- [ ] Add a short `docs/features/groq-whisper-backend.md` (or a subsection in the existing transcription feature doc) describing the backend-selection order, the `GROQ_API_KEY` secret, and the fallback semantics.
- [ ] Ensure it is linked from `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on `transcribe_audio_file` updated to state "Groq whisper-large-v3 preferred, OpenAI whisper-1 fallback."
- [ ] Grain-of-salt comments on the new endpoint/model constants marking them provisional/tunable.

## Success Criteria

- [ ] `GROQ_API_KEY` exists as a nullable field in `config/settings.py` `APISettings` (in the `validate_api_keys` list and dict-export block) and as a commented placeholder in `.env.example`. This field is for format validation + `.env` completeness ONLY; it is NOT read by `transcribe_audio_file`.
- [ ] `transcribe_audio_file` gates the backend on `os.getenv("GROQ_API_KEY", "")` directly (mirroring `os.getenv("OPENAI_API_KEY", "")` at `__init__.py:267`), NOT on `APISettings.groq_api_key`. It posts to Groq `whisper-large-v3` when the env key is set, and falls back to OpenAI `whisper-1` on Groq failure or when only `OPENAI_API_KEY` is set.
- [ ] With neither key set, behavior is unchanged (`None` + warning).
- [ ] New backend-selection + fallback tests in `tools/link_analysis/tests/` pass; existing `tools/video_watch` tests remain green.
- [ ] Backend used (and any fallback) is logged.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (transcribe-backend)**
  - Name: transcribe-builder
  - Role: Add `groq_api_key` config + `.env.example`; refactor `transcribe_audio_file` for Groq-preferred/OpenAI-fallback; add tests.
  - Agent Type: builder
  - Domain: MCP-tool/API integration
  - Resume: true

- **Validator (transcribe-backend)**
  - Name: transcribe-validator
  - Role: Verify backend selection, fallback ordering, config wiring, and that video_watch tests stay green.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: transcribe-docs
  - Role: Feature doc + README index + docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add GROQ_API_KEY config wiring
- **Task ID**: build-config
- **Depends On**: none
- **Validates**: `tools/link_analysis/tests/test_link_analysis.py` (new cases), `python -c "from config.settings import Settings"`
- **Assigned To**: transcribe-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `groq_api_key: str | None = Field(default=None, description="Groq API key for whisper-large-v3 transcription")` to `APISettings` (`config/settings.py:28-46`).
- Add `"groq_api_key"` to the `@field_validator(...)` list at line 40.
- Add the `if self.api.groq_api_key: config["groq"] = {"api_key": ...}` branch in the dict-export block (~line 855).
- Add a commented `GROQ_API_KEY=gsk_****` placeholder to `.env.example` near the `OPENAI_API_KEY` entry (comment line required above it).
- **Implementation Note (critique CONCERN):** This `APISettings.groq_api_key` field is for `validate_api_keys` format checking and `.env` completeness only. It is NOT the value the backend reads — `transcribe_audio_file` gates on `os.getenv("GROQ_API_KEY", "")` directly (see Task 2). Do not wire this field into `tools/link_analysis`.

### 2. Refactor transcribe_audio_file for Groq-preferred backend
- **Task ID**: build-backend
- **Depends On**: build-config
- **Validates**: `tools/link_analysis/tests/test_link_analysis.py` (new cases)
- **Informed By**: Research (Groq endpoint/model/25MB parity confirmed)
- **Assigned To**: transcribe-builder
- **Agent Type**: builder
- **Domain**: MCP-tool/API integration
- **Parallel**: false
- Add module-level constants `GROQ_TRANSCRIBE_URL`, `GROQ_WHISPER_MODEL="whisper-large-v3"`, `OPENAI_TRANSCRIBE_URL`, `OPENAI_WHISPER_MODEL="whisper-1"` with grain-of-salt comments.
- Extract `_post_transcription(client, filepath, mime_type, *, url, api_key, model) -> str | None`.
- Rewrite `transcribe_audio_file` selection: Groq-first when `GROQ_API_KEY` set, OpenAI fallback on failure/absence; log which backend served or failed.
- Preserve the existing MIME map, timeout, and `None`-on-total-failure contract.
- **Implementation Note (critique CONCERN):** The backend selection branch MUST read the key via `os.getenv("GROQ_API_KEY", "")`, mirroring the existing `os.getenv("OPENAI_API_KEY", "")` at `tools/link_analysis/__init__.py:267`. Do NOT import or read `config/settings.py`'s `APISettings.groq_api_key` in the selection branch — the `APISettings` field (Task 1) exists only for `validate_api_keys` format checking and `.env` completeness, and is functionally inert for gating the backend. Reading it here would couple the tool to the settings object, which Rabbit Holes explicitly rejects.

### 3. Add backend-selection + fallback tests
- **Task ID**: build-tests
- **Depends On**: build-backend
- **Validates**: `pytest tools/link_analysis/tests/test_link_analysis.py`
- **Assigned To**: transcribe-builder
- **Agent Type**: builder
- **Parallel**: false
- Mock `httpx` responses keyed by URL: Groq success; Groq-fail→OpenAI-success; only-OpenAI; neither-key→None; Groq empty text.
- Assert returned text and that the correct backend was chosen and logged.

### 4. Validate implementation
- **Task ID**: validate-backend
- **Depends On**: build-tests
- **Assigned To**: transcribe-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tools/link_analysis/tests/ tools/video_watch/tests/ -q`.
- Confirm video_watch tests remain green (symbol patched, backend-agnostic).
- Confirm config imports and validator accept/normalize the new key.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-backend
- **Assigned To**: transcribe-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create/extend the transcription feature doc with backend order, `GROQ_API_KEY`, and fallback semantics.
- Add/verify the `docs/features/README.md` index entry.
- Update the `transcribe_audio_file` docstring.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: transcribe-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands.
- Confirm all Success Criteria met including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tools/link_analysis/tests/ tools/video_watch/tests/ -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/link_analysis config/settings.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/link_analysis config/settings.py` | exit code 0 |
| Config field present | `python -c "from config.settings import APISettings; assert 'groq_api_key' in APISettings.model_fields"` | exit code 0 |
| Groq model wired | `grep -c "whisper-large-v3" tools/link_analysis/__init__.py` | output > 0 |
| OpenAI fallback retained | `grep -c "whisper-1" tools/link_analysis/__init__.py` | output > 0 |
| env.example placeholder | `grep -c "GROQ_API_KEY" .env.example` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) — LITE depth, 1 Consolidated Critic. Verdict: READY TO BUILD (with concerns). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Consolidated | `groq_api_key` is listed on `APISettings` (validator + dict-export) as a Solution Key Element, but Technical Approach/Rabbit Holes have the selection logic read `os.getenv("GROQ_API_KEY","")` directly — the config field is functionally inert for gating the backend. | Add one line to Success Criteria clarifying the `APISettings` field exists only for `validate_api_keys` format checking + `.env` completeness, NOT as the value read by `transcribe_audio_file`. | In `tools/link_analysis/__init__.py`, backend selection MUST call `os.getenv("GROQ_API_KEY","")`, mirroring the existing `os.getenv("OPENAI_API_KEY","")` at line 267 — do NOT import or read `config/settings.py`'s `APISettings.groq_api_key` in the selection branch. |
| NIT | Consolidated | Failure Path Test Strategy item (a) ("transport error → returns None **or** falls back") is ambiguous; Technical Approach already disambiguates on `OPENAI_API_KEY` presence. | Split (a) into two explicit cases mapping 1:1 to the selection logic. | (a1) Groq transport error + no `OPENAI_API_KEY` → warning + `None`; (a2) Groq transport error + `OPENAI_API_KEY` present → falls back to OpenAI. Assert both sub-cases separately. |

---

## Open Questions

1. Which existing feature doc should host the Groq backend note — extend `docs/features/video-watch-visual-grounding.md`, extend the transcription/ingestion doc, or create a dedicated `docs/features/groq-whisper-backend.md`? (Default assumption: a dedicated short doc, linked from the README index.)
2. Should deployments be advised to keep `OPENAI_API_KEY` set as the resilience fallback, or is Groq-only (returning `None` on outage) acceptable for machines without an OpenAI key? (Default assumption: keep OpenAI as fallback where a key exists; Groq-only degrades gracefully to `None`.)
