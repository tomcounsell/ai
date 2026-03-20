---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/yudame/cuttlefish/issues/97
last_comment_id:
---

# Add Kreuzberg for Document Parsing in Deep Research

## Problem

Research artifacts come in many formats — PDFs, DOCX files, images with text. The current pipeline handles markdown and plain text well but cannot extract text from binary file formats.

**Current behavior:**
- `add_manual_research()` only accepts pre-extracted text content (user must paste text)
- `create_artifacts()` in backfill stores PDFs as URL-only artifacts with empty `content` field — no text extraction
- The workflow UI (`PasteResearchView`) only accepts pasted text, not file uploads
- Binary research files (PDF papers, DOCX reports) cannot contribute their text to the analysis pipeline

**Desired outcome:**
- A utility function that accepts a file path and returns extracted text from PDF, DOCX, ODT, and images (OCR)
- `add_manual_research()` extended to accept file paths for non-text formats
- Backfill command extracts text from `.pdf` artifacts instead of storing them as empty-content URL-only records
- Research pipeline can ingest binary document formats seamlessly

## Prior Art

- **PR #184, #160, #115, #110, #88, #24, #13**: pypdf dependency bumps — pypdf is already in the project for basic PDF handling
- **Issue #61**: File Storage Service — established the abstract interface for binary file storage, relevant for how files are accessed
- **Issue #169 / PR #170**: MiroFish swarm intelligence integration — shows the pattern for adding new research sources to the pipeline

No prior work on kreuzberg or general document parsing (beyond pypdf for basic PDF reads).

## Data Flow

1. **Entry point**: File path (local or downloaded) containing a PDF, DOCX, ODT, or image
2. **Document parser utility** (`apps/common/utilities/document_parser.py`): Accepts file path, detects format via extension, calls kreuzberg's async extraction API, returns plain text string
3. **Research service** (`apps/podcast/services/research.py`): `add_manual_research()` or new `add_file_research()` calls the parser, stores extracted text in `EpisodeArtifact.content`
4. **Backfill command** (`_episode_import_utils.py`): `create_artifacts()` calls parser for `.pdf` files instead of creating empty-content URL-only artifacts
5. **Output**: `EpisodeArtifact` record with extracted text in `content` field, available to downstream analysis (cross-validation, synthesis, etc.)

## Architectural Impact

- **New dependencies**: `kreuzberg` (pure Python, async, minimal deps). Optional system dep: Tesseract for OCR (images only)
- **Interface changes**: New `parse_document()` utility function. `add_manual_research()` signature unchanged but a new `add_file_research()` function added alongside it
- **Coupling**: Low — document parser is a standalone utility used by research services
- **Data ownership**: No change — EpisodeArtifact still owns the content
- **Reversibility**: Easy — remove kreuzberg dep and parser utility, revert to URL-only PDFs

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — kreuzberg is a pip-installable pure Python library. Tesseract is optional (only needed for image OCR) and can be added later.

## Solution

### Key Elements

- **Document parser utility**: Standalone async function that accepts a file path and returns extracted text
- **File research service function**: New `add_file_research()` that parses files before storing as artifacts
- **Backfill enhancement**: PDF text extraction during episode import instead of URL-only artifacts

### Flow

**File path** → `parse_document(path)` → **extracted text** → `add_file_research(episode_id, title, path)` → **EpisodeArtifact with content**

### Technical Approach

- Use kreuzberg's async API (`kreuzberg.extract_file()`) for all format detection and extraction
- Create `apps/common/utilities/document_parser.py` with a single `parse_document(path: Path) -> str` function
- Add `add_file_research()` to `apps/podcast/services/research.py` that calls the parser and delegates to existing `add_manual_research()` for storage
- Update `create_artifacts()` in `_episode_import_utils.py` to extract PDF text via the parser instead of creating empty-content artifacts
- Graceful fallback: if kreuzberg fails on a file, log warning and fall back to URL-only artifact (current behavior)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `parse_document()` must handle kreuzberg extraction failures (corrupt files, unsupported formats) — test that it raises or returns empty string with logging
- [ ] `add_file_research()` must handle parse failures gracefully — test that a corrupt PDF still creates an artifact (with empty content + metadata noting the failure)

### Empty/Invalid Input Handling
- [ ] `parse_document()` with non-existent file path → raises FileNotFoundError
- [ ] `parse_document()` with empty file → returns empty string
- [ ] `parse_document()` with unsupported extension → raises ValueError or returns empty string with warning

### Error State Rendering
- [ ] Not applicable — this is a backend service with no direct user-visible output

## Test Impact

No existing tests affected — this is a greenfield feature adding new utility functions and a new service function. The existing `add_manual_research()` function and `create_artifacts()` function signatures are unchanged. The backfill enhancement adds a new code path for PDFs but doesn't modify the existing text artifact path.

## Rabbit Holes

- **Full OCR pipeline**: Tesseract setup, image preprocessing, multi-language OCR — defer to a separate issue. Kreuzberg supports it but we don't need to test/document image OCR in this scope
- **File upload UI**: Adding file upload to the workflow `PasteResearchView` — separate feature, not in scope
- **Replacing pypdf with kreuzberg**: pypdf is already used elsewhere; don't try to consolidate PDF handling libraries in this PR
- **Streaming/chunking large documents**: If a PDF is 500+ pages, we could chunk it. Not worth it now — store full extracted text

