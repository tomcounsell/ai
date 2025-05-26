import os
import asyncio
import sys
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from pyrogram import Client
from dotenv import load_dotenv
import anthropic
import requests

load_dotenv()

app_client = None

# Chat history storage - {chat_id: [{"role": "user/assistant", "content": "...", "timestamp": ...}]}
chat_histories = {}
MAX_HISTORY_MESSAGES = 20  # Keep last 20 messages per chat
HISTORY_FILE = Path(__file__).parent / "chat_history.json"
MAX_MESSAGE_AGE_SECONDS = 300  # Only respond to messages newer than 5 minutes
bot_start_time = None

# Track missed messages during catch-up
missed_messages_per_chat = {}

class NotionScout:
    """Simple Notion database query agent for Telegram integration."""
    
    def __init__(self, notion_key: str, anthropic_key: str):
        self.notion_key = notion_key
        self.anthropic_client = anthropic.Anthropic(api_key=anthropic_key)
        self.db_filter = None
    
    async def query_database_entries(self, database_id: str) -> dict:
        """Query actual entries from a specific Notion database."""
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        
        try:
            query_url = f"https://api.notion.com/v1/databases/{database_id}/query"
            response = requests.post(query_url, headers=headers, json={})
            
            if response.status_code != 200:
                return {"error": f"Error querying database: {response.status_code}"}
            
            return response.json()
            
        except Exception as e:
            return {"error": f"Error querying database entries: {str(e)}"}

    def extract_property_value(self, prop_value: dict) -> str:
        """Extract readable value from Notion property."""
        if not prop_value:
            return ""
            
        prop_type = prop_value.get("type", "")
        
        if prop_type == "title":
            return "".join([t.get("plain_text", "") for t in prop_value.get("title", [])])
        elif prop_type == "rich_text":
            return "".join([t.get("plain_text", "") for t in prop_value.get("rich_text", [])])
        elif prop_type == "select":
            select_obj = prop_value.get("select")
            return select_obj.get("name", "") if select_obj else ""
        elif prop_type == "multi_select":
            return ", ".join([s.get("name", "") for s in prop_value.get("multi_select", [])])
        elif prop_type == "status":
            status_obj = prop_value.get("status")
            return status_obj.get("name", "") if status_obj else ""
        elif prop_type == "checkbox":
            return "Yes" if prop_value.get("checkbox") else "No"
        elif prop_type == "number":
            return str(prop_value.get("number", ""))
        elif prop_type == "date":
            date_obj = prop_value.get("date")
            return date_obj.get("start", "") if date_obj else ""
        else:
            return str(prop_value.get(prop_type, ""))

    async def query_notion_directly(self, question: str) -> str:
        """Query Notion API directly to get actual database content."""
        headers = {
            "Authorization": f"Bearer {self.notion_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        
        try:
            search_url = "https://api.notion.com/v1/search"
            search_payload = {"filter": {"value": "database", "property": "object"}}
            
            response = requests.post(search_url, headers=headers, json=search_payload)
            
            if response.status_code != 200:
                return f"Error accessing Notion API: {response.status_code}"
            
            data = response.json()
            databases = data.get("results", [])
            
            if self.db_filter:
                databases = [db for db in databases if self.db_filter in db['id']]
                if not databases:
                    return f"No database found matching '{self.db_filter}'"
            
            if not databases:
                return "No databases found accessible to the integration."
            
            all_entries = []
            for db in databases:
                db_id = db['id']
                db_title = "".join([t.get("plain_text", "") for t in db.get("title", [])])
                
                entries_data = await self.query_database_entries(db_id)
                if "error" in entries_data:
                    continue
                
                entries = entries_data.get("results", [])
                
                for entry in entries:
                    entry_data = {
                        "database": db_title,
                        "id": entry["id"],
                        "url": entry.get("url", ""),
                        "properties": {}
                    }
                    
                    for prop_name, prop_value in entry.get("properties", {}).items():
                        entry_data["properties"][prop_name] = self.extract_property_value(prop_value)
                    
                    all_entries.append(entry_data)
            
            return self.analyze_entries_with_claude(all_entries, question)
            
        except Exception as e:
            return f"Error querying Notion: {str(e)}"

    def analyze_entries_with_claude(self, entries: list, question: str) -> str:
        """Use Claude to analyze the database entries and answer the question."""
        if not entries:
            return "No database entries found to analyze."
        
        entries_text = "NOTION DATABASE ENTRIES:\n\n"
        for i, entry in enumerate(entries[:20], 1):  # Limit for Telegram
            entries_text += f"Entry {i}:\n  Database: {entry['database']}\n"
            for prop_name, prop_value in entry['properties'].items():
                if prop_value and prop_value.strip():
                    entries_text += f"  {prop_name}: {prop_value}\n"
            entries_text += "\n"
        
        system_prompt = """You are analyzing Notion database entries to answer questions. Provide concise, specific answers suitable for Telegram messages (under 300 words). Focus on the most relevant and actionable information."""
        
        try:
            response = self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=400,
                temperature=0.3,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": f"Question: {question}\n\n{entries_text}"}
                ]
            )
            
            return response.content[0].text
            
        except Exception as e:
            return f"Error analyzing entries: {str(e)}"

    async def answer_question(self, question: str) -> str:
        """Answer a question by querying Notion database."""
        return await self.query_notion_directly(question)

