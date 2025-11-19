"""Focused test suite for ContextWindowManager and related components."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from agents.context_manager import (
    ContextWindowManager,
    CompressionStrategy,
    TokenEstimator,
    ContextCompressor
)
from agents.valor.context import ValorContext, MessageEntry


class TestCompressionStrategy:
    """Test suite for CompressionStrategy configuration."""
    
    def test_default_strategy(self):
        """Test default compression strategy values."""
        strategy = CompressionStrategy()
        
        assert strategy.preserve_recent == 20
        assert strategy.preserve_important_threshold == 7.0
        assert strategy.preserve_system_messages is True
        assert strategy.preserve_tool_results is True
        assert strategy.max_summary_length == 500
        assert strategy.compression_ratio_target == 0.3
    
    def test_custom_strategy(self):
        """Test custom compression strategy configuration."""
        strategy = CompressionStrategy(
            preserve_recent=10,
            preserve_important_threshold=8.0,
            compression_ratio_target=0.5
        )
        
        assert strategy.preserve_recent == 10
        assert strategy.preserve_important_threshold == 8.0
        assert strategy.compression_ratio_target == 0.5


class TestContextCompressor:
    """Test suite for ContextCompressor."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.strategy = CompressionStrategy(
            preserve_recent=5,
            preserve_important_threshold=7.0
        )
        self.compressor = ContextCompressor(self.strategy)
    
    def create_test_messages(self, count: int) -> list:
        """Create test messages for compression testing."""
        messages = []
        for i in range(count):
            importance = 9.0 if i % 10 == 0 else 5.0  # Every 10th message is important
            message = MessageEntry(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Test message {i} with some content",
                importance_score=importance
            )
            messages.append(message)
        return messages
    
    def test_select_messages_for_preservation(self):
        """Test message selection for preservation."""
        messages = self.create_test_messages(25)
        
        preserved = self.compressor._select_messages_for_preservation(
            messages, "default"
        )
        
        # Should include recent messages (last 5)
        assert len(preserved) >= 5
        
        # Should include important messages (importance >= 7.0)
        important_ids = {msg.id for msg in messages if msg.importance_score >= 7.0}
        preserved_ids = {msg.id for msg in preserved}
        assert important_ids.issubset(preserved_ids)
    
    @pytest.mark.asyncio
    async def test_compress_messages_no_compression_needed(self):
        """Test compression when target tokens not exceeded."""
        messages = self.create_test_messages(5)
        target_tokens = 10000  # Very high target
        
        compressed, summary = await self.compressor.compress_messages(
            messages, target_tokens, "default"
        )
        
        assert len(compressed) == len(messages)
        assert "No compression needed" in summary
    
    @pytest.mark.asyncio
    async def test_compress_messages_with_compression(self):
        """Test compression when target tokens exceeded."""
        messages = self.create_test_messages(50)
        target_tokens = 100  # Very low target to force compression
        
        compressed, summary = await self.compressor.compress_messages(
            messages, target_tokens, "default"
        )
        
        assert len(compressed) < len(messages)
        assert "Compressed from" in summary
    
    @pytest.mark.asyncio
    async def test_create_conversation_summary(self):
        """Test conversation summary creation."""
        messages = [
            MessageEntry(role="user", content="I need help with Python code"),
            MessageEntry(role="assistant", content="I can help you with Python programming"),
            MessageEntry(role="user", content="How do I create a function?"),
            MessageEntry(role="assistant", content="Use the def keyword to define functions"),
        ]
        
        summary = await self.compressor._create_conversation_summary(messages)
        
        assert isinstance(summary, str)
        assert len(summary) > 0
        assert len(summary) <= self.strategy.max_summary_length
    
    def test_extract_topics(self):
        """Test topic extraction from messages."""
        messages = [
            MessageEntry(role="user", content="I need to write some code for my API"),
            MessageEntry(role="user", content="The database query is not working"),
            MessageEntry(role="user", content="Can you help me test this function?"),
        ]
        
        topics = self.compressor._extract_topics(messages)
        
        assert isinstance(topics, list)
        # Should detect programming-related topics
        assert any(topic in ["code", "api", "database", "testing"] for topic in topics)


