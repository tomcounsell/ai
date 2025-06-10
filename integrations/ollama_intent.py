"""
Ollama-based intent recognition for preprocessing Telegram messages.

This module provides intent classification using local Ollama models to determine
the appropriate response strategy and tool access for incoming messages.
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class MessageIntent(Enum):
    """Possible message intents for classification."""

    # General conversation
    CASUAL_CHAT = "casual_chat"  # Regular conversation, friendly chat
    QUESTION_ANSWER = "question_answer"  # Direct questions requiring factual answers

    # Work and productivity
    PROJECT_QUERY = "project_query"  # Questions about projects, tasks, status
    DEVELOPMENT_TASK = "development_task"  # Code-related requests, programming help

    # Creative and content
    IMAGE_GENERATION = "image_generation"  # Requests to create images
    IMAGE_ANALYSIS = "image_analysis"  # Analyzing shared images

    # Information and research
    WEB_SEARCH = "web_search"  # Requests requiring current web information
    LINK_ANALYSIS = "link_analysis"  # Analyzing shared links

    # Health checks and system
    SYSTEM_HEALTH = "system_health"  # Health checks, ping, status

    # Catch-all
    UNCLEAR = "unclear"  # Intent cannot be determined


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
    """
    Multi-tier intent classification system with intelligent fallbacks:

    1. Primary: Ollama (granite3.2-vision) - Local, fast, privacy-preserving
    2. Fallback: GPT-3.5 Turbo - Requires OPENAI_API_KEY in environment
    3. Last resort: Rule-based classification - Always available
    """

    def __init__(
        self,
        model_name: str = "granite3.2-vision:latest",
        ollama_url: str = "http://localhost:11434",
    ):
        """
        Initialize the Ollama intent classifier.

        Args:
            model_name: Name of the Ollama model to use for classification
            ollama_url: URL of the Ollama server
        """
        self.model_name = model_name
        self.ollama_url = ollama_url
        self.session = None

        # Intent-specific emoji mapping using valid Telegram reaction emojis
        # Note: Updated to use only valid Telegram reactions
        self.intent_emojis = {
            MessageIntent.CASUAL_CHAT: "😁",
            MessageIntent.QUESTION_ANSWER: "🤔",
            MessageIntent.PROJECT_QUERY: "🙏",
            MessageIntent.DEVELOPMENT_TASK: "👨‍💻",
            MessageIntent.IMAGE_GENERATION: "🎉",  # Changed from 🎨 (not available)
            MessageIntent.IMAGE_ANALYSIS: "👀",
            MessageIntent.WEB_SEARCH: "🗿",
            MessageIntent.LINK_ANALYSIS: "🍾",
            MessageIntent.SYSTEM_HEALTH: "❤",  # Changed from ❤️ (variant selector issue)
            MessageIntent.UNCLEAR: "🤨",
        }

        # Import valid emojis and descriptions at initialization
        from .telegram.emoji_mapping import VALID_TELEGRAM_REACTIONS, EMOJI_DESCRIPTIONS
        
        # Create a formatted list of valid emojis with descriptions for the prompt
        emoji_entries = []
        for emoji in sorted(VALID_TELEGRAM_REACTIONS):
            if emoji in EMOJI_DESCRIPTIONS:
                emoji_entries.append(f"{emoji} - {EMOJI_DESCRIPTIONS[emoji]}")
            else:
                emoji_entries.append(emoji)
        valid_emoji_list = '\n'.join(emoji_entries)
        
        # System prompt for intent classification
        self.system_prompt = f"""You are an expert message intent classifier. Analyze the user's message and classify it into one of these specific intents:

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
- emoji: single emoji from the list below that best represents this message's intent and mood

IMPORTANT: You MUST choose your emoji from ONLY these valid Telegram reaction emojis. Each emoji has a specific meaning:

{valid_emoji_list}

Choose the emoji that best matches the message's mood, topic, or action based on the descriptions above. If unsure, use 🤔 (thinking).

