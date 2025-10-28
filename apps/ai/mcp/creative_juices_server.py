"""
Creative Juices MCP Server implementation using FastMCP.

Provides randomness tools to encourage out-of-the-box thinking.
"""

import logging
import random

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
    ],
}

# Flatten all questions for random selection
ALL_MUSK_QUESTIONS = [q for category in MUSK_QUESTIONS.values() for q in category]


@mcp.tool()
async def get_inspiration() -> dict:
    """
    Use at the start of creative or problem-solving tasks to frame challenges
    in unexpected ways. Helpful when you need to think outside the box from
    the beginning and want unconventional starting points.
    """
    from .creative_juices_words import VERBS, NOUNS

    verb_list = VERBS["inspiring"]
    noun_list = NOUNS["inspiring"]

    pairs = []
    for _ in range(3):
        verb = random.choice(verb_list)
        noun = random.choice(noun_list)
        pairs.append(f"{verb}-{noun}")

    return {
        "sparks": pairs,
        "instruction": "Use these unexpected combinations as initial lenses:",
    }


@mcp.tool()
async def think_outside_the_box() -> dict:
    """
    Use mid-conversation when exploration has stalled or thinking has become
    too linear. Helpful when you need to break out of convergent patterns and
    force radical divergence from your current approach.
    """
    from .creative_juices_words import VERBS, NOUNS

    verb_list = VERBS["out_of_the_box"]
    noun_list = NOUNS["out_of_the_box"]

    pairs = []
    for _ in range(3):
        verb = random.choice(verb_list)
        noun = random.choice(noun_list)
        pairs.append(f"{verb}-{noun}")

    return {"sparks": pairs, "instruction": "Shatter your assumptions with these:"}


@mcp.tool()
async def reality_check() -> dict:
    """
    Use to ground creative thinking in reality while maintaining openness.
    Helpful when wild ideas need pressure-testing against constraints, or when
    you need to validate assumptions and identify what actually matters.
    """
    # Get one random question from each framework
    questions = []
    frameworks = []

    for framework_name, framework_questions in MUSK_QUESTIONS.items():
        question = random.choice(framework_questions)
        questions.append(question)
        frameworks.append(framework_name)

    return {
        "questions": questions,
        "frameworks": frameworks,
        "instruction": "Ground your thinking with one question from each Musk framework:",
    }


def main():
    """Main entry point for the MCP server.

    Supports two modes:
    - stdio (default): For local development and testing
    - streamable-http: For production hosting at ai.yuda.me

    Set MCP_TRANSPORT environment variable to switch modes.
    """
    import os

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "streamable-http":
        # Production mode - HTTP transport for hosting at ai.yuda.me
        # This will be run as a separate service on Render
        logger.info("Starting Creative Juices MCP server in HTTP mode")
        mcp.run(transport="streamable-http")
    else:
        # Development mode - stdio transport
        logger.info("Starting Creative Juices MCP server in stdio mode")
        mcp.run()


if __name__ == "__main__":
    main()
