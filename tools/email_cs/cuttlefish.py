"""Cuttlefish ``manage.py`` subprocess wrapper.

Mirrors ``bridge/routing.py::_dispatch_subprocess_resolver`` exactly:
``asyncio.create_subprocess_exec`` (argv-form only — NEVER shell),
``stdin=DEVNULL``, a hard ``asyncio.wait_for`` timeout with ``proc.kill()`` on
timeout, and a non-zero exit raising. The chosen verb is always scoped
``--email <customer_email>`` so an agent can never touch another account, and
``--json`` is appended so the result is machine-readable.

The cuttlefish venv python is resolved from the project's ``working_directory``
(``<working_directory>/.venv/bin/python``) and ``cwd`` is set to that directory.

Contract: ``run_manage_command`` returns the parsed ``--json`` dict on success.
On timeout, non-zero exit, or malformed JSON it RAISES — the caller
(``handler.py``) catches and fails-safe to escalate, recording the failure in an
audit note. This module never silently returns a degraded result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)

# Default hard timeout for a manage.py invocation (seconds). A status lookup
# must return quickly; a hung command must not block the IMAP poll loop.
DEFAULT_TIMEOUT_SECONDS: float = 20.0


class CuttlefishCommandError(RuntimeError):
    """Raised on subprocess failure (timeout, non-zero exit, or bad JSON)."""


def resolve_venv_python(working_directory: str) -> str:
    """Resolve the cuttlefish venv python from the project working directory.

    Returns ``<working_directory>/.venv/bin/python`` with ``~`` expanded.
    Does not check existence — the caller's subprocess launch surfaces a missing
    interpreter as a launch failure, which fails-safe to escalate.
    """
    base = os.path.expanduser(working_directory)
    return os.path.join(base, ".venv", "bin", "python")


async def run_manage_command(
    verb_argv: list[str],
    customer_email: str,
    working_directory: str,
    *,
    extra_args: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Execute ``manage.py <verb...> --email <customer_email> --json``.

    Args:
        verb_argv: The manage.py verb tail, e.g. ``["customer", "show"]``.
        customer_email: The trusted resolved customer id. Always passed as
            ``--email`` — never sourced from email body content.
        working_directory: The cuttlefish project working_directory (for the
            venv python and subprocess cwd).
        extra_args: Optional additional argv (e.g. ``["--body", "..."]``). Must
            already be split into argv tokens — no shell interpolation.
        timeout: Hard timeout in seconds.

    Returns:
        The parsed ``--json`` payload as a dict.

    Raises:
        CuttlefishCommandError: On empty verb, subprocess launch failure,
            timeout, non-zero exit, or malformed/non-object JSON.
    """
    if not verb_argv:
        raise CuttlefishCommandError("empty verb_argv")
    if not customer_email:
        raise CuttlefishCommandError("empty customer_email — refusing unscoped command")

    cwd = os.path.expanduser(working_directory)
    python = resolve_venv_python(working_directory)
    argv = [
        python,
        "manage.py",
        *verb_argv,
        "--email",
        customer_email,
        *(extra_args or []),
        "--json",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except Exception as e:
        raise CuttlefishCommandError(f"failed to launch manage.py: {e}") from e

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise CuttlefishCommandError(f"manage.py timed out after {timeout}s: {verb_argv}") from e

    if proc.returncode != 0:
        stderr = stderr_bytes[:300].decode("utf-8", errors="replace")
        raise CuttlefishCommandError(
            f"manage.py exited {proc.returncode}: {verb_argv} (stderr: {stderr!r})"
        )

    raw = stdout_bytes.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(raw)
    except Exception as e:
        raise CuttlefishCommandError(
            f"manage.py returned malformed JSON: {e} (raw: {raw[:200]!r})"
        ) from e

    # Envelope validation: a CS command must return a JSON object, not a bare
    # scalar/array — a contract drift that would render garbage downstream.
    if not isinstance(payload, dict):
        raise CuttlefishCommandError(
            f"manage.py JSON is not an object (got {type(payload).__name__})"
        )

    logger.info(f"[email_cs.cuttlefish] {verb_argv} ok for {customer_email!r}")
    return payload
