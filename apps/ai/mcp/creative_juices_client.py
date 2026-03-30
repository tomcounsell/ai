"""
Creative Juices MCP Client - Thin proxy to hosted service.

This client forwards MCP protocol calls to the hosted Creative Juices server
at app.bwforce.ai. Users can install with zero dependencies using uvx.

Installation (one-click via .mcpb file):
    Download creative-juices.mcpb from app.bwforce.ai/mcp/creative-juices/download.mcpb

Manual installation:
    Add to claude_desktop_config.json:
    {
      "mcpServers": {
        "creative-juices": {
          "command": "uvx",
          "args": [
            "run",
            "--with", "mcp",
            "--with", "httpx",
            "https://app.bwforce.ai/mcp/creative-juices/client.py"
          ]
        }
      }
    }
"""

import asyncio
import sys

try:
    import httpx
    from mcp.client import Client
    from mcp.client.session import ClientSession
except ImportError:
    print(
        "Error: Required packages not found. uvx should install these automatically.",
        file=sys.stderr,
    )
    print("If running manually, install with: pip install mcp httpx", file=sys.stderr)
    sys.exit(1)

# Hosted service URL
HOSTED_SERVICE_URL = "https://app.bwforce.ai/mcp/creative-juices/serve"


async def forward_to_hosted_service():
    """Forward MCP protocol stdin/stdout to hosted HTTP service."""

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        # Connect to hosted service
        async with Client(
            read=sys.stdin.buffer,
            write=sys.stdout.buffer,
        ) as client:
            async with ClientSession(client):
                # Initialize connection to hosted service
                response = await http_client.post(
                    HOSTED_SERVICE_URL,
                    headers={"Content-Type": "application/json"},
                    json={"jsonrpc": "2.0", "method": "initialize", "params": {}},
                )

                if response.status_code != 200:
                    print(
                        f"Error connecting to hosted service: {response.status_code}",
                        file=sys.stderr,
                    )
                    sys.exit(1)

                # Forward all protocol messages
                while True:
                    try:
                        # Read from stdin
                        line = sys.stdin.buffer.readline()
                        if not line:
                            break

                        # Forward to hosted service
                        response = await http_client.post(
                            HOSTED_SERVICE_URL,
                            headers={"Content-Type": "application/json"},
                            data=line,
                        )

                        # Write response to stdout
                        sys.stdout.buffer.write(response.content)
                        sys.stdout.buffer.flush()

                    except Exception as e:
                        print(f"Error forwarding message: {e}", file=sys.stderr)
                        break


def main():
    """Main entry point for the proxy client."""
    try:
        asyncio.run(forward_to_hosted_service())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
