"""Response handlers for different types of Telegram questions."""

from datetime import datetime
from pathlib import Path


def load_persona() -> str:
    """Load the bot's persona from the persona document."""
    persona_file = Path(__file__).parent.parent / "persona.md"
    
    if not persona_file.exists():
        return "You are a helpful technical assistant."
    
    try:
        with open(persona_file, 'r') as f:
            return f.read()
    except Exception:
        return "You are a helpful technical assistant."


async def handle_user_priority_question(
    question: str, 
    anthropic_client, 
    chat_id: int, 
    notion_scout, 
    chat_history
) -> str:
    """Handle questions about user's work priorities by checking context and Notion."""
    
    # Get recent chat context to see if user mentioned any projects/tasks
    context_messages = chat_history.get_context(chat_id)
    
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


async def handle_general_question(question: str, anthropic_client, chat_id: int, chat_history) -> str:
    """Handle general questions using the bot's persona with chat context."""
    persona = load_persona()
    
    # Get recent chat context
    context_messages = chat_history.get_context(chat_id)
    
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