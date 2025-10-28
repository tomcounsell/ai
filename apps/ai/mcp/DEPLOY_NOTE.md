# Creative Juices MCP - Internal Deployment Note

**For internal use only. Users connect to: https://ai.yuda.me/mcp/creative-juices/serve**

## Render Deployment

On Render, create a **Web Service** for the MCP server:

**Start Command**:
```bash
MCP_TRANSPORT=streamable-http uv run python -m apps.ai.mcp.creative_juices_server
```

**Environment Variables**:
- `MCP_TRANSPORT=streamable-http` (enables HTTP mode)
- Render automatically provides `PORT` (FastMCP will use it)

**Service URL**: Map to `/mcp/creative-juices/serve` via Render routing or nginx

That's it. FastMCP handles the HTTP server internally when `transport='streamable-http'`.

## Local Testing

```bash
# Test stdio mode (default)
uv run python -m apps.ai.mcp.creative_juices_server

# Test HTTP mode
MCP_TRANSPORT=streamable-http uv run python -m apps.ai.mcp.creative_juices_server
```

## Notes

- No ASGI needed - FastMCP has built-in HTTP server
- Runs as separate service from Django app
- Port configured automatically by Render's PORT env var
