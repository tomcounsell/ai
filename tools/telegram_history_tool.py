"""Telegram message history search tool for context retrieval.

This tool implements intelligent search through Telegram conversation history using
a relevance + recency scoring algorithm to find the most useful historical context.

Search Algorithm:
- Searches message content for case-insensitive keyword matches
- Scores results using: relevance_score + (recency_score * 0.5)
- relevance_score = count of query term occurrences in message content
- recency_score = 1 - (message_age_hours / max_age_hours), providing time decay
- Results sorted by total score (relevance + recency) in descending order
- Configurable time window (default: 30 days) and result limit (default: 5)

This ensures recent relevant messages rank higher than old relevant messages,
while still allowing important older context to be found when highly relevant.
"""

def search_telegram_history(query: str, chat_history_obj, chat_id: int, max_results: int = 5) -> str:
    """
    Search through Telegram message history for relevant context using intelligent scoring.
    
    Uses a relevance + recency algorithm that balances content matching with message age
    to provide the most useful historical context. Recent relevant messages are prioritized
    over older ones, but highly relevant older messages can still be found.
    
    Search Algorithm Details:
    - Case-insensitive keyword search through message content
    - Scoring: relevance_count + (time_decay_factor * 0.5)
    - Time window: 30 days (configurable in ChatHistoryManager.search_history)
    - Results ranked by total score, limited by max_results parameter
    
    Use this tool when you need to find specific information from previous 
    conversations that might not be in the immediate recent context.
    
    Args:
        query: Search terms or keywords to find in message history
        chat_history_obj: Chat history manager instance with search_history method
        chat_id: ID of the chat to search
        max_results: Maximum number of relevant messages to return (default 5)
        
    Returns:
        Formatted string of relevant historical messages ranked by relevance + recency,
        or "No matches found" if no relevant messages exist in the search window.
        
    Architecture:
        This function provides a clean interface to ChatHistoryManager.search_history(),
        handling formatting and error cases for both PydanticAI agent tools and
        MCP server tools that need consistent conversation history search.
    """
    if not chat_history_obj:
        return "No chat history available for search"
    
    try:
        # Search message history 
        matches = chat_history_obj.search_history(
            chat_id=chat_id, 
            query=query, 
            max_results=max_results,
            max_age_days=30  # Search last 30 days
        )
        
        if not matches:
            return f"No messages found matching '{query}' in recent history"
        
        # Format results for the agent
        result_text = f"Found {len(matches)} relevant message(s) for '{query}':\n\n"
        
        for i, msg in enumerate(matches, 1):
            result_text += f"{i}. {msg['role']}: {msg['content']}\n\n"
        
        return result_text.strip()
        
    except Exception as e:
        return f"Error searching message history: {str(e)}"


def get_telegram_context_summary(chat_history_obj, chat_id: int, hours_back: int = 24) -> str:
    """
    Get a summary of recent conversation context for understanding the flow.
    
    Use this when you need to understand the broader conversation context
    beyond just the last few messages.
    
    Args:
        chat_history_obj: Chat history manager instance  
        chat_id: ID of the chat
        hours_back: How many hours back to summarize (default 24)
        
    Returns:
        Formatted summary of recent conversation or "No recent activity"
    """
    if not chat_history_obj:
        return "No chat history available"
        
    try:
        # Get extended context
        context_messages = chat_history_obj.get_context(
            chat_id=chat_id,
            max_context_messages=15,  # More messages for summary
            max_age_hours=hours_back,
            always_include_last=3     # Always include last 3
        )
        
        if not context_messages:
            return f"No conversation activity in the last {hours_back} hours"
        
        # Format as conversation summary
        summary = f"Conversation summary (last {hours_back} hours, {len(context_messages)} messages):\n\n"
        
        for msg in context_messages:
            summary += f"{msg['role']}: {msg['content']}\n\n"
            
        return summary.strip()
        
    except Exception as e:
        return f"Error getting conversation summary: {str(e)}"