#!/usr/bin/env python3
"""In-process PreToolUse/Bash dispatcher.

Replaces 7 separate interpreter starts (one per validator, all matched on
`Bash` in `.claude/settings.json`) with a single process that reads the hook
JSON from stdin ONCE and calls each validator's pure predicate function
in-process, in the same order the validators are currently registered:

  1. validate_commit_message
  2. validate_no_inline_timeout
  3. validate_merge_guard
  4. validate_no_raw_redis_delete
  5. validate_no_uv_sync_in_worktree
  6. validate_no_destructive_git_in_worktree
  7. validate_no_destructive_git_in_shared_checkout
  8. validate_design_system_sync (out-of-process -- see below)

Outcome is first-block-wins: the first validator to return a block reason
short-circuits the rest and the dispatcher emits ONE
``{"decision": "block", "reason": <that reason>}`` to stdout, exit 0. If
every validator passes (or fails open), the dispatcher emits nothing (allow).

Fail posture per validator (spike-1 / Risk 2 of
docs/plans/hook-registration-manifest-dispatcher.md):

- All validators except ``validate_merge_guard`` are wrapped in their own
  ``try/except`` and FAIL OPEN on an unexpected exception: the error is
  logged via ``log_hook_error`` and the dispatcher moves on to the next
  validator. A crash in one validator must never skip the validators that
  come after it (Risk 2) and must never silently block everything.
- ``validate_merge_guard`` is fail-CLOSED: an exception raised while
  evaluating its predicate (including a bug in the predicate call itself,
  not just an exception the predicate's own internals already convert to a
  block reason) produces a BLOCK decision, never a silent allow. This
  preserves the pre-existing fail-closed contract for merge enforcement.

``validate_design_system_sync.py`` runs out-of-process (spike-1: it re-runs
``python -m tools.design_system_sync --check`` as its own subprocess anyway,
so folding it in-process gains nothing and the module already owns its own
JSONL observability log). It is invoked as a subprocess with the same hook
JSON on stdin; a ``{"decision": "block", ...}`` line on its stdout is treated
identically to an in-process predicate's block reason.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_VALIDATORS_DIR = Path(__file__).resolve().parent.parent / "validators"
_HOOKS_DIR = _VALIDATORS_DIR.parent

for _p in (str(_HOOKS_DIR), str(_VALIDATORS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from hook_utils.constants import log_hook_error  # noqa: E402

_DESIGN_SYSTEM_SCRIPT = _VALIDATORS_DIR / "validate_design_system_sync.py"

# Subprocess timeout for the out-of-process design-system validator. Mirrors
# its own internal `subprocess.run(..., timeout=9)` call plus headroom for
# interpreter startup -- provisional/tunable, not wired to TimeoutSettings
# (see plan spike-4A: the harness, not Python, owns hook timeouts).
_DESIGN_SYSTEM_SUBPROCESS_TIMEOUT_S = 15


def read_stdin() -> dict:
    """Read and parse JSON from stdin exactly once. Empty/whitespace/invalid
    input is treated as an empty dict -- never raises.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _extract_command(hook_input: dict) -> str:
    tool_input = hook_input.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command", "")
    return command if isinstance(command, str) else ""


def _extract_cwd(hook_input: dict) -> str:
    cwd = hook_input.get("cwd", "") or ""
    return cwd if isinstance(cwd, str) else ""


def _run_commit_message(command: str, _cwd: str) -> str | None:
    import validate_commit_message

    return validate_commit_message.find_violation(command)


def _run_no_inline_timeout(command: str, _cwd: str) -> str | None:
    import validate_no_inline_timeout

    return validate_no_inline_timeout.find_violation_for_command(command)


def _run_merge_guard(command: str, _cwd: str) -> str | None:
    import validate_merge_guard

    return validate_merge_guard.find_violation(command)


def _run_no_raw_redis_delete(command: str, _cwd: str) -> str | None:
    import validate_no_raw_redis_delete

    return validate_no_raw_redis_delete.find_violation(command)


