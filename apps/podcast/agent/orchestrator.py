"""Anthropic Agent SDK orchestrator for podcast episode production.

Provides a ``run_episode`` entry point that starts or resumes the 12-phase
podcast production workflow for a given episode.  The orchestrator connects
the service-layer tools defined in :mod:`apps.podcast.agent.tools` to an
Anthropic Claude model via the Messages API with tool_use.

Usage::

    from apps.podcast.agent.orchestrator import run_episode

    # Start or resume production for episode with pk=42
    result = run_episode(episode_id=42)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anthropic

from apps.podcast.agent.tools import get_tool_by_name, to_anthropic_tool_schemas, tools

logger = logging.getLogger(__name__)

# The system prompt lives alongside this module
_SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

# Default model for the orchestrator agent
DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Maximum agentic loop iterations to prevent runaway execution
MAX_ITERATIONS = 50


def _load_system_prompt() -> str:
    """Read the system prompt from the markdown file."""
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _execute_tool(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Execute a tool by name with the given input and return JSON result.

    Looks up the tool definition, calls the underlying service function,
    and serialises the result to a JSON string suitable for returning to
    the Anthropic API as a ``tool_result`` content block.
    """
    tool_def = get_tool_by_name(tool_name)
    if tool_def is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    fn = tool_def["fn"]

    try:
        result = fn(**tool_input)
    except Exception as exc:
        logger.exception("Tool %s raised an exception", tool_name)
        return json.dumps(
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )

    # Serialise the result to JSON.  Django model instances are not
    # JSON-serialisable by default, so we convert them to a summary dict.
    return _serialise_result(result)


def _serialise_result(result: Any) -> str:
    """Convert a tool return value to a JSON string."""
    if result is None:
        return json.dumps({"result": None})

    # dict / list / str / int / float / bool -- already serialisable
    if isinstance(result, (dict, list, str, int, float, bool)):
        return json.dumps({"result": result}, default=str)

    # Django model instances -- return a useful summary
    if hasattr(result, "pk"):
        summary: dict[str, Any] = {"pk": result.pk}
        # Include common fields if present
        for field in (
            "title",
            "name",
            "status",
            "current_step",
            "content",
            "description",
            "audio_url",
            "transcript",
            "published_at",
        ):
            val = getattr(result, field, None)
            if val is not None:
                # Truncate very long text fields for the agent's context
                if isinstance(val, str) and len(val) > 5000:
                    summary[field] = val[:5000] + "... (truncated)"
                else:
                    summary[field] = val
        return json.dumps({"result": summary}, default=str)

    # Fallback: repr
    return json.dumps({"result": str(result)})


def run_episode(
    episode_id: int,
    *,
    model: str = DEFAULT_MODEL,
    max_iterations: int = MAX_ITERATIONS,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Start or resume podcast episode production.

    This is the main entry point.  It:

    1. Loads the system prompt and tool schemas.
    2. Sends an initial message asking the agent to produce the episode.
    3. Enters an agentic loop: when the model returns ``tool_use`` blocks,
       the corresponding service functions are executed and results are
       sent back.  The loop continues until the model produces a final
       text response (``end_turn``) or ``max_iterations`` is reached.

    Args:
        episode_id: Primary key of the Episode to produce.
        model: Anthropic model ID to use.  Defaults to Claude Sonnet.
        max_iterations: Safety limit on agentic loop iterations.
        api_key: Optional Anthropic API key.  If ``None``, the client
            reads from the ``ANTHROPIC_API_KEY`` environment variable.

    Returns:
        A dict with keys:

        - ``final_message``: The model's final text response.
        - ``iterations``: Number of loop iterations executed.
        - ``tool_calls``: List of tool names that were called.
        - ``episode_id``: The episode ID that was processed.
    """
    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = _load_system_prompt()
    tool_schemas = to_anthropic_tool_schemas()

    # Initial user message to kick off the workflow
    initial_message = (
        f"Produce podcast episode with ID {episode_id}. "
        f"Start by calling get_status to check the current workflow state, "
        f"then proceed through the production pipeline. "
        f"Call tools in the correct sequence, check quality gates, and "
        f"handle any errors. If the workflow is already in progress, "
        f"resume from the current step."
    )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": initial_message},
    ]

    tool_calls_log: list[str] = []
    iterations = 0
    final_text = ""

    logger.info(
        "run_episode: starting orchestrator for episode_id=%d model=%s",
        episode_id,
        model,
    )

    while iterations < max_iterations:
        iterations += 1

        logger.debug(
            "run_episode: iteration %d, messages=%d",
            iterations,
            len(messages),
        )

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=tool_schemas,
            messages=messages,
        )

        # Check if the model wants to use tools
        if response.stop_reason == "tool_use":
            # Append the assistant's response (contains tool_use blocks)
            messages.append({"role": "assistant", "content": response.content})

            # Process each tool_use block
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    tool_use_id = block.id

                    logger.info(
                        "run_episode: calling tool %s with %s",
                        tool_name,
                        json.dumps(tool_input, default=str),
                    )

                    tool_calls_log.append(tool_name)
                    result_json = _execute_tool(tool_name, tool_input)

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": result_json,
                        }
                    )

            # Send tool results back to the model
            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "end_turn":
            # Model produced a final response -- extract text
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            logger.info(
                "run_episode: completed after %d iterations, %d tool calls",
                iterations,
                len(tool_calls_log),
            )
            break

        else:
            # Unexpected stop reason
            logger.warning(
                "run_episode: unexpected stop_reason=%s at iteration %d",
                response.stop_reason,
                iterations,
            )
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            break

    else:
        logger.warning(
            "run_episode: hit max_iterations=%d for episode_id=%d",
            max_iterations,
            episode_id,
        )
        final_text = (
            f"Orchestrator reached maximum iterations ({max_iterations}). "
            f"The workflow may not be complete. Last tool calls: "
            f"{tool_calls_log[-5:]}"
        )

    # Persist the agent session ID on the workflow record if possible
    _save_session_metadata(episode_id, iterations, tool_calls_log)

    return {
        "final_message": final_text,
        "iterations": iterations,
        "tool_calls": tool_calls_log,
        "episode_id": episode_id,
    }


def _save_session_metadata(
    episode_id: int,
    iterations: int,
    tool_calls: list[str],
) -> None:
    """Best-effort update of the EpisodeWorkflow with session metadata."""
    try:
        from apps.podcast.models import EpisodeWorkflow

        wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
        # Store a summary in the agent_session_id field
        session_info = (
            f"orchestrator:iterations={iterations}," f"tools={len(tool_calls)}"
        )
        if len(session_info) <= 100:
            wf.agent_session_id = session_info
            wf.save(update_fields=["agent_session_id"])
    except Exception:
        logger.debug(
            "run_episode: could not save session metadata for episode %d",
            episode_id,
            exc_info=True,
        )
