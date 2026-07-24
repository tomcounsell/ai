# Adding Reflection Tasks

A developer guide for adding a new reflection to the `reflections/` package. Use this as a reference alongside the canonical example, `reflections/housekeeping/disk_space_check.py`, which demonstrates every convention below.

**Before adding a new reflection, check whether it belongs here at all.** If the task is a pure cloud-API-audit-that-files-issues with no dependency on local/Redis state, it's a candidate for a Claude Code Routine instead of a local reflection — see [Cowork Tasks](cowork-tasks.md) for the decision rule and its candidate re-triage table for which existing reflections can migrate and why most can't.

For the full scheduler architecture, registry field reference, schedule grammar, output sinks, and state model, see [Reflections: Autonomous Maintenance System](reflections.md). This guide covers only what a contributor needs to land one new reflection.

## The Callable Contract

Every reflection is a no-argument callable named `run()` that returns a dict:

```python
{"status": "ok" | "error" | "skipped" | "disabled", "findings": [...], "summary": str}
```

- `status` — one of `ok`, `error`, `skipped`, or `disabled`.
- `findings` — a list (empty when nothing to report).
- `summary` — a one-line string describing the outcome.

`run()` may be a plain sync `def` or an `async def`. The scheduler dispatches sync callables through `run_in_executor` (see [Async-Safety](#async-safety) below) so a reflection doing blocking I/O doesn't have to become async just to stay safe. This contract is documented at the top of `reflections/__init__.py` and enforced by the `assert_valid_result()` helper in the test suite (see [Testing](#testing)).

## File Layout (One File Per Reflection)

Since issue #1028, each reflection lives in its own file at `reflections/{group}/<name>.py` and exposes a single `run()` entry point. Current groups (`ls reflections/`): `agents/`, `audits/`, `housekeeping/`, `memory/`, `pm_briefings/`.

The canonical example, `reflections/housekeeping/disk_space_check.py`, follows a standardized module-docstring header:

```python
"""reflections/housekeeping/disk_space_check.py — Warn when free disk space is low.

What it does: Reads shutil.disk_usage on the project volume and records a finding
    when free space drops below 10 GB (read-only; no writes).
Cadence: 86400s (daily) (early warning before the volume fills)
Failure modes:
    - disk_usage raises -> caught, status="error" with the exception in summary
Related reflections:
    - redis_ttl_cleanup: reclaims space this check monitors
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""
```

Give every new reflection this same five-line header — What it does / Cadence / Failure modes / Related reflections / See also — before the imports.

### The Compatibility Re-Export Shim

Before #1028, reflections were bundled into group-level modules (`reflections/maintenance.py`, `reflections/auditing.py`, `reflections/task_management.py`, `reflections/memory_management.py`). Those files now exist only as **compatibility re-export shims**: each imports the relocated `run()` from its per-file module and re-exports it under its historical `run_<name>` symbol, so `config/reflections.yaml`'s pre-#1028 dotted paths (e.g. `reflections.maintenance.run_disk_space_check`) keep resolving without a registry edit.

`reflections/maintenance.py` in full:

```python
from reflections.housekeeping.disk_space_check import run as run_disk_space_check
# ... four more re-exports ...

__all__ = ["run_disk_space_check", ...]
```

**Do not add new code to `reflections/maintenance.py` (or its `auditing.py` / `task_management.py` / `memory_management.py` siblings).** They are generated-by-convention shims for old registry entries, not a place to register new reflections. A new reflection registers its per-file dotted path (`reflections.housekeeping.<name>.run`) directly in the YAML — see the next section.

## YAML Registration

Register the reflection in `config/reflections.yaml` (see [Registry Format](reflections.md#registry-format-configreflectionsyaml) and [Schedule Grammar](reflections.md#schedule-grammar) in `reflections.md` for the full field reference). A minimal `function`-type entry:

```yaml
- name: your-reflection-name
  description: "One line describing what this checks and why"
  every: 300s
  priority: normal
  execution_type: function
  callable: "reflections.housekeeping.your_reflection_name.run"
  enabled: true
```

- `every: <N>s` (or `<N>m` / `<N>h` / `<N>d`) is the schedule grammar — never the legacy `interval` key. The old `interval` field was collapsed into `every:` by issue #1273; a stale header comment at the top of `config/reflections.yaml` still shows the legacy field name in its field-reference table — don't copy it.
- `priority` is one of `urgent`, `high`, `normal`, `low`.
- `callable` is the dotted path to the per-file `run` — point it at the new module directly, not at a compatibility shim.

## Async-Safety

Reflection callables run inside the scheduler's asyncio event loop. A blocking call inside an `async def run()` freezes the whole scheduler and can starve the worker heartbeat. There are two safe shapes:

**1. `async def run()` wrapping blocking I/O in `asyncio.to_thread`** — e.g. `reflections/audits/redis_quality_audit.py`:

```python
all_links = await asyncio.to_thread(lambda: Link.query.all())
```

**2. Plain sync `def run()`**, which the scheduler dispatches via `run_in_executor` on its dedicated reflection thread pool (`agent/reflection_scheduler.py`) instead of running inline on the event loop. Use this shape when the reflection does filesystem or subprocess work throughout and there's no benefit to `async def`.

Whichever shape you pick, never call blocking I/O (`subprocess.run`, unwrapped Redis-model queries, synchronous file reads over unbounded data) directly inside an `async def` — always route it through `asyncio.to_thread` or write the callable as a plain sync `def`.

**Redis-connection failures are an expected failure mode, not an afterthought.** Because reflections touch Redis routinely and run unattended, `run()` should treat `redis.exceptions.ConnectionError` as a named case in its `Failure modes` docstring section and handle it explicitly — catch it and return `status: "error"` (or `status: "skipped"` if the reflection is a no-op without Redis) rather than letting it propagate. The canonical `disk_space_check.py` example only satisfies this incidentally, via a broad `except Exception` around its (non-Redis) `shutil.disk_usage` call — it never actually touches Redis. A new reflection that *does* read or write Redis models should not rely on that same catch-all; name the `ConnectionError` case explicitly in both the docstring and the except clause so a Redis outage degrades to a clean `error`/`skipped` result instead of an unhandled crash mid-run.

## Testing

Add a smoke test to `tests/unit/test_reflections_package.py` (or a sibling `tests/unit/test_reflections_<topic>.py` if the file is getting large) using the shared `assert_valid_result()` helper defined at the top of that file:

```python
def test_run_your_reflection_returns_valid(self):
    """run() returns valid dict."""
    from reflections.housekeeping.your_reflection_name import run

    with patch("shutil.disk_usage", return_value=mock_usage):  # mock whatever it touches
        result = run_async(run())
    assert_valid_result(result)
```

`run_async()` (also defined at the top of that test file) runs the callable synchronously whether `run()` is `async def` or a plain sync `def` returning a dict directly — use it uniformly rather than branching on the callable's signature. Mock Redis models, the filesystem, and any subprocess calls so the test suite stays fast and has no external dependencies.

## Agent-Type Reflections

Not every reflection is a Python callable. `execution_type: agent` spawns a full PM (Claude Code) session with a natural-language `command:` prompt instead of a `callable:` dotted path. Two real examples from `config/reflections.yaml`:

```yaml
- name: system-health-digest
  description: "Daily Telegram health summary: circuit states, throttle level, session counts, failure clusters"
  every: 86400s # daily
  priority: low
  execution_type: agent
  command: >
    Generate and send the daily sustainability digest for the Valor AI system.
    Required output fields: (1) circuit state per dependency (anthropic, telegram, redis),
    ...
  enabled: false
```

```yaml
- name: sentry-issue-triage
  description: "Triage unresolved Sentry issues for all projects with SENTRY_DSN in their .env"
  every: 86400s # daily
  priority: low
  execution_type: agent
  command: >
    Triage unresolved Sentry issues across all local projects.
    ...
  enabled: false
```

The scheduler (`agent/reflection_scheduler.py`) executes `agent`-type reflections by spawning and awaiting a PM session with `command` as its prompt, rather than calling a Python function directly. Use this type only when the task genuinely needs full agent reasoning (natural-language triage, cross-tool orchestration) — a task expressible as deterministic Python belongs in a `function`-type `run()` instead.

## Checklist

When adding a new reflection:

- [ ] Create `reflections/{group}/<name>.py` exposing `run()`, with the five-line module-docstring header (What it does / Cadence / Failure modes / Related reflections / See also)
- [ ] Handle `redis.exceptions.ConnectionError` explicitly if the reflection touches Redis
- [ ] Register it in `config/reflections.yaml` with `name`, `description`, `every:` (never the legacy `interval` key), `priority`, `execution_type: function`, `callable` pointing at the new per-file module, `enabled`
- [ ] Add a smoke test to `tests/unit/test_reflections_package.py` (or a sibling `test_reflections_<topic>.py`) using `assert_valid_result`
- [ ] Run `pytest tests/unit/test_reflections_package.py -x -q` to verify
- [ ] Update `docs/features/reflections.md` if the new reflection changes the registered set (e.g. adds a group, or belongs in its Registered Reflections tables)
