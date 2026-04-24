---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1161
last_comment_id:
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
- **`tools/valor_ingest.py`** — new CLI module. Mirrors the pattern of `tools/valor_telegram.py` and friends. Registered in `pyproject.toml` `[project.scripts]`.
- **`[knowledge]` optional extra** — `markitdown[pdf,docx,pptx,xlsx,outlook,youtube-transcription]` (notably **excluding** `audio-transcription`). Installation via `uv pip install -e '.[knowledge]'`.
- **LLM configuration** — new env var `MARKITDOWN_LLM_MODEL` (default unset = subprocess-only). If set to a Haiku model ID, the converter constructs an OpenAI-compatible client pointed at `https://api.anthropic.com/v1/` using `ANTHROPIC_API_KEY`, matching the pattern already used elsewhere in this codebase. Other allowed values: OpenAI model IDs (uses `OPENAI_API_KEY` and default OpenAI endpoint).

### Flow

**Automatic watcher path:**
PDF dropped in vault → watcher detects convertible extension → converter writes `file.pdf.md` sidecar → watcher sees new `.md` → existing indexer indexes it → available in subconscious memory recall

**On-demand CLI path:**
`valor-ingest ~/Downloads/proposal.pdf --vault-subdir Consulting/leads/acme/` → converter copies source to target subdir → writes sidecar alongside → watcher (or `--scan` flag) triggers index

### Technical Approach

- **Two converter code paths, selected by `MARKITDOWN_LLM_MODEL`:**
  1. **Subprocess path (default):** `subprocess.run(["markitdown", str(source), "-o", str(tmp_sidecar)], check=True, capture_output=True, timeout=120)`. On non-zero exit OR empty output with non-empty stderr, raise `ConversionFailed` and log. Move `tmp_sidecar` to final `{source}.md` atomically after prepending frontmatter.
  2. **Python API path (LLM configured):** Lazy `import markitdown` inside the function; build an OpenAI-compat client (Anthropic endpoint by default); call `MarkItDown(llm_client=..., llm_model=...).convert(str(source))`; write the `.text_content` to sidecar with frontmatter.
- **LLM path gating:** Only invoked when `MARKITDOWN_LLM_MODEL` is set AND the file extension is in a configurable `LLM_BENEFICIAL_EXTENSIONS` set (default: `{".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp"}`). For `.pdf`, `.docx`, `.xlsx`, `.html` — always use the subprocess path regardless of LLM config.
- **Content hashing:** `sha256(source_bytes).hexdigest()` — written to sidecar frontmatter as `source_hash`. Converter reads existing sidecar frontmatter via a tiny YAML-prefix parser (first `---` block) before deciding to regenerate.
- **Sidecar naming:** `{original_filename}.md` in the same directory. Example: `report.pdf` → `report.pdf.md`. Preserves the `.pdf` in the stem for traceability.
- **Watcher wiring (`bridge/knowledge_watcher.py`):** At line 44, replace the single extension check with: if ext in SUPPORTED → existing path; elif ext in CONVERTIBLE → call converter; else skip. The converter call must be guarded in a try/except because watcher crashes can never take down the bridge (per existing module docstring).
- **Loop prevention:** The generated `.md` sidecar's path ends in the original extension + `.md` (e.g., `report.pdf.md`). The watcher sees this as a .md file, passes to indexer, no re-convert loop. The converter checks before running: if the passed path already ends in `.md`, it's a sidecar — skip entirely.
- **CLI entry point (`tools/valor_ingest.py`):** `argparse`-based. Required positional: `source` (path or URL). Optional: `--vault-subdir PATH`, `--force` (ignore hash, regenerate), `--output PATH` (explicit sidecar path). Import `converter.convert_to_sidecar` and call with parsed args. Exit code 0 on success, 1 on conversion failure.
- **Frontmatter format:**
  ```yaml
  ---
  source_hash: <sha256>
  source_path: report.pdf
  generated_by: markitdown
  generated_at: 2026-04-24T12:00:00Z
  llm_model: none | claude-haiku-4-5-20251001 | gpt-4o-mini
  ---
  ```

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
**Impact:** `MARKITDOWN_LLM_MODEL=claude-haiku-...` combined with an image file could 4xx if Anthropic's OpenAI-compat endpoint doesn't proxy vision image URLs the way OpenAI does.
**Mitigation:** Add a build-time probe (a small test fixture PNG + Haiku) that validates the Anthropic compat path works for at least one image. If it doesn't, document `MARKITDOWN_LLM_MODEL=gpt-4o-mini` as the recommended setting and downgrade Haiku to "experimental" in the feature docs.

### Risk 4: pypdf version drift with markitdown's pinned pdf-extractor
**Impact:** We already have `pypdf>=6.10.2`; markitdown's `[pdf]` extra may pin a conflicting version.
**Mitigation:** After install, run `uv pip tree | grep pdf` and verify no conflict. If markitdown uses `pdfminer.six` (not pypdf), no conflict. If it uses pypdf at a different pin, choose the intersection in the pyproject constraint.

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
- **Backfilling existing binary files in the vault** — initial v1 only handles files dropped/modified after the feature ships. Retroactive ingestion via `valor-ingest --scan ~/work-vault/` can be a follow-up if the user wants it.
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
- Add `markitdown[pdf,docx,pptx,xlsx,outlook,youtube-transcription]>=0.1.0` to `[project.optional-dependencies]` under a `knowledge` key
- Existing machines pick it up via `uv pip install -e '.[knowledge]'` on next `/update`

