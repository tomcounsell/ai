# Social Tools MCP Server

## Overview

The Social Tools MCP Server provides comprehensive web interaction and content generation capabilities through a sophisticated integration of multiple AI services. Built following the **Gold Standard wrapper pattern**, it serves as the primary interface for search, image processing, transcription, and technical analysis operations.

## Server Architecture

### Core Design Philosophy

The server follows the **Gold Standard wrapper pattern**, importing standalone tool implementations and enhancing them with MCP-specific concerns:

```python
# GOLD STANDARD Pattern Example
from tools.search_tool import search_web

@mcp.tool()
def search_current_info(query: str, max_results: int = 3) -> str:
    """MCP wrapper with validation and context injection"""
    try:
        # MCP-specific validation
        if not query or not query.strip():
            return "🔍 Search error: Please provide a search query."
        
        # Call standalone implementation
        return search_web(query, max_results)
        
    except Exception as e:
        return f"🔍 Search error: {str(e)}"
```

### Tool Categories

1. **Search & Information**: Web search, link analysis, technical research
2. **Image Processing**: Generation, analysis, tagging
3. **Transcription**: Voice messages, YouTube content
4. **Session Management**: Claude Code workflow continuity

### Emoji System

The server uses validated Telegram reaction emojis for consistent user experience:

```python
MCP_TOOL_EMOJIS = {
    "search_current_info": "🗿",      # moai - solid, reliable info
    "create_image": "🎉",             # party - celebration, creation
    "transcribe_youtube_video": "🎥", # movie camera - video content
    "analyze_shared_image": "🤩",     # star eyes - amazed, impressed
    "save_link": "🍾",               # champagne - success, saved
    "technical_analysis": "🤓",      # nerd - deep technical analysis
    "transcribe_voice_message": "✍", # writing - taking notes
}
```

## Tool Specifications

### search_current_info

**Purpose**: Retrieve current information from the web using Perplexity AI

#### Input Parameters
```python
def search_current_info(query: str, max_results: int = 3) -> str:
```

- **query** (required): Search query string
  - Validation: Non-empty, max 500 characters
  - Content filtering: Basic sanitization applied
- **max_results** (optional): Number of results (1-10)
  - Default: 3
  - Validation: Integer between 1-10

#### Implementation Details

```python
# Validation Layer
if not query or not query.strip():
    return "🔍 Search error: Please provide a search query."

if len(query) > 500:
    return "🔍 Search error: Query too long (maximum 500 characters)."

# Call standalone implementation
return search_web(query, max_results)
```

#### API Integration

- **Service**: Perplexity API
- **Configuration**: `PERPLEXITY_API_KEY` environment variable
- **Request Format**: Structured query with context awareness
- **Response Processing**: AI-synthesized answers with source attribution

#### Error Handling

```python
# API Configuration Error
"🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration."

# Rate Limiting
"🔍 Search rate limited. Please try again in a moment."

# Network Issues
"🔍 Search service temporarily unavailable. Please check connection."

# Generic Error
"🔍 Search error: {error_message}"
```

#### Performance Characteristics

- **Response Time**: 1-3 seconds typical
- **Rate Limits**: Handled automatically with exponential backoff
- **Caching**: No caching (always fresh information)
- **Timeout**: 30 seconds request timeout

#### Output Format

```
🔍 **Query: "AI developments 2024"**

Recent AI developments in 2024 include significant advancements in 
large language models, with new architectures showing improved 
reasoning capabilities...

*Sources: Multiple web sources synthesized*
```

### create_image

**Purpose**: Generate images using DALL-E 3 with Telegram integration

#### Input Parameters
```python
def create_image(
    prompt: str, 
    size: str = "1024x1024", 
    chat_id: str = ""
) -> str:
```

- **prompt** (required): Image description
  - Validation: Non-empty, max 1000 characters
  - Content filtering: Explicit content detection
- **size** (optional): Image dimensions
  - Options: "1024x1024", "1024x1792", "1792x1024"
  - Default: "1024x1024"
- **chat_id** (optional): Telegram chat context for delivery

#### Implementation Details

```python
# Input validation
if not prompt or not prompt.strip():
    return "🎨 Image generation error: Please provide a description."

if len(prompt) > 1000:
    return "🎨 Image generation error: Description too long (max 1000 characters)."

# Context injection
chat_id, _ = inject_context_for_tool(chat_id, "")

# Generate image with standalone tool
image_path = generate_image(prompt, size, quality, style, save_directory=None)

# Telegram integration
if chat_id:
    return f"TELEGRAM_IMAGE_GENERATED|{image_path}|{chat_id}"
else:
    return f"🎨 Image generated successfully! Saved to: {image_path}"
```

#### API Integration

