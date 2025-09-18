# QuickBooks AI Analytics MCP Integration Specification

## Executive Summary

Enable QuickBooks AI Analytics users to seamlessly connect their financial data to ChatGPT Desktop through a one-click MCP (Model Context Protocol) installation, providing instant AI-powered financial insights directly within their preferred AI assistant.

## Feature Overview

### Product Vision
Transform ChatGPT into a personal CFO by connecting it directly to QuickBooks data through our secure MCP server, delivering the same instant insights our platform provides but within the user's existing AI workflow.

### Core Value Proposition
- **10-second setup**: One-click installation vs. manual API configuration
- **Zero context switching**: Financial insights without leaving ChatGPT
- **24/7 availability**: Your financial data accessible whenever ChatGPT is open
- **Secure by design**: Read-only access with bank-level encryption

## Technical Architecture

### System Components

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  ChatGPT        │────▶│  MCP Server      │────▶│  Django         │
│  Desktop        │◀────│  (Local Proxy)   │◀────│  Backend API    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │                            │
                              ▼                            ▼
                        ┌──────────────┐          ┌──────────────┐
                        │ Auth Token   │          │ PostgreSQL   │
                        │ Storage      │          │ Database     │
                        └──────────────┘          └──────────────┘
                                                           │
                                                           ▼
                                                  ┌──────────────┐
                                                  │ QuickBooks   │
                                                  │ API          │
                                                  └──────────────┘
```

### MCP Server Specification

#### Server Identity
```json
{
  "name": "quickbooks-ai-analytics",
  "version": "1.0.0",
  "description": "Connect ChatGPT to your QuickBooks data for instant financial insights",
  "author": "QuickBooks AI Analytics",
  "homepage": "https://quickbooks.yuda.me"
}
```

#### Available Tools

##### 1. `get_financial_summary`
```typescript
{
  "name": "get_financial_summary",
  "description": "Get a comprehensive financial overview including revenue, expenses, profit margins, and cash flow",
  "parameters": {
    "period": {
      "type": "string",
      "enum": ["today", "this_week", "this_month", "this_quarter", "this_year", "last_month", "last_quarter", "last_year", "custom"],
      "description": "Time period for the summary"
    },
    "start_date": {
      "type": "string",
      "format": "date",
      "description": "Start date for custom period (YYYY-MM-DD)"
    },
    "end_date": {
      "type": "string",
      "format": "date",
      "description": "End date for custom period (YYYY-MM-DD)"
    }
  }
}
```

##### 2. `analyze_expenses`
```typescript
{
  "name": "analyze_expenses",
  "description": "Analyze expense patterns, identify top spending categories, and find optimization opportunities",
  "parameters": {
    "category": {
      "type": "string",
      "description": "Specific expense category to analyze (optional)"
    },
    "period": {
      "type": "string",
      "enum": ["this_month", "last_month", "this_quarter", "this_year"]
    },
    "compare_to_previous": {
      "type": "boolean",
      "default": true,
      "description": "Compare to previous period"
    }
  }
}
```

##### 3. `get_cash_flow_forecast`
```typescript
{
  "name": "get_cash_flow_forecast",
  "description": "Project future cash flow based on current trends and scheduled transactions",
  "parameters": {
    "days_ahead": {
      "type": "integer",
      "default": 30,
      "minimum": 7,
      "maximum": 90,
      "description": "Number of days to forecast"
    },
    "include_scenarios": {
      "type": "boolean",
      "default": false,
      "description": "Include best/worst case scenarios"
    }
  }
}
```

##### 4. `find_tax_deductions`
```typescript
{
  "name": "find_tax_deductions",
  "description": "Identify potential tax deductions and forgotten write-offs",
  "parameters": {
    "tax_year": {
      "type": "integer",
      "description": "Tax year to analyze (defaults to current year)"
    },
    "business_type": {
      "type": "string",
      "enum": ["sole_proprietor", "llc", "s_corp", "c_corp"],
      "description": "Business structure for deduction rules"
    }
  }
}
```

##### 5. `get_invoice_insights`
```typescript
{
  "name": "get_invoice_insights",
  "description": "Analyze invoice status, aging, and payment patterns",
  "parameters": {
    "status": {
      "type": "string",
      "enum": ["all", "paid", "unpaid", "overdue"],
      "default": "all"
    },
    "customer": {
      "type": "string",
      "description": "Filter by specific customer (optional)"
    }
  }
}
```

##### 6. `compare_periods`
```typescript
{
  "name": "compare_periods",
  "description": "Compare financial metrics between two time periods",
  "parameters": {
    "metric": {
      "type": "string",
      "enum": ["revenue", "expenses", "profit", "cash_flow", "all"],
      "default": "all"
    },
    "period1": {
      "type": "object",
      "properties": {
        "start": { "type": "string", "format": "date" },
        "end": { "type": "string", "format": "date" }
      }
    },
    "period2": {
      "type": "object",
      "properties": {
        "start": { "type": "string", "format": "date" },
        "end": { "type": "string", "format": "date" }
      }
    }
  }
}
```

### Django Implementation Details

#### Database Models
The MCP integration leverages existing Django models:

```python
# apps/integration/models/quickbooks.py
- Organization: Multi-tenant organization model
- QuickBooksConnection: OAuth tokens and connection state
- MCPSession: MCP-specific session management

