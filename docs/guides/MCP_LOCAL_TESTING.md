# MCP Local Development & Testing Guide

This guide covers everything you need to know about running and testing MCP servers locally during development.

## Quick Start

```bash
# Basic execution
uv run python -m apps.ai.mcp.{service}_server

# With environment variables
API_KEY=abc uv run python -m apps.ai.mcp.{service}_server

# With Django settings
DJANGO_SETTINGS_MODULE=settings uv run python -m apps.ai.mcp.{service}_server
```

## Testing Methods

### 1. MCP Inspector (Recommended)

The MCP Inspector provides an interactive web interface for testing your server:

```bash
# Install the MCP Inspector
npm install -g @modelcontextprotocol/inspector

# Run your server with the inspector
mcp-inspector uv run python -m apps.ai.mcp.{service}_server
```

The inspector allows you to:
- View available tools and resources
- Execute tools with test parameters
- Inspect request/response payloads
- Debug protocol issues

### 2. Claude Desktop Integration

Test your MCP server with Claude Desktop locally:

#### Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "{service}-local": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/your/project",
        "python",
        "-m",
        "apps.ai.mcp.{service}_server"
      ],
      "env": {
        "DJANGO_SETTINGS_MODULE": "settings",
        "API_KEY": "your-test-key"
      }
    }
  }
}
```

#### Steps
1. Save the configuration
2. Restart Claude Desktop to load the new server
3. Test your tools by asking Claude to use them

### 3. Manual stdio Testing

MCP servers communicate via stdio, so you can test manually:

```bash
# Start your server
uv run python -m apps.ai.mcp.{service}_server

# In another terminal, send JSON-RPC messages
echo '{"jsonrpc":"2.0","method":"tools/list","id":1}' | uv run python -m apps.ai.mcp.{service}_server

# Test a specific tool
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_weather","arguments":{"city":"London"}},"id":2}' | uv run python -m apps.ai.mcp.{service}_server
```

### 4. Python Test Client

Create a test client to interact with your server programmatically:

```python
# test_mcp_client.py
import asyncio
import json
from subprocess import Popen, PIPE

async def test_mcp_server():
    # Start the MCP server as a subprocess
    process = Popen(
        ["uv", "run", "python", "-m", "apps.ai.mcp.{service}_server"],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True
    )

    # Send initialization
    request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "0.1.0",
            "capabilities": {}
        },
        "id": 1
    }

    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()

    # Read response
    response = process.stdout.readline()
    print("Initialize response:", response)

    # List tools
    request = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 2
    }

    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()

    response = process.stdout.readline()
    print("Tools:", response)

    # Test a tool
    request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "your_tool_name",
            "arguments": {"param1": "value1"}
        },
        "id": 3
    }

    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()

    response = process.stdout.readline()
    print("Tool response:", response)

    process.terminate()

if __name__ == "__main__":
    asyncio.run(test_mcp_server())
```

## Development Tools

### Auto-Reload Server

For development with automatic reloading on code changes:

```python
# dev_server.py
import subprocess
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class MCPServerReloader(FileSystemEventHandler):
    def __init__(self):
        self.process = None
        self.start_server()

    def start_server(self):
        if self.process:
            self.process.terminate()
            self.process.wait()

        print("Starting MCP server...")
        self.process = subprocess.Popen(
            ["uv", "run", "python", "-m", "apps.ai.mcp.{service}_server"],
            stdout=sys.stdout,
            stderr=sys.stderr
        )

    def on_modified(self, event):
        if event.src_path.endswith('.py'):
            print(f"Detected change in {event.src_path}, restarting...")
            self.start_server()

if __name__ == "__main__":
    handler = MCPServerReloader()
    observer = Observer()
    observer.schedule(handler, path='apps/ai/mcp', recursive=True)
    observer.schedule(handler, path='apps/integration/{service}', recursive=True)
    observer.start()

    try:
        handler.process.wait()
    except KeyboardInterrupt:
        observer.stop()
        handler.process.terminate()
    observer.join()
```

### Mock External Services

When testing locally, mock external services to avoid API calls:

```python
# apps/ai/mcp/{service}_server_mock.py
class Mock{ServiceName}Client:
    """Mock client for local testing without real API calls."""

    async def list_items(self):
        return [
            {"id": "1", "name": "Test Item 1"},
            {"id": "2", "name": "Test Item 2"},
        ]

    async def get_item(self, item_id: str):
        return {"id": item_id, "name": f"Test Item {item_id}"}

class {ServiceName}MCPServer:
    def _init_client(self):
        # Use mock for local testing
        if os.environ.get("USE_MOCK", "false").lower() == "true":
            return Mock{ServiceName}Client()
        return {ServiceName}Client()
```

Run with mock:
```bash
USE_MOCK=true uv run python -m apps.ai.mcp.{service}_server
```

## Debugging Techniques

### Enable Verbose Logging

```python
# At the top of your server file
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### Add Debug Output

