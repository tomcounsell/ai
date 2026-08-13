"""Redis ACL planner — Layer 2 of the flush-hardening design (#2645).

REPORT-ONLY BY DEFAULT. ``/update`` calls ``apply_redis_acl()`` with no
arguments, always — the report path. This module must never mutate the live
Redis server as a side effect of merging this PR or running ``/update``.
Applying the ACL is a human-signed runbook step (see
``docs/features/redis-flush-hardening.md``), gated behind an operator marker
file AND an explicit environment opt-in (see ``apply_redis_acl`` below).

**Why database number cannot be the discriminator (spike-1, empirical, Redis
8.6.2 on this machine).** The obvious-looking design — "let dbs 1-15 flush,
deny db 0" — does not exist as a server-side mechanism, in either form tried:

  - ``ACL SETUSER u db:1`` -> ``ERR Error in ACL SETUSER modifier 'db:1':
    Syntax error``. There is no db-scoped ACL syntax at all.
  - ACL *selectors* accept a key pattern, but ``FLUSHDB``/``FLUSHALL`` take no
    key arguments, so a selector's key pattern is **vacuously satisfied** no
    matter what it says. A user configured with
    ``-flushdb '(+flushdb ~onlythis:*)'`` **successfully flushed db 0** in the
    prototype. A selector here is a trap, not a solution — do not "optimize"
    the target rule set into one; it would silently reopen the incident this
    module exists to close.
  - Plain command denial (``-flushdb``, ``-flushall``) works and is
    deliberately db-blind: ``NOPERM ... has no permissions to run the
    'flushdb' command`` in every database. That is why Layer 2 discriminates
    by **user identity** instead (D3 in the plan): a locked-down ``valor-app``
    user for production traffic, and a ``default`` user that keeps ``flushdb``
    so the test suite's bare ``redis.Redis(db=N)`` clients (no credentials,
    authenticate as ``default``) keep working with zero test-side changes.

**Why ``/update`` must never apply this (D8, Risk 8).** ``/update`` runs
unconditionally after every merge. If this module issued ``ACL SETUSER`` /
``ACL SAVE`` from a ``/update`` step, *merging the PR would BE the apply* — a
live mutation of the production Redis ACL, on the same instance the
2026-08-07 incident just disrupted, with no human in the loop. That is not
authorized in this work. So the apply path is gated behind two independent,
operator-only actions (the marker file and the env flag below), a combination
``/update`` never supplies, and even when both gates are open this module
never restarts Redis, never issues ``CONFIG REWRITE``, and never opens
``redis.conf`` for writing on any path — the ``aclfile`` directive is staged
as text in the result for a human to add by hand.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Project root (ai/) — scripts/update/redis_acl.py → ../../
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

# Apply-gate marker: an operator opts in to allowing the apply path by
# touching this file. Mirrors data/redis-replication-enabled and
# data/auto-revert-enabled. Absent on every machine by default.
ACL_MARKER_FILE = _PROJECT_DIR / "data" / "redis-acl-enabled"

# Env var that must be the literal string "true" (case-insensitive) for the
# apply path to proceed, even with the marker present. Mirrors the
# MEMORY_DECAY_PRUNE_APPLY posture: an irreversible-in-practice operation
# defaults off and is reachable only by explicit, deliberate opt-in.
_APPLY_ENV_VAR = "REDIS_ACL_APPLY"

# The production application user. Production REDIS_URL becomes
# redis://valor-app:<pw>@localhost:6379/0 after the separate #2661 rotation.
_APP_USER = "valor-app"

# Literal placeholder emitted on the report path in place of the real
# password (D8a). Keeps the secret out of /update logs and the PR body —
# CLAUDE.md's Secrets section requires this unconditionally.
_PASSWORD_PLACEHOLDER = "<REDIS_APP_PASSWORD>"

# Fallback default-user rule suffix if `ACL LIST` is reachable but somehow
# omits a `default` line (should not happen on a stock Redis — defensive
# only). Matches this machine's current default user verbatim (freshness
# check, docs/plans/redis-flush-hardening.md).
_FALLBACK_DEFAULT_SUFFIX = "on nopass sanitize-payload ~* &* +@all"

# The aclfile directive staged for the operator (D8). Never written to
# redis.conf by this module — text only, for the runbook / result.
_ACLFILE_DIRECTIVE = "aclfile /opt/homebrew/etc/users.acl"


@dataclass
class RedisAclResult:
    """Result of ``apply_redis_acl``. Mirrors ``RedisReplicationResult`` plus
    the report-only fields (``planned_commands``, ``drift``,
    ``aclfile_directive``)."""

    success: bool
    action: str  # "reported" | "applied" | "applied_with_warning" | "skipped" | "failed"
    planned_commands: list[str] = field(default_factory=list)
    drift: bool = False
    aclfile_directive: str | None = None
    warning: str | None = None
    error: str | None = None


def _run(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a subprocess, capture stdout/stderr, return the result."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _redis_cli() -> str | None:
    """Return the path to redis-cli, or None if absent."""
    return shutil.which("redis-cli")


def _parse_acl_list(stdout: str) -> dict[str, str]:
    """Parse ``ACL LIST`` output into ``{username: rule_suffix}``.

    Each line looks like ``user default on nopass sanitize-payload ~* &*
    +@all``. This is a best-effort parse sufficient for this module's narrow
    needs (detecting flush-related grants); it is not a general ACL engine.
    """
    users: dict[str, str] = {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("user "):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        username = parts[1]
        suffix = parts[2] if len(parts) > 2 else ""
        users[username] = suffix
    return users


def _grants_command(rule_suffix: str, command: str) -> bool:
    """Best-effort: does this rule suffix currently grant ``command``?

    Walks tokens in order (later tokens override earlier ones for the same
    scope), tracking only ``+@all``/``-@all`` and the exact ``+command``/
    ``-command`` tokens — sufficient to detect flushdb/flushall grants on the
    two users this module manages. Not a full ACL selector/category engine.
    """
    allowed = False
    plus = f"+{command}"
    minus = f"-{command}"
    for tok in rule_suffix.split():
        if tok == "+@all":
            allowed = True
        elif tok == "-@all":
            allowed = False
        elif tok == plus:
            allowed = True
        elif tok == minus:
            allowed = False
    return allowed


def _valor_app_command(password_token: str) -> str:
    """Build the complete declarative ``ACL SETUSER valor-app ...`` command.

    Always a full rule set, never a delta (Race 3) — issuing it twice
    converges to the same state regardless of interleaving.
    """
    return f"ACL SETUSER {_APP_USER} on >{password_token} ~* &* +@all -flushdb -flushall"


def _default_command(existing_suffix: str) -> str:
    """Build the complete declarative ``ACL SETUSER default ...`` command.

    Preserves every existing ``default`` rule token and appends ``-flushall``
    only if not already present (idempotent — re-running never duplicates
    the token). ``flushdb`` is deliberately left alone: the test suite's bare
    ``redis.Redis(db=N)`` clients authenticate as ``default`` and must keep
    working with zero test-side changes (D3 / the seam against #2628).
    """
    tokens = existing_suffix.split()
    if "-flushall" in tokens:
        suffix = existing_suffix
    else:
        suffix = f"{existing_suffix} -flushall".strip()
    return f"ACL SETUSER default {suffix}"


def _plan_commands(users: dict[str, str]) -> tuple[list[str], bool]:
    """Compute the four planned commands and the drift flag from a parsed
    ``ACL LIST``.

    The four commands are the complete converge-and-verify sequence D8/D8a
    specify: (a) the ``valor-app`` declarative grant (placeholder password on
    the report path — D8a, this path never depends on ``REDIS_APP_PASSWORD``),
    (b) the ``default`` declarative grant with ``flushall`` removed and every
    other existing rule preserved, (c) ``ACL SAVE`` (persistence — a no-op at
    the ACL layer if no ``aclfile`` is loaded, downgraded to a warning on the
    apply path rather than treated as failure), and (d) a post-apply
    ``ACL GETUSER valor-app`` verification read (Race 3: the apply path
    re-reads both users after issuing the SETUSERs and reports a mismatch
    rather than retrying).
    """
    default_suffix = users.get("default", _FALLBACK_DEFAULT_SUFFIX)
    planned = [
        _valor_app_command(_PASSWORD_PLACEHOLDER),
        _default_command(default_suffix),
        "ACL SAVE",
        f"ACL GETUSER {_APP_USER}",
    ]

    valor_app_suffix = users.get(_APP_USER)
    drift = (
        valor_app_suffix is None
        or _grants_command(valor_app_suffix, "flushdb")
        or _grants_command(valor_app_suffix, "flushall")
        or _grants_command(default_suffix, "flushall")
    )
    return planned, drift


def _read_acl_list(cli: str) -> tuple[dict[str, str] | None, str | None]:
    """Read and parse ``ACL LIST``. Returns ``(users, error)`` — exactly one
    is ``None``. Never raises."""
    try:
        ping = _run([cli, "PING"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"redis-cli ping failed ({exc})"

    if ping.returncode != 0 or "PONG" not in ping.stdout.upper():
        stderr = ping.stderr.strip() or ping.stdout.strip() or "no output"
        return None, f"Redis not reachable (ping returned {ping.returncode}: {stderr})"

    try:
        acl_list = _run([cli, "ACL", "LIST"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"ACL LIST failed ({exc})"

    if acl_list.returncode != 0:
        stderr = acl_list.stderr.strip() or acl_list.stdout.strip() or "no output"
        return None, f"ACL LIST returned {acl_list.returncode}: {stderr}"

    users = _parse_acl_list(acl_list.stdout)
    if not users:
        return None, "ACL LIST output unparseable — no users found"

    return users, None


def apply_redis_acl(apply: bool = False) -> RedisAclResult:
    """Plan (and, only on explicit opt-in, apply) the target Redis ACL rule set.

    ``apply=False`` (the default, and the **only** value ``/update`` ever
    passes) is report-only: it reads ``ACL LIST``, computes the four commands
    that would converge the server onto the target rule set, and returns
    them. It issues no ``ACL SETUSER``, no ``ACL SAVE``, and writes no file.

    ``apply=True`` additionally requires, in order: the marker file
    ``ACL_MARKER_FILE`` present, and ``REDIS_ACL_APPLY=true`` in the
    environment. Either missing -> ``action="skipped"`` naming which gate
    failed, with **zero** server interaction (no redis-cli invocation at
    all) — see the apply-gate test matrix in ``tests/unit/test_redis_acl.py``.
    Only once both gates are open does it check ``REDIS_APP_PASSWORD``
    (D8a): unset -> ``action="skipped", error="REDIS_APP_PASSWORD unset"``
    (``skipped``, not a failure — a machine without the secret should log
    quietly, not warn, on every ``/update``-adjacent run). Only then does it
    issue the four commands for real.

    Never raises. Every failure path (redis-cli absent, Redis down, ``ACL
    LIST`` unparseable) returns ``success=False`` with a populated ``error``.
    """
    try:
        return _apply_redis_acl_inner(apply)
    except Exception as exc:  # never raise — non-fatal on every path
        logger.warning("[redis_acl] unexpected error: %s", exc)
        return RedisAclResult(success=False, action="failed", error=f"unexpected error: {exc}")


def _apply_redis_acl_inner(apply: bool) -> RedisAclResult:
    if apply:
        # Both gates checked BEFORE any redis-cli invocation — a gate that
        # fails must leave zero trace on the server, provably (asserted on
        # the faked redis-cli's invocation list in tests, not just the
        # returned action string).
        try:
            marker_present = ACL_MARKER_FILE.exists()
        except OSError as exc:
            return RedisAclResult(
                success=False, action="skipped", error=f"could not read ACL marker ({exc})"
            )

        apply_flag = os.environ.get(_APPLY_ENV_VAR, "").strip().lower() == "true"

        if not (marker_present and apply_flag):
            reasons = []
            if not marker_present:
                reasons.append(f"marker file {ACL_MARKER_FILE} absent")
            if not apply_flag:
                reasons.append(f"{_APPLY_ENV_VAR} not set to 'true'")
            msg = "; ".join(reasons)
            logger.info("[redis_acl] apply skipped: %s", msg)
            return RedisAclResult(success=False, action="skipped", error=msg)

    # Report computation — always runs (report path, and apply path once
    # both gates are open).
    cli = _redis_cli()
    if cli is None:
        msg = "redis-cli not found on PATH"
        logger.warning("[redis_acl] %s", msg)
        return RedisAclResult(success=False, action="failed", error=msg)

    users, err = _read_acl_list(cli)
    if err is not None:
        logger.warning("[redis_acl] %s", err)
        return RedisAclResult(success=False, action="failed", error=err)

    planned, drift = _plan_commands(users)

    if not apply:
        return RedisAclResult(
            success=True,
            action="reported",
            planned_commands=planned,
            drift=drift,
            aclfile_directive=_ACLFILE_DIRECTIVE,
        )

    # Apply path, gates already satisfied: D8a — password checked ONLY here.
    password = os.environ.get("REDIS_APP_PASSWORD", "").strip()
    if not password:
        return RedisAclResult(
            success=False,
            action="skipped",
            planned_commands=planned,
            drift=drift,
            error="REDIS_APP_PASSWORD unset",
        )

    return _do_apply(cli, users, password, planned, drift)


def _do_apply(
    cli: str,
    users: dict[str, str],
    password: str,
    planned: list[str],
    drift: bool,
) -> RedisAclResult:
    """Issue the real ACL mutation sequence. Only reached with both operator
    gates open and a real password present. Never restarts Redis, never
    issues ``CONFIG REWRITE``/``SHUTDOWN``/``brew services restart`` (Risk 6),
    and never opens ``redis.conf`` for writing."""
    default_suffix = users.get("default", _FALLBACK_DEFAULT_SUFFIX)

    # (a) ACL SETUSER valor-app — complete declarative grant.
    try:
        proc_a = _run(
            [
                cli,
                "ACL",
                "SETUSER",
                _APP_USER,
                "on",
                f">{password}",
                "~*",
                "&*",
                "+@all",
                "-flushdb",
                "-flushall",
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RedisAclResult(
            success=False,
            action="failed",
            planned_commands=planned,
            drift=drift,
            error=f"ACL SETUSER {_APP_USER} failed ({exc})",
        )
    if proc_a.returncode != 0:
        stderr = proc_a.stderr.strip() or proc_a.stdout.strip() or "no output"
        return RedisAclResult(
            success=False,
            action="failed",
            planned_commands=planned,
            drift=drift,
            error=f"ACL SETUSER {_APP_USER} returned {proc_a.returncode}: {stderr}",
        )

    # (b) ACL SETUSER default — preserve every existing rule, add -flushall.
    default_cmd = _default_command(default_suffix)
    default_tokens = default_cmd.split()[3:]  # drop "ACL SETUSER default"
    try:
        proc_b = _run([cli, "ACL", "SETUSER", "default", *default_tokens])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RedisAclResult(
            success=False,
            action="failed",
            planned_commands=planned,
            drift=drift,
            error=f"ACL SETUSER default failed ({exc})",
        )
    if proc_b.returncode != 0:
        stderr = proc_b.stderr.strip() or proc_b.stdout.strip() or "no output"
        return RedisAclResult(
            success=False,
            action="failed",
            planned_commands=planned,
            drift=drift,
            error=f"ACL SETUSER default returned {proc_b.returncode}: {stderr}",
        )

    # (c) ACL SAVE — persistence only; downgrade to a warning if no aclfile
    # is loaded yet (the runtime grants above are already live regardless).
    warning: str | None = None
    try:
        proc_c = _run([cli, "ACL", "SAVE"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        warning = f"ACL SAVE could not run ({exc}); runtime grants are live, persistence is not"
    else:
        if proc_c.returncode != 0:
            stderr = proc_c.stderr.strip() or proc_c.stdout.strip() or "no output"
            warning = (
                f"ACL SAVE returned {proc_c.returncode}: {stderr}; runtime grants are live, "
                "persistence is not (likely no aclfile configured — see the staged "
                f"'{_ACLFILE_DIRECTIVE}' directive in the runbook)"
            )

    # (d) Re-read ACL GETUSER for both users and report a mismatch rather
    # than retrying (Race 3: a concurrent apply converges to the same
    # declarative state, so a mismatch here means something else changed it).
    mismatch: str | None = None
    try:
        proc_d1 = _run([cli, "ACL", "GETUSER", _APP_USER])
        proc_d2 = _run([cli, "ACL", "GETUSER", "default"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        mismatch = f"post-apply ACL GETUSER verification failed ({exc})"
    else:
        if proc_d1.returncode != 0:
            mismatch = f"post-apply ACL GETUSER {_APP_USER} returned {proc_d1.returncode}"
        elif proc_d2.returncode != 0:
            mismatch = f"post-apply ACL GETUSER default returned {proc_d2.returncode}"

    if mismatch is not None:
        warning = f"{warning}; {mismatch}" if warning else mismatch

    action = "applied_with_warning" if warning else "applied"
    logger.info("[redis_acl] apply complete: action=%s", action)
    return RedisAclResult(
        success=True,
        action=action,
        planned_commands=planned,
        drift=drift,
        aclfile_directive=_ACLFILE_DIRECTIVE,
        warning=warning,
    )


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report-only: print the planned ACL commands, mutate nothing (default behavior).",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Reach the gated apply path. Still a no-op unless "
            f"{ACL_MARKER_FILE} exists AND {_APPLY_ENV_VAR}=true is set."
        ),
    )
    args = parser.parse_args()

    result = apply_redis_acl(apply=bool(args.apply))

    print(f"action: {result.action}")
    print(f"success: {result.success}")
    print(f"drift: {result.drift}")
    if result.planned_commands:
        print("planned commands:")
        for cmd in result.planned_commands:
            print(f"  {cmd}")
    if result.aclfile_directive:
        print(f"staged aclfile directive (not written): {result.aclfile_directive}")
    if result.warning:
        print(f"warning: {result.warning}")
    if result.error:
        print(f"error: {result.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
