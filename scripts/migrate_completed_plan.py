#!/usr/bin/env python3
"""Migrate completed plan to feature documentation.

Validates that feature documentation exists, is complete, and is indexed before
deleting the plan file and closing the tracking issue.

Usage:
    python scripts/migrate_completed_plan.py docs/plans/my-feature.md
    python scripts/migrate_completed_plan.py docs/plans/my-feature.md --dry-run

Also provides the path-independent migration primitive (issue #1900, Tier 0):
``migrate_plan_to_completed()`` performs a guarded ``git mv`` of a root plan into
the completed-plan archive -- the single authoritative mechanism two call sites
share: the deterministic ``/do-merge --issue`` invocation and the
``merged-branch-cleanup`` reflection backstop.

    python scripts/migrate_completed_plan.py --issue 1900 [--apply|--dry-run]
    python scripts/migrate_completed_plan.py --sweep [--apply] [--cap N]

Exit codes:
    0 - Plan successfully migrated (or would be in dry-run)
    1 - Validation failed, plan not migrated
    2 - File or command error
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from tools._sdlc_utils import _resolve_target_repo_fallback

# Ceiling on a single `gh` call (issue close / issue state lookup). Named
# rather than inline; provisional and tunable.
GH_SUBPROCESS_TIMEOUT_SECONDS = 30


def extract_tracking_issue(plan_text: str) -> str | None:
    """Extract tracking issue URL from plan frontmatter.

    Returns the issue URL or None if not found.
    """
    match = re.search(r"^tracking:\s*(.+)$", plan_text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def extract_feature_doc_path(plan_text: str) -> str | None:
    """Extract feature doc path from Documentation section.

    Looks for patterns like:
    - [ ] Create `docs/features/my-feature.md`
    - [ ] Update `docs/features/existing.md`

    Returns the first feature doc path found, or None.
    """
    # Find Documentation section
    section_match = re.search(
        r"^## Documentation\s*\n(.*?)(?=^## |\Z)",
        plan_text,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return None

    section = section_match.group(1)

    # Extract first docs/features/*.md path from backticks
    path_match = re.search(r"`(docs/features/[^`]+\.md)`", section)
    if path_match:
        return path_match.group(1)

    return None


def validate_feature_doc(doc_path: Path) -> tuple[bool, str]:
    """Validate feature doc exists and has minimum required sections.

    Returns (is_valid, error_message).
    """
    if not doc_path.exists():
        return False, f"Feature doc not found: {doc_path}"

    content = doc_path.read_text()

    # Check for title (# Heading)
    if not re.search(r"^# .+", content, re.MULTILINE):
        return False, f"Feature doc missing title: {doc_path}"

    # Check for substantial content (more than just title)
    # Must have at least 10 non-whitespace characters beyond the title
    content_without_title = re.sub(r"^#[^\n]*\n", "", content, count=1)
    stripped_content = content_without_title.strip()
    if len(stripped_content) < 10:
        return False, f"Feature doc too short (needs content beyond title): {doc_path}"

    return True, ""


def extract_feature_name_from_index(feature_doc_filename: str) -> str | None:
    """Extract the display name for a feature doc from the README index table.

    Searches docs/features/README.md for a table row whose link target matches
    the given filename (e.g., 'pm-dev-session-architecture.md') and returns the
    bracketed display text (e.g., 'PM/Dev Session Architecture').

    Returns None if no matching row is found.
    """
    index_path = Path("docs/features/README.md")
    if not index_path.exists():
        return None

    content = index_path.read_text()

    # Match table rows: | [Display Name](filename.md) | ... |
    # The filename in the link target must match exactly
    pattern = rf"\|\s*\[([^\]]+)\]\({re.escape(feature_doc_filename)}\)"
    match = re.search(pattern, content)
    if match:
        return match.group(1)

    return None


def validate_feature_index(feature_name: str) -> tuple[bool, str]:
    """Validate feature is indexed in docs/features/README.md.

    Returns (is_indexed, error_message).
    """
    index_path = Path("docs/features/README.md")
    if not index_path.exists():
        return False, "Feature index not found: docs/features/README.md"

    content = index_path.read_text()

    # Look for feature name in markdown table row
    # Pattern: | [Feature Name](filename.md) | Description | Status |
    pattern = rf"\|\s*\[.*{re.escape(feature_name)}.*\]"
    if not re.search(pattern, content, re.IGNORECASE):
        return (
            False,
            f"Feature not found in index: {feature_name}. Add entry to docs/features/README.md",
        )

    return True, ""


def close_tracking_issue(issue_url: str, dry_run: bool) -> tuple[bool, str]:
    """Close the tracking issue using gh CLI.

    Returns (success, error_message).
    """
    # Extract issue number from URL
    # Pattern: https://github.com/owner/repo/issues/123
    match = re.search(r"/issues/(\d+)", issue_url)
    if not match:
        return False, f"Could not extract issue number from URL: {issue_url}"

    issue_number = match.group(1)

    if dry_run:
        print(f"[DRY-RUN] Would close issue #{issue_number}")
        return True, ""

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "close",
                issue_number,
                "--comment",
                "Plan completed and migrated to feature documentation.",
            ],
            capture_output=True,
            text=True,
            timeout=GH_SUBPROCESS_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False, f"Failed to close issue: {result.stderr}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "gh command timed out"
    except FileNotFoundError:
        return False, "gh CLI not found. Install from https://cli.github.com/"
    except Exception as e:
        return False, f"Error closing issue: {e}"


def delete_plan(plan_path: Path, dry_run: bool) -> tuple[bool, str]:
    """Delete the plan file.

    Returns (success, error_message).
    """
    if dry_run:
        print(f"[DRY-RUN] Would delete plan: {plan_path}")
        return True, ""

    try:
        plan_path.unlink()
        return True, ""
    except Exception as e:
        return False, f"Error deleting plan: {e}"


# --- Path-independent migration primitive (issue #1900, Tier 0) ------------------
#
# ``migrate_plan_to_completed()`` is the ONE authoritative mechanism for moving a
# completed plan out of ``docs/plans/`` root into the archive. Two call sites
# share it: the deterministic ``/do-merge --issue`` invocation (Site D) and the
# ``merged-branch-cleanup`` reflection backstop (Site C). Both call this same
# function -- neither re-implements the git mv / guard logic.

# Repo-relative home of shipped plans (#2878). Named rather than spelled inline
# at each use so the destination has exactly one definition -- the whole point
# of there being a single mover. The archive deliberately sits OUTSIDE the
# ``docs/plans/`` prefix: four hook validators and the docs auditor treat that
# prefix as "live plan", and an archived plan is history, not a live plan.
#
# Anything keyed on this location must be updated with it, not alongside it:
# ``scripts/check_issue_disposition.py``'s EXEMPT_PREFIXES (the mover's own
# commit is refused otherwise) and ``reflections/docs_auditor.py``'s two
# plan-exclusion filters.
COMPLETED_PLANS_DIR = "docs/archive/plans-completed"


# Exit code used to report a git subcommand that blew its timeout. 124 is the
# conventional `timeout(1)` code; nothing here depends on the exact value, only
# on it being non-zero so every caller's existing returncode check fires.
GIT_TIMEOUT_RETURNCODE = 124


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand rooted at ``cwd``. Never raises.

    A non-zero exit and a blown timeout are reported identically, as a
    ``CompletedProcess`` with a non-zero ``returncode``. Letting
    ``subprocess.TimeoutExpired`` escape would bypass every rollback path in
    ``migrate_plan_to_completed`` (stranding the migration commit on ``main``,
    the exact #3530 failure) and propagate into the reflection sweep, which has
    no handler for it.
    """

    def _text(stream: str | bytes | None) -> str:
        if stream is None:
            return ""
        return stream.decode(errors="replace") if isinstance(stream, bytes) else stream

    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=GIT_TIMEOUT_RETURNCODE,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) + f"git {' '.join(args)} timed out after {timeout}s",
        )


