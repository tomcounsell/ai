"""
UnifiedMessageProcessor: Main entry point replacing the monolithic MessageHandler.

Implements the clean 5-step processing pipeline for all Telegram messages.
"""

import asyncio
import logging
import time

# Using pyrogram in this project, not python-telegram-bot
# These will be passed in as parameters
from typing import Any

from integrations.telegram.components import (
    AgentOrchestrator,
    ContextBuilder,
    ResponseManager,
    SecurityGate,
    TypeRouter,
)
from integrations.telegram.models import ProcessingResult
from integrations.telegram.reaction_manager import ReactionManager
from integrations.ollama_intent import OllamaIntentClassifier, IntentResult

logger = logging.getLogger(__name__)


class UnifiedMessageProcessor:
    """
    Main entry point for unified message processing.

    Replaces the 1,994-line monolithic handler with a clean 5-step pipeline:
    1. Security validation
    2. Context building
    3. Type routing
    4. Agent processing
    5. Response delivery
    """

    def __init__(self, telegram_bot: Any | None = None, valor_agent=None):
        """Initialize processor with all components."""
        self.security_gate = SecurityGate()
        self.context_builder = ContextBuilder()
        self.type_router = TypeRouter()
        self.agent_orchestrator = AgentOrchestrator(valor_agent=valor_agent)
        self.response_manager = ResponseManager(telegram_bot=telegram_bot)
        
        # Initialize sophisticated emoji reaction system
        self.reaction_manager = ReactionManager(telegram_bot) if telegram_bot else None
        self.ollama_classifier = OllamaIntentClassifier()

        # Metrics
        self.processed_count = 0
        self.error_count = 0
        self.total_processing_time = 0

    async def process_message(self, update: Any, context: Any) -> ProcessingResult:
        """
        Unified 5-step processing pipeline.

        Clean, predictable flow replacing the previous 19-step chaos.
        Each step has single responsibility and clear boundaries.
        """
        start_time = time.time()
        message = update.message

        if not message:
            return ProcessingResult.failed("No message in update")

        try:
            # Step 0: Immediate read receipt (👀)
            if self.reaction_manager:
                await self.reaction_manager.add_read_receipt(message.chat.id, message.id)
                logger.debug(f"👀 Added read receipt for message {message.id}")
            
            # Step 1: Security validation
            logger.debug(f"Step 1: Security validation for message {message.id}")
            access_result = self.security_gate.validate_access(message)

            if not access_result.allowed:
                # Add error reaction for access denied
                if self.reaction_manager:
                    await self.reaction_manager.add_completion_reaction(
                        message.chat.id, message.id, success=False, 
                        error=Exception(access_result.reason)
                    )
                
                # Skip silently for bot messages and old messages
                if access_result.metadata.get("skip_silently"):
                    logger.debug(f"Silently skipping message: {access_result.reason}")
                else:
                    logger.info(f"Access denied for message: {access_result.reason}")

                return ProcessingResult.denied(access_result.reason)

            # Step 2: Context building
            logger.debug(f"Step 2: Building context for message {message.id}")
            msg_context = await self.context_builder.build_context(message)

            # Check if response is required
            if not msg_context.requires_response:
                logger.debug("Message does not require response")
                return ProcessingResult(
                    success=True,
                    summary="Message processed, no response needed",
                    context=msg_context,
                )

            # Step 3: Type routing + Intent classification
            logger.debug(f"Step 3: Routing message type {msg_context.media_info}")
            plan = await self.type_router.route_message(msg_context)
            logger.info(
                f"Routed {plan.message_type.value} message, "
                f"priority={plan.priority.value}, "
                f"requires_agent={plan.requires_agent}"
            )
            
            # Intent classification with reaction if needed
            if plan.requires_agent and msg_context.cleaned_text:
                try:
                    intent = await self.ollama_classifier.classify_intent(msg_context.cleaned_text)
                    if intent and self.reaction_manager:
                        await self.reaction_manager.add_intent_reaction(
                            message.chat.id, message.id, intent
                        )
                        logger.debug(f"🧠 Added intent reaction for {intent.intent.value if intent.intent else 'unknown'}")
                        plan.intent = intent
                except Exception as intent_error:
                    logger.warning(f"Intent classification failed: {intent_error}")

            # Step 4: Agent processing with progress indicator
            logger.debug("Step 4: Processing with agent orchestrator")
            if self.reaction_manager:
                await self.reaction_manager.add_progress_reaction(
                    message.chat.id, message.id, "agent_processing"
                )
                logger.debug("⏳ Added progress reaction for agent processing")
                
            agent_response = await self.agent_orchestrator.process_with_agent(msg_context, plan)

            # Step 5: Response delivery
            logger.debug("Step 5: Delivering response")
            delivery_result = await self.response_manager.deliver_response(
                agent_response, msg_context
            )

            # Step 6: Success completion reaction (👍)
            if self.reaction_manager:
                await self.reaction_manager.add_completion_reaction(
                    message.chat.id, message.id, success=True
                )
                logger.debug("👍 Added success completion reaction")

            # Record metrics
            processing_time = time.time() - start_time
            self.processed_count += 1
            self.total_processing_time += processing_time

            # Build success result
            return ProcessingResult.succeeded(
                summary=f"Processed {plan.message_type.value} in {processing_time:.2f}s",
                context=msg_context,
                response=agent_response,
                delivery=delivery_result,
            )

        except asyncio.CancelledError:
            # Handle graceful shutdown
            logger.info("Processing cancelled")
            raise

        except Exception as e:
            # Record error metrics
            self.error_count += 1
            processing_time = time.time() - start_time

            logger.error(
                f"Processing error for message {message.id}: {str(e)}", exc_info=True
            )
            
            # Step 6: Error completion reaction (❌) + Recovery trigger
            if self.reaction_manager:
                await self.reaction_manager.add_completion_reaction(
                    message.chat.id, message.id, success=False, error=e
                )
                logger.debug(f"❌ Added error completion reaction for {type(e).__name__}")

            # Try to send error response
            if "msg_context" in locals():
                try:
                    from integrations.telegram.models import AgentResponse

                    error_response = self.response_manager.create_fallback_response(e)
                    await self.response_manager.deliver_response(
                        AgentResponse(content=error_response), msg_context
                    )
                except Exception as delivery_error:
                    logger.error(f"Failed to deliver error response: {delivery_error}")

            return ProcessingResult.failed(
                error=str(e), context=msg_context if "msg_context" in locals() else None
            )

    async def process_message_batch(self, messages: list) -> list:
        """Process multiple messages concurrently."""
        tasks = []
        for update in messages:
            task = asyncio.create_task(self.process_message(update, None))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions in results
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                processed_results.append(ProcessingResult.failed(str(result)))
            else:
                processed_results.append(result)

        return processed_results

    def get_metrics(self) -> dict:
        """Get processing metrics."""
        avg_time = (
            self.total_processing_time / self.processed_count if self.processed_count > 0 else 0
        )

        error_rate = (
            self.error_count / (self.processed_count + self.error_count)
            if (self.processed_count + self.error_count) > 0
            else 0
        )

        return {
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "error_rate": error_rate,
            "average_processing_time": avg_time,
            "total_processing_time": self.total_processing_time,
        }

    def reset_metrics(self):
        """Reset processing metrics."""
        self.processed_count = 0
        self.error_count = 0
        self.total_processing_time = 0

    async def health_check(self) -> dict:
        """Perform health check on all components."""
        health = {"status": "healthy", "components": {}, "metrics": self.get_metrics()}

        # Check each component
        try:
            # Security gate check
            self.security_gate.get_chat_status(123456)
            health["components"]["security_gate"] = "healthy"
        except Exception as e:
            health["components"]["security_gate"] = f"unhealthy: {str(e)}"
            health["status"] = "degraded"

        # Add more component checks as needed

        return health


async def create_unified_processor(bot: Any, valor_agent=None) -> UnifiedMessageProcessor:
    """Factory function to create and initialize processor."""
    processor = UnifiedMessageProcessor(telegram_bot=bot, valor_agent=valor_agent)

    # Perform initial health check
    health = await processor.health_check()
    if health["status"] != "healthy":
        logger.warning(f"Processor starting in degraded state: {health}")

    return processor
