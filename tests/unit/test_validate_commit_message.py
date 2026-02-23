"""Tests for validate_commit_message.py PreToolUse hook.

Tests the hook that blocks git commit commands with co-author trailers
or empty commit messages.
"""

import json
import subprocess
import sys
from pathlib import Path

VALIDATOR_PATH = (
    Path(__file__).parent.parent.parent
    / ".claude"
    / "hooks"
    / "validators"
    / "validate_commit_message.py"
)


def run_hook(hook_input: dict) -> tuple[int, dict | None, str]:
    """Run the validator with the given input dict.

    Returns: (exit_code, stdout_json_or_none, stderr)
    """
    result = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=10,
    )
    stdout_json = None
    if result.stdout.strip():
        try:
            stdout_json = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            pass
    return result.returncode, stdout_json, result.stderr


class TestFastPath:
    """Non-commit commands must pass through immediately."""

    def test_non_bash_tool_passes(self):
        inp = {"tool_name": "Read", "tool_input": {"file_path": "/tmp/foo.py"}}
        code, out, _ = run_hook(inp)
        assert code == 0
        # Should not block
        if out:
            assert out.get("decision") != "block"

    def test_git_status_passes(self):
        inp = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_git_log_passes(self):
        inp = {"tool_name": "Bash", "tool_input": {"command": "git log --oneline"}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_ls_command_passes(self):
        inp = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_empty_stdin_passes(self):
        """Missing or empty stdin should not crash."""
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


class TestCoAuthorBlocking:
    """Co-author trailers must be blocked regardless of case."""

    def test_blocks_co_authored_by_exact(self):
        cmd = (
            'git commit -m "fix: thing\nCo-Authored-By: Claude <noreply@anthropic.com>"'
        )
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        # Should block: either non-zero exit or decision=block in stdout
        blocked = code != 0 or (out and out.get("decision") == "block")
        assert blocked, f"Expected block, got code={code}, out={out}"

    def test_blocks_co_authored_by_lowercase(self):
        cmd = (
            'git commit -m "fix: thing\nco-authored-by: Claude <noreply@anthropic.com>"'
        )
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        blocked = code != 0 or (out and out.get("decision") == "block")
        assert blocked, f"Expected block, got code={code}, out={out}"

    def test_blocks_co_authored_by_mixed_case(self):
        cmd = (
            'git commit -m "fix: thing\nCO-AUTHORED-BY: Claude <noreply@anthropic.com>"'
        )
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        blocked = code != 0 or (out and out.get("decision") == "block")
        assert blocked, f"Expected block, got code={code}, out={out}"

    def test_blocks_co_authored_by_in_heredoc(self):
        trailer = "Co-Authored-By: Claude Sonnet <noreply@anthropic.com>"
        cmd = (
            f"git commit -m \"$(cat <<'EOF'\\nAdd feature\\n\\n{trailer}\\nEOF\\n)\"\\n"
        )
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        blocked = code != 0 or (out and out.get("decision") == "block")
        assert blocked, f"Expected block, got code={code}, out={out}"

    def test_block_reason_mentions_co_author(self):
        """Block reason should mention what was blocked."""
        cmd = (
            'git commit -m "fix: thing\nCo-Authored-By: Claude <noreply@anthropic.com>"'
        )
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        if out and out.get("decision") == "block":
            reason = out.get("reason", "").lower()
            assert "co-author" in reason or "co_author" in reason or "trailer" in reason


class TestEmptyMessageBlocking:
    """Empty commit messages must be blocked."""

    def test_blocks_empty_message(self):
        cmd = 'git commit -m ""'
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        blocked = code != 0 or (out and out.get("decision") == "block")
        assert blocked, f"Expected block, got code={code}, out={out}"

    def test_blocks_whitespace_only_message(self):
        cmd = "git commit -m '   '"
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        blocked = code != 0 or (out and out.get("decision") == "block")
        assert blocked, f"Expected block, got code={code}, out={out}"


class TestValidCommitsAllowed:
    """Valid commit messages must pass through."""

    def test_allows_normal_commit(self):
        cmd = 'git commit -m "fix: resolve import error in bridge module"'
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_allows_multiline_without_co_author(self):
        body = "Prevents co-author trailers from being added to commits."
        cmd = f'git commit -m "feat: add commit validator\\n\\n{body}"'
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_allows_commit_with_author_flag(self):
        """--author flag is different from co-author trailer, should be allowed."""
        cmd = 'git commit --author="Valor Engels <valor@example.com>" -m "fix: thing"'
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"

    def test_allows_git_commit_amend(self):
        cmd = "git commit --amend --no-edit"
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        code, out, _ = run_hook(inp)
        assert code == 0
        if out:
            assert out.get("decision") != "block"
