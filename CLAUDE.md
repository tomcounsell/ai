# CLAUDE.md

Guidance for Claude Code when working with this repository.

**IMPORTANT CONTEXT**: You ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself. Respond as the embodiment of this AI system.

## Quick Commands

| Command | Description |
|---------|-------------|
| `./scripts/start_bridge.sh` | Start Telegram bridge |
| `./scripts/valor-service.sh status` | Check bridge status |
| `./scripts/valor-service.sh restart` | Restart after code changes |
| `tail -f logs/bridge.log` | Stream bridge logs |
| `pytest tests/` | Run all tests |
| `black . && ruff check .` | Format and lint |

## Development Principles

### 1. NO LEGACY CODE TOLERANCE
- Never leave traces of legacy code or systems
- Always overwrite, replace, and delete obsolete code completely
- No commented-out code, no "temporary" bridges, no half-migrations

### 2. CRITICAL THINKING MANDATORY
- Foolish optimism is not allowed - always think deeply
- Question assumptions, validate decisions, anticipate consequences
- Prioritize robust solutions over quick fixes

### 3. INTELLIGENT SYSTEMS OVER RIGID PATTERNS
- Use LLM intelligence instead of keyword matching
- Context-aware decision making over static rule systems

### 4. MANDATORY COMMIT AND PUSH WORKFLOW
- ALWAYS commit and push changes at the end of every task
- Never leave work uncommitted in the repository
- Use `git add . && git commit -m "Description" && git push`

### 5. CONTEXT COLLECTION AND MANAGEMENT
- Context is the lifeblood of agentic systems
- Explicitly pass context when spawning sub-agents
- Track the "why" alongside the "what"

### 6. TOOL AND MCP SELECTION
- Loading all tools pollutes context and degrades performance
- Start with minimal tools, expand only if needed

### 7. DEFINITION OF DONE
- **Built**: Code is implemented and working
- **Tested**: Unit tests passing, manual verification complete
- **Documented**: Code comments, API docs as appropriate
- **Plans migrated**: `docs/plans/` → `docs/features/`

### 8. PARALLEL EXECUTION (P-Thread Pattern)
- When facing independent tasks, spawn parallel sub-agents using Task tool
- Do NOT parallelize sequential/dependent work
- Always aggregate results before reporting

### 9. SDLC PATTERN FOR CODE CHANGES
- All code changes MUST follow: Plan → Build → Test → Review → Ship
- If tests fail: loop back to Build, fix, re-test (up to 5 iterations)
- Do NOT skip phases. Do NOT ship without tests passing.

### 10. ALWAYS RESTART RUNNING SERVICES
- If bridge is running and you modify bridge/agent code, restart immediately after committing
- Restart: `./scripts/valor-service.sh restart`
- Verify: `tail -5 logs/bridge.log` shows "Connected to Telegram"

## System Architecture

```
Telegram → Python Bridge (Telethon) → Claude Agent SDK → Claude API
              (bridge/telegram_bridge.py)    (agent/sdk_client.py)
```

**Key Directories:**
- `.claude/commands/` - Slash command skills
- `.claude/agents/` - Subagent definitions
- `bridge/` - Telegram integration
- `tools/` - Local Python tools
- `config/` - Configuration files

## Testing Philosophy

- **Real integration testing** - No mocks, use actual APIs
- **Intelligence validation** - Use AI judges, not keyword matching
- **Quality gates**: Unit 100%, Integration 95%, E2E 90%

## Work Completion Criteria

Work is DONE when:
1. ✅ Deliverable exists and works
2. ✅ Code quality standards met (`ruff`, `black`)
3. ✅ Changes committed and pushed to git
4. ✅ Original request fulfilled

## Session Management

| State | Description |
|-------|-------------|
| **Active** | Currently processing message |
| **Dormant** | Work paused, waiting for reply |
| **Abandoned** | Unfinished work, auto-revived |

- Fresh messages create new sessions
- Reply-to messages resume original session
- Mark complete with `mark_work_done()` when finished

## Quick Reference

### Critical Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Memory | 600MB | 800MB |
| CPU | 80% | 95% |

### Emergency Recovery

- **Bridge Issues**: `./scripts/valor-service.sh restart`
- **Telegram Auth**: `python scripts/telegram_login.py`
- **SDK Issues**: Check `USE_CLAUDE_SDK=true` in `.env`

### Configuration Files

- `.env` - Environment variables and API keys
- `config/projects.json` - Multi-project configuration
- `.claude/settings.local.json` - Claude Code settings

## Plan Requirements (This Repo Only)

When creating plans with `/make-plan` for this repository, always include an **## Update System** section after **## No-Gos**. This system is deployed across multiple machines via the `/update` skill (`scripts/remote-update.sh`, `.claude/skills/update/`). New features frequently require complementary changes to the update process — new dependencies, config migrations, service restarts, symlink changes, etc.

The **## Update System** section should cover:
- Whether the update script or update skill needs changes
- New dependencies or config files that must be propagated
- Migration steps for existing installations
- If no update changes are needed, state that explicitly (e.g., "No update system changes required — this feature is purely internal")

This ensures update impact is considered during planning rather than discovered after deployment.

When creating plans that add new tools, capabilities, or external integrations, always include an **## Agent Integration** section after **## Update System**. The agent receives Telegram messages via the bridge (`bridge/telegram_bridge.py`) and can only use tools exposed through MCP servers registered in `.mcp.json`. New Python functions in `tools/` are invisible to the agent unless wrapped.

The **## Agent Integration** section should cover:
- Whether a new or existing MCP server needs to expose the functionality
- Changes to `.mcp.json` or `mcp_servers/` directory
- Whether the bridge itself needs to import/call the new code directly
- Integration tests that verify the agent can actually invoke the new tools
- If no agent integration is needed, state that explicitly (e.g., "No agent integration required — this is a bridge-internal change")

This ensures new capabilities are wired into the system the user actually interacts with, not just built as standalone libraries.

## See Also

| Resource | Purpose |
|----------|---------|
| `/prime` | Full architecture deep dive and codebase onboarding |
| `/setup` | New machine configuration |
| `/review` | Implementation validation with screenshots |
| `/add-feature` | How to extend the system |
| `/sdlc` | Autonomous Plan→Build→Test→Ship workflow |
| `docs/deployment.md` | Multi-instance deployment |
| `docs/tools-reference.md` | Complete tool documentation |
| `config/SOUL.md` | Valor persona and philosophy |
