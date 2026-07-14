"""
reflections/docs_auditor.py — Unified documentation auditor substrate.

Consolidates five disjointed docs-hygiene pieces into one substrate consumed
by two callers:
  * ``run_docs_auditor`` — daily rotation reflection (Caller A)
  * ``audit()`` — synchronous public API used by the ``/do-docs`` SDLC stage
    (Caller B) via ``python -c "from reflections.docs_auditor import audit; ..."``

Public surface:
  * ``audit(primary_path, *, scope_mode, apply_mode, project_key)`` — main entrypoint
  * ``run_docs_auditor()`` — reflection callable (rotation + Telegram)
  * ``run_docs_branch_sweeper()`` — reflection callable (branch/PR cleanup)
  * ``refresh_docs_in_memory(touched_paths)`` — no-op placeholder for #1249
  * ``STALE_TERMS`` — module-level dict; edit one place to extend

Reflection callables return ``{"status": ..., "findings": [...], "summary": str}``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from config.machine import get_machine_display_name
from config.settings import settings

logger = logging.getLogger("reflections.docs_auditor")

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

# Stale-term renames. Edit this dict to extend coverage. Keys are old terms,
# values are the canonical replacement.
STALE_TERMS: dict[str, str] = {
    "SessionLog": "AgentSession",
    "RedisJob": "AgentSession",
    "session_log": "agent_session",
    "redis_job": "agent_session",
}

# Hard caps and tunables.
NEIGHBORHOOD_CAP = 20
GIT_LOG_FOLLOW_CAP = 10
LOCK_TTL_SECONDS = 3600
SWEEPER_LOCK_TTL_SECONDS = 1800
STUB_DOC_LINE_THRESHOLD = 5
STALE_BRANCH_AGE_DAYS = 7
STALE_PR_AGE_DAYS = 14

# Redis key namespace for state/locks/liveness.
REDIS_LAST_RUN_HASH = "docs_audit:last_run"
REDIS_RUNNING_KEY = "docs_audit:running:global"
REDIS_SWEEPER_RUNNING_KEY = "docs_audit:sweeper:running"
REDIS_LAST_COMPLETED_TS_KEY = "docs_audit:last_completed_run_ts"
REDIS_LAST_COMPLETED_SUMMARY_KEY = "docs_audit:last_completed_run_summary"
REDIS_ISSUE_DEDUP_PREFIX = "docs_audit:issues_filed"
REDIS_DAILY_PR_KEY = "docs_audit:prs_today"  # capped at 1 PR per calendar day

# Vault docs are picked at half the rate of repo docs by default (read-mostly).
DEFAULT_VAULT_WEIGHT = 0.5


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


def _ok_result(
    status: str,
    files_touched: list[str] | None = None,
    fixes_applied: int = 0,
    issues_filed: int = 0,
    pr_url: str | None = None,
    extras: dict | None = None,
) -> dict:
    """Build the standard substrate return value."""
    res: dict = {
        "status": status,
        "files_touched": files_touched or [],
        "fixes_applied": fixes_applied,
        "issues_filed": issues_filed,
        "pr_url": pr_url,
    }
    if extras:
        res.update(extras)
    return res


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def _get_redis():
    """Return the shared Popoto Redis connection (lazy import)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _acquire_lock(key: str = REDIS_RUNNING_KEY, ttl: int = LOCK_TTL_SECONDS) -> bool:
    """Acquire a SETNX lock. Returns True on success, False if already held."""
    try:
        r = _get_redis()
        return bool(r.set(key, "1", nx=True, ex=ttl))
    except Exception as e:
        logger.warning(f"docs_auditor: lock acquire failed for {key}: {e}")
        return False


def _release_lock(key: str = REDIS_RUNNING_KEY) -> None:
    """Release a previously acquired lock. Best-effort."""
    try:
        _get_redis().delete(key)
    except Exception as e:
        logger.warning(f"docs_auditor: lock release failed for {key}: {e}")


# ---------------------------------------------------------------------------
# Auth probes
# ---------------------------------------------------------------------------


def _check_auth() -> tuple[bool, str]:
    """Probe Anthropic auth. Returns (ok, reason).

    On non-auth network errors, returns (True, "") so transient failures do
    not disable the substrate; only invalid keys do.
    """
    try:
        import anthropic as _anth
    except ImportError:
        return False, "anthropic package is not installed"

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.lower() in ("none", "null", "false", "0"):
        return False, "ANTHROPIC_API_KEY not set"

    try:
        client = _anth.Anthropic(api_key=key)
        client.models.list()
        return True, ""
    except Exception as e:
        err = str(e).lower()
        if "authentication" in err or "api_key" in err or "auth_token" in err:
            return False, f"ANTHROPIC_API_KEY invalid or expired: {e}"
        logger.warning(f"docs_auditor: auth probe non-auth error: {e} — proceeding")
        return True, ""


def _check_embedding_auth() -> bool:
    """Optional embedding auth probe. Returns True if available, False otherwise.

    Used for graceful degradation: when False, semantic detectors are skipped
    but lexical detectors still run.
    """
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# Path slug helpers
# ---------------------------------------------------------------------------


def _path_to_slug(path: str | Path) -> str:
    """Turn a repo-relative path into a stable rotation hash field name."""
    return str(path).replace("/", "_").replace(".", "_")


def _vault_field(project_key: str, path: str | Path) -> str:
    return f"vault:{project_key}:{_path_to_slug(path)}"


# ---------------------------------------------------------------------------
# Neighborhood resolution
# ---------------------------------------------------------------------------