def _rebase_in_progress(repo_root: Path) -> bool:
    """Detect a half-finished rebase (rebase-merge/rebase-apply state dir present)."""
    git_dir_result = _run_git(["rev-parse", "--git-dir"], cwd=repo_root)
    if git_dir_result.returncode != 0:
        return False
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _head_sha(repo_root: Path) -> str | None:
    """Sha at ``HEAD``, or ``None`` if git can't say.

    Read before ``git commit`` to anchor the range that
    ``_migration_commit_may_have_landed`` later searches. ``None`` means no
    usable range, which that helper treats as "cannot prove the commit is
    absent" rather than as proof it never happened.
    """
    result = _run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _migration_commit_may_have_landed(
    repo_root: Path, head_before_commit: str | None, expected_subject: str
) -> bool:
    """Did our migration commit land, despite ``git commit`` reporting failure?

    Matches the subject across the whole ``head_before_commit..HEAD`` range,
    not just the tip. This is the *shared* ``main`` checkout: a peer session
    can land its own commit on top of ours between our ``git commit`` and this
    read, leaving ours one below the tip. A tip-only check would then see the
    peer's subject, conclude "the commit did not happen", and reverse-rename --
    stranding our migration commit on ``main`` (#3530's end state) with a dirty
    index on top of it.

    Returns ``True`` whenever the commit cannot be *proven* absent: an
    unreadable pre-commit HEAD (no usable range) or a failing ``git log`` both
    mean "unknown", and the safe disposition for unknown is the rollback path.
    ``_rollback_migration_commit`` independently re-verifies that our commit is
    in ``origin/main..HEAD`` and refuses to touch ``main`` otherwise, so a
    false ``True`` costs a refusal (or a ``"migrated"`` report) with any staged
    rename cleanly undone rather than left dirty; a false ``False`` costs a
    stranded commit.
    """
    if head_before_commit is None:
        return True
    log_result = _run_git(["log", "--format=%s", f"{head_before_commit}..HEAD"], cwd=repo_root)
    if log_result.returncode != 0:
        return True
    return any(line == expected_subject for line in log_result.stdout.splitlines())


