---
title: Ancestor-safe service PID lookup (replace pgrep in Python service probes)
slug: ancestor-safe-service-pid-lookup
type: bug
status: Ready
appetite: Small
owner: Valor Engels
created: 2026-09-05
revised: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3164
revision_applied: true
---

# Ancestor-safe service PID lookup

## Problem

`/update` on Valor the Captain prints, at HEAD `45d5d42d4`, with a healthy
bridge and a correct fresh beacon:

```
WARNING: bridge release could not be confirmed (unknown) — not failing the run
release verify OK @ 45d5d42d4 (bridge unknown, worker matches)
```

`scripts/update/service.py::get_bridge_pid()` shells out to
`pgrep -f telegram_bridge.py`. On macOS, BSD `pgrep` **excludes the calling
process and all of its ancestors** from the match list unless `-a` is passed.
Agent sessions run as `claude -p` children of the bridge, so when a session
runs `/update`, the bridge is an ancestor and `pgrep` reports nothing.
`_classify_process()` returns `unknown` as soon as `pid is None` — before it
ever reads the beacon — so a healthy, correctly-beaconed bridge classifies as
`unknown`.

Verified on the machine: `pgrep -f telegram_bridge.py` exits 1 while the
bridge (pid 94804) is present in `KERN_PROC_ALL` with ordinary flags, and
`pgrep -a -f telegram_bridge.py` finds it. The ancestry is direct:

```
96594 zsh                                                      <- the caller
95415 claude -p ...                                            <- the session
94804 .../Python /Users/.../ai/bridge/telegram_bridge.py       <- the bridge
    1 /sbin/launchd
```

The same defect is present in every Python probe that resolves a long-lived
service PID via `pgrep`. It is latent for the watchdogs (launchd jobs, never
descendants today) but live for anything an agent session runs.

## No-Gos

- **`kill_stale_processes()` must NOT be converted.**
  `monitoring/bridge_watchdog.py:694` runs the same
  `pgrep -f telegram_bridge.py` and then `os.kill(pid, 9)` on every match
  (callers: the recovery ladder at lines 836, 843, 855). BSD `pgrep`'s
  ancestor exclusion is *accidentally load-bearing* there: an ancestor-safe
  lookup in a SIGKILL path lets a bridge-descended caller kill its own live
  ancestor bridge — fratricide the current code structurally cannot commit.
  Ancestor-inclusive matching is unsafe in any `os.kill` path. A future
  builder must not "finish the sweep" here. The call site gets a comment
  saying exactly this.
- **Shell probes stay on `pgrep`.** `scripts/valor-service.sh`,
  `scripts/remote-update.sh`, `scripts/start_bridge.sh` and
  `scripts/install_email_bridge.sh` keep their current mechanism. Their test
  harnesses shadow `pgrep` on `PATH` precisely so the sandbox cannot read — or
  `kill` against — the developer's real process table
  (`tests/unit/test_valor_service_bootstrap.py:126-171`,
  `tests/unit/test_remote_update_shell.py:76-86`). Converting them to a `ps`
  scan removes that isolation and needs its own plan. Follow-up on #3164.
- **`pgrep -a` is rejected.** On Linux/procps `-a` means "print the full
  command line", so the flag silently changes meaning off macOS.
- **`scripts/update/service.py:779`'s `run_cmd(["pgrep", "caffeinate"])`
  stays.** It matches by process *name* (no `-f`), which the new helper's
  python-interpreter precondition cannot express, and `caffeinate` is a
  launchd job that is never an ancestor of an agent session. Its survival in a
  file this plan otherwise converts is deliberate, not an oversight.
- **`tools/agent_session_scheduler.py:1059`'s `pgrep -f <session_id>` stays.**
  It matches an arbitrary substring against any process, not a Python service,
  so it does not fit the shared helper's contract. It has the same ancestor
  defect; noted as a follow-up on #3164, not fixed here.

## Design

New module `tools/process_lookup.py` — a single ancestor-safe lookup used by
every Python service-PID probe. `tools/` over `monitoring/` because three of
the five consumers live outside `monitoring/`, and `tools/` is already the
repo's home for shared non-CLI library helpers (`tools/pr_head_resolver.py`).

```python
def list_processes() -> list[tuple[int, list[str]]]
def find_python_service_pids(*, module=None, script_suffix=None) -> list[int]
```

