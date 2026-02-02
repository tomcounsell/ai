"""Django views for handling MCP protocol requests directly."""

import asyncio
import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class CreativeJuicesMCPServerView(View):
    """Handle MCP protocol requests for Creative Juices server.

    This view implements the MCP JSON-RPC 2.0 protocol, allowing Claude Desktop
    and other MCP clients to call tools directly via HTTP.
    """

    def get(self, request):
        """Handle GET requests - return server info."""
        return JsonResponse(
            {
                "name": "creative-juices",
                "version": "1.0.0",
                "description": "MCP server for creative thinking tools",
                "protocol": "MCP",
                "authentication": False,
                "endpoint": request.build_absolute_uri(),
            }
        )

    def post(self, request):
        """Handle MCP JSON-RPC requests."""
        try:
            # Parse MCP request
            mcp_request = json.loads(request.body)
            method = mcp_request.get("method")
            params = mcp_request.get("params", {})
            request_id = mcp_request.get("id")

            logger.info(f"MCP request: method={method}, id={request_id}")

            # Route to appropriate handler
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list()
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                result = asyncio.run(self._handle_tool_call(tool_name, arguments))
            elif method.startswith("notifications/"):
                # Handle notifications (no response required per MCP spec)
                logger.info(f"Received notification: {method}")
                return JsonResponse({"jsonrpc": "2.0"}, status=200)
            else:
                return JsonResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    },
                    status=400,
                )

            # Return successful response
            return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in request: {e}")
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
                status=400,
            )
        except Exception as e:
            logger.error(f"Error handling MCP request: {e}", exc_info=True)
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
                },
                status=500,
            )

    def _handle_initialize(self, params):
        """Handle MCP initialize request."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "creative-juices",
                "version": "1.0.0",
            },
        }

    def _handle_tools_list(self):
        """Handle tools/list request - return available tools."""
        return {
            "tools": [
                {
                    "name": "get_inspiration",
                    "description": (
                        "Use at the start of creative or problem-solving tasks to frame challenges "
                        "in unexpected ways. Helpful when you need to think outside the box from "
                        "the beginning and want unconventional starting points."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
                {
                    "name": "think_outside_the_box",
                    "description": (
                        "Use mid-conversation when exploration has stalled or thinking has become "
                        "too linear. Helpful when you need to break out of convergent patterns and "
                        "force radical divergence from your current approach."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
                {
                    "name": "reality_check",
                    "description": (
                        "Use to ground creative thinking in reality while maintaining openness. "
                        "Helpful when wild ideas need pressure-testing against constraints, or when "
                        "you need to validate assumptions and identify what actually matters."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            ]
        }

    async def _handle_tool_call(self, tool_name, arguments):
        """Handle tools/call request - execute the requested tool."""
        from apps.ai.mcp.creative_juices_server import (
            get_inspiration,
            reality_check,
            think_outside_the_box,
        )

        # Route to appropriate tool
        if tool_name == "get_inspiration":
            result = await get_inspiration()
        elif tool_name == "think_outside_the_box":
            result = await think_outside_the_box()
        elif tool_name == "reality_check":
            result = await reality_check()
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

        # Wrap result in MCP response format
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2),
                }
            ]
        }


@method_decorator(csrf_exempt, name="dispatch")
class CTOToolsMCPServerView(View):
    """Handle MCP protocol requests for CTO Tools server.

    This view implements the MCP JSON-RPC 2.0 protocol for CTO Tools.
    Requires OAuth Bearer token authentication.
    """

    def get(self, request):
        """Handle GET requests - return server info."""
        return JsonResponse(
            {
                "name": "cto-tools",
                "version": "1.1.0",
                "description": "MCP server for CTO and engineering leadership tools",
                "protocol": "MCP",
                "authentication": True,
                "endpoint": request.build_absolute_uri(),
            }
        )

    def post(self, request):
        """Handle MCP JSON-RPC requests."""
        # Validate Bearer token (auto-approve OAuth - just check presence)
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": "Authentication required - missing or invalid Bearer token",
                    },
                },
                status=401,
            )

        try:
            # Parse MCP request
            mcp_request = json.loads(request.body)
            method = mcp_request.get("method")
            params = mcp_request.get("params", {})
            request_id = mcp_request.get("id")

            logger.info(f"MCP request: method={method}, id={request_id}")

            # Route to appropriate handler
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list()
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                result = asyncio.run(self._handle_tool_call(tool_name, arguments))
            elif method.startswith("notifications/"):
                # Handle notifications (no response required per MCP spec)
                logger.info(f"Received notification: {method}")
                return JsonResponse({"jsonrpc": "2.0"}, status=200)
            else:
                return JsonResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    },
                    status=400,
                )

            # Return successful response
            return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in request: {e}")
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
                status=400,
            )
        except Exception as e:
            logger.error(f"Error handling MCP request: {e}", exc_info=True)
            return JsonResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
                },
                status=500,
            )

    def _handle_initialize(self, params):
        """Handle MCP initialize request."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": "cto-tools",
                "version": "1.0.0",
            },
        }

    def _handle_tools_list(self):
        """Handle tools/list request - return available tools."""
        return {
            "tools": [
                {
                    "name": "weekly_review",
                    "description": (
                        "Provides a structured framework for conducting weekly engineering team reviews. "
                        "Returns step-by-step instructions that guide you through gathering commit data, "
                        "analyzing work, and creating a concise summary suitable for any communication channel."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            ]
        }

    async def _handle_tool_call(self, tool_name, arguments):
        """Handle tools/call request - execute the requested tool."""
        from apps.ai.mcp.cto_tools.server import weekly_review

        # Route to appropriate tool
        if tool_name == "weekly_review":
            # This is a sync function, not async
            result = weekly_review()
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

        # Wrap result in MCP response format
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2),
                }
            ]
        }
