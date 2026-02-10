"""Hardlink sync for .claude/{skills,commands} to ~/.claude/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LinkAction:
    """A single hardlink action taken."""

    src: str
    dst: str
    action: str  # "created", "exists", "error"
    error: str | None = None


@dataclass
class SymlinkSyncResult:
    """Result of syncing project .claude dirs to user level."""

    success: bool = True
    actions: list[LinkAction] = field(default_factory=list)
    created: int = 0
    skipped: int = 0
    errors: int = 0


def sync_claude_dirs(project_dir: Path) -> SymlinkSyncResult:
    """Hardlink project .claude/{skills,commands} files to ~/.claude/.

    Skills (directories with SKILL.md) and commands (.md files) are
    shared cross-repo via hardlinks at the user level. Agents are
    repo-specific and are NOT synced.
    """
    result = SymlinkSyncResult()
    user_claude = Path.home() / ".claude"

    # Sync skills: each is a directory containing SKILL.md
    _sync_skills(project_dir / ".claude" / "skills", user_claude / "skills", result)

    # Sync commands: each is a .md file
    _sync_commands(
        project_dir / ".claude" / "commands", user_claude / "commands", result
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
