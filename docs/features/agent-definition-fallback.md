# Agent Definition Fallback

Graceful degradation when agent definition markdown files (`.claude/agents/*.md`) are missing, malformed, or unreadable.

## Problem

`_parse_agent_markdown()` in `agent/agent_definitions.py` is called for every agent at session-creation time. Without defensive handling, any of three failure modes — file absent, file present but malformed, or file present but unreadable — would propagate an exception back through `get_agent_definitions()` to `_create_options()` and kill the entire session.

These failures happen during deployment windows when code is updated but agent files have not yet synced, on new machines where `.claude/agents/` is incomplete, after a botched edit leaves a file without YAML frontmatter, or when the agents directory has bad permissions or non-UTF-8 bytes.

## Behavior

`_parse_agent_markdown()` handles three failure modes uniformly. Each one logs a warning and returns the same fallback dict (with `"_is_fallback": True`) so the session continues with a degraded prompt.

| Mode | Trigger | Detection |
|------|---------|-----------|
| **Missing file** | `path.exists() == False` | Fast-path check before the try block |
| **Malformed / no YAML frontmatter** | Regex match returns `None` -> explicit `raise ValueError(...)` | `except ValueError` |
| **Unreadable file (I/O)** | `read_text` raises `FileNotFoundError` (race), `PermissionError`, or other `OSError` subclass | `except OSError` |
| **Invalid UTF-8 bytes** | `read_text(encoding="utf-8")` raises `UnicodeDecodeError` (a `ValueError` subclass) | `except ValueError` |

**Warning log format** (one per fallback):

```
Agent definition <path> unusable (<ExceptionClass>: <message>) — using fallback prompt
```

(The missing-file branch keeps its older `"... not found ... — using fallback prompt"` format to preserve operator log-search habits and the existing test contract.)

Exceptions outside the `(OSError, ValueError)` tree (`KeyError`, `AttributeError`, `TypeError`, etc.) propagate unchanged — those indicate programmer error in this module, not an unusable input file, and should not be silently swallowed.

- **Session continues**: `get_agent_definitions()` returns a complete dict even when some or all agent files are unusable. The agent operates with degraded prompts rather than crashing.
- **Startup validation**: `validate_agent_files()` is called during process initialization to surface unusable files early via log warnings, giving operators a chance to fix the issue before users hit it. It performs a **trial-parse**: for each expected file, it checks existence first (missing files take the legacy existence-only branch), then for files that exist it calls `_parse_agent_markdown` and inspects the returned dict for `"_is_fallback": True`. The returned `list[str]` of "problematic" paths now includes missing AND malformed/unreadable files. Reasons go to the warning log only, not the return value. The check fires from two call sites:
  - `bridge/telegram_bridge.py::main()` — covers Telegram bridge processes.
  - `worker/__main__.py::main()` — covers the standalone worker, which is the actual session execution engine. A worker-only deployment (or one where the worker boots before the bridge) still gets the early-warning signal.

## Fallback Prompt

When an agent file is unusable, the fallback prompt is:

> Agent definition file {name}.md is not available ({reason}). Operate with your best judgment.

The agent's description is set to `"Fallback for unusable {name}.md: {reason}"`. The dict also carries `"_is_fallback": True` as a marker for `validate_agent_files()`. Downstream consumers (`get_agent_definitions` and `AgentDefinition` construction) read only `frontmatter` and `body`, so the extra key passes through harmlessly.

## Key Files

| File | Role |
|------|------|
| `agent/agent_definitions.py` | Fallback logic in `_parse_agent_markdown()`, helper `_fallback_definition()`, trial-parse in `validate_agent_files()` |
| `bridge/telegram_bridge.py` | Calls `validate_agent_files()` at startup |
| `worker/__main__.py` | Calls `validate_agent_files()` at startup (mirrors bridge — worker is the session execution engine) |
| `tests/unit/test_agent_definitions.py` | Unit tests covering normal load, all three failure modes, propagation of unrelated exceptions, and trial-parse behavior |
| `tests/unit/test_worker_startup_validation.py` | Static-import test asserting the worker module wires `validate_agent_files` into `main()` |

## Related

- Plan: `docs/plans/sdk_graceful_agent_fallback.md` (original missing-file fallback)
- Plan: `docs/plans/widen_agent_definition_fallback.md` (this expansion to malformed + OS errors)
- Issues: [#539](https://github.com/tomcounsell/ai/issues/539) (original), [#1350](https://github.com/tomcounsell/ai/issues/1350) (this expansion)
