"""
Branch-based work tracking for persistent state management.

Uses git branches as the source of truth for work-in-progress.
Each work request gets its own feature branch with a plan doc.
"""

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

WorkStatus = Literal["CLEAN", "IN_PROGRESS", "BLOCKED"]


@dataclass
class BranchState:
    """Current state of the git repository."""
    current_branch: str
    is_main: bool
    has_uncommitted_changes: bool
    active_plan: Path | None
    work_status: WorkStatus


def get_current_branch(working_dir: Path) -> str:
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        )
        return result.stdout.strip()
    except Exception as e:
        logger.error(f"Failed to get current branch: {e}")
        return "main"  # Fallback


def has_uncommitted_changes(working_dir: Path) -> bool:
    """Check if there are uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=5
        )
        return bool(result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to check git status: {e}")
        return False


def find_active_plan(working_dir: Path) -> Path | None:
    """Find active plan document in docs/plans/."""
    plans_dir = working_dir / "docs" / "plans"
    if not plans_dir.exists():
        return None

    # Look for ACTIVE-*.md files
    for plan_file in plans_dir.glob("ACTIVE-*.md"):
        return plan_file

    return None


def get_branch_state(working_dir: Path) -> BranchState:
    """
    Get current repository state.

    Returns information about what branch we're on and if there's
    active work in progress.
    """
    current_branch = get_current_branch(working_dir)
    is_main = current_branch in ("main", "master")
    uncommitted = has_uncommitted_changes(working_dir)
    active_plan = find_active_plan(working_dir)

    # Determine work status
    if is_main and not uncommitted and not active_plan:
        status: WorkStatus = "CLEAN"
    elif not is_main or active_plan:
        status = "IN_PROGRESS"
    else:
        status = "BLOCKED"  # Uncommitted on main - unusual state

    return BranchState(
        current_branch=current_branch,
        is_main=is_main,
        has_uncommitted_changes=uncommitted,
        active_plan=active_plan,
        work_status=status,
    )


def sanitize_branch_name(description: str) -> str:
    """
    Convert user request into a valid git branch name.

    Examples:
    - "update readme and add docs" → "update-readme-and-add-docs"
    - "Fix bug in auth module" → "fix-bug-in-auth-module"
    """
    # Lowercase
    name = description.lower()

    # Remove special characters, keep alphanumeric and spaces
    name = re.sub(r'[^a-z0-9\s-]', '', name)

    # Replace spaces with hyphens
    name = re.sub(r'\s+', '-', name)

    # Remove consecutive hyphens
    name = re.sub(r'-+', '-', name)

    # Trim and limit length
    name = name.strip('-')[:50]

    return name


def create_work_branch(
    working_dir: Path,
    description: str,
    base_branch: str = "main"
) -> tuple[bool, str]:
    """
    Create a new feature branch for work.

    Args:
        working_dir: Project directory
        description: User's request (used for branch name)
        base_branch: Branch to create from (default: main)

    Returns:
        (success, branch_name)
    """
    # Generate branch name
    sanitized = sanitize_branch_name(description)
    timestamp = datetime.now().strftime("%Y%m%d")
    branch_name = f"feature/{timestamp}-{sanitized}"

    try:
        # Ensure we're on base branch and clean
        subprocess.run(
            ["git", "checkout", base_branch],
            cwd=working_dir,
            capture_output=True,
            timeout=10,
            check=True
        )

        # Create and checkout new branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=working_dir,
            capture_output=True,
            timeout=10,
            check=True
        )

        logger.info(f"Created feature branch: {branch_name}")
        return True, branch_name

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create branch {branch_name}: {e.stderr}")
        return False, ""


def create_plan_document(
    working_dir: Path,
    branch_name: str,
    user_request: str,
    success_criteria: str = ""
) -> Path:
    """
    Create initial plan document as first commit in feature branch.

    Args:
        working_dir: Project directory
        branch_name: Current feature branch
        user_request: Original request from user
        success_criteria: What defines completion (optional)

    Returns:
        Path to created plan file
    """
    plans_dir = working_dir / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    # Plan filename includes branch for easy tracking
    plan_file = plans_dir / f"ACTIVE-{branch_name.replace('feature/', '')}.md"

    # Load completion criteria from CLAUDE.md if not provided
    if not success_criteria:
        claude_md = working_dir / "CLAUDE.md"
        if claude_md.exists():
            content = claude_md.read_text()
            match = re.search(
                r'## Work Completion Criteria\n\n.*?### Required Completion Checks(.*?)(?=\n## |\n### Why|$)',
                content,
                re.DOTALL
            )
            if match:
                success_criteria = match.group(1).strip()

    # Create plan content
    plan_content = f"""# Work Plan: {branch_name}

