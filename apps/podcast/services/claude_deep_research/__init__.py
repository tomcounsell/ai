"""Deep research orchestrator: multi-agent pipeline for comprehensive research.

Architecture
------------

    Research Command
          |
    [Stage 1: Planner (Opus)]
          |
    ResearchPlan (3-5 subtasks)
          |
    [Stage 2: Researchers (Sonnet x N)]  -- WebSearchTool + fetch_page
          |
    list[SubagentFindings]
          |
    [Stage 3: Synthesizer (Opus)]
          |
    DeepResearchReport

Each stage is a separate Named AI Tool module with its own agent,
output model, and system prompt. The orchestrator ties them together.
"""

from apps.podcast.services.claude_deep_research.orchestrate import deep_research
from apps.podcast.services.claude_deep_research.synthesizer import DeepResearchReport

__all__ = ["deep_research", "DeepResearchReport"]
