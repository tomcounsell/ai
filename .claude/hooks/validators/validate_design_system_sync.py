"""PreToolUse hook: block commits that leave design-system artifacts out of sync.

Fires ONLY on ``git add``/``git commit`` Bash commands that reference a
design-system filename. Re-runs ``python -m tools.design_system_sync --check``
and emits ``{"decision": "block", "reason": <diff>}`` on drift. Fails open
on internal error so a broken validator cannot block all commits.

Reads the hook payload from stdin per the existing validator pattern
(see ``validate_no_raw_redis_delete.py``).

Emergency bypass: set ``DESIGN_SYSTEM_HOOK_DISABLED=1`` on the command
invocation. Use only for genuine hotfixes - drift will be caught by
``--check`` in CI and on the next normal commit. Bypasses are logged as
``result: "bypassed"`` in the JSONL log so audits remain possible.

Every invocation appends one JSON line to
``logs/validate_design_system_sync.jsonl``:
``{"ts", "tool_name", "matched", "result", "duration_ms", "reason", "error"}``.
This is the observability surface that closes the fail-open gap.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Path-anchored regex: ``(?:^|/)`` prefix on brand.css / source.css prevents
# false positives on ``my-brand.css`` / ``source.css.bak``. See Risk 6 in
# the plan. Any change here MUST be mirrored in Risk 6.
_COMMAND_REGEX = re.compile(
    r"git (add|commit).*(?:^|/)(design-system\.(pen|md)|brand\.css|source\.css)\b"
)

_LOG_PATH = Path("logs/validate_design_system_sync.jsonl")


def _log(entry: dict) -> None:
    """Append one JSON line. Wrapped so logging cannot break the hook."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _find_pen_path(command: str) -> Path | None:
    """Extract a design-system.pen path from the command, if present."""
    match = re.search(r"(\S*design-system\.pen)", command)
    if match:
        candidate = Path(match.group(1))
        if candidate.is_file():
            return candidate.resolve()
    for base in (Path.cwd(), *Path.cwd().parents):
        adjacent = base / "docs" / "designs" / "design-system.pen"
        if adjacent.is_file():
            return adjacent.resolve()
        adjacent = base / "tests" / "fixtures" / "design_system" / "design-system.pen"
        if adjacent.is_file():
            return adjacent.resolve()
    return None


def main() -> int:
    start = time.monotonic()
    tool_name = "?"
    matched = False

    if os.environ.get("DESIGN_SYSTEM_HOOK_DISABLED") == "1":
        _log(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "tool_name": tool_name,
                "matched": False,
                "result": "bypassed",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "reason": None,
                "error": None,
            }
        )
        return 0

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        hook_input = json.loads(raw)
    except Exception as exc:
        _log(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "tool_name": tool_name,
                "matched": False,
                "result": "error",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "reason": None,
                "error": f"stdin parse error: {exc}",
            }
        )
        return 0

    tool_name = hook_input.get("tool_name") or "?"
    if tool_name != "Bash":
        _log(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "tool_name": tool_name,
                "matched": False,
                "result": "ok",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "reason": None,
                "error": None,
            }
        )
        return 0

    command = hook_input.get("tool_input", {}).get("command", "") or ""
    if not _COMMAND_REGEX.search(command):
        _log(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "tool_name": tool_name,
                "matched": False,
                "result": "ok",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "reason": None,
                "error": None,
            }
        )
        return 0
    matched = True

    try:
        pen_path = _find_pen_path(command)
        if pen_path is None:
            _log(
                {
                    "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                    "tool_name": tool_name,
                    "matched": True,
                    "result": "ok",
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "reason": "no .pen path resolved; skipping check",
                    "error": None,
                }
            )
            return 0
        result_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.design_system_sync",
                "--check",
                "--pen",
                str(pen_path),
            ],
            capture_output=True,
            text=True,
            timeout=9,
        )
        if result_proc.returncode != 0:
            reason = (result_proc.stderr or result_proc.stdout).strip() or "drift detected"
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": (
                            "BLOCKED: design-system artifacts are out of sync with .pen.\n"
                            "Run `python -m tools.design_system_sync --generate` (or --all) "
                            "and re-stage the files.\n\n" + reason
                        ),
                    }
                )
            )
            _log(
                {
                    "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                    "tool_name": tool_name,
                    "matched": True,
                    "result": "block",
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "reason": reason[:2000],
                    "error": None,
                }
            )
            return 0
        _log(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "tool_name": tool_name,
                "matched": True,
                "result": "ok",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "reason": None,
                "error": None,
            }
        )
        return 0
    except Exception as exc:
        sys.stderr.write(
            f"[validate_design_system_sync] fail-open: {type(exc).__name__}: {exc}\n"
        )
        _log(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(),
                "tool_name": tool_name,
                "matched": matched,
                "result": "error",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "reason": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
