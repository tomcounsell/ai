#!/usr/bin/env python3
"""
Re-establish the hardlink from .claude/skills-global/ into ~/.claude/ after an edit.

Why this exists
---------------
Global skills ship to every machine by being *hardlinked* into ``~/.claude/``
(``scripts/update/hardlinks.py``). The link is the propagation mechanism: one
inode, two paths, so editing the repo copy IS editing the live skill.

The Edit and Write tools do not write in place. They write a replacement file
and rename it over the target, which allocates a NEW inode and drops the link
count to 1. The repo copy then holds the edit and the ``~/.claude/`` copy silently
keeps serving the pre-edit text, at the old inode, forever — until someone runs
``/update`` again. Nothing fails, nothing warns; the skill change simply does not
take effect on the machine that authored it.

This was observed as a merged skills-global change still running on pre-merge
text. Rather than detect and report, this hook repairs: after any write under a
synced source directory, it relinks the destination to the current inode.

Registered as a PostToolUse hook for the Write and Edit matchers. Fires on every
write and no-ops cheaply for paths outside the synced directories.
"""

import json
import os
import select
import sys
from pathlib import Path

# Source dirs under the repo's .claude/ that sync into ~/.claude/, mapped to
# their destination subdirectory. Mirrors sync_claude_dirs() in
# scripts/update/hardlinks.py — keep the two in step when adding a synced dir.
SYNCED_DIRS = {
    "skills-global": "skills",
    "commands": "commands",
    "agents": "agents",
}


def _relink(src: Path, dst: Path) -> str:
    """Point dst at src's inode. Returns a short status for logging."""
    if not src.is_file():
        return "src-missing"

    if dst.exists() and src.samefile(dst):
        return "already-linked"

    dst.parent.mkdir(parents=True, exist_ok=True)

    # Link to a temp name then rename, so a failure never leaves the
    # destination absent — a missing skill file is worse than a stale one.
    tmp = dst.with_suffix(dst.suffix + ".relink.tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        os.link(src, tmp)
        os.replace(tmp, dst)
        return "relinked"
    except OSError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return f"failed: {exc}"


def main() -> int:
    if not select.select([sys.stdin], [], [], 0.1)[0]:
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0

    raw_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not raw_path:
        return 0

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
    claude_src = project_dir / ".claude"

    try:
        src = Path(raw_path).resolve()
        rel = src.relative_to(claude_src)
    except (ValueError, OSError):
        return 0  # not under this repo's .claude/ — nothing to sync

    if not rel.parts:
        return 0
    dest_subdir = SYNCED_DIRS.get(rel.parts[0])
    if dest_subdir is None:
        return 0

    dst = Path.home() / ".claude" / dest_subdir / Path(*rel.parts[1:])
    status = _relink(src, dst)

    # Only speak up when something actually happened or went wrong. A silent
    # repair on every edit would be noise; a silent FAILURE would recreate the
    # exact invisible-staleness bug this hook exists to close.
    if status == "relinked":
        print(f"relink-global-skills: re-established hardlink for {rel}", file=sys.stderr)
    elif status.startswith("failed"):
        print(
            f"relink-global-skills: COULD NOT relink {rel} ({status}). "
            f"The copy at {dst} is now STALE — run /update before relying on it.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
