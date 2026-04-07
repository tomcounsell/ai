"""Agent definitions registry for SDK sessions.

Provides programmatic AgentDefinition instances derived from the markdown
agent files in .claude/agents/. For SDK sessions, these definitions take
precedence over the raw markdown files.

Each agent definition includes:
- description: from the YAML frontmatter
- prompt: the markdown body after frontmatter
- tools: tool access list (None = inherit all)
- model: model override (None = inherit from parent)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from claude_agent_sdk import AgentDefinition

logger = logging.getLogger(__name__)

# Root of the repository, resolved relative to this file's location.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_AGENTS_DIR = _REPO_ROOT / ".claude" / "agents"

# Read-only tools suitable for validation and review agents.
# Bash is intentionally excluded — it can write files and run arbitrary commands.
_READ_ONLY_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
]


def _parse_agent_markdown(path: Path) -> dict[str, str | dict[str, str]]:
    """Parse a markdown agent file into frontmatter fields and body.

    Returns a dict with string keys for each frontmatter field plus 'body'
    containing the markdown content after the closing '---'.

    If the file does not exist, logs a warning and returns a fallback dict
    with empty frontmatter and a minimal body. This prevents the entire
    session from crashing when an agent definition file is missing (e.g.,
    during deployment before file sync completes).
    """
    if not path.exists():
        logger.warning("Agent definition file not found: %s — using fallback prompt", path)
        return {
            "frontmatter": {"description": f"Fallback for missing {path.name}"},
            "body": (
                f"Agent definition file {path.name} is not available."
                " Operate with your best judgment."
            ),
        }

    text = path.read_text(encoding="utf-8")

    # Match YAML frontmatter delimited by '---'
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        raise ValueError(f"No YAML frontmatter found in {path}")

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    # Simple YAML key-value parsing (sufficient for flat frontmatter)
    frontmatter: dict[str, str] = {}
    for line in frontmatter_text.splitlines():
        # Skip lines that are indented (nested YAML) or empty
        if not line.strip() or line.startswith(" ") or line.startswith("\t"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip()

    return {"frontmatter": frontmatter, "body": body}


def get_agent_definitions() -> dict[str, AgentDefinition]:
    """Build and return the registry of agent definitions.

    Returns:
        A dict mapping agent names ('builder', 'validator', 'code-reviewer')
        to their corresponding AgentDefinition instances.
    """
    definitions: dict[str, AgentDefinition] = {}

    # --- builder ---
    builder_data = _parse_agent_markdown(_AGENTS_DIR / "builder.md")
    builder_fm = builder_data["frontmatter"]
    definitions["builder"] = AgentDefinition(
        description=str(builder_fm.get("description", "")),
        prompt=str(builder_data["body"]),
        tools=None,  # Inherits all tools from the parent session
        model=None,  # Inherits model from parent
    )

    # --- validator ---
    validator_data = _parse_agent_markdown(_AGENTS_DIR / "validator.md")
    validator_fm = validator_data["frontmatter"]
    definitions["validator"] = AgentDefinition(
        description=str(validator_fm.get("description", "")),
        prompt=str(validator_data["body"]),
        tools=_READ_ONLY_TOOLS,
        model="sonnet",  # Explicit from frontmatter: model: sonnet
    )

    # --- code-reviewer ---
    reviewer_data = _parse_agent_markdown(_AGENTS_DIR / "code-reviewer.md")
    reviewer_fm = reviewer_data["frontmatter"]
    definitions["code-reviewer"] = AgentDefinition(
        description=str(reviewer_fm.get("description", "")),
        prompt=str(reviewer_data["body"]),
        tools=_READ_ONLY_TOOLS,  # Read-only access for review
        model=None,  # Inherits model from parent
    )

    # --- dev-session ---
    # Full-permission developer session for code changes.
    # Spawned by a PM-role AgentSession to do actual work.
    # tools=None means all tools available — full write permissions.
    definitions["dev-session"] = AgentDefinition(
        description="Full-permission developer session for code changes",
        prompt=_load_dev_session_prompt(),
        tools=None,  # All tools — full permissions
        model=None,  # Inherit from parent
    )

    return definitions


def _load_dev_session_prompt() -> str:
    """Load the dev-session agent prompt.

    Falls back to a minimal prompt if the markdown file doesn't exist yet.
    """
    dev_session_md = _AGENTS_DIR / "dev-session.md"
    if dev_session_md.exists():
        try:
            data = _parse_agent_markdown(dev_session_md)
            return str(data["body"])
        except (ValueError, KeyError):
            pass

    return (
        "You are a Developer agent with full permissions to read, write, and execute code.\n\n"
        "You are spawned by a PM-role AgentSession to do the actual coding work.\n"
        "Follow the SDLC pipeline stages as directed by your parent PM session.\n"
        "Commit at logical checkpoints as you work.\n"
    )


# Agent files referenced by get_agent_definitions(). Used by validate_agent_files()
# to check that all expected files exist on disk at bridge startup.
_EXPECTED_AGENT_FILES = [
    "builder.md",
    "validator.md",
    "code-reviewer.md",
    "dev-session.md",
]


def validate_agent_files() -> list[str]:
    """Check that all expected agent definition files exist on disk.

    Returns a list of missing file paths (as strings). An empty list means
    all files are present. Called during bridge startup to surface missing
    files early via log warnings.
    """
    missing = []
    for filename in _EXPECTED_AGENT_FILES:
        path = _AGENTS_DIR / filename
        if not path.exists():
            missing.append(str(path))
    return missing
