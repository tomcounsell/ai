"""Message routing, config loading, response decisions, and mention detection."""

import asyncio
import json
import logging
import re
from pathlib import Path

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
DM_WHITELIST_CONFIG = {}

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
# User Permissions
# =============================================================================


def get_user_permissions(sender_id: int | None) -> str:
    """Get the permission level for a whitelisted user.

    Returns:
        "full" - Can do anything (default)
        "qa_only" - Q&A only, no code changes allowed
    """
    if not sender_id or sender_id not in DM_WHITELIST_CONFIG:
        return "full"
    return DM_WHITELIST_CONFIG[sender_id].get("permissions", "full")


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


def classify_needs_response(text: str) -> bool:
    """
    Use Ollama to quickly classify if a message needs a response.

    Returns True if the message appears to be a work request, question, or
    instruction that needs action. Returns False for acknowledgments like
    "thanks", "ok", "got it", side conversations, etc.
    """
    # Fast path: very short messages are usually acknowledgments
    if len(text.strip()) < 3:
        return False

    # Fast path: common acknowledgments (case-insensitive)
    acknowledgments = {
        "thanks",
        "thank you",
        "thx",
        "ty",
        "ok",
        "okay",
        "k",
        "kk",
        "got it",
        "gotcha",
        "understood",
        "nice",
        "great",
        "awesome",
        "perfect",
        "cool",
        "yes",
        "yep",
        "yeah",
        "yup",
        "no",
        "nope",
        "👍",
        "👌",
        "✅",
        "🙏",
        "❤️",
        "🔥",
        "lol",
        "lmao",
        "haha",
        "heh",
        "brb",
        "afk",
        "bbl",
    }
    text_lower = text.strip().lower().rstrip("!.,")
    if text_lower in acknowledgments:
        return False

    # Use Ollama for more nuanced classification
    try:
        import ollama

        response = ollama.chat(
            model="qwen3:1.7b",
            messages=[
                {
                    "role": "user",
                    "content": f"""Classify this message. Reply with ONLY "work" or "ignore".

- "work" = question, request, instruction, bug report, or anything needing action
- "ignore" = acknowledgment, thanks, greeting, side chat, or social message

Message: {text[:200]}

Classification:""",
                }
            ],
            options={"temperature": 0},
        )
        result = response["message"]["content"].strip().lower()
        return "work" in result
    except Exception as e:
        logger.debug(f"Ollama classification failed, defaulting to respond: {e}")
        # Default to responding if Ollama fails
        return True


async def classify_needs_response_async(text: str) -> bool:
    """Async wrapper for Ollama classification."""
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
        "question" - Q&A -> direct in target project, pass through as-is
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

    # Fast path: short acknowledgments / continuation commands
    first_word = text_lower.split()[0] if text_lower.split() else ""
    if first_word in _PASSTHROUGH_EXACT or text_lower.rstrip("!.,") in _PASSTHROUGH_EXACT:
        logger.info(f"[routing] Classified as passthrough (acknowledgment): {text[:120]}")
        return "passthrough"

    # Fast path: bare "#N" → question (Telegram eats # as hashtag, too ambiguous for SDLC)
    if re.match(r"^#\d+$", text_lower):
        logger.info(f"[routing] Classified as question (bare hash reference): {text[:120]}")
        return "question"

    # Fast path: issue/PR references like "issue 123", "pr 363", "pull request 363"
    if re.match(r"^(?:issue|pr|pull request)\s+#?\d+$", text_lower):
        logger.info(f"[routing] Classified as sdlc (issue/PR reference): {text[:120]}")
        return "sdlc"

    # Use Ollama for nuanced classification with Haiku fallback
    try:
        result = _classify_work_request_llm(text)
        logger.info(f"[routing] Classified as {result}: {text[:120]}")
        return result
    except Exception as e:
        logger.warning(f"Work request classification failed: {e}")
        # Conservative default: treat as question (no SDLC overhead)
        logger.info(f"[routing] Classified as question (fallback): {text[:120]}")
        return "question"


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
        '- "sdlc" = work request: fix bug, add feature, implement, refactor,\n'
        "  investigate issue, create/update codebase, deploy, resolve problem\n"
        '- "question" = asking for info, explanation, opinion, status check,\n'
        "  how does X work, what is Y, conversational/social\n\n"
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
            return "sdlc"
        if "question" in result:
            return "question"
        logger.debug(f"Ollama returned ambiguous classification: {result}")
    except Exception as e:
        logger.debug(f"Ollama classification failed, trying Haiku: {e}")

    # Fallback: Haiku via Anthropic API (singleton client)
    try:
        from config.models import MODEL_FAST

        client = _get_anthropic_client()
        if not client:
            logger.debug("No API key for Haiku classification fallback")
            return "question"

        response = client.messages.create(
            model=MODEL_FAST,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
        if "sdlc" in result:
            return "sdlc"
        return "question"
    except Exception as e:
        logger.debug(f"Haiku classification fallback also failed: {e}")
        return "question"


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
    """
    Async response decision with full context.

    Returns (should_respond, is_reply_to_valor) tuple.

    Decision logic for groups with respond_to_unaddressed:
    - Case 1: Unaddressed message → Ollama classifies if it needs work
    - Case 2: Reply to Valor → Always respond (continue session)
    - Case 3: @valor → Always respond
    - Case 4: @someoneelse → Always ignore
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

    # Case 1: Unaddressed message → use Ollama to classify
    logger.debug("Case 1: Unaddressed message - classifying with Ollama")
    needs_response = await classify_needs_response_async(text)
    if not needs_response:
        logger.info(f"Ollama classified as ignore: {text[:50]}...")
    return needs_response, False
