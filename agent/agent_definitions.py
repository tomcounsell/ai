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


def _fallback_definition(path: Path, reason: str) -> dict[str, str | dict[str, str] | bool]:
    """Build a fallback agent definition dict.

    The returned dict carries an explicit ``"_is_fallback": True`` marker so
    callers (notably ``validate_agent_files``) can detect fallback dicts via
    key lookup rather than parsing the free-text description.

    ``reason`` is a short string suitable for logs and the agent's description
    field (e.g. "missing file" or "ValueError: No YAML frontmatter found").
    """
    return {
        "frontmatter": {"description": f"Fallback for unusable {path.name}: {reason}"},
        "body": (
            f"Agent definition file {path.name} is not available ({reason})."
            " Operate with your best judgment."
        ),
        "_is_fallback": True,
    }


def _parse_agent_markdown(path: Path) -> dict[str, str | dict[str, str] | bool]:
    """Parse a markdown agent file into frontmatter fields and body.

    Returns a dict with string keys for each frontmatter field plus 'body'
    containing the markdown content after the closing '---'.

    On any of the following failure modes, logs a warning and returns a
    fallback dict (with ``"_is_fallback": True``) instead of raising — so
    that a single broken agent file cannot kill the session:

    - **Missing file**: ``path.exists() == False`` (fast-path check).
    - **OSError and subclasses**: ``FileNotFoundError`` (race after the
      ``exists()`` check), ``PermissionError``, other I/O failures from
      ``path.read_text``.
    - **ValueError and subclasses**: explicit ``ValueError`` raised when no
      YAML frontmatter is found, plus ``UnicodeDecodeError`` (a
      ``ValueError`` subclass) from decoding invalid UTF-8 bytes.

    Exceptions outside the ``(OSError, ValueError)`` tree (e.g. ``KeyError``,
    ``AttributeError``, ``TypeError``) propagate unchanged — those indicate
    programmer error in this module, not an unusable input file.
    """
    if not path.exists():
        logger.warning("Agent definition file not found: %s — using fallback prompt", path)
        return _fallback_definition(path, "missing file")

    try:
        text = path.read_text(encoding="utf-8")

        # Match YAML frontmatter delimited by '---'
        match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
        if not match:
            raise ValueError(f"No YAML frontmatter found in {path}")
    # OSError covers FileNotFoundError, PermissionError, and other I/O errors.
    # ValueError covers the explicit raise above plus UnicodeDecodeError (a
    # ValueError subclass) from read_text() on invalid UTF-8.
    except (OSError, ValueError) as exc:
        logger.warning(
            "Agent definition %s unusable (%s: %s) — using fallback prompt",
            path,
            exc.__class__.__name__,
            exc,
        )
        return _fallback_definition(path, f"{exc.__class__.__name__}: {exc}")

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

    return definitions


# Agent files referenced by get_agent_definitions(). Used by validate_agent_files()
# to check that all expected files exist on disk at process startup (worker and bridge).
_EXPECTED_AGENT_FILES = [
    "builder.md",
    "validator.md",
    "code-reviewer.md",
]


def validate_agent_files() -> list[str]:
    """Check that all expected agent definition files are usable.

    For each expected agent file, this checks:

    1. **Existence**: if the file is missing, append its path to the returned
       list. Per-path warnings are emitted by ``_parse_agent_markdown`` for
       parse failures; the missing-file case is surfaced via the returned list
       (callers log a summary).
    2. **Trial-parse**: if the file exists, attempt to parse it via
       ``_parse_agent_markdown``. Because that helper now returns a fallback
       dict (with ``"_is_fallback": True``) for malformed YAML, OS read
       errors, and Unicode-decode errors instead of raising, we detect those
       failures via the marker key rather than by catching exceptions. Any
       file that fell back to the placeholder is also appended to the
       returned list. Reasons are logged by ``_parse_agent_markdown`` itself
       — this function only returns the list of problematic paths.

    Returns a list of problematic file paths (as strings). An empty list
    means all files are present AND parse cleanly. Called during bridge and
    worker startup to surface unusable files early via log warnings.
    """
    problematic: list[str] = []
    for filename in _EXPECTED_AGENT_FILES:
        path = _AGENTS_DIR / filename
        if not path.exists():
            problematic.append(str(path))
            continue
        # File exists — trial-parse to surface malformed or unreadable files.
        # _parse_agent_markdown already logs a warning on any fallback path,
        # so we only need to record the path here.
        result = _parse_agent_markdown(path)
        if result.get("_is_fallback"):
            problematic.append(str(path))
    return problematic
