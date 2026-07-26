"""reflections/audits/skills_audit.py — Per-project skills-audit reflection.

What it does: Invokes each local project's own `audit_skills.py` script,
    collects FAIL/WARN findings, and files GitHub issues for FAIL findings
    that persist across 2 consecutive runs (Redis-gated streak + dedup).
Cadence: 86400s (skill rules change slowly; daily is sufficient to catch drift)
Failure modes:
    - audit script missing -> project silently skipped via skip_if
    - subprocess timeout / bad JSON / OSError -> per-project error result
    - Redis unavailable -> issue filing skipped, telemetry continues
    - gh CLI missing / failure -> issue not filed, retried next run
Related reflections:
    - hooks-audit: sibling per-project audit over the same project list
    - pr-review-audit: also files GitHub issues for code-quality findings
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path

from config.settings import settings
from reflections.utilities import (
    PROJECT_ROOT,
    run_per_project_audit,
)

logger = logging.getLogger("reflections.auditing")

# Skills-audit issue filing — raw Redis bookkeeping namespaces, NOT
# Popoto-managed. Mirrors docs_auditor.REDIS_ISSUE_DEDUP_PREFIX precedent.
_SKILLS_AUDIT_STREAK_PREFIX = "skills_audit:streak"
_SKILLS_AUDIT_DEDUP_PREFIX = "skills_audit:issues_filed"
_SKILLS_AUDIT_LOCK_PREFIX = "skills_audit:filing_lock"

# TTLs in seconds.
_SKILLS_AUDIT_STREAK_TTL = 7 * 86400
_SKILLS_AUDIT_DEDUP_TTL = 30 * 86400
_SKILLS_AUDIT_LOCK_TTL = 60


def _skills_audit_script_path(repo_root: Path) -> Path:
    """Return the path to a repo's `audit_skills.py`.

    Dual-name window: prefers the post-rename `audit-skills` dir, then falls
    back to the pre-rename `do-skills-audit` dir, across both roots
    (`skills-global` for this repo's canonical location, `skills` for foreign
    repos that vendor the skill project-locally). This keeps an un-migrated
    foreign repo — one that still vendors the skill under the old name — audited
    by its own per-project reflection. Returns the canonical not-found path when
    none exist — callers gate on `.exists()` via `skip_if`.
    """
    # TODO(sunset): drop the do-skills-audit fallback once no configured foreign
    # repo vendors the skill under the pre-rename name.
    for skill_name in ("audit-skills", "do-skills-audit"):
        for skills_dir in ("skills-global", "skills"):
            p = repo_root / ".claude" / skills_dir / skill_name / "scripts" / "audit_skills.py"
            if p.exists():
                return p
    return repo_root / ".claude" / "skills-global" / "audit-skills" / "scripts" / "audit_skills.py"


def _skills_audit_get_redis():
    """Return the shared Popoto Redis connection (lazy import)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _skills_audit_finding_hash(project_slug: str, skill: str, rule_id: int | str) -> str:
    """Stable 16-hex hash of (project, skill, rule).

    Message text is intentionally excluded — rewording must not break dedup.
    """
    raw = f"{project_slug}/{skill}/{rule_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _resolve_repo_name_with_owner(repo_root: Path) -> str | None:
    """Look up the GitHub OWNER/NAME for a repo. Returns None on failure."""
    try:
        proc = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=str(repo_root),
        )
    except FileNotFoundError:
        logger.warning("skills_audit: gh CLI not on PATH; cannot resolve repo")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("skills_audit: gh repo view timed out for %s", repo_root)
        return None
    except Exception as exc:
        logger.warning("skills_audit: gh repo view failed for %s: %s", repo_root, exc)
        return None
    if proc.returncode != 0:
        logger.warning(
            "skills_audit: gh repo view rc=%d for %s: %s",
            proc.returncode,
            repo_root,
            proc.stderr.strip()[:200],
        )
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    name = data.get("nameWithOwner")
    return name if isinstance(name, str) and name else None


