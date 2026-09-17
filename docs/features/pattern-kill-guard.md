# Pattern Kill Guard

The PreToolUse hook validator (`.claude/hooks/validators/validate_no_broad_process_kill.py`)
that blocks machine-wide, pattern-matched kills before they execute. It started narrow
(test runners only, issue #2562) and now also covers the long-lived shared services
(issue #3316): a reviewer stopping a throwaway dashboard with
`pkill -f "python -m ui.app"` killed the production dashboard on port 8500, because the
pattern carries no port, no PID, and no scoping.

## Blocked kill shapes

Four verb shapes, parameterized over the target pattern:

- `kill`/`killall` taking targets from a `pgrep` substitution (`kill -9 $(pgrep -f ...)`, backtick form)
- `pkill` matching a pattern directly (`pkill -f ...`)
- `killall` by process name (`killall ...`)
- `pgrep` piped into a killer (`pgrep -f ... | xargs kill`)

## Target tables

Test runners (`_TEST_RUNNER_PATTERN`): `pytest`, `py.test`, `xdist`, `pytest-clean`.
The block reason names the sanctioned sweep `scripts/reap-xdist.sh`, which checks parent
liveness before killing. The sweep itself (`_SANCTIONED`) is exempt so it is never blocked.

Long-lived services (`_SERVICES`, one row per service: display name, plain substring
alternatives, sanctioned stop path):

| Service | Production command line | Sanctioned stop |
|---------|------------------------|-----------------|
| Dashboard | `python -m ui.app` | `scripts/valor-service.sh restart` (`valor-service.sh` has no dashboard-only stop verb; `stop` only stops the Telegram bridge) |
| Worker | `python -m worker` | `worker-stop` (`worker-disable` to keep it down) |
| Telegram bridge | `bridge/telegram_bridge.py` | `scripts/valor-service.sh stop` |
| Email bridge | `python -m bridge.email_bridge` | `email-stop` (`email-disable` to keep it down) |
| Watchdog | `monitoring/worker_watchdog.py` (plus `worker-watchdog` launchd-label alias) | `launchctl bootout gui/$(id -u)/com.valor.worker-watchdog` (an independent launchd job, not managed by `valor-service.sh`) |
| Reflection worker | `python -m reflections` (plus `reflection_worker` alias) | `launchctl bootout gui/$(id -u)/com.valor.reflection-worker` |

Adding the next service is one table row, not a new regex. The alternatives stay literal
substrings on purpose: process-name matching has platform edge cases, so there is no
clever regex. Bare `worker` is deliberately never matched: it is a substring of too many
innocent strings (`homework`, `coworker`), so only service-shaped forms (`-m worker`, the
watchdog path and labels) block. The one exception is `killall`, which takes a process
name rather than a command-line substring: a standalone `killall worker` token names the
service itself, so it blocks while `killall my-worker` and `killall CoWorker` stay allowed
-- and its reason is routed to the worker service's own stop path explicitly, not the
generic "shared service" fallback.

## Reason routing

`find_violation` checks test-runner shapes first (keeping the original pytest reason
byte-for-byte), then the service table. The `reap-xdist.sh` exemption is scoped to the
test-runner branch only: a command mentioning it is never exempted from the service
guard, so `scripts/reap-xdist.sh --apply && pkill -f "python -m ui.app"` still blocks.
A service block names that service's sanctioned stop path plus the kill-by-PID rule
for throwaway instances: find the PID with read-only inspection (`pgrep -af`, `ps`, both
always allowed) and `kill <pid>`. Never clear processes by pattern.

## Enforcement path

Agent issues a Bash command, the `pre_tool_use_bash.py` dispatcher runs
`find_violation` before execution, and a returned reason string blocks the command while
`None` lets it through. Dispatcher wiring, hook registration, and the allow/block contract
are untouched by the widening. `find_violation` is a synchronous pure-string predicate:
a malformed command matches nothing and is allowed through, which is the safe default for
a blocklist.

## Verification

`scripts/pytest-clean.sh tests/unit/test_validate_no_broad_process_kill.py -q` covers one
BLOCKED row per service per kill shape, PID-kill and read-only negatives, bare-`worker`
near-miss negatives, sanctioned-stop ALLOWED rows, and a reason-content assertion per
service mirroring the existing `reap-xdist.sh` assertion. The dispatcher end-to-end test
in `tests/unit/test_pre_tool_use_dispatcher.py` keeps passing as the wiring regression guard.
