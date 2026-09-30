"""GitHub CLI authentication module.

Configures `gh` to use GITHUB_PAT as the primary GitHub access token.
Called during every update run so all machines stay consistently authenticated.

The step is idempotent: if `gh auth status` already shows the correct token
host, we skip the re-auth. If the PAT is absent or empty in .env, we skip with
a warning rather than clearing an existing valid auth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GhAuthResult:
    """Result of GitHub CLI auth configuration."""

    success: bool
    action: str  # "configured", "already_ok", "skipped", "failed"
    detail: str | None = None
    error: str | None = None


def configure_gh_auth(project_dir: Path | None = None) -> GhAuthResult:
    """Configure gh CLI with GITHUB_PAT from the environment.

    Reads GITHUB_PAT from the process environment (which remote-update.sh
    sources from .env before invoking run.py). If the variable is set and
    non-empty, runs ``echo "$PAT" | gh auth login --with-token`` to configure
    github.com authentication. Idempotent: safe to call on every update run.

    Args:
        project_dir: Project root (unused; kept for API consistency with other
            update modules).

    Returns:
        GhAuthResult describing what happened.
    """
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return GhAuthResult(
            success=False,
            action="skipped",
            detail="gh CLI not found on PATH — install via `brew install gh`",
        )

    pat = os.environ.get("GITHUB_PAT", "").strip()
    if not pat:
        return GhAuthResult(
            success=False,
            action="skipped",
            detail="GITHUB_PAT not set in environment — skipping gh auth",
        )

    # gh refuses `auth login` when GITHUB_TOKEN is set in the environment
    # (it treats the env var as the active credential and rejects the command).
    # Strip both GITHUB_TOKEN and GH_TOKEN from the subprocess env so both the
    # status pre-check and the login operate on gh's stored credential, not the
    # env var. We are intentionally relying on the PAT stored via gh's keyring.
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)

    # Idempotency: if gh's stored credential already authenticates against
    # github.com, skip the re-login entirely. Re-running `gh auth login
    # --with-token` when the token is already in the keyring trips a gh quirk
    # (observed on 2.88.1) that fails with a misleading "no token found"
    # despite the token being valid. Checking status first avoids that noise
    # and matches this module's documented idempotent contract.
    try:
        precheck = subprocess.run(
            [gh_bin, "auth", "status", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if precheck.returncode == 0:
            return GhAuthResult(
                success=True,
                action="already_ok",
                detail=(precheck.stdout.strip() or precheck.stderr.strip()),
            )
    except (subprocess.TimeoutExpired, OSError):
        # Best-effort: fall through to an explicit login if the status check
        # cannot run for any reason.
        pass

    # Attempt the login
    try:
        proc = subprocess.run(
            [gh_bin, "auth", "login", "--with-token"],
            input=pat,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return GhAuthResult(
            success=False,
            action="failed",
            error="gh auth login timed out after 30s",
        )
    except OSError as exc:
        return GhAuthResult(
            success=False,
            action="failed",
            error=f"gh auth login exec error: {exc}",
        )

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        return GhAuthResult(
            success=False,
            action="failed",
            error=f"gh auth login failed (exit {proc.returncode}): {stderr}",
        )

    # Verify: confirm gh can reach github.com
    try:
        status = subprocess.run(
            [gh_bin, "auth", "status", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        detail = status.stdout.strip() or status.stderr.strip()
        if status.returncode == 0:
            return GhAuthResult(
                success=True,
                action="configured",
                detail=detail,
            )
        # Login succeeded but status check returned non-zero — still treat as ok
        return GhAuthResult(
            success=True,
            action="configured",
            detail=f"login ok; status check: {detail}",
        )
    except Exception as exc:
        # Login succeeded; status verification is best-effort
        return GhAuthResult(
            success=True,
            action="configured",
            detail=f"login ok; status check skipped: {exc}",
        )