def _resolve_neighborhood(
    primary_path: Path,
    repo_root: Path,
    cap: int = NEIGHBORHOOD_CAP,
) -> list[Path]:
    """Expand from primary doc to its neighborhood, capped at ``cap`` files.

    Includes:
      * The primary doc itself
      * Outbound markdown links (from ``[label](path.md)``)
      * Inbound references (other docs linking back)

    Returns a deduplicated list of repo-relative paths, capped at ``cap``.
    """
    neighborhood: list[Path] = [primary_path]
    seen: set[str] = {str(primary_path)}

    full = repo_root / primary_path
    if not full.exists():
        return neighborhood

    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return neighborhood

    # Outbound markdown links to .md files
    for m in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", content):
        target = m.group(1).strip()
        # Resolve relative to the primary doc's parent
        target_path = (full.parent / target).resolve()
        try:
            rel = target_path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        rel_str = str(rel)
        if rel_str not in seen:
            seen.add(rel_str)
            neighborhood.append(rel)
            if len(neighborhood) >= cap:
                return neighborhood

    # Inbound references via grep
    try:
        result = subprocess.run(
            ["grep", "-rln", primary_path.name, "docs/"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or "docs/plans/" in line:
                continue
            if line not in seen:
                seen.add(line)
                neighborhood.append(Path(line))
                if len(neighborhood) >= cap:
                    return neighborhood
    except Exception:
        pass

    return neighborhood


def _resolve_pr_changed_files(repo_root: Path) -> list[Path]:
    """Return doc paths changed in the current PR (relative to origin/main).

    Returns an empty list if git is unavailable or the diff is empty.
    """
    try:
        # Determine merge-base with origin/main (or main if no remote).
        base = "origin/main"
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", base],
                capture_output=True,
                check=True,
                cwd=str(repo_root),
                timeout=settings.timeouts.git_subprocess_s,
            )
        except Exception:
            base = "main"

        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        files = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.endswith(".md") and not line.startswith("docs/plans/"):
                files.append(Path(line))
        return files[:NEIGHBORHOOD_CAP]
    except Exception as e:
        logger.warning(f"docs_auditor: PR-changed-files resolution failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Auto-fix detectors
# ---------------------------------------------------------------------------


_RENAME_QUERY_COUNT = 0


def _git_log_follow_renames(old_path: str, repo_root: Path) -> list[tuple[str, str]]:
    """Use ``git log --follow`` to find renames. Capped to GIT_LOG_FOLLOW_CAP per run.

    Returns list of (old_name, new_name) tuples.
    """
    global _RENAME_QUERY_COUNT
    if _RENAME_QUERY_COUNT >= GIT_LOG_FOLLOW_CAP:
        logger.warning(
            f"docs_auditor: git log --follow cap ({GIT_LOG_FOLLOW_CAP}) reached, "
            f"skipping rename detection for {old_path}"
        )
        return []
    _RENAME_QUERY_COUNT += 1

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--follow",
                "--diff-filter=R",
                "--name-status",
                "--format=",
                "--",
                old_path,
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        renames: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("R"):
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                renames.append((parts[1], parts[2]))
        return renames
    except Exception as e:
        logger.warning(f"docs_auditor: git log --follow failed for {old_path}: {e}")
        return []


def _detect_renamed_link_fixes(
    doc_path: Path, content: str, repo_root: Path
) -> list[tuple[str, str]]:
    """Detect markdown link targets whose path was renamed.

    Returns list of (old_text, new_text) replacements.
    """
    fixes: list[tuple[str, str]] = []
    for m in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", content):
        target = m.group(1).strip()
        # Resolve target absolute path; skip URLs
        if target.startswith(("http://", "https://", "#")):
            continue
        target_path = (
            (doc_path.parent / target).resolve() if not target.startswith("/") else Path(target)
        )
        try:
            rel = target_path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if (repo_root / rel).exists():
            continue
        # Try rename detection
        renames = _git_log_follow_renames(str(rel), repo_root)
        if renames:
            new_name = renames[0][1]
            fixes.append((target, new_name))
    return fixes


def _detect_renamed_symbol_fixes(content: str, repo_root: Path) -> list[tuple[str, str]]:
    """Detect Python symbol/file references that were renamed.

    Looks for backtick-wrapped paths like ``foo/bar.py``. If the path no
    longer exists but git history shows a rename, queue a replacement.
    """
    fixes: list[tuple[str, str]] = []
    for m in re.finditer(r"`((?:[\w.-]+/)+[\w.-]+\.py)`", content):
        path = m.group(1)
        if (repo_root / path).exists():
            continue
        renames = _git_log_follow_renames(path, repo_root)
        if renames:
            fixes.append((path, renames[0][1]))
    return fixes


def _detect_readme_broken_entries(
    readme_path: Path, content: str, repo_root: Path
) -> list[tuple[str, str]]:
    """Detect README index entries whose target file is gone.

    Returns list of (old_line, replacement_or_empty) tuples. An empty
    replacement signals deletion of the line.
    """
    fixes: list[tuple[str, str]] = []
    for line in content.splitlines():
        m = re.search(r"\(([^)]+\.md)\)", line)
        if not m:
            continue
        target = m.group(1).strip()
        if target.startswith(("http://", "https://", "#")):
            continue
        target_path = (readme_path.parent / target).resolve()
        try:
            rel = target_path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if (repo_root / rel).exists():
            continue
        # Broken entry — try rename, otherwise delete the line
        renames = _git_log_follow_renames(str(rel), repo_root)
        if renames:
            new_target = renames[0][1]
            fixes.append((target, new_target))
        else:
            fixes.append((line, ""))
    return fixes


def _detect_stale_term_fixes(content: str) -> list[tuple[str, str]]:
    """Detect stale terms from STALE_TERMS dict that lack migration context."""
    fixes: list[tuple[str, str]] = []
    for old_term, new_term in STALE_TERMS.items():
        if old_term not in content:
            continue
        migration_context = (
            f"renamed to {new_term}" in content
            or f"replaced by {new_term}" in content
            or f"now {new_term}" in content
            or f"formerly {old_term}" in content
            or f"Replaces {old_term}" in content
            or f"replaces {old_term}" in content
        )
        if not migration_context:
            fixes.append((old_term, new_term))
    return fixes


def _apply_fixes_to_file(path: Path, repo_root: Path, fixes: list[tuple[str, str]]) -> int:
    """Apply (old, new) text replacements to a file. Returns count of fixes applied."""
    full = repo_root / path
    if not full.exists() or not fixes:
        return 0
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"docs_auditor: cannot read {path}: {e}")
        return 0

    new_text = text
    applied = 0
    for old, new in fixes:
        if not old:
            continue
        if new == "":
            # Delete the entire line that exactly equals `old` (after stripping trailing newline).
            # Substring match would risk collateral deletion when `old` happens to appear
            # inside an unrelated line.
            lines = new_text.splitlines(keepends=True)
            kept = [ln for ln in lines if ln.rstrip("\n\r") != old.rstrip("\n\r")]
            removed = len(lines) - len(kept)
            if removed > 0:
                new_text = "".join(kept)
                applied += removed
        else:
            if old in new_text:
                count = new_text.count(old)
                new_text = new_text.replace(old, new)
                applied += count

    if new_text != text:
        try:
            full.write_text(new_text, encoding="utf-8")
        except Exception as e:
            logger.warning(f"docs_auditor: cannot write {path}: {e}")
            return 0
    return applied


# ---------------------------------------------------------------------------
# File-as-issue detectors
# ---------------------------------------------------------------------------


# Path components that are obvious illustrative stand-ins, not real module names.
_PLACEHOLDER_PATH_COMPONENTS = frozenset(
    {"foo", "bar", "baz", "qux", "quux", "example", "your-module", "mymodule", "sample"}
)

# Heading keywords whose presence means the doc is deliberately recording a deletion.
_DELETION_HEADING_KEYWORDS = ("migration", "removed", "deleted", "deprecated")

# Prose cues that a nearby line is documenting a deletion rather than a live reference.
_DELETION_PROSE_CUES = (
    "deleted module",
    "no longer in the codebase",
    "no longer exists",
    "previously in",
    "formerly",
)


def _is_placeholder_path(path: str) -> bool:
    """Return True if a path is an illustrative placeholder, not a real module path.

    A path is a placeholder when any of its components is a well-known stand-in
    (``foo``, ``bar``, ``example`` ...) or a single lowercase letter directory.
    Empty or single-segment paths return False (the detector regex guarantees a
    ``dir/file.py`` shape, so this only guards malformed/odd input).
    """
    if not path or "/" not in path:
        return False
    components = path.split("/")
    for i, component in enumerate(components):
        # For the final component, compare the file stem (strip the .py suffix)
        # so ``agent/docs_handler/foo.py`` is caught on its ``foo`` stem.
        is_last = i == len(components) - 1
        candidate = component[:-3] if is_last and component.endswith(".py") else component
        lowered = candidate.lower()
        if lowered in _PLACEHOLDER_PATH_COMPONENTS:
            return True
        # A single lowercase letter directory (e.g. ``a/foo.py``) is illustrative.
        if len(candidate) == 1 and candidate.isalpha() and candidate.islower():
            return True
    return False


def _build_line_context(content: str) -> tuple[list[bool], list[str]]:
    """Single-scan precompute of per-line context for deletion-aware filtering.

    Returns ``(in_fence, heading_for_line)`` where:
    - ``in_fence[i]`` is True if line ``i`` sits inside a fenced ``` code block.
    - ``heading_for_line[i]`` is the text of the nearest preceding Markdown
      heading for line ``i`` (lowercased), or ``""`` if none precedes it.

    No I/O; pure string scan over ``content``.
    """
    lines = content.splitlines()
    in_fence: list[bool] = []
    heading_for_line: list[str] = []
    fence_open = False
    current_heading = ""
    for line in lines:
        stripped = line.lstrip()
        is_fence_marker = stripped.startswith("```")
        # A fence marker line is itself part of the block boundary; treat the
        # marker line as inside the fence so matches on it are suppressed too.
        if is_fence_marker:
            in_fence.append(True)
            fence_open = not fence_open
        else:
            in_fence.append(fence_open)
        # Track nearest preceding heading only outside fenced blocks.
        if not fence_open and not is_fence_marker and stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip().lower()
        heading_for_line.append(current_heading)
    return in_fence, heading_for_line


def _is_documented_deletion(
    line_idx: int, lines: list[str], in_fence: list[bool], heading_for_line: list[str]
) -> bool:
    """Return True if a match at ``line_idx`` is an illustrative or documented deletion.

    Three conservative cues (any one suppresses the finding):
    1. The match falls inside a fenced code block (illustrative example).
    2. The nearest preceding heading names a deletion (migration/removed/
       deleted/deprecated).
    3. The match's line or an immediately adjacent line carries a deletion-prose
       cue ("deleted module", "no longer exists", ...).

    Inline single-backtick code is NOT suppressed — that is how genuine
    references are written.
    """
    if line_idx < len(in_fence) and in_fence[line_idx]:
        return True
    if line_idx < len(heading_for_line):
        heading = heading_for_line[line_idx]
        if any(kw in heading for kw in _DELETION_HEADING_KEYWORDS):
            return True
    for adj in (line_idx - 1, line_idx, line_idx + 1):
        if 0 <= adj < len(lines):
            lowered = lines[adj].lower()
            if any(cue in lowered for cue in _DELETION_PROSE_CUES):
                return True
    return False


def _detect_deleted_target_issues(doc_path: Path, content: str, repo_root: Path) -> list[dict]:
    """File issues for references to deleted (non-renamed) targets.

    Suppresses three classes of false positive before emitting a finding:
    placeholder/example paths (``foo/bar.py``), paths inside fenced illustrative
    code blocks, and paths under a deletion-recording heading or deletion prose.
    Every suppressed match is logged at DEBUG so operators can audit the filter.
    """
    findings: list[dict] = []
    lines = content.splitlines()
    in_fence, heading_for_line = _build_line_context(content)
    for m in re.finditer(r"`((?:[\w.-]+/)+[\w.-]+\.py)`", content):
        path = m.group(1)
        if _is_placeholder_path(path):
            logger.debug(
                "docs_auditor: suppressed deleted-target finding for placeholder path %s in %s",
                path,
                doc_path,
            )
            continue
        line_idx = content.count("\n", 0, m.start())
        if _is_documented_deletion(line_idx, lines, in_fence, heading_for_line):
            logger.debug(
                "docs_auditor: suppressed deleted-target finding for %s in %s "
                "(fenced block or documented deletion)",
                path,
                doc_path,
            )
            continue
        if (repo_root / path).exists():
            continue
        renames = _git_log_follow_renames(path, repo_root)
        if renames:
            continue
        findings.append(
            {
                "title": f"Doc references deleted target: {path} (in {doc_path})",
                "body": f"`{doc_path}` references `{path}` which no longer exists in the repo.",
                "category": "deleted-target",
            }
        )
    return findings


def _detect_stub_doc(doc_path: Path, content: str) -> dict | None:
    """File an issue if a doc has fewer than STUB_DOC_LINE_THRESHOLD content lines."""
    content_lines = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    if len(content_lines) < STUB_DOC_LINE_THRESHOLD:
        body = (
            f"`{doc_path}` has only {len(content_lines)} content lines "
            f"(<{STUB_DOC_LINE_THRESHOLD})."
        )
        return {
            "title": f"Stub doc: {doc_path}",
            "body": body,
            "category": "stub-doc",
        }
    return None


def _detect_orphan_plan_issues(repo_root: Path) -> list[dict]:
    """Find docs/plans/*.md files that lack a tracking issue link."""
    findings: list[dict] = []
    plans_dir = repo_root / "docs" / "plans"
    if not plans_dir.exists():
        return findings
    for plan in sorted(plans_dir.glob("*.md")):
        try:
            text = plan.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "tracking:" in text or re.search(r"issues/\d+", text):
            continue
        findings.append(
            {
                "title": f"Orphan plan: {plan.relative_to(repo_root)} (no tracking issue)",
                "body": f"`{plan.relative_to(repo_root)}` has no tracking-issue link.",
                "category": "orphan-plan",
            }
        )
    return findings


# ---------------------------------------------------------------------------
# Memory refresh hook (no-op placeholder for #1249)
# ---------------------------------------------------------------------------


def refresh_docs_in_memory(touched_paths: list[str]) -> None:
    """Re-ingest touched docs into the Memory substrate.

    No-op placeholder for #1249. The hook is **stable** — call sites in this
    module will not change when #1249 lands a real implementation. Always
    non-blocking and fire-and-forget; callers wrap invocations in
    ``try/except Exception`` so the auditor never fails because the hook failed.
    """
    logger.debug("refresh_docs_in_memory called with %d path(s) (no-op)", len(touched_paths))


# ---------------------------------------------------------------------------
# Issue filing (gh CLI)
# ---------------------------------------------------------------------------


def _normalize_title(title: str) -> str:
    """Collapse internal whitespace and strip — for exact title comparison."""
    return " ".join(title.split())


def _filing_machine_name() -> str:
    """Human-facing name of the machine filing the issue, for multi-machine triage.

    Matches the ``machine`` field used by the single-machine-ownership system
    (macOS ComputerName via ``scutil``), falling back to the OS hostname. This
    is stamped into every issue body so duplicates fanned across hosts — or a
    host still running this reflection after it was disabled in the synced
    config — name themselves instead of being anonymous.

    Delegates to :func:`config.machine.get_machine_display_name`, the shared
    helper that owns the ComputerName→hostname→"unknown" fallback chain.
    """
    return get_machine_display_name()


def _open_issue_exists(title: str, repo_root: Path) -> bool:
    """Return True if an open `documentation` issue already has this exact title.

    This is the authoritative cross-machine dedup gate: local Redis dedup keys
    are per-machine and invisible across hosts, so two machines would otherwise
    file the same finding. Queries the live tracker via
    ``gh issue list --search`` (REST-backed full-text search) and confirms with
    an exact normalized-title comparison in Python (the title already encodes
    both the path and the doc, making it a natural composite key).

    Fails open: on any `gh` failure, non-zero exit, or malformed output, log a
    WARNING and return False so a genuine finding is never silently dropped —
    the worst case is the duplicate this gate was meant to prevent, which the
    Redis fast-path still suppresses on the next run.
    """
    normalized_query = _normalize_title(title)
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                "documentation",
                "--search",
                title,
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            logger.warning(
                "docs_auditor: gh issue list (dedup) failed for '%s' (rc=%d): %s "
                "— falling back to Redis-only dedup",
                title,
                result.returncode,
                result.stderr.strip()[:200],
            )
            return False
        issues = json.loads(result.stdout or "[]")
        for issue in issues:
            if _normalize_title(issue.get("title", "")) == normalized_query:
                return True
        return False
    except Exception as e:
        logger.warning(
            "docs_auditor: gh issue list (dedup) errored for '%s': %s "
            "— falling back to Redis-only dedup",
            title,
            e,
        )
        return False


def _file_issue_if_new(finding: dict, repo_root: Path) -> bool:
    """File a GitHub issue via gh CLI, deduped by title. Returns True if filed.

    Two-tier dedup: a local Redis fast-path (per-machine cache) gates the
    expensive live-tracker query, and `_open_issue_exists` is the authoritative
    cross-machine gate. Local Redis alone is insufficient because each machine
    keeps its own Redis, so the same finding would be filed once per machine.
    """
    title = finding.get("title", "").strip()
    if not title:
        return False
    title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    dedup_key = f"{REDIS_ISSUE_DEDUP_PREFIX}:{title_hash}"
    redis_client = None
    try:
        redis_client = _get_redis()
        # Fast-path: if this machine already filed it, skip the tracker query entirely.
        if redis_client.exists(dedup_key):
            return False  # already filed
    except Exception:
        redis_client = None  # If Redis is unavailable, attempt to file without dedup

    # Authoritative cross-machine gate: another machine may have already filed this.
    if _open_issue_exists(title, repo_root):
        # Record the local fast-path key so subsequent runs skip the tracker query.
        if redis_client is not None:
            try:
                redis_client.set(dedup_key, "1", ex=86400 * 30)
            except (
                Exception
            ):  # swallow-ok: best-effort cache write; tracker already confirmed dedup
                pass
        return False

    # Stamp the filing machine into the body (not the title — title is the dedup
    # key and must stay stable). Lets duplicates fanned across hosts, or a host
    # still running this reflection after it was disabled in synced config, name
    # themselves for triage.
    body = finding.get("body", "")
    body = f"{body}\n\n---\n*Filed by docs-auditor reflection on `{_filing_machine_name()}`.*"

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "documentation",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            logger.warning(
                f"docs_auditor: gh issue create failed for '{title}' "
                f"(rc={result.returncode}): {result.stderr.strip()[:200]}"
            )
            return False
        # Only set dedup key after successful gh issue create — transient failures retry next run.
        if redis_client is not None:
            try:
                redis_client.set(dedup_key, "1", ex=86400 * 30)
            except Exception:  # swallow-ok: best-effort cache write after successful issue create
                pass
        return True
    except Exception as e:
        logger.warning(f"docs_auditor: gh issue create failed for '{title}': {e}")
        return False


# ---------------------------------------------------------------------------
# Telegram notification (mirrors _send_log_review_telegram pattern)
# ---------------------------------------------------------------------------


def _send_telegram_notification(message: str) -> None:
    """Best-effort Telegram notification. Swallows all subprocess failures."""
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("docs_auditor: valor-telegram not on PATH; skipping Telegram notify")
    except subprocess.TimeoutExpired:
        logger.warning("docs_auditor: valor-telegram send timed out")
    except Exception as e:
        logger.warning(f"docs_auditor: valor-telegram send failed: {e}")


# ---------------------------------------------------------------------------
# Public substrate API
# ---------------------------------------------------------------------------


def audit(
    primary_path: str | Path | None = None,
    *,
    scope_mode: str = "rotation",
    apply_mode: str = "apply",
    project_key: str = "valor",
    repo_root: Path | None = None,
) -> dict:
    """Unified docs-auditor entrypoint. Synchronous.

    Args:
        primary_path: Repo-relative path to the primary doc to audit. When
            ``scope_mode == "pr-changed-files"`` this is ignored.
        scope_mode: One of ``"rotation"`` (single primary + neighborhood) or
            ``"pr-changed-files"`` (PR diff scope).
        apply_mode: ``"apply"`` writes fixes; ``"dry-run"`` reports only.
        project_key: Used for vault-namespaced rotation keys.
        repo_root: Override repo root (defaults to PROJECT_ROOT).

    Returns:
        Dict with ``status``, ``files_touched``, ``fixes_applied``,
        ``issues_filed``, ``pr_url``.
    """
    global _RENAME_QUERY_COUNT
    _RENAME_QUERY_COUNT = 0  # reset per-run cap

    root = (repo_root or PROJECT_ROOT).resolve()

    # Auth probe (Anthropic required)
    ok, reason = _check_auth()
    if not ok:
        logger.warning(f"docs_auditor: auth disabled: {reason}")
        return _ok_result("disabled", extras={"reason": reason})

    # Optional embedding probe; degrade gracefully if missing
    if not _check_embedding_auth():
        logger.debug("docs_auditor: embedding auth missing — lexical-only mode")

    # Resolve scope
    files: list[Path] = []
    if scope_mode == "pr-changed-files":
        files = _resolve_pr_changed_files(root)
    elif scope_mode == "rotation":
        if primary_path is None:
            return _ok_result("skipped", extras={"reason": "no_primary_path"})
        primary = Path(str(primary_path))
        full = root / primary
        if not full.exists():
            return _ok_result("skipped", extras={"reason": "primary_not_found"})
        files = _resolve_neighborhood(primary, root, cap=NEIGHBORHOOD_CAP)
    else:
        return _ok_result("error", extras={"reason": f"unknown scope_mode: {scope_mode}"})

    if not files:
        return _ok_result("ok", files_touched=[], fixes_applied=0, issues_filed=0)

    # Run detectors per file
    touched: list[str] = []
    total_fixes = 0
    issues_filed = 0
    issue_findings: list[dict] = []

    for path in files:
        full = root / path
        if not full.exists():
            continue
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Auto-fix detectors
        fixes: list[tuple[str, str]] = []
        if path.name == "README.md":
            fixes.extend(_detect_readme_broken_entries(path, content, root))
        else:
            fixes.extend(_detect_renamed_link_fixes(path, content, root))
            fixes.extend(_detect_renamed_symbol_fixes(content, root))
        fixes.extend(_detect_stale_term_fixes(content))

        # Apply-mode writes are markdown-only (#2058). The detectors above are
        # markdown-regex based (bare-term renames, backtick link/symbol fixes),
        # so a committed non-.md file that lands in the same PR — e.g. a
        # site/*.html doc page — must never be auto-rewritten inside tags,
        # attributes, or inline <script>. Reporting still runs; only the
        # write-back is guarded.
        if fixes and apply_mode == "apply" and str(path).endswith(".md"):
            applied = _apply_fixes_to_file(path, root, fixes)
            if applied > 0:
                total_fixes += applied
                touched.append(str(path))

        # File-as-issue detectors (advisory). Editorial, not auto-fixable — a
        # deleted-target reference has no rename to correct to. These are
        # rotation-only: Caller B (/do-docs, scope=pr-changed-files) runs on
        # every PR's docs stage, so filing advisory issues there re-files the
        # same unfixable findings per-PR (and re-files any that were closed
        # without fixing the doc, since the dedup gate only sees open issues),
        # which is the documentation-label duplicate flood. Auto-fix detectors
        # above still run per-PR; only issue-filing is gated to rotation.
        if scope_mode == "rotation":
            issue_findings.extend(_detect_deleted_target_issues(path, content, root))
            stub = _detect_stub_doc(path, content)
            if stub is not None:
                issue_findings.append(stub)

    # Orphan plans (repo-wide, run once)
    if scope_mode == "rotation":
        issue_findings.extend(_detect_orphan_plan_issues(root))

    # File issues (deduped); only when applying in rotation scope.
    # Hard per-run cap prevents flood: rotation allows up to 5.
    per_run_cap = 5 if scope_mode == "rotation" else 3
    if apply_mode == "apply" and scope_mode == "rotation":
        for finding in issue_findings:
            if issues_filed >= per_run_cap:
                logger.warning(
                    "docs_auditor: per-run cap (%d) reached for scope=%s — "
                    "%d finding(s) suppressed; re-run to file remaining",
                    per_run_cap,
                    scope_mode,
                    len(issue_findings) - issues_filed,
                )
                break
            if _file_issue_if_new(finding, root):
                issues_filed += 1

    # Caller B (pr-changed-files): commit to current branch and fire memory
    # refresh hook here so the /do-docs skill stays a thin caller. Caller A
    # (rotation) handles its own commit/push/hook in run_docs_auditor.
    if scope_mode == "pr-changed-files" and apply_mode == "apply" and touched:
        _commit_current_branch(root, touched)
        try:
            refresh_docs_in_memory(touched)
        except Exception as e:
            logger.warning(f"docs_auditor: refresh_docs_in_memory hook failed: {e}")

    return _ok_result(
        "ok",
        files_touched=touched,
        fixes_applied=total_fixes,
        issues_filed=issues_filed,
    )


def _commit_current_branch(repo_root: Path, touched: list[str]) -> None:
    """Stage and commit substrate-applied changes on the current branch.

    Best-effort: errors are logged not raised. Used by Caller B (/do-docs)
    so the skill itself does not need to invoke git after the substrate.
    """
    try:
        subprocess.run(
            ["git", "add"] + touched,
            capture_output=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=False,
        )
        subprocess.run(
            ["git", "commit", "-m", f"Docs: cascade fixes ({len(touched)} files)"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=False,
        )
    except Exception as e:
        logger.warning(f"docs_auditor: current-branch commit failed: {e}")


# ---------------------------------------------------------------------------
# Caller A — daily rotation reflection
# ---------------------------------------------------------------------------


def _select_primary_doc(
    repo_root: Path, project_key: str, vault_weight: float = DEFAULT_VAULT_WEIGHT
) -> tuple[Path | None, dict[str, float]]:
    """Pick the least-recently-audited primary doc.

    Returns (selected_path, last_run_map). The map is the parsed Redis hash.
    """
    try:
        r = _get_redis()
        last_run_raw = r.hgetall(REDIS_LAST_RUN_HASH) or {}
    except Exception as e:
        logger.warning(f"docs_auditor: cannot read rotation hash: {e}")
        last_run_raw = {}

    # Decode bytes -> str if necessary
    last_run: dict[str, float] = {}
    for k, v in last_run_raw.items():
        try:
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = float(v.decode() if isinstance(v, bytes) else v)
            last_run[key] = val
        except Exception:
            continue

    # Enumerate candidate docs
    docs_dir = repo_root / "docs" / "features"
    candidates: list[Path] = []
    if docs_dir.exists():
        for md in sorted(docs_dir.glob("*.md")):
            if md.name == "README.md":
                continue
            candidates.append(md.relative_to(repo_root))

    if not candidates:
        return None, last_run

    # Pick oldest / never-run
    def _key(path: Path) -> float:
        return last_run.get(_path_to_slug(path), 0.0)

    candidates.sort(key=_key)
    return candidates[0], last_run


def _git_dirty(repo_root: Path) -> bool:
    """Return True if the working tree is dirty."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        return bool(result.stdout.strip())
    except Exception as e:
        logger.warning(f"docs_auditor: git status failed: {e}")
        return True  # err on the side of caution


def _git_diff_quiet(repo_root: Path) -> bool:
    """Return True if there are no diffs (zero-diff)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet"],
            capture_output=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        return result.returncode == 0  # 0 means no diff
    except Exception:
        return False


def _has_open_pr_for_slug(slug: str, repo_root: Path) -> bool:
    """Return True if any open PR already targets a docs-audit branch for this slug."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "headRefName"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )
        if result.returncode != 0:
            return False
        prs = json.loads(result.stdout or "[]")
        prefix = f"docs-audit/{slug}-"
        return any(p.get("headRefName", "").startswith(prefix) for p in prs)
    except Exception as e:
        logger.warning(f"docs_auditor: open-PR check failed: {e}")
        return False


def _daily_pr_cap_reached(repo_root: Path) -> bool:
    """Return True if a docs-audit PR was already created today (calendar day, UTC)."""
    try:
        r = _get_redis()
        today = datetime.now(UTC).strftime("%Y%m%d")
        key = f"{REDIS_DAILY_PR_KEY}:{today}"
        return bool(r.exists(key))
    except Exception as e:
        logger.warning(f"docs_auditor: daily PR cap check failed: {e}")
        return False


def _record_daily_pr(repo_root: Path) -> None:
    """Mark that a PR was created today so the daily cap is enforced."""
    try:
        r = _get_redis()
        today = datetime.now(UTC).strftime("%Y%m%d")
        key = f"{REDIS_DAILY_PR_KEY}:{today}"
        r.set(key, "1", ex=86400 * 2)  # expires after 2 days
    except Exception as e:
        logger.warning(f"docs_auditor: daily PR cap record failed: {e}")


def _push_branch_and_pr(slug: str, repo_root: Path) -> str | None:
    """Create timestamped branch, push, open PR. Returns PR URL or None on failure.

    Always returns the repo to the main branch afterward, even on error.
    Skips PR creation if an open PR for the same slug already exists or if
    the daily cap (1 PR per calendar day) has been reached.
    """
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    branch = f"docs-audit/{slug}-{ts}"
    try:
        # Guard: daily cap
        if _daily_pr_cap_reached(repo_root):
            logger.info("docs_auditor: daily PR cap reached, skipping PR creation")
            return None

        # Guard: open PR already exists for this slug
        if _has_open_pr_for_slug(slug, repo_root):
            logger.info(f"docs_auditor: open PR already exists for {slug}, skipping")
            return None

        subprocess.run(
            ["git", "checkout", "-b", branch],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"Docs: auditor pass for {slug}"],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=True,
        )
        pr_result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                f"Docs auditor: {slug}",
                "--body",
                "Automated docs auditor pass.",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
            check=False,
        )
        url = (pr_result.stdout or "").strip().splitlines()[-1] if pr_result.stdout else None
        if url and url.startswith("http"):
            _record_daily_pr(repo_root)
            return url
        return None
    except subprocess.CalledProcessError as e:
        logger.warning(f"docs_auditor: branch/push/PR failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"docs_auditor: branch/push/PR error: {e}")
        return None
    finally:
        # Always return to main so the next run starts from a clean base.
        subprocess.run(
            ["git", "checkout", "main"],
            capture_output=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(repo_root),
        )


def _write_liveness(slug: str, status: str, pr_url: str | None, files_touched: int) -> None:
    """Persist liveness signals for PM monitoring (Phase 2)."""
    try:
        r = _get_redis()
        ts = time.time()
        r.set(REDIS_LAST_COMPLETED_TS_KEY, str(ts))
        summary = {
            "slug": slug,
            "pr_url": pr_url,
            "files_touched": files_touched,
            "status": status,
        }
        r.set(REDIS_LAST_COMPLETED_SUMMARY_KEY, json.dumps(summary))
    except Exception as e:
        logger.warning(f"docs_auditor: liveness write failed: {e}")


def _update_rotation_hash(project_key: str, paths: list[str], is_vault: bool = False) -> None:
    """Stamp rotation hash with current timestamp for each touched path."""
    try:
        r = _get_redis()
        ts = time.time()
        mapping = {}
        for p in paths:
            field = _vault_field(project_key, p) if is_vault else _path_to_slug(p)
            mapping[field] = str(ts)
        if mapping:
            r.hset(REDIS_LAST_RUN_HASH, mapping=mapping)
    except Exception as e:
        logger.warning(f"docs_auditor: rotation hash write failed: {e}")


def run_docs_auditor() -> dict:
    """Daily rotation reflection callable.

    Sequence:
      1. Auth probe (cheap, no side effects)
      2. SETNX lock acquire (global)
      3. Dirty-tree guard
      4. Rotation pick
      5. Run substrate
      6. Zero-diff gate
      7. (If diff) push branch + PR
      8. Memory refresh hook (fire-and-forget)
      9. Telegram notification
      10. Update rotation hash
      11. Liveness signal
      12. Lock release (try/finally)
    """
    findings: list[str] = []

    # 1. Auth probe
    ok, reason = _check_auth()
    if not ok:
        return {
            "status": "disabled",
            "findings": [f"Docs auditor disabled: {reason}"],
            "summary": f"docs-auditor disabled: {reason}",
        }

    # 2. Lock
    if not _acquire_lock(REDIS_RUNNING_KEY, LOCK_TTL_SECONDS):
        return {
            "status": "ok",
            "findings": ["docs-auditor already running, skipped"],
            "summary": "docs-auditor skipped: locked",
        }

    project_key = os.environ.get("VALOR_PROJECT_KEY", "valor").strip() or "valor"

    try:
        # 3. Dirty-tree guard
        if _git_dirty(PROJECT_ROOT):
            _write_liveness("(dirty)", "skipped", None, 0)
            return {
                "status": "ok",
                "findings": ["docs-auditor skipped: working tree dirty"],
                "summary": "docs-auditor skipped: dirty_tree",
            }

        # 4. Rotation pick
        primary, _last_run = _select_primary_doc(PROJECT_ROOT, project_key)
        if primary is None:
            _write_liveness("(no-candidates)", "skipped", None, 0)
            return {
                "status": "ok",
                "findings": ["No candidate docs found"],
                "summary": "docs-auditor skipped: no candidates",
            }

        slug = _path_to_slug(primary)

        # 5. Substrate
        result = audit(
            primary_path=primary,
            scope_mode="rotation",
            apply_mode="apply",
            project_key=project_key,
            repo_root=PROJECT_ROOT,
        )

        files_touched: list[str] = result.get("files_touched", [])

        # 6. Zero-diff gate
        if not files_touched or _git_diff_quiet(PROJECT_ROOT):
            _update_rotation_hash(project_key, [str(primary)])
            _write_liveness(slug, "skipped", None, 0)
            return {
                "status": "ok",
                "findings": [f"docs-auditor: zero-diff for {primary}"],
                "summary": f"docs-auditor: zero-diff ({slug})",
            }

        # 7. Memory refresh hook (fire-and-forget) — fired after commit
        # 8. Push branch + PR
        pr_url = _push_branch_and_pr(slug, PROJECT_ROOT)

        try:
            refresh_docs_in_memory(files_touched)
        except Exception as e:
            logger.warning(f"docs_auditor: refresh_docs_in_memory hook failed: {e}")

        # 9. Telegram notification
        msg = (
            f"docs-auditor pass for {slug}: "
            f"{len(files_touched)} files, {result.get('fixes_applied', 0)} fixes"
            + (f"\nPR: {pr_url}" if pr_url else "")
        )
        _send_telegram_notification(msg)

        # 10. Update rotation hash for all touched files
        _update_rotation_hash(project_key, files_touched)

        # 11. Liveness signal
        _write_liveness(slug, "ok", pr_url, len(files_touched))

        findings.append(
            f"Touched {len(files_touched)} files; {result.get('fixes_applied', 0)} fixes applied"
        )
        if pr_url:
            findings.append(f"PR: {pr_url}")

        return {
            "status": "ok",
            "findings": findings,
            "summary": (
                f"docs-auditor: {len(files_touched)} files touched, "
                f"{result.get('fixes_applied', 0)} fixes, PR={pr_url or 'none'}"
            ),
        }

    except Exception as e:
        logger.warning(f"docs_auditor: unexpected error: {e}")
        return {
            "status": "error",
            "findings": [f"docs-auditor error: {e}"],
            "summary": f"docs-auditor error: {e}",
        }
    finally:
        # 12. Lock release
        _release_lock(REDIS_RUNNING_KEY)


# ---------------------------------------------------------------------------
# Branch sweeper reflection
# ---------------------------------------------------------------------------


def _pr_is_auto_merge_eligible(pr_number: int) -> bool:
    """Return True if a docs-audit PR meets the conservative auto-merge bar.

    Heuristics (all must pass):
    - Only ``docs/`` files changed (no code, no config)
    - ≤ 5 files changed
    - ≤ 50 net lines changed (additions + deletions)
    - No reviews, review requests, or comments
    - PR is between 1 and 7 days old (not brand-new, not stale)
    """
    try:
        meta_res = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "files,reviews,reviewRequests,comments,createdAt,additions,deletions",
            ],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            cwd=str(PROJECT_ROOT),
        )
        if meta_res.returncode != 0:
            return False
        meta = json.loads(meta_res.stdout or "{}")

        # No reviewer activity
        if meta.get("reviews") or meta.get("reviewRequests") or meta.get("comments"):
            return False

        # File count and path guard
        files = meta.get("files", [])
        if not files or len(files) > 5:
            return False
        for f in files:
            path = f.get("path", "")
            if not (path.startswith("docs/") or path in ("README.md", "CLAUDE.md")):
                return False

        # Diff size guard
        net_lines = meta.get("additions", 0) + meta.get("deletions", 0)
        if net_lines > 50:
            return False

        # Age guard: 1–7 days
        created_raw = meta.get("createdAt", "")
        if not created_raw:
            return False
        age_days = (
            datetime.now(UTC) - datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        ).days
        if age_days < 1 or age_days > 7:
            return False

        return True
    except Exception as e:
        logger.warning(f"docs_auditor: auto-merge eligibility check failed for #{pr_number}: {e}")
        return False


def run_docs_branch_sweeper() -> dict:
    """Sweep stale ``docs-audit/*`` branches and PRs.

    Conservative: only touches ``docs-audit/*`` branches, never any other
    prefix and never branches with reviewer activity.

    Also auto-merges PRs that pass the conservative eligibility check (docs-only,
    small diff, no reviewer activity, 1–7 days old).
    """
    if not _acquire_lock(REDIS_SWEEPER_RUNNING_KEY, SWEEPER_LOCK_TTL_SECONDS):
        return {
            "status": "ok",
            "findings": ["sweeper already running, skipped"],
            "summary": "do-docs-branch-sweeper skipped: locked",
        }

    findings: list[str] = []
    branches_deleted = 0
    prs_closed = 0
    prs_merged = 0

    try:
        # List remote branches under docs-audit/
        try:
            res = subprocess.run(
                ["git", "ls-remote", "--heads", "origin", "docs-audit/*"],
                capture_output=True,
                text=True,
                timeout=settings.timeouts.git_subprocess_s,
                cwd=str(PROJECT_ROOT),
            )
        except Exception as e:
            return {
                "status": "error",
                "findings": [f"sweeper ls-remote failed: {e}"],
                "summary": f"do-docs-branch-sweeper error: {e}",
            }

        for line in res.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) != 2:
                continue
            ref = parts[1]
            if not ref.startswith("refs/heads/docs-audit/"):
                continue
            branch = ref[len("refs/heads/") :]

            # Query PR state for this branch
            try:
                pr_res = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "list",
                        "--head",
                        branch,
                        "--state",
                        "all",
                        "--json",
                        "number,state,createdAt",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=settings.timeouts.git_subprocess_s,
                    cwd=str(PROJECT_ROOT),
                )
                prs = json.loads(pr_res.stdout) if pr_res.stdout.strip() else []
            except Exception as e:
                logger.warning(f"sweeper: gh pr list failed for {branch}: {e}")
                continue

            now = datetime.now(UTC)

            # Branches with all PRs already closed/merged: delete if branch is old enough
            open_prs = [p for p in prs if p.get("state", "").upper() == "OPEN"]
            closed_prs = [p for p in prs if p.get("state", "").upper() != "OPEN"]
            if prs and not open_prs:
                # All PRs closed — delete branch if oldest closed PR is stale
                newest_close = max((p.get("createdAt", "") for p in closed_prs), default="")
                if newest_close:
                    try:
                        age_days = (
                            now - datetime.fromisoformat(newest_close.replace("Z", "+00:00"))
                        ).days
                        if age_days >= STALE_BRANCH_AGE_DAYS:
                            subprocess.run(
                                ["git", "push", "origin", "--delete", branch],
                                capture_output=True,
                                timeout=settings.timeouts.git_subprocess_s,
                                cwd=str(PROJECT_ROOT),
                                check=False,
                            )
                            branches_deleted += 1
                            findings.append(
                                f"Deleted branch with closed PR: {branch} ({age_days}d)"
                            )
                    except Exception as e:
                        logger.warning(
                            f"sweeper: closed-PR branch cleanup failed for {branch}: {e}"
                        )
                continue

            if not prs:
                # No PR ever; check branch age via creation time of the latest commit
                try:
                    commit_res = subprocess.run(
                        ["git", "log", "-1", "--format=%cI", f"origin/{branch}"],
                        capture_output=True,
                        text=True,
                        timeout=settings.timeouts.git_subprocess_s,
                        cwd=str(PROJECT_ROOT),
                    )
                    commit_ts = commit_res.stdout.strip()
                    if not commit_ts:
                        continue
                    age_days = (now - datetime.fromisoformat(commit_ts)).days
                    if age_days >= STALE_BRANCH_AGE_DAYS:
                        subprocess.run(
                            ["git", "push", "origin", "--delete", branch],
                            capture_output=True,
                            timeout=settings.timeouts.git_subprocess_s,
                            cwd=str(PROJECT_ROOT),
                            check=False,
                        )
                        branches_deleted += 1
                        findings.append(f"Deleted stale branch: {branch} ({age_days}d)")
                except Exception as e:
                    logger.warning(f"sweeper: branch-age check failed for {branch}: {e}")
                continue

            for pr in open_prs:
                state = pr.get("state", "").upper()
                if state not in ("OPEN",):
                    continue
                created_at = pr.get("createdAt", "")
                if not created_at:
                    continue
                try:
                    age_days = (
                        now - datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    ).days
                except Exception:
                    continue
                pr_num = pr.get("number")
                if not pr_num:
                    continue

                # Auto-merge eligible PRs before stale-close check
                if _pr_is_auto_merge_eligible(pr_num):
                    try:
                        merge_res = subprocess.run(
                            ["gh", "pr", "merge", str(pr_num), "--squash", "--delete-branch"],
                            capture_output=True,
                            text=True,
                            timeout=settings.timeouts.git_subprocess_s,
                            cwd=str(PROJECT_ROOT),
                            check=False,
                        )
                        if merge_res.returncode == 0:
                            prs_merged += 1
                            findings.append(
                                f"Auto-merged PR #{pr_num} (branch={branch}, {age_days}d)"
                            )
                            continue
                        else:
                            logger.warning(
                                f"sweeper: auto-merge failed for #{pr_num}: {merge_res.stderr}"
                            )
                    except Exception as e:
                        logger.warning(f"sweeper: auto-merge error for #{pr_num}: {e}")

                if age_days >= STALE_PR_AGE_DAYS:
                    try:
                        subprocess.run(
                            ["gh", "pr", "close", "--delete-branch", str(pr_num)],
                            capture_output=True,
                            timeout=settings.timeouts.git_subprocess_s,
                            cwd=str(PROJECT_ROOT),
                            check=False,
                        )
                        prs_closed += 1
                        findings.append(f"Closed stale PR #{pr_num} (branch={branch}, {age_days}d)")
                    except Exception as e:
                        logger.warning(f"sweeper: gh pr close failed for #{pr_num}: {e}")

        summary = (
            f"do-docs-branch-sweeper: {branches_deleted} branches deleted, "
            f"{prs_closed} PRs closed, {prs_merged} PRs auto-merged"
        )
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    finally:
        _release_lock(REDIS_SWEEPER_RUNNING_KEY)


# ---------------------------------------------------------------------------
# CLI entrypoint (one-shot for /do-docs)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Allow `python -m reflections.docs_auditor` to print a JSON result for the
    # ``/do-docs`` skill bash block. Args via env: SCOPE_MODE, APPLY_MODE.
    scope = os.environ.get("DOCS_AUDIT_SCOPE", "pr-changed-files")
    apply = os.environ.get("DOCS_AUDIT_APPLY", "apply")
    project = os.environ.get("VALOR_PROJECT_KEY", "valor")
    out = audit(
        primary_path=None,
        scope_mode=scope,
        apply_mode=apply,
        project_key=project,
    )
    print(json.dumps(out))