# Initialize Notion Scout
notion_scout = None

def load_project_mapping():
    """Load project name to database ID mapping."""
    mapping_file = Path(__file__).parent / "integrations" / "notion" / "database_mapping.json"
    
    if not mapping_file.exists():
        return {}, {}
    
    try:
        with open(mapping_file, 'r') as f:
            data = json.load(f)
            projects = data.get("projects", {})
            aliases = data.get("aliases", {})
            return projects, aliases
    except Exception:
        return {}, {}

def resolve_project_name(project_input: str) -> tuple[str, str]:
    """Resolve a project input to project name and database ID."""
    projects, aliases = load_project_mapping()
    
    if project_input in projects:
        return project_input, projects[project_input]["database_id"]
    
    if project_input.lower() in aliases:
        project_name = aliases[project_input.lower()]
        return project_name, projects[project_name]["database_id"]
    
    return None, None

def is_notion_question(text: str) -> bool:
    """Detect if a message is asking about Notion."""
    notion_keywords = [
        'notion', 'task', 'project', 'database', 'milestone', 'status', 
        'priority', 'deadline', 'due', 'todo', 'progress', 'development',
        'psyoptimal', 'flextrip', 'psy', 'flex'
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in notion_keywords)

def is_user_priority_question(text: str) -> bool:
    """Detect if a message is asking about user's work priorities or next tasks."""
    priority_patterns = [
        'what should i work on',
        'what am i working on', 
        'what are you working on',
        'what will you work on',
        'what should you work on',
        'what\'s next',
        'whats next',
        'next priority',
        'next task',
        'upcoming work',
        'work on next',
        'priorities',
        'roadmap'
    ]
    text_lower = text.lower()
    return any(pattern in text_lower for pattern in priority_patterns)

def load_persona() -> str:
    """Load the bot's persona from the persona document."""
    persona_file = Path(__file__).parent / "integrations" / "persona.md"
    
    if not persona_file.exists():
        return "You are a helpful technical assistant."
    
    try:
        with open(persona_file, 'r') as f:
            return f.read()
    except Exception:
        return "You are a helpful technical assistant."

def load_chat_history():
    """Load chat history from persistent storage."""
    global chat_histories
    
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, 'r') as f:
                data = json.load(f)
                # Convert string keys back to int
                chat_histories = {int(k): v for k, v in data.items()}
                print(f"Loaded chat history for {len(chat_histories)} conversations")
        else:
            chat_histories = {}
            print("No existing chat history found")
    except Exception as e:
        print(f"Error loading chat history: {e}")
        chat_histories = {}