def _commits_ahead_of_origin(
    repo_root: Path, expected_subject: str
) -> tuple[int | None, str | None]:
    """Inspect ``origin/main..HEAD``: how many commits are ahead, and which one
    (if any) is the migration commit this call authored, matched by subject.

    Returns ``(ahead_count, our_sha)``. ``ahead_count`` is ``None`` when the
    ahead-set can't be determined at all (git itself failed), which callers
    must treat as "unknown, do not mutate". ``our_sha`` is ``None`` when none
    of the commits ahead carries our subject.
    """
    log_result = _run_git(["log", "--format=%H%x00%s", "origin/main..HEAD"], cwd=repo_root)
    if log_result.returncode != 0:
        return None, None
    lines = [line for line in log_result.stdout.splitlines() if line.strip()]
    our_sha = None
    for line in lines:
        sha, _, subject = line.partition("\0")
        if subject == expected_subject and our_sha is None:
            our_sha = sha
    return len(lines), our_sha


def _rename_present_at(repo_root: Path, ref: str, completed_path: Path) -> bool:
    """True if the migrated plan already exists at its archive path in the tree at ``ref``."""
    try:
        rel = completed_path.relative_to(repo_root).as_posix()
    except ValueError:
        return False
    return _run_git(["cat-file", "-e", f"{ref}:{rel}"], cwd=repo_root).returncode == 0


def _rename_on_origin(repo_root: Path, completed_path: Path) -> bool:
    """True if the migrated plan already exists at its archive path on ``origin/main``."""
    return _rename_present_at(repo_root, "origin/main", completed_path)


def _undo_staged_rename_if_present(repo_root: Path, plan_path: Path, completed_path: Path) -> None:
    """Undo a `git mv` this call staged, only if it's still staged (uncommitted).

    `_rollback_migration_commit` is called whenever a `git commit` reports
    failure but might have landed anyway (see `_migration_commit_may_have_landed`).
    When it genuinely did NOT land, the `git mv` from before the failed commit
    is still sitting in the index -- `git diff --cached` on the rename's own
    paths is nonzero. When the commit DID land (committed, not staged) there is
    nothing to undo, and this is a no-op. Never raises; a failure to undo is
    logged and left for manual recovery, matching every other refusal in this
    module.
    """
    staged_diff = _run_git(
        ["diff", "--cached", "--quiet", "--", str(plan_path), str(completed_path)],
        cwd=repo_root,
    )
    if staged_diff.returncode == 1:
        undo = _run_git(["mv", str(completed_path), str(plan_path)], cwd=repo_root)
        if undo.returncode != 0:
            print(
                f"[ERROR] Could not undo the staged rename for {plan_path.name}: "
                f"{undo.stderr.strip()}"
            )
    elif staged_diff.returncode not in (0, 1):
        print(
            f"[ERROR] Could not determine whether a rename for {plan_path.name} is staged "
            f"(git diff --cached exited {staged_diff.returncode}"
            + (f": {staged_diff.stderr.strip()}" if staged_diff.stderr else "")
            + "); leaving the index as-is for manual recovery"
        )


