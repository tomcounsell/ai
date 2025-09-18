# MCP Server Development Guide

## Overview

This guide provides a standardized approach for building Model Context Protocol (MCP) servers in our Django application. MCP servers enable AI assistants like Claude to interact with your application's data and functionality through a well-defined protocol.

## Quick Start: MCP Server Skeleton

### 1. Basic MCP Server Template

Create your MCP server in `apps/ai/mcp/{service_name}_server.py`:

```python
"""
{Service Name} MCP Server implementation.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import Resource, Tool
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .{service_name}_tools import {SERVICE_NAME}_TOOLS

logger = logging.getLogger(__name__)


class {ServiceName}MCPServer:
    """MCP Server for {Service Name} integration."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the MCP server.

        Args:
            api_key: API key for authentication (if required)
        """
        self.api_key = api_key
        self.server = Server("{service-name}-mcp")

        # Initialize your service client here
        self.client = self._init_client()

        # Register MCP protocol handlers
        self._register_handlers()

    def _init_client(self):
        """Initialize the service client."""
        # Initialize your service-specific client
        # Example: return ServiceClient(self.api_key)
        pass

    def _register_handlers(self):
        """Register MCP protocol handlers."""

        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available tools."""
            return {SERVICE_NAME}_TOOLS

        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
            """Execute a tool."""

            # Route tool calls to appropriate handlers
            handlers = {
                "{service}_tool1": self._handle_tool1,
                "{service}_tool2": self._handle_tool2,
                # Add more tool handlers here
            }

            handler = handlers.get(name)
            if not handler:
                raise ValueError(f"Unknown tool: {name}")

            try:
                return await handler(arguments)
            except Exception as e:
                logger.error(f"Tool {name} failed: {e}")
                return {"error": str(e)}

        @self.server.list_resources()
        async def list_resources() -> List[Resource]:
            """List available resources."""
            return [
                Resource(
                    uri="{service}://items",
                    name="{Service} Items",
                    description="Access to {service} item data",
                    mimeType="application/json",
                ),
                # Add more resources here
            ]

        @self.server.read_resource()
        async def read_resource(uri: str) -> str:
            """Read a resource."""

            resource_handlers = {
                "{service}://items": self._read_items,
                # Add more resource handlers here
            }

            handler = resource_handlers.get(uri)
            if not handler:
                raise ValueError(f"Unknown resource: {uri}")

            data = await handler()
            return json.dumps(data, default=str)

    # Tool Handlers
    async def _handle_tool1(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool1 execution."""
        # Implement your tool logic here
        # Example:
        # result = await self.client.do_something(args)
        # return {"status": "success", "data": result}
        pass

    async def _handle_tool2(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool2 execution."""
        pass

    # Resource Handlers
    async def _read_items(self) -> List[Dict[str, Any]]:
        """Read items resource."""
        # Example:
        # return await self.client.list_items()
        pass

    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream)


def main():
    """Main entry point for the MCP server."""
    import django
    django.setup()

    # Parse any command-line arguments if needed
    # api_key = os.environ.get("API_KEY")

    server = {ServiceName}MCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
```

### 2. Tool Definitions Template

Create tool definitions in `apps/ai/mcp/{service_name}_tools.py`:

```python
"""
MCP tools for {Service Name} integration.
"""

from typing import Any, Dict, List, Optional

from mcp import Tool
from pydantic import BaseModel, Field


# Define parameter schemas using Pydantic
class Tool1Params(BaseModel):
    """Parameters for tool1."""

    param1: str = Field(description="Description of param1")
    param2: Optional[int] = Field(default=10, description="Optional param2")


class Tool2Params(BaseModel):
    """Parameters for tool2."""

    required_field: str = Field(description="A required field")
    optional_field: Optional[str] = Field(default=None, description="An optional field")


# MCP Tool definitions
{SERVICE_NAME}_TOOLS = [
    Tool(
        name="{service}_tool1",
        description="Clear description of what tool1 does",
        inputSchema=Tool1Params.model_json_schema(),
    ),
    Tool(
        name="{service}_tool2",
        description="Clear description of what tool2 does",
        inputSchema=Tool2Params.model_json_schema(),
    ),
]
```

