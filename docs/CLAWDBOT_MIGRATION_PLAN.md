# Clawdbot Migration Plan

**Document Version**: 4.0
**Created**: 2025-01-19
**Updated**: 2026-01-19
**Status**: Phase 6 Complete - Agentic Philosophy Integrated

---

## Migration Progress

### Completed

| Phase | Component | Status | Notes |
|-------|-----------|--------|-------|
| 1 | Clawdbot Installation | **Done** | v2026.1.16-2 installed globally |
| 2 | SOUL.md Persona | **Done** | Deployed to ~/clawd/SOUL.md |
| 3 | Telegram Integration | **Done** | Python bridge with Telethon (user account) |
| 3 | End-to-End Flow | **Done** | Message → Bridge → Clawdbot → Claude → Reply |
| 3 | Self-Management | **Done** | valor-service.sh for restart/status/health |
| 3 | Launchd Service | **Done** | Auto-start on boot, auto-restart on crash |
| 4 | Sentry Skill | **Done** | 8 tools for error monitoring |
| 4 | GitHub Skill | **Done** | 10 tools for repository operations |
| 4 | Linear Skill | **Done** | 9 tools for project management |
| 4 | Notion Skill | **Done** | 8 tools for documentation |
| 4 | Stripe Skill | **Done** | 9 tools for payment processing |
| 4 | Render Skill | **Done** | 9 tools for deployment |
| 5 | Daydream Cron | **Done** | 6-step daily maintenance process |
| 6 | Agentic Philosophy | **Done** | Core Four, Thread Types, Validation Loops in SOUL.md |
| 6 | /pthread Skill | **Done** | Parallel thread execution, auto-parallelization |
| 6 | /sdlc Skill | **Done** | Autonomous Plan→Build→Test→Review→Ship workflow |
| 6 | .claude/README.md | **Done** | Philosophy docs, thread types, skills reference |

---

## Current Architecture

The system uses a **hybrid architecture**:

```
Telegram → Python Bridge (Telethon) → clawdbot agent --local → Claude API
    ↑              ↓                           ↓
    └──────────────┘                    ~/clawd/SOUL.md
        Response                              │
                                              ↓
                                    ~/clawd/skills/
                                    ├── sentry/
                                    ├── github/
                                    ├── linear/
                                    ├── notion/
                                    ├── stripe/
                                    ├── render/
                                    └── daydream/
```

**Why hybrid?**
- Clawdbot's Telegram support uses bot tokens, not user accounts
- We need user account for natural @mentions in groups
- Python bridge handles Telegram, Clawdbot handles AI logic
- Clean separation of concerns

### Key Files

| File | Purpose |
|------|---------|
| `bridge/telegram_bridge.py` | Telegram ↔ Clawdbot bridge |
| `~/clawd/SOUL.md` | Valor persona definition |
| `~/.clawdbot/clawdbot.json` | Clawdbot configuration |
| `scripts/valor-service.sh` | Service management |
| `~/Library/LaunchAgents/com.valor.bridge.plist` | Auto-start service |
| `~/clawd/skills/` | All Clawdbot skills |
| `.claude/commands/` | Claude Code skills (/prime, /pthread, /sdlc) |
| `.claude/README.md` | Agentic philosophy documentation |

---

## Core Principles (Preserved)

1. **Pure Agency** - System handles complexity internally. No "working on it" messages.

2. **Valor as Coworker** - Not an assistant. A colleague with their own machine.

3. **No Custom Subagent System** - Clawdbot + Claude Code orchestrate. No custom agent layer.

4. **No Restrictions** - Valor owns the machine entirely. No sandboxing.

5. **Security Simplified** - Protect API keys. That's it.

6. **Self-Improving** - Valor can modify his own code and restart himself.

---

## Phase 4: Skills Migration (Complete)

All skills implemented in `~/clawd/skills/` with consistent structure:

```
~/clawd/skills/<skill-name>/
├── manifest.json     # Triggers, permissions, env requirements
├── index.js          # Skill entry point
├── prompts/
│   └── system.md     # Skill-specific system prompt
├── tools/
│   └── *.js          # Individual tool implementations
└── README.md         # Documentation
```

### Sentry Skill (8 tools)

Error monitoring and performance analysis for self-awareness.

| Tool | Description |
|------|-------------|
| `list_issues` | List error issues with filters |
| `get_issue` | Get detailed issue info |
| `list_events` | List error events for an issue |
| `get_event` | Get specific event details |
| `list_projects` | List all Sentry projects |
| `get_performance_data` | Get transaction performance |
| `update_issue_status` | Change issue status |
| `resolve_issue` | Mark issue as resolved |

**Requires**: `SENTRY_API_KEY`, `SENTRY_ORG_SLUG`

### GitHub Skill (10 tools)

