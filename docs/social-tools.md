# Social Tools MCP Server Documentation

## Overview

The Social Tools MCP Server (`mcp_servers/social_tools.py`) provides web search, image generation, link analysis, voice transcription, and YouTube transcription capabilities for Claude Code integration. This server follows the **GOLD STANDARD wrapper pattern** by importing functions from standalone tools and adding MCP-specific concerns (context injection, validation, error handling).

## Architecture

```
MCP Wrapper → Standalone Implementation
├── search_current_info → tools/search_tool.py
├── create_image → tools/image_generation_tool.py
├── analyze_shared_image → tools/image_analysis_tool.py
├── save_link → tools/link_analysis_tool.py
├── search_links → tools/link_analysis_tool.py
├── transcribe_voice_message → tools/voice_transcription_tool.py
├── technical_analysis → Unique Claude Code delegation approach
└── YouTube transcription tools → integrations/youtube_transcription.py
```

## Tool Emoji Mappings

The system uses validated Telegram reaction emojis for consistent user experience:

```python
MCP_TOOL_EMOJIS = {
    "search_current_info": "🗿",        # moai - stone face, based, solid info
    "create_image": "🎉",               # party popper - let's gooo, celebration mode
    "transcribe_youtube_video": "🎥",   # movie camera - video content
    "transcribe_youtube_playlist": "📝", # memo - multiple transcriptions
    "search_youtube_transcriptions": "🔍", # magnifying glass - search functionality
    "learn_from_ai_video": "🧠",        # brain - learning and AI content
    "analyze_shared_image": "🤩",       # star eyes - shook, amazing, mind blown
    "save_link": "🍾",                  # champagne - we poppin bottles, saved successfully
    "search_links": "🔥",               # fire - that's fire, lit search results
    "transcribe_voice_message": "✍",    # writing hand - taking notes, documenting
    "technical_analysis": "🤓",         # nerd - big brain time, technical deep dive
    "manage_claude_code_sessions": "👨‍💻", # technologist - coding time, tech management
    "show_workspace_prime_content": "💯" # 100 - facts, complete info, real talk
}

STATUS_EMOJIS = {
    "done": "🫡",      # saluting - yes chief, copy that, respect
    "error": "🥴",     # woozy - drunk thoughts, confused, lost the plot
    "read_receipt": "👀" # eyes - I see you, watching this, acknowledged
}
```

## Tool Implementations

### 1. `search_current_info` - Web Search with Perplexity

**Purpose**: Search the web and return AI-synthesized answers using Perplexity API.

**Input Parameters**:
- `query` (str): The search query to execute
- `max_results` (int, default=3): Maximum number of results (not used with Perplexity, kept for compatibility)

**Validation**:
- Query must not be empty or only whitespace
- Query length must not exceed 500 characters

**Output Format**:
```
🔍 **{query}**

{AI-synthesized answer based on current web information}
```

**Error Handling**:
- Missing API key: "🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration."
- Empty query: "🔍 Search error: Please provide a search query."
- Query too long: "🔍 Search error: Query too long (maximum 500 characters)."
- API errors: "🔍 Search error: {error message}"

**API Integration**:
- Uses OpenAI client with Perplexity base URL
- Model: `sonar-pro`
- Temperature: 0.2
- Max tokens: 400
- Timeout: 180 seconds

**Performance**: Typical response time 1-3 seconds for web search queries.

### 2. `create_image` - Image Generation with DALL-E 3

**Purpose**: Generate images from text descriptions using OpenAI's DALL-E 3 model.

**Input Parameters**:
- `prompt` (str): Text description of the image to generate
- `size` (str, default="1024x1024"): Image size - "1024x1024", "1792x1024", or "1024x1792"
- `quality` (str, default="standard"): Image quality - "standard" or "hd"
- `style` (str, default="natural"): Image style - "natural" (realistic) or "vivid" (dramatic/artistic)
- `chat_id` (str, optional): Chat ID for context (injected from CONTEXT_DATA if available)