```python
@self.server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    logger.debug(f"Tool called: {name}")
    logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")

    result = await handler(arguments)

    logger.debug(f"Result: {json.dumps(result, indent=2)}")
    return result
```

### HTTP Transport Testing (If Implemented)

```bash
# If you implement HTTP transport
curl -X POST http://localhost:3000/tools/list \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

## Integration Testing

### Pytest Integration Tests

Write comprehensive integration tests for your MCP server:

```python
# apps/ai/tests/test_mcp_{service}_integration.py
import pytest
import asyncio
import json
import os
from subprocess import Popen, PIPE, STDOUT

class TestMCPServerIntegration:
    @pytest.fixture
    async def mcp_server(self):
        """Start MCP server for testing."""
        process = Popen(
            ["uv", "run", "python", "-m", "apps.ai.mcp.{service}_server"],
            stdin=PIPE,
            stdout=PIPE,
            stderr=STDOUT,
            text=True,
            env={**os.environ, "USE_MOCK": "true"}
        )

        # Initialize
        await self._send_request(process, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "0.1.0"},
            "id": 1
        })

        yield process
        process.terminate()

    async def _send_request(self, process, request):
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        response = process.stdout.readline()
        return json.loads(response)

    @pytest.mark.asyncio
    async def test_list_tools(self, mcp_server):
        response = await self._send_request(mcp_server, {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        })

        assert "result" in response
        assert len(response["result"]["tools"]) > 0

    @pytest.mark.asyncio
    async def test_call_tool(self, mcp_server):
        response = await self._send_request(mcp_server, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_item",
                "arguments": {"item_id": "123"}
            },
            "id": 3
        })

        assert "result" in response
        assert response["result"]["content"][0]["text"]
```

### Running Integration Tests

```bash
# Run all MCP integration tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_*_integration.py -v

# Run specific test
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_{service}_integration.py::test_list_tools -v

# Run with coverage
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_*_integration.py --cov=apps.ai.mcp
```

## Performance Testing

Test your server's performance under load:

```python
# perf_test.py
import asyncio
import time
import statistics
import json
from subprocess import Popen, PIPE

async def call_tool(process, tool_name, args):
    start = time.time()

    request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": args
        },
        "id": 1
    }

    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    response = process.stdout.readline()

    return time.time() - start

async def performance_test():
    # Start server
    process = Popen(
        ["uv", "run", "python", "-m", "apps.ai.mcp.{service}_server"],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        env={"USE_MOCK": "true"}
    )

    # Initialize
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "0.1.0"},
        "id": 0
    }
    process.stdin.write(json.dumps(init_request) + "\n")
    process.stdin.flush()
    process.stdout.readline()  # Read init response

    # Run multiple requests
    times = []
    for i in range(100):
        elapsed = await call_tool(process, "get_item", {"id": str(i)})
        times.append(elapsed)

    process.terminate()

    # Print statistics
    print(f"Average response time: {statistics.mean(times):.3f}s")
    print(f"Median response time: {statistics.median(times):.3f}s")

    if len(times) >= 20:
        print(f"95th percentile: {statistics.quantiles(times, n=20)[18]:.3f}s")

if __name__ == "__main__":
    asyncio.run(performance_test())
```

## Common Issues and Solutions

### Issue: Server doesn't start

**Solutions:**
- Check Django is properly configured: `DJANGO_SETTINGS_MODULE=settings`
- Verify database is running: `psql -l`
- Ensure virtual environment is activated: `source .venv/bin/activate`

### Issue: Tools not appearing in Claude Desktop

**Solutions:**
- Verify config file path is correct
- Check JSON syntax in config file
- Restart Claude Desktop completely
- Check server logs for errors

### Issue: Mock data not being used

**Solutions:**
- Ensure `USE_MOCK=true` is set correctly
- Check the mock condition in `_init_client()`
- Verify mock client methods match real client interface

### Issue: Tests hanging

**Solutions:**
- Add timeout to subprocess operations
- Ensure proper cleanup in test fixtures
- Check for deadlocks in async code

## Best Practices

1. **Always use mocks for unit tests** - Real API calls should only happen in integration tests
2. **Log extensively during development** - Remove verbose logs before committing
3. **Test error cases** - Ensure your server handles invalid inputs gracefully
4. **Use the inspector first** - It's the fastest way to debug protocol issues
5. **Keep test data realistic** - Mock data should resemble real API responses
6. **Version your test fixtures** - Keep test data in sync with API changes

## Next Steps

- Review the [MCP Development Guide](../MCP_DEVELOPMENT_GUIDE.md) for server implementation details
- Check [docs/TODO.md](../TODO.md) for infrastructure components in development
- See existing implementations in `apps/ai/mcp/` for examples
