"""Message routing, config loading, response decisions, and mention detection."""

import asyncio
import json
import logging
import re
from pathlib import Path

from config.enums import ClassificationType, PersonaType
from config.models import OLLAMA_LOCAL_MODEL
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# =============================================================================
# Module-level globals (set by telegram_bridge.py after config loading)
# =============================================================================

CONFIG = {}
DEFAULTS = {}
GROUP_TO_PROJECT = {}
EMAIL_TO_PROJECT: dict[str, dict] = {}
EMAIL_DOMAIN_TO_PROJECT: dict[str, dict] = {}  # domain -> project config
DM_USER_TO_PROJECT: dict[int, dict] = {}  # sender_id -> project config
ALL_MONITORED_GROUPS = []
ACTIVE_PROJECTS = []
RESPOND_TO_DMS = True
DM_WHITELIST = set()

# =============================================================================
# Constants
# =============================================================================

# Pattern to detect @mentions in messages
AT_MENTION_PATTERN = re.compile(r"@(\w+)")

# Default mention triggers (set after config loading from
# defaults.telegram.mention_triggers in projects.json). This is the single
# source of truth for self-mention detection.
DEFAULT_MENTIONS = []

# =============================================================================
# Config Loading
# =============================================================================


def _resolve_config_path() -> Path:
    """Resolve projects.json path from env var or default location.

    Resolution order:
    1. PROJECTS_CONFIG_PATH env var (explicit override)
    2. ~/Desktop/Valor/projects.json (iCloud-synced default) — skipped under launchd
    3. config/projects.json (local copy, updated by install_worker.sh)
    """
    import os

    env_path = os.environ.get("PROJECTS_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser()

    # When running under launchd (VALOR_LAUNCHD=1), skip the iCloud-synced
    # Desktop path entirely. macOS TCC blocks open() and even stat() on
    # ~/Desktop files from launchd agents, causing indefinite hangs.
    # install_worker.sh copies projects.json → config/projects.json at install time.
    if not os.environ.get("VALOR_LAUNCHD"):
        desktop_path = Path.home() / "Desktop" / "Valor" / "projects.json"
        if desktop_path.exists():
            return desktop_path

    # Local copy (updated by install_worker.sh) or legacy in-repo fallback
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


def build_email_to_project_map(config: dict) -> tuple[dict, dict]:
    """Build mappings from email addresses and domains to project configs.

    Reads the 'email.contacts' and 'email.domains' sections from each project
    in projects.json.

    Returns:
        Tuple of (address_map, domain_map) where:
        - address_map: lowercase email address -> project config dict (exact match)
        - domain_map: lowercase domain -> project config dict (wildcard match)
    """
    email_map: dict[str, dict] = {}
    domain_map: dict[str, dict] = {}
    projects = config.get("projects", {})

    for project_key in ACTIVE_PROJECTS:
        if project_key not in projects:
            continue

        project = projects[project_key]
        project["_key"] = project_key  # Ensure key is set (mirrors group map behavior)

        email_config = project.get("email", {})

        # Exact-match contacts
        contacts = email_config.get("contacts", {})
        for email_addr, contact_info in contacts.items():
            email_lower = email_addr.lower()
            if email_lower in email_map:
                logger.warning(f"Email '{email_addr}' is mapped to multiple projects, using first")
                continue
            email_map[email_lower] = project
            logger.info(
                f"Mapping email '{email_addr}' -> project '{project.get('name', project_key)}'"
            )

        # Domain wildcard (e.g. "psyoptimal.com" matches *@psyoptimal.com)
        domains = email_config.get("domains", [])
        for domain in domains:
            domain_lower = domain.lower().lstrip("@")
            if domain_lower in domain_map:
                logger.warning(f"Domain '{domain}' is mapped to multiple projects, using first")
                continue
            domain_map[domain_lower] = project
            logger.info(
                f"Mapping domain '@{domain_lower}' -> project '{project.get('name', project_key)}'"
            )

    return email_map, domain_map


def ensure_email_routing_loaded() -> bool:
    """Populate ``EMAIL_TO_PROJECT`` and ``EMAIL_DOMAIN_TO_PROJECT`` on demand.

    Idempotent: a no-op when the maps are already populated (the normal case
    inside ``run_email_bridge`` after startup). Used by out-of-process callers
    like ``tools/valor_email.py`` so the CLI does not have to reach into this
    module's internals to prime routing state for the IMAP read-only fallback.

    Returns ``True`` if the maps are populated (either already or after this
    call), ``False`` if loading failed. Errors are logged but never raised —
    the CLI fallback path tolerates an empty routing table by refusing to
    read INBOX (see ``tools/valor_email.py::_imap_fallback_fetch``).
    """
    if EMAIL_TO_PROJECT or EMAIL_DOMAIN_TO_PROJECT:
        return True
    try:
        # Out-of-process callers (e.g. tools/valor_email.py) don't import
        # telegram_bridge, so ACTIVE_PROJECTS may still be empty. Populate it
        # from the hostname so build_email_to_project_map filters correctly.
        # Note: importing telegram_bridge has a side effect of rebinding
        # _routing_module.ACTIVE_PROJECTS, so re-read after the import.
        if not ACTIVE_PROJECTS:
            from bridge import routing as _r
            from bridge.telegram_bridge import _get_active_projects

            if not _r.ACTIVE_PROJECTS:
                _r.ACTIVE_PROJECTS = _get_active_projects()
        config = load_config()
        addr_map, domain_map = build_email_to_project_map(config)
        EMAIL_TO_PROJECT.update(addr_map)
        EMAIL_DOMAIN_TO_PROJECT.update(domain_map)
        return True
    except Exception as e:
        logger.warning(f"ensure_email_routing_loaded: failed to load routing config: {e}")
        return False


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


def find_project_for_dm(sender_id: int | None) -> dict | None:
    """Find which project a DM sender belongs to.

    Looks up the sender_id in DM_USER_TO_PROJECT, built from whitelist entries
    that have a 'project' field in projects.json dms.whitelist.

    Returns the project config dict with '_key' set, or None if no mapping.
    """
    if not sender_id:
        return None
    return DM_USER_TO_PROJECT.get(sender_id)


def get_known_email_search_terms() -> list[str]:
    """Return IMAP FROM search terms for all configured email senders.

    Returns exact addresses from email.contacts (e.g. "tom@yuda.me") and
    domain tokens from email.domains (e.g. "@psyoptimal.com"). These can be
    used to build an IMAP UNSEEN+FROM query so the bridge never fetches
    messages from unknown senders — leaving them UNSEEN for other machines.

    Both maps are already filtered to ACTIVE_PROJECTS for this machine.
    """
    terms: list[str] = list(EMAIL_TO_PROJECT.keys())
    terms += [f"@{domain}" for domain in EMAIL_DOMAIN_TO_PROJECT]
    return terms


def find_project_for_email(sender_email: str | None) -> dict | None:
    """Find which project an email sender belongs to.

    Checks exact-match first (email.contacts), then domain wildcard (email.domains).
    The maps are built from projects.json 'email.contacts' and 'email.domains' sections.

    Args:
        sender_email: The sender's email address (case-insensitive).

    Returns:
        Project config dict with '_key' set, or None if no match.
    """
    if not sender_email:
        return None

    email_lower = sender_email.lower()

    # Exact match first
    if email_lower in EMAIL_TO_PROJECT:
        return EMAIL_TO_PROJECT[email_lower]

    # Domain wildcard: someone@psyoptimal.com -> psyoptimal.com
    if "@" in email_lower:
        domain = email_lower.split("@", 1)[1]
        if domain in EMAIL_DOMAIN_TO_PROJECT:
            return EMAIL_DOMAIN_TO_PROJECT[domain]

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
    """Get all usernames that should be treated as Valor.

    Source of truth: ``project["telegram"]["mention_triggers"]`` from
    ``projects.json``, falling back to ``DEFAULT_MENTIONS`` (loaded from
    ``defaults.telegram.mention_triggers``). When ``project`` is ``None``
    we return an empty set so unit tests that don't load a config stay
    inert. Production startup must verify ``DEFAULT_MENTIONS`` is non-empty
    (see ``bridge/telegram_bridge.py``).
    """
    if project is None:
        return set()
    mentions = project.get("telegram", {}).get("mention_triggers", DEFAULT_MENTIONS)
    return {t.lstrip("@").lower() for t in mentions}


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


# Acknowledgment and social tokens that don't need a response.
_ACKNOWLEDGMENT_TOKENS: set[str] = {
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
    "brb",
    "afk",
    "bbl",
    # Social banter — no session needed
    "nice",
    "great",
    "awesome",
    "perfect",
    "cool",
    "lol",
    "lmao",
    "haha",
    "heh",
    "legit",
    "dope",
    "sick",
    "fire",
    "based",
    "wow",
    "whoa",
    "damn",
    "omg",
    "rofl",
}


def classify_needs_response(text: str) -> bool:
    """Classify whether a message needs a full response.

    Returns ``True`` if the message warrants an agent session, ``False`` if
    it is a simple acknowledgment, social banter, or emoji that can be ignored.

    The function is intentionally conservative: if Ollama classification
    fails, it defaults to ``True`` so no genuine question is dropped.
    """
    # Fast path: very short messages are usually acknowledgments
    if len(text.strip()) < 3:
        return False

    # Fast path: check the acknowledgment token set
    text_lower = text.strip().lower().rstrip("!.,")
    if text_lower in _ACKNOWLEDGMENT_TOKENS:
        return False

    # Use Ollama for more nuanced classification
    try:
        import ollama

        response = ollama.chat(
            model=OLLAMA_LOCAL_MODEL,
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
        return "work" in result
    except Exception as e:
        logger.debug(f"Ollama classification failed, defaulting to respond: {e}")
        # Default to responding if Ollama fails (conservative)
        return True


async def classify_needs_response_async(text: str) -> bool:
    """Async wrapper for Ollama classification.

    Returns ``True`` if the message needs a response, ``False`` otherwise.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, classify_needs_response, text)


# Regex for standalone "?" — excludes URL query-string params like ?q=1 or &page=2
# Lookbehind: not preceded by =, &, or word chars (domain path before ?)
# Lookahead: not followed by \w+= (query param name=value pattern)
_STANDALONE_QUESTION_RE = re.compile(r"(?<![=&\w])\?|(?<![=&])\?(?!\w+=)")


async def classify_conversation_terminus(
    text: str,
    thread_messages: list[str],  # recent turns, oldest first
    sender_is_bot: bool = False,
) -> str:
    """Classify whether a reply-to-Valor message is a conversation terminus.

    Returns one of:
    - "RESPOND" — message warrants a reply (default/conservative)
    - "REACT"   — thread is winding down; set an acknowledgment emoji (human-only)
    - "SILENT"  — bot loop or acknowledgment; do nothing

    Fast-path order (critical — checked before LLM):
    1. sender_is_bot + no question → SILENT  (primary loop-break signal)
    2. acknowledgment token or very short (≤1 word) → SILENT
       (unless thread_messages contains a question — then fall through, so a
       human short answer like "Yes"/"No" to a Valor question is not dropped;
       see issue #1090)
    3. standalone "?" in text (not URL query param) → RESPOND

    LLM (Ollama-first, Haiku fallback) handles everything else.
    REACT is collapsed to SILENT when sender_is_bot=True.
    Conservative default: any classifier error → RESPOND.
    """
    # Guard: empty/None text — treat as continuation
    if not text or not text.strip():
        return "RESPOND"

    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    # Fast-path 1: bot sender with no question → SILENT (strongest signal for loop break)
    if sender_is_bot and not _STANDALONE_QUESTION_RE.search(text_stripped):
        return "SILENT"

    # Fast-path 2: acknowledgment token (fires AFTER sender check, never before)
    # — but skip the check entirely when the replied-to context contained a
    # question, so a short human reply ("Yes"/"No") to a Valor question is
    # not silently dropped. Fast-Path 1 above already handled the bot case.
    # NOTE (issue #1090): thread_messages is currently the single immediate
    # replied-to message. If a future change widens this to include older
    # Valor messages, this `?` heuristic may fire on a stale upstream question
    # and route an unrelated short reply to RESPOND. Revisit then.
    valor_asked_question = any(
        _STANDALONE_QUESTION_RE.search(msg)
        for msg in thread_messages
        if isinstance(msg, str) and msg
    )
    token_normalized = text_lower.rstrip("!.,").strip()
    word_count = len(text_stripped.split())
    if not valor_asked_question and (token_normalized in _ACKNOWLEDGMENT_TOKENS or word_count <= 1):
        return "SILENT"

    # Fast-path 3: standalone "?" → RESPOND (excludes URL query params)
    if _STANDALONE_QUESTION_RE.search(text_stripped):
        return "RESPOND"

    # LLM classification: Ollama-first, Haiku fallback
    thread_context = "\n".join(thread_messages[-2:]) if thread_messages else ""
    prompt = (
        "Classify this reply in a conversation thread. "
        "The reply was sent to Valor (an AI agent).\n\n"
        f"Reply text: {text_stripped[:300]}\n\n"
        "Recent thread context (may be empty):\n"
        f"{thread_context[:400] if thread_context else '(none)'}\n\n"
        f"Sender is a bot: {sender_is_bot}\n\n"
        "Instructions:\n"
        "- If the message contains a question or requests action → reply RESPOND\n"
        "- If the message is a natural conversation closer (completion language,\n"
        "  agreement, acknowledgment without question) → reply REACT\n"
        "- If the message adds nothing new or is redundant with prior context"
        " → reply REACT\n"
        "- If the sender is a bot and the message is declarative (no question)"
        " → reply SILENT\n"
        "- Default to RESPOND when uncertain\n\n"
        "Reply with ONLY one word: RESPOND, REACT, or SILENT."
    )

    result = None

    # Try Ollama first
    try:
        import ollama

        response = ollama.chat(
            model=OLLAMA_LOCAL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0},
        )
        raw = response["message"]["content"].strip().upper()
        if raw in ("RESPOND", "REACT", "SILENT"):
            result = raw
    except Exception as e:
        logger.debug(f"Ollama terminus classification failed: {e}")

    # Haiku fallback if Ollama failed or returned garbage
    if result is None:
        try:
            import anthropic

            api_key = get_anthropic_api_key()
            if api_key:
                client = anthropic.Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=10,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text.strip().upper()
                if raw in ("RESPOND", "REACT", "SILENT"):
                    result = raw
        except Exception as e:
            logger.debug(f"Haiku terminus classification failed: {e}")

    # Conservative default on any failure
    if result is None:
        result = "RESPOND"

    # Collapse REACT → SILENT for bot senders (no emoji spam in bot loops)
    if sender_is_bot and result == "REACT":
        result = "SILENT"

    return result


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
    """Classify a message into one of four routing buckets (or passthrough).

    Returns:
        "sdlc" - Work request that could result in code changes or a PR
        "collaboration" - Direct task the PM can handle without a dev-session
        "other" - Ambiguous task; PM uses judgment
        "question" - Informational query, pass through as-is
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
    """Use LLM to classify a message into sdlc, collaboration, other, or question.

    Four-way classification with "collaboration" as the default for ambiguous
    messages. Tries Ollama first (fast, local), falls back to Haiku (cheap,
    reliable). Includes principal context (project priorities) when available.
    Uses first-token extraction with exact match to avoid substring collisions.
    """
    # Inject principal context for better classification of project-related messages
    principal = _get_principal_priorities_for_classification()
    principal_hint = ""
    if principal:
        principal_hint = f"\n\nContext — active projects and priorities:\n{principal[:500]}\n\n"

    prompt = (
        "Classify this message. Reply with ONLY one word: "
        '"sdlc", "collaboration", "other", or "question".\n\n'
        '- "sdlc" = work request that could result in code changes or a PR:\n'
        "  fix bug, add feature, implement, refactor, investigate issue,\n"
        "  create/update codebase, deploy, resolve problem, continue/resume work\n"
        '- "collaboration" = direct task the PM can handle without coding:\n'
        "  add this to the knowledge base, draft an issue, send a status update,\n"
        "  write a doc about Y, save this file, search memory, look up info and act\n"
        '- "other" = ambiguous task that does not clearly fit sdlc or collaboration\n'
        '- "question" = purely asking for info, explanation, opinion,\n'
        "  how does X work, what is Y, conversational/social\n\n"
        "If in doubt, classify as collaboration.\n\n"
        f"{principal_hint}"
        f"Message: {text[:300]}\n\n"
        "Classification:"
    )

    # Try Ollama first (fast, local)
    try:
        import ollama

        response = ollama.chat(
            model=OLLAMA_LOCAL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 10},
        )
        result = response["message"]["content"].strip().lower().split()[0]
        if result == "sdlc":
            return ClassificationType.SDLC
        if result == "collaboration":
            return ClassificationType.COLLABORATION
        if result == "other":
            return ClassificationType.OTHER
        if result == "question":
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
        result = response.content[0].text.strip().lower().split()[0]
        if result == "sdlc":
            return ClassificationType.SDLC
        if result == "collaboration":
            return ClassificationType.COLLABORATION
        if result == "other":
            return ClassificationType.OTHER
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

    # DMs: use sync logic, but check reply_to_msg_id for session continuation (#996)
    if is_dm:
        should = should_respond_sync(
            text,
            is_dm,
            project,
            sender_id,
            sender_username,
        )
        # Any reply in a DM thread should trigger session continuation, not a fresh session.
        is_reply = bool(message.reply_to_msg_id)
        return should, is_reply

    # Must be in a monitored group
    if not project:
        return False, False

    telegram_config = project.get("telegram", {})

    # Reply-to detection — needed for session continuation regardless of who sent the
    # replied-to message (#996: replies to own messages should also steer the session).
    # Must run before any early returns so is_reply_to_valor is set correctly.
    is_reply_to_non_valor_thread = False
    if message.reply_to_msg_id:
        try:
            replied_msg = await client.get_messages(event.chat_id, ids=message.reply_to_msg_id)
            if replied_msg and replied_msg.out:  # .out means sent by us (Valor)
                try:
                    _sender_obj = await event.get_sender()
                    sender_is_bot = getattr(_sender_obj, "bot", False)
                except Exception:
                    sender_is_bot = False
                terminus = await classify_conversation_terminus(
                    text=text,
                    thread_messages=[replied_msg.message or ""] if replied_msg else [],
                    sender_is_bot=sender_is_bot,
                )
                if terminus == "RESPOND":
                    logger.info("Reply to Valor detected - continuing session")
                    return True, True
                if terminus == "REACT" and not sender_is_bot:
                    try:
                        from bridge.response import (
                            set_reaction,  # deferred to avoid circular import
                        )

                        await set_reaction(client, event.chat_id, message.id, "👍")
                    except Exception as react_err:
                        logger.debug(f"set_reaction failed (non-fatal): {react_err}")
                logger.info(f"Reply to Valor: terminus={terminus}, not responding")
                return False, True
            elif replied_msg:
                # Reply to a non-Valor message. Remember for session continuation
                # (#996) but don't short-circuit — team chats and Teammate-persona
                # groups must still honor their mention-only policy.
                is_reply_to_non_valor_thread = True
        except Exception as e:
            logger.debug(f"Could not check replied message: {e}")

    # Config-driven Teammate groups: passive listener (mention/reply only, skip Ollama)
    persona = resolve_persona(project, chat_title, is_dm=False)
    if persona == PersonaType.TEAMMATE:
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
        text_lower = text.lower()
        if any(mention.lower() in text_lower for mention in mentions):
            logger.debug("Teammate-persona group: @mention detected - responding")
            return True, is_reply_to_non_valor_thread
        # Completely silent -- no response, no reaction
        logger.debug(f"Teammate-persona group: silent storage for {chat_title!r}")
        return False, False

    # Team chats (no Dev:/PM: prefix) are mention-only
    if is_team_chat(chat_title):
        mentions = telegram_config.get("mention_triggers", DEFAULT_MENTIONS)
        text_lower = text.lower()
        if any(mention.lower() in text_lower for mention in mentions):
            return True, is_reply_to_non_valor_thread
        return False, False

    # Dev:/PM: groups: reply-to-non-Valor triggers session continuation (#996).
    # Must run AFTER team-chat/Teammate gates so mention-only policy wins there.
    if is_reply_to_non_valor_thread:
        logger.info("Reply to non-Valor thread message - treating as session continuation")
        return True, True

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
    should_respond = await classify_needs_response_async(text)
    if not should_respond:
        logger.info(f"Classified as ignore: {text[:50]}...")
        return False, False
    return True, False