def _file_skills_audit_issue_if_streaked(
    finding: dict,
    repo_root: Path,
    project_slug: str,
    *,
    repo_name_with_owner: str | None = None,
) -> bool:
    """File a GitHub issue for a FAIL finding, gated by 2-consecutive-runs.

    Returns True iff a NEW issue was filed this call. Returns False for any
    of: streak < 2, dedup already set, lock contention, gh failure, missing
    repo identity, Redis failure (in which case telemetry continues).
    """
    skill = (finding.get("skill") or "").strip()
    rule_id = finding.get("rule")
    message = (finding.get("message") or "").strip()
    if not skill or rule_id is None:
        return False

    finding_hash = _skills_audit_finding_hash(project_slug, skill, rule_id)
    streak_key = f"{_SKILLS_AUDIT_STREAK_PREFIX}:{finding_hash}"
    dedup_key = f"{_SKILLS_AUDIT_DEDUP_PREFIX}:{finding_hash}"
    lock_key = f"{_SKILLS_AUDIT_LOCK_PREFIX}:{finding_hash}"

    try:
        r = _skills_audit_get_redis()
    except Exception as exc:
        logger.warning("skills_audit: Redis unavailable, skipping issue filing: %s", exc)
        return False

    # Per-finding filing lock to prevent double-fire on concurrent reflection ticks.
    try:
        if not r.set(lock_key, "1", nx=True, ex=_SKILLS_AUDIT_LOCK_TTL):
            return False
    except Exception as exc:
        logger.warning("skills_audit: lock set failed for %s: %s", lock_key, exc)
        return False

    try:
        # Streak counter — INCR semantics: first writer creates and sets TTL,
        # subsequent writers just bump. We set TTL after INCR to refresh it
        # so flapping doesn't expire the counter mid-cycle.
        try:
            streak = int(r.incr(streak_key))
            r.expire(streak_key, _SKILLS_AUDIT_STREAK_TTL)
        except Exception as exc:
            logger.warning("skills_audit: streak INCR failed for %s: %s", streak_key, exc)
            return False

        if streak < 2:
            return False

        try:
            if r.exists(dedup_key):
                return False
        except Exception as exc:
            logger.warning("skills_audit: dedup EXISTS failed: %s", exc)
            return False

        # Resolve target repo identity (cacheable by caller).
        repo_id = repo_name_with_owner or _resolve_repo_name_with_owner(repo_root)
        if not repo_id:
            logger.warning(
                "skills_audit: cannot resolve gh repo for %s, skipping filing", repo_root
            )
            return False

        title = f"skills-audit FAIL: {skill} (rule {rule_id})"
        body = (
            f"The `skills-audit` reflection observed a FAIL finding on **2 consecutive runs** "
            f"for skill `{skill}` (rule {rule_id}) in `{project_slug}`.\n\n"
            f"**Message:** {message or '(no message)'}\n\n"
            f"This issue was auto-filed by the `skills-audit` reflection. "
            f"It will NOT be re-filed for 30 days even if the finding persists. "
            f"Close this issue to silence; the streak counter will reset naturally "
            f"when the underlying rule passes."
        )
        try:
            proc = subprocess.run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    repo_id,
                    "--title",
                    title,
                    "--body",
                    body,
                    "--label",
                    "skills",
                    "--label",
                    "bug",
                ],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                check=False,
                cwd=str(repo_root),
            )
        except FileNotFoundError:
            logger.warning("skills_audit: gh CLI not on PATH; cannot file issue")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("skills_audit: gh issue create timed out for %s", title)
            return False
        except Exception as exc:
            logger.warning("skills_audit: gh issue create raised: %s", exc)
            return False

        if proc.returncode != 0:
            logger.warning(
                "skills_audit: gh issue create rc=%d for %s: %s",
                proc.returncode,
                title,
                (proc.stderr or "").strip()[:200],
            )
            return False

        # Only commit dedup key AFTER gh succeeds — transient gh failures retry next run.
        try:
            r.set(dedup_key, "1", ex=_SKILLS_AUDIT_DEDUP_TTL)
        except Exception as exc:
            logger.warning("skills_audit: dedup set failed (issue already filed): %s", exc)
        return True
    finally:
        # Release lock immediately — no need to hold for full TTL.
        try:
            r.delete(lock_key)
        except Exception:
            pass


def _skills_audit_for_project(project: dict) -> dict:
    """Per-project body for skills-audit.

    Invokes the TARGET repo's copy of ``audit_skills.py`` (NOT the AI repo's
    copy) so each repo audits its own skills. The script self-derives
    REPO_ROOT from its own file location.
    """
    import time as _time

    wd = project.get("working_directory", "")
    repo_root = Path(wd) if wd else PROJECT_ROOT
    audit_script = _skills_audit_script_path(repo_root)

    t0 = _time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(audit_script), "--no-sync", "--json"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
            cwd=str(repo_root),
        )
        audit_data = json.loads(result.stdout) if result.stdout else {}
        sub_summary = audit_data.get("summary", {})
        fails = sub_summary.get("fail", 0)
        warns = sub_summary.get("warn", 0)
        total = sub_summary.get("total_skills", 0)

        findings: list[str] = []
        fail_findings: list[dict] = []
        if fails > 0:
            findings.append(f"{fails} skill(s) have FAIL findings")
            for f in audit_data.get("findings", []):
                if f.get("severity") == "FAIL":
                    findings.append(f"  {f.get('skill')}: {f.get('message')}")
                    fail_findings.append(f)

        # Issue filing: gated by 2-consecutive-runs streak. Resolve repo
        # identity once per project per run.
        issues_filed = 0
        if fail_findings:
            project_slug = project.get("slug", "?")
            repo_id = _resolve_repo_name_with_owner(repo_root)
            if repo_id:
                for f in fail_findings:
                    try:
                        if _file_skills_audit_issue_if_streaked(
                            f, repo_root, project_slug, repo_name_with_owner=repo_id
                        ):
                            issues_filed += 1
                    except Exception as exc:
                        logger.warning(
                            "skills_audit: issue filing raised for %s: %s",
                            f.get("skill"),
                            exc,
                        )

        return {
            "status": "ok",
            "findings": findings,
            "summary": (
                f"Skills audit: {total} skills, {fails} fails, {warns} warns, "
                f"{issues_filed} issues filed"
            ),
            "duration": _time.time() - t0,
            "issues_filed": issues_filed,
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.error(f"Skills audit failed for {project.get('slug')}: {e}")
        return {
            "status": "error",
            "findings": [],
            "summary": f"Skills audit error: {e}",
            "duration": _time.time() - t0,
            "error": str(e),
        }


def run() -> dict:
    """Run skills audit per project.

    Iterates every local project that has its own
    ``.claude/skills-global/audit-skills/scripts/audit_skills.py`` script
    (or the pre-rename ``do-skills-audit`` path for un-migrated repos) and
    runs the audit there. Projects without the script are silently skipped.
    """

    def skip_if(repo_root: Path) -> bool:
        return not _skills_audit_script_path(repo_root).exists()

    return run_per_project_audit(_skills_audit_for_project, skip_if=skip_if, name="skills-audit")