### 3. Client Implementation Template

Create your service client in `apps/integration/{service}/client.py`:

```python
"""
{Service Name} API client.
"""

import aiohttp
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from apps.integration.models import {ServiceName}Connection

logger = logging.getLogger(__name__)


class {ServiceName}Client:
    """Async client for {Service Name} API."""

    def __init__(self, connection: {ServiceName}Connection):
        """
        Initialize the client.

        Args:
            connection: Database connection with credentials
        """
        self.connection = connection
        self.base_url = "https://api.{service}.com/v1"
        self.session = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.connection.access_token}",
            "Content-Type": "application/json",
        }

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request body
            params: Query parameters

        Returns:
            API response data
        """
        if not self.session:
            self.session = aiohttp.ClientSession()

        # Check and refresh token if needed
        if await self._token_needs_refresh():
            await self.refresh_token()

        url = f"{self.base_url}/{endpoint}"
        headers = self._get_headers()

        async with self.session.request(
            method,
            url,
            json=data,
            params=params,
            headers=headers,
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def _token_needs_refresh(self) -> bool:
        """Check if the access token needs refresh."""
        if not self.connection.token_expires_at:
            return False
        return datetime.now() >= self.connection.token_expires_at - timedelta(minutes=5)

    async def refresh_token(self):
        """Refresh the access token."""
        # Implement token refresh logic
        pass

    # API Methods
    async def list_items(self, **kwargs) -> List[Dict[str, Any]]:
        """List items from the service."""
        return await self._make_request("GET", "items", params=kwargs)

    async def get_item(self, item_id: str) -> Dict[str, Any]:
        """Get a specific item."""
        return await self._make_request("GET", f"items/{item_id}")

    async def create_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new item."""
        return await self._make_request("POST", "items", data=data)
```

## Directory Structure

```
apps/
├── ai/
│   └── mcp/
│       ├── __init__.py
│       ├── {service}_server.py      # MCP server implementation
│       └── {service}_tools.py       # Tool definitions
│
├── integration/
│   └── {service}/
│       ├── __init__.py
│       ├── client.py                # API client
│       ├── models.py                # Django models
│       └── tests/                   # Integration tests
│
└── common/
    └── models/
        └── api_key.py               # API key models (if needed)
```

## Development Workflow

### Step 1: Plan Your Integration

1. **Identify the service** you're integrating with
2. **Define the tools** the MCP server will expose
3. **Design the resources** (if applicable)
4. **Plan authentication** (OAuth, API keys, etc.)

### Step 2: Create Models

If your service needs persistent storage:

```python
# apps/integration/models/{service}.py

from django.db import models
from apps.common.behaviors import Timestampable

class {ServiceName}Connection(Timestampable, models.Model):
    """Connection to {Service Name} API."""

    user = models.ForeignKey(
        'common.User',
        on_delete=models.CASCADE,
        related_name='{service}_connections',
    )

    # Authentication
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)

    # Service-specific fields
    account_id = models.CharField(max_length=255)
    account_name = models.CharField(max_length=255)

    # Status
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ['user', 'account_id']
```

### Step 3: Implement the Client

1. Create the async API client
2. Handle authentication and token refresh
3. Implement API methods needed by your tools

### Step 4: Define MCP Tools

1. Create Pydantic models for parameters
2. Define tools with clear descriptions
3. Use appropriate input schemas

### Step 5: Build the MCP Server

1. Initialize with proper authentication
2. Register protocol handlers
3. Implement tool handlers
4. Add resource handlers if needed

### Step 6: Write Tests

