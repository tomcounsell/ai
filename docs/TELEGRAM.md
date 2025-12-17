# Telegram Integration

Telegram is the primary (but not only) interface for the Valor AI System. This integration is strictly an **interface layer** - it handles input, displays running status, delivers responses, and provides debug commands. It is mutually exclusive from the core AI system.

## Architecture

```
Telegram Messages
       |
       v
+------------------+
|  Interface Layer |  <-- This document covers this layer only
+------------------+
       |
       v
+------------------+
|   Core System    |  <-- Separate from interface
|   (ValorAgent)   |
+------------------+
```

The Telegram integration provides:
- **Input**: Receives user messages and media
- **Status**: Shows thinking/processing indicators
- **Response**: Delivers AI responses with formatting
- **Debug**: Administrative commands for system control

## Important: Real User Account

This system uses a **real Telegram user account** via Telethon, not a bot.

| Aspect | Bot | Real User (Our Approach) |
|--------|-----|--------------------------|
| Authentication | Bot token | Phone + 2FA |
| Appearance | Bot badge | Regular user "Valor Engels" |
| Capabilities | Limited | Full client features |
| Message access | Only when addressed | Full history access |
| Presence | Always "bot" | Natural user presence |

## Setup

### One-Time Authentication

```bash
./scripts/telegram_login.sh
# Enter verification code when prompted
# If 2FA enabled, enter password
# Session is saved for future use
```

### Starting the Interface

```bash
# Start with Telegram
./scripts/start.sh --telegram

# The bot runs in foreground for interactive auth if needed
```

### Environment Variables

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

## Response Behavior

### Default Behavior

| Context | Behavior |
|---------|----------|
| Direct Messages | Always respond (if user whitelisted) |
| Groups | Only respond when @valor is mentioned |
| Replies | Respond to replies to own messages |
| Keywords | Respond if configured keywords detected |

### Mention Detection

The client responds to:
1. **Direct mentions**: `@valor`, `@valorengels`
2. **Name mentions**: `valor`, `hey valor`, `hi valor`
3. **Custom keywords**: Per-group configurable
4. **Reply chains**: Replies to messages from the client

### Message Flow

```
Message Received
       |
       v
Is DM? --Yes--> Check whitelist --> Respond
       |
       No
       |
       v
Is Group?
       |
       Yes
       |
       v
@valor mentioned? --Yes--> Respond
       |
       No
       |
       v
Reply to us? --Yes--> Respond
       |
       No
       |
       v
Contains keyword? --Yes--> Respond (if configured)
       |
       No
       |
       v
Ignore
```

## Status Indicators

During processing, the interface shows status through emoji reactions:

| Stage | Emoji | Meaning |
|-------|-------|---------|
| Received | :eyes: | Message acknowledged |
| Processing | :technologist: | Working on response |
| Success | :+1: | Completed successfully |
| Error | :x: | Something went wrong |

## Configuration

### Group Configuration: `config/telegram_groups.json`

```json
{
  "default_behavior": {
    "respond_to_mentions": true,
    "respond_to_all": false,
    "respond_to_replies": true,
    "typing_indicator": true,
    "read_receipts": true
  },
  "groups": {
    "Development Team": {
      "enabled": true,
      "respond_to_mentions": true,
      "respond_to_all": false,
      "keywords": ["help", "question"],
      "ignore_users": []
    }
  }
}
```

### Workspace Mapping: `config/workspace_config.json`

Maps Telegram chats to system workspaces:

```json
{
  "workspaces": {
    "Project Name": {
      "telegram_chat_ids": ["-123456789"],
      "working_directory": "/path/to/project",
      "is_dev_group": true
    }
  },
  "dm_whitelist": {
    "allowed_users": {
      "username": {
        "working_directory": "/path/to/default"
      }
    }
  }
}
```

## Debug Commands

Administrative commands for system control (owner only):

| Command | Description |
|---------|-------------|
| `/status` | Show system health |
| `/restart` | Restart the system |
| `/logs` | Show recent logs |
| `/clear` | Clear conversation context |

## Security

### Access Control Layers

1. **User Whitelist**: Only approved users in DMs
2. **Group Whitelist**: Only approved groups
3. **Rate Limiting**: 30 messages per minute per user
4. **Workspace Isolation**: Each chat maps to specific workspace

### Session Security

- Session files stored in `data/` directory
- Never share session files
- Re-authenticate if session compromised: `./scripts/telegram_login.sh`

## Best Practices

1. **Always use mention detection** in groups (default)
2. **Never set `respond_to_all: true`** unless necessary
3. **Use keywords sparingly** to avoid spam
4. **Test in private groups** before public deployment
5. **Configure ignore lists** for other bots in groups

## Troubleshooting

### Not Responding to Mentions

1. Check `telegram_groups.json` is valid JSON
2. Verify group name matches exactly
3. Check logs for "Mention detected"
4. Ensure `respond_to_mentions: true`

### Responding to Everything

1. Verify `respond_to_all: false`
2. Check for overly broad keywords
3. Review group-specific overrides

### Authentication Issues

```bash
# Re-authenticate
./scripts/telegram_login.sh

# Or delete session and retry
rm data/telegram_session.session
./scripts/telegram_login.sh
```

### Connection Issues

- Check internet connectivity
- Verify API credentials in `.env`
- Check for Telegram service status
- Review logs: `./scripts/logs.sh --telegram`

## Interface Boundaries

The Telegram integration is strictly an interface. It does NOT:
- Make AI decisions
- Process or understand content
- Store conversation state (beyond message delivery)
- Access tools or MCP servers directly

All intelligence comes from the core system. The interface simply:
- Receives text/media input
- Shows processing status
- Delivers formatted responses
- Handles connection management
