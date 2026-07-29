"""Tests for _merge_hook_settings in scripts/update/hardlinks.py.

The SDLC hooks are registered in the *user-scope* ~/.claude/settings.json and
therefore fire inside every repo on the machine, not just this one. Claude Code
executes hook commands via /bin/sh, which is non-interactive and never sources
~/.zshenv -- so a bare `python` resolves only when a virtualenv happens to be on
PATH. On a machine whose only interpreter is `python3`, a bare `python` command
dies with `command not found` in every foreign repo, silently disabling the
guardrails (including the block-code-commits-to-main check).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from update.hardlinks import (  # noqa: E402
    _SDLC_HOOK_DEFS,
    HardlinkSyncResult,
    _merge_hook_settings,
)

HOOKS_DIR = Path("/Users/test/.claude/hooks/sdlc")


def _all_commands(settings: dict) -> list[str]:
    """Flatten every hook command across every event in a settings dict."""
    return [
        hook.get("command", "")
        for event_blocks in settings.get("hooks", {}).values()
        for block in event_blocks
        for hook in block.get("hooks", [])
    ]


def test_hook_commands_use_python3(tmp_path: Path) -> None:
    """Registered commands must invoke python3, never a bare python."""
    settings_path = tmp_path / "settings.json"
    _merge_hook_settings(settings_path, HOOKS_DIR, HardlinkSyncResult())

    settings = json.loads(settings_path.read_text())
    commands = _all_commands(settings)

    assert commands, "expected SDLC hook commands to be registered"
    for command in commands:
        assert command.startswith("python3 "), f"bare interpreter in: {command}"


def test_legacy_bare_python_entry_is_rewritten_not_duplicated(tmp_path: Path) -> None:
    """A deployed `python ...` entry is upgraded in place, not shadowed by a twin.

    Dedup is by exact command string, so switching the interpreter would
    otherwise append a second block and leave the broken original firing
    alongside it -- every hook running twice, once failing.
    """
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python {HOOKS_DIR / 'validate_commit_message.py'}",
                                    "timeout": 10,
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )

    _merge_hook_settings(settings_path, HOOKS_DIR, HardlinkSyncResult())
    settings = json.loads(settings_path.read_text())

    commands = _all_commands(settings)
    legacy = f"python {HOOKS_DIR / 'validate_commit_message.py'}"
    assert legacy not in commands, "stale bare-python entry survived the merge"
    assert commands.count(f"python3 {HOOKS_DIR / 'validate_commit_message.py'}") == 1


def test_non_sdlc_user_hooks_survive(tmp_path: Path) -> None:
    """Merging never clobbers hooks the user registered themselves."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "bash /some/user/hook.sh"}],
                        }
                    ]
                }
            }
        )
    )

    _merge_hook_settings(settings_path, HOOKS_DIR, HardlinkSyncResult())
    settings = json.loads(settings_path.read_text())

    assert "bash /some/user/hook.sh" in _all_commands(settings)


def test_hook_scripts_tolerate_system_python() -> None:
    """Every SDLC hook script must parse and evaluate under macOS system Python.

    `python3` on PATH is whichever interpreter comes first -- under a stripped
    environment (launchd, cron, a minimal PATH) that is /usr/bin/python3, which
    is 3.9 on macOS. These scripts annotate with PEP 604 unions (`str | None`),
    which 3.9 evaluates at runtime and rejects with a TypeError at import time.
    The __future__ import makes annotations lazy, so they parse as strings.
    """
    scripts = sorted((REPO_ROOT / ".claude" / "hooks" / "sdlc").glob("*.py"))
    assert scripts, "expected SDLC hook scripts to exist"

    for script in scripts:
        source = script.read_text()
        assert "from __future__ import annotations" in source, (
            f"{script.name} lacks the __future__ import and will crash on Python 3.9"
        )


def test_merge_is_idempotent(tmp_path: Path) -> None:
    """Running the sync twice leaves exactly one entry per hook definition."""
    settings_path = tmp_path / "settings.json"
    _merge_hook_settings(settings_path, HOOKS_DIR, HardlinkSyncResult())
    _merge_hook_settings(settings_path, HOOKS_DIR, HardlinkSyncResult())

    settings = json.loads(settings_path.read_text())
    commands = _all_commands(settings)

    for _event, _matcher, script_name, _timeout in _SDLC_HOOK_DEFS:
        expected = f"python3 {HOOKS_DIR / script_name}"
        assert commands.count(expected) == 1, f"duplicate registration for {script_name}"
