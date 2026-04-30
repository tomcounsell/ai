# Markitdown Ingestion

Multi-format document ingestion for the knowledge pipeline. Converts binary sources (PDF, DOCX, PPTX, XLSX, HTML, images, etc.) into `.md` sidecars that the existing indexer consumes unchanged. The source file remains canonical; the sidecar is a regenerable search proxy.

## Quick Reference

| Action | Command |
|--------|---------|
| Convert one file | `valor-ingest ~/Downloads/report.pdf --vault-subdir Consulting/leads/acme/` |
| Backfill an existing directory | `valor-ingest --scan ~/work-vault/` |
| Convert a YouTube URL | `valor-ingest https://youtu.be/VIDEO_ID --vault-subdir Research/` |
| Regenerate sidecars even on hash match | `valor-ingest <path> --force` |
| Enable LLM image descriptions | `export MARKITDOWN_LLM_MODEL=<HAIKU from config.models>` |

## Architecture

```
~/work-vault/**.{pdf,docx,pptx,xlsx,html,png,jpg,...}
        │
        ▼
bridge/knowledge_watcher.py  (2s debounce → _flush)
        │
        ▼
tools/knowledge/converter.py::convert_to_sidecar()
        │  (subprocess `markitdown` by default;
        │   Python API + HAIKU vision when MARKITDOWN_LLM_MODEL is set)
        ▼
{source}.md sidecar with YAML frontmatter
        │
        ▼
tools/knowledge/indexer.py::index_file()  → KnowledgeDocument + DocumentChunks
        │
        ▼
Memory recall, semantic search, xref audit
```

Two code paths in the converter:

1. **Subprocess path (default).** Spawns the `markitdown` CLI with `capture_output=True, text=True, timeout=120, check=False`. Handles PDF/DOCX/XLSX/HTML/EPUB/MSG with a text layer. No LLM required. `ANTHROPIC_API_KEY` is not needed.
2. **Python API path (opt-in).** Triggered only when `MARKITDOWN_LLM_MODEL` is set AND the file extension is in `LLM_BENEFICIAL_EXTENSIONS` (`.pptx` and standalone images). Lazy-imports `markitdown` + `openai`, builds an OpenAI-compat client against `https://api.anthropic.com/v1/`, and sends the image through Haiku vision.

The Python API path has a probe-and-cache mechanism (`_llm_path_available`): the first call constructs the client and does a 1-token ping. On failure we log ONCE at WARNING and route every subsequent image/PPTX conversion through the subprocess path for the rest of the process's lifetime. There is **no** gpt-4o-mini fallback — the `OPENAI_API_KEY` surface was deliberately eliminated.

## Sidecar Pattern

A converted file lives next to its source with a double-extension name. Example:

```
Consulting/leads/acme/
├── proposal.pdf
└── proposal.pdf.md
```

Sidecars carry YAML frontmatter so tooling can reason about them without reading the body:

```yaml
---
source_hash: <sha256 of the source bytes>
source_path: proposal.pdf
generated_by: markitdown
generated_at: 2026-04-24T12:00:00Z        # first-generation timestamp (preserved)
regenerated_at: 2026-04-26T09:30:00Z      # updated on every hash-mismatch regen
llm_model: none                            # or the resolved value of config.models.HAIKU
---
```

Downstream tooling can:

- Use `generated_by: markitdown` as a skip-signal when auditing the vault's authoritative content (see [`do-xref-audit` SKILL.md](../../.claude/skills/do-xref-audit/SKILL.md)).
- Use `regenerated_at` to detect content changes without re-reading the entire body.
- Use `source_hash` to verify the sidecar is still in sync with its source.

## LLM Configuration

| `MARKITDOWN_LLM_MODEL` | Image files | PPTX with embedded images | PDF / DOCX / XLSX / HTML |
|------------------------|-------------|---------------------------|--------------------------|
| unset (default)        | filename-only markdown | no image OCR | text extraction (subprocess) |
| set to `HAIKU` value   | vision-generated description | image OCR via Haiku | text extraction (subprocess) |
| set, probe fails       | subprocess fallback, WARNING logged once | subprocess fallback | text extraction (subprocess) |

