"""Hardlink sync for .claude/{skills,commands} to ~/.claude/."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Old names that were renamed. The update system removes these from ~/.claude/
# if the old name is still present. Add entries here when renaming skills or commands.
# Format: list of (kind, old_name) where kind is "commands" or "skills".
RENAMED_REMOVALS: list[tuple[str, str]] = [
    ("commands", "build.md"),
    ("commands", "make-plan.md"),
    ("commands", "update-docs.md"),
    ("commands", "review.md"),
    ("skills", "build"),
    ("skills", "make-plan"),
    ("skills", "update-docs"),
]


@dataclass
class LinkAction:
    """A single hardlink action taken."""

    src: str
    dst: str
    action: str  # "created", "exists", "removed", "error"
    error: str | None = None


@dataclass
class SymlinkSyncResult:
    """Result of syncing project .claude dirs to user level."""

    success: bool = True
    actions: list[LinkAction] = field(default_factory=list)
    created: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0


def sync_claude_dirs(project_dir: Path) -> SymlinkSyncResult:
    """Hardlink project .claude/{skills,commands,agents} files to ~/.claude/.

    Skills (directories with SKILL.md), commands (.md files), and agents
    (.md files) are shared cross-repo via hardlinks at the user level.

    Also cleans up stale hardlinks in ~/.claude/ that no longer have
    a corresponding source in the project directory.
    """
    result = SymlinkSyncResult()
    user_claude = Path.home() / ".claude"

    # Sync skills: each is a directory containing SKILL.md.
    # Skills are cross-repo tools (do-test, do-plan, etc.) — always shared.
    _sync_skills(project_dir / ".claude" / "skills", user_claude / "skills", result)

    # Sync commands: each is a .md file.
    # Commands are slash-command aliases — always shared.
    _sync_commands(
        project_dir / ".claude" / "commands", user_claude / "commands", result
    )

    # Sync agents: each is a .md file.
    # Agents defined here are general-purpose subagents used across all projects
    # (e.g. frontend-tester, builder, validator). They are shared to ~/.claude/agents/
    # so they are available regardless of which repo Claude Code is running in.
    # Project-specific agents that should NOT be shared belong in a project's own
    # .claude/agents/ directory outside this repo.
    _sync_commands(project_dir / ".claude" / "agents", user_claude / "agents", result)

    # Remove explicitly renamed commands/skills (by name, not inode)
    _cleanup_renamed(user_claude, result)

    # Clean up stale hardlinks that no longer have a source
    _cleanup_stale_commands(
        project_dir / ".claude" / "commands", user_claude / "commands", result
    )
    _cleanup_stale_skills(
        project_dir / ".claude" / "skills", user_claude / "skills", result
    )
    _cleanup_stale_commands(
        project_dir / ".claude" / "agents", user_claude / "agents", result
    )

    if result.errors > 0:
        result.success = False

    return result


def _sync_skills(src_dir: Path, dst_dir: Path, result: SymlinkSyncResult) -> None:
    """Sync skill directories (each containing SKILL.md)."""
    if not src_dir.is_dir():
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(src_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue

        dst_skill_dir = dst_dir / skill_dir.name
        dst_skill_file = dst_skill_dir / "SKILL.md"

        _ensure_hardlink(skill_file, dst_skill_file, dst_skill_dir, result)


def _sync_commands(src_dir: Path, dst_dir: Path, result: SymlinkSyncResult) -> None:
    """Sync command .md files."""
    if not src_dir.is_dir():
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    for cmd_file in sorted(src_dir.glob("*.md")):
        dst_file = dst_dir / cmd_file.name
        _ensure_hardlink(cmd_file, dst_file, dst_dir, result)


def _ensure_hardlink(
    src: Path, dst: Path, dst_parent: Path, result: SymlinkSyncResult
) -> None:
    """Ensure dst is a hardlink to src. Create if missing or stale."""
    rel_src = str(src).replace(str(Path.home()), "~")
    rel_dst = str(dst).replace(str(Path.home()), "~")

    try:
        if dst.exists():
            # Check if already the same inode (hardlinked)
            if os.stat(src).st_ino == os.stat(dst).st_ino:
                result.actions.append(LinkAction(rel_src, rel_dst, "exists"))
                result.skipped += 1
                return

            # Different file — replace with hardlink
            dst.unlink()

        dst_parent.mkdir(parents=True, exist_ok=True)
        os.link(src, dst)
        result.actions.append(LinkAction(rel_src, rel_dst, "created"))
        result.created += 1

    except OSError as e:
        result.actions.append(LinkAction(rel_src, rel_dst, "error", str(e)))
        result.errors += 1


def _is_hardlinked_to_project(dst_file: Path, src_dir: Path) -> bool:
    """Check if dst_file shares an inode with any file under src_dir.

    This ensures we only clean up files that THIS project created,
    not files from other projects that also sync to ~/.claude/.
    """
    try:
        dst_ino = os.stat(dst_file).st_ino
    except OSError:
        return False

    for src_file in src_dir.rglob("*"):
        if src_file.is_file():
            try:
                if os.stat(src_file).st_ino == dst_ino:
                    return True
            except OSError:
                continue
    return False


def _cleanup_renamed(user_claude: Path, result: SymlinkSyncResult) -> None:
    """Remove old-name commands/skills listed in RENAMED_REMOVALS."""
    for kind, old_name in RENAMED_REMOVALS:
        target = user_claude / kind / old_name
        if not target.exists():
            continue

        rel = str(target).replace(str(Path.home()), "~")
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            result.actions.append(LinkAction("", rel, "removed"))
            result.removed += 1
        except OSError as e:
            result.actions.append(LinkAction("", rel, "error", str(e)))
            result.errors += 1


def _cleanup_stale_commands(
    src_dir: Path, dst_dir: Path, result: SymlinkSyncResult
) -> None:
    """Remove command files in dst_dir that were hardlinked from old source names.

    Only removes files that share an inode with a file in src_dir (proving
    this project created them) but whose name no longer exists in src_dir.
    Files from other projects are left untouched.
    """
    if not dst_dir.is_dir():
        return

    # Collect inodes of all current source commands
    src_inodes: set[int] = set()
    if src_dir.is_dir():
        for src_file in sorted(src_dir.glob("*.md")):
            try:
                src_inodes.add(os.stat(src_file).st_ino)
            except OSError:
                continue

    for dst_file in sorted(dst_dir.glob("*.md")):
        src_file = src_dir / dst_file.name
        if src_file.exists():
            continue  # Still has a source — not stale

        # Only remove if this file's inode matches a current source file,
        # meaning it's an old hardlink from a previous name in this project
        try:
            dst_ino = os.stat(dst_file).st_ino
        except OSError:
            continue

        if dst_ino not in src_inodes:
            continue  # Not from this project — leave it alone

        rel_dst = str(dst_file).replace(str(Path.home()), "~")
        try:
            dst_file.unlink()
            result.actions.append(LinkAction("", rel_dst, "removed"))
            result.removed += 1
        except OSError as e:
            result.actions.append(LinkAction("", rel_dst, "error", str(e)))
            result.errors += 1


def _cleanup_stale_skills(
    src_dir: Path, dst_dir: Path, result: SymlinkSyncResult
) -> None:
    """Remove skill directories in dst_dir that were hardlinked from old source names.

    Only removes directories whose SKILL.md shares an inode with a file in
    src_dir (proving this project created them) but whose name no longer
    exists in src_dir. Directories from other projects are left untouched.
    """
    if not dst_dir.is_dir():
        return

    # Collect inodes of all current source SKILL.md files
    src_inodes: set[int] = set()
    if src_dir.is_dir():
        for skill_dir in sorted(src_dir.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if skill_file.is_file():
                try:
                    src_inodes.add(os.stat(skill_file).st_ino)
                except OSError:
                    continue

    for dst_skill_dir in sorted(dst_dir.iterdir()):
        if not dst_skill_dir.is_dir():
            continue

        src_skill_dir = src_dir / dst_skill_dir.name
        if src_skill_dir.exists():
            continue  # Still has a source — not stale

        # Only remove if SKILL.md inode matches a current source,
        # meaning it's an old hardlink from a previous name in this project
        dst_skill_file = dst_skill_dir / "SKILL.md"
        if not dst_skill_file.is_file():
            continue

        try:
            dst_ino = os.stat(dst_skill_file).st_ino
        except OSError:
            continue

        if dst_ino not in src_inodes:
            continue  # Not from this project — leave it alone

        rel_dst = str(dst_skill_dir).replace(str(Path.home()), "~")
        try:
            shutil.rmtree(dst_skill_dir)
            result.actions.append(LinkAction("", rel_dst, "removed"))
            result.removed += 1
        except OSError as e:
            result.actions.append(LinkAction("", rel_dst, "error", str(e)))
            result.errors += 1
