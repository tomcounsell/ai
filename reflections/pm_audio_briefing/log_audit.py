"""
reflections/pm_audio_briefing/log_audit.py — Log-audit slot.

Per-project text-only summary of the previous day's logs (file sizes, error
counts, regression markers, optional Sentry counts). Wraps the existing
``reflections.auditing`` log-scan helpers so the substantive scan logic
stays where it has tests.

This slot is PURE — it does NOT enqueue Telegram payloads, does NOT release
the SETNX lock, does NOT mark the per-project Reflection record. The
dispatcher in ``__init__.py`` owns all side effects.

Output format: a plain-text findings block (no audio). The dispatcher
delivers it as a Telegram text message rather than a voice note.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("reflections.pm_audio_briefing.log_audit")


SLOT_TYPE = "log_audit"


def _scan_project_logs(project: dict, yesterday: str) -> list[str]:
    """Run the per-project log scan, returning a list of finding strings.

    Mirrors the per-project body of ``reflections.auditing.run_log_review``,
    scoped to a single project. Defensive against missing ``logs/`` dirs.
    """
    from reflections.auditing import (
        _collect_sentry_counts,
        _read_log_tail_lines,
        _read_log_text_bounded,
        extract_structured_errors,
    )

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
    from bridge.utc import utc_now

    yesterday = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    findings = _scan_project_logs(project, yesterday)

    raw_signals: dict[str, list[dict]] = {"findings": [{"text": f} for f in findings]}

    pm = project.get("pm_briefing") or {}
    skip_when_empty = bool(slot_config.get("skip_when_empty", pm.get("skip_when_empty", True)))
    if not findings and skip_when_empty:
        return ("", "", {})

    slug = project.get("slug", "?")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if findings:
        body_lines = [
            f"## Daily Log Audit — {today} ({slug})",
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
            f"## Daily Log Audit — {today} ({slug})\n0 findings — log-scan pipeline healthy.\n"
        )

    return ("", followup_md, raw_signals)
