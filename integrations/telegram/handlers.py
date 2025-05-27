"""Message handlers for Telegram bot functionality."""

from pyrogram.enums import ChatType

from tools.link_analysis_tool import extract_urls, is_url_only_message, store_link_with_analysis

# All functionality now handled by valor_agent (telegram_chat_agent)
from .utils import (
    generate_catchup_response,
    is_message_too_old,
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
        chat_id = message.chat.id

        # Handle different message types
        if message.photo:
            await self._handle_photo_message(client, message, chat_id)
            return
        elif message.document:
            await self._handle_document_message(client, message, chat_id)
            return
        elif message.voice or message.audio:
            await self._handle_audio_message(client, message, chat_id)
            return
        elif message.video or message.video_note:
            await self._handle_video_message(client, message, chat_id)
            return
        elif not message.text:
            # Other message types we don't handle yet
            return

        # Continue with text message processing

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
        """Route message to valor_agent for all text processing."""
        text = processed_text.lower().strip()
        
        # Keep ping-pong for health check
        if text == "ping":
            await self._handle_ping(message, chat_id)
            return
            
        # Use valor_agent for all other message handling
        await self._handle_with_valor_agent(message, chat_id, processed_text)

    async def _handle_with_valor_agent(self, message, chat_id: int, processed_text: str):
        """Handle all messages using valor_agent (telegram_chat_agent)."""
        try:
            # Use telegram_chat_agent directly for all message processing
            from agents.telegram_chat_agent import handle_telegram_message
            
            # Determine if this might be a priority question for context
            is_priority = is_user_priority_question(processed_text) if 'is_user_priority_question' in globals() else False
            
            # Get notion data if this seems like a priority question and we have notion_scout
            notion_data = None
            if is_priority and self.notion_scout:
                try:
                    # For priority questions, try to get relevant Notion data
                    notion_data = await self._get_notion_context(processed_text)
                except Exception as e:
                    print(f"Warning: Could not get Notion context: {e}")

            answer = await handle_telegram_message(
                message=processed_text,
                chat_id=chat_id,
                username=message.from_user.username if message.from_user else None,
                is_group_chat=message.chat.type != ChatType.PRIVATE,
                chat_history_obj=self.chat_history,
                notion_data=notion_data,
                is_priority_question=is_priority,
            )

            # Process the agent response (handles both images and text)
            await self._process_agent_response(message, chat_id, answer)

        except Exception as e:
            error_msg = f"❌ Error processing message: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_ping(self, message, chat_id: int):
        """Handle ping command with system health metrics."""
        try:
            import psutil
            import platform
            from datetime import datetime
            
            # Get system metrics
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Format uptime
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.now() - boot_time
            
            response = f"""🏓 **pong**

📊 **System Health:**
• CPU: {cpu_percent}%
• Memory: {memory.percent}% ({memory.available // (1024**3)}GB free)
• Disk: {disk.percent}% used
• Uptime: {uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m
• Platform: {platform.system()} {platform.release()}

🤖 **Bot Status:**
• Agent: ✅ Active (valor_agent)
• Tools: ✅ {len(self._get_available_tools())} available
• Notion: {'✅ Connected' if self.notion_scout else '❌ Not configured'}"""
            
        except Exception as e:
            # Fallback if psutil not available or error occurs
            response = f"""🏓 **pong**

🤖 **Bot Status:**
• Agent: ✅ Active (valor_agent)
• Notion: {'✅ Connected' if self.notion_scout else '❌ Not configured'}
• Health: ✅ Running

⚠️ Detailed metrics unavailable: {str(e)[:50]}"""
            
        await message.reply(response)
        self.chat_history.add_message(chat_id, "assistant", response)
    
    def _get_available_tools(self) -> list[str]:
        """Get list of available tools for health check."""
        return [
            "search_current_info",
            "create_image", 
            "analyze_shared_image",
            "delegate_coding_task",
            "save_link_for_later",
            "search_saved_links",
            "query_notion_projects"
        ]

    async def _get_notion_context(self, processed_text: str) -> str | None:
        """Get Notion context for priority questions."""
        try:
            if not self.notion_scout:
                return None
                
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
            
            return answer

        except Exception as e:
            print(f"Error getting Notion context: {e}")
            return None


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
                response = "thx, saved."
            else:
                response = "thx, saved. (had trouble analyzing)"

            await message.reply(response)

            # Store response in chat history
            self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error saving link: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_photo_message(self, client, message, chat_id: int):
        """Handle photo messages using PydanticAI agent with vision capabilities."""
        try:
            # Get bot's own info for mention processing
            me = await client.get_me()
            bot_username = me.username
            bot_id = me.id

            # Check if this is a direct message or if bot is mentioned in group
            is_private_chat = message.chat.type == ChatType.PRIVATE
            is_mentioned, caption_text = self._process_mentions(
                message, bot_username, bot_id, is_private_chat
            )

            # Only respond in private chats or when mentioned in groups
            if not (is_private_chat or is_mentioned):
                # Still store the message for context, but don't respond
                if message.caption:
                    self.chat_history.add_message(chat_id, "user", f"[Photo] {message.caption}")
                else:
                    self.chat_history.add_message(chat_id, "user", "[Photo shared]")
                return

            # Download the photo
            file_path = await message.download(in_memory=False)

            # Store user message in chat history
            if caption_text:
                self.chat_history.add_message(chat_id, "user", f"[Photo] {caption_text}")
            else:
                self.chat_history.add_message(chat_id, "user", "[Photo shared]")

            # Use PydanticAI agent to analyze the image
            if self.notion_scout and self.notion_scout.anthropic_client:
                from agents.telegram_chat_agent import handle_telegram_message

                # Prepare message for the agent
                if caption_text:
                    agent_message = f"Please analyze this image: {caption_text}"
                else:
                    agent_message = "Please analyze this image and tell me what you see."

                # Add image path context to the message so the agent can use the tool
                agent_message += f"\n\n[Image downloaded to: {file_path}]"

                answer = await handle_telegram_message(
                    message=agent_message,
                    chat_id=chat_id,
                    username=message.from_user.username if message.from_user else None,
                    is_group_chat=not is_private_chat,
                    chat_history_obj=self.chat_history,
                )

                # Process the response (handles both images and text)
                await self._process_agent_response(message, chat_id, answer)

            else:
                response = "👁️ I can see you shared an image, but I need my AI capabilities configured to analyze it!"
                await message.reply(response)
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing image: {str(e)}"
            await message.reply(error_msg)
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_document_message(self, client, message, chat_id: int):
        """Handle document messages - placeholder for future implementation."""
        try:
            # Get bot's own info for mention processing
            me = await client.get_me()
            is_private_chat = message.chat.type == ChatType.PRIVATE
            is_mentioned = False

            # Check mentions for groups (simplified)
            if not is_private_chat and message.caption:
                is_mentioned = f"@{me.username}" in message.caption

            if is_private_chat or is_mentioned:
                doc_name = message.document.file_name or "unknown file"
                response = f"📄 I see you shared a document: {doc_name}. Document analysis isn't implemented yet, but I'm working on it!"
                await message.reply(response)
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing document: {str(e)}"
            await message.reply(error_msg)

    async def _handle_audio_message(self, client, message, chat_id: int):
        """Handle audio/voice messages - placeholder for future implementation."""
        try:
            # Get bot's own info for mention processing
            me = await client.get_me()
            is_private_chat = message.chat.type == ChatType.PRIVATE
            is_mentioned = False

            # Check mentions for groups (simplified)
            if not is_private_chat and message.caption:
                is_mentioned = f"@{me.username}" in message.caption

            if is_private_chat or is_mentioned:
                if message.voice:
                    response = "🎙️ I hear you sent a voice message! Voice transcription isn't implemented yet, but it's on my roadmap."
                else:
                    response = "🎵 I see you shared an audio file! Audio analysis isn't implemented yet, but I'm working on it."
                await message.reply(response)
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing audio: {str(e)}"
            await message.reply(error_msg)

    async def _handle_video_message(self, client, message, chat_id: int):
        """Handle video messages - placeholder for future implementation."""
        try:
            # Get bot's own info for mention processing
            me = await client.get_me()
            is_private_chat = message.chat.type == ChatType.PRIVATE
            is_mentioned = False

            # Check mentions for groups (simplified)
            if not is_private_chat and message.caption:
                is_mentioned = f"@{me.username}" in message.caption

            if is_private_chat or is_mentioned:
                if message.video_note:
                    response = "📹 I see you sent a video note! Video analysis isn't implemented yet, but it's planned."
                else:
                    response = "🎬 I see you shared a video! Video analysis isn't implemented yet, but I'm working on it."
                await message.reply(response)
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing video: {str(e)}"
            await message.reply(error_msg)

    async def _process_agent_response(self, message, chat_id: int, answer: str, prefix: str = "") -> bool:
        """
        Process agent response, handling image generation and standard text responses.
        
        Args:
            message: Telegram message object
            chat_id: Chat ID for history storage
            answer: Agent response text
            prefix: Optional prefix for text responses
            
        Returns:
            True if image was processed, False if standard text response was sent
        """
        import os
        from pathlib import Path
        
        # Check if response contains generated image
        if answer.startswith("TELEGRAM_IMAGE_GENERATED|"):
            try:
                # Parse the special format: TELEGRAM_IMAGE_GENERATED|path|caption
                parts = answer.split("|", 2)
                if len(parts) == 3:
                    image_path = parts[1]
                    caption = parts[2]
                    
                    # Verify image file exists
                    if Path(image_path).exists():
                        # Send the image with caption
                        await self.client.send_photo(
                            chat_id=chat_id,
                            photo=image_path,
                            caption=caption
                        )
                        
                        # Store response in chat history (without the special format)
                        self.chat_history.add_message(chat_id, "assistant", caption)
                        
                        # Clean up temporary file
                        try:
                            os.remove(image_path)
                            print(f"Cleaned up temporary image: {image_path}")
                        except Exception as cleanup_error:
                            print(f"Warning: Failed to cleanup image {image_path}: {cleanup_error}")
                        
                        return True
                    else:
                        # Image file doesn't exist, send error message
                        error_msg = "🎨 Image was generated but file not found. Please try again."
                        await message.reply(error_msg)
                        self.chat_history.add_message(chat_id, "assistant", error_msg)
                        return True
                        
            except Exception as e:
                error_msg = f"❌ Error sending image: {str(e)}"
                await message.reply(error_msg)
                self.chat_history.add_message(chat_id, "assistant", error_msg)
                return True
        
        # Standard text response handling
        if len(answer) > 4000:
            parts = [answer[i : i + 4000] for i in range(0, len(answer), 4000)]
            for part in parts:
                response_text = f"{prefix} {part}".strip() if prefix else part
                await message.reply(response_text)
            self.chat_history.add_message(chat_id, "assistant", answer)
        else:
            response_text = f"{prefix} {answer}".strip() if prefix else answer
            await message.reply(response_text)
            self.chat_history.add_message(chat_id, "assistant", answer)
        
        return False
