"""Message routing, config loading, response decisions, and mention detection."""

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path

from config.enums import ClassificationType, PersonaType
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# =============================================================================
# Module-level globals (set by telegram_bridge.py after config loading)
# =============================================================================

CONFIG = {}
DEFAULTS = {}
GROUP_TO_PROJECT = {}
ALL_MONITORED_GROUPS = []
ACTIVE_PROJECTS = []
RESPOND_TO_DMS = True
DM_WHITELIST = set()

# =============================================================================
# Constants
# =============================================================================

# Pattern to detect @mentions in messages
AT_MENTION_PATTERN = re.compile(r"@(\w+)")

# Known Valor usernames for @mention detection
VALOR_USERNAMES = {"valor", "valorengels"}

# Default mention triggers (set after config loading)
DEFAULT_MENTIONS = []

# =============================================================================
# Config Loading
# =============================================================================


def _resolve_config_path() -> Path:
    """Resolve projects.json path from env var or default location.

    Resolution order:
    1. PROJECTS_CONFIG_PATH env var (explicit override)
    2. ~/Desktop/Valor/projects.json (iCloud-synced default)
    3. config/projects.json (legacy in-repo fallback)
    """
    import os

    env_path = os.environ.get("PROJECTS_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser()

    desktop_path = Path.home() / "Desktop" / "Valor" / "projects.json"
    if desktop_path.exists():
        return desktop_path

    # Legacy fallback: in-repo config
    return Path(__file__).parent.parent / "config" / "projects.json"


def load_config() -> dict:
    """Load project configuration from projects.json.

    Loads from ~/Desktop/Valor/projects.json by default (iCloud-synced, private).
    Override with PROJECTS_CONFIG_PATH env var.
    Falls back to config/projects.json if ~/Desktop/Valor/ path doesn't exist.
    """
    config_path = _resolve_config_path()

    if not config_path.exists():
        logger.warning(f"Project config not found at {config_path}, using defaults")
        return {"projects": {}, "defaults": {}}

    with open(config_path) as f:
        config = json.load(f)

    # Expand ~ in working_directory values
    for _proj in config.get("projects", {}).values():
        wd = _proj.get("working_directory", "")
        if wd.startswith("~"):
            _proj["working_directory"] = str(Path(wd).expanduser())
    _defs = config.get("defaults", {})
    if _defs.get("working_directory", "").startswith("~"):
        _defs["working_directory"] = str(Path(_defs["working_directory"]).expanduser())

    # Validate defaults section exists and has working_directory
    defaults = config.get("defaults", {})
    if not defaults:
        logger.warning(
            "No 'defaults' section in projects.json. "
            "Add a defaults section with working_directory and telegram settings. "
            "See config/projects.example.json for the expected format."
        )
    elif not defaults.get("working_directory"):
        logger.warning(
            "No 'working_directory' in defaults section of projects.json. "
            "Projects without working_directory will fail. "
            "Check ~/Desktop/Valor/projects.json and add a working_directory to defaults."
        )

    # Validate each active project
    projects = config.get("projects", {})
    for project_key in ACTIVE_PROJECTS:
        if project_key not in projects:
            continue
        project = projects[project_key]
        working_dir = project.get("working_directory") or defaults.get("working_directory")
        if not working_dir:
            logger.error(
                f"Project '{project_key}' has no working_directory and no default set. "
                "The bridge WILL fail when processing messages for this project. "
                "Fix: add 'working_directory' to the project in ~/Desktop/Valor/projects.json"
            )
        elif not Path(working_dir).exists():
            logger.warning(
                f"Project '{project_key}' working_directory does not exist: {working_dir}"
            )

    return config


def build_group_to_project_map(config: dict) -> dict:
    """Build a mapping from group names (lowercase) to project configs."""
    group_map = {}
    projects = config.get("projects", {})

    for project_key in ACTIVE_PROJECTS:
        if project_key not in projects:
            logger.warning(f"Project '{project_key}' not found in config, skipping")
            continue

        project = projects[project_key]
        project["_key"] = project_key  # Store the key for reference

        telegram_config = project.get("telegram", {})
        groups = telegram_config.get("groups", [])

        for group in groups:
            group_lower = group.lower()
            if group_lower in group_map:
                logger.warning(f"Group '{group}' is mapped to multiple projects, using first")
                continue
            group_map[group_lower] = project
            logger.info(f"Mapping group '{group}' -> project '{project.get('name', project_key)}'")

    return group_map


# =============================================================================
# Project and Chat Mapping
# =============================================================================


def find_project_for_chat(chat_title: str | None) -> dict | None:
    """Find which project a chat belongs to."""
    if not chat_title:
        return None

    chat_lower = chat_title.lower()
    for group_name, project in GROUP_TO_PROJECT.items():
        if group_name in chat_lower:
            return project

    return None


def is_team_chat(chat_title: str | None) -> bool:
    """Team chats (no Dev:/PM: prefix) are mention-only."""
    if not chat_title:
        return False
    return not chat_title.startswith(("Dev:", "PM:"))


# =============================================================================
# Config-Driven Chat Mode Resolution
# =============================================================================


def resolve_persona(
    project: dict | None,
    chat_title: str | None,
    is_dm: bool = False,
) -> str | None:
    """Resolve the effective persona from config, title prefix, or DM status.

    Resolution order:
    1. DMs -> always PersonaType.TEAMMATE
    2. Group persona field in projects.json -> return PersonaType directly
    3. Title prefix "Dev:" -> PersonaType.DEVELOPER, "PM:" -> PersonaType.PROJECT_MANAGER
    4. None (unconfigured -- fall through to existing classifier behavior)

    Args:
        project: Project configuration dict from projects.json, or None.
        chat_title: Telegram chat/group title, or None for DMs.
        is_dm: Whether this is a direct message.

    Returns:
        PersonaType member or None (unconfigured).
    """
    # DMs are always Teammate
    if is_dm:
        return PersonaType.TEAMMATE

    # Look up persona from group config in projects.json
    if project and chat_title:
        telegram_config = project.get("telegram", {})
        groups = telegram_config.get("groups", {})
        if isinstance(groups, dict):
            for group_name, group_config in groups.items():
                if group_name.lower() in chat_title.lower():
                    if isinstance(group_config, dict):
                        persona_str = group_config.get("persona", "")
                        try:
                            persona = PersonaType(persona_str)
                            logger.debug(f"resolve_persona: persona={persona!r} for {chat_title!r}")
                            return persona
                        except ValueError:
                            pass  # Unknown persona value, fall through
                    break  # Found matching group but no valid persona

    # Title prefix fallback
    if chat_title:
        if chat_title.startswith("Dev:"):
            return PersonaType.DEVELOPER
        if chat_title.startswith("PM:"):
            return PersonaType.PROJECT_MANAGER

    # Unconfigured -- caller should fall through to existing behavior
    return None


# =============================================================================
# Mention Detection
# =============================================================================


def extract_at_mentions(text: str) -> list[str]:
    """Extract all @mentions from text, returning lowercase usernames."""
    return [m.lower() for m in AT_MENTION_PATTERN.findall(text)]


def get_valor_usernames(project: dict | None) -> set[str]:
    """Get all usernames that should be treated as Valor."""
    usernames = VALOR_USERNAMES.copy()
    if project:
        mentions = project.get("telegram", {}).get("mention_triggers", DEFAULT_MENTIONS)
        for trigger in mentions:
            clean_trigger = trigger.lstrip("@").lower()
            usernames.add(clean_trigger)
    return usernames


def is_message_for_valor(text: str, project: dict | None) -> bool:
    """Check if message explicitly @mentions Valor."""
    at_mentions = extract_at_mentions(text)
    if not at_mentions:
        return False
    valor_usernames = get_valor_usernames(project)
    return any(mention in valor_usernames for mention in at_mentions)


def is_message_for_others(text: str, project: dict | None) -> bool:
    """Check if message is @directed to someone other than Valor."""
    at_mentions = extract_at_mentions(text)
    if not at_mentions:
        return False
    valor_usernames = get_valor_usernames(project)
    # If ALL @mentions are for others (none for Valor), it's directed elsewhere
    return not any(mention in valor_usernames for mention in at_mentions)


# =============================================================================
# Ollama Classification
# =============================================================================


# 3-way social classification tokens.
# "ignore" tokens produce no response at all; "react" tokens get an emoji
# reaction without spawning a session.  Merged into a single dict so there
# is no duplication between the two sets (critique concern #6).
_SOCIAL_TOKENS: dict[str, str] = {
    # --- ignore (acknowledgments / affirmations) ---
    "thanks": "ignore",
    "thank you": "ignore",
    "thx": "ignore",
    "ty": "ignore",
    "ok": "ignore",
    "okay": "ignore",
    "k": "ignore",
    "kk": "ignore",
    "got it": "ignore",
    "gotcha": "ignore",
    "understood": "ignore",
    "yes": "ignore",
    "yep": "ignore",
    "yeah": "ignore",
    "yup": "ignore",
    "no": "ignore",
    "nope": "ignore",
    "👍": "ignore",
    "👌": "ignore",
    "✅": "ignore",
    "🙏": "ignore",
    "❤️": "ignore",
    "🔥": "ignore",
    "brb": "ignore",
    "afk": "ignore",
    "bbl": "ignore",
    # --- react (social / banter — deserve a reaction emoji) ---
    "nice": "react",
    "great": "react",
    "awesome": "react",
    "perfect": "react",
    "cool": "react",
    "lol": "react",
    "lmao": "react",
    "haha": "react",
    "heh": "react",
    "legit": "react",
    "dope": "react",
    "sick": "react",
    "fire": "react",
    "based": "react",
    "wow": "react",
    "whoa": "react",
    "damn": "react",
    "omg": "react",
    "rofl": "react",
}

# Emoji to react with, keyed by the flavour of the social token.
REACT_EMOJI_MAP: dict[str, str] = {
    "humor": "😁",
    "acknowledgment": "👍",
    "positive": "🔥",
}

# Tokens whose reaction flavour is "humor"; everything else is "positive".
_HUMOR_TOKENS = {"lol", "lmao", "haha", "heh", "rofl"}


def _pick_reaction_emoji(token: str) -> str:
    """Choose a contextually appropriate reaction emoji for *token*."""
    if token in _HUMOR_TOKENS:
        return REACT_EMOJI_MAP["humor"]
    return REACT_EMOJI_MAP["positive"]


def classify_needs_response(text: str) -> str:
    """Classify whether a message needs a full response, a reaction, or nothing.

    Returns one of three string values:

    * ``"respond"`` -- the message is a work request, question, or instruction
      that warrants a full agent session.
    * ``"react"`` -- the message is social banter or a compliment; send an
      emoji reaction without spawning a session.
    * ``"ignore"`` -- the message is a simple acknowledgment; do nothing.

    The function is intentionally conservative: if Ollama classification
    fails, it defaults to ``"respond"`` so no genuine question is dropped.
    """
    # Fast path: very short messages are usually acknowledgments
    if len(text.strip()) < 3:
        return "ignore"

    # Fast path: check the unified social-token dict
    text_lower = text.strip().lower().rstrip("!.,")
    token_class = _SOCIAL_TOKENS.get(text_lower)
    if token_class is not None:
        return token_class

    # Emoji-only messages (1-3 emoji, no text) → react
    stripped = text.strip()
    if stripped and all(
        unicodedata.category(ch).startswith(("So", "Sk", "Sm"))
        or ch in "\ufe0f\u200d"  # variation selectors / ZWJ
        for ch in stripped
    ):
        return "react"

    # Use Ollama for more nuanced classification
    try:
        import ollama

        response = ollama.chat(
            model="qwen3:1.7b",
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"""Classify this message. Reply with ONLY "work" or "ignore".

- "work" = question, request, instruction, bug report, or anything needing action
- "ignore" = acknowledgment, thanks, greeting, side chat, or social message

Message: {text[:200]}

Classification:"""
                    ),
                }
            ],
            options={"temperature": 0},
        )
        result = response["message"]["content"].strip().lower()
        # Ollama only distinguishes work vs ignore (2-way).
        # The react path is handled entirely by the fast-path token
        # matching above (critique concern #4: don't rely on Ollama
        # for the 3rd category).
        return "respond" if "work" in result else "ignore"
    except Exception as e:
        logger.debug(f"Ollama classification failed, defaulting to respond: {e}")
        # Default to responding if Ollama fails (conservative)
        return "respond"


