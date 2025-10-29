# MCP Server Development Guide

## Overview

Model Context Protocol (MCP) servers in this project use **FastMCP** from the official MCP Python SDK.

**Requirements:**
- MCP SDK v1.15+
- Python 3.11+
- All MCP servers in: `apps/ai/mcp/`

**External Documentation:**
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - Official SDK and FastMCP guide
- [MCP Specification](https://spec.modelcontextprotocol.io/) - Protocol details

---

## Project-Specific Patterns

### File Structure

```
apps/ai/mcp/
├── __init__.py
├── quickbooks_server.py        # QuickBooks integration
├── creative_juices_server.py   # Creative Juices tool
└── creative_juices_words.py    # Data for Creative Juices
```

### Server Template

```python
"""Service Name MCP Server using FastMCP."""

import logging
import os
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("Service Name MCP")

# Global client initialization pattern
_client = None

def get_client():
    if _client is None:
        raise RuntimeError("Client not initialized")
    return _client

def initialize_client(api_key: str):
    global _client
    # _client = ServiceClient(api_key)

# Define tools/resources/prompts
@mcp.tool()
async def my_tool(param: str) -> dict:
    """Tool description."""
    return {}

def main():
    api_key = os.environ.get("SERVICE_API_KEY")
    if not api_key:
        logger.error("Missing SERVICE_API_KEY")
        return

    initialize_client(api_key)
    mcp.run()  # Starts event loop internally

if __name__ == "__main__":
    main()
```

### Django Integration Pattern

When MCP server needs Django models:

```python
def main():
    import os, sys

    # Add project root to path
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    sys.path.insert(0, project_root)

    # Setup Django
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
        import django
        django.setup()
    except Exception as e:
        logger.warning(f"Django setup skipped: {e}")

    mcp.run()  # Starts event loop internally
```

---

## Running MCP Servers

### Hosting & Installation Philosophy

**Yudame AI MCP servers follow a hosted-first approach:**

1. **Hosted Service (Preferred)**
   - All MCP servers are hosted at `https://ai.yuda.me/mcp/{server-name}/serve`
   - Users configure via simple URL (no local installation required)
   - Examples: Creative Juices, CTO Tools
   - Benefits: Zero setup, always latest version, no local dependencies

2. **Local Execution (When Required)**
   - Only for servers requiring local file system access or sensitive credentials
   - Examples: QuickBooks (requires OAuth), local development tools
   - Uses `uvx run` for zero-install execution from GitHub

**Configuration Examples:**

### Hosted MCP Server (Recommended)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://ai.yuda.me/mcp/creative-juices/serve"
    }
  }
}
```

### Local Execution (When Needed)

For servers requiring local access or credentials:

```json
{
  "mcpServers": {
    "quickbooks": {
      "command": "uvx",
      "args": [
        "run",
        "https://raw.githubusercontent.com/yudame/cuttlefish/main/apps/ai/mcp/quickbooks_server.py"
      ],
      "env": {
        "QUICKBOOKS_ORG_ID": "your_org_id",
        "QUICKBOOKS_API_KEY": "your_api_key"
      }
    }
  }
}
```

### Local Development Testing

```bash
# Set environment variables
export QUICKBOOKS_ORG_ID=your_org_id
export QUICKBOOKS_API_KEY=your_api_key

# Run server locally
uv run python -m apps.ai.mcp.quickbooks_server
```

---

## Creating New MCP Servers

### Decision: Hosted vs Local

**Choose Hosted (default):**
- Server provides information, data transformation, or stateless operations
- No local file system access needed
- No sensitive credentials required
- Examples: Creative tools, data formatters, reference data

**Choose Local only when:**
- Requires reading/writing local files
- Needs access to user's file system
- Handles sensitive OAuth tokens or API keys
- Needs to interact with local development environment

### Hosted Server Checklist

1. **Create server file:** `apps/ai/mcp/{name}_server.py`
2. **Add Django view:** Serve at `/mcp/{name}/serve`
3. **Create documentation page:** `apps/ai/mcp/{name}_web.html`
4. **Update URLs:** Add routes for serve endpoint and docs page
5. **Add to homepage:** Include in MCP servers section
6. **Test hosted endpoint:** Verify at `https://ai.yuda.me/mcp/{name}/serve`

### Installation Guide Format

**For hosted servers, always use this format in docs:**

```json
{
  "mcpServers": {
    "server-name": {
      "url": "https://ai.yuda.me/mcp/server-name/serve"
    }
  }
}
```

**For local-only servers, use uvx format:**

