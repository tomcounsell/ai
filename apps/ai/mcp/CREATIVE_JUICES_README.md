# Creative Juices MCP - Internal Development Documentation

**For internal cuttlefish development only. Public documentation is at: https://github.com/yudame/ai-skills/creative-juices**

## Overview

Creative Juices is an MCP (Model Context Protocol) server hosted at `https://ai.yuda.me/mcp/creative-juices/serve` that provides randomness tools for divergent thinking.

## Tools Provided

1. **`get_inspiration()`** - Gentle creative nudges with everyday metaphors
2. **`think_outside_the_box()`** - Intense creative shocks with dramatic metaphors
3. **`reality_check()`** - Strategic validation using proven thinking frameworks

## Development Setup

### Local Testing (stdio mode)

```bash
# From cuttlefish repository root
uv run python -m apps.ai.mcp.creative_juices_server
```

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector uv run python -m apps.ai.mcp.creative_juices_server
```

### Local HTTP Testing

See `CREATIVE_JUICES_DEPLOYMENT.md` for ASGI server configuration.

## Production Deployment

Creative Juices is deployed as an HTTP-based MCP server at:
**https://ai.yuda.me/mcp/creative-juices/serve**

See `CREATIVE_JUICES_DEPLOYMENT.md` for full deployment guide.

## Tool Reference

See public documentation at https://github.com/yudame/ai-skills/creative-juices for usage examples and detailed tool descriptions.

## Code Structure

```
apps/ai/mcp/
├── creative_juices_server.py       # Main MCP server (FastMCP)
├── creative_juices_words.py        # Curated word lists (600+ words)
├── creative_juices_web.html        # Landing page
├── creative_juices_manifest.json   # MCP manifest
├── CREATIVE_JUICES_README.md       # This file (internal docs)
└── CREATIVE_JUICES_DEPLOYMENT.md   # Deployment guide

apps/ai/views/mcp_views.py
├── CreativeJuicesLandingView       # Serves web page
├── CreativeJuicesManifestView      # Serves manifest.json
├── CreativeJuicesBundleView        # Generates .mcpb bundle
└── creative_juices_mcp_http()      # ASGI endpoint for HTTP transport
```

## Running Tests

```bash
uv run pytest apps/ai/tests/test_mcp_creative_juices.py -v
```

## Adding New Words

Edit `creative_juices_words.py`:
- `VERBS["inspiring"]` - Gentle, constructive actions
- `VERBS["out_of_the_box"]` - Intense, dramatic actions
- `NOUNS["inspiring"]` - Everyday concrete objects
- `NOUNS["out_of_the_box"]` - Extreme, dramatic concepts

## Related Documentation

- **Deployment**: `CREATIVE_JUICES_DEPLOYMENT.md` - Full HTTP/ASGI deployment guide
- **Specification**: `docs/specs/CREATIVE_JUICES_MCP.md` - Design philosophy and detailed spec
- **Public Docs**: https://github.com/yudame/ai-skills/creative-juices - User-facing documentation
- **Task Doc**: `/TASK_AI_SKILLS_UPDATE.md` - Guide for updating public ai-skills repo
