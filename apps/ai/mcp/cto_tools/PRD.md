# CTO Tools MCP Server - Product Requirements Document

**Version:** 1.1.0
**Last Updated:** December 9, 2025
**Status:** Production
**URL:** https://ai.yuda.me/mcp/cto-tools/serve

## Table of Contents

1. [Product Overview](#product-overview)
2. [Technical Architecture](#technical-architecture)
3. [OAuth 2.0 Authentication](#oauth-20-authentication)
4. [MCP Protocol Implementation](#mcp-protocol-implementation)
5. [Tools Specification](#tools-specification)
6. [Django Views Implementation](#django-views-implementation)
7. [Testing Requirements](#testing-requirements)
8. [Deployment Specification](#deployment-specification)
9. [Security Considerations](#security-considerations)
10. [Future Enhancements](#future-enhancements)

---

## Product Overview

### Purpose

The CTO Tools MCP server provides engineering leadership and security tools accessible via the Model Context Protocol (MCP). It enables CTOs and engineering leaders to interact with AI assistants (like Claude Code) to perform engineering management tasks including team reviews, architecture documentation, and security risk correlation.

### Target Users

- **Primary**: CTOs, VPs of Engineering, Engineering Managers
- **Secondary**: Security Engineers, DevOps Engineers, Technical Leads

### Key Features

1. **Weekly Review Framework** - Structured process for conducting team commit reviews
2. **Architecture Review Framework** - Guided architecture documentation with diagrams
3. **Security Risk Correlation** - Cross-tool security alert analysis (planned)
4. **OAuth 2.0 + PKCE Authentication** - Secure, auto-approve authentication for Claude Code
5. **HTTP Transport** - Accessible via HTTPS for remote MCP connections

### Business Value

- **Time Savings**: Reduces weekly review time from 2 hours to 30 minutes
- **Consistency**: Ensures standardized review format across all teams
- **Accessibility**: Makes technical work understandable to non-technical stakeholders
- **Security**: Proactive risk identification across multiple security tools

---

## Technical Architecture

### System Components

```mermaid
graph TB
    subgraph "Claude Code Client"
        Client[MCP Client]
    end

    subgraph "ai.yuda.me (Django)"
        OAuth[OAuth Server<br/>RFC 7636 + 7591 + 8414]
        MCPView[MCP Server View<br/>JSON-RPC 2.0]
        Tools[FastMCP Tools]
    end

    subgraph "External Systems (Future)"
        SAST[SAST Tools]
        CSPM[CSPM Tools]
        ThreatIntel[Threat Intel]
    end

    Client -->|1. OAuth Discovery| OAuth
    Client -->|2. Token Exchange| OAuth
    Client -->|3. MCP Requests + Bearer| MCPView
    MCPView --> Tools
    Tools -.->|Future| SAST
    Tools -.->|Future| CSPM
    Tools -.->|Future| ThreatIntel
```

### Technology Stack

- **Backend Framework**: Django 5.2.5
- **MCP Implementation**: FastMCP (FastAPI-based)
- **Python Version**: 3.12+
- **Transport Protocol**: HTTP (streamable-http mode)
- **Authentication**: OAuth 2.0 with PKCE (RFC 7636)
- **Protocol**: JSON-RPC 2.0 over HTTP
- **Deployment**: Render (auto-deploy from main branch)

### Directory Structure

```
apps/ai/
├── mcp/
│   ├── cto_tools_server.py          # FastMCP server implementation
│   ├── cto_tools_manifest.json      # MCP manifest for discovery
│   ├── cto-tools/
│   │   └── PRD.md                   # This document
│   └── security/                    # Security review components (future)
│       ├── correlation_engine.py
│       ├── risk_scorer.py
│       └── connector_registry.py
├── views/
│   ├── mcp_server_views.py          # Django MCP protocol handlers
│   └── mcp_oauth_views.py           # OAuth 2.0 endpoints
├── tests/
│   ├── test_mcp_server_views.py     # MCP protocol tests (16 tests)
│   └── test_mcp_oauth_flow.py       # OAuth flow tests (21 tests)
└── urls.py                          # URL routing
```

---

## OAuth 2.0 Authentication

### Overview

The server implements OAuth 2.0 with PKCE (Proof Key for Code Exchange) using the S256 challenge method. This provides secure authentication while maintaining an "auto-approve" flow that doesn't require manual user authorization.

### Supported RFCs

- **RFC 7636**: PKCE (Proof Key for Code Exchange)
- **RFC 7591**: Dynamic Client Registration
- **RFC 8414**: OAuth Authorization Server Metadata

### OAuth Flow

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant Meta as /.well-known/oauth-authorization-server
    participant Reg as /mcp/oauth/register
    participant Auth as /mcp/oauth/authorize
    participant Token as /mcp/oauth/token
    participant MCP as /mcp/cto-tools/serve

    CC->>Meta: GET metadata
    Meta-->>CC: {registration_endpoint, code_challenge_methods_supported: ["S256"], ...}

    CC->>Reg: POST {redirect_uris}
    Reg-->>CC: {client_id, client_id_issued_at, ...}

    CC->>Auth: GET ?client_id&redirect_uri&code_challenge&code_challenge_method=S256
    Auth-->>CC: 302 redirect with auth code

    CC->>Token: POST {grant_type, code, code_verifier}
    Token-->>CC: {access_token, token_type: "Bearer", expires_in, scope}

    CC->>MCP: POST initialize (Authorization: Bearer {access_token})
    MCP-->>CC: {result: {protocolVersion, capabilities, serverInfo}}

    CC->>MCP: POST notifications/initialized
    MCP-->>CC: {jsonrpc: "2.0"}

    CC->>MCP: POST tools/list
    MCP-->>CC: {result: {tools: [...]}}
```

### Endpoint Specifications

#### 1. OAuth Metadata Discovery

**Endpoint**: `GET /.well-known/oauth-authorization-server`

**Response** (RFC 8414):
```json
{
  "issuer": "https://ai.yuda.me",
  "authorization_endpoint": "https://ai.yuda.me/mcp/oauth/authorize",
  "token_endpoint": "https://ai.yuda.me/mcp/oauth/token",
  "registration_endpoint": "https://ai.yuda.me/mcp/oauth/register",
  "scopes_supported": ["mcp:read", "mcp:write"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "token_endpoint_auth_methods_supported": ["none"],
  "code_challenge_methods_supported": ["S256"]
}
```

#### 2. Dynamic Client Registration

**Endpoint**: `POST /mcp/oauth/register`

**Request**:
```json
{
  "redirect_uris": ["http://localhost:3000/callback"]
}
```

**Response** (RFC 7591):
```json
{
  "client_id": "randomly_generated_32_byte_token",
  "client_id_issued_at": 1733734800,
  "redirect_uris": ["http://localhost:3000/callback"],
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

#### 3. Authorization Endpoint

**Endpoint**: `GET /mcp/oauth/authorize`

**Query Parameters**:
- `client_id` (required): Client identifier from registration
- `redirect_uri` (required): Callback URL
- `response_type` (required): Must be "code"
- `state` (optional but recommended): CSRF protection token
- `code_challenge` (required for PKCE): SHA256 hash of code_verifier
- `code_challenge_method` (required): Must be "S256"

**Response**: HTTP 302 redirect to `{redirect_uri}?code={auth_code}&state={state}`

**Auto-Approve Behavior**: Server immediately generates authorization code without user interaction.

#### 4. Token Endpoint

**Endpoint**: `POST /mcp/oauth/token`

**Request**:
```json
{
  "grant_type": "authorization_code",
  "code": "auth_code_from_authorize_endpoint",
  "redirect_uri": "http://localhost:3000/callback",
  "code_verifier": "original_random_string_before_hashing"
}
```

**Response**:
```json
{
  "access_token": "randomly_generated_32_byte_token",
  "token_type": "Bearer",
  "expires_in": 31536000,
  "scope": "mcp:read mcp:write"
}
```

**Token Lifetime**: 1 year (31,536,000 seconds)

**PKCE Verification**: In auto-approve mode, code_verifier is accepted without validation. In production with real user authorization, server would verify: `SHA256(code_verifier) == stored_code_challenge`

### Security Model

- **Auto-Approve**: Server automatically approves all OAuth requests without user consent screen
- **Stateless**: No session storage - each request is independent
- **No Secrets**: Public clients (Claude Code) don't use client_secret
- **PKCE Required**: S256 challenge method protects against authorization code interception
- **Short-Lived Codes**: Authorization codes are single-use (not enforced in auto-approve mode)
- **Bearer Tokens**: Access tokens must be included in `Authorization: Bearer {token}` header

### Implementation Files

- **Views**: `/apps/ai/views/mcp_oauth_views.py`
  - `MCPOAuthMetadataView` - Metadata discovery
  - `MCPOAuthRegistrationView` - Client registration
  - `MCPOAuthAuthorizeView` - Authorization endpoint
  - `MCPOAuthTokenView` - Token endpoint
- **URLs**: `/apps/ai/urls.py` - Route mapping
- **Settings**: `/settings/urls.py` - `.well-known` route mapping

---

## MCP Protocol Implementation

### Protocol Overview

The Model Context Protocol (MCP) is a JSON-RPC 2.0 based protocol that enables AI assistants to call tools and access resources. Our implementation uses HTTP transport for remote access.

### MCP Server Initialization Sequence

```mermaid
sequenceDiagram
    participant Client as Claude Code
    participant Server as /mcp/cto-tools/serve

    Note over Client,Server: Phase 1: Discovery
    Client->>Server: GET /mcp/cto-tools/serve
    Server-->>Client: {name, version, protocol, authentication: true}

    Note over Client,Server: Phase 2: Initialize (with Bearer token)
    Client->>Server: POST initialize {protocolVersion, capabilities, clientInfo}
    Server-->>Client: {result: {protocolVersion, capabilities, serverInfo}}

    Note over Client,Server: Phase 3: Notification (CRITICAL)
    Client->>Server: POST notifications/initialized {}
    Server-->>Client: {jsonrpc: "2.0"}

    Note over Client,Server: Phase 4: Operations
    Client->>Server: POST tools/list {}
    Server-->>Client: {result: {tools: [...]}}

    Client->>Server: POST tools/call {name, arguments}
    Server-->>Client: {result: {content: [...]}}
```

### Endpoint: GET /mcp/cto-tools/serve

**Purpose**: Server discovery and metadata

**Response**:
```json
{
  "name": "cto-tools",
  "version": "1.1.0",
  "description": "MCP server for CTO and engineering leadership tools",
  "protocol": "MCP",
  "authentication": true,
  "endpoint": "https://ai.yuda.me/mcp/cto-tools/serve"
}
```

**CRITICAL**: `authentication: true` must match OAuth manifest configuration. Mismatch causes connection failures.

### Endpoint: POST /mcp/cto-tools/serve

**Purpose**: Handle all MCP JSON-RPC requests

**Authentication**: Required - `Authorization: Bearer {access_token}` header

**Methods Supported**:
- `initialize` - Start MCP session
- `tools/list` - Get available tools
- `tools/call` - Execute a tool
- `notifications/initialized` - Client readiness signal
- `notifications/*` - Other notifications (ignored)

### JSON-RPC 2.0 Format

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "claude-code",
      "version": "1.0"
    }
  },
  "id": 1
}
```

**Success Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {}
    },
    "serverInfo": {
      "name": "cto-tools",
      "version": "1.0.0"
    }
  }
}
```

**Error Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32601,
    "message": "Method not found: invalid/method"
  }
}
```

### Error Codes (JSON-RPC 2.0)

| Code | Meaning | HTTP Status |
|------|---------|-------------|
| -32700 | Parse error | 400 |
| -32600 | Invalid Request | 400 |
| -32601 | Method not found | 400 |
| -32602 | Invalid params | 400 |
| -32603 | Internal error | 500 |
| -32000 | Authentication required | 401 |

### Method: initialize

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "client-name",
      "version": "1.0"
    }
  },
  "id": 1
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": {}
    },
    "serverInfo": {
      "name": "cto-tools",
      "version": "1.0.0"
    }
  }
}
```

### Method: notifications/initialized

**Purpose**: Client signals it's ready to receive server-initiated notifications

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized",
  "params": {}
}
```

**Response**:
```json
{
  "jsonrpc": "2.0"
}
```

**CRITICAL**: This notification is sent AFTER initialize and MUST return 200. Rejecting this (returning 400) causes connection failure in Claude Code. This was a production bug that took 4 iterations to discover.

### Method: tools/list

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "params": {},
  "id": 2
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "weekly_review",
        "description": "Provides a structured framework for conducting weekly engineering team reviews...",
        "inputSchema": {
          "type": "object",
          "properties": {
            "days": {
              "type": "integer",
              "description": "Number of days to review",
              "default": 7
            },
            "categories": {
              "type": "integer",
              "description": "Number of work categories",
              "default": 5
            }
          },
          "required": []
        }
      }
    ]
  }
}
```

### Method: tools/call

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "weekly_review",
    "arguments": {
      "days": 7,
      "categories": 5
    }
  },
  "id": 3
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "# Engineering Team Review Framework\n\n..."
      }
    ]
  }
}
```

### Implementation Files

- **Django View**: `/apps/ai/views/mcp_server_views.py`
  - Class: `CTOToolsMCPServerView`
  - GET handler: Returns server metadata
  - POST handler: Routes JSON-RPC methods
- **FastMCP Server**: `/apps/ai/mcp/cto_tools_server.py`
  - Defines tools using `@mcp.tool()` decorator
  - Runs in streamable-http mode

---

## Tools Specification

### Tool 1: weekly_review

**Purpose**: Provides a structured framework for conducting weekly engineering team reviews

**Category**: Leadership

**Function Signature**:
```python
def weekly_review(
    days: int = 7,
    categories: int = 5
) -> str
```

**Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| days | int | 7 | Number of days to review |
| categories | int | 5 | Number of work categories to organize output |

**Returns**: Markdown-formatted instructions for conducting the review

**Output Format**:
```markdown
# Engineering Team Review Framework

## GOAL
[Description of output format and goals]

## PHASE 1: GATHER DATA
[Git commands to run]

## PHASE 2: ANALYZE INTERNALLY
[Analysis guidelines]

## PHASE 3: WRITE THE FINAL SUMMARY
[Output template with emojis and categories]

## WRITING GUIDELINES
[Formatting rules]
```

**Key Features**:
- Three-phase approach: Gather → Analyze → Write
- Git commands for commit history analysis
- Emoji-based categorization (🔐, 🔌, 💬, etc.)
- Plain-text output suitable for copy-paste
- Stakeholder-friendly language (no technical jargon)
- Team statistics with contributor breakdown

**Design Philosophy**:
- **Concise Output**: 2-5 bullets per category, not verbose analysis
- **Business Impact**: Focus on "why it matters" not "what changed"
- **Accessibility**: Understandable by non-technical stakeholders
- **Flexibility**: Works with any codebase and tech stack

### Tool 2: architecture_review

**Purpose**: Provides a structured framework for conducting architecture reviews

**Category**: Leadership

**Function Signature**:
```python
def architecture_review(
    focus: str = "system",
    depth: Literal["overview", "detailed", "deep-dive"] = "detailed",
    include_diagrams: bool = True
) -> str
```

**Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| focus | str | "system" | Area to focus: "system", "api", "data", "security", or component name |
| depth | Literal | "detailed" | Detail level: "overview", "detailed", "deep-dive" |
| include_diagrams | bool | True | Whether to include Mermaid diagram examples |

**Returns**: Markdown-formatted architecture review template

**Output Structure**:
- Executive Summary
- System Overview with C4 diagrams
- Key Components breakdown
- Key Flows with sequence diagrams
- Design Patterns table
- Data Architecture
- Security Architecture
- Scalability & Performance
- Strengths and Areas for Improvement
- Recommendations (short/medium/long-term)

**Diagram Types**:
- **C4 Model**: System Context, Container, Component diagrams
- **Sequence Diagrams**: For key flows
- **Data Flow Diagrams**: For data architecture
- All diagrams use Mermaid syntax for GitHub/GitLab rendering

**Output Locations**:
1. **Preferred**: `docs/architecture/` in repository (versioned with code)
2. **Alternative**: Claude Artifact, `/tmp/` file, or inline

### Tool 3: security_review

**Purpose**: Correlate security alerts across multiple tools with policy context

**Category**: Security

**Status**: Partially implemented (correlation engine is planned)

**Function Signature**:
```python
async def security_review(
    query: str,
    time_window_hours: int = 72,
    min_severity: Literal["Low", "Medium", "High", "Critical"] = "Medium",
    data_types: list[str] | None = None,
    create_tickets: bool = False,
    max_results: int = 10
) -> str
```

**Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| query | str | (required) | Natural language query (e.g., "top 3 high-severity PII risks") |
| time_window_hours | int | 72 | How many hours back to review |
| min_severity | Literal | "Medium" | Minimum severity to include |
| data_types | list[str] | None | Filter by data types (e.g., ["PII", "credentials"]) |
| create_tickets | bool | False | Create Linear tickets (not yet implemented) |
| max_results | int | 10 | Maximum risks to return |

**Response Schema**:
```python
class SecurityReviewResponse(BaseModel):
    summary: str
    risks: list[Risk]
    total_alerts_reviewed: int
    correlation_confidence: float  # 0-1 scale
    timestamp: str  # ISO format
```

**Risk Schema**:
```python
class Risk(BaseModel):
    risk_id: str
    severity: Literal["Low", "Medium", "High", "Critical"]
    title: str
    description: str
    linked_alerts: list[str]
    policy_violations: list[str]
    affected_assets: list[str]
    actions: list[RiskAction]
    ticket_id: str | None
    score: float  # 0-100 scale
```

**Integration Points** (Future):
- SAST tools (Semgrep, CodeQL, Snyk)
- DAST tools (OWASP ZAP, Burp)
- CSPM tools (Prisma Cloud, Wiz)
- Threat Intelligence feeds
- Policy document sources (Confluence, SharePoint)
- Linear (for ticket creation)

**Implementation Status**:
- ✅ Tool signature and schema defined
- ✅ Response formatting
- ⏳ Correlation engine (planned)
- ⏳ Connector registry (planned)
- ⏳ Risk scorer (planned)
- ❌ Linear integration (requires user auth)

### Tool 4: list_connectors

**Purpose**: List all available security tool connectors and their status

**Category**: Security

**Status**: Planned

**Function Signature**:
```python
async def list_connectors() -> str
```

**Returns**: Formatted list of connectors with status

**Output Format**:
```markdown
## Available Security Tool Connectors

### ✅ Semgrep SAST
**Type**: sast
**Status**: connected
**Capabilities**: code_scanning, vulnerability_detection
**Last Sync**: 2025-12-09T10:30:00Z

### ❌ Prisma Cloud CSPM
**Type**: cspm
**Status**: disconnected
```

### Tool 5: configure_connector

**Purpose**: Configure a new security tool connector

**Category**: Security

**Status**: Planned

**Function Signature**:
```python
async def configure_connector(
    connector_type: Literal["sast", "dast", "cspm", "threat_intel", "policy"],
    connector_name: str,
    api_key: str,
    api_url: str | None = None,
    additional_config: dict | None = None
) -> str
```

**Security Considerations**:
- API keys must be encrypted at rest
- Requires secure credential storage (e.g., HashiCorp Vault)
- Multi-tenant support required for production use

---

## Django Views Implementation

### File: /apps/ai/views/mcp_server_views.py

**Class**: `CTOToolsMCPServerView`

**Decorators**: `@method_decorator(csrf_exempt, name="dispatch")`

**Methods**:

#### 1. GET Handler

```python
def get(self, request):
    """Return server metadata for MCP discovery."""
    return JsonResponse({
        "name": "cto-tools",
        "version": "1.1.0",
        "description": "MCP server for CTO and engineering leadership tools",
        "protocol": "MCP",
        "authentication": True,  # CRITICAL: Must match OAuth manifest
        "endpoint": request.build_absolute_uri()
    })
```

#### 2. POST Handler

```python
def post(self, request):
    """Handle MCP JSON-RPC requests."""

    # PHASE 1: Validate Bearer token
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32000,
                "message": "Authentication required - missing or invalid Bearer token"
            }
        }, status=401)

    # PHASE 2: Parse JSON-RPC request
    try:
        mcp_request = json.loads(request.body)
        method = mcp_request.get("method")
        params = mcp_request.get("params", {})
        request_id = mcp_request.get("id")
    except json.JSONDecodeError as e:
        return JsonResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"}
        }, status=400)

    # PHASE 3: Route to method handler
    if method == "initialize":
        result = self._handle_initialize(params)
    elif method == "tools/list":
        result = self._handle_tools_list()
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        result = asyncio.run(self._handle_tool_call(tool_name, arguments))
    elif method.startswith("notifications/"):
        # CRITICAL: Must handle notifications or connection fails
        logger.info(f"Received notification: {method}")
        return JsonResponse({"jsonrpc": "2.0"}, status=200)
    else:
        return JsonResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }, status=400)

    # PHASE 4: Return successful response
    return JsonResponse({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    })