- Reads `ps -axo pid=,args=`. `ps` has no ancestor filter, so a descendant
  sees its own ancestors.
- Matching is **tokenised**, not substring. A candidate must satisfy both:
  1. `os.path.basename(argv[0]).lower().startswith("python")` — this covers
     `python`, `python3.14`, and the launchd-spawned framework binary `Python`
     (capital P), which is what `pgrep -fi`'s `-i` flag was working around.
  2. either an adjacent `["-m", module]` pair appears in argv, or some argv
     token after `argv[0]` is a path equal to `script_suffix` or ending in
     `"/" + script_suffix`.
- Tokenised matching is what kills the `ps | grep` false positive: a
  `zsh -c '... telegram_bridge.py ...'` decoy fails the interpreter test, and a
  `python -c "print('telegram_bridge.py')"` decoy fails the token test because
  the payload is the single value token of `-c`, not a standalone path token.
- Returns PIDs sorted ascending; callers take the first, matching the previous
  `pgrep` ordering.
- Never raises: any failure returns an empty list, so callers degrade to
  today's `None`/`unknown` behaviour rather than to a wrong PID.

**Why latent-today sites are converted too.** The defect is a function of
*caller context*, not of the probe, and caller context here is not static: the
bridge and the worker both spawn agent sessions, and those sessions already run
`tools/doctor.py` (which calls `is_bridge_running`), `/update`, and the UI's
health endpoints. Leaving three probes on `pgrep` means the next time a caller
moves under a service — the same move that produced this bug — it reappears
silently in a path that SIGKILLs a worker (`worker_watchdog`, the #1331 failure
mode). The conversion is a mechanical one-line swap per site, so the marginal
cost of doing all of them is far below the cost of a second silent recurrence.

Known limitation, documented in the module: `ps -o args=` returns one string,
so argv is recovered by whitespace split. A service path containing a space
would not match. No path in this repo's launchd plists contains one.

## Changes

1. **New** `tools/process_lookup.py` as above.
2. `scripts/update/service.py` — `get_bridge_pid`, `get_worker_pid`,
   `get_email_pid` reimplemented on the helper. Signatures and return contracts
   unchanged.
3. `monitoring/bridge_watchdog.py::is_bridge_running` — same, keeps its
   `(bool, int | None)` signature. `kill_stale_processes()` is left on `pgrep`
   and gains a comment recording why (see No-Gos).
4. `monitoring/worker_watchdog.py::_get_worker_pid` — same. Its stale
   `-i`-rationale comment at lines 173-174 is rewritten in the same commit.
5. `ui/app.py` email-bridge liveness probe — same.
6. `tests/_worker_guard.py::_pgrep_worker_pids` — converted. pytest run inside
   a worker-hosted agent session is a descendant of the launchd worker, so this
   safety guard's host-level signal is dead exactly when it matters (#2146 /
   #2147). Renamed to `_host_worker_pids`; its `subprocess_env()` workaround is
   dropped because the new argv (`ps -axo ...`) contains no `python` token for
   the isolation guard's heuristic to trip on.

## Update System

No update-system changes required. `scripts/remote-update.sh` and the `/update`
skill are unchanged; the fix is entirely inside the Python probes that
`scripts/update/service.py` already exposes, and `python -m
scripts.update.verify_release` keeps its current CLI contract, exit codes and
output strings. No new dependency, config file, or migration step. The change
takes effect on the next `/update` pull with no operator action.

## Agent Integration

