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

import json
from pathlib import Path

_STATE_FILENAME = "update_warn_state.json"


def _state_path(project_dir: Path) -> Path:
    return project_dir / "data" / _STATE_FILENAME


def _load(project_dir: Path) -> dict:
    try:
        return json.loads(_state_path(project_dir).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save(project_dir: Path, state: dict) -> None:
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
