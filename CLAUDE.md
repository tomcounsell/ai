# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**IMPORTANT CONTEXT**: When working with this codebase, you ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself - asking about "your" features, "your" capabilities, "your" daydreaming logic, etc. Respond as the embodiment of this AI system, not as an external assistant working on it.

## Common Development Commands

### Running the System

```bash
# Start the production server with startup checks
./scripts/start.sh

# Start the demo server (no API keys required)
./scripts/start.sh --demo

# Start the Telegram bot
./scripts/start.sh --telegram

# Validate configuration without starting
./scripts/start.sh --dry-run

# Start with verbose logging
./scripts/start.sh --verbose

# Shutdown cleanly
./scripts/stop.sh

# Alternative: Run Python scripts directly
python demo_server.py        # Demo server
python scripts/startup.py    # Production startup
python telegram_bot.py       # Telegram bot
python scripts/shutdown.py   # Clean shutdown
```

### Monitoring Logs

```bash
# Tail all logs (default - follows updates)
./scripts/logs.sh

# Tail specific logs
./scripts/logs.sh --main       # Main application logs only
./scripts/logs.sh --startup    # Startup logs only
./scripts/logs.sh --telegram   # Telegram-related logs only
./scripts/logs.sh --errors     # Error messages only

# Custom options
./scripts/logs.sh -n 100       # Show last 100 lines
./scripts/logs.sh --no-follow   # Don't follow updates

# Typical workflow
./scripts/start.sh              # Start the system
./scripts/logs.sh               # In another terminal, tail the logs
```

### Testing

```bash
# Run all tests
pytest tests/

# Run specific test categories
pytest tests/unit/
pytest tests/integration/
pytest tests/performance/

# Run with coverage
pytest tests/ --cov=. --cov-report=html

# Run the comprehensive test runner with AI judge
python tests/test_runner.py

# Run a specific test file
pytest tests/test_agents.py -v
```

### Code Quality

```bash
# Format code with Black
black .

# Check code style with Ruff
ruff check .

# Type checking with mypy
mypy . --strict

# Run all quality checks
black . && ruff check . && mypy . --strict
```

### Database Management

```bash
# Initialize database
python scripts/init_db.py

# Run database maintenance
python scripts/db_maintenance.py

# Backup database (automatic on startup if configured)
python scripts/startup.py --backup
```

## System Architecture

### Core Components

The system follows a **Living Codebase** philosophy with these key architectural components:

1. **Unified Agent System** (`agents/`)
   - Valor persona: Advanced AI assistant with comprehensive capabilities
   - Context management: Maintains conversation state across 100k+ token windows
   - Tool orchestration: Seamless integration of multiple tools and APIs
   - Built on PydanticAI for robust type safety

2. **MCP Server Integration** (`mcp_servers/`)
   - Stateless tool architecture following Model Context Protocol
   - Development tools: Code execution, linting, testing
   - PM tools: GitHub, Linear, documentation management
   - Social tools: Search, calendar, content creation
   - Telegram tools: Message management, reactions, history

3. **Message Processing Pipeline** (`integrations/telegram/`)
   - 5-step processing: Security → Type routing → Context → Response → Delivery
   - 91% complexity reduction through component isolation
   - Graceful error handling and recovery

4. **Tool Ecosystem** (`tools/`)
   - Quality framework: 9.8/10 gold standard implementation
   - Test judge: AI-powered test quality assessment
   - Search, image generation, knowledge management tools
   - All tools follow intelligent context-aware patterns (no keyword matching)

5. **Monitoring & Operations** (`utilities/monitoring/`)
   - Resource monitoring with auto-restart capabilities
   - Health scoring system (0-100 scale)
   - Alert management and metrics dashboard
   - Daydream system for autonomous analysis

### Key Design Patterns

- **No Mocks Testing**: Always use real integrations and APIs in tests
- **Context Injection**: All tools receive full conversation context
- **Stateless Tools**: Tools don't maintain state between calls
- **Error Categorization**: Structured error handling with recovery strategies
- **Component Isolation**: Each component is independently testable

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

## Testing Philosophy

### Real Integration Testing
- **Do not write tests that mock real libraries and APIs. Use the actual library and actual API**
- Focus on testing real integrations and end-to-end functionality
- Test the happy path thoroughly; edge cases are secondary
- Use actual services (Notion, Perplexity, Claude) rather than mocks when possible
- When you write a test, run the test to view results
- **Don't be tempted to simplify tests to get them working. Don't take shortcuts or cheat**

### Test Categories
- **Unit Tests**: Component isolation and function verification
- **Integration Tests**: Real API and service integration
- **Performance Tests**: Load testing and resource optimization
- **E2E Tests**: Complete workflow validation

## Environment Configuration

### Required Environment Variables

```bash
# Telegram Configuration (optional)
TELEGRAM_API_ID=***
TELEGRAM_API_HASH=***
TELEGRAM_PHONE=***
TELEGRAM_PASSWORD=***
TELEGRAM_ALLOWED_GROUPS=***
TELEGRAM_ALLOW_DMS=***

# API Keys (for full functionality)
OPENAI_API_KEY=***
ANTHROPIC_API_KEY=***
PERPLEXITY_API_KEY=***

# Database
DATABASE_PATH=data/ai_rebuild.db
DATABASE_BACKUP_ON_STARTUP=true
DATABASE_RUN_MIGRATIONS=true

# Monitoring
MONITORING_ENABLED=true
MONITORING_DASHBOARD_PORT=8080
MONITORING_ALERT_EMAIL=admin@example.com
```

### Configuration Files
- `config/workspace_config.json`: Multi-workspace configuration
- `.env`: Environment variables and API keys
- `pyproject.toml`: Python dependencies and project metadata

## Special Considerations

### Telegram Integration
- If Telegram auth doesn't work, just give up. Human action is needed
- Session files are stored in `data/` directory
- Auto-authentication with phone/password if configured
- **2FA Authentication**: When starting the Telegram bot with `./scripts/start.sh --telegram`, you will be prompted for your 2FA code if enabled
- The script runs in foreground mode to allow interactive input during authentication

#### Telegram Setup & Usage
```bash
# First-time authentication (one-time only)
./scripts/telegram_login.sh
# This will prompt for verification code and save the session

# Normal operation (uses saved session)
./scripts/start.sh --telegram
# Bot runs continuously, handling messages

# Check authentication status
python scripts/telegram_auth.py
# Shows if session is valid
```

**Current Status**: ✅ Authenticated as Valor (@valorengels) - Session is saved and ready to use.

### Server Development
- Always use startup/shutdown scripts for clean state management
- Database is automatically backed up on startup if configured
- Resource monitoring includes auto-restart on failure

### Quality Standards
- All new tools must meet 9.8/10 quality standard
- Comprehensive error handling with categorized errors
- Performance metrics tracked for all operations
- Documentation required for all public interfaces
- after every fix, restart the server
- There are API keys in the .env file.