No agent integration required. `tools/process_lookup.py` is a library imported
by existing callers, not an agent-facing surface: no new
`pyproject.toml [project.scripts]` entry, and the bridge does not import it
directly. The agent already reaches this code path through the existing
`python -m scripts.update.verify_release` and `python -m tools.doctor`
entry points, whose behaviour is what this plan fixes.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` §3 Health Checks bullet
      (currently "Process running (`pgrep -f telegram_bridge.py`)"), §18 Worker
      Watchdog (the `pgrep` poll reference at ~line 499), and §20 Update
      Release Verification — the `unknown` row of the classification table must
      say that "no PID" is a lookup result, and record that the lookup is
      ancestor-safe so a bridge-hosted caller no longer produces a false
      `unknown`.
- [ ] Update `docs/features/worker-service.md` (~line 157) where
      `is_worker_running()` is listed in the Python API surface.
- [ ] Add the new module to `docs/tools-reference.md` if that file enumerates
      `tools/` library modules; otherwise no entry.

## Failure Path Test Strategy

The helper's only failure mode is "cannot read the process table". Every
failure path returns an empty list rather than raising or guessing, so each
caller falls back to its existing "not running" branch — the same state
`pgrep` produced on failure today. Tests drive `ps` non-zero exit, `ps`
raising, an empty table, and a garbage line interleaved with a valid one
(the valid line must still resolve).

## Test Impact

- [ ] `tests/unit/test_update_release_verify.py` — no change: the
      `live_processes` fixture (lines 93-100) monkeypatches `get_bridge_pid` /
      `get_worker_pid` as function objects, and the CLI tests patch
      `verify_running_release` wholesale, so they never reach the probe layer.
      Re-run as regression proof.
- [ ] `tests/unit/test_bridge_watchdog.py` — no change: all ten sites patch
      `monitoring.bridge_watchdog.is_bridge_running` as a function object. The
      `patch("monitoring.bridge_watchdog.subprocess.run")` decorators in
      `TestEnumerateClaudeProcesses` target `_enumerate_claude_processes`, not
      the probe. Re-run as regression proof.
- [ ] `tests/unit/test_worker_watchdog.py` — no change: lines 477/481 patch
      `wwd._get_worker_pid` directly. Its global `patch("subprocess.run")`
      sites are scoped to `_is_operator_disabled` / `_kickstart_worker` /
      `_enable_worker` and never reach `_get_worker_pid`. Re-run.
- [ ] `tests/unit/test_migrate_session_type.py` — VERIFY: the only file that
      blanket-patches `subprocess.run` with a single `returncode=1` mock
      (lines 143, 185, 224, 248, 300, 353, 391). A `ps` call returns the same
      `returncode=1` / empty stdout, so it still resolves to "not running", but
      this must be proven by running the file, not assumed.
- [ ] `tests/integration/test_watchdog_recovery.py` — UPDATE (docs only): its
      module docstring (lines 9-13) and the `#2147 service-isolation audit`
      block describe `_get_worker_pid()` as running `pgrep -f "python -m
      worker"`; both go stale. `_spawn_fake_worker` spawns
      `python -c "...sys.argv[0] = '-m'..."`, whose OS-level argv has no
      adjacent `-m worker` pair (assigning `sys.argv` does not rewrite process
      argv), so tokenised matching will not match it — the same non-match
      `pgrep` produces today, and the `("starting", "down")` assertion is
      unaffected. Text update only, no assertion change.
- [ ] `tests/unit/test_valor_service_bootstrap.py`,
      `tests/unit/test_remote_update_shell.py`,
      `tests/unit/test_install_scripts_bootstrap.py` — no change: they stub
      `pgrep` on `PATH` for the shell scripts, which this plan does not touch
      (see No-Gos).
- [ ] **NEW** `tests/unit/test_process_lookup.py` — see Test plan.

## Test plan

New `tests/unit/test_process_lookup.py`, driven by synthetic `ps` output so it
is host-independent:

- launchd framework-Python bridge line (`.../MacOS/Python /abs/path/bridge/telegram_bridge.py`)
  resolves to the bridge PID.
- `python -m worker` resolves via `module="worker"`;
  `python /abs/path/worker/__main__.py` resolves via
  `script_suffix="worker/__main__.py"`.
- `zsh -c '... telegram_bridge.py ...'` decoy is **not** returned.
- `grep telegram_bridge.py` decoy is **not** returned.
- `python -c "...telegram_bridge.py..."` decoy is **not** returned.
- `python -m workerfoo` does **not** satisfy `module="worker"` (token equality,
  not prefix).
- Multiple matches return ascending PIDs.
- `ps` non-zero exit, `ps` raising, empty output, and a garbage line beside a
  valid line: never raises; the valid line still resolves.
- Caller-independence: the same synthetic table yields the same result
  regardless of the calling process — the property `pgrep` violated. Asserted
  by construction (the helper takes no caller identity as input) plus a live
  test that the real lookup finds a PID that `os.getpid()`'s own ancestor
  chain contains, which `pgrep` cannot.
- `scripts.update.service.get_bridge_pid` returns `None`, not a wrong PID,
  when the lookup finds nothing.

## Verification on the machine

1. **Bridge-descended caller (the reported bug).** Re-run
   `python -m scripts.update.verify_release` from this bridge-hosted session.
   Before: `bridge unknown`. After: `release verify OK @ <sha> (bridge matches,
   worker matches)`.
