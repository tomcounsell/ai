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

# Elon Musk's thinking frameworks as questions
MUSK_QUESTIONS = {
    # First Principles Thinking - strip to fundamental truths
    "first_principles": [
        "What are the absolute truths here, known by physics?",
        "What assumptions can you remove completely?",
        "What's expensive: the materials or the manufacturing?",
        "If everyone agrees, what are they all wrong about?",
        "What's the foundational truth underneath this problem?",
        "Strip away all bias and conjecture - what remains?",
    ],

    # Think in the Limit - scale to extremes
    "limit_thinking": [
        "What happens at 1 unit vs 1 million units?",
        "If this scaled to billions, what breaks first?",
        "What if it was 1000x smaller? 1000x larger?",
        "At minimum viable scale, does the problem still exist?",
        "At infinite scale, what's the limiting factor?",
        "If you had just one customer vs a billion, what changes?",
    ],

    # Platonic Ideal - perfect solution first
    "platonic_ideal": [
        "What does the perfect version of this look like?",
        "Ignoring your current skills, what's the ideal solution?",
        "If you designed this from scratch today, what would it be?",
        "What would the perfect [product/service/process] do?",
        "Work backwards from perfection - what do you need?",
        "What's the ideal outcome, unconstrained by reality?",
    ],

    # Five-Step Optimization - question, delete, optimize, accelerate, automate
    "optimization": [
        "Question: Are your requirements dumb? Does this even matter?",
        "Delete: What can you remove? Are you adding things 'just in case'?",
        "If you're not adding back 10% of deletions, did you delete enough?",
        "What shouldn't exist at all that you're trying to optimize?",
        "What steps can be eliminated entirely?",
        "What are you optimizing that doesn't need to exist?",
    ]
}

# Flatten all questions for random selection
ALL_MUSK_QUESTIONS = [q for category in MUSK_QUESTIONS.values() for q in category]


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
async def focus(count: int = 2, framework: str = "random") -> dict:
    """
    Generate random questions from Elon Musk's thinking frameworks.

    Use this to ground creative thinking in reality while maintaining openness.
    Based on Musk's actual methods: first principles thinking, limit case analysis,
    platonic ideal visualization, and five-step optimization (question → delete → optimize).

    Args:
        count: Number of random questions (1-5, default: 2)
        framework: "random", "first_principles", "limit_thinking", "platonic_ideal", or "optimization"

    Returns:
        Random reality-grounding questions from Musk's frameworks
    """
    count = max(1, min(5, count))

    if framework == "random" or framework not in MUSK_QUESTIONS:
        # Random questions from all frameworks
        questions = random.sample(ALL_MUSK_QUESTIONS, count)
    else:
        # Questions from specific framework
        framework_questions = MUSK_QUESTIONS[framework]
        questions = random.sample(framework_questions, min(count, len(framework_questions)))

    return {
        "questions": questions,
        "instruction": "Ground your thinking with Musk-style reality checks:",
        "framework": framework
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
