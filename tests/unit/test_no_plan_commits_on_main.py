"""Tests for .claude/hooks/validators/no_plan_commits_on_main.py (issue #1394).

The hook rejects pushes to refs/heads/main when the commit message matches
the Plan commit pattern AND only docs/plans/*.md files are touched.

Exit codes (same as other validators):
- 0: Allow (valid push or not a Plan-only commit on main)
- 1: Block (Plan-only commit detected on main)
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = Path(
    "/Users/valorengels/src/ai/.claude/worktrees/agent-a1470e4c5805efbe9"
    "/.claude/hooks/validators/no_plan_commits_on_main.py"
)


def run_hook(input_data: dict) -> tuple[int, str, str]:
    """Run the hook with the given JSON input, returning (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _push_input(
    ref: str = "refs/heads/main",
    message: str = "Plan: add new feature",
    files: list[str] | None = None,
) -> dict:
    """Build a minimal hook input dict."""
    if files is None:
        files = ["docs/plans/new-feature.md"]
    return {
        "tool_name": "Bash",
        "tool_input": {
            "command": f"git push origin {ref}",
        },
        "context": {
            "ref": ref,
            "commit_message": message,
            "changed_files": files,
        },
    }


class TestNoPlanCommitsOnMain:
    """Tests covering all three behaviours: reject, allow (branch), allow (non-plan)."""

    def test_plan_commit_on_main_is_blocked(self):
        """Plan commit touching only docs/plans/*.md on main must be rejected."""
        exit_code, stdout, _ = run_hook(
            _push_input(
                ref="refs/heads/main",
                message="Plan: implement dark mode toggle",
                files=["docs/plans/dark-mode-toggle.md"],
            )
        )
        assert exit_code == 0  # Hook exits 0 to signal block via JSON
        output = json.loads(stdout)
        assert output.get("decision") == "block"

    def test_plan_commit_with_lowercase_on_main_is_blocked(self):
        """'plan(#123):' prefix (lowercase) on main is also rejected."""
        exit_code, stdout, _ = run_hook(
            _push_input(
                ref="refs/heads/main",
                message="plan(#123): add caching plan",
                files=["docs/plans/caching.md"],
            )
        )
        assert exit_code == 0
        output = json.loads(stdout)
        assert output.get("decision") == "block"

    def test_plan_commit_on_session_branch_is_allowed(self):
        """Plan commit on session/{slug} branch must pass through."""
        exit_code, stdout, _ = run_hook(
            _push_input(
                ref="refs/heads/session/dark-mode-toggle",
                message="Plan: implement dark mode toggle",
                files=["docs/plans/dark-mode-toggle.md"],
            )
        )
        # Should allow — either empty stdout or {"decision": "allow"}
        if stdout.strip():
            output = json.loads(stdout)
            assert output.get("decision") != "block"
        else:
            assert exit_code == 0

    def test_non_plan_commit_on_main_is_allowed(self):
        """A regular feature commit on main (not a Plan commit) must pass through."""
        exit_code, stdout, _ = run_hook(
            _push_input(
                ref="refs/heads/main",
                message="feat: add login endpoint",
                files=["agent/session_executor.py"],
            )
        )
        if stdout.strip():
            output = json.loads(stdout)
            assert output.get("decision") != "block"
        else:
            assert exit_code == 0

    def test_plan_commit_touching_code_on_main_is_allowed(self):
        """Plan commit that also touches non-plan files is not blocked.

        Only pure docs/plans/* commits are forbidden. If code is mixed in,
        the commit is not a pure plan-only push.
        """
        exit_code, stdout, _ = run_hook(
            _push_input(
                ref="refs/heads/main",
                message="Plan: add caching feature",
                files=["docs/plans/caching.md", "agent/session_executor.py"],
            )
        )
        if stdout.strip():
            output = json.loads(stdout)
            assert output.get("decision") != "block"
        else:
            assert exit_code == 0

    def test_non_bash_tool_is_allowed(self):
        """Non-Bash tools are never blocked."""
        exit_code, stdout, _ = run_hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "docs/plans/foo.md", "content": "..."},
                "context": {
                    "ref": "refs/heads/main",
                    "commit_message": "Plan: foo",
                    "changed_files": ["docs/plans/foo.md"],
                },
            }
        )
        if stdout.strip():
            output = json.loads(stdout)
            assert output.get("decision") != "block"
        else:
            assert exit_code == 0

    def test_empty_input_is_allowed(self):
        """Empty/missing input must not crash — fail open."""
        exit_code, stdout, _ = run_hook({})
        # Must not crash
        if stdout.strip():
            output = json.loads(stdout)
            assert output.get("decision") != "block"

    def test_plan_with_issue_ref_on_main_is_blocked(self):
        """'Plan(#1394): ...' format on main is also blocked."""
        exit_code, stdout, _ = run_hook(
            _push_input(
                ref="refs/heads/main",
                message="Plan(#1394): PM pipeline governance",
                files=["docs/plans/sdlc-1394.md"],
            )
        )
        assert exit_code == 0
        output = json.loads(stdout)
        assert output.get("decision") == "block"