## Risks

### Risk 1: Kreuzberg extraction quality varies by format
**Impact:** Some PDFs (scanned, image-heavy) may produce poor text extraction without Tesseract
**Mitigation:** Log extraction quality metadata (char count, detected format). Accept that text-based PDFs work well; image-heavy PDFs degrade gracefully to empty content with a warning

### Risk 2: kreuzberg dependency size or compatibility
**Impact:** Could add unexpected transitive dependencies or conflict with existing deps
**Mitigation:** Check `uv add kreuzberg` output carefully. Kreuzberg is designed to be lightweight with minimal deps

## Race Conditions

No race conditions identified — document parsing is synchronous-style work (async but single-operation). No shared mutable state involved.

## No-Gos (Out of Scope)

- File upload UI in the workflow view
- Image OCR with Tesseract installation
- Replacing pypdf with kreuzberg for existing PDF handling
- Streaming or chunking for very large documents
- URL-based document fetching (download + parse from URL)

## Update System

No update system changes required — this is a cuttlefish-internal feature. The `uv add kreuzberg` dependency will be picked up naturally by `uv sync` during deployment.

## Agent Integration

No agent integration required — this is a podcast pipeline internal feature. The document parser is called by research services and management commands, not exposed via MCP or Telegram bridge.

## Documentation

- [ ] Update `docs/features/podcast-services.md` to document the new `parse_document()` utility and `add_file_research()` service function
- [ ] Add docstrings to all new public functions
- [ ] Add inline comments explaining kreuzberg format detection

## Success Criteria

- [ ] kreuzberg installed via `uv add kreuzberg`
- [ ] `parse_document(path)` utility function extracts text from PDF, DOCX, and ODT files
- [ ] `add_file_research(episode_id, title, file_path)` service function parses file and stores as artifact
- [ ] Backfill `create_artifacts()` extracts text from PDF files instead of empty-content URL-only artifacts
- [ ] PDF parsing tested with a sample document
- [ ] Graceful error handling for corrupt/unsupported files
- [ ] Tests pass
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (parser)**
  - Name: parser-builder
  - Role: Implement document parser utility, service function, and backfill enhancement
  - Agent Type: builder
  - Resume: true

- **Validator (parser)**
  - Name: parser-validator
  - Role: Verify parsing works, tests pass, error handling is solid
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update podcast-services docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add kreuzberg dependency and create parser utility
- **Task ID**: build-parser
- **Depends On**: none
- **Validates**: apps/podcast/tests/test_document_parser.py (create)
- **Assigned To**: parser-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `uv add kreuzberg` to install the dependency
- Create `apps/common/utilities/document_parser.py` with async `parse_document(path: Path) -> str`
- Handle PDF, DOCX, ODT formats via kreuzberg's `extract_file()`
- Add error handling for missing files, corrupt files, unsupported formats
- Create tests in `apps/podcast/tests/test_document_parser.py` with a small sample PDF

### 2. Add file research service function
- **Task ID**: build-service
- **Depends On**: build-parser
- **Validates**: apps/podcast/tests/test_document_parser.py
- **Assigned To**: parser-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `add_file_research(episode_id, title, file_path)` to `apps/podcast/services/research.py`
- Function calls `parse_document()` then delegates to `add_manual_research()`
- Handle parse failures gracefully (create artifact with metadata noting failure)

### 3. Enhance backfill PDF handling
- **Task ID**: build-backfill
- **Depends On**: build-parser
- **Validates**: apps/podcast/tests/test_commands.py
- **Assigned To**: parser-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `create_artifacts()` in `_episode_import_utils.py` to call `parse_document()` for `.pdf` files
- Store extracted text in `content` field alongside the URL
- Fall back to URL-only if extraction fails

### 4. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-service, build-backfill
- **Assigned To**: parser-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify `parse_document()` handles PDF, DOCX correctly
- Verify error handling for corrupt files
- Check that backfill PDF path extracts text

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/podcast-services.md` with document parsing section
- Verify all new functions have docstrings

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: parser-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `cd /Users/valorengels/src/cuttlefish && uv run pytest apps/podcast/tests/ -x -q` | exit code 0 |
| Lint clean | `cd /Users/valorengels/src/cuttlefish && uv run python -m ruff check .` | exit code 0 |
| Format clean | `cd /Users/valorengels/src/cuttlefish && uv run python -m ruff format --check .` | exit code 0 |
| Kreuzberg installed | `cd /Users/valorengels/src/cuttlefish && uv run python -c "import kreuzberg; print('ok')"` | output contains ok |
| Parser exists | `test -f apps/common/utilities/document_parser.py` | exit code 0 |

---

## Open Questions

1. Should `parse_document()` be sync or async? Kreuzberg provides async APIs, but `create_artifacts()` in the backfill command is synchronous. I'm leaning toward providing both `parse_document()` (sync wrapper) and `parse_document_async()` for use in the async research pipeline. Does that sound right, or should we just use sync everywhere?

2. For the backfill enhancement — should we re-run the backfill on existing episodes to populate PDF content that was previously stored as URL-only? Or is this only for future imports?
