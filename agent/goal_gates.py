"""Deterministic gate checks for SDLC pipeline stages.

Each gate function checks whether a specific SDLC stage has been completed
by examining filesystem artifacts, GitHub API state, or session history.
Gates return GateResult objects and never raise exceptions -- all subprocess
and IO errors are caught and returned as unsatisfied results.

Usage:
    from agent.goal_gates import check_gate, check_all_gates

    result = check_gate("PLAN", slug="my-feature", working_dir="/path/to/repo")
    if result.satisfied:
        print(result.evidence)
    else:
        print(result.missing)

    all_results = check_all_gates("my-feature", "/path/to/repo")
    for stage, result in all_results.items():
        print(f"{stage}: {'PASS' if result.satisfied else 'FAIL'} - {result.evidence}")
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# Ordered list of stages that have gate checks
GATE_STAGES = ["PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]


@dataclass
class GateResult:
    """Result of a deterministic gate check.

    Attributes:
        satisfied: Whether the gate condition is met.
        evidence: Human-readable description of what was checked and found.
        missing: Description of what is missing, if not satisfied.
    """

    satisfied: bool
    evidence: str
    missing: str | None = None


def _run_gh_command(args: list[str], working_dir: str | Path | None = None) -> str:
    """Run a gh CLI command and return stdout.

    Args:
        args: Command arguments (without the leading 'gh').
        working_dir: Working directory for the subprocess.

    Returns:
        Stripped stdout string.

    Raises:
        subprocess.CalledProcessError: If the command fails.
        subprocess.TimeoutExpired: If the command takes longer than 10 seconds.
    """
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=working_dir,
    )
    result.check_returncode()
    return result.stdout.strip()


def check_plan_gate(slug: str, working_dir: str | Path) -> GateResult:
    """Check if a plan document exists for the given slug.

    Looks for docs/plans/{slug}.md in the working directory.

    Args:
        slug: Work item identifier, e.g. "my-feature".
        working_dir: Repository root directory.

    Returns:
        GateResult indicating whether the plan file exists.
    """
    try:
        plan_path = Path(working_dir) / "docs" / "plans" / f"{slug}.md"
        if plan_path.exists():
            return GateResult(
                satisfied=True,
                evidence=f"Plan file exists at docs/plans/{slug}.md",
            )
        return GateResult(
            satisfied=False,
            evidence=f"Plan file NOT FOUND at docs/plans/{slug}.md",
            missing=f"docs/plans/{slug}.md",
        )
    except Exception as e:
        logger.warning(f"check_plan_gate failed for slug {slug!r}: {e}")
        return GateResult(
            satisfied=False,
            evidence=f"Plan gate check failed: {e}",
            missing=f"docs/plans/{slug}.md (check error)",
        )


def check_build_gate(slug: str, working_dir: str | Path) -> GateResult:
    """Check if a pull request exists for the slug's branch.

    Queries GitHub via `gh pr list` for PRs targeting the session/{slug} branch.

    Args:
        slug: Work item identifier, e.g. "my-feature".
        working_dir: Repository root directory (used as cwd for gh commands).

    Returns:
        GateResult indicating whether a PR was found.
    """
    branch = f"session/{slug}"
    try:
        output = _run_gh_command(
            ["pr", "list", "--head", branch, "--json", "number", "--jq", "length"],
            working_dir=working_dir,
        )
        count = int(output) if output else 0
        if count > 0:
            return GateResult(
                satisfied=True,
                evidence=f"PR found for branch {branch}",
            )
        return GateResult(
            satisfied=False,
            evidence=f"No PR found for branch {branch}",
            missing=f"Pull request for branch {branch}",
        )
    except Exception as e:
        logger.warning(f"check_build_gate failed for slug {slug!r}: {e}")
        return GateResult(
            satisfied=False,
            evidence=f"Build gate check failed: {e}",
            missing=f"Pull request for branch {branch} (check error)",
        )


def check_test_gate(slug: str, session: AgentSession | None = None) -> GateResult:
    """Check if test stage has been completed.

    Primary: Scans session history for a [stage] entry containing both
    "TEST" and "COMPLETED" (case-insensitive).

    Fallback: Checks data/pipeline/{slug}/state.json for test stage
    in the completed_stages list.

    Args:
        slug: Work item identifier, e.g. "my-feature".
        session: Optional AgentSession to check history entries.

    Returns:
        GateResult indicating whether test completion evidence was found.
    """
    # Primary: check session history
    if session is not None:
        try:
            history = session.get_history_list()
            for entry in history:
                if not isinstance(entry, str):
                    continue
                entry_lower = entry.lower()
                if (
                    "[stage]" in entry_lower
                    and "test" in entry_lower
                    and "completed" in entry_lower
                ):
                    return GateResult(
                        satisfied=True,
                        evidence="TEST COMPLETED found in session history",
                    )
        except Exception as e:
            logger.warning(f"check_test_gate session history check failed: {e}")

    # Fallback: check pipeline state file
    try:
        repo_root = Path(__file__).parent.parent
        state_path = repo_root / "data" / "pipeline" / slug / "state.json"
        if state_path.exists():
            with open(state_path) as f:
                state = json.load(f)
            completed_stages = state.get("completed_stages", [])
            if "test" in completed_stages:
                return GateResult(
                    satisfied=True,
                    evidence="Test stage found in pipeline state completed_stages",
                )
    except Exception as e:
        logger.warning(f"check_test_gate pipeline state check failed for slug {slug!r}: {e}")

    return GateResult(
        satisfied=False,
        evidence="No TEST COMPLETED evidence found",
        missing="Test completion evidence in session history or pipeline state",
    )


def check_review_gate(slug: str, working_dir: str | Path) -> GateResult:
    """Check if a review exists on the PR for the slug's branch.

    Queries GitHub for PR reviews and review-style comments (comments
    whose body starts with "## Review:").

    Args:
        slug: Work item identifier, e.g. "my-feature".
        working_dir: Repository root directory (used as cwd for gh commands).

    Returns:
        GateResult indicating whether a review was found.
    """
    branch = f"session/{slug}"
    try:
        # Get PR number
        pr_number_str = _run_gh_command(
            ["pr", "list", "--head", branch, "--json", "number", "--jq", ".[0].number"],
            working_dir=working_dir,
        )
        if not pr_number_str:
            return GateResult(
                satisfied=False,
                evidence=f"No PR found for branch {branch}",
                missing=f"Pull request and review for branch {branch}",
            )

        pr_number = pr_number_str.strip()

        # Get owner/repo
        name_with_owner = _run_gh_command(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            working_dir=working_dir,
        )
        if not name_with_owner:
            return GateResult(
                satisfied=False,
                evidence="Could not determine repository owner/name",
                missing="Repository identity for review check",
            )

        # Check PR reviews
        review_count_str = _run_gh_command(
            ["api", f"repos/{name_with_owner}/pulls/{pr_number}/reviews", "--jq", "length"],
            working_dir=working_dir,
        )
        review_count = int(review_count_str) if review_count_str else 0

        # Check review comments (comments starting with "## Review:")
        review_comment_count_str = _run_gh_command(
            [
                "api",
                f"repos/{name_with_owner}/issues/{pr_number}/comments",
                "--jq",
                '[.[] | select(.body | startswith("## Review:"))] | length',
            ],
            working_dir=working_dir,
        )
        review_comment_count = int(review_comment_count_str) if review_comment_count_str else 0

        if review_count > 0 or review_comment_count > 0:
            return GateResult(
                satisfied=True,
                evidence=f"Review found on PR #{pr_number}",
            )

        return GateResult(
            satisfied=False,
            evidence=f"No review found for PR #{pr_number}",
            missing=f"Review on PR #{pr_number}",
        )
    except Exception as e:
        logger.warning(f"check_review_gate failed for slug {slug!r}: {e}")
        return GateResult(
            satisfied=False,
            evidence=f"Review gate check failed: {e}",
            missing=f"Review for branch {branch} (check error)",
        )


def check_docs_gate(slug: str, working_dir: str | Path) -> GateResult:
    """Check if documentation exists for the given slug.

    Satisfied if:
    - docs/features/{slug_with_hyphens}.md exists, OR
    - The plan doc contains "No documentation changes needed" or
      "No docs needed" (case-insensitive).

    Args:
        slug: Work item identifier, e.g. "my_feature" or "my-feature".
        working_dir: Repository root directory.

    Returns:
        GateResult indicating whether documentation requirements are met.
    """
    try:
        working = Path(working_dir)
        # Convert underscores to hyphens for the feature doc filename
        slug_hyphens = slug.replace("_", "-")

        # Check if feature doc exists
        feature_doc_path = working / "docs" / "features" / f"{slug_hyphens}.md"
        if feature_doc_path.exists():
            return GateResult(
                satisfied=True,
                evidence=f"Feature doc exists at docs/features/{slug_hyphens}.md",
            )

        # Check if plan explicitly skips docs
        plan_path = working / "docs" / "plans" / f"{slug}.md"
        if plan_path.exists():
            plan_content = plan_path.read_text().lower()
            if (
                "no documentation changes needed" in plan_content
                or "no docs needed" in plan_content
            ):
                return GateResult(
                    satisfied=True,
                    evidence="Plan explicitly skips docs",
                )

        return GateResult(
            satisfied=False,
            evidence="No feature doc and plan does not skip docs",
            missing=f"docs/features/{slug_hyphens}.md or docs skip declaration in plan",
        )
    except Exception as e:
        logger.warning(f"check_docs_gate failed for slug {slug!r}: {e}")
        return GateResult(
            satisfied=False,
            evidence=f"Docs gate check failed: {e}",
            missing=f"Documentation for {slug} (check error)",
        )


def check_gate(
    stage: str,
    slug: str,
    working_dir: str | Path,
    session: AgentSession | None = None,
) -> GateResult:
    """Dispatch a gate check for the given SDLC stage.

    Maps stage names to their respective check functions.

    Args:
        stage: SDLC stage name (PLAN, BUILD, TEST, REVIEW, DOCS).
        slug: Work item identifier.
        working_dir: Repository root directory.
        session: Optional AgentSession (used by TEST gate).

    Returns:
        GateResult from the appropriate gate check function,
        or an unsatisfied result for unrecognized stages.
    """
    stage_upper = stage.upper()

    gate_map = {
        "PLAN": lambda: check_plan_gate(slug, working_dir),
        "BUILD": lambda: check_build_gate(slug, working_dir),
        "TEST": lambda: check_test_gate(slug, session),
        "REVIEW": lambda: check_review_gate(slug, working_dir),
        "DOCS": lambda: check_docs_gate(slug, working_dir),
    }

    gate_fn = gate_map.get(stage_upper)
    if gate_fn is None:
        return GateResult(
            satisfied=False,
            evidence=f"Unknown stage: {stage}",
        )

    return gate_fn()


def check_all_gates(
    slug: str,
    working_dir: str | Path,
    session: AgentSession | None = None,
) -> dict[str, GateResult]:
    """Run all gate checks and return results for every stage.

    Does NOT short-circuit: always checks all gates regardless of
    individual results.

    Args:
        slug: Work item identifier.
        working_dir: Repository root directory.
        session: Optional AgentSession (used by TEST gate).

    Returns:
        Dict mapping stage name to its GateResult.
    """
    results: dict[str, GateResult] = {}
    for stage in GATE_STAGES:
        results[stage] = check_gate(stage, slug, working_dir, session)
    return results
