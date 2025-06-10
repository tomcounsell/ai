# System Operations Guide

## Overview

This guide covers running, monitoring, and maintaining the AI agent system in development and production environments. It includes server management, environment configuration, health monitoring, and deployment considerations.

## Development Workflow

### Server Management

#### Starting the Development Server

```bash
# Start complete system (FastAPI server + Telegram client + Huey consumer)
scripts/start.sh

# What this does:
# - Checks for existing server processes
# - Prevents database locks with proactive session cleanup
# - Checks Telegram authentication status
# - Starts FastAPI server, Telegram client, and Huey task consumer
# - Validates system health with self-ping end-to-end testing
# - Saves PIDs for process management
# - Provides immediate feedback on startup status
```

The startup script also launches the **Huey consumer** for background task processing. See [Promise Queue Documentation](promise-queue.md) for details on the asynchronous task system.

**Script Details** (`scripts/start.sh`):
```bash
#!/bin/bash
# Check if server is already running
if [ -f "server.pid" ]; then
    PID=$(cat server.pid)
    if ps -p $PID > /dev/null; then
        echo "Server already running on PID $PID"
        exit 1
    fi
fi

# Start server with hot reload
echo "Starting FastAPI development server..."
uvicorn main:app --reload --host 0.0.0.0 --port 9000 &
SERVER_PID=$!

# Save PID for management
echo $SERVER_PID > server.pid
echo "Server started on PID $SERVER_PID"
echo "Access at: http://localhost:9000"
```

#### Stopping the Development Server

```bash
# Stop server and cleanup orphaned processes
scripts/stop.sh

# What this does:
# - Terminates main server process
# - Cleans up orphaned uvicorn processes
# - Cleans up Telegram session files to prevent database locks
# - Removes PID files
# - Confirms successful shutdown
```

**Script Details** (`scripts/stop.sh`):
```bash
#!/bin/bash
echo "Stopping FastAPI server..."

# Stop main process if PID file exists
if [ -f "server.pid" ]; then
    PID=$(cat server.pid)
    if ps -p $PID > /dev/null; then
        kill $PID
        echo "Stopped server with PID $PID"
    fi
    rm server.pid
fi

# Clean up any orphaned uvicorn processes
pkill -f "uvicorn main:app"
echo "Cleaned up orphaned processes"
echo "Server stopped successfully"
```

#### Quick Server Health Check

```bash
# Quick server test (start, verify, stop)
python main.py & PID=$! && sleep 3 && curl -s http://localhost:9000/health && kill $PID

# Use for:
# - CI/CD health checks
# - Quick deployment verification
# - Testing configuration changes
```

### Agent Execution

#### Running UV Script Agents

```bash
# Notion Scout with project queries
uv run agents/notion_scout.py --project PsyOPTIMAL "What tasks are ready for dev?"
uv run agents/notion_scout.py --project FlexTrip "Show me project status"

# Project aliases supported
uv run agents/notion_scout.py --project psy "Quick status check"
uv run agents/notion_scout.py --project flex "What's the priority?"

# Telegram chat agent testing
uv run agents/telegram_chat_agent.py

# Valor agent standalone testing
uv run agents/valor_agent.py
```

#### Agent Demo and Testing

```bash
# Comprehensive agent functionality demo
scripts/demo_agent.sh

# Monitor demo progress
tail -f logs/agent_demo.log

# Quick agent functionality test
python tests/test_agent_quick.py

# Run full test suite
cd tests && python run_tests.py
```

## Environment Configuration

### Required Environment Variables

Create `.env` file with the following configuration:

```bash
# Core AI API Keys
ANTHROPIC_API_KEY=sk-ant-api03-...
OPENAI_API_KEY=sk-proj-...  # For Claude Code tool compatibility

# Telegram Integration
TELEGRAM_BOT_TOKEN=7123456789:AAH...
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef...

# External Services
PERPLEXITY_API_KEY=pplx-...
NOTION_API_KEY=secret_...

# Optional Configuration
ENVIRONMENT=development
LOG_LEVEL=INFO
MAX_CONVERSATION_HISTORY=50
AGENT_TIMEOUT_SECONDS=30

# Telegram Chat Filtering (Multi-Server Setup)
TELEGRAM_ALLOWED_GROUPS=PsyOPTIMAL,PsyOPTIMAL Dev  # Comma-separated workspace names
TELEGRAM_ALLOW_DMS=false  # DMs now use user whitelist instead of global setting
```

### Environment Validation

```python
# Validate environment setup
def validate_environment():
    """Check required environment variables."""
    required_vars = [
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "PERPLEXITY_API_KEY",
        "NOTION_API_KEY"
    ]

    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)

    if missing:
        raise EnvironmentError(f"Missing required environment variables: {missing}")

    print("✅ Environment configuration validated")

# Run validation
python -c "from main import validate_environment; validate_environment()"
```

### MCP Configuration

```bash
# Update MCP configuration from environment
scripts/update_mcp.sh

# What this does:
# - Reads API keys from .env
# - Generates .mcp.json configuration
# - Configures Claude Code integration
# - Sets up Notion API access
```

**Script Details** (`scripts/update_mcp.sh`):
```bash
#!/bin/bash
# Load environment variables
source .env

# Generate MCP configuration
cat > .mcp.json << EOF
{
  "mcpServers": {
    "notion": {
      "command": "npx",
      "args": ["@anthropic-ai/mcp-server-notion"],
      "env": {
        "NOTION_API_KEY": "$NOTION_API_KEY"
      }
    }
  }
}
EOF

echo "✅ MCP configuration updated"
```

### Workspace Configuration

Configure project workspaces and dev group behavior in `config/workspace_config.json`:

```json
{
  "workspaces": {
    "Yudame Dev": {
      "database_id": "****",
      "description": "Yudame development team tasks and management",
      "workspace_type": "yudame",
      "working_directory": "/Users/valorengels/src/ai",
      "telegram_chat_ids": ["-4891178445"],
      "aliases": ["yudame dev"],
      "is_dev_group": true
    },
    "PsyOPTIMAL": {
      "database_id": "****",
      "description": "PsyOPTIMAL team chat and project management",
      "workspace_type": "psyoptimal", 
      "working_directory": "/Users/valorengels/src/psyoptimal",
      "telegram_chat_ids": ["-1002600253717"],
      "aliases": ["psyoptimal", "PO"]
    }
  },
  "telegram_groups": {
    "-4891178445": "Yudame Dev",
    "-1002600253717": "PsyOPTIMAL"
  }
}
```

**Dev Group Configuration:**
- **`is_dev_group: true`**: Agent responds to ALL messages (no @mention required)
- **`is_dev_group: false` or omitted**: Agent only responds to @mentions
- **Working directory**: Each workspace specifies a single working directory for Claude Code execution
- **Notion database mapping**: Automatic project-specific database access

**Current Dev Groups:**
- Yudame Dev (-4891178445)
- PsyOPTIMAL Dev (-4897329503) 
- DeckFusion Dev (-4851227604)

### Enhanced DM User Whitelisting

Direct messages are restricted to whitelisted users with **dual whitelist support**. Configure DM access in the `dm_whitelist` section:

```json
{
  "dm_whitelist": {
    "description": "Users allowed to send direct messages to the bot",
    "default_working_directory": "/Users/valorengels/src/ai",
    "allowed_users": {
      "tomcounsell": {
        "username": "tomcounsell",
        "description": "Tom Counsell - Owner and Boss",
        "working_directory": "/Users/valorengels/src/ai"
      },
      "valorengels": {
        "username": "valorengels",
        "description": "Bot self - for self-ping tests and system validation",
        "working_directory": "/Users/valorengels/src/ai"
      }
    },
    "allowed_user_ids": {
      "179144806": {
        "description": "Tom Counsell - User ID fallback (no public username)",
        "working_directory": "/Users/valorengels/src/ai"
      },
      "66968934582": {
        "description": "Bot self (valorengels) - for self-ping tests",
        "working_directory": "/Users/valorengels/src/ai"
      }
    }
  }
}
```

**Enhanced DM Security Features:**
- **Dual whitelist support**: Both username and user ID-based access control
- **Username fallback**: User ID support for users without public usernames
- **Self-ping capability**: Bot can message itself for end-to-end system validation
- **Case-insensitive**: Username matching works regardless of case
- **Working directory isolation**: Each user can have a specific working directory
- **Default fallback**: Non-specified users get default working directory but are denied access
- **Claude Code restriction**: DM users' coding tasks are restricted to their assigned working directory

**Currently Whitelisted:**
- **@tomcounsell** (Tom Counsell - Owner/Boss) → `/Users/valorengels/src/ai`
- **@valorengels** (Bot Self - System Validation) → `/Users/valorengels/src/ai`
- **User ID 179144806** (Tom Counsell - Fallback) → `/Users/valorengels/src/ai`
- **User ID 66968934582** (Bot Self - Fallback) → `/Users/valorengels/src/ai`

## Dependency Management

### UV Package Management

```bash
# Compile requirements from base specifications
uv pip compile requirements/base.txt -o requirements.txt

# Create virtual environment
uv venv

# Install all dependencies
uv pip install -r requirements.txt

# Install development dependencies
uv pip install -e ".[dev]"

# Update specific dependency
uv pip install --upgrade pydantic-ai

# Check for security vulnerabilities
uv pip audit
```

### Package Structure

```
requirements/
├── base.txt          # Core dependencies
├── development.txt   # Dev-only dependencies
└── production.txt    # Production-specific dependencies

# Generated files
requirements.txt      # Compiled from base.txt
```

**Base Dependencies** (`requirements/base.txt`):
```
# Core Framework
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.5.0

# AI Integration
pydantic-ai>=0.0.13
anthropic>=0.34.0
openai>=1.0.0

# External Services
python-telegram-bot>=20.7
python-dotenv>=1.0.0
aiofiles>=23.0.0

# Utilities
rich>=13.0.0
click>=8.1.0
```

## Health Monitoring

### Promise Queue Monitoring

Monitor the asynchronous task execution system:

```bash
# Check pending promises
sqlite3 system.db "SELECT id, task_description, status, created_at FROM promises WHERE status='pending' ORDER BY created_at;"

# View promise statistics
sqlite3 system.db "SELECT status, COUNT(*) as count FROM promises GROUP BY status;"

# Check Huey consumer status
ps aux | grep huey_consumer

# Monitor Huey logs
tail -f logs/huey.log

# View recent completed promises
sqlite3 system.db "SELECT task_description, completed_at, result_summary FROM promises WHERE status='completed' ORDER BY completed_at DESC LIMIT 5;"
```

For detailed promise queue operations, see [Promise Queue Documentation](promise-queue.md).

### Agent Health Checks

```python
class AgentHealthMonitor:
    """Monitor agent health and performance."""

    async def check_agent_health(self, agent_name: str) -> dict:
        """Comprehensive agent health check."""
        start_time = time.time()

        try:
            # Test basic agent functionality
            test_response = await self.test_agent_response(agent_name)
            response_time = (time.time() - start_time) * 1000

            return {
                "agent_name": agent_name,
                "status": "healthy",
                "response_time_ms": response_time,
                "last_check": datetime.now().isoformat(),
                "test_response": test_response[:100] + "..." if len(test_response) > 100 else test_response
            }
        except Exception as e:
            return {
                "agent_name": agent_name,
                "status": "unhealthy",
                "error": str(e),
                "last_check": datetime.now().isoformat()
            }

# Usage
monitor = AgentHealthMonitor()
health_status = await monitor.check_agent_health("telegram_chat_agent")
```

### System Health Dashboard

```python
class SystemHealthDashboard:
    """System-wide health monitoring."""

    async def generate_health_report(self) -> dict:
        """Generate comprehensive system health report."""

        return {
            "timestamp": datetime.now().isoformat(),
            "system_status": await self.check_system_status(),
            "agents": await self.check_all_agents(),
            "external_services": await self.check_external_services(),
            "performance_metrics": await self.collect_performance_metrics()
        }

    async def check_external_services(self) -> dict:
        """Check external service connectivity."""
        services = {}

        # Anthropic API
        try:
            client = anthropic.Anthropic()
            # Test with minimal request
            services["anthropic"] = {"status": "available", "latency_ms": 0}
        except Exception as e:
            services["anthropic"] = {"status": "unavailable", "error": str(e)}

        # Perplexity API
        try:
            # Test search functionality
            result = search_web("test", max_results=1)
            services["perplexity"] = {"status": "available", "test_result": result[:50]}
        except Exception as e:
            services["perplexity"] = {"status": "unavailable", "error": str(e)}

        return services

# Generate health report
dashboard = SystemHealthDashboard()
report = await dashboard.generate_health_report()
```

### Performance Metrics

```python
class PerformanceTracker:
    """Track system performance metrics."""

    def __init__(self):
        self.metrics = defaultdict(list)

    def record_agent_execution(self, agent_name: str, execution_time: float, success: bool):
        """Record agent execution metrics."""
        self.metrics[f"{agent_name}_execution_time"].append(execution_time)
        self.metrics[f"{agent_name}_success_rate"].append(1 if success else 0)

    def get_performance_summary(self) -> dict:
        """Get performance summary across all agents."""
        summary = {}

        for metric_name, values in self.metrics.items():
            if "_execution_time" in metric_name:
                summary[metric_name] = {
                    "avg_ms": sum(values) / len(values) * 1000,
                    "min_ms": min(values) * 1000,
                    "max_ms": max(values) * 1000,
                    "count": len(values)
                }
            elif "_success_rate" in metric_name:
                summary[metric_name] = {
                    "success_rate": sum(values) / len(values),
                    "total_requests": len(values),
                    "successful_requests": sum(values)
                }

        return summary

# Usage
tracker = PerformanceTracker()
# Record metrics during agent execution
tracker.record_agent_execution("telegram_chat_agent", 1.5, True)
# Get summary
summary = tracker.get_performance_summary()
```

## Logging and Debugging

### Structured Logging

```python
import logging
from rich.logging import RichHandler

# Configure structured logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RichHandler(rich_tracebacks=True),
        logging.FileHandler("logs/agent_system.log")
    ]
)

# Agent-specific loggers
agent_logger = logging.getLogger("agents")
tool_logger = logging.getLogger("tools")
telegram_logger = logging.getLogger("telegram")

# Usage in agents
class LoggedAgent:
    def __init__(self, name: str):
        self.logger = logging.getLogger(f"agents.{name}")

    async def execute(self, message: str) -> str:
        self.logger.info(f"Executing agent with message: {message[:50]}...")

        try:
            result = await self.process_message(message)
            self.logger.info(f"Agent execution successful, response length: {len(result)}")
            return result
        except Exception as e:
            self.logger.error(f"Agent execution failed: {str(e)}")
            raise
```

### Debug Mode

```python
# Enable debug mode for detailed logging
export LOG_LEVEL=DEBUG

# Debug configuration
DEBUG_CONFIG = {
    "log_agent_inputs": True,
    "log_agent_outputs": True,
    "log_tool_calls": True,
    "log_api_requests": True,
    "preserve_temp_files": True
}

def debug_agent_execution(func):
    """Decorator for debugging agent execution."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if DEBUG_CONFIG["log_agent_inputs"]:
            logger.debug(f"Agent input: {args}, {kwargs}")

        result = await func(*args, **kwargs)

        if DEBUG_CONFIG["log_agent_outputs"]:
            logger.debug(f"Agent output: {result}")

        return result
    return wrapper
```

## Production Deployment

### Docker Configuration

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
```

### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  ai-agents:
    build: .
    ports:
      - "9000:9000"
    environment:
      - ENVIRONMENT=production
      - LOG_LEVEL=INFO
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped

volumes:
  redis_data:
```

### Production Environment

```bash
# Production environment variables
ENVIRONMENT=production
LOG_LEVEL=INFO
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql://user:pass@localhost/db

# Security considerations
API_RATE_LIMIT=100
MAX_REQUEST_SIZE=10MB
CORS_ORIGINS=https://yourdomain.com
SSL_KEYFILE=/path/to/keyfile
SSL_CERTFILE=/path/to/certfile
```

### Monitoring and Alerting

```python
# Production monitoring
class ProductionMonitor:
    """Production environment monitoring."""

    async def setup_alerts(self):
        """Configure production alerting."""

        # Error rate monitoring
        if error_rate > 0.05:  # 5% error rate threshold
            await self.send_alert("High error rate detected")

        # Response time monitoring
        if avg_response_time > 5.0:  # 5 second threshold
            await self.send_alert("High response time detected")

        # Service availability
        if service_uptime < 0.99:  # 99% uptime threshold
            await self.send_alert("Service availability below threshold")

    async def send_alert(self, message: str):
        """Send alert notification."""
        # Implementation depends on alerting system
        # (Slack, email, PagerDuty, etc.)
        pass
```

This operations guide ensures reliable deployment and monitoring of the AI agent system across development and production environments.
