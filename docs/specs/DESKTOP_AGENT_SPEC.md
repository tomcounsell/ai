# Voice-Driven AI Agent Desktop Application
## Prototype Specification v1.0

---

## 1. Executive Summary

This document specifies a desktop application that enables users to create mini-applications through voice interaction with an AI agent. The system uses voice as the primary input mechanism, displays real-time visual feedback as applications are generated, and provides voice summaries of agent activity.

**Core User Flow:**
1. User speaks a request (e.g., "Create a calculator app")
2. System transcribes speech and sends to AI agent
3. Agent generates application code using local and remote tools
4. User sees mini-app build in real-time on screen
5. System provides voice summary of work completed
6. User can iterate with additional voice commands

---

## 2. System Architecture Overview

### 2.1 High-Level Components

```
┌─────────────────────────────────────────────────────────┐
│                    TAURI DESKTOP APP                     │
│  ┌──────────────┐  ┌────────────────────────────────┐  │
│  │              │  │                                │  │
│  │   Frontend   │  │      Rust Backend (Tauri)      │  │
│  │ (TypeScript) │  │  - File system access          │  │
│  │              │  │  - WebView management          │  │
│  │              │  │  - IPC bridge                  │  │
│  └──────────────┘  └────────────────────────────────┘  │
│         │                       │                        │
│         └───────────┬───────────┘                        │
└─────────────────────┼────────────────────────────────────┘
                      │
                      │ HTTPS / WebSocket
                      │
┌─────────────────────▼────────────────────────────────────┐
│                  DJANGO WEB SERVER                        │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Django REST API                                  │   │
│  │  - Authentication                                 │   │
│  │  - Voice transcription (Whisper API)             │   │
│  │  - TTS generation (OpenAI/ElevenLabs)            │   │
│  │  - Claude API proxy                              │   │
│  │  - MCP server hosting                            │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  WebSocket Server (Django Channels)              │   │
│  │  - Real-time progress updates                    │   │
│  │  - Mini-app code streaming                       │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  PostgreSQL Database                             │   │
│  │  - User accounts                                 │   │
│  │  - Mini-app metadata                             │   │
│  │  - Conversation history                          │   │
│  └──────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────┘
                      │
                      │ HTTPS
                      │
┌─────────────────────▼────────────────────────────────────┐
│              EXTERNAL SERVICES                            │
│  - Anthropic Claude API (claude-sonnet-4-5)              │
│  - OpenAI Whisper API (transcription)                    │
│  - OpenAI TTS API (speech synthesis)                     │
└───────────────────────────────────────────────────────────┘
```

### 2.2 Technology Stack

