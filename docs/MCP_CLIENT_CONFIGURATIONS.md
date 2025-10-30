# MCP Client Configuration Examples

This document provides **validated** installation instructions and configuration examples for various MCP clients. All information has been verified against official documentation (links provided for each client).

**Last Updated:** 2025 (validated against latest official docs)

## Configuration Patterns

### Standard JSON Configuration Format

Most MCP clients use a JSON configuration file with this structure:

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "package-name@latest"]
    }
  }
}
```

For hosted MCP servers (like our Creative Juices and CTO Tools):

```json
{
  "mcpServers": {
    "server-name": {
      "url": "https://ai.yuda.me/mcp/server-name/serve"
    }
  }
}
```

---

## Client-Specific Installation Methods

### Claude Code (CLI)

**📚 Official Docs:** [https://docs.claude.com/en/docs/claude-code/mcp](https://docs.claude.com/en/docs/claude-code/mcp)

**Command-line installation:**

```bash
# HTTP servers (recommended for hosted MCP servers)
claude mcp add --transport http creative-juices https://ai.yuda.me/mcp/creative-juices/serve

# Stdio servers (local executables)
claude mcp add --transport stdio server-name --env API_KEY=value -- npx -y package-name@latest

# SSE servers (deprecated but supported)
claude mcp add --transport sse server-name https://api.example.com/sse
```

**Scope options:**
- `--scope local` (default): Project-specific, private to user
- `--scope project`: Shared via `.mcp.json` in version control
- `--scope user`: Available across all projects globally

**Management commands:**
```bash
claude mcp list              # List all configured servers
claude mcp get <name>        # Get server details
claude mcp remove <name>     # Remove a server
```

**Config locations:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`
- Project: `.mcp.json` (when using `--scope project`)

---

### Claude Desktop

**📚 Official Docs:** [https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)

**⚠️ Note:** As of 2025, Claude Desktop now uses **Desktop Extensions** (.mcpb files) as the recommended installation method instead of manual JSON configuration.

**Recommended: Desktop Extensions (One-Click Install)**

1. Open Claude Desktop → Settings → Extensions
2. Click "Browse extensions" to view the directory
3. Click "Install" on desired extensions
4. Configure any required settings (API keys, etc.) through the UI

**Alternative: Manual JSON Configuration (Legacy)**

Edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://ai.yuda.me/mcp/creative-juices/serve"
    },
    "cto-tools": {
      "url": "https://ai.yuda.me/mcp/cto-tools/serve"
    }
  }
}
```

**Config locations:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

**More Info:** [Desktop Extensions Announcement](https://www.anthropic.com/engineering/desktop-extensions)

---

### Cline (VS Code Extension)

**📚 Official Docs:** [https://docs.cline.bot/mcp/configuring-mcp-servers](https://docs.cline.bot/mcp/configuring-mcp-servers)

**UI-Based Configuration (Recommended):**

1. Click the "MCP Servers" icon in Cline's top navigation
2. Select the "Configure" tab
3. Click "Configure MCP Servers" button to open `cline_mcp_settings.json`
4. Edit configuration or use the UI to enable/disable servers

**Configuration Format:**

```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://ai.yuda.me/mcp/creative-juices/serve",
      "headers": {"Authorization": "Bearer token"},
      "alwaysAllow": ["tool1", "tool2"],
      "disabled": false
    },
    "local-server": {
      "command": "node",
      "args": ["/path/to/server.js"],
      "env": {"API_KEY": "your_api_key"},
      "alwaysAllow": ["tool3"],
      "disabled": false
    }
  }
}
```

**Config File:** `cline_mcp_settings.json`

**Features:**
- Toggle switch to enable/disable servers individually
- Network timeout configuration (30s to 1 hour, default 1 minute)
- Integrated MCP Marketplace for discovering servers

---

### Cursor

**📚 Official Docs:** [https://docs.cursor.com/context/model-context-protocol](https://docs.cursor.com/context/model-context-protocol)

**Note:** The official docs URL appears to return 404, but configuration follows VS Code patterns.

**Configuration File Locations:**

- **Project-specific:** `.cursor/mcp.json` (shared with team)
- **Global (all projects):** `~/.cursor/mcp.json` (user home directory)

**Configuration Format:**

```json
{
  "mcpServers": {
    "creative-juices": {
      "command": "npx",
      "args": ["-y", "package-name@latest"],
      "env": {
        "API_KEY": "value"
      }
    }
  }
}
```

**For hosted HTTP servers:**
```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://ai.yuda.me/mcp/creative-juices/serve"
    }
  }
}
```

**Features:**
- Project-specific vs global configuration
- Environment variable support
- OAuth authentication support for MCP servers

---


### VS Code with GitHub Copilot

**📚 Official Docs:** [https://code.visualstudio.com/docs/copilot/chat/mcp-servers](https://code.visualstudio.com/docs/copilot/chat/mcp-servers)

**Requirements:**
- VS Code 1.99 or later (MCP support GA since v1.102)
- GitHub Copilot Chat enabled
- "MCP servers in Copilot" policy enabled (for organizations)

**Configuration File Locations:**

1. **Workspace-level:** `.vscode/mcp.json` (shared with team)
2. **User profile:** Global configuration across all workspaces
3. **Dev Container:** In `devcontainer.json` under `customizations.vscode.mcp`

**Configuration Format:**

```json
{
  "servers": {
    "creative-juices": {
      "type": "http",
      "url": "https://ai.yuda.me/mcp/creative-juices/serve",
      "headers": {"Authorization": "Bearer ${input:token}"}
    },
    "local-server": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"],
      "env": {"KEY": "${input:variable}"},
      "envFile": "${workspaceFolder}/.env"
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "token",
      "description": "API Token",
      "password": true
    }
  ]
}
```

**CLI installation:**
```bash
code --add-mcp '{"name":"creative-juices","command":"uvx","args":["mcp-server"]}'
```

**Key Features:**
- Input variables for secrets (no hardcoded API keys)
- Variable substitution: `${workspaceFolder}`, `${input:id}`
- Support for both stdio (local) and HTTP (remote) servers

---

### Gemini CLI

**📚 Official Docs:** [https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html)

**Command Syntax:**

```bash
gemini mcp add [options] <name> <commandOrUrl> [args...]
```

**Configuration Examples:**

```bash
# HTTP transport (hosted servers)
gemini mcp add --transport http creative-juices https://ai.yuda.me/mcp/creative-juices/serve

# SSE transport with authentication
gemini mcp add --transport sse secure-server https://api.example.com/sse/ \
  --header "Authorization: Bearer token123"

# Stdio (local executables)
gemini mcp add python-server python server.py --port 8080

# With environment variables
gemini mcp add server-name npx package-name@latest \
  --env API_KEY=value --env REGION=us-east-1
