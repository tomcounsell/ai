---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
revised: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2118
last_comment_id:
revision_applied: true
---

# Fix run_email_bridge async-teardown hang (un-awaited coroutine leak)

## Problem

During the 2026-07-16 reliability batch, 6 of 8 parallel SDLC merge gates wedged: the
full pytest suite reached ~99% and then hung in async teardown, emitting
`coroutine 'run_email_bridge' was never awaited` followed by `gc.collect()`, and never
wrote junitxml. The merge-guard had no suite evidence, forcing every lane onto the
documented targeted-test bypass. This silently degrades every future full-suite gate.

**Root cause (confirmed):** `bridge/email_bridge.py::main()` ends with
`asyncio.run(run_email_bridge())`. Two unit tests in
`tests/unit/test_email_bridge.py::TestMainEnvLoading`
(`test_main_calls_load_dotenv_with_correct_paths` and
`test_main_skips_dotenv_under_launchd`) patch `bridge.email_bridge.asyncio.run` with a
bare `MagicMock`. Python evaluates the argument `run_email_bridge()` **before** the call,
creating a coroutine object, and hands it to the mock — which never awaits it. The
coroutine object is retained on the mock's `call_args` and is only finalized later during
garbage collection / interpreter teardown, at which point CPython emits
`RuntimeWarning: coroutine 'run_email_bridge' was never awaited`. Under pytest this
surfaces as a `PytestUnraisableExceptionWarning` during loop/interpreter teardown and,
combined with the full suite's teardown ordering on a contended machine, wedges the run
before junit is written.

This is **purely test-side** — in production `asyncio.run` really awaits the coroutine.
It is distinct from #2064 (machine-global lock) and #2060 (per-process Redis-db isolation),
both merged.

**Deterministic repro:**
```
python -W error::RuntimeWarning -m pytest \
  tests/unit/test_email_bridge.py::TestMainEnvLoading -p no:cacheprovider -n0 -q
```
emits two `coroutine 'run_email_bridge' was never awaited` warnings (one per test).

## Solution

Make the patched `asyncio.run` consume the coroutine instead of dropping it. Replace the
bare `patch("bridge.email_bridge.asyncio.run")` in both tests with a patch whose
`side_effect` closes the coroutine it receives:

```python
with patch("bridge.email_bridge.asyncio.run", side_effect=lambda coro: coro.close()):
```

`coro.close()` finalizes the coroutine immediately and deterministically, so no
"never awaited" warning is ever emitted. The tests still exercise the real `main()` code
path (dotenv gating) without actually running the async loop. This fixes the leak at its
source rather than suppressing the warning via filterwarnings.

## Success Criteria

- [ ] The two `TestMainEnvLoading` tests patch `asyncio.run` so the received coroutine is closed, not dropped.
- [ ] `python -W error::RuntimeWarning -m pytest tests/unit/test_email_bridge.py::TestMainEnvLoading -p no:cacheprovider -n0 -q` passes with zero `coroutine 'run_email_bridge' was never awaited` warnings.
- [ ] The full `tests/unit/test_email_bridge.py` module passes clean.
- [ ] No production code in `bridge/email_bridge.py` changed.

## No-Gos

- Do NOT suppress the warning via `filterwarnings` / `pytest.ini` — that hides the leak
  instead of fixing it, and would mask genuinely un-awaited coroutines elsewhere.
- Do NOT change `bridge/email_bridge.py::main()` production behavior — the leak is entirely
  in the test's mock, not in production code.
- Do NOT re-solve #2064 (lock) or #2060 (db isolation).

## Update System

No update system changes required — this is a test-only fix. No new dependencies, config
files, or migration steps; nothing propagates to other machines beyond the normal git pull.

## Agent Integration

No agent integration required — this is a test-only fix. No new CLI entry point, no bridge
import changes. The agent's ability to invoke the email bridge is unchanged.

## Failure Path Test Strategy

The fix is itself a test-correctness fix. The failure path is "the coroutine leaks a
RuntimeWarning." We prove the failure path is closed by running the two affected tests
under `-W error::RuntimeWarning` and asserting zero `never awaited` warnings — the same
repro command that currently fails now passes clean.

## Test Impact
- [ ] `tests/unit/test_email_bridge.py::TestMainEnvLoading::test_main_calls_load_dotenv_with_correct_paths` — UPDATE: change the `asyncio.run` patch to close the received coroutine (`side_effect=lambda coro: coro.close()`). Assertions unchanged.
- [ ] `tests/unit/test_email_bridge.py::TestMainEnvLoading::test_main_skips_dotenv_under_launchd` — UPDATE: same patch change. Assertions unchanged.

## Rabbit Holes

- Chasing a production-side fix in `main()` — there is none; production awaits correctly.
- Auditing every `patch(..., "asyncio.run")` in the suite. A sibling instance exists in
  `tests/unit/test_memory_bridge.py`, but it is out of scope for #2118 (different coroutine,
  not implicated in the gate wedge). Note it in the PR for a possible follow-up; do not
  expand this fix to cover it.

## Documentation
No documentation changes needed — this is a test-internal correctness fix with no
user-facing surface, no new feature, and no behavior change to any documented component.
`tests/README.md` already documents `bridge/email_bridge.py` `main()` coverage and remains
accurate.
