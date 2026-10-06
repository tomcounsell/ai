---
tracking: none
slug: m2-4c-workspace-suite
type: bug
status: built
critique_rounds: 1
review_rounds: 2
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
   none of which the profile allows. Two test servers bind fixed ports and
   never read it: `tests/smart_http.py` (6481 to 6489, for
   `test_credential_push` and `test_targets`) and `tests/test_look.py`
   (6451 to 6459).
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
2. `tests/ports.py`: `listen(taken=())`, the port a test server binds: 0
   (the OS chooses) when `VALOR_TEST_PORTS` is unset; else the first port
   after the one it last returned, wrapping around the span, that is not
   in `taken` and that a `SO_REUSEADDR` bind on `127.0.0.1` accepts (the
   way the servers bind, so a port in TIME_WAIT is free and a live
   listener is not). It raises naming the span when none is left. The
   rotating cursor keeps back-to-back calls distinct while the
   subprocesses they name start, and leaves just-released ports for last.
   `span` keeps its meaning. `mailserver.Ports` uses it in place of its
   own copy, and the tests' service ports (`scripted.provisioned`,
   `test_workspace`) come from it when the span is set, in place of
   `kws.choose_port`, so `core/workspace.py` is unchanged.
   The budget, with the span at the ten dev ports: the shared judgement
   upstream holds 1 for the session, the `mail` session fixture 4 once
   Dovecot is present, a `telegram_emulator` module fixture 1, and
   `test_credentials`' TCP cluster 1 for its module, which leaves at least
   three for any one test's gateway, upstream, and task services. A test
   that needs more than the span holds fails with `listen()`'s error
   naming the span.
3. Every test server listed in Cause 2 binds `ports.listen()`:
   `judgement_upstream.Upstream` (default), `ScriptedUpstream.start`
   (default), `Gateway.start(port=ports.listen())` at each test call,
   `kernel_child`'s silent upstream and gateway, `telegram_emulator.main`
   (it inherits the environment), and the test-local servers and probe
   sockets, `tests/smart_http.py`, and `tests/test_look.py`.
   `test_demo_sandbox` draws its gateway ports and its task's
   service ports from the span when it is set, and keeps its denied probes
   (machine Postgres, 6379, the local bridge port) as they are.
   `Gateway.start` already takes a port, and `serve` takes the test's
   gateway, so neither changes.
4. `core/backup.py`: `scratch_cluster` and `start_cluster` take an
   optional `port` for the TCP case, `_free_port()` when absent, so
   `test_credentials` passes `ports.listen()`. Every kernel caller keeps
   the default; this is the one line of kernel code the fix touches.
5. `pyproject.toml` dev group gains `ruff==0.16.10` (what `uvx ruff`
   resolves on this Mac), and `uv.lock` is regenerated, so
   `uv run ruff` runs in a workspace.
6. `tests/README.md`: `VALOR_TEST_PORTS` moves every server the tests
   start, and `projects/valor.toml` sets it to the dev ports.
7. `tests/test_intake.py::test_steer_mid_turn` (its wait for
   `turn.started`) and the same wait in `tests/test_objective_tree.py`
   look at the `driving` task on each pass and re-raise its exception when
   it has failed, so a drive that fails before `turn.started` fails the
   test instead of spinning forever. No timeout is added; the suite has
   no time limit.
8. Whatever else the run under the real check profile shows the suite's
   own configuration can fix: the spec's `[env]` (`VALOR_PG_SCRATCH`, now
   `/tmp`, which the profile denies, moves to a path inside the workspace
   the profile allows, within the socket path limit) and test-side paths.
   No sandbox profile changes; that is a security change outside this
   task. The build record names each one.

## Done, as evidence

Against `VALOR_TEST_DB=valor_rebuild_test_24cbuilder`, ports 6620 to 6629,
real Postgres and real `sandbox-exec`:

- The valor suite, collected under the real check profile
  (`workspace.check_profile` for a layout in the test's directory) with
  `VALOR_TEST_PORTS=8000-8009`, collects every module with no error.
- The valor suite collects and runs inside the real check profile, in a
  workspace the build makes for the measurement (never `~/valor-tasks`,
  never the live kernel), with the spec's `[env]`: every test either
  passes, or skips with a reason naming the sandbox denial it met, or
  fails as it fails on the host at the same commit (Dovecot and node are
  not installed on this Mac). A skip names a denial the run actually
  met, never an invented one. The measured counts, and the list of
  skipped tests by denial, go under Left out. `uv run ruff check .` exits
  0 in that workspace.
- The suite on the host, with `VALOR_TEST_PORTS` unset and with it set to
  a ten-port span, gives the same results as before the change, and both
  ruff checks are green.

On Tom's Mac, once merged: a message-started `valor` task's `suite.ran`
rows show passed tests at base and head.

## Tests

- `tests/test_checks.py::test_the_valor_suite_collects_under_the_check_profile`:
  renders `check_profile` for a layout under `tmp_path`, appends
  `(allow file-read* (subpath <repo>))` and the same for the interpreter's
  own prefix (the profile denies `~/src`, so neither the repository nor its
  `.venv` is readable otherwise; the later rule wins, and the network
  rules under test stay as rendered), and runs
  `<repo>/.venv/bin/python -m pytest --collect-only -q tests` with `cwd`
  the repository, the valor spec's `[env]` with `VALOR_TEST_PORTS` at the
  dev ports, and `TMPDIR` under the check's directory. It asserts exit 0
  and no `ERROR collecting`. On the code as it is it fails with the
  incident's `PermissionError`.
- `tests/test_ports.py::test_listen_draws_from_the_span`: unset, `listen()`
  is 0; set to a span with its first port held by a listener, it returns
  the second, a port in `taken` is passed over, a span port in TIME_WAIT
  is returned, a used-up span raises naming it, and a server bound on a
  returned port is reachable under a profile that allows only the span.
- The workspace run of the Done item is recorded evidence (one run, its
  counts in the plan), not a test: a test that runs the whole suite would
  run itself.

## Left out

- `core/checks.py` and its doc: the verdict follows the doc (Cause, "Why
  `checks.test` passed it").
- The reviewer's `pass` over a `verify.ran` with nothing passing (event
  190): the review session's judgement, not the kernel's.
- Allowing `localhost:*` or a per-task test span in the profiles: it
  widens what turn-written code can bind, and the dev ports already serve.
- Installing Dovecot on this Mac: a machine change; the email tests name
  it as missing.
- A time limit on the tests' waits: fix 7 makes a failed drive fail its
  test, and a suite with no time limit is the documented rule.
- Any sandbox profile change, including one that would let a skipped test
  run in a check: a security change outside this task.

## Questions for Tom

- Should a `checks.test` run whose head has no passing test be `red`?
  It is a new check, so it needs your grant. Assumed answer: no; this plan
  fixes the suite so that it runs, and adds no check.

## Decided by default

- The span is the existing dev ports, not a new reserved span per task:
  no profile change, and the turn slot keeps them free during a check.
- `tests/ports.py` returns port 0 when `VALOR_TEST_PORTS` is unset, so a
  host run keeps OS-chosen ports.
- Stakes: critique 1, review 2 (the lead's call). No profile, check, or
  stored data changes, and the one kernel line (`backup`'s optional port)
  keeps every kernel caller's behavior; review covers it. The rest is the
  tests' port choice, project spec lines, and a dev dependency, all
  reversible, with the host suite and the workspace run as evidence.
  Review's focus is `test_demo_sandbox`, whose probes change ports under
  the span, and `backup.py`.

## Critique rounds

Round 1 (critic-2-4c-r1, verdict revise; the one round the stakes allow,
so its findings ride into the build). Lead's decisions:

1. and 2. Taken. `listen()` probes with `SO_REUSEADDR`, rotates a cursor
   over the span, and hands out the service and mail ports too; the port
   budget is stated (fix 2).
3. Taken. `tests/smart_http.py` and `tests/test_look.py` move onto the
   helper (Cause 3, fix 3).
4. Taken. The build measures under the real check profile, not only its
   loopback rules, and fixes what the suite's own configuration can: the
   spec's env (`VALOR_PG_SCRATCH` inside the workspace) and test-side
   paths (fix 8). No sandbox profile changes. The Done item is rewritten
   to be reachable; the measured list goes under Left out.
5. Taken. The third test is cut; the workspace run is recorded evidence.
6. Taken. The collection test says how it reaches pytest.
7. Taken. Both unbounded waits check the driving task and fail when it
   fails, with no timeout (fix 7).
8. Taken (the critique's 8 to 10). `ruff==0.16.10`; `review_rounds` is 2.

## Build record

Built on `m2-4c-workspace-suite` in one commit over the plan at
`88949b680`. Fixes 1 to 7 as written. What differs from the plan, or
the run under the real check profile added:

- **Denials are skips that name them.** `tests/denials.py` and a report
  hook in `tests/conftest.py`: a failure is reported skipped, with a
  reason naming a denial of the sandbox the suite runs under, only when
  its traceback or captured stderr shows that denial's own error and
  trying the same operation in that run is denied too. The denials met:
  running `/bin/ps` (setuid), listening on a port the OS chooses, the
  shared `/private/tmp`, applying a sandbox inside the sandbox, and
  DiskArbitration. On the host nothing is denied, so nothing is skipped
  this way. `tests/test_denials.py` holds the rule.
- **Failures show their cause.** Waits on a task the test started
  (`test_steer_mid_turn`, `test_objective_tree`, the `serve` gateway test,
  the session record test) re-raise that task's error instead of spinning
  or timing out; two `test_targets` assertions, one `test_intake`
  assertion, and the assertions in `test_judgement_sites`, `test_pi`,
  `test_objective_tree` and `test_workspace` carry the stderr or rows that
  show why.
- **A second kernel line.** `core/serve.py` logs a failed step as the
  exception's text (`PermissionError: [Errno 1] Operation not permitted:
  '/bin/ps'`), not its `repr`, which drops the file name. Without it a
  kernel step failing on a denial shows nothing a reader or the hook can
  name. The first kernel line is `backup`'s optional port, as planned.
- **The page tests' browser.** `tests/test_local_page.py` runs Chrome
  with `--no-sandbox` (as `look` does) and `MAC_CHROMIUM_TMPDIR` at the
  suite's temp directory: in a check it could neither apply its own
  sandbox nor make its socket directory in the user temp directory, both
  denied. With these the four page tests pass inside the check. When the
  browser exits, the wait fails with its stderr.
- **Task Redis ports.** `tests/conftest.py` sets `VALOR_PG_PORTS` and
  `VALOR_REDIS_PORTS` to the span when it is set, so the ports the kernel
  chooses for a task the tests provision lie inside the span too.
- **`VALOR_PG_SCRATCH` stays `/tmp`.** Fix 8 cannot be done in the spec.
  The spec's `[env]` knows only `{port}` and `{passfile}`. Every path the
  check profile lets a check write is under its check directory, and a
  scratch cluster's socket there
  (`~/valor-tasks/<id>/checks/test-head-<sha12>/tmp/vk-XXXXXXXX/.s.PGSQL.5432`)
  is 103 bytes or more, past macOS's limit once a test adds its own
  prefix. The turn profile allows `/tmp`, which is why the spec sets it.
  In a check the scratch cluster tests skip on the `/private/tmp` denial.

### Measured inside the check profile

At the built tree, in a workspace provisioned from the valor spec under
the build's scratch directory (task port 6629), through `checks.suite`
with `lint=True`: **1064 passed, 2 failed, 527 skipped, 0 errors**
(exit 1). `uv run ruff check --output-format concise .` exited 0 in that
checkout. Before this build (incident): 0 passed, 29 errors in
collection.

Skipped, by reason:

| reason | tests | modules (count) |
|---|---|---|
| running `/bin/ps`, a setuid program | 334 | test_workspace 47, test_pipeline 55, test_docs_runner 28, test_fresh 26, test_judgement_sites 26, test_review 26, test_credential_push 25, test_checks 23, test_harness_contract 20, test_pi 9, test_serve 9, test_kernel 7, test_session 6, test_intake 5, test_targets 4, test_look 3, test_persona 3, test_reap 3, test_objective_tree 2, test_telegram_pipeline 2, and one each in test_corrections, test_emulator_metering, test_gateway_openai, test_replay, test_transcripts |
| the shared temp directory `/private/tmp` | 82 | test_email_kernel 38, test_email_smtp 17, test_credentials 15, test_email_imap 6, and one each in test_checks, test_demo_sandbox, test_email_bridge, test_mailserver, test_pipeline, test_workspace |
| applying a sandbox inside the sandbox | 35 | test_workspace 19, test_demo_sandbox 10, test_harness_contract 2, test_pi 2, test_ports 1, test_reap 1 |
| DiskArbitration, so no disk image attaches | 16 | test_backup 15, test_workspace 1 |
| listening on a port the OS chooses | 4 | one each in test_demo_sandbox, test_judgement, test_judgement_sites, test_session |
| the suite's own skips, as on the host | 56 | live tests (no `VALOR_LIVE`) 19, Pi not installed or not the pinned release 21, no Playwright shell 9, no node 2, and five single gated tests |

The `/bin/ps` skips met the denial in the kernel's own process reads
(for example `runs` listing a turn's processes after it ends); the port-0 skips are the kernel's
own gateway in `python -m core run` and `serve`, which binds a port the
OS chooses. The email tests skip on `/private/tmp` because the mail
servers' run directory is there; on the host they error because Dovecot
is not installed.

The two failures pass on the host and are not sandbox denials: the
workspace's Postgres gives the tests the `app` role, which is not a
superuser, so `test_backup::test_a_backup_directory_on_the_clusters_own_disk_is_refused`
cannot read `data_directory` and
`test_credentials::test_migrate_touches_no_credential_on_the_machine_cluster`
cannot read `pg_authid`. Neither the spec nor the tests can change that
without granting the task's role more, which is a provisioning change
outside this task. Named under Left out.

### On the host

`VALOR_TEST_DB=valor_rebuild_test_24cbuilder`:

- head, `VALOR_TEST_PORTS=6620-6629`: 1474 passed, 55 skipped, 2 failed,
  62 errors.
- head, `VALOR_TEST_PORTS` unset: 1474 passed, 55 skipped, 2 failed,
  62 errors.
- base `88949b680`, unset: 1467 passed, 55 skipped, 2 failed, 62 errors.
  Per test, both head runs match base exactly; the seven more passed are
  the new tests.

The 62 errors are the email tests (Dovecot is not installed on this Mac);
the two failures are `test_mailserver` (Dovecot) and `test_pi`'s node
loader test, as before the change. `uvx ruff check .` and
`uvx ruff format --check .` pass.

### Left out, measured

- The two workspace failures above: the task Postgres's `app` role is no
  superuser.
- Every skip in the table above: each is a denial of the check profile,
  and changing the profile is a security change outside this task. The
  biggest is `/bin/ps` (334): the kernel reads processes with a setuid
  program the check profile cannot run.
- `VALOR_PG_SCRATCH` inside the check directory: the socket path limit.

## Patch round 1

Review round 1 (review-2-4c-p0) asked for changes; the test check
(test-2-4c) was red. Patched by builder-2-4c-p1 against
`VALOR_TEST_DB=valor_rebuild_test_24cbuilder`, ports 6620 to 6629.

1. **A port given to a task's service is no server's.** With the span
   set, `test_checks::test_a_kernel_killed_mid_suite_leaves_nothing_on_the_tasks_port`
   failed every time: the test provisions a task whose Redis port comes
   from the span, Redis starts later inside a kernel process of its own,
   and that process's `listen()` starts at the span's low end and gave
   the gateway the Redis port first. Fixed in `tests/ports.py`: a port
   `service()` gives is written to the file `VALOR_TEST_RESERVED_PORTS`
   names (a session file `tests/conftest.py` makes, so every process the
   tests start reads it), `listen()` passes over it, and the
   `release_ports` fixture releases it when the test ends, after the
   task's services stop. `tests/test_ports.py` holds it, with a fresh
   process's `listen()`. The test passed 5 of 5 alone and in both host
   runs.
2. **A denial is read only from its own error.** The nested sandbox and
   DiskArbitration matchers took the bare words `sandbox-exec`,
   `diskutil` and `hdiutil`, which a traceback's source lines carry, so a
   plain assertion failure in a test naming them was reported skipped.
   Now every matcher is a pattern over the denial's error:
   `sandbox_apply: Operation not permitted`, `unable to use the
   DiskManagement framework`, and for the shared temp directory one
   `(Operation not permitted|File exists): '/(private/)?tmp` message. The
   hook also reads the output a failed command carried
   (`CalledProcessError.stderr` and `stdout`), where `diskutil` prints its
   error. Where the error was not in the failure, the test now puts it
   there: the two `test_harness_contract` Pi credential tests and
   `test_workspace`'s fstat test carry the command's stderr. The one test
   whose command prints no error naming the denial
   (`test_a_turn_mounts_nothing_and_opens_nothing_outside_its_sandbox`,
   where `hdiutil create` fails bare) skips up front on the disks probe.
   `tests/test_denials.py` runs the hook with every denial met: failures
   naming `sandbox-exec`, `hdiutil` or `/tmp` only in their source stay
   failed, and a command whose stderr names a denial is skipped. Its
   inner pytest now runs from its own directory with its own ini file;
   with `-c /dev/null` it walked the home directory, denied in a check.

### Measured inside the check profile, after the patch

At the patched tree, provisioned from the valor spec under the
worktree's `w/` (task port 6629), through `checks.suite` with
`lint=True`: **1066 passed, 2 failed, 527 skipped, 0 errors** (exit 1),
lint exit 0. The two failures are the `app` role ones under Left out,
measured. Skips by reason, identical in tests and counts to the build's
table: `/bin/ps` 334, `/private/tmp` 82, nested sandbox 35,
DiskArbitration 16 (15 `test_backup` matched on `diskutil`'s own error,
1 the up-front skip), port 0 4, the suite's own 56. A second run with
`-rs` gave the same skips and one more failure,
`test_local_page::test_the_page_sends_shows_replies_and_renders_text_as_text`
(a 10 second browser wait, the host suite running beside it); it passed
in the first run and 5 of 5 alone in the profile.

### On the host, after the patch

- `VALOR_TEST_PORTS=6620-6628`: 1476 passed, 55 skipped, 2 failed,
  62 errors.
- unset: 1476 passed, 55 skipped, 2 failed, 62 errors.

The failures (`test_mailserver`, `test_pi`'s node loader test) and the
errors (Dovecot) are those at base. `uvx ruff check .` and
`uvx ruff format --check .` pass.
