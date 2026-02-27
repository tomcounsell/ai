"""Hardlink sync for .claude/{skills,commands,hooks} to ~/.claude/."""

from __future__ import annotations

import json
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
    # Retired thin wrapper commands — consolidated into skills (issue #152)
    ("commands", "do-build.md"),
    ("commands", "do-plan.md"),
    ("commands", "do-test.md"),
    ("commands", "do-docs.md"),
    ("commands", "do-pr-review.md"),
    ("commands", "update.md"),
    ("commands", "sdlc.md"),
]

# Skills tightly coupled to this repo's infrastructure (Telegram bridge,
# macOS Messages, system logs, Google Workspace). These are NOT synced to
# ~/.claude/skills/ because they only work in the context of this project.
# All other skills are shared cross-repo via hardlinks.
PROJECT_ONLY_SKILLS: set[str] = {
    "telegram",
    "reading-sms-messages",
    "checking-system-logs",
    "google-workspace",
}


@dataclass
class LinkAction:
    """A single hardlink action taken."""

    src: str
    dst: str
    action: str  # "created", "exists", "removed", "error"
    error: str | None = None


@dataclass
class HardlinkSyncResult:
    """Result of syncing project .claude dirs to user level."""

    success: bool = True
    actions: list[LinkAction] = field(default_factory=list)
    created: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0


def sync_claude_dirs(project_dir: Path) -> HardlinkSyncResult:
    """Hardlink project .claude/{skills,commands,agents} files to ~/.claude/.

    Skills (directories with SKILL.md), commands (.md files), and agents
    (.md files) are shared cross-repo via hardlinks at the user level.

    Also cleans up stale hardlinks in ~/.claude/ that no longer have
    a corresponding source in the project directory.
    """
    result = HardlinkSyncResult()
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

    # Sync SDLC enforcement hooks to user level
    hook_result = sync_user_hooks(project_dir)
    result.actions.extend(hook_result.actions)
    result.created += hook_result.created
    result.skipped += hook_result.skipped
    result.removed += hook_result.removed
    result.errors += hook_result.errors

    if result.errors > 0:
        result.success = False

    return result


