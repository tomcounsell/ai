---
tracking: none
slug: fast-suite
type: plan
status: planned
critique_rounds: 1
review_rounds: 1
---

# The whole suite in under ten minutes

Today the full suite takes 72 minutes serially on the lead's Mac (M1 Max,
10 cores, 64 GB): 1887 passed, 65 failed, 45 skipped on `3ab790df5`'s
parent. Five builders running it at once took most of a day. A builder
that waits an hour for evidence cannot iterate, so this serves Mission
item 1 (testing actual use is part of the job) and Mission item 4 (larger
undertakings need a fast loop).

**Goal.** `uv run pytest -n auto --dist loadgroup` runs every test,
`container` tests included, in under ten minutes of wall time on the
lead's Mac, green apart from the documented skips. A plain `pytest` (no
workers) still runs the same suite, since the verification VM and a
task's check profile run it serially.

**Stakes 1.** Test code and test settings, plus three small kernel changes
(phase 4) that keep behavior identical. No stored data, money, or
authority moves.

**Governance.** Nothing here adds a check, gate, hook, or round. The ten
minutes is a target this plan meets, not a limit anything enforces: no
test, hook, or CI step fails a run for being slow.

## Where the 72 minutes go

Measured by three read-only probes on 2026-10-10 (single files timed, no
full profiling run), and by the 72-minute run's log:

| Cost | Minutes, serial | Kind |
|---|---|---|
| `container` tests: 41 tests in `test_container.py` (15), `test_review.py` (22), and three others; each session rebuilds the base image (about 4 to 5 min, Dovecot compiles), dependency images without cache (about 37 s each), two VM runs per verification, one test that runs this repository's suite in VMs twice (5 to 10 min) | about 35 | work, serialized on the machine lock |
| Email tests failing on the test certificate and waiting out 30 s poll loops | about 5 to 10 | bug |
| Telegram children refused every send because `VALOR_OPERATOR_CHANNEL=local` leaks from the shell, each waiting 15 s; a leaked child then holds `bridge:telegram:m-test` and one test waits on the advisory lock with no end | about 3, or a hang | bug |
| Two test servers whose shutdown waits 60 s for a hung handler | 2 | bug |
| Pipeline driving per test: `git config --list` before every kernel git call (2059 calls in `test_pipeline` alone), workspace provisioning with `initdb` per task, fresh migrated databases per test | about 20 | work |
| Fixed sleeps and polls: local page polls every 2 s, "nothing else happens" sleeps, the emulator slot poll at 5 s | about 2 | waiting |
| Everything else: subprocess starts, sandbox spawns, big payload tests | about 5 | work |

Processes ran at 53 to 70 percent CPU with system time above user time:
the suite mostly starts processes. That parallelizes well across workers,
but the `container` share cannot, since every VM takes the machine lock.
So the critical path is the `container` group, and phase 3 is what makes
ten minutes possible.

## Phase 1: fix the bugs (removes about 12 min and the hang)

1. **Test certificate.** `/usr/bin/openssl` is LibreSSL 3.3.6, whose
   `req -x509` writes no Subject Key Identifier, so the server
   certificate's Authority Key Identifier carries no key id and Python
   3.14 rejects it. In `tests/mailserver.py:56` add
   `-addext subjectKeyIdentifier=hash`, `-addext
   authorityKeyIdentifier=keyid:always`, and the CA's `basicConstraints`
   and `keyUsage`. Verified by a probe: all 104 email tests pass, 408 s
   becomes 119 s.
2. **Machine environment leaking into tests.** `tests/conftest.py`
   removes `VALOR_OPERATOR_CHANNEL` and every other machine-only `VALOR_*`
   setting before `core.settings` loads, and `tests/telegram_port.py:66`
   builds the child environment from the cleaned one. Each test that
   starts a child (`test_telegram_crash.py`, `test_telegram_pipeline.py`,
   and the email kill tests) kills it in `finally`, so a failing test
   leaks nothing. The lock-waiting test
   (`test_telegram_pipeline.py:247`) bounds its own wait for the second
   bridge, so a leak fails it instead of hanging the run.
3. **Slow server shutdowns.** `tests/scripted_upstream.py:123`,
   `tests/judgement_upstream.py:150`, and
   `tests/test_emulator_metering.py:84` build their `AppRunner` with
   `handler_cancellation=True`, as `core/gateway.py:455` does.
   `test_email_kernel.py::test_a_lookup_that_never_answers_does_not_hold_the_outbox`
   sets `free` before it cancels the bridge.

## Phase 2: run in parallel (non-container work: about 30 min becomes about 4)

1. `pytest-xdist>=3.8` joins the dev dependencies.
2. `tests/conftest.py`, before its first `os.environ` line: when
   `PYTEST_XDIST_WORKER` is set, `VALOR_TEST_DB` becomes
   `<base>_<worker>` and `VALOR_TEST_PORTS` is cut into an equal slice
   per worker. Everything keyed on the database name (`LABEL`, `REPO`,
   the derived `_expiry`, `_serve`, `_rollout`, `_twice`, `_prio`
   databases, advisory locks, image labels) follows. `VALOR_PG_PORTS` and
   `VALOR_REDIS_PORTS` are assigned from the worker's slice, not
   `setdefault` from the controller's whole span.
