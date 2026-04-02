# Memory Hook Performance

Performance optimization for the PostToolUse memory recall hook, addressing import chain latency and noisy deja vu thought injection.

## Problem

The PostToolUse memory recall hook added 160-470ms latency per tool call due to two root causes:

1. **Import tax (344ms):** `recall()` in `memory_bridge.py` lazy-imported `extract_topic_keywords` from `agent/memory_hook.py`. Python's package loading triggered `agent/__init__.py`, which eagerly imported `claude_agent_sdk` (162ms), `mcp.types` (191ms), `telethon` (61ms), and `fastmcp` (65ms). The actual retrieval logic took ~5ms.

2. **Noisy keywords producing useless deja vu thoughts:** `extract_topic_keywords()` split file paths on `/` and `.`, producing generic segments like `users`, `valorengels`, `agent`. These common segments always hit the bloom filter but `retrieve_memories()` returned 0 results, triggering the deja vu fallback: "I have encountered something related to users, valorengels, agent before" -- pure noise on every 3rd tool call.

## Solution

### Import Chain Break

Keyword extraction utilities were extracted from `agent/memory_hook.py` to `utils/keyword_extraction.py`:

- `extract_topic_keywords()` -- extracts meaningful terms from tool inputs
- `_cluster_keywords()` -- groups keywords for multi-query retrieval
- `_apply_category_weights()` -- re-ranks results by category weight
- `_NOISE_WORDS` -- frozenset of filtered terms

The new module depends only on `re`, `os`, `typing`, and `config.memory_defaults` -- no `agent/`, `bridge/`, or `models/` imports. This eliminates the 344ms import chain entirely.

Backward compatibility is maintained via re-exports in `agent/memory_hook.py`, so agent-side callers (`memory_extraction.py`, `health_check.py`) continue working without changes.

### Project-Path Stopword Filtering

The `_NOISE_WORDS` frozenset was expanded with:

- **Directory names:** `users`, `valorengels`, `home`, `desktop`, `agent`, `bridge`, `models`, `tools`, `config`, `tests`, `hooks`, `claude`, `scripts`, `docs`, `data`, `logs`, `utils`, `monitoring`, `sessions`
- **Generic dev terms:** `init`, `main`, `index`, `setup`, `base`, `core`, `common`, `abstract`, `interface`, `module`, `package`
- **Tool names:** `grep`, `edit`, `glob` (some already present)

Additionally, `extract_topic_keywords()` now strips the project root prefix before splitting path segments, so only project-relative segments are considered. File stems (last path segment without extension) are preserved as compound terms (e.g., `agent_session_queue` stays intact rather than being split into `agent`, `session`, `queue`).

### Deja Vu Removal

The "vague recognition" deja vu fallback was removed from both code paths:

- `agent/memory_hook.py` `check_and_inject()`: bloom hits with no retrieval results now returns `None`
- `.claude/hooks/hook_utils/memory_bridge.py` `recall()`: same change

The "novel territory" signal (bloom_hits == 0 with many keywords) is preserved as it provides useful context.

## Key Files

| File | Change |
|------|--------|
| `utils/keyword_extraction.py` | New module -- extracted keyword utilities with no agent deps |
| `agent/memory_hook.py` | Re-exports from `utils.keyword_extraction`; deja vu fallback removed |
| `.claude/hooks/hook_utils/memory_bridge.py` | Imports from `utils.keyword_extraction`; deja vu fallback removed |
| `tests/unit/test_memory_hook.py` | Updated imports, new stopword tests, deja vu test removed |

## Related

- [Subconscious Memory](subconscious-memory.md) -- parent feature documentation
- [Claude Code Memory](claude-code-memory.md) -- hooks integration details
- PR #525: Initial hook implementation
- PR #604: BM25+RRF fusion retrieval
- Issue #627: Tracking issue for this optimization
