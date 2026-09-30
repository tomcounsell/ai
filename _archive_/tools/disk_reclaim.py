"""tools/disk_reclaim.py — Age out on-disk state that nothing else reclaims.

What it does: sweeps three categories of unbounded on-disk state — merged
    worktree lanes under ``.worktrees/``, Claude CLI transcripts under
    ``~/.claude/projects/``, and session snapshots under ``logs/sessions/`` —
    reporting what it would remove and, only when explicitly armed, removing it.

The transcript sweep works at file granularity *inside* each project directory
and never removes the project directory itself. ``<project>/memory/`` is the
durable per-project memory store and is never touched; only session transcripts
(``<uuid>.jsonl``) and their sibling ``<uuid>/`` session directories are
candidates, each judged on its own mtime. Anything else (``.timelines/``,
``sessions-index.json``, unrecognized names) is preserved.

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
import re
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
#
# The window applies to each transcript's OWN mtime, not to the project
# directory's. Judging by the directory would couple two unrelated lifetimes:
# a project's `memory/` store is curated and permanent while its transcripts
# are disposable, and the directory's recency is driven almost entirely by
# transcript writes. Whichever way that coupling is resolved it is wrong --
# either a live project's month-old transcripts are kept forever, or a quiet
# project's memory is deleted along with them. So the project directory is
# never removed and each transcript ages out alone.
DEFAULT_TRANSCRIPT_MAX_AGE_DAYS = 30

# Session transcripts are named for their session UUID: `<uuid>.jsonl` plus, for
# some sessions, a sibling `<uuid>/` directory of session-scoped scratch files.
# Matching the shape (rather than deleting whatever is not `memory/`) is what
# makes the sweep fail closed: a name Claude Code starts writing tomorrow is
# preserved by default instead of reaped by default.
_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Never a candidate, at any age. The per-project memory store (MEMORY.md plus
# one file per memory) is durable curated state, not a transcript.
#
# Defence in depth, and unreachable in production today: `memory` does not match
# `_SESSION_ID_RE`, so the allow-list already excludes it and removing this check
# changes nothing that currently happens. The hazard it guards is someone
# loosening that pattern later, which is precisely the change that would find no
# backstop if this were deleted for looking inert. Pinned by
# `test_preserved_entries_backstop_a_loosened_allow_list`, which loosens the
# regex and asserts the memory store still survives.
PRESERVED_PROJECT_ENTRIES = frozenset({"memory"})

# Worktree lanes that are infrastructure, not SDLC work, and must never be
# reaped. `.worktrees/nightly-baseline/` is the persistent, provisioned
# baseline checkout the nightly regression classifier re-points at the prior
# run's HEAD SHA every night (issue #2334); reaping it forces a full
# `uv sync` re-provision on the nightly critical path. It is genuinely in
# sweep_worktrees' scope and survives today only by guard-order accident
# (`too_young` while the nightly keeps touching it, then `merged_via_tree`
# returning False for a branch that never existed), which inverts the moment
# a branchless lane is treated as reapable.
PROTECTED_WORKTREE_SLUGS = frozenset({"nightly-baseline"})

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


def _tree_stats(path: Path) -> tuple[int, float]:
    """One walk of ``path``, returning ``(apparent_bytes, newest_mtime)``.

    Size is apparent, not physical. On APFS a worktree `.venv` is copy-on-write
    cloned from uv's global cache, so this over-reports the reclaimable bytes by
    roughly 60x for that subtree (measured: 541 MB apparent, 9 MB actual `df`
    delta). Treated as an upper bound in reporting and never as a justification.

    ``newest_mtime`` falls back to the directory's own mtime -- via a plain
    ``stat()``, which needs no read permission and so succeeds even for a
    tree the walk could not read -- when the tree is empty or unreadable, and
    only reaches *now* when ``stat()`` on the directory itself also fails.
    An unreadable lane is not caught by this fallback; it is caught
    downstream (``git_status_unavailable`` for worktrees, ``sweep.errors``
    for transcripts).

    Both figures come from a single ``rglob`` because every caller wants both
    and a lane carrying a `.venv` is tens of thousands of entries.
    """
    total = 0
    newest = 0.0
    try:
        for entry in path.rglob("*"):
            try:
                stat = entry.stat()
                newest = max(newest, stat.st_mtime)
                if entry.is_file() and not entry.is_symlink():
                    total += stat.st_size
            except OSError:
                continue
    except OSError:
        pass
    if newest == 0.0:
        try:
            newest = path.stat().st_mtime
        except OSError:
            newest = time.time()
    return total, newest


def _entry_stats(path: Path) -> tuple[int, float]:
    """``(apparent_bytes, newest_mtime)`` for a file or a directory."""
    if path.is_dir() and not path.is_symlink():
        return _tree_stats(path)
    try:
        stat = path.stat()
    except OSError:
        return 0, time.time()  # unreadable: treat as brand new, never reap
    return stat.st_size, stat.st_mtime


def _is_transcript_entry(entry: Path) -> bool:
    """Whether ``entry`` inside a project dir is a disposable session transcript.

    Allow-list, not a deny-list: ``<uuid>.jsonl`` and ``<uuid>/`` only. Every
    other name -- ``memory/`` above all, but also ``.timelines/``,
    ``sessions-index.json``, symlinks, and anything Claude Code adds in a future
    release -- is preserved. A sweep that reaped "everything except a hardcoded
    keep-list" would delete tomorrow's durable state by default.
    """
    if entry.is_symlink():
        return False
    if entry.name in PRESERVED_PROJECT_ENTRIES:
        return False
    if entry.is_dir():
        return bool(_SESSION_ID_RE.match(entry.name))
    return entry.suffix == ".jsonl" and bool(_SESSION_ID_RE.match(entry.stem))


def _git(repo_root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or repo_root),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _origin_repo_slug(repo_root: Path) -> str | None:
    """``owner/name`` parsed from ``repo_root``'s ``origin`` remote, or None.

    Handles both ssh (``git@host:owner/name.git``) and https
    (``https://host/owner/name.git``) remote URL forms, same shape as
    ``tools/pr_head_resolver.py::_origin_matches_repo``. Returns None on any
    failure -- no remote, an unparseable URL, or a subprocess error -- so the
    caller can fail closed instead of guessing.
    """
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip().removesuffix(".git")
    if not url:
        return None
    # Split on both ':' (ssh scp-like form) and '/' (path separators) and take
    # the last two non-empty components as owner/name.
    parts = [p for p in re.split(r"[:/]", url) if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    return f"{owner}/{name}"


def open_pr_branches(repo_root: Path) -> set[str] | None:
    """Branch names with an open PR **in ``repo_root``**, or None on failure.

    ``gh`` resolves its target repository through a chain that a forced
    ``cwd`` does not control: ``GH_REPO`` outranks the working directory, so a
    bare call under a forced cwd can answer *successfully* about the wrong
    repository -- and a successful wrong answer cannot be caught by the None
    fail-closed branch below. Every ``session/*`` lane would then read "no
    open PR" because none of them appear among another repo's branch names,
    silently voiding this guard. ``GH_REPO`` is not a hypothetical: it is set
    automatically for cross-repo SDLC work (``agent/session_executor.py``,
    ``agent/sdk_client.py``), exactly the context this reflection runs in.

    The remedy is explicit, not positional: derive ``owner/name`` from
    ``repo_root``'s own ``origin`` remote (``_origin_repo_slug``) and pass it
    as ``--repo``, and scrub ``GH_REPO`` from the child's environment as
    defense in depth. If the slug cannot be derived -- no git repo, no
    ``origin`` remote, an unparseable URL -- this returns None rather than
    falling back to a bare, repo-ambiguous ``gh pr list``. Same hazard, same
    remedy as ``tools/pr_head_resolver.py::_origin_matches_repo``.

    None is not an empty set. The predecessor script (`scripts/worktree-gc.sh`)
    collapsed a failed `gh` call into an empty string, so an auth failure or a
    network blip read as "no branch has an open PR" and made every worktree a
    prune candidate. Callers must treat None as "cannot tell" and skip.
    """
    slug = _origin_repo_slug(repo_root)
    if slug is None:
        logger.warning(
            "disk_reclaim: could not derive owner/name from %s's origin remote; "
            "worktree sweep will skip all",
            repo_root,
        )
        return None

    env = dict(os.environ)
    env.pop("GH_REPO", None)

    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                slug,
                "--state",
                "open",
                "--limit",
                "500",
                "--json",
                "headRefName",
            ],
            # `--repo` above is what actually scopes this call — it outranks
            # both cwd and GH_REPO. The cwd pin and the GH_REPO scrub are
            # belt-and-braces for the day someone edits the argv; neither is
            # load-bearing on its own, so do not read this line as the
            # mechanism.
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
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
    removed only when it is not in ``PROTECTED_WORKTREE_SLUGS``, old enough,
    clean, unclaimed by any live session or OS process, carries no open PR,
    and its branch has landed on main.
    """
    from agent.worktree_manager import (
        WORKTREES_DIR,
        _worktree_has_live_process,
        cleanup_after_merge,
        merged_via_tree,
        worktree_busy_probe,
        worktree_busy_probe_many,
    )

    sweep = Sweep(category="worktrees")
    worktrees_root = repo_root / WORKTREES_DIR
    if not worktrees_root.is_dir():
        return sweep

    open_branches = open_pr_branches(repo_root)
    if open_branches is None:
        # Fail closed across the whole category: without PR state we cannot
        # distinguish an abandoned lane from one under active review.
        for child in sorted(worktrees_root.iterdir()):
            if child.is_dir():
                sweep.skip(child.name, "pr_state_unavailable")
        return sweep

    cutoff = time.time() - (min_age_days * 86400)
    children = sorted(worktrees_root.iterdir())

    # Batch busy map, built lazily on first need: one session query for the
    # whole sweep instead of one per lane that reaches this guard. Queried
    # over every non-protected lane (a superset of the lanes that will
    # actually reach guard 5) -- correct, and free, because the underlying
    # fetch is one query regardless of how many slugs are classified against
    # it. An all-`too_young` sweep never builds this and pays zero queries.
    busy_map: dict[str, tuple[str, str]] | None = None

    for child in children:
        if not child.is_dir():
            continue
        slug = child.name

        # First guard, ahead of `too_young`: a protected lane is never
        # reapable no matter how old, clean, or idle it looks.
        if slug in PROTECTED_WORKTREE_SLUGS:
            sweep.skip(slug, "protected")
            continue

        size, newest = _tree_stats(child)
        if newest > cutoff:
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

        if busy_map is None:
            candidate_slugs = [
                c.name for c in children if c.is_dir() and c.name not in PROTECTED_WORKTREE_SLUGS
            ]
            busy_map = worktree_busy_probe_many(repo_root, candidate_slugs)
        # Never default a missing slug to "clear" -- a lookup bug must read
        # as an unanswerable question, not as a silent all-clear.
        state, detail = busy_map.get(slug, ("error", "not_probed"))
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

        if not apply:
            sweep.removed.append(slug)
            sweep.freed_bytes += size
            continue

        # Fresh, single-slug, fail-closed re-probe immediately before
        # authorizing removal. The batch snapshot above can be seconds to
        # minutes stale by the time a lane reaches here -- everything the
        # sweep does per remaining lane after it (_tree_stats, git status,
        # merged_via_tree) sits inside that window -- so the read that
        # actually authorizes deletion is never older than the guard right
        # below it. Skipped on the apply=False path above: dry run deletes
        # nothing, so there is no TOCTOU window here to close.
        state, detail = worktree_busy_probe(repo_root, slug)
        if state == "error":
            sweep.skip(slug, f"busy_check_error:{detail}")
            continue
        if state == "busy":
            sweep.skip(slug, f"live_session:{detail}")
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
    """Age out Claude CLI session transcripts under ``~/.claude/projects/``.

    A transcript is the only thing backing ``claude --resume`` for its session,
    so the window is the answer to "past what point is resuming not wanted".

    Scope is deliberately narrow. Each project directory holds disposable
    transcripts (``<uuid>.jsonl`` and their sibling ``<uuid>/`` directories)
    alongside durable state: ``memory/`` (the per-project memory store),
    ``.timelines/``, ``sessions-index.json``. Only the transcripts are
    candidates, each judged on its own mtime, and the project directory itself
    is never removed -- so a quiet project keeps its curated memory while its
    dead transcripts still age out, and a busy project's month-old transcripts
    are not kept alive by a fresh sibling.

    Anything that does not match the transcript shape is preserved, counted in
    the skip reason, and never deleted.
    """
    sweep = Sweep(category="transcripts")
    root = projects_dir if projects_dir is not None else CLAUDE_PROJECTS_DIR
    if not root.is_dir():
        return sweep

    cutoff = time.time() - (max_age_days * 86400)

    for project in sorted(root.iterdir()):
        if not project.is_dir() or project.is_symlink():
            continue

        preserved = 0
        too_young = 0
        try:
            entries = sorted(project.iterdir())
        except OSError as e:
            sweep.skip(project.name, f"unreadable:{type(e).__name__}")
            continue

        for entry in entries:
            if not _is_transcript_entry(entry):
                preserved += 1
                continue

            size, newest = _entry_stats(entry)
            if newest > cutoff:
                too_young += 1
                continue

            name = f"{project.name}/{entry.name}"
            if not apply:
                sweep.removed.append(name)
                sweep.freed_bytes += size
                continue

            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except OSError as e:
                sweep.errors.append(f"{name}: {e}")
                continue
            sweep.removed.append(name)
            sweep.freed_bytes += size

        reason = ", ".join(
            part
            for part in (
                f"too_young:{too_young}" if too_young else "",
                f"preserved:{preserved}" if preserved else "",
            )
            if part
        )
        if reason:
            sweep.skip(project.name, reason)

    return sweep


def sweep_session_snapshots(
    *,
    max_age_hours: float = DEFAULT_SNAPSHOT_MAX_AGE_HOURS,
    apply: bool = False,
) -> Sweep:
    """Wire up ``cleanup_old_snapshots``, which has had no caller since the
    reflections monolith was deleted.

    Not parameterized by ``repo_root``: ``cleanup_old_snapshots`` takes no
    directory argument and resolves ``agent/session_logs.py``'s module-relative
    ``SESSION_LOGS_DIR``, so this sweep always targets the checkout the code was
    imported from. ``--repo-root`` therefore applies to the worktree sweep only,
    which its argparse help says.
    """
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
            "Age out merged worktree lanes, old Claude session transcripts, and "
            "session snapshots. Dry-run unless DISK_RECLAIM_APPLY=true is set."
        ),
        epilog=(
            "Worktree removal goes through cleanup_after_merge, so a lane with "
            "uncommitted changes, a live session, or an unmerged branch is never "
            "removed. The transcript sweep never removes a project directory and "
            "never touches its memory/ store. Replaces the unguarded "
            "scripts/worktree-gc.sh."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help=(
            "Checkout whose .worktrees/ lanes to sweep, and whose open PRs are "
            "queried. Applies to the worktree sweep only: the transcript sweep "
            "reads ~/.claude/projects/ and the snapshot sweep reads the "
            "SESSION_LOGS_DIR of the installed agent package."
        ),
    )
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
