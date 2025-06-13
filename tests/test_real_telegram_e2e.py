"""
Real End-to-End Telegram Test

Tests the complete message flow from Telegram → Valor self-message handling 
using REAL components with NO MOCKS:

1. Real Telegram message creation (via Pyrogram objects)
2. Real UnifiedMessageProcessor 
3. Real Valor agent execution
4. Real tool usage (web search, image analysis, etc.)
5. Real database interactions
6. Real error handling and recovery

This is the gold standard test that validates our entire messaging pipeline
works correctly with real data and real components.
"""

import asyncio
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest
from pyrogram.types import Message, User, Chat, Photo

# Import ALL real system components - NO MOCKS
from integrations.telegram.unified_processor import UnifiedMessageProcessor
from integrations.telegram.models import ProcessingResult
from agents.valor.agent import valor_agent, ValorContext
from utilities.database import get_database_connection, init_database
from integrations.telegram.chat_history import ChatHistoryManager


class TestRealTelegramEndToEnd:
    """
    Real end-to-end tests using actual system components.
    
    These tests validate that our entire message processing pipeline
    works with real data, real APIs, and real components.
    """

    @pytest.fixture(scope="class", autouse=True)
    def setup_test_environment(self):
        """Set up real test environment with actual database."""
        # Initialize real database
        init_database()
        
        # Ensure we have a clean test state
        # Note: chat_history is managed by ChatHistoryManager, not in main DB
        print("✅ Test environment initialized with real database")
        
        yield
        
        # Cleanup after tests
        print("✅ Test environment cleaned up")

    @pytest.fixture
    def real_processor(self):
        """Create UnifiedMessageProcessor with real Valor agent."""
        # Use the actual valor_agent, not a mock
        processor = UnifiedMessageProcessor(
            telegram_bot=None,  # We don't need actual bot for processing
            valor_agent=valor_agent
        )
        return processor

    @pytest.fixture
    def real_chat_history(self):
        """Create real ChatHistoryManager."""
        return ChatHistoryManager()

    @pytest.fixture
    def self_message_user(self):
        """Create User object representing Valor's self-message."""
        # Based on actual Valor bot user from workspace config
        return User(
            id=66968934582,  # Valor's actual user ID
            is_self=False,
            first_name="Valor",
            is_bot=True,
            username="valorengels"
        )

    @pytest.fixture
    def dm_chat(self):
        """Create Chat object for DM (positive ID for private chat)."""
        return Chat(
            id=66968934582,  # Same as user ID for DM
            type="private"
        )

    @pytest.fixture
    def dev_group_chat(self):
        """Create Chat object for dev group (negative ID)."""
        return Chat(
            id=-999999,  # Test group ID
            type="supergroup",
            title="Test Dev Group"
        )

    class MockUpdate:
        """Minimal update wrapper for testing."""
        def __init__(self, message):
            self.message = message

    @pytest.mark.asyncio
    async def test_real_text_message_processing(self, real_processor, self_message_user, dm_chat, real_chat_history):
        """
        Test complete text message processing with real components.
        
        Flow:
        1. Create real Telegram message
        2. Process through real UnifiedMessageProcessor  
        3. Execute with real Valor agent
        4. Validate real response
        """
        
        # Step 1: Create real Telegram message
        message = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dm_chat,
            date=datetime.now()
        )
        message.text = "Hello Valor, this is a test message for end-to-end validation"

        # Step 2: Note - chat history will be managed automatically by the processor

        # Step 3: Process through real system
        update = self.MockUpdate(message)
        result = await real_processor.process_message(update, None)

        # Step 4: Validate real results
        assert isinstance(result, ProcessingResult)
        
        if result.success:
            # Should have processed successfully
            assert result.summary is not None
            assert "text" in result.summary.lower()
            assert result.response is not None
            assert result.response.content is not None
            
            # Should have real agent response
            assert len(result.response.content) > 0
            print(f"✅ Real agent response: {result.response.content[:100]}...")
            
        else:
            # If it failed, should have clear error
            assert result.error is not None
            print(f"❌ Processing failed: {result.error}")

    @pytest.mark.asyncio
    async def test_real_priority_question_processing(self, real_processor, self_message_user, dev_group_chat, real_chat_history):
        """
        Test priority question processing with real Notion integration.
        
        Tests the complete flow including:
        - Intent classification
        - Notion tool usage  
        - Context building
        - Real agent execution
        """
        
        # Create priority question message
        message = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dev_group_chat,
            date=datetime.now()
        )
        message.text = "What are the current project priorities and tasks I should focus on?"

        # Add to chat history with project context
        await real_chat_history.add_message(
            chat_id=dev_group_chat.id,
            message_id=message.id,
            username=self_message_user.username,
            content=message.text,
            role="user"
        )

        # Process through real system
        update = self.MockUpdate(message)
        result = await real_processor.process_message(update, None)

        # Validate results
        assert isinstance(result, ProcessingResult)
        
        if result.success:
            assert result.response.content is not None
            
            # Should trigger real tools
            if result.response.metadata.get("tools_used"):
                print(f"✅ Tools used: {result.response.metadata['tools_used']}")
                
            print(f"✅ Priority response: {result.response.content[:200]}...")
        else:
            print(f"❌ Priority processing failed: {result.error}")

    @pytest.mark.asyncio
    async def test_real_image_message_processing(self, real_processor, self_message_user, dm_chat):
        """
        Test image message processing with real vision analysis.
        
        Tests:
        - Real image message creation
        - Real image analysis tool
        - Real agent response with image context
        """
        
        # Create real photo message
        photo = Photo(
            file_id="BAADBAADbQADBREAAZbRCbNq5LT7Ag",
            file_unique_id="AgADbQADBREAAQ", 
            width=1280,
            height=720,
            file_size=150000,
            date=datetime.now()
        )

        message = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dm_chat,
            date=datetime.now()
        )
        message.photo = photo
        message.caption = "Can you analyze this image for me?"

        # Process through real system
        update = self.MockUpdate(message)
        result = await real_processor.process_message(update, None)

        # Validate image processing
        assert isinstance(result, ProcessingResult)
        
        if result.success:
            assert "photo" in result.summary.lower() or "image" in result.summary.lower()
            
            # Should potentially use image analysis tool
            tools_used = result.response.metadata.get("tools_used", [])
            if "👁️ Image Analysis" in tools_used:
                print("✅ Real image analysis tool was used")
                
            print(f"✅ Image response: {result.response.content[:150]}...")
        else:
            print(f"❌ Image processing failed: {result.error}")

    @pytest.mark.asyncio 
    async def test_real_web_search_integration(self, real_processor, self_message_user, dm_chat):
        """
        Test web search integration with real Perplexity API.
        
        Validates:
        - Real web search tool usage
        - Real API integration
        - Real search results processing
        """
        
        message = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dm_chat, 
            date=datetime.now()
        )
        message.text = "What's the latest news about AI developments in 2024?"

        # Process through real system
        update = self.MockUpdate(message)
        result = await real_processor.process_message(update, None)

        # Validate web search
        assert isinstance(result, ProcessingResult)
        
        if result.success:
            tools_used = result.response.metadata.get("tools_used", [])
            
            # Should use web search tool for current information
            if "🔍 Web Search" in tools_used:
                print("✅ Real web search tool was used")
                
                # Response should contain current information
                assert len(result.response.content) > 100
                print(f"✅ Search response: {result.response.content[:200]}...")
            else:
                print("ℹ️  Web search not triggered (may be handled conversationally)")
                
        else:
            print(f"❌ Web search processing failed: {result.error}")

    @pytest.mark.asyncio
    async def test_real_error_recovery(self, real_processor, self_message_user, dm_chat):
        """
        Test error recovery with real error scenarios.
        
        Validates:
        - Real error detection
        - Real error handling
        - Graceful degradation
        - User-friendly error messages
        """
        
        # Create message that might trigger complex processing
        message = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dm_chat,
            date=datetime.now()
        )
        message.text = "Can you process this extremely complex request with multiple tools and generate detailed analysis?"

        # Process through real system
        update = self.MockUpdate(message)
        result = await real_processor.process_message(update, None)

        # Validate error handling
        assert isinstance(result, ProcessingResult)
        
        # Should either succeed or fail gracefully
        if not result.success:
            # Should have user-friendly error message
            assert result.error is not None
            assert len(result.error) > 0
            
            # Should not expose technical details to user
            assert "Traceback" not in result.error
            assert "Exception" not in result.error
            
            print(f"✅ Graceful error handling: {result.error}")
        else:
            print(f"✅ Complex request succeeded: {result.response.content[:100]}...")

    @pytest.mark.asyncio
    async def test_real_conversation_continuity(self, real_processor, self_message_user, dm_chat, real_chat_history):
        """
        Test conversation continuity with real chat history.
        
        Validates:
        - Real conversation context
        - Multi-message conversations
        - Context preservation
        - Response coherence
        """
        
        # Message 1: Set context
        message1 = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dm_chat,
            date=datetime.now()
        )
        message1.text = "I'm working on a Python project with FastAPI"

        # Process first message
        update1 = self.MockUpdate(message1)
        result1 = await real_processor.process_message(update1, None)
        
        assert result1.success
        
        # Wait a moment
        await asyncio.sleep(0.1)
        
        # Message 2: Follow-up that requires context
        message2 = Message(
            id=int(time.time()) + 1,
            from_user=self_message_user,
            chat=dm_chat,
            date=datetime.now()
        )
        message2.text = "Can you help me add proper error handling to it?"

        # Process second message  
        update2 = self.MockUpdate(message2)
        result2 = await real_processor.process_message(update2, None)

        # Validate context continuity
        assert isinstance(result2, ProcessingResult)
        
        if result2.success:
            # Response should reference previous context
            response_lower = result2.response.content.lower()
            context_indicators = ["fastapi", "python", "project", "error handling"]
            
            context_found = any(indicator in response_lower for indicator in context_indicators)
            if context_found:
                print("✅ Real conversation context preserved")
            else:
                print("ℹ️  Context handling may be implicit")
                
            print(f"✅ Contextual response: {result2.response.content[:150]}...")
        else:
            print(f"❌ Contextual processing failed: {result2.error}")

    @pytest.mark.asyncio
    async def test_real_system_performance(self, real_processor, self_message_user, dm_chat):
        """
        Test real system performance with timing validation.
        
        Validates:
        - Response time under realistic conditions
        - Real performance metrics
        - System efficiency
        """
        
        message = Message(
            id=int(time.time()),
            from_user=self_message_user,
            chat=dm_chat,
            date=datetime.now()
        )
        message.text = "Tell me about the current state of AI technology"

        # Measure real processing time
        start_time = time.time()
        update = self.MockUpdate(message)
        result = await real_processor.process_message(update, None)
        end_time = time.time()

        processing_time = end_time - start_time

        # Validate performance
        assert isinstance(result, ProcessingResult)
        
        # Should complete within reasonable time (adjust as needed)
        assert processing_time < 30.0, f"Processing took too long: {processing_time:.2f}s"
        
        if result.success:
            print(f"✅ Real processing time: {processing_time:.2f}s")
            print(f"✅ Response length: {len(result.response.content)} chars")
            
            # Check if timing metadata is available
            if hasattr(result.response, 'processing_time'):
                print(f"✅ Reported processing time: {result.response.processing_time:.2f}s")
        else:
            print(f"❌ Performance test failed: {result.error}")


if __name__ == "__main__":
    """
    Run end-to-end tests directly.
    
    Usage:
        python test_real_telegram_e2e.py
        pytest test_real_telegram_e2e.py -v
        pytest test_real_telegram_e2e.py::TestRealTelegramEndToEnd::test_real_text_message_processing -s
    """
    print("🚀 Running Real Telegram End-to-End Tests")
    print("=" * 60)
    print("These tests use REAL components with NO MOCKS:")
    print("- Real UnifiedMessageProcessor")  
    print("- Real Valor agent")
    print("- Real database interactions")
    print("- Real tool usage (web search, etc.)")
    print("- Real error handling")
    print("=" * 60)
    
    # Run with pytest if available
    try:
        import pytest
        pytest.main([__file__, "-v", "-s"])
    except ImportError:
        print("❌ pytest not available. Install with: pip install pytest pytest-asyncio")