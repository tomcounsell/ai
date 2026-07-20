---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2161
last_comment_id:
---

# install_worker: Injection-Aware Idempotency + Drain Before Bootout

## Problem

`scripts/update/run.py:1475` calls `service.install_worker()` every cron
run. Its idempotency check (`service.py:~421`) compares the on-disk plist
(which HAS `.env`-injected `EnvironmentVariables`) with the freshly-rendered
template (which does NOT) — never equal, so every run executes
bootout+bootstrap: an unconditional worker restart bypassing the #2141
drain. Three same-day restarts (10:13, 11:31, 15:23 UTC 2026-07-18) each
killed or endangered an in-flight PM turn; 15:23 killed session
`0_1784334995307`'s TEST turn and orphaned two pytest-clean runs.

## Freshness Check

Baseline f1cec2d2+ (main). Verified live: `service.py:395-409` "harmless"
comment; compare at `:421` (`existing_text == plist_text`);
`_inject_env_into_plist` semantics = only-add-missing-keys, skip None
values; `run.py:1475` unconditional call; update.log restarts with no
drain/skip lines.

## Prior Art

#2141 (merged 55c40990) gated the SHELL restart path and added
`scripts/update/drain.py` + `worker/shutdown_cleanup.py`. This closes the
remaining Python-path hole using the same drain module.

## Solution

In `install_worker`:

1. **Injection-aware idempotency**: compute the EXPECTED final plist dict —
   `plistlib.loads(rendered_template)` with `_inject_env_into_plist`'s exact
   semantics applied in-memory (add dotenv keys not already present, skip
   None values; dotenv/.env missing → template dict unchanged). Compare
   against `plistlib.loads(existing_bytes)`. Equal AND already-loaded →
   return True with NO bootout. Any parse failure → fall through to the
   current rebuild path (fail-open to restart, never to a wedged update).
2. **Drain before bootout** (parity with #2141's shell path): when a
   restart is genuinely needed AND the worker is already loaded, call
   `scripts.update.drain.wait_for_idle(UPDATE_WORKER_DRAIN_TIMEOUT_S,
   UPDATE_WORKER_DRAIN_POLL_S)`; on busy-timeout, log
   `install_worker: restart DEFERRED (running sessions) — retrying next
   update cycle` and return True WITHOUT bootout (the worker keeps serving
   on the old plist/code; next cycle retries). Drain errors fail open
   (restart proceeds) — same contract as the module.
3. Replace the `:395-409` "harmless" comment with the real semantics.

First-install (no existing plist / not loaded) is unchanged: no drain
needed (nothing running under a dead/absent worker), bootstrap proceeds.

## No-Gos

- No change to `_inject_env_into_plist` file-mutation behavior (still used
  post-write); the in-memory application is a parallel pure computation.
- No change to the bootstrap/kickstart EIO retry ladder.
- No new env knobs (reuses the #2141 pair).

## Update System

This IS an update-system change; propagates via normal git pull. The fix
takes effect the first run AFTER the pull that delivers it (the Python
module is imported fresh each run).

## Agent Integration

No agent integration required — update-pipeline internal.

## Failure Path Test Strategy

- plist parse failure (corrupt on-disk file) → falls through to rebuild
  (restart), never raises.
- dotenv import/read failure → expected dict = template dict (matches
  injection's own no-op), comparison still meaningful.
- drain probe raises → fail-open restart (module contract, already tested).

## Test Impact

- [ ] `tests/unit/test_update_install_worker.py` — UPDATE: existing
  `TestInstallWorkerEnvInjection` cases keep passing (first-install path
  unchanged); ADD `TestInstallWorkerIdempotency`: unchanged template +
  unchanged .env + loaded worker → returns True, NO bootout/bootstrap
  run_cmd calls; changed .env key → restart path taken; drain-busy →
  deferred (no bootout) with loud log; drain-idle → bootout proceeds.

## Rabbit Holes

- Don't normalize plist key ordering manually — `plistlib.loads` dict
  equality is order-independent by construction.
- Don't try to unify with the shell path's logic; the shell section stays
  as the label-liveness/kickstart owner.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` § "Update
  restart semantics for in-flight sessions (#2141)": add a paragraph
  documenting that BOTH restart paths are now gated — the shell path
  (diff gate + drain + pgrep liveness) AND `service.install_worker()`
  (injection-aware plist idempotency: expected-final-plist comparison,
  drain-before-bootout, defer-on-busy). State explicitly that a healthy
  loaded worker with an unchanged template and unchanged `.env` is never
  cycled by either path.
- [ ] Update `docs/features/config-timeout-catalog.md` § "Update-restart
  drain knobs (issue #2141)": note that `UPDATE_WORKER_DRAIN_TIMEOUT_S` /
  `UPDATE_WORKER_DRAIN_POLL_S` are consumed by `scripts/update/service.py::
  install_worker` as well as `remote-update.sh` (add the consumer to the
  table's "Used by" column).

## Success Criteria

- [ ] Unchanged cycle (same template, same .env, worker loaded) → no
  bootout, update.log shows no 'Worker restarted' (acceptance 1+3)
- [ ] Genuine change → drain first; busy → defer with loud log (acceptance 2)
- [ ] Existing #1171 injection tests still pass

## Verification

1. `pytest tests/unit/test_update_install_worker.py -n0`
2. Live: next two cron cycles on this machine log zero worker restarts
   while nothing changed.