The HAIKU model ID is resolved from `config.models.HAIKU` at convert time. Never hardcode the Anthropic model string — when the model rotates (PR #615 precedent), the single-source-of-truth constant propagates automatically.

## Audio Exclusion

Audio formats (`.mp3`, `.wav`, `.m4a`, etc.) are **deliberately out of scope** for this feature. Markitdown's audio converter uses `SpeechRecognition.recognize_google()` with an unauthenticated shared key intended for "personal or testing purposes only" (50 requests/day). Uploading consulting material to an unauthenticated Google Web Speech endpoint — no enterprise DPA, no key ownership, no privacy guarantees — is unacceptable.

If audio transcription is needed, it will be a separate feature using local Whisper (`openai-whisper` or `faster-whisper`) with its own install footprint and test surface.

## Installation

The feature ships as an optional extra:

```bash
uv sync --all-extras   # Installs markitdown + knowledge extras for the current venv
```

`pyproject.toml` declares `knowledge = ["markitdown[pdf,docx,pptx,xlsx,outlook]>=0.1.0", "onnxruntime>=1.25.0"]`. The `youtube-transcription` extra is deliberately not installed — `valor-ingest <youtube-url>` delegates to the existing `youtube-transcript-api` path.

### First-Install Backfill Reminder

When the `[knowledge]` extra is installed for the first time on a machine, `scripts/update/run.py` appends a one-line tip to the Telegram update summary:

> Tip: run `valor-ingest --scan ~/work-vault/` to backfill existing binary files into sidecars.

The reminder is gated by `~/.cache/valor/markitdown-backfill-reminded` so subsequent updates don't re-nag. This covers both cron-driven updates (via `remote-update.sh` → `scripts/update/run.py --cron`) and human-invoked `/update` runs.

## Watcher Integration

`bridge/knowledge_watcher.py` imports `CONVERTIBLE_EXTENSIONS` from the converter — the two sets cannot drift. Convertible extensions are routed through the converter inside the **same `_flush()` iteration** that handles indexing; the sidecar does not re-enter `_schedule` and does not reset the 2-second debounce under rapid drops. The converter's own `.md` short-circuit is a belt-and-suspenders guard for the watchdog event that fires when the sidecar appears.

Crash isolation is preserved — the converter call is wrapped in try/except and its exceptions are logged at WARNING without ever propagating out of the watcher thread.

### Vault writers

The vault is fed from several sources. Each writer drops files under `~/work-vault/`; the watcher coalesces them in its 2-second debounce window and runs the converter:

- **Manual ingest CLI** — `valor-ingest <source>` (one-off) or `valor-ingest --scan <dir>` (backfill). Primary entry point for explicit user-driven imports.
- **Telegram steering attachments** (issue #1215) — when a file lands in a chat with a live session, `bridge/telegram_bridge.py:_ack_steering_routed` schedules a fire-and-forget `_ingest_attachments` task that copies the downloaded file into `~/work-vault/telegram-attachments/` with the disambiguated name `{YYYYMMDD_HHMMSS}_{sender}_{message_id}_{basename}`. The copy runs **after** the steering push so a slow filesystem write never blocks delivery. See [Telegram Integration → Inbound attachments](telegram.md#inbound-attachments--steering-enrichment--auto-ingest).
- **Telegram new-session deferred enrichment** — `bridge/enrichment.py:enrich_message` already downloads media for new sessions. The steering-side helper above closes the gap so live-session attachments get the same vault treatment.

## Loop Prevention

The converter refuses to re-run on any `.md` input — the first line of `convert_to_sidecar()` checks `ext == ".md"` and short-circuits to `None`. This protects against:

- The watchdog firing `on_created` for a newly-written sidecar.
- A `weird.pdf.md.md` file landing in the vault.
- The indexer passing a sidecar path back through the watcher.

## CLI Reference

```
valor-ingest [-h] [--vault-subdir PATH] [--force] [--output PATH] [--verbose]
             (--scan DIR | source)
```

- `source` and `--scan` are mutually exclusive (exactly one required).
- `--scan` recurses into subdirectories and skips hidden dirs + `_archive_` dirs.
- `--scan` cannot be combined with `--vault-subdir` or `--output` (in-place operation).
- Exit codes: `0` success, `1` conversion failure, `2` argparse error.

URL inputs:

- YouTube URLs (`youtube.com`, `youtu.be`) are handled by `youtube-transcript-api` — markitdown is bypassed.
- Generic URLs are downloaded to `--vault-subdir` (or CWD with a reminder to set `--vault-subdir`) and then passed through the converter.

## Image Size Guard

Image extensions (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`) over 20 MB are skipped with a WARNING — phone photos easily exceed 10 MB and the vision API does not need raw-resolution input. The 20 MB cap only applies to images; PDFs and other documents can be larger without triggering the guard.

## Testing

| File | Scope |
|------|-------|
| `tests/unit/test_knowledge_converter.py` | Subprocess success/failure, hash idempotency, 20 MB guard, probe cache, unicode paths |
| `tests/unit/test_valor_ingest_cli.py` | argparse edge cases, --scan recursion, URL dispatch |
| `tests/unit/test_knowledge_watcher.py` | `_is_relevant` updates, `_flush` routing, crash-isolation |
| `tests/integration/test_markitdown_ingestion.py` | End-to-end watcher + converter + stubbed indexer |
| `tests/integration/test_markitdown_haiku_vision.py` | Live Haiku vision probe (skipped without `ANTHROPIC_API_KEY`) |

The Haiku vision test is a **hard gate** when the key is present — a failure means `MARKITDOWN_LLM_MODEL=HAIKU` is broken. Production falls back to subprocess on probe failure; CI must not.

## Deliberate Exclusions

- **Audio transcription** — privacy disqualifier (see above). Future feature with local Whisper.
- **markitdown-ocr plugin** — separate install, requires `--use-plugins`. Revisit only if vault content demonstrably needs OCR.
- **`markitdown[youtube-transcription]`** — redundant with our existing `youtube-transcript-api` dep.
- **Azure Document Intelligence (`[az-doc-intel]`)** — paid service, not needed.
- **Indexing binary originals directly** — sidecars are the searchable proxy.

## See Also

- [Plan document](../plans/markitdown-ingestion.md) — design decisions, spike results, risk analysis
- [`do-xref-audit` SKILL.md](../../.claude/skills/do-xref-audit/SKILL.md) — consumes the `generated_by` sidecar skip-signal
- [`update` SKILL.md](../../.claude/skills/update/SKILL.md) — mirrors the Telegram summary backfill reminder for human invocations
- [Subconscious Memory](subconscious-memory.md) — recall pipeline now covers converted binary formats
