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

import functools
import json
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from config.paths import CONFIG_DIR, VALOR_DIR

logger = logging.getLogger(__name__)

# scutil is fast, but a hung ComputerName lookup must never wedge a caller on a
# tight budget (e.g. the calendar hook). Provisional/tunable — grain of salt.
_SCUTIL_TIMEOUT_SECONDS = 5

# Apostrophe variants that macOS and hand-edited JSON disagree about. A default
# macOS ComputerName like "Tom’s MacBook Air" carries U+2019 (RIGHT SINGLE
# QUOTATION MARK); someone typing the same name into projects.json produces
# U+0027. Both spell the same machine, so ownership matching folds them to one
# form before comparing (issue #2541).
_APOSTROPHE_VARIANTS = "’‘ʼ´`"


def normalize_machine_name(name: str | None) -> str:
    """Fold a machine name to its comparison form: casefolded, apostrophes unified.

    Ownership matching compares a ``projects.<key>.machine`` string against a
    macOS ComputerName. Those two values are authored in different places and
    routinely disagree on apostrophe encoding, which silently unowns a project
    on a machine that is very much alive. Normalizing both sides makes an
    encoding difference stop being a routing decision.
    """
    if not name:
        return ""
    folded = name.strip().casefold()
    for variant in _APOSTROPHE_VARIANTS:
        folded = folded.replace(variant, "'")
    return folded


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


@functools.cache
def get_machine_id() -> str:
    """Return this machine's stable hardware identity; ``""`` on failure.

    Unlike hostname / ComputerName / ``projects.json``'s ``machine`` field --
    all mutable labels an operator can rename -- this identifier survives a
    machine rename, so it is what same-machine comparisons key on (issue
    #2537: the SDLC issue-lock liveness check failed open forever after a
    rename because it compared hostnames).

    Resolution: macOS ``IOPlatformUUID`` via ``ioreg``; elsewhere
    ``/etc/machine-id`` (or the dbus fallback). Fail-soft to ``""`` -- callers
    treat an unresolvable id as indeterminate and fall back to hostname
    comparison, never raise. Cached for the process lifetime (the value is
    immutable per machine and the lookup shells out). Note the cache also
    memoizes a FAILED lookup: a process that once resolved ``""`` keeps
    answering ``""`` until it restarts. Acceptable because every consumer
    treats ``""`` as indeterminate-with-fallback, and the processes involved
    (CLIs, heartbeat, worker turns) are short-lived relative to a transient
    ``ioreg`` failure clearing.
    """
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=_SCUTIL_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', result.stdout)
                if match:
                    return match.group(1)
            logger.debug("ioreg IOPlatformUUID lookup failed (rc=%s)", result.returncode)
        else:
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    value = Path(path).read_text().strip()
                except OSError:
                    continue
                if value:
                    return value
    except Exception as exc:
        logger.debug("machine-id lookup failed: %r", exc)
    return ""


def _resolve_projects_json_path() -> Path:
    """Launchd-safe ``projects.json`` path (mirrors ``bridge.routing``).

    Under ``VALOR_LAUNCHD=1`` this never touches the iCloud-synced
    ``VALOR_DIR`` path: macOS TCC / iCloud eviction can block ``open()``/
    ``stat()`` on it indefinitely from a launchd agent, wedging the caller
    at "alive but never finishes starting" (see
    ``bridge/routing.py::_resolve_config_path`` and the outage it
    documents). ``install_worker.sh`` copies ``projects.json`` to
    ``CONFIG_DIR`` at install time for exactly this fallback.
    """
    local_path = CONFIG_DIR / "projects.json"
    if os.environ.get("VALOR_LAUNCHD"):
        return local_path
    desktop_path = VALOR_DIR / "projects.json"
    return desktop_path if desktop_path.exists() else local_path


def get_machine_project_keys(machine: str | None = None) -> list[str]:
    """Return the ``project_key``s this machine owns in ``projects.json``.

    Reads ``VALOR_DIR / "projects.json"`` (or the launchd-safe local copy,
    see :func:`_resolve_projects_json_path`) and returns every key whose
    ``projects.<key>.machine`` field matches ``machine`` under
    :func:`normalize_machine_name` (case-insensitive, apostrophe-insensitive).
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
        config = json.loads(_resolve_projects_json_path().read_text())
    except Exception:
        return []
    target = normalize_machine_name(machine)
    return [
        project_key
        for project_key, project in config.get("projects", {}).items()
        if normalize_machine_name(project.get("machine", "")) == target
    ]