```

#### 3. Helper Methods

```python
def _handle_initialize(self, params):
    """Handle MCP initialize request."""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": "cto-tools",
            "version": "1.0.0"
        }
    }

def _handle_tools_list(self):
    """Return available tools."""
    return {
        "tools": [
            {
                "name": "weekly_review",
                "description": "...",
                "inputSchema": {...}
            },
            # ... other tools
        ]
    }

async def _handle_tool_call(self, tool_name, arguments):
    """Execute requested tool."""
    from apps.ai.mcp.cto_tools_server import weekly_review

    if tool_name == "weekly_review":
        result = weekly_review(**arguments)
    else:
        raise ValueError(f"Unknown tool: {tool_name}")

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(result, indent=2)
        }]
    }
```

### File: /apps/ai/views/mcp_oauth_views.py

**Classes**:
- `MCPOAuthMetadataView` - OAuth server metadata (RFC 8414)
- `MCPOAuthRegistrationView` - Dynamic client registration (RFC 7591)
- `MCPOAuthAuthorizeView` - Authorization endpoint
- `MCPOAuthTokenView` - Token endpoint

**Implementation Details**: See [OAuth 2.0 Authentication](#oauth-20-authentication) section

### URL Routing

**File**: `/apps/ai/urls.py`

```python
urlpatterns = [
    # OAuth endpoints
    path("oauth/authorize", MCPOAuthAuthorizeView.as_view(), name="mcp-oauth-authorize"),
    path("oauth/token", MCPOAuthTokenView.as_view(), name="mcp-oauth-token"),
    path("oauth/register", MCPOAuthRegistrationView.as_view(), name="mcp-oauth-register"),

    # MCP server endpoint
    path("cto-tools/serve", CTOToolsMCPServerView.as_view(), name="mcp-cto-tools-serve"),
]
```

**File**: `/settings/urls.py`

```python
urlpatterns = [
    # OAuth metadata discovery
    path('.well-known/oauth-authorization-server', MCPOAuthMetadataView.as_view()),

    # MCP routes
    path('mcp/', include('apps.ai.urls')),
]
```

---

## Testing Requirements

### Test Coverage Requirements

- **Minimum Coverage**: 90% for all OAuth and MCP views
- **Test Count**: 37 tests minimum (21 OAuth + 16 MCP)
- **Test Runtime**: Must complete in < 1 second
- **Test Database**: PostgreSQL (no SQLite)

### Test File 1: OAuth Flow Tests

**File**: `/apps/ai/tests/test_mcp_oauth_flow.py`

**Test Count**: 21 tests

**Categories**:

1. **Metadata Discovery** (5 tests)
   - Endpoint exists and returns 200
   - Contains all required RFC 8414 fields
   - Advertises PKCE S256 support
   - Advertises registration endpoint
   - Endpoint URLs are correctly formed

2. **Client Registration** (3 tests)
   - Registration succeeds and returns client_id
   - Response includes metadata fields
   - Rejects invalid JSON

3. **Authorization** (4 tests)
   - Returns authorization code via redirect
   - Accepts PKCE parameters
   - Requires redirect_uri
   - Preserves state parameter

4. **Token Exchange** (5 tests)
   - Issues access token successfully
   - Accepts PKCE code_verifier
   - Validates grant_type
   - Requires authorization code
   - Rejects invalid JSON

5. **End-to-End Flows** (4 tests)
   - Complete OAuth flow without PKCE
   - Complete OAuth flow with PKCE
   - **Complete real-world MCP session** (10 phases)
   - Claude Code requirements validation

### Test File 2: MCP Server Tests

**File**: `/apps/ai/tests/test_mcp_server_views.py`

**Test Count**: 16 tests

**Categories**:

1. **CTO Tools Server - Discovery** (2 tests)
   - GET returns server info
   - Indicates authentication required

2. **CTO Tools Server - Authentication** (4 tests)
   - POST requires Bearer token
   - Rejects invalid auth headers
   - Initialize succeeds with auth
   - Tools/list succeeds with auth

3. **CTO Tools Server - Protocol** (3 tests)
   - Handles notifications correctly
   - Returns error for invalid methods
   - Handles malformed JSON gracefully

4. **Creative Juices Server** (4 tests)
   - GET returns server info
   - No authentication required
   - Initialize works without auth
   - Tools/list returns creative tools

5. **Integration Tests** (3 tests)
   - Complete session with OAuth
   - Authentication mismatch detection
   - Server info matches manifest

### Critical Test: Real-World Session Simulation

**Test**: `test_complete_real_world_mcp_session`

**Purpose**: Comprehensive end-to-end test simulating the EXACT flow that Claude Code performs

**10 Phases Validated**:

1. **OAuth Discovery** - Fetch metadata from `.well-known` endpoint
2. **Dynamic Client Registration** - Register and receive client_id
3. **PKCE Authorization** - Get auth code with S256 challenge
4. **Token Exchange** - Trade code for Bearer token
5. **MCP Server Discovery** - GET server info and check auth requirements
6. **Initialize** - POST initialize with Bearer token
7. **Notifications** - POST notifications/initialized (CRITICAL)
8. **Operations** - POST tools/list and validate tool schemas
9. **Error Handling** - Test invalid methods return proper error codes
10. **Auth Enforcement** - Verify unauthenticated requests are rejected

**This Test Would Have Caught**:
- ❌ Missing OAuth registration endpoint
- ❌ Missing PKCE S256 support
- ❌ Authentication mismatch (GET vs POST)
- ❌ Missing notifications handler (the production bug)
- ❌ Invalid JSON-RPC responses
- ❌ Missing required response fields

### Test Execution

```bash
# Run all MCP tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_oauth_flow.py apps/ai/tests/test_mcp_server_views.py -v

