"""Fixed version of the missed messages check function."""

async def _check_startup_missed_messages(self, notion_scout=None):
    """Check for missed messages during startup and process them.
    
    FIXED: Corrected logic to properly identify messages sent while bot was offline.
    """
    if not self.client or not self.message_handler:
        return

    print("🔍 Checking for missed messages during startup...")
    
    try:
        from .utils import MAX_MESSAGE_AGE_SECONDS
        from pyrogram.enums import ChatType
        import time

        missed_message_count = 0
        processed_chats = []
        
        # Calculate catchup window
        catchup_window_start = self.bot_start_time - MAX_MESSAGE_AGE_SECONDS  # 5 minutes before startup
        current_time = time.time()

        # Get all dialogs (conversations) - this includes both DMs and groups
        async for dialog in self.client.get_dialogs():
            chat = dialog.chat
            chat_id = chat.id
            
            # Check if this chat should be handled by this server instance
            is_private_chat = chat.type == ChatType.PRIVATE
            if not self.message_handler._should_handle_chat(chat_id, is_private_chat):
                continue

            processed_chats.append(chat_id)
            chat_missed_messages = []

            # Get recent messages from this chat
            try:
                message_count = 0
                async for message in self.client.get_chat_history(chat_id, limit=50):
                    message_count += 1
                    
                    # Skip non-text messages for startup check
                    if not message.text:
                        continue
                    
                    msg_timestamp = message.date.timestamp()
                    
                    # FIXED LOGIC: Check if message is in the catchup window
                    # Message must be:
                    # 1. From BEFORE bot started (we missed it)
                    # 2. Within the catchup window (not too old)
                    if catchup_window_start < msg_timestamp < self.bot_start_time:
                        # This is a missed message!
                        chat_missed_messages.append(message.text)
                        missed_message_count += 1
                        
                        # Store the message in chat history for context
                        self.chat_history.add_message(chat_id, "user", message.text)
                        
                        print(f"📬 Found missed message in chat {chat_id} "
                              f"(sent {int(self.bot_start_time - msg_timestamp)}s before startup): "
                              f"{message.text[:50]}...")
                    
                    # Stop if we've gone too far back in time
                    if msg_timestamp < catchup_window_start:
                        break

            except Exception as e:
                print(f"Warning: Could not check messages in chat {chat_id}: {e}")
                continue

            # If we found missed messages for this chat, add them to the handler
            if chat_missed_messages:
                if chat_id not in self.message_handler.missed_messages_per_chat:
                    self.message_handler.missed_messages_per_chat[chat_id] = []
                
                # Add messages in chronological order (reverse the list since we got newest first)
                self.message_handler.missed_messages_per_chat[chat_id].extend(reversed(chat_missed_messages))
                
                print(f"💬 Stored {len(chat_missed_messages)} missed messages from chat {chat_id}")

        if missed_message_count > 0:
            print(f"✅ Found {missed_message_count} missed messages across {len(processed_chats)} chats")
            print(f"📬 Missed messages will be processed when users send new messages")
        else:
            print("✅ No missed messages found during startup")

    except Exception as e:
        print(f"❌ Error checking for missed messages during startup: {e}")
        # Don't fail startup if missed message check fails


# Quick test to verify the logic
def test_missed_message_logic():
    """Test the fixed logic for identifying missed messages."""
    import time
    
    # Simulate bot start time
    bot_start_time = time.time()
    MAX_MESSAGE_AGE_SECONDS = 300  # 5 minutes
    
    # Test cases
    test_cases = [
        # (message_time_offset, should_be_missed, description)
        (-600, False, "Message 10 minutes before startup - too old"),
        (-400, True, "Message 6.7 minutes before startup - should catch"),
        (-200, True, "Message 3.3 minutes before startup - should catch"),
        (-30, True, "Message 30 seconds before startup - should catch"),
        (0, False, "Message at exact startup time - not missed"),
        (30, False, "Message 30 seconds after startup - not missed"),
    ]
    
    catchup_window_start = bot_start_time - MAX_MESSAGE_AGE_SECONDS
    
    print("Testing missed message logic:")
    print(f"Bot start time: {bot_start_time}")
    print(f"Catchup window: {MAX_MESSAGE_AGE_SECONDS}s")
    print(f"Catchup window start: {catchup_window_start}")
    print()
    
    for offset, expected_missed, description in test_cases:
        msg_timestamp = bot_start_time + offset
        
        # Apply the fixed logic
        is_missed = catchup_window_start < msg_timestamp < bot_start_time
        
        status = "✅" if is_missed == expected_missed else "❌"
        print(f"{status} {description}")
        print(f"   Message time: {msg_timestamp} (offset: {offset}s)")
        print(f"   Is missed: {is_missed} (expected: {expected_missed})")
        print()


if __name__ == "__main__":
    test_missed_message_logic()