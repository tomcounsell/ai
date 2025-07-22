# Environment Setup and Dependencies

## Overview

This document provides comprehensive setup instructions for the unified conversational development environment, covering all dependencies, environment configuration, installation procedures, and troubleshooting guidance.

## System Requirements

### Minimum Requirements
- **Python**: 3.11 or higher (required for type hints and modern async features)
- **Operating System**: macOS, Linux, or Windows with WSL2
- **Memory**: 4GB RAM minimum, 8GB recommended
- **Storage**: 2GB free space for dependencies and databases
- **Internet**: Required for API services and package installation

### Recommended Development Environment
- **Python**: 3.12.x (latest stable)
- **Package Manager**: UV (fast Python package manager) or pip
- **IDE**: VS Code with Python extensions or PyCharm
- **Terminal**: iTerm2 (macOS) or Windows Terminal

## Dependency Management

### Core Dependencies (pyproject.toml)

```toml
[project]
name = "ai-agent-system"
version = "0.1.0"
description = "PydanticAI agent system with intelligent tool orchestration"
requires-python = ">=3.11"

dependencies = [
    "pydantic-ai",          # Core AI agent framework
    "anthropic",            # Claude AI integration
    "openai",               # OpenAI API (GPT-4, DALL-E, Whisper)
    "python-dotenv",        # Environment variable management
    "fastapi",              # Web API framework
    "uvicorn[standard]",    # ASGI server
    "pyrogram",             # Telegram client library
    "requests",             # HTTP requests
    "popoto",               # Database toolkit
    "mcp[cli]",             # Model Context Protocol
    "pytest>=8.3.5",        # Testing framework
]
```

### Complete Dependency List (requirements/base.txt)

```txt
# Core AI and Agent Libraries
pydantic-ai             # Agent framework with tool orchestration
anthropic               # Claude AI API client
openai                  # OpenAI services (GPT-4, DALL-E, Whisper)

# Web Framework
fastapi                 # Modern async web framework
uvicorn[standard]       # ASGI server with WebSocket support

# Database and Storage
popoto                  # SQLite database toolkit
huey                    # Task queue for background processing

# External Integrations
pyrogram                # Telegram MTProto client
requests                # HTTP library
aiohttp                 # Async HTTP client

# Security and Authentication
python-jose[cryptography]  # JWT token handling
passlib[bcrypt]           # Password hashing

# Utilities
python-dotenv           # Environment variable loading
psutil                  # System monitoring
pydantic                # Data validation

# Development Tools
ruff                    # Fast Python linter
mypy                    # Static type checker
pre-commit              # Git hook framework
```

### Development vs Production Dependencies

#### Production Dependencies
All dependencies in `requirements/base.txt` are required for production deployment:
- API clients (anthropic, openai)
- Web framework (fastapi, uvicorn)
- Database (popoto, SQLite via stdlib)
- Task queue (huey)
- Integrations (pyrogram, requests)

#### Development Dependencies
Additional tools for development workflow:
- **ruff**: Code formatting and linting
- **mypy**: Type checking
- **pre-commit**: Automated code quality checks
- **pytest**: Testing framework
- **aider-chat**: AI pair programming (optional)

#### Optional Dependencies
Services that enhance functionality but aren't required:
- **perplexity**: Web search (requires PERPLEXITY_API_KEY)
- **notion-client**: Project management (requires NOTION_API_KEY)
- **browse-ai**: Web scraping (requires BROWSE_AI_API_KEY)
- **tavily**: Search API (requires TAVILY_API_KEY)

## Environment Configuration

### Required Environment Variables

Create a `.env` file from the template:

```bash
cp .env.example .env
```

#### Core API Keys (Required)

```bash
# Anthropic - Powers main conversational AI and Claude Code
ANTHROPIC_API_KEY=sk-ant-****

# OpenAI - Image generation, vision analysis, voice transcription
OPENAI_API_KEY=sk-proj-****

# Telegram - Bot authentication and messaging
TELEGRAM_API_ID=12345****
TELEGRAM_API_HASH=abcd1234****
TELEGRAM_BOT_TOKEN=1234567890:****
```

#### Service Integration Keys (Optional but Recommended)

```bash
# Perplexity - Web search for current information
PERPLEXITY_API_KEY=pplx-****

# Notion - Project management integration
NOTION_API_KEY=ntn_****
NOTION_INTEGRATION_NAME=My_Integration_****
NOTION_INTEGRATION_SECRET=secret_****
```

#### Multi-Server Deployment Configuration

```bash
# Workspace filtering for multi-tenant deployments
TELEGRAM_ALLOWED_GROUPS=PsyOPTIMAL,PsyOPTIMAL Dev,Tom's Team
TELEGRAM_ALLOW_DMS=true

# Example configurations:
# Server 1 (PsyOPTIMAL only): 
#   TELEGRAM_ALLOWED_GROUPS=PsyOPTIMAL,PsyOPTIMAL Dev
#   TELEGRAM_ALLOW_DMS=false
#
# Server 2 (DMs only):
#   TELEGRAM_ALLOWED_GROUPS=
#   TELEGRAM_ALLOW_DMS=true
```

### Configuration Validation

The system validates environment configuration at startup:

```python
# Automatic validation in main.py
def validate_environment():
    """Validate required environment variables"""
    required_vars = [
        "ANTHROPIC_API_KEY",
        "TELEGRAM_API_ID", 
        "TELEGRAM_API_HASH"
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise EnvironmentError(f"Missing required: {', '.join(missing)}")
```

### Development vs Production Environments

#### Development Environment
```bash
# .env.development
DEBUG=true
LOG_LEVEL=DEBUG
DATABASE_PATH=data/dev_system.db
HUEY_IMMEDIATE=true  # Execute tasks synchronously
```

#### Production Environment
```bash
# .env.production
DEBUG=false
LOG_LEVEL=INFO
DATABASE_PATH=data/system.db
HUEY_IMMEDIATE=false  # Use background queue
WORKERS=4            # Number of worker processes
```

## Installation Process

### 1. Prerequisites Installation

#### macOS
```bash
# Install Homebrew if not present
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.11+
brew install python@3.12

# Install UV (recommended) for fast package management
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install SQLite (usually pre-installed)
brew install sqlite3
```

#### Ubuntu/Debian
```bash
# Update package list
sudo apt update

# Install Python 3.11+ and dependencies
sudo apt install python3.12 python3.12-venv python3-pip

# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install SQLite
sudo apt install sqlite3
```

### 2. Project Setup

```bash
# Clone repository
git clone https://github.com/yourusername/ai-agent-system.git
cd ai-agent-system

# Create virtual environment with UV
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install all dependencies
uv pip install -r requirements.txt

# Or with standard pip
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Database Initialization

```bash
# Create data directory
mkdir -p data logs

# Initialize database tables
python -c "from utilities.database import init_database; init_database()"

# Verify database creation
sqlite3 data/system.db ".tables"
```

### 4. Telegram Authentication

```bash
# Run interactive authentication
./scripts/telegram_login.sh

# This will:
# 1. Prompt for your phone number
# 2. Send verification code via Telegram
# 3. Create session file for persistent authentication
```

### 5. Service Dependencies Setup

#### MCP Configuration
The system auto-generates `.mcp.json` from environment variables:

```json
{
  "mcpServers": {
    "social-tools": {
      "command": "python",
      "args": ["mcp_servers/social_tools.py"],
      "env": {
        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
        "PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"
      }
    }
  }
}
```

#### Background Task Queue
Huey is configured automatically with SQLite backend:

```python
# tasks/huey_config.py
huey = SqliteHuey(
    filename=os.getenv('HUEY_DB_PATH', 'data/huey.db'),
    immediate=os.getenv('HUEY_IMMEDIATE', 'false').lower() == 'true'
)
```

## Development Tools Configuration

### 1. Code Formatting (Ruff)

Configuration in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "N"]
ignore = ["E501", "B008", "B904"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### 2. Type Checking (MyPy)

```toml
[tool.mypy]
python_version = "3.11"
warn_return_any = false
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["pyrogram.*", "anthropic.*"]
ignore_missing_imports = true
```

### 3. Pre-commit Hooks

Create `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.11
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
```

Install hooks:
```bash
pre-commit install
```

### 4. Testing Framework Setup

```bash
# Run all tests
pytest

# Run specific test categories
pytest tests/test_performance_comprehensive.py
pytest tests/test_production_readiness.py

# Run with coverage
pytest --cov=agents --cov=tools --cov=integrations
```

## Troubleshooting

### Common Setup Issues

#### 1. Missing API Keys
**Problem**: `EnvironmentError: Missing required: ANTHROPIC_API_KEY`

**Solution**:
```bash
# Ensure .env file exists and contains keys
cp .env.example .env
# Edit .env and add your API keys
```

#### 2. Python Version Mismatch
**Problem**: `ERROR: Package requires Python >=3.11`

**Solution**:
```bash
# Check Python version
python --version