```python
# apps/ai/tests/test_mcp_{service}.py

import pytest
from unittest.mock import AsyncMock, patch

from apps.ai.mcp.{service}_server import {ServiceName}MCPServer


@pytest.mark.asyncio
async def test_list_tools():
    """Test that tools are properly listed."""
    server = {ServiceName}MCPServer()
    tools = await server.server.list_tools()
    assert len(tools) > 0
    assert all(tool.name for tool in tools)


@pytest.mark.asyncio
async def test_tool_execution():
    """Test tool execution."""
    server = {ServiceName}MCPServer()

    with patch.object(server.client, 'list_items', new=AsyncMock(return_value=[])):
        result = await server._handle_tool1({})
        assert result["status"] == "success"
```

## Common Patterns

### 1. Rate Limiting

**🚧 TODO**: Pre-built rate limiting utilities are under development. See `docs/TODO.md` for status.

Once available, use the standard implementation:
```python
from apps.common.utilities.rate_limiting import RateLimiter

# In your MCP server
self.rate_limiter = RateLimiter(
    max_calls=100,
    window_seconds=3600,
    identifier="user_id"
)

async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
    await self.rate_limiter.check(self.user.id)
    # ... rest of tool execution
```

Until then, implement basic rate limiting as needed for your use case.

### 2. Error Handling

```python
async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    try:
        result = await handler(arguments)
        return {"success": True, "data": result}
    except ValidationError as e:
        return {"success": False, "error": str(e), "type": "validation"}
    except AuthenticationError as e:
        return {"success": False, "error": "Authentication failed", "type": "auth"}
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return {"success": False, "error": "Internal error", "type": "internal"}
```

### 3. Caching

```python
from django.core.cache import cache

async def _get_cached_data(self, key: str, fetch_func, ttl: int = 300):
    """Get data from cache or fetch."""
    data = cache.get(key)
    if data is None:
        data = await fetch_func()
        cache.set(key, data, ttl)
    return data
```

## Authentication Patterns

### API Key Authentication

```python
def __init__(self, api_key: str):
    self.api_key = api_key
    self.validate_api_key()

def validate_api_key(self):
    from apps.common.models import UserAPIKey
    try:
        key = UserAPIKey.objects.get_from_key(self.api_key)
        self.user = key.user
    except UserAPIKey.DoesNotExist:
        raise ValueError("Invalid API key")
```

### OAuth 2.0 Authentication

```python
async def refresh_token(self):
    """Refresh OAuth token."""
    async with self.session.post(
        "https://oauth.{service}.com/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": self.connection.refresh_token,
            "client_id": settings.{SERVICE}_CLIENT_ID,
            "client_secret": settings.{SERVICE}_CLIENT_SECRET,
        }
    ) as response:
        data = await response.json()
        self.connection.access_token = data["access_token"]
        self.connection.token_expires_at = datetime.now() + timedelta(
            seconds=data["expires_in"]
        )
        await sync_to_async(self.connection.save)()
```

## Running Your MCP Server Locally

For comprehensive local development and testing instructions, see the [MCP Local Testing Guide](guides/MCP_LOCAL_TESTING.md).

**Quick Start:**

```bash
# Basic execution
uv run python -m apps.ai.mcp.{service}_server

# With environment variables
API_KEY=abc uv run python -m apps.ai.mcp.{service}_server

# Testing with MCP Inspector (recommended)
mcp-inspector uv run python -m apps.ai.mcp.{service}_server
```

The local testing guide covers:
- Multiple testing methods (Inspector, Claude Desktop, stdio, Python client)
- Development tools (auto-reload, mocking)
- Debugging techniques
- Integration and performance testing
- Common issues and solutions

## Best Practices

### 1. Tool Design

- **Single Responsibility**: Each tool should do one thing well
- **Clear Descriptions**: Help AI assistants understand what tools do
- **Sensible Defaults**: Provide defaults for optional parameters
- **Validation**: Use Pydantic for robust parameter validation

### 2. Error Messages

- **Be Specific**: "Customer not found with ID: 123" vs "Not found"
- **Be Actionable**: Include what the user can do to fix it
- **Be Consistent**: Use similar error formats across tools

### 3. Performance

