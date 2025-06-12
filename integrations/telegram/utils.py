"""Utility functions for Telegram integration."""


def get_message_text(message):
    """Extract text from a message object."""
    if hasattr(message, "text"):
        return message.text or ""
    if hasattr(message, "caption"):
        return message.caption or ""
    return ""


# Removed hardcoded keyword detection - PM task relevance determined by Valor agent intelligence


# Removed hardcoded priority question detection - Valor agent handles PM context intelligently


async def generate_catchup_response(missed_messages: list[str], anthropic_client) -> str:
    """Generate a brief response to summarize missed messages."""
    if not missed_messages or not anthropic_client:
        return "Hi! I'm back and ready to help with any questions."

    # Get the most recent messages (last 3) for context
    recent_messages = missed_messages[-3:]
    messages_text = "\n".join([f"- {msg}" for msg in recent_messages])

    system_prompt = """You are a technical assistant who was temporarily offline. A user sent messages while you were away. Generate a VERY brief (1-2 sentences max) acknowledgment that:
1. Acknowledges you missed their messages
2. Offers to help with their most recent question/topic
3. Is friendly but concise

DO NOT try to answer the questions in detail - just acknowledge and offer to help."""

    try:
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=150,
            temperature=0.7,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"I sent these messages while you were offline:\n{messages_text}",
                }
            ],
        )

        return response.content[0].text

    except Exception:
        return "Hi! I'm back and caught up on your messages. How can I help?"


def format_dialogs_list(dialogs_data: dict) -> str:
    """Format the dialogs data into a human-readable string.

    Args:
        dialogs_data: Dictionary returned by TelegramClient.list_active_dialogs()

    Returns:
        Formatted string with groups and DMs information
    """
    if not dialogs_data:
        return "No dialog data available"

    output = []
    output.append("📊 **Telegram Dialogs Summary**")
    output.append(
        f"Total: {dialogs_data.get('total_dialogs', 0)} dialogs ({dialogs_data.get('total_groups', 0)} groups, {dialogs_data.get('total_dms', 0)} DMs)"
    )
    output.append("")

    # Format groups
    groups = dialogs_data.get("groups", [])
    if groups:
        output.append("👥 **Groups/Channels:**")
        for group in groups:
            title = group.get("title", "Unknown")
            chat_id = group.get("id", "N/A")
            chat_type = group.get("type", "Unknown")
            member_count = group.get("member_count", "Unknown")
            unread = group.get("unread_count", 0)

            line = f"  • {title} (ID: {chat_id}, Type: {chat_type}"
            if member_count and member_count != "Unknown":
                line += f", Members: {member_count}"
            if unread > 0:
                line += f", Unread: {unread}"
            line += ")"
            output.append(line)
        output.append("")

    # Format DMs
    dms = dialogs_data.get("dms", [])
    if dms:
        output.append("💬 **Direct Messages:**")
        for dm in dms:
            title = dm.get("title", "Unknown")
            chat_id = dm.get("id", "N/A")
            username = dm.get("username", None)
            unread = dm.get("unread_count", 0)
            is_contact = dm.get("is_contact", False)

            line = f"  • {title} (ID: {chat_id}"
            if username:
                line += f", @{username}"
            if is_contact:
                line += ", Contact"
            if unread > 0:
                line += f", Unread: {unread}"
            line += ")"
            output.append(line)

    return "\n".join(output)


async def list_telegram_dialogs_safe(telegram_client) -> tuple[dict | None, str | None]:
    """Safely list Telegram dialogs with error handling.

    Args:
        telegram_client: TelegramClient instance

    Returns:
        Tuple of (dialogs_data, error_message). One will be None.
    """
    try:
        if not telegram_client or not telegram_client.is_connected:
            return None, "Telegram client is not connected"

        dialogs_data = await telegram_client.list_active_dialogs()
        return dialogs_data, None

    except ConnectionError as e:
        return None, f"Connection error: {e}"
    except PermissionError as e:
        return None, f"Permission error: {e}"
    except Exception as e:
        return None, f"Error retrieving dialogs: {e}"
