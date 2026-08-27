"""State-transition warning suppression for human-gated /update checks (#2329, #2328).

Some /update verifications can only be cleared by a one-time INTERACTIVE HUMAN
action that no agent and no automated /update cycle can perform:

- a browser OAuth consent to mint the Google Workspace token (#2329), and
- a System Settings > Full Disk Access grant so `tools/sms_reader` can read the
  Messages DB (#2328).

Re-emitting these warnings on every 30-minute `com.valor.update` cycle is pure
log spam with no actionable next step for the machine — the exact complaint in
both issues. This module collapses such a warning to a SINGLE emission per state
transition: warn once when a check first goes unresolved (or its detail changes),
stay silent while the state is unchanged, and warn once more if it later resolves
or regresses.

State lives in `data/update_warn_state.json` (gitignored, per-machine). Every
read/write fails soft — a warn-suppression bookkeeping error must never crash or
block `/update`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_STATE_FILENAME = "update_warn_state.json"

# scripts/update/ -> repo root, the same idiom as run.py and verify.py.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The bare stdout spelling of the suppression trailer's leading token
# (#2845). `run.py`'s `log()` prepends "[update] " before writing to
# data/update.txt, so that sink carries this constant offset by those five
# bytes — `bridge/update.py` matches the bare form because it reads
# stdout-derived `status_lines`. Shared by both sides so the producer and
# consumer can never spell it differently (the drift that produced Defect 2).
SUPPRESSED_PREFIX = "suppressed (unchanged since first warning):"


def _state_path(project_dir: Path) -> Path:
    return project_dir / "data" / _STATE_FILENAME


def _load(project_dir: Path) -> dict:
    try:
        return json.loads(_state_path(project_dir).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


# Read-only mode (issue #2898). When set, this module computes and returns its
# verdicts exactly as normal but persists nothing.
#
# `should_emit` writes the signature the instant it returns True, which under a
# read-only `--verify` run silently consumed the one emission the next
# unattended cron cycle would have made: an operator's diagnostic run left the
# durable record in logs/update.log silent about a machine that was still
# broken.
#
# The switch lives on the writer rather than on each of the six `should_emit`
# call sites deliberately. A per-call-site `persist=` argument is a checklist
# that the next call site added has to remember to join; a gate inside `_save`
# is honored by every present and future writer in this module for free.
_READ_ONLY = False


def set_read_only(value: bool) -> None:
    """Suppress all state persistence in this module.

    Called once by the `--verify` entry point. Verdicts are unaffected, so
    `--verify` still reports accurately -- it just stops mutating the
    suppression state that the scheduled runs depend on.
    """
    global _READ_ONLY
    _READ_ONLY = value


def _save(project_dir: Path, state: dict) -> None:
    if _READ_ONLY:
        return
    try:
        path = _state_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass  # never crash /update on a state-write failure


def should_emit(key: str, signature: str, project_dir: Path) -> bool:
    """Return True iff ``signature`` differs from the last-emitted one for ``key``.

    Records ``signature`` as the new last-emitted value whenever it returns True,
    so the next identical cycle is suppressed (returns False). Pass an empty
    ``signature`` to mark a check RESOLVED: the stored entry is cleared and the
    first resolved cycle returns True (emit a one-time "resolved" note), while
    subsequent resolved cycles return False. A later regression to an unresolved
    signature differs from the (now-absent) stored value and warns again.

    Semantics summary for one key:

    | previous | signature | returns | effect                          |
    |----------|-----------|---------|---------------------------------|
    | absent   | "u:X"     | True    | store "u:X" (first warn)        |
    | "u:X"    | "u:X"     | False   | unchanged — suppress            |
    | "u:X"    | "u:Y"     | True    | store "u:Y" (detail changed)    |
    | "u:X"    | "" (ok)   | True    | clear — one-time resolved note  |
    | absent   | "" (ok)   | False   | already clear — stay silent     |
    """
    state = _load(project_dir)
    previous = state.get(key)
    if signature == (previous or ""):
        return False
    if signature:
        state[key] = signature
    else:
        state.pop(key, None)
    _save(project_dir, state)
    return True


def active(project_dir: Path = PROJECT_ROOT) -> dict[str, str]:
    """Return the currently-suppressed key -> signature map, fail-soft.

    Enumerates whatever `_load` returns, including keys this module's
    callers never wired explicitly (e.g. `calendar-config`, wired bespoke at
    its own call site) — the retrieval surface for Risk 4: a suppressed
    condition must stay discoverable by an operator who missed the one
    emission. Fails soft to `{}`, matching `_load`'s own contract.
    """
    return _load(project_dir)


def _main() -> int:
    """`python -m scripts.update.warn_state` — the retrieval surface's CLI.

    Prints which state file it read FIRST, because every failure mode of
    this surface is silent: `_load`'s fail-soft `{}` on OSError means a
    `_main()` that resolved the wrong root would otherwise print an empty
    map and exit 0, indistinguishable from a machine with nothing
    suppressed.
    """
    parser = argparse.ArgumentParser(description="Show currently-suppressed /update warnings.")
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    print(f"state: {_state_path(args.project_dir)}")
    state = active(args.project_dir)
    if not state:
        print("(nothing suppressed)")
        return 0
    for key, signature in sorted(state.items()):
        print(f"{key}: {signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