- **Async Everything**: Use async/await for all I/O operations
- **Batch Operations**: Support batch operations where sensible
- **Implement Caching**: Cache frequently accessed data
- **Connection Pooling**: Reuse HTTP connections

### 4. Security

- **Validate Inputs**: Never trust client input - use Pydantic models
- **Sanitize Outputs**: Remove sensitive data from responses
- **Audit Logging**: Use standard audit logger (see below)
- **Rate Limiting**: Use standard rate limiter (see Common Patterns)

#### Audit Logging

**🚧 TODO**: Pre-built audit logging is under development. See `docs/TODO.md` for status.

Once available, use:
```python
from apps.common.utilities.audit import AuditLogger

audit = AuditLogger("mcp.{service}")

@self.server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    await audit.log_tool_call(
        user_id=self.user.id,
        tool_name=name,
        arguments=arguments,
        timestamp=datetime.now()
    )
    # ... rest of implementation
```

Until then, use Django's logging framework for critical operations.

### 5. Testing

```python
# Test checklist for each MCP server:
- [ ] Tools are listed correctly
- [ ] Each tool executes successfully
- [ ] Error cases return appropriate errors
- [ ] Authentication is properly validated
- [ ] Rate limits are enforced
- [ ] Resources are accessible
- [ ] Token refresh works
```

## Debugging Tips

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# In your server
logger.debug(f"Tool called: {name} with args: {arguments}")
```

### Test Individual Components

```python
# Test your client directly
async def test_client():
    from apps.integration.{service}.client import {ServiceName}Client
    connection = {ServiceName}Connection.objects.first()

    async with {ServiceName}Client(connection) as client:
        items = await client.list_items()
        print(f"Found {len(items)} items")
```

### Use MCP Inspector

```bash
# Install MCP inspector (if available)
npm install -g @modelcontextprotocol/inspector

# Run your server with inspector
mcp-inspector -- uv run python -m apps.ai.mcp.{service}_server
```

## Migration Guide: Adding MCP to Existing Service

If you already have a service integration and want to add MCP:

1. **Keep existing client**: Your MCP server should use the existing client
2. **Map to tools**: Convert client methods to MCP tools
3. **Add async support**: Ensure client supports async operations
4. **Maintain backwards compatibility**: Don't break existing code

```python
# Wrapper for sync client
from asgiref.sync import sync_to_async

class MCPServerWrapper:
    def __init__(self):
        self.sync_client = ExistingSyncClient()

    async def _handle_tool(self, args):
        # Wrap sync methods
        result = await sync_to_async(self.sync_client.method)(args)
        return result
```

## Example: Weather Service MCP

Here's a complete minimal example:

```python
# apps/ai/mcp/weather_server.py
from mcp import Tool, Server
from mcp.server.stdio import stdio_server
import asyncio
import aiohttp

class WeatherMCPServer:
    def __init__(self):
        self.server = Server("weather-mcp")
        self._register_handlers()

    def _register_handlers(self):
        @self.server.list_tools()
        async def list_tools():
            return [
                Tool(
                    name="get_weather",
                    description="Get current weather for a city",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"},
                        },
                        "required": ["city"],
                    },
                )
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict):
            if name == "get_weather":
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://api.weather.example.com/current",
                        params={"q": arguments["city"]},
                    ) as response:
                        data = await response.json()
                        return {
                            "temperature": data["temp"],
                            "conditions": data["conditions"],
                        }
            raise ValueError(f"Unknown tool: {name}")

    async def run(self):
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream)

if __name__ == "__main__":
    asyncio.run(WeatherMCPServer().run())
```

## Next Steps

1. Choose your integration service
2. Copy the templates above
3. Implement your specific logic
4. Test thoroughly
5. Document your MCP server

## Resources

- [MCP Specification](https://modelcontextprotocol.io/docs)
- [Django Async Views](https://docs.djangoproject.com/en/5.0/topics/async/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [aiohttp Documentation](https://docs.aiohttp.org/)