2. **Non-descendant caller (the cron path, must not regress).** Run the same
   command under a launchd-parented process so the bridge is not an ancestor:
   `launchctl submit -l tmp.verify-release -- <venv-python> -m
   scripts.update.verify_release`, capture its output, then
   `launchctl remove tmp.verify-release`. Must also print `bridge matches`.
   A transient label is used so no plist is installed and nothing survives the
   check.

## Rabbit Holes

- Do not try to recover a true argv array (`KERN_PROCARGS2`, `psutil`). `ps`
  plus whitespace tokenisation is sufficient for every path this repo runs, and
  a sysctl-based reader would be a new native-code dependency for no behaviour
  change.
- Do not unify the shell probes in this PR (see No-Gos).
- Do not "fix" `kill_stale_processes()` (see No-Gos).

## Acceptance criteria

- Release verify reports `bridge matches` from a bridge-descended caller and
  from a launchd-parented (cron-equivalent) caller.
- No probe can return the PID of a process that merely mentions the pattern.
- No behavioural change for callers: same signatures, same `None`-on-failure.
- `kill_stale_processes()` still uses `pgrep` and carries a comment explaining
  why.

## Critique Results

| Severity | Critic(s) | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Structural check; History & Consistency | The plan omits all four sections this repo mandates on every plan doc — `## Documentation`, `## Update System`, `## Agent Integration`, `## Test Impact` — and has no `## Critique Results` section of its own. Its section list is exactly: Problem, Non-goals, Design, Changes, Test plan, Verification on the machine, Acceptance criteria. | revision (2026-09-05) | `docs/sdlc/do-plan.md` requires all four, enforced by `.claude/hooks/validators/validate_documentation_section.py` and `validate_test_impact_section.py`. `## Documentation` needs at least one `- [ ]` checkbox naming a `docs/features/` path (the plan's Changes item 6 already names `bridge-self-healing.md` and `worker-service.md` — restate them as checkboxes). `## Update System` and `## Agent Integration` can both be explicit "no changes required" statements, but must exist as headers. `## Test Impact` needs UPDATE/DELETE/REPLACE dispositions, not the prose list currently under `## Test plan`. |
| BLOCKER | History & Consistency; Risk & Robustness | `monitoring/bridge_watchdog.py` contains a SECOND `pgrep -f telegram_bridge.py` call site — `kill_stale_processes()` at line 694 — that the plan neither converts (Changes item 3 names only `is_bridge_running`, line 191) nor carves out in `## Non-goals`. The Problem section's claim that "the same defect is present in every Python probe that resolves a long-lived service PID via `pgrep`" is contradicted by the Changes list inside a single file the plan edits. | revision (2026-09-05) | This site must be carved out EXPLICITLY, not converted. `kill_stale_processes()` does `os.kill(pid, 9)` on every match and is called from `monitoring/bridge_watchdog.py:836,843,855` in the recovery ladder. BSD `pgrep`'s ancestor exclusion is accidentally load-bearing there: swapping in an ancestor-safe lookup means a bridge-descended caller (an agent session running a recovery path) would SIGKILL its own live ancestor bridge — fratricide the current code cannot commit. Add a `## Non-goals` bullet naming `kill_stale_processes()` and stating that ancestor-INCLUSIVE matching is unsafe in any `os.kill` path, so a future builder does not "finish the sweep". |
| CONCERN | Scope & Value | For an `appetite: Small` bug fix the Changes list converts three call sites the issue's own blast-radius table marks "latent" (`bridge_watchdog.py::is_bridge_running`, `worker_watchdog.py::_get_worker_pid`, `ui/app.py`'s email probe — watchdogs are launchd jobs and never bridge descendants), while `## Non-goals` defers other equally-unaffected sites for scope discipline. It also creates the repo's first `scripts/ -> monitoring/` import dependency plus a new `ui/ -> monitoring/` one, to fix a warning line the plan itself notes is "not failing the run". | revision (2026-09-05) | Verified: `scripts/update/` imports nothing from `monitoring/` today (zero hits). Either (a) scope Changes to `scripts/update/service.py` and defer items 3-5 as follow-ups on #3164, or (b) keep the full sweep but state the rationale explicitly in `## Design` — that latent-today sites are converted so the *next* caller context change cannot silently reintroduce the bug. Whichever is chosen, if the shared helper stays, consider `tools/process_lookup.py` over `monitoring/process_lookup.py`: three of its four consumers live outside `monitoring/`. |
| CONCERN | Risk & Robustness; History & Consistency; Structural check | The `## Test plan` inventory of existing suites is inaccurate and incomplete. "all patch the function objects, not `subprocess`" is false — `tests/unit/test_worker_watchdog.py` carries global `patch("subprocess.run", ...)` calls. More importantly the list omits `tests/integration/test_watchdog_recovery.py`, which calls the REAL unmocked `check()` -> `_get_worker_pid()` and whose `_spawn_fake_worker` fixture and module docstring are both built on `pgrep -f` substring semantics. | revision (2026-09-05) | Verified correction: the global `patch("subprocess.run")` sites in `test_worker_watchdog.py` are scoped to `_is_operator_disabled` / `_kickstart_worker` / `_enable_worker` and never reach `_get_worker_pid`, so they do not break — but the plan's blanket claim is still wrong and must be restated precisely. `tests/integration/test_watchdog_recovery.py::test_check_reports_healthy_while_worker_runs` calls `check()` unmocked; `_spawn_fake_worker` spawns `python -c "import sys, time; sys.argv[0] = '-m'; sys.argv[1:] = ['worker']; ..."`, whose OS-level argv has NO adjacent `-m worker` pair, so tokenised matching will never match it (assigning `sys.argv` does not rewrite the process argv). The test's `("starting", "down")` assertion still passes, but the module docstring and the `#2147 service-isolation audit` block become factually stale and must be updated in the same PR. |
| CONCERN | Structural check | `tests/_worker_guard.py::_pgrep_worker_pids()` runs `pgrep -f "python -m worker"` — a Python service-PID probe with the exact ancestor-exclusion defect this plan exists to fix — and is neither converted nor listed in `## Non-goals`. It is safety-critical: the guard exists because the unattended suite killed the live production worker on 2026-07-17 (#2146/#2147). | revision (2026-09-05) | pytest run inside a worker-hosted agent session IS a descendant of the launchd worker, so `_pgrep_worker_pids()` returns an empty set exactly when the guard matters most. Not fail-open on its own — `assert_not_live_worker` also checks `_pid_cmdline(pid)` via `ps -ww -p <pid> -o command=` and the Redis `worker:registered_pid:*` keys, and either alone is sufficient — but one of three independent signals is silently dead. Convert it to `find_python_service_pids(module="worker")` or add a `## Non-goals` bullet stating the redundancy argument explicitly. |
| CONCERN | Risk & Robustness | `## Acceptance criteria` requires `bridge matches` "from a bridge-descended caller **and from cron**", but `## Verification on the machine` only re-runs `verify_release` from the bridge-hosted session. The cron/launchd path — the one that works today and must not regress — has no verification step. | revision (2026-09-05) | Add a second verification invocation whose parent is not the bridge, e.g. `launchctl kickstart -k gui/$(id -u)/com.valor.update` or a `setsid`/detached shell, and record its `bridge matches` output. Cheapest in-test guard: assert `os.getppid()` chain does not contain the bridge PID before treating a run as the cron-path proof. `tests/unit/test_process_lookup.py` as listed has no non-descendant-caller case either. |
| NIT | Structural check | `scripts/update/service.py:779` still calls `run_cmd(["pgrep", "caffeinate"])` after the rewrite, so the file the plan describes as fully converted retains a `pgrep` probe. | revision (2026-09-05) | Harmless — `caffeinate` is a launchd job and never an ancestor of an agent session, and it is matched by process NAME (no `-f`), which the new helper's python-interpreter precondition cannot express. Worth one sentence in `## Non-goals` so the remaining call is not read as an oversight. |
| NIT | Structural check | Two in-code comments describe the `pgrep` mechanism and go stale on merge: `monitoring/worker_watchdog.py:173-174` ("Use case-insensitive flag (-i) to match both `python` and `Python`") and the `tests/integration/test_watchdog_recovery.py` module docstring ("detects a gone process by calling `_get_worker_pid()`, which runs `pgrep -f "python -m worker"`"). Changes item 6 lists only `docs/features/*.md`. | revision (2026-09-05) | N/A (NIT) — fold the docstring/comment updates into the same commit as the code change rather than tracking them separately. |