3. `pytest_collection_modifyitems` gives every `container` test
   `xdist_group("container")`, so they run on one worker and build one
   base image.
4. With no `VALOR_TEST_PORTS` set, the suite picks a span of 20 ports per
   worker from 6600 up, so no run falls back to the fixed ranges two local
   page tests share.
5. The machine cluster's `max_connections` goes from 100 to 300, since ten
   workers each run kernels, bridges, and pools (32 connections were
   already in use).
6. The flaky `test_local_page::test_a_hostile_row_renders_with_nothing_live`
   (failed once under `-n 8`) waits for the page's render event instead of
   wall time.

Probe results with change 2 applied by hand: 119 tests 14.5 s serial,
11.1 s at `-n 4`; six mixed files 69.7 s serial, 27.5 s at `-n 8`; the
reap and slot tests 29.3 s to 13.8 s, with no worker killing another's
processes.

## Phase 3: the `container` group under eight minutes (about 35 min today)

1. **Keep the test base image across sessions.** Tag it by the hash of
   `core/images/base/Containerfile` and what it copies, not by session
   label; a session that finds the tag uses it, and only stale hashes are
   deleted. Saves the 4 to 5 minute build on every run but the first after
   a Containerfile change.
2. **Reuse dependency images** whose dependency key matches, instead of
   building them with `--no-cache` in each test.
3. **Reuse the base run.** Tests that verify a candidate pass the
   earlier base run's rows, so `reusable_base` skips the base VM, except
   the tests whose subject is the base run itself.
4. **One verification per module in `test_review.py`.** Its 22 tests
   assert different facts about the same kind of verification; a
   module-scoped verification serves the tests that only read it.
5. **The repository-suite test runs a narrow suite at head only.**
   `test_this_repositorys_suite_runs_in_the_vm_with_the_macos_tests_skipped`
   proves this repository's suite runs in the VM with the macOS tests
   skipped; a handful of files, one marked `macos`, proves that. The full
   suite in a VM runs in every real review already.
6. **The emulator metering test** (258 s) shares one driver step between
   its assertions.

If the group is still over eight minutes after these, the machine lock
takes a slot count from settings (one by default, matching Valor's 16 GB
Air) and the lead's Mac sets two, so two VMs run at once. That is a kernel
change and comes last.

## Phase 4: trim the remaining work (about 20 min serial, spread across workers)

1. **The hostile-config check.** `core/git.py:413` runs `git config
   --list` before every kernel git call. Cache its answer per workspace,
   keyed by the content hash of the config files it reads (never their
   modification times, which a turn controls). Same refusal, same cases;
   the existing hostile-config tests cover it.
2. **Template databases.** The `fresh` fixtures in `test_serve.py:63` and
   `test_rollout.py:43` and `world` in `test_expiry.py:41` migrate one
   template per session and create each database from it.
3. **Task Postgres from a template data directory.** Provisioning
   initdbs once per session into a template, and each task's cluster is an
   APFS clone with its port and passwords rewritten. Kernel code in
   `core/workspace.py`; it speeds real provisioning too.
4. **Module-scoped Telegram emulator** in the five files that start one
   per test, as `test_telegram_inbound.py` already does.
5. **Waits on events, not time.** The local page's poll interval becomes
   a setting the page is served with (tests set 100 ms); fixed sleeps in
   `test_email_kernel.py`, `test_objective_tree.py`, `test_workspace.py`,
   `test_kernel.py`, and `test_telegram_*` wait for tick events or ledger
   rows past a mark; `tests/emulator/common.py:92` polls the slot at a
   module constant the test lowers.

## Order and estimate

Phase 1 first: it removes failures every run pays for, and a parallel run
on broken tests only hides them. Phase 2 next, then phase 3, which
decides whether ten minutes is reached. Phase 4 items go in as measured
need shows, longest first.

| After | Non-container work, wall | `container` group, wall | Suite, wall |
|---|---|---|---|
| today | about 37 min | about 35 min | 72 min |
| phase 1 | about 25 min | about 35 min | about 60 min |
| phase 2 (`-n 10`) | about 4 min | about 35 min | about 35 min |
| phase 3 | about 4 min | about 6 to 8 min | about 8 min |
| phase 4 | about 3 min | about 6 to 8 min | about 8 min |

The estimates come from per-file timings and logs, not a full profiled
run. After phase 2 a single `--durations=50` run (then about 35 minutes)
measures the `container` group exactly, and phase 3 is sized from it.

## Done

- `uv run pytest -n auto --dist loadgroup` on the lead's Mac: the whole
  suite, `container` tests included, under ten minutes, three runs in a
  row, no failures, only the documented skips.
- `pytest` with no workers still passes, for the verification VM and the
  check profile (whose ten dev ports cannot be split across workers).
- `tests/README.md` states how to run the suite in parallel and why
  `container` tests share one worker; `.claude/skills/build/SKILL.md`'s
  suite line uses the parallel command, and each builder gets a port span
  of 200.
- No leaked process or database after a run, including a run whose tests
  fail.

## Leaves out

- Removing or weakening tests. Every test still asserts what it asserts.
- Mocks. Real Postgres, real servers, real VMs stay (`tests/README.md`).
- The kernel's own verification runs in a task's VM, which run the suite
  serially under the check profile. They get faster from phases 1 and 4,
  not from workers.
- A time limit anywhere.
