"""Chat history management for Telegram conversations."""

import json
import time
from pathlib import Path
from typing import Any


class ChatHistoryManager:
    """Manages persistent chat history storage and retrieval."""

    def __init__(self, history_file: str = "chat_history.json", max_messages: int = 20):
        self.history_file = Path(history_file)
        self.max_messages = max_messages
        self.chat_histories: dict[int, list[dict[str, Any]]] = {}
        # Mapping from Telegram message ID to internal message ID for each chat
        self.telegram_to_internal_id: dict[int, dict[int, int]] = {}

    def load_history(self):
        """Load chat history from persistent storage."""
        try:
            if self.history_file.exists():
                with open(self.history_file) as f:
                    data = json.load(f)
                    # Convert string keys back to int and ensure timestamps are floats
                    self.chat_histories = {}
                    for k, v in data.items():
                        chat_id = int(k)
                        messages = []
                        for msg in v:
                            # Ensure timestamp is a float, not a string
                            if "timestamp" in msg:
                                try:
                                    msg["timestamp"] = float(msg["timestamp"])
                                except (ValueError, TypeError):
                                    msg["timestamp"] = 0.0
                            messages.append(msg)
                        self.chat_histories[chat_id] = messages
                    print(f"Loaded chat history for {len(self.chat_histories)} conversations")
            else:
                self.chat_histories = {}
                print("No existing chat history found")
        except Exception as e:
            print(f"Error loading chat history: {e}")
            self.chat_histories = {}

    def save_history(self):
        """Save chat history to persistent storage."""
        try:
            # Convert int keys to string for JSON serialization
            data = {str(k): v for k, v in self.chat_histories.items()}
            with open(self.history_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving chat history: {e}")

    def add_message(self, chat_id: int, role: str, content: str, reply_to_message_id: int = None, telegram_message_id: int = None, is_telegram_id: bool = False):
        """Add a message to chat history with automatic cleanup and persistence."""
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []

        # Debug: Check for potential duplicates before adding
        existing_count = len(self.chat_histories[chat_id])
        print(
            f"🔍 Adding to chat history - Chat: {chat_id}, Role: {role}, Content: '{content[:30]}...', Current count: {existing_count}"
        )

        # Check if this exact message was just added (potential duplicate)
        if (
            self.chat_histories[chat_id]
            and self.chat_histories[chat_id][-1]["content"] == content
            and self.chat_histories[chat_id][-1]["role"] == role
        ):
            print(f"⚠️  DUPLICATE DETECTED: Same message being added twice: '{content[:50]}...'")
            return  # Don't add duplicate

        # Add new message with reply context
        internal_message_id = len(self.chat_histories[chat_id]) + 1
        message_data = {
            "role": role, 
            "content": content, 
            "timestamp": time.time(),
            "message_id": internal_message_id  # Simple incrementing ID
        }
        
        # Store Telegram message ID if provided
        if telegram_message_id:
            message_data["telegram_message_id"] = telegram_message_id
            # Update mapping
            if chat_id not in self.telegram_to_internal_id:
                self.telegram_to_internal_id[chat_id] = {}
            self.telegram_to_internal_id[chat_id][telegram_message_id] = internal_message_id
            print(f"📱 Telegram message {telegram_message_id} mapped to internal ID {internal_message_id}")
        
        # Handle reply context with proper ID mapping
        if reply_to_message_id:
            if is_telegram_id:
                # Convert Telegram message ID to internal message ID
                internal_reply_id = self.get_internal_message_id(chat_id, reply_to_message_id)
                if internal_reply_id:
                    message_data["reply_to_message_id"] = internal_reply_id
                    print(f"🔗 Message replies to internal message ID: {internal_reply_id} (Telegram ID: {reply_to_message_id})")
                else:
                    print(f"⚠️ Could not find internal ID for Telegram message {reply_to_message_id}")
            else:
                # Direct internal message ID (for backward compatibility)
                message_data["reply_to_message_id"] = reply_to_message_id
                print(f"🔗 Message replies to internal message ID: {reply_to_message_id}")
        
        self.chat_histories[chat_id].append(message_data)

        print(f"✅ Message added. New count: {len(self.chat_histories[chat_id])}")

        # Keep only the last max_messages
        if len(self.chat_histories[chat_id]) > self.max_messages:
            self.chat_histories[chat_id] = self.chat_histories[chat_id][-self.max_messages :]

        # Save to disk periodically (every 5 messages to avoid excessive I/O)
        total_messages = sum(len(history) for history in self.chat_histories.values())
        if total_messages % 5 == 0:
            self.save_history()

    def get_internal_message_id(self, chat_id: int, telegram_message_id: int) -> int | None:
        """Convert Telegram message ID to internal message ID."""
        if chat_id not in self.telegram_to_internal_id:
            return None
        return self.telegram_to_internal_id[chat_id].get(telegram_message_id)

    def get_reply_chain(self, chat_id: int, message_id: int, max_depth: int = 5) -> list[dict[str, Any]]:
        """Get reply chain for a specific message, following reply_to_message_id links."""
        if chat_id not in self.chat_histories:
            return []
        
        all_messages = self.chat_histories[chat_id]
        
        # Build a lookup map for message_id to message
        message_map = {}
        for msg in all_messages:
            if "message_id" in msg:
                message_map[msg["message_id"]] = msg
        
        # Find the original message
        if message_id not in message_map:
            return []
        
        # Follow the reply chain upwards (to original message)
        chain = []
        current_msg = message_map[message_id]
        depth = 0
        
        while current_msg and depth < max_depth:
            chain.insert(0, current_msg)  # Insert at beginning to maintain chronological order
            depth += 1
            
            # Follow reply_to_message_id if it exists
            reply_to_id = current_msg.get("reply_to_message_id")
            if reply_to_id and reply_to_id in message_map:
                current_msg = message_map[reply_to_id]
            else:
                break
        
        print(f"🔗 Found reply chain of {len(chain)} messages for message {message_id}")
        return chain

    def get_context_with_reply_priority(self, chat_id: int, current_message_reply_to_id: int = None, 
                                       max_context_messages: int = 10, max_age_hours: int = 24, 
                                       always_include_last: int = 2) -> list[dict[str, str]]:
        """
        Get chat history context with priority for reply chains.
        
        If current_message_reply_to_id is provided, prioritize that reply chain
        and supplement with recent temporal context.
        """
        if chat_id not in self.chat_histories:
            print(f"🔍 No chat history found for chat {chat_id}")
            return []

        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        all_messages = self.chat_histories[chat_id]
        
        priority_messages = []
        
        # If we have a reply chain, prioritize it
        if current_message_reply_to_id:
            reply_chain = self.get_reply_chain(chat_id, current_message_reply_to_id)
            priority_messages = reply_chain
            print(f"🔗 Prioritizing reply chain: {len(priority_messages)} messages")
        
        # Calculate remaining slots for temporal context
        remaining_slots = max_context_messages - len(priority_messages)
        
        # Get temporal context (avoiding duplicates from reply chain)
        temporal_messages = []
        if remaining_slots > 0:
            # Get message IDs already included in priority messages
            priority_message_ids = {msg.get("message_id") for msg in priority_messages if msg.get("message_id")}
            
            # Always include the last N messages regardless of age (soft threshold)
            guaranteed_count = min(always_include_last, remaining_slots)
            guaranteed_messages = []
            
            for msg in reversed(all_messages):  # Most recent first
                if len(guaranteed_messages) >= guaranteed_count:
                    break
                # Skip if already in priority messages
                if msg.get("message_id") not in priority_message_ids:
                    guaranteed_messages.insert(0, msg)  # Insert at beginning for chronological order
            
            temporal_messages.extend(guaranteed_messages)
            remaining_slots -= len(guaranteed_messages)
            
            # Add more recent messages within time limit
            if remaining_slots > 0:
                older_messages = [msg for msg in all_messages 
                                if msg.get("message_id") not in priority_message_ids 
                                and msg not in guaranteed_messages]
                
                for msg in reversed(older_messages):  # Start from most recent
                    if len(temporal_messages) >= max_context_messages - len(priority_messages):
                        break
                    if current_time - float(msg.get("timestamp", 0)) <= max_age_seconds:
                        temporal_messages.insert(-len(guaranteed_messages), msg)  # Insert before guaranteed messages
        
        # Combine priority (reply chain) + temporal context
        # Sort by timestamp to maintain chronological order
        all_context_messages = priority_messages + temporal_messages
        all_context_messages.sort(key=lambda x: float(x.get("timestamp", 0)))
        
        # Remove duplicates while preserving order
        seen_message_ids = set()
        final_messages = []
        for msg in all_context_messages:
            msg_id = msg.get("message_id")
            if msg_id not in seen_message_ids:
                final_messages.append(msg)
                seen_message_ids.add(msg_id)
        
        # Debug logging
        reply_count = len(priority_messages)
        temporal_count = len(final_messages) - reply_count
        debug_info = []
        for m in final_messages:
            age_hours = (current_time - float(m.get("timestamp", 0))) / 3600
            msg_type = "reply-chain" if m in priority_messages else "temporal"
            debug_info.append(f"{m['role']}: {m['content'][:30]}... ({age_hours:.1f}h ago, {msg_type})")
        
        print(f"🔍 Context with reply priority ({len(final_messages)} total: {reply_count} reply-chain + {temporal_count} temporal): {debug_info}")

        # Format for Claude API
        formatted_messages = []
        for msg in final_messages:
            if msg["role"] in ["user", "assistant"]:
                formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        return formatted_messages

    def get_context(self, chat_id: int, max_context_messages: int = 10, max_age_hours: int = 24, always_include_last: int = 2) -> list[dict[str, str]]:
        """Get recent chat history for context with guaranteed recent messages."""
        if chat_id not in self.chat_histories:
            print(f"🔍 No chat history found for chat {chat_id}")
            return []

        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        all_messages = self.chat_histories[chat_id]
        
        # Always include the last N messages regardless of age (soft threshold)
        guaranteed_messages = all_messages[-always_include_last:] if len(all_messages) >= always_include_last else all_messages[:]
        guaranteed_count = len(guaranteed_messages)
        
        # Then add more recent messages within time limit
        additional_messages = []
        remaining_slots = max_context_messages - guaranteed_count
        
        if remaining_slots > 0:
            # Look at messages before the guaranteed ones
            older_messages = all_messages[:-always_include_last] if len(all_messages) > always_include_last else []
            
            for msg in reversed(older_messages):  # Start from most recent of the older messages
                if current_time - float(msg.get("timestamp", 0)) <= max_age_seconds:
                    additional_messages.insert(0, msg)  # Insert at beginning to maintain order
                    if len(additional_messages) >= remaining_slots:
                        break
        
        # Combine: additional messages + guaranteed recent messages
        recent_messages = additional_messages + guaranteed_messages
        
        # Debug: Show messages with timestamps and categorization
        raw_debug = []
        for i, m in enumerate(recent_messages):
            age_hours = (current_time - float(m.get("timestamp", 0))) / 3600
            category = "guaranteed" if i >= len(additional_messages) else "recent"
            raw_debug.append(f"{m['role']}: {m['content'][:30]}... ({age_hours:.1f}h ago, {category})")
        print(f"🔍 Context messages ({len(recent_messages)}, {guaranteed_count} guaranteed + {len(additional_messages)} recent): {raw_debug}")

        # Format for Claude API
        formatted_messages = []
        for msg in recent_messages:
            if msg["role"] in ["user", "assistant"]:
                formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        return formatted_messages

    def search_history(self, chat_id: int, query: str, max_results: int = 5, max_age_days: int = 30) -> list[dict[str, str]]:
        """Search message history for relevant context."""
        if chat_id not in self.chat_histories:
            return []

        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 3600
        query_lower = query.lower()
        
        # Search through messages
        matches = []
        for msg in self.chat_histories[chat_id]:
            # Skip messages that are too old
            if current_time - float(msg.get("timestamp", 0)) > max_age_seconds:
                continue
                
            # Search in message content
            content = msg.get("content", "").lower()
            if query_lower in content:
                age_hours = (current_time - float(msg.get("timestamp", 0))) / 3600
                # Score by relevance (exact match bonus) and recency
                relevance_score = content.count(query_lower)
                recency_score = max(0, 1 - (age_hours / (max_age_days * 24)))  # Decay over time
                total_score = relevance_score + (recency_score * 0.5)
                
                matches.append({
                    "role": msg["role"],
                    "content": msg["content"],
                    "timestamp": msg.get("timestamp", 0),
                    "age_hours": age_hours,
                    "score": total_score
                })
        
        # Sort by score (relevance + recency) and limit results
        matches.sort(key=lambda x: x["score"], reverse=True)
        top_matches = matches[:max_results]
        
        # Debug logging
        if top_matches:
            match_debug = [f"{m['role']}: {m['content'][:40]}... (score: {m['score']:.2f}, {m['age_hours']:.1f}h ago)" for m in top_matches]
            print(f"🔍 History search for '{query}' found {len(top_matches)} matches: {match_debug}")
        
        # Return formatted for LLM
        return [{"role": m["role"], "content": m["content"]} for m in top_matches]
