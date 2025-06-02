# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Principles

### Critical Architecture Standards

**1. NO LEGACY CODE TOLERANCE**
- **Never leave behind traces of legacy code or systems**
- **Always overwrite, replace, and delete obsolete code completely**
- When upgrading architectures, eliminate all remnants of old approaches
- Clean removal of deprecated patterns, imports, and unused infrastructure
- No commented-out code, no "temporary" bridges, no half-migrations

**2. CRITICAL THINKING MANDATORY**
- **Foolish optimism is not allowed - always think deeply**
- **Question assumptions, validate decisions, anticipate consequences**
- Analyze trade-offs critically before implementing changes
- Consider edge cases, failure modes, and long-term maintenance
- Prioritize robust solutions over quick fixes
- Validate architectural decisions through comprehensive testing

**3. INTELLIGENT SYSTEMS OVER RIGID PATTERNS**
- **Use LLM intelligence instead of keyword matching**
- **Context-aware decision making over static rule systems**
- Natural language understanding drives system behavior
- Flexible, adaptive responses based on conversation flow
- Future-proof designs that leverage AI capabilities

**4. MANDATORY COMMIT AND PUSH WORKFLOW**
- **ALWAYS commit and push changes at the end of every task**
- **Never leave work uncommitted in the repository**
- Create clear, descriptive commit messages explaining the changes
- Push to remote repository to ensure changes are preserved
- Use `git add . && git commit -m "Description" && git push` pattern
- This ensures all work is properly saved and available for future sessions

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
# Start the complete system (FastAPI server + Telegram client)
# This is the primary way to start the system - handles authentication automatically
scripts/start.sh

# Stop server and cleanup processes
scripts/stop.sh

# Update MCP configuration from .env
scripts/update_mcp.sh
```

**Important:** `scripts/start.sh` is the **unified startup command** that:
- Checks Telegram authentication status
- Prompts for interactive login if needed (phone + verification code)
- **Prevents database locks** with proactive session cleanup
- **Validates system health** with self-ping end-to-end testing
- **Enhanced error handling** with detailed logging and diagnostics
- Starts both FastAPI server and Telegram client together
- Ensures the system is fully operational before completing

### Telegram Authentication (Manual)
```bash
# Manual Telegram authentication (only needed if start.sh fails)
scripts/telegram_login.sh

# Check existing session status
python integrations/telegram/auth.py
```

**Note:** Telegram authentication is automatically handled by `scripts/start.sh`. Manual authentication is only needed for troubleshooting.

### Agent Execution
```bash
# Run UV script agents directly
uv run agents/notion_scout.py --project PsyOPTIMAL "What tasks are ready for dev?"
uv run agents/notion_scout.py --project FlexTrip "Show me project status"

# Available project aliases: psy, optimal, flex, trip
uv run agents/notion_scout.py --project psy "Quick status check"

# Test PydanticAI Telegram chat agent
uv run agents/valor_agent.py

# Run comprehensive agent demo
scripts/demo_agent.sh
```

### Testing
```bash
# Quick agent functionality test
python tests/test_agent_quick.py

# Comprehensive agent demo (runs in background)
scripts/demo_agent.sh

# Monitor demo progress
tail -f logs/agent_demo.log

# Run all tests
cd tests && python run_tests.py
```

## Architecture Overview

### Unified Conversational Development Environment
This codebase implements a **production-ready unified system** with Claude Code integration:

- **Conversational Development**: Seamless chat-to-code execution without boundaries
- **Claude Code Integration**: Direct tool access through MCP (Model Context Protocol)
- **Intelligent Context Management**: Smart conversation optimization with 97-99% compression
- **Performance Optimization**: Real-time streaming, resource monitoring, automatic cleanup
- **Production-Ready**: Comprehensive testing, monitoring, and error recovery
- **Unified Database**: Single SQLite database (`system.db`) for all persistent data storage

### System Architecture
```
/agents/                    # Unified AI system
  ├── valor_agent.py         # Entry point with test functions
  ├── valor/                 # MAIN: Valor agent module
  │   ├── agent.py          # Core conversational AI with tool integration
  │   ├── handlers.py       # Telegram message handlers with intent preprocessing
  │   └── persona.md        # Valor Engels persona definition
  ├── context_window_manager.py    # Intelligent conversation optimization
  ├── streaming_optimizer.py       # Performance-optimized streaming
  ├── resource_monitor.py          # Production monitoring and cleanup
  ├── integrated_monitoring.py     # Unified system orchestration
  └── notion_scout.py              # Notion database query agent

