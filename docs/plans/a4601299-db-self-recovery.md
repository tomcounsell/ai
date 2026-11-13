# Each Mac heals its own database, and the Macs watch each other

Task a46012997a42.

**Incident, 2026-11-13.** The 04:00 nightly (`com.valor.brew-nightly`) upgraded postgresql@18 while it ran and deleted the old keg. On all four Macs, every ledger INSERT failed: the plpgsql trigger's library would not load.

Downtime, from 21:00 UTC on 12 Nov:
- the Captain until 02:13 UTC;
- the Cowboy until 09:08;
- the Pirate and the Bald until 09:21, when this task restarted them.

Why nothing recovered:
- **Retries never escalate.** The kernel logs each failed wake and tries again, with no end.
- **No crash, no restart.** Postgres's LaunchAgent restarts it only on a crash, and it never crashed.
- **One email at 04:00.** That was everything Sentry sent.
- **Reports need the ledger.** Every way the kernel reports trouble writes to the ledger that was broken.
- **The reconnect itself crashes the kernel.** On restart, the reconnect (`serve.py:1348/1357`, outside any `try`) died on "starting up", which cancelled running turns.

The bridges lost nothing: they retook held messages by gap fill, or left emails unseen.

**Stakes.** This changes the resident kernel and the nightly update on four Macs left unattended for weeks.

## Build

1. **`core/health.py`.**
   - `probe` runs `DO $$BEGIN END$$`, which loads plpgsql. `pg_isready` passed all through the incident.
   - `curable(exc)` is true for:
     - SQLSTATE 58P01;
     - XX000 "could not load library";
     - a refused or lost connection.

     It is never true for 57P03 ("starting up"), which is waited out.
   - `restart_postgres()` finds the LaunchAgent whose program runs `settings.pg_data_dir`. It kickstarts it with `-k`, or bootstraps it if it isn't loaded, with a 120 s timeout. This is launchd, not `brew services`, which can wait on brew's lock. The plist runs the `opt/` path, so a restart picks up the new keg.
2. **The kernel heals itself.**
   - The existing failure handlers in `serve` and `Kernel.tick` pass each exception to `curable`.
   - After 60 s of curable failures with `probe` failing, the kernel calls `restart_postgres()`. It does this at most once per 10 minutes while the database stays broken. Nothing still alive is killed.
   - Reconnects back off and retry, and never leave `serve`.
   - Once writes work again, it writes `db.restarted {cause, down_since}`.
   - `recover()` is not re-run mid-life, because it would reap live turns whose locks dropped. A turn that lost its lock returns `lock lost`, and the existing scheduling steps it again.
   - Bridges exit and launchd restarts them. Their catch-up brings in held messages.
3. **A Python move drains.**
   - When the resolved `.venv/bin/python` differs from the one at start, the kernel sets the rollout's hold (`restart_due`): it waits for running jobs, then raises `rollout.Restart`.
   - It writes `kernel.restarting {reason: python}`, and the next `recover` kicks every bridge and the UI.
   - The vault script's `kickstart -k` block is deleted.
4. **`python -m core health [--fix]`.**
   - Reads the env from this Mac's kernel plist and reports as JSON:
     - the probe;
     - a pid for `com.valor.kernel` and for each loaded `com.valor.kernel.*` job;
     - free disk.
   - `--fix` restarts Postgres when the probe fails curably, and kickstarts only Valor jobs that have no pid.
5. **The `watch` routine** runs every 30 minutes. `VALOR_WATCH` sets which Macs each one watches:
   - the Cowboy watches the Captain, the Pirate and the Bald;
   - the Captain watches the Cowboy;
   - the Pirate and the Bald watch none.

   For each Mac it watches, it runs `ssh -o BatchMode=yes -o ConnectTimeout=10 <host> …python -m core health`:
   - **Healthy:** delete `~/.config/brew-nightly/hold-<host>` on both Macs.
   - **Broken:** write that file on both Macs. If it was also broken at the last watch, run `health --fix` (self-heal has had 30 minutes).
   - **Unreachable:** record it, and set no hold.