**Validation**:
- Prompt must not be empty or only whitespace
- Prompt length must not exceed 1000 characters

**Output Format**:
- With chat_id: `TELEGRAM_IMAGE_GENERATED|{image_path}|{chat_id}`
- Without chat_id: `{absolute_path_to_generated_image}`

**Error Handling**:
- Missing API key: "🎨 Image generation unavailable: Missing OPENAI_API_KEY configuration."
- Empty prompt: "🎨 Image generation error: Please provide a description for the image."
- Prompt too long: "🎨 Image generation error: Description too long (maximum 1000 characters)."
- API/generation errors: "🎨 Image generation error: {error message}"

**File Management**:
- Default save directory: `/tmp`
- Filename format: `generated_{sanitized_prompt}.png`
- Sanitization: Alphanumeric characters, spaces, hyphens, underscores only
- Maximum filename length: 50 characters from prompt

**Performance**: Generation time varies by quality setting (standard: 5-10s, HD: 10-20s).

### 3. `analyze_shared_image` - Image Analysis with GPT-4o Vision

**Purpose**: Analyze images using AI vision capabilities (GPT-4o) - the gold standard implementation.

**Input Parameters**:
- `image_path` (str): Path to the image file to analyze
- `question` (str, optional): Specific question about the image content
- `chat_id` (str, optional): Chat ID for context (injected from CONTEXT_DATA if available)

**Validation**:
- Image path must not be empty
- Supported formats: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`
- File must exist and be readable

**Output Format**:
- With question: `👁️ **Image Analysis**\n\n{answer}`
- Without question: `👁️ **What I see:**\n\n{description}`

**Error Handling Categories**:
1. **API Errors**: "👁️ OpenAI API error: {error message}"
2. **File Errors**: 
   - "👁️ Image analysis error: Image file not found."
   - "👁️ Image file error: Failed to read image file - {error message}"
3. **Encoding Errors**: "👁️ Image encoding error: Failed to process image format - {error message}"
4. **Format Errors**: "👁️ Image analysis error: Unsupported format '{extension}'. Supported: {formats}"
5. **General Errors**: "👁️ Image analysis error ({error_type}): {error message}"

**API Integration**:
- Model: `gpt-4o` (vision-capable)
- Temperature: 0.3
- Max tokens: 500
- Image encoding: Base64
- Context-aware prompting based on question/chat context

**Performance**: Typical analysis time 2-5 seconds depending on image size.

### 4. `save_link` - Link Analysis and Storage

**Purpose**: Save URLs with AI-generated analysis to the knowledge base.

**Input Parameters**:
- `url` (str): The URL to analyze and save
- `chat_id` (str, optional): Chat ID for context (injected from CONTEXT_DATA if available)
- `username` (str, optional): Username for context (injected from CONTEXT_DATA if available)

**Validation**:
- URL must have valid format (scheme and netloc)
- URL pattern matching via optimized regex

**Output Format**:
```
🔗 **Link Saved**: {domain}

✅ Successfully stored with AI analysis
```

**Database Schema**:
```sql
CREATE TABLE links (
    url TEXT PRIMARY KEY,
    domain TEXT,
    timestamp TEXT,
    analysis_result TEXT,
    analysis_status TEXT,
    title TEXT,
    main_topic TEXT,
    reasons_to_care TEXT,
    error_message TEXT,
    updated_at TEXT
)
```

**Analysis Structure**:
- **TITLE**: The actual title of the page/article
- **MAIN_TOPIC**: The primary subject matter in 1-2 sentences
- **REASONS_TO_CARE**: 2-3 bullet points explaining value/interest

**Caching**: Previously analyzed URLs are cached to avoid redundant API calls.

**Error Handling**:
- Invalid URL: Returns False
- Analysis errors stored in database with error status
- Generic errors: "🔗 Link save error: {error message}"

### 5. `search_links` - Search Stored Links

**Purpose**: Search through previously saved links by domain, content, or timestamp.

**Input Parameters**:
- `query` (str): Search query (domain name, URL content, title, or date pattern)
- `chat_id` (str, optional): Chat ID for context (injected from CONTEXT_DATA if available)
- `limit` (int, default=10): Maximum number of results to return

**Search Fields**:
- Domain (case-insensitive)
- URL (case-insensitive)
- Title (case-insensitive)
- Main topic (case-insensitive)
- Timestamp (date matching)

**Output Format**:
```
📂 **Found {count} link(s) matching '{query}':**

