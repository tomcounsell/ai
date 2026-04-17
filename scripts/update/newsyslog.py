"""Newsyslog log rotation config installer.

Bridge and worker launchd services write stderr to `logs/bridge.error.log` and
`logs/worker_error.log` via `StandardErrorPath`. launchd holds the file descriptor
open for the lifetime of the process, which bypasses Python's `RotatingFileHandler`.
Without an external rotator these logs grow unbounded (observed: 18+ MB).

macOS's built-in `newsyslog` handles rotation for these files via a config at
`/etc/newsyslog.d/valor.conf`. Installing it requires root. This module detects
drift (missing, stale, or out-of-date) and tries a passwordless `sudo -n` install;
when sudo needs a password it returns a structured status so the caller can print
a one-line actionable message for the user instead of failing silently.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

NEWSYSLOG_DST = Path("/etc/newsyslog.d/valor.conf")


@dataclass
class NewsyslogStatus:
    """Result of a newsyslog config check/install."""

    # True when /etc/newsyslog.d/valor.conf exists and matches the rendered template.
    up_to_date: bool
    # True when we performed a write (either first-time install or drift repair).
    installed: bool
    # True when the config is missing or stale AND we could not install.
    needs_sudo: bool
    # Absolute path of the rendered template (suitable for documentation).
    template_path: Path
    # One-line human message when user action is required; empty when up-to-date.
    action_message: str = ""


def _render_template(project_dir: Path) -> str | None:
    template_path = project_dir / "config" / "newsyslog.conf.template"
    if not template_path.exists():
        return None
    import os

    return (
        template_path.read_text()
        .replace("__PROJECT_DIR__", str(project_dir))
        .replace("__USERNAME__", os.environ.get("USER", os.getlogin()))
    )


def check_newsyslog(project_dir: Path) -> NewsyslogStatus:
    """Check newsyslog config state and install if a passwordless sudo is available.

    Returns a status that describes whether action is still required.
    """
    rendered = _render_template(project_dir)
    template_path = project_dir / "config" / "newsyslog.conf.template"

    if rendered is None:
        # Template missing — can't do anything. Treat as up-to-date since there
        # is nothing to install.
        return NewsyslogStatus(
            up_to_date=True,
            installed=False,
            needs_sudo=False,
            template_path=template_path,
        )

    # Compare against the installed file.
    try:
        current = NEWSYSLOG_DST.read_text()
    except (FileNotFoundError, PermissionError):
        current = None

    if current == rendered:
        return NewsyslogStatus(
            up_to_date=True,
            installed=False,
            needs_sudo=False,
            template_path=template_path,
        )

    # Need to install or refresh. Try passwordless sudo first.
    try:
        result = subprocess.run(
            ["sudo", "-n", "tee", str(NEWSYSLOG_DST)],
            input=rendered,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return NewsyslogStatus(
                up_to_date=True,
                installed=True,
                needs_sudo=False,
                template_path=template_path,
            )
    except (subprocess.TimeoutExpired, OSError):
        pass

    # sudo needs a password — surface an actionable one-liner.
    import os

    username = os.environ.get("USER", os.getlogin())
    install_cmd = (
        f"sudo cp <(sed 's|__PROJECT_DIR__|{project_dir}|g;s|__USERNAME__|{username}|g'"
        f" {template_path}) {NEWSYSLOG_DST}"
    )
    reason = "missing" if current is None else "out-of-date"
    return NewsyslogStatus(
        up_to_date=False,
        installed=False,
        needs_sudo=True,
        template_path=template_path,
        action_message=(f"Log rotation config {reason} at {NEWSYSLOG_DST}. Run: {install_cmd}"),
    )
