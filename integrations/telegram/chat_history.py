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

    def load_history(self):
        """Load chat history from persistent storage."""
        try:
            if self.history_file.exists():
                with open(self.history_file) as f:
                    data = json.load(f)
                    # Convert string keys back to int
                    self.chat_histories = {int(k): v for k, v in data.items()}
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

    def add_message(self, chat_id: int, role: str, content: str):
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

        # Add new message
        self.chat_histories[chat_id].append(
            {"role": role, "content": content, "timestamp": time.time()}
        )

        print(f"✅ Message added. New count: {len(self.chat_histories[chat_id])}")

        # Keep only the last max_messages
        if len(self.chat_histories[chat_id]) > self.max_messages:
            self.chat_histories[chat_id] = self.chat_histories[chat_id][-self.max_messages :]

        # Save to disk periodically (every 5 messages to avoid excessive I/O)
        total_messages = sum(len(history) for history in self.chat_histories.values())
        if total_messages % 5 == 0:
            self.save_history()

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
                if current_time - msg.get("timestamp", 0) <= max_age_seconds:
                    additional_messages.insert(0, msg)  # Insert at beginning to maintain order
                    if len(additional_messages) >= remaining_slots:
                        break
        
        # Combine: additional messages + guaranteed recent messages
        recent_messages = additional_messages + guaranteed_messages
        
        # Debug: Show messages with timestamps and categorization
        raw_debug = []
        for i, m in enumerate(recent_messages):
            age_hours = (current_time - m.get("timestamp", 0)) / 3600
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
            if current_time - msg.get("timestamp", 0) > max_age_seconds:
                continue
                
            # Search in message content
            content = msg.get("content", "").lower()
            if query_lower in content:
                age_hours = (current_time - msg.get("timestamp", 0)) / 3600
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
