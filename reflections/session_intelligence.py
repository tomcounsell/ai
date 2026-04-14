"""
reflections/session_intelligence.py — Session intelligence pipeline callable.

Extracted from scripts/reflections.py pipeline:
  step_session_analysis → step_llm_reflection → step_auto_fix_bugs

This is a single callable that runs all three sub-steps internally,
preserving ordering without depends_on complexity in the YAML scheduler.

Returns:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from reflections.utils import (
    CORRECTION_PATTERNS,
    THRASH_RATIO_THRESHOLD,
    has_existing_github_work,
    is_high_confidence,
    is_ignored,
    load_ignore_entries,
    load_local_projects,
    run_llm_reflection,
)

logger = logging.getLogger("reflections.session_intelligence")


def _analyze_sessions_from_redis(target_date: str) -> dict:
    """Analyze sessions using Redis AgentSession and BridgeEvent models."""
    result: dict = {
        "sessions_analyzed": 0,
        "corrections": [],
        "thrash_sessions": [],
        "error_patterns": [],
    }

    try:
        from models.agent_session import AgentSession

        all_sessions = AgentSession.query.all()
        target_sessions = []
        for session in all_sessions:
            if session.started_at:
                sa = session.started_at
                if isinstance(sa, datetime):
                    session_date = sa.strftime("%Y-%m-%d")
                else:
                    session_date = datetime.fromtimestamp(sa).strftime("%Y-%m-%d")
                if session_date == target_date:
                    target_sessions.append(session)

        target_sessions.sort(key=lambda s: s.turn_count or 0, reverse=True)
        target_sessions = target_sessions[:20]

        for session in target_sessions:
            result["sessions_analyzed"] += 1
            session_id = session.session_id or "unknown"

            tool_calls = session.tool_call_count or 0
            turn_count = session.turn_count or 0
            if tool_calls > 5 and turn_count > 0:
                failure_ratio = max(0.0, 1.0 - (turn_count / tool_calls))
                if failure_ratio > THRASH_RATIO_THRESHOLD:
                    result["thrash_sessions"].append(
                        {
                            "session_id": session_id,
                            "tool_calls": tool_calls,
                            "successes": turn_count,
                            "failure_ratio": round(failure_ratio, 2),
                        }
                    )

            if session.log_path:
                log_path = Path(session.log_path)
                if log_path.exists():
                    try:
                        content = log_path.read_text()
                        for line in content.splitlines():
                            if "USER:" in line or "user:" in line:
                                for pattern in CORRECTION_PATTERNS:
                                    if pattern.search(line):
                                        result["corrections"].append(
                                            {
                                                "session_id": session_id,
                                                "message": line.strip()[:200],
                                                "pattern": pattern.pattern,
                                            }
                                        )
                                        break
                    except OSError:
                        pass

            if session.status == "failed":
                summary_text = (session.summary or "")[:200]
                if not summary_text.strip():
                    logger.warning("Skipping failed session %s with empty summary", session_id)
                    continue
                result["error_patterns"].append(
                    {
                        "session_id": session_id,
                        "status": "failed",
                        "summary": summary_text,
                    }
                )

        try:
            from models.bridge_event import BridgeEvent

            all_events = BridgeEvent.query.filter(event_type="error")
            for event in all_events:
                if event.timestamp:
                    event_date = datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d")
                    if event_date == target_date:
                        result["error_patterns"].append(
                            {
                                "event_type": "bridge_error",
                                "data": event.data or {},
                                "timestamp": event_date,
                            }
                        )
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"Redis session analysis failed: {e}")

    return result


async def run() -> dict:
    """Run the full session intelligence pipeline.

    Pipeline: Session Analysis → LLM Reflection → Bug Issue Filing

    Maps to monolith: step_session_intelligence (which calls step_session_analysis,
    step_llm_reflection, step_auto_fix_bugs in sequence)

    Raises exceptions on sub-step failure (propagates to scheduler for
    last_status=error tracking).
    """
    from bridge.utc import utc_now

    findings: list[str] = []
    yesterday = (utc_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Sub-step 1: Session analysis
    analysis = _analyze_sessions_from_redis(yesterday)

    if analysis["corrections"]:
        findings.append(
            f"Detected {len(analysis['corrections'])} user corrections "
            f"across {analysis['sessions_analyzed']} sessions"
        )
    if analysis["thrash_sessions"]:
        findings.append(
            f"Detected {len(analysis['thrash_sessions'])} thrashing sessions (high failure ratio)"
        )
    error_patterns = analysis.get("error_patterns", [])
    if error_patterns:
        findings.append(f"Found {len(error_patterns)} error patterns in sessions/events")

    logger.info(
        f"Session analysis: sessions={analysis['sessions_analyzed']}, "
        f"corrections={len(analysis['corrections'])}, "
        f"thrash={len(analysis['thrash_sessions'])}"
    )

    # Sub-step 2: LLM reflection
    reflections_list = run_llm_reflection(analysis)
    if reflections_list:
        findings.append(f"Generated {len(reflections_list)} reflection entries")
        for r in reflections_list:
            findings.append(
                f"[{r.get('category', '?')}] {r.get('summary', '')} "
                f"(pattern: {r.get('pattern', '')[:60]})"
            )

    # Sub-step 3: Auto-fix bugs (file GitHub issues)
    enabled = os.environ.get("REFLECTIONS_AUTO_FIX_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("REFLECTIONS_AUTO_FIX_ENABLED is false, skipping bug issue step")
    else:
        ignore_entries = load_ignore_entries()
        candidates = [r for r in reflections_list if is_high_confidence(r)]
        logger.info(
            f"Bug issues: {len(candidates)} candidate(s) from {len(reflections_list)} reflection(s)"
        )

        projects = load_local_projects()
        project_wd = None
        for project in projects:
            if project.get("github"):
                project_wd = project["working_directory"]
                break

        for r in candidates:
            pattern = r.get("pattern", "")
            summary = r.get("summary", "")
            prevention = r.get("prevention", "")

            if is_ignored(pattern, ignore_entries):
                logger.info(f"Bug issues: skipping ignored pattern: {pattern[:60]}")
                findings.append(f"Ignored (in ignore log): {summary[:80]}")
                continue

            if project_wd and has_existing_github_work(pattern, project_wd):
                logger.info(f"Bug issues: duplicate found for pattern: {pattern[:60]}")
                findings.append(f"Skipped (existing PR/issue): {summary[:80]}")
                continue

            if not project_wd:
                logger.warning("Bug issues: no project with github config found, skipping")
                continue

            issue_body = (
                f"## Bug Pattern\n\n{pattern}\n\n"
                f"## Summary\n\n{summary}\n\n"
                f"## Suggested Prevention\n\n{prevention}\n\n"
                f"---\n*Filed automatically by the reflections system.*"
            )
            issue_title = f"Bug: {summary[:80]}"
            logger.info(f"Bug issues: creating issue for: {summary[:80]}")

            try:
                result = subprocess.run(
                    [
                        "gh",
                        "issue",
                        "create",
                        "--title",
                        issue_title,
                        "--body",
                        issue_body,
                        "--label",
                        "bug",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=project_wd,
                )
                if result.returncode == 0:
                    issue_url = result.stdout.strip()
                    findings.append(f"Issue created: {issue_url}")
                    logger.info(f"Bug issues: created {issue_url}")
                else:
                    output_snippet = (result.stderr or result.stdout or "")[:200]
                    findings.append(f"Issue creation failed: {summary[:80]}")
                    logger.warning(f"Bug issues: gh issue create failed: {output_snippet}")
            except Exception as e:
                logger.warning(f"Bug issues: error: {e}")
                findings.append(f"Issue creation error: {summary[:80]}: {e}")

    summary = (
        f"Session intelligence: {analysis['sessions_analyzed']} sessions analyzed, "
        f"{len(reflections_list)} reflections, {len(findings)} finding(s)"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


# Import needed at module bottom to avoid circular
from datetime import timedelta  # noqa: E402
