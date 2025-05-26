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
- `/integrations/notion/database_mapping.json` - Maps friendly project names to Notion database IDs
- Supports aliases for convenient access (e.g., "psy" → "PsyOPTIMAL")
- Separates integration configuration from agent logic

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
- `.env` file contains API keys (Anthropic, OpenAI, Notion)
- `.env.example` provides template with proper placeholder formats
- Environment variables drive MCP server configuration

## Valor - AI Assistant Persona
**Valor Engels** refers to the Telegram bot implementation with a complete persona:
- Software engineer at Yudame with German/Californian background
- Handles technical questions, Notion queries, and general conversation
- Maintains persistent chat history across server restarts
- Responds to @mentions in groups and all messages in direct chats
- Smart catch-up handling for offline periods with batched responses
- Context-aware priority checking using chat history and Notion data
- Technical persona focused on implementation details and requirements clarification

## Agent Development Patterns

### Creating New Agents
1. Use UV script format with inline dependencies
2. Implement rich console output (no borders)
3. Support project-based filtering where applicable
4. Place integration configs in `/integrations/`
5. Use Claude/Anthropic API for intelligent analysis

### Integration Mappings
When adding new integrations, create mapping files in `/integrations/{service}/` to translate user-friendly names to service-specific identifiers.

### Error Handling
Agents should provide clear, actionable error messages and gracefully handle missing configuration or API connectivity issues.