**Status**: IN_PROGRESS
**Created**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Branch**: `{branch_name}`

## Original Request

{user_request}

## Success Criteria

{success_criteria if success_criteria else "- Fulfill the original request\n- All code committed and pushed\n- Tests passing (if applicable)"}

## Implementation Notes

(Agent will update this section as work progresses)

## Completion Checklist

- [ ] Deliverable exists and works
- [ ] Code quality standards met
- [ ] Changes committed
- [ ] Artifacts created
- [ ] Original request fulfilled

---

*This file will be deleted when work is complete and merged to main.*
"""

    plan_file.write_text(plan_content)
    logger.info(f"Created plan document: {plan_file}")

    return plan_file


def commit_plan_document(working_dir: Path, plan_file: Path, branch_name: str) -> bool:
    """
    Commit the plan document as first commit in branch.

    Args:
        working_dir: Project directory
        plan_file: Path to plan document
        branch_name: Current feature branch

    Returns:
        True if commit succeeded
    """
    try:
        # Add plan file
        subprocess.run(
            ["git", "add", str(plan_file)],
            cwd=working_dir,
            capture_output=True,
            timeout=5,
            check=True
        )

        # Commit
        commit_message = f"Plan: {branch_name}\n\nInitial work plan and success criteria."
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=working_dir,
            capture_output=True,
            timeout=10,
            check=True
        )

        logger.info(f"Committed plan document for {branch_name}")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to commit plan: {e.stderr}")
        return False


def should_create_branch(user_request: str) -> bool:
    """
    Determine if request requires a feature branch.

    Quick fixes and trivial changes can stay on main.
    Multi-step work gets a branch.

    Args:
        user_request: The user's message

    Returns:
        True if should create branch
    """
    # Quick heuristics
    request_lower = user_request.lower()

    # Keywords indicating multi-step work
    multi_step_indicators = [
        "update", "add", "create", "implement", "refactor",
        "fix bug", "improve", "enhance", "build", "develop",
        "and", "then", "also", "documentation", "tests"
    ]

    # Count indicators
    indicator_count = sum(1 for indicator in multi_step_indicators if indicator in request_lower)

    # Branch if:
    # - Multiple indicators (suggests complexity)
    # - Request is long (> 100 chars suggests detail)
    return indicator_count >= 2 or len(user_request) > 100


def initialize_work_branch(
    working_dir: Path,
    user_request: str,
    force_branch: bool = False
) -> tuple[bool, str, Path | None]:
    """
    Set up work environment: create branch and plan doc if needed.

    Args:
        working_dir: Project directory
        user_request: User's request text
        force_branch: Always create branch even for simple requests

    Returns:
        (branch_created, branch_name, plan_file_path)
    """
    # Check if we should create a branch
    if not force_branch and not should_create_branch(user_request):
        logger.info("Simple request, staying on main branch")
        return False, get_current_branch(working_dir), None

    # Create feature branch
    success, branch_name = create_work_branch(working_dir, user_request)
    if not success:
        logger.error("Failed to create branch, staying on current branch")
        return False, get_current_branch(working_dir), None

    # Create plan document
    plan_file = create_plan_document(working_dir, branch_name, user_request)

    # Commit plan
    commit_success = commit_plan_document(working_dir, plan_file, branch_name)
    if not commit_success:
        logger.warning("Plan created but not committed")

    return True, branch_name, plan_file


def return_to_main(working_dir: Path) -> bool:
    """
    Switch back to main branch.

    Used when starting new work or when branch work is complete.

    Returns:
        True if successfully switched to main
    """
    try:
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=working_dir,
            capture_output=True,
            timeout=10,
            check=True
        )
        logger.info("Switched to main branch")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to switch to main: {e.stderr}")
        # Try master as fallback
        try:
            subprocess.run(
                ["git", "checkout", "master"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
                check=True
            )
            logger.info("Switched to master branch")
            return True
        except:
            return False


def format_branch_state_message(state: BranchState) -> str:
    """
    Format branch state for user notification.

    Returns a human-readable message about current work state.
    """
    if state.work_status == "CLEAN":
        return "✅ Repository is clean. Ready for new work."

    elif state.work_status == "IN_PROGRESS":
        msg = f"⚠️ Work in progress detected:\n\n"
        msg += f"**Branch**: `{state.current_branch}`\n"

        if state.active_plan:
            msg += f"**Plan**: `{state.active_plan.name}`\n\n"
            msg += "Options:\n"
            msg += "- Reply 'continue' to resume this work\n"
            msg += "- Send a new request to start fresh (will switch to main)\n"
        else:
            msg += "\nNo active plan found. You may want to commit or stash changes."

        return msg

    else:  # BLOCKED
        return f"⚠️ Unusual state: uncommitted changes on {state.current_branch}"
