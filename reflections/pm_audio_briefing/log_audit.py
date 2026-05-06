"""
reflections/pm_audio_briefing/log_audit.py — Log-audit slot.

Per-project text-only summary of the previous day's logs (file sizes, error
counts, regression markers, optional Sentry counts).

This slot is PURE — it does NOT enqueue Telegram payloads, does NOT release
the SETNX lock, does NOT mark the per-project Reflection record. The
dispatcher in ``__init__.py`` owns all side effects.

Output format: a plain-text findings block (no audio). The dispatcher
delivers it as a Telegram text message rather than a voice note.

This module owns the bounded log reader and Sentry helper that used to
live in ``reflections/auditing.py`` (where they were called by
``run_log_review``). The helpers were inlined as part of issue #1292 (the
legacy ``daily-log-review`` registry entry was retired in the same cutover).
``extract_structured_errors`` stays in ``reflections.utils`` because
``run_hooks_audit`` still imports it from there.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from reflections.utils import extract_structured_errors

logger = logging.getLogger("reflections.pm_audio_briefing.log_audit")


SLOT_TYPE = "log_audit"

# Bound the bytes read from any single log file so a runaway log
# (e.g. ``logs/worker.log``) cannot stall the reflection scheduler. If a
# file exceeds ``_LOG_READ_MAX_BYTES``, the reader tail-reads only the last
# ``_LOG_READ_TAIL_BYTES``. Constants originated as a hotfix (sibling of
# PR #1056) in ``reflections/auditing.py`` and were inlined here in #1292.
_LOG_READ_MAX_BYTES = 50 * 1024 * 1024  # 50 MB trip point
_LOG_READ_TAIL_BYTES = 1 * 1024 * 1024  # Tail-read the last 1 MB


def _read_log_text_bounded(log_file: Path) -> str:
    """Read a log file as text, tail-reading if it exceeds the size cap.

    Returns the decoded text content (errors replaced). Always closes the
    file. If the file is larger than ``_LOG_READ_MAX_BYTES``, only the last
    ``_LOG_READ_TAIL_BYTES`` are returned (with a leading truncation marker).
    """
    try:
        size = os.path.getsize(log_file)
    except OSError:
        size = 0

    if size > _LOG_READ_MAX_BYTES:
        # Seek from end to avoid loading a multi-GB file into memory.
        with open(log_file, "rb") as f:
            f.seek(-_LOG_READ_TAIL_BYTES, os.SEEK_END)
            chunk = f.read()
        text = chunk.decode("utf-8", errors="replace")
        return f"[... truncated: showing last {_LOG_READ_TAIL_BYTES} bytes of {size} ...]\n{text}"

    with open(log_file, encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_log_tail_lines(log_file: Path, n: int = 1000) -> list[str]:
    """Return the last ``n`` lines of a log file, honoring the size cap."""
    text = _read_log_text_bounded(log_file)
    lines = text.splitlines(keepends=True)
    return lines[-n:]


def _collect_sentry_counts(project: dict) -> str | None:
    """Best-effort Sentry unresolved-issues count for a single project.

    Returns a one-line summary string when the project has ``SENTRY_DSN``
    configured AND ``sentry-cli`` is on PATH AND the call succeeds within
    10 seconds. Returns ``None`` on any error condition (missing CLI,
    missing DSN, subprocess failure, timeout, JSON decode error, etc.).
    Sentry data is purely additive — never raises.
    """
    if shutil.which("sentry-cli") is None:
        return None

    project_dir = Path(project.get("working_directory", ""))
    if not project_dir.is_dir():
        return None

    # Cheap inline DSN probe: read the project's .env if present. We do not
    # rely on the parent worker's process env because the slot runs
    # cross-project and each project has its own DSN.
    env_file = project_dir / ".env"
    sentry_dsn = ""
    if env_file.is_file():
        try:
            for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if line.startswith("SENTRY_DSN="):
                    sentry_dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            return None
    if not sentry_dsn:
        return None

    try:
        proc_env = {**os.environ, "SENTRY_DSN": sentry_dsn}
        result = subprocess.run(
            ["sentry-cli", "issues", "list", "--status", "unresolved", "--json"],
            cwd=str(project_dir),
            env=proc_env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None
        issues = json.loads(result.stdout) if result.stdout else []
        count = len(issues) if isinstance(issues, list) else 0
        if count == 0:
            return None
        return f"[{project.get('slug', '?')}] Sentry: {count} unresolved issue(s)"
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    except json.JSONDecodeError:
        return None
    except Exception as e:
        logger.warning(f"[log-audit] sentry-cli unexpected error for {project.get('slug')}: {e}")
        return None


def _scan_project_logs(project: dict) -> list[str]:
    """Run the per-project log scan, returning a list of finding strings.

    Defensive against missing ``logs/`` dirs. All single-file failures are
    swallowed so one bad file doesn't abort the rest of the scan.
    """
    findings: list[str] = []
    slug = project.get("slug", "?")
    project_dir = Path(project.get("working_directory", ""))
    logs_dir = project_dir / "logs"

    if not logs_dir.exists():
        return findings

    log_files = list(logs_dir.glob("*.log"))
    for log_file in log_files:
        if not log_file.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=UTC)
            if mtime < datetime.now(UTC) - timedelta(days=7):
                findings.append(f"[{slug}] Log file {log_file.name} is older than 7 days")

            size_mb = log_file.stat().st_size / (1024 * 1024)
            if size_mb > 10:
                findings.append(
                    f"[{slug}] Log file {log_file.name} is {size_mb:.1f}MB - consider rotation"
                )

            try:
                errors = extract_structured_errors(log_file)
            except Exception as exc:  # swallow-ok: per-file scan failure
                errors = []
                logger.debug(
                    "[%s] structured-error scan failed for %s: %s", slug, log_file.name, exc
                )
            if errors:
                findings.append(
                    f"[{slug}] {log_file.name}: {len(errors)} structured errors extracted"
                )
                for error in errors[-5:]:
                    msg = error["message"][:200]
                    findings.append(f"  [{error['level']}] {error['timestamp']}: {msg}")

            lines = _read_log_tail_lines(log_file, n=1000)
            warning_count = sum(1 for line in lines if "WARNING" in line)
            if warning_count > 10:
                findings.append(
                    f"[{slug}] {log_file.name}: {warning_count} warnings in recent logs"
                )

            log_content = _read_log_text_bounded(log_file)
            stale_index_count = log_content.count("Stale index entry")
            if stale_index_count > 0:
                findings.append(
                    f"[{slug}] {log_file.name}: {stale_index_count} 'Stale index entry' "
                    "warnings (regression marker for #898)"
                )
        except Exception as e:  # swallow-ok: one file's scan must not abort
            findings.append(f"[{slug}] Could not analyze {log_file.name}: {e}")

    sentry_line = _collect_sentry_counts(project)
    if sentry_line:
        findings.append(sentry_line)

    return findings


def build(project: dict, slot_config: dict) -> tuple[str, str, dict[str, Any]]:
    """Build the per-project log-audit slot.

    Returns:
        ``(transcript, followup_markdown, raw_signals)``. ``transcript`` is
        empty (this slot ships text only — no audio). ``followup_markdown``
        is the formatted findings block. ``raw_signals`` carries the raw
        list of findings under the ``findings`` key for skip-when-empty
        detection.
    """
    findings = _scan_project_logs(project)

    raw_signals: dict[str, list[dict]] = {"findings": [{"text": f} for f in findings]}

    pm = project.get("pm_briefing") or {}
    skip_when_empty = bool(slot_config.get("skip_when_empty", pm.get("skip_when_empty", True)))
    if not findings and skip_when_empty:
        return ("", "", {})

    slug = project.get("slug", "?")
    report_date = datetime.now(UTC).strftime("%Y-%m-%d")
    if findings:
        body_lines = [
            f"## Daily Log Audit — {report_date} ({slug})",
            f"{len(findings)} finding(s)",
            "",
        ]
        for f in findings[:30]:
            body_lines.append(f"- {f}")
        if len(findings) > 30:
            body_lines.append(f"({len(findings) - 30} more findings — see worker.log)")
        followup_md = "\n".join(body_lines) + "\n"
    else:
        followup_md = (
            f"## Daily Log Audit — {report_date} ({slug})\n"
            "0 findings — log-scan pipeline healthy.\n"
        )

    return ("", followup_md, raw_signals)