# Run specific test
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/test_mcp_oauth_flow.py::test_complete_real_world_mcp_session -v

# Run with coverage
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/ --cov=apps.ai.views --cov-report=html
```

### Test Fixtures

```python
@pytest.fixture
def client():
    """Return Django test client."""
    return Client()

@pytest.fixture
def valid_bearer_token():
    """Return a valid Bearer token for testing."""
    return "test_access_token_12345"

@pytest.fixture
def pkce_params():
    """Generate PKCE code verifier and challenge for testing."""
    code_verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode('utf-8').rstrip('=')

    challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode('utf-8').rstrip('=')

    return {
        'code_verifier': code_verifier,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }
```

---

## Deployment Specification

### Platform

- **Hosting**: Render.com
- **Region**: Oregon (us-west-2)
- **Plan**: Starter
- **Auto-Deploy**: Enabled on main branch push
- **Health Check**: None (rely on Render's default)

### Environment Variables

```bash
# Django Settings
DJANGO_SETTINGS_MODULE=settings
SECRET_KEY=<generated-secret-key>
DEBUG=False
DEPLOYMENT_TYPE=PRODUCTION

# Database
DATABASE_URL=postgres://user:pass@host:5432/dbname

# OAuth (not required for auto-approve mode)
# OAUTH_CLIENT_ID=<not-used-in-auto-approve>
# OAUTH_CLIENT_SECRET=<not-used-in-auto-approve>
```

### Build Configuration

**File**: `build.sh`

```bash
#!/usr/bin/env bash
set -o errexit

