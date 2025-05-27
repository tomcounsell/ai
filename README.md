# AI Agent System

A comprehensive AI agent system built with **PydanticAI** for intelligent tool orchestration and conversation management.

## 🚀 Quick Start

```bash
# Setup environment
uv venv
uv pip install -r requirements.txt

# Start development server
scripts/start.sh

# Test agents directly
uv run agents/valor_agent.py
uv run agents/notion_scout.py --project psy "What tasks are ready for dev?"

# Run comprehensive tests
python tests/run_tests.py
```

## 🤖 Architecture

### PydanticAI Agent System
- **Valor Agent**: Main conversational AI with Valor Engels persona and comprehensive tool suite
- **Function Tools**: Web search, Notion workspace queries, Claude Code delegation, image tools, and extensible ecosystem
- **Message History**: Conversation continuity through context injection
- **Type Safety**: Full Pydantic validation and schema generation

### Key Components

```
/agents/                    # PydanticAI agents
  ├── valor_agent.py         # Entry point with test functions
  ├── valor/                # MAIN: Valor agent structured module
  │   ├── agent.py          # Core agent with ALL tools integrated
  │   ├── handlers.py       # Telegram message handlers
  │   └── persona.md        # Valor Engels persona definition
  └── notion_scout.py        # Notion database query agent

/tools/                     # PydanticAI function tools
  ├── search_tool.py         # Web search using Perplexity AI
  ├── notion_tool.py         # Workspace-based Notion queries
  ├── claude_code_tool.py    # Code delegation capabilities
  ├── image_*.py             # Image generation and analysis
  └── models.py              # Tool infrastructure and base models

/integrations/              # External service connections
  ├── telegram/             # Telegram bot with chat history
  └── notion/               # Project data queries and database mapping

/tests/                     # Comprehensive testing suite
  ├── test_chat_history.py  # Chat history management tests
  ├── test_valor_conversations.py # Conversation flow tests
  └── run_tests.py          # Test runner with environment setup

/docs/                      # Architecture documentation
  ├── agent-architecture.md # Current PydanticAI implementation
  ├── tool-development.md   # Tool creation patterns
  └── future-plans.md       # Multi-agent vision and roadmap
```

## 🛠️ Tool Development

Create tools as simple functions:

```python
def search_web(query: str) -> str:
    """Search the web and return AI-synthesized answers."""
    return search_result

@agent.tool
def my_tool(ctx: RunContext[ContextType], param: str) -> str:
    """Tool description for LLM to understand when to use it."""
    return my_tool_function(param)
```

**Benefits:**
- LLM automatically selects appropriate tools
- No manual routing or keyword detection needed
- Type-safe with automatic schema generation
- Simple testing and validation

## 🧪 Testing

```bash
# Run all tests with environment setup
python tests/run_tests.py

# Individual test suites
python tests/test_chat_history.py          # Chat history management
python tests/test_valor_conversations.py   # Conversation flow validation
python tests/test_agent_quick.py          # Quick functionality tests

# Agent testing directly
uv run agents/telegram_chat_agent.py      # Test Telegram agent
uv run agents/notion_scout.py --project psy "Status check"  # Test Notion queries

# Server testing
scripts/start.sh                          # Start development server
python main.py & PID=$! && sleep 3 && curl http://localhost:9000/health && kill $PID
```

## 📋 Features

### ✅ Implemented
- **PydanticAI Agent System**: Complete migration with function tools
- **Telegram Integration**: Chat history, persona, and conversation continuity
- **Notion Scout Agent**: Project data queries with database mapping
- **Web Search Tool**: Current information through Perplexity AI
- **Valor Engels Persona**: Consistent character with Claude Code tool usage
- **Chat History Management**: Duplicate prevention and context formatting
- **Type Safety**: Full Pydantic validation and tool infrastructure
- **Comprehensive Testing**: Chat history, conversation flow, and agent validation
- **Documentation Suite**: Architecture, tool development, and future planning

### 🔮 Next Steps
- **Tool Ecosystem Expansion**: Code execution, file operations, and development tools
- **Multi-Agent Workflows**: Agent collaboration and orchestration (see docs/future-plans.md)
- **Enhanced Context**: Persistent conversation memory across sessions
- **Claude Code Integration**: Advanced tool delegation and workflow automation

## 🎯 Agent Capabilities

**Valor Engels** - Software Engineer at Yudame:
- Technical discussions with implementation focus
- Current technology information through web search
- Project context awareness and priority management
- Natural conversation with German/Californian background
- Real work references (never fabricated activities)

**Tool Orchestration**:
- Search triggered automatically for current information requests
- Context-aware tool selection based on conversation flow
- Seamless integration of tool results into responses
- Type-safe tool execution with error handling

## 🔧 Configuration

Required environment variables:
- `ANTHROPIC_API_KEY` - For Claude AI conversations
- `PERPLEXITY_API_KEY` - For web search functionality
- `NOTION_API_KEY` - For project data integration (optional)
- `TELEGRAM_API_ID/HASH` - For Telegram bot (optional)

## 📖 Documentation

See `CLAUDE.md` for complete development documentation including:
- Agent creation patterns
- Tool development guidelines
- Integration strategies
- Testing approaches
- Architecture details

## 🏗️ Research Goals

Continued research in _Artificial Intelligence_, _Synthetic Mind_, _Autonomous Digital Agent_, and _Brain Emulation_:

- Long term planning and autonomous decision making
- Role-based cooperation between agents
- Competitive cooperation dynamics
- Product development automation
- Autonomous corporation management

### Inspirations
- Numenta - 1000 brains theory of intelligence
- MuZero by Deep Mind
- A Cooperative Species by Herbert Gintis and Samuel Bowles
- The Triple Helix by Richard Lewontin

---

**Architecture**: Telegram → PydanticAI Agent → Function Tools → Intelligent Response
