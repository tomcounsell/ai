---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1161
last_comment_id:
revision_applied: true
---

# Markitdown Integration — Multi-Format Document Ingestion for the Knowledge Pipeline

## Problem

The knowledge pipeline (`bridge/knowledge_watcher.py`, `tools/knowledge/indexer.py`) only indexes `.md` and `.txt` files. When a PDF, Word doc, PowerPoint deck, or HTML file lands in `~/work-vault/`, it is silently skipped. A client proposal, a meeting deck, or a saved article is present on disk but contributes zero knowledge to embeddings, subconscious memory recall, or cross-reference audits.

**Current behavior:**
- `bridge/knowledge_watcher.py:21` and `tools/knowledge/indexer.py:28` both declare `SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown", ".text"}` — any other format is filtered out at `bridge/knowledge_watcher.py:44` and `tools/knowledge/indexer.py:37-40`
- `pypdf` and `html2text` are installed (for `bridge/media.py` attachment handling) but not wired into the indexer
- No `valor-ingest` CLI exists; there is no on-demand path to convert a file or URL into the vault

**Desired outcome:**
- Any file dropped into the vault that markitdown can convert automatically produces a `.md` sidecar alongside the original
- The existing indexer picks up the sidecar with zero changes to its core logic
- A `valor-ingest` CLI supports on-demand conversion of local paths and URLs (YouTube transcripts, HTML pages)
- The LLM used for image description is configurable; defaults to Haiku; never silently uses Opus
- Audio transcription is **explicitly out of scope for v1** — markitdown's audio path uses an unauthenticated Google Web Speech API, which is unacceptable for consulting material

## Freshness Check

**Baseline commit:** `fef96895` (`Docs: migrate completed plan for #1155`)
**Issue filed at:** 2026-04-24T08:43:02Z (today)
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/knowledge_watcher.py:21` — `SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown", ".text"}` — still holds
- `bridge/knowledge_watcher.py:44` — extension filter in `_is_relevant` — still holds
- `tools/knowledge/indexer.py:28` — same constant — still holds
- `tools/knowledge/indexer.py:37-40` — `_is_supported_file` — still holds
- `tools/knowledge/indexer.py:223, 429` — both call sites still present

**Cited sibling issues/PRs re-checked:**
- PR #605 (knowledge document integration) — merged 2026-03-30, foundation intact
- PR #864 (chunked retrieval) — merged 2026-04-10, foundation intact
- PR #615 (stale Haiku model ID fix) — merged 2026-03-31, relevant: indexer already imports `HAIKU` from `config.models`
- Issue #728 (agent-maintained wiki) — still open, complementary, not blocking

**Commits on main since issue was filed:** None touching the referenced files.

**Active plans in `docs/plans/` overlapping this area:** None — no open plan touches knowledge/indexer/watcher code.

## Prior Art

- **PR #605** (`Add knowledge document integration system`) — shipped `KnowledgeDocument` model, `knowledge_watcher.py`, `indexer.py`. Foundation this plan extends.
- **PR #864** (`feat: chunked document retrieval`) — shipped `DocumentChunk` model and heading-aware chunking. Already handles splitting once text exists.
- **PR #615** (`Fix stale Haiku model ID in knowledge indexer`) — relevant: confirms `HAIKU` from `config.models` is the canonical source of the model ID used by the indexer's summarizer. We should reuse this constant rather than introduce a new Haiku reference.
- **Issue #728** (`Agent-maintained knowledge wiki in Obsidian work vault`) — still open, complementary. That issue builds an *output* pipeline (agents writing wiki pages); this plan builds an *input* pipeline (converting external docs). They compose: markitdown-generated sidecars become sources for #728's ingest operation.

No prior attempts to integrate markitdown or a similar multi-format converter were found. This is greenfield for this codebase.

## Research

**Queries used:**
- "microsoft markitdown python library PDF docx conversion 2026"
- "markitdown LLM client image description OCR configuration"
- "markitdown audio transcription Whisper local vs API privacy"

**Key findings:**

