# Clawdbot Migration Plan

**Document Version**: 2.0
**Created**: 2025-01-19
**Updated**: 2025-01-19
**Status**: Phase 3 Complete - Core System Working

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

### In Progress / Planned

| Phase | Component | Status | Priority |
|-------|-----------|--------|----------|
| 4 | Sentry Skill | Planned | High |
| 4 | GitHub Skill | Planned | High |
| 4 | Linear Skill | Planned | Medium |
| 4 | Notion Skill | Planned | Medium |
| 4 | Stripe Skill | Planned | Low |
| 4 | Render Skill | Planned | Low |
| 5 | Daydream Cron | Planned | Medium |

---

## Current Architecture

The system uses a **hybrid architecture**:

```
Telegram → Python Bridge (Telethon) → clawdbot agent --local → Claude API
    ↑              ↓                           ↓
    └──────────────┘                    ~/clawd/SOUL.md
        Response
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

---

## Core Principles (Preserved)

1. **Pure Agency** - System handles complexity internally. No "working on it" messages.

2. **Valor as Coworker** - Not an assistant. A colleague with their own machine.

3. **No Custom Subagent System** - Clawdbot + Claude Code orchestrate. No custom agent layer.

4. **No Restrictions** - Valor owns the machine entirely. No sandboxing.

5. **Security Simplified** - Protect API keys. That's it.

6. **Self-Improving** - Valor can modify his own code and restart himself.

---

## Phase 4: Skills Migration (Planned)

Skills will be implemented as Clawdbot skills in `~/clawd/skills/`.

### Priority Order

1. **Sentry** - Error monitoring for self-awareness
2. **GitHub** - Code operations for self-improvement
3. **Linear** - Task management for Daydream
4. **Notion** - Documentation for knowledge base
5. **Stripe** - Payment operations (as needed)
6. **Render** - Deployment operations (as needed)

### Skill Structure

```
~/clawd/skills/sentry/
├── SKILL.md        # Description and usage
├── manifest.json   # Triggers, permissions, env requirements
└── handlers.py     # Python implementation
```

See [SKILLS_MIGRATION.md](SKILLS_MIGRATION.md) for detailed implementation guide.

---

## Phase 5: Daydream (Planned)

Daily autonomous maintenance process.

### Steps

1. **Clean Legacy Code** - Remove deprecated patterns, dead code
2. **Review Logs** - Analyze yesterday's logs for issues
3. **Check Sentry** - Query for new/recurring errors
4. **Clean Tasks** - Update Linear issues, close stale items
5. **Update Docs** - Ensure docs match code
6. **Daily Report** - Summary sent to supervisor via Telegram

### Implementation

Will be implemented as a Clawdbot cron job:

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

### Phase 4: Skills (Pending)

- [ ] Sentry skill implemented and tested
- [ ] GitHub skill implemented and tested
- [ ] Linear skill implemented and tested
- [ ] Notion skill implemented and tested
- [ ] Stripe skill implemented and tested
- [ ] Render skill implemented and tested

### Phase 5: Daydream (Pending)

- [ ] Daydream skill created
- [ ] All 6 steps implemented
- [ ] Cron job scheduled
- [ ] Resumability tested
- [ ] Daily report working

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

## Next Steps

1. **Implement Sentry skill** - Highest priority for self-awareness
2. **Implement GitHub skill** - Needed for self-improvement workflow
3. **Test skill integration** - Ensure Clawdbot loads and uses skills
4. **Implement Daydream** - Daily maintenance automation

---

*Document updated: 2025-01-19*
