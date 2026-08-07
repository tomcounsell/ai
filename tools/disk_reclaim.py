"""tools/disk_reclaim.py — Age out on-disk state that nothing else reclaims.

What it does: sweeps three categories of unbounded on-disk state — merged
    worktree lanes under ``.worktrees/``, Claude CLI transcripts under
    ``~/.claude/projects/``, and session snapshots under ``logs/sessions/`` —
    reporting what it would remove and, only when explicitly armed, removing it.

Dry-run is the default and the only mode reachable without operator intent.
Applying requires the ``DISK_RECLAIM_APPLY=true`` environment variable, read
here and nowhere else. It is deliberately NOT wired to the reflection's
``params.apply``, matching ``memory-decay-prune``'s tier-1 gate: a destructive
sweep should not be armed by editing a YAML file that a dozen other knobs share.

Worktree removal delegates entirely to ``cleanup_after_merge``, which carries
the guards this module must not reimplement: the live-session busy check
(#1357), uncommitted-change preservation (#2137), the unmerged-branch guard
(#1646), and path containment (#880). ``force=True`` is never passed anywhere.

Every guard below fails CLOSED. A check that cannot answer skips the candidate;
the cost of keeping a stale worktree for another day is a directory, and the
cost of guessing wrong is someone's unpushed work.

See also: config/reflections.yaml (declaration),
    docs/features/scheduled-disk-reclaim.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("reflections.housekeeping")

APPLY_ENV = "DISK_RECLAIM_APPLY"

# Generous by design. A lane is not stale because it is idle; it is stale
# because nobody has touched it across more than a working fortnight.
DEFAULT_WORKTREE_MIN_AGE_DAYS = 14

# Transcripts back `claude --resume`. Past a month, resuming a session is not
# realistically wanted -- the branch has merged or the work was abandoned, and
# the surviving record is the PR. Chosen to sit well clear of the 14-day
# worktree window so a lane's transcript outlives its worktree.
DEFAULT_TRANSCRIPT_MAX_AGE_DAYS = 30

# Matches cleanup_old_snapshots' own long-standing default (7 days).
DEFAULT_SNAPSHOT_MAX_AGE_HOURS = 168

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


@dataclass
class Sweep:
    """One category's result: what would go, what stays, and why."""

    category: str
    removed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append((name, reason))

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "removed": self.removed,
            "skipped": [{"name": n, "reason": r} for n, r in self.skipped],
            "freed_bytes": self.freed_bytes,
            "errors": self.errors,
        }


def apply_armed() -> bool:
    """True only when the operator set DISK_RECLAIM_APPLY to a truthy value.

    Read from the environment on every call rather than cached at import, so a
    long-lived reflection worker picks up an arming change without a restart.
    """
    return os.environ.get(APPLY_ENV, "").strip().lower() in {"1", "true", "yes"}


