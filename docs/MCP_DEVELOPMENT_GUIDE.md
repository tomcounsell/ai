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

### Local Development

```bash
# Set environment variables
export QUICKBOOKS_ORG_ID=your_org_id
export QUICKBOOKS_API_KEY=your_api_key

# Run server
uv run python -m apps.ai.mcp.quickbooks_server
```

### Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "quickbooks": {
      "command": "uv",
      "args": ["run", "python", "-m", "apps.ai.mcp.quickbooks_server"],
      "env": {
        "QUICKBOOKS_ORG_ID": "your_org_id",
        "QUICKBOOKS_API_KEY": "your_api_key"
      }
    }
  }
}
```

---

## Examples in This Project

### QuickBooks Server
**File:** `apps/ai/mcp/quickbooks_server.py`

Shows:
- Resource definitions for QuickBooks data access
- Tools for creating invoices, searching customers
- Client initialization with organization ID

### Creative Juices Server
**File:** `apps/ai/mcp/creative_juices_server.py`

Shows:
- Simple tool without external dependencies
- Prompt definitions
- Minimal Django integration

---

## Key Patterns

- ✅ Module-level `mcp = FastMCP()` instance
- ✅ `@mcp.tool()`, `@mcp.resource()`, `@mcp.prompt()` decorators
- ✅ Type hints for automatic schema generation
- ✅ Single file per server
- ✅ Global client initialization pattern

See the [official FastMCP guide](https://github.com/modelcontextprotocol/python-sdk#fastmcp) for complete API details.
