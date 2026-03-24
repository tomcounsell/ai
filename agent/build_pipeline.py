"""Build pipeline state persistence for SDLC build stages.

Renamed from agent/pipeline_state.py to avoid naming collision with
bridge/pipeline_state.py (PipelineStateMachine).

Tracks progress of a build pipeline keyed by slug (e.g., "my-feature").
State is stored at data/pipeline/{slug}/state.json and survives process
restarts, enabling resumable builds that pick up where they left off.

Resume logic:
  - load() returns None when no state file exists (start fresh)
  - advance_stage() moves the pipeline forward: appends the current stage
    to completed_stages, then sets the new stage as current
  - The do-build orchestrator checks load() on entry; if state exists and
    stage != "plan", it skips already-completed stages
"""

import json
from datetime import UTC, datetime
from pathlib import Path

# Ordered pipeline stages. The pipeline progresses left-to-right.
# "patch" may be re-entered multiple times (tracked via patch_iterations).
STAGES = [
    "plan",
    "critique",
    "branch",
    "implement",
    "test",
    "patch",
    "review",
    "document",
    "commit",
    "pr",
]

# Root directory for all pipeline state files, relative to repo root.
# Resolved at import time so callers don't need to know the layout.
_REPO_ROOT = Path(__file__).parent.parent
_STATE_ROOT = _REPO_ROOT / "data" / "pipeline"


def _state_path(slug: str) -> Path:
    """Return the path to the state file for a given slug."""
    return _STATE_ROOT / slug / "state.json"


def _utcnow() -> str:
    """Return current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def exists(slug: str) -> bool:
    """Return True if a pipeline state file exists for the given slug.

    Args:
        slug: Build identifier, e.g. "my-feature"

    Returns:
        True if data/pipeline/{slug}/state.json exists, False otherwise.
    """
    return _state_path(slug).exists()


def load(slug: str) -> dict | None:
    """Load pipeline state for the given slug.

    Returns None when no state file exists — this signals that the build
    should start from scratch at the "plan" stage rather than resuming.

    Args:
        slug: Build identifier, e.g. "my-feature"

    Returns:
        State dict if the file exists, None if this is a new build.

    Raises:
        ValueError: If the state file exists but contains invalid JSON.
    """
    path = _state_path(slug)

    # Missing file is the normal "new build" case — return None, not an error.
    if not path.exists():
        return None

    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Corrupt pipeline state for slug {slug!r}: {e}") from e


def save(state: dict) -> None:
    """Persist a state dict to data/pipeline/{slug}/state.json.

    Creates parent directories as needed. Uses a write-then-rename pattern
    to avoid leaving a half-written file if the process is interrupted.

    Args:
        state: State dict containing at least a "slug" key.

    Raises:
        KeyError: If "slug" is missing from the state dict.
        OSError: If the file cannot be written.
    """
    slug = state["slug"]  # Fail loudly if missing — slug is mandatory
    path = _state_path(slug)

    # Ensure data/pipeline/{slug}/ exists before writing
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to a temp file, then rename over the target.
    # This prevents a partial write from corrupting the state file.
    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        tmp_path.rename(path)
    except Exception:
        # Clean up the temp file so stale .tmp files don't linger
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def initialize(
    slug: str,
    branch: str,
    worktree: str,
    target_repo: str | None = None,
) -> dict:
    """Create a fresh pipeline state starting at the "plan" stage.

    Call this when starting a brand-new build for a slug that has no
    existing state (i.e., load() returned None).

    Args:
        slug: Build identifier, e.g. "my-feature"
        branch: Git branch name, e.g. "session/my-feature"
        worktree: Relative path to the git worktree, e.g. ".worktrees/my-feature"
        target_repo: Absolute path to the target repository root when the
            build targets a repo other than the ai (orchestrator) repo.
            None means the build targets the current (ai) repo.

    Returns:
        Newly created state dict (already persisted to disk).
    """
    now = _utcnow()
    state = {
        "slug": slug,
        "branch": branch,
        "worktree": worktree,
        "stage": "plan",
        "completed_stages": [],
        "patch_iterations": 0,
        "started_at": now,
        "updated_at": now,
    }
    if target_repo is not None:
        state["target_repo"] = str(target_repo)
    save(state)
    return state


def advance_stage(slug: str, next_stage: str) -> dict:
    """Advance the pipeline to the next stage.

    Appends the current stage to completed_stages before setting the new
    stage. This preserves a full audit trail of which stages ran.

    Example transition (implement -> test):
        Before: stage="implement", completed_stages=["plan", "branch"]
        After:  stage="test",      completed_stages=["plan", "branch", "implement"]

    If next_stage is "patch", patch_iterations is also incremented so the
    orchestrator can enforce a retry cap.

    Args:
        slug: Build identifier, e.g. "my-feature"
        next_stage: Target stage name (must be in STAGES)

    Returns:
        Updated state dict (already persisted to disk).

    Raises:
        FileNotFoundError: If no state file exists for the slug.
        ValueError: If next_stage is not a recognised pipeline stage.
    """
    if next_stage not in STAGES:
        raise ValueError(f"Unknown stage {next_stage!r}. Valid stages: {STAGES}")

    state = load(slug)
    if state is None:
        raise FileNotFoundError(
            f"No pipeline state found for slug {slug!r}. Call initialize() before advance_stage()."
        )

    # Move current stage into the completed list before advancing
    current_stage = state["stage"]
    completed = list(state.get("completed_stages", []))
    if current_stage not in completed:
        completed.append(current_stage)

    state["completed_stages"] = completed
    state["stage"] = next_stage
    state["updated_at"] = _utcnow()

    # Track how many times we've entered the patch stage (retry counter)
    if next_stage == "patch":
        state["patch_iterations"] = state.get("patch_iterations", 0) + 1

    save(state)
    return state
