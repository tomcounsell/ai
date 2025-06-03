# Unified Conversational Development Environment

This is a **production-ready unified conversational development environment** that seamlessly integrates chat conversation with code execution through Claude Code, featuring the Valor Engels AI persona with comprehensive tool integration.

## 🔍 EXPLORE - REQUIRED STEPS

**MANDATORY**: Follow these commands in order to guarantee complete codebase understanding:

```bash
# 1. FIRST: Get complete project structure (REQUIRED)
git ls-files | head -50

# 2. THEN: See all documentation files (READ ALL OF THESE)
find docs -maxdepth 1 -name "*.md"

# 3. NEXT: Check database architecture
find . -name "*.db*" -type f

# 4. FINALLY: Understand current system state
ls -la agents/ mcp_servers/ utilities/
```

## 📖 READ - MANDATORY DOCUMENTATION

**YOU MUST READ THESE FILES IN THIS ORDER**:

1. **`CLAUDE.md`** - Development principles, commands, and architecture overview (READ FIRST)
2. **`docs/agent-architecture.md`** - Core system architecture and patterns (READ SECOND)
3. **`docs/system-operations.md`** - Development workflow and environment setup (READ THIRD)
4. **`docs/tool-development.md`** - Tool creation patterns and MCP integration (READ FOURTH)

**After reading the above**, you will understand:
- How the unified conversational development environment works
- The difference between MCP tools and legacy PydanticAI patterns
- How SQLite serves as the default storage engine
- How Valor Engels persona integrates with Claude Code
- The production-ready optimization components

## 🏗️ Current System Architecture

### Unified Claude Code Integration with MCP Tools
```
/agents/                    # Unified AI system with production optimization
  ├── valor_agent.py         # Entry point with test functions
  ├── valor/                 # MAIN: Valor agent module
  │   ├── agent.py          # Core conversational AI with tool integration
  │   ├── handlers.py       # Telegram message handlers with intent preprocessing
  │   └── persona.md        # Valor Engels persona definition
  └── notion_scout.py        # Notion database query agent

/mcp_servers/              # MCP tool servers for Claude Code integration
  ├── social_tools.py       # Web search, image generation, link analysis
  ├── pm_tools.py           # Workspace-based project queries (Project Manager tools)
  ├── telegram_tools.py     # Conversation history and context
  └── development_tools.py  # Code linting, documentation, test generation

/integrations/              # Enhanced with intent recognition
  ├── ollama_intent.py       # Ollama-based intent classification
  ├── intent_tools.py        # Intent-based tool access control
  ├── intent_prompts.py      # Intent-specific system prompts
  └── telegram/              # Production Telegram integration
      ├── handlers.py        # Message handlers with intent preprocessing
      └── reaction_manager.py # Visual reaction feedback system

/tools/                    # Function tools (legacy PydanticAI integration)
  ├── search_tool.py        # Web search using Perplexity AI
  ├── notion_tool.py        # Workspace-based Notion queries
  ├── image_*.py            # Image generation and analysis
  └── models.py             # Tool infrastructure and base models

/utilities/                 # Shared utilities and database layer
  ├── database.py           # **UNIFIED SQLITE DATABASE** - Main storage layer
  ├── token_tracker.py      # Token usage tracking with SQLite backend
  └── monitoring/           # Production optimization components
      ├── context_window_manager.py    # 97-99% conversation compression
      ├── streaming_optimizer.py       # 2.21s average intervals
      ├── resource_monitor.py          # Automatic cleanup and health scoring
      └── integrated_monitoring.py     # Unified system orchestration

# Database Files
system.db                   # **PRIMARY DATABASE** - Unified SQLite storage
├── token_usage            # AI model usage tracking and cost monitoring
├── links                  # URL analysis and storage
├── projects               # Project metadata
├── hosts                  # AI provider configurations
└── models                 # AI model pricing and metadata
```

### 🗄️ SQLite Database Architecture (DEFAULT STORAGE ENGINE)

**Primary Database**: `system.db` (unified storage for all features)

**Core Tables**:
- **`token_usage`** - AI model usage tracking with cost monitoring
- **`links`** - URL analysis and storage with AI-powered content analysis  
- **`projects`** - Project metadata and configurations
- **`hosts`** - AI provider configurations (Anthropic, OpenAI, Ollama)
- **`models`** - AI model pricing and metadata

**Database Access Pattern**:
```python
from utilities.database import get_database_connection, init_database

# Initialize database (creates all tables)
init_database()

# Get connection to shared database
with get_database_connection() as conn:
    conn.execute("INSERT INTO table_name ...")
```

**For New Features**: Always use `utilities/database.py` as the default storage engine. Add new tables to the `init_database()` function and create corresponding utility functions.

## 🤖 Valor Engels - Unified Conversational Development Interface

### Primary Agent: valor/agent.py
- **Core conversational AI** with comprehensive tool integration
- **Valor Engels persona**: Software engineer specializing in conversational development
- **Production-ready capabilities**: Context management, streaming optimization, error recovery
- **Seamless integration**: Technical discussions, web search, development tasks, general conversation

### MCP Tool Integration Architecture
**Three-Layer Tool System:**
1. **Agent Layer**: PydanticAI tools in `/tools/` (legacy integration)
2. **Implementation Layer**: Core functions with shared business logic
3. **MCP Layer**: Claude Code integration via Model Context Protocol

