"""
Claude Agent SDK client wrapper for Valor.

This module provides a wrapper around ClaudeSDKClient configured for Valor's use case:
- Loads system prompt from SOUL.md
- Configures permission mode for autonomous operation
- Handles session management
- Extracts text response from message stream
"""

import logging
import os
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

logger = logging.getLogger(__name__)

# Path to SOUL.md system prompt
SOUL_PATH = Path(__file__).parent.parent / "config" / "SOUL.md"


def load_completion_criteria() -> str:
    """Load completion criteria from CLAUDE.md."""
    claude_md = Path(__file__).parent.parent / "CLAUDE.md"
    if not claude_md.exists():
        return ""

    import re
    content = claude_md.read_text()
    match = re.search(
        r'## Work Completion Criteria\n\n(.*?)(?=\n## |\Z)',
        content,
        re.DOTALL
    )
    return match.group(0) if match else ""


def load_system_prompt() -> str:
    """Load Valor's system prompt from SOUL.md with completion criteria."""
    soul_prompt = ""
    if SOUL_PATH.exists():
        soul_prompt = SOUL_PATH.read_text()
    else:
        logger.warning(f"SOUL.md not found at {SOUL_PATH}, using default prompt")
        soul_prompt = "You are Valor, an AI coworker. Be direct, concise, and helpful."

    # Append completion criteria
    criteria = load_completion_criteria()
    if criteria:
        soul_prompt += f"\n\n---\n\n{criteria}"

    return soul_prompt


class ValorAgent:
    """
    Valor's Claude Agent SDK wrapper.

    Provides a simplified interface for sending messages and receiving responses
    using the Claude Agent SDK with Valor's configuration.

    Permission mode is set to "bypassPermissions" (YOLO mode) - Valor has full
    system access with no approval gates.
    """

    def __init__(
        self,
        working_dir: str | Path | None = None,
        system_prompt: str | None = None,
        permission_mode: str = "bypassPermissions",
    ):
        """
        Initialize ValorAgent.

        Args:
            working_dir: Working directory for the agent. Defaults to ai/ repo root.
            system_prompt: Custom system prompt. Defaults to SOUL.md contents.
            permission_mode: Permission mode for tool use. Defaults to "bypassPermissions" (YOLO mode).
        """
        self.working_dir = Path(working_dir) if working_dir else Path(__file__).parent.parent
        self.system_prompt = system_prompt or load_system_prompt()
        self.permission_mode = permission_mode

    def _create_options(self, session_id: str | None = None) -> ClaudeAgentOptions:
        """Create ClaudeAgentOptions configured for Valor with full permissions."""
        return ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            cwd=str(self.working_dir),
            permission_mode=self.permission_mode,  # type: ignore[arg-type]
            # Use continue_conversation for session continuity
            continue_conversation=session_id is not None,
            resume=session_id,
            # Inherit MCP servers and settings from Claude Code's config
            setting_sources=["local", "project"],
            # Environment variables for API access
            env={
                "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            },
        )

    async def query(self, message: str, session_id: str | None = None, max_retries: int = 2) -> str:
        """
        Send a message and get a response. On error, feeds the error back
        to the agent so it can attempt a different approach.

        For file-related errors (invalid PDF, corrupted files), instructs the
        agent to avoid reading the problematic file and work with text context only.

        Args:
            message: The user message to send
            session_id: Optional session ID for conversation continuity
            max_retries: Max times to retry by feeding error back to agent

        Returns:
            The assistant's text response
        """
        options = self._create_options(session_id)
        response_parts: list[str] = []
        retries = 0

        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(message)

                while True:
                    async for msg in client.receive_response():
                        if isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    response_parts.append(block.text)
                        elif isinstance(msg, ResultMessage):
                            if msg.total_cost_usd is not None:
                                logger.debug(
                                    f"Query completed: {msg.num_turns} turns, "
                                    f"${msg.total_cost_usd:.4f}, "
                                    f"{msg.duration_ms}ms"
                                )
                            if msg.is_error and retries < max_retries:
                                retries += 1
                                error_text = msg.result or "(empty)"
                                recovery_msg = _build_error_recovery_message(
                                    error_text
                                )
                                logger.warning(
                                    f"Agent error (attempt {retries}/{max_retries}), "
                                    f"feeding error back: {error_text}"
                                )
                                response_parts.clear()
                                await client.query(recovery_msg)
                                break  # Re-enter receive_response() loop
                            elif msg.is_error:
                                logger.error(
                                    f"Agent error after {retries} retries: "
                                    f"{msg.result}"
                                )
                    else:
                        # async for completed without break — done
                        break

        except Exception as e:
            logger.error(f"SDK query failed: {e}")
            raise

        return "\n".join(response_parts) if response_parts else ""


# Patterns that indicate file/media-related API errors
_FILE_ERROR_PATTERNS = [
    "pdf",
    "image",
    "base64",
    "file",
    "media_type",
    "not valid",
    "could not process",
    "invalid_request_error",
]


def _is_file_related_error(error_text: str) -> bool:
    """Check if an error is related to file/media processing."""
    error_lower = error_text.lower()
    return any(pattern in error_lower for pattern in _FILE_ERROR_PATTERNS)


def _build_error_recovery_message(error_text: str) -> str:
    """
    Build an appropriate recovery message based on the error type.

    For file-related errors, instructs the agent to avoid reading problematic files.
    For other errors, uses the generic retry approach.
    """
    if _is_file_related_error(error_text):
        return (
            f"That failed with a file-related error:\n{error_text}\n\n"
            f"IMPORTANT: Do NOT attempt to read any PDF, image, or binary files from "
            f"the data/media/ directory. These files may be corrupted or invalid. "
            f"Work only with the text context provided in the conversation. "
            f"If you need file contents, they have already been extracted as text "
            f"in the message above. Please respond to the user's request using "
            f"only the text context available."
        )
    return (
        f"That failed with this error:\n{error_text}\n\n"
        f"Please try a different approach to accomplish the original task."
    )


async def get_agent_response_sdk(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
) -> str:
    """
    Get agent response using Claude Agent SDK.

    This function matches the signature of the existing get_agent_response()
    in telegram_bridge.py to enable seamless switching via feature flag.

    Args:
        message: The message to process (already enriched with context)
        session_id: Session ID for conversation continuity
        sender_name: Name of the sender (for logging)
        chat_title: Chat title (for logging)
        project: Project configuration dict
        chat_id: Chat ID (unused, for compatibility)

    Returns:
        The assistant's response text
    """
    import time

    start_time = time.time()
    request_id = f"{session_id}_{int(start_time)}"

    # Determine working directory from project config
    if project:
        working_dir = project.get("working_directory")
    else:
        working_dir = None

    # Fall back to ai/ repo root
    if not working_dir:
        working_dir = str(Path(__file__).parent.parent)

    project_name = project.get("name", "Valor") if project else "Valor"
    logger.info(f"[{request_id}] SDK query for {project_name}")
    logger.debug(f"[{request_id}] Working directory: {working_dir}")

    try:
        agent = ValorAgent(working_dir=working_dir)
        response = await agent.query(message, session_id=session_id)

        elapsed = time.time() - start_time
        logger.info(f"[{request_id}] SDK responded in {elapsed:.1f}s ({len(response)} chars)")

        return response

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[{request_id}] SDK error after {elapsed:.1f}s: {e}")
        return "Sorry, I ran into an issue and couldn't recover. The error has been logged for investigation."
