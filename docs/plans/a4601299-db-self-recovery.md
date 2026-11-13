# Each Mac heals its own database, and the Macs watch each other

Task a46012997a42.

## Incident, 2026-11-13

The 04:00 nightly (`com.valor.brew-nightly`) upgraded postgresql@18 while it was running and deleted the old keg. On all four Macs, any connection that loaded the plpgsql trigger then failed (`UndefinedFile`, "could not load library"). From that point every ledger INSERT from the bridges, steps and gateway failed.

| Mac | Down from | Down until | How it ended |
|---|---|---|---|
| Captain | 12 Nov 21:00 UTC | 02:13 UTC | |
| Cowboy | 12 Nov 21:00 UTC | 09:08 UTC | |
| Pirate | 12 Nov 21:00 UTC | 09:21 UTC | this task restarted it |
| Bald | 12 Nov 21:00 UTC | 09:22 UTC | this task restarted it |

Why nothing recovered:
- **Retries never escalate.** Every failure site logs and retries forever.
- **Postgres never crashed.** Its LaunchAgent restarts it only on a crash.
- **Sentry emailed once.**
- **Reports need the ledger.** Every report path writes to the ledger, so nothing could report.
- **The reconnect crashed the kernel.** It ran outside any `try` (`serve.py:1348/1357`), died on "starting up", and the crash cancelled running turns.

The bridges lost no message. Slack and Telegram retake by gap fill, and email stays unseen until it's taken. On the Pirate and the Bald, about a minute after the restart:
- the Slack and Telegram bridges exited, because `due()` raised on the dead lock connection and `bridge.serve` exits;
- launchd started them again: `telegram.log` shows a fresh start ("libssl detected") at 16:22:21 and 16:23:34 local.

**Stakes.** This changes the resident kernel and the nightly update on four Macs that run unattended for weeks.

## Build

### 1. `core/health.py`

- **`probe(dsn)`.** Opens a **new** connection and runs `DO $$BEGIN END$$`, which loads plpgsql. An old backend that loaded plpgsql before the keg was deleted keeps working, and `pg_isready` passed all through the incident, so neither is a usable check.
- **`curable(exc)`.** True for SQLSTATE 58P01, for XX000 "could not load library", and for a refused or lost connection. False for 57P03 "starting up", which is waited out.
- **`restart_postgres()`.**
  - Finds the LaunchAgent whose program runs `settings.pg_data_dir`.
  - Runs `launchctl kickstart -k` on it, or bootstraps it if it isn't loaded, with a 120 s timeout. It uses launchd directly because `brew services` waits on brew's lock.
  - The plist runs the keg's `opt/` path, so a restart picks up the new keg.
  - **Spacing.** It acts only when `<pg_data_dir>/postmaster.pid` is absent or more than 10 minutes old. That file is the server's own start record. It holds the spacing across kernel restarts and while the ledger is down, and it is shared with a peer's remote fix, so two fixers never restart the server within 10 minutes of each other.

### 2. The kernel heals itself (`serve`)

- **Healing a broken database.** At start, and on every `serve_tick_s` wake, the kernel runs `probe`. The new connection on each wake catches a quiet Mac whose old `conn` still works.
- **Failure sites feed the same clock.** A curable exception from any of these starts the 60 s clock too:
  - `tick`;
  - `_step`'s handler;
  - `_start`'s job callback;
  - `react`'s callback.
- **The restart.** After 60 s of failing probes, the kernel calls `restart_postgres()`.
- **Start-up on a broken database.** The first connect, the kernel lock and `recover()` wait inside one loop until the server is healthy, through the same probe, the same 60 s clock and the same restart. They do not raise to launchd.
- **Reconnects.** `conn` and `listener` back off and retry, and never leave `serve`.
- **Live work keeps its locks.**
  - `router.step`'s `alive()` reconnects a dead holder and re-takes `run:<task>`. It reports `lock lost` only when that lock is refused. Nothing else steps a task the kernel is running.
  - `_Services` re-claims `services:<task>` the same way.
  - `slot.held` re-takes its turn slot the same way.

  A turn that is running keeps running.
