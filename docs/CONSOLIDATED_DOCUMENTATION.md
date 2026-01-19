# Valor AI System - Complete Documentation

**A Claude Code-Powered AI Assistant for Chat Applications**

*Version: 2.2 | Last Updated: December 17, 2025*

---

# Table of Contents

1. [Executive Summary](#executive-summary)
2. [Product Vision](#product-vision)
3. [System Architecture](#system-architecture)
4. [Telegram Integration](#telegram-integration)
5. [Tools, Workflows, and Skills](#tools-workflows-and-skills)
6. [Tool Implementation Patterns](#tool-implementation-patterns)
7. [Testing Strategy](#testing-strategy)
8. [Operations & Monitoring](#operations--monitoring)
9. [Daydream System](#daydream-system)
10. [Security & Compliance](#security--compliance)
11. [Development Guidelines](#development-guidelines)
12. [Codebase Context & RAG](#codebase-context--rag)
13. [Quick Reference](#quick-reference)

---

# Executive Summary

## What Is This System?

The Valor AI System is a **unified conversational development environment** that eliminates boundaries between natural conversation and code execution. Built on Claude Code, it creates a living codebase where users interact directly WITH the system through chat applications like Telegram.

## Key Capabilities

- **Conversational AI**: Natural language understanding with the Valor Engels persona
- **Tool Orchestration**: Integration of development, search, and knowledge tools
- **Telegram Integration**: Real user account (not a bot) for natural presence
- **Hardware Access**: Runs on a MacBook with full system capabilities

## High-Level Goals

1. **Pure Agency**: The system handles complexity internally without exposing intermediate steps to the user
2. **Intelligent Context**: System understands project context without repeated explanation
3. **Productivity**: Enable users to accomplish more with less effort

---

# Product Vision

## The Concept

Valor is an AI coworker - not an assistant, not a tool, but a colleague with its own personality, its own Mac machine, and its own work to do.

The supervisor assigns work and provides direction. Valor executes autonomously on its machine, using whatever tools and integrations it needs. Only when necessary does Valor reach out via Telegram to ask questions, report progress, or request decisions.

This isn't about solving pain points in the traditional dev process. It's about replacing that process entirely with a new model: delegate to a capable coworker who handles the details.

## How It Works

- **Valor has a machine**: A Mac with full system access, development tools, and API integrations
- **Valor has context**: Persistent memory of projects, preferences, and past work
- **Valor has agency**: Makes decisions, executes tasks, and only escalates when appropriate
- **Valor communicates via Telegram**: Natural conversation with the supervisor, not command-line interaction

---

# System Architecture

## Design Philosophy

This is a **living codebase**. When the supervisor says "you" or "your code," they mean the `/ai` directory (`tomcounsell/ai` repository) - the code that runs Valor. Valor is talking about himself, his own implementation, his own capabilities.

Valor can work on other projects and repositories, but those are separate from "self." Questions about "your features" or "how do you work" refer to this codebase specifically.

## Core Architecture

```
+-------------------------------------------------------------------+
|                      User Interface Layer                          |
|-------------------------------------------------------------------|
|                       Telegram Client                              |
|                  (Real User Account via Telethon)                  |
+-------------------------+-----------------------+------------------+
                          |                       |
                          v                       v
+-------------------------+     +-----------------------------------+
|      HTTP Server        |     |        Background Workers         |
+-------------------------+     +-----------------------------------+
                          |
                          v
+-------------------------------------------------------------------+
|                      Core Agent Layer                              |
|                      (Valor Agent)                                 |
+-------------------------+-----------------------+------------------+
                          |                       |
                          v                       v
+-------------------------+     +-----------------------------------+
|      Tool Layer         |     |          MCP Servers              |
+-------------------------+     +-----------------------------------+
                          |                       |
                          +----------+------------+
                                     v
+-------------------------------------------------------------------+
|                    Data Persistence Layer                          |
|                        (SQLite)                                    |
+-------------------------------------------------------------------+
```

## Design Principles

### 1. No Legacy Code Tolerance
**Principle**: Never leave behind traces of legacy code or systems.
- Complete elimination of deprecated patterns
- No commented-out code blocks
- No temporary bridges or half-migrations
- Clean removal of unused imports and infrastructure

### 2. Critical Thinking Mandatory
**Principle**: Foolish optimism is not allowed - always think deeply.
- Question all assumptions before implementation
- Analyze trade-offs comprehensively
- Consider edge cases and failure modes
- Prioritize robust solutions over quick fixes

### 3. Match the Approach to the Context Boundary
**Principle**: An LLM's competency is bounded by its context. Choose the right tool for the scope.

- **Keyword/exact match**: Best for precise lookups, known identifiers, structured queries
- **RAG/embeddings**: Required when relevant information exceeds context limits
- **LLM reasoning**: Effective when all necessary context fits in the window

The danger is LLM confidence without competency - the model sounds certain but the answer isn't in its context. Always ask: does the model have access to the information needed to answer correctly?

When scope is small and context is complete, let the LLM reason naturally. When scope is large, use retrieval first. Don't force LLM reasoning on problems that need search, and don't add retrieval overhead when simple logic suffices.

### 4. Mandatory Commit and Push Workflow
**Principle**: Always commit and push changes at task completion.
- Never leave work uncommitted
- Clear, descriptive commit messages
- Push to remote for availability

### 5. Context Collection and Management
**Principle**: Context is the lifeblood of agentic systems. Without proper context, even the most capable model makes poor decisions.

**Why this matters:**
- An agent making 10+ tool calls in sequence loses track of why it started
- Sub-agents spawned without context repeat work or contradict the parent
- Long conversations drift from the original intent without anchoring
- Tool results must be synthesized, not just appended to history

**Context categories to maintain:**
- **Task context**: What are we trying to accomplish? What's the success criteria?
- **Conversation context**: What has been discussed? What decisions were made?
- **Workspace context**: What project? What files are relevant? What's the current state?
- **Tool context**: What tools have been called? What were the results? What failed?

**Implementation requirements:**
- Explicitly pass context when spawning sub-agents (don't assume inheritance)
- Summarize and compress context before it exceeds limits (don't truncate blindly)
- Track the "why" alongside the "what" for every significant action
- Maintain a task-level summary that persists across tool calls

### 6. Tool and MCP Selection
**Principle**: Loading all available tools pollutes context and degrades performance. Tools must be selectively exposed based on task relevance.

**The problem:**
- MCP servers like GitHub, Notion, or Stripe expose 20-50+ tools each
- Loading all tools consumes 40-60k tokens before any work begins
- The model wastes reasoning capacity parsing irrelevant tool schemas
- More tools = more confusion about which tool to use

**The solution - dynamic tool filtering:**
- Analyze the incoming task before loading tools
- Load only the MCP servers relevant to the task domain
- Within each server, filter to only the tools likely needed
- Tools like [mcproxy](https://github.com/team-attention/mcproxy) demonstrate this pattern

**Implementation approach:**
- Maintain a tool registry with categorization and descriptions
- Use a lightweight classifier to match tasks to tool categories
- Start with minimal tools, expand only if the agent requests more
- Cache tool schemas to avoid repeated loading overhead

**Example flow:**
```
User: "Check if there are any critical errors in Sentry"
→ Classifier identifies: monitoring/error-tracking domain
→ Load: Sentry MCP (filtered to read-only tools)
→ Skip: GitHub, Stripe, Notion, Linear MCPs entirely
→ Result: ~5 tools loaded instead of 100+
```

## Technology Stack

**Required:**
| Component | Technology | Why |
|-----------|------------|-----|
| **AI Engine** | Anthropic Claude | Primary reasoning engine |
| **Local LLMs** | Ollama | Lightweight tasks: sentiment, labeling, transcription, classification |
| **Messaging** | Telegram (Telethon) | Real user account, not a bot |
| **Database** | SQLite | Simple, zero-config persistence |
| **Tool Protocol** | MCP | Model Context Protocol for tools |

**When to use local vs Claude:**
- **Local (Ollama)**: Sentiment analysis, text classification, labeling, transcription, simple extraction, test judging - tasks that are repetitive, low-stakes, or need fast iteration
- **Claude**: Complex reasoning, code generation, multi-step planning, nuanced decisions - tasks requiring deep understanding

**Current choices** (not prescriptive):
- PydanticAI for agent framework
- FastAPI for HTTP server
- UV for package management

## Performance Targets

| Metric | Target |
|--------|--------|
| Response Latency (P95) | <2s |
| Streaming Interval | 2-3s |
| Context Compression | >95% |
| Memory Baseline | <50MB |

---

# Telegram Integration

## Important: This is NOT a Bot

This system uses a **real Telegram user account** with the Telethon library, not a bot.

**Key Differences:**
- **Real User Account**: Uses phone number authentication with 2FA support
- **Full Client Capabilities**: Can read messages, see edits, access message history
- **Natural Presence**: Appears as a regular user "Valor Engels", not a bot
- **Session Persistence**: Maintains login session across restarts

## Authentication

```bash
# One-time setup
./scripts/telegram_login.sh
# Enter verification code when prompted
# Session is saved for future use

# Normal operation (uses saved session)
./scripts/start.sh --telegram
```

## Group Behavior Configuration

### Default Behavior
By default, the client:
- **NEVER responds to all messages** in groups
- **ONLY responds when @valor is mentioned**
- **Always responds to direct messages** (configurable)
- **Responds to replies** to its own messages

### Configuration File: `config/telegram_groups.json`

```json
{
  "default_behavior": {
    "respond_to_mentions": true,
    "respond_to_all": false,
    "respond_to_replies": true,
    "typing_indicator": true,
    "read_receipts": true
  }
}
```

### Mention Detection

The client detects mentions through:
1. **Direct mentions**: `@valor`, `@valorengels`
2. **Name mentions**: `valor`, `hey valor`, `hi valor`
3. **Custom keywords**: Per-group configurable keywords
4. **Reply chains**: Replies to messages from the client

### Message Processing Flow

```
Message Received
    |
Is it a DM? --> Yes --> Check whitelist/blacklist --> Respond
    | No
Is it a Group?
    | Yes
Is @valor mentioned? --> Yes --> Respond
    | No
Is it a reply to us? --> Yes --> Respond
    | No
Contains keyword? --> Yes --> Respond (if configured)
    | No
Ignore Message
```

## Environment Variables

```bash
# Required
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
TELEGRAM_PASSWORD=your_2fa_password  # If 2FA enabled

# Optional
TELEGRAM_ALLOWED_GROUPS="Group1,Group2"
TELEGRAM_ALLOW_DMS=true
```

## Best Practices

1. **Always use mention detection** in groups (default behavior)
2. **Never set `respond_to_all: true`** unless absolutely necessary
3. **Use group-specific keywords** sparingly to avoid spam
4. **Configure ignore lists** for bot accounts in groups
5. **Test in private groups** before deploying to public ones

---

# Tools, Workflows, and Skills

## Philosophy

Valor does not manage its own sub-agent system. Instead, Valor provides **well-documented tools, workflows, and skills** that Claude Code orchestrates via the SDK.

Claude Code decides when and how to spawn sub-agents. Valor's job is to make the available capabilities clear and easy to use.

## What Valor Provides

### Tools (via MCP Servers)
Individual operations that can be composed into larger workflows:
- **Stripe**: Payment processing, subscriptions, billing
- **Sentry**: Error monitoring, performance analysis
- **GitHub**: Repository operations, PRs, issues
- **Render**: Deployment, infrastructure management
- **Notion**: Knowledge base, documentation
- **Linear**: Project management, issue tracking

### Workflows
Multi-step processes that combine tools for common tasks:
- Code review workflow: fetch PR → analyze changes → check tests → post review
- Incident response: check Sentry → identify cause → create fix → deploy
- Research workflow: search web → summarize → store in Notion

### Skills
Higher-level capabilities with clear invocation patterns:
- `/commit` - stage, commit, and push changes
- `/review-pr` - comprehensive PR review
- `/search` - web search with context

## How Claude Code Uses These

When running Valor through the Claude Code SDK:
1. **Tools are registered** as MCP servers available to Claude Code
2. **Workflows are documented** so Claude Code knows common patterns
3. **Skills provide shortcuts** for frequent operations
4. **Claude Code decides** when to spawn sub-agents for parallel work

We can provide light suggestions (e.g., "this task might benefit from parallel execution") but Claude Code handles the orchestration.

## Documentation Requirements

For Claude Code to effectively use these capabilities:
- Every tool must have clear input/output schemas
- Every workflow must document its steps and when to use it
- Every skill must explain what it does and its prerequisites
- Error cases must be documented with recovery suggestions

---

# Tool Implementation Patterns

## Error Categorization

```python
# Hierarchical error handling
ERROR_CATEGORIES = {
    1: "Configuration Errors",     # Missing API keys
    2: "Validation Errors",        # Invalid inputs
    3: "File System Errors",       # File not found
    4: "Network/API Errors",       # Timeouts, rate limits
    5: "Processing Errors",        # Encoding, parsing
    6: "Generic Errors"            # Unexpected issues
}
```

## Pre-Validation

```python
# Validate inputs BEFORE expensive operations
valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
file_extension = Path(image_path).suffix.lower()
if file_extension not in valid_extensions:
    return f"Error: Unsupported format '{file_extension}'"
```

## Three-Layer Architecture

```
Layer 1: Agent Tool (Context Extraction)
    |
Layer 2: Implementation (Core Logic)
    |
Layer 3: MCP Tool (Claude Code Integration)
```

## Context-Aware Behavior

Tools adapt their behavior based on:
- Different prompts for different use cases
- Platform-aware response formatting
- Context injection for relevance
- Adaptive response length limits

## Operation Timing Targets

| Operation Type | Target | Maximum |
|----------------|--------|---------|
| Simple Query | <500ms | 1s |
| API Call | <2s | 5s |
| File Processing | <1s | 3s |
| Batch Operation | <5s | 30s |

---

# Testing Strategy

## Testing Philosophy

### 1. Intelligence Validation vs Keyword Matching

```python
# DON'T: Keyword-based validation
assert "success" in response.lower()

# DO: Intelligence-based validation using AI judges
judgment = judge_test_result(
    test_output=response,
    expected_criteria=[
        "provides specific actionable suggestions",
        "considers user experience principles"
    ]
)
assert judgment.pass_fail and judgment.confidence > 0.8
```

### 2. Real Integrations Over Mocks

> "Do not write tests that mock real libraries and APIs. Use the actual library and actual API" - CLAUDE.md

**When Mocks Are Acceptable:**
- External service downtime (graceful skip preferred)
- Cost-prohibitive operations (use local alternatives)
- Destructive operations (use test accounts)

### 3. Happy Path Focus

**Priority Order:**
1. **Primary Flow** (80%) - Common user interactions
2. **Integration Points** (15%) - API connections
3. **Error Handling** (4%) - Graceful degradation
4. **Edge Cases** (1%) - Only after core stability

## Test Categories

### Unit Tests
- Validate individual component behavior
- Fast execution (<1s per test)
- Minimal dependencies
- No external service calls

### Integration Tests
- Validate component interactions
- Message type handling
- Database interactions
- Use real services when possible

### End-to-End Tests
- Complete user journeys with real services
- Real Telegram messages
- Full pipeline processing
- Database state validation

### Performance Tests
- Memory: <500MB baseline, <50MB per session
- CPU: <80% sustained, <95% peak
- Response time: <2s text, <5s media
- Concurrent: 50+ users

### Intelligence Tests (AI Judges)
- Uses local LLMs (Ollama with gemma2:3b)
- Structured judgment results
- Configurable strictness levels
- Fallback parsing for robustness

## Quality Gates

| Test Type | Pass Rate Required |
|-----------|-------------------|
| Unit Tests | 100% |
| Integration Tests | 95% |
| E2E Tests | 90% |
| Performance Tests | Meet all baselines |
| Intelligence Tests | >0.8 confidence |

---

# Operations & Monitoring

## Health Check Endpoints

### Core Health
```http
GET /health
Response: {"status": "healthy", "telegram": "connected"}
```

### Resource Status
```http
GET /resources/status
Response: {
  "health": {
    "memory_mb": 345.2,
    "cpu_percent": 25.5,
    "active_sessions": 12,
    "health_score": 87.5
  }
}
```

### Telegram Status
```http
GET /telegram/status
```

## Health Score Calculation

```python
def calculate_health_score():
    memory_health = max(0, 100 - (memory_percent * 1.5))
    cpu_health = max(0, 100 - (cpu_percent * 1.2))
    session_health = max(0, 100 - (session_load * 100))

    return (memory_health * 0.4 +
            cpu_health * 0.3 +
            session_health * 0.3)
```

## Alert Levels

| Level | Action |
|-------|--------|
| **Low** | Logged only |
| **Medium** | Logged + callback |
| **High** | Immediate callback |
| **Critical** | Auto-restart trigger |

## Startup Procedure

```bash
# 1. Check existing processes
check_server()
check_telegram_auth()

# 2. Database recovery
recover_database_locks()
test_database_connectivity()

# 3. Initialize services
initialize_database()
start_huey()
start_server()

# 4. Enable monitoring
resource_monitor.start_monitoring()
auto_restart_manager.start_monitoring()
```

## Shutdown Procedure

```bash
# Graceful Shutdown
scripts/stop.sh
# 1. Stop services gracefully (SIGTERM)
# 2. Wait for completion (2s timeout)
# 3. Force termination if needed (SIGKILL)
# 4. Cleanup orphaned processes
# 5. Release database locks
```

## Log Management

**Log Files:**
- `logs/system.log` - Main application (rotating, 10MB max)
- `logs/tasks.log` - Background task execution
- `logs/telegram.log` - Telegram-specific operations

**Log Levels:**
- **DEBUG**: Detailed execution flow
- **INFO**: Normal operations, health checks
- **WARNING**: Recoverable issues
- **ERROR**: Failures requiring attention
- **CRITICAL**: System-threatening issues

## Common Issues and Solutions

### Database Lock Errors
```bash
scripts/start.sh  # Includes automatic recovery
# Or manual:
lsof data/*.db | awk '{print $2}' | xargs kill -9
sqlite3 data/system.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

### High Memory Usage
1. Check `/resources/status` for session count
2. Trigger manual cleanup via API
3. Review context window sizes
4. Consider restart if > 1GB

### Telegram Disconnections
```bash
scripts/telegram_logout.sh
scripts/telegram_login.sh
scripts/start.sh
```

## Maintenance Schedule

| Frequency | Tasks |
|-----------|-------|
| **Hourly** | Resource check, session review, alert processing |
| **Daily** | Database task cleanup, log rotation |
| **Weekly** | Database VACUUM, log archival |
| **Monthly** | Full health audit, capacity planning |

---

# Daydream System

## Overview

The Daydream System is an autonomous AI-powered analysis and reflection framework that performs deep codebase exploration and generates architectural insights. It operates on a 6-phase execution lifecycle, running during non-office hours.

## 6-Phase Execution Lifecycle

### Phase 1: System Readiness Check
- Pending tasks < 5
- No critical system alerts
- Available memory > 400MB
- Office hours check (skip 9 AM - 6 PM)

### Phase 2: Pre-Analysis Cleanup
- Kill Claude Code processes > 24 hours old
- Terminate orphaned Aider sessions
- Remove temporary analysis files
- Archive old insight files (keep last 10)

### Phase 3: Comprehensive Context Gathering
- Workspace analysis (git status, tech stack)
- System metrics (success rates, trends)
- Development trends (activity patterns)
- Recent activity (last 7 days)

### Phase 4: AI Analysis Execution
Uses local AI model (Ollama) for:
- Architecture Patterns & Design
- Code Quality & Technical Health
- Development Velocity & Productivity
- Technology Stack & Dependencies
- Strategic Opportunities
- Future Direction & Vision

### Phase 5: Output Processing and Archival
- Log insights to console
- Write to `logs/daydream_insights.md`
- Archive historical insights
- Web interface at `/daydreams`

### Phase 6: Post-Analysis Cleanup
- Kill active Aider process
- Clean analysis artifacts
- Archive insights
- Generate session summary

## Execution Schedule

```python
# Cron: minute=0, hour='18,21,0,3,6'
# 6:00 PM - Evening analysis
# 9:00 PM - Night analysis
# 12:00 AM - Midnight analysis
# 3:00 AM - Early morning analysis
# 6:00 AM - Dawn analysis
```

## Resource Limits

- Session timeout: 2 hours maximum
- Max workspaces per cycle: 3
- Database timeout: 5 seconds
- Memory target: <400MB during analysis

---

# Security & Compliance

## Security Architecture

### Defense in Depth

```
Layer 1: Network Security (Firewall, DDoS, TLS)
    |
Layer 2: Application Security (Input Validation, Sandboxing)
    |
Layer 3: Authentication/Authorization (User Auth, Workspace Isolation)
    |
Layer 4: Data Security (Encryption at Rest/Transit, Key Management)
    |
Layer 5: Monitoring/Response (Security Monitoring, Incident Response)
```

### Security Zones

| Zone | Security Level | Access Control |
|------|----------------|----------------|
| **Public** | Standard | Rate limiting, user whitelist |
| **Application** | High | Authenticated users only |
| **Execution** | Maximum | Isolated containers |
| **Data** | Maximum | Encrypted, access logged |
| **Management** | Critical | MFA, audit trail |

## STRIDE Analysis

### Spoofing Identity
- User whitelist validation
- Session token rotation
- API key encryption and rotation
- Rate limiting per user

### Tampering with Data
- TLS for all communications
- Database integrity checks
- Input sanitization
- Parameterized queries

### Repudiation
- Comprehensive audit logging
- Digital signatures on critical operations
- Immutable log storage

### Information Disclosure
- Encryption at rest and in transit
- Secrets management system
- Access control lists

### Denial of Service
- Rate limiting (10 req/min per user)
- Resource quotas
- Circuit breakers

### Elevation of Privilege
- Container isolation
- Principle of least privilege
- Security boundaries enforcement

## Data Protection

### Encryption Standards

**At Rest:**
- Algorithm: AES-256-GCM
- Scope: Database, backups, logs, config

**In Transit:**
- Protocol: TLS 1.3 minimum
- Scope: All API communications

### Data Classification

| Level | Examples |
|-------|----------|
| **Public** | System status, documentation |
| **Internal** | Configuration, metrics |
| **Confidential** | Conversations, code, API responses |
| **Restricted** | API keys, encryption keys, PII |

## Code Execution Security

### Sandbox Environment
- Container: Docker with security profiles
- CPU: 1 core limit
- Memory: 512MB limit
- Disk: 100MB limit
- Network: Restricted egress
- Time: 30 second timeout

### Restrictions
- No file system access outside sandbox
- No network access to internal services
- No system calls (seccomp)
- No privilege escalation
- Read-only root filesystem

## Compliance

### GDPR Requirements
- User consent for data processing
- Right to access, delete, port data
- Data breach notification (72 hours)
- Privacy policy and DPAs

### SOC 2 Trust Service Criteria
- Security: Access controls, monitoring
- Availability: SLA compliance, disaster recovery
- Confidentiality: Encryption, access restrictions
- Processing Integrity: Validation, logging
- Privacy: Collection limits, retention policies

### OWASP Top 10 Addressed
- A01: Broken Access Control - RBAC, workspace isolation
- A02: Cryptographic Failures - TLS 1.3, AES-256
- A03: Injection - Input sanitization
- A04: Insecure Design - Threat modeling
- A05: Security Misconfiguration - Hardening
- A06: Vulnerable Components - Dependency scanning
- A07: Authentication Failures - MFA, sessions
- A08: Data Integrity Failures - Integrity checks
- A09: Logging Failures - Comprehensive audits
- A10: SSRF - Network restrictions

## Emergency Response

```
IMMEDIATE (0-15 min):
1. Isolate affected systems
2. Preserve evidence
3. Notify security team
4. Begin investigation

SHORT TERM (15-60 min):
5. Assess scope and impact
6. Implement containment
7. Notify stakeholders
8. Prepare communications

RECOVERY (1-24 hours):
9. Eradicate threat
10. Restore from clean backups
11. Verify system integrity
12. Resume operations

POST-INCIDENT (24-72 hours):
13. Complete investigation
14. Document lessons learned
15. Update security controls
16. Regulatory notifications
```

---

# Development Guidelines

## Common Commands

### Running the System

```bash
# Start production server
./scripts/start.sh

# Start demo server (no API keys)
./scripts/start.sh --demo

# Start Telegram bot
./scripts/start.sh --telegram

# Validate configuration
./scripts/start.sh --dry-run

# Shutdown cleanly
./scripts/stop.sh
```

### Monitoring Logs

```bash
# Tail all logs
./scripts/logs.sh

# Specific logs
./scripts/logs.sh --main       # Main application
./scripts/logs.sh --telegram   # Telegram
./scripts/logs.sh --errors     # Errors only
```

### Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=. --cov-report=html

# Run specific categories
pytest tests/unit/
pytest tests/integration/
```

### Code Quality

```bash
# Format code
black .

# Check style
ruff check .

# Type checking
mypy . --strict

# All checks
black . && ruff check . && mypy . --strict
```

## Environment Variables

```bash
# Telegram Configuration
TELEGRAM_API_ID=***
TELEGRAM_API_HASH=***
TELEGRAM_PHONE=***
TELEGRAM_PASSWORD=***

# API Keys
OPENAI_API_KEY=***
ANTHROPIC_API_KEY=***
PERPLEXITY_API_KEY=***

# Database
DATABASE_PATH=data/ai_rebuild.db
DATABASE_BACKUP_ON_STARTUP=true

# Monitoring
MONITORING_ENABLED=true
MONITORING_DASHBOARD_PORT=8080
```

## Project Structure

```
ai/
|-- agents/
|   |-- valor/
|   |   |-- agent.py          # Main ValorAgent
|   |   +-- persona.md        # Persona definition
|   +-- subagents/            # Specialized subagents
|-- config/
|   |-- workspace_config.json # Multi-workspace config
|   +-- telegram_groups.json  # Group behavior config
|-- data/                     # SQLite databases
|-- docs/                     # Documentation
|-- integrations/
|   +-- telegram/             # Telegram handlers
|-- logs/                     # Log files
|-- mcp_servers/              # MCP tool servers
|-- scripts/                  # Operational scripts
|-- tests/                    # Test suites
|-- tools/                    # Tool implementations
+-- utilities/                # Shared utilities
```

---

# Codebase Context & RAG

## Strategy Overview

For organizing and retrieving information across multiple project workspaces, we recommend a **local embedding approach** over more complex RAG systems.

## Evaluated Options

### Apple CLaRa (Not Recommended Yet)

[Apple's CLaRa](https://github.com/apple/ml-clara) offers state-of-the-art document compression (32x-64x) but:
- Requires full Mistral-7B base model (~14GB FP16)
- No MLX version yet (announced "coming soon")
- Trained on QA datasets, not code
- Total memory: ~17-20GB FP16

**Wait for MLX version before adopting.**

### Local Embedding + Vector DB (Recommended)

```
Codebase Files
    |
    v
Local Embeddings (nomic-embed-text via Ollama, ~300MB)
    |
    v
Vector DB (ChromaDB or SQLite-vec)
    |
    v
Relevant chunks passed to Claude Code context
```

**Memory Budget: ~1-2GB total**

| Component | RAM Usage |
|-----------|-----------|
| Embedding model | ~300MB |
| Vector DB | ~100-500MB |
| Overhead | ~200MB |

## Per-Workspace Indexing

Each workspace gets its own index:

```json
{
  "workspaces": {
    "project-name": {
      "index_config": {
        "enabled": true,
        "include_patterns": ["**/*.py", "**/*.md"],
        "exclude_patterns": [".venv/**", "__pycache__/**"]
      }
    }
  }
}
```

## File Types to Index

**High Priority:** `*.py`, `*.md`, `CLAUDE.md`, `README.md`, `*.json`

**Exclude:** `node_modules/`, `.venv/`, `__pycache__/`, `.git/`, binaries

## Integration with Subagents

```
User Query → Main Agent → WorkspaceIndexer.query()
    → Relevant chunks → Claude Code context
```

---

# Quick Reference

## Useful Commands

| Command | Description |
|---------|-------------|
| `./scripts/start.sh` | Start the system |
| `./scripts/stop.sh` | Stop the system |
| `./scripts/logs.sh` | View logs |
| `./scripts/telegram_login.sh` | Authenticate Telegram |
| `curl localhost:9000/health` | Check health |
| `curl localhost:9000/resources/status` | Check resources |

## Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `/health` | Basic health check |
| `/resources/status` | Detailed resource status |
| `/telegram/status` | Telegram connection status |
| `/daydreams` | View daydream insights |
| `/restart/status` | Auto-restart status |

## Critical Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Memory | 600MB | 800MB |
| CPU | 80% | 95% |
| Health Score | <70 | <60 |
| Sessions | 80 | 100 |

## Emergency Contacts

- **System Issues**: Check logs at `logs/system.log`
- **Telegram Issues**: Re-authenticate with `telegram_login.sh`
- **Database Issues**: Run startup script (includes recovery)

---

*This documentation consolidates the complete Valor AI System documentation for easy reference and printing.*

*Document Version: 2.2 | Generated: December 17, 2025*
