"""Single source of truth for this machine's identity.

Two distinct identifiers are in play and must not be conflated:

* **ComputerName** (``scutil --get ComputerName``) — the human-set macOS name
  that ``projects.json``'s ``projects.<key>.machine`` field is matched against
  for single-machine ownership. Ownership logic MUST use this and nothing else;
  substituting the OS hostname would silently break owner matching.
* **OS hostname** (``socket.gethostname()``) — a lower-level identifier that is
  fine as a *display* fallback when no ComputerName is set, but is NOT
  interchangeable with ComputerName for ownership decisions.

``computer_name()`` returns the raw ComputerName (empty string on any failure),
so callers that match against ``projects.json`` can fail-open correctly.
``display_machine_name()`` layers a hostname fallback on top for human-facing
labels where any stable identifier beats an anonymous blank.
"""

from __future__ import annotations

import socket
import subprocess


def computer_name() -> str:
    """Return this machine's macOS ComputerName, or ``""`` on any failure.

    The empty-string-on-failure contract is load-bearing for ownership logic:
    it matches against ``projects.json``'s ``machine`` field, and an empty
    result must trigger fail-open (do not disable) rather than a wrong match.
    """
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def display_machine_name() -> str:
    """Human-facing machine label: ComputerName, then OS hostname, then ``"unknown"``.

    For triage/stamping only — never use this for ownership matching (use
    :func:`computer_name`), since the hostname fallback is a different
    identifier than ``projects.json``'s ``machine`` field.
    """
    name = computer_name()
    if name:
        return name
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"
