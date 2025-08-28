# Model Context Protocol (MCP) Integration Architecture

## Overview

The Model Context Protocol (MCP) integration represents the core tool infrastructure that enables Claude Code to seamlessly access capabilities through a standardized, discoverable interface. This architecture implements a sophisticated three-layer design that separates concerns while maintaining context flow through stateless tools.

## MCP Foundation

### What is MCP?

Model Context Protocol is an emerging standard for exposing tools and capabilities to AI systems. It provides:
- **Standardized Interface**: Consistent tool discovery and invocation
- **Language Agnostic**: Servers can be implemented in any language
- **Stateless Design**: Cloud-native architecture principles
- **Auto-Discovery**: Tools are automatically available to Claude Code

### Why MCP Was Chosen

1. **Native Claude Code Integration**: MCP is the protocol Claude Code uses natively, enabling seamless tool usage without custom adapters

2. **Future-Proof Architecture**: As an emerging standard, MCP positions the system for compatibility with future AI systems

3. **Composability**: Mix and match tools from different sources (npm packages, custom servers) in a unified interface

4. **Separation of Concerns**: Clean boundary between tool implementation and AI interface

5. **Development Velocity**: Rapid tool development without modifying core agent code

### Stateless Tool Design

MCP tools are inherently stateless, requiring context injection for conversation awareness:

```python
# Stateless MCP tool signature
@mcp.tool()
def search_current_info(query: str, max_results: int = 3) -> str:
    """Tool has no inherent context about the conversation"""
    # Must receive context through parameters or other means
```

This stateless design enables:
- Horizontal scaling without session affinity
- Simple deployment and testing
- Clear input/output contracts
- Predictable behavior

### Server Discovery and Communication

```
┌────────────────────────────────────────────────┐
│              Claude Code Session               │
│                                                │
│  1. Reads .mcp.json configuration              │
│  2. Spawns MCP server processes                │
│  3. Discovers available tools via protocol     │
│  4. Invokes tools with parameters              │
└────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────┐
│                  .mcp.json                     │
│                                                │
│  {                                             │
│    "mcpServers": {                             │
│      "social-tools": {                         │
│        "command": "python",                    │
│        "args": ["mcp_servers/social_tools.py"]│
│      }                                         │
│    }                                           │
│  }                                             │
└────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────┐
│              MCP Server Process                │
│                                                │
│  - Runs as subprocess                          │
│  - Communicates via stdio                      │
│  - Exposes tools through protocol              │
│  - Handles requests/responses                  │
└────────────────────────────────────────────────┘
```

## Server Architecture

### Three-Layer Architecture Pattern

The system implements a clean three-layer architecture:

```
┌─────────────────────────────────────────────────────────────┐
│                    Layer 1: Agent Tools                      │
│                  (agents/valor/agent.py)                     │
│            Legacy PydanticAI tool integration                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  Layer 2: MCP Servers                        │
│                    (mcp_servers/*.py)                        │
│     Protocol handling, context injection, validation         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Layer 3: Standalone Tools                       │
│                      (tools/*.py)                            │
│        Pure functions with no MCP dependencies               │
└─────────────────────────────────────────────────────────────┘
```

**Benefits of Three-Layer Design:**
1. **Testability**: Standalone tools can be tested without MCP complexity
2. **Reusability**: Tools can be used in multiple contexts
3. **Migration Path**: Smooth transition from legacy to MCP
4. **Clear Boundaries**: Each layer has specific responsibilities

### Social Tools Server

**Purpose**: Web interaction and content generation

```python
# mcp_servers/social_tools.py
mcp = FastMCP("Social Tools")

# Tool inventory with emojis for visual feedback
MCP_TOOL_EMOJIS = {
    "search_current_info": "🗿",      # Web search
    "create_image": "🎉",             # Image generation
    "analyze_shared_image": "🤩",     # Vision analysis
    "save_link": "🍾",               # Link storage
    "search_links": "🔥",            # Link retrieval
    "transcribe_voice_message": "✍", # Audio transcription
    "technical_analysis": "🤓"        # Deep technical research
}

@mcp.tool()
def search_current_info(query: str, max_results: int = 3) -> str:
    """Search the web using Perplexity AI"""
    try:
        # Import standalone implementation
        from tools.search_tool import search_web
        return search_web(query, max_results)
    except Exception as e:
        return f"🔍 Search error: {str(e)}"
```

