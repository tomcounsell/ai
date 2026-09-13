# Ancestor-Safe Process Lookup

**Issues:** #3164, #3187, #3265

## The defect

macOS ships BSD `pgrep`, which excludes the calling process **and its entire
ancestor chain** from the match list. Every agent session here is a `claude -p`
descendant of the worker, so any `pgrep` liveness probe run from inside a session
reads its own live host service as absent.

The canonical symptom: `./scripts/valor-service.sh worker-status` printing
`STOPPED` against a worker with 21h uptime.

`-a` is not the fix. On Linux/procps `-a` means "print the full command line", so
the flag silently changes meaning off macOS.

## The shape of the fix

`tools/process_lookup.py` reads the whole process table with
`ps -axww -o pid=,args=` — `ps` has no ancestor filter — and parses each command
line as a **CPython invocation** (the interpreter's own `-m` module, or its own
script position) rather than scanning argv for a token that looks right. That
distinction is load-bearing: a substring scan reports
`python -m ruff check bridge/telegram_bridge.py` as the bridge.

`scripts/lib/service_pids.sh` is how shell reaches that same module. It is a
**transport, not a second implementation** — nothing in it parses a process
table. Selectors are defined once so a call site cannot drift from the shape the
launchd plists actually spawn:

| Function | Target |
|---|---|
| `service_pids_worker` | `python -m worker` and `python .../worker/__main__.py` |
| `service_pids_bridge` | `python .../bridge/telegram_bridge.py` |
| `service_pids_email` | `python -m bridge.email_bridge` |

The CLI mirrors `pgrep`'s contract — matching PIDs on stdout one per line, exit 0
when any matched and 1 when none did — so a converted call site keeps its
existing `if ...; then` shape. It is stdlib-only, so it runs under a bare
`python3` on a host whose virtualenv is missing or half-built.

## The second-order hazard: self-kill

Making the lookup ancestor-safe *creates* a danger `pgrep`'s bug was accidentally
masking. `pgrep` hid the host service, so `stop_worker` printed "not running" and
did nothing — a lie, but a harmless one. With a correct lookup, `kill -9`
resolves the real PID and would terminate the very session running the command,
mid-command: no completion, no exit status, a half-finished stop.

Every kill path therefore gates on a refusal:

- Shell: `service_pid_refuse_self_kill <pids> <name> <alternative>`
- Python: `tools.process_lookup.is_own_ancestor(pid, on_unreadable=True)`

Two properties make the refusal trustworthy:

1. **It checks every PID in the list.** A selector can return several — a
   double-bridge is precisely the state `start_bridge.sh` cleans up — and all of
   them are about to be signalled.
2. **It fails closed.** Only the CLI's dedicated exit code 3 — "walked to init,
   no match", a conclusive "not an ancestor" — counts as "not an ancestor".
   Exit 3 is a dedicated code rather than the more obvious exit 1, because exit
   1 is also what a generic Python crash (an import failure, an unhandled
   exception before argparse even runs) produces; reusing it would let a
   broken checkout read as a conclusive "no". Argparse rejecting a malformed
   PID token, a missing interpreter, a crash — every outcome other than 0 and
   3 answers "yes, ancestor". Mapping those to "no" would fail open in exactly
   the case the guard exists for.

A refusal propagates through every fallback PID-kill branch, including
`restart_bridge`, `restart_worker`, `restart_email`, `disable_worker`,
`disable_email`, and the bridge install path, which abort rather than
proceeding — proceeding anyway could leave two services running. That
propagation holds for the launchd-fallback branch each of those functions
falls back to when `bootout`/`bootstrap` alone did not stick. It does **not**
cover the separate `launchctl kickstart -k` branch some call sites take as
their fast path when the service is already loaded — `launchctl kickstart -k`
signals through launchd by label, not through this module's PID lookup, so
there is no PID here for the refusal to gate on. That branch bypasses the
refusal entirely; it is inherent to using launchctl directly, not a gap in
this module.

## Which probes keep `pgrep`

The rule is not "which files are left" but two questions, asked in order.

**1. Can the probe's target ever be an ancestor of the probe?** Two read probes
keep `pgrep` because it structurally cannot be:

- `scripts/update/service.py::get_caffeinate_status` — a launchd job running
  `/usr/bin/caffeinate`
- `tools/transcribe/__init__.py::_is_superwhisper_available` — a user-launched
  GUI app

Neither ever spawns an agent session, so neither can appear in a caller's
ancestor chain. Anything that *could* be an ancestor is converted.

**2. Does the PID feed an unconditional kill?** `pgrep` stays there, because
hiding an ancestor is the protection rather than the defect:

- `monitoring/bridge_watchdog.py::kill_stale_processes`
- `tools/agent_session_scheduler.py::_find_process_by_session_id`
- the xdist reapers in `scripts/reap-xdist.sh`, `scripts/pytest-clean.sh` and
  `tests/conftest.py`

A probe that needs a PID from an ancestor-safe lookup and then signals it must
gate on the refusal instead.

## Testing

A test that passes against both old and new code proves nothing here, because the
defect only exists in one topology. Every regression test runs the probe as a
genuine **descendant** of its own target:

- `tests/unit/test_service_pids_lib.py` — the shell helper, including a companion
  assertion that BSD `pgrep` *does* hide the ancestor, so the visibility test
  cannot pass vacuously
- `tests/unit/test_process_lookup.py` — the module and the migration guards, in
  both the child topology (launch-shape coverage, every machine) and the
  descendant topology (the defect, macOS-only)

The shell harnesses in `test_valor_service_bootstrap.py`,
`test_remote_update_shell.py` and `test_install_scripts_bootstrap.py` install a
**stub of `scripts/lib/service_pids.sh`** into their fake project. A PATH shadow
of `pgrep` intercepts nothing now that the real helper reads `ps`.
