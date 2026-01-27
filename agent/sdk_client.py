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
    """

    # Pre-approved operations - Valor has YOLO mode (full system access)
    ALLOWED_PROMPTS = [
        # Git operations - full autonomy
        {"tool": "Bash", "prompt": "git operations"},
        {"tool": "Bash", "prompt": "git commit"},
        {"tool": "Bash", "prompt": "git push"},
        {"tool": "Bash", "prompt": "git pull"},
        {"tool": "Bash", "prompt": "git checkout"},
        {"tool": "Bash", "prompt": "git branch"},
        {"tool": "Bash", "prompt": "git merge"},
        {"tool": "Bash", "prompt": "git rebase"},
        {"tool": "Bash", "prompt": "git stash"},
        {"tool": "Bash", "prompt": "gh operations"},
        # Development commands
        {"tool": "Bash", "prompt": "run tests"},
        {"tool": "Bash", "prompt": "run pytest"},
        {"tool": "Bash", "prompt": "run linting"},
        {"tool": "Bash", "prompt": "run formatting"},
        {"tool": "Bash", "prompt": "install dependencies"},
        {"tool": "Bash", "prompt": "build project"},
        # System operations
        {"tool": "Bash", "prompt": "file operations"},
        {"tool": "Bash", "prompt": "process management"},
        {"tool": "Bash", "prompt": "service management"},
        {"tool": "Bash", "prompt": "script execution"},
    ]

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
            # Pre-approved operations for autonomous execution
            allowed_prompts=self.ALLOWED_PROMPTS,
            # Use continue_conversation for session continuity
            continue_conversation=session_id is not None,
            resume=session_id,
            # Environment variables for API access
            env={
                "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
            },
        )

    async def query(self, message: str, session_id: str | None = None) -> str:
        """
        Send a message and get a response.

        Args:
            message: The user message to send
            session_id: Optional session ID for conversation continuity

        Returns:
            The assistant's text response
        """
        options = self._create_options(session_id)
        response_parts: list[str] = []

        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(message)

                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                response_parts.append(block.text)
                    elif isinstance(msg, ResultMessage):
                        # Log usage info
                        if msg.total_cost_usd is not None:
                            logger.debug(
                                f"Query completed: {msg.num_turns} turns, "
                                f"${msg.total_cost_usd:.4f}, "
                                f"{msg.duration_ms}ms"
                            )
                        if msg.is_error:
                            logger.error(f"Agent returned error: {msg.result}")
                            if msg.result:
                                return f"Error: {msg.result}"

        except Exception as e:
            logger.error(f"SDK query failed: {e}")
            raise

        return "\n".join(response_parts) if response_parts else ""


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
        return f"Error: {str(e)}"
