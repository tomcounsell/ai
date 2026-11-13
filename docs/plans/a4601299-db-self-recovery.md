# Each Mac heals its own database, and the Macs watch each other

Task a46012997a42.

**Incident, 2026-11-13.** The 04:00 nightly upgraded postgresql@18 while it was running and deleted the old keg.

- **What failed.** Every new connection that loaded the plpgsql trigger failed (`UndefinedFile`, "could not load library"). From then on, no ledger write succeeded on any of the four Macs.
- **How long.** From 21:00 UTC on 12 Nov until 02:13 on the Captain, 09:08 on the Cowboy, and 09:21 on the Pirate and the Bald (the last two restarted by this task).
- **Why nothing recovered.** Every failure site retries forever, and Postgres's LaunchAgent restarts it only on a crash.
- **The restart cancelled turns.** When Postgres was restarted, the kernel's reconnect (`serve.py:1348/1357`, outside any `try`) crashed on "starting up", and that cancelled the running turns.
- **No message was lost.** The bridges exited on their dead lock connection, launchd restarted them, and gap fill retook every message. `telegram.log` on the Pirate and the Bald shows the fresh starts at 16:22 and 16:23 local.

**Stakes.** This changes the resident kernel and the nightly update on four Macs that are unattended for weeks.

## Build

1. **`core/health.py`.**
   - **`probe(dsn)`** opens a new connection and runs `DO $$BEGIN END$$`, which loads plpgsql. It does not use an old backend or `pg_isready`: both passed throughout the incident.
   - **`curable(exc)`** is true for SQLSTATE 58P01, for XX000 "could not load library", and for a refused or lost connection. It is false for 57P03 "starting up", which the kernel waits out.
   - **`restart_postgres()`** acts only when the stamp `<pg_data_dir>.valor-restart` is absent or more than 10 minutes old; it writes the stamp first, all under an exclusive `flock` on it. The stamp exists while the server is down, outlives kernel restarts, and is shared with `health --fix`, so two restarters at once restart once. The restart goes in three steps:
     1. `pg_ctl -D <pg_data_dir> -m fast stop -t 60`. A fast stop ends client sessions. The default smart stop would wait forever on the bridges and the kernel.
     2. If the fast stop fails, `-m immediate`.
     3. `launchctl kickstart` on the LaunchAgent whose program runs that data dir, or bootstrap it if it isn't loaded. Its program is the `opt/` path, so the server starts on the new keg.

     It does not use `brew services`, which waits on brew's lock.
