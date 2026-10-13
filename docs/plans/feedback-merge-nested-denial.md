# The merged-head session test under the check sandbox

## What is red, and why

`tests/test_feedback_merge.py::test_the_session_brings_the_merged_head_in_and_tells_the_turn_when_it_cannot`
fails at fccf34305 in the verify routine's check sandbox, and in a turn's
sandbox (reproduced here).

`workspace.bring_merged` runs git in the clone under `sandbox-exec` with the
turn's profile. The check sandbox the suite runs under denies applying a
sandbox inside it (`sandbox-exec: sandbox_apply: Operation not permitted`).
`session.merged_into_work` catches that `GitError` and returns its note, so:

- the first call returns a note for the wrong reason (the denial, not the
  uncommitted `docs.md`), and the test's first assertion passes by accident;
- the second call, after `docs.md` is removed, still returns the note, and
  the test fails.

Reproduced: the note reads "the clone's git config names what the kernel
will not run: the config could not be read: sandbox-exec: sandbox_apply:
Operation not permitted".

The two sibling tests in the file meet the same denial but are reported
skipped. `tests/conftest.py`'s `pytest_runtest_makereport` turns a failure
into a skip when its text shows a denial's error, and their traceback carries
the `GitError`. This test gets the error only as a returned string, which
pytest's assertion repr truncates ("The ...e86e01ff1eb"), so the conftest
never sees `sandbox_apply` and the failure stays red.

The kernel code is right: in production the kernel is not nested in a
sandbox. The defect is in the test.

## The fix

Skip the test up front when the nested-sandbox denial is met, the way
`tests/denials.py` documents for "a test whose failure under it carries no
error of its own". That is the pattern `tests/test_workspace.py:695` already
uses for the disk denial:

```python
from tests import denials

@pytest.mark.skipif(denials.met(denials._nested), reason=denials.NESTED)
def test_the_session_brings_the_merged_head_in_and_tells_the_turn_when_it_cannot(tmp_path):
```

Add a one-line comment above it, matching test_workspace's style, saying that
under the denial the failure shows up only in the returned note.

This is test code, not governance. It adds no check, gate, hook or review
step to the kernel. It only reports a test as skipped where the environment
cannot run it, and the conftest already does the same for the sibling tests.

I considered `assert note is None, note` instead, so the denial text would
reach the conftest. I rejected it: under the denial the first assertion
already passes for the wrong reason, so the test would prove nothing even
when it is not red.

## Out of scope

- `session.merged_into_work` and `workspace.bring_merged`: no change.
- Why the verify routine's sandbox denies nested sandboxing: that is by
  design, and the suite's own `denials` mechanism covers it.

## Tests

- Under the check or turn sandbox: the test reports skipped with the
  `NESTED` reason, and the file reports 3 skipped, 0 failed.
- Unsandboxed on macOS (the kernel's own `uv run pytest` outside a sandbox):
  the test runs and passes. The first call returns the note naming the
  overwrite, and the second fast-forwards to the head. The builder cannot run
  this from inside its own sandbox. If it cannot, the delivery names it as
  unverified, with the evidence that the skip triggers only on the denial
  (`denials._nested` probes `sandbox-exec` directly).
- `tests/test_denials.py` and ruff stay green.

## Stakes

A test-only change to one test's skip condition. It is reversible, and the
only cost of a mistake is a test skipped where it could run.