Be decisive and pick the most likely intent even if uncertain."""

    async def __aenter__(self):
        """Async context manager entry."""
        # Increased timeout to handle resource contention from multiple aider processes
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    async def classify_intent(
        self, message: str, context: dict[str, Any] | None = None
    ) -> IntentResult:
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
                suggested_emoji="🤔",
            )

        try:
            logger.debug(f"Starting Ollama intent classification for message: '{message[:50]}...'")
            
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

            logger.debug(f"Prepared user prompt: {user_prompt}")

            # Make request to Ollama with retry for timeouts
            logger.debug(f"Making Ollama request to {self.ollama_url} with model {self.model_name}")
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    response = await self._make_ollama_request(user_prompt)
                    logger.debug(f"Ollama response received: {response[:200]}...")
                    break
                except Exception as e:
                    if ("timeout" in str(e).lower() or "TimeoutError" in str(e)) and attempt < max_retries:
                        logger.warning(f"Ollama timeout on attempt {attempt + 1}/{max_retries + 1}, retrying...")
                        continue
                    else:
                        raise

            # Parse the response
            logger.debug("Parsing Ollama response")
            result = self._parse_classification_response(response, message)

            logger.info(
                f"Intent classified: {result.intent.value} (confidence: {result.confidence:.2f})"
            )
            return result

        except Exception as e:
            error_msg = str(e) if e else "Unknown error"
            if "TimeoutError" in str(e) or "timeout" in str(e).lower():
                logger.warning(f"Ollama timeout (resource contention from multiple aider processes): {error_msg}")
            else:
                logger.error(f"Intent classification failed: {error_msg}", exc_info=True)
            logger.debug(f"Message that failed: '{message[:100]}...'")
            logger.debug(f"Context that failed: {context}")
            # Fallback to GPT-3.5 Turbo classification
            return await self._gpt_fallback_classification(message, context)

    async def _make_ollama_request(self, prompt: str) -> str:
        """Make a request to the Ollama API."""
        logger.debug(f"Creating Ollama request session if needed")
        if not self.session:
            # Increased timeout to handle resource contention from multiple aider processes
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0))

        payload = {
            "model": self.model_name,
            "prompt": f"{self.system_prompt}\n\n{prompt}",
            "stream": False,
            "options": {
                "temperature": 0.1,  # Low temperature for consistent classification
                "top_p": 0.9,
                "num_predict": 200,  # Limit response length
            },
        }

        logger.debug(f"Ollama request payload: model={self.model_name}, prompt_length={len(payload['prompt'])}")
        
        try:
            async with self.session.post(f"{self.ollama_url}/api/generate", json=payload) as response:
                logger.debug(f"Ollama response status: {response.status}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ollama API error: {response.status} - {error_text}")
                    raise Exception(f"Ollama API error: {response.status} - {error_text}")

                result = await response.json()
                response_text = result.get("response", "")
                logger.debug(f"Ollama response length: {len(response_text)} characters")
                return response_text
        except aiohttp.ClientError as e:
            logger.error(f"Ollama client error: {e}")
            raise Exception(f"Ollama connection error: {e}")
        except Exception as e:
            logger.error(f"Ollama request error: {e}")
            raise

    def _parse_classification_response(self, response: str, original_message: str) -> IntentResult:
        """Parse the Ollama response into an IntentResult."""
        try:
            # Try to extract JSON from response
            response_clean = response.strip()

            # Handle cases where response might not be pure JSON
            json_start = response_clean.find("{")
            json_end = response_clean.rfind("}") + 1

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

            # Use default emoji if not provided or invalid for Telegram
            from .telegram.emoji_mapping import VALID_TELEGRAM_REACTIONS

            if (
                not suggested_emoji
                or len(suggested_emoji) != 1
                or suggested_emoji not in VALID_TELEGRAM_REACTIONS
            ):
                suggested_emoji = self.intent_emojis.get(intent, "🤔")

            return IntentResult(
                intent=intent,
                confidence=confidence,
                reasoning=reasoning,
                suggested_emoji=suggested_emoji,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            error_msg = str(e) if e else "Unknown parsing error"
            logger.warning(f"Failed to parse Ollama response: {error_msg}")
            logger.debug(f"Raw response: {response[:200]}...")

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

    def _fallback_classification(
        self, message: str, context: dict[str, Any] | None = None
    ) -> IntentResult:
        """Fallback rule-based classification when Ollama fails."""
        message_lower = message.lower().strip()

        # System health checks
        if message_lower in ["ping", "health", "status"]:
            return IntentResult(
                intent=MessageIntent.SYSTEM_HEALTH,
                confidence=1.0,
                reasoning="System health keyword detected",
                suggested_emoji="❤",
            )

        # Image analysis (check for image markers)
        if any(marker in message.upper() for marker in ["[IMAGE]", "[PHOTO]", "IMAGE FILE PATH:"]):
            return IntentResult(
                intent=MessageIntent.IMAGE_ANALYSIS,
                confidence=0.9,
                reasoning="Image content markers detected",
                suggested_emoji="👀",
            )

        # Link analysis (check for URLs)
        if any(url in message_lower for url in ["http://", "https://", "www."]):
            return IntentResult(
                intent=MessageIntent.LINK_ANALYSIS,
                confidence=0.9,
                reasoning="URL detected in message",
                suggested_emoji="🍾",
            )

        # Image generation requests
        image_keywords = ["generate", "create", "make", "draw", "image", "picture", "art"]
        if any(keyword in message_lower for keyword in image_keywords):
            return IntentResult(
                intent=MessageIntent.IMAGE_GENERATION,
                confidence=0.7,
                reasoning="Image creation keywords detected",
                suggested_emoji="🎉",
            )

        # Development tasks
        dev_keywords = ["code", "bug", "fix", "implement", "function", "class", "variable", "debug"]
        if any(keyword in message_lower for keyword in dev_keywords):
            return IntentResult(
                intent=MessageIntent.DEVELOPMENT_TASK,
                confidence=0.7,
                reasoning="Development keywords detected",
                suggested_emoji="👨‍💻",
            )

        # Project queries
        project_keywords = [
            "project",
            "task",
            "deadline",
            "status",
            "progress",
            "psyoptimal",
            "flextrip",
        ]
        if any(keyword in message_lower for keyword in project_keywords):
            return IntentResult(
                intent=MessageIntent.PROJECT_QUERY,
                confidence=0.7,
                reasoning="Project keywords detected",
                suggested_emoji="🙏",
            )

        # Web search indicators
        search_keywords = ["what's", "latest", "news", "current", "recent", "today", "now"]
        if any(keyword in message_lower for keyword in search_keywords):
            return IntentResult(
                intent=MessageIntent.WEB_SEARCH,
                confidence=0.6,
                reasoning="Current information keywords detected",
                suggested_emoji="🗿",
            )

        # Question indicators
        question_markers = ["?", "what", "how", "why", "when", "where", "who"]
        if any(marker in message_lower for marker in question_markers):
            return IntentResult(
                intent=MessageIntent.QUESTION_ANSWER,
                confidence=0.6,
                reasoning="Question markers detected",
                suggested_emoji="🤔",
            )

        # Default to casual chat
        return IntentResult(
            intent=MessageIntent.CASUAL_CHAT,
            confidence=0.5,
            reasoning="No specific intent markers detected, defaulting to casual chat",
            suggested_emoji="😁",
        )

    async def _gpt_fallback_classification(
        self, message: str, context: dict[str, Any] | None = None
    ) -> IntentResult:
        """Fallback to GPT-3.5 Turbo when Ollama fails."""
        try:
            import os

            import openai

            # Check if OpenAI API key is available
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.warning(
                    "OPENAI_API_KEY not found, falling back to rule-based classification"
                )
                return self._fallback_classification(message, context)

            # Initialize OpenAI client
            client = openai.OpenAI(api_key=api_key)

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

            # Make request to GPT-3.5 Turbo
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=200,
            )

            result_text = response.choices[0].message.content

            # Parse the response using the same parsing logic
            result = self._parse_classification_response(result_text, message)

            logger.info(
                f"GPT-3.5 fallback classified: {result.intent.value} (confidence: {result.confidence:.2f})"
            )
            return result

        except Exception as e:
            logger.error(f"GPT-3.5 fallback also failed: {e}")
            # Last resort: rule-based classification
            return self._fallback_classification(message, context)

    async def check_ollama_availability(self) -> bool:
        """Check if Ollama server is available."""
        try:
            if not self.session:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0))

            async with self.session.get(f"{self.ollama_url}/api/tags") as response:
                return response.status == 200

        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False


# Singleton instance for use throughout the application
intent_classifier = OllamaIntentClassifier()


async def classify_message_intent(
    message: str, context: dict[str, Any] | None = None
) -> IntentResult:
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
