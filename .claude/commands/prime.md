# AI Project Context Primer

This is an AI research and development project focused on building programmable agents, tools, scripts, and integrations.

## 🏗️ Project Structure

```
/agents/                    # UV scripts for AI agents
  └── notion_scout.py      # Notion database query agent with Claude analysis

/integrations/             # External service integrations
  └── notion/
      └── database_mapping.json  # Project name to database ID mappings

/scripts/                  # Development and automation scripts
  ├── start.sh            # FastAPI server start with hot reload
  ├── stop.sh             # FastAPI server stop with cleanup
  └── update_mcp.sh       # MCP configuration generator

/apps/                     # Core application modules
  ├── agent/              # Agent framework (foxtrot, delta, whiskey, numenta)
  ├── data/               # Data handling modules
  ├── stimulus/           # Input processing
  └── structures/         # Data structures

/utilities/                # Shared utility functions
/main.py                  # Minimal FastAPI server (bare bones)
/requirements/base.txt    # FastAPI, uvicorn, pydantic, pydanticai deps
```

## 🤖 Key Components

### Notion Scout Agent
- **Location**: `agents/notion_scout.py`
- **Purpose**: Intelligent Notion database querying with Claude analysis
- **Usage**: `uv run agents/notion_scout.py --project PsyOPTIMAL "What tasks are ready for dev?"`
- **Features**: 
  - Project-based filtering (PsyOPTIMAL, FlexTrip)
  - Aliases support (psy, flex)
  - Claude-powered priority analysis
  - Actionable development recommendations

### Database Mapping System
- **Location**: `integrations/notion/database_mapping.json`
- **Maps**: Friendly project names → Notion database IDs
- **Projects**: PsyOPTIMAL, FlexTrip
- **Aliases**: psy, optimal, flex, trip

### Server Management
- **Start**: `scripts/start.sh` (FastAPI with uvicorn hot reload)
- **Stop**: `scripts/stop.sh` (cleanup with orphan process handling)
- **MCP**: `scripts/update_mcp.sh` (generates .mcp.json from .env)

## 🔧 Development Patterns

### UV Scripts
- All agents use UV script format with inline metadata
- Dependencies specified in script headers
- Executable with `uv run` command
- Rich console output (no ASCII borders)

### Environment Configuration
- `.env` file with API keys (Anthropic, OpenAI, Notion)
- `.env.example` template with placeholder formats
- MCP auto-configuration from environment

### Agent Architecture
- Modular design in `/apps/agent/`
- Multiple agent types (foxtrot, delta, whiskey, numenta)
- Agent cooperation and fitness systems

## 🎯 Recent Accomplishments

1. **Created Notion Scout**: Intelligent database querying agent
2. **Established UV script pattern**: Self-contained, executable agents
3. **Built mapping system**: User-friendly project name resolution
4. **Implemented Claude integration**: Anthropic API for analysis
5. **Organized integrations**: Proper separation of external service configs
6. **Streamlined server management**: Simple start/stop scripts

## 🚀 Current Capabilities

- **Intelligent Querying**: Ask complex questions about project status
- **Priority Analysis**: AI-powered task prioritization and recommendations  
- **Project Isolation**: Query specific databases by friendly names
- **Development Focus**: "What's the highest priority task ready for dev?"
- **Status Tracking**: Monitor progress across multiple projects

## 🔮 Potential Next Steps

- Expand agent capabilities (GitHub, Slack, other integrations)
- Build agent cooperation workflows
- Enhance MCP server configurations
- Create more UV script agents for different tasks
- Implement agent fitness and performance tracking

## 💡 Key Insights

- UV scripts provide excellent self-contained agent distribution
- Claude excels at intelligent analysis of structured data
- Project mapping enables intuitive natural language interaction
- Clean, borderless console output ensures cross-platform compatibility
- Modular architecture supports rapid agent development

---
*Use this context to understand the current state and continue building agents, tools, scripts, and integrations in this AI development platform.*