def _rollback_migration_commit(
    repo_root: Path, plan_path: Path, expected_subject: str, completed_path: Path, has_origin: bool
) -> str:
    """Drop the migration commit this call authored -- and only that commit.

    This runs against the *shared* ``main`` checkout, where another session may
    hold uncommitted tracked edits or have landed its own commit. Safety is
    enforced by git rather than pre-checked by this code: ``reset --keep`` and
    ``rebase --onto`` both abort rather than overwrite local modifications, so
    there is no check-then-act window for a peer to lose work in (a
    ``git status --porcelain`` pre-check could not close that window).

    Four shapes:

    * no ``origin`` remote at all -- the ahead/fetch/reset logic below is
      entirely origin-relative and cannot run. Checked directly against
      ``HEAD``'s own tree instead: reported as ``"migrated"`` if the rename is
      already committed there, or ``"mutation-failed-skip"`` (after undoing
      any staged rename) if the commit genuinely never landed.
    * nothing ahead of ``origin/main`` -- the push actually landed (server-side
      success the client reported as failure, or a peer carried our commit up).
      Confirmed against ``origin/main`` and reported as ``"migrated"`` rather
      than escalating to a human over a migration that is already done. The
      refusal shape below (migration absent everywhere) shares this ``ahead ==
      0`` branch; both also clean up any staged rename left in the index by a
      commit that never actually landed, so neither leaves a dirty tree behind.
    * exactly our commit ahead -- ``git reset --keep origin/main``.
    * our commit plus a peer's -- ``git rebase --onto <ours>^ <ours>``, which
      drops only ours and replays theirs.

    Anything git refuses, or any shape without our commit in it, returns
    ``"rollback-refused-skip"`` with ``main`` left untouched for manual
    recovery.
    """
    plan_name = plan_path.name

    # No 'origin' means there is nothing to fetch or compare against -- the
    # ahead/fetch/reset logic below is entirely origin-relative and cannot run
    # at all. But "no origin" is not the same as "the commit landed": check
    # HEAD's own tree directly, which is ground truth regardless of origin.
    # If the rename really is committed there, it's already durable locally
    # (consistent with the no-origin philosophy elsewhere in this file). If
    # not, the commit genuinely never happened -- undo the staged rename
    # rather than reporting a false "migrated" over a dirty index.
    if not has_origin:
        if _rename_present_at(repo_root, "HEAD", completed_path):
            print(f"[MIGRATED] {plan_name} -> {COMPLETED_PLANS_DIR}/ (no 'origin' remote)")
            return "migrated"
        _undo_staged_rename_if_present(repo_root, plan_path, completed_path)
        print(
            f"[ERROR] Commit for {plan_name} did not land locally and there is no 'origin' "
            "to reconcile against; undid the staged rename"
        )
        return "mutation-failed-skip"

    # Refresh origin/main first: the ahead-set is only meaningful against the
    # remote's current tip, and a push that landed server-side shows up here.
    _run_git(["fetch", "origin", "main"], cwd=repo_root, timeout=60)

    ahead, our_sha = _commits_ahead_of_origin(repo_root, expected_subject)
    if ahead is None:
        _undo_staged_rename_if_present(repo_root, plan_path, completed_path)
        print(
            f"[ERROR] Refusing to roll back local main for {plan_name}: could not determine "
            "what HEAD carries ahead of origin/main; leaving main as-is for manual recovery"
        )
        return "rollback-refused-skip"

    if ahead == 0:
        # A commit that never actually landed (the commit-failure call site)
        # can still leave its own `git mv` staged in the index even though
        # nothing is ahead of origin/main. A commit that DID land (the other
        # call sites, reached here only once it's already on origin/main) has
        # nothing staged -- its rename is committed, not staged, and HEAD
        # already matches the index. Only undo when there's a real staged
        # difference to clean up, so a landed migration is never dirtied by
        # an undo it doesn't need.
        _undo_staged_rename_if_present(repo_root, plan_path, completed_path)
        if _rename_on_origin(repo_root, completed_path):
            print(
                f"[MIGRATED] {plan_name} -> {COMPLETED_PLANS_DIR}/ "
                "(push reported failure but landed on origin/main)"
            )
            return "migrated"
        print(
            f"[ERROR] Refusing to roll back local main for {plan_name}: nothing is ahead of "
            "origin/main, yet the migration is absent there; leaving main as-is for manual "
            "recovery"
        )
        return "rollback-refused-skip"

    if our_sha is None:
        _undo_staged_rename_if_present(repo_root, plan_path, completed_path)
        print(
            f"[ERROR] Refusing to roll back local main for {plan_name}: none of the "
            f"{ahead} commit(s) ahead of origin/main is our migration commit; leaving main "
            "as-is for manual recovery"
        )
        return "rollback-refused-skip"

    if ahead == 1:
        # --keep, never --hard: git aborts if the reset would overwrite a peer's
        # uncommitted tracked edits in this shared checkout.
        drop = _run_git(["reset", "--keep", "origin/main"], cwd=repo_root)
    else:
        # A peer's commit is here too. Excise only ours and replay theirs.
        drop = _run_git(["rebase", "--onto", f"{our_sha}^", our_sha], cwd=repo_root, timeout=60)
        if drop.returncode != 0:
            _run_git(["rebase", "--abort"], cwd=repo_root)

    if drop.returncode != 0:
        print(
            f"[ERROR] Refusing to roll back local main for {plan_name}: git declined to drop "
            f"the migration commit ({drop.stderr.strip()}); leaving main as-is for manual "
            "recovery"
        )
        return "rollback-refused-skip"

    print(f"[ERROR] Rolled back the migration commit on local main for {plan_name}")
    return "rolled-back-skip"


