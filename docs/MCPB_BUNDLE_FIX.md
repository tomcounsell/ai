# MCPB Bundle Fix - Creative Juices & CTO Tools

## Problem

The current `.mcpb` bundles for Creative Juices and CTO Tools **cannot load into Claude Desktop** because:

1. **Incorrect manifest structure** - The `server` object is missing required fields
2. **Missing bundled files** - The `client.py` file is not included in the ZIP
3. **Invalid file references** - Trying to download files via URL instead of bundling them

## Official MCPB Requirements

Per [anthropics/mcpb](https://github.com/anthropics/mcpb):

### Required Manifest Structure

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
    "type": "python",           // Required: "node" | "python" | "binary"
    "entry_point": "server.py", // Required: Path to main file
    "mcp_config": {             // Required: Execution configuration
      "command": "python",
      "args": ["${__dirname}/server.py"],
      "env": {}
    }
  }
}
```

### Required Bundle Contents

The `.mcpb` ZIP must contain:

1. **manifest.json** (required)
2. **Actual server code** (e.g., `client.py`)
3. **Dependencies** (if not using external tools like uvx)
4. **icon.png** (optional)

**Key insight:** MCPB is for **local servers** that run on the user's machine. The bundle must contain the actual executable code.

## Current Implementation (Broken)

**File:** `apps/ai/views/mcp_views.py:CreativeJuicesBundleView`

### Issues:

**1. Incorrect manifest structure:**
```python
manifest = {
    "server": {
        "type": "python",
        "command": "uvx",  # ❌ Wrong location - should be in mcp_config
        "args": [...]      # ❌ Wrong location - should be in mcp_config
    }
}
```

**2. Missing client.py in bundle:**
```python
with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
    zip_file.writestr('manifest.json', json.dumps(manifest, indent=2))
    # ❌ client.py not added to ZIP!
```

**3. Trying to download client.py via URL:**
```python
"args": [
    "run",
    "--with", "mcp",
    "--with", "httpx",
    "https://app.bwforce.ai/mcp/creative-juices/client.py"  # ❌ External URL
]
```

## Solution

### Fix 1: Correct Manifest Structure

```python
manifest = {
    "manifest_version": "0.3",
    "name": "creative-juices",
    "version": "1.0.0",
    "display_name": "Creative Juices",
    "description": "...",
    "author": {
        "name": "Tom Counsell",
        "url": "https://github.com/tomcounsell"
    },
    "server": {
        "type": "python",
        "entry_point": "client.py",  # ✅ Path to bundled file
        "mcp_config": {              # ✅ Execution config
            "command": "uvx",
            "args": [
                "run",
                "--with", "mcp",
                "--with", "httpx",
                "${__dirname}/client.py"  # ✅ Use bundled file
            ],
            "env": {}
        }
    },
    "tools": [...],
    "compatibility": {...}
}
```

### Fix 2: Bundle client.py in ZIP

```python
with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
    # Add manifest.json
    zip_file.writestr('manifest.json', json.dumps(manifest, indent=2))

    # ✅ Add client.py to bundle
    client_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "mcp",
        "creative_juices_client.py"
    )
    zip_file.write(client_path, 'client.py')

    # Optional: Add icon
    # icon_path = os.path.join(...)
    # if os.path.exists(icon_path):
    #     zip_file.write(icon_path, 'icon.png')
```

### Fix 3: Proper Variable Substitution

Use `${__dirname}` to reference bundled files:

```json
{
  "args": [
    "run",
    "--with", "mcp",
    "--with", "httpx",
    "${__dirname}/client.py"  // ✅ Resolves to bundle directory
  ]
}
```

## Alternative: Node.js Implementation

**Recommendation from MCPB docs:** Use Node.js instead of Python to reduce friction.

> "We recommend implementing MCP servers in Node.js rather than Python to reduce installation friction. Node.js ships with Claude for macOS and Windows."

### Benefits:
- Node.js pre-installed in Claude Desktop
- No Python/uvx dependency
- Simpler user experience

### Implementation:

**1. Create `client.js`:**
```javascript
#!/usr/bin/env node
const http = require('https');

const HOSTED_SERVICE_URL = 'https://app.bwforce.ai/mcp/creative-juices/serve';

// Read from stdin, forward to hosted service, write to stdout
process.stdin.on('data', async (chunk) => {
  const response = await fetch(HOSTED_SERVICE_URL, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: chunk
  });

  const data = await response.text();
  process.stdout.write(data);
});
```

**2. Update manifest:**
```json
{
  "server": {
    "type": "node",
    "entry_point": "client.js",
    "mcp_config": {
      "command": "node",
      "args": ["${__dirname}/client.js"],
      "env": {}
    }
  },
  "compatibility": {
    "runtimes": {
      "node": ">=16.0.0"
    }
  }
}
```

## Testing the Fix

### Local Testing

1. **Download the .mcpb file:**
   ```bash
   curl -O http://localhost:8000/mcp/creative-juices/download.mcpb
   ```

2. **Inspect contents:**
   ```bash
   unzip -l creative-juices.mcpb
   # Should show:
   # manifest.json
   # client.py (or client.js)
   ```

3. **Validate manifest:**
   ```bash
   unzip -p creative-juices.mcpb manifest.json | jq .
   # Check structure matches MCPB spec
   ```

4. **Install in Claude Desktop:**
   - Open Claude Desktop → Settings → Extensions
   - Click "Install from file"
   - Select `creative-juices.mcpb`
   - Should load without errors

### Validation Checklist

- [ ] Manifest has `entry_point` field
- [ ] Manifest has `mcp_config` object with `command` and `args`
- [ ] `args` uses `${__dirname}` to reference bundled files
- [ ] ZIP contains `client.py` (or `client.js`)
- [ ] ZIP contains `manifest.json`
- [ ] File opens in Claude Desktop without errors
- [ ] Tools appear in Claude Desktop MCP servers list
- [ ] Tools execute successfully when called

## Implementation Priority

**Immediate Fix:** Update existing Python implementation with correct manifest structure

**Future Enhancement:** Migrate to Node.js for better user experience

## Related Files

- `apps/ai/views/mcp_views.py` - Bundle generation views
- `apps/ai/mcp/creative_juices_client.py` - Python proxy client
- `apps/ai/mcp/cto_tools_client.py` - CTO Tools proxy client (if exists)

## References

- [MCPB GitHub Repository](https://github.com/anthropics/mcpb)
- [MCPB Manifest Specification](https://github.com/anthropics/mcpb/blob/main/MANIFEST.md)
- [MCP Protocol Specification](https://spec.modelcontextprotocol.io/)
- [Claude Desktop Extensions](https://www.anthropic.com/engineering/desktop-extensions)
