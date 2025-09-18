"""
MCP tools for Creative Juices integration.
"""

from typing import Optional

from mcp import Tool
from pydantic import BaseModel, Field


class GetCreativeSparkParams(BaseModel):
    """Parameters for get_creative_spark."""

    count: Optional[int] = Field(
        default=2,
        ge=1,
        le=5,
        description="Number of verb-noun pairs to generate (1-5)"
    )
    intensity: Optional[str] = Field(
        default="wild",
        description="Creativity level: 'mild', 'wild', or 'chaos'"
    )


# MCP Tool definitions
CREATIVE_JUICES_TOOLS = [
    Tool(
        name="get_creative_spark",
        description="Generate random verb-noun combinations with divergent thinking instructions to break out of predictable patterns",
        inputSchema=GetCreativeSparkParams.model_json_schema(),
    ),
]

# Prompt template for creative reframing
CREATIVE_PROMPT = """When approaching problems or generating ideas, consider using conceptual
verb-noun combinations to break conventional thinking patterns.

Ask yourself: How would [verb]-[noun] change your approach?

This technique forces lateral thinking by creating unexpected metaphors
and connections that can reveal innovative solutions.

Try using the get_creative_spark tool to generate random combinations that will:
- Force you to see the problem from new angles
- Create unexpected metaphors and associations
- Break through mental blocks and fixed assumptions
- Inspire novel solutions through conceptual blending

Example: If you get "dissolve-constellation", you might ask:
- How could the solution dissolve boundaries like stars dissolve into light?
- What if the components were as interconnected as a constellation?
- Could the problem be broken down into smaller, distant but related parts?"""