/integrations/              # Enhanced with intent recognition
  ├── ollama_intent.py       # Ollama-based intent classification
  ├── intent_tools.py        # Intent-based tool access control
  ├── intent_prompts.py      # Intent-specific system prompts
  └── telegram/
      ├── handlers.py        # Message handlers with intent preprocessing
      └── reaction_manager.py # Visual reaction feedback system

/mcp_servers/              # MCP tool servers for Claude Code
  ├── social_tools.py       # Web search, image generation, link analysis
  ├── notion_tools.py       # Workspace-based project queries
  └── telegram_tools.py     # Conversation history and context

/tools/                    # Function tools (legacy PydanticAI integration)
  ├── search_tool.py        # Web search using Perplexity AI
  ├── notion_tool.py        # Workspace-based Notion queries
  ├── claude_code_tool.py   # Development task delegation
  ├── image_*.py            # Image generation and analysis
  └── models.py             # Tool infrastructure and base models

/tests/                    # Production-grade testing suite
  ├── test_performance_comprehensive.py  # Performance validation
  ├── test_production_readiness.py       # Production deployment tests
  ├── test_concurrency_recovery.py       # Multi-user and error recovery
  ├── test_context_injection.py          # Context management validation
  └── test_mcp_servers.py                # MCP tool integration tests

/integrations/telegram/     # Telegram integration
  ├── handlers.py          # Unified agent routing with health checks
  └── utils.py             # Message timing utilities
```

### Tool Development Pattern
Create tools as simple functions with proper typing:

```python
def search_web(query: str, max_results: int = 3) -> str:
    """Search the web and return AI-synthesized answers."""
    # Tool implementation
    return search_result

@agent.tool
def my_tool(ctx: RunContext[ContextType], param: str) -> str:
    """Tool description for LLM to understand when to use it."""
    return my_tool_function(param)
```

### Integration System
External service integrations support the agent system:

- `/integrations/telegram/` - Telegram bot with PydanticAI agent integration
- `/integrations/notion/` - Project data queries with database mapping
- `/integrations/search/` - Web search (replaced by PydanticAI tool)

#### Current Integration Capabilities:
- **Unified Conversational Development**: Seamless Claude Code integration with natural language interface
- **Production Performance**: 2.21s streaming intervals, <1ms integration processing, 97% health scores
- **Intelligent Context Management**: 97-99% conversation compression while preserving critical information
- **Real-time Streaming**: Live progress updates during development tasks with adaptive rate control
- **Intent Recognition**: Ollama-based message classification with **valid Telegram reaction emojis** and optimized tool access
- **Web Search Intelligence**: Automatic current information retrieval through Perplexity AI
- **Image Generation**: DALL-E 3 integration with Telegram delivery
- **Image Analysis** ⭐: **GOLD STANDARD** AI vision with GPT-4o (Quality Score: 9.8/10)
  - Context-aware prompting with chat history integration
  - Sophisticated error categorization (API, encoding, file, OSError)
  - Format validation before file operations (efficiency optimization)
  - Perfect test coverage (22/22 tests, 100% success rate)
  - Exemplary architecture serving as reference for other tools
- **Link Analysis**: Automatic URL analysis and storage with AI-powered content analysis
- **Development Integration**: Direct code execution, file operations, and workflow automation
- **Notion Integration**: Workspace-based project query intelligence with AI-powered analysis
- **Resource Monitoring**: Automatic cleanup, health scoring, and production-ready alerts
- **Error Recovery**: Multi-user support with automatic failure handling and graceful degradation

### Server Architecture
- Minimal FastAPI server (`main.py`) with basic health endpoints
- Designed for extension, not as a monolithic application
- Server management scripts handle PID tracking and orphaned process cleanup
- Hot reload enabled for development

### Project Structure Philosophy
- `/agents/` - PydanticAI agents for AI interactions
- `/tools/` - Function tools for agent capabilities and tool infrastructure models
- `/integrations/` - External service configurations and connections
- `/scripts/` - Development and automation scripts
- `/tests/` - Agent testing and validation

### MCP Integration
- **Model Context Protocol**: Direct Claude Code tool access with three MCP servers
- **Auto-configuration**: Generates `.mcp.json` from environment variables
- **Context Injection**: Stateless tool integration with chat data through enhanced prompts
- **Production Integration**: Complete tool suite accessible through conversational interface

### Environment Configuration
- `.env` file contains API keys (Anthropic, OpenAI, Notion, Telegram, Perplexity)
- `.env.example` provides template with proper placeholder formats
- Environment variables drive MCP server configuration

#### Required API Keys:
- `ANTHROPIC_API_KEY` - For Claude AI conversations and analysis
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` - For Telegram bot functionality
- `NOTION_API_KEY` - For project data integration
- `PERPLEXITY_API_KEY` - For intelligent web search