1. **CLI pattern** — `markitdown <file>` writes to stdout by default; `-o <path>` writes to a file; stdin piping supported. `python -m markitdown` is not documented. Source: [github.com/microsoft/markitdown](https://github.com/microsoft/markitdown). Informs: the converter can use subprocess for most formats.

2. **LLM config is library-only** — `llm_client` and `llm_model` are Python API parameters; no CLI flags exist. Source: README and [realpython.com/python-markitdown](https://realpython.com/python-markitdown/). Informs: for LLM-assisted conversion (images, PPTX with images), we must use the Python API, not the subprocess.

3. **LLM matters only for images and PPTX image descriptions** — not for PDFs with text layer, .docx, .xlsx, HTML, or CSV. Informs: subprocess CLI path handles 90%+ of realistic vault content; Python API path reserved for image-heavy formats.

4. **markitdown-ocr plugin** — separate package (`pip install markitdown-ocr`, requires `--use-plugins`). Adds OCR to PDF/DOCX/PPTX/XLSX for embedded images via the same `llm_client`/`llm_model`. Out of scope for v1.

5. **Audio transcription uses Google Web Speech API** (NOT Whisper) — via `SpeechRecognition.recognizer.recognize_google()` with an unauthenticated shared key intended for "personal or testing purposes only" (50 req/day). Source: [markitdown/packages/markitdown/src/markitdown/converters/_transcribe_audio.py](https://github.com/microsoft/markitdown/blob/main/packages/markitdown/src/markitdown/converters/_transcribe_audio.py). **Disqualifies markitdown's audio path for consulting material.** Informs: exclude `[audio-transcription]` extra; defer audio support to a separate feature using local Whisper if needed.

6. **Installation extras** — `[pdf]`, `[docx]`, `[pptx]`, `[xlsx]`, `[outlook]`, `[youtube-transcription]`, `[audio-transcription]`, `[az-doc-intel]`, `[all]`. Informs: install a specific subset (explicitly excluding audio-transcription) rather than `[all]`.

Memory saves attempted for findings #5 (audio privacy disqualifier) and #2 (LLM library-only).

## Spike Results

### spike-1: Does markitdown's CLI support the subprocess-only invocation pattern I need?
- **Assumption:** `markitdown <file> > out.md` works for text-extractable formats without requiring the Python API or LLM config.
- **Method:** web-research (fetched README from main)
- **Finding:** CLI works as described. Writes to stdout by default. `-o` writes to file. LLM flags are library-only, not exposed on CLI. No `--llm-model` or `--llm-client` on CLI at all.
- **Confidence:** high
- **Impact on plan:** The converter must support **two code paths**: (a) subprocess for formats where no LLM is needed — the cheap default; (b) Python API when `MARKITDOWN_LLM_MODEL` is set AND the format benefits (image-heavy PPTX, standalone images). Exit code semantics on CLI are undocumented — treat empty stdout + non-empty stderr as failure and raise explicitly.

### spike-2: Does markitdown use local Whisper or the OpenAI Whisper API for audio?
- **Assumption:** Local Whisper, configurable to API — privacy-preserving either way.
- **Method:** web-research (read source at `_transcribe_audio.py`)
- **Finding:** **Neither.** It uses `SpeechRecognition.recognize_google()` with an unauthenticated shared generic key. This uploads audio to Google's Web Speech API with no enterprise DPA, no key ownership, no privacy guarantees, and a 50-req/day quota. Worse privacy posture than the OpenAI Whisper API.
- **Confidence:** high (read the source directly)
- **Impact on plan:** **Exclude `[audio-transcription]` extra entirely.** Document why in the feature doc. Audio support becomes a future feature using local Whisper (`openai-whisper` or `faster-whisper`), tracked as a separate issue, not this plan.

## Data Flow

End-to-end for an automatic ingestion (the common path):

1. **Entry point:** User drops `report.pdf` into `~/work-vault/Consulting/leads/acme/`
2. **Watcher (`bridge/knowledge_watcher.py`):** `_DebouncedHandler.on_created` fires; the new `_is_convertible(path)` check passes; `_schedule` queues it for the debounce window
3. **Converter (`tools/knowledge/converter.py`):** receives the path, checks for an existing `report.pdf.md` sidecar, reads current source hash from the sidecar's YAML frontmatter, skips if unchanged; otherwise invokes `markitdown` subprocess, writes `report.pdf.md` with frontmatter `source_hash: <sha256>` and `source_path: report.pdf`
4. **Watcher sees new `.md`:** `report.pdf.md` fires `on_created`; extension matches original `SUPPORTED_EXTENSIONS`; passes to existing indexer
5. **Indexer (`tools/knowledge/indexer.py`):** reads sidecar content, upserts `KnowledgeDocument`, syncs `DocumentChunk`s, creates companion `Memory` record (importance=3.0, source="knowledge")
6. **Output:** Content is now embedded, chunk-searchable, and recallable via subconscious memory

For the on-demand path (`valor-ingest <path>`):
1. CLI parses args, resolves source path (local file) or URL (YouTube, HTML)
2. For URLs: download to a staging location (temp file) or directly pass URL to markitdown (library supports URL input; subprocess behavior undocumented — use library path)
3. Call the same converter module as the watcher does; write sidecar to `--vault-subdir` or alongside source
4. Watcher picks it up on next debounce (or user ran with `--scan` to trigger index directly)

## Architectural Impact

- **New dependencies:** `markitdown` (optional extra `[knowledge]` in `pyproject.toml`) — NOT a runtime import at startup. Subprocess for base path; lazy `import markitdown` inside converter only when LLM path is taken.
- **Interface changes:** `bridge/knowledge_watcher.py._is_relevant` gains a convertible-extension branch; indexer unchanged. New module `tools/knowledge/converter.py`. New CLI `tools/valor_ingest.py`.
- **Coupling:** Minimal — converter is a pure function (`path → sidecar_path`) with no dependency on the indexer or watcher beyond the shared extension sets.
- **Data ownership:** The source file remains canonical; the sidecar is regenerable. If a sidecar exists without its source, the indexer still handles it as a regular `.md`.
- **Reversibility:** High. Remove `markitdown` from deps, revert watcher/extension change, delete `converter.py` and `valor_ingest.py`, rm any `*.md` sidecars the user doesn't want. Generated sidecars are distinguishable by their frontmatter (`source_hash:` key).

## Appetite

**Size:** Medium

**Team:** Solo dev (Valor), plus builder/validator agent pairs

**Interactions:**
- PM check-ins: 1 (scope verification after the audio-scope-cut)
- Review rounds: 1 (code review before merge)

Solo dev work with clear scope boundaries — no ambiguous requirements once audio is explicitly dropped. The blast radius is small: two files extended, two new files created.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Required by the Haiku LLM path (via Anthropic's OpenAI-compat endpoint) when `MARKITDOWN_LLM_MODEL` is set |
| `uv` available | `which uv` | Required to install the `[knowledge]` optional extra |

Run all checks: `python scripts/check_prerequisites.py docs/plans/markitdown-ingestion.md`

## Solution

### Key Elements

- **`tools/knowledge/converter.py`** — new module. Pure function interface: `convert_to_sidecar(source_path) -> Path`. Handles subprocess CLI path (default) and optional Python API path (when LLM is configured and format benefits).
- **Idempotency via frontmatter hash** — generated sidecars carry YAML frontmatter with `source_hash`, `source_path`, and `generated_by: markitdown`. The converter reads that frontmatter before regenerating and skips when the source hash matches.
- **Extended watcher** — `bridge/knowledge_watcher.py` gains a `CONVERTIBLE_EXTENSIONS` set (PDF, docx, pptx, xlsx, html, msg, epub). On match, the watcher calls the converter; the generated sidecar then trips the existing `SUPPORTED_EXTENSIONS` branch.
- **`tools/valor_ingest.py`** — new CLI module. Mirrors the pattern of `tools/valor_telegram.py` and friends. Registered in `pyproject.toml` `[project.scripts]`. Supports `--scan <dir>` for backfilling existing binary files into sidecars.
- **`/update` skill reminder** — after a successful update, the skill reminds the user that `valor-ingest --scan ~/work-vault/` will backfill any new binary formats that have been sitting in the vault unindexed.
- **`[knowledge]` optional extra** — `markitdown[pdf,docx,pptx,xlsx,outlook,youtube-transcription]` (notably **excluding** `audio-transcription`). Installation via `uv pip install -e '.[knowledge]'`.
- **LLM configuration** — new env var `MARKITDOWN_LLM_MODEL` (default unset = subprocess-only). The **only supported value** when setting this is Haiku via Anthropic's OpenAI-compat endpoint (`https://api.anthropic.com/v1/`) using the existing `ANTHROPIC_API_KEY`. A dedicated build-time probe test (`tests/integration/test_markitdown_haiku_vision.py`) must confirm that markitdown's vision request flow works end-to-end through Anthropic's OpenAI-compat endpoint before the plan ships.
- **Implementation Note (from C5):** Do NOT carry a gpt-4o-mini fallback. Per CLAUDE.md's "NO LEGACY CODE TOLERANCE" principle, a half-working fallback is worse than a clean disable. At module load in `converter.py`, if `MARKITDOWN_LLM_MODEL` is set, probe once on first call and cache the result in a module-level `_llm_path_available: bool | None`. If the probe fails (any exception constructing the client, or the 1-token ping errors), set `_llm_path_available = False`, log ONCE at WARNING ("markitdown LLM probe failed: %s — falling back to subprocess path for all image/PPTX conversions"), and route all subsequent image/PPTX conversions through the subprocess path. Image conversions via subprocess produce a generic markdown with the image filename but no description — acceptable degradation. Eliminates the `OPENAI_API_KEY` surface entirely from this feature. Update the Haiku probe test so that failure is a test failure (not a silent fallback) — the fallback inside the converter is a production safety net, not a test escape hatch.

### Flow

**Automatic watcher path:**
PDF dropped in vault → watcher detects convertible extension → converter writes `file.pdf.md` sidecar → watcher sees new `.md` → existing indexer indexes it → available in subconscious memory recall

**On-demand CLI path:**
`valor-ingest ~/Downloads/proposal.pdf --vault-subdir Consulting/leads/acme/` → converter copies source to target subdir → writes sidecar alongside → watcher (or `--scan` flag) triggers index

### Technical Approach

- **Two converter code paths, selected by `MARKITDOWN_LLM_MODEL`:**
  1. **Subprocess path (default):** `subprocess.run(["markitdown", str(source), "-o", str(tmp_sidecar)], capture_output=True, text=True, timeout=120, check=False)`. **Implementation Note (from C1):** The canonical call MUST use `check=False` (so we can inspect `returncode` + `stderr` ourselves and raise `ConversionFailed` with a stderr snippet truncated to 500 chars — required by the Failure Path Test Strategy) and `text=True` (so the `len(stdout) == 0 AND len(stderr) > 0` heuristic compares strings, not bytes). Do NOT use `check=True` — that would raise `CalledProcessError` before our explicit handling runs. On non-zero exit OR empty stdout with non-empty stderr, raise `ConversionFailed` and log. Move `tmp_sidecar` to final `{source}.md` atomically after prepending frontmatter.
  2. **Python API path (LLM configured):** Lazy `import markitdown` inside the function; build an OpenAI-compat client (Anthropic endpoint by default); call `MarkItDown(llm_client=..., llm_model=...).convert(str(source))`; write the `.text_content` to sidecar with frontmatter.
- **LLM path gating:** Only invoked when `MARKITDOWN_LLM_MODEL` is set AND the file extension is in a configurable `LLM_BENEFICIAL_EXTENSIONS` set (default: `{".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp"}`). For `.pdf`, `.docx`, `.xlsx`, `.html` — always use the subprocess path regardless of LLM config.
- **Implementation Note (from C4):** Image extensions `.png, .jpg, .jpeg, .gif, .webp` MUST be present in BOTH `CONVERTIBLE_EXTENSIONS` and `LLM_BENEFICIAL_EXTENSIONS` so that the watcher auto-ingests standalone images (e.g. architecture screenshots dropped into the vault). Without this, the `MARKITDOWN_LLM_MODEL` setting becomes PowerPoint-only, contradicting the Haiku vision probe (which uses a PNG) and Success Criterion line 338. Add a size guard in the converter: `if source.stat().st_size > 20_000_000: logger.warning("skipping %s: exceeds 20MB image size limit", source); return None` — prevents sending 50MB phone photos to a vision API. The 20MB threshold applies only to image extensions; PDFs/docx can be larger without triggering the skip.
- **Content hashing:** `sha256(source_bytes).hexdigest()` — written to sidecar frontmatter as `source_hash`. Converter reads existing sidecar frontmatter via a tiny YAML-prefix parser (first `---` block) before deciding to regenerate.
- **Sidecar naming:** `{original_filename}.md` in the same directory. Example: `report.pdf` → `report.pdf.md`. Preserves the `.pdf` in the stem for traceability.
- **Watcher wiring (`bridge/knowledge_watcher.py`):** At line 44, replace the single extension check with: if ext in SUPPORTED → existing path; elif ext in CONVERTIBLE → call converter; else skip. The converter call must be guarded in a try/except because watcher crashes can never take down the bridge (per existing module docstring).
- **Loop prevention:** The generated `.md` sidecar's path ends in the original extension + `.md` (e.g., `report.pdf.md`). The watcher sees this as a .md file, passes to indexer, no re-convert loop. The converter checks before running: if the passed path already ends in `.md`, it's a sidecar — skip entirely.
- **CLI entry point (`tools/valor_ingest.py`):** `argparse`-based. Required positional: `source` (path or URL). Optional: `--vault-subdir PATH`, `--force` (ignore hash, regenerate), `--output PATH` (explicit sidecar path), `--scan PATH` (backfill directory). **Destination defaults:** For local files, sidecar lands in the same directory as source (the filename itself is the value — `report.pdf.md` next to `report.pdf`). For URLs (no source directory exists), default to CWD but emit a notice encouraging `--vault-subdir` for vault ingestion. Import `converter.convert_to_sidecar` and call with parsed args. Exit code 0 on success, 1 on conversion failure.
- **Frontmatter format:**
  ```yaml
  ---
  source_hash: <sha256>
  source_path: report.pdf
  generated_by: markitdown
  generated_at: 2026-04-24T12:00:00Z         # first generation
  regenerated_at: 2026-04-24T12:00:00Z        # updated on any hash-mismatch regeneration
  llm_model: none | <value of config.models.HAIKU>
  ---
  ```
  **Implementation Note (from N1):** The `llm_model` frontmatter field is populated by resolving `HAIKU` from `config/models.py` at convert time — NOT by hardcoding a version-specific model ID. This ensures the feature tracks Anthropic model rotations through the single canonical source already established by PR #615. Valid values in practice: `none` (subprocess path) or the resolved value of `config.models.HAIKU`. `gpt-4o-mini` is explicitly not a valid value because C5 eliminated the OpenAI fallback. `regenerated_at` is what downstream tools (xref audit, KnowledgeDocument indexing) watch to detect content changes without reading every sidecar's body. On first generation, `regenerated_at` equals `generated_at`. On subsequent regenerations (hash mismatch triggers conversion), only `regenerated_at` is updated.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Converter subprocess failure path: assert `ConversionFailed` is raised AND logged via `logger.warning` with the source path and stderr
- [ ] Watcher catches converter exceptions: assert a converter raise does not propagate out of `_DebouncedHandler._schedule` (matches existing watcher "never crash the bridge" contract)
- [ ] `_is_convertible` path in watcher — if converter is missing (`markitdown` not installed), log once at WARNING and skip the file rather than looping on every event
- [ ] Existing `except Exception: pass` blocks in `knowledge_watcher.py` are kept — add one test asserting the watcher thread keeps running after a converter exception

### Empty/Invalid Input Handling
- [ ] Zero-byte PDF: assert converter returns without writing a sidecar (no empty `.md` files)
- [ ] Source path with whitespace/unicode: assert subprocess call quotes correctly
- [ ] URL input to CLI when offline: assert a clean error message, not a crash
- [ ] `valor-ingest` with missing argument: argparse prints usage, exit code 2

### Error State Rendering
- [ ] Conversion failure in CLI context: assert the user sees the stderr snippet (truncated to 500 chars), not a Python traceback
- [ ] Watcher context: assert failures are logged but do not produce user-visible output (the bridge is non-interactive)

## Test Impact

- [ ] `tests/unit/test_knowledge_indexer.py` (if it exists) — UPDATE: add tests for sidecar `.md` files being indexed identically to hand-written `.md` files
- [ ] `tests/unit/test_knowledge_watcher.py` (if it exists) — UPDATE: add tests for `CONVERTIBLE_EXTENSIONS` triggering the converter path
- [ ] `tests/integration/test_knowledge_pipeline.py` (if it exists) — UPDATE: add end-to-end test: drop PDF → watcher → converter → indexer → KnowledgeDocument exists

If the existing test files above do not exist, the builder creates them:
- [ ] `tests/unit/test_knowledge_converter.py` — CREATE: unit tests for the new converter module (subprocess path, Python API path, idempotency via hash)
- [ ] `tests/unit/test_valor_ingest_cli.py` — CREATE: unit tests for CLI arg parsing and error messages
- [ ] `tests/integration/test_markitdown_ingestion.py` — CREATE: real end-to-end integration using a fixture PDF

**Verification before build:** run `grep -l "knowledge_indexer\|knowledge_watcher" tests/` to find the actual existing test files and confirm their dispositions above.

## Rabbit Holes

- **Audio transcription** — markitdown's Google Web Speech path is privacy-unacceptable. Do not attempt to swap in local Whisper *in this plan* — that's a separate feature with its own install footprint (GB-sized models), streaming concerns, and test surface. Open a follow-up issue if needed.
- **`markitdown-ocr` plugin** — tempting for PDFs with image-heavy content, but requires `--use-plugins` and a separate install. Hold for v2 once we have real evidence that vault content needs OCR.
- **Converting existing `bridge/media.py` pypdf usage to markitdown** — those are for Telegram attachments, different code path, different lifetime. Do not touch.
- **Building a "smart" destination resolver for `valor-ingest` URLs** — just require `--vault-subdir` or put next to the source. No inference magic.
- **Indexing the source file itself (not just the sidecar)** — tempting for traceability but the source is a binary; the sidecar is the searchable proxy. Skip.
- **Adding markitdown to the worker runtime** — never. Subprocess or lazy import only. The worker must start even when `markitdown` is not installed.

## Risks

### Risk 1: `markitdown` CLI exit codes are undocumented
**Impact:** Silent failures where the converter thinks conversion succeeded but produced empty/garbage markdown.
**Mitigation:** In the subprocess path, treat `returncode != 0` OR `len(stdout) == 0 AND len(stderr) > 0` as failure. Write a probe test during build (Step 3) that calls `markitdown missing.pdf` empirically and records the observed exit code in a code comment for future reference.

### Risk 2: Sidecar regeneration loop
**Impact:** Watcher sees `.pdf.md` sidecar, triggers converter, converter sees it's already `.md` and skips — but could loop if naming is wrong.
**Mitigation:** Converter first-line check: if `path.suffix == ".md"`, return immediately. Unit test with a file named `weird.md.md` to confirm no recursion.

### Risk 3: Haiku via Anthropic OpenAI-compat endpoint may break on vision requests
**Impact:** `MARKITDOWN_LLM_MODEL=<HAIKU>` combined with an image file could 4xx if Anthropic's OpenAI-compat endpoint doesn't proxy vision image URLs the way OpenAI does.
**Mitigation:** The build-time probe test (`tests/integration/test_markitdown_haiku_vision.py`) is a hard gate — if Haiku vision fails, the build fails and no PR merges. At runtime, the converter's `_llm_path_available` cache (C5 Implementation Note) provides a production safety net: on probe failure at first invocation, the converter logs once at WARNING and routes all subsequent image/PPTX conversions through the subprocess path. Result: production degrades gracefully to "image filename + no description" markdown rather than hard-failing the conversion pipeline. Per C5, there is NO gpt-4o-mini fallback — the OPENAI_API_KEY surface is eliminated from this feature.

### Risk 4: pypdf version drift with markitdown's pinned pdf-extractor
**Impact:** We already have `pypdf>=6.10.2` (pyproject.toml:22) due to a CVE (XMP metadata RAM exhaustion); markitdown's `[pdf]` extra may pin a conflicting version.
**Mitigation:** Task 1a (`verify-pypdf-resolution`) is a build-time gate per C7 Implementation Note. Empirical resolution must occur before Task 2 begins. Three outcomes documented inline: (a) markitdown uses pdfminer.six — no conflict; (b) markitdown's pypdf range intersects `>=6.10.2` — uv resolves to our pin; (c) strict downgrade pin — drop the `[pdf]` extra from the `knowledge` key and rely on markitdown's subprocess CLI discovering whatever PDF backend it ships with at runtime. Builder commits the `uv.lock` diff as evidence.

### Risk 5: Sidecars pollute the vault
**Impact:** The user's hand-written vault now contains `.pdf.md` files alongside originals — visual noise.
**Mitigation:** Sidecars are visibly distinguishable by their `.pdf.md` / `.docx.md` double extension. Frontmatter `generated_by: markitdown` makes them grep-able. Document the convention in the feature doc. Optionally add a `valor-ingest --clean` that removes all stale sidecars (a follow-up, not v1).

## Race Conditions

### Race 1: Watcher detects source file before it's fully written
**Location:** `bridge/knowledge_watcher.py` `on_created` / `on_modified`
**Trigger:** User copies a large PDF (slow rsync, Finder drag). `on_created` fires on the stub, converter reads a truncated file, produces a bad sidecar, then `on_modified` fires on the full file and the debounce batches them.
**Data prerequisite:** Source file's content hash must match at convert time.
**State prerequisite:** The 2-second debounce window (`DEBOUNCE_SECONDS = 2.0` at `bridge/knowledge_watcher.py:24`) must be large enough to absorb the copy.
**Mitigation:** The existing debounce already covers this for markdown files. For larger binary sources, the converter computes the hash of the file at read time and embeds it in the sidecar. If a subsequent `on_modified` event arrives, the hash differs and the sidecar is regenerated. Net result: we may pay for 1-2 extra conversions during a large copy, but never produce a stale sidecar silently.

### Race 2: Concurrent writes of the same sidecar from watcher and CLI
**Location:** `tools/knowledge/converter.py`
**Trigger:** User runs `valor-ingest report.pdf` while the watcher is also processing the same file.
**Data prerequisite:** Both code paths must converge on the same sidecar path.
**State prerequisite:** Filesystem-level atomic write guarantees for the final rename.
**Mitigation:** Converter writes to `{sidecar}.tmp.<pid>` and `os.replace()`s to the final sidecar path. `os.replace` is atomic on POSIX. If two processes race, one wins; both computed the same content anyway (same source → same hash → same markitdown output).

## No-Gos (Out of Scope)

- **Audio transcription** — explicitly deferred. Requires local Whisper, GB-scale models, async streaming, and its own test surface. Separate issue.
- **`markitdown-ocr` plugin** — defer until vault content demonstrably needs OCR.
- **Modifying `bridge/media.py`** — different code path, different lifetime.
- **Extending `do-xref-audit` to understand binary formats** — the sidecar pattern subsumes this.
- **Indexing binary originals directly** — always index the `.md` sidecar, never the source.
- **Azure Document Intelligence extra (`[az-doc-intel]`)** — paid service, not needed.
- **Cross-machine vault sync concerns** — the vault is already in iCloud; markitdown conversion is machine-local. Sidecars sync as ordinary files.

## Update System

**Update script changes required:**
- `scripts/update/env_sync.py` — ensure `MARKITDOWN_LLM_MODEL` is listed as an optional env var (not required) so the env-sync report flags it appropriately
- `scripts/remote-update.sh` — after `uv sync`, run `uv pip install -e '.[knowledge]'` to install the markitdown extra on every machine
- `.env.example` — add a commented placeholder for `MARKITDOWN_LLM_MODEL` with documentation

**New dependencies propagated via pyproject.toml:**
- Add `markitdown[pdf,docx,pptx,xlsx,outlook]>=0.1.0` to `[project.optional-dependencies]` under a `knowledge` key. **Per N2:** the `youtube-transcription` extra is deliberately excluded; `valor-ingest <youtube-url>` delegates to the existing `youtube-transcript-api` path (pyproject.toml:16) used by `tools/valor_youtube_search` rather than carrying a second transcript fetcher.
- Existing machines pick it up via `uv pip install -e '.[knowledge]'` on next `/update`

**Migration for existing installations:**
- First run after update: the watcher ignores existing non-`.md` files in the vault until they're modified. To pick up everything already present, run `valor-ingest --scan ~/work-vault/`.
- The reminder MUST fire in front of the human exactly once, on the run that actually installs the `[knowledge]` extra — not on every cron update, not silently to stdout. **Implementation Note (from C6):** Wire the reminder into `scripts/update/run.py`'s Telegram summary formatter (the path that reports cron runs to the user per `remote-update.sh:97`). Add `backfill_reminder_needed: bool` to the update result struct in `scripts/update/deps.py`, set it to True iff (`markitdown` was absent from the pre-run `uv.lock` AND is present post-run) AND (`~/.cache/valor/markitdown-backfill-reminded` does not exist). The Telegram summary formatter appends `Tip: run 'valor-ingest --scan ~/work-vault/' to backfill existing binary files into sidecars.` when true, then the post-summary block touches the flag file. Do NOT put the reminder in `.claude/skills/update/SKILL.md` alone — the `/update` skill is only invoked by humans in Claude Code, whereas cron-driven updates go through `scripts/update/run.py` directly and would never see a skill-level reminder. The skill can ALSO include a human-readable mention, but the authoritative delivery is the Telegram summary.

## Agent Integration

**MCP server changes:** None required — `valor-ingest` is a standalone CLI, not an agent-facing tool. Exposing it via an MCP tool could be a v2 if the agent needs to initiate ingestion from chat ("ingest this file"). For v1, the agent uses existing `Read` to read `.md` sidecars once they're indexed.

**Bridge imports:** `bridge/knowledge_watcher.py` imports the new `tools/knowledge/converter.py` module. The converter module must be import-safe (no module-level `import markitdown`) so the bridge starts cleanly even when the `[knowledge]` extra isn't installed.

**Integration test requirement:** A real end-to-end test that:
1. Starts a temporary watcher pointed at a tmp dir
2. Drops a fixture PDF
3. Waits for debounce + conversion
4. Asserts the `KnowledgeDocument` model has an entry for the sidecar

No new MCP registration. No agent-facing behavior change beyond: the agent now recalls context from previously-opaque files.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/markitdown-ingestion.md` — describes the feature, the sidecar pattern, the LLM config, and the audio exclusion rationale
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/subconscious-memory.md` — note that knowledge recall now includes converted binary formats
- [ ] Update `CLAUDE.md` — add a "Knowledge Ingestion" section in the Quick Commands table with `valor-ingest` usage

### External Documentation Site
- Not applicable — this repo does not use Sphinx/MkDocs/Read the Docs.

### Inline Documentation
- [ ] Module docstring on `tools/knowledge/converter.py` describing the two code paths, idempotency model, and the sidecar frontmatter schema
- [ ] Module docstring on `tools/valor_ingest.py` describing the CLI contract
- [ ] Comment above `CONVERTIBLE_EXTENSIONS` in watcher explaining the rationale for the specific extension set (and why audio is excluded)

## Success Criteria

- [ ] `tools/knowledge/converter.py` exists with `convert_to_sidecar(source_path) -> Path` function
- [ ] `bridge/knowledge_watcher.py` routes convertible extensions through the converter
- [ ] `valor-ingest` CLI registered in `pyproject.toml` `[project.scripts]` and installed via `uv sync`
- [ ] `valor-ingest <local-path>` produces a sidecar and returns exit code 0
- [ ] `valor-ingest <youtube-url>` produces a transcript sidecar by delegating to the existing `youtube-transcript-api` path (not markitdown's `[youtube-transcription]` extra, which is deliberately excluded per N2)
- [ ] `MARKITDOWN_LLM_MODEL` env var controls the LLM path; unset → subprocess only; set → Python API for image-benefit formats
- [ ] Default LLM is Haiku (resolved from `config.models.HAIKU`, not hardcoded) via Anthropic OpenAI-compat endpoint when set
- [ ] No `OPENAI_API_KEY` references in converter/CLI code — gpt-4o-mini fallback eliminated per C5
- [ ] Python API path has a probe-and-cache mechanism: first-call probe, cached `_llm_path_available` flag, single WARNING log on failure, silent subprocess routing thereafter (per C5 Implementation Note)
- [ ] Image files over 20MB are skipped with a WARNING log (per C4 Implementation Note)
- [ ] `CONVERTIBLE_EXTENSIONS` includes `.png, .jpg, .jpeg, .gif, .webp` so standalone images in the vault are auto-described (per C4 Implementation Note)
- [ ] `valor-ingest --scan <dir>` is implemented as a mutually exclusive alternative to the `source` positional via argparse (per C2 Implementation Note)
- [ ] Watcher flush handler converts-then-indexes in the same `_flush()` iteration — no re-entrant `_schedule` call for sidecars (per C3 Implementation Note)
- [ ] Subprocess call uses `check=False, text=True` (per C1 Implementation Note) — explicit returncode + stderr inspection, stderr snippet truncated to 500 chars on failure
- [ ] Backfill reminder is emitted by `scripts/update/run.py` Telegram summary (not just the skill) on the run that first installs `markitdown`, gated by `~/.cache/valor/markitdown-backfill-reminded` (per C6 Implementation Note)
- [ ] Pre-Task-2 `uv.lock` diff shows pypdf resolved to `>=6.10.2` or pdfminer.six — documented in `pyproject.toml` comment (per C7 Implementation Note)
- [ ] Audio formats (`.mp3`, `.wav`, `.m4a`) are NOT in `CONVERTIBLE_EXTENSIONS` and NOT in the installed extras
- [ ] Sidecar frontmatter contains `source_hash`, `source_path`, `generated_by`, `generated_at`, `regenerated_at`, `llm_model`
- [ ] Re-running converter on unchanged source skips (hash match), logs at DEBUG only; regeneration on hash-mismatch updates only `regenerated_at`
- [ ] `valor-ingest --scan <dir>` backfills all convertible files in the directory (recursive)
- [ ] `/update` skill prints a one-line backfill reminder on first install; suppressed thereafter via flag file
- [ ] `do-xref-audit` skips files with `generated_by: markitdown` frontmatter
- [ ] Haiku-via-Anthropic-compat vision probe test (`tests/integration/test_markitdown_haiku_vision.py`) passes using a small fixture PNG; falls back to gpt-4o-mini only if that test fails (documented in the test file)
- [ ] Integration test: drop `tests/fixtures/sample.pdf` → sidecar → `KnowledgeDocument` row present
- [ ] Bridge starts cleanly when the `[knowledge]` extra is NOT installed (lazy import verified by test)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -r "recognize_google\|audio-transcription" tools/ bridge/ pyproject.toml` returns empty — audio path is nowhere in scope

## Team Orchestration

### Team Members

- **Builder (converter)**
  - Name: `converter-builder`
  - Role: Implement `tools/knowledge/converter.py` — subprocess path, Python API path, idempotency, frontmatter
  - Agent Type: builder
  - Resume: true

- **Builder (watcher extension)**
  - Name: `watcher-builder`
  - Role: Extend `bridge/knowledge_watcher.py` with `CONVERTIBLE_EXTENSIONS` routing; wire to converter; preserve crash-isolation
  - Agent Type: builder
  - Resume: true

- **Builder (CLI)**
  - Name: `cli-builder`
  - Role: Implement `tools/valor_ingest.py`; register in `pyproject.toml`
  - Agent Type: builder
  - Resume: true

- **Builder (dependency wiring)**
  - Name: `deps-builder`
  - Role: Add `[knowledge]` extra to `pyproject.toml`; update `.env.example`; update `scripts/remote-update.sh`
  - Agent Type: builder
  - Resume: true

- **Test-engineer (converter + CLI)**
  - Name: `converter-tester`
  - Role: Unit tests for converter and CLI; integration test end-to-end
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: `integration-validator`
  - Role: Verify bridge starts without `[knowledge]` installed; verify sidecars don't loop; verify LLM config resolves correctly
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `markitdown-docs`
  - Role: `docs/features/markitdown-ingestion.md`, update README index, update CLAUDE.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Dependency wiring
- **Task ID**: build-deps
- **Depends On**: none
- **Validates**: `uv sync && uv pip install -e '.[knowledge]'` succeeds; `which markitdown` returns a path
- **Informed By**: spike-1 (subprocess CLI is the default path), spike-2 (audio extra excluded), N2 (drop youtube-transcription — delegate to existing youtube-transcript-api)
- **Assigned To**: deps-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `knowledge = ["markitdown[pdf,docx,pptx,xlsx,outlook]>=0.1.0"]` to `[project.optional-dependencies]` in `pyproject.toml`. **Implementation Note (from N2):** `youtube-transcription` is deliberately excluded — `tools/valor_youtube_search` already depends on the lighter-weight `youtube-transcript-api` (pyproject.toml:16). `valor-ingest <youtube-url>` delegates to that existing path rather than carrying a second way to fetch transcripts.
- Add `valor-ingest = "tools.valor_ingest:main"` to `[project.scripts]`
- Add commented `MARKITDOWN_LLM_MODEL=` placeholder to `.env.example` with docstring
- Update `scripts/remote-update.sh` to include the `[knowledge]` extra in the install step

### 1a. Empirical pypdf resolution check
- **Task ID**: verify-pypdf-resolution
- **Depends On**: build-deps
- **Validates**: `uv pip tree | grep -E 'pypdf|pdfminer'` succeeds AND `python -c "import pypdf; assert pypdf.__version__ >= '6.10.2'"` exits 0
- **Assigned To**: deps-builder
- **Agent Type**: builder
- **Parallel**: false (gates Task 2)
- **Implementation Note (from C7):** Risk 4's "manual post-hoc check" is upgraded to a deterministic build-time gate. After Task 1's install, run `uv pip tree | grep -E 'pypdf|pdfminer'` and capture the resolved `pypdf` version. The existing pin `pypdf>=6.10.2` (pyproject.toml:22) is in place because of a CVE — downgrade is not acceptable. Three possible outcomes:
  1. `markitdown[pdf]` uses `pdfminer.six` (not `pypdf`) — no conflict, proceed.
  2. `markitdown[pdf]` uses `pypdf` at a range that intersects `>=6.10.2` — direct dep wins in uv resolution, proceed.
  3. `markitdown[pdf]` pins `pypdf<6` strictly — STOP. Builder must decide between two options before touching Task 2: (a) drop the `[pdf]` extra from the knowledge key (markitdown will still convert PDFs via the subprocess CLI path using whatever PDF backend it discovers at runtime; we keep our CVE-safe pypdf for bridge attachment handling), OR (b) return the plan to NEEDS REVISION.
- Commit the `uv.lock` diff as evidence of resolution.
- Document the observed resolved PDF extractor (pdfminer vs pypdf vs both) in a comment above the `knowledge` extra in `pyproject.toml` for future maintainers.

### 2. Converter module
- **Task ID**: build-converter
- **Depends On**: verify-pypdf-resolution
- **Validates**: `tests/unit/test_knowledge_converter.py`
- **Informed By**: spike-1 (two code paths), spike-2 (audio excluded from convertible set)
- **Assigned To**: converter-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/knowledge/converter.py` with `convert_to_sidecar(source_path: Path, *, force: bool = False) -> Path | None`
- Implement subprocess path: `subprocess.run(["markitdown", str(source), "-o", str(tmp)], capture_output=True, text=True, timeout=120, check=False)`; raise `ConversionFailed` on rc != 0 or empty stdout+non-empty stderr
- Implement Python API path gated by `MARKITDOWN_LLM_MODEL` env var AND extension in `LLM_BENEFICIAL_EXTENSIONS`
- Lazy `import markitdown` inside the Python API branch only
- Implement content-hash idempotency: read existing sidecar frontmatter, compare `source_hash`, skip if match
- Write sidecar with YAML frontmatter (`source_hash`, `source_path`, `generated_by`, `generated_at`, `llm_model`)
- Use `os.replace(tmp, final)` for atomic write
- Module-level constant `CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".msg", ".epub", ".png", ".jpg", ".jpeg", ".gif", ".webp"}` — image extensions included per C4 Implementation Note so standalone images in the vault are auto-described. **Explicitly not** `.mp3`, `.wav`, `.m4a` (per spike-2).
- Module-level constant `LLM_BENEFICIAL_EXTENSIONS = {".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp"}` — subset that benefits from LLM vision path when `MARKITDOWN_LLM_MODEL` is set.
- Implement the 20MB image size guard (C4): for extensions in `{".png", ".jpg", ".jpeg", ".gif", ".webp"}`, skip with a WARNING log when `source.stat().st_size > 20_000_000`.
- Implement the LLM probe-and-cache (C5): module-level `_llm_path_available: bool | None = None`; on first Python-API-path call when `MARKITDOWN_LLM_MODEL` is set, construct the OpenAI-compat client against Anthropic + do a 1-token ping; cache result; on failure log WARNING once and route all subsequent image/PPTX conversions through subprocess.
- Import `HAIKU` from `config.models` (per N1) and use it as the model identifier in frontmatter and in the probe client config — do NOT hardcode the string `"claude-haiku-4-5-20251001"`.
- No `import markitdown` at module top — only lazy import inside Python API path

### 3. Watcher extension
- **Task ID**: build-watcher
- **Depends On**: build-converter
- **Validates**: `tests/unit/test_knowledge_watcher.py`
- **Assigned To**: watcher-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `CONVERTIBLE_EXTENSIONS` and `convert_to_sidecar` from `tools.knowledge.converter`
- In `_DebouncedHandler._is_relevant` at `bridge/knowledge_watcher.py:44`, expand the filter: accept a file if its extension is in `SUPPORTED_EXTENSIONS` OR `CONVERTIBLE_EXTENSIONS`. Do NOT route from `_is_relevant` itself — routing happens in `_flush`.
- **Implementation Note (from C3):** The convert-then-index handoff MUST happen inside the SAME `_flush()` iteration — do NOT rely on the watchdog OS event firing `on_created` for the new sidecar. Concretely, replace `for path in paths: index_file(path)` with: `for path in paths: ext = Path(path).suffix.lower(); if ext in CONVERTIBLE_EXTENSIONS: sidecar = convert_to_sidecar(Path(path)); if sidecar is not None: index_file(str(sidecar)); elif ext in SUPPORTED_EXTENSIONS: index_file(path)`. Rationale: (a) keeps ownership in-flush so there's no re-entrant `_schedule` call that could reset the 2s debounce for every convertible file (starving the indexing pass under rapid drops); (b) the converter's own `.md` self-check remains a belt-and-suspenders guard for when the watchdog DOES still fire on the new sidecar — that second event hits the converter, sees `.md`, returns early (None), and the caller falls through to existing `SUPPORTED_EXTENSIONS` handling for the sidecar. Do NOT add a separate scheduling queue for conversions — single queue + single flush is simpler and correct.
- Wrap the converter call in try/except; log WARNING on exception; never propagate (the watcher's "never crash the bridge" contract).
- Preserve the existing module docstring contract: "A crash in the watcher thread must never take down the bridge"

### 4. `valor-ingest` CLI
- **Task ID**: build-cli
- **Depends On**: build-converter
- **Validates**: `tests/unit/test_valor_ingest_cli.py`
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true (with build-watcher)
- Create `tools/valor_ingest.py` mirroring the shape of `tools/valor_email.py` / `tools/valor_telegram.py`
- Argparse: positional `source` (nargs="?"), `--scan PATH`, `--vault-subdir PATH`, `--force`, `--output PATH`
- **Implementation Note (from C2):** Use `argparse.add_mutually_exclusive_group(required=True)` with one member being the `source` positional (`nargs="?"` so argparse treats it as optional when absent) and the other being `--scan PATH`. Semantics: without `--scan`, call `convert_to_sidecar(source)` once. With `--scan PATH`, `os.walk` the directory, filter for `ext in CONVERTIBLE_EXTENSIONS`, and call `convert_to_sidecar` on each (recursive, unlimited depth — the vault is user-managed). `--scan` combined with `--vault-subdir` is nonsensical (scan operates in place) — raise argparse error. `--scan` is required to satisfy Success Criterion line 335 and the `/update` backfill reminder.
- Support URL input: if source starts with `http`, pass directly to converter's Python API path (subprocess CLI support for URLs is unverified per spike-1)
- Exit code 0 on success, 1 on conversion failure, 2 on argparse error (argparse default)
- Entry point function named `main()`

### 5. Test suite
- **Task ID**: build-tests
- **Depends On**: build-converter, build-watcher, build-cli
- **Validates**: the tests themselves (they should pass against the built code)
- **Assigned To**: converter-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_knowledge_converter.py` — 10+ cases covering subprocess success, subprocess failure, Python API path, hash idempotency, sidecar naming, `.md` skip, frontmatter parsing (including `regenerated_at` update), empty source, unicode paths, loop prevention
- Create `tests/unit/test_valor_ingest_cli.py` — argparse edge cases, URL handling, exit codes, `--scan` recursion
- Create `tests/integration/test_markitdown_ingestion.py` — real end-to-end using fixture PDF: drop file → wait for watcher debounce → assert `KnowledgeDocument` row exists
- Create `tests/integration/test_markitdown_haiku_vision.py` — probe test that imports `HAIKU` from `config.models`, sets `MARKITDOWN_LLM_MODEL=HAIKU` in the test env, converts a small fixture PNG, and asserts the resulting markdown contains an LLM-generated description. **Implementation Note (from N1 + C5):** The test MUST import the `HAIKU` constant (not hardcode the version string) so that future Anthropic model rotations don't silently bit-rot this test. Probe failure is a hard test failure — do NOT document a gpt-4o-mini fallback path (C5 removed it). The production converter's `_llm_path_available = False` flag is a runtime safety net for production, not a test escape hatch; in the test environment, the probe must succeed for the build to ship.
- **Implementation Note (from C2, test coverage):** The CLI test suite must cover `--scan` including: (a) directory with mixed convertible/non-convertible/existing-sidecar files; (b) nested subdirectories; (c) empty directory; (d) `--scan` + `--vault-subdir` combination (expected argparse error); (e) `--scan` with no `source` positional (valid) and `source` without `--scan` (valid); (f) neither `--scan` nor `source` (argparse error).
- Create `tests/fixtures/` directory if absent and check in: `tests/fixtures/sample.pdf` (small, <100KB) and `tests/fixtures/sample.png` (small, <100KB). Per the Structural Check CAUTION note on line 618, the directory does not exist today — the build must create it.
- Run `pytest tests/ -k markitdown -v` and confirm all pass

### 6. Integration validation
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify bridge starts when `[knowledge]` is NOT installed — simulate with `uv pip uninstall markitdown` and confirm bridge boot
- Verify sidecar loop prevention — create `weird.pdf.md.md` and confirm no infinite conversion
- Verify `MARKITDOWN_LLM_MODEL=claude-haiku-4-5-20251001` path — PNG fixture produces a sidecar with LLM-generated description
- Confirm `grep -r "recognize_google\|audio-transcription" tools/ bridge/ pyproject.toml` returns empty

### 6.5. Xref-audit sidecar skip + /update backfill reminder
- **Task ID**: build-skill-integrations
- **Depends On**: build-cli
- **Validates**: manual smoke — run `do-xref-audit` in a vault containing a sidecar; simulate a cron update where `markitdown` is newly installed and confirm the Telegram summary shows the backfill tip exactly once
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true (with build-tests)
- In `.claude/skills/do-xref-audit/SKILL.md`, add a filter step: when inventorying vault markdown, skip any file whose YAML frontmatter contains `generated_by: markitdown` (sidecars are derivative; their source is the authoritative content)
- **Per C6 Implementation Note**, wire the backfill reminder through `scripts/update/run.py` + `scripts/update/deps.py`, NOT just `.claude/skills/update/SKILL.md`:
  - In `scripts/update/deps.py`: detect `markitdown` newly present in `uv.lock` after `uv sync` and return `backfill_reminder_needed: bool` as part of the dependency step result
  - In `scripts/update/run.py`: in the Telegram summary formatter, if `backfill_reminder_needed` AND `~/.cache/valor/markitdown-backfill-reminded` does not exist, append `Tip: run 'valor-ingest --scan ~/work-vault/' to backfill existing binary files into sidecars.` to the summary, then touch the flag file
  - In `.claude/skills/update/SKILL.md`: add a human-readable mention of the same reminder, gated on the same flag file, so that when a human explicitly runs `/update` they also see it. The skill mention is secondary; the Telegram summary is authoritative.
- Document the `regenerated_at` frontmatter convention in the xref-audit skill so future audit extensions can use it for change-detection

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration, build-skill-integrations
- **Assigned To**: markitdown-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/markitdown-ingestion.md` with: overview, sidecar pattern, LLM config, audio exclusion rationale, troubleshooting
- Update `docs/features/README.md` index table
- Update `docs/features/subconscious-memory.md` to note binary-format coverage
- Update `CLAUDE.md` Quick Commands with `valor-ingest` usage including `--scan` backfill
- Document the `regenerated_at` frontmatter convention and how future tools should detect sidecar changes

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite (`pytest tests/ -x`)
- Run ruff: `python -m ruff check . && python -m ruff format --check .`
- Confirm all Success Criteria checkboxes
- Confirm all Verification table commands pass

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Markitdown installed | `which markitdown` | exit code 0 |
| Audio path absent | `grep -r "recognize_google\|audio-transcription" tools/ bridge/ pyproject.toml` | exit code 1 |
| Converter imports cleanly | `python -c "from tools.knowledge.converter import convert_to_sidecar; print(convert_to_sidecar)"` | exit code 0 |
| CLI registered | `valor-ingest --help` | exit code 0 |
| Bridge starts without extra | `uv pip uninstall -y markitdown && python -c "from bridge import knowledge_watcher" && uv pip install -e '.[knowledge]'` | exit code 0 |

## Critique Results

**Plan**: `docs/plans/markitdown-ingestion.md`
**Issue**: #1161
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor (inline — sub-agent Task tool not available in this environment; each lens executed against the plan + verified source)
**Findings**: 9 total (0 blockers, 7 concerns, 2 nits)

## Blockers

None.

## Concerns

### C1. Plan self-contradicts on subprocess `check=` flag
- **Severity**: CONCERN
- **Critics**: Consistency Auditor
- **Location**: Solution → Technical Approach (line 169) vs Step-by-Step Tasks → Task 2 build-converter (line 416)
- **Finding**: The Technical Approach section specifies `subprocess.run(..., check=True, capture_output=True, timeout=120)` while the Step-by-Step task body specifies `subprocess.run(..., capture_output=True, text=True, timeout=120, check=False)`. Two different signatures for the same call — `check=True` would itself raise `CalledProcessError` on non-zero rc, but the plan then also says "raise `ConversionFailed` on rc != 0", which only runs if check=False. A builder will pick one and the tests will reflect the wrong contract.
- **Suggestion**: Fix the inconsistency in one direction. The task body at line 416 is the correct form (`check=False` + explicit `returncode` check + `text=True` for stderr decoding). Update line 169 to match, and also add `text=True` there so the stderr-inspection branch ("empty stdout AND non-empty stderr") actually works on bytes-returning default.
- **Implementation Note**: Canonical call must be `subprocess.run(["markitdown", str(source), "-o", str(tmp)], capture_output=True, text=True, timeout=120, check=False)` because (a) `check=False` is required so the code can inspect returncode+stderr, wrap as `ConversionFailed`, and include a stderr snippet truncated to 500 chars (the Failure Path Test Strategy already requires this); (b) `text=True` is required so the `len(stdout)==0 AND len(stderr)>0` heuristic compares strings, not bytes.

### C2. `--scan` is in Solution and Success Criteria but dropped from Task 4 argparse spec
- **Severity**: CONCERN
- **Critics**: Consistency Auditor, Simplifier (dead flag risk), User (backfill is an advertised capability)
- **Location**: Solution line 176 lists `--scan PATH`; Success Criterion line 335 requires it; the `/update` backfill reminder (line 291, 484) invokes it; Task 4 at line 446 lists only `positional source, --vault-subdir PATH, --force, --output PATH` — `--scan` is missing.
- **Finding**: A builder working strictly from Task 4 will not add `--scan`, which will then fail the `valor-ingest --scan <dir>` success criterion and break the `/update` post-install reminder that the same plan prescribes. This is the single strongest contradiction between Solution + Success Criteria + Task list.
- **Suggestion**: Add `--scan PATH` explicitly to Task 4's argparse list. Specify that `--scan` is mutually exclusive with the `source` positional (argparse: `source` becomes optional when `--scan` is provided; otherwise `source` is required).
- **Implementation Note**: Use `argparse.add_mutually_exclusive_group(required=True)` with one member being the `source` positional (set `nargs="?"`) and the other being `--scan PATH`. Semantics: without `--scan`, call `convert_to_sidecar(source)` once. With `--scan PATH`, `os.walk` the directory, filter for `ext in CONVERTIBLE_EXTENSIONS`, and call `convert_to_sidecar` on each. Recursion depth is unlimited (vault is user-managed). `--scan` + `--vault-subdir` is nonsensical — raise argparse error.

### C3. Watcher "converts then re-fires on sidecar" relies on debounce coincidence, not a durable guard
- **Severity**: CONCERN
- **Critics**: Adversary, Skeptic
- **Location**: Data Flow step 4; Solution "Loop prevention" (line 175); Step 3 watcher task (line 433-436)
- **Finding**: The loop-prevention story says: convertible file creates sidecar → sidecar's `.md` extension puts it in `SUPPORTED_EXTENSIONS` → indexer runs → no loop because the converter checks for `.md` on input. But the watcher runs converter + indexer inside the SAME `_flush()` batch. In the current `_DebouncedHandler._flush` (SOURCE_FILES `bridge/knowledge_watcher.py:91-120`), both `paths` and `deletes` are processed once. If the converter writes the sidecar from INSIDE `_flush()`, the watchdog observer will fire `on_created` on the sidecar AFTER `_flush()` starts — that event lands in a NEW debounce window 2s later, and the sidecar DOES get indexed (good). But if the Task 3 wiring has the converter route go through the existing `index_file` path (it does: "pass the generated sidecar through the existing indexer"), the plan never says whether the converter call happens in `_flush()` or as a side effect that re-enters `_schedule`. If the converter writes the sidecar and `_schedule(sidecar)` is called synchronously, it resets the 2s debounce for every convertible file — repeated rapid drops will starve the indexing pass.
- **Suggestion**: Make the ordering explicit in Task 3: inside `_flush()`, for each pending convertible path, call `convert_to_sidecar(path)` synchronously, then immediately call `index_file(sidecar_path)` in the same flush iteration. Do NOT rely on the watchdog event to re-pick up the sidecar — that's a round-trip through the OS that depends on timing.
- **Implementation Note**: The watcher's current `_flush` iterates `for path in paths: index_file(path)`. New shape: `for path in paths: if ext in CONVERTIBLE: sidecar = convert_to_sidecar(path); if sidecar: index_file(str(sidecar)); elif ext in SUPPORTED: index_file(path)`. This keeps ownership in-flush (no re-entrant `_schedule` call), and the converter's own `.md` self-check remains a belt-and-suspenders guard for when the watchdog DOES still fire on the new sidecar — that second event will hit the converter, see `.md`, return early, and then flow into the indexer path.

### C4. `LLM_BENEFICIAL_EXTENSIONS` conflicts with `CONVERTIBLE_EXTENSIONS` on PNG/JPG
- **Severity**: CONCERN
- **Critics**: Consistency Auditor, Skeptic
- **Location**: Solution → Technical Approach (line 171) lists `LLM_BENEFICIAL_EXTENSIONS = {".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp"}`; Task 2 (line 422) lists `CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".msg", ".epub"}` — no image extensions.
- **Finding**: If `.png/.jpg/...` are NOT in `CONVERTIBLE_EXTENSIONS`, the watcher will never route them to the converter, and the LLM path for image description will only ever fire on `.pptx` — making the entire `MARKITDOWN_LLM_MODEL` configuration effectively a PowerPoint-only feature for automatic ingestion. Yet the Haiku vision probe test (line 461) uses a PNG, and Success Criterion (line 338) also uses a PNG.
- **Suggestion**: Decide: either images ARE auto-ingested (add `.png, .jpg, .jpeg, .gif, .webp` to `CONVERTIBLE_EXTENSIONS`) or they are NOT (drop them from `LLM_BENEFICIAL_EXTENSIONS`, restrict the LLM path to PPTX-with-embedded-images, and reword the Haiku probe as a `valor-ingest <png>` CLI test rather than a watcher test).
- **Implementation Note**: Recommended: add image extensions to `CONVERTIBLE_EXTENSIONS`. A standalone image in the vault (e.g. a client's architecture screenshot dropped into `~/work-vault/Consulting/leads/acme/diagram.png`) is exactly the kind of content the user wants described and recalled. Flag for watcher: image files can be large (phone photos = 8-12MB); honor a max-size guard, e.g. `if source.stat().st_size > 20_000_000: log and skip` — don't send 50MB JPEGs to a vision API.

### C5. LLM path leaks `OPENAI_API_KEY` requirement without stating it in Prerequisites
- **Severity**: CONCERN
- **Critics**: Operator, User
- **Location**: Solution → "LLM configuration" (line 156); Prerequisites table (line 137-143)
- **Finding**: The plan lists only `ANTHROPIC_API_KEY` as a prerequisite, but the fallback path ("falls back to gpt-4o-mini") and the "Other allowed values: OpenAI model IDs (uses `OPENAI_API_KEY`)" sentence mean the feature's documented recommended-fallback requires a key that's nowhere in the prereq check. `OPENAI_API_KEY` is already in `.env.example:57` for embeddings, so it's likely present, but the plan's recommended Haiku-fails-→-gpt-4o-mini flow depends on it silently.
- **Suggestion**: Either (a) add `OPENAI_API_KEY` to the Prerequisites table with a note "required only if `MARKITDOWN_LLM_MODEL` names a gpt-* model or the Haiku probe fails", or (b) drop the gpt-4o-mini fallback and simply disable the LLM path entirely if Haiku probe fails — logging once at WARNING and continuing with subprocess-only.
- **Implementation Note**: Option (b) is cleaner and matches CLAUDE.md's "NO LEGACY CODE TOLERANCE" principle — don't carry a half-working fallback. Concretely: in `converter.py` at module load, if `MARKITDOWN_LLM_MODEL` is set, probe once at first call (cached result in a module-level `_llm_path_available: bool | None`) by constructing the client and making a 1-token ping; on any failure set `_llm_path_available = False` and route all subsequent image/PPTX conversions through the subprocess path with a single WARNING log. Eliminates the OPENAI_API_KEY surface entirely.

### C6. Backfill reminder flag file fights `/update`'s cron-driven model
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Update System (line 289-291); Task 6.5 (line 484)
- **Finding**: `scripts/remote-update.sh` runs via cron (see bridge-worker architecture) and is also invoked by the Telegram `/update` command. The plan's flag file `~/.cache/valor/markitdown-backfill-reminded` is per-machine and persists forever after first write. But: (a) the reminder is emitted to stdout, which on cron runs goes nowhere visible to the user; (b) the `/update` skill is separate from `remote-update.sh` — the plan says "In `.claude/skills/update/SKILL.md` (or the remote-update flow)" as if they're interchangeable, but SKILL.md is only consulted when a human invokes `/update` in Claude Code, while the cron path runs `scripts/update/run.py` directly and will never see the reminder. Net: the reminder will fire at most once, in a context the user probably isn't watching.
- **Suggestion**: Wire the reminder into `scripts/update/run.py`'s final summary block (the one that goes to Telegram on cron runs per line 97 of `remote-update.sh`: "Output goes directly to Telegram - keep it clean for PM-style summary"). Gate it on the flag file AND on detecting that the `[knowledge]` extra was installed for the first time in this run (so it appears in the first "I just upgraded you" Telegram message, not silently on cron).
- **Implementation Note**: `scripts/update/run.py` (or `scripts/update/deps.py`) already knows whether a `uv sync` actually installed new packages. Add `backfill_reminder_needed: bool` to the result struct, set it to True iff (`markitdown` package was not in the lockfile before this run) AND (`~/.cache/valor/markitdown-backfill-reminded` does not exist). In the Telegram summary formatter, append the Tip line when true, then touch the flag file. This puts the reminder in front of the human exactly once, on the run that actually landed the dep.

### C7. Plan claims `pypdf>=6.10.2` "may conflict with markitdown" but has no concrete handling
- **Severity**: CONCERN
- **Critics**: Archaeologist, Skeptic
- **Location**: Risks → Risk 4 (line 244-246); SOURCE_FILES `pyproject.toml:22`
- **Finding**: The plan's Risk 4 mitigation is "after install, run `uv pip tree | grep pdf`" — that's a manual post-hoc check, not a deterministic build-time guard. If markitdown[pdf] pins `pypdf<6`, `uv sync` will fail the first time it runs on every installed machine, and the `/update` cron will keep re-failing with no human seeing it until someone notices the bridge isn't picking up new docs. The existing pin exists because of a CVE (XMP metadata RAM exhaustion) — downgrading is not acceptable.
- **Suggestion**: Upgrade Risk 4 to a pre-build empirical test: before merging, run `uv pip install -e '.[knowledge]'` locally and record the resolved `pypdf` version in the plan. If it's <6.10.2, negotiate the intersection via explicit pyproject constraint (e.g., `pypdf>=6.10.2` as a direct dep already wins because direct deps beat transitive ranges in uv). If markitdown actually uses `pdfminer.six` (which several distributions do), there's no conflict — settle this empirically in Task 1 before any builder touches Task 2.
- **Implementation Note**: Add a new Task 1a (Validates: `uv pip tree | grep -E 'pypdf|pdfminer'` succeeds and `pypdf>=6.10.2` still resolves). Builder must commit the `uv.lock` diff as evidence. If resolution fails, the plan returns to NEEDS REVISION with a specific decision on extras (`markitdown[pdf]` vs `markitdown` without pdf extra + rely on pypdf we already have — the latter would be the simplification).

## Nits

### N1. `HAIKU` constant from `config.models` is mentioned in Prior Art but never wired into the plan
- **Severity**: NIT
- **Critics**: Archaeologist
- **Location**: Prior Art (line 56); Solution → LLM configuration (line 156) refers to "claude-haiku-4-5-20251001" as a string in the frontmatter schema (line 186) and in the test task (line 461).
- **Finding**: The plan correctly notes that PR #615 established `HAIKU = "claude-haiku-4-5-20251001"` in `config.models` is the canonical source (verified in SOURCE_FILES `config/models.py:20`), but the example frontmatter and the probe test hardcode the string rather than importing the constant. Small drift hazard — next time Anthropic rotates the model ID, this feature will lag.
- **Suggestion**: In `converter.py`, import `HAIKU` from `config.models` and write that resolved value into frontmatter `llm_model`. The probe test file can also import `HAIKU` rather than hardcoding the string.

### N2. "YouTube transcription" is in the install extras but Success Criterion gated on it
- **Severity**: NIT
- **Critics**: User
- **Location**: Update System (line 286); Success Criterion line 329
- **Finding**: The `[youtube-transcription]` extra adds install footprint; meanwhile `tools/valor_youtube_search` already uses `youtube-transcript-api` (pyproject.toml:16) which is lighter weight and already in the base deps. Adding markitdown's YouTube path on top is a second way to do the same thing.
- **Suggestion**: Either drop `youtube-transcription` from the extras and have `valor-ingest <youtube-url>` delegate to the existing `youtube-transcript-api`-based path (which produces clean text without markitdown), or explicitly justify why markitdown's path is better for consulting-material transcripts.

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | `## Documentation`, `## Update System`, `## Agent Integration`, `## Test Impact` all present and non-empty |
| Task numbering | PASS | Tasks 1, 2, 3, 4, 5, 6, 6.5, 7, 8 — the 6.5 is a deliberate insertion for skill-integration and is acknowledged |
| Dependencies valid | PASS | All `Depends On` references point to real task IDs (`build-deps`, `build-converter`, etc.) |
| File paths exist | PASS | `bridge/knowledge_watcher.py`, `tools/knowledge/indexer.py`, `config/models.py`, `pyproject.toml`, `scripts/remote-update.sh`, `scripts/update/env_sync.py`, `.claude/skills/update/SKILL.md`, `.claude/skills/do-xref-audit/SKILL.md` all verified. New paths (`tools/knowledge/converter.py`, `tools/valor_ingest.py`) flagged as intentionally new |
| Prerequisites met | PASS | `python scripts/check_prerequisites.py docs/plans/markitdown-ingestion.md` → both checks pass (`ANTHROPIC_API_KEY`, `uv` available) |
| Cross-references | PARTIAL | Success Criteria mostly map to tasks; `valor-ingest --scan` is in SC but missing from Task 4 argparse spec (see C2). All No-Gos are honored in Tasks. Rabbit Holes are honored (audio never appears as planned work). |
| Test fixture path | CAUTION | Plan references `tests/fixtures/sample.pdf` and `tests/fixtures/sample.png` (line 462), but `tests/fixtures/` does not exist today. Test Impact section's builder instruction should explicitly create the directory and check in small fixtures (each <100KB to avoid repo bloat). |

## Verdict

**READY TO BUILD (with concerns)** — No BLOCKERs. Seven CONCERNs identified (C1-C7), each with a concrete Implementation Note. Triggering a revision pass on `/do-plan` to embed the Implementation Notes (especially C1 subprocess signature, C2 `--scan` in Task 4, C3 flush-ordering, C4 image-extension consistency) into the plan text before build begins. Two NITs do not block.

The plan is substantively sound — spikes are well-run, audio exclusion is correctly reasoned, idempotency and loop-prevention are thought through. The concerns are almost entirely self-consistency issues between sections written independently; a single revision pass should close them all without reshaping the design.

---

## Resolved Decisions

*(from Open Questions, confirmed with Valor on 2026-04-24)*

1. **Default LLM → Haiku via Anthropic OpenAI-compat endpoint**, gated by a build-time vision probe test (`tests/integration/test_markitdown_haiku_vision.py`). If the probe fails at build time, the converter documents the failure and falls back to `gpt-4o-mini`.
2. **`valor-ingest` destinations:** For local paths, sidecar next to source — the filename itself carries the value (`report.pdf.md` next to `report.pdf`). For URLs, default to CWD but print a notice encouraging `--vault-subdir`. CLI help text encourages explicit destination.
3. **Backfill is in scope:** `valor-ingest --scan <dir>` added to v1. The `/update` skill emits a one-time reminder after install (flag-file-gated so it doesn't spam on every update).
4. **`do-xref-audit` skips sidecars** via the `generated_by: markitdown` frontmatter tag. Change detection uses a new `regenerated_at` field — updated only on hash-mismatch regeneration. No commit hook (explicitly ruled out).