def save_chat_history():
    """Save chat history to persistent storage."""
    try:
        # Convert int keys to string for JSON serialization
        data = {str(k): v for k, v in chat_histories.items()}
        with open(HISTORY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving chat history: {e}")

def add_to_chat_history(chat_id: int, role: str, content: str):
    """Add a message to chat history with automatic cleanup and persistence."""
    import time
    
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    
    # Debug: Check for potential duplicates before adding
    existing_count = len(chat_histories[chat_id])
    print(f"🔍 Adding to chat history - Chat: {chat_id}, Role: {role}, Content: '{content[:30]}...', Current count: {existing_count}")
    
    # Check if this exact message was just added (potential duplicate)
    if chat_histories[chat_id] and chat_histories[chat_id][-1]["content"] == content and chat_histories[chat_id][-1]["role"] == role:
        print(f"⚠️  DUPLICATE DETECTED: Same message being added twice: '{content[:50]}...'")
        return  # Don't add duplicate
    
    # Add new message
    chat_histories[chat_id].append({
        "role": role,
        "content": content,
        "timestamp": time.time()
    })
    
    print(f"✅ Message added. New count: {len(chat_histories[chat_id])}")
    
    # Keep only the last MAX_HISTORY_MESSAGES
    if len(chat_histories[chat_id]) > MAX_HISTORY_MESSAGES:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY_MESSAGES:]
    
    # Save to disk periodically (every 5 messages to avoid excessive I/O)
    total_messages = sum(len(history) for history in chat_histories.values())
    if total_messages % 5 == 0:
        save_chat_history()

def get_chat_context(chat_id: int) -> list:
    """Get recent chat history for context, formatted for Claude API."""
    if chat_id not in chat_histories:
        print(f"🔍 No chat history found for chat {chat_id}")
        return []
    
    # Get recent messages (exclude system messages, keep last 10 for context)
    recent_messages = chat_histories[chat_id][-10:]
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

async def handle_user_priority_question(question: str, anthropic_client, chat_id: int, notion_scout) -> str:
    """Handle questions about user's work priorities by checking context and Notion."""
    
    # Get recent chat context to see if user mentioned any projects/tasks
    context_messages = get_chat_context(chat_id)
    
    # Check if there's relevant project context in recent conversation
    context_has_project_info = False
    for msg in context_messages[-5:]:  # Check last 5 messages
        if any(keyword in msg['content'].lower() for keyword in ['project', 'task', 'working on', 'psyoptimal', 'flextrip']):
            context_has_project_info = True
            break
    
    # If no recent project context, check Notion for current priorities
    notion_data = ""
    if notion_scout and not context_has_project_info:
        try:
            priority_query = "What are the highest priority tasks that are ready for development?"
            notion_data = await notion_scout.answer_question(priority_query)
        except Exception as e:
            notion_data = f"Error checking project data: {str(e)}"
    
    persona = load_persona()
    
    system_prompt = f"""Based on this persona document, respond to the user's question about their work priorities:

{persona}

You have access to:
1. Recent conversation history for context
2. Current project data from Notion (if available)

The user is asking about THEIR work priorities/next tasks. Use available context and project data to provide specific, actionable recommendations. If you have project data, prioritize tasks that are ready for development and align with their technical capabilities.

Keep responses concise but actionable (under 400 words for Telegram)."""
    
    try:
        # Build comprehensive context
        context_text = ""
        if context_messages:
            recent_context = context_messages[-3:]  # Last 3 for brevity
            context_text += "Recent conversation:\n"
            for msg in recent_context:
                context_text += f"{msg['role']}: {msg['content']}\n"
        
        if notion_data and "Error" not in notion_data:
            context_text += f"\nCurrent project priorities:\n{notion_data}"
        
        final_question = f"{question}\n\nContext:\n{context_text}" if context_text else question
        
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": final_question}]
        )
        
        return response.content[0].text
        
    except Exception as e:
        return f"Error processing priority question: {str(e)}"

