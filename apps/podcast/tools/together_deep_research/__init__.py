"""Together Open Deep Research - Iterative multi-hop research via LangGraph.

Uses the Open Deep Research framework to conduct iterative multi-hop research
with source quality filtering and synthesis.

Usage as library::

    from apps.podcast.tools.together_deep_research import run_together_research

    content, metadata = run_together_research(prompt="Your research query")

Usage as CLI::

    python -m apps.podcast.tools.together_deep_research "Your research query"

Requirements:
    - ANTHROPIC_API_KEY or OPENROUTER_API_KEY or OPENAI_API_KEY
    - TAVILY_API_KEY (get at https://tavily.com/)
    - uv sync --extra together-research

Documentation:
    https://github.com/langchain-ai/open-deep-research
"""

from .config import env_for_library, get_api_keys, make_logger, resolve_provider
from .runner import run_together_research

__all__ = [
    "run_together_research",
    "env_for_library",
    "get_api_keys",
    "make_logger",
    "resolve_provider",
]
