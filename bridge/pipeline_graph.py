"""Backward-compatibility shim: pipeline graph has moved to agent/pipeline_graph.py.

All code should import from agent.pipeline_graph instead. This shim re-exports
everything so existing import sites keep working without modification.
"""

# Re-export everything from the canonical location
from agent.pipeline_graph import (  # noqa: F401
    DISPLAY_STAGES,
    MAX_CRITIQUE_CYCLES,
    MAX_PATCH_CYCLES,
    PIPELINE_EDGES,
    STAGE_TO_SKILL,
    get_next_stage,
)
