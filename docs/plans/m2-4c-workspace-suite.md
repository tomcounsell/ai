---
tracking: none
slug: m2-4c-workspace-suite
type: bug
status: planned
critique_rounds: 1
review_rounds: 1
governance_grant: none
---

# 2.4c: the valor suite cannot run inside its own workspace

Found on Tom's Mac during the 2.4 rollout, on the smoke task that
message-started the `valor` project (`projects/valor.toml`). It breaks
milestone 1.4's Done item "The kernel provisions each task's workspace ...
including the app's environment so the suite can run"
([valor-rebuild.md](valor-rebuild.md)).

## Incident

Task `3785e89a3d48` in the ledger `valor_rebuild`, workspace
`~/valor-tasks/3785e89a3d48` (task ports `{"postgres": 5440}`). Every run of
the project's suite the kernel made in the check sandbox stopped during
collection:

| event | type | role | exit | passed | failed | errored |
|---|---|---|---|---|---|---|
| 170 | `suite.ran` | base `e900727d90dc` | 2 | 0 | 0 | 29 |
| 171 | `suite.ran` | head `5ccf855eb26f` | 2 | 0 | 0 | 29 |
| 178 | `suite.ran` | review `5ccf855eb26f` | 2 | 0 | 0 | 29 |
| 179 | `verify.ran` | suites 170 and 178 | 2 | 0 | 0 | 29 |

Each log (`checks/test-base-e900727d90dc.suite.out`,
`checks/test-head-5ccf855eb26f.suite.out`,
`checks/test-review-5ccf855eb26f.suite.out`) ends with
`Interrupted: 29 errors during collection`, every one the same:

```
tests/scripted.py:206: in <module>
    State.JUDGE: judge("precise"),
tests/judgement_upstream.py:283: in shared
    _SHARED = Upstream()
...
E   PermissionError: [Errno 1] error while attempting to bind on address ('127.0.0.1', 0): [errno 1] operation not permitted
```

The lint in events 178 and 179 exited 2 as well;
`checks/test-review-5ccf855eb26f.lint.out` reads
`error: Failed to spawn: ruff`.

`test.decided` (event 172) recorded verdict `pass`, `failures: []`, and all
29 modules in `failing_at_base`. Review passed (event 190, citing verify
179), and the task was delivered `passed` (event 192).

## Cause

