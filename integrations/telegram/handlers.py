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
        
        # Load chat filtering configuration from environment
        self._load_chat_filters()

    def _load_chat_filters(self):
        """Load chat filtering configuration from environment variables with validation."""
        import os
        import logging
        from utilities.workspace_validator import validate_telegram_environment
        
        logger = logging.getLogger(__name__)
        
        try:
            # Validate environment configuration
            validation_results = validate_telegram_environment()
            
            if validation_results["status"] == "failed":
                logger.error(f"Telegram environment validation failed: {validation_results['errors']}")
                # Set safe defaults
                self.allowed_groups = set()
                self.allow_dms = False
                return
            
            # Parse allowed groups from environment
            allowed_groups_env = os.getenv("TELEGRAM_ALLOWED_GROUPS", "")
            self.allowed_groups = set()
            if allowed_groups_env.strip():
                try:
                    # Parse comma-separated group chat IDs
                    group_ids = [int(group_id.strip()) for group_id in allowed_groups_env.split(",") if group_id.strip()]
                    self.allowed_groups = set(group_ids)
                    logger.info(f"Group whitelist configured: {len(self.allowed_groups)} groups allowed")
                except ValueError as e:
                    logger.error(f"Error parsing TELEGRAM_ALLOWED_GROUPS: {e}. Denying all groups.")
                    self.allowed_groups = set()
            else:
                logger.info("No groups specified in TELEGRAM_ALLOWED_GROUPS. Denying all groups.")
            
            # Parse DM setting from environment
            allow_dms_env = os.getenv("TELEGRAM_ALLOW_DMS", "true").lower().strip()
            self.allow_dms = allow_dms_env in ("true", "1", "yes", "on")
            logger.info(f"DM handling: {'Enabled' if self.allow_dms else 'Disabled'}")
            
            # Log validation warnings if present
            if validation_results.get("errors"):
                for error in validation_results["errors"]:
                    logger.warning(f"Environment validation warning: {error}")
        
        except Exception as e:
            logger.error(f"Failed to load chat filters: {e}")
            # Set safe defaults on error
            self.allowed_groups = set()
            self.allow_dms = False
    
    def _should_handle_chat(self, chat_id: int, is_private_chat: bool = False) -> bool:
        """Check if this server instance should handle messages from the given chat with enhanced validation."""
        import logging
        from utilities.workspace_validator import validate_chat_whitelist_access
        
        logger = logging.getLogger(__name__)
        
        try:
            # Use centralized validation function for consistency
            is_allowed = validate_chat_whitelist_access(chat_id, is_private_chat)
            
            if not is_allowed:
                chat_type = "DM" if is_private_chat else "group"
                logger.warning(f"Chat access denied: {chat_type} {chat_id} not in whitelist")
            
            return is_allowed
            
        except Exception as e:
            # Log error and deny access for safety
            logger.error(f"Chat validation failed for {chat_id}: {e}")
            return False

    async def handle_message(self, client, message):
        """Main message handling logic with routing to appropriate handlers."""
        chat_id = message.chat.id
        is_private_chat = message.chat.type == ChatType.PRIVATE
        
        # Check if this server instance should handle this chat
        if not self._should_handle_chat(chat_id, is_private_chat):
            import logging
            logger = logging.getLogger(__name__)
            
            chat_type = "DM" if is_private_chat else "group"
            username = message.from_user.username if message.from_user else "unknown"
            message_preview = (message.text[:50] + "...") if message.text and len(message.text) > 50 else (message.text or "[no text]")
            
            # Enhanced logging for security audit trail
            logger.warning(
                f"MESSAGE REJECTED - Chat whitelist violation: "
                f"{chat_type} {chat_id} from user @{username} - "
                f"Message: '{message_preview}'"
            )
            
            # Also log to console for immediate visibility during development
            print(f"🚫 Rejected {chat_type} {chat_id} (@{username}): {message_preview}")
            return

        # Mark message as read (read receipt)
        try:
            await client.read_chat_history(chat_id, message.id)
        except Exception as e:
            print(f"Warning: Could not mark message as read: {e}")

        # Add reaction to show message is being processed
        try:
            await client.send_reaction(chat_id, message.id, "👀")
        except Exception as e:
            print(f"Warning: Could not add reaction: {e}")

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
            reply_to_telegram_message_id = None
            if hasattr(message, 'reply_to_message') and message.reply_to_message:
                reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)
            self.chat_history.add_message(chat_id, "user", message.text, reply_to_telegram_message_id, message.id, is_telegram_id=True)
            print(f"Collecting missed message from chat {chat_id}: {message.text[:50]}...")
            return

        # Get bot's own info
        me = await client.get_me()
        bot_username = me.username
        bot_id = me.id

        # is_private_chat already determined above for filtering

        print(
            f"Processing message from chat {chat_id} (private: {is_private_chat}): '{message.text[:50]}...'"
        )

        # Debug: Check current chat history length to detect duplication
        current_history_count = len(self.chat_history.chat_histories.get(chat_id, []))
        print(f"Current chat history length: {current_history_count}")

        # Check if we have missed messages for this chat and respond to them first
        if chat_id in self.missed_messages_per_chat and self.missed_messages_per_chat[chat_id]:
            await self._handle_missed_messages(chat_id, message)

        # Handle group mentions and process message with error handling
        try:
            is_mentioned, processed_text = self._process_mentions(
                message, bot_username, bot_id, is_private_chat
            )
        except Exception as e:
            print(f"Error processing mentions: {e}")
            # Fallback: treat as regular message without mentions
            is_mentioned = is_private_chat  # Only respond in private chats if error
            processed_text = getattr(message, 'text', None) or getattr(message, 'caption', None) or ""

        # Extract reply information for context building
        reply_to_telegram_message_id = None
        if hasattr(message, 'reply_to_message') and message.reply_to_message:
            reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)

        # Only respond in private chats or when mentioned in groups
        if not (is_private_chat or is_mentioned):
            # Still store the message for context, but don't respond
            self.chat_history.add_message(chat_id, "user", message.text, reply_to_telegram_message_id, message.id, is_telegram_id=True)
            return

        # Store user message in chat history with reply context
        self.chat_history.add_message(chat_id, "user", processed_text, reply_to_telegram_message_id, message.id, is_telegram_id=True)

        # Check if this is a single-link message for link tracking
        if is_url_only_message(processed_text):
            await self._handle_link_message(message, chat_id, processed_text)
            return

        # Route to appropriate handler
        await self._route_message(message, chat_id, processed_text, reply_to_telegram_message_id)

    async def _handle_missed_messages(self, chat_id: int, message):
        """Handle catch-up response for missed messages."""
        try:
            if self.notion_scout and self.notion_scout.anthropic_client:
                catchup_response = await generate_catchup_response(
                    self.missed_messages_per_chat[chat_id], self.notion_scout.anthropic_client
                )
                await self._safe_reply(message, f"📬 {catchup_response}", "📬 Caught up on missed messages")
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
        
        # Get text content from either message.text or message.caption (for photos/videos)
        # Handle all possible None cases explicitly
        text_content = getattr(message, 'text', None) or getattr(message, 'caption', None) or ""
        processed_text = text_content

        # Validate inputs
        if not bot_username or not isinstance(bot_id, int):
            return is_mentioned, processed_text

        # Check for @mentions in groups
        if not is_private_chat and text_content:
            # Check if bot is mentioned with @username
            if f"@{bot_username}" in text_content:
                is_mentioned = True
                # Remove the @mention from the text for processing
                processed_text = text_content.replace(f"@{bot_username}", "").strip()

            # Check if bot is mentioned via reply to bot's message
            elif (hasattr(message, 'reply_to_message') and 
                  message.reply_to_message and 
                  hasattr(message.reply_to_message, 'from_user') and
                  message.reply_to_message.from_user and
                  message.reply_to_message.from_user.id == bot_id):
                is_mentioned = True

            # Check if message has entities (mentions, text_mentions)
            # Handle both regular entities and caption entities
            entities_to_check = []
            if hasattr(message, 'entities') and message.entities:
                try:
                    entities_to_check.extend(message.entities)
                except TypeError:
                    # Handle mock objects or non-iterable entities
                    if message.entities is not None:
                        entities_to_check.append(message.entities)
            if hasattr(message, 'caption_entities') and message.caption_entities:
                try:
                    entities_to_check.extend(message.caption_entities)
                except TypeError:
                    # Handle mock objects or non-iterable caption_entities
                    if message.caption_entities is not None:
                        entities_to_check.append(message.caption_entities)
            
            if entities_to_check:
                for entity in entities_to_check:
                    try:
                        if entity.type == "mention":
                            # Extract the mentioned username with bounds checking
                            start_offset = max(0, entity.offset)
                            end_offset = min(len(text_content), entity.offset + entity.length)
                            mentioned_text = text_content[start_offset:end_offset]
                            if mentioned_text == f"@{bot_username}":
                                is_mentioned = True
                                # Remove the mention from processed text
                                processed_text = (
                                    text_content[:start_offset]
                                    + text_content[end_offset:]
                                ).strip()
                                break
                        elif (entity.type == "text_mention" and 
                              hasattr(entity, 'user') and 
                              entity.user and 
                              entity.user.id == bot_id):
                            is_mentioned = True
                            # Remove the mention from processed text with bounds checking
                            start_offset = max(0, entity.offset)
                            end_offset = min(len(text_content), entity.offset + entity.length)
                            processed_text = (
                                text_content[:start_offset]
                                + text_content[end_offset:]
                            ).strip()
                            break
                    except (AttributeError, IndexError, TypeError) as e:
                        # Log entity processing error but continue
                        print(f"Warning: Error processing entity {entity}: {e}")
                        continue

        return is_mentioned, processed_text

    async def _route_message(self, message, chat_id: int, processed_text: str, reply_to_telegram_message_id: int = None):
        """Route message to valor_agent for all text processing."""
        text = processed_text.lower().strip()

        # Keep ping-pong for health check
        if text == "ping":
            await self._handle_ping(message, chat_id)
            return

        # Use valor_agent for all other message handling
        await self._handle_with_valor_agent(message, chat_id, processed_text, reply_to_telegram_message_id)

    async def _handle_with_valor_agent(self, message, chat_id: int, processed_text: str, reply_to_telegram_message_id: int = None):
        """Handle all messages using valor agent system."""
        try:
            # Use valor agent for message processing
            from agents.valor.handlers import handle_telegram_message

            # Determine if this might be a priority question for context
            is_priority = (
                is_user_priority_question(processed_text)
                if "is_user_priority_question" in globals()
                else False
            )

            # Get notion data - prioritize group-specific database, fallback to priority question detection
            notion_data = None
            if self.notion_scout:
                try:
                    # For group chats, use the group-specific Notion database
                    is_private_chat = message.chat.type == ChatType.PRIVATE
                    if not is_private_chat:
                        notion_data = await self._get_notion_context_for_group(chat_id, processed_text)
                    # For DMs or if no group-specific database, check if it's a priority question
                    elif is_priority:
                        notion_data = await self._get_notion_context(processed_text)
                except Exception as e:
                    print(f"Warning: Could not get Notion context: {e}")

            # Get chat history for context with reply priority
            reply_internal_id = None
            if reply_to_telegram_message_id:
                reply_internal_id = self.chat_history.get_internal_message_id(chat_id, reply_to_telegram_message_id)
            
            if reply_internal_id:
                print(f"🔗 Using reply-aware context for message replying to internal ID {reply_internal_id} (Telegram ID: {reply_to_telegram_message_id})")
                chat_history = self.chat_history.get_context_with_reply_priority(
                    chat_id, reply_internal_id, max_context_messages=10
                )
            else:
                chat_history = self.chat_history.get_context(chat_id, max_context_messages=10)

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
            await self._safe_reply(message, error_msg, "❌ Error processing message")
            self.chat_history.add_message(chat_id, "assistant", error_msg)

    async def _handle_ping(self, message, chat_id: int):
        """Handle ping command with system health metrics."""
        try:
            import platform
            from datetime import datetime

            import psutil

            # Get system metrics
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

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

        await self._safe_reply(message, response, "🏓 pong - Bot is running")
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
            "query_notion_projects",
        ]

    async def _get_notion_context_for_group(self, chat_id: int, processed_text: str) -> str | None:
        """Get Notion context for group-specific database."""
        try:
            if not self.notion_scout:
                return None

            # Get the project associated with this Telegram group
            from ..notion.utils import get_telegram_group_project
            
            project_name, db_id = get_telegram_group_project(chat_id)
            if not db_id:
                print(f"No Notion database configured for group {chat_id}")
                return None

            print(f"Using Notion database for {project_name} (group {chat_id})")
            
            # Set the database filter for this specific project
            self.notion_scout.db_filter = db_id[:8]

            # Get answer from Notion Scout
            answer = await self.notion_scout.answer_question(processed_text)

            # Reset filter for next query
            self.notion_scout.db_filter = None

            return answer

        except Exception as e:
            print(f"Error getting group-specific Notion context: {e}")
            return None

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

            await self._safe_reply(message, response, "Link saved")

            # Store response in chat history
            self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error saving link: {str(e)}"
            await self._safe_reply(message, error_msg, "❌ Error saving link")
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
            try:
                is_mentioned, caption_text = self._process_mentions(
                    message, bot_username, bot_id, is_private_chat
                )
            except Exception as e:
                print(f"Error processing photo mentions: {e}")
                # Fallback: treat as regular photo without mentions
                is_mentioned = is_private_chat  # Only respond in private chats if error
                caption_text = getattr(message, 'caption', None) or ""

            # Extract reply information for context building
            reply_to_telegram_message_id = None
            if hasattr(message, 'reply_to_message') and message.reply_to_message:
                reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)

            # Only respond in private chats or when mentioned in groups
            if not (is_private_chat or is_mentioned):
                # Still store the message for context, but don't respond
                if message.caption:
                    # Explicitly indicate this message contains BOTH text and image  
                    self.chat_history.add_message(chat_id, "user", f"[Image+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                else:
                    self.chat_history.add_message(chat_id, "user", "[Image]", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                return

            # Download the photo
            file_path = await message.download(in_memory=False)

            # Store user message in chat history with reply context
            if caption_text:
                # Explicitly indicate this message contains BOTH text and image
                self.chat_history.add_message(chat_id, "user", f"[Image+Text] {caption_text}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
            else:
                self.chat_history.add_message(chat_id, "user", "[Image]", reply_to_telegram_message_id, message.id, is_telegram_id=True)

            # Use valor agent system to analyze the image
            if self.notion_scout and self.notion_scout.anthropic_client:
                try:
                    from agents.valor.handlers import handle_telegram_message

                    # Get chat history for context with reply priority
                    reply_internal_id = None
                    if reply_to_telegram_message_id:
                        reply_internal_id = self.chat_history.get_internal_message_id(chat_id, reply_to_telegram_message_id)
                    
                    if reply_internal_id:
                        print(f"🔗 Using reply-aware context for photo message replying to internal ID {reply_internal_id} (Telegram ID: {reply_to_telegram_message_id})")
                        chat_history = self.chat_history.get_context_with_reply_priority(
                            chat_id, reply_internal_id, max_context_messages=5
                        )
                    else:
                        chat_history = self.chat_history.get_context(chat_id, max_context_messages=5)

                    # Prepare message for the agent with explicit text+image indication
                    if caption_text:
                        agent_message = f"🖼️📝 MIXED CONTENT MESSAGE: This message contains BOTH TEXT AND AN IMAGE.\n\nUser's text: {caption_text}\n\nThe user has shared both text content (above) and an image. Please analyze and respond to BOTH components - the text message and the visual content in the image."
                    else:
                        agent_message = "🖼️ IMAGE MESSAGE: The user has shared an image. Please analyze the image and describe what you see."

                    # Add image path context to the message so the agent can use the tool
                    agent_message += f"\n\n[Image file path: {file_path}]"

                    answer = await handle_telegram_message(
                        message=agent_message,
                        chat_id=chat_id,
                        username=message.from_user.username if message.from_user else None,
                        is_group_chat=not is_private_chat,
                        chat_history_obj=self.chat_history,
                    )

                    # Process the response (handles both images and text)
                    await self._process_agent_response(message, chat_id, answer)

                except Exception as e:
                    error_msg = f"❌ Error processing image with agent: {str(e)}"
                    await self._safe_reply(message, error_msg, "❌ Error processing image")
                    self.chat_history.add_message(chat_id, "assistant", error_msg)

            else:
                response = "👁️ I can see you shared an image, but I need my AI capabilities configured to analyze it!"
                await self._safe_reply(message, response, "👁️ Image received")
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing image: {str(e)}"
            await self._safe_reply(message, error_msg, "❌ Error processing image")
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

            # Extract reply information for context building
            reply_to_telegram_message_id = None
            if hasattr(message, 'reply_to_message') and message.reply_to_message:
                reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)

            # Store message in chat history even if not responding
            if not (is_private_chat or is_mentioned):
                if message.caption:
                    self.chat_history.add_message(chat_id, "user", f"[Document+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                else:
                    self.chat_history.add_message(chat_id, "user", "[Document]", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                return

            # Store user message in chat history
            if message.caption:
                self.chat_history.add_message(chat_id, "user", f"[Document+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
            else:
                self.chat_history.add_message(chat_id, "user", "[Document]", reply_to_telegram_message_id, message.id, is_telegram_id=True)

            if is_private_chat or is_mentioned:
                doc_name = message.document.file_name or "unknown file"
                if message.caption:
                    response = f"📄 I see you shared a document '{doc_name}' with text: '{message.caption}'. Document analysis isn't implemented yet, but I'm working on it!"
                else:
                    response = f"📄 I see you shared a document: {doc_name}. Document analysis isn't implemented yet, but I'm working on it!"
                await self._safe_reply(message, response, "📄 Document received")
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing document: {str(e)}"
            await self._safe_reply(message, error_msg, "❌ Error processing document")

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

            # Extract reply information for context building
            reply_to_telegram_message_id = None
            if hasattr(message, 'reply_to_message') and message.reply_to_message:
                reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)

            # Store message in chat history even if not responding
            if not (is_private_chat or is_mentioned):
                if message.caption:
                    audio_type = "Voice" if message.voice else "Audio"
                    self.chat_history.add_message(chat_id, "user", f"[{audio_type}+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                else:
                    audio_type = "[Voice]" if message.voice else "[Audio]"
                    self.chat_history.add_message(chat_id, "user", audio_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)
                return

            # Store user message in chat history
            if message.caption:
                audio_type = "Voice" if message.voice else "Audio"
                self.chat_history.add_message(chat_id, "user", f"[{audio_type}+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
            else:
                audio_type = "[Voice]" if message.voice else "[Audio]"
                self.chat_history.add_message(chat_id, "user", audio_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)

            if is_private_chat or is_mentioned:
                if message.voice:
                    if message.caption:
                        response = f"🎙️ I hear you sent a voice message with text: '{message.caption}'. Voice transcription isn't implemented yet, but it's on my roadmap."
                    else:
                        response = "🎙️ I hear you sent a voice message! Voice transcription isn't implemented yet, but it's on my roadmap."
                else:
                    if message.caption:
                        response = f"🎵 I see you shared an audio file with text: '{message.caption}'. Audio analysis isn't implemented yet, but I'm working on it."
                    else:
                        response = "🎵 I see you shared an audio file! Audio analysis isn't implemented yet, but I'm working on it."
                await self._safe_reply(message, response, "🎵 Audio received")
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing audio: {str(e)}"
            await self._safe_reply(message, error_msg, "❌ Error processing audio")

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

            # Extract reply information for context building
            reply_to_telegram_message_id = None
            if hasattr(message, 'reply_to_message') and message.reply_to_message:
                reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)

            # Store message in chat history even if not responding
            if not (is_private_chat or is_mentioned):
                if message.caption:
                    self.chat_history.add_message(chat_id, "user", f"[Video+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                else:
                    video_type = "[VideoNote]" if message.video_note else "[Video]"
                    self.chat_history.add_message(chat_id, "user", video_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)
                return

            # Store user message in chat history
            if message.caption:
                self.chat_history.add_message(chat_id, "user", f"[Video+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
            else:
                video_type = "[VideoNote]" if message.video_note else "[Video]"
                self.chat_history.add_message(chat_id, "user", video_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)

            if is_private_chat or is_mentioned:
                if message.video_note:
                    if message.caption:
                        response = f"📹 I see you sent a video note with text: '{message.caption}'. Video analysis isn't implemented yet, but it's planned."
                    else:
                        response = "📹 I see you sent a video note! Video analysis isn't implemented yet, but it's planned."
                else:
                    if message.caption:
                        response = f"🎬 I see you shared a video with text: '{message.caption}'. Video analysis isn't implemented yet, but I'm working on it."
                    else:
                        response = "🎬 I see you shared a video! Video analysis isn't implemented yet, but I'm working on it."
                await self._safe_reply(message, response, "🎬 Video received")
                self.chat_history.add_message(chat_id, "assistant", response)

        except Exception as e:
            error_msg = f"❌ Error processing video: {str(e)}"
            await self._safe_reply(message, error_msg, "❌ Error processing video")

    async def _process_agent_response(
        self, message, chat_id: int, answer: str, prefix: str = ""
    ) -> bool:
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

        # Validate input parameters
        if not isinstance(answer, str):
            answer = str(answer) if answer is not None else ""
        
        if not isinstance(prefix, str):
            prefix = str(prefix) if prefix is not None else ""

        # Check if response contains generated image
        if answer.startswith("TELEGRAM_IMAGE_GENERATED|"):
            try:
                # Parse the special format: TELEGRAM_IMAGE_GENERATED|path|caption
                parts = answer.split("|", 2)
                if len(parts) == 3:
                    image_path = parts[1]
                    caption = parts[2]

                    # Validate caption content
                    caption = self._validate_message_content(caption, "🖼️ Generated image")

                    # Verify image file exists
                    if Path(image_path).exists():
                        # Send the image with caption
                        await self.client.send_photo(
                            chat_id=chat_id, photo=image_path, caption=caption
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
                        await self._safe_reply(message, error_msg, "🎨 Image generation error")
                        self.chat_history.add_message(chat_id, "assistant", error_msg)
                        return True

            except Exception as e:
                error_msg = f"❌ Error sending image: {str(e)}"
                await self._safe_reply(message, error_msg, "❌ Error sending image")
                self.chat_history.add_message(chat_id, "assistant", error_msg)
                return True

        # Validate the answer content before processing
        validated_answer = self._validate_message_content(answer, "🤔 I processed your message but didn't have a response.")

        # Standard text response handling
        if len(validated_answer) > 4000:
            parts = [validated_answer[i : i + 4000] for i in range(0, len(validated_answer), 4000)]
            for part in parts:
                response_text = f"{prefix} {part}".strip() if prefix else part
                # Additional validation for each part
                response_text = self._validate_message_content(response_text, "📝 Message part")
                await self._safe_reply(message, response_text, "📝 Message part")
            self.chat_history.add_message(chat_id, "assistant", validated_answer)
        else:
            response_text = f"{prefix} {validated_answer}".strip() if prefix else validated_answer
            # Final validation before sending
            response_text = self._validate_message_content(response_text, "📨 Response")
            await self._safe_reply(message, response_text, "📨 Response")
            self.chat_history.add_message(chat_id, "assistant", validated_answer)

        return False

    def _validate_message_content(self, content: str, fallback_message: str = "📝 Message") -> str:
        """
        Validate message content before sending to Telegram API.
        
        Handles:
        - Empty or whitespace-only content
        - Character encoding issues
        - Invalid characters that could cause Telegram API errors
        
        Args:
            content: The message content to validate
            fallback_message: Default message if content is invalid
            
        Returns:
            str: Valid message content ready for Telegram API
        """
        # Handle None or non-string input
        if not isinstance(content, str):
            content = str(content) if content is not None else ""
        
        # Remove leading/trailing whitespace
        content = content.strip()
        
        # Check for empty content
        if not content:
            return fallback_message
        
        # Check for whitespace-only content (including special characters)
        if not content.replace('\n', '').replace('\t', '').replace(' ', '').replace('\r', ''):
            return fallback_message
        
        # Validate character encoding - replace problematic characters
        try:
            # Ensure content can be encoded to UTF-8
            content.encode('utf-8')
        except UnicodeEncodeError:
            # Replace problematic characters with safe alternatives
            content = content.encode('utf-8', errors='replace').decode('utf-8')
            print(f"Warning: Fixed character encoding issues in message content")
        
        # Remove control characters that might cause issues (except newlines and tabs)
        import re
        content = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', content)
        
        # Final check after cleaning
        content = content.strip()
        if not content:
            return fallback_message
        
        # Ensure message isn't too long for Telegram (4096 character limit)
        if len(content) > 4000:
            content = content[:3997] + "..."
        
        return content

    async def _safe_reply(self, message, content: str, fallback_message: str = "🤖 Message processed") -> None:
        """
        Safely send a reply with content validation to prevent MESSAGE_EMPTY errors.
        
        Args:
            message: Telegram message object to reply to
            content: Content to send
            fallback_message: Fallback if content is invalid
        """
        validated_content = self._validate_message_content(content, fallback_message)
        try:
            await message.reply(validated_content)
        except Exception as e:
            print(f"Error sending reply: {e}")
            # Try once more with a simple fallback
            try:
                await message.reply("🤖 Error sending response")
            except Exception as e2:
                print(f"Failed to send fallback reply: {e2}")
