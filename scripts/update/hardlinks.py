"""Hardlink sync for .claude/{skills-global,commands,hooks} to ~/.claude/."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from scripts.update.hook_manifest import HookDeclaration, HookManifestError, load_hook_manifest

# Leading interpreter token for every PROJECT-scope generated hook command
# (issue #2503). Claude Code runs hook commands through a non-interactive
# /bin/sh with no PATH, so a bare `python` fails exit 127 and silently disables
# the guard. The shim resolves the repo venv at hook-execution time and works
# from both the main checkout and any .worktrees/{slug}/ checkout. It is quoted
# the same way the existing script paths quote $CLAUDE_PROJECT_DIR, so the
# committed .claude/settings.json stays machine-neutral (no absolute path).
PROJECT_HOOK_INTERPRETER = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python'

# The worst-case interpreter a GLOBAL-scope hook can land on: /usr/bin/python3
# on macOS is 3.9. Every declared global-scope script must import and run clean
# under this floor; enforced by test, not convention. Importable by tests so the
# floor lives in exactly one place. Grain of salt: bump only in lockstep with the
# oldest system python3 across the fleet.
MIN_GLOBAL_PYTHON = (3, 9)

# Absolute system python3 candidates probed (in order) to interpret GLOBAL-scope
# hooks. The first that actually *executes* wins — see resolve_global_interpreter.
GLOBAL_INTERPRETER_CANDIDATES: tuple[str, ...] = (
    "/usr/bin/python3",
    "/usr/local/bin/python3",
    "/opt/homebrew/bin/python3",
)

# Seconds to wait for a candidate's `-V` probe. Provisional/tunable: a stub
# interpreter answers instantly and a real one nearly so, so this only bounds a
# pathological hang.
_GLOBAL_INTERPRETER_PROBE_TIMEOUT = 5.0


def resolve_global_interpreter() -> str | None:
    """Return the first GLOBAL_INTERPRETER_CANDIDATES entry that runs, else None.

    Probes by *executing* ``<candidate> -V`` under a stripped environment and
    accepting only an exit-0 result — deliberately NOT ``os.path.exists``. On
    macOS ``/usr/bin/python3`` exists even without the Command Line Tools, where
    it is a stub that exits non-zero and pops a GUI install dialog. A presence
    check would select that stub and re-create exactly the silent-breakage class
    (issue #2503) this resolver exists to end. ``env={}`` mirrors the stripped
    /bin/sh a hook actually runs under.
    """
    for candidate in GLOBAL_INTERPRETER_CANDIDATES:
        try:
            proc = subprocess.run(
                [candidate, "-V"],
                env={},
                capture_output=True,
                timeout=_GLOBAL_INTERPRETER_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return candidate
    return None


# Old names that were renamed. The update system removes these from ~/.claude/
# if the old name is still present. Add entries here when renaming skills,
# commands, or hooks.
# Format: list of (kind, old_name) where kind is "commands", "skills", or
# "hooks" (old_name for "hooks" is the script path relative to
# .claude/hooks/, e.g. "sdlc/old_script.py").
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
    # Renamed: pencil-design -> pen-design (the Pencil app/company rebranded to
    # Pen at pen.dev; CLI package is @pen.dev/cli, MCP server key is "pen").
    ("skills", "pencil-design"),
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
    # Renamed: do-skills-audit -> audit-skills (the auditor keeps the audit-* verb
    # naming; the stale user-level do-skills-audit/ dir is swept on every machine).
    ("skills", "do-skills-audit"),
    # Merged: skillify folded into new-skill as its SESSION_CAPTURE.md sub-file.
    ("skills", "skillify"),
    # Pruned dead skills — sources deleted, no live replacement.
    ("skills", "analyze"),
    ("skills", "claude-standards"),
    ("skills", "deepen"),
    ("skills", "observability"),
    ("skills", "do-oop-audit"),
    ("skills", "pthread"),
    ("skills", "tdd"),
    # Renamed: sdlc/validate_commit_message.py -> sdlc/validate_commit_message_sdlc.py
    # to disambiguate from validators/validate_commit_message.py, a different
    # check under a colliding name (co-author/empty-message vs code-to-main
    # block; docs/plans/hook-registration-manifest-dispatcher.md spike-4B).
    ("hooks", "sdlc/validate_commit_message.py"),
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


def user_hooks_root_is_repo_aliased(
    project_dir: Path, user_claude: Path | None = None
) -> Path | None:
    """Return the resolved target when ``~/.claude/hooks`` aliases the repo tree.

    Under the legacy layout ``~/.claude/hooks`` is a directory symlink into a
    checkout's ``.claude/hooks/``. There is then no separate user tree: every
    path beneath it is tracked source, and a pass that enumerates the directory
    and removes what it reads as an orphaned user file removes the checkout
    instead. That is not hypothetical — it deleted 36 tracked files during the
    #2521 build (issue #2567).

    The same inode under both paths is the trap that made that build's audit
    conclude the two were independent hardlinks. They were one file. Resolve the
    paths and compare directories rather than reasoning from ``stat``.

    Returns ``None`` when the path is a real, independent user directory or is
    absent, so callers read a truthy result as "do not write or delete here".
    """
    hooks_root = (user_claude if user_claude is not None else Path.home() / ".claude") / "hooks"
    if hooks_root.is_symlink():
        return hooks_root.resolve()
    if not hooks_root.is_dir():
        return None
    repo_hooks = project_dir / ".claude" / "hooks"
    try:
        if repo_hooks.is_dir() and hooks_root.resolve() == repo_hooks.resolve():
            return hooks_root.resolve()
    except OSError:
        return None
    return None


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

    # The matching hooks migration deliberately does NOT live here. It runs
    # inside sync_user_hooks, adjacent to the rebuild that justifies it -- see
    # the comment there for why the distance is the whole problem (issue #2567).

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

    # Sync hook registration (manifest-driven, both scopes). Load once so
    # both generators see the same declarations from the same read.
    try:
        hook_manifest = load_hook_manifest(project_dir / ".claude" / "hooks" / "manifest.toml")
    except HookManifestError as e:
        result.actions.append(LinkAction("", "hook manifest", "error", str(e)))
        result.errors += 1
        hook_manifest = None

    if hook_manifest is not None:
        project_hook_result = sync_project_hooks(project_dir, hook_manifest)
        result.actions.extend(project_hook_result.actions)
        result.created += project_hook_result.created
        result.skipped += project_hook_result.skipped
        result.removed += project_hook_result.removed
        result.errors += project_hook_result.errors

        hook_result = sync_user_hooks(project_dir, hook_manifest)
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
        "hooks": project_dir / ".claude" / "hooks",
    }
    # A renamed hook's old name no longer exists in the project, so the inode
    # guard below finds no live source and clears the removal. Under the legacy
    # symlink layout the target it would then unlink is the repo's own file
    # (issue #2567), so this whole kind is skipped while the alias stands. This
    # runs before sync_user_hooks migrates the alias away, so the guard is live
    # on the production path and not only for standalone callers.
    hooks_aliased = user_hooks_root_is_repo_aliased(project_dir, user_claude)
    # Report the skip only when something was actually withheld, so the action
    # log never claims a prune was declined on a machine with nothing to prune.
    if hooks_aliased is not None and any(
        (user_claude / kind / old_name).exists()
        for kind, old_name in RENAMED_REMOVALS
        if kind == "hooks"
    ):
        result.actions.append(
            LinkAction(
                "",
                "~/.claude/hooks",
                "skipped",
                f"aliased to the repo tree ({hooks_aliased}); no separate user tree to prune",
            )
        )
        result.skipped += 1

    for kind, old_name in RENAMED_REMOVALS:
        if kind == "hooks" and hooks_aliased is not None:
            continue
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


# Trailing marker embedded in every user-scope (global) generated command
# string, e.g. "... || true # hook:validate_sdlc_on_stop_fork". This is the
# add/update/remove identity key for the user-scope generator — stable across
# script renames/timeout/matcher edits, unlike the volatile command string
# itself. A `#` comment is inert to the shell that executes the command.
_MANIFEST_ID_MARKER_RE = re.compile(r"#\s*hook:(\S+)\s*$")


def _build_hook_command(
    decl: HookDeclaration, script_base: str, *, interpreter: str, embed_marker: bool
) -> str:
    """Build the shell command string for one hook declaration.

    ``script_base`` is the already-formatted leading path expression (e.g.
    ``'"$CLAUDE_PROJECT_DIR"/.claude/hooks'`` for project scope, or the
    concrete ``~/.claude/hooks`` directory for user/global scope) — the
    declaration's ``script`` field (which may include a subdirectory, e.g.
    ``"validators/foo.py"`` or ``"sdlc/foo.py"``) is appended to it.

    ``interpreter`` is the leading interpreter token, supplied by the caller
    because the correct value differs by scope (issue #2503): project scope uses
    the ``hook_python`` shim (``PROJECT_HOOK_INTERPRETER``); global scope uses a
    resolved absolute system ``python3``. It is deliberately NOT a
    ``HookDeclaration`` field — the interpreter is a property of *where* a hook
    is deployed, not of the individual hook.
    """
    parts = [f"{interpreter} {script_base}/{decl.script}"]
    if decl.args:
        parts.append(" ".join(shlex.quote(a) for a in decl.args))
    command = " ".join(parts)
    if not decl.blocking:
        command += " || true"
    if embed_marker:
        command += f" # hook:{decl.manifest_id}"
    return command


def _extract_manifest_id(command: str) -> str | None:
    """Pull the trailing ``# hook:<id>`` marker out of a generated command string."""
    match = _MANIFEST_ID_MARKER_RE.search(command)
    return match.group(1) if match else None


def generate_project_hooks(manifest: list[HookDeclaration]) -> dict:
    """Project the manifest's ``scope == "project"`` entries into a hooks dict.

    Groups declarations by ``(event, matcher)`` in first-appearance manifest
    order and returns the ``event -> [{matcher, hooks: [...]}]`` shape used by
    ``.claude/settings.json``. Declaration order is load-bearing (Risk 4):
    this must reproduce the current hand-maintained JSON byte-for-byte when
    the manifest is unchanged, so entries are never resorted.
    """
    hooks: dict[str, list[dict]] = {}
    block_by_key: dict[tuple[str, str], dict] = {}

    for decl in manifest:
        if decl.scope != "project":
            continue

        command = _build_hook_command(
            decl,
            '"$CLAUDE_PROJECT_DIR"/.claude/hooks',
            interpreter=PROJECT_HOOK_INTERPRETER,
            embed_marker=False,
        )
        hook_entry = {"type": "command", "command": command, "timeout": decl.timeout}

        key = (decl.event, decl.matcher)
        block = block_by_key.get(key)
        if block is None:
            block = {"matcher": decl.matcher, "hooks": []}
            block_by_key[key] = block
            hooks.setdefault(decl.event, []).append(block)
        block["hooks"].append(hook_entry)

    return hooks


def sync_project_hooks(
    project_dir: Path, manifest: list[HookDeclaration] | None = None
) -> HardlinkSyncResult:
    """Rewrite the repo's ``.claude/settings.json`` ``hooks`` block from the manifest.

    Project scope is a single file this repo owns outright, so — unlike the
    user-scope merge below — this is a full regeneration each run rather than
    an incremental add/update/remove: the ``hooks`` key is replaced wholesale,
    all other top-level keys (e.g. ``permissions``) are preserved untouched.
    """
    result = HardlinkSyncResult()
    settings_path = project_dir / ".claude" / "settings.json"
    rel_path = str(settings_path)

    if manifest is None:
        try:
            manifest = load_hook_manifest(project_dir / ".claude" / "hooks" / "manifest.toml")
        except HookManifestError as e:
            result.actions.append(LinkAction("", rel_path, "error", str(e)))
            result.errors += 1
            return result

    try:
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    except (json.JSONDecodeError, OSError) as e:
        result.actions.append(LinkAction("", rel_path, "error", f"Failed to read settings: {e}"))
        result.errors += 1
        return result

    new_hooks = generate_project_hooks(manifest)

    if settings.get("hooks") == new_hooks:
        result.actions.append(LinkAction("", rel_path, "exists"))
        result.skipped += 1
        return result

    settings["hooks"] = new_hooks
    try:
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        result.actions.append(LinkAction("", rel_path, "created", "regenerated hooks block"))
        result.created += 1
    except OSError as e:
        result.actions.append(LinkAction("", rel_path, "error", f"Failed to write settings: {e}"))
        result.errors += 1

    return result


def sync_user_hooks(
    project_dir: Path, manifest: list[HookDeclaration] | None = None
) -> HardlinkSyncResult:
    """Hardlink global-scope hook scripts to ~/.claude/hooks/ and merge into settings.json.

    Only ``scope == "global"`` manifest entries (the SDLC-fork scripts that
    run inside foreign repos) are synced to user level. Other hooks
    (validators, calendar, etc.) remain project-specific.
    """
    result = HardlinkSyncResult()
    user_claude = Path.home() / ".claude"

    if manifest is None:
        try:
            manifest = load_hook_manifest(project_dir / ".claude" / "hooks" / "manifest.toml")
        except HookManifestError as e:
            result.actions.append(LinkAction("", "hook manifest", "error", str(e)))
            result.errors += 1
            return result

    global_decls = [d for d in manifest if d.scope == "global"]
    hooks_root = user_claude / "hooks"

    # Resolve the absolute system interpreter for global-scope commands, per
    # machine, at generation time (issue #2503). Emitting a command whose
    # interpreter cannot run is exactly the failure mode this fix exists to end,
    # so a total probe failure records an error and returns rather than writing a
    # dead command. Only required when there are global hooks to (re)register; an
    # empty global scope still needs the merge below to run its removal pass.
    interpreter = resolve_global_interpreter()
    if interpreter is None and global_decls:
        result.actions.append(
            LinkAction(
                "",
                str(user_claude / "settings.json"),
                "error",
                "No working system python3 for global hooks "
                f"(probed {', '.join(GLOBAL_INTERPRETER_CANDIDATES)})",
            )
        )
        result.errors += 1
        return result

    # Hardlink each unique declared script (scripts can repeat if reused
    # across event/matcher declarations, though none do today). Note: even
    # when global_decls is empty (every global hook removed from the
    # manifest), we still fall through to _merge_hook_settings below so the
    # removal pass can sweep any now-undeclared marked entries.
    # ~/.claude/hooks aliasing a checkout is handled here, at the last possible
    # moment before the rebuild, and nowhere earlier (issue #2567).
    #
    # The distance between the unlink and the rebuild IS the hazard. Everything
    # that can fail in between -- a malformed manifest, an unresolvable
    # interpreter, an interrupt -- leaves ~/.claude/hooks absent while
    # ~/.claude/settings.json still registers blocking global hooks against it,
    # and /usr/bin/python3 on a missing script exits 2, which is the PreToolUse
    # deny code. That denies every Bash call in every repo, including the
    # /update that would repair it. So both fallible steps above (manifest load,
    # interpreter probe) return before this point, and the deployment loop
    # follows immediately below.
    #
    # Migrating at all is safe only because deployment is directory-granular
    # (issue #2561, landed): a real directory holding just the declared scripts
    # and no sdlc/sdlc_context.py kills every global hook with
    # ModuleNotFoundError, converting a working machine into a broken one.
    aliased = user_hooks_root_is_repo_aliased(project_dir, user_claude)
    if aliased is not None:
        if hooks_root.is_symlink():
            # unlink() removes the link, never its target. Fall through to the
            # rebuild on the very next statement.
            hooks_root.unlink()
            result.actions.append(
                LinkAction(
                    "",
                    str(hooks_root),
                    "removed",
                    f"migrated from dir-symlink to hardlinks (was {aliased})",
                )
            )
            result.removed += 1
        else:
            # A parent directory carries the alias, so there is no link here to
            # remove and unlinking a real directory would raise. Decline to
            # write. Registration below is still correct: the scripts really
            # are reachable at the registered paths through the alias.
            result.actions.append(
                LinkAction(
                    "",
                    str(hooks_root),
                    "skipped",
                    f"a parent aliases the repo tree ({aliased}); scripts are already in place",
                )
            )
            result.skipped += 1
            _merge_hook_settings(
                user_claude / "settings.json", hooks_root, global_decls, interpreter, result
            )
            return result

    seen_scripts: set[str] = set()
    helper_dirs: set[PurePosixPath] = set()
    for decl in global_decls:
        if decl.script in seen_scripts:
            continue
        seen_scripts.add(decl.script)

        # A declaration at the hooks root deploys ONLY its own file: globbing
        # the root would sweep every project-scope script into the user tree.
        parent = PurePosixPath(decl.script).parent
        if str(parent) != ".":
            helper_dirs.add(parent)

        src_file = project_dir / ".claude" / "hooks" / decl.script
        dst_file = hooks_root / decl.script
        if not src_file.is_file():
            result.actions.append(
                LinkAction(str(src_file), str(dst_file), "error", f"Source missing: {src_file}")
            )
            result.errors += 1
            continue

        _ensure_hardlink(src_file, dst_file, dst_file.parent, result)

    # The DIRECTORY, not the declaration, is the deployment unit (issue #2561).
    # Global-scope scripts import shared sibling modules that register no hook
    # and therefore can never appear in the manifest. PR #2453 narrowed this to
    # per-declaration deployment and every global hook died with
    # ModuleNotFoundError in every foreign repo; PR #195's whole-directory glob
    # is the behavior restored here. Do NOT re-narrow this to the declared
    # files. The directory set is DERIVED from the declarations above, so a new
    # helper -- or a new global hook in a new directory -- is covered with no
    # manifest change. Deployment is additive: nothing under ~/.claude/hooks/ is
    # removed here, and the alias guard above already declined the whole write
    # when there is no separate user tree (#2567). Registration stays
    # declaration-granular below, so helpers are deployed but never registered.
    for rel_dir in sorted(helper_dirs):
        src_dir = project_dir / ".claude" / "hooks" / rel_dir
        for src_file in sorted(src_dir.glob("*.py")):
            if str(rel_dir / src_file.name) in seen_scripts:
                continue  # already hardlinked by the declaration loop
            dst_file = hooks_root / rel_dir / src_file.name
            _ensure_hardlink(src_file, dst_file, dst_file.parent, result)

    # Merge hook entries into ~/.claude/settings.json
    _merge_hook_settings(
        user_claude / "settings.json", hooks_root, global_decls, interpreter, result
    )

    return result


def _merge_hook_settings(
    settings_path: Path,
    hooks_root: Path,
    global_decls: list[HookDeclaration],
    interpreter: str | None,
    result: HardlinkSyncResult,
) -> None:
    """Merge global-scope hook entries into ~/.claude/settings.json.

    Add/update/remove is keyed on the stable ``manifest_id`` embedded as a
    trailing ``# hook:<id>`` comment in each generated command string — NOT
    the volatile command string itself (unlike the legacy dedupe). A removal
    pass deletes any existing manifest-marked entry whose id is no longer
    declared. Entries with no marker at all (hand-added or pre-migration
    legacy entries) are left completely untouched — this function never
    clobbers non-manifest user hooks.
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
    removed = 0

    # Index every currently-registered entry that carries a manifest_id marker.
    existing_by_id: dict[str, tuple[dict, dict]] = {}
    for event_hooks in hooks.values():
        for block in event_hooks:
            for hook_entry in block.get("hooks", []):
                mid = _extract_manifest_id(hook_entry.get("command", ""))
                if mid:
                    existing_by_id[mid] = (block, hook_entry)

    # Removal pass: a manifest-marked entry whose id is no longer declared.
    declared_ids = {d.manifest_id for d in global_decls}
    for mid, (block, hook_entry) in list(existing_by_id.items()):
        if mid in declared_ids:
            continue
        block["hooks"].remove(hook_entry)
        removed += 1
        del existing_by_id[mid]

    # Prune blocks/events emptied by the removal pass.
    for event in list(hooks.keys()):
        hooks[event] = [b for b in hooks[event] if b.get("hooks")]
        if not hooks[event]:
            del hooks[event]

    # Add/update pass, in manifest declaration order. ``interpreter`` may be
    # None on entry to this function — sync_user_hooks only returns early on a
    # total probe failure when global_decls is non-empty, and deliberately calls
    # through with interpreter=None when global_decls is empty so the removal and
    # pruning passes above still run. The *loop body* is what is unreachable with
    # a None interpreter: it only executes when global_decls is non-empty, which
    # is exactly the case sync_user_hooks guarantees a resolved interpreter for.
    for decl in global_decls:
        assert interpreter is not None
        command = _build_hook_command(
            decl, str(hooks_root), interpreter=interpreter, embed_marker=True
        )

        existing = existing_by_id.get(decl.manifest_id)
        if existing is not None:
            block, hook_entry = existing
            changed = False
            if hook_entry.get("command") != command:
                hook_entry["command"] = command
                changed = True
            if hook_entry.get("timeout") != decl.timeout:
                hook_entry["timeout"] = decl.timeout
                changed = True
            if block.get("matcher", "") != decl.matcher:
                block["matcher"] = decl.matcher
                changed = True
            if changed:
                updated += 1
            continue

        event_hooks = hooks.setdefault(decl.event, [])
        target_block = next((b for b in event_hooks if b.get("matcher", "") == decl.matcher), None)
        if target_block is None:
            target_block = {"matcher": decl.matcher, "hooks": []}
            event_hooks.append(target_block)
        target_block["hooks"].append(
            {"type": "command", "command": command, "timeout": decl.timeout}
        )
        added += 1

    if added > 0 or updated > 0 or removed > 0:
        try:
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            parts = []
            if added:
                parts.append(f"added {added}")
            if updated:
                parts.append(f"updated {updated}")
            if removed:
                parts.append(f"removed {removed}")
            result.actions.append(
                LinkAction("", rel_path, "created", f"Merged hooks: {', '.join(parts)}")
            )
            result.created += added
            result.removed += removed
        except OSError as e:
            result.actions.append(
                LinkAction("", rel_path, "error", f"Failed to write settings: {e}")
            )
            result.errors += 1
    else:
        result.actions.append(LinkAction("", rel_path, "exists"))
        result.skipped += 1
