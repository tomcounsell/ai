"""Google Workspace CLI (`gws`) authentication bootstrap.

Surfaces the one-time `gws` OAuth step as a first-class part of every `/update`
run, rather than an undocumented external footnote. Companion to `gh_auth.py`,
but with a deliberately different contract:

`gh` can be authenticated non-interactively with a stored PAT, so `gh_auth`
*configures* auth. `gws` cannot — `gws auth login` opens a browser for Google
OAuth **consent**, and `gws auth setup` additionally requires `gcloud` plus a
GCP project. Those are human-gated, and `/update` also runs non-interactively
via launchd polling, so we never auto-run them. Instead this module *detects*
the auth state and surfaces an actionable instruction when setup is needed:

- `gws` not on PATH        -> skipped (not installed yet; installed by /update)
- authenticated            -> already_ok (idempotent, silent)
- present-but-unauthed     -> needs_auth (actionable WARN, non-blocking)

The step is idempotent and cron-safe: it never blocks, never opens a browser,
and only emits a warning the human can act on at their next interactive moment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Exact commands a human runs to complete first-time auth. `gws auth setup`
# provisions the GCP project + OAuth client (needs gcloud); `--login` chains
# the browser consent flow immediately after.
SETUP_HINT = "gws auth setup --login   (or: gws auth setup && gws auth login)"


@dataclass
class GwsAuthResult:
    """Result of the gws auth bootstrap check."""

    success: bool
    action: str  # "already_ok", "needs_auth", "skipped", "failed"
    detail: str | None = None
    error: str | None = None


def configure_gws_auth(project_dir: Path | None = None) -> GwsAuthResult:
    """Check `gws` auth state and surface the one-time setup step if needed.

    Detection only — never runs the interactive OAuth flow (see module docstring
    for why). Safe to call on every update run, interactive or not.

    Args:
        project_dir: Project root (unused; kept for API consistency with the
            other update modules).

    Returns:
        GwsAuthResult describing the auth state and, when unauthenticated, the
        command the human should run.
    """
    gws_bin = shutil.which("gws")
    if not gws_bin:
        # Not installed yet — the npm install step earlier in the run handles
        # presence; nothing to authenticate against. Silent skip.
        return GwsAuthResult(
            success=True,
            action="skipped",
            detail="gws not on PATH — install via `/update` (@googleworkspace/cli)",
        )

    try:
        proc = subprocess.run(
            [gws_bin, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return GwsAuthResult(
            success=False,
            action="failed",
            error="gws auth status timed out after 15s",
        )
    except OSError as exc:
        return GwsAuthResult(
            success=False,
            action="failed",
            error=f"gws auth status exec error: {exc}",
        )

    # `gws auth status` exits 0 even when unauthenticated and emits a JSON blob
    # whose `auth_method` is "none" until OAuth completes. Parse it; fall back to
    # a substring check if the output shape ever changes so we fail soft.
    authed = False
    method = "unknown"
    try:
        status = json.loads(proc.stdout)
        method = str(status.get("auth_method", "none"))
        authed = method != "none"
    except (json.JSONDecodeError, ValueError):
        # Unexpected output — treat a literal '"auth_method": "none"' as the
        # only confident unauthenticated signal; otherwise assume authed so we
        # don't nag on a parsing quirk.
        authed = '"auth_method": "none"' not in proc.stdout

    if authed:
        return GwsAuthResult(
            success=True,
            action="already_ok",
            detail=f"gws authenticated (auth_method={method})",
        )

    return GwsAuthResult(
        success=True,
        action="needs_auth",
        detail=(
            "gws is installed but not authenticated — first use needs a "
            f"one-time human OAuth step: {SETUP_HINT}"
        ),
    )
