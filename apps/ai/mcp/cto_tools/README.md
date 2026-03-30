# CTO Tools - MCP Server

Engineering leadership frameworks for AI assistants.

## Overview

CTO Tools is a Model Context Protocol (MCP) server that provides structured frameworks and best practices for common CTO and engineering leadership activities. It helps AI assistants guide engineering leaders through weekly reviews, team analysis, and strategic decision-making.

## Features

- **📊 Weekly Team Reviews**: Structured framework for analyzing team progress
- **🏗️ Architecture Reviews**: Document system architecture with C4 diagrams
- **🔍 Git Analysis**: Commands and techniques for extracting insights from commit history
- **📝 Executive Summaries**: Templates for stakeholder communication
- **🎯 Action-Oriented**: Clear next steps and decision frameworks

## Installation

### Option 1: One-Click Install (Recommended)

Download and install the `.mcpb` bundle in Claude Desktop:

**https://app.bwforce.ai/mcp/cto-tools/download.mcpb**

1. Click the download link above
2. Open Claude Desktop → Settings → Extensions
3. Click "Install from file"
4. Select the downloaded `cto-tools.mcpb` file
5. Done! No configuration needed.

**Architecture:**
- Bundle contains a Node.js proxy client (no Python/uvx needed)
- Connects to hosted server at `https://app.bwforce.ai/mcp/cto-tools/serve`
- Zero dependencies (Node.js ships with Claude Desktop)

### Option 2: Direct Hosted Connection

For clients that support HTTP/SSE MCP servers directly:

```json
{
  "mcpServers": {
    "cto-tools": {
      "url": "https://app.bwforce.ai/mcp/cto-tools/serve"
    }
  }
}
```

### Option 3: Standalone Single-File Server (Local)

The CTO Tools MCP server is a **single executable file** with inline dependencies.
You can run it directly with `uv` without needing the full cuttlefish project.

**Run from URL:**

```json
{
  "mcpServers": {
    "cto-tools": {
      "command": "uv",
      "args": [
        "run",
        "https://raw.githubusercontent.com/tomcounsell/cuttlefish/main/apps/ai/mcp/cto_tools_server.py"
      ]
    }
  }
}
```

**Run Local File:**

```bash
# Download the file
curl -O https://raw.githubusercontent.com/tomcounsell/cuttlefish/main/apps/ai/mcp/cto_tools_server.py

# Make it executable (optional)
chmod +x cto_tools_server.py

# Run it
uv run cto_tools_server.py
# or
./cto_tools_server.py
```

**Claude Desktop config** (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cto-tools": {
      "command": "uv",
      "args": ["run", "/absolute/path/to/cto_tools_server.py"]
    }
  }
}
```

**From Cuttlefish Project:**

If you have the cuttlefish repository:

```json
{
  "mcpServers": {
    "cto-tools": {
      "command": "uv",
      "args": ["run", "/path/to/cuttlefish/apps/ai/mcp/cto_tools_server.py"]
    }
  }
}
```

## Available Tools

### weekly_review(days=7, categories=5)

Provides a streamlined 3-phase framework for conducting engineering team reviews.

**Purpose**: Systematically review your development team's work to produce a concise summary. Analysis-focused, not prescriptive. Works with ANY codebase and tech stack.

**Parameters**:
- `days` (int, default: 7): Number of days to review (e.g., 7 for weekly, 14 for bi-weekly, 30 for monthly)
- `categories` (int, default: 5): Number of work categories to organize the output into

**Returns**: Phase-by-phase instructions for creating a concise summary including:

**Phase 1 - Data Gathering**:
- Git commands to run in parallel for efficient data collection
- Commit history, author statistics, and file changes
- Commands automatically adjusted based on `days` parameter

**Phase 2 - Internal Analysis**:
- Review commits and identify patterns
- Choose N categories (based on `categories` parameter) that emerge from the work
- Note key stats (commits, files, contributors)
- Suggested categories: AI & ML, Auth & Security, Frontend/UX, Performance, Code Quality, Bug Fixes, Data & Analytics, DevOps, API, Billing, Reporting, Database, Testing

**Phase 3 - Concise Output**:
- Plain text (.txt) format with full Unicode emoji support
- Single summary organized by N categories (2-5 bullets each)
- Stats section with commit counts and contributor recognition
- Suitable for any communication channel (chat, email, reports)

**Example Usage**:

```
# Default 7-day weekly review
Claude, use CTO Tools to run a weekly review of my team's work.

# Custom 14-day review with 3 categories
Claude, use CTO Tools weekly_review with days=14 and categories=3.

