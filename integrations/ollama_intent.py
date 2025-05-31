"""
Ollama-based intent recognition for preprocessing Telegram messages.

This module provides intent classification using local Ollama models to determine
the appropriate response strategy and tool access for incoming messages.
"""

import aiohttp
import asyncio
import json
import logging
from enum import Enum
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class MessageIntent(Enum):
    """Possible message intents for classification."""
    
    # General conversation
    CASUAL_CHAT = "casual_chat"          # Regular conversation, friendly chat
    QUESTION_ANSWER = "question_answer"   # Direct questions requiring factual answers
    
    # Work and productivity
    PROJECT_QUERY = "project_query"       # Questions about projects, tasks, status
    DEVELOPMENT_TASK = "development_task" # Code-related requests, programming help
    
    # Creative and content
    IMAGE_GENERATION = "image_generation" # Requests to create images
    IMAGE_ANALYSIS = "image_analysis"     # Analyzing shared images
    
    # Information and research
    WEB_SEARCH = "web_search"            # Requests requiring current web information
    LINK_ANALYSIS = "link_analysis"      # Analyzing shared links
    
    # Health checks and system
    SYSTEM_HEALTH = "system_health"      # Health checks, ping, status
    
    # Catch-all
    UNCLEAR = "unclear"                  # Intent cannot be determined


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: MessageIntent
    confidence: float
    reasoning: str
    suggested_emoji: str
    
    @property
    def is_high_confidence(self) -> bool:
        """Check if classification confidence is high enough to trust."""
        return self.confidence >= 0.7