async def classify_needs_response_async(text: str) -> str:
    """Async wrapper for 3-way Ollama classification.

    Returns ``"respond"``, ``"react"``, or ``"ignore"``.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, classify_needs_response, text)


# =============================================================================
# Work Request Classification (SDLC Routing)
# =============================================================================

# Lazy singleton for Anthropic client (avoid per-call instantiation)
_anthropic_client = None


def _get_anthropic_client():
    """Get or create a singleton Anthropic client for classification."""
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        api_key = get_anthropic_api_key()
        if not api_key:
            return None
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


# Fast-path patterns that bypass LLM classification entirely
_PASSTHROUGH_PREFIXES = (
    "/sdlc",
    "/do-plan",
    "/do-build",
    "/do-test",
    "/do-patch",
    "/do-pr-review",
    "/do-docs",
    "/prime",
    "/setup",
    "/update",
)

_PASSTHROUGH_EXACT = {
    "continue",
    "merge",
    "\U0001f44d",  # 👍
    "yes",
    "no",
    "ok",
    "lgtm",
}


def classify_work_request(message: str) -> str:
    """Classify if a message is a work request that should go through SDLC.

    Returns:
        "sdlc" - Work request -> orchestrator in ai/, prepend SDLC directive
        "question" - informational query -> direct in target project, pass through as-is
        "passthrough" - Already has skill invocation or is conversational
    """
    if not message or not message.strip():
        return "passthrough"

    text = message.strip()
    text_lower = text.lower()

    # Fast path: already routed (slash commands)
    for prefix in _PASSTHROUGH_PREFIXES:
        if text_lower.startswith(prefix):
            logger.info(f"[routing] Classified as passthrough (slash command): {text[:120]}")
            return "passthrough"

    # Fast path: any message containing an issue or PR reference → SDLC
    # This takes priority over acknowledgment matching because "continue issue 463"
    # is SDLC work, not a bare "continue" passthrough.
    if re.search(r"(?:issue|pr|pull request)\s+#?\d+", text_lower) or re.match(
        r"^#\d+$", text_lower
    ):
        logger.info(f"[routing] Classified as sdlc (issue/PR reference): {text[:120]}")
        return ClassificationType.SDLC

    # Fast path: short acknowledgments / continuation commands
    first_word = text_lower.split()[0] if text_lower.split() else ""
    if first_word in _PASSTHROUGH_EXACT or text_lower.rstrip("!.,") in _PASSTHROUGH_EXACT:
        logger.info(f"[routing] Classified as passthrough (acknowledgment): {text[:120]}")
        return "passthrough"

    # Use Ollama for nuanced classification with Haiku fallback
    try:
        result = _classify_work_request_llm(text)
        logger.info(f"[routing] Classified as {result}: {text[:120]}")
        return result
    except Exception as e:
        logger.warning(f"Work request classification failed: {e}")
        # Conservative default: treat as question (no SDLC overhead)
        logger.info(f"[routing] Classified as question (fallback): {text[:120]}")
        return ClassificationType.QUESTION


def _get_principal_priorities_for_classification() -> str:
    """Load condensed principal context for classification decisions.

    Provides the classifier with project priorities so it can make better
    routing decisions (e.g., recognizing project names, understanding which
    requests are high-priority work vs. casual questions).

    Returns:
        A short principal context string, or empty string if unavailable.
    """
    try:
        from agent.sdk_client import load_principal_context

        return load_principal_context(condensed=True)
    except Exception:
        return ""


def _classify_work_request_llm(text: str) -> str:
    """Use LLM to classify whether a message is a work request.

    Tries Ollama first (fast, local), falls back to Haiku (cheap, reliable).
    Includes principal context (project priorities) when available.
    """
    # Inject principal context for better classification of project-related messages
    principal = _get_principal_priorities_for_classification()
    principal_hint = ""
    if principal:
        principal_hint = f"\n\nContext — active projects and priorities:\n{principal[:500]}\n\n"

    prompt = (
        'Classify this message. Reply with ONLY one word: "sdlc" or "question".\n\n'
        '- "sdlc" = work request that could result in code changes or a PR:\n'
        "  fix bug, add feature, implement, refactor, investigate issue,\n"
        "  create/update codebase, deploy, resolve problem, continue/resume work\n"
        '- "question" = purely asking for info, explanation, opinion,\n'
        "  how does X work, what is Y, conversational/social\n\n"
        "If in doubt, classify as sdlc.\n\n"
        f"{principal_hint}"
        f"Message: {text[:300]}\n\n"
        "Classification:"
    )

    # Try Ollama first (fast, local)
    try:
        import ollama

        response = ollama.chat(
            model="qwen3:1.7b",
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 10},
        )
        result = response["message"]["content"].strip().lower()
        if "sdlc" in result:
            return ClassificationType.SDLC
        if "question" in result:
            return ClassificationType.QUESTION
        logger.debug(f"Ollama returned ambiguous classification: {result}")
    except Exception as e:
        logger.debug(f"Ollama classification failed, trying Haiku: {e}")

    # Fallback: Haiku via Anthropic API (singleton client)
    try:
        from config.models import MODEL_FAST

        client = _get_anthropic_client()
        if not client:
            logger.debug("No API key for Haiku classification fallback")
            return ClassificationType.QUESTION

        response = client.messages.create(
            model=MODEL_FAST,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
        if "sdlc" in result:
            return ClassificationType.SDLC
        return ClassificationType.QUESTION
    except Exception as e:
        logger.debug(f"Haiku classification fallback also failed: {e}")
        return ClassificationType.QUESTION


async def classify_work_request_async(message: str) -> str:
    """Async wrapper for work request classification."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, classify_work_request, message)


