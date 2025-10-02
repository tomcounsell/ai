"""
Creative Juices MCP Server implementation using FastMCP.
"""

import asyncio
import logging
import os
import random
import sys

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("Creative Juices MCP")

# Creative prompt for the prompt feature
CREATIVE_PROMPT = """You have access to the Creative Juices tool. Use it to generate unexpected verb-noun combinations that can help reframe problems and spark creative thinking.

The tool offers three intensity levels:
- "mild": Gentle, accessible combinations
- "wild": Unexpected, thought-provoking pairs (default)
- "chaos": Surreal, assumption-shattering combinations

When you receive a creative challenge or problem to solve:
1. Use get_creative_spark with the appropriate intensity
2. Present the combinations as reframing lenses
3. Help explore how each pairing might reveal new perspectives
4. Encourage the user to build on the most promising angles

Remember: These aren't solutions - they're catalysts for breaking mental patterns."""


@mcp.tool()
async def get_creative_spark(count: int = 2, intensity: str = "wild") -> dict:
    """
    Generate random verb-noun combinations for creative thinking.

    Args:
        count: Number of combinations to generate (1-5, default: 2)
        intensity: Intensity level - "mild", "wild", or "chaos" (default: "wild")

    Returns:
        Dictionary with pairs, instruction, and a prompt suggestion
    """
    from .creative_juices_words import VERBS, NOUNS

    # Validate parameters
    count = max(1, min(5, count))

    if intensity not in ["mild", "wild", "chaos"]:
        intensity = "wild"

    # Select words based on intensity
    verb_list = VERBS[intensity]
    noun_list = NOUNS[intensity]

    # Generate pairs
    pairs = []
    for _ in range(count):
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
    first_pair = pairs[0].replace('-', ' the ')
    prompt = f"What if your solution could {first_pair}?"

    return {
        "pairs": pairs,
        "instruction": instructions[intensity],
        "prompt": prompt
    }


@mcp.prompt()
async def creative_reframe() -> str:
    """Prompt for applying creative thinking techniques."""
    return CREATIVE_PROMPT


async def main():
    """Main entry point for the MCP server."""
    # Add project root to path for Django setup
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    sys.path.insert(0, project_root)

    # Setup Django (if needed for word lists from database)
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
        import django
        django.setup()
    except Exception as e:
        logger.warning(f"Django setup skipped: {e}")

    # Run the MCP server
    await mcp.run()


if __name__ == "__main__":
    asyncio.run(main())
