#!/usr/bin/env python3
"""
MCP Server for Valor's local Python tools.

This exposes local tools (sms_reader, telegram_history, etc.) as MCP tools
so Claude Code / Agent SDK can use them natively.

Run with: python tools/mcp_server.py
Or register with: claude mcp add valor-tools -- python /path/to/tools/mcp_server.py
"""

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Import local tools
from tools.sms_reader import (
    get_2fa,
    get_latest_2fa_code,
    get_recent_messages,
    search_messages,
    list_senders,
)

app = Server("valor-tools")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="sms_get_2fa",
            description="Get the most recent 2FA verification code from SMS/iMessage. Returns just the code string or null if not found.",
            inputSchema={
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "Look back this many minutes (default 5)",
                        "default": 5,
                    },
                    "sender": {
                        "type": "string",
                        "description": "Filter by sender phone number (partial match)",
                    },
                },
            },
        ),
        Tool(
            name="sms_get_2fa_detailed",
            description="Get detailed info about the most recent 2FA code including the full message, sender, and timestamp.",
            inputSchema={
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "Look back this many minutes (default 10)",
                        "default": 10,
                    },
                    "sender": {
                        "type": "string",
                        "description": "Filter by sender phone number (partial match)",
                    },
                },
            },
        ),
        Tool(
            name="sms_get_recent",
            description="Get recent SMS/iMessage messages. Returns list of messages with sender, text, date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum messages to return (default 20)",
                        "default": 20,
                    },
                    "sender": {
                        "type": "string",
                        "description": "Filter by sender (partial match)",
                    },
                    "since_minutes": {
                        "type": "integer",
                        "description": "Only messages from last N minutes",
                    },
                },
            },
        ),
        Tool(
            name="sms_search",
            description="Search SMS/iMessage messages by text content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum messages to return (default 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="sms_list_senders",
            description="List unique message senders with message counts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum senders to return (default 50)",
                        "default": 50,
                    },
                    "since_days": {
                        "type": "integer",
                        "description": "Only senders from last N days (default 30)",
                        "default": 30,
                    },
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "sms_get_2fa":
            code = get_2fa(
                minutes=arguments.get("minutes", 5),
                sender=arguments.get("sender"),
            )
            return [TextContent(type="text", text=code if code else "No 2FA code found")]

        elif name == "sms_get_2fa_detailed":
            result = get_latest_2fa_code(
                minutes=arguments.get("minutes", 10),
                sender=arguments.get("sender"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2) if result else "No 2FA code found")]

        elif name == "sms_get_recent":
            messages = get_recent_messages(
                limit=arguments.get("limit", 20),
                sender=arguments.get("sender"),
                since_minutes=arguments.get("since_minutes"),
            )
            return [TextContent(type="text", text=json.dumps(messages, indent=2))]

        elif name == "sms_search":
            messages = search_messages(
                query=arguments["query"],
                limit=arguments.get("limit", 20),
            )
            return [TextContent(type="text", text=json.dumps(messages, indent=2))]

        elif name == "sms_list_senders":
            senders = list_senders(
                limit=arguments.get("limit", 50),
                since_days=arguments.get("since_days", 30),
            )
            return [TextContent(type="text", text=json.dumps(senders, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