# Install dependencies
uv sync --all-extras

# Collect static files
uv run python manage.py collectstatic --no-input

# Run migrations (requires approval)
# uv run python manage.py migrate --no-input
```

**Note**: Migrations require manual approval and are run separately

### Start Command

```bash
gunicorn settings.wsgi:application
```

### Domain Configuration

- **Production URL**: `https://ai.yuda.me`
- **MCP Endpoint**: `https://ai.yuda.me/mcp/cto-tools/serve`
- **OAuth Metadata**: `https://ai.yuda.me/.well-known/oauth-authorization-server`
- **Manifest**: `https://ai.yuda.me/mcp/cto-tools/manifest.json`

### SSL/TLS

- **Certificate**: Managed by Render (auto-renewal)
- **Minimum TLS Version**: TLS 1.2
- **HTTPS Redirect**: Enabled

### Deployment Process

1. **Push to main branch**: `git push origin main`
2. **Render detects commit**: Auto-trigger build
3. **Build phase** (~2-3 minutes):
   - Install dependencies with uv
   - Collect static files
   - Run build.sh script
4. **Deploy phase** (~1 minute):
   - Start gunicorn server
   - Health check passes
5. **Go live**: Traffic routes to new deployment
6. **Old deployment**: Deactivated after successful health checks

