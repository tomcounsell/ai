"""PreToolUse hook: block Write/Edit of design-system generated artifacts.

Artifacts like ``design-system.md``, ``brand.css``, ``source.css``, and
the DTCG / Tailwind exports MUST flow through the generator. Direct edits
will be caught by ``validate_design_system_sync.py`` at commit time, but
this hook blocks them earlier (at the tool call) with a more actionable
message.

``.pen`` writes are allowed. The skill's existing Safety gate handles the
``name == design-system.pen`` constraint at agent-Python level.

Emergency bypass: ``DESIGN_SYSTEM_HOOK_DISABLED=1`` disables BOTH this
hook and ``validate_design_system_sync.py`` for one invocation. Use only
for genuine hotfixes.
"""

from __future__ import annotations

import json
import os
import re
import sys

_BLOCKED_REGEX = re.compile(
    r"(?:^|/)(design-system\.md|brand\.css|source\.css)$|\.dtcg\.json$|tailwind\.theme\.json$"
)


def main() -> int:
    if os.environ.get("DESIGN_SYSTEM_HOOK_DISABLED") == "1":
        return 0

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        hook_input = json.loads(raw)
    except Exception:
        return 0

    tool_name = hook_input.get("tool_name")
    if tool_name not in ("Write", "Edit"):
        return 0

    file_path = hook_input.get("tool_input", {}).get("file_path", "") or ""
    if not file_path:
        return 0
    if not _BLOCKED_REGEX.search(file_path):
        return 0

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    f"BLOCKED: {file_path} is a generated artifact.\n"
                    "Do not Write/Edit it directly - run "
                    "`python -m tools.design_system_sync --generate` "
                    "(or --all) to regenerate from the .pen ground truth.\n"
                    "Set DESIGN_SYSTEM_HOOK_DISABLED=1 for genuine hotfixes only."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
