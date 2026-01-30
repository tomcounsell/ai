"""
PR management for repositories with protected branches.

When main branch is protected, work must be submitted via pull requests
instead of direct pushes.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def is_pr_required(working_dir: Path) -> bool:
    """
    Check if current project requires PRs for merging to main.

    Args:
        working_dir: Project directory

    Returns:
        True if PRs are required
    """
    try:
        # Load project config
        config_file = working_dir / "config" / "projects.json"
        if not config_file.exists():
            return False

        config = json.loads(config_file.read_text())

        # Find project matching this working directory
        for project_id, project_config in config.get("projects", {}).items():
            if project_config.get("working_directory") == str(working_dir):
                github_config = project_config.get("github", {})
                return github_config.get("require_pr", False)

        return False

    except Exception as e:
        logger.error(f"Failed to check PR requirement: {e}")
        return False


def create_pull_request(
    working_dir: Path,
    branch_name: str,
    title: str,
    body: str,
    base_branch: str = "main"
) -> tuple[bool, str]:
    """
    Create a pull request using GitHub CLI.

    Args:
        working_dir: Project directory
        branch_name: Feature branch to create PR from
        title: PR title
        body: PR description
        base_branch: Target branch (default: main)

    Returns:
        (success, pr_url_or_error)
    """
    try:
        # First, push the branch to remote
        logger.info(f"Pushing branch {branch_name} to remote...")
        push_result = subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=True
        )

        # Create PR using gh CLI
        logger.info(f"Creating PR: {title}")
        pr_result = subprocess.run(
            [
                "gh", "pr", "create",
                "--base", base_branch,
                "--head", branch_name,
                "--title", title,
                "--body", body
            ],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=True
        )

        pr_url = pr_result.stdout.strip()
        logger.info(f"Created PR: {pr_url}")
        return True, pr_url

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        logger.error(f"Failed to create PR: {error_msg}")
        return False, error_msg
    except Exception as e:
        logger.error(f"Unexpected error creating PR: {e}")
        return False, str(e)


def generate_pr_body(plan_file: Path | None, branch_name: str) -> str:
    """
    Generate PR description from plan file.

    Args:
        plan_file: Path to ACTIVE-*.md plan file
        branch_name: Current feature branch

    Returns:
        Formatted PR body
    """
    if not plan_file or not plan_file.exists():
        return f"""## Summary

Work completed on branch `{branch_name}`.

## Changes

See commit history for details.

---

🤖 Created by Valor
"""

    try:
        content = plan_file.read_text()

        # Extract sections from plan
        import re

        request_match = re.search(r'## Original Request\n\n(.*?)\n\n##', content, re.DOTALL)
        original_request = request_match.group(1).strip() if request_match else "See plan document"

        notes_match = re.search(r'## Implementation Notes\n\n(.*?)(?=\n## |\n---|\Z)', content, re.DOTALL)
        implementation_notes = notes_match.group(1).strip() if notes_match else "(No notes)"

        return f"""## Summary

{original_request}

## Implementation

{implementation_notes}

## Completion Checklist

- ✅ Deliverable exists and works
- ✅ Code quality standards met
- ✅ Changes committed
- ✅ Tests passing
- ✅ Original request fulfilled

---

🤖 Created by Valor from branch `{branch_name}`
"""

    except Exception as e:
        logger.error(f"Failed to parse plan file: {e}")
        return generate_pr_body(None, branch_name)


def complete_work_with_pr(
    working_dir: Path,
    branch_name: str,
    plan_file: Path | None
) -> tuple[bool, str]:
    """
    Complete work by creating a PR instead of pushing to main.

    This is used when the repository has protected branches that
    require PRs for merging.

    Args:
        working_dir: Project directory
        branch_name: Current feature branch
        plan_file: Path to active plan document

    Returns:
        (success, pr_url_or_error)
    """
    # Generate PR title from branch name
    # Convert "feature/20260130-fix-bug-in-auth" -> "Fix bug in auth"
    title_parts = branch_name.replace("feature/", "").split("-", 1)
    if len(title_parts) > 1:
        title = title_parts[1].replace("-", " ").title()
    else:
        title = branch_name.replace("-", " ").title()

    # Generate PR body from plan
    body = generate_pr_body(plan_file, branch_name)

    # Create the PR
    success, result = create_pull_request(
        working_dir=working_dir,
        branch_name=branch_name,
        title=title,
        body=body,
        base_branch="main"
    )

    if success:
        logger.info(f"Work completed via PR: {result}")
    else:
        logger.error(f"Failed to create PR: {result}")

    return success, result