2. **The kernel heals itself (`serve`).**
   - **What triggers a restart.** `probe` runs at start and on every `serve_tick_s` wake, on a new connection, so a failure at any site (a step's write, the gateway's, a bridge's, or none on a quiet Mac) is seen within a wake. Once probes have failed curably for 60 s (`health.Mend`), the kernel calls `restart_postgres()`.
   - **Start-up on a broken database.** The first connect, the kernel lock and `recover()` wait in that same loop instead of raising to launchd. Reconnects of `conn` and `listener` back off and never leave `serve`.
   - **Live work keeps its locks.** `router.step`'s `alive()` replaces a dead `holder` with a new connection and `pg_try_advisory_lock(run:<task>)`, and reports `lock lost` only when that is refused. `_Services.claim` does the same for `services:<task>`, and `Kernel.regain` calls it for every handle. A turn slot is not re-taken: for a moment after a restart one more turn may start than `max_turns`; nothing running is touched.
   - **After a restart: `db.restarted {cause, down_s, restarts}`** on stream `kernel`. Then a no-kill pass (`recover(live=<tasks with a job>)`) runs:
     - it never reaps, and never charges a gateway call at its estimate;
     - it ends a turn `interrupted` only when `runs`' process search (the one `reap` uses, without signalling) finds none of the turn's processes;
     - any other holder of a run, such as `python -m core run`, re-takes its lock on its next write.

     It skips collection for tasks in `live`. Handles that could not be re-claimed are dropped from `services`. Interrupted turns then resume or report through the existing paths. The bridges exit on their lost lock connection (`Outbox.due` raises out of `bridge.serve`) and launchd starts them; their gap fills retake a failed receive (`tests/test_telegram_wire.py`, `tests/test_slack_workspaces.py`).
3. **A Python move drains.**
   - When the resolved `sys.executable` changes, `Kernel.drain` holds new jobs (`schedule`), waits for the foreground ones, cancels background turns as a rollout does, writes `kernel.restarting {reason: python}` and raises `rollout.Restart`.
   - The next start (`kick_services`) restarts every other loaded `com.valor.*` job whose plist keeps it alive (the bridges, email, voice, the dashboard, caffeinate) and writes `kernel.kicked`. A routine's or the backup's job is not running between firings and starts on the new Python at its next.
   - The vault script's `kickstart -k` block is deleted only once this is live on all four Macs (Build 7).
4. **`python -m core health [--fix]`** prints JSON:
   - the probe result;
   - a pid for each loaded `com.valor.kernel*` job;
   - free disk.

   `--fix` calls `restart_postgres()` when the probe fails curably. It kickstarts only jobs that have no pid.
5. **The `watch` routine**, a new runner `watch` registered in `_routine_runners` (`routines/watch/runner.py`, deterministic, no agent turn and no task), runs every 30 minutes; its plist is placed by hand on the Cowboy and the Captain. `VALOR_WATCH` sets the targets: the Cowboy watches the Captain, the Pirate and the Bald; the Captain watches the Cowboy.
   - **Probe.** Each target is read with `ssh -o BatchMode=yes -o ConnectTimeout=10 <host> …python -m core health`.
   - **Healthy.** `~/.config/brew-nightly/hold-<host>` is deleted on both Macs.
   - **Broken.** The hold is written on both Macs. If the target was also broken at the previous watch, `health --fix` runs.
   - **Unreachable.** No hold is set.
   - **What it writes.** One `routine.ran` per run (as every routine does), plus a `watch.*` row only when a target's state changes: `held`, `fixed`, `failed` or `unreachable`.
6. **Telling Tom.** A fix that worked is only recorded. A target still broken after its fix, or unreachable for 24 h, reaches Tom once:
   - the Cowboy sends a `notices.request` on stream `watch` to the operator chat's session;
   - when the Captain is reporting on the Cowboy, it uses `sentry_sdk.capture_message(level="fatal")`.
7. **Vault script.** This sits outside the repo. Valor edits it by 2026-11-14, on top of whatever bdfbd371345e has landed. The edit:
   - skips upgrade and cleanup while any `hold-*` file exists;
   - holds the Cowboy's upgrade until 05:00;
   - runs `brew upgrade` with `HOMEBREW_NO_INSTALL_CLEANUP=1` (the upgrade's own cleanup deletes the old keg, 2026-10-23) and skips `brew cleanup` on a night Python moved, so a draining kernel and the services it restarts next never lose their interpreter; the next night's cleanup removes the old keg;
   - deletes the `kickstart -k` block after the rollout of this merge is confirmed on all four Macs.

**Choices.**
- **Probes, not Sentry errors.** The watch decides from probes; an arbitrary Sentry error has no automatic fix.
- **One watcher per Mac,** and a remote fix waits one watch and shares the spacing, so it never races self-heal.
- **Rejected:** Sentry heartbeats (they notice but fix nothing), an agent deciding fixes, and holds in iCloud.
- **Residual cases.** A full disk, a server that won't start, a crash loop, and a Python minor move (3.14 to 3.15: the venv breaks and needs `uv sync`) each get one fix attempt and are then reported. If the Cowboy and the Captain are both down, Sentry is the only report.

## Governance

The two holds are governance:
- the Cowboy skipping its update while a peer is broken;
- a broken Mac skipping its own update.

**Grant.** Tom, valor team chat, #see-issue-63, 2026-11-13, items 4 and 5 of his design:
- "what if the cowboy machine updated last? … The cowboy is the one that has tailscale access to help the others";
- "i'm also trying to prevent getting me involved at all."

**Incident.** On 2026-11-13 the upgrade broke every Mac's database for 5 to 12 hours.

**Mission item.** 6, under the constraint "reliable stop, recovery, and correction".

**Ledger.** Guard `nightly.hold` goes in `guards.SEEDED`, expiring 90 days after merge. It fires as `watch.held`.

Nothing else here limits anything: it is all recovery on paths that already exist.

**Out of scope.** Preventing the upgrade (bdfbd371345e), task-workspace clusters, and Slack reactions past Slack's retries.

## Tests

All in `tests/test_health.py`; the existing serve, reap, rollout and orchestration suites stay green.
- **Restart mechanics:** `curable` for each SQLSTATE; fast stop then kickstart, the immediate fallback, bootstrap of an unloaded job (stub `pg_ctl`, `launchctl`); the stamp's spacing with no `postmaster.pid`; two concurrent restarters restart once; `--fix` starts only services with no pid.
- **The incident, reproduced:** a trigger on `events` and the probe raise SQLSTATE 58P01 with the 2026-11-13 message; a ledger write fails with `UndefinedFile`, the kernel restarts the database once after 60 s (the stand-in ends every session, as a restart does), reconnects, writes `db.restarted`, and its next writes land. A quiet Mac whose old connection works is restarted by the probe. A healthy database, or one only "starting up", never is. A kernel started on a refusing database restarts it and comes up.
- **Live work:** a step whose sessions were all ended takes its run lock again and finishes; a services handle takes its lock again; the no-kill `recover` ends only the turn with no process, leaves a turn of another process (a live `VALOR_TURN` process) and a live job alone, and charges no call.
- **Bridges:** a bridge whose database sessions were ended leaves `bridge.serve` for launchd.
- **Python move:** the kernel holds new jobs while one runs, then restarts; the next start restarts every other service once.
- **Watch:** healthy, held, fixed a watch later, failed and reported once, unreachable holds nothing and is reported once after a day, rows only on a change, a report with no session to hear it is one fatal Sentry event.
- **Live proof** (after rollout), on the Cowboy with no other turn running: restart with live clients, timed; stop Postgres, send a Slack DM, show `db.restarted` and the reply; run `watch` once. It proves the refused-connection path; the plpgsql path is the unit test's.
