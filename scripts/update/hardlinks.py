"""Hardlink sync for .claude/{skills-global,commands,hooks} to ~/.claude/."""

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
    # Retired skills — consolidated into new-skill
    ("skills", "add-feature"),
    ("skills", "new-valor-skill"),
    # Orphan hardlink from an old repo version — removed with no source remaining
    ("skills", "prepare-app"),
    # Retired skills — moved to reflections
    ("skills", "daily-integration-audit"),
    # Retired skills — superseded by byob MCP + bowser subagent (issue #1256).
    # agent-browser predates the skills-global split and was synced under the old
    # ~/.claude/skills dir-symlink layout, so a stale hardlink can linger on
    # long-lived fleet machines — swept here alongside its bowser sibling.
    ("skills", "bowser"),
    ("skills", "agent-browser"),
    # Moved from skills-global to project-only .claude/skills/
    ("skills", "linkedin"),
    ("skills", "linkedin-messaging"),
    ("skills", "officecli"),
    ("skills", "x-com"),
    # Moved from global commands to project-only .claude/commands/
    ("commands", "kill.md"),
    # Moved from skills-global to project-only .claude/skills/
    ("skills", "update"),
    # Old command files superseded by same-named skills (removed to prevent duplicates)
    ("commands", "add-feature.md"),
    ("commands", "audit-models.md"),
    ("commands", "audit-next-tool.md"),
    ("commands", "do-merge.md"),
    ("commands", "prepare_app.md"),
    ("commands", "prime.md"),
    ("commands", "pthread.md"),
    ("commands", "queue-status.md"),
    ("commands", "setup.md"),
    # Project-only skills removed from user-level (only work in this repo's context)
    ("skills", "checking-system-logs"),
    ("skills", "reading-sms-messages"),
    ("skills", "telegram"),
    # Renamed: tts -> do-voice-recording (canonical TTS step the other skills defer to)
    ("skills", "tts"),
    # Moved from skills-global to project-only .claude/skills/ (issue #1783, Bucket C —
    # these only ever run from this repo's orchestrator context, never cross-repo).
    ("skills", "setup"),
    ("skills", "prime"),
    ("skills", "sdlc"),
    ("skills", "do-deploy"),
    # Renamed namespace: .claude/commands/granite/ -> .claude/commands/roles/
    # (plan #1924 PTY teardown — the prime commands survive under the new
    # name; the stale user-level granite/ dir is removed on every machine).
    ("commands", "granite"),
    # Deleted docs/xref skills consolidated into reflections/docs_auditor.py
    # (#1247, #2084). do-docs-audit predates the skills-global split and was
    # synced under the old ~/.claude/skills dir-symlink layout; the do-xref* names
    # are untracked sync residue with no repo source. All swept on every machine.
    ("skills", "do-docs-audit"),
    ("skills", "do-xref-audit"),
    ("skills", "do-xref"),
    # Orphan hardlinks — source deleted, no live replacement (issue #2065)
    ("skills", "audit-next-tool"),
    ("skills", "do-design-review"),
    ("skills", "get-telegram-messages"),
    ("skills", "searching-message-history"),
]

