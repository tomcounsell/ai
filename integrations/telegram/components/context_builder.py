"""
ContextBuilder: Unified context building for all message types.

Consolidates context gathering logic from multiple locations into
a single, consistent component.
"""

import logging
import re
from datetime import datetime

# Using pyrogram Message type
from typing import Any, Any as TelegramMessage

# MessageEntityType will be handled differently with pyrogram
from integrations.telegram.models import MediaInfo, MessageContext, MessageType
from integrations.telegram.utils import get_message_text
from utilities.workspace_validator import get_workspace_validator

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Unified context building for all message types."""

    def __init__(self, workspace_validator=None, chat_history_store=None):
        """Initialize with optional dependencies."""
        self.workspace_validator = workspace_validator or get_workspace_validator()
        self.chat_history_store = chat_history_store  # Will use existing store
        self.bot_username = "valoraibot"  # TODO: Get from env

    async def build_context(self, message: TelegramMessage) -> MessageContext:
        """
        Build complete message context in one place.

        Consolidates:
        - Workspace extraction
        - Chat history loading
        - Mention processing
        - Reply context detection
        - Media info extraction

        Returns:
            Complete MessageContext object
        """
        chat_id = message.chat.id
        username = message.from_user.username if message.from_user else "unknown"

        # Extract workspace and working directory
        workspace_info = self._extract_workspace(chat_id)

        # Load chat history
        chat_history = await self._load_chat_history(chat_id)

        # Process mentions and clean text
        is_mention, cleaned_text = self._process_mentions(message)

        # Detect reply context
        reply_context = await self._detect_reply_context(message)

        # Extract media info if present
        media_info = self._extract_media_info(message)

        # Build complete context
        return MessageContext(
            message=message,
            chat_id=chat_id,
            username=username,
            workspace=workspace_info.get("workspace"),
            working_directory=workspace_info.get("working_directory"),
            is_dev_group=workspace_info.get("is_dev_group", False),
            is_mention=is_mention,
            cleaned_text=cleaned_text,
            chat_history=chat_history,
            reply_context=reply_context,
            media_info=media_info,
            timestamp=message.date or datetime.now(),
        )

    def _extract_workspace(self, chat_id: int) -> dict[str, Any]:
        """Get workspace info from chat ID."""
        workspace_info = {"workspace": None, "working_directory": None, "is_dev_group": False}

        try:
            # Get workspace name
            workspace = self.workspace_validator.get_workspace_for_chat(str(chat_id))
            logger.debug(f"Chat ID {chat_id} mapped to workspace: {workspace}")
            if workspace:
                workspace_info["workspace"] = workspace

                # Get working directory using WorkspaceResolver
                try:
                    from utilities.workspace_validator import WorkspaceResolver
                    working_dir, _ = WorkspaceResolver.resolve_working_directory(
                        chat_id=str(chat_id),
                        is_group_chat=chat_id < 0
                    )
                    if working_dir:
                        workspace_info["working_directory"] = working_dir
                except Exception as e:
                    logger.debug(f"Could not resolve working directory for chat {chat_id}: {e}")

                # Check if dev group
                # Get config through file access instead of attribute access
                try:
                    import json
                    from pathlib import Path
                    config_file = Path(__file__).parent.parent.parent / "config" / "workspace_config.json"
                    if config_file.exists():
                        with open(config_file) as f:
                            config = json.load(f)
                    else:
                        config = {"workspaces": {}}
                except Exception:
                    config = {"workspaces": {}}
                workspaces = config.get("workspaces", {})
                workspace_config = workspaces.get(workspace, {})
                workspace_info["is_dev_group"] = workspace_config.get("is_dev_group", False)
                logger.debug(f"Workspace '{workspace}' config: is_dev_group={workspace_info['is_dev_group']}")

        except Exception as e:
            logger.error(f"Error extracting workspace for chat {chat_id}: {str(e)}")

        return workspace_info

    async def _load_chat_history(self, chat_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Load recent conversation history."""
        history = []

        try:
            if self.chat_history_store:
                # Use existing chat history store
                from integrations.telegram.chat_history import get_recent_messages

                recent_messages = await get_recent_messages(chat_id, limit=limit)

                for msg in recent_messages:
                    history.append(
                        {
                            "role": "user" if msg.get("is_user") else "assistant",
                            "content": msg.get("text", ""),
                            "timestamp": msg.get("timestamp"),
                            "username": msg.get("username"),
                            "message_id": msg.get("message_id"),
                        }
                    )
            else:
                # Fallback: Try to load from database
                from utilities.database import get_database_connection

                with get_database_connection() as conn:
                    cursor = conn.execute(
                        """
                        SELECT message_id, username, text, is_bot_message, timestamp
                        FROM chat_messages
                        WHERE chat_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    """,
                        (chat_id, limit),
                    )

                    rows = cursor.fetchall()
                    for row in reversed(rows):  # Oldest first
                        history.append(
                            {
                                "role": "assistant" if row[3] else "user",
                                "content": row[2] or "",
                                "timestamp": row[4],
                                "username": row[1],
                                "message_id": row[0],
                            }
                        )

        except Exception as e:
            logger.error(f"Error loading chat history for {chat_id}: {str(e)}")

        return history

    def _process_mentions(self, message: TelegramMessage) -> tuple[bool, str]:
        """Extract mentions and clean text."""
        text = get_message_text(message)
        if not text:
            return False, ""

        is_mention = False
        cleaned_text = text

        # Check for bot mention in entities
        if message.entities:
            for entity in message.entities:
                if hasattr(entity, "type") and entity.type == "mention":
                    mention_text = text[entity.offset : entity.offset + entity.length]
                    if mention_text.lower() == f"@{self.bot_username.lower()}":
                        is_mention = True
                        # Remove mention from text
                        cleaned_text = (
                            text[: entity.offset] + text[entity.offset + entity.length :]
                        ).strip()

        # Fallback: Check for @mention in text
        if not is_mention:
            mention_pattern = f"@{self.bot_username}"
            if mention_pattern.lower() in text.lower():
                is_mention = True
                cleaned_text = re.sub(
                    f"@{self.bot_username}", "", text, flags=re.IGNORECASE
                ).strip()

        # Clean up extra whitespace
        cleaned_text = " ".join(cleaned_text.split())

        return is_mention, cleaned_text

    async def _detect_reply_context(self, message: TelegramMessage) -> dict[str, Any] | None:
        """Extract reply-to message context."""
        if not message.reply_to_message:
            return None

        reply_msg = message.reply_to_message
        reply_context = {
            "message_id": reply_msg.id,
            "text": get_message_text(reply_msg),
            "username": reply_msg.from_user.username if reply_msg.from_user else None,
            "is_bot": reply_msg.from_user.is_bot if reply_msg.from_user else False,
            "timestamp": reply_msg.date,
        }

        # Add media type if present
        if reply_msg.photo:
            reply_context["media_type"] = "photo"
        elif reply_msg.document:
            reply_context["media_type"] = "document"
        elif reply_msg.audio:
            reply_context["media_type"] = "audio"
        elif reply_msg.video:
            reply_context["media_type"] = "video"

        return reply_context

    def _extract_media_info(self, message: TelegramMessage) -> MediaInfo | None:
        """Extract media information from message."""
        if message.photo:
            # In Pyrogram, message.photo is a Photo object, not a list
            photo = message.photo
            return MediaInfo(
                media_type=MessageType.PHOTO,
                file_id=photo.file_id,
                file_unique_id=photo.file_unique_id,
                file_size=getattr(photo, 'file_size', 0),
                width=getattr(photo, 'width', 0),
                height=getattr(photo, 'height', 0),
            )

        elif message.document:
            doc = message.document
            return MediaInfo(
                media_type=MessageType.DOCUMENT,
                file_id=doc.file_id,
                file_unique_id=doc.file_unique_id,
                file_size=doc.file_size,
                mime_type=doc.mime_type,
                file_name=doc.file_name,
                thumbnail_file_id=doc.thumbnail.file_id if doc.thumbnail else None,
            )

        elif message.audio:
            audio = message.audio
            return MediaInfo(
                media_type=MessageType.AUDIO,
                file_id=audio.file_id,
                file_unique_id=audio.file_unique_id,
                file_size=audio.file_size,
                mime_type=audio.mime_type,
                duration=audio.duration,
                thumbnail_file_id=audio.thumbnail.file_id if audio.thumbnail else None,
            )

        elif message.video:
            video = message.video
            return MediaInfo(
                media_type=MessageType.VIDEO,
                file_id=video.file_id,
                file_unique_id=video.file_unique_id,
                file_size=video.file_size,
                mime_type=video.mime_type,
                width=video.width,
                height=video.height,
                duration=video.duration,
                thumbnail_file_id=video.thumbnail.file_id if video.thumbnail else None,
            )

        elif message.voice:
            voice = message.voice
            return MediaInfo(
                media_type=MessageType.VOICE,
                file_id=voice.file_id,
                file_unique_id=voice.file_unique_id,
                file_size=voice.file_size,
                mime_type=voice.mime_type,
                duration=voice.duration,
            )

        elif message.video_note:
            video_note = message.video_note
            return MediaInfo(
                media_type=MessageType.VIDEO_NOTE,
                file_id=video_note.file_id,
                file_unique_id=video_note.file_unique_id,
                file_size=video_note.file_size,
                duration=video_note.duration,
                width=video_note.length,  # Video notes are square
                height=video_note.length,
            )

        return None

    def extract_urls(self, text: str) -> list[str]:
        """Extract URLs from text."""
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        return re.findall(url_pattern, text)

    def detect_code_blocks(self, text: str) -> list[dict[str, str]]:
        """Detect code blocks in text."""
        code_blocks = []

        # Markdown code blocks
        markdown_pattern = r"```(\w*)\n(.*?)\n```"
        for match in re.finditer(markdown_pattern, text, re.DOTALL):
            code_blocks.append({"language": match.group(1) or "unknown", "code": match.group(2)})

        # Inline code
        inline_pattern = r"`([^`]+)`"
        for match in re.finditer(inline_pattern, text):
            code_blocks.append({"language": "inline", "code": match.group(1)})

        return code_blocks