def migrate_plan_to_completed(plan_path: Path, *, apply: bool) -> str:
    """Guarded git-mv of a root plan into the completed-plan archive.

    Returns one of: "migrated", "already-migrated", "dirty-tree-skip",
    "fetch-failed-skip", "stale-main-skip", "mutation-failed-skip",
    "rolled-back-skip", "rollback-refused-skip". Never raises -- all failure
    modes return a verdict string and log the reason, including a git
    subcommand that blows its timeout (see ``_run_git``).

    Freshness precondition (issue #3530): before mutating, local ``main`` must
    already match ``origin/main`` or be cleanly fast-forwardable to it. A
    local ``main`` that already carries commits ``origin/main`` lacks is
    refused outright (``"stale-main-skip"``) rather than stacking another
    migration commit on top of it -- a machine that can never win the push
    race would otherwise accumulate unpushed commits on ``main`` forever,
    breaking every subsequent ``/update`` fast-forward on that machine. A
    remote-side precondition failure (fetch/compare/fast-forward against
    `origin` all fail) is reported separately as ``"fetch-failed-skip"`` --
    it means the remote couldn't be reached, not that the tree is dirty.

    Rollback on failure to land (issue #3530): if the eventual push can't
    land -- either a genuine rebase conflict, or exhausting the retry budget
    on a plain non-fast-forward rejection -- exactly our own migration commit
    is dropped (``git reset --keep`` / ``git rebase --onto``, see
    ``_rollback_migration_commit``) before returning ``"rolled-back-skip"``.
    The commit is a pure rename of one file with no unique content, so the
    next ``--sweep``/``--issue`` invocation just redoes it from a clean base.
    If git declines the drop -- because a peer holds uncommitted tracked edits
    the drop would overwrite, or the ahead-set has a shape this primitive
    doesn't recognise -- ``"rollback-refused-skip"`` is returned with ``main``
    untouched for manual recovery.

    A failed ``git mv``/``git commit`` returns ``"mutation-failed-skip"``: the
    preconditions passed and the primitive was mid-mutation, which is a
    distinct operator signal from the report-only ``"dirty-tree-skip"``. One
    exception: a failed ``git commit`` whose commit is found anywhere in
    ``HEAD_before..HEAD`` -- or whose absence can't be proven at all -- is
    treated as landed (see ``_migration_commit_may_have_landed``) and routed
    into the rollback above instead, so the verdict is whatever that rollback
    returns.
    """
    plan_path = Path(plan_path)

    # Resolve repo layout from the plan's own path: docs/plans/{name}.md implies
    # repo_root == plan_path.parent.parent.parent. This keeps the function usable
    # from callers with different process cwds (CLI vs. reflection worker) without
    # needing a cwd parameter in the public signature.
    anchor = plan_path if plan_path.is_absolute() else plan_path.resolve()
    plans_dir = anchor.parent
    repo_root = plans_dir.parent.parent
    # The destination is repo-rooted, not plans-dir-relative: the archive is a
    # sibling of docs/plans/, not a child of it (#2878). repo_root is still
    # derived from the SOURCE plan, which remains in docs/plans/, so this
    # resolves correctly from any caller cwd exactly as before.
    completed_path = repo_root / COMPLETED_PLANS_DIR / plan_path.name

    # Existence-guard (idempotency): git mv is NOT idempotent -- a second attempt
    # on an already-moved plan must not look like a failure.
    if not plan_path.exists():
        if completed_path.exists():
            print(f"[SKIP] Already migrated: {plan_path.name}")
            return "already-migrated"
        print(f"[SKIP] Plan not found in root or {COMPLETED_PLANS_DIR}/: {plan_path}")
        return "already-migrated"

    # Clean-tree/HEAD==main precondition. If either fails, this is the
    # report-only fallback: log what would be migrated, mutate nothing.
    try:
        branch_result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        status_result = _run_git(["status", "--porcelain"], cwd=repo_root)
        tree_dirty = bool(status_result.stdout.strip()) if status_result.returncode == 0 else True
    except Exception as e:
        print(f"[SKIP] Could not inspect git state for {plan_path.name}: {e}")
        return "dirty-tree-skip"

    if current_branch != "main" or tree_dirty:
        reasons = []
        if current_branch != "main":
            reasons.append(f"HEAD is '{current_branch}', not 'main'")
        if tree_dirty:
            reasons.append("working tree is dirty")
        print(
            f"[REPORT-ONLY] Would migrate {plan_path.name} -> {COMPLETED_PLANS_DIR}/ "
            f"(blocked: {'; '.join(reasons)})"
        )
        return "dirty-tree-skip"

    if not apply:
        print(f"[DRY-RUN] Would migrate {plan_path.name} -> {COMPLETED_PLANS_DIR}/")
        return "migrated"

    # Freshness precondition (#3530): refuse to mutate on top of a local main
    # that's already ahead of origin/main -- that's exactly how a machine that
    # keeps losing the push race accumulates permanently unpushed commits on
    # main. If local is simply behind, fast-forward it first. A repo with no
    # 'origin' remote (e.g. a local-only test repo) has nothing to compare
    # against, so it skips straight to the commit as before.
    remote_check = _run_git(["remote", "get-url", "origin"], cwd=repo_root)
    has_origin = remote_check.returncode == 0
    if has_origin:
        fetch_result = _run_git(["fetch", "origin", "main"], cwd=repo_root, timeout=60)
        if fetch_result.returncode != 0:
            print(
                f"[ERROR] git fetch origin main failed for {plan_path.name}: "
                f"{fetch_result.stderr.strip()}"
            )
            return "fetch-failed-skip"

        ahead_behind = _run_git(
            ["rev-list", "--left-right", "--count", "main...origin/main"], cwd=repo_root
        )
        if ahead_behind.returncode != 0:
            print(
                f"[ERROR] Could not compare main to origin/main for {plan_path.name}: "
                f"{ahead_behind.stderr.strip()}"
            )
            return "fetch-failed-skip"

        ahead_str, _, _behind_str = ahead_behind.stdout.strip().partition("\t")
        try:
            ahead = int(ahead_str or "0")
        except ValueError:
            print(
                f"[ERROR] Unexpected `git rev-list` output comparing main to origin/main "
                f"for {plan_path.name}: {ahead_behind.stdout.strip()!r}"
            )
            return "fetch-failed-skip"
        if ahead > 0:
            print(
                f"[SKIP] local main is {ahead} commit(s) ahead of origin/main; refusing to "
                f"stack another migration commit on a diverged main: {plan_path.name}"
            )
            return "stale-main-skip"

        ff_result = _run_git(["merge", "--ff-only", "origin/main"], cwd=repo_root)
        if ff_result.returncode != 0:
            print(
                f"[ERROR] fast-forward to origin/main failed for {plan_path.name}: "
                f"{ff_result.stderr.strip()}"
            )
            return "fetch-failed-skip"

    completed_path.parent.mkdir(parents=True, exist_ok=True)
    mv_result = _run_git(["mv", str(plan_path), str(completed_path)], cwd=repo_root)
    if mv_result.returncode != 0:
        print(f"[ERROR] git mv failed for {plan_path.name}: {mv_result.stderr.strip()}")
        return "mutation-failed-skip"

    # Single definition of the commit subject: the rollback guard matches on it,
    # so a second independent literal would let an edit to one silently degrade
    # every rollback into a refusal.
    migration_subject = f"Migrate completed plan: {plan_path.stem}"

    # Pathspec-scoped commit: this is the shared main checkout, and a peer's
    # `git add` between the clean-tree precondition and this line would
    # otherwise be swept onto main under our subject. Scoping to the rename's
    # two paths is also what makes "a pure rename, nothing else" true, which is
    # the premise the rollback rests on.
    head_before_commit = _head_sha(repo_root)
    commit_result = _run_git(
        ["commit", "-m", migration_subject, "--", str(plan_path), str(completed_path)],
        cwd=repo_root,
    )
    if commit_result.returncode != 0:
        print(f"[ERROR] git commit failed for {plan_path.name}: {commit_result.stderr.strip()}")
        # A failed `git commit` does not prove the commit did not land. The
        # hook chain (`core.hooksPath=.githooks`) can push a commit past the
        # timeout in ``_run_git``, and a kill after git's ref update but before
        # process exit reports non-zero for a commit that exists. Reverse-
        # renaming in that state would strand the migration commit on the
        # shared main AND leave its index dirty -- worse than #3530 itself.
        # Ask the commit range -- not just the tip, a peer can be on top of
        # ours by now -- what actually happened before compensating.
        if _migration_commit_may_have_landed(repo_root, head_before_commit, migration_subject):
            print(
                f"[WARN] git commit reported failure for {plan_path.name} but the commit "
                "may have landed; rolling it back instead of undoing the rename"
            )
            return _rollback_migration_commit(
                repo_root, plan_path, migration_subject, completed_path, has_origin
            )
        # The commit genuinely did not happen. Undo only our own rename. A
        # `reset --hard HEAD` here would discard whatever else a peer has
        # staged or modified in this shared checkout.
        revert = _run_git(["mv", str(completed_path), str(plan_path)], cwd=repo_root)
        if revert.returncode != 0:
            print(
                f"[ERROR] Could not undo the rename for {plan_path.name}: {revert.stderr.strip()}"
            )
        return "mutation-failed-skip"

    # If there's no 'origin' remote (e.g. a local-only test repo), the migration
    # is already durable as a local commit -- nothing more to do.
    if not has_origin:
        print(f"[MIGRATED] {plan_path.name} -> {COMPLETED_PLANS_DIR}/ (no 'origin' remote)")
        return "migrated"

    # Rebase-retry loop: a losing push replays atop the winner. Distinguish a
    # genuine textual conflict (abort, roll back, never resolve unattended)
    # from a plain non-fast-forward rejection (retry).
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        push_result = _run_git(["push", "origin", "main"], cwd=repo_root, timeout=60)
        if push_result.returncode == 0:
            print(f"[MIGRATED] {plan_path.name} -> {COMPLETED_PLANS_DIR}/")
            return "migrated"

        print(
            f"[WARN] git push rejected for {plan_path.name} "
            f"(attempt {attempt}/{max_attempts}): {push_result.stderr.strip()}"
        )
        # Fetch, then rebase onto the NAMED ref (#2650). `git pull --rebase`
        # takes its onto-target from `.git/FETCH_HEAD`, which every worktree of
        # the repo shares -- and this script runs on the shared main checkout
        # while concurrent lanes are fetching, which is exactly when a peer's
        # fetch can retarget the rebase.
        _run_git(["fetch", "origin", "main"], cwd=repo_root, timeout=60)
        pull_result = _run_git(["rebase", "origin/main"], cwd=repo_root, timeout=60)
        conflict_text = (pull_result.stdout + pull_result.stderr).lower()
        if pull_result.returncode != 0 and (
            _rebase_in_progress(repo_root) or "conflict" in conflict_text
        ):
            _run_git(["rebase", "--abort"], cwd=repo_root)
            # Drop the stranded local commit (#3530) rather than leaving main
            # permanently ahead of origin/main: it's a pure rename with no
            # unique content, so the next sweep/--issue run just redoes it.
            # Scoped to only our own commit, with git itself enforcing that a
            # peer's work survives (#3530 follow-up) -- see
            # _rollback_migration_commit.
            print(f"[ERROR] Rebase conflict migrating {plan_path.name}")
            return _rollback_migration_commit(
                repo_root, plan_path, migration_subject, completed_path, has_origin
            )

    # Exhausted the retry budget without a conflict (e.g. repeatedly losing
    # the push race, or a transient network/gh failure). Same rollback as the
    # conflict path (#3530): never return leaving local main ahead of origin,
    # scoped to only our own commit (#3530 follow-up).
    print(f"[ERROR] Failed to push migration for {plan_path.name} after {max_attempts} attempts")
    return _rollback_migration_commit(
        repo_root, plan_path, migration_subject, completed_path, has_origin
    )