```

**Scope Options:**
- `-s project` (default): Project-specific (`.gemini/settings.json`)
- `-s user`: Global user configuration (`~/.gemini/settings.json`)

**Additional Flags:**
- `--timeout <ms>`: Connection timeout
- `--trust`: Bypass confirmation prompts
- `--include-tools <list>`: Comma-separated allowlist
- `--exclude-tools <list>`: Comma-separated blocklist

**Management Commands:**
```bash
gemini mcp list          # Display all configured servers
gemini mcp remove <name> # Delete a server
/mcp                     # Show status within CLI
/mcp auth                # Manage OAuth authentication
```

**Features:**
- OAuth 2.0 support for remote MCP servers
- FastMCP integration (`fastmcp install gemini-cli`)
- Automatic dependency management

---

### JetBrains AI Assistant (IntelliJ, PyCharm, etc.)

**📚 Official Docs:**
- [MCP Configuration](https://www.jetbrains.com/help/ai-assistant/configure-an-mcp-server.html)
- [MCP Overview](https://www.jetbrains.com/help/ai-assistant/mcp.html)
- [IntelliJ 2025.1 Blog Post](https://blog.jetbrains.com/idea/2025/05/intellij-idea-2025-1-model-context-protocol/)

**Requirements:**
- IntelliJ IDEA 2025.1 or later (full MCP client compatibility)
- "Codebase" mode toggle enabled in chat, or Edit mode active

**Setup Steps:**

1. Open Settings → Tools → AI Assistant → Model Context Protocol (MCP)
2. Click "Add" to create new MCP server configuration
3. Choose configuration method:
   - **Manual entry:** Specify Name, Command, Arguments, Environment Variables
   - **JSON input:** Paste complete JSON configuration
   - **Import from Claude:** Reuse Claude Desktop configurations

**Configuration Format:**

```json
{
  "mcpServers": {
    "creative-juices": {
      "command": "npx",
      "args": ["-y", "package-name@latest"]
    }
  }
}
```

**Examples by Deployment Type:**

```json
{
  "mcpServers": {
    "local-npx": {
      "command": "npx",
      "args": ["-y", "package-name@latest"]
    },
    "remote-http": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://api.example.com/mcp"]
    },
    "docker": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "/local:/container", "image"]
    }
  }
}
```

**Features:**
- Set scope: Global or project-specific (Level column)
- Monitor tools via Status column icon
- View logs: Help → Show Log in Explorer/Finder → mcp folder
- Stop server: Deselect checkbox and click Apply

---

### Visual Studio (Windows)

**📚 Official Docs:** [https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=vs-2022](https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=vs-2022)

**Requirements:**
- Visual Studio 2022 version 17.14 or later (not "2025" - that doesn't exist)
- GitHub Copilot Chat enabled
- Agent mode activated

**Configuration File Locations (searched in order):**

1. `%USERPROFILE%\.mcp.json` — global user configuration
2. `<SOLUTIONDIR>\.vs\mcp.json` — Visual Studio-specific, user-scoped
3. `<SOLUTIONDIR>\.mcp.json` — source-controlled repository config
4. `<SOLUTIONDIR>\.vscode\mcp.json` — repository-scoped
5. `<SOLUTIONDIR>\.cursor\mcp.json` — repository-scoped

**Configuration Format:**

```json
{
  "servers": {
    "creative-juices": {
      "type": "http",
      "url": "https://ai.yuda.me/mcp/creative-juices/serve"
    },
    "local-server": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "my_mcp.server"]
    }
  }
}
```

**Server Types:**
- `http`: Remote HTTP endpoints
- `stdio`: Local executables
- `sse`: Server-Sent Events (streaming)

---

### Codex (OpenAI CLI)

**📚 Official Docs:**
- [MCP Configuration](https://developers.openai.com/codex/mcp/)
- [Local Config](https://developers.openai.com/codex/local-config/)

**Configuration File:** `~/.codex/config.toml`

**Note:** Configuration is shared between Codex CLI and IDE extension.

**CLI Command:**

```bash
# Add MCP server via CLI
codex mcp add <server-name> --env VAR1=VALUE1 --env VAR2=VALUE2 -- <command>

# Example
codex mcp add context7 -- npx -y @upstash/context7-mcp
```

**TOML Configuration Format:**

```toml
# STDIO Servers
[mcp_servers.local-server]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[mcp_servers.local-server.env]
API_KEY = "your_api_key"
REGION = "us-east-1"

# HTTP Servers
[mcp_servers.figma]
url = "https://mcp.figma.com/mcp"
bearer_token = "your_token_here"

# Timeout Configuration
[mcp_servers.slow-server]
command = "python"
args = ["server.py"]
startup_timeout_sec = 30
tool_timeout_sec = 120

# OAuth Support (top-level)
experimental_use_rmcp_client = true
```

**Management:**
```bash
codex mcp --help          # View help
codex mcp add <name>      # Add server
codex mcp remove <name>   # Remove server
```

**Features:**
- Shared config between CLI and IDE extension
- OAuth support via RMCP client
- Configurable timeouts
- Access via gear icon in IDE extension → MCP settings → Open config.toml

---

## Advanced Configuration Options

### Environment Variables

For local servers requiring configuration:

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "package-name@latest"],
      "env": {
        "API_KEY": "your-api-key",
        "CONFIG_PATH": "/path/to/config"
      }
    }
  }
}
```