**Key Responsibilities:**
- Web search via Perplexity AI
- Image generation with DALL-E 3
- Image analysis using GPT-4o vision
- Link storage and retrieval
- YouTube transcription
- Voice message transcription
- Technical analysis delegation

### PM Tools Server

**Purpose**: Project management and workspace queries

```python
# mcp_servers/pm_tools.py
@mcp.tool()
def query_notion_projects(
    workspace: str, 
    question: str,
    include_completed: bool = False,
    chat_id: str = ""
) -> str:
    """Query Notion workspace for project information"""
    
    # Workspace isolation enforcement
    if chat_id:
        allowed_workspace = get_allowed_workspace(chat_id)
        if workspace != allowed_workspace:
            return f"❌ Access denied: Only {allowed_workspace} allowed"
    
    # Execute query with AI analysis
    return query_notion_with_ai(workspace, question, include_completed)
```

**Key Responsibilities:**
- Notion database queries
- Workspace access control
- AI-powered query analysis
- Project status tracking
- Task management

### Telegram Tools Server

**Purpose**: Conversation history and context management

```python
# mcp_servers/telegram_tools.py
@mcp.tool()
def search_conversation_history(
    query: str,
    chat_id: str = "",
    max_results: int = 5
) -> str:
    """Search through Telegram conversation history"""
    
    # Input validation
    if not query or not query.strip():
        return "❌ Search query cannot be empty."
    
    # Context injection
    chat_id, _ = inject_context_for_tool(chat_id, "")
    
    # Search implementation
    return search_telegram_history(query, chat_id, max_results)
```

**Key Responsibilities:**
- Conversation history search
- Recent context retrieval
- Message analytics
- Dialog management

### Development Tools Server

**Purpose**: Code analysis and development support

```python
# mcp_servers/development_tools.py
@mcp.tool()
def execute_bug_report_with_screenshot(
    task_description: str,
    notion_task_id: str = "",
    chat_id: str = ""
) -> str:
    """Complete bug investigation with screenshot evidence"""
    
    # Workspace validation
    working_dir = resolve_working_directory(chat_id)
    
    # Delegate to Claude Code with screenshot capture
    result = delegate_to_claude_with_screenshot(
        task_description,
        working_dir,
        notion_task_id
    )
    
    return format_bug_report(result)
```

**Key Responsibilities:**
- Code linting and analysis
- Documentation generation
- Screenshot capture workflows
- Bug report automation
- Test generation
- Development task delegation

## Context Injection Strategy

### The Context Challenge

MCP tools are stateless but need conversation context. The solution: sophisticated context injection.

### Context Flow Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram Message                          │
│              (Contains chat_id, username)                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Valor Agent                               │
│         (Builds enhanced prompt with context)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 Claude Code Session                          │
│                                                              │
│  CONTEXT_DATA:                                               │
│  CHAT_ID=123456                                              │
│  USERNAME=user123                                            │
│  RECENT_HISTORY:                                             │
│  User: Previous message                                      │
│  Assistant: Previous response                                │
│                                                              │
│  USER_REQUEST: Current request                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    MCP Tool                                  │
│           (Extracts context from parameters)                 │
└─────────────────────────────────────────────────────────────┘
```

### Context Manager Implementation

```python
class MCPContextManager:
    """Thread-safe context management for stateless MCP tools"""
    
    def __init__(self):
        self._context_store = threading.local()
        self._context_file = Path.home() / ".cache/ai_agent/mcp_context.json"
        self._lock = threading.Lock()
    
    def set_context(self, chat_id: int, username: str):
        """Store context for current thread"""
        self._context_store.chat_id = chat_id
        self._context_store.username = username
        
        # Persist to file for cross-process access
        with self._lock:
            self._context_file.parent.mkdir(exist_ok=True)
            context_data = {
                "chat_id": chat_id,
                "username": username,
                "timestamp": datetime.now().isoformat()
            }
            self._context_file.write_text(json.dumps(context_data))
    
    def inject_context_for_tool(self, chat_id: str = "", username: str = "") -> tuple:
        """Inject context with multiple fallback strategies"""
        
        # Priority 1: Explicit parameters
        if chat_id and username:
            return chat_id, username
        
        # Priority 2: Thread-local storage
        if hasattr(self._context_store, 'chat_id'):
            resolved_chat_id = chat_id or str(self._context_store.chat_id)
            resolved_username = username or self._context_store.username
            return resolved_chat_id, resolved_username
        
        # Priority 3: Persistent file
        if self._context_file.exists():
            try:
                data = json.loads(self._context_file.read_text())
                return (
                    chat_id or str(data.get("chat_id", "")),
                    username or data.get("username", "")
                )
            except:
                pass
        
        # Priority 4: Environment variables
        return (
            chat_id or os.getenv("CURRENT_CHAT_ID", ""),
            username or os.getenv("CURRENT_USERNAME", "")
        )