# Standalone executable scripts hardlinked into ~/.local/bin so they're available
# anywhere on PATH (not just from the repo). Each tuple is (src_relpath, dst_name).
# Adding an entry here propagates to every machine on the next /update run.
USER_BIN_SCRIPTS: list[tuple[str, str]] = [
    ("scripts/sdlc-tool", "sdlc-tool"),
]


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

    # Migrate old directory-symlink layout: ~/.claude/skills used to be a symlink
    # to .claude/skills/. Now .claude/skills/ is project-only and globally-shared
    # skills live in .claude/skills-global/. If the symlink is still in place,
    # remove it so _sync_skills can create a real directory with proper hardlinks.
    user_skills = user_claude / "skills"
    if user_skills.is_symlink():
        user_skills.unlink()
        result.actions.append(
            LinkAction("", "~/.claude/skills", "removed", "migrated from dir-symlink to hardlinks")
        )
        result.removed += 1

    # Sync globally-shared skills from skills-global/ (not skills/, which is project-only).
    _sync_skills(project_dir / ".claude" / "skills-global", user_claude / "skills", result)

    # Sync commands: each is a .md file.
    # Commands are slash-command aliases — always shared.
    _sync_commands(project_dir / ".claude" / "commands", user_claude / "commands", result)

    # Sync agents: each is a .md file.
    # Agents defined here are general-purpose subagents used across all projects
    # (e.g. frontend-tester, builder, validator). They are shared to ~/.claude/agents/
    # so they are available regardless of which repo Claude Code is running in.
    # Project-specific agents that should NOT be shared belong in a project's own
    # .claude/agents/ directory outside this repo.
    _sync_commands(project_dir / ".claude" / "agents", user_claude / "agents", result)

    # Remove explicitly renamed commands/skills (inode-guarded: a target still
    # hardlinked to a live project source is preserved; only genuine orphans go)
    _cleanup_renamed(user_claude, project_dir, result)

    # Clean up stale hardlinks that no longer have a source
    _cleanup_stale_commands(project_dir / ".claude" / "commands", user_claude / "commands", result)
    _cleanup_stale_skills(project_dir / ".claude" / "skills-global", user_claude / "skills", result)
    _cleanup_stale_commands(project_dir / ".claude" / "agents", user_claude / "agents", result)

    # Sync SDLC enforcement hooks to user level
    hook_result = sync_user_hooks(project_dir)
    result.actions.extend(hook_result.actions)
    result.created += hook_result.created
    result.skipped += hook_result.skipped
    result.removed += hook_result.removed
    result.errors += hook_result.errors

    # Sync standalone scripts to ~/.local/bin (e.g. sdlc-tool)
    script_result = sync_user_scripts(project_dir)
    result.actions.extend(script_result.actions)
    result.created += script_result.created
    result.skipped += script_result.skipped
    result.removed += script_result.removed
    result.errors += script_result.errors

    # Sync baseline env vars and editor settings to ~/.claude/settings.json
    editor_result = sync_user_editor_settings()
    result.actions.extend(editor_result.actions)
    result.created += editor_result.created
    result.skipped += editor_result.skipped
    result.errors += editor_result.errors

    if result.errors > 0:
        result.success = False

    return result


# Baseline env vars and top-level settings synced to every machine's
# ~/.claude/settings.json. Additive only — a key is set when absent, but a
# value the user has already customized is left alone.
_USER_ENV_DEFAULTS: dict[str, str] = {
    # Agent teams: INTERACTIVE sessions only. Every headless `claude -p`
    # spawn overrides this to "0" via a CLI --settings env block (the only
    # settings layer that outranks this one) — see HEADLESS_ENV_OVERRIDES in
    # agent/session_runner/hook_edge.py and the decision record at
    # docs/features/agent-teams-headless-policy.md. Re-review both when
    # agent teams goes GA-default in Claude Code.
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
    "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
}

_USER_TOP_LEVEL_DEFAULTS: dict[str, object] = {
    # UX chrome the fleet runs without.
    "spinnerTipsEnabled": False,
    "promptSuggestionEnabled": False,
    "showTurnDuration": False,
    "awaySummaryEnabled": False,
    # Never co-author commits to Claude (global rule).
    "includeCoAuthoredBy": False,
    # Release + behavior baseline shared across machines.
    "autoUpdatesChannel": "stable",
    "effortLevel": "high",
    "skipDangerousModePermissionPrompt": True,
}