- **After a restart, interrupted work is recovered.** Once writes work again, the kernel:
  - writes `db.restarted {cause, down_since}`;
  - drops `services` and `swept` handles that could not be re-claimed;
  - runs `recover(skip=<tasks with a live job>)`.

  For any other task, `recover` reaps the turn that has no end, writes `turn.ended interrupted`, and collects it. The existing paths then resume or report it. A live job is never reaped.
- **Bridges.** No change: they exit and launchd restarts them, as above. The tests pin this down.

### 3. A Python move drains

- **The trigger.** On a wake, the resolved `.venv/bin/python` differs from the one at start.
- **What the kernel does:**
  1. It sets the rollout's hold (`restart_due`).
  2. It waits for the running jobs.
  3. It writes `kernel.restarting {reason: python}` and raises `rollout.Restart`.
- **The next start.** `recover` kicks every bridge and the UI.
- **Why this is enough.** The venv points at `/opt/homebrew/opt/python@3.14`, so a patch move keeps it valid. A minor move would be a new formula (`python@3.15`), which only a deliberate change installs. That case is listed as a residual risk.
- **The vault script.** Its `kickstart -k` block is deleted.

### 4. `python -m core health [--fix]`

- **What it reports.** It reads this Mac's kernel plist env and prints JSON:
  - the probe result;
  - the pid of `com.valor.kernel` and of each loaded `com.valor.kernel.*` job;
  - free disk.
- **What `--fix` does.**
  - When the probe fails curably, it calls `restart_postgres()`, with the same spacing.
  - It kickstarts only jobs that have no pid.

### 5. The `watch` routine, every 30 minutes

**Who watches whom** (`VALOR_WATCH`):

| Mac | Watches |
|---|---|
| Cowboy | Captain, Pirate, Bald |
| Captain | Cowboy |
| Pirate | none |
| Bald | none |

**What it runs on each target:** `ssh -o BatchMode=yes -o ConnectTimeout=10 <host> …python -m core health`.

| Result | What happens |
|---|---|
| Healthy | Delete `~/.config/brew-nightly/hold-<host>` on both Macs. |
| Broken | Write that file on both Macs and record `watch.held`. |
| Broken at two watches in a row | Run `health --fix`. |
| Unreachable | Record it. No hold. |

### 6. Telling Tom

- **A fix that worked:** recorded only (`watch.fixed`).
- **A target still broken after the fix, or unreachable for 24 h:**
  - if the watcher is the Cowboy, it sends one `notices.request` on stream `watch` to the operator chat's session, which is Valor's normal voice in Tom's DM;
  - if the watcher is the Captain, the target is the Cowboy, and the Cowboy's session may be what's down, so the Captain sends `sentry_sdk.capture_message(level="fatal")` instead.

### 7. The vault script (outside the repo; this task edits it, owner Valor, by 2026-11-14)

- Reread fresh before the edit, on top of whatever bdfbd371345e has landed. bdfbd371345e is the separate task that stops the upgrade itself.
- Skips upgrade and cleanup while any `hold-*` file exists, and logs which ones.
- On the Cowboy (by hostname), waits until 05:00 before upgrading. The shared plist stays at 04:00.

## Choices (from two workers' assessment: code facts, failure modes)

- **Kept from Tom's design:**
  - self-heal comes first and needs no network;
  - the Cowboy is the backstop for a kernel that is dead;
  - the stagger and the holds make the Cowboy a canary.
- **Changed:**
  - the watch reads probes, not "new Sentry errors", because an arbitrary error has no automatic fix and the nightly `sentry` routine triages them already;
  - the remote fix waits one watch and shares the `postmaster.pid` spacing;
  - each Mac has at most one watcher;
  - a Mac that can't be reached never holds an update;
  - a hold lifts once its Mac is healthy.