```

### Enhanced Prompt Building

```python
def build_enhanced_prompt(message: str, context: dict) -> str:
    """Build prompt with injected context for Claude Code"""
    
    context_parts = []
    
    # Chat identification
    if context.get('chat_id'):
        context_parts.append(f"CHAT_ID={context['chat_id']}")
    
    if context.get('username'):
        context_parts.append(f"USERNAME={context['username']}")
    
    # Conversation history
    if context.get('chat_history'):
        recent = context['chat_history'][-5:]  # Last 5 messages
        history_text = "\n".join([
            f"{msg['role']}: {msg['content'][:100]}..."
            for msg in recent
        ])
        context_parts.append(f"RECENT_HISTORY:\n{history_text}")
    
    # Workspace context
    if context.get('workspace'):
        context_parts.append(f"WORKSPACE={context['workspace']}")
        context_parts.append(f"WORKING_DIR={context['working_directory']}")
    
    # Build final prompt
    return f"""CONTEXT_DATA:
{chr(10).join(context_parts)}

USER_REQUEST: {message}"""
```

### Workspace-Aware Tool Execution

```python
@mcp.tool()
def execute_in_workspace(command: str, chat_id: str = "") -> str:
    """Execute command in chat-specific workspace"""
    
    # Resolve workspace from chat context
    workspace_config = load_workspace_config()
    workspace = None
    
    for ws_name, config in workspace_config.get("workspaces", {}).items():
        if int(chat_id) in config.get("telegram_chat_ids", []):
            workspace = {
                "name": ws_name,
                "directory": config.get("working_directory"),
                "database_id": config.get("database_id")
            }
            break
    
    if not workspace:
        return "❌ No workspace configured for this chat"
    
    # Execute with isolation
    result = subprocess.run(
        command,
        shell=True,
        cwd=workspace["directory"],
        capture_output=True,
        text=True,
        timeout=300
    )
    
    return format_execution_result(result)
```

## Tool Quality Standards

### The Gold Standard: image_analysis_tool.py

The image analysis tool achieved a 9.8/10 quality score and serves as the reference implementation.

### Quality Characteristics

#### 1. Sophisticated Error Categorization

```python
try:
    # Tool implementation
    result = perform_operation()
    return result
    
except FileNotFoundError:
    return "👁️ Image analysis error: Image file not found."
    
except OSError as e:
    return f"👁️ Image file error: Failed to read image file - {str(e)}"
    
except Exception as e:
    error_type = type(e).__name__
    
    # API errors
    if "API" in str(e) or "OpenAI" in str(e):
        return f"👁️ OpenAI API error: {str(e)}"
    
    # Encoding errors
    if "base64" in str(e).lower() or "encoding" in str(e).lower():
        return f"👁️ Image encoding error: Failed to process image format - {str(e)}"
    
    # Generic with type info
    return f"👁️ Image analysis error ({error_type}): {str(e)}"