#### Optional Configuration (Multi-Server Deployments):
- `TELEGRAM_ALLOWED_GROUPS` - Comma-separated group chat IDs to handle (whitelist)
- `TELEGRAM_ALLOW_DMS` - Enable/disable DM handling (true/false)

Each group chat can be mapped to a specific Notion database in `config/workspace_config.json`

#### Enhanced Whitelist System:
- **Dual Whitelist Support**: Both username and user ID-based access control in `config/workspace_config.json`
- **Fallback Handling**: User ID support for users without public usernames
- **Self-Ping Capability**: Bot can message itself for end-to-end system validation

## Valor - Unified Conversational Development Environment
**Valor Engels** represents the unified system with a complete technical persona:
- Software engineer at Yudame with German/Californian background specializing in conversational development
- Seamless integration of technical discussions, web search, development tasks, and general conversation
- Intelligent context management with 97-99% conversation optimization while preserving critical information
- Real-time streaming responses with adaptive performance optimization (2.21s average intervals)
- Production-ready capabilities: automatic resource management, error recovery, multi-user support
- Context-aware responses using optimized chat history and intelligent tool orchestration
- Technical expertise with direct code execution, file operations, and workflow automation

## Agent Development Patterns

### Current Architecture: Unified Claude Code Integration with MCP Tools
The system provides a unified conversational development environment through Claude Code with MCP tool integration:

**Unified System Pattern**:
```python
# Claude Code integration with MCP servers
class ValorAgent:
    def __init__(self):
        self.claude_session = ClaudeCodeSession(
            system_prompt=self._build_unified_prompt(),
            mcp_servers=['social-tools', 'notion-tools', 'telegram-tools']
        )
        self.context_manager = ContextWindowManager()
        self.streaming_optimizer = StreamingOptimizer()
        self.resource_monitor = ResourceMonitor()

@mcp.tool()
def tool_function(param: str) -> str:
    """Tool description for Claude Code integration."""
    return tool_implementation(param)
```

**Tool Integration**:
- **MCP Protocol**: Direct Claude Code access to all tools through Model Context Protocol
- **Context Injection**: Enhanced prompts provide chat_id, username, and conversation context
- **Intelligent Selection**: LLM automatically chooses optimal tools based on conversation flow
- **Production Performance**: Tools execute with <1ms integration processing

**Context Management**:
- **Intelligent Optimization**: 97-99% conversation compression while preserving critical information
- **Priority-based Retention**: Smart message filtering with MessagePriority enum
- **Real-time Processing**: 5.8ms context optimization for 1000→21 message compression
- **Production Monitoring**: Automatic resource management and health validation

### Creating New Tools
1. Create MCP tool function in `/mcp_servers/{server_name}.py`
2. Implement with `@mcp.tool()` decorator and proper type hints
3. Add context injection handling for chat_id and username parameters
4. Update `.mcp.json` configuration for Claude Code discovery
5. Test tool through Claude Code interface
6. Validate production performance and error handling

### Creating New System Components
1. Add optimization component in `/agents/{component_name}.py`
2. Integrate with `IntegratedMonitoringSystem` for unified orchestration
3. Implement production monitoring and health validation
4. Add comprehensive testing in `/tests/test_{component_name}.py`
5. Validate performance benchmarks and production readiness
6. Test integration with unified conversational development environment

### Integration Mappings
Service integrations use mapping files in `/integrations/{service}/` to translate user-friendly names to service-specific identifiers.

### Testing Strategy
**PRODUCTION-READY VALIDATION SYSTEM**

**Core Test Philosophy:**
- **Production Performance**: Validate performance benchmarks, resource management, and scalability
- **End-to-End Integration**: Complete conversational development workflows with real-time streaming
- **Intelligence Validation**: LLM-driven tool selection and context-aware decision making

**Test Suite Architecture:**
- `test_performance_comprehensive.py` - **Performance benchmarks and optimization validation**
- `test_production_readiness.py` - **Production deployment and environment validation**
- `test_concurrency_recovery.py` - **Multi-user support and error recovery testing**
- `test_context_injection.py` - **Context management and optimization validation**
- `test_mcp_servers.py` - **MCP tool integration and functionality testing**