def _dir_size(path: Path) -> int:
    """Apparent size of a tree in bytes; 0 if it cannot be walked.

    Apparent, not physical. On APFS a worktree `.venv` is copy-on-write cloned
    from uv's global cache, so this over-reports the reclaimable bytes by
    roughly 60x for that subtree (measured: 541 MB apparent, 9 MB actual `df`
    delta). Treated as an upper bound in reporting and never as a justification.
    """
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _newest_mtime(path: Path) -> float:
    """Most recent mtime anywhere under ``path``, or the dir's own if empty."""
    newest = 0.0
    try:
        for entry in path.rglob("*"):
            try:
                newest = max(newest, entry.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    if newest == 0.0:
        try:
            newest = path.stat().st_mtime
        except OSError:
            newest = time.time()  # unreadable: treat as brand new, never reap
    return newest


def _git(repo_root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )


def open_pr_branches() -> set[str] | None:
    """Branch names with an open PR, or None when the query fails.

    None is not an empty set. The predecessor script (`scripts/worktree-gc.sh`)
    collapsed a failed `gh` call into an empty string, so an auth failure or a
    network blip read as "no branch has an open PR" and made every worktree a
    prune candidate. Callers must treat None as "cannot tell" and skip.
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "500", "--json", "headRefName"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("disk_reclaim: gh pr list failed (%s); worktree sweep will skip all", e)
        return None
    if proc.returncode != 0:
        logger.warning(
            "disk_reclaim: gh pr list exited %s (%s); worktree sweep will skip all",
            proc.returncode,
            proc.stderr.strip()[:200],
        )
        return None
    try:
        return {p["headRefName"] for p in json.loads(proc.stdout)}
    except (ValueError, KeyError, TypeError) as e:
        logger.warning("disk_reclaim: could not parse gh output (%s); skipping all", e)
        return None


def _worktree_is_dirty(worktree_dir: Path) -> bool | None:
    """True if the worktree has uncommitted changes; None if git cannot say."""
    try:
        proc = _git(worktree_dir, "status", "--porcelain", cwd=worktree_dir)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return bool(proc.stdout.strip())


def sweep_worktrees(
    repo_root: Path,
    *,
    min_age_days: int = DEFAULT_WORKTREE_MIN_AGE_DAYS,
    apply: bool = False,
) -> Sweep:
    """Reap merged, idle worktree lanes through ``cleanup_after_merge``.

    Guards run cheapest-first and every one of them fails closed. A lane is
    removed only when it is old enough, clean, unclaimed by any live session or
    OS process, carries no open PR, and its branch has landed on main.
    """
    from agent.worktree_manager import (
        WORKTREES_DIR,
        _worktree_has_live_process,
        cleanup_after_merge,
        merged_via_tree,
        worktree_busy_probe,
    )

    sweep = Sweep(category="worktrees")
    worktrees_root = repo_root / WORKTREES_DIR
    if not worktrees_root.is_dir():
        return sweep

    open_branches = open_pr_branches()
    if open_branches is None:
        # Fail closed across the whole category: without PR state we cannot
        # distinguish an abandoned lane from one under active review.
        for child in sorted(worktrees_root.iterdir()):
            if child.is_dir():
                sweep.skip(child.name, "pr_state_unavailable")
        return sweep

    cutoff = time.time() - (min_age_days * 86400)

    for child in sorted(worktrees_root.iterdir()):
        if not child.is_dir():
            continue
        slug = child.name

        if _newest_mtime(child) > cutoff:
            sweep.skip(slug, "too_young")
            continue

        dirty = _worktree_is_dirty(child)
        if dirty is None:
            sweep.skip(slug, "git_status_unavailable")
            continue
        if dirty:
            sweep.skip(slug, "uncommitted_changes")
            continue

        try:
            live_pid = _worktree_has_live_process(child)
        except Exception as e:
            sweep.skip(slug, f"process_scan_error:{type(e).__name__}")
            continue
        if live_pid is not None:
            sweep.skip(slug, f"live_process:{live_pid}")
            continue

        state, detail = worktree_busy_probe(repo_root, slug)
        if state == "error":
            sweep.skip(slug, f"busy_check_error:{detail}")
            continue
        if state == "busy":
            sweep.skip(slug, f"live_session:{detail}")
            continue

        branch = f"session/{slug}"
        if branch in open_branches:
            sweep.skip(slug, "open_pr")
            continue

        try:
            landed = merged_via_tree(str(repo_root), branch, "main")
        except Exception as e:
            sweep.skip(slug, f"merge_check_error:{type(e).__name__}")
            continue
        if not landed:
            sweep.skip(slug, "unmerged")
            continue

        size = _dir_size(child)
        if not apply:
            sweep.removed.append(slug)
            sweep.freed_bytes += size
            continue

        try:
            result = cleanup_after_merge(repo_root, slug)
        except Exception as e:
            sweep.errors.append(f"{slug}: cleanup_after_merge raised {type(e).__name__}: {e}")
            continue
        if result.get("worktree_removed"):
            sweep.removed.append(slug)
            sweep.freed_bytes += size
        else:
            reason = result.get("blocked_by_session") or "; ".join(result.get("errors", []))
            sweep.skip(slug, f"cleanup_declined:{reason or 'unknown'}")

    return sweep


def sweep_transcripts(
    *,
    max_age_days: int = DEFAULT_TRANSCRIPT_MAX_AGE_DAYS,
    apply: bool = False,
    projects_dir: Path | None = None,
) -> Sweep:
    """Age out Claude CLI transcript directories under ``~/.claude/projects/``.

    A transcript is the only thing backing ``claude --resume`` for its session,
    so the window is the answer to "past what point is resuming not wanted".
    """
    sweep = Sweep(category="transcripts")
    root = projects_dir if projects_dir is not None else CLAUDE_PROJECTS_DIR
    if not root.is_dir():
        return sweep

    cutoff = time.time() - (max_age_days * 86400)

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if _newest_mtime(child) > cutoff:
            sweep.skip(child.name, "too_young")
            continue

        size = _dir_size(child)
        if not apply:
            sweep.removed.append(child.name)
            sweep.freed_bytes += size
            continue

        try:
            shutil.rmtree(child)
        except OSError as e:
            sweep.errors.append(f"{child.name}: {e}")
            continue
        sweep.removed.append(child.name)
        sweep.freed_bytes += size

    return sweep


def sweep_session_snapshots(
    *,
    max_age_hours: float = DEFAULT_SNAPSHOT_MAX_AGE_HOURS,
    apply: bool = False,
) -> Sweep:
    """Wire up ``cleanup_old_snapshots``, which has had no caller since the
    reflections monolith was deleted."""
    from agent.session_logs import cleanup_old_snapshots

    sweep = Sweep(category="session_snapshots")
    try:
        names = cleanup_old_snapshots(max_age_hours=max_age_hours, dry_run=not apply)
    except Exception as e:
        sweep.errors.append(f"cleanup_old_snapshots raised {type(e).__name__}: {e}")
        return sweep
    sweep.removed.extend(names)
    return sweep


def reclaim(
    repo_root: Path,
    *,
    apply: bool | None = None,
    worktree_min_age_days: int = DEFAULT_WORKTREE_MIN_AGE_DAYS,
    transcript_max_age_days: int = DEFAULT_TRANSCRIPT_MAX_AGE_DAYS,
    snapshot_max_age_hours: float = DEFAULT_SNAPSHOT_MAX_AGE_HOURS,
) -> dict:
    """Run all three sweeps and return a combined report.

    ``apply=None`` (the default, and what the reflection passes) defers to
    ``DISK_RECLAIM_APPLY``. An explicit ``apply=True`` is honored only so tests
    and a deliberate CLI ``--apply`` can exercise the destructive path.
    """
    effective_apply = apply_armed() if apply is None else apply

    sweeps = [
        sweep_worktrees(repo_root, min_age_days=worktree_min_age_days, apply=effective_apply),
        sweep_transcripts(max_age_days=transcript_max_age_days, apply=effective_apply),
        sweep_session_snapshots(max_age_hours=snapshot_max_age_hours, apply=effective_apply),
    ]

    return {
        "applied": effective_apply,
        "sweeps": [s.as_dict() for s in sweeps],
        "freed_bytes": sum(s.freed_bytes for s in sweeps),
        "removed_count": sum(len(s.removed) for s in sweeps),
        "skipped_count": sum(len(s.skipped) for s in sweeps),
        "errors": [e for s in sweeps for e in s.errors],
    }


def format_report(report: dict) -> str:
    """Human-readable summary naming what went and what stayed, with reasons."""
    mode = "APPLIED" if report["applied"] else "DRY-RUN"
    lines = [f"=== disk-reclaim ({mode}) ==="]
    for sweep in report["sweeps"]:
        lines.append(f"\n[{sweep['category']}]")
        for name in sweep["removed"]:
            verb = "removed" if report["applied"] else "would remove"
            lines.append(f"  {verb}: {name}")
        for entry in sweep["skipped"]:
            lines.append(f"  kept:    {entry['name']}  ({entry['reason']})")
        for err in sweep["errors"]:
            lines.append(f"  ERROR:   {err}")
        if not sweep["removed"] and not sweep["skipped"] and not sweep["errors"]:
            lines.append("  nothing to consider")
    mb = report["freed_bytes"] / (1024 * 1024)
    lines.append(
        f"\n{report['removed_count']} removed, {report['skipped_count']} kept, "
        f"~{mb:.0f} MB apparent (APFS clones make this an upper bound)"
    )
    if not report["applied"]:
        lines.append(f"Dry-run. Set {APPLY_ENV}=true to arm removal.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="disk-reclaim",
        description=(
            "Age out merged worktree lanes, old Claude transcripts, and session "
            "snapshots. Dry-run unless DISK_RECLAIM_APPLY=true is set."
        ),
        epilog=(
            "Worktree removal goes through cleanup_after_merge, so a lane with "
            "uncommitted changes, a live session, or an unmerged branch is never "
            "removed. Replaces the unguarded scripts/worktree-gc.sh."
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--worktree-min-age-days", type=int, default=DEFAULT_WORKTREE_MIN_AGE_DAYS)
    parser.add_argument(
        "--transcript-max-age-days", type=int, default=DEFAULT_TRANSCRIPT_MAX_AGE_DAYS
    )
    parser.add_argument(
        "--snapshot-max-age-hours", type=float, default=DEFAULT_SNAPSHOT_MAX_AGE_HOURS
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=f"Remove, rather than report. Also requires {APPLY_ENV}=true in the environment.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the raw report as JSON.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.apply and not apply_armed():
        print(
            f"--apply refused: {APPLY_ENV} is not set to a truthy value.\n"
            f"Both the flag and the environment variable are required, so a "
            f"destructive sweep cannot happen by shell history alone.",
            file=sys.stderr,
        )
        return 2

    report = reclaim(
        args.repo_root.resolve(),
        apply=args.apply,
        worktree_min_age_days=args.worktree_min_age_days,
        transcript_max_age_days=args.transcript_max_age_days,
        snapshot_max_age_hours=args.snapshot_max_age_hours,
    )
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
