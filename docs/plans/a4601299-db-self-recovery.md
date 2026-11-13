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
   - **`restart_postgres()`** acts only when `<pg_data_dir>/postmaster.pid` is absent or more than 10 minutes old. That file is the server's own start record, so the spacing holds across kernel restarts and is shared with a remote fix. The restart goes in three steps:
     1. `pg_ctl -D <pg_data_dir> -m fast stop -t 60`. A fast stop ends client sessions. The default smart stop would wait forever on the bridges and the kernel.
     2. If the fast stop fails, `-m immediate`.
     3. `launchctl kickstart` on the LaunchAgent whose program runs that data dir, or bootstrap it if it isn't loaded. Its program is the `opt/` path, so the server starts on the new keg.

     It does not use `brew services`, which waits on brew's lock.
2. **The kernel heals itself (`serve`).**
   - **What triggers a restart.** `probe` runs at start and on every `serve_tick_s` wake. A curable exception from `tick`, `_step`'s handler, or the job and reaction callbacks also starts a 60 s clock. Once probes have failed for 60 s, the kernel calls `restart_postgres()`.
   - **Start-up on a broken database.** The first connect, the kernel lock and `recover()` wait in that same loop instead of raising to launchd. Reconnects of `conn` and `listener` back off and never leave `serve`.
   - **Live work keeps its locks.** `router.step`'s `alive()` reconnects and re-takes `run:<task>`. `_Services` and `slot.held` re-take their locks the same way. A step reports `lock lost` only when that lock is refused.
   - **After a restart: `db.restarted {cause, down_since}`.** Then a no-kill pass (`recover(after_restart=True)`) runs:
     - it never reaps, and never charges a gateway call at its estimate;
     - it ends a turn `interrupted` only when `runs`' process search (the one `reap` uses, without signalling) finds none of the turn's processes;
     - any other holder of a run, such as `python -m core run`, re-takes its lock on its next write.

     Handles that could not be re-claimed are dropped from `services` and `swept`. Interrupted turns then resume or report through the existing paths.
3. **A Python move drains.**
   - When the resolved `.venv/bin/python` changes, the kernel sets the rollout's hold (`restart_due`) and waits for running jobs. It then writes `kernel.restarting {reason: python}` and raises `rollout.Restart`.
   - The next start's `recover` kicks every bridge and the UI.
   - The vault script's `kickstart -k` block is deleted.
4. **`python -m core health [--fix]`** prints JSON:
   - the probe result;
   - a pid for each loaded `com.valor.kernel*` job;
   - free disk.

   `--fix` calls `restart_postgres()` when the probe fails curably. It kickstarts only jobs that have no pid.
5. **The `watch` routine**, a deterministic runner (no agent turn), runs every 30 minutes. `VALOR_WATCH` sets the targets: the Cowboy watches the Captain, the Pirate and the Bald; the Captain watches the Cowboy.
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
   - keeps `brew cleanup` off the Python keg the running kernel started on, so a draining kernel never loses its imports. bdfbd371345e's rule for running services covers this too, if it lands first.

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

- **`test_health.py`**
  - `curable` for each SQLSTATE above.
  - Stop and start order with stub `pg_ctl` and `launchctl`, including the fall back to immediate.
  - The `postmaster.pid` spacing.
  - `--fix` kicks only jobs that have no pid.
- **`test_serve.py`**
  - **Quiet Mac:** the old `conn` works while new connections fail with the real plpgsql `UndefinedFile`. Exactly one restart.
  - **The same failure from a step's ledger write:** exactly one restart.
  - **Start-up against a curably failing database:** one restart, and the kernel comes up.
  - **After `pg_terminate_backend` of every session:**
    - a running kernel step re-takes its locks and finishes;
    - a turn held by another process (`core run`) is not reaped or charged;
    - an open `turn.started` with no processes ends `interrupted` and is stepped again;
    - the next message is bound and answered.
  - **No restart:** a healthy database, or one that is only "starting up", is never restarted.
  - **Python move:** the kernel drains, then restarts.
- **`test_bridges_restart.py`.** Slack and Telegram fakes with their backends terminated: `bridge.serve` exits, and after the restart the failed message is taken in.
- **`test_watch.py`.** Every outcome through a stub `ssh`, and rows written only on a change of state.
- **Existing suites.** Rollout and reap stay green.
- **Live proof.** On the Cowboy, with no other turn running:
  1. `restart_postgres` with live clients, timed.
  2. Boot out Postgres and send a Slack DM.
  3. Show `db.restarted` and Valor's reply.
  4. Run `watch` once.

  This proves the refused-connection path. The plpgsql path is proven by the unit test.
