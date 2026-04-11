"""Backward-compatibility shim: pipeline state has moved to agent/pipeline_state.py.

All code should import from agent.pipeline_state instead. This shim re-exports
everything so existing import sites keep working without modification.
"""

# Re-export everything from the canonical location
from agent.pipeline_state import (  # noqa: F401
    ALL_STAGES,
    VALID_STATUSES,
    PipelineStateMachine,
    StageStates,
    _parse_outcome_contract,
    _record_stage_metric,
)