- **Rejected:**
  - Sentry heartbeats, which only notice and would add a second notice path;
  - an agent deciding fixes, which is slow and nondeterministic;
  - holds in iCloud, which lag;
  - `brew pin`, which is the prevention task's job;
  - a cap on restarts, since restarting a server that is already broken costs nothing.
- **Residual:**
  - **Full disk, a Postgres that won't start, a crash loop:** fixed once, then reported.
  - **Cowboy and Captain both down:** reported to Sentry.
  - **Python minor move:** the venv breaks and the kernel crash-loops. The watch sees no pid, fixes once, then reports it; the fix is a `uv sync` by hand.

## Governance

**What is governed.** The two holds: the Cowboy skipping its update while a peer is broken, and a broken Mac skipping its own update.

**Grant.** Tom, valor team chat, #see-issue-63, 2026-11-13, items 4 and 5 of his design. His words:
- "what if the cowboy machine updated last? … The cowboy is the one that has tailscale access to help the others"
- "i'm also trying to prevent getting me involved at all."

**Incident.** 2026-11-13: the 04:00 upgrade broke every Mac's database for 5 to 12 h.

**Mission item.** 6 (attention), under the constraint "reliable stop, recovery, and correction".

**Ledger.**
- The guard is `nightly.hold` in `guards.SEEDED`. It expires 90 days after merge.
- It fires when the watch writes or keeps a hold. That writes a `watch.held` row on the watcher, naming the host. The script itself has no ledger.
- Until the script edit in Build 7 lands, the holds do nothing.

**Not governance.** Nothing else here limits anything. The probe, the restart, the reconnects, the re-taken locks, the drain, `health` and the ssh reads are recovery on paths that already exist.

## Out of scope

- Stopping the upgrade itself (bdfbd371345e).
- Task-workspace Postgres clusters.
- Slack reactions past Slack's retries.

## Tests

| File | Test |
|---|---|
| `test_health.py` | `curable` per SQLSTATE: 58P01, XX000 library, refused and lost are true; 57P03, a unique violation and a syntax error are false. |
| `test_health.py` | `restart_postgres` finds the label by data dir, then kickstarts or bootstraps it (stub `launchctl`). It skips when `postmaster.pid` is younger than 10 min. |
| `test_health.py` | `--fix` kicks only jobs that have no pid. |
| `test_serve.py` | A quiet Mac: `tick` succeeds on its old `conn` while new connections fail with the plpgsql `UndefinedFile`. The probe triggers one restart. |
| `test_serve.py` | The same trigger from a step job's ledger write, and from a job callback. |
| `test_serve.py` | Start-up against a database that fails curably: one restart, the kernel comes up and recovers, and `serve` stays in its loop. |
| `test_serve.py` | After a real `pg_terminate_backend` of every kernel connection: a running step re-takes its run, services and slot locks and finishes. An open `turn.started` with no live job gets `turn.ended interrupted`, and the task is stepped again. The next message is bound and answered. |
| `test_serve.py` | A healthy database is never restarted, and neither is one that only reports "starting up". |
| `test_serve.py` | A Python move waits for the running job, then restarts. The next start kicks every bridge. |
| `test_bridges_restart.py` | Slack and Telegram fakes: the backends are terminated mid-run; `bridge.serve` exits; on restart, the message whose receive failed is taken in by gap fill. |
| `test_watch.py` | Through a stub `ssh`: healthy, first broken, second broken (fix), still broken (report), unreachable (no hold), 24 h unreachable (one report), the Captain's report goes to Sentry. |

**Existing suites.** The rollout and reap suites stay green.

**Live (after rollout).** On the Cowboy, when no other turn runs:
1. Boot out Postgres.
2. Send a Slack DM while it is down.
3. Show `db.restarted` and Valor's reply.
4. Run `watch` once.

This proves the refused-connection path only. The plpgsql path is proved by the unit test with the real SQLSTATE.