def _sync_skills(src_dir: Path, dst_dir: Path, result: HardlinkSyncResult) -> None:
    """Sync skill directories (each containing SKILL.md) and all sub-files.

    Skills use progressive disclosure: SKILL.md is loaded on invocation,
    but sub-files (templates, scripts, references) are loaded on-demand
    via Read tool calls. All files in the skill directory must be synced
    so sub-file references resolve at the user level too.
    """
    if not src_dir.is_dir():
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(src_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue

        # Skip project-only skills — they only work in this repo's context
        if skill_dir.name in PROJECT_ONLY_SKILLS:
            continue

        dst_skill_dir = dst_dir / skill_dir.name

        # Sync SKILL.md
        _ensure_hardlink(skill_file, dst_skill_dir / "SKILL.md", dst_skill_dir, result)

        # Sync all sub-files (templates, scripts, references)
        for src_file in sorted(skill_dir.rglob("*")):
            if not src_file.is_file():
                continue
            if src_file.name == "SKILL.md":
                continue  # Already synced above
            if src_file.suffix == ".pyc" or "__pycache__" in src_file.parts:
                continue  # Skip compiled Python cache

            rel = src_file.relative_to(skill_dir)
            dst_file = dst_skill_dir / rel
            _ensure_hardlink(src_file, dst_file, dst_file.parent, result)


def _sync_commands(src_dir: Path, dst_dir: Path, result: HardlinkSyncResult) -> None:
    """Sync command .md files."""
    if not src_dir.is_dir():
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    for cmd_file in sorted(src_dir.glob("*.md")):
        dst_file = dst_dir / cmd_file.name
        _ensure_hardlink(cmd_file, dst_file, dst_dir, result)


def _ensure_hardlink(
    src: Path, dst: Path, dst_parent: Path, result: HardlinkSyncResult
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


def _cleanup_renamed(user_claude: Path, result: HardlinkSyncResult) -> None:
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
    src_dir: Path, dst_dir: Path, result: HardlinkSyncResult
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
    src_dir: Path, dst_dir: Path, result: HardlinkSyncResult
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


def sync_user_hooks(project_dir: Path) -> HardlinkSyncResult:
    """Copy SDLC hooks to ~/.claude/hooks/sdlc/ and merge into settings.json.

    Only SDLC enforcement hooks are synced to user level. Other hooks
    (validators, calendar, etc.) remain project-specific.
    """
    result = HardlinkSyncResult()
    user_claude = Path.home() / ".claude"

    src_hooks = project_dir / ".claude" / "hooks" / "sdlc"
    dst_hooks = user_claude / "hooks" / "sdlc"

    if not src_hooks.is_dir():
        return result

    dst_hooks.mkdir(parents=True, exist_ok=True)

    # Copy hook scripts via hardlinks
    for src_file in sorted(src_hooks.glob("*.py")):
        dst_file = dst_hooks / src_file.name
        _ensure_hardlink(src_file, dst_file, dst_hooks, result)

    # Merge hook entries into ~/.claude/settings.json
    _merge_hook_settings(user_claude / "settings.json", dst_hooks, result)

    return result


# SDLC hook definitions for ~/.claude/settings.json.
# Each tuple: (hook_event, matcher, script_name, timeout)
_SDLC_HOOK_DEFS: list[tuple[str, str, str, int]] = [
    ("PreToolUse", "Bash", "validate_commit_message.py", 10),
    ("PostToolUse", "Write", "sdlc_reminder.py", 10),
    ("PostToolUse", "Edit", "sdlc_reminder.py", 10),
    ("Stop", "", "validate_sdlc_on_stop.py", 15),
]


def _merge_hook_settings(
    settings_path: Path, hooks_dir: Path, result: HardlinkSyncResult
) -> None:
    """Merge SDLC hook entries into ~/.claude/settings.json.

    Reads existing settings, adds SDLC hook entries if not already present
    (deduplicating by command string with matcher-aware updates), and writes
    back. When a hook command already exists but its matcher has changed, the
    existing block's matcher is updated in place. Never clobbers non-SDLC
    user hooks.
    """
    rel_path = str(settings_path).replace(str(Path.home()), "~")

    try:
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
        else:
            settings = {}
    except (json.JSONDecodeError, OSError) as e:
        result.actions.append(
            LinkAction("", rel_path, "error", f"Failed to read settings: {e}")
        )
        result.errors += 1
        return

    hooks = settings.setdefault("hooks", {})
    added = 0

    for hook_event, matcher, script_name, timeout in _SDLC_HOOK_DEFS:
        command = f"python {hooks_dir / script_name}"
        hook_entry = {
            "type": "command",
            "command": command,
            "timeout": timeout,
        }
        matcher_block = {
            "matcher": matcher,
            "hooks": [hook_entry],
        }

        event_hooks = hooks.setdefault(hook_event, [])

        # Check if a hook with the same command already exists
        already_exists = False
        for existing_block in event_hooks:
            for existing_hook in existing_block.get("hooks", []):
                if existing_hook.get("command", "") == command:
                    already_exists = True
                    # Update matcher if it changed
                    if existing_block.get("matcher", "") != matcher:
                        existing_block["matcher"] = matcher
                    break
            if already_exists:
                break

        if not already_exists:
            event_hooks.append(matcher_block)
            added += 1

    if added > 0:
        try:
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            result.actions.append(
                LinkAction("", rel_path, "created", f"Merged {added} hook entries")
            )
            result.created += added
        except OSError as e:
            result.actions.append(
                LinkAction("", rel_path, "error", f"Failed to write settings: {e}")
            )
            result.errors += 1
    else:
        result.actions.append(LinkAction("", rel_path, "exists"))
        result.skipped += 1