def _run_no_uv_sync_in_worktree(command: str, cwd: str) -> str | None:
    import validate_no_uv_sync_in_worktree

    return validate_no_uv_sync_in_worktree.find_violation(command, cwd)


def _run_no_destructive_git_in_worktree(command: str, cwd: str) -> str | None:
    import validate_no_destructive_git_in_worktree

    return validate_no_destructive_git_in_worktree.find_violation_from_hook_input(command, cwd)


def _run_no_destructive_git_in_shared_checkout(command: str, cwd: str) -> str | None:
    import validate_no_destructive_git_in_shared_checkout

    return validate_no_destructive_git_in_shared_checkout.find_violation_from_hook_input(
        command, cwd
    )


def _run_design_system_sync(hook_input: dict) -> str | None:
    """Out-of-process invocation (spike-1). Returns the block reason if the
    subprocess emits a ``{"decision": "block", "reason": ...}`` line on
    stdout, else None. Any subprocess failure (missing interpreter, timeout,
    non-JSON output) fails open -- this validator is one of the fail-open six.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(_DESIGN_SYSTEM_SCRIPT)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            timeout=_DESIGN_SYSTEM_SUBPROCESS_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decision = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(decision, dict) and decision.get("decision") == "block":
            reason = decision.get("reason")
            if isinstance(reason, str) and reason:
                return reason
    return None


# Manifest declaration order == the order these were registered in
# .claude/settings.json's Bash matcher block prior to this dispatcher.
# Each entry: (name for logging, callable(command, cwd) -> reason|None,
# fail_closed).
_VALIDATORS: list[tuple[str, object, bool]] = [
    ("validate_commit_message", _run_commit_message, False),
    ("validate_no_inline_timeout", _run_no_inline_timeout, False),
    ("validate_merge_guard", _run_merge_guard, True),
    ("validate_no_raw_redis_delete", _run_no_raw_redis_delete, False),
    ("validate_no_uv_sync_in_worktree", _run_no_uv_sync_in_worktree, False),
    ("validate_no_destructive_git_in_worktree", _run_no_destructive_git_in_worktree, False),
    (
        "validate_no_destructive_git_in_shared_checkout",
        _run_no_destructive_git_in_shared_checkout,
        False,
    ),
]


def dispatch(hook_input: dict) -> str | None:
    """Run every validator predicate in declaration order; return the first
    block reason encountered, or None if all pass/fail-open.

    Per-validator try/except (Risk 2): a raising validator never skips the
    validators that follow it. ``validate_merge_guard`` is fail-closed -- an
    exception there produces a synthesized block reason instead of being
    swallowed.
    """
    if hook_input.get("tool_name") != "Bash":
        return None

    command = _extract_command(hook_input)
    cwd = _extract_cwd(hook_input)
    if not command:
        return None

    for name, fn, fail_closed in _VALIDATORS:
        try:
            reason = fn(command, cwd)
        except Exception as exc:
            log_hook_error(
                f"dispatch.pre_tool_use_bash.{name}",
                f"{exc.__class__.__name__}: {exc}",
            )
            if fail_closed:
                return (
                    "Merge blocked (fail-closed): the merge-guard validator raised"
                    f" an unexpected error inside the dispatcher"
                    f" ({exc.__class__.__name__}: {exc})."
                    " Run /do-merge {pr_number} to drive the gate through the"
                    " normal path."
                )
            # Fail open: log and continue to the next validator.
            continue
        if reason:
            return reason

    try:
        reason = _run_design_system_sync(hook_input)
    except Exception as exc:
        log_hook_error(
            "dispatch.pre_tool_use_bash.validate_design_system_sync",
            f"{exc.__class__.__name__}: {exc}",
        )
        reason = None
    if reason:
        return reason

    return None


def main() -> None:
    hook_input = read_stdin()
    if not hook_input:
        return
    reason = dispatch(hook_input)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_hook_error("dispatch.pre_tool_use_bash", str(e))