```json
{
  "mcpServers": {
    "server-name": {
      "command": "uvx",
      "args": [
        "run",
        "https://raw.githubusercontent.com/yudame/cuttlefish/main/apps/ai/mcp/server_name.py"
      ]
    }
  }
}
```

---

## Django Integration Implementation

### Architecture

MCP server endpoints are **integrated directly into Django** via views that handle MCP JSON-RPC protocol requests. No separate service needed!

**Key files:**
- **Views:** `apps/ai/views/mcp_server_views.py` - Handle MCP protocol
- **URLs:** `apps/ai/urls.py` - Routes like `/mcp/{name}/serve`
- **Server logic:** `apps/ai/mcp/{name}_server.py` - Tool implementations

### How It Works

The Django view receives JSON-RPC requests and calls the MCP tool functions directly:

```python
# apps/ai/views/mcp_server_views.py
from apps.ai.mcp.creative_juices_server import get_inspiration

async def _handle_tool_call(self, tool_name, arguments):
    if tool_name == "get_inspiration":
        result = await get_inspiration()

    return {
        "content": [{"type": "text", "text": json.dumps(result)}]
    }
```

The view implements the MCP JSON-RPC 2.0 protocol:
- `initialize` - Server handshake
- `tools/list` - List available tools
- `tools/call` - Execute a tool

### Creating a New Hosted Server

**1. Create the server file** (`apps/ai/mcp/your_server.py`):
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Your Server")

@mcp.tool()
async def your_tool() -> dict:
    """Tool description."""
    return {"result": "data"}
```

**2. Add a Django view** (`apps/ai/views/mcp_server_views.py`):
```python
@method_decorator(csrf_exempt, name="dispatch")
class YourServerMCPView(View):
    def post(self, request):
        # Parse MCP request
        mcp_request = json.loads(request.body)
        method = mcp_request.get("method")

        # Handle initialize, tools/list, tools/call
        # ...

        return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
```

**3. Add URL route** (`apps/ai/urls.py`):
```python
path("your-server/serve", YourServerMCPView.as_view(), name="mcp-your-server-serve")
```

**4. Export the view** (`apps/ai/views/__init__.py`):
```python
from .mcp_server_views import YourServerMCPView

__all__ = [..., "YourServerMCPView"]
```

### Local Testing

```bash
# Start Django server
uv run python manage.py runserver

# Test initialize
curl -X POST http://localhost:8000/mcp/your-server/serve \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'

# Test tools/list
curl -X POST http://localhost:8000/mcp/your-server/serve \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'

# Test tool call
curl -X POST http://localhost:8000/mcp/your-server/serve \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"your_tool","arguments":{}},"id":3}'
```

### Production Deployment

The endpoint is deployed automatically with the main Django app on Render:
- No separate service needed
- No ASGI/async server needed (uses `asyncio.run()` in view)
- CSRF exempt for MCP client compatibility
- Available at: `https://ai.yuda.me/mcp/{name}/serve`

**That's it!** Just commit and push - the endpoint will be live when Django deploys.

---

## Examples in This Project

### Creative Juices Server (Hosted)
**File:** `apps/ai/mcp/creative_juices_server.py`
**URL:** `https://ai.yuda.me/mcp/creative-juices/serve`
**Docs:** `https://ai.yuda.me/mcp/creative-juices`

Shows:
- Hosted MCP server pattern (no local installation)
- Three distinct tools with clear use cases
- Pure randomness for creativity (no intelligence)
- Tool descriptions that instruct LLMs when to use each
- Minimal external dependencies
- Complete documentation page with installation guide

### CTO Tools Server (Hosted)
**File:** `apps/ai/mcp/cto_tools_server.py`
**URL:** `https://ai.yuda.me/mcp/cto-tools/serve`
**Docs:** `https://ai.yuda.me/mcp/cto-tools`

Shows:
- Hosted MCP server for engineering leadership
- Weekly review framework tool
- Structured prompts for CTO workflows
- Documentation with brand-compliant styling

### QuickBooks Server (Local - OAuth Required)
**File:** `apps/ai/mcp/quickbooks_server.py`

Shows:
- Local execution pattern for OAuth credentials
- Resource definitions for QuickBooks data access
- Tools for creating invoices, searching customers
- Client initialization with organization ID
- Environment variable configuration

---

## Key Patterns

- ✅ Module-level `mcp = FastMCP()` instance
- ✅ `@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()` decorators
- ✅ Type hints for automatic schema generation
- ✅ Single file per server
- ✅ Global client initialization pattern

See the [official FastMCP guide](https://github.com/modelcontextprotocol/python-sdk#fastmcp) for complete API details.