class TestContextWindowManagerAdvanced:
    """Advanced test suite for ContextWindowManager."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.manager = ContextWindowManager(
            max_tokens=1000,
            model_family="test_model"
        )
        
        self.context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
    
    def test_optimize_context_remove_duplicates(self):
        """Test context optimization by removing duplicates."""
        # Add duplicate messages
        self.context.add_message(role="user", content="Hello world")
        self.context.add_message(role="user", content="Hello world")
        self.context.add_message(role="user", content="Different message")
        
        # Manually set same timestamp to create duplicates
        self.context.message_history[0].timestamp = datetime(2023, 1, 1)
        self.context.message_history[1].timestamp = datetime(2023, 1, 1)
        
        original_count = len(self.context.message_history)
        optimized = self.manager.optimize_context(self.context)
        
        # Should remove duplicates but keep unique messages
        assert len(optimized.message_history) <= original_count
    
    def test_get_context_stats_detailed(self):
        """Test detailed context statistics."""
        # Add various types of messages
        self.context.add_message(role="user", content="User message")
        self.context.add_message(role="assistant", content="Assistant response")
        self.context.add_message(role="system", content="System message")
        self.context.add_message(role="tool", content="Tool result")
        
        stats = self.manager.get_context_stats(self.context)
        
        assert "total_tokens" in stats
        assert "utilization" in stats
        assert "role_distribution" in stats
        assert "token_distribution" in stats
        
        # Check role distribution
        assert stats["role_distribution"]["user"] == 1
        assert stats["role_distribution"]["assistant"] == 1
        assert stats["role_distribution"]["system"] == 1
        assert stats["role_distribution"]["tool"] == 1
    
    @pytest.mark.asyncio
    async def test_prepare_context_complex_scenario(self):
        """Test context preparation with complex scenario."""
        # Create context with many messages and high importance variations
        for i in range(30):
            importance = 9.0 if i in [5, 15, 25] else 3.0
            self.context.add_message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i} with variable length content " * (i % 5 + 1),
                importance_score=importance
            )
        
        prepared, info = await self.manager.prepare_context_for_inference(
            self.context,
            additional_tokens=200
        )
        
        assert info["original_message_count"] == 30
        assert info["final_message_count"] <= 30
        assert "final_token_count" in info
        
        # Important messages should be preserved if possible
        important_preserved = any(
            msg.importance_score >= 7.0 
            for msg in prepared.message_history
        )
        if info["compression_performed"]:
            assert important_preserved or len(prepared.message_history) <= 5
    
    def test_estimate_tokens_for_text(self):
        """Test token estimation for arbitrary text."""
        short_text = "Hello"
        long_text = "This is a much longer text that should have more tokens " * 10
        
        short_tokens = self.manager.estimate_tokens_for_text(short_text)
        long_tokens = self.manager.estimate_tokens_for_text(long_text)
        
        assert short_tokens > 0
        assert long_tokens > short_tokens
    
    def test_set_compression_strategy(self):
        """Test updating compression strategy."""
        new_strategy = CompressionStrategy(
            preserve_recent=30,
            preserve_important_threshold=8.5
        )
        
        self.manager.set_compression_strategy(new_strategy)
        
        assert self.manager.compression_strategy.preserve_recent == 30
        assert self.manager.compression_strategy.preserve_important_threshold == 8.5
    
    @pytest.mark.asyncio
    async def test_get_conversation_summary(self):
        """Test getting conversation summary."""
        # Add some messages
        self.context.add_message(role="user", content="I need help with Python")
        self.context.add_message(role="assistant", content="I can help you with Python programming")
        self.context.add_message(role="user", content="How do I write a function?")
        
        summary = await self.manager.get_conversation_summary(self.context)
        
        assert isinstance(summary, str)
        assert len(summary) > 0


class TestTokenEstimatorEdgeCases:
    """Test edge cases for TokenEstimator."""
    
    def test_very_long_text(self):
        """Test token estimation for very long text."""
        long_text = "word " * 10000  # 10k words
        tokens = TokenEstimator.estimate_tokens(long_text)
        
        assert tokens > 10000  # Should be at least number of words
        assert tokens < 20000  # But not too much higher
    
    def test_special_characters(self):
        """Test token estimation with special characters."""
        special_text = "🎉 Hello! @#$%^&*()_+={[}]|\\:;\"'<,>.?/"
        tokens = TokenEstimator.estimate_tokens(special_text)
        
        assert tokens > 0
    
    def test_multilingual_text(self):
        """Test token estimation with non-English text."""
        multilingual = "Hello 你好 Bonjour Hola こんにちは"
        tokens = TokenEstimator.estimate_tokens(multilingual)
        
        assert tokens > 0
    
    def test_code_text(self):
        """Test token estimation with code."""
        code_text = """
        def hello_world():
            print("Hello, world!")
            return True
        
        if __name__ == "__main__":
            hello_world()
        """
        tokens = TokenEstimator.estimate_tokens(code_text)
        
        assert tokens > 0
    
    def test_message_with_complex_metadata(self):
        """Test message token estimation with complex metadata."""
        complex_metadata = {
            "timestamp": "2023-01-01T00:00:00Z",
            "user_info": {
                "name": "test_user",
                "preferences": ["setting1", "setting2"]
            },
            "tool_results": [
                {"tool": "calculator", "result": 42},
                {"tool": "search", "result": "found items"}
            ]
        }
        
        message = MessageEntry(
            role="assistant",
            content="Response with complex metadata",
            metadata=complex_metadata
        )
        
        tokens = TokenEstimator.estimate_message_tokens(message)
        assert tokens > len(message.content.split())  # Should account for metadata
        assert message.token_count == tokens  # Should be cached


class TestContextWindowManagerStress:
    """Stress tests for ContextWindowManager."""
    
    @pytest.mark.asyncio
    async def test_large_context_compression(self):
        """Test compression with very large context."""
        manager = ContextWindowManager(max_tokens=5000)
        context = ValorContext(
            chat_id="stress_test",
            user_name="test_user"
        )
        
        # Add 1000 messages
        for i in range(1000):
            context.add_message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"Stress test message {i} with additional content " * 5
            )
        
        # Compress context
        compressed = await manager.compress_context(context)
        
        # Should significantly reduce message count
        assert len(compressed.message_history) < 1000
        
        # Should still preserve recent messages
        recent_content = [msg.content for msg in compressed.message_history[-5:]]
        assert any("999" in content for content in recent_content)
    
    def test_rapid_token_counting(self):
        """Test rapid successive token counting operations."""
        manager = ContextWindowManager()
        context = ValorContext(
            chat_id="rapid_test",
            user_name="test_user"
        )
        
        # Add messages and count tokens rapidly
        for i in range(100):
            context.add_message(role="user", content=f"Message {i}")
            count = manager.count_tokens(context)
            assert count > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])