### Monitoring

**Render Dashboard**: https://dashboard.render.com/web/srv-d3ho96p5pdvs73feafhg

**Logs Access**:
```bash
# Via Render CLI (if installed)
render logs -s srv-d3ho96p5pdvs73feafhg

# Via web dashboard
# Navigate to service → Logs tab
```

**Key Metrics to Monitor**:
- Request latency (p50, p95, p99)
- Error rate (4xx, 5xx)
- Memory usage
- OAuth token generation rate
- MCP initialize failures

### Rollback Procedure

1. **Navigate to Render Dashboard** → Services → cuttlefish
2. **Go to Deploys tab**
3. **Find previous working deploy**
4. **Click "Redeploy"**
5. **Verify connection** with Claude Code `/mcp` command

### Disaster Recovery

**Scenario**: Complete deployment failure

**Steps**:
1. Check Render status page: https://status.render.com
2. Review build logs for errors
3. Test locally: `uv run python manage.py runserver`
4. If local works but Render fails:
   - Check environment variables in Render dashboard
   - Verify DATABASE_URL is correct
   - Check for dependency conflicts
5. If critical: Rollback to last known good deploy

---

## Security Considerations

### Authentication & Authorization

- **OAuth 2.0 + PKCE**: Industry-standard authentication
- **S256 Challenge Method**: SHA256 hash prevents MITM attacks
- **Bearer Tokens**: Transmitted in HTTP headers, not URLs
- **Auto-Approve**: Acceptable for development; requires user consent in production
- **No Secrets**: Public clients (Claude Code) don't require client_secret

