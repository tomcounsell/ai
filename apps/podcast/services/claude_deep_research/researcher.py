"""Stage 2: Sonnet researcher subagent with WebSearchTool and fetch_page."""

import logging
from pathlib import Path

import httpx
from html2text import html2text
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, WebSearchTool

logger = logging.getLogger(__name__)


# --- Output schema ---


class SubagentFindings(BaseModel):
    focus: str
    findings: str  # detailed research text
    sources: list[str]  # URLs cited
    key_data_points: list[str]  # specific facts, stats, quotes
    confidence: str  # high/medium/low
    gaps_identified: list[str]  # what couldn't be found


# --- Agent factory ---

_PROMPT_FILE = Path(__file__).parent / "prompts" / "researcher.md"
_SYSTEM_PROMPT = _PROMPT_FILE.read_text()


def _create_researcher_agent(
    max_searches: int = 10,
    allowed_domains: list[str] | None = None,
) -> Agent:
    """Create a researcher agent with configured WebSearchTool.

    Separate function because allowed_domains may vary per subtask
    based on the planner's output.
    """
    web_search = WebSearchTool(
        max_uses=max_searches,
        allowed_domains=allowed_domains if allowed_domains else None,
    )

    agent = Agent(
        "anthropic:claude-sonnet-4-6",
        output_type=SubagentFindings,
        system_prompt=_SYSTEM_PROMPT,
        builtin_tools=[web_search],
        defer_model_check=True,
        retries=3,
    )

    @agent.tool
    async def fetch_page(ctx: RunContext, url: str) -> str:
        """Fetch the full text content of a web page.

        Use this after web_search to get detailed content from
        a promising URL. Returns plain text extracted from HTML.
        """
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
            text = html2text(resp.text)
            # Truncate to avoid context window pressure
            return text[:8000]
        except Exception as e:
            return f"Failed to fetch {url}: {e}"

    return agent


# --- Public interface ---


def research_subtask(
    focus: str,
    search_strategy: str,
    key_questions: list[str],
    allowed_domains: list[str] | None = None,
) -> SubagentFindings:
    """Run a single researcher subagent on one subtask.

    Args:
        focus: What this subagent should investigate.
        search_strategy: Suggested search approach.
        key_questions: 3-5 specific questions to answer.
        allowed_domains: Optional domain hints for WebSearchTool.

    Returns:
        SubagentFindings with research results.
    """
    agent = _create_researcher_agent(
        max_searches=10,
        allowed_domains=allowed_domains or None,
    )

    prompt = (
        f"Research focus: {focus}\n\n"
        f"Key questions:\n"
        + "\n".join(f"- {q}" for q in key_questions)
        + f"\n\nSearch strategy: {search_strategy}"
    )

    result = agent.run_sync(prompt)
    logger.info(
        "research_subtask: focus='%s' model=%s input_tokens=%d output_tokens=%d",
        focus[:50],
        agent.model,
        result.usage().input_tokens,
        result.usage().output_tokens,
    )
    return result.output
