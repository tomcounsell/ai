# Popoto Version-Floor Guard

A fail-closed interlock that refuses to rebuild Popoto indexes when the running
interpreter's popoto is below the floor declared in `pyproject.toml`.

**Why it exists:** issue [#2536](https://github.com/tomcounsell/ai/issues/2536).
Running repo code under a below-floor popoto silently destroys the AgentSession
index. This guard makes it refuse instead.

## The failure it prevents

Popoto at and above the declared floor stores a server-authoritative index
pointer inside every model hash for each `IndexedField` (on `AgentSession`:
`status`, `task_type`, `claude_session_uuid`). Those pointers are raw Redis
Set-key strings, deliberately not msgpack-encoded, and their field names carry
an embedded NUL byte. An at-or-above-floor popoto skips them when decoding a
hash; a below-floor popoto has no such skip and runs `msgpack.unpackb` over
them, raising `ExtraData: unpack(b) received extra data.`

That alone would be a harmless read error. The damage comes from *where* it
fires. `Model.rebuild_indexes` (`popoto/models/base.py`) does this in order:

1. Delete the class set and every secondary index key.
2. Scan all instance hashes and eagerly decode each one.
3. Re-add each record to the rebuilt indexes.

Under a below-floor popoto, step 2 raises on the very first record. Step 1 has
already completed. The result is an AgentSession keyspace with every hash intact
and **no index at all**: `query.all()` returns 0, and the dashboard,
`valor-session list`, and the worker all report "zero sessions."

Worse, nothing warns beforehand. Reads use Popoto's lazy decoder, which defers
unpacking to attribute access and therefore never touches the pointer fields —
`query.all()` and per-field access both keep working normally under a below-floor
popoto. The system looks completely healthy right up to the moment a rebuild
destroys it.

This is the 2026-07-14 incident recorded in the module docstring of
`agent/index_drift.py`. That module is the **alarm** — it detects the aftermath.
This guard is the **interlock** — it prevents the cause.

## How it works

`config/popoto_floor.py` resolves two values and compares them:

| Value | Source | Why this source |
|---|---|---|
| Required floor | the `>=` bound on the `popoto` requirement in `pyproject.toml`, parsed at runtime | A hardcoded literal rots the moment the pin moves, and a version predicate drifting out of sync with reality is the exact bug class being prevented. |
| Installed version | `popoto.__version__` on the imported module | **Not** package metadata. Metadata is a static string over mutable source and can report a version for a popoto that cannot even be imported (a stale editable-install `.pth` pointing at a deleted checkout did exactly this). A wrong answer here either blocks index repair fleet-wide or lets the index be destroyed. |

Comparison uses `packaging.version.Version`, never string ordering — `"1.10.0"`
sorts below `"1.8.0"` as a string and above it as a version.

### Two guard points, because there are two teardowns

**1. The seam** — `install_rebuild_interlock()` wraps popoto's
`Model.rebuild_indexes` classmethod, and `models/__init__.py` calls it as its
first statement, before any model import. Every caller in the repo reaches its
model class through the `models` package, so one install covers them all,
including callers that do not exist yet. The wrapper preserves `classmethod`
binding, so `cls` is the concrete subclass and the generic
`model_class.rebuild_indexes()` form is covered too. The install is idempotent
via a sentinel attribute (`__popoto_floor_guarded__`) stamped on the patched
function.

**2. The entry guard** — `AgentSession.repair_indexes()` calls
`assert_popoto_floor()` at entry. This is not redundant: `repair_indexes()`
deletes every `$IndexF:AgentSession:*` key *before* it delegates to popoto, so
the seam wrapper fires too late to protect that teardown.

`scripts/checks/no_new_rebuild_callers.sh` guards against drift. It compares the
current set of `rebuild_indexes()` callers against an explicit baseline file
list. A file disappearing from the set is fine; only additions fail.

## Failure policy: runtime fails open, observability fails loud

This asymmetry is deliberate.

**Runtime fails open.** If the floor cannot be resolved — unreadable
`pyproject.toml`, no popoto requirement, no `>=` bound, missing or unparseable
`popoto.__version__` — `assert_popoto_floor()` returns without raising.
`repair_indexes()` runs on worker startup and on an hourly reflection; a false
positive there would block index repair across the fleet, which is a worse
incident than the one being prevented. The guard raises only on an unambiguous
verdict with both versions successfully parsed.

**Observability fails loud.** Every unresolvable branch emits `logger.error` plus
a Sentry capture from inside the resolver itself (mirroring
`agent/index_drift.py::_report_loud`), so the signal never depends on a caller's
error handling. The `popoto_floor` check in `python -m tools.doctor` renders the
same condition as a **FAIL**, not a pass-with-note: `CheckResult` has no degraded
state, so a note would collapse to `passed=True` in any boolean summary and a
silently-disabled interlock would look healthy. Doctor gates nothing, so failing
there is free.

`install_rebuild_interlock()` likewise never raises. `models/__init__.py` is on
the import path for the bridge, the worker, and every repo script; raising there
would turn a popoto rename into a full outage. A missing seam is reported loudly
and caught by the doctor check and by tests instead.

## Operator remedy

The violation message names all four facts needed to act: the running
interpreter (`sys.executable`), the installed version, the required floor, and
the fix. The fix is always the same — run repo code under the project venv:

```
/Users/valorengels/src/ai/.venv/bin/python -m tools.doctor
```

Nothing in this repo should be run under an ambient `python3`. launchd services
were never at risk; their plists hardcode the venv interpreter. The exposure is
`#!/usr/bin/env python3` scripts and ad-hoc shell invocations.

If `doctor` reports a below-floor or unresolvable state, the machine has a stale
popoto outside the venv. Removing it is an operator action:

```
python3 -m pip uninstall -y popoto
```

## Files

| Path | Role |
|---|---|
| `config/popoto_floor.py` | Floor resolution, version oracle, the raising assertion, and the seam installer |
| `models/__init__.py` | Installs the seam before any model import |
| `models/agent_session.py` | `repair_indexes()` entry guard, above its own `$IndexF` teardown |
| `tools/doctor.py` | `popoto_floor` check (Environment) |
| `scripts/checks/no_new_rebuild_callers.sh` | Baseline-diff guard against new callers |
| `tests/unit/test_popoto_floor.py` | Unit coverage, including the no-mutation regression test |

## Popoto coupling

`config/popoto_floor.py` **monkeypatches a third-party classmethod**. That is a
new pattern in this repo, not an established precedent — the comment convention
in `models/session_lifecycle.py` covers popoto-internals *reads*, and only the
re-verify-on-upgrade discipline is borrowed from it.

**On any popoto upgrade, re-verify** that `popoto.models.base.Model.rebuild_indexes`
still exists and is still the single entry point for index rebuilds. If it is
renamed or relocated, the install reports loudly and leaves the seam off; the
`popoto_floor` doctor check and `tests/unit/test_popoto_floor.py` will both fail.

## Related

- [`agent/index_drift.py`](../../agent/index_drift.py) — detect-only drift reconciliation; the alarm to this interlock
- [`docs/features/popoto-descriptor-pollution-ledger.md`](popoto-descriptor-pollution-ledger.md) — the index-pointer contract this guard depends on
- Issues [#2536](https://github.com/tomcounsell/ai/issues/2536) (this guard), [#2086](https://github.com/tomcounsell/ai/issues/2086) (same root cause, closed without code), [#2207](https://github.com/tomcounsell/ai/issues/2207) (phantom index hashes — a different problem)
