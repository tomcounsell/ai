"""
Creative Juices MCP Server implementation using FastMCP.

Provides randomness tools to encourage out-of-the-box thinking.
"""

import logging
import os
import random
import sys

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("Creative Juices")

# Elon-style reality check questions
REALITY_QUESTIONS = [
    "What are the first principles here?",
    "What's the physics limit?",
    "What would 10x scale require?",
    "What if you removed this constraint entirely?",
    "What's the simplest version that could possibly work?",
    "What's actually impossible vs just hard?",
    "How would you test this core assumption?",
    "What's the limiting factor?",
    "What would a child's solution be?",
    "If you had unlimited resources, what changes?",
    "What can't change? (physics, human nature, etc.)",
    "What's the 80/20 here?",
    "What breaks first at scale?",
    "What would make this 10x cheaper?",
    "What would make this 10x faster?",
    "What's the obvious solution you're avoiding?",
    "What if the constraint is the solution?",
    "What happens at the limit case?",
    "What would Feynman say is actually happening here?",
    "What's the dumbest way to solve this that might work?",
]


@mcp.tool()
async def ignite(count: int = 3) -> dict:
    """
    Generate random conceptual sparks for starting fresh problem-solving.

    Use this at the beginning of any creative or problem-solving task where
    out-of-the-box thinking would be valuable. Returns random verb-noun
    combinations to frame the challenge in unexpected ways.

    Args:
        count: Number of random sparks (1-5, default: 3)

    Returns:
        Random verb-noun pairs with intensity-based framing
    """
    from .creative_juices_words import VERBS, NOUNS

    count = max(1, min(5, count))

    # Use "wild" intensity for initial sparks
    verb_list = VERBS["wild"]
    noun_list = NOUNS["wild"]

    pairs = []
    for _ in range(count):
        verb = random.choice(verb_list)
        noun = random.choice(noun_list)
        pairs.append(f"{verb}-{noun}")

    return {
        "sparks": pairs,
        "instruction": "Use these unexpected combinations as initial lenses:"
    }


@mcp.tool()
async def scatter(count: int = 3, intensity: str = "chaos") -> dict:
    """
    Generate random divergent sparks when exploration has stalled.

    Use this mid-conversation when you've been working on something and need
    to break out of linear/convergent thinking. Returns random verb-noun pairs
    at higher intensity to force radical divergence.

    Args:
        count: Number of random sparks (1-5, default: 3)
        intensity: "wild" or "chaos" (default: "chaos")

    Returns:
        Random verb-noun pairs with high-intensity framing
    """
    from .creative_juices_words import VERBS, NOUNS

    count = max(1, min(5, count))
    intensity = "chaos" if intensity not in ["wild", "chaos"] else intensity

    verb_list = VERBS[intensity]
    noun_list = NOUNS[intensity]

    pairs = []
    for _ in range(count):
        verb = random.choice(verb_list)
        noun = random.choice(noun_list)
        pairs.append(f"{verb}-{noun}")

    instructions = {
        "wild": "Break out of your current path with these:",
        "chaos": "Shatter your assumptions with these:"
    }

    return {
        "sparks": pairs,
        "instruction": instructions[intensity]
    }


@mcp.tool()
async def focus(count: int = 2) -> dict:
    """
    Generate random first-principles and limit-case questions.

    Use this to ground creative thinking in reality while maintaining openness.
    Returns random questions inspired by Elon Musk's approach: first principles,
    physics limits, constraint removal, scaling, and simplification.

    Args:
        count: Number of random questions (1-5, default: 2)

    Returns:
        Random reality-checking questions
    """
    count = max(1, min(5, count))

    questions = random.sample(REALITY_QUESTIONS, count)

    return {
        "questions": questions,
        "instruction": "Ground your thinking with these first-principles questions:"
    }


def main():
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

    # Run the MCP server (starts event loop internally)
    mcp.run()


if __name__ == "__main__":
    main()
