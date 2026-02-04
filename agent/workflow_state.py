"""Workflow state persistence with file-based storage.

Implements WorkflowState class for managing persistent state across
multi-phase workflows (plan → build → test → review → document).

State is stored in agents/{workflow_id}/state.json and supports:
- Atomic write operations (temp file + rename)
- stdin/stdout piping for shell workflow chaining
- Type-safe state using WorkflowStateData from workflow_types
"""

import json
import secrets
import sys
from datetime import datetime
from pathlib import Path

from agent.workflow_types import WorkflowStateData


def generate_workflow_id() -> str:
    """Generate a unique 8-character workflow ID.

    Uses URL-safe base64 encoding for readable, collision-resistant IDs.

    Returns:
        8-character unique identifier string
    """
    # Generate 6 random bytes, encode as URL-safe base64, take first 8 chars
    # This gives us 48 bits of entropy (2^48 = 281 trillion combinations)
    return secrets.token_urlsafe(6)[:8]


class WorkflowState:
    """Manages persistent workflow state with file-based storage.

    Stores state in agents/{workflow_id}/state.json with atomic writes
    to prevent corruption. Supports stdin/stdout piping for shell workflows.

    Example:
        # Create new workflow
        state = WorkflowState("abc12345")
        state.update(plan_file="docs/plans/feature.md", tracking_url="https://...")
        state.save(phase="plan")

        # Load existing workflow
        state = WorkflowState.load("abc12345")

        # Pipe to next phase
        state.to_stdout()  # Shell: python script.py | next_phase.py

        # Read from pipe
        state = WorkflowState.from_stdin()
    """

    def __init__(self, workflow_id: str):
        """Initialize workflow state.

        Args:
            workflow_id: Unique 8-character workflow identifier
        """
        self.workflow_id = workflow_id
        self.state_dir = Path(f"/Users/valorengels/src/ai/agents/{workflow_id}")
        self.state_file = self.state_dir / "state.json"
        self._data: WorkflowStateData | None = None

    def update(self, **kwargs) -> None:
        """Update state with key-value pairs.

        Updates the internal state dictionary and sets updated_at timestamp.
        Does NOT save to disk - call save() to persist.

        Args:
            **kwargs: Key-value pairs to update in state.
                     Must be valid WorkflowStateData fields.

        Raises:
            ValueError: If required fields (workflow_id, plan_file, tracking_url)
                       are missing when creating new state
        """
        if self._data is None:
            # Create new state - ensure required fields are present
            if "workflow_id" not in kwargs:
                kwargs["workflow_id"] = self.workflow_id

            # Validate required fields
            if "plan_file" not in kwargs:
                raise ValueError("plan_file is required for new workflow state")
            if "tracking_url" not in kwargs:
                raise ValueError("tracking_url is required for new workflow state")

            self._data = WorkflowStateData(**kwargs)
        else:
            # Update existing state
            update_dict = self._data.model_dump()
            update_dict.update(kwargs)
            update_dict["updated_at"] = datetime.utcnow()
            self._data = WorkflowStateData(**update_dict)

    def save(self, phase: str | None = None) -> None:
        """Save state to agents/{workflow_id}/state.json atomically.

        Creates directory if needed, writes to temp file, then atomically
        renames to prevent corruption from interrupted writes.

        Args:
            phase: Optional workflow phase to set before saving

        Raises:
            ValueError: If no state data exists to save
            OSError: If directory creation or file write fails
        """
        if self._data is None:
            raise ValueError("No state data to save. Call update() first.")

        # Update phase if provided
        if phase is not None:
            self.update(phase=phase)

        # Create directory if it doesn't exist
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Atomic write: temp file + rename
        temp_file = self.state_file.with_suffix(".json.tmp")
        try:
            with open(temp_file, "w") as f:
                json.dump(
                    self._data.model_dump(mode="json"),
                    f,
                    indent=2,
                    default=str,  # Handle datetime serialization
                )

            # Atomic rename
            temp_file.rename(self.state_file)
        except Exception as e:
            # Clean up temp file on error
            if temp_file.exists():
                temp_file.unlink()
            raise OSError(f"Failed to save workflow state: {e}") from e

    @classmethod
    def load(cls, workflow_id: str) -> "WorkflowState":
        """Load existing workflow state from disk.

        Args:
            workflow_id: Unique workflow identifier

        Returns:
            WorkflowState instance with loaded data

        Raises:
            FileNotFoundError: If state file doesn't exist
            ValueError: If state file is invalid JSON or missing required fields
        """
        instance = cls(workflow_id)

        if not instance.state_file.exists():
            raise FileNotFoundError(f"Workflow state not found: {instance.state_file}")

        try:
            with open(instance.state_file) as f:
                data = json.load(f)

            instance._data = WorkflowStateData(**data)
            return instance
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in state file: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to load workflow state: {e}") from e

    @classmethod
    def from_stdin(cls) -> "WorkflowState":
        """Read workflow state from stdin for piped workflows.

        Enables shell workflow chaining:
            python plan.py | python build.py | python test.py

        Returns:
            WorkflowState instance with data from stdin

        Raises:
            ValueError: If stdin is empty or contains invalid JSON
        """
        stdin_data = sys.stdin.read().strip()

        if not stdin_data:
            raise ValueError("No data received from stdin")

        try:
            data = json.loads(stdin_data)
            workflow_id = data.get("workflow_id")

            if not workflow_id:
                raise ValueError("workflow_id missing from stdin data")

            instance = cls(workflow_id)
            instance._data = WorkflowStateData(**data)
            return instance
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from stdin: {e}") from e
        except Exception as e:
            raise ValueError(f"Failed to parse workflow state from stdin: {e}") from e

    def to_stdout(self) -> None:
        """Output state to stdout for piping to next phase.

        Enables shell workflow chaining:
            python plan.py | python build.py | python test.py

        Raises:
            ValueError: If no state data exists to output
        """
        if self._data is None:
            raise ValueError("No state data to output. Call update() first.")

        # Output JSON to stdout
        json.dump(self._data.model_dump(mode="json"), sys.stdout, indent=2, default=str)
        sys.stdout.flush()

    @property
    def data(self) -> WorkflowStateData | None:
        """Get current state data.

        Returns:
            WorkflowStateData instance or None if no state loaded
        """
        return self._data

    def __repr__(self) -> str:
        """String representation of workflow state."""
        if self._data:
            return (
                f"WorkflowState(id={self.workflow_id}, "
                f"phase={self._data.phase}, "
                f"status={self._data.status})"
            )
        return f"WorkflowState(id={self.workflow_id}, data=None)"