async def handle_general_question(question: str, anthropic_client, chat_id: int) -> str:
    """Handle general questions using the bot's persona with chat context."""
    import time
    from datetime import datetime
    
    persona = load_persona()
    
    # Get recent chat context
    context_messages = get_chat_context(chat_id)
    
    # Add current environment context
    current_time = datetime.now()
    env_context = f"""
CURRENT ENVIRONMENT CONTEXT:
- Today's date: {current_time.strftime('%A, %B %d, %Y')}
- Current time: {current_time.strftime('%I:%M %p')}
- Your location: Working at Yudame (software company)
- Platform: macOS development environment
"""
    
    system_prompt = f"""Based on this persona document, respond to the user's question while embodying this character:

{persona}

{env_context}

You have access to recent conversation history for context. Use this context to provide more relevant and coherent responses, but don't reference the conversation history unless it's directly relevant to the current question.

IMPORTANT: For simple questions (like "what's today's date?"), give SHORT, human answers. Don't over-explain or turn everything into a technical discussion. Match the energy of the question."""
    
    try:
        # Check if the current question is already in context (avoid duplication)
        if context_messages and context_messages[-1]["role"] == "user" and context_messages[-1]["content"] == question:
            # Question already in context, use as-is
            messages = context_messages
            print(f"🔍 Current question already in context, using existing messages")
        else:
            # Add current question to context
            messages = context_messages + [{"role": "user", "content": question}]
            print(f"🔍 Adding current question to context")
        
        print(f"🚀 SENDING TO LLM - Question: '{question[:50]}...'")
        print(f"🚀 Total messages to LLM: {len(messages)}")
        for i, msg in enumerate(messages):
            print(f"  {i+1}. {msg['role']}: '{msg['content'][:50]}...'")
        
        # Determine if this is a simple question that needs a short answer
        simple_question_patterns = [
            'what\'s', 'whats', 'what is', 'when is', 'where', 'who', 
            'how are you', 'date', 'time', 'today', 'yesterday', 'tomorrow'
        ]
        is_simple_question = any(pattern in question.lower() for pattern in simple_question_patterns)
        max_tokens = 100 if is_simple_question else 500
        
        print(f"🚀 Simple question: {is_simple_question}, Max tokens: {max_tokens}")
        
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=max_tokens,
            temperature=0.7,
            system=system_prompt,
            messages=messages
        )
        
        return response.content[0].text
        
    except Exception as e:
        return f"Error processing question: {str(e)}"

class AuthCode(BaseModel):
    code: str

class AuthPassword(BaseModel):
    password: str

def is_message_too_old(message_timestamp: int) -> bool:
    """Check if a message is too old to respond to (for catch-up handling)."""
    import time
    current_time = time.time()
    message_age = current_time - message_timestamp
    return message_age > MAX_MESSAGE_AGE_SECONDS

async def generate_catchup_response(missed_messages: list, anthropic_client) -> str:
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
                {"role": "user", "content": f"I sent these messages while you were offline:\n{messages_text}"}
            ]
        )
        
        return response.content[0].text
        
    except Exception as e:
        return "Hi! I'm back and caught up on your messages. How can I help?"

