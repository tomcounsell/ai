"""Message handlers for Telegram bot functionality."""

from pyrogram.enums import ChatType

from tools.link_analysis_tool import extract_urls, is_url_only_message, store_link_with_analysis

# All functionality now handled by valor_agent (telegram_chat_agent)
from .utils import (
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
        
        # Access denial tracking for aggregated logging
        self.access_denials = {}
        self.last_denial_log = 0
        
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
            
            # Log validation warnings if present
            if validation_results.get("errors"):
                for error in validation_results["errors"]:
                    logger.warning(f"Environment validation warning: {error}")
        
        except Exception as e:
            logger.error(f"Failed to load chat filters: {e}")
            # Set safe defaults on error
            self.allowed_groups = set()
    
    def _track_access_denial(self, chat_id: int, is_private_chat: bool, username: str, message_preview: str):
        """Track access denials for aggregated logging to reduce log spam."""
        import time
        import logging
        
        logger = logging.getLogger(__name__)
        current_time = time.time()
        
        # Detailed audit logging (for security/forensics)
        chat_type = "DM" if is_private_chat else "group"
        username_display = f"@{username}" if username else f"ID:{chat_id}"
        logger.warning(
            f"MESSAGE REJECTED - Chat whitelist violation: "
            f"{chat_type} {chat_id} from user {username_display} - "
            f"Message: '{message_preview}'"
        )
        
        # Track for aggregated console logging
        denial_key = f"{chat_type}_{chat_id}"
        if denial_key not in self.access_denials:
            self.access_denials[denial_key] = {
                "chat_id": chat_id,
                "chat_type": chat_type,
                "username": username,
                "count": 0,
                "first_seen": current_time,
                "last_seen": current_time
            }
        
        self.access_denials[denial_key]["count"] += 1
        self.access_denials[denial_key]["last_seen"] = current_time
        
        # Log aggregated summary every 30 seconds to avoid spam
        if current_time - self.last_denial_log > 30:
            self._log_access_denial_summary()
            self.last_denial_log = current_time
    
    def _log_access_denial_summary(self):
        """Log aggregated summary of access denials to reduce console spam."""
        if not self.access_denials:
            return
        
        # Group by chat type
        groups = [d for d in self.access_denials.values() if d["chat_type"] == "group"]
        dms = [d for d in self.access_denials.values() if d["chat_type"] == "DM"]
        
        summary_parts = []
        
        if groups:
            total_group_messages = sum(d["count"] for d in groups)
            unique_groups = len(groups)
            summary_parts.append(f"group chat access denied for {unique_groups} chats ({total_group_messages} messages)")
        
        if dms:
            total_dm_messages = sum(d["count"] for d in dms)
            unique_users = len(dms)
            summary_parts.append(f"DM access denied for {unique_users} users ({total_dm_messages} messages)")
        
        if summary_parts:
            print(f"🚫 ACCESS SUMMARY: {' | '.join(summary_parts)} (not whitelisted)")
            
            # Clear old entries (keep last hour)
            import time
            current_time = time.time()
            cutoff_time = current_time - 3600  # 1 hour
            self.access_denials = {
                k: v for k, v in self.access_denials.items() 
                if v["last_seen"] > cutoff_time
            }
    
    def _should_handle_chat(self, chat_id: int, is_private_chat: bool = False, username: str = None) -> bool:
        """Check if this server instance should handle messages from the given chat with enhanced validation."""
        from utilities.workspace_validator import validate_chat_whitelist_access
        
        try:
            # Use centralized validation function for consistency (it handles all logging)
            is_allowed = validate_chat_whitelist_access(chat_id, is_private_chat, username)
            return is_allowed
            
        except Exception as e:
            # Log error and deny access for safety
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Chat validation failed for {chat_id}: {e}")
            return False

    async def handle_message(self, client, message):
        """
        Main message handling entry point - orchestrates the complete message lifecycle.
        
        This is the primary entry point for all incoming Telegram messages. It performs:
        1. Message validation and access control
        2. Message type detection and routing
        3. Read receipts and reaction management
        4. Error handling and logging
        
        Args:
            client: Telegram client instance
            message: Incoming Telegram message object
        """
        # === STEP 1: EXTRACT MESSAGE METADATA ===
        chat_id = message.chat.id
        is_private_chat = message.chat.type == ChatType.PRIVATE
        username = message.from_user.username if message.from_user else None
        user_id = message.from_user.id if message.from_user else None
        message_id = message.id
        
        # Create message identifier for logging
        chat_type = "DM" if is_private_chat else "group"
        username_display = f"@{username}" if username else f"ID:{user_id}"
        message_preview = (message.text[:50] + "...") if message.text and len(message.text) > 50 else (message.text or "[no text]")
        
        print(f"📨 INCOMING MESSAGE: {chat_type} {chat_id} from {username_display} (msg_id: {message_id})")
        print(f"   Content preview: {message_preview}")
        
        # === STEP 2: ACCESS CONTROL VALIDATION ===
        print(f"🔒 Checking access permissions for {chat_type} {chat_id}...")
        if not self._should_handle_chat(chat_id, is_private_chat, username):
            # Track denied access for aggregated logging
            self._track_access_denial(chat_id, is_private_chat, username, message_preview)
            return
        
        print(f"✅ Access granted for {chat_type} {chat_id} ({username_display})")

        # === STEP 3: MESSAGE ACKNOWLEDGMENT ===
        print(f"📖 Marking message {message_id} as read...")
        try:
            await client.read_chat_history(chat_id, message.id)
            print(f"✅ Message marked as read")
        except Exception as e:
            print(f"⚠️  Could not mark message as read: {e}")

        # === STEP 4: INITIAL REACTION (USER FEEDBACK) ===
        print(f"👀 Adding 'received' reaction to message {message_id}...")
        from .reaction_manager import add_message_received_reaction
        try:
            await add_message_received_reaction(client, chat_id, message.id)
            print(f"✅ Received reaction added")
        except Exception as e:
            print(f"⚠️  Could not add received reaction: {e}")

        # === STEP 5: MESSAGE TYPE DETECTION AND ROUTING ===
        print(f"🔍 Detecting message type for {message_id}...")
        
        if message.photo:
            print(f"📸 PHOTO MESSAGE detected - routing to photo handler")
            await self._handle_photo_message(client, message, chat_id)
            return
        elif message.document:
            print(f"📄 DOCUMENT MESSAGE detected - routing to document handler")
            await self._handle_document_message(client, message, chat_id)
            return
        elif message.voice or message.audio:
            audio_type = "voice" if message.voice else "audio"
            print(f"🎵 {audio_type.upper()} MESSAGE detected - routing to audio handler")
            await self._handle_audio_message(client, message, chat_id)
            return
        elif message.video or message.video_note:
            video_type = "video_note" if message.video_note else "video"
            print(f"🎬 {video_type.upper()} MESSAGE detected - routing to video handler")
            await self._handle_video_message(client, message, chat_id)
            return
        elif not message.text:
            print(f"❓ UNSUPPORTED MESSAGE TYPE detected - skipping (no text content)")
            print(f"   Message type: {type(message).__name__}")
            return

        # === STEP 6: TEXT MESSAGE PROCESSING ===
        print(f"💬 TEXT MESSAGE detected - proceeding with text processing pipeline")
        print(f"   Message length: {len(message.text)} characters")

        # === STEP 7: MESSAGE AGE VALIDATION ===
        from .utils import is_message_too_old
        message_timestamp = message.date.timestamp()
        print(f"⏰ Checking message age (timestamp: {message_timestamp})...")
        
        if is_message_too_old(message_timestamp):
            print(f"⏰ MESSAGE TOO OLD - collecting as missed message for batch processing")
            
            # Collect missed messages for later batch response
            if chat_id not in self.missed_messages_per_chat:
                self.missed_messages_per_chat[chat_id] = []
                print(f"📝 Created new missed message queue for chat {chat_id}")
            
            self.missed_messages_per_chat[chat_id].append(message.text)
            print(f"📝 Added to missed messages queue (total: {len(self.missed_messages_per_chat[chat_id])})")

            # Still store old messages for context
            reply_to_telegram_message_id = None
            if hasattr(message, 'reply_to_message') and message.reply_to_message:
                reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)
                print(f"🔗 Message is replying to Telegram message {reply_to_telegram_message_id}")
            
            self.chat_history.add_message(chat_id, "user", message.text, reply_to_telegram_message_id, message.id, is_telegram_id=True)
            print(f"💾 Stored missed message in chat history: {message.text[:50]}...")
            return

        print(f"✅ Message is recent - proceeding with real-time processing")

        # === STEP 8: BOT METADATA RETRIEVAL ===
        print(f"🤖 Retrieving bot information...")
        me = await client.get_me()
        bot_username = me.username
        bot_id = me.id
        print(f"   Bot username: @{bot_username}")
        print(f"   Bot ID: {bot_id}")

        # === STEP 9: CHAT HISTORY CONTEXT CHECK ===
        current_history_count = len(self.chat_history.chat_histories.get(chat_id, []))
        print(f"📚 Current chat history length for {chat_id}: {current_history_count} messages")

        print(f"🔄 Processing message from {chat_type} {chat_id}: '{message.text[:50]}...'")

        # === STEP 10: MISSED MESSAGES PROCESSING ===
        if chat_id in self.missed_messages_per_chat and self.missed_messages_per_chat[chat_id]:
            missed_count = len(self.missed_messages_per_chat[chat_id])
            print(f"📬 Found {missed_count} missed messages for chat {chat_id} - processing batch")
            await self._process_missed_messages_through_agent(client, chat_id, message, processed_text, reply_to_telegram_message_id)

        # === STEP 11: MENTION DETECTION AND TEXT PROCESSING ===
        print(f"🏷️  Processing mentions and cleaning message text...")
        try:
            is_mentioned, processed_text = self._process_mentions(
                message, bot_username, bot_id, is_private_chat
            )
            print(f"   Is mentioned: {is_mentioned}")
            print(f"   Processed text: '{processed_text[:50]}...'")
        except Exception as e:
            print(f"❌ Error processing mentions: {e}")
            # Fallback: treat as regular message without mentions
            is_mentioned = is_private_chat  # Only respond in private chats if error
            processed_text = getattr(message, 'text', None) or getattr(message, 'caption', None) or ""
            print(f"   Fallback - is_mentioned: {is_mentioned}, text: '{processed_text[:50]}...'")

        # === STEP 12: REPLY CONTEXT EXTRACTION ===
        reply_to_telegram_message_id = None
        if hasattr(message, 'reply_to_message') and message.reply_to_message:
            reply_to_telegram_message_id = getattr(message.reply_to_message, 'id', None)
            print(f"🔗 Message is replying to Telegram message {reply_to_telegram_message_id}")
        else:
            print(f"📝 Message is not a reply")

        # === STEP 13: SPECIAL GROUP HANDLING ===
        from ..notion.utils import is_dev_group
        is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
        print(f"🛠️  Is dev group: {is_dev_group_chat}")
        
        # === STEP 14: RESPONSE DECISION LOGIC ===
        should_respond = is_private_chat or is_mentioned or is_dev_group_chat
        print(f"🎯 Response decision:")
        print(f"   Private chat: {is_private_chat}")
        print(f"   Mentioned: {is_mentioned}")
        print(f"   Dev group: {is_dev_group_chat}")
        print(f"   Will respond: {should_respond}")
        
        if not should_respond:
            print(f"💾 Storing message for context but not responding")
            self.chat_history.add_message(chat_id, "user", message.text, reply_to_telegram_message_id, message.id, is_telegram_id=True)
            return

        # === STEP 15: CHAT HISTORY STORAGE ===
        print(f"💾 Storing user message in chat history...")
        self.chat_history.add_message(chat_id, "user", processed_text, reply_to_telegram_message_id, message.id, is_telegram_id=True)
        new_history_count = len(self.chat_history.chat_histories.get(chat_id, []))
        print(f"   Chat history updated: {current_history_count} → {new_history_count} messages")

        # === STEP 16: SPECIAL MESSAGE TYPE DETECTION ===
        print(f"🔍 Checking for special message types...")
        if is_url_only_message(processed_text):
            print(f"🔗 LINK-ONLY MESSAGE detected - routing to link handler")
            await self._handle_link_message(message, chat_id, processed_text)
            return

        # === STEP 17: INTENT CLASSIFICATION AND AGENT ROUTING ===
        print(f"🧠 Starting intent classification and agent routing...")
        await self._route_message_with_intent(client, message, chat_id, processed_text, reply_to_telegram_message_id)

    async def _process_missed_messages_through_agent(self, client, chat_id: int, message, processed_text: str, reply_to_telegram_message_id):
        """Process missed messages through the normal agent routing system with context-aware filtering."""
        try:
            missed_messages = self.missed_messages_per_chat[chat_id]
            print(f"Processing {len(missed_messages)} missed messages for chat {chat_id}")
            
            # Get bot info for mention detection
            me = await client.get_me()
            bot_username = me.username
            
            # Determine chat type for filtering
            is_private_chat = message.chat.type.name in ['PRIVATE', 'BOT']
            
            # Check if this is a dev group
            from ..notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            # Filter messages based on chat type
            if is_private_chat or is_dev_group_chat:
                # DMs and dev groups: process all missed messages
                messages_to_process = missed_messages
                print(f"Chat type: {'DM' if is_private_chat else 'dev group'} - processing all {len(messages_to_process)} missed messages")
            else:
                # Non-dev groups: only process messages where bot was @mentioned
                messages_to_process = [msg for msg in missed_messages if f"@{bot_username}" in msg]
                print(f"Chat type: non-dev group - processing {len(messages_to_process)} of {len(missed_messages)} missed messages (mentions only)")
            
            # Only respond if we have relevant messages to process
            if messages_to_process:
                # Create a summary of relevant missed messages and route through normal agent system
                missed_summary = f"I was offline and missed {len(messages_to_process)} relevant messages. Recent messages were: " + "; ".join(messages_to_process[-3:])
                
                # Route the missed message summary through normal agent processing
                await self._route_message_with_intent(client, message, chat_id, missed_summary, reply_to_telegram_message_id)
            else:
                print(f"No relevant missed messages to process for chat {chat_id}")
            
            # Clear missed messages for this chat
            del self.missed_messages_per_chat[chat_id]
            
        except Exception as e:
            print(f"Error processing missed messages through agent: {e}")
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

    async def _classify_message_intent(self, processed_text: str, message, chat_id: int):
        """
        Classify message intent using multi-tier classification system.
        
        Classification hierarchy:
        1. Primary: Ollama (granite3.2-vision) - Local, privacy-preserving
        2. Fallback: GPT-3.5 Turbo - High quality when Ollama fails
        3. Last resort: Rule-based classification - Always available
        
        Args:
            processed_text: Cleaned message text for classification
            message: Telegram message object for context extraction
            chat_id: Chat identifier for logging
            
        Returns:
            IntentResult with intent, confidence, reasoning, and suggested emoji
        """
        print(f"🧠 INTENT CLASSIFICATION START for chat {chat_id}")
        from ..ollama_intent import classify_message_intent
        
        # === STEP 17.2.1: CONTEXT PREPARATION ===
        print(f"📋 Preparing classification context...")
        context = {
            "chat_id": chat_id,
            "is_group_chat": message.chat.type != ChatType.PRIVATE,
            "username": message.from_user.username if message.from_user else None,
            "has_image": bool(message.photo),
            "has_links": any(url in processed_text.lower() for url in ["http://", "https://", "www."]),
        }
        
        print(f"   Context prepared:")
        print(f"   - Chat type: {'group' if context['is_group_chat'] else 'private'}")
        print(f"   - Username: {context['username'] or 'unknown'}")
        print(f"   - Has image: {context['has_image']}")
        print(f"   - Has links: {context['has_links']}")
        print(f"   - Text length: {len(processed_text)} chars")
        
        # === STEP 17.2.2: INTENT CLASSIFICATION EXECUTION ===
        print(f"🔮 Executing intent classification (Ollama → GPT-3.5 → Rule-based)...")
        try:
            intent_result = await classify_message_intent(processed_text, context)
            
            print(f"✅ Intent classification successful:")
            print(f"   Intent: {intent_result.intent.value}")
            print(f"   Confidence: {intent_result.confidence:.2f}")
            print(f"   Reasoning: {intent_result.reasoning}")
            print(f"   Suggested emoji: {intent_result.suggested_emoji}")
            
            return intent_result
            
        except Exception as e:
            # === STEP 17.2.3: ERROR HANDLING AND LOGGING ===
            error_msg = str(e) if str(e).strip() else f"{type(e).__name__}: {repr(e)}"
            if not error_msg.strip():
                error_msg = f"Unknown {type(e).__name__} exception occurred"
            
            print(f"❌ Intent classification failed: {error_msg}")
            import traceback
            print(f"Full error traceback:")
            print(traceback.format_exc())
            
            # === STEP 17.2.4: FALLBACK INTENT RESULT ===
            print(f"🔄 Creating fallback intent result...")
            from ..ollama_intent import IntentResult, MessageIntent
            fallback_result = IntentResult(
                intent=MessageIntent.UNCLEAR,
                confidence=0.5,
                reasoning=f"Classification failed: {error_msg}",
                suggested_emoji="🤔"
            )
            
            print(f"   Fallback intent: {fallback_result.intent.value}")
            print(f"   Fallback confidence: {fallback_result.confidence}")
            
            return fallback_result

    async def _handle_with_valor_agent_intent(self, message, chat_id: int, processed_text: str, 
                                            reply_to_telegram_message_id: int = None, intent_result=None):
        """
        Process message through Valor agent with intent-specific configuration.
        
        This method handles the complete agent processing pipeline:
        1. Priority question detection
        2. Notion context retrieval (group-specific or priority-based)
        3. Chat history preparation with reply context
        4. Agent execution with intent context
        5. Response processing and delivery
        
        Args:
            message: Telegram message object
            chat_id: Chat identifier
            processed_text: Cleaned message text
            reply_to_telegram_message_id: Optional ID of replied message
            intent_result: Intent classification result
        """
        print(f"🤖 VALOR AGENT PROCESSING START for chat {chat_id}")
        print(f"   Intent: {intent_result.intent.value if intent_result else 'unknown'}")
        print(f"   Confidence: {intent_result.confidence if intent_result else 'N/A'}")
        
        try:
            from agents.valor.handlers import handle_telegram_message_with_intent

            # === STEP 17.4.1: PRIORITY QUESTION DETECTION ===
            print(f"🎯 Checking if message is a priority question...")
            is_priority = (
                is_user_priority_question(processed_text)
                if "is_user_priority_question" in globals()
                else False
            )
            print(f"   Is priority question: {is_priority}")

            # === STEP 17.4.2: NOTION CONTEXT RETRIEVAL ===
            print(f"📝 Retrieving Notion context...")
            notion_data = None
            if self.notion_scout:
                try:
                    is_private_chat = message.chat.type == ChatType.PRIVATE
                    if not is_private_chat:
                        print(f"   Group chat detected - fetching group-specific Notion data...")
                        notion_data = await self._get_notion_context_for_group(chat_id, processed_text)
                    elif is_priority:
                        print(f"   Priority question in DM - fetching Notion data...")
                        notion_data = await self._get_notion_context(processed_text)
                    else:
                        print(f"   No Notion context needed for this message")
                    
                    if notion_data:
                        print(f"   ✅ Notion context retrieved ({len(notion_data)} chars)")
                    else:
                        print(f"   ⚪ No Notion context available")
                        
                except Exception as e:
                    print(f"   ⚠️  Could not get Notion context: {e}")

            # === STEP 17.4.3: CHAT HISTORY PREPARATION ===
            print(f"📚 Preparing chat history context...")
            reply_internal_id = None
            if reply_to_telegram_message_id:
                reply_internal_id = self.chat_history.get_internal_message_id(chat_id, reply_to_telegram_message_id)
                print(f"   Reply detected - Telegram ID {reply_to_telegram_message_id} → Internal ID {reply_internal_id}")
            
            if reply_internal_id:
                print(f"   Using reply-aware context prioritizing message {reply_internal_id}")
                chat_history = self.chat_history.get_context_with_reply_priority(
                    chat_id, reply_internal_id, max_context_messages=10
                )
            else:
                print(f"   Using standard context (no reply)")
                chat_history = self.chat_history.get_context(chat_id, max_context_messages=10)
            
            history_count = len(chat_history) if chat_history else 0
            print(f"   Chat history prepared: {history_count} messages")

            # === STEP 17.4.4: AGENT EXECUTION ===
            print(f"🚀 Executing Valor agent with intent context...")
            print(f"   Message length: {len(processed_text)} chars")
            print(f"   Username: {message.from_user.username if message.from_user else 'unknown'}")
            print(f"   Chat type: {'group' if message.chat.type != ChatType.PRIVATE else 'private'}")
            
            answer = await handle_telegram_message_with_intent(
                message=processed_text,
                chat_id=chat_id,
                username=message.from_user.username if message.from_user else None,
                is_group_chat=message.chat.type != ChatType.PRIVATE,
                chat_history_obj=self.chat_history,
                notion_data=notion_data,
                is_priority_question=is_priority,
                intent_result=intent_result,
            )

            # === STEP 17.4.5: RESPONSE VALIDATION AND PROCESSING ===
            print(f"📤 Processing agent response...")
            if answer:
                print(f"   ✅ Agent returned response ({len(answer)} chars)")
                print(f"   Response preview: {answer[:100]}..." if len(answer) > 100 else f"   Response: {answer}")
                
                # Check for ASYNC_PROMISE marker
                if "ASYNC_PROMISE|" in answer:
                    print(f"   🔄 ASYNC_PROMISE marker detected in response!")
                    promise_parts = answer.split("ASYNC_PROMISE|", 1)
                    print(f"   Promise parts: {len(promise_parts)} parts")
                    if len(promise_parts) > 1:
                        print(f"   Promise message: {promise_parts[1][:100]}...")
            else:
                print(f"   ⚠️  Agent returned empty response")
            
            await self._process_agent_response(message, chat_id, answer)
            print(f"✅ VALOR AGENT PROCESSING COMPLETE for chat {chat_id}")

        except Exception as e:
            print(f"❌ Intent-aware agent processing failed: {e}")
            import traceback
            print(f"Full error traceback:")
            print(traceback.format_exc())
            print(f"🔄 Falling back to regular handler...")
            await self._handle_with_valor_agent(message, chat_id, processed_text, reply_to_telegram_message_id)

    async def _route_message_with_intent(self, client, message, chat_id: int, processed_text: str, reply_to_telegram_message_id: int = None):
        """
        Route message through intent classification pipeline.
        
        This method orchestrates the complete message processing workflow:
        1. System command detection (ping, etc.)
        2. Intent classification via Ollama/GPT-3.5/rule-based fallback
        3. Intent-specific reaction addition
        4. Message processing via Valor agent with intent context
        5. Completion/error reaction management
        
        Args:
            client: Telegram client instance
            message: Telegram message object
            chat_id: Chat identifier
            processed_text: Cleaned message text (mentions removed)
            reply_to_telegram_message_id: Optional ID of message being replied to
        """
        print(f"🚦 INTENT ROUTING PIPELINE START for chat {chat_id}")
        text = processed_text.lower().strip()

        # === STEP 17.1: SYSTEM COMMAND DETECTION ===
        print(f"⚡ Checking for system commands...")
        if text == "ping":
            print(f"🏓 PING COMMAND detected - routing to ping handler")
            await self._handle_ping(message, chat_id)
            return

        # === STEP 17.2: INTENT CLASSIFICATION ===
        print(f"🧠 Starting intent classification for: '{processed_text[:50]}...'")
        intent_result = await self._classify_message_intent(processed_text, message, chat_id)
        
        print(f"🎯 Intent classification result:")
        print(f"   Intent: {intent_result.intent.value}")
        print(f"   Confidence: {intent_result.confidence:.2f}")
        print(f"   Reasoning: {intent_result.reasoning}")
        print(f"   Suggested emoji: {intent_result.suggested_emoji}")
        
        # === STEP 17.3: INTENT REACTION ADDITION ===
        print(f"😊 Adding intent-specific reaction ({intent_result.suggested_emoji})...")
        from .reaction_manager import add_intent_based_reaction
        try:
            await add_intent_based_reaction(client, chat_id, message.id, intent_result)
            print(f"✅ Intent reaction added successfully")
        except Exception as e:
            print(f"⚠️  Could not add intent reaction: {e}")

        # === STEP 17.4: AGENT PROCESSING WITH INTENT CONTEXT ===
        print(f"🤖 Starting Valor agent processing with intent context...")
        success = False
        try:
            await self._handle_with_valor_agent_intent(message, chat_id, processed_text, reply_to_telegram_message_id, intent_result)
            success = True
            print(f"✅ Agent processing completed successfully")
        except Exception as e:
            print(f"❌ Agent processing failed for intent {intent_result.intent.value}: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            success = False
        
        # === STEP 17.5: COMPLETION REACTION MANAGEMENT ===
        print(f"🏁 Adding completion reaction (success: {success})...")
        from .reaction_manager import complete_reaction_sequence
        try:
            await complete_reaction_sequence(client, chat_id, message.id, intent_result, success)
        except Exception as e:
            print(f"Warning: Could not complete reaction sequence: {e}")

    async def _route_message(self, message, chat_id: int, processed_text: str, reply_to_telegram_message_id: int = None):
        """Legacy route message method for backward compatibility."""
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
            print(f"   Full Database ID: {db_id}")
            print(f"   Database Filter (first 8 chars): {db_id[:8]}")
            
            # Get answer from Notion Scout using the query_all_accessible_databases method
            answer = await self.notion_scout.query_all_accessible_databases(processed_text, db_filter=db_id[:8])

            return answer

        except Exception as e:
            print(f"Error getting group-specific Notion context: {e}")
            return None

    async def _get_notion_context(self, processed_text: str) -> str | None:
        """Get Notion context for priority questions."""
        try:
            if not self.notion_scout:
                return None

            # Check if specific project mentioned and get db_filter
            text_lower = processed_text.lower()
            db_filter = None
            for project_name in ["psyoptimal", "flextrip", "psy", "flex"]:
                if project_name in text_lower:
                    from ..notion.utils import resolve_project_name

                    resolved_name, db_id = resolve_project_name(project_name)
                    if db_id:
                        db_filter = db_id[:8]
                        break

            # Get answer from Notion Scout using the query_all_accessible_databases method
            answer = await self.notion_scout.query_all_accessible_databases(processed_text, db_filter=db_filter)

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

            # Check if this is a dev group that should handle all messages
            from ..notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            # Only respond in private chats, when mentioned in groups, or in dev groups
            if not (is_private_chat or is_mentioned or is_dev_group_chat):
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

            # Check if this is a dev group that should handle all messages
            from ..notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            # Store message in chat history even if not responding
            if not (is_private_chat or is_mentioned or is_dev_group_chat):
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

            if is_private_chat or is_mentioned or is_dev_group_chat:
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
        """Handle audio/voice messages with transcription support."""
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

            # Check if this is a dev group that should handle all messages
            from ..notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            # Store message in chat history even if not responding
            if not (is_private_chat or is_mentioned or is_dev_group_chat):
                if message.caption:
                    audio_type = "Voice" if message.voice else "Audio"
                    self.chat_history.add_message(chat_id, "user", f"[{audio_type}+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                else:
                    audio_type = "[Voice]" if message.voice else "[Audio]"
                    self.chat_history.add_message(chat_id, "user", audio_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)
                return

            # Process voice/audio transcription if we should respond
            if is_private_chat or is_mentioned or is_dev_group_chat:
                try:
                    # Download the audio file to a temporary location
                    import tempfile
                    import os
                    from utilities.logger import get_logger
                    
                    logger = get_logger("telegram.voice_transcription")
                    logger.info(f"🎙️ Starting voice transcription for chat_id={chat_id}, message_id={message.id}")
                    
                    # Create temporary file with appropriate extension
                    if message.voice:
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.ogg')
                        audio_type = "Voice"
                        duration = getattr(message.voice, 'duration', 'unknown')
                        file_size = getattr(message.voice, 'file_size', 'unknown')
                        logger.info(f"📥 Voice message details: duration={duration}s, size={file_size} bytes")
                    else:
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
                        audio_type = "Audio"
                        duration = getattr(message.audio, 'duration', 'unknown')
                        file_size = getattr(message.audio, 'file_size', 'unknown')
                        logger.info(f"📥 Audio file details: duration={duration}s, size={file_size} bytes")
                    
                    temp_file.close()
                    temp_path = temp_file.name
                    logger.info(f"📂 Created temporary file: {temp_path}")
                    
                    # Download the audio file
                    logger.info("⬇️ Starting audio file download...")
                    await message.download(temp_path)
                    
                    # Verify file was downloaded
                    if os.path.exists(temp_path):
                        file_size_downloaded = os.path.getsize(temp_path)
                        logger.info(f"✅ Download complete: {file_size_downloaded} bytes written to {temp_path}")
                    else:
                        logger.error(f"❌ Download failed: file not found at {temp_path}")
                        raise Exception("Audio file download failed")
                    
                    # Transcribe using our voice transcription tool
                    logger.info("🔄 Starting Whisper transcription...")
                    from tools.voice_transcription_tool import transcribe_audio_file
                    transcribed_text = transcribe_audio_file(temp_path, cleanup_file=True)
                    logger.info(f"✅ Transcription successful: {len(transcribed_text)} characters")
                    logger.debug(f"📝 Transcribed text: {transcribed_text[:100]}...")
                    
                    # Handle caption text along with transcription
                    if message.caption:
                        logger.info(f"📝 Processing voice message with caption: {len(message.caption)} chars")
                        # Store both caption and transcribed audio
                        full_message = f"[{audio_type}+Text] Caption: {message.caption}\nTranscribed audio: {transcribed_text}"
                        self.chat_history.add_message(chat_id, "user", full_message, reply_to_telegram_message_id, message.id, is_telegram_id=True)
                        
                        # Process both caption and transcribed text together
                        combined_text = f"{message.caption}\n\n{transcribed_text}"
                        logger.info("🤖 Routing combined caption+transcription to agent...")
                        await self._route_message_with_intent(client, message, chat_id, combined_text, reply_to_telegram_message_id)
                        logger.info("✅ Voice message with caption processed successfully")
                        return  # Exit since _route_message_with_intent handles the full response
                    else:
                        logger.info("📝 Processing voice message (no caption)")
                        # Store transcribed audio only
                        self.chat_history.add_message(chat_id, "user", transcribed_text, reply_to_telegram_message_id, message.id, is_telegram_id=True)
                        
                        # Process transcribed text
                        logger.info("🤖 Routing transcription to agent...")
                        await self._route_message_with_intent(client, message, chat_id, transcribed_text, reply_to_telegram_message_id)
                        logger.info("✅ Voice message processed successfully")
                        return  # Exit since _route_message_with_intent handles the full response
                    
                except Exception as transcription_error:
                    # Enhanced error logging
                    from utilities.logger import get_logger
                    logger = get_logger("telegram.voice_transcription")
                    logger.error(f"❌ Voice transcription failed for chat_id={chat_id}, message_id={message.id}")
                    logger.error(f"❌ Error type: {type(transcription_error).__name__}")
                    logger.error(f"❌ Error details: {str(transcription_error)}")
                    
                    # Log additional context
                    try:
                        import traceback
                        logger.error(f"❌ Full traceback:\n{traceback.format_exc()}")
                    except:
                        pass
                    
                    # Store original message format in chat history
                    if message.caption:
                        audio_type = "Voice" if message.voice else "Audio"
                        self.chat_history.add_message(chat_id, "user", f"[{audio_type}+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                        logger.info(f"📝 Stored caption in chat history: {message.caption}")
                    else:
                        audio_type = "[Voice]" if message.voice else "[Audio]"
                        self.chat_history.add_message(chat_id, "user", audio_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)
                        logger.info(f"📝 Stored audio placeholder in chat history")
                    
                    # Provide fallback response
                    if message.voice:
                        if message.caption:
                            response = f"🎙️ I hear you sent a voice message with text: '{message.caption}'. Voice transcription failed, but I can still help with your text message!"
                            # Process the caption text at least
                            try:
                                logger.info("🔄 Attempting to process caption text as fallback...")
                                await self._route_message_with_intent(client, message, chat_id, message.caption, reply_to_telegram_message_id)
                                logger.info("✅ Caption processed successfully as fallback")
                                return  # Exit since _route_message_with_intent handles the full response
                            except Exception as caption_error:
                                logger.error(f"❌ Caption processing also failed: {caption_error}")
                                pass  # Keep fallback message
                        else:
                            response = f"🎙️ I hear you sent a voice message! Transcription failed with error: {str(transcription_error)}"
                            logger.warning(f"⚠️ Sending transcription failure message to user")
                    else:
                        if message.caption:
                            response = f"🎵 I see you shared an audio file with text: '{message.caption}'. Audio transcription failed, but I can help with your text!"
                            # Process the caption text at least
                            try:
                                logger.info("🔄 Attempting to process audio caption as fallback...")
                                await self._route_message_with_intent(client, message, chat_id, message.caption, reply_to_telegram_message_id)
                                logger.info("✅ Audio caption processed successfully as fallback")
                                return  # Exit since _route_message_with_intent handles the full response
                            except Exception as caption_error:
                                logger.error(f"❌ Audio caption processing also failed: {caption_error}")
                                pass  # Keep fallback message
                        else:
                            response = f"🎵 I see you shared an audio file! Transcription failed with error: {str(transcription_error)}"
                            logger.warning(f"⚠️ Sending audio transcription failure message to user")
                    
                    await self._safe_reply(message, response, "🎵 Audio received")
                    self.chat_history.add_message(chat_id, "assistant", response)
                    logger.info("📤 Sent fallback response to user")
            else:
                # Just store in history without transcription for non-responding cases
                if message.caption:
                    audio_type = "Voice" if message.voice else "Audio"
                    self.chat_history.add_message(chat_id, "user", f"[{audio_type}+Text] {message.caption}", reply_to_telegram_message_id, message.id, is_telegram_id=True)
                else:
                    audio_type = "[Voice]" if message.voice else "[Audio]"
                    self.chat_history.add_message(chat_id, "user", audio_type, reply_to_telegram_message_id, message.id, is_telegram_id=True)

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

            # Check if this is a dev group that should handle all messages
            from ..notion.utils import is_dev_group
            is_dev_group_chat = is_dev_group(chat_id) if not is_private_chat else False
            
            # Store message in chat history even if not responding
            if not (is_private_chat or is_mentioned or is_dev_group_chat):
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

            if is_private_chat or is_mentioned or is_dev_group_chat:
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

        # Check for ASYNC_PROMISE marker indicating a long-running task
        if answer and "ASYNC_PROMISE|" in answer:
            print(f"🔄 Detected ASYNC_PROMISE marker in response")
            parts = answer.split("ASYNC_PROMISE|", 1)
            promise_message = parts[1].strip() if len(parts) > 1 else "I'll work on this task in the background."
            
            # Extract task description from the promise message
            task_description = promise_message.replace("I'll work on this task in the background: ", "").strip()
            
            # Create promise in database
            from utilities.database import create_promise
            promise_id = create_promise(chat_id, message.id, task_description)
            print(f"📝 Created promise {promise_id} for long-running task")
            
            # Send immediate response to user
            await self._safe_reply(message, promise_message, "📝 Working on task")
            self.chat_history.add_message(chat_id, "assistant", promise_message)
            
            # Execute promise using Huey
            from utilities.promise_manager_huey import HueyPromiseManager
            promise_manager = HueyPromiseManager()
            
            # Update the promise manager to handle the execution
            from tasks.promise_tasks import execute_promise_by_type
            execute_promise_by_type(promise_id)
            
            print(f"🚀 Queued promise {promise_id} for Huey execution")
            
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
    
    async def _execute_promise_background(self, original_message, chat_id: int, promise_id: int, task_description: str):
        """Execute a long-running task in the background and send completion message.
        
        Args:
            original_message: Original Telegram message that triggered the promise
            chat_id: Chat ID for sending completion message
            promise_id: Promise ID in database
            task_description: Description of the task to execute
        """
        print(f"🔄 Starting background execution for promise {promise_id}")
        print(f"   Task: {task_description}")
        print(f"   Chat ID: {chat_id}")
        print(f"   Original message ID: {original_message.id}")
        
        try:
            # Update promise status to in_progress
            print(f"📊 Updating promise {promise_id} status to 'in_progress'...")
            from utilities.database import update_promise_status, get_promise
            update_promise_status(promise_id, "in_progress")
            
            # Verify promise was updated
            promise_data = get_promise(promise_id)
            print(f"   Promise status after update: {promise_data.get('status') if promise_data else 'NOT FOUND'}")
            
            # Import delegation tool
            print(f"📦 Importing delegation tool...")
            from tools.valor_delegation_tool import spawn_valor_session
            
            # Determine working directory (use current directory as default)
            import os
            working_directory = os.getcwd()
            print(f"📂 Working directory: {working_directory}")
            
            # Execute the task using the delegation tool (without time check since we're already async)
            print(f"🚀 Executing task via Claude Code...")
            print(f"   Calling spawn_valor_session with:")
            print(f"   - task_description: {task_description}")
            print(f"   - target_directory: {working_directory}")
            print(f"   - force_sync: True")
            
            import time
            start_time = time.time()
            result = spawn_valor_session(
                task_description=task_description,
                target_directory=working_directory,
                specific_instructions=None,
                tools_needed=None,
                force_sync=True  # Always execute synchronously in background
            )
            execution_time = time.time() - start_time
            print(f"✅ Task completed in {execution_time:.1f} seconds")
            print(f"📄 Result preview: {result[:200]}..." if len(result) > 200 else f"📄 Result: {result}")
            
            # Check if result contains the ASYNC_PROMISE marker (shouldn't happen in background)
            if "ASYNC_PROMISE|" in result:
                # Extract the actual result
                result = result.split("ASYNC_PROMISE|", 1)[0].strip()
            
            # Truncate result if too long for Telegram (4096 char limit)
            max_result_length = 3500  # Leave room for the wrapper text
            truncated_result = result[:max_result_length] + "..." if len(result) > max_result_length else result
            
            # Send completion message
            completion_message = f"""✅ **Task Complete!**

I finished working on: {task_description}

**Result:**
{truncated_result}

This task took {execution_time:.1f} seconds to complete."""
            
            # Send the completion message as a reply to the original message
            try:
                print(f"📤 Attempting to send completion message to chat {chat_id}...")
                await original_message.reply(completion_message)
                self.chat_history.add_message(chat_id, "assistant", completion_message)
                print(f"✅ Sent completion message for promise {promise_id}")
            except Exception as send_error:
                print(f"❌ Failed to send completion message via reply: {type(send_error).__name__}: {send_error}")
                # Try sending without reply using the client
                try:
                    if hasattr(self, 'client') and self.client:
                        print(f"🔄 Attempting to send via client.send_message...")
                        await self.client.send_message(chat_id, completion_message)
                        self.chat_history.add_message(chat_id, "assistant", completion_message)
                        print(f"✅ Sent completion message via client")
                    else:
                        print(f"❌ Client not available for sending message")
                except Exception as e:
                    print(f"❌ Failed to send message to chat: {type(e).__name__}: {e}")
            
            # Update promise status to completed
            update_promise_status(promise_id, "completed", result_summary=result[:500])
            print(f"✅ Promise {promise_id} marked as completed")
            
        except Exception as e:
            error_msg = f"❌ **Task Failed**\n\nI encountered an error while working on: {task_description}\n\nError: {str(e)}"
            print(f"❌ Background task failed for promise {promise_id}: {type(e).__name__}: {e}")
            import traceback
            print(f"Traceback:\n{traceback.format_exc()}")
            
            # Try to send error message
            try:
                print(f"📤 Attempting to send error message to chat {chat_id}...")
                await original_message.reply(error_msg)
                self.chat_history.add_message(chat_id, "assistant", error_msg)
                print(f"✅ Sent error message via reply")
            except Exception as reply_error:
                print(f"❌ Failed to send error via reply: {type(reply_error).__name__}: {reply_error}")
                try:
                    if hasattr(self, 'client') and self.client:
                        print(f"🔄 Attempting to send error via client.send_message...")
                        await self.client.send_message(chat_id, error_msg)
                        self.chat_history.add_message(chat_id, "assistant", error_msg)
                        print(f"✅ Sent error message via client")
                    else:
                        print(f"❌ Client not available for sending error message")
                except Exception as send_error:
                    print(f"❌ Could not send error message to user: {type(send_error).__name__}: {send_error}")
            
            # Update promise status to failed
            from utilities.database import update_promise_status
            update_promise_status(promise_id, "failed", error_message=str(e))
            print(f"❌ Promise {promise_id} marked as failed")