**Migration for existing installations:**
- First run after update: the watcher will ignore non-`.md` files already in the vault (only the modified-after-ship files trigger conversion). Document this explicitly.
- Optional backfill: `valor-ingest --scan ~/work-vault/` — out of scope for v1, but the architecture supports it.

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
- [ ] `valor-ingest <youtube-url>` produces a transcript sidecar (when `[youtube-transcription]` extra is installed)
- [ ] `MARKITDOWN_LLM_MODEL` env var controls the LLM path; unset → subprocess only; set → Python API for image-benefit formats
- [ ] Default LLM is Haiku via Anthropic OpenAI-compat endpoint when set
- [ ] Audio formats (`.mp3`, `.wav`, `.m4a`) are NOT in `CONVERTIBLE_EXTENSIONS` and NOT in the installed extras
- [ ] Sidecar frontmatter contains `source_hash`, `source_path`, `generated_by`, `generated_at`, `llm_model`
- [ ] Re-running converter on unchanged source skips (hash match), logs at DEBUG only
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
- **Informed By**: spike-1 (subprocess CLI is the default path), spike-2 (audio extra excluded)
- **Assigned To**: deps-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `knowledge = ["markitdown[pdf,docx,pptx,xlsx,outlook,youtube-transcription]>=0.1.0"]` to `[project.optional-dependencies]` in `pyproject.toml`
- Add `valor-ingest = "tools.valor_ingest:main"` to `[project.scripts]`
- Add commented `MARKITDOWN_LLM_MODEL=` placeholder to `.env.example` with docstring
- Update `scripts/remote-update.sh` to include the `[knowledge]` extra in the install step
- Verify no pypdf version conflict: `uv pip tree | grep -i pdf`

### 2. Converter module
- **Task ID**: build-converter
- **Depends On**: build-deps
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
- Module-level constant `CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".msg", ".epub"}` — **explicitly not** `.mp3`, `.wav`, `.m4a`
- No `import markitdown` at module top — only lazy import inside Python API path

### 3. Watcher extension
- **Task ID**: build-watcher
- **Depends On**: build-converter
- **Validates**: `tests/unit/test_knowledge_watcher.py`
- **Assigned To**: watcher-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `CONVERTIBLE_EXTENSIONS` and `convert_to_sidecar` from `tools.knowledge.converter`
- In `_DebouncedHandler._is_relevant` at `bridge/knowledge_watcher.py:44`, add: if extension is in `CONVERTIBLE_EXTENSIONS`, route through converter; if in `SUPPORTED_EXTENSIONS`, existing behavior
- Wrap converter call in try/except; log WARNING on exception; never propagate
- Add a separate scheduling queue for conversions (converter runs before indexing); reuse existing debounce timer mechanism
- Preserve the existing module docstring contract: "A crash in the watcher thread must never take down the bridge"

### 4. `valor-ingest` CLI
- **Task ID**: build-cli
- **Depends On**: build-converter
- **Validates**: `tests/unit/test_valor_ingest_cli.py`
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true (with build-watcher)
- Create `tools/valor_ingest.py` mirroring the shape of `tools/valor_email.py` / `tools/valor_telegram.py`
- Argparse: positional `source`, optional `--vault-subdir PATH`, `--force`, `--output PATH`
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
- Create `tests/unit/test_knowledge_converter.py` — 10+ cases covering subprocess success, subprocess failure, Python API path, hash idempotency, sidecar naming, `.md` skip, frontmatter parsing, empty source, unicode paths, loop prevention
- Create `tests/unit/test_valor_ingest_cli.py` — argparse edge cases, URL handling, exit codes
- Create `tests/integration/test_markitdown_ingestion.py` — real end-to-end using fixture PDF: drop file → wait for watcher debounce → assert `KnowledgeDocument` row exists
- Fixture: `tests/fixtures/sample.pdf` (small, checked-in)
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

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: markitdown-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/markitdown-ingestion.md` with: overview, sidecar pattern, LLM config, audio exclusion rationale, troubleshooting
- Update `docs/features/README.md` index table
- Update `docs/features/subconscious-memory.md` to note binary-format coverage
- Update `CLAUDE.md` Quick Commands with `valor-ingest` usage

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Haiku vision via Anthropic OpenAI-compat:** Should we default `MARKITDOWN_LLM_MODEL` to Haiku via Anthropic's OpenAI-compat endpoint, or to OpenAI's `gpt-4o-mini`? Haiku matches the issue's stated preference but requires we prove Anthropic's compat layer handles markitdown's vision requests. If the build-time probe fails, we fall back to documenting OpenAI as recommended. Confirm the desired default ordering.

2. **`valor-ingest` URL destination:** When a URL is passed (e.g., YouTube), where should the sidecar land by default? Options: (a) require explicit `--vault-subdir`, (b) default to `~/work-vault/_ingested/` (new subdir), (c) default to CWD. I've defaulted to (a) in the plan; is that what you want?

3. **Backfill policy:** The plan explicitly defers backfilling existing binary files in the vault. If there are already PDFs/docx files sitting in the vault today that you'd want indexed immediately, we could either (a) add a one-shot `valor-ingest --scan ~/work-vault/` command to this plan, or (b) handle it manually after merge. Currently (b); let me know if you want (a) in scope.

4. **Sidecar visibility in `do-xref-audit`:** Sidecars named `report.pdf.md` will appear as "unreferenced" in future xref audits (since nothing links to them). Should the audit skip files with `generated_by: markitdown` in frontmatter, or should we require every sidecar's source be linked from at least one human-written doc? I'm leaning "skip in audit" — sidecars are derivative, not primary content.