**Context Injection Strategy:**
Since MCP tools are stateless, context flows through enhanced prompts:
```
CONTEXT_DATA:
CHAT_ID={chat_id}
USERNAME={username}
CONVERSATION_HISTORY={optimized_history}

USER_REQUEST: {message}
```

## 🛠️ MCP Tool Servers (Claude Code Integration)

### social-tools MCP Server
- **Web Search**: Current information via Perplexity AI  
- **Image Generation**: DALL-E 3 with local file management
- **Image Analysis**: GPT-4o vision with context-aware prompting
- **Link Analysis**: Automatic URL analysis and storage
- **Technical Analysis**: Complex research delegation to Claude Code

### pm-tools MCP Server (Project Manager)
- **Workspace-based Queries**: Automatic project detection via working directory
- **Notion Integration**: Real-time database queries with AI analysis
- **Security**: Chat-to-workspace mappings ensure data isolation
- **Fresh Data**: Always gets latest information from Notion

### telegram-tools MCP Server
- **Conversation History**: Search and context retrieval
- **Recent Context**: Extended conversation summaries
- **Dialog Management**: List and manage Telegram conversations

### development-tools MCP Server
- **Code Linting**: Python linting with ruff, black, mypy
- **Documentation**: Summarize and analyze code documentation
- **Image Analysis**: Technical image assessment and tagging
- **Test Generation**: AI-powered test parameter generation

## 🚀 Production-Ready Capabilities

### Performance Optimization
- **Context Intelligence**: 97-99% conversation compression while preserving critical information
- **Streaming Performance**: 2.21s average intervals with adaptive rate control
- **Resource Management**: Automatic cleanup, health scoring, production alerts
- **Multi-user Support**: Concurrent sessions with error recovery

### Current Production Metrics
- **Performance**: <2s response latency, 2-3s streaming intervals
- **Reliability**: >95% tool success rate, automatic error recovery
- **Context Efficiency**: 5.8ms optimization for 1000→21 message compression
- **Health Monitoring**: Real-time system validation with self-ping capability

### Telegram Integration Features
- **Intent Recognition**: Ollama-based message classification with valid emoji reactions
- **Whitelist System**: Username and user ID-based access control
- **Enhanced Error Handling**: Detailed logging and graceful degradation
- **Database Lock Prevention**: Proactive session cleanup and concurrent limits

## 🔧 Development Commands

### System Management
```bash
# Start unified system (recommended)
scripts/start.sh          # FastAPI + Telegram with auth validation

# Stop system with cleanup
scripts/stop.sh

# Update MCP configuration
scripts/update_mcp.sh
```

### Agent Execution
```bash
# Test unified agent
uv run agents/valor_agent.py

# Query Notion projects
uv run agents/notion_scout.py --project PsyOPTIMAL "What tasks are ready for dev?"

# Run comprehensive demo
scripts/demo_agent.sh
```

### Testing Production Readiness
```bash
# Production performance validation
python tests/test_performance_comprehensive.py

# End-to-end integration tests
python tests/test_production_readiness.py

# Run all tests
cd tests && python run_tests.py
```

## 📋 Workspace Configuration

### Project Mappings (`config/workspace_config.json`)
```json
{
  "workspace_mappings": {
    "PsyOPTIMAL": "database_id_1",
    "FlexTrip": "database_id_2"
  },
  "directory_mappings": {
    "/path/to/project": "workspace_name"
  },
  "telegram_groups": {
    "chat_id": "workspace_name"
  }
}
```

### Environment Requirements
- `ANTHROPIC_API_KEY` - Claude AI conversations
- `OPENAI_API_KEY` - GPT-4o vision and DALL-E 3
- `TELEGRAM_API_ID/HASH` - Telegram integration
- `NOTION_API_KEY` - Project data access
- `PERPLEXITY_API_KEY` - Web search intelligence

## 🎯 Key Architectural Principles

### Unified Conversational Development
- **Seamless Integration**: Chat-to-code execution without boundaries
- **Context Intelligence**: Smart conversation optimization with critical information preservation
- **Tool Orchestration**: LLM automatically selects optimal tools based on conversation flow
- **Real-time Streaming**: Live progress updates during development tasks

### Production-Ready Design
- **No Legacy Code Tolerance**: Complete elimination of obsolete patterns
- **Critical Thinking Mandatory**: Deep analysis over quick fixes
- **Intelligent Systems**: LLM intelligence over rigid keyword matching
- **Mandatory Commit Workflow**: Always commit and push changes

### Three-Layer Tool Architecture
1. **MCP Integration**: Primary tool access via Claude Code
2. **Legacy PydanticAI**: Transitional agent-integrated tools
3. **Core Implementation**: Shared business logic across all interfaces

## 💡 Current State Summary

**Primary Interface**: Claude Code with MCP tool integration
**Core Agent**: Valor Engels conversational development persona  
**Architecture**: Unified system with production optimization
**Performance**: 97-99% context compression, 2.21s streaming, >95% success rates
**Integration**: Telegram, Notion, web search, image generation/analysis
**Testing**: Comprehensive production validation with performance benchmarks

This system represents a **production-ready unified conversational development environment** where technical work seamlessly flows through natural conversation, powered by Claude Code integration and optimized for real-world deployment.

---
*This primer reflects the actual production system architecture as of the current implementation.*