def find_plan_by_issue(issue_number: str, plans_dir: Path = Path("docs/plans")) -> Path | None:
    """Scan root plans for the one whose tracking: frontmatter matches issue_number."""
    for plan_file in sorted(plans_dir.glob("*.md")):
        text = plan_file.read_text(errors="replace")
        tracking_url = extract_tracking_issue(text)
        if not tracking_url:
            continue
        match = re.search(r"/issues/(\d+)", tracking_url)
        if match and match.group(1) == str(issue_number):
            return plan_file
    return None


def _gh_issue_state(issue_number: str) -> str:
    """Look up a GitHub issue's state via gh. Returns 'unknown' on any failure.

    Scoped with ``--repo``: a bare ``gh issue view`` resolves GH_REPO from the
    environment before cwd, so under a foreign GH_REPO (or from the wrong
    checkout) it would answer about a different repository's issue #N and
    exit 0 (issue #2889). The resolved slug mirrors
    ``tools/sdlc_stage_query.py``'s ladder (GH_REPO env first, else
    ``gh repo view --json nameWithOwner`` from the working-tree root).
    """
    try:
        repo = _resolve_target_repo_fallback()
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                *(["--repo", repo] if repo else []),
                "--json",
                "state",
            ],
            capture_output=True,
            text=True,
            timeout=GH_SUBPROCESS_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return str(data.get("state", "unknown")).lower()
    except Exception as e:
        print(f"[WARN] Could not check issue #{issue_number}: {e}")
    return "unknown"