**Desktop Application:**
- **Framework:** Tauri v2 ([docs](https://v2.tauri.app/))
- **Frontend:** TypeScript + React (or Vue/Svelte)
- **Agent SDK:** TypeScript Claude Agent SDK ([docs](https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-typescript))
- **Build:** Tauri CLI + npm/pnpm

**Backend Server:**
- **Framework:** Django 5.x + Django REST Framework
- **WebSocket:** Django Channels ([docs](https://channels.readthedocs.io/))
- **Database:** PostgreSQL 15+
- **ASGI Server:** Daphne (for WebSocket support)

**External APIs:**
- **AI Agent:** Anthropic Claude API
- **Speech-to-Text:** OpenAI Whisper API
- **Text-to-Speech:** OpenAI TTS API
- **MCP Protocol:** Model Context Protocol ([spec](https://modelcontextprotocol.io/))

---

## 3. Frontend Architecture (Tauri Desktop App)

### 3.1 Responsibilities

The Tauri frontend is responsible for:
- **User Interface:** Voice recording, mini-app display, transcript viewing
- **Voice Input Capture:** Using Web Audio API
- **Agent Orchestration:** Running Claude Agent SDK locally
- **Local File Operations:** Reading/writing mini-app files via Tauri APIs
- **WebView Management:** Creating isolated environments for mini-apps
- **Backend Communication:** HTTP and WebSocket connections to Django

### 3.2 Core Modules

#### 3.2.1 Voice Input Manager
**Purpose:** Capture and process user voice input

**Key Functions:**
- `startRecording()`: Initiate voice capture
- `stopRecording()`: End capture and send to backend
- `sendAudioToBackend(audioBlob)`: POST audio to Django transcription endpoint

**Technologies:**
- Web Audio API ([MDN docs](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API))
- MediaRecorder API ([MDN docs](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder))

**Data Flow:**
```
User speaks → MediaRecorder captures → 
WebM/Opus blob → Base64 encode → 
POST to /api/voice/transcribe → 
Receive transcript
```

#### 3.2.2 Agent Orchestration Manager
**Purpose:** Run Claude Agent SDK with access to local and remote tools

**Key Functions:**
- `initializeAgent(config)`: Set up Agent SDK with MCP servers
- `runAgentTask(transcript, context)`: Execute agent with user input
- `handleToolExecution(tool, args)`: Route tool calls (local vs remote)

**Technologies:**
- Claude Agent SDK for TypeScript
- Tauri Command API for file system access

**Configuration Schema:**
```typescript
interface AgentConfig {
  claudeApiUrl: string;        // Django proxy endpoint
  systemPrompt: string;        // From Django
  mcpServers: MCPServerConfig[];
  allowedTools: string[];      // ['Read', 'Write', 'Bash']
  workingDirectory: string;    // Path to mini-apps folder
}

interface MCPServerConfig {
  name: string;
  url: string;                 // Django MCP endpoint
  transport: 'sse' | 'websocket';
  headers?: Record<string, string>;
}
```

**Agent SDK Integration:**
```typescript
// Reference: Claude Agent SDK TypeScript docs
import { ClaudeSDKClient, ClaudeAgentOptions } from '@claude/agent-sdk';

const options: ClaudeAgentOptions = {
  apiUrl: config.claudeApiUrl,
  systemPrompt: config.systemPrompt,
  mcpServers: config.mcpServers,
  allowedTools: config.allowedTools,
  workingDirectory: config.workingDirectory
};

const client = new ClaudeSDKClient(options);
```

#### 3.2.3 Mini-App Preview Manager
**Purpose:** Display and manage mini-app webviews

**Key Functions:**
- `createMiniAppWindow(appId, htmlPath)`: Open mini-app in new window
- `updateMiniAppPreview(appId, code)`: Hot-reload mini-app with new code
- `closeMiniApp(appId)`: Clean up webview resources

**Technologies:**
- Tauri Window API ([docs](https://v2.tauri.app/reference/javascript/api/namespacewindow/))
- Tauri WebviewWindow API

**Webview Configuration:**
```typescript
interface MiniAppWebviewConfig {
  appId: string;
  title: string;
  width: number;
  height: number;
  resizable: boolean;
  url: string;              // Path to local HTML file
  permissions: {
    allowScripts: boolean;
    allowSameOrigin: boolean;
    allowedDomains: string[];
  };
}
```

#### 3.2.4 Voice Output Manager
**Purpose:** Play TTS audio responses from backend

**Key Functions:**
- `playTTS(audioUrl)`: Play audio from URL
- `queueAudio(audioUrl)`: Add to playback queue
- `stopPlayback()`: Interrupt current audio

**Technologies:**
- HTML5 Audio API
- Fetch API for downloading audio files

#### 3.2.5 WebSocket Client
**Purpose:** Receive real-time updates from Django

**Key Functions:**
- `connect(taskId)`: Establish WebSocket connection
- `onMessage(handler)`: Register message handlers
- `disconnect()`: Clean up connection

**Message Types Received:**
```typescript
type WebSocketMessage = 
  | { type: 'mini_app_update', payload: MiniAppUpdate }
  | { type: 'agent_progress', payload: AgentProgress }
  | { type: 'voice_ready', payload: VoiceReady }
  | { type: 'error', payload: ErrorMessage };

interface MiniAppUpdate {
  appId: string;
  stage: 'scaffolding' | 'styling' | 'logic' | 'refinement' | 'complete';
  code: string;              // Full HTML/CSS/JS
  percentComplete: number;
  timestamp: string;
}

interface AgentProgress {
  message: string;
  currentTool?: string;
  status: 'thinking' | 'working' | 'complete';
}

interface VoiceReady {
  audioUrl: string;
  text: string;
  duration: number;
}
```

### 3.3 Tauri Backend (Rust Commands)

#### 3.3.1 File System Operations

**Commands:**
```rust
#[tauri::command]
async fn save_mini_app(
    app_id: String, 
    content: String, 
    app_data_dir: State<'_, PathBuf>
) -> Result<String, String>;

#[tauri::command]
async fn load_mini_app(
    app_id: String, 
    app_data_dir: State<'_, PathBuf>
) -> Result<String, String>;

#[tauri::command]
async fn list_mini_apps(
    app_data_dir: State<'_, PathBuf>
) -> Result<Vec<MiniAppMetadata>, String>;
```

**File Structure:**
```
~/Library/Application Support/YourApp/  (macOS)
~/.local/share/YourApp/                 (Linux)
C:\Users\{user}\AppData\Roaming\YourApp\ (Windows)
  └── mini-apps/
      ├── {app-id-1}/
      │   ├── index.html
      │   ├── manifest.json
      │   └── preview.png (optional)
      └── {app-id-2}/
          └── index.html
```

**Manifest Schema:**
```json
{
  "id": "uuid-v4",
  "name": "Calculator",
  "description": "Simple calculator app",
  "createdAt": "2025-10-06T10:30:00Z",
  "updatedAt": "2025-10-06T10:35:00Z",
  "permissions": {
    "network": false,
    "storage": false
  },
  "tags": ["utility", "math"]
}
```

#### 3.3.2 Window Management

**Commands:**
```rust
#[tauri::command]
async fn create_mini_app_window(
    app_handle: tauri::AppHandle,
    config: MiniAppWindowConfig
) -> Result<(), String>;
```

---

## 4. Backend Architecture (Django Server)

### 4.1 Responsibilities

The Django backend is responsible for:
- **User Authentication:** JWT-based auth
- **Voice Processing:** Transcription (Whisper) and TTS generation
- **Claude API Proxy:** Centralized API key management, rate limiting
- **MCP Server Hosting:** Expose custom tools to Agent SDK
- **Real-time Updates:** WebSocket connections for progress streaming
- **Data Persistence:** User accounts, mini-app metadata, conversation history

### 4.2 Django Apps Structure

```
myproject/
├── manage.py
├── myproject/
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py          # For Channels
├── accounts/            # User authentication
├── voice/               # Voice transcription & TTS
├── agent/               # Claude agent proxy & orchestration
├── mcp_servers/         # MCP server implementations
├── miniapps/            # Mini-app metadata & storage
└── realtime/            # WebSocket consumers
```

### 4.3 API Endpoints

#### 4.3.1 Authentication

**POST /api/auth/register**
```json
Request:
{
  "username": "string",
  "email": "string",
  "password": "string"
}

Response:
{
  "user": {
    "id": "uuid",
    "username": "string",
    "email": "string"
  },
  "token": "jwt-token"
}
```

**POST /api/auth/login**
```json
Request:
{
  "username": "string",
  "password": "string"
}

Response:
{
  "token": "jwt-token",
  "user": { /* user object */ }
}
```

#### 4.3.2 Voice Processing

**POST /api/voice/transcribe**
```json
Request:
{
  "audio": "base64-encoded-audio-data",
  "format": "webm" | "wav" | "mp3",
  "language": "en" (optional)
}

Response:
{
  "transcript": "string",
  "confidence": 0.95,
  "duration": 3.2,
  "processingTime": 0.8
}
```

**Implementation Notes:**
- Use OpenAI Whisper API ([docs](https://platform.openai.com/docs/guides/speech-to-text))
- Store audio temporarily, delete after transcription
- Support streaming for long recordings (future)

**POST /api/voice/synthesize**
```json
Request:
{
  "text": "string",
  "voice": "alloy" | "echo" | "fable" | "onyx" | "nova" | "shimmer",
  "speed": 1.0 (optional)
}

Response:
{
  "audioUrl": "https://your-cdn.com/tts/{uuid}.mp3",
  "duration": 5.2,
  "expiresAt": "2025-10-06T12:00:00Z"
}
```

**Implementation Notes:**
- Use OpenAI TTS API ([docs](https://platform.openai.com/docs/guides/text-to-speech))
- Store generated audio in Django media storage or S3
- Return signed URLs with expiration

#### 4.3.3 Agent Configuration

**GET /api/agent/config**
```json
Request Headers:
Authorization: Bearer {jwt-token}

Response:
{
  "systemPrompt": "You are an AI assistant that creates mini-applications...",
  "mcpServers": [
    {
      "name": "database-tools",
      "url": "https://your-django.com/mcp/database/sse",
      "transport": "sse",
      "headers": {
        "Authorization": "Bearer {jwt-token}"
      }
    },
    {
      "name": "custom-tools",
      "url": "https://your-django.com/mcp/custom/sse",
      "transport": "sse"
    }
  ],
  "allowedTools": ["Read", "Write", "Bash"],
  "maxTokens": 4000,
  "features": {
    "allowBash": true,
    "allowNetworkAccess": false
  }
}
```

#### 4.3.4 Agent Execution

**POST /api/agent/execute**
```json
Request:
{
  "transcript": "Create a calculator app",
  "context": {
    "currentAppId": "uuid" (optional),
    "conversationHistory": [ /* previous exchanges */ ]
  }
}

Response:
{
  "taskId": "uuid",
  "websocketUrl": "wss://your-django.com/ws/agent/{taskId}/"
}
```

**Implementation Notes:**
- Create async task for agent execution
- Return taskId immediately
- Client connects to WebSocket for streaming updates
- Agent runs in background with Django Q or Celery

#### 4.3.5 Mini-App Management

**GET /api/miniapps/**
```json
Response:
{
  "miniApps": [
    {
      "id": "uuid",
      "name": "Calculator",
      "description": "Simple calculator",
      "createdAt": "2025-10-06T10:30:00Z",
      "updatedAt": "2025-10-06T10:35:00Z",
      "tags": ["utility", "math"],
      "thumbnailUrl": "https://...",
      "syncStatus": "synced" | "local-only" | "conflict"
    }
  ]
}
```

**POST /api/miniapps/**
```json
Request:
{
  "name": "Calculator",
  "description": "Simple calculator app",
  "htmlContent": "<!DOCTYPE html>...",
  "tags": ["utility"]
}

Response:
{
  "miniApp": { /* mini-app object */ }
}
```

**GET /api/miniapps/{id}/**
**PUT /api/miniapps/{id}/**
**DELETE /api/miniapps/{id}/**

### 4.4 WebSocket Protocol (Django Channels)

#### 4.4.1 Connection

**URL:** `wss://your-django.com/ws/agent/{taskId}/`

**Authentication:** JWT token in query string or header

**Implementation:**
```python
# realtime/consumers.py
from channels.generic.websocket import AsyncWebsocketConsumer

class AgentProgressConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.task_id = self.scope['url_route']['kwargs']['task_id']
        # Verify user has access to this task
        # Join task-specific channel group
        await self.channel_layer.group_add(
            f"task_{self.task_id}",
            self.channel_name
        )
        await self.accept()
```

**Reference:** Django Channels documentation ([docs](https://channels.readthedocs.io/))

#### 4.4.2 Message Types

**Client → Server:**
```json
{
  "type": "cancel_task"
}
```

**Server → Client:**

*Mini-App Update:*
```json
{
  "type": "mini_app_update",
  "payload": {
    "appId": "uuid",
    "stage": "styling",
    "code": "<!DOCTYPE html>...",
    "percentComplete": 45,
    "timestamp": "2025-10-06T10:32:15Z"
  }
}
```

*Agent Progress:*
```json
{
  "type": "agent_progress",
  "payload": {
    "message": "Adding calculator button grid",
    "currentTool": "Write",
    "status": "working"
  }
}
```

*Voice Ready:*
```json
{
  "type": "voice_ready",
  "payload": {
    "audioUrl": "https://cdn.../tts/uuid.mp3",
    "text": "I've created your calculator app",
    "duration": 3.5
  }
}
```

*Task Complete:*
```json
{
  "type": "task_complete",
  "payload": {
    "appId": "uuid",
    "summary": "Successfully created calculator app",
    "fullTranscript": "..."
  }
}
```

*Error:*
```json
{
  "type": "error",
  "payload": {
    "code": "AGENT_ERROR",
    "message": "Failed to generate app",
    "details": "..."
  }
}
```

### 4.5 MCP Server Implementation

#### 4.5.1 MCP Server Architecture

**Purpose:** Expose Django-backed tools to Claude Agent SDK

**Transport:** Server-Sent Events (SSE) over HTTP

**Endpoints:**
- `GET /mcp/database/sse` - Database query tools
- `GET /mcp/custom/sse` - Custom business logic tools

**Implementation Reference:** 
- MCP Specification ([spec](https://modelcontextprotocol.io/))
- Python MCP SDK ([GitHub](https://github.com/anthropics/claude-agent-sdk-python))

#### 4.5.2 Example MCP Server: Database Tools

```python
# mcp_servers/database.py
from mcp import MCPServer, Tool, ToolResult

server = MCPServer("database-tools")

@server.tool()
async def query_user_data(
    table: str, 
    filters: dict,
    user: User  # Injected from auth middleware
) -> ToolResult:
    """
    Query user's database tables
    
    Args:
        table: Table name to query
        filters: Django ORM filters
    """
    # Verify user has access to this table
    if not user.has_perm(f'access_{table}'):
        return ToolResult.error("Permission denied")
    
    # Execute query with Django ORM
    queryset = apps.get_model('myapp', table).objects.filter(
        user=user,
        **filters
    )
    
    results = [obj.to_dict() for obj in queryset]
    
    return ToolResult.success({
        "count": len(results),
        "data": results
    })

@server.resource("user://documents/{doc_id}")
async def get_document(doc_id: str, user: User):
    """Retrieve user's document"""
    doc = Document.objects.get(id=doc_id, user=user)
    return {
        "content": doc.content,
        "metadata": doc.metadata
    }
```

**SSE Endpoint:**
```python
# mcp_servers/views.py
from django.http import StreamingHttpResponse

@api_view(['GET'])
def database_mcp_sse(request):
    """SSE endpoint for database MCP server"""
    user = request.user
    
    def event_stream():
        # MCP handshake and tool listing
        # Handle incoming tool calls
        # Stream responses
        pass
    
    return StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream'
    )
```

### 4.6 Database Schema

#### 4.6.1 User Model
```python
class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    preferences = models.JSONField(default=dict)
```

#### 4.6.2 MiniApp Model
```python
class MiniApp(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    html_content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tags = models.JSONField(default=list)
    is_public = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at']),
        ]
```

#### 4.6.3 ConversationHistory Model
```python
class ConversationHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    mini_app = models.ForeignKey(MiniApp, null=True, on_delete=models.SET_NULL)
    
    role = models.CharField(max_length=20)  # 'user' or 'assistant'
    content_type = models.CharField(max_length=20)  # 'text' or 'voice'
    
    text_content = models.TextField()
    audio_url = models.URLField(blank=True)
    
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp']
```

#### 4.6.4 AgentTask Model
```python
class AgentTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    mini_app = models.ForeignKey(MiniApp, null=True, on_delete=models.SET_NULL)
    
    transcript = models.TextField()
    context = models.JSONField(default=dict)
    
    status = models.CharField(max_length=20)  # 'pending', 'running', 'complete', 'failed'
    progress = models.IntegerField(default=0)  # 0-100
    
    result = models.JSONField(null=True)
    error = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    
    class Meta:
        ordering = ['-created_at']
```

---

## 5. Data Flow Specifications

### 5.1 Voice Input to Mini-App Creation Flow

```
┌─────────┐
│  User   │
│ Speaks  │
└────┬────┘
     │
     ▼
┌─────────────────────────────────────┐
│ Frontend: Voice Input Manager       │
│ - Capture audio (WebM/Opus)         │
│ - Convert to base64                 │
└────┬────────────────────────────────┘
     │
     │ POST /api/voice/transcribe
     │ { audio: "base64...", format: "webm" }
     ▼
┌─────────────────────────────────────┐
│ Django: Voice Transcription         │
│ - Decode base64                     │
│ - Call Whisper API                  │
│ - Return transcript                 │
└────┬────────────────────────────────┘
     │
     │ { transcript: "Create a calculator" }
     ▼
┌─────────────────────────────────────┐
│ Frontend: Agent Orchestration       │
│ - Receive transcript                │
│ - Get agent config from Django      │
│ - Initialize Claude Agent SDK       │
└────┬────────────────────────────────┘
     │
     │ POST /api/agent/execute
     │ { transcript: "...", context: {} }
     ▼
┌─────────────────────────────────────┐
│ Django: Create Agent Task           │
│ - Save task to database             │
│ - Return taskId and WebSocket URL   │
│ - Start async agent execution       │
└────┬────────────────────────────────┘
     │
     │ { taskId: "uuid", websocketUrl: "wss://..." }
     ▼
┌─────────────────────────────────────┐
│ Frontend: Connect to WebSocket      │
│ - Open WebSocket connection         │
│ - Listen for updates                │
└────┬────────────────────────────────┘
     │
     │ WebSocket connection established
     ▼
┌─────────────────────────────────────┐
│ Django: Agent Execution (Async)     │
│ - Initialize Claude Agent           │
│ - Agent uses local tools (via SDK)  │
│ - Agent uses remote tools (MCP)     │
│ - Stream progress via WebSocket     │
└────┬────────────────────────────────┘
     │
     │ Multiple WebSocket messages:
     │ - mini_app_update (with code)
     │ - agent_progress
     │ - voice_ready (TTS URL)
     ▼
┌─────────────────────────────────────┐
│ Frontend: Process Updates           │
│ - Receive code updates              │
│ - Update mini-app preview           │
│ - Play TTS audio                    │
│ - Show progress indicators          │
└────┬────────────────────────────────┘
     │
     │ Agent writes final files
     ▼
┌─────────────────────────────────────┐
│ Tauri: File System                  │
│ - Agent SDK writes to disk          │
│ - Files saved in mini-apps/{id}/    │
└────┬────────────────────────────────┘
     │
     │ task_complete message
     ▼
┌─────────────────────────────────────┐
│ Frontend: Task Complete             │
│ - Save metadata to Django           │
│ - Display completed mini-app        │
│ - Enable user to launch app         │
└─────────────────────────────────────┘
```

### 5.2 Agent Tool Execution Routing

**Local Tools (executed by Agent SDK in Tauri):**
- `Read`: Read local files
- `Write`: Write local files  
- `Bash`: Execute shell commands
- `ListDirectory`: List files in directory

**Remote Tools (executed via MCP on Django):**
- `QueryDatabase`: Query user's database
- `FetchUserDocument`: Retrieve user documents
- `SaveToCloud`: Sync data to Django
- Custom business logic tools

**Routing Logic:**
```typescript
// In Frontend Agent Orchestration Manager
async function handleToolCall(toolName: string, args: any) {
  const localTools = ['Read', 'Write', 'Bash', 'ListDirectory'];
  
  if (localTools.includes(toolName)) {
    // Execute locally via Agent SDK
    return await agentSDK.executeLocalTool(toolName, args);
  } else {
    // Route to Django MCP server
    return await mcpClient.callTool(toolName, args);
  }
}
```

---

## 6. Security Considerations

### 6.1 Authentication & Authorization

**Frontend:**
- Store JWT token in secure storage (Tauri Store plugin)
- Include token in all API requests
- Refresh token mechanism for long sessions

**Backend:**
- JWT-based authentication
- Token expiration: 24 hours
- Refresh tokens: 7 days
- Rate limiting on auth endpoints

### 6.2 Mini-App Sandboxing

**Webview Isolation:**
- Each mini-app runs in isolated webview
- Content Security Policy (CSP) headers
- No access to parent window globals
- Restricted API access

**Permissions System:**
```json
{
  "permissions": {
    "network": false,
    "storage": false,
    "fileSystem": false
  }
}
```

### 6.3 API Security

**Django Backend:**
- HTTPS only in production
- CORS configuration for Tauri app origin
- API rate limiting (Django REST Framework throttling)
- Input validation on all endpoints

**Claude API Proxy:**
- API keys stored as environment variables
- Never exposed to frontend
- Per-user usage tracking
- Rate limiting per user

---

## 7. Error Handling

### 7.1 Frontend Error States

**Voice Input Errors:**
- Microphone permission denied
- Audio encoding failure
- Network timeout during upload
- Transcription service unavailable

**Agent Errors:**
- Agent SDK initialization failure
- Tool execution errors
- WebSocket connection lost
- File system permission issues

**User Feedback:**
- Toast notifications for transient errors
- Modal dialogs for critical errors
- Retry mechanisms with exponential backoff
- Graceful degradation (e.g., text input fallback)

### 7.2 Backend Error Responses

**Standard Error Format:**
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {},
    "timestamp": "2025-10-06T10:30:00Z"
  }
}
```

**HTTP Status Codes:**
- 400: Bad Request (validation errors)
- 401: Unauthorized (auth required)
- 403: Forbidden (insufficient permissions)
- 404: Not Found
- 429: Too Many Requests (rate limited)
- 500: Internal Server Error
- 503: Service Unavailable (external API down)

---

## 8. Development Environment Setup

### 8.1 Frontend Setup

**Prerequisites:**
- Node.js 18+
- Rust 1.70+
- Tauri CLI

**Installation:**
```bash
# Install Tauri CLI
cargo install tauri-cli

# Install dependencies
npm install

# Install Claude Agent SDK
npm install @claude/agent-sdk

# Run in development
npm run tauri dev
```

**Environment Variables:**
```env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
```

### 8.2 Backend Setup

**Prerequisites:**
- Python 3.10+
- PostgreSQL 15+
- Redis (for Channels layer)

**Installation:**
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver

# Start Channels worker
daphne -b 0.0.0.0 -p 8001 myproject.asgi:application
```

**Environment Variables:**
```env
DJANGO_SECRET_KEY=...
DATABASE_URL=postgresql://...
REDIS_URL=redis://localhost:6379

ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:1420
```

---

## 9. Testing Strategy

### 9.1 Frontend Testing

**Unit Tests:**
- Voice input encoding/decoding
- WebSocket message parsing
- Agent SDK configuration
- File system operations

**Integration Tests:**
- Voice input → Transcription flow
- Agent execution → Mini-app creation
- WebSocket real-time updates
- Multi-window management

**E2E Tests:**
- Full voice-to-mini-app workflow
- User authentication flow
- Error recovery scenarios

**Tools:**
- Vitest for unit tests
- Playwright for E2E tests

### 9.2 Backend Testing

**Unit Tests:**
- API endpoint logic
- MCP server tools
- Database models
- Serializers/validators

**Integration Tests:**
- Whisper API integration
- Claude API integration
- WebSocket consumers
- Agent task execution

**Load Tests:**
- Concurrent agent tasks
- WebSocket connection limits
- API rate limiting

**Tools:**
- pytest + pytest-django
- pytest-asyncio for async tests
- Factory Boy for test data

---

## 10. Deployment Considerations

### 10.1 Frontend Deployment

**Build Process:**
```bash
npm run tauri build
```

**Outputs:**
- `.app` bundle (macOS)
- `.exe` installer (Windows)
- `.AppImage` or `.deb` (Linux)

**Code Signing:**
- macOS: Apple Developer certificate
- Windows: Code signing certificate
- Required for auto-updater

**Distribution:**
- GitHub Releases for auto-updater
- Direct download links
- Future: App stores (Mac App Store, Microsoft Store)

### 10.2 Backend Deployment

**Infrastructure:**
- Django + Gunicorn for HTTP
- Daphne for WebSocket
- Nginx as reverse proxy
- PostgreSQL database
- Redis for Channels

**Scaling Considerations:**
- Horizontal scaling of web servers
- Separate WebSocket servers
- Database connection pooling
- Celery for background tasks (agent execution)

**Monitoring:**
- Application logs (Django logging)
- Error tracking (Sentry)
- Performance monitoring (New Relic/DataDog)
- WebSocket connection metrics

---

## 11. Future Enhancements (Out of Scope for Prototype)

- Multi-language support (i18n)
- Collaborative editing of mini-apps
- Mini-app marketplace for sharing
- Advanced voice commands (interruption, corrections)
- Offline mode with local models
- Performance optimizations (code caching, lazy loading)
- Advanced security (code scanning, permission requests)
- Analytics and usage tracking
- A/B testing framework
- Mobile companion app

---

## 12. References

### Documentation Links

**Tauri:**
- Official Docs: https://v2.tauri.app/
- Window API: https://v2.tauri.app/reference/javascript/api/namespacewindow/
- Auto-Updater: https://v2.tauri.app/plugin/updater/

**Claude Agent SDK:**
- TypeScript SDK: https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-typescript
- Python SDK: https://github.com/anthropics/claude-agent-sdk-python

**Model Context Protocol:**
- Specification: https://modelcontextprotocol.io/
- Python SDK: https://github.com/modelcontextprotocol/python-sdk

**Django:**
- Django: https://docs.djangoproject.com/
- Django REST Framework: https://www.django-rest-framework.org/
- Django Channels: https://channels.readthedocs.io/

**External APIs:**
- OpenAI Whisper: https://platform.openai.com/docs/guides/speech-to-text
- OpenAI TTS: https://platform.openai.com/docs/guides/text-to-speech
- Anthropic Claude API: https://docs.anthropic.com/

---

## 13. Glossary

**Agent SDK**: Claude Agent SDK - Framework for building AI agents with Claude
**MCP**: Model Context Protocol - Standard for connecting AI to external tools/data
**SSE**: Server-Sent Events - HTTP protocol for server-to-client streaming
**TTS**: Text-to-Speech - Converting text to audio
**STT/ASR**: Speech-to-Text / Automatic Speech Recognition
**JWT**: JSON Web Token - Authentication token format
**IPC**: Inter-Process Communication - Communication between app components
**CSP**: Content Security Policy - Web security standard

---

**Document Version:** 1.0  
**Last Updated:** October 6, 2025  
**Authors:** Engineering Team  
**Status:** Draft for Review