• **{domain}** ({date}) {status_emoji}
  {title}
  {url}
```

**Status Indicators**:
- ✅ = Successfully analyzed
- ❌ = Analysis failed

**Error Handling**:
- Database errors: "📂 Error reading stored links."
- No matches: "📂 No links found matching '{query}'"

### 6. `transcribe_voice_message` - Voice Transcription with Whisper

**Purpose**: Transcribe audio/voice files to text using OpenAI Whisper API.

**Input Parameters**:
- `file_path` (str): Path to the audio/voice file to transcribe
- `language` (str, optional): Language code for better accuracy (e.g., "en", "es", "fr", "de")
- `cleanup_file` (bool, default=False): Whether to delete the audio file after transcription
- `chat_id` (str, optional): Chat ID for context

**Supported Formats**: OGG, MP3, WAV, MP4, and other audio formats supported by Whisper.

**Output Format**:
- With chat_id: `🎙️ **Voice Transcription**\n\n{transcribed_text}`
- Without chat_id: `{transcribed_text}`

**Error Handling**:
- File not found: "🎙️ Audio file not found: {file_path}"
- Transcription errors: "🎙️ Voice transcription error: {error message}"

**File Cleanup**:
- Optional automatic deletion after successful transcription
- Cleanup also attempted on errors if requested

**Logging**:
- Detailed logging of file details (size, extension)
- Transcription preview in debug logs (first 50 chars)
- Cleanup status logging

### 7. `technical_analysis` - Claude Code Delegation

**Purpose**: Perform comprehensive technical research and analysis using Claude Code.

**Focus Areas**:
- Exploring codebases and understanding architectures
- Analyzing technical documentation and specifications
- Researching industry best practices and patterns
- Investigating technologies, frameworks, and tools
- Comparing different approaches and solutions
- Reading and analyzing files across projects

**Input Parameters**:
- `research_topic` (str): The technical topic or question to research
- `focus_areas` (str, optional): Specific areas to focus on
- `chat_id` (str, optional): Chat ID for workspace context

**Session Management**:
- Automatically continues recent sessions (within 2 hours)
- Stores session metadata for continuity
- Workspace-aware with prime content injection

**Output Format**:
```
🔬 **Technical Research Results**

{Claude Code analysis output}
```

**Error Recovery**:
- Timeout handling (2 hour limit)
- Process error recovery with detailed context
- SWE error recovery formatting

### 8. YouTube Transcription Tools

#### `transcribe_youtube_video`

**Purpose**: Transcribe a single YouTube video using transcribe-anything.

**Input Parameters**:
- `youtube_url` (str): YouTube video URL to transcribe
- `device` (str, default="cpu"): Transcription device ("cpu", "insane" for GPU, "mlx" for Apple Silicon)
- `batch_size` (int, default=16): Batch size for processing
- `verbose` (bool, default=True): Enable detailed progress output
- `flash` (bool, default=False): Use flash attention for compatible devices

**Output Format**:
```
🎥 **YouTube Video Transcribed**

**Title:** {title}
**Duration:** {duration}s
**Uploader:** {uploader}
**Device:** {device}
**Processing Time:** {time}s
**Word Count:** {count:,}