def run_issue(issue_number: str, *, apply: bool) -> int:
    """CLI handler for --issue <N>: resolve the plan by tracking issue, migrate it.

    Evidence-gated like run_sweep and the reflection: the migration only fires
    when the tracking issue is literally "closed". A multi-PR issue (PR 1
    merged, issue still open for PR 2) must keep its plan live in the root;
    a gh outage ("unknown") must defer, never migrate.
    """
    plan_file = find_plan_by_issue(issue_number)
    if not plan_file:
        print(f"Error: no plan found with tracking issue #{issue_number}")
        return 2

    state = _gh_issue_state(issue_number)
    if state != "closed":
        print(f"Verdict: skipped-open (issue #{issue_number} state={state})")
        return 1

    verdict = migrate_plan_to_completed(plan_file, apply=apply)
    print(f"Verdict: {verdict}")
    return 0 if verdict in ("migrated", "already-migrated") else 1


def run_sweep(*, apply: bool, cap: int | None) -> int:
    """CLI handler for --sweep: migrate every root plan with a closed tracking issue."""
    plans_dir = Path("docs/plans")
    migrated_count = 0
    rows: list[tuple[str, str, str]] = []

    for plan_file in sorted(plans_dir.glob("*.md")):
        text = plan_file.read_text(errors="replace")
        tracking_url = extract_tracking_issue(text)
        if not tracking_url:
            rows.append((plan_file.name, "no tracking issue in frontmatter", "skip"))
            continue

        match = re.search(r"/issues/(\d+)", tracking_url)
        if not match:
            rows.append((plan_file.name, f"unparseable tracking url: {tracking_url}", "skip"))
            continue

        issue_number = match.group(1)
        state = _gh_issue_state(issue_number)
        if state != "closed":
            rows.append((plan_file.name, f"issue #{issue_number} state={state}", "skip"))
            continue

        if cap is not None and migrated_count >= cap:
            rows.append((plan_file.name, f"issue #{issue_number} closed", "deferred (cap reached)"))
            continue

        verdict = migrate_plan_to_completed(plan_file, apply=apply)
        rows.append((plan_file.name, f"issue #{issue_number} closed", verdict))
        if verdict == "migrated":
            migrated_count += 1

    print(f"{'plan':<50} {'evidence':<40} action")
    for name, evidence, action in rows:
        print(f"{name:<50} {evidence:<40} {action}")
    return 0


