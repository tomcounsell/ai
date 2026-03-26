"""Q&A handler for ChatSession direct responses.

When the intent classifier identifies a message as an informational query,
this module provides Q&A-specific instructions that replace the PM dispatch
block. The ChatSession answers directly using read-only tools without
spawning a DevSession.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Reduced nudge cap for Q&A sessions (vs 50 for normal sessions)
QA_MAX_NUDGE_COUNT = 10


def build_qa_instructions() -> str:
    """Build Q&A-specific instructions to replace PM dispatch block.

    These instructions guide the ChatSession to answer directly and
    conversationally, using only read-only tools.

    Returns:
        Instruction string to inject into the enriched message.
    """
    return (
        "\n\nYou are answering an informational query directly. "
        "Do NOT spawn a DevSession or use the Agent tool.\n\n"
        "RESEARCH FIRST — before answering, gather evidence:\n"
        "1. Search source code with Grep/Glob to find relevant files and implementations\n"
        "2. Query the memory system: `python -m tools.memory_search search \"relevant query\"`\n"
        "3. Consult knowledge base docs in docs/features/ and docs/ directories\n"
        "4. Cite your findings — file paths, line numbers, memory entries, doc references\n\n"
        "GUIDELINES:\n"
        "- Answer the question directly and conversationally\n"
        "- Back up claims with evidence from the codebase, memory, or docs\n"
        "- Use read-only tools: Bash (git log, git status, gh issue view, "
        "gh pr list, cat, grep, find), Read, Glob, Grep\n"
        "- Do NOT write files, create branches, run tests, or modify code\n"
        "- Do NOT use the Agent tool to spawn sub-agents\n"
        "- If the question requires actual work (fixes, changes, deployments), "
        "say so and suggest the user request it explicitly\n"
        "- Keep responses focused and concise\n\n"
        "Write in a clear, direct style. You are a knowledgeable teammate "
        "who knows the codebase well. Your return text will be "
        "automatically summarized and sent via Telegram."
    )
