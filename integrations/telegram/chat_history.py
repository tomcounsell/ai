"""Chat history management for Telegram conversations."""

import json
import time
from pathlib import Path
from typing import Dict, List, Any


class ChatHistoryManager:
    """Manages persistent chat history storage and retrieval."""
    
    def __init__(self, history_file: str = "chat_history.json", max_messages: int = 20):
        self.history_file = Path(history_file)
        self.max_messages = max_messages
        self.chat_histories: Dict[int, List[Dict[str, Any]]] = {}
        
    def load_history(self):
        """Load chat history from persistent storage."""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
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
            with open(self.history_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving chat history: {e}")

    def add_message(self, chat_id: int, role: str, content: str):
        """Add a message to chat history with automatic cleanup and persistence."""
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []
        
        # Debug: Check for potential duplicates before adding
        existing_count = len(self.chat_histories[chat_id])
        print(f"🔍 Adding to chat history - Chat: {chat_id}, Role: {role}, Content: '{content[:30]}...', Current count: {existing_count}")
        
        # Check if this exact message was just added (potential duplicate)
        if (self.chat_histories[chat_id] and 
            self.chat_histories[chat_id][-1]["content"] == content and 
            self.chat_histories[chat_id][-1]["role"] == role):
            print(f"⚠️  DUPLICATE DETECTED: Same message being added twice: '{content[:50]}...'")
            return  # Don't add duplicate
        
        # Add new message
        self.chat_histories[chat_id].append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        
        print(f"✅ Message added. New count: {len(self.chat_histories[chat_id])}")
        
        # Keep only the last max_messages
        if len(self.chat_histories[chat_id]) > self.max_messages:
            self.chat_histories[chat_id] = self.chat_histories[chat_id][-self.max_messages:]
        
        # Save to disk periodically (every 5 messages to avoid excessive I/O)
        total_messages = sum(len(history) for history in self.chat_histories.values())
        if total_messages % 5 == 0:
            self.save_history()

    def get_context(self, chat_id: int, max_context_messages: int = 10) -> List[Dict[str, str]]:
        """Get recent chat history for context, formatted for Claude API."""
        if chat_id not in self.chat_histories:
            print(f"🔍 No chat history found for chat {chat_id}")
            return []
        
        # Get recent messages (exclude system messages, keep last N for context)
        recent_messages = self.chat_histories[chat_id][-max_context_messages:]
        # Debug: Show raw messages
        raw_debug = [f"{m['role']}: {m['content'][:30]}..." for m in recent_messages]
        print(f"🔍 Raw recent messages ({len(recent_messages)}): {raw_debug}")
        
        # Format for Claude API
        formatted_messages = []
        for msg in recent_messages:
            if msg["role"] in ["user", "assistant"]:
                formatted_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        # Debug: Show formatted messages
        formatted_debug = [f"{m['role']}: {m['content'][:30]}..." for m in formatted_messages]
        print(f"🔍 Formatted messages for LLM ({len(formatted_messages)}): {formatted_debug}")
        return formatted_messages