- **Service**: OpenAI DALL-E 3
- **Configuration**: `OPENAI_API_KEY` environment variable
- **Quality**: HD quality by default
- **Style**: Natural style (not vivid) for realistic results

#### Error Handling

```python
# Missing Configuration
"🎨 Image generation unavailable: Missing OPENAI_API_KEY configuration."

# Content Policy Violation
"🎨 Image generation blocked: Content violates usage policies."

# API Rate Limit
"🎨 Image generation rate limited. Please try again later."

# File System Error
"🎨 Image generation error: Unable to save image file."
```

#### Telegram Integration

Special response format for Telegram delivery:
```
TELEGRAM_IMAGE_GENERATED|/path/to/image.png|chat_id
```

This triggers automatic image upload in the Telegram integration layer.

#### Performance Characteristics

- **Generation Time**: 10-30 seconds typical
- **File Size**: 1-5 MB typical (PNG format)
- **Storage**: Temporary files auto-cleaned after 24 hours
- **Concurrent Limit**: 5 simultaneous generations

### analyze_shared_image

**Purpose**: AI-powered image analysis using GPT-4o vision (Gold Standard implementation)

#### Input Parameters
```python
def analyze_shared_image(
    image_path: str, 
    question: str = "", 
    chat_id: str = ""
) -> str:
```

- **image_path** (required): Path to image file
  - Validation: File existence, format, size
  - Supported: JPG, PNG, GIF, WebP
- **question** (optional): Specific question about image
  - Validation: Max 500 characters
- **chat_id** (optional): Context for analysis relevance

#### Gold Standard Implementation

This tool exemplifies the **Gold Standard** with sophisticated error handling:

```python
# Pre-validation (efficiency optimization)
valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
file_extension = Path(image_path).suffix.lower()
if file_extension not in valid_extensions:
    return f"👁️ Image analysis error: Unsupported format '{file_extension}'. Supported: {', '.join(valid_extensions)}"

# Existence check before processing
if not Path(image_path).exists():
    return "👁️ Image analysis error: Image file not found."

# Size validation (prevent memory issues)
file_size = Path(image_path).stat().st_size
if file_size > 20 * 1024 * 1024:  # 20MB limit
    return "👁️ Image analysis error: File too large (max 20MB)."
```

#### Error Categorization

```python
except FileNotFoundError:
    return "👁️ Image analysis error: Image file not found."
    
except OSError as e:
    return f"👁️ Image file error: Failed to read image file - {str(e)}"
    
except Exception as e:
    error_type = type(e).__name__
    
    # API-specific errors
    if "API" in str(e) or "OpenAI" in str(e):
        return f"👁️ OpenAI API error: {str(e)}"
    
    # Encoding errors
    if "base64" in str(e).lower() or "encoding" in str(e).lower():
        return f"👁️ Image encoding error: Failed to process image format - {str(e)}"
    
    # Generic with context
    return f"👁️ Image analysis error ({error_type}): {str(e)}"
```

#### Context-Aware Behavior

```python
# Adaptive prompting based on use case
if question:
    system_prompt = (
        "You are an AI assistant with vision capabilities. "
        "Analyze the provided image and answer the specific question about it. "
        "Be detailed and accurate in your response. "
        "Keep responses under 400 words for messaging platforms."
    )
else:
    system_prompt = (
        "You are an AI assistant with vision capabilities. "
        "Describe what you see in the image in a natural, conversational way. "
        "Focus on the most interesting or relevant aspects. "
        "Keep responses under 300 words for messaging platforms."
    )
```

#### Performance Characteristics

- **Analysis Time**: 2-5 seconds typical
- **Accuracy**: Very high with GPT-4o vision
- **File Size Limit**: 20MB maximum
- **Concurrent Analysis**: 3 simultaneous maximum

### save_link

**Purpose**: Save and analyze URLs with AI-powered content analysis

#### Input Parameters
```python
def save_link(url: str, chat_id: str = "", username: str = "") -> str:
```

- **url** (required): URL to analyze and store
  - Validation: URL format, accessibility
  - Protocols: HTTP/HTTPS only
- **chat_id** (optional): Telegram chat context
- **username** (optional): User attribution

#### Implementation Details

```python
# Context injection
chat_id, username = inject_context_for_tool(chat_id, username)

# URL validation
if not url or not url.strip():
    return "🔗 Link save error: Please provide a URL."

# Basic URL format validation
if not (url.startswith('http://') or url.startswith('https://')):
    return "🔗 Link save error: URL must start with http:// or https://"

# Call standalone implementation with context
result = store_link_with_analysis(url, chat_id, username)
```

#### Database Integration

```python
# SQLite schema for link storage
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    content_summary TEXT,
    chat_id TEXT,
    username TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    analysis_status TEXT DEFAULT 'pending'
)
```

#### AI Analysis Integration