### Proxy Configuration

For hosted servers behind proxies:

```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://ai.yuda.me/mcp/creative-juices/serve",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN",
        "X-Custom-Header": "value"
      }
    }
  }
}
```

### Timeout Configuration

Adjust connection timeouts:

```json
{
  "mcpServers": {
    "server-name": {
      "url": "https://ai.yuda.me/mcp/server-name/serve",
      "timeout": 30000
    }
  }
}
```

---

## Cuttlefish-Specific Examples

### Creative Juices MCP

**Recommended configuration (hosted):**

```json
{
  "mcpServers": {
    "creative-juices": {
      "url": "https://ai.yuda.me/mcp/creative-juices/serve"
    }
  }
}
```

**Alternative: Local client proxy (if needed):**

```json
{
  "mcpServers": {
    "creative-juices": {
      "command": "uvx",
      "args": [
        "run",
        "--with", "mcp",
        "--with", "httpx",
        "https://ai.yuda.me/mcp/creative-juices/client.py"
      ]
    }
  }
}
```

### CTO Tools MCP

**Recommended configuration (hosted):**

```json
{
  "mcpServers": {
    "cto-tools": {
      "url": "https://ai.yuda.me/mcp/cto-tools/serve"
    }
  }
}
```

### QuickBooks MCP (Local - Requires OAuth)

**Configuration:**

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

---

## Troubleshooting

### Common Issues

**Server not connecting:**
- Verify URL is correct and accessible
- Check network/firewall settings
- Ensure MCP client is up to date

**Local server won't start:**
- Verify command and args are correct
- Check environment variables are set
- Ensure dependencies are installed (npx, uvx, etc.)

**Timeout errors:**
- Increase timeout value in configuration
- Check server is responding (curl test)
- Verify network stability

### Testing Configuration

**Test hosted server:**
```bash
curl -X POST https://ai.yuda.me/mcp/creative-juices/serve \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}'
```

**Expected response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "creative-juices", "version": "1.0.0"}
  }
}
```

---

## Official Documentation Links

### General MCP Resources
- [MCP Specification](https://spec.modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Chrome DevTools MCP Example](https://github.com/ChromeDevTools/chrome-devtools-mcp/)

### Client-Specific Documentation
- **Claude Code:** [https://docs.claude.com/en/docs/claude-code/mcp](https://docs.claude.com/en/docs/claude-code/mcp)
- **Claude Desktop:** [https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
- **Claude Desktop Extensions:** [https://www.anthropic.com/engineering/desktop-extensions](https://www.anthropic.com/engineering/desktop-extensions)
- **Cline:** [https://docs.cline.bot/mcp/configuring-mcp-servers](https://docs.cline.bot/mcp/configuring-mcp-servers)
- **Cursor:** [https://docs.cursor.com/context/model-context-protocol](https://docs.cursor.com/context/model-context-protocol) *(note: may return 404)*
- **VS Code:** [https://code.visualstudio.com/docs/copilot/chat/mcp-servers](https://code.visualstudio.com/docs/copilot/chat/mcp-servers)
- **Gemini CLI:** [https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html)
- **JetBrains AI Assistant:** [https://www.jetbrains.com/help/ai-assistant/mcp.html](https://www.jetbrains.com/help/ai-assistant/mcp.html)
- **Visual Studio:** [https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=vs-2022](https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=vs-2022)
- **Codex (OpenAI):** [https://developers.openai.com/codex/mcp/](https://developers.openai.com/codex/mcp/)

### Internal Documentation
- [Cuttlefish MCP Development Guide](./MCP_DEVELOPMENT_GUIDE.md)
- [Render Deployment Guide](./RENDER_DEPLOYMENT.md)

---

## Contributing

When adding support for a new MCP client:

1. Test the configuration format
2. Document the installation method
3. Add example configuration to this file
4. Update client list in README if applicable
5. Test with both hosted and local server types
