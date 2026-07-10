"""Single source of truth for "what machine am I / what do I own".

This is the lowest shared layer for machine identity (stdlib only, plus
``config.paths``). Every ``scutil --get ComputerName`` call and every
``projects.json`` ownership match in the codebase resolves through here, so a
fix to the resolution logic (e.g. the #1834 empty-machine fail-to-development
guard) propagates everywhere at once instead of drifting across copies.

Fail-soft contracts (never raise on a read failure):
  * :func:`get_machine_name` returns ``""`` on any failure. It deliberately
    does **not** fall back to ``platform.node()``: the ownership consumers
    (``ui``, ``monitoring``) and ``scripts/update/readme_check`` all need ``""``
    to signal "unknown host → do not match / skip". A ``platform.node()``
    fallback here would let an unresolved host silently match a
    ``"machine": ""`` entry and mis-tag itself as an owner (the #1834 bug).
  * :func:`get_machine_slug` is the filesystem-safe variant used for per-machine
    token filenames. It slugifies :func:`get_machine_display_name` when the
    ComputerName is unresolved because its invariant is the opposite: the slug
    must never be empty (an empty slug would collapse every machine's token
    onto one filename); the display chain's terminal ``"unknown"`` makes that
    guarantee real.
  * :func:`get_machine_project_keys` returns ``[]`` on any failure and applies
    the empty-machine guard before reading the file.
  * :func:`get_machine_display_name` is the human-facing variant (triage
    stamps, issue bodies, /update replies): ComputerName → OS hostname →
    ``"unknown"``. Never use it for ownership matching — the hostname fallback
    is a different identifier than ``projects.json``'s ``machine`` field.

Contract note (#1997 consolidation): this module absorbed the retired
``tools/machine_identity.py`` hub. That hub's ``computer_name()`` returned
``scutil`` stdout without checking the exit status; :func:`get_machine_name`
deliberately keeps the **stricter** ``returncode == 0`` check so a failing
``scutil`` can never leak partial stderr-adjacent output into ownership
matching. All former consumers now share this stricter contract.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess

from config.paths import VALOR_DIR

logger = logging.getLogger(__name__)

# scutil is fast, but a hung ComputerName lookup must never wedge a caller on a
# tight budget (e.g. the calendar hook). Provisional/tunable — grain of salt.
_SCUTIL_TIMEOUT_SECONDS = 5


def get_machine_name() -> str:
    """Return this machine's macOS ComputerName via ``scutil``; ``""`` on failure.

    Success returns the stripped ``scutil --get ComputerName`` stdout. A
    non-zero exit, empty output, timeout, or any other exception returns ``""``
    (the fail-to-development / "unknown host" signal). No ``platform.node()``
    fallback by design — see the module docstring.
    """
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=_SCUTIL_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.debug(
            "scutil ComputerName lookup exited %s: %s", result.returncode, result.stderr.strip()
        )
    except Exception as exc:
        # Fail-soft by contract, but leave a trace so a failing scutil is
        # distinguishable from a genuinely empty ComputerName when debugging.
        logger.debug("scutil ComputerName lookup failed: %r", exc)
    return ""


def get_machine_display_name() -> str:
    """Human-facing machine label: ComputerName, then OS hostname, then ``"unknown"``.

    For triage/stamping and human-facing messages only (e.g. naming the machine
    that filed an issue, or the ``/update`` status replies) — never use this for
    ownership matching (use :func:`get_machine_name`), since the hostname
    fallback is a different identifier than ``projects.json``'s ``machine``
    field and would silently break owner matching.
    """
    name = get_machine_name()
    if name:
        return name
    try:
        return socket.gethostname() or "unknown"
    except Exception:
        return "unknown"


def get_machine_slug() -> str:
    """Return a filesystem-safe, guaranteed-non-empty machine slug.

    Lowercases :func:`get_machine_name` and replaces spaces with hyphens; when
    the ComputerName is unresolved (``""``), slugifies
    :func:`get_machine_display_name` instead (first hostname label, lowercased)
    — whose terminal ``"unknown"`` makes the non-empty guarantee real. Used for
    per-machine token filenames (``google_workspace/auth.py``), where an empty
    slug would collapse every host's token onto one path.
    """
    name = get_machine_name()
    if name:
        return name.lower().replace(" ", "-")
    slug = get_machine_display_name().split(".")[0].lower().replace(" ", "-")
    return slug or "unknown"


def get_machine_project_keys(machine: str | None = None) -> list[str]:
    """Return the ``project_key``s this machine owns in ``projects.json``.

    Reads ``VALOR_DIR / "projects.json"`` and returns every key whose
    ``projects.<key>.machine`` field matches ``machine`` (case-insensitive).
    When ``machine`` is ``None`` it resolves via :func:`get_machine_name`; a
    caller that already resolved the name can pass it to avoid a second
    ``scutil`` call.

    Empty-machine guard (#1834): an unresolved ``machine`` (``""``) returns
    ``[]`` before any file read, so it can never match a ``"machine": ""`` entry
    and mis-tag a dev/misconfigured host as an owner. Any missing/unreadable/
    malformed ``projects.json`` also returns ``[]`` (fail-to-development).
    """
    if machine is None:
        machine = get_machine_name()
    if not machine:
        return []
    try:
        config = json.loads((VALOR_DIR / "projects.json").read_text())
    except Exception:
        return []
    machine_lower = machine.lower()
    return [
        project_key
        for project_key, project in config.get("projects", {}).items()
        if project.get("machine", "").lower() == machine_lower
    ]