# apps/ai/models/
- ChatSession: Track MCP chat sessions
- AIFeedback: User feedback on AI responses
```

#### API Endpoints

##### Token Generation (Django REST Framework)
```python
# apps/api/quickbooks/views.py
POST /api/mcp/generate-token/
Authorization: Bearer {user_session_token}

Response:
{
  "mcp_token": "mcp_1234567890abcdef",
  "expires_at": "2025-10-09T00:00:00Z",
  "installation_url": "quickbooks-ai://install-mcp?token=mcp_1234567890abcdef",
  "server_endpoint": "wss://quickbooks.yuda.me/mcp/connect"
}
```

##### MCP WebSocket Connection
```python
# apps/ai/mcp/quickbooks_server.py
WSS /mcp/connect
Headers: {
  "Authorization": "Bearer {mcp_token}"
}
```

#### MCP Server Implementation
```python
# apps/ai/mcp/quickbooks_server.py
class QuickBooksMCPServer:
    """
    Main MCP server handling ChatGPT Desktop connections.
    Uses asyncio for concurrent request handling.
    """
    
    async def handle_tool_call(self, tool_name: str, params: dict):
        """Route tool calls to appropriate QuickBooks client methods"""
        
    async def authenticate(self, token: str) -> MCPSession:
        """Validate MCP token and establish session"""

# apps/ai/mcp/quickbooks_tools.py
QUICKBOOKS_TOOLS = [
    # Tool definitions matching TypeScript specs above
]

# apps/integration/quickbooks/client.py
class QuickBooksClient:
    """
    Async QuickBooks API client with OAuth handling.
    All MCP tool implementations go here.
    """
```

### Installation Package Structure
```
quickbooks-ai-analytics.dxt/
├── manifest.json
├── server.py (MCP proxy server)
├── requirements.txt
├── config/
│   ├── auth.json (encrypted token storage)
│   └── server.json (endpoint configuration)
├── certs/ (SSL certificates for secure local proxy)
└── README.md
```

### Security Requirements

#### Data Protection
- **Django Security Middleware**: CSRF, XSS, SQL injection protection
- **TLS 1.3**: All connections encrypted (enforced by Django)
- **Token Storage**: Django's secure token backend with encryption
- **Read-only Access**: Enforced at QuickBooks OAuth scope level
- **Audit Logging**: Django's logging framework for all MCP queries

#### Authentication Flow
1. **Django Session Auth**: User logs in via Django auth
2. **MCP Token Generation**: Create time-limited token in PostgreSQL
3. **Token Validation**: Each MCP request validated against database
4. **Session Management**: Django sessions track active MCP connections

#### Rate Limiting
```python
# Using Django's rate limiting middleware
RATELIMIT_ENABLE = True
MCP_RATE_LIMIT = "100/m"  # 100 requests per minute
```

## User Experience

### Installation Flow

1. **Generate MCP Token** (Django Admin Dashboard)
   ```
   Dashboard → Settings → Integrations → ChatGPT
   [Generate MCP Token] → Creates token in MCPSession model
   ```

2. **Download Installer**
   ```
   [Download for ChatGPT] → Generates .dxt package dynamically
   Package includes user's encrypted token
   ```

3. **One-Click Install** (ChatGPT Desktop)
   ```
   Settings → Model Context Protocol → Install Extension
   Select quickbooks-ai-analytics.dxt → [Install]
   Auto-connects to wss://quickbooks.yuda.me/mcp/connect
   ```

4. **Verification**
   ```
   Automatic test query: "What's my current cash position?"
   Success logged in Django admin
   ```

### Django Admin Interface
```python
# apps/ai/admin.py
class MCPSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'token_preview', 'created_at', 'last_used', 'is_active']
    list_filter = ['is_active', 'created_at']
    readonly_fields = ['token', 'device_id', 'queries_count']
    
    actions = ['revoke_tokens', 'export_usage_report']
```

### Error Handling

#### Django Error Responses
```python
# apps/ai/mcp/exceptions.py
class MCPError(Exception):
    def to_dict(self):
        return {
            "error": self.code,
            "message": self.message,
            "resolution": self.resolution,
            "support_url": settings.MCP_SUPPORT_URL
        }

class MCPConnectionError(MCPError):
    code = "connection_failed"
    message = "Unable to reach QuickBooks AI Analytics"
    resolution = "Check your internet connection and try again"