async def start_telegram_client():
    """Initialize the Telegram client using Pyrogram"""
    global app_client, notion_scout, bot_start_time
    import time
    
    bot_start_time = time.time()
    
    # Load existing chat history
    load_chat_history()
    
    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    notion_key = os.getenv('NOTION_API_KEY')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')
    
    print(f"Loading Telegram credentials: api_id={api_id}, api_hash={'*' * len(api_hash) if api_hash else None}")
    
    if not all([api_id, api_hash]):
        print("Telegram credentials not found in environment variables")
        return
    
    # Initialize Notion Scout if keys are available
    if notion_key and anthropic_key:
        notion_scout = NotionScout(notion_key, anthropic_key)
        print("Notion Scout initialized successfully")
    else:
        print("Notion Scout not initialized - missing API keys")
    
    try:
        app_client = Client(
            "ai_project_bot",
            api_id=int(api_id),
            api_hash=api_hash,
            workdir="/Users/valorengels/src/ai"
        )
        
        # Start the client
        await app_client.start()
        print("Telegram client started successfully")
        
        # Add message handler
        @app_client.on_message()
        async def handle_message(client, message):
            if not message.text:
                return
            
            chat_id = message.chat.id
            
            # Check if message is too old (catch-up from offline period)
            if is_message_too_old(message.date.timestamp()):
                # Collect missed messages for later batch response
                if chat_id not in missed_messages_per_chat:
                    missed_messages_per_chat[chat_id] = []
                missed_messages_per_chat[chat_id].append(message.text)
                
                # Still store old messages for context
                add_to_chat_history(chat_id, "user", message.text)
                print(f"Collecting missed message from chat {chat_id}: {message.text[:50]}...")
                return
            
            # Get bot's own info
            me = await client.get_me()
            bot_username = me.username
            bot_id = me.id
            
            # Check if this is a direct message or if bot is mentioned in group
            # In Pyrogram, private chats use ChatType.PRIVATE enum
            from pyrogram.enums import ChatType
            is_private_chat = message.chat.type == ChatType.PRIVATE
            
            print(f"Processing message from chat {chat_id} (private: {is_private_chat}): '{message.text[:50]}...'")
            
            # Debug: Check current chat history length to detect duplication  
            current_history_count = len(chat_histories.get(chat_id, []))
            print(f"Current chat history length: {current_history_count}")
            
            # Check if we have missed messages for this chat and respond to them first
            if chat_id in missed_messages_per_chat and missed_messages_per_chat[chat_id]:
                try:
                    if notion_scout and notion_scout.anthropic_client:
                        catchup_response = await generate_catchup_response(
                            missed_messages_per_chat[chat_id], 
                            notion_scout.anthropic_client
                        )
                        await message.reply(f"📬 {catchup_response}")
                        add_to_chat_history(chat_id, "assistant", catchup_response)
                    
                    # Clear missed messages for this chat
                    del missed_messages_per_chat[chat_id]
                    
                except Exception as e:
                    print(f"Error sending catch-up response: {e}")
                    # Clear anyway to avoid getting stuck
                    del missed_messages_per_chat[chat_id]
            
            # Handle group mentions
            is_mentioned = False
            original_text = message.text
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
                            mentioned_text = message.text[entity.offset:entity.offset + entity.length]
                            if mentioned_text == f"@{bot_username}":
                                is_mentioned = True
                                # Remove the mention from processed text
                                processed_text = (message.text[:entity.offset] + 
                                                message.text[entity.offset + entity.length:]).strip()
                                break
                        elif entity.type == "text_mention" and entity.user.id == bot_id:
                            is_mentioned = True
                            # Remove the mention from processed text
                            processed_text = (message.text[:entity.offset] + 
                                            message.text[entity.offset + entity.length:]).strip()
                            break
            
            # Only respond in private chats or when mentioned in groups
            if not (is_private_chat or is_mentioned):
                # Still store the message for context, but don't respond
                add_to_chat_history(chat_id, "user", message.text)
                return
            
            # Store user message in chat history
            add_to_chat_history(chat_id, "user", processed_text)
            
            text = processed_text.lower()
            
            # Basic commands
            if text == 'ping':
                response = 'pong'
                await message.reply(response)
                add_to_chat_history(chat_id, "assistant", response)
                
            elif text == 'status':
                response = 'AI Project API is running and listening!'
                await message.reply(response)
                add_to_chat_history(chat_id, "assistant", response)
                
            elif text.startswith('help') or text == '':
                response = """🤖 Available commands:

• ping - Test bot responsiveness
• status - Check API status
• Ask any question about your Notion projects!

Examples:
• "What tasks are ready for dev?"
• "Show me project PsyOPTIMAL status"
• "What's the highest priority task?"
• "FlexTrip progress update"

💡 In groups, just @mention me with your question!
"""
                await message.reply(response)
                add_to_chat_history(chat_id, "assistant", response)
            
            # User priority questions - check first for work-related queries
            elif is_user_priority_question(processed_text):
                try:
                    await message.reply("🎯 Checking your current priorities...")
                    
                    # Use specialized priority handler
                    answer = await handle_user_priority_question(
                        processed_text, 
                        notion_scout.anthropic_client if notion_scout else None, 
                        chat_id, 
                        notion_scout
                    )
                    
                    # Split long messages for Telegram
                    if len(answer) > 4000:
                        parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
                        for part in parts:
                            await message.reply(part)
                        full_response = "\n".join(parts)
                    else:
                        full_response = f"🎯 {answer}"
                        await message.reply(full_response)
                    
                    add_to_chat_history(chat_id, "assistant", answer)
                
                except Exception as e:
                    error_msg = f"❌ Error checking priorities: {str(e)}"
                    await message.reply(error_msg)
                    add_to_chat_history(chat_id, "assistant", error_msg)
            
            # Notion integration - check for specific Notion keywords
            elif notion_scout and is_notion_question(processed_text):
                try:
                    await message.reply("🔍 Searching your Notion databases...")
                    
                    # Check if specific project mentioned
                    text_lower = processed_text.lower()
                    for project_name in ['psyoptimal', 'flextrip', 'psy', 'flex']:
                        if project_name in text_lower:
                            resolved_name, db_id = resolve_project_name(project_name)
                            if db_id:
                                notion_scout.db_filter = db_id[:8]
                                break
                    
                    # Get answer from Notion Scout
                    answer = await notion_scout.answer_question(processed_text)
                    
                    # Reset filter for next query
                    notion_scout.db_filter = None
                    
                    # Split long messages for Telegram
                    if len(answer) > 4000:
                        parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
                        for part in parts:
                            await message.reply(part)
                        full_response = "\n".join(parts)
                    else:
                        full_response = f"🎯 **Notion Scout Results**\n\n{answer}"
                        await message.reply(full_response)
                    
                    add_to_chat_history(chat_id, "assistant", full_response)
                
                except Exception as e:
                    error_msg = f"❌ Error querying Notion: {str(e)}"
                    await message.reply(error_msg)
                    add_to_chat_history(chat_id, "assistant", error_msg)
            
            # General questions - use persona for any other meaningful text
            elif len(processed_text.strip()) > 2:  # Ignore very short messages
                try:
                    # Use the same anthropic client from notion_scout
                    if notion_scout and notion_scout.anthropic_client:
                        answer = await handle_general_question(processed_text, notion_scout.anthropic_client, chat_id)
                        
                        # Split long messages for Telegram
                        if len(answer) > 4000:
                            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
                            for part in parts:
                                await message.reply(part)
                            full_response = "\n".join(parts)
                        else:
                            await message.reply(answer)
                        
                        add_to_chat_history(chat_id, "assistant", answer)  # Store without emoji prefix
                    else:
                        response = "💭 I'd love to help, but I need my AI capabilities configured first!"
                        await message.reply(response)
                        add_to_chat_history(chat_id, "assistant", response)
                
                except Exception as e:
                    error_msg = f"❌ Error processing question: {str(e)}"
                    await message.reply(error_msg)
                    add_to_chat_history(chat_id, "assistant", error_msg)
            
            # Fallback for very short or unrecognized commands
            else:
                response = "🤔 Could you provide more details? I'm here to help with technical questions and Notion queries!"
                await message.reply(response)
                add_to_chat_history(chat_id, "assistant", response)
        
    except Exception as e:
        print(f"Failed to start Telegram client: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    # Startup
    await start_telegram_client()
    
    yield
    
    # Shutdown
    global app_client
    
    # Save chat history before shutdown
    print("Saving chat history...")
    save_chat_history()
    
    if app_client:
        try:
            await app_client.stop()
        except:
            pass

app = FastAPI(title="AI Project API", version="1.0.0", lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "AI Project API is running"}

@app.get("/health")
async def health_check():
    telegram_status = "connected" if app_client and app_client.is_connected else "disconnected"
    return {"status": "healthy", "telegram": telegram_status}

@app.get("/telegram/status")
async def telegram_status():
    """Get Telegram client status"""
    if app_client:
        return {
            "telegram": "connected" if app_client.is_connected else "disconnected",
            "client_id": getattr(app_client, 'session_name', 'ai_project_bot')
        }
    return {"telegram": "disconnected"}

@app.post("/telegram/initialize")
async def initialize_telegram():
    """Manually initialize Telegram client"""
    try:
        await start_telegram_client()
        return {"status": "initialization_started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to initialize: {e}")

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )