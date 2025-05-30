#!/usr/bin/env python3
"""
Unified Integration Module

This module provides integration between the UnifiedValorClaudeAgent and existing
Telegram infrastructure, ensuring seamless transition from the delegation model
to the unified conversational development environment.
"""

import asyncio
import os
from typing import Any, Dict, Optional

from .unified_valor_claude_agent import UnifiedValorClaudeAgent, UnifiedContext


class UnifiedTelegramIntegration:
    """
    Integration layer between UnifiedValorClaudeAgent and Telegram infrastructure.
    
    This class handles the transition from the existing Valor agent system to the
    unified agent while maintaining compatibility with current Telegram features.
    """
    
    def __init__(self):
        """Initialize the unified integration."""
        self.unified_agent: Optional[UnifiedValorClaudeAgent] = None
        self._initialize_agent()
    
    def _initialize_agent(self):
        """Initialize the unified agent with proper configuration."""
        try:
            working_directory = os.getenv('WORKING_DIRECTORY', '/Users/valorengels/src/ai')
            self.unified_agent = UnifiedValorClaudeAgent(working_directory=working_directory)
        except Exception as e:
            print(f"Error initializing unified agent: {e}")
            self.unified_agent = None
    
    async def handle_telegram_message(
        self, 
        message: str, 
        chat_id: int, 
        username: str = "",
        is_group_chat: bool = False,
        chat_history: list = None,
        chat_history_obj: Any = None,
        notion_data: str = None
    ) -> str:
        """
        Handle Telegram messages through the unified agent system.
        
        This method replaces the existing Valor agent message handling and provides
        seamless integration with Claude Code capabilities.
        
        Args:
            message: The message content from Telegram.
            chat_id: Telegram chat ID.
            username: Username of the sender.
            is_group_chat: Whether this is a group chat.
            chat_history: Recent chat history.
            chat_history_obj: Chat history manager instance.
            notion_data: Optional Notion project data.
            
        Returns:
            str: Complete response from the unified system.
        """
        if not self.unified_agent:
            return "❌ Unified agent not available. Falling back to basic response."
        
        # Build context for unified agent
        context = {
            'username': username,
            'is_group_chat': is_group_chat,
            'chat_history': chat_history or [],
            'chat_history_obj': chat_history_obj,
            'notion_data': notion_data,
            'is_priority_question': self._is_priority_question(message, notion_data)
        }
        
        # Collect streaming response
        response_parts = []
        try:
            async for response_chunk in self.unified_agent.handle_telegram_message(message, chat_id, context):
                response_parts.append(response_chunk)
            
            # Join all response parts
            complete_response = '\n'.join(response_parts)
            
            # Handle special response formats (like image generation)
            if complete_response.startswith('TELEGRAM_IMAGE_GENERATED|'):
                return complete_response
            
            return complete_response
            
        except Exception as e:
            return f"❌ Error processing message through unified agent: {str(e)}"
    
    def _is_priority_question(self, message: str, notion_data: str = None) -> bool:
        """Determine if this is a priority/work-related question."""
        priority_keywords = [
            'priority', 'urgent', 'task', 'project', 'work', 'dev', 'development',
            'status', 'progress', 'deadline', 'milestone', 'ready', 'done'
        ]
        
        message_lower = message.lower()
        return any(keyword in message_lower for keyword in priority_keywords) or notion_data is not None
    
    async def handle_image_message(
        self,
        image_path: str,
        chat_id: int,
        caption: str = "",
        username: str = "",
        chat_history: list = None
    ) -> str:
        """
        Handle image messages through the unified agent.
        
        Args:
            image_path: Path to the downloaded image.
            chat_id: Telegram chat ID.
            caption: Optional caption from the user.
            username: Username of the sender.
            chat_history: Recent chat history for context.
            
        Returns:
            str: AI analysis of the image.
        """
        if not self.unified_agent:
            return "❌ Unified agent not available for image analysis."
        
        # Build image analysis message
        if caption:
            message = f"Please analyze this image. User caption: {caption}"
        else:
            message = "Please analyze this image and tell me what you see."
        
        # Add image path to context
        context = {
            'username': username,
            'chat_history': chat_history or [],
            'project_context': f"IMAGE_PATH: {image_path}"
        }
        
        # Process through unified agent
        response_parts = []
        try:
            async for response_chunk in self.unified_agent.handle_telegram_message(message, chat_id, context):
                response_parts.append(response_chunk)
            
            return '\n'.join(response_parts)
            
        except Exception as e:
            return f"❌ Error analyzing image: {str(e)}"
    
    def get_agent_status(self) -> Dict[str, Any]:
        """Get status information about the unified agent."""
        if not self.unified_agent:
            return {
                'status': 'unavailable',
                'error': 'Unified agent not initialized'
            }
        
        return {
            'status': 'available',
            'session_info': self.unified_agent.get_session_info(),
            'mcp_servers': self.unified_agent.mcp_servers
        }
    
    def terminate_session(self):
        """Terminate the current unified agent session."""
        if self.unified_agent:
            self.unified_agent.terminate_session()