class MCPAuthExpiredError(MCPError):
    code = "auth_expired"
    message = "Your MCP token has expired"
    resolution = "Generate a new token from your dashboard"
```

## Implementation Phases

### Phase 1: MVP (Week 1-2)
- [ ] Basic MCP server in `apps/ai/mcp/quickbooks_server.py`
- [ ] MCPSession model and token generation
- [ ] Manual .dxt package creation script
- [ ] Integration with existing QuickBooksClient

### Phase 2: Core Features (Week 3-4)
- [ ] All 6 tools implemented in QuickBooksClient
- [ ] Django admin interface for MCP management
- [ ] Automated .dxt packaging via Django view
- [ ] WebSocket connection handling with Django Channels

### Phase 3: Enhanced Experience (Week 5-6)
- [ ] Redis caching for improved performance
- [ ] Celery tasks for background processing
- [ ] Django signals for usage analytics
- [ ] Rate limiting with django-ratelimit

### Phase 4: Advanced Features (Week 7-8)
- [ ] Multi-organization support via Django's multi-tenancy
- [ ] Scheduled reports using Celery Beat
- [ ] Export capabilities via Django REST Framework
- [ ] Webhook notifications for important insights

## Testing Requirements

### Django Testing
```python
# apps/ai/tests/test_mcp_quickbooks.py
class TestMCPServer(TestCase):
    """Test MCP server functionality"""
    
# apps/ai/tests/test_mcp_tools.py
class TestMCPTools(TestCase):
    """Test each MCP tool implementation"""
    
# apps/integration/quickbooks/tests/test_mcp_client.py
class TestMCPQuickBooksClient(TestCase):
    """Test QuickBooks client MCP methods"""
```

### Testing Checklist
- [ ] All 6 tools return accurate data (pytest)
- [ ] Authentication flow works end-to-end
- [ ] WebSocket connections handled properly
- [ ] Rate limiting prevents abuse
- [ ] Multi-tenant data isolation verified

## Database Schema

### MCP-Specific Tables
```sql
-- MCPSession (apps.ai)
CREATE TABLE ai_mcpsession (
    id UUID PRIMARY KEY,
    organization_id UUID REFERENCES integration_organization(id),
    user_id INTEGER REFERENCES auth_user(id),
    token VARCHAR(255) UNIQUE NOT NULL,
    device_id VARCHAR(255),
    expires_at TIMESTAMP,
    last_used TIMESTAMP,
    queries_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- MCPQueryLog (apps.ai)
CREATE TABLE ai_mcpquerylog (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES ai_mcpsession(id),
    tool_name VARCHAR(100),
    parameters JSONB,
    response_time_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMP
);
```

## Deployment Configuration

### Environment Variables
```bash
# .env.production
MCP_ENABLED=True
MCP_WEBSOCKET_URL=wss://quickbooks.yuda.me/mcp/connect
MCP_TOKEN_EXPIRY_DAYS=90
MCP_RATE_LIMIT=100/m
MCP_MAX_CONNECTIONS_PER_USER=3
MCP_SUPPORT_URL=https://quickbooks.yuda.me/support/mcp
```

### Docker Deployment
```dockerfile
# Dockerfile.mcp
FROM python:3.11
# MCP server specific configuration
EXPOSE 8765  # WebSocket port
CMD ["python", "-m", "apps.ai.mcp.quickbooks_server"]
```

### Monitoring
- Django Admin dashboard for session management
- Sentry integration for error tracking
- CloudWatch metrics for usage analytics
- PostgreSQL query performance monitoring

## Success Metrics

### Django Analytics Views
```python
# apps/ai/views/analytics.py
class MCPAnalyticsView(StaffRequiredMixin, TemplateView):
    """Dashboard showing MCP usage metrics"""
    
    def get_context_data(self):
        return {
            'active_sessions': MCPSession.objects.filter(is_active=True).count(),
            'daily_queries': self.get_daily_query_count(),
            'popular_tools': self.get_popular_tools(),
            'average_response_time': self.get_avg_response_time(),
        }
```

### Key Performance Indicators
- **Installation rate**: Track via MCPSession creation
- **Daily active usage**: Query MCPQueryLog daily
- **Query volume**: Aggregate from MCPQueryLog
- **Response times**: Monitor via Django middleware
- **Error rates**: Track via Sentry integration

## Support Infrastructure

### Django-Powered Support
- Help articles in Django CMS
- Support tickets via Django admin
- FAQ managed in PostgreSQL
- Video tutorials hosted on CDN

### Integration with Existing Systems
- Leverage existing Django auth system
- Use current QuickBooks OAuth implementation
- Extend existing Organization model
- Reuse QuickBooksClient for data access

---

*This specification adapts the MCP ChatGPT Desktop integration to our Django/PostgreSQL architecture, leveraging existing infrastructure while adding powerful AI assistant capabilities for our users.*