- **Content Extraction**: Full HTML parsing with readability processing
- **AI Summarization**: Claude/GPT integration for content analysis
- **Metadata Extraction**: Title, description, keywords
- **Duplicate Detection**: URL normalization and deduplication

#### Error Handling

```python
# URL Accessibility
"🔗 Link save error: URL is not accessible or returned an error."

# Invalid Format
"🔗 Link save error: Invalid URL format."

# Database Error
"🔗 Link save error: Unable to store link in database."

# Analysis Failure
"🔗 Link saved but analysis failed. Content stored without summary."
```

### transcribe_voice_message

**Purpose**: Convert voice messages to text using OpenAI Whisper

#### Input Parameters
```python
def transcribe_voice_message(
    file_path: str,
    language: str = "auto",
    chat_id: str = ""
) -> str:
```

- **file_path** (required): Path to audio file
  - Validation: File existence, format, size
  - Supported: MP3, MP4, MPEG, MPGA, M4A, WAV, WEBM
- **language** (optional): Language hint for transcription
- **chat_id** (optional): Context for response formatting

#### Implementation Details

```python
# File validation
if not Path(file_path).exists():
    return "✍ Transcription error: Audio file not found."

# Size validation (25MB Whisper limit)
file_size = Path(file_path).stat().st_size
if file_size > 25 * 1024 * 1024:
    return "✍ Transcription error: File too large (max 25MB)."

# Format validation
supported_formats = ['.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.wav', '.webm']
if Path(file_path).suffix.lower() not in supported_formats:
    return f"✍ Transcription error: Unsupported format. Supported: {', '.join(supported_formats)}"
```

#### Performance Characteristics

- **Processing Time**: 5-15 seconds for typical voice messages
- **Accuracy**: Very high with Whisper model
- **Language Support**: 99+ languages with auto-detection
- **File Size Limit**: 25MB (OpenAI Whisper limit)

### technical_analysis

**Purpose**: Deep technical research and analysis using Claude Code

#### Input Parameters
```python
def technical_analysis(
    research_topic: str,
    focus_areas: str = "",
    working_directory: str = "",
    chat_id: str = ""
) -> str:
```

- **research_topic** (required): Topic for technical investigation
- **focus_areas** (optional): Specific areas to emphasize
- **working_directory** (optional): Workspace context
- **chat_id** (optional): Chat context for workspace resolution

#### Implementation Details

```python
# Workspace resolution from chat context
if chat_id and not working_directory:
    workspace_config = load_workspace_config()
    workspace_info = None
    
    for workspace_name, config in workspace_config.get("workspaces", {}).items():
        if int(chat_id) in config.get("telegram_chat_ids", []):
            working_directory = config.get("working_directory", "")
            break

# Build research-focused prompt
prompt_parts = [
    f"TECHNICAL RESEARCH TASK: {research_topic}",
    "",
    "RESEARCH GUIDELINES:",
    "- Use Read, Glob, Grep, and other analysis tools extensively to understand the codebase",
    "- Do NOT edit, write, or modify any files - this is READ-ONLY research",
    "- Focus on understanding and explaining what currently exists",
    "- Provide comprehensive analysis based on actual code inspection"
]
```

#### Claude Code Integration

- **Session Management**: Persistent sessions for continuity
- **Timeout**: 2-hour maximum execution time
- **Working Directory**: Workspace-aware execution
- **Tool Access**: Full Claude Code tool suite available

#### Error Recovery

```python
except subprocess.TimeoutExpired:
    from utilities.swe_error_recovery import SWEErrorRecovery
    return SWEErrorRecovery.format_recovery_response(
        tool_name="technical_analysis",
        task_description=research_topic,
        error_message="Research exceeded 2 hour timeout",
        working_directory=working_directory,
        execution_time=execution_time
    )
```

## Context Injection System

### Thread-Safe Context Manager

```python
class MCPContextManager:
    """Thread-safe context management for stateless MCP tools"""
    
    def __init__(self):
        self._context_store = threading.local()
        self._context_file = Path.home() / ".cache/ai_agent/mcp_context.json"
        self._lock = threading.Lock()
    
    def inject_context_for_tool(self, chat_id: str = "", username: str = "") -> tuple:
        """Multi-fallback context injection strategy"""
        
        # Priority 1: Explicit parameters
        if chat_id and username:
            return chat_id, username
        
        # Priority 2: Thread-local storage
        if hasattr(self._context_store, 'chat_id'):
            resolved_chat_id = chat_id or str(self._context_store.chat_id)
            resolved_username = username or self._context_store.username
            return resolved_chat_id, resolved_username
        
        # Priority 3: Persistent file cache
        if self._context_file.exists():
            try:
                data = json.loads(self._context_file.read_text())
                return (
                    chat_id or str(data.get("chat_id", "")),
                    username or data.get("username", "")
                )
            except Exception:
                pass
        
        # Priority 4: Environment variables
        return (
            chat_id or os.getenv("CURRENT_CHAT_ID", ""),
            username or os.getenv("CURRENT_USERNAME", "")
        )
```