# Install Python 3.11+ if needed
# macOS: brew install python@3.12
# Ubuntu: sudo apt install python3.12
```

#### 3. Database Lock Errors
**Problem**: `sqlite3.OperationalError: database is locked`

**Solution**:
```bash
# Use the recovery script
./scripts/start.sh  # Includes automatic lock recovery

# Or manually:
lsof data/*.db | awk 'NR>1 {print $2}' | xargs kill -9
```

#### 4. Telegram Authentication Failures
**Problem**: `Authorization failed!`

**Solution**:
1. Verify API credentials in `.env`
2. Ensure phone number includes country code (+1234567890)
3. Check internet connectivity
4. Try clearing session: `rm ai_project_bot.session*`

#### 5. Import Errors
**Problem**: `ModuleNotFoundError: No module named 'pydantic_ai'`

**Solution**:
```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Reinstall dependencies
uv pip install -r requirements.txt --force-reinstall
```

### Environment Validation Script

Create `scripts/validate_env.py`:

```python
#!/usr/bin/env python3
"""Validate environment setup"""

import os
import sys
from pathlib import Path

def check_environment():
    """Comprehensive environment validation"""
    
    issues = []
    
    # Check Python version
    if sys.version_info < (3, 11):
        issues.append(f"Python 3.11+ required, found {sys.version}")
    
    # Check required environment variables
    required_vars = [
        "ANTHROPIC_API_KEY",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH"
    ]
    
    for var in required_vars:
        if not os.getenv(var):
            issues.append(f"Missing environment variable: {var}")
    
    # Check database accessibility
    db_path = Path("data/system.db")
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:
            issues.append(f"Database not accessible: {e}")
    
    # Check Telegram session
    session_file = Path("ai_project_bot.session")
    if not session_file.exists():
        issues.append("Telegram not authenticated (run scripts/telegram_login.sh)")
    
    # Report results
    if issues:
        print("❌ Environment validation failed:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("✅ Environment validation passed!")
        return True

if __name__ == "__main__":
    sys.exit(0 if check_environment() else 1)
```

### Performance Optimization Tips

#### 1. Database Optimization
```bash
# Enable WAL mode for better concurrency
sqlite3 data/system.db "PRAGMA journal_mode=WAL;"

# Optimize database periodically
sqlite3 data/system.db "VACUUM; ANALYZE;"
```

#### 2. Python Optimization
```bash
# Use production Python flags
export PYTHONOPTIMIZE=2  # Remove docstrings and asserts
export PYTHONDONTWRITEBYTECODE=1  # Don't create .pyc files
```

#### 3. Memory Management
```python
# In production settings
os.environ['MALLOC_TRIM_THRESHOLD_'] = '2'  # Aggressive memory release
os.environ['PYTHONMALLOC'] = 'malloc'  # Use system malloc
```

## Dependency Conflict Resolution

### Common Conflicts and Solutions

#### 1. OpenAI vs Anthropic Version Conflicts
```bash
# If httpx version conflicts occur
uv pip install --upgrade httpx httpcore
```

#### 2. Pyrogram Compatibility
```bash
# Pyrogram requires specific pyaes version
uv pip install pyaes==1.6.1 --force-reinstall
```

#### 3. Type Stub Issues
```bash
# Install type stubs for better IDE support
uv pip install types-requests types-aiofiles
```

### Version Pinning Strategy

For production deployments, use exact versions:

```bash
# Generate exact versions
uv pip freeze > requirements-lock.txt

# Install from locked versions
uv pip install -r requirements-lock.txt
```

## Security Considerations

### API Key Management
1. Never commit `.env` files to version control
2. Use environment-specific key rotation
3. Implement key usage monitoring
4. Set up rate limiting alerts

### Database Security
1. Use WAL mode for concurrent access
2. Regular backups: `sqlite3 data/system.db ".backup data/backup.db"`
3. Implement access logging
4. Monitor for unusual query patterns

### Network Security
1. Use HTTPS for all API communications
2. Implement request timeouts
3. Validate all external inputs
4. Monitor for suspicious activity

## Conclusion

This environment setup provides a robust foundation for the unified conversational development environment. Following these guidelines ensures consistent development environments, smooth deployments, and reliable system operation. Regular validation and monitoring help maintain system health and catch issues early.