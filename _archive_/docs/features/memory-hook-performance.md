# Memory Hook Performance

Performance work on the memory hooks, covering two distinct problems: PostToolUse recall latency (import chain + noisy deja vu injection, below) and the Stop-hook extraction timeout (see [Stop-Hook Timeout: Detaching Extraction Off the 10s Wall](#stop-hook-timeout-detaching-extraction-off-the-10s-wall) further down).

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

## Stop-Hook Timeout: Detaching Extraction Off the 10s Wall

A second, later performance problem hit the **Stop** hook rather than PostToolUse recall: the Haiku extraction + outcome-detection + post-merge learning work that used to run inline in `stop.py` raced the harness's 10-second Stop-hook timeout wall.

### Measured Root Cause

Over the 50 most-recently-modified session transcripts at the time this was investigated (2026-07-23 → 2026-07-28), `stop.py` timed out **126 of 131 runs**, with the median duration landing almost exactly on the 10,000ms wall. `user_prompt_submit.py`, `pre_tool_use.py`, and four validators showed the same pattern less severely. The extraction work routinely never finished; the harness SIGKILLed the process before it could return.

Two structural facts made this un-patchable in place:

1. **Synchronous network I/O on the critical path.** Up to three Haiku round-trips plus two 10s-budgeted `gh` calls all had to fit inside a single 10-second wall, with zero backgrounding anywhere in the call chain.
2. **A SIGKILL runs no `finally`, no `except`.** The killed work was lost with no log line — but the root cause was SIGKILL truncation, not a swallowing handler. `memory_bridge.py`'s own extraction failure path (`except Exception as e: logger.warning(...)`) was already a real log handler; it simply never got the chance to fire because the process was dead first. (Two separate genuine bare `except Exception: pass` swallows *did* exist at `stop.py:225,242,262` around the three inline extraction calls, and those were fixed too, but they were not the reason drops went unlogged — the timing was.)

### The Fix: Detach, Don't Bound

An inline SIGALRM-style deadline was considered and rejected: capping the inline call would only guarantee loss on every slow turn (cap → drop → log-drop, every time) rather than fixing anything. Extraction produces no output the harness/session consumes (unlike `user_prompt_submit`'s prefetch, which must return inline as `additionalContext`), so there is no correctness reason it needs to run on the critical path at all.

`stop.py` now persists the session transcript synchronously (as before), then spawns `.claude/hooks/hook_utils/stop_detach_worker.py` as a **real detached subprocess** (`Popen(..., start_new_session=True)`, redirected/closed streams — never a daemon thread, since `stop.py` exits within milliseconds and would kill an in-process thread before the Haiku call returned) and exits 0 immediately. The 10s wall no longer applies to the extraction work at all; it runs to completion off the critical path.

Two safety mechanisms prevent the detached model from trading one failure mode for another:

- **Self-deadline** (`HOOK_DETACH_DEADLINE_SECONDS`, default `120`, in `.claude/hooks/hook_utils/detach_lock.py`): the worker sets its own `SIGALRM` and raises a `DetachDeadlineExceeded` exception that subclasses `BaseException` (not `Exception`) so `memory_bridge`'s broad `except Exception` handlers cannot swallow it. On deadline the worker logs `deadline-exceeded` to `~/.claude/logs/hooks.log` and exits — it self-terminates rather than lingering forever.
- **Concurrency cap** (`HOOK_DETACH_MAX_INFLIGHT`, default `3`): an SDLC batch that ends many turns in a short window could otherwise fan out an unbounded number of Haiku/`gh` workers. `stop.py` reserves one of a fixed set of numbered lock-slot files under an absolute, cwd-independent state dir (`~/.claude/hooks-state/stop-detach/`) before spawning; over-cap invocations log `detach-skipped: at capacity` and skip spawning rather than piling on.

Both the log path and the lock-slot state dir are deliberately **absolute** (under `~/.claude/`, not repo-relative `logs/`), because the same worker also runs inside foreign repos under the user-scope SDLC hooks, where a repo-relative directory would not exist — a silently-failing log write there would have re-swallowed the very drops this fix was meant to surface.

### Before / After

The 126/131 timeout figure above is the measured **before** state. There is no equivalent measured **after** figure yet — this fix has not had a production sample window to accumulate a comparable timeout-rate measurement post-deploy. What can be said with certainty is the mechanism-level claim: the 10-second Stop-hook wall no longer bounds the Haiku/`gh` round-trips at all (extraction runs in a separate process with its own 120s self-deadline), so the specific failure mode that produced the 126/131 figure — the harness SIGKILLing `stop.py` mid-extraction — is structurally eliminated. Confirming the resulting timeout rate empirically is follow-up observability work, not something this document should claim without a real post-deploy sample.

### Key Files

| File | Role |
|------|------|
| `.claude/hooks/stop.py` | Persists transcript, spawns the detached worker, exits 0 immediately |
| `.claude/hooks/hook_utils/stop_detach_worker.py` | Runs extraction/TUI-capture/post-merge learning under its own `SIGALRM` self-deadline |
| `.claude/hooks/hook_utils/detach_lock.py` | Absolute log path, absolute state dir, env-overridable deadline/cap readers, atomic slot reservation |

## Related

- [Subconscious Memory](subconscious-memory.md) -- parent feature documentation
- [Claude Code Memory](claude-code-memory.md) -- hooks integration details, extraction pipeline
- [Hook Manifest](hook-manifest.md) -- manifest declaration for `stop.py` and the `HOOK_DETACH_*` knobs
- PR #525: Initial hook implementation
- PR #604: BM25+RRF fusion retrieval
- Issue #627: Tracking issue for the PostToolUse recall-latency optimization
- `docs/plans/hook-registration-manifest-dispatcher.md`: plan for the Stop-hook detach fix (spike-3)