## Performance Optimization

### Caching Strategies

```python
# Link analysis caching to prevent duplicate API calls
def check_existing_analysis(url: str) -> Optional[dict]:
    """Check if URL already analyzed"""
    with get_database_connection() as conn:
        cursor = conn.execute("""
            SELECT title, description, content_summary, analysis_status
            FROM links 
            WHERE url = ? AND analysis_status = 'success'
        """, (url,))
        
        row = cursor.fetchone()
        if row:
            return {
                "title": row[0],
                "description": row[1], 
                "summary": row[2],
                "cached": True
            }
    return None
```

### Resource Management

```python
# Automatic cleanup of generated images
def schedule_cleanup(file_path: str, delay_hours: int = 24):
    """Schedule automatic file cleanup"""
    cleanup_time = time.time() + (delay_hours * 3600)
    
    # Store cleanup task
    cleanup_registry[file_path] = cleanup_time
    
    # Periodic cleanup process handles actual deletion
```

## Security and Validation

### Input Sanitization

```python
def sanitize_filename(original_name: str) -> str:
    """Create safe filename from user input"""
    
    # Remove dangerous characters
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', original_name)
    
    # Limit length
    safe_name = safe_name[:100]
    
    # Ensure not empty
    if not safe_name.strip():
        safe_name = "untitled"
    
    return safe_name
```

### API Key Protection

```python
def validate_api_configuration(required_keys: List[str]) -> Optional[str]:
    """Validate required API keys are present"""
    
    missing_keys = []
    for key in required_keys:
        if not os.getenv(key):
            missing_keys.append(key)
    
    if missing_keys:
        return f"Missing required configuration: {', '.join(missing_keys)}"
    
    return None
```

## Integration Requirements

### Environment Variables

Required configuration for full functionality:

```bash
# Core AI Services
OPENAI_API_KEY=sk-proj-...           # DALL-E 3, Whisper, GPT-4o vision
PERPLEXITY_API_KEY=pplx-...          # Web search

# Optional Services  
ANTHROPIC_API_KEY=sk-ant-...         # Link content analysis
YOUTUBE_API_KEY=...                  # Enhanced YouTube metadata

# Database
DATABASE_PATH=system.db              # SQLite database location
```

### Database Schema

```sql
-- Links storage table
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    description TEXT,
    content_summary TEXT,
    chat_id TEXT,
    username TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    analysis_status TEXT DEFAULT 'pending',
    content_type TEXT,
    word_count INTEGER,
    tags TEXT  -- JSON array of tags
);

-- Index for performance
CREATE INDEX IF NOT EXISTS idx_links_chat_id ON links(chat_id);
CREATE INDEX IF NOT EXISTS idx_links_created_at ON links(created_at);
```

## Testing and Validation

### Test Coverage Requirements

```python
class TestSocialToolsIntegration:
    """Comprehensive integration testing"""
    
    async def test_search_functionality(self):
        """Test web search with real API"""
        result = await search_current_info("Python async programming")
        assert "🔍" in result
        assert len(result) > 50  # Meaningful response
    
    async def test_image_analysis_gold_standard(self):
        """Test image analysis error handling"""
        # Test file not found
        result = await analyze_shared_image("nonexistent.jpg")
        assert "Image file not found" in result
        
        # Test unsupported format
        result = await analyze_shared_image("test.txt")
        assert "Unsupported format" in result
    
    async def test_link_storage_with_analysis(self):
        """Test link analysis and storage"""
        result = await save_link("https://example.com")
        assert "🍾" in result or "🔗" in result
```

### Performance Benchmarks

| Tool | Target Response Time | Success Rate | Error Recovery |
|------|---------------------|--------------|----------------|
| Search | <3 seconds | >95% | Automatic retry |
| Image Generation | <30 seconds | >90% | Queue management |
| Image Analysis | <5 seconds | >98% | Graceful degradation |
| Voice Transcription | <15 seconds | >95% | Format fallbacks |
| Link Analysis | <10 seconds | >90% | Cache utilization |

## Conclusion

The Social Tools MCP Server represents a comprehensive, production-ready implementation that demonstrates best practices for:

- **Error Handling**: Sophisticated categorization with user-friendly messages
- **Performance**: Optimized execution with appropriate caching and resource management  
- **Security**: Input validation, API key protection, and safe file operations
- **Integration**: Seamless Telegram integration with context awareness
- **Reliability**: Robust error recovery and graceful degradation

The server serves as both a functional tool suite and an architectural reference for developing high-quality MCP integrations.