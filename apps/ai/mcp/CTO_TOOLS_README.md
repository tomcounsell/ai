# CTO Tools - MCP Server

Engineering leadership frameworks for AI assistants.

## Overview

CTO Tools is a Model Context Protocol (MCP) server that provides structured frameworks and best practices for common CTO and engineering leadership activities. It helps AI assistants guide engineering leaders through weekly reviews, team analysis, and strategic decision-making.

## Features

- **📊 Weekly Team Reviews**: Structured framework for analyzing team progress
- **🔍 Git Analysis**: Commands and techniques for extracting insights from commit history
- **📝 Executive Summaries**: Templates for stakeholder communication
- **🎯 Action-Oriented**: Clear next steps and decision frameworks

## Installation

### Standalone Single-File Server

The CTO Tools MCP server is a **single executable file** with inline dependencies.
You can run it directly with `uv` without needing the full cuttlefish project.

#### Option 1: Run from URL (Recommended)

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

#### Option 2: Run Local File

Download the file and run it directly:

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

#### Option 3: Run from Cuttlefish Project

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

### Web Manifest

Access the web manifest at: `https://ai.yuda.me/mcp/cto-tools/manifest.json`

## Available Tools

### weekly_review()

Provides a streamlined 3-phase framework for conducting weekly engineering team reviews.

**Purpose**: Systematically review your development team's work to produce a concise summary. Analysis-focused, not prescriptive. Works with ANY codebase and tech stack.

**Returns**: Phase-by-phase instructions for creating a ~200 word summary including:

**Phase 1 - Data Gathering**:
- Git commands to run in parallel for efficient data collection
- Commit history, author statistics, and file changes

**Phase 2 - Internal Analysis**:
- Review commits and identify patterns
- Choose 5 relevant categories that emerge from the work
- Note key stats (commits, files, contributors)
- Suggested categories: AI & ML, Auth & Security, Frontend/UX, Performance, Code Quality, Bug Fixes, Data & Analytics, DevOps, API, Billing, Reporting, Database, Testing

**Phase 3 - Concise Output**:
- Single summary organized by 5 categories (2-5 bullets each)
- Stats section with commit counts and contributor recognition
- Suitable for any communication channel (chat, email, reports)

**Example Usage**:

```
Claude, use CTO Tools to run a weekly review of my team's work.
```

**Features**:
- 📊 Concise output (~200 words) suitable for any channel
- 🎯 Focus on what changed, not lengthy analysis
- 👥 Automatic contributor recognition with commit counts
- 🔍 Internal analysis (using sequential thinking) with brief output
- ⚡ Fast reviews (15-20 min) with clear deliverables

The framework guides you through systematic analysis that produces a concise, scannable summary
of your team's week with category-based organization and contributor recognition.

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

### Weekly Team Reviews
Review your team's progress every Friday:
- Extract commit history from the past 7 days
- Analyze internally to identify 5 key categories
- Generate concise summary (~200 words) with bullets
- Include contributor stats and recognition

### Communication-Ready Output
The summary format is designed for immediate sharing:
- Chat channels (Slack, Teams, Discord)
- Email updates to stakeholders
- Status reports for leadership
- Team retrospectives

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
- `architecture_review()` - Architecture decision frameworks

## Support

- **Documentation**: https://ai.yuda.me/mcp/cto-tools
- **Repository**: https://github.com/tomcounsell/cuttlefish
- **Issues**: https://github.com/tomcounsell/cuttlefish/issues

## License

MIT License - See repository for details

---

Part of the [Cuttlefish AI Integration Platform](https://ai.yuda.me)