```

**Key Principles:**
- Specific error types for common failures
- User-friendly messages with context
- Emoji indicators for visual recognition
- Error type included for debugging

#### 2. Pre-Validation for Efficiency

```python
@mcp.tool()
def analyze_shared_image(image_path: str, question: str = "", chat_id: str = "") -> str:
    """Analyze image with pre-validation"""
    
    # Validate format BEFORE file operations
    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
    file_extension = Path(image_path).suffix.lower()
    
    if file_extension not in valid_extensions:
        return f"👁️ Image analysis error: Unsupported format '{file_extension}'. Supported: {', '.join(valid_extensions)}"
    
    # Check file exists before processing
    if not Path(image_path).exists():
        return "👁️ Image analysis error: Image file not found."
    
    # Size validation
    file_size = Path(image_path).stat().st_size
    if file_size > 20 * 1024 * 1024:  # 20MB limit
        return "👁️ Image analysis error: File too large (max 20MB)."
    
    # Now safe to process
    return analyze_image_impl(image_path, question)
```

**Validation Hierarchy:**
1. Format validation (cheap)
2. Existence check (file system)
3. Size validation (prevent OOM)
4. API call (expensive)

#### 3. Context-Aware Behavior

```python
def analyze_image(image_path: str, question: str = None, context: str = None) -> str:
    """Adapt behavior based on use case"""
    
    # Different prompts for different scenarios
    if question:
        system_prompt = (
            "You are an AI assistant with vision capabilities. "
            "Analyze the provided image and answer the specific question about it. "
            "Be detailed and accurate in your response. "
            "Keep responses under 400 words for messaging platforms."
        )
        user_prompt = f"Question: {question}"
    else:
        system_prompt = (
            "You are an AI assistant with vision capabilities. "
            "Describe what you see in the image in a natural, conversational way. "
            "Focus on the most interesting or relevant aspects. "
            "Keep responses under 300 words for messaging platforms."
        )
        user_prompt = "What do you see in this image?"
    
    # Include context if available
    if context:
        user_prompt += f"\n\nContext: {context}"
```

#### 4. Comprehensive Test Coverage

```python
class TestImageAnalysisTool:
    """100% test coverage with real scenarios"""
    
    def test_format_validation(self):
        """Test format validation happens before file operations"""
        result = analyze_image("test.txt")
        assert "Unsupported format" in result
        
    def test_file_not_found(self):
        """Test graceful handling of missing files"""
        result = analyze_image("nonexistent.jpg")
        assert "not found" in result
        
    def test_api_error_handling(self):
        """Test API error categorization"""
        with patch('openai.OpenAI') as mock:
            mock.side_effect = Exception("API rate limit exceeded")
            result = analyze_image("valid.jpg")
            assert "API error" in result
            
    def test_successful_analysis(self):
        """Test happy path with mocked API"""
        with patch('openai.OpenAI') as mock:
            mock.return_value.chat.completions.create.return_value = mock_response
            result = analyze_image("valid.jpg", "What color is the sky?")
            assert "blue" in result.lower()
```

### Input Validation Standards

```python
# Length validation with clear limits
if len(query) > 200:
    return "❌ Search query too long (max 200 characters)."

# Range validation with bounds
if not 1 <= max_results <= 50:
    return "❌ max_results must be between 1 and 50."

# Type conversion with error handling
try:
    numeric_value = int(string_param)
except ValueError:
    return f"❌ Invalid number format: {string_param}"

# Path validation with security
if ".." in file_path or file_path.startswith("/"):
    return "❌ Invalid file path. Use relative paths only."

# Required parameter validation
if not query or not query.strip():
    return "❌ Query parameter is required and cannot be empty."
```

### Quality Metrics

Tools should achieve these benchmarks:

| Metric | Target | Gold Standard |
|--------|--------|---------------|
| Error Handling | >90% coverage | 100% |
| Input Validation | All inputs validated | ✓ |
| Test Coverage | >95% | 100% |
| Response Time | <1s validation | <100ms |
| Error Clarity | User-friendly messages | ✓ |
| Context Awareness | Adaptive behavior | ✓ |

## Configuration Management

### Dynamic .mcp.json Generation

The system dynamically generates MCP configuration based on available services:

```bash
#!/bin/bash
# scripts/update_mcp.sh

# Detect available API keys
NOTION_KEY=$(grep NOTION_API_KEY .env | cut -d'=' -f2)
OPENAI_KEY=$(grep OPENAI_API_KEY .env | cut -d'=' -f2)