# =============================================================================
# Escalation Decision Logic
# =============================================================================


def should_escalate_to_human(
    issue_summary: str,
    severity: str = "unknown",
    project_key: str = "",
) -> dict:
    """Decide whether an issue warrants escalating (interrupting) the human.

    Uses principal context to understand which projects and problems are
    high-priority enough to justify an interruption. Without principal
    context, falls back to conservative defaults.

    Args:
        issue_summary: Brief description of the issue or blocker.
        severity: Estimated severity ("critical", "high", "medium", "low", "unknown").
        project_key: The project this issue relates to (e.g., "valor-ai").

    Returns:
        Dict with keys:
        - escalate: bool — whether to interrupt the human
        - reason: str — explanation of the decision
        - priority: str — inferred priority level
    """
    # Always escalate critical issues regardless of context
    if severity == "critical":
        return {
            "escalate": True,
            "reason": "Critical severity — always escalate",
            "priority": "critical",
        }

    # Load principal context for priority-aware decisions
    principal = _get_principal_priorities_for_classification()

    if not principal:
        # No principal context: conservative default — escalate high+, skip medium/low
        should = severity in ("critical", "high")
        return {
            "escalate": should,
            "reason": f"No principal context available, using severity-based default ({severity})",
            "priority": severity,
        }

    # Check if the project is mentioned in principal priorities
    project_mentioned = project_key.lower() in principal.lower() if project_key else False
    issue_lower = issue_summary.lower()

    # High-priority project + any non-low severity = escalate
    if project_mentioned and severity in ("high", "unknown"):
        return {
            "escalate": True,
            "reason": (
                f"Project '{project_key}' is in principal priorities with {severity} severity"
            ),
            "priority": "high",
        }

    # Check if issue text matches strategic keywords from principal context
    strategic_keywords = ["mission", "revenue", "production", "outage", "data loss"]
    if any(kw in issue_lower for kw in strategic_keywords):
        return {
            "escalate": True,
            "reason": "Issue matches strategic keywords from principal context",
            "priority": "high",
        }

    # Default: don't escalate low/medium issues for non-priority projects
    return {
        "escalate": severity == "high",
        "reason": (
            f"Standard priority assessment"
            f" (severity={severity}, project_in_priorities={project_mentioned})"
        ),
        "priority": severity,
    }


