# Ollama Internal Transport Client

`tools/ollama_client.py` is the sole owner of Ollama HTTP transport and config
resolution in this codebase. All call sites delegate to this module; transport
logic does not exist anywhere else.

## Why this exists

Three modules previously hand-rolled identical Ollama HTTP transport:
`tools/memory_search/title_generator.py`, `tools/knowledge/indexer.py`, and
`tools/email_cs/triage.py`. Two used `urllib` POST to `/api/generate`; one used
the `ollama` package's module-level `chat()`. Config resolution (host, model,
timeout) was duplicated across all three.

This module consolidates transport into one place so a change to connection
behavior (retry, headers, base-URL scheme) needs to happen once.

## Public API

### `resolve_config() -> tuple[str, str, float]`

Returns `(base_url, model, timeout_s)` from `settings.models` when available,
falling back to constructing `ModelSettings()` directly (Pydantic applies field
defaults). Config literals (host URL, generation model name, timeout value) live
in `config/settings.py` ModelSettings field definitions **only** — this module
contains no such literals.

### `generate(prompt, *, model, timeout_s, base_url=None, caller=None) -> str | None`

Calls the Ollama generate endpoint via `ollama.Client`. Returns the stripped
response string, or `None` on any failure (connection error, timeout, bad
response) and on empty/whitespace model output.

**Fail-silent.** Never raises. Returns `None` on any error, including when the
model returns an empty string — so callers' None-on-failure fallback chains fire
correctly (e.g. the knowledge indexer's Haiku fallback triggers when Ollama
returns `""`).

Constructs `ollama.Client` inside a `with` block for deterministic socket close
(httpx has no `__del__`; the `with` block closes the connection pool eagerly).

Logs the exception class name at DEBUG on failure:
`[{caller}] generate failed: {ExceptionClassName}`.

### `chat(messages, *, model, options=None, base_url=None, timeout_s=None) -> str`

Calls the Ollama chat endpoint via `ollama.Client`. Returns the assistant message
content string.

**Raises on failure.** Does not return `None`; propagates the exception so
callers' `try/except` escalation paths fire. This preserves the triage module's
`escalate_triage()` behavior, which depends on exception propagation.

When `timeout_s` is `None` (default), no timeout is passed to the `Client`,
preserving the infinite-wait behavior that was already in place when the module-
level `ollama.chat()` was used directly.

## Error contract split

The two functions intentionally differ:

| Function | On failure | Why |
|----------|-----------|-----|
| `generate()` | Returns `None` | Title-gen and indexer want silent fallback to a backup path |
| `chat()` | Raises | Triage wants exception-to-escalate — its `except Exception` block calls `escalate_triage()` |

## Callers

Each caller keeps a thin adapter function with its original name, delegating
to this module. This preserves existing test mock targets (e.g.
`patch("tools.knowledge.indexer._summarize_via_ollama")`) without re-pointing
~10 patch sites.

| Caller | Adapter | Transport call |
|--------|---------|---------------|
| `tools/memory_search/title_generator.py` | `_resolve_ollama_config()`, `_post_ollama_generate()` | `resolve_config()`, `generate()` |
| `tools/knowledge/indexer.py` | `_summarize_via_ollama()` | `resolve_config()` + `generate()` with `timeout_s=8.0` |
| `tools/email_cs/triage.py` | direct call in `triage_local()` | `chat()` |

## Config literal ownership rule

`localhost:11434`, the generation model name, and the default timeout value live
in `config/settings.py` ModelSettings field defaults **only**. They do not appear
in this module or in any of the caller files. This ensures a config change is made
in exactly one place.

Verify: `grep -rln "localhost:11434\|gemma4" tools/` should return nothing.

## Tests

`tests/unit/test_ollama_client.py` covers all three public functions, including:
- Empty-string coalescing to `None` in `generate()` (prevents silent indexer regression)
- Content extraction via `response.message.content` in `chat()`
- Context-manager invocation (deterministic socket close)
- Exception propagation in `chat()`
- Fail-silent behavior in `generate()`