6. **Telling Tom.**
   - A fix that worked is only recorded (`watch.fixed`).
   - If the watched Mac is still broken after the fix, or unreachable for 24 h:
     - on the Cowboy, one `notices.request` on stream `watch` to the operator chat's session, which is Valor's normal voice in Tom's DM;
     - on the Captain, about the Cowboy, whose session may be the thing that is down, `sentry_sdk.capture_message(level="fatal")`.
7. **Vault script** (outside the repo; edited after bdfbd371345e's change to it lands):
   - It skips upgrade and cleanup while any `hold-*` file exists, and logs which.
   - The Cowboy runs at 05:00, the others at 04:00.

## Choices (from two workers' assessment: code facts, failure modes)

**Kept from Tom's design:**
- Self-heal first, because it needs no network.
- The Cowboy backstop, because it fixes what a dead kernel can't.
- The stagger with holds, which makes the Cowboy a canary.

**Changed:**
- **The watch reads probes, not "new Sentry errors".** The nightly `sentry` routine already triages errors, and an arbitrary error has no automatic fix.
- **The remote fix waits one watch.** That way it never races self-heal.
- **Disjoint watcher pairs.** No Mac is fixed by two watchers at once.
- **Unreachable never holds.** A Mac asleep, off, or behind a Tailscale outage holds nobody's updates.
- **A hold lifts itself.** It goes the moment its Mac is healthy.

**Rejected:**
- **Sentry heartbeats instead of ssh:** they only notice, and would add a second notice path.
- **An agent deciding the fixes:** slow and nondeterministic.
- **A hold file in iCloud:** sync lag.
- **`brew pin`:** that is the prevention task's job.
- **A cap on restarts:** restarting a server that is already broken costs nothing.

**Residual:**
- **Full disk, a Postgres that won't start, or a kernel crash loop:** the Mac is fixed once, then reported.
- **Cowboy and Captain both down:** the Sentry fatal event is what tells Tom.

## Governance

The two holds are governance: the Cowboy skipping its update while a peer is broken, and a broken Mac skipping its own.

- **Grant:** Tom, valor team chat, #see-issue-63, 2026-11-13, items 4 and 5 of his design: "what if the cowboy machine updated last? … The cowboy is the one that has tailscale access to help the others"; "i'm also trying to prevent getting me involved at all."
- **Incident:** 2026-11-13. The 04:00 upgrade broke every Mac's database for 5 to 12 hours.
- **Mission item:** 6, attention, under the constraint "reliable stop, recovery, and correction".
- **Ledger:** guard `nightly.hold` in `guards.SEEDED`, expiring 90 days after merge. Each skip fires a `watch.held` row.

Nothing else here limits anything. The probe, the restart, the reconnect, the drain, `health` and the ssh reads are recovery on existing paths.

## Out of scope

- Stopping the upgrade itself: that is task bdfbd371345e.
- Task-workspace Postgres clusters.
- Slack reactions past Slack's retries.

## Tests

| Covers | Test |
|---|---|
| `health` | `curable` per SQLSTATE: true for 58P01, XX000 "could not load library", refused and lost; false for 57P03, a unique violation, a syntax error. |
| `health` | Finding the label by data dir; kickstart if loaded, bootstrap if not, through a stub `launchctl`. |
| `health` | `--fix` kicks only pid-less jobs. |
| `serve` | A ledger write failing with the plpgsql `UndefinedFile` for 60 s causes one restart. The kernel reconnects through a 57P03, `serve` stays up, and the next message is bound and answered. |
| `serve` | The second restart waits 10 minutes. |
| `serve` | A healthy database, or one only "starting up", is never restarted. |
| `serve` | A turn whose lock dropped is stepped again. |
| `serve` | A Python move waits for the running job, then restarts, and the next start kicks every bridge. |
| `watch` | Through a stub `ssh`: healthy, first broken, second broken (fix), still broken (report), unreachable (no hold), 24 h unreachable (one report). |
| `watch` | The Captain's report goes to Sentry. |
| Existing | The rollout and reap suites stay green. |

Live: on the Cowboy, when no other turn runs, boot out Postgres. Send a Slack DM while it's down, and show `db.restarted` and Valor's reply. Then run `watch` once by hand.
