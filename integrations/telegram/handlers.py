"""Message handlers for Telegram bot functionality."""

from pyrogram.enums import ChatType

from tools.link_analysis_tool import extract_urls, is_url_only_message, store_link_with_analysis

# Search functionality now handled by PydanticAI agents
from .response_handlers import handle_general_question, handle_user_priority_question
from .utils import (
    generate_catchup_response,
    is_message_too_old,
    is_notion_question,
    is_user_priority_question,
)


class MessageHandler:
    """Handles incoming Telegram messages with context awareness."""

    def __init__(self, client, chat_history, notion_scout=None, bot_start_time=None):
        self.client = client
        self.chat_history = chat_history
        self.notion_scout = notion_scout
        self.bot_start_time = bot_start_time
        self.missed_messages_per_chat = {}
        # Web search now handled by PydanticAI agents
        # Link analysis now handled by link_analysis_tool

    async def handle_message(self, client, message):
        """Main message handling logic with routing to appropriate handlers."""
        if not message.text:
            return

        chat_id = message.chat.id

        # Check if message is too old (catch-up from offline period)
        if is_message_too_old(message.date.timestamp()):
            # Collect missed messages for later batch response
            if chat_id not in self.missed_messages_per_chat:
                self.missed_messages_per_chat[chat_id] = []
            self.missed_messages_per_chat[chat_id].append(message.text)

            # Still store old messages for context
            self.chat_history.add_message(chat_id, "user", message.text)
            print(f"Collecting missed message from chat {chat_id}: {message.text[:50]}...")
            return

        # Get bot's own info
        me = await client.get_me()
        bot_username = me.username
        bot_id = me.id

        # Check if this is a direct message or if bot is mentioned in group
        is_private_chat = message.chat.type == ChatType.PRIVATE

        print(
            f"Processing message from chat {chat_id} (private: {is_private_chat}): '{message.text[:50]}...'"
        )

        # Debug: Check current chat history length to detect duplication
        current_history_count = len(self.chat_history.chat_histories.get(chat_id, []))
        print(f"Current chat history length: {current_history_count}")

        # Check if we have missed messages for this chat and respond to them first
        if chat_id in self.missed_messages_per_chat and self.missed_messages_per_chat[chat_id]:
            await self._handle_missed_messages(chat_id, message)

        # Handle group mentions and process message
        is_mentioned, processed_text = self._process_mentions(
            message, bot_username, bot_id, is_private_chat
        )

        # Only respond in private chats or when mentioned in groups
        if not (is_private_chat or is_mentioned):
            # Still store the message for context, but don't respond
            self.chat_history.add_message(chat_id, "user", message.text)
            return

        # Store user message in chat history
        self.chat_history.add_message(chat_id, "user", processed_text)

        # Check if this is a single-link message for link tracking
        if is_url_only_message(processed_text):
            await self._handle_link_message(message, chat_id, processed_text)
            return

        # Route to appropriate handler
        await self._route_message(message, chat_id, processed_text)

    async def _handle_missed_messages(self, chat_id: int, message):
        """Handle catch-up response for missed messages."""
        try:
            if self.notion_scout and self.notion_scout.anthropic_client:
                catchup_response = await generate_catchup_response(
                    self.missed_messages_per_chat[chat_id], self.notion_scout.anthropic_client
                )
                await message.reply(f"📬 {catchup_response}")
                self.chat_history.add_message(chat_id, "assistant", catchup_response)

            # Clear missed messages for this chat
            del self.missed_messages_per_chat[chat_id]

        except Exception as e:
            print(f"Error sending catch-up response: {e}")
            # Clear anyway to avoid getting stuck
            if chat_id in self.missed_messages_per_chat:
                del self.missed_messages_per_chat[chat_id]

    def _process_mentions(
        self, message, bot_username: str, bot_id: int, is_private_chat: bool
    ) -> tuple[bool, str]:
        """Process @mentions and return whether bot was mentioned and cleaned text."""
        is_mentioned = False
        processed_text = message.text

        # Check for @mentions in groups
        if not is_private_chat:
            # Check if bot is mentioned with @username
            if f"@{bot_username}" in message.text:
                is_mentioned = True
                # Remove the @mention from the text for processing
                processed_text = message.text.replace(f"@{bot_username}", "").strip()

            # Check if bot is mentioned via reply to bot's message
            elif message.reply_to_message and message.reply_to_message.from_user.id == bot_id:
                is_mentioned = True

            # Check if message has entities (mentions, text_mentions)
            elif message.entities:
                for entity in message.entities:
                    if entity.type == "mention":
                        # Extract the mentioned username
                        mentioned_text = message.text[entity.offset : entity.offset + entity.length]
                        if mentioned_text == f"@{bot_username}":
                            is_mentioned = True
                            # Remove the mention from processed text
                            processed_text = (
                                message.text[: entity.offset]
                                + message.text[entity.offset + entity.length :]
                            ).strip()
                            break
                    elif entity.type == "text_mention" and entity.user.id == bot_id:
                        is_mentioned = True
                        # Remove the mention from processed text
                        processed_text = (
                            message.text[: entity.offset]
                            + message.text[entity.offset + entity.length :]
                        ).strip()
                        break

        return is_mentioned, processed_text

    async def _route_message(self, message, chat_id: int, processed_text: str):
        """Route message to appropriate handler based on content."""
        text = processed_text.lower()

        # Basic commands
        if text == "ping":
            response = "pong"
            await message.reply(response)
            self.chat_history.add_message(chat_id, "assistant", response)

        elif text == "status":
            response = "AI Project API is running and listening!"
            await message.reply(response)
            self.chat_history.add_message(chat_id, "assistant", response)

        elif text.startswith("help") or text == "":
            response = """🤖 Available commands:

• ping - Test bot responsiveness
• status - Check API status
• Ask any question - I can search the web for current info!
• Ask about your Notion projects!
• Request coding tasks - I can delegate to Claude Code!

Examples:
• "What tasks are ready for dev?"
• "Show me project PsyOPTIMAL status"
• "What's the latest AI news?"
• "Create a todo app in /tmp"

💡 In groups, just @mention me with your question!
"""
            await message.reply(response)
            self.chat_history.add_message(chat_id, "assistant", response)

        # User priority questions - check first for work-related queries
        elif is_user_priority_question(processed_text):
            await self._handle_priority_question(message, chat_id, processed_text)

        # Notion integration - check for specific Notion keywords
        elif self.notion_scout and is_notion_question(processed_text):
            await self._handle_notion_question(message, chat_id, processed_text)

        # General questions - use persona for any other meaningful text
        elif len(processed_text.strip()) > 2:  # Ignore very short messages
            await self._handle_general_question(message, chat_id, processed_text)

        # Fallback for very short or unrecognized commands
        else:
            response = "🤔 Could you provide more details? I'm here to help with technical questions and Notion queries!"
            await message.reply(response)
            self.chat_history.add_message(chat_id, "assistant", response)

    async def _handle_priority_question(self, message, chat_id: int, processed_text: str):
        """Handle user priority questions."""
        try:
            await message.reply("🎯 Checking your current priorities...")

            # Use specialized priority handler
            answer = await handle_user_priority_question(
                processed_text,
                self.notion_scout.anthropic_client if self.notion_scout else None,
                chat_id,
                self.notion_scout,
                self.chat_history,
            )

            # Split long messages for Telegram
            if len(answer) > 4000:
                parts = [answer[i : i + 4000] for i in range(0, len(answer), 4000)]
                for part in parts:
                    await message.reply(part)
                full_response = "\n".join(parts)
            else:
                full_response = f"🎯 {answer}"
                await message.reply(full_response)

            self.chat_history.add_message(chat_id, "assistant", answer)

        except Exception as e:
            error_msg = f"❌ Error checking priorities: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_notion_question(self, message, chat_id: int, processed_text: str):
        """Handle Notion-related questions."""
        try:
            await message.reply("🔍 Searching your Notion databases...")

            # Check if specific project mentioned
            text_lower = processed_text.lower()
            for project_name in ["psyoptimal", "flextrip", "psy", "flex"]:
                if project_name in text_lower:
                    from ..notion.utils import resolve_project_name

                    resolved_name, db_id = resolve_project_name(project_name)
                    if db_id:
                        self.notion_scout.db_filter = db_id[:8]
                        break

            # Get answer from Notion Scout
            answer = await self.notion_scout.answer_question(processed_text)

            # Reset filter for next query
            self.notion_scout.db_filter = None

            # Split long messages for Telegram
            if len(answer) > 4000:
                parts = [answer[i : i + 4000] for i in range(0, len(answer), 4000)]
                for part in parts:
                    await message.reply(part)
                full_response = "\n".join(parts)
            else:
                full_response = f"🎯 **Notion Scout Results**\n\n{answer}"
                await message.reply(full_response)

            self.chat_history.add_message(chat_id, "assistant", full_response)

        except Exception as e:
            error_msg = f"❌ Error querying Notion: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_general_question(self, message, chat_id: int, processed_text: str):
        """Handle general questions using persona."""
        try:
            # Use the same anthropic client from notion_scout
            if self.notion_scout and self.notion_scout.anthropic_client:
                answer = await handle_general_question(
                    processed_text, self.notion_scout.anthropic_client, chat_id, self.chat_history
                )

                # Split long messages for Telegram
                if len(answer) > 4000:
                    parts = [answer[i : i + 4000] for i in range(0, len(answer), 4000)]
                    for part in parts:
                        await message.reply(part)
                    "\n".join(parts)
                else:
                    await message.reply(answer)

                self.chat_history.add_message(
                    chat_id, "assistant", answer
                )  # Store without emoji prefix
            else:
                response = "💭 I'd love to help, but I need my AI capabilities configured first!"
                await message.reply(response)
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing question: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_link_message(self, message, chat_id: int, processed_text: str):
        """Handle messages that contain only a URL - store with AI analysis."""
        try:
            # Extract the URL from the message
            urls = extract_urls(processed_text.strip())
            if not urls:
                return  # Shouldn't happen if is_url_only_message returned True

            url = urls[0]

            # Get user info for storage
            username = None
            if message.from_user:
                username = message.from_user.username or message.from_user.first_name

            # Store the link with AI analysis
            success = store_link_with_analysis(
                url=url, chat_id=chat_id, message_id=message.id, username=username
            )

            if success:
                response = "🔗 Thanks, I saved the link!"
            else:
                response = "🔗 Thanks! (Had trouble analyzing but saved the link)"

            await message.reply(response)

            # Store response in chat history
            self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error saving link: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)
