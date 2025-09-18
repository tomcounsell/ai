# Creative Juices MCP Specification

## Implementation Status

### Phase 1 (✅ COMPLETED - Basic Functionality)
- ✅ MCP server implementation using standard MCP protocol
- ✅ `get_creative_spark` tool with Pydantic validation
- ✅ `creative_reframe` prompt
- ✅ Curated word lists for three intensity levels
- ✅ Basic tests for server functionality
- ✅ Django integration (apps/ai/mcp/)

### Phase 2 (🔄 TODO - Refactoring)
- ⬜ Extract common MCP base class
- ⬜ Standardize error handling patterns
- ⬜ Add comprehensive logging

### Phase 3 (⬜ TODO - Enhancements)
- ⬜ FastMCP migration (if needed)
- ⬜ Claude Desktop DXT package
- ⬜ PydanticAI example integration
- ⬜ Extended word categories
- ⬜ User-configurable word lists
- ⬜ Session history tracking
- ⬜ Analytics on tool usage

## Overview

**Name**: Creative Juices MCP
**Purpose**: Provides random verb-noun combinations to nudge LLMs into divergent thinking patterns
**Key Value Proposition**: Break out of predictable AI responses by injecting unexpected conceptual combinations that force creative reframing of problems

## Core Functionality

### Primary Tools
- `get_creative_spark` - Generate random verb-noun pairs with divergent thinking instructions

### Prompts
- `creative_reframe` - Pre-configured prompt to apply creative thinking to current context

## Tool Definitions

### get_creative_spark

```
Description: Generates random verb-noun combinations with instructions for creative thinking
Parameters:
  - count: integer, number of verb-noun pairs (1-5), default=2, optional
  - intensity: string, creativity level ("mild", "wild", "chaos"), default="wild", optional
Returns: 
  - pairs: list of verb-noun combinations
  - instruction: specific divergent thinking instruction  
  - prompt: suggested way to apply these concepts
Example Use Case: When stuck on a problem or need fresh perspective
```

## Implementation Details

- **External Dependencies**: None (uses Python stdlib random)
- **Data Flow**: 
  1. Tool called → Select random verbs/nouns from internal lists
  2. Generate contextual instruction based on intensity
  3. Return structured response with pairs and guidance
- **Error Handling**: Validate parameters with Pydantic, default to safe values
- **Rate Limits/Constraints**: None, fully local operation

## Configuration

- **Environment Variables**: None required
- **Optional Settings**: None (intentionally simple)
- **Default Behavior**: Works immediately with built-in curated word lists

## Installation Requirements

- **Python Version**: 3.12+
- **Key Dependencies**: 
  - `fastmcp` 
  - `pydantic>=2.0`
- **System Requirements**: Any OS with Python 3.12
- **Claude Desktop**: Install via DXT file

## Project Structure

```
creative-juices-mcp/
├── src/
│   ├── __init__.py
│   ├── server.py           # FastMCP server implementation
│   ├── tools.py            # Tool definitions with Pydantic models
│   └── words.py            # Curated word lists by intensity
├── examples/
│   └── pydantic_ai_demo.py # Example usage with PydanticAI
├── tests/
│   └── test_server.py      # Server tests
├── requirements.txt        # FastMCP, pydantic
├── pyproject.toml
├── README.md
└── creative-juices.dxt     # Claude Desktop package
```

## Implementation Code

### src/server.py

```python
"""Creative Juices MCP Server implementation."""

import asyncio
from fastmcp import FastMCP

from .tools import get_creative_spark, CREATIVE_PROMPT

# Initialize FastMCP server
mcp = FastMCP("creative-juices")

# Register the tool
mcp.tool()(get_creative_spark)

# Register the prompt
@mcp.prompt()
async def creative_reframe() -> str:
    """Provide prompt for creative reframing."""
    return CREATIVE_PROMPT

def main():
    """Main entry point."""
    asyncio.run(mcp.run())

if __name__ == "__main__":
    main()
```

### src/tools.py

