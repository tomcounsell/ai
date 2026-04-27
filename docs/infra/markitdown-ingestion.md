# Markitdown Ingestion — Infrastructure

## Current State

- **Knowledge pipeline** runs inside the bridge process. `bridge/knowledge_watcher.py` uses `watchdog` to monitor `~/work-vault/`; `tools/knowledge/indexer.py` upserts `KnowledgeDocument` records (Popoto/Redis) with OpenAI embeddings.
- **Supported formats:** only `.md`, `.txt`, `.markdown`, `.text` (hardcoded at `bridge/knowledge_watcher.py:21` and `tools/knowledge/indexer.py:28`).
- **Embedding provider:** OpenAI `text-embedding-3-small` (existing dependency).
- **Summarization LLM:** Anthropic Haiku via the canonical `HAIKU` constant in `config.models` (used by `tools/knowledge/indexer.py:20`).
- **pypdf** is installed at `>=6.10.2` but only used in `bridge/media.py` for Telegram attachment handling — not wired into the indexer.

## New Requirements

### New dependency (optional extra)

Add to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
knowledge = ["markitdown[pdf,docx,pptx,xlsx,outlook,youtube-transcription]>=0.1.0"]
```

**Explicitly excluded extras:**
- `[audio-transcription]` — markitdown uploads audio to Google Web Speech API with an unauthenticated shared key (personal/testing only, 50 req/day). Unacceptable for confidential consulting material.
- `[az-doc-intel]` — paid Azure service, not needed.
- `[all]` — avoided because it pulls `[audio-transcription]` transitively.

### Installation on existing machines

`scripts/remote-update.sh` runs `uv sync` to install the project. After sync, it must also run:

```bash
uv pip install -e '.[knowledge]'
```

This installs `markitdown` plus the enumerated extras.

### New CLI entry point

New console script `valor-ingest` registered in `[project.scripts]`:

```toml
valor-ingest = "tools.valor_ingest:main"
```

### New env var

`MARKITDOWN_LLM_MODEL` (optional, default unset):
- Unset → converter uses subprocess CLI path only (no LLM calls)
- Set to a Haiku model ID (e.g., `claude-haiku-4-5-20251001`) → converter uses Python API path with an OpenAI-compatible client pointed at `https://api.anthropic.com/v1/` using the existing `ANTHROPIC_API_KEY`
- Set to an OpenAI model ID (e.g., `gpt-4o-mini`) → converter uses Python API with `OPENAI_API_KEY` (already required by the embedding pipeline)

Add commented placeholder to `.env.example`.

### Resource estimates

- **Disk:** markitdown and its dependencies add ~80–150 MB to the venv (pdfminer.six, python-docx, python-pptx, openpyxl, olefile, etc.). No model downloads since we exclude audio-transcription.
- **Memory:** Subprocess path uses a forked process — bounded by markitdown's per-file footprint (typically <200 MB for documents up to ~50 MB).
- **CPU:** Conversion is CPU-bound and single-threaded per file. The existing watcher debounce (2s) already serializes conversion requests.
- **LLM calls:** Only when `MARKITDOWN_LLM_MODEL` is set AND the file extension is in `LLM_BENEFICIAL_EXTENSIONS` (`{.pptx, .png, .jpg, .jpeg, .gif, .webp}`). Haiku at $1/Mtok is negligible for image descriptions.
- **Network (YouTube):** `[youtube-transcription]` fetches published captions from YouTube — no audio upload, just caption retrieval. No quota concerns under normal use.

## Rules & Constraints

### Runtime isolation
- `markitdown` MUST NOT be imported at worker or bridge startup. Lazy `import markitdown` is allowed only inside converter code that is itself only invoked on demand.
- Subprocess invocation (`subprocess.run(["markitdown", ...])`) is the preferred path — it isolates markitdown's heavy deps from the main process.

### LLM selection constraints
- Default LLM (when `MARKITDOWN_LLM_MODEL` is unset): **none** — subprocess path only.
- Recommended when set: **Haiku** via Anthropic's OpenAI-compat endpoint (cheapest + already authenticated).
- Never silently default to Opus or Sonnet. If a future maintainer wants Opus, it must be set explicitly and will be logged at INFO on every conversion.
- LLM path is only taken for formats where it measurably improves output (PPTX with images, standalone images). For PDF/DOCX/XLSX/HTML, the subprocess path is used regardless of LLM config because the LLM adds no value for pure text extraction.

### Privacy constraints
- Audio files (`.mp3`, `.wav`, `.m4a`, etc.) MUST NOT be routed through markitdown. The watcher's `CONVERTIBLE_EXTENSIONS` set must explicitly exclude them, and `[audio-transcription]` extra must not be installed.
- The `grep -r "recognize_google\|audio-transcription" tools/ bridge/ pyproject.toml` check is part of the success criteria — enforces the exclusion.

### Sidecar conventions
- Generated sidecars use `{original_filename}.md` naming — e.g., `report.pdf` → `report.pdf.md`.
- Sidecars carry YAML frontmatter identifying them as generated (`generated_by: markitdown`), enabling downstream filtering by tools like `do-xref-audit`.
- Atomic writes via `os.replace()` on POSIX.

### Rate limits / API quotas
- OpenAI embeddings (pre-existing): `text-embedding-3-small` at existing limits — no change.
- Anthropic Haiku (new, optional): standard per-account rate limits — monitor `logs/bridge.log` for 429s.
- YouTube captions: no documented quota, but fetches are infrequent (explicit user action).

## Rollback Plan

If markitdown integration is rolled back:

1. **Remove optional dependency** — delete the `knowledge` entry from `[project.optional-dependencies]` in `pyproject.toml`.
2. **Remove CLI registration** — delete `valor-ingest` from `[project.scripts]`.
3. **Revert watcher extension** — restore `bridge/knowledge_watcher.py` to the state before `CONVERTIBLE_EXTENSIONS` routing was added.
4. **Delete new modules** — `tools/knowledge/converter.py`, `tools/valor_ingest.py`.
5. **Delete `[project.scripts]` entry** and run `uv sync` on each machine.
6. **Leave generated sidecars in place** — they are valid standalone markdown files and will continue to be indexed by the existing pipeline. Users can delete them manually if desired by running `find ~/work-vault -name "*.pdf.md" -o -name "*.docx.md"` and inspecting.
7. **Remove `MARKITDOWN_LLM_MODEL`** from `.env` on each machine (no functional impact if left — unused env vars are ignored).

No database migrations required — the `KnowledgeDocument` schema is unchanged. Rollback is clean and reversible in under 10 minutes per machine.