**Transcription:**
{full_transcription}
```

#### `transcribe_youtube_playlist`

**Purpose**: Batch transcribe YouTube playlists.

**Input Parameters**:
- `playlist_url` (str): YouTube playlist URL
- `device` (str, default="cpu"): Transcription device
- `max_videos` (int, default=10): Maximum videos to process
- `skip_existing` (bool, default=True): Skip already transcribed videos
- `batch_size` (int, default=16): Batch size for processing

**Output Summary**:
```
📝 **YouTube Playlist Transcribed**

**Videos Processed:** {count}
**Total Duration:** {duration}
**Total Words:** {words:,}
**Device Used:** {device}

**Transcribed Videos:**
1. {title} ({word_count:,} words)
...
```

#### `learn_from_ai_video`

**Purpose**: Transcribe and automatically learn from AI-focused YouTube videos.

**Special Features**:
- Automatic AI content categorization
- Key concept extraction
- Learning database integration
- Tag-based organization

**Output Includes**:
- Video metadata
- Category classification
- Key AI concepts identified
- Learning integration status
- Transcription preview

## Context Injection Pattern

The MCP server uses a context manager (`context_manager.py`) to handle chat context injection:

```python
def inject_context_for_tool(chat_id: str = "", username: str = ""):
    """Inject context parameters for MCP tools."""
    return context_manager.inject_context_params(chat_id, username)
```

**Context Storage**:
- Thread-safe singleton pattern
- Persistent JSON storage in `~/.cache/ai_agent/mcp_context.json`
- Environment variable fallbacks

**Context Fields**:
- `chat_id`: Current chat/conversation ID
- `username`: Current user's username
- `workspace`: Current workspace name
- Additional custom fields via kwargs

## Error Handling Patterns

### 1. **API Errors**
- Clear indication of which API failed
- Specific error messages for missing credentials
- Timeout handling with appropriate limits

### 2. **Encoding/Format Errors**
- File format validation before processing
- Base64 encoding error handling
- Clear format requirements in error messages

### 3. **File System Errors**
- File existence checks
- Read permission validation
- Cleanup on error conditions

### 4. **OS/System Errors**
- Process execution error handling
- Resource availability checks
- Graceful degradation

## Workspace Awareness and Security

### Workspace Resolution
- Uses `WorkspaceResolver` for directory mapping
- Chat ID to workspace association
- Group chat context handling

### Security Considerations
- Input validation on all user-provided data
- Path sanitization for file operations
- API key protection through environment variables
- Safe file naming with character restrictions

## Integration with Telegram

### Image Upload Pattern
When `chat_id` is provided, image generation returns a special format:
```
TELEGRAM_IMAGE_GENERATED|{image_path}|{chat_id}
```

This allows the Telegram bot to:
1. Parse the response format
2. Upload the image to the correct chat
3. Handle the file cleanup

### Voice Message Handling
- Temporary file management for voice downloads
- Automatic cleanup after transcription
- Language detection support

## Performance Characteristics

### Response Times
- **Web Search**: 1-3 seconds (Perplexity API)
- **Image Generation**: 5-20 seconds (DALL-E 3, quality-dependent)
- **Image Analysis**: 2-5 seconds (GPT-4o vision)
- **Link Analysis**: 2-4 seconds (Perplexity API + DB write)
- **Voice Transcription**: Variable (file size dependent)
- **YouTube Transcription**: Device-dependent (CPU: slower, GPU: faster)

### Resource Usage
- **Memory**: Minimal for most tools, higher for YouTube transcription
- **Storage**: Temporary files cleaned up automatically
- **Network**: API calls with 180-second timeouts
- **Database**: SQLite with optimized queries and indexing

## Best Practices

1. **Always use context injection** for chat-aware operations
2. **Validate inputs early** to provide clear error messages
3. **Use appropriate emojis** from the validated set
4. **Handle cleanup** for temporary files
5. **Implement caching** where appropriate (e.g., link analysis)
6. **Log important operations** for debugging
7. **Follow the GOLD STANDARD** wrapper pattern
8. **Provide meaningful error messages** with recovery suggestions