**Production Test Categories:**
- **Performance Validation**: Response latency <2s, streaming 2-3s intervals, tool success >95%
- **Context Intelligence**: 97-99% compression validation while preserving critical information
- **Resource Management**: Memory efficiency, automatic cleanup, health scoring
- **Concurrency Testing**: 50+ simultaneous users with error recovery
- **Integration Testing**: MCP tools, Claude Code, Telegram streaming
- **Optimization Testing**: Adaptive streaming, context window management, resource monitoring

## Development Philosophy

### Code Quality Standards
- **Never support backward compatibility** - Always implement the latest, cleanest approach
- **Never mock responses** - Use real integrations and services; if unavailable, fail gracefully
- **Always write optimistic logic** - Implement the main successful path first and foremost
- **Very crude exception handling** - Simple try/catch with basic error messages, no complex fallbacks
- **Single main path** - Avoid multiple branches, fallbacks, or "defensive" programming patterns

### Testing Approach
- Focus on real integrations and end-to-end functionality
- Test the happy path thoroughly; edge cases are secondary
- Use actual services (Notion, Perplexity, Claude) rather than mocks when possible

## Important Notes
**UNIFIED SYSTEM ARCHITECTURE FACTS:**
- **Conversational development environment** - seamless chat-to-code execution without boundaries
- **Claude Code primary interface** - enhanced with MCP tool integration and Valor persona
- **Production-ready performance** - comprehensive monitoring, optimization, and error recovery
- **Context intelligence** - 97-99% conversation compression while preserving critical information
- **Real-time streaming** - adaptive rate control with 2.21s average intervals

**TECHNICAL IMPLEMENTATION:**
- **Claude Code integration** with Model Context Protocol for direct tool access
- **Context injection** provides chat data to stateless MCP tools through enhanced prompts
- **Intelligent optimization** with context window management, streaming rate control, and resource monitoring
- **Production monitoring** with automatic cleanup, health scoring, and comprehensive metrics
- **Multi-user reliability** with concurrent session support and error recovery
- **Database lock prevention** with proactive session cleanup and concurrent transmission limits
- **Enhanced error handling** with detailed logging and empty error message fixes
- **Valid Telegram reactions** using only confirmed working emoji reactions
- **Self-ping validation** for end-to-end system health verification
- **Performance validation** through comprehensive test suites covering all production requirements

## Documentation References

### Core System Documentation
- **`docs/agent-architecture.md`** - PydanticAI foundation, current agent system, and development patterns
- **`docs/tool-development.md`** - Complete guide for creating and integrating tools with best practices
- **`docs/telegram-integration.md`** - Telegram interface, Valor Engels persona, and message handling
- **`docs/message-handling.md`** - Complete step-by-step message processing flow and multi-server configuration
- **`docs/ollama-intent-recognition.md`** - Comprehensive guide to the intent recognition system with examples and flows
- **`docs/system-operations.md`** - Development workflow, environment setup, and deployment guidance
- **`docs/testing-strategy.md`** - Current testing infrastructure, frameworks, and validation methods
- **`docs/teamwork-personas.md`** - Current persona implementation and collaboration framework

### Strategic Planning
- **`docs/plan/future-plans.md`** - Long-term architectural vision and multi-agent system evolution

### Agent Configuration Files
- **`agents/valor/persona.md`** - Valor Engels persona definition with Claude Code tool usage guidelines

### Integration Configuration Files
- **`config/workspace_config.json`** - Consolidated workspace configuration with project mappings, Telegram groups, and directory restrictions

These documents provide comprehensive guidance for understanding, developing, testing, and operating the AI agent system.

## Quick Reference for Common Tasks

### Understanding the System
- Read `docs/agent-architecture.md` for overall system design
- Check `docs/tool-development.md` for tool creation patterns
- Review `agents/valor/persona.md` for Valor Engels behavioral guidelines

### Development Tasks
- Follow `docs/system-operations.md` for server management and environment setup
- Use `docs/testing-strategy.md` for validation approaches
- Reference `docs/telegram-integration.md` for message handling patterns
- Review `docs/message-handling.md` for detailed message processing flow and multi-server setup

### Planning and Architecture
- Consult `docs/plan/future-plans.md` for long-term system evolution plans
- Review `docs/teamwork-personas.md` for multi-agent collaboration concepts
- Check `/agents/` for production optimization and monitoring components

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