# Global instance for use across the application
unified_integration = UnifiedTelegramIntegration()


async def process_message_unified(
    message: str,
    chat_id: int,
    username: str = "",
    is_group_chat: bool = False,
    chat_history: list = None,
    chat_history_obj: Any = None,
    notion_data: str = None
) -> str:
    """
    Global function to process messages through the unified system.
    
    This function provides a simple interface for existing code to transition
    to the unified agent system.
    
    Args:
        message: The message content.
        chat_id: Telegram chat ID.
        username: Username of the sender.
        is_group_chat: Whether this is a group chat.
        chat_history: Recent chat history.
        chat_history_obj: Chat history manager instance.
        notion_data: Optional Notion project data.
        
    Returns:
        str: Response from the unified system.
    """
    return await unified_integration.handle_telegram_message(
        message=message,
        chat_id=chat_id,
        username=username,
        is_group_chat=is_group_chat,
        chat_history=chat_history,
        chat_history_obj=chat_history_obj,
        notion_data=notion_data
    )


async def process_image_unified(
    image_path: str,
    chat_id: int,
    caption: str = "",
    username: str = "",
    chat_history: list = None
) -> str:
    """
    Global function to process images through the unified system.
    
    Args:
        image_path: Path to the downloaded image.
        chat_id: Telegram chat ID.
        caption: Optional caption from the user.
        username: Username of the sender.
        chat_history: Recent chat history for context.
        
    Returns:
        str: AI analysis of the image.
    """
    return await unified_integration.handle_image_message(
        image_path=image_path,
        chat_id=chat_id,
        caption=caption,
        username=username,
        chat_history=chat_history
    )


def get_unified_status() -> Dict[str, Any]:
    """Get status of the unified agent system."""
    return unified_integration.get_agent_status()


def terminate_unified_session():
    """Terminate the current unified agent session."""
    unified_integration.terminate_session()


# Test function for validating the integration
async def test_unified_integration():
    """Test the unified integration with sample messages."""
    print("Testing unified integration...")
    
    # Test basic message
    response = await process_message_unified(
        message="Hello, how are you?",
        chat_id=12345,
        username="test_user"
    )
    print(f"Basic message response: {response[:100]}...")
    
    # Test development message
    response = await process_message_unified(
        message="Check the git status of this project",
        chat_id=12345,
        username="test_user"
    )
    print(f"Development message response: {response[:100]}...")
    
    # Test priority question
    response = await process_message_unified(
        message="What are the highest priority tasks?",
        chat_id=12345,
        username="test_user",
        notion_data="Sample project data"
    )
    print(f"Priority question response: {response[:100]}...")
    
    # Get status
    status = get_unified_status()
    print(f"Agent status: {status}")
    
    # Cleanup
    terminate_unified_session()


if __name__ == "__main__":
    asyncio.run(test_unified_integration())