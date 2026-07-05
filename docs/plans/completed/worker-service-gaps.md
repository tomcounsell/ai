---
slug: worker-service-gaps
status: docs_complete
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/755
---

# Worker Service Gaps

## Problem

Three operational gaps in the worker service integration with `valor-service.sh`:

1. `uninstall` does not unload/remove `com.valor.worker` plist — worker stays auto-starting on boot.
2. `restart` only restarts the bridge/watchdog — worker keeps running stale code after edits to shared modules.
3. `config/newsyslog.valor.conf` does not include `worker.log` or `worker_error.log` — these grow unbounded.

## Appetite

Small (< 1 hour). Three localized, mechanical fixes.

## Solution

**Fix 1: `uninstall_service()`** — append a block mirroring the bridge/update/watchdog pattern to unload `WORKER_PLIST_PATH` and `rm -f` it. Also call `stop_worker` alongside `stop_bridge` at the end.

**Fix 2: `restart`** — change the top-level `restart` case to call `restart_bridge` followed by `restart_worker` so a single `./scripts/valor-service.sh restart` cycles both services.

**Fix 3: `newsyslog.valor.conf`** — add two new lines for `worker.log` and `worker_error.log` mirroring the existing entries (mode 644, count 5, 10240 KB, NJ flag).

**Fix 4: `CLAUDE.md`** — update the Quick Commands table entry for `restart` to read "Restart bridge, watchdog, and worker after code changes". The existing `worker-restart` row stays for targeted use.

## Step by Step Tasks

- [ ] Edit `scripts/valor-service.sh` `uninstall_service()` to also unload + remove the worker plist and call `stop_worker`.
- [ ] Edit `scripts/valor-service.sh` main `restart` case to call both `restart_bridge` and `restart_worker`.
- [ ] Edit `config/newsyslog.valor.conf` to add `worker.log` and `worker_error.log` rotation entries.
- [ ] Update `CLAUDE.md` Quick Commands table description for `restart`.
- [ ] Run `bash -n scripts/valor-service.sh` to syntax-check.
- [ ] Run `./scripts/valor-service.sh` with no args to confirm usage output renders cleanly.

## Success Criteria

- `./scripts/valor-service.sh uninstall` removes `com.valor.worker` plist.
- `./scripts/valor-service.sh restart` restarts bridge AND worker.
- `config/newsyslog.valor.conf` contains worker log entries.
- `CLAUDE.md` accurately describes the restart command.
- `bash -n scripts/valor-service.sh` exits 0.

## Risks

- Restarting the worker as part of `restart` interrupts in-flight sessions. This is acceptable — restart already does this for the bridge, and the standard expectation is that `restart` cycles all Valor services.

## No-Gos

- Do not introduce a new `restart-all` command — extend the existing `restart` instead, per Definition of Done #1 (no half-migrations).
- Do not change worker plist contents.

## Update System

No update system changes required — `scripts/remote-update.sh` already calls `valor-service.sh restart`, which will automatically pick up the new behavior. The newsyslog config is reinstalled by `install_service()`, and `/update` skill does not need changes.

## Agent Integration

No agent integration required — these are operational shell-script and config changes invisible to the agent and MCP layer.

## Failure Path Test Strategy

Manual smoke test: run `bash -n scripts/valor-service.sh` for syntax. Run `./scripts/valor-service.sh` with no args to verify usage prints. The functions modified (`uninstall_service`, the `restart` case) are idempotent and safe to re-run.

## Test Impact

No existing tests affected — `valor-service.sh` and `newsyslog.valor.conf` are operational shell/config artifacts with no automated test coverage in this repo. Validation is via `bash -n` syntax check and manual smoke run.

## Rabbit Holes

- Refactoring the entire service-management script to a Python tool. Out of scope.
- Adding launchd integration tests. Out of scope; no harness exists.

## Documentation

- [ ] Update `CLAUDE.md` Quick Commands table to reflect that `restart` covers bridge + watchdog + worker.
- [ ] No new feature doc needed — this is a bug fix to existing operational tooling.
