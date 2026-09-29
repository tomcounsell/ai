# Post-Test Quality Gates

Loaded after tests pass, before the OUTCOME line.

## Exception Swallow Gate (mandatory, blocking)

Run the bundled script from this skill's directory:

```bash
python3 <skill-dir>/scripts/swallow_gate.py   # --base <ref> to override the diff base
```

It fails (exit 1) when the diff adds an `except Exception` whose next 3 lines
neither log nor re-raise, unless the except line carries
`# swallow-ok: <reason of 10+ non-whitespace chars>`. Exit 2 means the diff
could not be read (e.g. no `main`; pass `--base`): not a pass. On failure emit
`<!-- OUTCOME {"status":"fail","stage":"TEST","artifacts":{"swallow_gate":"failed","new_swallows":[...]}} -->`
and stop; never emit a success OUTCOME after a failed gate. The script covers
Python; on other stacks apply the same rule to the language's catch-all handler.

## Stale xfail scan (advisory)

An expected-failure marker whose bug is fixed hides regressions. Flag each as
`STALE XFAIL: <file>:<line> — [decorator|runtime] form` and suggest converting
it to a hard assertion:

- Decorator `@pytest.mark.xfail`: stale when the run reports `XPASS`.
- Runtime `pytest.xfail(...)` inside a test body: never reports XPASS because it
  short-circuits the test, so every one needs review (check whether its guard
  condition still holds). In `--changed` mode on a bug fix, runtime xfails in
  related test files are blockers, not warnings.