Repository operations for self-improvement workflow.

| Tool | Description |
|------|-------------|
| `list_prs` | List pull requests |
| `get_pr` | Get PR details |
| `create_pr` | Create new PR |
| `merge_pr` | Merge a PR |
| `list_issues` | List repository issues |
| `create_issue` | Create new issue |
| `get_commits` | Get commit history |
| `get_checks` | Get CI/CD status |
| `search_code` | Search repository code |
| `get_file` | Get file contents |

**Requires**: `GITHUB_TOKEN`

### Linear Skill (9 tools)

Project management and issue tracking for Daydream.

| Tool | Description |
|------|-------------|
| `list_issues` | List team issues |
| `get_issue` | Get issue details |
| `create_issue` | Create new issue |
| `update_issue` | Update issue fields |
| `close_issue` | Close an issue |
| `list_cycles` | List sprint cycles |
| `get_team_velocity` | Get velocity metrics |
| `search_issues` | Search all issues |
| `get_roadmap` | Get project roadmap |

**Requires**: `LINEAR_API_KEY`

### Notion Skill (8 tools)

Documentation and knowledge base management.

| Tool | Description |
|------|-------------|
| `search` | Search pages and databases |
| `get_page` | Get page content |
| `create_page` | Create new page |
| `update_page` | Update page properties |
| `append_blocks` | Add content blocks |
| `list_databases` | List all databases |
| `query_database` | Query database entries |
| `create_database_entry` | Add database entry |

**Requires**: `NOTION_API_KEY`

### Stripe Skill (9 tools)

Payment processing with security features.

| Tool | Description |
|------|-------------|
| `list_customers` | List customers |
| `get_customer` | Get customer details |
| `list_subscriptions` | List subscriptions |
| `get_subscription` | Get subscription details |
| `list_invoices` | List invoices |
| `create_refund` | Process refund (confirms > $100) |
| `get_balance` | Get account balance |
| `get_mrr` | Calculate MRR |
| `cancel_subscription` | Cancel subscription |

**Requires**: `STRIPE_API_KEY`

### Render Skill (9 tools)

Cloud infrastructure and deployment management.

| Tool | Description |
|------|-------------|
| `list_services` | List all services |
| `get_service` | Get service details |
| `get_service_logs` | Get service logs |
| `deploy_service` | Trigger deployment |
| `restart_service` | Restart service |
| `scale_service` | Scale instances |
| `list_deploys` | List deployments |
| `get_env_vars` | Get environment variables |
| `update_env_vars` | Update environment variables |

**Requires**: `RENDER_API_KEY`

---

## Phase 5: Daydream (Complete)

Daily autonomous maintenance process scheduled at 6 AM Pacific.

---

## Phase 6: Agentic Philosophy & Advanced Skills (Complete)

Enhanced system with agentic engineering principles and autonomous workflows.

### Core Four Framework (in SOUL.md)

Everything reduces to four primitives:

| Primitive | Description |
|-----------|-------------|
| **Context** | What information the agent has access to |
| **Model** | The intelligence powering the agent |
| **Prompt** | The instructions driving behavior |
| **Tools** | The capabilities the agent can invoke |

### Thread-Based Engineering

| Thread Type | Description | Use Case |
|-------------|-------------|----------|
| **Base** | Single prompt → work → review | Simple tasks |
| **P-Thread** | Multiple agents in parallel | Independent subtasks |
| **C-Thread** | Chained phases with checkpoints | Large changes |
| **F-Thread** | Same prompt to multiple agents | Exploring approaches |
| **B-Thread** | Agents orchestrating other agents | Complex orchestration |
| **L-Thread** | Extended autonomous work | Long-running tasks |

### /pthread Skill

Parallel thread execution with auto-parallelization:

- **Triggers**: Multiple independent searches, multi-file analysis, exploration
- **Patterns**: Fusion threads (same goal, multiple agents)
- **Aggregation**: Merge, Best-of-N, Synthesize, Deduplicate
- **Integration**: Ralph Wiggum pattern (don't stop until aggregation complete)

### /sdlc Skill

Autonomous AI Developer Workflow:

```
Plan → Build → Test → Review → Ship
         ↑           │
         └───────────┘ (loop on failure)
```

- **Quality gates** at each phase
- **Auto loop-back** on test failures (up to 5 iterations)
- **Self-verification** via validation loops
- **Completion criteria**: All gates pass, code pushed

### Validation Loops (Ralph Wiggum Pattern)

Agents verify their own work:

1. Agent attempts completion
2. Validation runs (tests, linting, checks)
3. If fail → continue with feedback
4. If pass → complete

Creates closed-loop systems that self-correct.

### Key Philosophy

*"Master the agent, master engineering."*

*"Scale your compute to scale your impact."*

*"Build the system that builds the system."*

---

## Phase 5: Daydream Details

### Steps

| Step | Name | Description |
|------|------|-------------|
| 1 | `clean_legacy` | Remove deprecated patterns, dead code |
| 2 | `review_logs` | Analyze yesterday's logs for issues |
| 3 | `check_sentry` | Query for new/recurring errors |
| 4 | `clean_tasks` | Update Linear issues, close stale items |
| 5 | `update_docs` | Ensure docs match code |
| 6 | `daily_report` | Summary sent to supervisor via Telegram |

### Configuration

```json
{
  "cron": {
    "jobs": [{
      "name": "daydream",
      "schedule": "0 6 * * *",
      "timezone": "America/Los_Angeles",
      "resumable": true
    }]
  }
}
```

### Resumability

State persisted to `~/.clawd/skills/daydream/state.json`:
- Tracks completed steps
- Records last run date
- Stores findings from each step
- Enables crash recovery

---

## Validation Checklist

### Phase 1-3: Core System (Complete)

- [x] Clawdbot installed (`clawdbot --version`)
- [x] SOUL.md deployed (`~/clawd/SOUL.md`)
- [x] Clawdbot config created (`~/.clawdbot/clawdbot.json`)
- [x] Python bridge working (`bridge/telegram_bridge.py`)
- [x] Telegram connected and responding
- [x] End-to-end message flow working
- [x] Self-management scripts (`valor-service.sh`)
- [x] Launchd service installed (auto-start)
- [x] Service survives restart

### Phase 4: Skills (Complete)

- [x] Sentry skill implemented (8 tools)
- [x] GitHub skill implemented (10 tools)
- [x] Linear skill implemented (9 tools)
- [x] Notion skill implemented (8 tools)
- [x] Stripe skill implemented (9 tools)
- [x] Render skill implemented (9 tools)

### Phase 5: Daydream (Complete)

- [x] Daydream skill created
- [x] All 6 steps implemented
- [x] Cron job configured (6 AM Pacific)
- [x] Resumability with state persistence
- [x] Daily report step included

### Phase 6: Agentic Philosophy (Complete)

- [x] SOUL.md enhanced with Core Four Framework
- [x] Thread-Based Engineering documented (6 thread types)
- [x] /pthread skill created (parallel execution)
- [x] /sdlc skill created (autonomous dev workflow)
- [x] Validation Loops (Ralph Wiggum Pattern) documented
- [x] .claude/README.md updated with philosophy reference
- [x] Key wisdom quotes integrated

---

## Service Commands

```bash
# Check status
./scripts/valor-service.sh status

# Restart (after code changes)
./scripts/valor-service.sh restart

# View logs
./scripts/valor-service.sh logs

# Health check
./scripts/valor-service.sh health

# Install service (first time)
./scripts/valor-service.sh install

# Uninstall service
./scripts/valor-service.sh uninstall
```

---

## Rollback Plan

If issues arise:

1. **Stop the bridge:**
   ```bash
   ./scripts/valor-service.sh stop
   ```

2. **Check logs:**
   ```bash
   tail -100 logs/bridge.error.log
   ```

3. **Restart:**
   ```bash
   ./scripts/valor-service.sh start
   ```

4. **If persistent issues**, the old Python-only system can be restored from git history (tag: `v1.0-pre-clawdbot`).

---

## Skills Directory Summary

### Clawdbot Skills (`~/clawd/skills/`)

```
~/clawd/skills/
├── daydream/     # 6-step daily maintenance (cron)
├── github/       # 10 repository tools
├── linear/       # 9 project management tools
├── notion/       # 8 documentation tools
├── render/       # 9 deployment tools
├── self-manage/  # Self-management utilities
├── sentry/       # 8 error monitoring tools
└── stripe/       # 9 payment processing tools

Total: 454 JavaScript files, 1085 files overall
```

### Claude Code Skills (`.claude/commands/`)

```
.claude/commands/
├── prime.md          # Codebase primer and context loader
├── pthread.md        # Parallel thread execution
├── sdlc.md           # Autonomous dev workflow (Plan→Build→Test→Ship)
└── audit-next-tool.md # Tool quality audits
```

| Skill | Purpose |
|-------|---------|
| `/prime` | Load codebase context and architecture |
| `/pthread` | Scale compute via parallel agent execution |
| `/sdlc` | Autonomous Plan→Build→Test→Review→Ship workflow |
| `/audit-next-tool` | Quality audits for tools |

---

## Future Enhancements

With all core phases complete, potential future work:

1. **Skill Testing** - Integration tests for each skill
2. **Metrics Dashboard** - Visualize Daydream findings
3. **Additional Skills** - Slack, Jira, AWS as needed
4. **Performance Tuning** - Optimize tool response times

---

*Document updated: 2026-01-19*