### Data Protection

- **No User Data Stored**: Server is stateless
- **No PII Collection**: No personal information collected or stored
- **Logging**: Only log request metadata, never tokens or credentials
- **HTTPS Only**: All traffic encrypted in transit
- **CORS**: Not applicable (server-to-server communication)

### Input Validation

- **JSON-RPC Validation**: All requests validated against JSON-RPC 2.0 spec
- **Parameter Validation**: Tool parameters validated by Pydantic schemas
- **SQL Injection**: Not applicable (no database queries from user input)
- **XSS**: Not applicable (no HTML rendering)
- **Command Injection**: Git commands in tools use parameterized calls

### Rate Limiting

**Current**: None implemented

**Recommended** (for production):
- OAuth endpoints: 10 requests/minute per IP
- MCP endpoints: 100 requests/minute per token
- DDoS protection: Render's built-in protection

### Secrets Management

**Current**: Environment variables in Render

**Future** (for security tool connectors):
- HashiCorp Vault for API keys
- AWS Secrets Manager alternative
- Encrypted at rest
- Audit log for secret access

### Security Headers

**Required Headers**:
```python
# In Django middleware or view
response['X-Content-Type-Options'] = 'nosniff'
response['X-Frame-Options'] = 'DENY'
response['X-XSS-Protection'] = '1; mode=block'
response['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
```

### Vulnerability Management

**Dependencies**:
- Scan with `pip-audit` or `safety` before each deployment
- Auto-update dependencies weekly (via Dependabot)
- Review security advisories for Django, FastMCP, FastAPI

**Monitoring**:
- GitHub Security Alerts enabled
- Render security scanning enabled
- Manual review of Dependabot PRs

### OWASP Top 10 Mitigation

| Vulnerability | Mitigation |
|---------------|-----------|
| Broken Access Control | Bearer token required for all MCP operations |
| Cryptographic Failures | HTTPS only, TLS 1.2+, no sensitive data stored |
| Injection | Parameterized queries, input validation |
| Insecure Design | Security by default, least privilege |
| Security Misconfiguration | Django security settings, no debug in production |
| Vulnerable Components | Dependency scanning, regular updates |
| Authentication Failures | OAuth 2.0 + PKCE, secure token generation |
| Software Integrity | Signed commits, verified deployments |
| Logging Failures | Structured logging, no sensitive data logged |
| SSRF | No external requests from user input |

---

## Future Enhancements

### Short-Term (1-3 months)

1. **Security Correlation Engine**
   - Implement multi-tool alert correlation
   - Connect SAST, DAST, CSPM tools
   - Policy document integration
   - Risk scoring algorithm

