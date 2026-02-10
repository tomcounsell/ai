"""Message routing, config loading, response decisions, and mention detection."""

import asyncio
import json
import logging
import re
from pathlib import Path

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


def load_config() -> dict:
    """Load project configuration from projects.json."""
    config_path = Path(__file__).parent.parent / "config" / "projects.json"
    example_path = config_path.with_suffix(".json.example")

    if not config_path.exists():
        if example_path.exists():
            logger.error(
                f"Project config not found at {config_path}. "
                f"Copy the example: cp {example_path} {config_path}"
            )
        else:
            logger.warning(f"Project config not found at {config_path}, using defaults")
        return {"projects": {}, "defaults": {}}

    with open(config_path) as f:
        config = json.load(f)

    # Validate defaults section exists and has working_directory
    defaults = config.get("defaults", {})
    if not defaults:
        logger.warning(
            "No 'defaults' section in projects.json. "
            "Add a defaults section with working_directory and telegram settings. "
            "See config/projects.json.example for proper setup."
        )
    elif not defaults.get("working_directory"):
        logger.warning(
            "No 'working_directory' in defaults section of projects.json. "
            "Projects without working_directory will fail. "
            "See config/projects.json.example for proper setup."
        )

    # Validate each active project
    projects = config.get("projects", {})
    for project_key in ACTIVE_PROJECTS:
        if project_key not in projects:
            continue
        project = projects[project_key]
        working_dir = project.get("working_directory") or defaults.get(
            "working_directory"
        )
        if not working_dir:
            logger.error(
                f"Project '{project_key}' has no working_directory and no default set. "
                "The bridge WILL fail when processing messages for this project. "
                "Fix: add 'working_directory' to the project in config/projects.json"
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
                logger.warning(
                    f"Group '{group}' is mapped to multiple projects, using first"
                )
                continue
            group_map[group_lower] = project
            logger.info(
                f"Mapping group '{group}' -> project '{project.get('name', project_key)}'"
            )

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
            model="llama3.2:3b",
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

    # === respond_to_unaddressed logic (the 4 cases) ===

    # Case 2: Reply to Valor's message → always respond (no Ollama needed)
    if message.reply_to_msg_id:
        try:
            replied_msg = await client.get_messages(
                event.chat_id, ids=message.reply_to_msg_id
            )
            if replied_msg and replied_msg.out:  # .out means sent by us (Valor)
                logger.debug("Case 2: Reply to Valor - responding")
                return True, True
        except Exception as e:
            logger.debug(f"Could not check replied message: {e}")

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