def sync_user_editor_settings() -> HardlinkSyncResult:
    """Merge baseline env vars and top-level settings into ~/.claude/settings.json.

    Additive only: sets a key if it's missing, never overwrites a value the
    user (or a prior manual edit) has already set.
    """
    result = HardlinkSyncResult()
    settings_path = Path.home() / ".claude" / "settings.json"
    rel_path = str(settings_path).replace(str(Path.home()), "~")

    try:
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
        else:
            settings = {}
    except (json.JSONDecodeError, OSError) as e:
        result.actions.append(LinkAction("", rel_path, "error", f"Failed to read settings: {e}"))
        result.errors += 1
        return result

    added = 0
    env = settings.setdefault("env", {})
    for key, value in _USER_ENV_DEFAULTS.items():
        if key not in env:
            env[key] = value
            added += 1

    for key, value in _USER_TOP_LEVEL_DEFAULTS.items():
        if key not in settings:
            settings[key] = value
            added += 1

    if added > 0:
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            result.actions.append(
                LinkAction("", rel_path, "created", f"Merged editor settings: added {added}")
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

    return result


def sync_user_scripts(project_dir: Path) -> HardlinkSyncResult:
    """Hardlink standalone scripts from scripts/ to ~/.local/bin.

    Each entry in USER_BIN_SCRIPTS is hardlinked so the file in the repo and
    the one on PATH share an inode — editing either updates both. Missing
    sources are logged as errors so the operator notices a deleted source.
    """
    result = HardlinkSyncResult()
    user_bin = Path.home() / ".local" / "bin"
    user_bin.mkdir(parents=True, exist_ok=True)

    for src_relpath, dst_name in USER_BIN_SCRIPTS:
        src = project_dir / src_relpath
        dst = user_bin / dst_name
        rel_src = str(src).replace(str(Path.home()), "~")
        rel_dst = str(dst).replace(str(Path.home()), "~")

        if not src.is_file():
            result.actions.append(LinkAction(rel_src, rel_dst, "error", f"Source missing: {src}"))
            result.errors += 1
            continue

        # Reuse the same hardlink helper as skills/commands. The wrapper is
        # an executable shell script — the file mode is preserved by hardlinking
        # (both inodes share permissions), so no chmod is needed after linking.
        _ensure_hardlink(src, dst, user_bin, result)

    return result


def _sync_skills(src_dir: Path, dst_dir: Path, result: HardlinkSyncResult) -> None:
    """Sync skill directories (each containing SKILL.md) and all sub-files.

    Skills use progressive disclosure: SKILL.md is loaded on invocation,
    but sub-files (templates, scripts, references) are loaded on-demand
    via Read tool calls. All files in the skill directory must be synced
    so sub-file references resolve at the user level too.

    Project-only skills are excluded *structurally*: this function is only
    ever called with ``.claude/skills-global/`` as ``src_dir`` (see
    ``sync_claude_dirs``). Skills under ``.claude/skills/`` are never a sync
    source, so they never reach the user level. The
    ``test_no_project_only_skill_is_a_sync_destination`` unit test asserts this
    invariant holds against the live filesystem.
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
    """Sync command .md files, including namespaced subdirectories.

    Commands live either at the top level (``foo.md`` -> ``/foo``) or in a
    namespace subdirectory (``roles/prime-pm-role.md`` ->
    ``/roles:prime-pm-role``). The session runner primes claude in OTHER
    repos' worktrees, so its namespaced prime commands MUST sync to
    ``~/.claude/commands/roles/`` to be resolvable there — a top-level-only
    glob once left every session hanging on "Unknown command:
    /roles:prime-pm-role" (PR #1694 moved persona delivery from
    --append-system-prompt to these prime slash commands). Recurse with
    rglob and preserve the relative subdir so the namespace is kept intact.
    """
    if not src_dir.is_dir():
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    for cmd_file in sorted(src_dir.rglob("*.md")):
        dst_file = dst_dir / cmd_file.relative_to(src_dir)
        _ensure_hardlink(cmd_file, dst_file, dst_file.parent, result)


def _ensure_hardlink(src: Path, dst: Path, dst_parent: Path, result: HardlinkSyncResult) -> None:
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


def _target_is_hardlinked_to_project(target: Path, src_dir: Path) -> bool:
    """True if ``target`` (file or directory) shares an inode with a live source under ``src_dir``.

    For a file target this is a direct inode check. For a directory target
    (a skill dir), any single file inside the directory that shares an inode
    with a source file under ``src_dir`` proves the directory was synced from
    that project — enough to mark it project-backed.
    """
    if target.is_dir():
        for child in target.rglob("*"):
            if child.is_file() and _is_hardlinked_to_project(child, src_dir):
                return True
        return False
    return _is_hardlinked_to_project(target, src_dir)


def _cleanup_renamed(user_claude: Path, project_dir: Path, result: HardlinkSyncResult) -> None:
    """Remove old-name commands/skills listed in RENAMED_REMOVALS.

    Inode-guarded (issue #1783, concern #2): a target that is still hardlinked
    to a live source under this project's ``skills-global/`` (skills) or
    ``commands/`` / ``agents/`` (commands) is legitimately synced and is
    **preserved** — this protects a foreign repo that provides its own
    same-named user-level skill (e.g. a moved Bucket C name like ``sdlc``)
    from being deleted by our blanket ``RENAMED_REMOVALS`` sweep. Only genuine
    orphans (not hardlinked to any live project source) are removed.
    """
    src_for_kind = {
        "skills": project_dir / ".claude" / "skills-global",
        "commands": project_dir / ".claude" / "commands",
        "agents": project_dir / ".claude" / "agents",
    }
    for kind, old_name in RENAMED_REMOVALS:
        target = user_claude / kind / old_name
        if not target.exists():
            continue

        src_dir = src_for_kind.get(kind)
        if src_dir is not None and src_dir.is_dir():
            if _target_is_hardlinked_to_project(target, src_dir):
                continue  # legitimately project-backed — preserve, do NOT remove

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


def _cleanup_stale_commands(src_dir: Path, dst_dir: Path, result: HardlinkSyncResult) -> None:
    """Remove command files in dst_dir that were hardlinked from old source names.

    Only removes files that share an inode with a file in src_dir (proving
    this project created them) but whose name no longer exists in src_dir.
    Files from other projects are left untouched.
    """
    if not dst_dir.is_dir():
        return

    # Collect inodes of all current source commands (recursing into
    # namespace subdirs so roles/*.md are recognized as live sources).
    src_inodes: set[int] = set()
    if src_dir.is_dir():
        for src_file in sorted(src_dir.rglob("*.md")):
            try:
                src_inodes.add(os.stat(src_file).st_ino)
            except OSError:
                continue

    for dst_file in sorted(dst_dir.rglob("*.md")):
        src_file = src_dir / dst_file.relative_to(dst_dir)
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


def _cleanup_stale_skills(src_dir: Path, dst_dir: Path, result: HardlinkSyncResult) -> None:
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
            # Source dir survives — prune individual files whose source was
            # deleted (intra-dir orphans), then keep the dir.
            _prune_intra_dir_orphans(src_skill_dir, dst_skill_dir, result)
            continue

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


def _prune_intra_dir_orphans(
    src_skill_dir: Path, dst_skill_dir: Path, result: HardlinkSyncResult
) -> None:
    """Remove files inside a synced skill dir whose source file was deleted.

    The dir-level stale cleanup only handles a skill dir whose entire source is
    gone. When a single tracked file is deleted from a *surviving* skill dir
    (e.g. do-pr-review/sub-skills/README.md, folded into SKILL.md), the old
    hardlink lingers at the user level and can be loaded alongside the current
    SKILL.md, contradicting it.

    Ownership guard mirrors the dir-level cleanup: the prune only runs when
    the destination SKILL.md shares an inode with this project's source
    SKILL.md — proving this project synced the dir. Foreign or hand-made
    skill dirs are left untouched. Subdirectories emptied by the prune are
    removed bottom-up (never the skill dir itself).
    """
    src_skill_file = src_skill_dir / "SKILL.md"
    dst_skill_file = dst_skill_dir / "SKILL.md"
    if not src_skill_file.is_file() or not dst_skill_file.is_file():
        return

    try:
        if os.stat(src_skill_file).st_ino != os.stat(dst_skill_file).st_ino:
            return  # Not synced from this project — leave it alone
    except OSError:
        return

    emptied_parents: set[Path] = set()
    for dst_file in sorted(dst_skill_dir.rglob("*")):
        if not dst_file.is_file():
            continue
        rel = dst_file.relative_to(dst_skill_dir)
        if (src_skill_dir / rel).exists():
            continue  # Still has a source — not stale

        rel_dst = str(dst_file).replace(str(Path.home()), "~")
        try:
            dst_file.unlink()
            result.actions.append(LinkAction("", rel_dst, "removed"))
            result.removed += 1
            emptied_parents.add(dst_file.parent)
        except OSError as e:
            result.actions.append(LinkAction("", rel_dst, "error", str(e)))
            result.errors += 1

    # Remove subdirectories the prune emptied, walking up toward (but never
    # including) the skill dir. rmdir only succeeds on empty dirs, so a dir
    # that still holds live files stops the walk naturally.
    for parent in sorted(emptied_parents, key=lambda p: len(p.parts), reverse=True):
        current = parent
        while current != dst_skill_dir:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


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


def _merge_hook_settings(settings_path: Path, hooks_dir: Path, result: HardlinkSyncResult) -> None:
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
        result.actions.append(LinkAction("", rel_path, "error", f"Failed to read settings: {e}"))
        result.errors += 1
        return

    hooks = settings.setdefault("hooks", {})
    added = 0
    updated = 0

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
                        updated += 1
                    break
            if already_exists:
                break

        if not already_exists:
            event_hooks.append(matcher_block)
            added += 1

    if added > 0 or updated > 0:
        try:
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            parts = []
            if added:
                parts.append(f"added {added}")
            if updated:
                parts.append(f"updated {updated}")
            result.actions.append(
                LinkAction("", rel_path, "created", f"Merged hooks: {', '.join(parts)}")
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