# =============================================================================
# Response Decision Logic
# =============================================================================


def should_respond_sync(
    text: str,
    is_dm: bool,
    project: dict | None,
    sender_id: int | None = None,
    sender_username: str | None = None,
) -> bool:
    """
    Synchronous check for basic response conditions.
    Used for DMs and groups without respond_to_unaddressed.
    """
    if is_dm:
        if not RESPOND_TO_DMS:
            return False
        # Check whitelist if configured (matches on immutable Telegram user ID)
        if DM_WHITELIST:
            if sender_id not in DM_WHITELIST:
                return False
        return True

    # Must be in a monitored group
    if not project:
        return False

    telegram_config = project.get("telegram", {})

    # If respond_to_all is set, respond to everything
    if telegram_config.get("respond_to_all", True):
        return True

    # For groups NOT using respond_to_unaddressed, use mention-based logic
    if not telegram_config.get("respond_to_unaddressed", False):
        if telegram_config.get("respond_to_mentions", True):
            mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
            text_lower = text.lower()
            return any(mention.lower() in text_lower for mention in mentions)

    return False


async def should_respond_async(
    client,
    event,
    text: str,
    is_dm: bool,
    chat_title: str | None,
    project: dict | None,
    sender_name: str | None = None,
    sender_username: str | None = None,
    sender_id: int | None = None,
) -> tuple[bool, bool]:
    """Async response decision with full context.

    Returns (should_respond, is_reply_to_valor) tuple.

    Uses config-driven persona resolution (resolve_persona) as the first
    routing gate. When a group resolves to Teammate persona (via "teammate"
    persona in projects.json), the group becomes a passive listener: messages
    are stored but the agent only responds on @mention or reply-to-Valor.
    This skips Ollama classification entirely for those groups, reducing
    latency and preventing unwanted responses in observation-only channels.

    Decision logic after persona resolution:
    - Reply to Valor -> always respond (continue session, checked before mode)
    - Teammate persona group -> @mention only (passive listener)
    - Team chat (no Dev:/PM: prefix) -> @mention only
    - respond_to_all -> always respond
    - respond_to_unaddressed -> Ollama classifies need
    - @valor -> always respond
    - @someoneelse -> always ignore
    - Unaddressed -> Ollama classifies if it needs work
    """
    message = event.message

    # DMs: use sync logic
    if is_dm:
        return (
            should_respond_sync(
                text,
                is_dm,
                project,
                sender_id,
                sender_username,
            ),
            False,
        )

    # Must be in a monitored group
    if not project:
        return False, False

    telegram_config = project.get("telegram", {})

    # Reply to Valor's message → always detect (needed for session continuation)
    # Must run before any early returns so is_reply_to_valor is set correctly
    if message.reply_to_msg_id:
        try:
            replied_msg = await client.get_messages(event.chat_id, ids=message.reply_to_msg_id)
            if replied_msg and replied_msg.out:  # .out means sent by us (Valor)
                logger.debug("Reply to Valor detected - continuing session")
                return True, True
        except Exception as e:
            logger.debug(f"Could not check replied message: {e}")

    # Config-driven Teammate groups: passive listener (mention/reply only, skip Ollama)
    persona = resolve_persona(project, chat_title, is_dm=False)
    if persona == PersonaType.TEAMMATE:
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
        text_lower = text.lower()
        if any(mention.lower() in text_lower for mention in mentions):
            logger.debug("Teammate-persona group: @mention detected - responding")
            return True, False
        # Completely silent -- no response, no reaction
        logger.debug(f"Teammate-persona group: silent storage for {chat_title!r}")
        return False, False

    # Team chats (no Dev:/PM: prefix) are mention-only
    if is_team_chat(chat_title):
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
        text_lower = text.lower()
        if any(mention.lower() in text_lower for mention in mentions):
            return True, False
        return False, False

    # respond_to_all means respond to everything
    if telegram_config.get("respond_to_all", True):
        return True, False

    # For groups NOT using respond_to_unaddressed, use sync mention-based logic
    if not telegram_config.get("respond_to_unaddressed", False):
        return (
            should_respond_sync(
                text,
                is_dm,
                project,
                sender_id,
                sender_username,
            ),
            False,
        )

    # Case 3: @valor → always respond (no Ollama needed)
    if is_message_for_valor(text, project):
        logger.debug("Case 3: @valor mentioned - responding")
        return True, False

    # Case 4: @someoneelse → always ignore (no Ollama needed)
    if is_message_for_others(text, project):
        logger.debug("Case 4: Message @directed to others - ignoring")
        return False, False

    # Case 1: Unaddressed message → use Ollama to classify (3-way)
    logger.debug("Case 1: Unaddressed message - classifying with Ollama")
    classification = await classify_needs_response_async(text)
    if classification == "ignore":
        logger.info(f"Ollama classified as ignore: {text[:50]}...")
        return False, False
    if classification == "react":
        logger.info(f"Classified as react-only: {text[:50]}...")
        return "react", False  # Special string; bridge sends emoji, no session
    return True, False