# Build configuration dynamically
cat > .mcp.json << EOF
{
  "mcpServers": {
EOF

# Add social tools (always available)
cat >> .mcp.json << EOF
    "social-tools": {
      "command": "python",
      "args": ["mcp_servers/social_tools.py"]
    }
EOF

# Add PM tools if Notion configured
if [ ! -z "$NOTION_KEY" ]; then
  cat >> .mcp.json << EOF
    ,
    "pm-tools": {
      "command": "python", 
      "args": ["mcp_servers/pm_tools.py"]
    }
EOF
fi

# Close configuration
cat >> .mcp.json << EOF
  }
}
EOF

# Validate JSON
jq . .mcp.json > /dev/null || echo "Error: Invalid JSON generated"
```

### Server Lifecycle Management

```python
class MCPServerManager:
    """Manages MCP server lifecycle"""
    
    def __init__(self):
        self.servers = {}
        self.config_path = Path(".mcp.json")
    
    def start_servers(self):
        """Start all configured MCP servers"""
        config = json.loads(self.config_path.read_text())
        
        for server_name, server_config in config["mcpServers"].items():
            try:
                process = subprocess.Popen(
                    [server_config["command"]] + server_config["args"],
                    env={**os.environ, **server_config.get("env", {})},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                self.servers[server_name] = process
                logger.info(f"Started MCP server: {server_name}")
            except Exception as e:
                logger.error(f"Failed to start {server_name}: {e}")
    
    def stop_servers(self):
        """Gracefully stop all servers"""
        for name, process in self.servers.items():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"Stopped MCP server: {name}")
```

### Error Handling and Failover

```python
class MCPErrorHandler:
    """Handles MCP communication errors"""
    
    def handle_tool_error(self, tool_name: str, error: Exception) -> str:
        """Convert errors to user-friendly messages"""
        
        if isinstance(error, TimeoutError):
            return f"⏱️ {tool_name} timed out. Please try again."
            
        elif isinstance(error, ConnectionError):
            return f"🔌 Cannot connect to {tool_name} server."
            
        elif "rate limit" in str(error).lower():
            return f"🚦 Rate limit reached for {tool_name}."
            
        elif "not found" in str(error).lower():
            return f"❓ {tool_name} tool not available."
            
        else:
            # Log full error for debugging
            logger.error(f"MCP tool error: {tool_name}", exc_info=error)
            return f"❌ {tool_name} error: {str(error)}"
    
    def retry_with_backoff(self, func, max_retries=3):
        """Retry failed operations with exponential backoff"""
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = 2 ** attempt
                time.sleep(wait_time)
```

### Environment Integration

```python
class MCPEnvironment:
    """Manages MCP environment configuration"""
    
    def __init__(self):
        self.env_file = Path(".env")
        self.mcp_config = Path(".mcp.json")
    
    def sync_configuration(self):
        """Sync .env to MCP configuration"""
        
        # Load environment
        env_vars = dotenv_values(self.env_file)
        
        # Load current MCP config
        if self.mcp_config.exists():
            config = json.loads(self.mcp_config.read_text())
        else:
            config = {"mcpServers": {}}
        
        # Update environment variables in MCP servers
        for server_name, server_config in config["mcpServers"].items():
            if "env" not in server_config:
                server_config["env"] = {}
            
            # Map common environment variables
            if server_name == "notionApi" and "NOTION_API_KEY" in env_vars:
                server_config["env"]["NOTION_API_KEY"] = env_vars["NOTION_API_KEY"]
            
            if "OPENAI_API_KEY" in env_vars:
                server_config["env"]["OPENAI_API_KEY"] = env_vars["OPENAI_API_KEY"]
        
        # Save updated configuration
        self.mcp_config.write_text(json.dumps(config, indent=2))
```

## Implementation Patterns for Rebuild

### 1. Tool Implementation Pattern

```python
# Step 1: Create standalone tool (tools/new_tool.py)
def perform_operation(param1: str, param2: int = 10) -> str:
    """Pure function implementation"""
    # Validation
    if not param1:
        raise ValueError("param1 is required")
    
    # Business logic
    result = complex_operation(param1, param2)
    
    # Return formatted result
    return f"✅ Operation completed: {result}"

