# AI Agent System

A comprehensive AI agent system built with **PydanticAI** for intelligent tool orchestration and conversation management.

## 🚀 Quick Start

```bash
# Install dependencies
uv pip install -r requirements.txt

# Test the main Telegram chat agent
uv run agents/telegram_chat_agent.py

# Run comprehensive demo (background)
scripts/demo_agent.sh

# Monitor demo progress
tail -f logs/agent_demo.log
```

## 🤖 Architecture

### PydanticAI Agent System
- **Telegram Chat Agent**: Main conversational AI with Valor Engels persona
- **Function Tools**: Web search, Notion queries, and extensible tool ecosystem
- **Message History**: Conversation continuity through context injection
- **Type Safety**: Full Pydantic validation and schema generation

### Key Components

```
/agents/                    # PydanticAI agents
  ├── telegram_chat_agent.py # Main Telegram conversation agent
  └── valor_agent.py         # Standalone agent example

/tools/                     # PydanticAI function tools
  └── search_tool.py         # Web search using Perplexity AI

/integrations/              # External service connections
  ├── telegram/             # Telegram bot integration
  └── notion/               # Project data queries

/tests/                     # Agent testing and validation
  ├── test_agent_quick.py   # Quick functionality tests
  └── test_agent_demo.py    # Comprehensive conversation demos
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
# Quick agent functionality test
python tests/test_agent_quick.py

# Comprehensive conversation demo
python tests/test_agent_demo.py

# Background demo execution
scripts/demo_agent.sh
```

## 📋 Features

### ✅ Implemented
- **PydanticAI Integration**: Complete migration from direct API calls
- **Intelligent Tool Selection**: LLM chooses appropriate tools automatically  
- **Conversation Continuity**: Message history integration
- **Web Search Tool**: Current information through Perplexity AI
- **Valor Engels Persona**: Consistent character with technical expertise
- **Type Safety**: Full Pydantic validation throughout
- **Comprehensive Testing**: Quick tests and full conversation demos

### 🔮 Next Steps
- **Notion Tool**: Convert existing integration to PydanticAI function tool
- **Code Execution Tool**: Integrate with development workflows
- **Multi-Agent Workflows**: Agent collaboration and orchestration
- **Enhanced Context**: Persistent conversation memory

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