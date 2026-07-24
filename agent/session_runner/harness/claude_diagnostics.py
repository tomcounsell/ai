"""Sanitized spawn diagnostics + early-exit classification for the ``claude -p``
harness child (issue #2100).

A worker-spawned Claude Code CLI child resolves on some machines to a
version-named binary (e.g. ``/Users/.../claude/versions/2.1.202``), so macOS
logs it as the bare process name ``2.1.202`` and cannot be mapped back to Claude
Code. When that child hits a TLS/trust evaluation failure it can surface a
*destructive* macOS Keychain dialog whose "Reset to Defaults" would delete the
login keychain. This module gives the harness three containment primitives:

1. ``describe_claude_binary`` / ``describe_auth_mode`` / ``trust_env_presence`` /
   ``build_spawn_diagnostic`` — a sanitized, secret-free pre-exec record that
   attributes the version-named child back to Claude Code.
2. ``HarnessExitClass`` + ``classify_harness_early_exit`` — a stderr/exit-shape
   classifier that separates a TLS/trust failure (the destructive-dialog class)
   from an auth failure, a missing binary, a stale resume UUID, or a generic
   nonzero exit. **TLS wins over auth** — that precedence is the load-bearing
   contract.
3. ``HARNESS_TLS_CONSECUTIVE_SUPPRESS`` — the streak threshold at which the
   caller stops retrying a repeated TLS/trust failure (a retry only re-triggers
   the same dialog).

**No macOS Keychain read/write/reset here** and **no secret values** ever appear
in a diagnostic — auth mode is reported present/absent only.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from enum import StrEnum

# Number of *consecutive* TLS_TRUST early-exits after which the caller suppresses
# the stale-UUID fresh-session retry (a repeated hard TLS failure would only
# re-trigger the destructive Keychain dialog). The FIRST TLS_TRUST exit still
# takes the normal recovery path — an intermittent chain race at keychain unlock
# could self-heal on retry, so we never treat a single match as permanent.
#
# Provisional/tunable: 2 is a grain-of-salt default (one retry, then suppress);
# override with HARNESS_TLS_CONSECUTIVE_SUPPRESS in the environment. Named
# locally (#1968 promote-vs-name-locally) — single-file knob, not promoted to
# config.settings.
HARNESS_TLS_CONSECUTIVE_SUPPRESS = int(os.environ.get("HARNESS_TLS_CONSECUTIVE_SUPPRESS", "2"))


# Bare-version basename shape, e.g. "2.1.202". When the resolved claude binary's
# basename matches this, macOS logs/dialogs show it as the process name and we
# render it as "Claude Code CLI {version}".
_BARE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


def describe_claude_binary(cmd0: str) -> dict:
    """Resolve and attribute a ``claude`` binary path without executing it.

    Resolves ``shutil.which(cmd0)`` (the on-PATH symlink), ``os.path.realpath``
    (the symlink target), and ``os.path.basename`` of the realpath. When the
    basename is a bare version number (``^\\d+\\.\\d+\\.\\d+``), sets
    ``display = "Claude Code CLI {basename}"`` and ``version = basename``;
    otherwise ``display = basename`` and ``version = None``.

    NEVER runs ``claude --version`` — that can hang under launchd TCC. The path
    basename IS the version; we read it, never execute the binary.

    Returns ``{which, realpath, basename, version, display}``. ``which`` and
    ``realpath`` are ``None`` when the binary is not found on PATH.
    """
    which = shutil.which(cmd0)
    if which:
        realpath = os.path.realpath(which)
        basename = os.path.basename(realpath)
    else:
        realpath = None
        basename = cmd0

    if basename and _BARE_VERSION_RE.match(basename):
        version: str | None = basename
        display = f"Claude Code CLI {basename}"
    else:
        version = None
        display = basename

    return {
        "which": which,
        "realpath": realpath,
        "basename": basename,
        "version": version,
        "display": display,
    }


def describe_auth_mode(proc_env: dict) -> str:
    """Report the child's auth mode by env-var *presence only* (never values).

    Returns ``"oauth"`` when ``CLAUDE_CODE_OAUTH_TOKEN`` is present, ``"api_key"``
    when ``ANTHROPIC_API_KEY`` is present (should never happen post-strip), else
    ``"unknown"``. OAuth takes precedence — the subscription posture is the
    intended one.
    """
    if proc_env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "oauth"
    if proc_env.get("ANTHROPIC_API_KEY"):
        return "api_key"
    return "unknown"


# TLS trust-material env vars the child might inherit. These are filesystem paths
# or a 0|1 flag — NOT secrets — so we report their value when present. A
# mis-pointed CA bundle or a NODE_TLS_REJECT_UNAUTHORIZED override is exactly the
# kind of thing an operator needs to see in the spawn diagnostic.
_TRUST_ENV_VARS = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
    "NODE_TLS_REJECT_UNAUTHORIZED",
)


def trust_env_presence(proc_env: dict) -> dict:
    """Report presence + value of TLS trust-material env vars.

    For each of ``SSL_CERT_FILE``, ``SSL_CERT_DIR``, ``NODE_EXTRA_CA_CERTS``,
    ``REQUESTS_CA_BUNDLE``, ``NODE_TLS_REJECT_UNAUTHORIZED``: report
    ``{"present": True, "value": <path-or-flag>}`` or ``{"present": False}``.
    These are paths / a 0|1 flag, not secrets, so the value is safe to surface.
    """
    out: dict[str, dict] = {}
    for name in _TRUST_ENV_VARS:
        val = proc_env.get(name)
        if val:
            out[name] = {"present": True, "value": val}
        else:
            out[name] = {"present": False}
    return out


def build_spawn_diagnostic(
    cmd: list[str],
    proc_env: dict,
    working_dir: str,
    session_id: str | None,
    worker_label: str,
) -> dict:
    """Compose one sanitized pre-exec spawn record for a ``claude`` child.

    Composes ``describe_claude_binary`` (on ``cmd[0]``), ``describe_auth_mode``,
    and ``trust_env_presence`` with the working dir, session id, and worker
    label. NEVER includes the prompt (``cmd[-1]``) or any secret value — auth
    mode is presence-only, trust-env values are paths/flags. This record is the
    thing we ``logger.info("[harness-spawn] %s", json.dumps(...))`` immediately
    before spawn, so the diagnostic itself proves the no-secret guarantee.
    """
    cmd0 = cmd[0] if cmd else "claude"
    binary = describe_claude_binary(cmd0)
    return {
        "worker_label": worker_label,
        "session_id": session_id,
        "working_dir": working_dir,
        "binary": binary,
        "auth_mode": describe_auth_mode(proc_env),
        "trust_env": trust_env_presence(proc_env),
    }


class HarnessExitClass(StrEnum):
    """Classification of a harness subprocess early exit (issue #2100 §2).

    ``StrEnum`` — members ARE ``str`` (per the repo convention in
    ``config/enums.py``), so the values compare equal to plain strings.
    """

    BINARY_MISSING = "binary_missing"
    AUTH_UNAVAILABLE = "auth_unavailable"
    TLS_TRUST = "tls_trust"
    STALE_UUID = "stale_uuid"
    CLEAN_NO_OUTPUT = "clean_no_output"
    GENERIC_NONZERO = "generic_nonzero"


# Curated TLS/trust stderr tokens. macOS Security-framework error names
# (MissingIntermediate, AnchorTrusted) + common OpenSSL/Node TLS fragments.
# Tunable implementation detail — the LOAD-BEARING contracts are the enum
# membership and the TLS-wins-over-auth precedence, not this exact list.
_TLS_TOKENS = (
    "missingintermediate",
    "anchortrusted",
    "unable to get local issuer",
    "self-signed certificate",
    "self signed certificate",
    "ssl certificate problem",
    "cert_",
    "certificate verify failed",
    "tls",
)

# Auth-failure stderr tokens. Also tunable implementation detail.
_AUTH_TOKENS = (
    "invalid api key",
    "authentication",
    "oauth",
    "401",
    "unauthorized",
    "credit balance",
)


def classify_harness_early_exit(
    *,
    returncode: int | None,
    stderr_snippet: str | None,
    init_seen: bool,
    result_event_fired: bool,
) -> HarnessExitClass | None:
    """Classify a harness subprocess exit into a ``HarnessExitClass`` (or None).

    Precedence (§2):

    * ``None`` — the turn completed normally (``result_event_fired`` True).
    * ``BINARY_MISSING`` — ``returncode is None`` (FileNotFoundError path).
    * ``TLS_TRUST`` — stderr matches any TLS token (case-insensitive).
    * ``AUTH_UNAVAILABLE`` — stderr matches any auth token **and not** a TLS
      token. **TLS wins over auth** (it is the destructive-dialog class) — this
      precedence is load-bearing.
    * ``STALE_UUID`` — ``init_seen`` False on an exit with no TLS/auth match
      (retained for parity with the existing stale-UUID fallback). Its condition
      is returncode-independent, so it keeps first claim on every
      ``init_seen=False`` exit — including a returncode-0 one — ahead of
      ``CLEAN_NO_OUTPUT``.
    * ``CLEAN_NO_OUTPUT`` — ``returncode == 0`` with ``init_seen`` True and no
      TLS/auth match: a benign exit-0 empty turn (returncode 0 stops
      masquerading as ``GENERIC_NONZERO``). This guard runs *after* the
      ``STALE_UUID`` check so an ``init_seen=False`` exit-0 stays error-level
      ``STALE_UUID``, not warning-level ``CLEAN_NO_OUTPUT`` (issue #2219).
    * ``GENERIC_NONZERO`` — nonzero exit, none of the above.
    """
    if result_event_fired:
        return None
    if returncode is None:
        return HarnessExitClass.BINARY_MISSING

    stderr_lc = (stderr_snippet or "").lower()

    tls_match = any(tok in stderr_lc for tok in _TLS_TOKENS)
    if tls_match:
        return HarnessExitClass.TLS_TRUST

    # TLS WINS: only reachable here when there is NO TLS match.
    if any(tok in stderr_lc for tok in _AUTH_TOKENS):
        return HarnessExitClass.AUTH_UNAVAILABLE

    if not init_seen:
        return HarnessExitClass.STALE_UUID

    # LAST guard — must sit after the STALE_UUID check (which is
    # returncode-independent) so a (returncode=0, init_seen=False) exit still
    # returns STALE_UUID. Placing it earlier would steal that case and downgrade
    # it from error-level STALE_UUID to warning-level CLEAN_NO_OUTPUT (#2219).
    if returncode == 0:
        return HarnessExitClass.CLEAN_NO_OUTPUT

    return HarnessExitClass.GENERIC_NONZERO


def describe_harness_exit_for_sentry(
    exit_class: HarnessExitClass | None,
    returncode: int | None,
    init_seen: bool,
    stderr_snippet: str | None,
) -> tuple[int, dict]:
    """Build the ``(log_level, sentry_payload)`` for a BRANCH-C harness exit.

    Pure, dependency-free level-selection + scope-payload builder so the
    BRANCH-C Sentry wiring (``agent/session_runner/harness/claude.py``) is
    unit-testable without driving the whole subprocess (issue #2219).

    ``log_level`` is ``logging.WARNING`` for ``CLEAN_NO_OUTPUT`` (a benign
    exit-0 empty turn — drops below Sentry's error threshold so it stops paging)
    and ``logging.ERROR`` for every other class.

    ``sentry_payload`` is ``{"tags", "context", "fingerprint"}``:

    * ``tags`` — ``harness_exit_class`` (the class value) and
      ``harness_returncode`` (so Sentry can facet by exit shape).
    * ``context`` — a ``harness_exit`` dict carrying ``returncode``,
      ``init_seen``, and ``stderr_snippet`` (tolerates ``stderr_snippet=None``,
      the returncode-0 case that never populates stderr).
    * ``fingerprint`` — ``["harness-exit-no-result", str(exit_class)]`` so the
      single VALOR-2M bucket splits into one Sentry issue per exit class.
    """
    log_level = logging.WARNING if exit_class == HarnessExitClass.CLEAN_NO_OUTPUT else logging.ERROR
    sentry_payload = {
        "tags": {
            "harness_exit_class": str(exit_class),
            "harness_returncode": returncode,
        },
        "context": {
            "harness_exit": {
                "returncode": returncode,
                "init_seen": init_seen,
                "stderr_snippet": stderr_snippet,
            },
        },
        "fingerprint": ["harness-exit-no-result", str(exit_class)],
    }
    return log_level, sentry_payload