1. **The sandbox lets the suite listen only on 8000 to 8009.** The check
   profile (`workspace.check_profile`, through `workspace.profile`) denies
   `network-bind` and allows it only on `localhost:8000` to `localhost:8009`
   (`DEV_PORTS`) and on unix sockets in its own directories. Loopback
   connects are denied except to the gateway parameter (port 1 for a check,
   `workspace.sandboxed`), `DEV_PORTS`, and the task's own service ports.
   The turn profile has the same network rules (docs/harnesses.md,
   "Binding"). sandbox-exec cannot allow port 0: checked on this Mac,
   `(local ip "localhost:0")` fails to parse ("invalid port in network
   address"), and a bind to port 0 is refused under any rule but
   `localhost:*`.
2. **The valor suite listens on ports the OS picks.** `tests/scripted.py`
   builds `RUNNERS` at import, which starts `judgement_upstream.shared()`,
   an aiohttp server on `127.0.0.1:0`; `test_judgement` and others call
   `shared()` at import directly. The 29 modules that import either fail
   collection before any test runs, and pytest stops the session. The
   same pattern runs through the rest of the suite: every
   `Gateway.start()` in the tests (60 calls; its default port is 0),
   `ScriptedUpstream.start()`, the test upstreams in `test_gateway_meter`,
   `test_gateway_openai`, and `test_emulator_metering`,
   `tests/kernel_child.py`'s silent upstream, `tests/telegram_emulator.py`,
   `mailserver._free_port`, and the probe sockets in `test_demo_sandbox`,
   `test_judgement`, `test_workspace`, `test_email_kernel`, and
   `test_kernel`. One is kernel code: `backup.scratch_cluster(tcp=True)`
   (used by `test_credentials`) starts its cluster on
   `backup._free_port()`, a port-0 bind. A server a test does start on an
   allowed port is then unreachable when its client is told a port
   outside the allowed set.
3. **`VALOR_TEST_PORTS` covers only some of them.** `tests/ports.py`
   (`span`) moves the test-started Postgres and Redis, the local bridge
   tests (6530 to 6549 by default), and the mail servers
   (`mailserver.Ports`) into one span. `projects/valor.toml`'s `[env]` sets
   no `VALOR_TEST_PORTS`, so in a workspace even those use their defaults,
   none of which the profile allows.
4. **Lint has no binary.** `lint = "uv run ruff check ."`, but `ruff` is in
   no dependency group of `pyproject.toml`, so `uv run` in the check's
   environment has nothing to spawn. On the host the suite's own docs say
   `uvx ruff`, which hides it.

### Measured past collection

To see what fails once collection gets through, the suite at `4d794a4d0`
was run on this Mac under a profile carrying only the check profile's
loopback rules (bind and connect on 8000 to 8009, everything else on
loopback denied, unix sockets open), with `VALOR_TEST_PORTS=8000-8008` and
the shared judgement upstream pinned to 8009 by a one-line local edit.
Through `test_intake` (alphabetical, about a third of the suite): 203
passed, 286 failed, 70 errors, 20 skipped. Failures by module:
`test_gateway_openai` 113, `test_email_kernel` 38 (Dovecot, as on the
host), `test_docs_runner` 28, `test_credential_push` 27, `test_fresh` 26,
`test_gateway_meter` 23, `test_checks` 23, `test_harness_contract` 22,
`test_email_smtp` 17 (Dovecot), `test_demo_sandbox` 12,
`test_emulator_metering` 8, `test_credentials` 8 (`scratch_cluster`'s
port), `test_email_imap` 6 (Dovecot), and one each in `test_intake`,
`test_email_bridge`, and `test_corrections`. The ones read were all the
port-0 bind. Then `test_intake::test_steer_mid_turn` hung: its gateway
never bound, and it waits for a `turn.started` row with no bound, so a
check (which has no time limit) would hold the turn slot until stopped.
The run was stopped by its PID.

### Why `checks.test` passed it

It is the documented rule working as written, not a bug in
`core/checks.py`. docs/sdlc-checks-test.md:

> `test.decided`: the candidate, the command, the failures at head that do
> not fail at base, `deleted_at_head` (tests that passed at base and are
> gone at head), `failing_at_base` (shown, never counted), ... and the
> verdict the kernel computes: any failure `red`, else any behavior `gaps`,
> else `pass`. Per-test results at base and none at head is `red`.

and the state machine's exit evidence for `checks.test`
(docs/sdlc-state-machine.md): "the suite passes at head where it passed at
base". The JUnit report was written and read (`junit: null` means no read
error), so both runs had per-test results: 29 errored at base, the same 29
at head. `compare` puts them in `failing_at_base` and finds no failure at
head that did not fail at base, so the verdict is `pass`. Neither doc says
a suite that cannot run is no evidence, or that a run with nothing passing
is `red`. The code's `BOTH_FAIL` rule (no per-test results on either side
and both failing is a failure) does not apply when a JUnit report exists.
`checks.py` is left as it is.

Making the test check refuse a suite that ran nothing would be a new check
in the sense of CLAUDE.md, needing its incident (this one), its mission
item (1, "testing actual use"), and Tom's grant. This plan carries
`governance_grant: none` and does not add it. Question for Tom, below.

## Threat model

- A check runs the candidate's own suite and conftest, which a turn wrote,
  under the check profile; a build turn runs it under the turn profile.
  Both can bind and reach only what those profiles allow.
- The fix must not widen either profile: no new bind or connect rule, no
  `localhost:*`. `VALOR_TEST_PORTS` is an environment variable naming ports
  the profile already allows; a turn setting it otherwise gains nothing,
  since the sandbox, not the variable, decides.
- Every listener the suite starts binds `127.0.0.1`, never `0.0.0.0`, so
  the dev ports' LAN reach (docs/harnesses.md, "Binding") is not used.
- Nothing new is read by the kernel from the check directory; the JUnit
  read is unchanged.

## Fix

1. `projects/valor.toml` `[env]`: `VALOR_TEST_PORTS = "8000-8009"`, the
   span both the check and turn profiles allow on loopback for bind and
   connect. The turn slot holds one turn or check per machine
   (`core/slot.py`), so nothing else uses those ports while the suite runs.
2. `tests/ports.py`: `listen()`, the port a test server binds: 0 (the OS
   chooses) when `VALOR_TEST_PORTS` is unset, else the first port of the
   span that a bind on `127.0.0.1` accepts, skipping any `listen()`
   returned whose server still holds it. `span` keeps its meaning.
   `mailserver.Ports` uses it in place of its own copy.
