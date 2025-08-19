"""Basic MCP server implementation for the chat application."""

import json
from typing import Any, Dict, List, Optional

from mcp import Tool, server
from mcp.server import Server
from mcp.types import TextContent, ToolCall, ToolResponse


class ChatMCPServer:
    """MCP server for chat-related tools and resources."""

    def __init__(self):
        self.server = Server("django-chat-mcp")
        self.setup_tools()
        self.setup_resources()

    def setup_tools(self):
        """Set up available tools for the MCP server."""

        @self.server.tool()
        async def get_user_info(user_id: int) -> dict[str, Any]:
            """
            Get information about a user.

            Args:
                user_id: The ID of the user

            Returns:
                User information dictionary
            """
            # This would normally query the Django User model
            from apps.common.models import User

            try:
                user = User.objects.get(id=user_id)
                return {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "is_active": user.is_active,
                }
            except User.DoesNotExist:
                return {"error": f"User with ID {user_id} not found"}

        @self.server.tool()
        async def search_knowledge_base(
            query: str, limit: int = 5
        ) -> list[dict[str, Any]]:
            """
            Search the knowledge base for relevant information.

            Args:
                query: The search query
                limit: Maximum number of results to return

            Returns:
                List of search results
            """
            # This is a placeholder - would integrate with a real knowledge base
            results = [
                {
                    "title": "Django Documentation",
                    "snippet": "Django is a high-level Python web framework...",
                    "url": "https://docs.djangoproject.com/",
                    "relevance": 0.95,
                }
            ]
            return results[:limit]

        @self.server.tool()
        async def get_chat_history(
            session_id: str, limit: int = 10
        ) -> list[dict[str, Any]]:
            """
            Get chat history for a session.

            Args:
                session_id: The session ID
                limit: Maximum number of messages to return

            Returns:
                List of chat messages
            """
            # This would query the ChatMessage model
            from apps.ai.models import ChatMessage

            messages = ChatMessage.objects.filter(session_id=session_id).order_by(
                "-created_at"
            )[:limit]

            return [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat(),
                }
                for msg in reversed(messages)
            ]

        @self.server.tool()
        async def save_feedback(
            session_id: str,
            message_id: int,
            feedback: str,
            rating: int | None = None,
        ) -> dict[str, Any]:
            """
            Save user feedback for a message.

            Args:
                session_id: The session ID
                message_id: The message ID
                feedback: The feedback text
                rating: Optional rating (1-5)

            Returns:
                Confirmation of saved feedback
            """
            # This would save to a Feedback model
            return {
                "status": "success",
                "message": "Feedback saved successfully",
                "feedback_id": 123,  # Would be the actual saved feedback ID
            }

    def setup_resources(self):
        """Set up available resources for the MCP server."""

        @self.server.resource("chat://sessions")
        async def list_sessions() -> list[dict[str, Any]]:
            """List all available chat sessions."""
            from apps.ai.models import ChatSession

            sessions = ChatSession.objects.all().order_by("-updated_at")[:20]
            return [
                {
                    "uri": f"chat://sessions/{session.id}",
                    "name": f"Session {session.id}",
                    "description": f"Chat session started at {session.created_at}",
                    "mimeType": "application/json",
                }
                for session in sessions
            ]

        @self.server.resource("chat://sessions/{session_id}")
        async def get_session(session_id: str) -> dict[str, Any]:
            """Get details of a specific chat session."""
            from apps.ai.models import ChatSession

            try:
                session = ChatSession.objects.get(id=session_id)
                return {
                    "id": session.id,
                    "user_id": session.user_id,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "message_count": session.messages.count(),
                }
            except ChatSession.DoesNotExist:
                return {"error": f"Session {session_id} not found"}

        @self.server.resource("chat://prompts")
        async def list_prompts() -> list[dict[str, Any]]:
            """List available prompt templates."""
            prompts = [
                {
                    "uri": "chat://prompts/greeting",
                    "name": "Greeting",
                    "description": "Welcome message for new users",
                    "template": "Hello! I'm your AI assistant. How can I help you today?",
                },
                {
                    "uri": "chat://prompts/help",
                    "name": "Help",
                    "description": "Help message explaining capabilities",
                    "template": "I can help you with various tasks including answering questions, providing information, and assisting with your Django application.",
                },
            ]
            return prompts

    async def handle_tool_call(self, tool_call: ToolCall) -> ToolResponse:
        """Handle a tool call from the client."""
        tool = self.server.get_tool(tool_call.name)
        if tool:
            result = await tool(**tool_call.arguments)
            return ToolResponse(
                content=[TextContent(text=json.dumps(result))], isError=False
            )
        else:
            return ToolResponse(
                content=[TextContent(text=f"Unknown tool: {tool_call.name}")],
                isError=True,
            )

    def run(self, host: str = "localhost", port: int = 3000):
        """Run the MCP server."""
        import asyncio

        async def serve():
            await self.server.run(host=host, port=port)

        asyncio.run(serve())


# Create a global server instance
mcp_server = ChatMCPServer()


def start_mcp_server(host: str = "localhost", port: int = 3000):
    """Start the MCP server."""
    mcp_server.run(host=host, port=port)