```python
"""MCP tools for Creative Juices."""

from pydantic import BaseModel, Field
from typing import Literal
import random

from .words import VERBS, NOUNS

class CreativeSparkParams(BaseModel):
    """Parameters for get_creative_spark."""
    count: int = Field(default=2, ge=1, le=5, description="Number of verb-noun pairs")
    intensity: Literal["mild", "wild", "chaos"] = Field(
        default="wild", 
        description="Creativity level"
    )

class CreativeSparkResponse(BaseModel):
    """Response from get_creative_spark."""
    pairs: list[str]
    instruction: str
    prompt: str

async def get_creative_spark(params: CreativeSparkParams) -> CreativeSparkResponse:
    """
    Generate random verb-noun pairs for creative thinking.
    
    Returns verb-noun combinations with instructions for applying
    divergent thinking to the current problem.
    """
    # Select words based on intensity
    verb_list = VERBS[params.intensity]
    noun_list = NOUNS[params.intensity]
    
    # Generate pairs
    pairs = []
    for _ in range(params.count):
        verb = random.choice(verb_list)
        noun = random.choice(noun_list)
        pairs.append(f"{verb}-{noun}")
    
    # Generate instruction based on intensity
    instructions = {
        "mild": "Consider how these concepts might relate to your problem:",
        "wild": "Use these unexpected combinations as lenses to radically reframe:",
        "chaos": "Let these surreal pairings shatter your assumptions:"
    }
    
    # Generate prompt suggestion
    prompt = f"What if your solution could {pairs[0].replace('-', ' the ')}?"
    
    return CreativeSparkResponse(
        pairs=pairs,
        instruction=instructions[params.intensity],
        prompt=prompt
    )

# Prompt template
CREATIVE_PROMPT = """
When approaching problems or generating ideas, consider using conceptual 
verb-noun combinations to break conventional thinking patterns. 

Ask yourself: How would [verb]-[noun] change your approach?

This technique forces lateral thinking by creating unexpected metaphors
and connections that can reveal innovative solutions.
"""
```

### src/words.py

```python
"""Curated word lists for different creativity intensities."""

VERBS = {
    "mild": [
        "transform", "connect", "balance", "shift", "merge",
        "expand", "compress", "redirect", "layer", "simplify"
    ],
    "wild": [
        "dissolve", "crystallize", "ferment", "cascade", "whisper",
        "shatter", "bloom", "unravel", "ignite", "transmute",
        "echo", "fragment", "weave", "distill", "resonate"
    ],
    "chaos": [
        "obliterate", "transcend", "devour", "birth", "dream",
        "fracture", "metamorphose", "liquefy", "combust", "vaporize",
        "implode", "resurrect", "disintegrate", "fuse", "sublime"
    ]
}

NOUNS = {
    "mild": [
        "pattern", "system", "structure", "network", "framework",
        "process", "cycle", "pathway", "foundation", "mechanism"
    ],
    "wild": [
        "constellation", "algorithm", "paradox", "frequency", "void",
        "machinery", "prism", "membrane", "circuit", "tide",
        "entropy", "symmetry", "threshold", "vortex", "matrix"
    ],
    "chaos": [
        "singularity", "infinity", "multiverse", "quantum", "abyss",
        "nebula", "antimatter", "dimension", "continuum", "nexus",
        "chaos", "vacuum", "plasma", "zeitgeist", "paradigm"
    ]
}
```

### examples/pydantic_ai_demo.py

```python
"""Example usage of Creative Juices MCP with PydanticAI."""

import asyncio
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from src.tools import get_creative_spark

async def demo():
    """Demonstrate using Creative Juices with PydanticAI."""
    
    # Initialize agent with the tool
    agent = Agent(
        OpenAIModel("gpt-4"),
        tools=[get_creative_spark]
    )
    
    # Example 1: Problem-solving
    print("=== Problem Solving Example ===")
    result = await agent.run(
        "I need fresh ideas for making my todo app more engaging"
    )
    print(result)
    
    # Example 2: Creative writing  
    print("\n=== Creative Writing Example ===")
    result = await agent.run(
        "Help me brainstorm unique sci-fi concepts"
    )
    print(result)
    
    # Example 3: Direct tool call
    print("\n=== Direct Tool Call ===")
    from src.tools import CreativeSparkParams
    spark = await get_creative_spark(
        CreativeSparkParams(count=3, intensity="chaos")
    )
    print(f"Pairs: {spark.pairs}")
    print(f"Instruction: {spark.instruction}")
    print(f"Prompt: {spark.prompt}")

if __name__ == "__main__":
    asyncio.run(demo())
```