3. Every test server listed in Cause 2 binds `ports.listen()`:
   `judgement_upstream.Upstream` (default), `ScriptedUpstream.start`
   (default), `Gateway.start(port=ports.listen())` at each test call,
   `kernel_child`'s silent upstream and gateway, `telegram_emulator.main`
   (it inherits the environment), and the test-local servers and probe
   sockets. `test_demo_sandbox` draws its gateway ports and its task's
   service ports from the span when it is set, and keeps its denied probes
   (machine Postgres, 6379, the local bridge port) as they are.
   `Gateway.start` already takes a port, and `serve` takes the test's
   gateway, so neither changes.
4. `core/backup.py`: `scratch_cluster` and `start_cluster` take an
   optional `port` for the TCP case, `_free_port()` when absent, so
   `test_credentials` passes `ports.listen()`. Every kernel caller keeps
   the default; this is the one line of kernel code the fix touches.
5. `pyproject.toml` dev group gains `ruff`, pinned to the version the
   repository is checked with, and `uv.lock` is regenerated, so
   `uv run ruff` runs in a workspace.
6. `tests/README.md`: `VALOR_TEST_PORTS` moves every server the tests
   start, and `projects/valor.toml` sets it to the dev ports.

## Done, as evidence

Against `VALOR_TEST_DB=valor_rebuild_test_24cbuilder`, ports 6620 to 6629,
real Postgres and real `sandbox-exec`:

- The valor suite, collected under the real check profile
  (`workspace.check_profile` for a layout in the test's directory) with
  `VALOR_TEST_PORTS=8000-8009`, collects every module with no error.
- A real `checks.suite` run of the valor project at the fix's commit, in a
  provisioned workspace with the check's own Postgres, exits with per-test
  results whose `passed` is the host's set at the same commit, and whose
  `failed` and `errored` hold only the host's own environmental ones
  (Dovecot not installed on this Mac: the 62 email errors and
  `test_mailserver`'s kill test; `test_pi`'s loader test). With Dovecot
  installed it is green: `failed` and `errored` empty. Its lint exits 0.
- The suite on the host, with `VALOR_TEST_PORTS` unset and with it set to
  a ten-port span, gives the same results as before the change, and both
  ruff checks are green.

On Tom's Mac, once merged: a message-started `valor` task's `suite.ran`
rows show passed tests at base and head.

## Tests

- `tests/test_checks.py::test_the_valor_suite_collects_under_the_check_profile`:
  renders `check_profile` for a layout under `tmp_path`, runs
  `pytest --collect-only -q tests` under it with the valor spec's
  environment, and asserts exit 0 and no `ERROR collecting`. On the code
  as it is it fails with the incident's `PermissionError`.
- `tests/test_ports.py::test_listen_draws_from_the_span`: unset, `listen()`
  is 0; set to a span with its first port held by a listener, it returns
  the second, and a server bound there is reachable under a profile that
  allows only the span.
- The Done item's workspace run is a test in `tests/test_checks.py`
  marked by its duration, as the live suite runs are: it provisions the
  valor spec from the test's own mirror of this repository and runs
  `checks.suite` at `HEAD`.

## Left out

- `core/checks.py` and its doc: the verdict follows the doc (Cause, "Why
  `checks.test` passed it").
- The reviewer's `pass` over a `verify.ran` with nothing passing (event
  190): the review session's judgement, not the kernel's.
- Allowing `localhost:*` or a per-task test span in the profiles: it
  widens what turn-written code can bind, and the dev ports already serve.
- Installing Dovecot on this Mac: a machine change; the email tests name
  it as missing.
- Bounding the tests' unbounded waits (`test_steer_mid_turn` waiting for a
  row): with the gateway on an allowed port the wait ends, and a suite with
  no time limit is the documented rule.

## Questions for Tom

- Should a `checks.test` run whose head has no passing test be `red`?
  It is a new check, so it needs your grant. Assumed answer: no; this plan
  fixes the suite so that it runs, and adds no check.

## Decided by default

- The span is the existing dev ports, not a new reserved span per task:
  no profile change, and the turn slot keeps them free during a check.
- `tests/ports.py` returns port 0 when `VALOR_TEST_PORTS` is unset, so a
  host run keeps OS-chosen ports.
- Stakes 1 and 1: no profile, check, or stored data changes, and the one
  kernel line (`backup`'s optional port) keeps every kernel caller's
  behavior; the rest is the tests' port choice, a project spec line, and a
  dev dependency, all reversible, with the host suite and the workspace
  run as evidence. Review's focus is `test_demo_sandbox`, whose probes
  change ports under the span. If the lead counts `backup.py` as the
  kernel, it is 2 and 2.
