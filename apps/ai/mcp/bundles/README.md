# MCP Bundle Build Instructions

This directory contains the source files for building `.mcpb` (MCP Bundle) files that can be installed in Claude Desktop.

## Directory Structure

```
bundles/
├── creative-juices/
│   ├── client.js         # Node.js proxy that forwards to hosted service
│   └── manifest.json     # MCPB manifest with server configuration
│
└── cto-tools/
    ├── client.js         # Node.js proxy that forwards to hosted service
    └── manifest.json     # MCPB manifest with server configuration
```

## Built Bundles

The compiled `.mcpb` files are stored in the parent directory:
- `../creative-juices.mcpb`
- `../cto-tools.mcpb`

## Building Bundles

### Prerequisites

No special tools required - just `zip` (standard on macOS/Linux).

### Build Commands

**Creative Juices:**
```bash
cd creative-juices
zip -r ../creative-juices.mcpb manifest.json client.js
```

**CTO Tools:**
```bash
cd cto-tools
zip -r ../cto-tools.mcpb manifest.json client.js
```

**Build All:**
```bash
# From the bundles/ directory
cd creative-juices && zip -r ../../creative-juices.mcpb manifest.json client.js && cd ..
cd cto-tools && zip -r ../../cto-tools.mcpb manifest.json client.js && cd ..
```

## Architecture

### Hybrid Approach

These bundles use a **hybrid architecture**:

1. **Local client**: Node.js proxy script bundled in the `.mcpb`
2. **Remote server**: Actual MCP server hosted at `ai.yuda.me`

### Why Node.js?

Per [MCPB recommendations](https://github.com/anthropics/mcpb):
> "We recommend implementing MCP servers in Node.js rather than Python to reduce installation friction. Node.js ships with Claude for macOS and Windows."

Benefits:
- ✅ No Python/uvx dependency
- ✅ Node.js pre-installed in Claude Desktop
- ✅ Works out-of-the-box for all users
- ✅ Zero external dependencies

### How It Works

1. User installs `.mcpb` file in Claude Desktop
2. Claude Desktop extracts bundle to local directory
3. When tool is called, Claude runs `node ${__dirname}/client.js`
4. Client proxy forwards stdin/stdout to `https://ai.yuda.me/mcp/*/serve`
5. Hosted Django server processes MCP protocol requests
6. Response flows back through proxy to Claude

## Manifest Structure

Per [MCPB specification](https://github.com/anthropics/mcpb/blob/main/MANIFEST.md):

```json
{
  "manifest_version": "0.3",
  "name": "server-name",
  "version": "1.0.0",
  "description": "Brief description",
  "author": {
    "name": "Author Name"
  },
  "server": {
    "type": "node",              // ✅ Required: "node" | "python" | "binary"
    "entry_point": "client.js",  // ✅ Required: Path to main file
    "mcp_config": {              // ✅ Required: Execution configuration
      "command": "node",
      "args": ["${__dirname}/client.js"],
      "env": {}
    }
  },
  "tools": [...],
  "compatibility": {...}
}
```

**Key Points:**
- `entry_point`: Must point to bundled file
- `${__dirname}`: Resolves to bundle extraction directory
- `mcp_config`: Required nested object with `command` and `args`

## Testing Bundles

### Local Testing

```bash
# Verify bundle contents
unzip -l ../creative-juices.mcpb

# Extract and inspect manifest
unzip -p ../creative-juices.mcpb manifest.json | jq .

# Test Django download
curl -O http://localhost:8000/mcp/creative-juices/download.mcpb
```

### Claude Desktop Testing

1. Download `.mcpb` file from `https://ai.yuda.me/mcp/*/download.mcpb`
2. Open Claude Desktop → Settings → Extensions
3. Click "Install from file"
4. Select the downloaded `.mcpb` file
5. Verify tools appear in MCP servers list
6. Test tool execution in chat

### Validation Checklist

- [ ] Bundle contains `manifest.json` and `client.js`
- [ ] Manifest has `server.entry_point` field
- [ ] Manifest has `server.mcp_config.command` and `args`
- [ ] `args` uses `${__dirname}/client.js`
- [ ] Bundle downloads via Django view
- [ ] Bundle installs in Claude Desktop without errors
- [ ] Tools execute successfully

## Troubleshooting

### Bundle Won't Install

**Symptom:** Claude Desktop shows error when installing `.mcpb`

**Causes:**
1. Missing required manifest fields (`entry_point`, `mcp_config`)
2. Invalid JSON in manifest
3. Missing `client.js` file in bundle

**Fix:**
```bash
# Validate manifest structure
unzip -p creative-juices.mcpb manifest.json | jq .

# Verify file list
unzip -l creative-juices.mcpb
```

### Tools Don't Execute

**Symptom:** Tools appear in Claude but fail when called

**Causes:**
1. Hosted server not responding
2. Network connectivity issues
3. Client.js syntax errors

**Fix:**
```bash
# Test hosted endpoint directly
curl -X POST https://ai.yuda.me/mcp/creative-juices/serve \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}'

# Verify Node.js syntax
node -c client.js
```

## Deployment

The built `.mcpb` files are:
1. Committed to the repository at `apps/ai/mcp/*.mcpb`
2. Served by Django views at `/mcp/*/download.mcpb`
3. Available for download at `https://ai.yuda.me/mcp/*/download.mcpb`

No separate deployment needed - the bundles deploy with the Django app.

## References

- [MCPB Specification](https://github.com/anthropics/mcpb)
- [MCPB Manifest Format](https://github.com/anthropics/mcpb/blob/main/MANIFEST.md)
- [MCP Protocol Specification](https://spec.modelcontextprotocol.io/)
- [Claude Desktop Extensions](https://www.anthropic.com/engineering/desktop-extensions)
- [Fix Documentation](../../docs/MCPB_BUNDLE_FIX.md)