2. **Connector Registry**
   - Pluggable connector architecture
   - Support for popular security tools:
     - SAST: Semgrep, CodeQL, Snyk
     - DAST: OWASP ZAP, Burp Suite
     - CSPM: Prisma Cloud, Wiz, Lacework
   - Secure credential storage

3. **Enhanced Metrics**
   - Engineering velocity trends
   - Code quality metrics (test coverage, complexity)
   - Team productivity analytics

### Medium-Term (3-6 months)

1. **Linear Integration**
   - Create tickets from security reviews
   - OAuth-based user authentication
   - Multi-tenant support
   - Ticket status tracking

2. **Real User Authentication**
   - Replace auto-approve with real OAuth consent
   - User account management
   - Per-user API quotas
   - Usage analytics dashboard

3. **Additional Tools**
   - `incident_postmortem` - Guided incident analysis
   - `hiring_review` - Interview feedback aggregation
   - `roadmap_planning` - Feature prioritization framework
   - `tech_debt_audit` - Technical debt identification

### Long-Term (6-12 months)

1. **Multi-Tenant Architecture**
   - Organization-level isolation
   - Team-based access control
   - Usage billing and quotas
   - Admin dashboard

2. **Advanced Security Features**
   - ML-based risk prediction
   - Automated remediation suggestions
   - Policy-as-code integration
   - Compliance reporting (SOC 2, ISO 27001)

3. **Enterprise Features**
   - SSO integration (Okta, Azure AD)
   - Audit logging and compliance
   - On-premise deployment option
   - SLA guarantees

4. **Developer Experience**
   - CLI tool for direct MCP access
   - VSCode extension
   - Slack/Discord integration
   - API webhooks for automation

---

## Appendix A: Complete Request/Response Examples

### Example 1: Complete OAuth + MCP Flow

```bash
# 1. Discover OAuth metadata
curl https://ai.yuda.me/.well-known/oauth-authorization-server

# Response:
{
  "issuer": "https://ai.yuda.me",
  "authorization_endpoint": "https://ai.yuda.me/mcp/oauth/authorize",
  "token_endpoint": "https://ai.yuda.me/mcp/oauth/token",
  "registration_endpoint": "https://ai.yuda.me/mcp/oauth/register",
  "scopes_supported": ["mcp:read", "mcp:write"],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "token_endpoint_auth_methods_supported": ["none"],
  "code_challenge_methods_supported": ["S256"]
}

# 2. Register client
curl -X POST https://ai.yuda.me/mcp/oauth/register \
  -H "Content-Type: application/json" \
  -d '{"redirect_uris": ["http://localhost:3000/callback"]}'

# Response:
{
  "client_id": "abc123xyz",
  "client_id_issued_at": 1733734800,
  "redirect_uris": ["http://localhost:3000/callback"],
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}

# 3. Get authorization code (browser redirect)
# Navigate to:
https://ai.yuda.me/mcp/oauth/authorize?client_id=abc123xyz&redirect_uri=http://localhost:3000/callback&response_type=code&code_challenge=CHALLENGE&code_challenge_method=S256&state=xyz

# Redirects to:
http://localhost:3000/callback?code=AUTH_CODE&state=xyz

# 4. Exchange code for token
curl -X POST https://ai.yuda.me/mcp/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "authorization_code",
    "code": "AUTH_CODE",
    "redirect_uri": "http://localhost:3000/callback",
    "code_verifier": "VERIFIER"
  }'

# Response:
{
  "access_token": "def456uvw",
  "token_type": "Bearer",
  "expires_in": 31536000,
  "scope": "mcp:read mcp:write"
}

# 5. Discover MCP server
curl https://ai.yuda.me/mcp/cto-tools/serve

# Response:
{
  "name": "cto-tools",
  "version": "1.1.0",
  "description": "MCP server for CTO and engineering leadership tools",
  "protocol": "MCP",
  "authentication": true,
  "endpoint": "https://ai.yuda.me/mcp/cto-tools/serve"
}

# 6. Initialize MCP session
curl -X POST https://ai.yuda.me/mcp/cto-tools/serve \
  -H "Authorization: Bearer def456uvw" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "1.0"}
    },
    "id": 1
  }'

# Response:
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {
      "name": "cto-tools",
      "version": "1.0.0"
    }
  }
}

# 7. Send initialized notification
curl -X POST https://ai.yuda.me/mcp/cto-tools/serve \
  -H "Authorization: Bearer def456uvw" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {}
  }'

# Response:
{
  "jsonrpc": "2.0"
}

# 8. List tools
curl -X POST https://ai.yuda.me/mcp/cto-tools/serve \
  -H "Authorization: Bearer def456uvw" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/list",
    "params": {},
    "id": 2
  }'

# Response:
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "weekly_review",
        "description": "Provides a structured framework for conducting weekly engineering team reviews...",
        "inputSchema": {
          "type": "object",
          "properties": {
            "days": {"type": "integer", "default": 7},
            "categories": {"type": "integer", "default": 5}
          }
        }
      }
    ]
  }
}

# 9. Call a tool
curl -X POST https://ai.yuda.me/mcp/cto-tools/serve \
  -H "Authorization: Bearer def456uvw" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "weekly_review",
      "arguments": {"days": 7, "categories": 5}
    },
    "id": 3
  }'

# Response:
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{
      "type": "text",
      "text": "# Engineering Team Review Framework\n\n..."
    }]
  }
}
```

---

## Appendix B: Troubleshooting Guide

### Issue: "Incompatible auth server: does not support dynamic client registration"

**Cause**: OAuth metadata missing `registration_endpoint`

**Fix**: Verify metadata endpoint returns:
```json
{
  "registration_endpoint": "https://ai.yuda.me/mcp/oauth/register"
}
```

