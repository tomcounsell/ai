"""Core research runner for Open Deep Research via LangGraph."""

from __future__ import annotations

import time
import traceback
from datetime import datetime

from .config import env_for_library, get_api_keys, make_logger, resolve_provider

# Try to load dotenv, but don't fail if not installed
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


async def _run_graph(prompt: str, config: dict, log) -> dict:
    """Execute the LangGraph research pipeline (async).

    Phase 1 generates a report plan and pauses at the human_feedback
    interrupt.  Phase 2 auto-approves and writes all sections.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command
    from open_deep_research.graph import builder

    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    log(f"\n[{time.strftime('%H:%M:%S')}] Generating report plan...")
    await graph.ainvoke({"topic": prompt}, config=config)

    log(
        f"[{time.strftime('%H:%M:%S')}] Plan generated,"
        " auto-approving and writing sections..."
    )
    return await graph.ainvoke(Command(resume=True), config=config)


def run_together_research(
    prompt: str,
    max_search_depth: int = 2,
    number_of_queries: int = 2,
    planner_provider: str | None = None,
    planner_model: str | None = None,
    writer_provider: str | None = None,
    writer_model: str | None = None,
    timeout: int = 900,
    verbose: bool = False,
    log_file: str | None = None,
) -> tuple[str | None, dict]:
    """Run Open Deep Research and return (content, metadata).

    Uses a LangGraph-based agentic loop that plans sections, searches,
    evaluates, and re-searches until coverage is sufficient.

    This is the synchronous entry point.  Uses ``asgiref.async_to_sync``
    when available (Django context) and falls back to ``asyncio.run``
    for CLI usage.

    Args:
        prompt: Research prompt/query (becomes the report topic).
        max_search_depth: Max reflection + search iterations per section.
        number_of_queries: Number of search queries per iteration.
        planner_provider: LLM provider for planning (auto-detected if None).
        planner_model: LLM model for planning (auto-detected if None).
        writer_provider: LLM provider for writing (auto-detected if None).
        writer_model: LLM model for writing (auto-detected if None).
        timeout: Maximum time in seconds before aborting.
        verbose: Whether to print progress messages.
        log_file: Optional path to write log output.

    Returns:
        Tuple of (report_text, metadata_dict). report_text is None on failure.
    """
    log = make_logger(verbose, log_file)

    # Validate API keys
    keys = get_api_keys()

    has_llm_key = any(
        keys.get(k)
        for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"]
    )
    if not has_llm_key:
        log("=" * 60)
        log("ERROR: No LLM API key found")
        log("=" * 60)
        log("\nNeed one of:")
        log("  ANTHROPIC_API_KEY  (default, recommended)")
        log("  OPENAI_API_KEY")
        log("  OPENROUTER_API_KEY (fallback)")
        log("\nFix: Add to ~/.env or .env.local")
        log("=" * 60)
        return None, {}

    if not keys.get("TAVILY_API_KEY"):
        log("=" * 60)
        log("ERROR: TAVILY_API_KEY not found")
        log("=" * 60)
        log("\nOpen Deep Research requires Tavily for web search.")
        log("\nFix: Add to ~/.env or .env.local:")
        log("  TAVILY_API_KEY=tvly-...")
        log("\nGet FREE key at: https://tavily.com/")
        log("=" * 60)
        return None, {}

    # Resolve provider/model if not explicitly set
    auto_provider, auto_model = resolve_provider(keys)
    planner_provider = planner_provider or auto_provider
    planner_model = planner_model or auto_model
    writer_provider = writer_provider or auto_provider
    writer_model = writer_model or auto_model

    log("=" * 60)
    log(f"OPEN DEEP RESEARCH (via {planner_provider})")
    log("=" * 60)
    prompt_display = (
        f"Prompt: {prompt[:200]}..." if len(prompt) > 200 else f"Prompt: {prompt}"
    )
    log("")
    log(prompt_display)
    log("")
    log("Configuration:")
    log(f"  Planner: {planner_provider}/{planner_model}")
    log(f"  Writer: {writer_provider}/{writer_model}")
    log(f"  Max search depth: {max_search_depth}")
    log(f"  Queries per iteration: {number_of_queries}")
    log(f"  Timeout: {timeout}s ({timeout // 60} minutes)")
    log("")
    log("Starting iterative research...")
    log("-" * 60)

    # Lazy import check - open_deep_research is an optional dependency
    try:
        from open_deep_research.graph import builder  # noqa: F401
    except ImportError:
        log("\nERROR: open-deep-research not installed")
        log("Install with: uv sync --extra together-research")
        return None, {}

    metadata: dict = {
        "timestamp": datetime.now().isoformat(),
        "tool": "open-deep-research",
        "planner_provider": planner_provider,
        "planner_model": planner_model,
        "writer_provider": writer_provider,
        "writer_model": writer_model,
        "max_search_depth": max_search_depth,
        "number_of_queries": number_of_queries,
    }

    start_time = time.time()

    thread_id = f"odr-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    config = {
        "configurable": {
            "thread_id": thread_id,
            "planner_provider": planner_provider,
            "planner_model": planner_model,
            "writer_provider": writer_provider,
            "writer_model": writer_model,
            "max_search_depth": max_search_depth,
            "number_of_queries": number_of_queries,
            "search_api": "tavily",
        }
    }

    try:
        # Scope env var mutations to the library call only
        with env_for_library(keys):
            # Use async_to_sync in Django context; fall back to asyncio.run for CLI
            try:
                from asgiref.sync import async_to_sync

                result = async_to_sync(_run_graph)(prompt, config, log)
            except ImportError:
                import asyncio

                result = asyncio.run(_run_graph(prompt, config, log))

        elapsed = int(time.time() - start_time)
        metadata["elapsed_seconds"] = elapsed

        answer = result.get("final_report", "")

        if answer:
            word_count = len(answer.split())
            metadata["word_count"] = word_count

            log(f"\n{'=' * 60}")
            log(f"RESEARCH COMPLETE (took {elapsed}s)")
            log(f"Length: ~{word_count} words")
            log(f"{'=' * 60}\n")

            return answer, metadata
        else:
            log(
                f"\nWARNING: Research completed but returned empty result"
                f" (took {elapsed}s)"
            )
            metadata["error"] = "Empty result returned"
            return None, metadata

    except TimeoutError:
        elapsed = int(time.time() - start_time)
        metadata["elapsed_seconds"] = elapsed
        metadata["error"] = f"Timed out after {elapsed}s"
        log(f"\nERROR: Research timed out after {elapsed}s")
        log("Try reducing --max-search-depth or simplifying the prompt")
        return None, metadata

    except Exception as e:
        elapsed = int(time.time() - start_time)
        metadata["elapsed_seconds"] = elapsed
        metadata["error"] = str(e)
        log(f"\nERROR: Research failed after {elapsed}s: {e}")
        if verbose:
            traceback.print_exc()
        return None, metadata
