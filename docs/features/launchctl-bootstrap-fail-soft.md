# launchctl Bootstrap Fail-Soft

Shared launchd bootstrap helper that recovers the two distinct
`Bootstrap failed: 5: Input/output error` (errno-5 / EIO) failure shapes and,
for resident services, verifies the process actually came up live before
reporting success.

- Helper: `scripts/lib/launchctl.sh::launchctl_bootstrap_fail_soft`
- Python parity: `scripts/update/service.py::install_worker`
- Tracking issue: [#2104](https://github.com/tomcounsell/ai/issues/2104)
- Introduced by [#2013](https://github.com/tomcounsell/ai/issues/2013); errno-5
  reference pattern from #2017/PR #2018.

## The two errno-5 shapes

`launchctl bootstrap gui/<uid> <plist>` can return errno 5 even when the plist
is fine. There are two causes, and they need different recovery:

| Shape | Cause | Recovery |
|-------|-------|----------|
| **Drain race** | The label is still registered/draining in `gui/<uid>/` when `bootstrap` runs (right after a `bootout`, or a stale half-load from a prior crash). | `kickstart -k gui/<uid>/<label>` restarts the already-registered label. |
| **Fresh-install transient** | `bootstrap` hits a transient EIO but the label is **not** yet registered. `kickstart -k` is a no-op (nothing to kick). | Simply **retry the `bootstrap`** — the transient clears. This is what cleared the 2026-07-15 incident manually. |

Before #2104 the helper only handled the drain race, so the fresh-install
transient left the service DOWN. Additionally, a `bootstrap` exit 0 does not
prove the resident process actually spawned and stayed up.

## Loop A — bounded errno-5 bootstrap retry (before load)

The helper wraps `bootstrap` in a retry loop **gated on the transient EIO
shape** (`5: Input/output error` in captured stderr):

- Retry only on errno-5, sleeping `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` seconds
  between attempts, up to `LAUNCHCTL_BOOTSTRAP_RETRIES` total attempts.
- **Any other non-zero failure breaks out immediately** to the kickstart
  fallback — a genuine plist error is never masked behind N sleeps.

If loop A never loads the service, the helper falls back to a **single**
`kickstart -k` (the drain-race recovery, deliberately not retried). If that
also fails, it prints the distinct WARNING and returns 1.

## Loop B — opt-in live-PID verify (after load)

When a non-empty 4th `verify-pid` argument is passed, the helper runs a
**separate** bounded probe loop **after** the service is loaded:

- Re-runs `launchctl print gui/<uid>/<label>` and checks for a `pid = <N>` line,
  sleeping `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` between attempts, up to
  `LAUNCHCTL_BOOTSTRAP_RETRIES` times.
- **Loop B never re-invokes `bootstrap` or `kickstart`.** The label is already
  registered, so re-bootstrapping cannot reproduce errno-5 and re-kickstarting
  is explicitly forbidden (the drain race is single-shot). Loop B only waits out
  a slow-forking resident process.
- A persistently missing PID → the distinct WARNING + return 1.

The two loops are independent: (A) errno-5 bootstrap retry before load, then
(B) PID-wait probe after load.

## Resident vs scheduled (the opt-in rule)

Only **resident** services (RunAtLoad + KeepAlive, a persistent PID) pass
`verify-pid`. **Scheduled** services (StartCalendarInterval / StartInterval)
have no persistent PID between runs — a blanket PID check would falsely fail
every one of them, so they stay 3-arg.

| Call site | Service | plist trigger | Resident? | `verify-pid`? |
|-----------|---------|---------------|-----------|---------------|
| `install_worker.sh` (worker) | `com.valor.worker` | RunAtLoad + KeepAlive | yes | yes |
| `install_reflection_worker.sh` | `com.valor.reflection-worker` | RunAtLoad + KeepAlive | yes | yes |
| `install_email_bridge.sh` | `com.valor.email-bridge` | RunAtLoad + KeepAlive | yes | yes |
| `valor-service.sh` (bridge install) | `com.valor.bridge` | RunAtLoad + KeepAlive | yes | yes |
| `valor-service.sh` (worker-start) | `com.valor.worker` | RunAtLoad + KeepAlive | yes | yes |
| `install_worker.sh` (watchdog) | `com.valor.worker-watchdog` | StartInterval 300 | no | no |
| `valor-service.sh` (`bootstrap_plist_idempotent`, watchdog) | `com.valor.bridge-watchdog` | StartInterval 60 | no | no |
| `install_nightly_tests.sh` | `com.valor.nightly-tests` | StartCalendarInterval | no | no |
| `install_sdlc_reflection.sh` | `com.valor.sdlc-reflection` | StartInterval | no | no |
| `valor-service.sh` (`bootstrap_plist_idempotent`, update-cron) | `com.valor.update` | StartInterval | no | no |

The resident (verify-pid) set is exactly: **worker, bridge, reflection-worker,
email-bridge** — plus **worker-start**, which reuses the `com.valor.worker` label.
Everything else is 3-arg.

> **Correction to the original plan.** The #2104 plan's resident-vs-scheduled
> table mislabeled **both watchdogs** (`com.valor.worker-watchdog`,
> `com.valor.bridge-watchdog`) as resident. They are not: both are `StartInterval`
> one-shots (300 s and 60 s respectively) that hold **no persistent PID** between
> runs, so loop B's `launchctl print` would find no `pid =` line and falsely emit
> the WARNING — at `install_worker.sh`'s watchdog site that WARNING hits `|| exit 1`
> and would **abort a real worker install**. This build corrects the classification
> to honor the plan's own scheduled-service principle ("a blanket PID check would
> falsely fail every scheduled service — they have no persistent PID between runs").
> Because no caller of `bootstrap_plist_idempotent` is resident, that helper stays
> 3-arg (no `verify-pid` plumbing) — no dead code.

## Tunable constants

Both the shell helper and `service.py` read the **same env-var names with
identical defaults**. They are provisional/tunable install-time knobs (env
overrides, not `config/settings.py`).

| Env var | Default | Meaning |
|---------|---------|---------|
| `LAUNCHCTL_BOOTSTRAP_RETRIES` | `3` | Total bootstrap attempts (loop A) and PID probes (loop B). |
| `LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP` | `2` | Seconds slept between attempts (both loops). Set to `0` in tests. |

## Python parity — bootstrap retry only

`scripts/update/service.py::install_worker` is the `/update` reimplementation of
the worker install. It gains the **same bounded, errno-5-gated bootstrap retry
loop** (loop A), sharing the env-var names above.

Its live-PID check (`_launchctl_label_running`, which reads the PID column of
`launchctl list`) **stays single-shot by design** — parity covers the bootstrap
retry, not a PID re-probe loop. This is an **accepted, documented divergence**:
the shell probe (`launchctl print | grep 'pid = <N>'`) and the Python probe
(`launchctl list` PID column) are independently maintained. Only the
`LAUNCHCTL_BOOTSTRAP_*` env-var names/defaults must stay in sync between the two.

## Fail-loud contract (preserved)

The distinct, greppable line

```
WARNING: launchctl bootstrap+kickstart failed for <label>
```

still reaches stderr and the helper returns non-zero — emitted **only** on
genuine exhaustion of either loop (a real bootstrap+kickstart double-failure, or
a resident service that never came up live). Success is never masked.

## Tests

- `tests/unit/test_install_scripts_bootstrap.py` — the five `install_*.sh`
  helpers, including retry-then-succeed and PID-verification-failure cases, and
  the resident-emits-a-`print`-probe / scheduled-does-not assertion.
- `tests/unit/test_valor_service_bootstrap.py` — `valor-service.sh` bridge
  install, worker-start, and `bootstrap_plist_idempotent` (both the
  bridge-watchdog and update-cron labels are scheduled — neither gets a probe).
- `tests/unit/test_update_install_worker.py` — the Python `install_worker`
  bootstrap-retry + single-shot PID verify.
