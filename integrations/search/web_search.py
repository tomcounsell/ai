"""Web search integration using Perplexity API."""

import os
import logging
from typing import Optional, Dict, List
from openai import OpenAI
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()


class WebSearcher:
    """Clean web search integration for Telegram bot using Perplexity."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('PERPLEXITY_API_KEY')
        self.client = None
        if self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.perplexity.ai"
            )
        
    def is_available(self) -> bool:
        """Check if search service is properly configured."""
        return bool(self.api_key and self.client)
    
    async def search(self, query: str, max_results: int = 3) -> Dict:
        """
        Perform web search and return formatted results.
        
        Args:
            query: Search terms
            max_results: Maximum number of results to return (not used with Perplexity)
            
        Returns:
            Dict with success status and results or error message
        """
        if not self.is_available():
            return {
                "success": False,
                "error": "Search service not configured. Missing PERPLEXITY_API_KEY."
            }
        
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful search assistant. Provide a concise, "
                        "informative answer based on current web information. "
                        "Keep responses under 300 words for messaging platforms."
                    ),
                },
                {
                    "role": "user",
                    "content": query,
                },
            ]
            
            response = self.client.chat.completions.create(
                model="sonar-pro",
                messages=messages,
                temperature=0.2,
                max_tokens=400
            )
            
            answer = response.choices[0].message.content
            return {
                "success": True,
                "query": query,
                "type": "perplexity",
                "answer": answer
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Search error: {str(e)}"
            }
    
    def format_for_telegram(self, search_result: Dict) -> str:
        """Format search results as a Telegram message."""
        if not search_result["success"]:
            return f"🔍 Search failed: {search_result['error']}"
        
        query = search_result["query"]
        
        # Handle Perplexity results (direct answer)
        if search_result["type"] == "perplexity":
            answer = search_result["answer"]
            return f"🔍 **{query}**\n\n{answer}"
        
        # Fallback for other result types
        return f"🔍 **{query}**\n\nNo results available."