# Monthly review with 7 categories
Claude, use CTO Tools weekly_review with days=30 and categories=7.
```

**Features**:
- 📊 Concise output suitable for any channel
- 🎯 Focus on what changed, not lengthy analysis
- 👥 Automatic contributor recognition with commit counts
- 🔍 Internal analysis (using sequential thinking) with brief output
- ⚡ Fast reviews (15-20 min) with clear deliverables
- 🎨 Full Unicode emoji support in plain text format
- ⚙️ Flexible time periods and category counts

The framework guides you through systematic analysis that produces a concise, scannable summary
of your team's work with category-based organization and contributor recognition.

### architecture_review(focus="system", depth="detailed", include_diagrams=True)

Provides a structured framework for conducting architecture reviews with diagram guidance.

**Purpose**: Systematically review and document system architecture with clear diagrams. Works with ANY codebase and tech stack.

**Parameters**:
- `focus` (str, default: "system"): Area to focus on - "system", "api", "data", "security", or a specific component name
- `depth` (str, default: "detailed"): Level of detail - "overview" (1-2 pages), "detailed" (3-5 pages), or "deep-dive" (5+ pages)
- `include_diagrams` (bool, default: True): Whether to include Mermaid diagram templates and guidance

**Returns**: Phase-by-phase instructions for creating architecture documentation including:

**Phase 1 - Exploration**:
- Commands to explore project structure and dependencies
- Focus-specific guidance (system, api, data, security)
- Entry point and configuration file discovery

**Phase 2 - Analysis**:
- Identify architectural style (Monolith, Microservices, etc.)
- Map key components and boundaries
- Assess patterns and quality attributes
- Note concerns for recommendations

**Phase 3 - Documentation**:
- Complete document template with all sections
- Mermaid diagram examples (C4 model, sequence, data flow)
- Quality checklist before finalizing

**Example Usage**:

```
# Full system architecture review
Claude, use CTO Tools to review this project's architecture.

# API-focused review with overview depth
Claude, use CTO Tools architecture_review with focus="api" and depth="overview".

# Deep-dive into data architecture
Claude, use CTO Tools architecture_review with focus="data" and depth="deep-dive".

# Quick review without diagrams
Claude, use CTO Tools architecture_review with depth="overview" and include_diagrams=False.
```

**Diagram Types Included**:
- 🏗️ C4 Model (Context, Container, Component)
- 🔄 Sequence diagrams for key flows
- 📊 Data flow diagrams
- All in Mermaid syntax (GitHub/Notion compatible)

**Features**:
- 📐 Structured document template
- 🎨 Mermaid diagram guidance
- ✅ Quality checklist
- 🎯 Focus-specific exploration
- 📊 Multiple depth levels

## How It Works

1. **Request a Tool**: Ask Claude to use CTO Tools for engineering leadership tasks
2. **Get Framework**: Receive structured instructions and best practices
3. **Follow Steps**: Use provided git commands and analysis frameworks
4. **Generate Insights**: Create executive summaries and action items

## Privacy & Security

- ✅ **No external API calls** - All operations are local
- ✅ **No authentication** - No credentials required
- ✅ **No data collection** - Nothing stored or transmitted
- ✅ **Local execution** - Git commands run in your environment only

## Use Cases

### Weekly/Monthly Team Reviews
Review your team's progress with flexible time periods:
- Extract commit history from any number of days (default: 7)
- Analyze internally to identify N key categories (default: 5)
- Generate concise summary with bullets
- Include contributor stats and recognition
- Plain text format with full Unicode emoji support

### Communication-Ready Output
The plain text format is designed for immediate sharing:
- Chat channels (Slack, Teams, Discord) - full emoji support
- Email updates to stakeholders
- Status reports for leadership
- Team retrospectives
- Copy-paste into any application without formatting issues

## Technical Details

- **Protocol**: Model Context Protocol (MCP)
- **Framework**: FastMCP
- **Language**: Python 3.11+
- **Dependencies**: None (uses stdlib only for tool logic)
- **Architecture**: Stateless, single-function server

## Development

### Running Locally

```bash
# From cuttlefish directory
uv run python -m apps.ai.mcp.cto_tools_server
```

### Testing

```bash
# Run MCP tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_cto_tools.py -v

# Test with MCP Inspector
mcp-inspector uv run python -m apps.ai.mcp.cto_tools_server
```

## Roadmap

Future tools planned:
- `quarterly_planning()` - Strategic planning frameworks
- `technical_debt_review()` - Debt identification and prioritization
- `hiring_interview_guide()` - Engineering interview frameworks
- `incident_postmortem()` - Post-incident review templates

## Support

- **Documentation**: https://app.bwforce.ai/mcp/cto-tools
- **Repository**: https://github.com/tomcounsell/cuttlefish
- **Issues**: https://github.com/tomcounsell/cuttlefish/issues

## License

MIT License - See repository for details

---

Part of the [Cuttlefish AI Integration Platform](https://app.bwforce.ai)