def main() -> int:
    args = sys.argv[1:]

    # --issue <N>: path-independent migration keyed on the plan's own tracking
    # frontmatter (issue #1900, Tier 0). This is what /do-merge invokes after a
    # real merge, so it defaults to apply=True unless --dry-run is passed.
    if "--issue" in args:
        idx = args.index("--issue")
        if idx + 1 >= len(args):
            print("Error: --issue requires an issue number")
            return 2
        issue_number = args[idx + 1]
        apply = "--dry-run" not in args
        return run_issue(issue_number, apply=apply)

    # --sweep [--apply] [--cap N]: iterate every root plan, migrate the ones
    # whose tracking issue is closed. Report-only (apply=False) by default.
    if "--sweep" in args:
        apply = "--apply" in args
        cap: int | None = None
        if "--cap" in args:
            cap_idx = args.index("--cap")
            if cap_idx + 1 >= len(args):
                print("Error: --cap requires an integer value")
                return 2
            try:
                cap = int(args[cap_idx + 1])
            except ValueError:
                print("Error: --cap requires an integer value")
                return 2
        return run_sweep(apply=apply, cap=cap)

    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_completed_plan.py <plan-path> [--dry-run]")
        print("       python scripts/migrate_completed_plan.py --issue <N> [--dry-run]")
        print("       python scripts/migrate_completed_plan.py --sweep [--apply] [--cap N]")
        print()
        print("Validates feature documentation and migrates completed plan.")
        print()
        print("Checks:")
        print("  - Feature doc exists at path specified in plan")
        print("  - Feature doc contains minimum sections (title + content)")
        print("  - Feature is indexed in docs/features/README.md")
        print("  - Tracking issue exists (closed on PR merge, not here)")
        print()
        print("On success:")
        print("  - Deletes the plan file")
        print()
        print("Options:")
        print("  --dry-run  Validate only, do not delete plan or close issue")
        print()
        print("  --issue <N>          Migrate the plan tracking issue N (git mv, not delete)")
        print("  --sweep [--apply]    Migrate every root plan with a closed tracking issue")
        print("  --cap N              With --sweep, migrate at most N plans this run")
        return 2

    plan_path = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    # Validate plan exists
    if not plan_path.exists():
        print(f"Error: Plan file not found: {plan_path}")
        return 2

    # Read plan
    try:
        plan_text = plan_path.read_text()
    except Exception as e:
        print(f"Error reading plan: {e}")
        return 2

    print(f"Validating migration for: {plan_path}")
    if dry_run:
        print("[DRY-RUN MODE]")
    print()

    # Extract feature doc path
    feature_doc_path_str = extract_feature_doc_path(plan_text)
    if not feature_doc_path_str:
        print("Error: Could not find feature doc path in ## Documentation section")
        print("Expected pattern: - [ ] Create `docs/features/my-feature.md`")
        return 1

    feature_doc_path = Path(feature_doc_path_str)
    print(f"Feature doc path: {feature_doc_path}")

    # Validate feature doc
    valid, error = validate_feature_doc(feature_doc_path)
    if not valid:
        print(f"Error: {error}")
        return 1
    print("  PASS: Feature doc exists and has content")

    # Extract feature name from README index (avoids .title() mangling acronyms)
    feature_doc_filename = feature_doc_path.name
    feature_name = extract_feature_name_from_index(feature_doc_filename)
    if feature_name:
        print(f"Feature name (from index): {feature_name}")
        # Validate using the extracted name
        valid, error = validate_feature_index(feature_name)
        if not valid:
            print(f"Error: {error}")
            return 1
    else:
        # Fallback: check if the filename appears as a link target in the index
        print(
            f"Warning: No README entry found linking to {feature_doc_filename}. "
            f"Add entry to docs/features/README.md"
        )
        return 1
    print("  PASS: Feature indexed in docs/features/README.md")

    # Note: Issues are closed automatically when the PR merges (via `Closes #N` in PR body)
    tracking_issue = extract_tracking_issue(plan_text)
    if tracking_issue:
        print(f"Tracking issue: {tracking_issue} (will close on PR merge)")
    else:
        print("Warning: No tracking issue found in plan frontmatter")

    # Delete plan
    success, error = delete_plan(plan_path, dry_run)
    if not success:
        print(f"Error: {error}")
        return 1
    if not dry_run:
        print("  PASS: Plan file deleted")

    print()
    if dry_run:
        print("Dry-run validation complete. Plan would be migrated successfully.")
    else:
        print("Plan migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