class OllamaIntentClassifier:
    """Local Ollama-based intent classification for message preprocessing."""
    
    def __init__(self, model_name: str = "granite3.2-vision:latest", ollama_url: str = "http://localhost:11434"):
        """
        Initialize the Ollama intent classifier.
        
        Args:
            model_name: Name of the Ollama model to use for classification
            ollama_url: URL of the Ollama server
        """
        self.model_name = model_name
        self.ollama_url = ollama_url
        self.session = None
        
        # Intent-specific emoji mapping
        self.intent_emojis = {
            MessageIntent.CASUAL_CHAT: "😁",
            MessageIntent.QUESTION_ANSWER: "🤔",
            MessageIntent.PROJECT_QUERY: "🕊️",
            MessageIntent.DEVELOPMENT_TASK: "⚡",
            MessageIntent.IMAGE_GENERATION: "🍓",
            MessageIntent.IMAGE_ANALYSIS: "🙈",
            MessageIntent.WEB_SEARCH: "🗿",
            MessageIntent.LINK_ANALYSIS: "🍾",
            MessageIntent.SYSTEM_HEALTH: "🤝",
            MessageIntent.UNCLEAR: "🤨",
        }
        
        # System prompt for intent classification
        self.system_prompt = """You are an expert message intent classifier. Analyze the user's message and classify it into one of these specific intents:

1. casual_chat - Friendly conversation, greetings, personal topics, casual remarks
2. question_answer - Direct questions requiring factual answers, "what is...", "how does..."
3. project_query - Questions about work projects, tasks, deadlines, status updates
4. development_task - Code requests, programming help, technical implementation
5. image_generation - Requests to create, generate, or make images/artwork
6. image_analysis - Messages containing images for analysis (look for [Image] markers)
7. web_search - Requests for current information, news, recent events
8. link_analysis - Messages containing URLs for analysis (look for http/https)
9. system_health - Health checks, ping, status requests, system monitoring
10. unclear - Cannot determine intent clearly

Respond with a JSON object containing:
- intent: one of the exact intent names above
- confidence: float between 0.0-1.0
- reasoning: brief explanation of classification
- emoji: single appropriate emoji for this intent

Be decisive and pick the most likely intent even if uncertain."""

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5.0)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    async def classify_intent(self, message: str, context: Optional[Dict[str, Any]] = None) -> IntentResult:
        """
        Classify the intent of a message using Ollama.
        
        Args:
            message: The message text to classify
            context: Optional context information (chat_id, username, etc.)
            
        Returns:
            IntentResult with classification details
        """
        if not message or not message.strip():
            return IntentResult(
                intent=MessageIntent.UNCLEAR,
                confidence=1.0,
                reasoning="Empty message",
                suggested_emoji="🤔"
            )
            
        try:
            # Prepare the classification prompt
            user_prompt = f"Message to classify: '{message.strip()}'"
            
            # Add context if available
            if context:
                if context.get("has_image"):
                    user_prompt += "\n[Note: This message contains an image]"
                if context.get("has_links"):
                    user_prompt += "\n[Note: This message contains URLs]"
                if context.get("is_group_chat"):
                    user_prompt += "\n[Note: This is from a group chat]"
            
            # Make request to Ollama
            response = await self._make_ollama_request(user_prompt)
            
            # Parse the response
            result = self._parse_classification_response(response, message)
            
            logger.info(f"Intent classified: {result.intent.value} (confidence: {result.confidence:.2f})")
            return result
            
        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            # Fallback to rule-based classification
            return self._fallback_classification(message, context)

    async def _make_ollama_request(self, prompt: str) -> str:
        """Make a request to the Ollama API."""
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5.0)
            )
            
        payload = {
            "model": self.model_name,
            "prompt": f"{self.system_prompt}\n\n{prompt}",
            "stream": False,
            "options": {
                "temperature": 0.1,  # Low temperature for consistent classification
                "top_p": 0.9,
                "num_predict": 200,  # Limit response length
            }
        }
        
        async with self.session.post(
            f"{self.ollama_url}/api/generate",
            json=payload
        ) as response:
            if response.status != 200:
                raise Exception(f"Ollama API error: {response.status}")
                
            result = await response.json()
            return result.get("response", "")

    def _parse_classification_response(self, response: str, original_message: str) -> IntentResult:
        """Parse the Ollama response into an IntentResult."""
        try:
            # Try to extract JSON from response
            response_clean = response.strip()
            
            # Handle cases where response might not be pure JSON
            json_start = response_clean.find('{')
            json_end = response_clean.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_clean[json_start:json_end]
                result_data = json.loads(json_str)
            else:
                # If no JSON found, try to parse the whole response
                result_data = json.loads(response_clean)
            
            # Extract and validate fields
            intent_str = result_data.get("intent", "unclear").lower()
            confidence = float(result_data.get("confidence", 0.5))
            reasoning = result_data.get("reasoning", "Classified by AI")
            suggested_emoji = result_data.get("emoji", "🤔")
            
            # Map intent string to enum
            intent = self._map_intent_string(intent_str)
            
            # Validate confidence
            confidence = max(0.0, min(1.0, confidence))
            
            # Use default emoji if not provided
            if not suggested_emoji or len(suggested_emoji) != 1:
                suggested_emoji = self.intent_emojis.get(intent, "🤔")
            
            return IntentResult(
                intent=intent,
                confidence=confidence,
                reasoning=reasoning,
                suggested_emoji=suggested_emoji
            )
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse Ollama response: {e}")
            logger.debug(f"Raw response: {response}")
            
            # Fallback to rule-based classification
            return self._fallback_classification(original_message)

    def _map_intent_string(self, intent_str: str) -> MessageIntent:
        """Map intent string to MessageIntent enum."""
        intent_mapping = {
            "casual_chat": MessageIntent.CASUAL_CHAT,
            "question_answer": MessageIntent.QUESTION_ANSWER,
            "project_query": MessageIntent.PROJECT_QUERY,
            "development_task": MessageIntent.DEVELOPMENT_TASK,
            "image_generation": MessageIntent.IMAGE_GENERATION,
            "image_analysis": MessageIntent.IMAGE_ANALYSIS,
            "web_search": MessageIntent.WEB_SEARCH,
            "link_analysis": MessageIntent.LINK_ANALYSIS,
            "system_health": MessageIntent.SYSTEM_HEALTH,
            "unclear": MessageIntent.UNCLEAR,
        }
        
        return intent_mapping.get(intent_str.lower(), MessageIntent.UNCLEAR)

    def _fallback_classification(self, message: str, context: Optional[Dict[str, Any]] = None) -> IntentResult:
        """Fallback rule-based classification when Ollama fails."""
        message_lower = message.lower().strip()
        
        # System health checks
        if message_lower in ["ping", "health", "status"]:
            return IntentResult(
                intent=MessageIntent.SYSTEM_HEALTH,
                confidence=1.0,
                reasoning="System health keyword detected",
                suggested_emoji="🏓"
            )
        
        # Image analysis (check for image markers)
        if any(marker in message.upper() for marker in ["[IMAGE]", "[PHOTO]", "IMAGE FILE PATH:"]):
            return IntentResult(
                intent=MessageIntent.IMAGE_ANALYSIS,
                confidence=0.9,
                reasoning="Image content markers detected",
                suggested_emoji="👁️"
            )
        
        # Link analysis (check for URLs)
        if any(url in message_lower for url in ["http://", "https://", "www."]):
            return IntentResult(
                intent=MessageIntent.LINK_ANALYSIS,
                confidence=0.9,
                reasoning="URL detected in message",
                suggested_emoji="🔗"
            )
        
        # Image generation requests
        image_keywords = ["generate", "create", "make", "draw", "image", "picture", "art"]
        if any(keyword in message_lower for keyword in image_keywords):
            return IntentResult(
                intent=MessageIntent.IMAGE_GENERATION,
                confidence=0.7,
                reasoning="Image creation keywords detected",
                suggested_emoji="🎨"
            )
        
        # Development tasks
        dev_keywords = ["code", "bug", "fix", "implement", "function", "class", "variable", "debug"]
        if any(keyword in message_lower for keyword in dev_keywords):
            return IntentResult(
                intent=MessageIntent.DEVELOPMENT_TASK,
                confidence=0.7,
                reasoning="Development keywords detected",
                suggested_emoji="⚙️"
            )
        
        # Project queries
        project_keywords = ["project", "task", "deadline", "status", "progress", "psyoptimal", "flextrip"]
        if any(keyword in message_lower for keyword in project_keywords):
            return IntentResult(
                intent=MessageIntent.PROJECT_QUERY,
                confidence=0.7,
                reasoning="Project keywords detected",
                suggested_emoji="📋"
            )
        
        # Web search indicators
        search_keywords = ["what's", "latest", "news", "current", "recent", "today", "now"]
        if any(keyword in message_lower for keyword in search_keywords):
            return IntentResult(
                intent=MessageIntent.WEB_SEARCH,
                confidence=0.6,
                reasoning="Current information keywords detected",
                suggested_emoji="🔍"
            )
        
        # Question indicators
        question_markers = ["?", "what", "how", "why", "when", "where", "who"]
        if any(marker in message_lower for marker in question_markers):
            return IntentResult(
                intent=MessageIntent.QUESTION_ANSWER,
                confidence=0.6,
                reasoning="Question markers detected",
                suggested_emoji="🤔"
            )
        
        # Default to casual chat
        return IntentResult(
            intent=MessageIntent.CASUAL_CHAT,
            confidence=0.5,
            reasoning="No specific intent markers detected, defaulting to casual chat",
            suggested_emoji="💬"
        )

    async def check_ollama_availability(self) -> bool:
        """Check if Ollama server is available."""
        try:
            if not self.session:
                self.session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=2.0)
                )
                
            async with self.session.get(f"{self.ollama_url}/api/tags") as response:
                return response.status == 200
                
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False


# Singleton instance for use throughout the application
intent_classifier = OllamaIntentClassifier()


async def classify_message_intent(message: str, context: Optional[Dict[str, Any]] = None) -> IntentResult:
    """
    Classify the intent of a message.
    
    Convenience function that uses the singleton classifier instance.
    
    Args:
        message: The message text to classify
        context: Optional context information
        
    Returns:
        IntentResult with classification details
    """
    async with intent_classifier as classifier:
        return await classifier.classify_intent(message, context)