### Issue: "Incompatible auth server: does not support code challenge method S256"

**Cause**: OAuth metadata missing PKCE support

**Fix**: Verify metadata endpoint returns:
```json
{
  "code_challenge_methods_supported": ["S256"]
}
```

### Issue: "Authentication successful, but server reconnection failed"

**Cause 1**: Server advertises `authentication: false` but requires Bearer token

**Fix**: In `CTOToolsMCPServerView.get()`, ensure:
```python
return JsonResponse({
    "authentication": True  # Must be True if POST requires auth
})
```

**Cause 2**: Server rejects `notifications/initialized` message

**Fix**: In `CTOToolsMCPServerView.post()`, add handler:
```python
elif method.startswith("notifications/"):
    return JsonResponse({"jsonrpc": "2.0"}, status=200)
```

### Issue: "401 Unauthorized" on MCP requests

**Cause**: Missing or invalid Bearer token

**Fix**: Ensure `Authorization: Bearer {token}` header is included in all POST requests

### Issue: "400 Bad Request - Method not found"

**Cause**: Unsupported MCP method or typo in method name

**Fix**: Verify method is one of: `initialize`, `tools/list`, `tools/call`, `notifications/*`

### Issue: Tests failing with "No module named 'apps.ai.mcp.security'"

**Cause**: Security review components not yet implemented

**Fix**: Comment out security review tests or implement stub modules

---

## Appendix C: Migration from Local to Production

### Step 1: Update Environment Variables

```bash
# Local (.env.local)
DEPLOYMENT_TYPE=LOCAL
DEBUG=True
DATABASE_URL=postgres://user@localhost:5432/cuttlefish

# Production (Render)
DEPLOYMENT_TYPE=PRODUCTION
DEBUG=False
DATABASE_URL=<render-postgres-url>
```

### Step 2: Update OAuth URLs

**Local Manifest**:
```json
{
  "oauth": {
    "authorizationUrl": "http://localhost:8000/mcp/oauth/authorize",
    "tokenUrl": "http://localhost:8000/mcp/oauth/token"
  }
}
```

**Production Manifest**:
```json
{
  "oauth": {
    "authorizationUrl": "https://ai.yuda.me/mcp/oauth/authorize",
    "tokenUrl": "https://ai.yuda.me/mcp/oauth/token"
  }
}
```

### Step 3: Configure HTTPS

Local development uses HTTP, production requires HTTPS. No code changes needed - Render handles SSL termination.

### Step 4: Test Locally Before Deploy

```bash
# 1. Run tests
DJANGO_SETTINGS_MODULE=settings pytest apps/ai/tests/

# 2. Start server
uv run python manage.py runserver

# 3. Test OAuth flow
curl http://localhost:8000/.well-known/oauth-authorization-server

# 4. Test MCP endpoint
curl http://localhost:8000/mcp/cto-tools/serve
```

### Step 5: Deploy to Render

```bash
git add .
git commit -m "Deploy CTO Tools MCP server"
git push origin main
```

Render will automatically:
1. Detect the push
2. Build the application
3. Run tests (if configured)
4. Deploy to production
5. Route traffic to new deployment

---

## Appendix D: Reference Implementation Checklist

Use this checklist when rebuilding the server from this PRD:

### OAuth 2.0 Implementation
- [ ] Metadata endpoint at `/.well-known/oauth-authorization-server`
- [ ] Registration endpoint with RFC 7591 compliance
- [ ] Authorization endpoint with PKCE S256 support
- [ ] Token endpoint with authorization_code grant
- [ ] Auto-approve mode (no user consent screen)
- [ ] 32-byte random token generation
- [ ] 1-year token expiration

### MCP Protocol Implementation
- [ ] GET handler returns server metadata with `authentication: true`
- [ ] POST handler validates Bearer token (401 if missing)
- [ ] `initialize` method returns protocol version and capabilities
- [ ] `tools/list` method returns tool schemas
- [ ] `tools/call` method executes tools and returns results
- [ ] `notifications/initialized` handler returns 200
- [ ] Error responses follow JSON-RPC 2.0 spec
- [ ] Logging for all MCP requests

### Tools Implementation
- [ ] `weekly_review` tool with days and categories parameters
- [ ] `architecture_review` tool with focus and depth parameters
- [ ] `security_review` tool signature (implementation optional)
- [ ] Tool responses formatted as MCP content blocks
- [ ] Input validation using Pydantic schemas

### Testing
- [ ] 21 OAuth flow tests passing
- [ ] 16 MCP server tests passing
- [ ] Comprehensive real-world session test (10 phases)
- [ ] All tests use PostgreSQL (no SQLite)
- [ ] Tests run in < 1 second
- [ ] 90%+ code coverage for views

### Deployment
- [ ] `build.sh` script configured
- [ ] Environment variables set in Render
- [ ] Auto-deploy enabled on main branch
- [ ] HTTPS enforced (Render automatic)
- [ ] Health checks passing
- [ ] Logs accessible in Render dashboard

### Security
- [ ] CSRF exempt for MCP endpoints
- [ ] Bearer token validation
- [ ] HTTPS only in production
- [ ] No sensitive data logged
- [ ] Security headers configured
- [ ] Dependencies scanned for vulnerabilities

### Documentation
- [ ] This PRD complete and accurate
- [ ] README.md with setup instructions
- [ ] API documentation (this document)
- [ ] Troubleshooting guide (Appendix B)
- [ ] Architecture diagrams (this document)

---

**END OF DOCUMENT**

This PRD is comprehensive enough to recreate the entire CTO Tools MCP server from scratch. All technical specifications, implementation details, and operational procedures are documented above.
