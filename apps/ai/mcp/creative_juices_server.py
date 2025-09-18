"""
Creative Juices MCP Server implementation.
"""

import asyncio
import json
import logging
import random
from typing import Any, Dict, List

from mcp import Tool
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .creative_juices_tools import CREATIVE_JUICES_TOOLS, CREATIVE_PROMPT

logger = logging.getLogger(__name__)


class CreativeJuicesMCPServer:
    """MCP Server for Creative Juices - random verb-noun combinations for creative thinking."""

    def __init__(self):
        """Initialize the MCP server."""
        self.server = Server("creative-juices-mcp")
        self._register_handlers()

    def _register_handlers(self):
        """Register MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available tools."""
            return CREATIVE_JUICES_TOOLS

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
            """Execute a tool."""

            handlers = {
                "get_creative_spark": self._handle_get_creative_spark,
            }

            handler = handlers.get(name)
            if not handler:
                raise ValueError(f"Unknown tool: {name}")

            try:
                return await handler(arguments)
            except Exception as e:
                logger.error(f"Tool {name} failed: {e}")
                return {"error": str(e)}

        @self.server.list_prompts()
        async def list_prompts() -> List[Dict[str, str]]:
            """List available prompts."""
            return [
                {
                    "name": "creative_reframe",
                    "description": "Prompt for applying creative thinking techniques",
                }
            ]

        @self.server.get_prompt()
        async def get_prompt(name: str, arguments: Dict[str, Any] = None) -> Dict[str, Any]:
            """Get a specific prompt."""
            if name == "creative_reframe":
                return {
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": CREATIVE_PROMPT,
                            }
                        }
                    ]
                }
            raise ValueError(f"Unknown prompt: {name}")

    async def _handle_get_creative_spark(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle get_creative_spark tool execution."""
        from .creative_juices_words import VERBS, NOUNS

        # Extract parameters with defaults
        count = args.get("count", 2)
        intensity = args.get("intensity", "wild")

        # Validate parameters
        if not 1 <= count <= 5:
            count = max(1, min(5, count))

        if intensity not in ["mild", "wild", "chaos"]:
            intensity = "wild"

        # Select words based on intensity
        verb_list = VERBS[intensity]
        noun_list = NOUNS[intensity]

        # Generate pairs
        pairs = []
        for _ in range(count):
            verb = random.choice(verb_list)
            noun = random.choice(noun_list)
            pairs.append(f"{verb}-{noun}")

        # Generate instruction based on intensity
        instructions = {
            "mild": "Consider how these concepts might relate to your problem:",
            "wild": "Use these unexpected combinations as lenses to radically reframe:",
            "chaos": "Let these surreal pairings shatter your assumptions:"
        }

        # Generate prompt suggestion
        first_pair = pairs[0].replace('-', ' the ')
        prompt = f"What if your solution could {first_pair}?"

        return {
            "pairs": pairs,
            "instruction": instructions[intensity],
            "prompt": prompt
        }

    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream)


def main():
    """Main entry point for the MCP server."""
    import os
    import sys

    # Add project root to path
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    sys.path.insert(0, project_root)

    # Setup Django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
    import django
    django.setup()

    server = CreativeJuicesMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()