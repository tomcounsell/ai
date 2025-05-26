# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Dependency Management
```bash
# Compile dependencies from base requirements
uv pip compile requirements/base.txt -o requirements.txt

# Create virtual environment
uv venv

# Install dependencies
uv pip install -r requirements.txt
```

### Server Management
```bash
# Start FastAPI development server with hot reload
scripts/start.sh

# Stop server and cleanup processes
scripts/stop.sh

# Update MCP configuration from .env
scripts/update_mcp.sh
```

### Agent Execution
```bash
# Run UV script agents directly
uv run agents/notion_scout.py --project PsyOPTIMAL "What tasks are ready for dev?"
uv run agents/notion_scout.py --project FlexTrip "Show me project status"

# Available project aliases: psy, optimal, flex, trip
uv run agents/notion_scout.py --project psy "Quick status check"
```

### Testing
```bash
# Run chat history and duplication tests
python tests/test_chat_history.py

# Run Valor conversation evaluation tests (requires OpenAI API key)
python tests/test_valor_conversations.py

# Run comprehensive end-to-end test suite with LLM evaluation
python tests/run_e2e_tests.py

# Run all tests
cd tests && python run_tests.py
```

## Architecture Overview

### UV Script Agent Pattern
This codebase uses UV scripts as the primary pattern for creating self-contained, executable agents. Each agent:
- Contains inline dependency metadata in script headers
- Uses rich console output without ASCII borders for cross-platform compatibility
- Implements specific integrations (Notion, GitHub, etc.)
- Can be executed directly with `uv run` without environment setup

### Integration System
External service integrations are organized under `/integrations/`:
- `/integrations/telegram/` - Complete Telegram bot architecture with message routing
- `/integrations/notion/` - Project data queries with NotionScout + database mapping
- `/integrations/search/` - Web search using Perplexity AI for intelligent responses
- Supports aliases for convenient access (e.g., "psy" → "PsyOPTIMAL")
- Clean separation between integrations and core logic

#### Current Integration Capabilities:
- **Telegram Bot**: Valor Engels persona with chat history and @mention support
- **Notion Queries**: Project status, task management, database insights
- **Web Search**: Perplexity AI integration for current information
- **Keyword Routing**: Intelligent message routing to appropriate handlers

### Server Architecture
- Minimal FastAPI server (`main.py`) with basic health endpoints
- Designed for extension, not as a monolithic application
- Server management scripts handle PID tracking and orphaned process cleanup
- Hot reload enabled for development

### Project Structure Philosophy
- `/agents/` - UV script agents for specific tasks
- `/integrations/` - External service configurations and mappings  
- `/scripts/` - Development and automation scripts
- `/apps/` - Core framework modules (can be ignored for agent development)

### MCP Integration
- Uses Model Context Protocol for Claude Code tool access
- Auto-generates `.mcp.json` configuration from environment variables
- Supports Notion API integration out of the box

### Environment Configuration
- `.env` file contains API keys (Anthropic, OpenAI, Notion, Telegram, Perplexity)
- `.env.example` provides template with proper placeholder formats
- Environment variables drive MCP server configuration

#### Required API Keys:
- `ANTHROPIC_API_KEY` - For Claude AI conversations and analysis
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` - For Telegram bot functionality
- `NOTION_API_KEY` - For project data integration
- `PERPLEXITY_API_KEY` - For intelligent web search (optional)

#### Search Integration:
The Perplexity search integration provides intelligent web search through Telegram:
- Trigger: `search [query]`, `find [info]`, `lookup [topic]`
- Example: `search latest AI developments 2024`
- Returns: AI-synthesized answers with current web information

## Valor - AI Assistant Persona
**Valor Engels** refers to the Telegram bot implementation with a complete persona:
- Software engineer at Yudame with German/Californian background
- Handles technical questions, Notion queries, and general conversation
- Maintains persistent chat history across server restarts
- Responds to @mentions in groups and all messages in direct chats
- Smart catch-up handling for offline periods with batched responses
- Context-aware priority checking using chat history and Notion data
- Technical persona focused on implementation details and requirements clarification

## Integration Development Patterns

### Current Architecture: Integrations → Tools → Agents
The system evolves from direct integrations to a full agent system:

**Phase 1 (Current)**: Direct integrations with keyword routing
- Create integration classes in `/integrations/{service}/`
- Implement detection functions (e.g., `is_search_query()`)
- Add routing logic to Telegram handlers
- Focus on reliable, working functionality

**Phase 2 (Next)**: Tool registry system
- Convert integrations to tools with capability definitions
- Implement agent base classes with tool access
- Build HG Wells as first intelligent agent

**Phase 3 (Future)**: Multi-agent workflows
- Agent collaboration and response aggregation
- Advanced intent classification
- Cross-conversation context management

### Creating New Integrations
1. Create integration directory: `/integrations/{service}/`
2. Implement main integration class with async methods
3. Add detection utilities in `utils.py`
4. Update Telegram message handlers for routing
5. Add environment configuration to `.env.example`
6. Test integration independently before Telegram integration

### Integration Mappings
When adding new integrations, create mapping files in `/integrations/{service}/` to translate user-friendly names to service-specific identifiers.

### Error Handling
Integrations should provide clear, actionable error messages and gracefully handle missing configuration or API connectivity issues.