# Step 2: Create MCP wrapper (mcp_servers/category_tools.py)
from tools.new_tool import perform_operation

@mcp.tool()
def new_tool(param1: str, param2: int = 10, chat_id: str = "") -> str:
    """MCP wrapper with context injection"""
    try:
        # Context injection if needed
        chat_id, _ = inject_context_for_tool(chat_id, "")
        
        # Input validation (MCP-specific)
        if len(param1) > 1000:
            return "❌ Input too long (max 1000 characters)"
        
        # Call standalone implementation
        return perform_operation(param1, param2)
        
    except ValueError as e:
        return f"❌ Invalid input: {str(e)}"
    except Exception as e:
        return f"❌ Operation failed: {str(e)}"
```

### 2. Context Flow Pattern

```python
class ContextAwareTool:
    """Pattern for tools needing rich context"""
    
    @mcp.tool()
    def context_aware_operation(
        self,
        user_input: str,
        chat_id: str = "",
        include_history: bool = False
    ) -> str:
        """Tool that leverages conversation context"""
        
        # Get base context
        chat_id, username = inject_context_for_tool(chat_id)
        
        # Load additional context if needed
        context = {
            "chat_id": chat_id,
            "username": username
        }
        
        if include_history:
            # Load from database
            history = load_chat_history(int(chat_id), limit=10)
            context["recent_messages"] = history
        
        # Load workspace context
        workspace = resolve_workspace(int(chat_id))
        if workspace:
            context.update(workspace)
        
        # Execute with full context
        return execute_with_context(user_input, context)
```

### 3. Error Recovery Pattern

```python
class ResilientTool:
    """Pattern for tools with graceful degradation"""
    
    @mcp.tool()
    def resilient_operation(self, input_data: str) -> str:
        """Tool with multiple fallback strategies"""
        
        # Try primary service
        try:
            return primary_service(input_data)
        except ServiceUnavailable:
            pass
        
        # Try secondary service
        try:
            return secondary_service(input_data)
        except ServiceUnavailable:
            pass
        
        # Fallback to cached/degraded response
        cached = get_cached_response(input_data)
        if cached:
            return f"⚠️ Using cached result (services unavailable):\n{cached}"
        
        # Final fallback
        return "❌ Service temporarily unavailable. Please try again later."
```

### 4. Validation Pattern

```python
class ValidatedTool:
    """Pattern for comprehensive input validation"""
    
    @mcp.tool()
    def validated_operation(
        self,
        text_input: str,
        number_input: int,
        file_path: str = ""
    ) -> str:
        """Tool with thorough validation"""
        
        # Text validation
        if not text_input or not text_input.strip():
            return "❌ Text input cannot be empty"
        
        if len(text_input) > 1000:
            return "❌ Text too long (max 1000 characters)"
        
        if any(char in text_input for char in ['<', '>', '&']):
            return "❌ Invalid characters in text"
        
        # Number validation
        if not 0 <= number_input <= 100:
            return "❌ Number must be between 0 and 100"
        
        # Path validation
        if file_path:
            if not self._validate_safe_path(file_path):
                return "❌ Invalid file path"
        
        # All validated - proceed
        return process_validated_input(text_input, number_input, file_path)
```

## Best Practices Summary

1. **Always Use Three Layers**: Separate MCP, implementation, and agent concerns
2. **Implement Context Injection**: Use the standard context manager pattern
3. **Follow Error Standards**: Categorize errors with clear emoji indicators
4. **Validate Early**: Check inputs before expensive operations
5. **Test Thoroughly**: Aim for 100% coverage of error paths
6. **Document Tool Purpose**: Clear descriptions help Claude Code use tools correctly
7. **Handle Timeouts**: Long operations need timeout protection
8. **Provide Fallbacks**: Graceful degradation for service failures
9. **Log Appropriately**: Debug logs for errors, info for normal operation
10. **Version Your Tools**: Include version info for debugging

The MCP integration architecture provides a robust, scalable foundation for extending the system with new capabilities while maintaining clean separation of concerns and excellent user experience.