### tests/test_server.py

```python
"""Tests for Creative Juices MCP Server."""

import pytest
from src.tools import get_creative_spark, CreativeSparkParams

@pytest.mark.asyncio
async def test_get_creative_spark_default():
    """Test with default parameters."""
    params = CreativeSparkParams()
    result = await get_creative_spark(params)
    
    assert len(result.pairs) == 2
    assert all("-" in pair for pair in result.pairs)
    assert result.instruction
    assert result.prompt

@pytest.mark.asyncio
async def test_get_creative_spark_intensity_levels():
    """Test all intensity levels."""
    for intensity in ["mild", "wild", "chaos"]:
        params = CreativeSparkParams(count=1, intensity=intensity)
        result = await get_creative_spark(params)
        
        assert len(result.pairs) == 1
        assert "-" in result.pairs[0]

@pytest.mark.asyncio  
async def test_get_creative_spark_max_count():
    """Test maximum pair count."""
    params = CreativeSparkParams(count=5)
    result = await get_creative_spark(params)
    
    assert len(result.pairs) == 5
```

### creative-juices.dxt

```json
{
  "name": "creative-juices",
  "version": "1.0.0",
  "description": "Random verb-noun combinations for creative thinking",
  "type": "python",
  "main": "src/server.py",
  "requirements": "requirements.txt",
  "python": "3.12",
  "commands": {
    "start": "python -m src.server"
  },
  "tools": [
    {
      "name": "get_creative_spark",
      "description": "Generate creative verb-noun combinations"
    }
  ],
  "prompts": [
    {
      "name": "creative_reframe",
      "description": "Prompt for applying creative thinking"
    }
  ]
}
```

### requirements.txt

```
fastmcp>=0.1.0
pydantic>=2.0
```

## Usage Examples

### Scenario 1: Product Design
**User**: "Help me design a better todo app"  
**Response**: 
- Pairs: `["dissolve-mountain", "whisper-machinery"]`
- Instruction: "Use these unexpected combinations as lenses to radically reframe:"
- Prompt: "What if your solution could dissolve the mountain?"
- **Outcome**: Leads to concept of breaking down overwhelming tasks into micro-actions

### Scenario 2: Creative Writing  
**User**: "I need unique sci-fi concepts"  
**Response**:
- Pairs: `["ferment-constellation", "crystallize-memory"]`  
- Instruction: "Use these unexpected combinations as lenses to radically reframe:"
- Prompt: "What if your solution could ferment the constellation?"
- **Outcome**: Sparks ideas about civilizations that grow across star systems like cultures

### Scenario 3: Business Strategy
**User**: "How can we differentiate our service?"  
**Response**:
- Pairs: `["liquefy-paradigm", "sublime-nexus"]` (chaos mode)
- Instruction: "Let these surreal pairings shatter your assumptions:"  
- Prompt: "What if your solution could liquefy the paradigm?"
- **Outcome**: Inspires fluid, adaptive business model concepts

## Running Locally

```bash
# Basic execution
python -m src.server

# Testing with MCP Inspector (recommended)
mcp-inspector python -m src.server

# Run examples
python examples/pydantic_ai_demo.py

# Run tests
pytest tests/
```

## Security Considerations

- **Data Handling**: No user data stored or transmitted
- **Permissions**: Read-only access to internal word lists
- **No External APIs**: Completely offline operation
- **No Authentication**: Tool is stateless and requires no credentials

## Notes

- Intentionally minimal - no configuration files, no database, no external APIs
- Word lists are curated for maximum creative impact
- Can be extended by adding more intensity levels or word categories
- Designed for immediate value with zero setup