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

### Claude Desktop

Add to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cto-tools": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/cuttlefish",
        "run",
        "python",
        "-m",
        "apps.ai.mcp.cto_tools_server"
      ]
    }
  }
}
```

### Web Manifest

Access the web manifest at: `https://ai.yuda.me/mcp/cto-tools/manifest.json`

## Available Tools

### weekly_review()

Provides a comprehensive framework for conducting weekly engineering team reviews.

**Purpose**: Systematically review your development team's work to understand accomplishments, identify blockers, recognize contributions, and plan priorities.

**Returns**: Step-by-step instructions including:
- Git commands for extracting commit history
- Framework for categorizing and analyzing work
- Templates for generating executive summaries
- Team recognition guidelines
- Action item checklist

**Example Usage**:

```
Claude, use CTO Tools to help me run a weekly review of my team's work.
```

The tool will provide detailed instructions for:
1. Gathering commit history data
2. Analyzing patterns and metrics
3. Generating executive summaries
4. Recognizing team contributions
5. Defining action items

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
- Extract commit history from the past week
- Categorize work (features, bugs, refactoring, etc.)
- Generate executive summary for stakeholders
- Identify blockers and risks
- Plan next week's priorities

### Fast Reviews (15 minutes)
Focus on highlights, key metrics, and blockers only.

### Deep Reviews (60 minutes)
Include full categorization, pattern analysis, and individual deep-dives.

### Board/Executive Updates
Lead with business value delivered, risks identified, and resource needs.

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
